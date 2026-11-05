import { parseRasputinPly, parseTerrainManifest, verifyTerrainHash } from './terrainModel'

self.onmessage = async (event: MessageEvent<{ buffer: ArrayBuffer; metadata: string; fileName: string }>) => {
  try {
    const manifest = parseTerrainManifest(event.data.metadata)
    await verifyTerrainHash(event.data.buffer, manifest.meshSha256)
    const mesh = parseRasputinPly(event.data.buffer)
    self.postMessage(
      { terrain: { ...mesh, manifest, fileName: event.data.fileName } },
      { transfer: [mesh.positions.buffer, mesh.indices.buffer] },
    )
  } catch (reason) {
    self.postMessage({ error: reason instanceof Error ? reason.message : 'Terrain import failed.' })
  }
}
