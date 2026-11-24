import { MAX_MANIFEST_BYTES, MAX_TERRAIN_BYTES, type ImportedTerrain } from './terrainModel'

export async function importTerrain(mesh: File, metadata: File, signal: AbortSignal): Promise<ImportedTerrain> {
  if (!/\.ply$/i.test(mesh.name) || !mesh.size || mesh.size > MAX_TERRAIN_BYTES) throw new Error('Select a non-empty surface .ply file, at most 32 MiB.')
  if (!/\.json$/i.test(metadata.name) || !metadata.size || metadata.size > MAX_MANIFEST_BYTES) throw new Error('Select a non-empty metadata .json file, at most 16 KiB.')
  signal.throwIfAborted()
  const [buffer, text] = await Promise.all([mesh.arrayBuffer(), metadata.text()])
  signal.throwIfAborted()
  // Inline code avoids a separate protected-asset request from the native worker loader.
  const { default: TerrainWorker } = await import('./terrain.worker?worker&inline')
  signal.throwIfAborted()
  return new Promise((resolve, reject) => {
    const worker = new TerrainWorker({ name: 'terrain-import' })
    const cleanup = () => { worker.terminate(); signal.removeEventListener('abort', cancel); window.clearTimeout(timeout) }
    const cancel = () => { cleanup(); reject(new DOMException('Terrain import cancelled.', 'AbortError')) }
    const timeout = window.setTimeout(() => { cleanup(); reject(new Error('Terrain import exceeded 60 seconds. Export a smaller mesh.')) }, 60_000)
    signal.addEventListener('abort', cancel, { once: true })
    worker.onmessage = (event: MessageEvent<{ terrain?: ImportedTerrain; error?: string }>) => {
      cleanup()
      if (event.data.terrain) resolve(event.data.terrain)
      else reject(new Error(event.data.error ?? 'Terrain worker returned no mesh.'))
    }
    worker.onerror = event => {
      event.preventDefault()
      cleanup()
      reject(new Error(`Terrain validation worker failed${event.message ? `: ${event.message}` : '. Check whether your browser blocks local workers.'}`))
    }
    worker.onmessageerror = () => { cleanup(); reject(new Error('The terrain worker returned unreadable data.')) }
    try { worker.postMessage({ buffer, metadata: text, fileName: mesh.name }, [buffer]) }
    catch (reason) { cleanup(); reject(reason) }
  })
}
