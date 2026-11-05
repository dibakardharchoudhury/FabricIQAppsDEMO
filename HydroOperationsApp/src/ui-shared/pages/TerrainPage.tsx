import { lazy, Suspense, useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Mountain } from 'lucide-react'
import { importTerrain } from '../terrainImport'
import type { ImportedTerrain } from '../terrainModel'
import '../styles/terrain.css'

const TerrainCanvas = lazy(() => import('../components/TerrainCanvas').then(module => ({ default: module.TerrainCanvas })))

export function TerrainPage() {
  const [mesh, setMesh] = useState<File>()
  const [metadata, setMetadata] = useState<File>()
  const [terrain, setTerrain] = useState<ImportedTerrain>()
  const [error, setError] = useState<string>()
  const [busy, setBusy] = useState(false)
  const request = useRef<AbortController | null>(null)
  const form = useRef<HTMLFormElement>(null)
  useEffect(() => () => request.current?.abort(), [])
  const invalidate = () => {
    request.current?.abort()
    request.current = null
    setTerrain(undefined)
    setError(undefined)
    setBusy(false)
  }
  const clear = () => {
    invalidate()
    setMesh(undefined)
    setMetadata(undefined)
    form.current?.reset()
  }
  const renderError = useCallback((message: string) => { setTerrain(undefined); setError(message) }, [])
  const load = async (event: FormEvent) => {
    event.preventDefault()
    invalidate()
    if (!mesh || !metadata) { setError('Select both the surface PLY and its metadata JSON.'); return }
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    try {
      const imported = await importTerrain(mesh, metadata, controller.signal)
      if (!controller.signal.aborted) setTerrain(imported)
    } catch (reason) {
      if (!controller.signal.aborted) {
        console.error('Terrain import failed.', reason)
        setError(reason instanceof Error ? reason.message : 'Terrain import failed.')
      }
    } finally {
      if (!controller.signal.aborted) { setBusy(false); request.current = null }
    }
  }
  return <div className="v2-domain-page terrain-page">
    <header className="terrain-heading"><Mountain size={28} /><div><span className="v2-eyebrow">Rasputin terrain</span><h1>Catchment terrain</h1><p>Inspect one pre-generated catchment mesh. No processing service or Fabric source connection is required.</p></div></header>
    <p className="terrain-notice">Imported reference data, not live operational data or an inflow forecast. Catchment identity and provenance are supplied by the file author, not verified against Fabric assets. No ontology relationships are inferred.</p>
    <form className="terrain-import" onSubmit={event => void load(event)} ref={form} aria-label="Import catchment terrain">
      <label>Surface mesh (.ply, up to 32 MiB)<input type="file" accept=".ply" onChange={event => { invalidate(); setMesh(event.target.files?.[0]) }} /></label>
      <label>Provenance metadata (.json, up to 16 KiB)<input type="file" accept=".json,application/json" onChange={event => { invalidate(); setMetadata(event.target.files?.[0]) }} /></label>
      <div className="terrain-import-actions"><button type="submit" disabled={busy || !mesh || !metadata}>{busy ? 'Validating mesh...' : 'Import terrain'}</button><button type="button" onClick={clear}>Clear / cancel</button></div>
    </form>
    <p className="terrain-help">ASCII and binary little-endian Rasputin surface PLY; up to 500,000 vertices and 500,000 triangles. Metre-based UTM only. Files stay in this page&apos;s memory and are cleared on navigation or reload; they are not uploaded or saved in browser storage.</p>
    <details className="terrain-instructions"><summary>Preparing a mesh and metadata</summary>
      <p>Generate a surface PLY with Rasputin outside the app, for example:</p>
      <pre>rasputin mesh --dem terrain.tif --domain catchment.geojson --tolerance 5 --out catchment.ply</pre>
      <p>Save the following as catchment.json, replacing every example value with your actual provenance. Compute meshSha256 with <code>shasum -a 256 catchment.ply</code> (macOS), <code>sha256sum catchment.ply</code> (Linux), or <code>Get-FileHash catchment.ply -Algorithm SHA256</code> (PowerShell).</p>
      <pre>{JSON.stringify({
        schemaVersion: 1, catchmentName: 'Your catchment name', crs: 'EPSG:25833', elevationUnit: 'm',
        verticalDatum: "Your DEM's documented vertical datum", source: 'Your DEM dataset, version and acquisition date',
        attribution: 'Required source credits and data licence', generatedAt: '2026-11-05T10:00:00Z',
        meshSha256: '<replace with the mesh file SHA-256>', toleranceMetres: 5,
      }, null, 2)}</pre>
      <p>This sidecar is the viewer&apos;s metadata contract, not a native Rasputin run record. Optional toleranceMetres records the author&apos;s stated tolerance; this viewer does not verify it against a DEM. For a geographic DEM, generate the mesh with a supported UTM <code>--out-crs</code>.</p>
      <p>Geographic coordinates, VTK, GeoPackage, edge-only PLY, map overlays, land-cover colouring and on-demand mesh generation are not supported by this viewer.</p>
    </details>
    {busy && <p role="status">Checking metadata, file hash and triangle geometry...</p>}
    {error && <p className="terrain-error" role="alert">{error}</p>}
    {!terrain && !busy && !error && <section className="terrain-empty"><Mountain size={40} /><h2>No terrain imported</h2><p>Select a Rasputin surface mesh and matching provenance metadata to inspect your catchment.</p></section>}
    {terrain && <>
      <section className="terrain-metadata" aria-label="Imported terrain provenance">
        <h2>{terrain.manifest.catchmentName}</h2>
        <dl>
          <div><dt>Mesh</dt><dd>{terrain.fileName} · {(terrain.positions.length / 3).toLocaleString()} vertices · {(terrain.indices.length / 3).toLocaleString()} triangles</dd></div>
          <div><dt>Coordinates</dt><dd>{terrain.manifest.crs} · metres · vertical datum: {terrain.manifest.verticalDatum}</dd></div>
          <div><dt>Extent (easting / northing)</dt><dd>{terrain.bounds.min[0].toFixed(2)} / {terrain.bounds.min[1].toFixed(2)} to {terrain.bounds.max[0].toFixed(2)} / {terrain.bounds.max[1].toFixed(2)} m</dd></div>
          <div><dt>Elevation</dt><dd>{terrain.bounds.min[2].toFixed(2)} to {terrain.bounds.max[2].toFixed(2)} m</dd></div>
          <div><dt>Generated (author supplied)</dt><dd>{terrain.manifest.generatedAt}</dd></div>
          <div><dt>DEM source</dt><dd>{terrain.manifest.source}</dd></div>
          <div><dt>Attribution / licence</dt><dd>{terrain.manifest.attribution}</dd></div>
          <div><dt>Declared tolerance</dt><dd>{terrain.manifest.toleranceMetres === undefined ? 'Not supplied' : `${terrain.manifest.toleranceMetres} m (not DEM-verified)`}</dd></div>
          <div><dt>Verified file SHA-256</dt><dd><code>{terrain.manifest.meshSha256}</code></dd></div>
        </dl>
      </section>
      {terrain.ignoredFaceProperties.length > 0 && <p className="terrain-notice">Additional face properties are not visualized: {terrain.ignoredFaceProperties.join(', ')}. Land-cover codes, fractions and DEM height shares are not interpreted as elevation or operational data.</p>}
      <Suspense fallback={<p role="status">Loading 3D terrain viewer...</p>}><TerrainCanvas terrain={terrain} onError={renderError} /></Suspense>
    </>}
    <footer className="terrain-help">Meshes generated with <a href="https://github.com/expertanalytics/rasputin" target="_blank" rel="noreferrer">Rasputin by Expert Analytics (MIT)</a>. Terrain and land-cover data retain their own attribution and licence requirements.</footer>
  </div>
}
