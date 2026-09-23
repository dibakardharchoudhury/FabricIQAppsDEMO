import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Database, Layers, MapPin, RefreshCw, Search, X } from 'lucide-react'
import { beginInteractiveConnect, refreshEnergyMap } from '../../services/fabric'
import { queryEnergyMap, queryEnergySourceStatus, queryUnplottedMarketMessages } from '../../services/energyMap'
import {
  DEFAULT_LAYERS, FEATURE_LIMIT, INITIAL_VIEW, MAP_LAYERS, safeSourceUrl, sourceAge, sourceIsStale,
  type EnergyFeature, type EnergyLayerId, type EnergySourceStatus, type MapViewport,
} from '../energyMapModel'
import '../styles/energy-map.css'

const EnergyMapCanvas = lazy(() => import('../components/energyMap/EnergyMapCanvas').then(module => ({ default: module.EnergyMapCanvas })))

export function OperationsMapPage() {
  const [viewport, setViewport] = useState<MapViewport>(INITIAL_VIEW)
  const [layers, setLayers] = useState<EnergyLayerId[]>(DEFAULT_LAYERS)
  const [features, setFeatures] = useState<EnergyFeature[]>([])
  const [statuses, setStatuses] = useState<EnergySourceStatus[]>([])
  const [unplotted, setUnplotted] = useState<EnergyFeature[]>([])
  const [selected, setSelected] = useState<EnergyFeature>()
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string>()
  const [sourceError, setSourceError] = useState<string>()
  const [importStatus, setImportStatus] = useState<string>()
  const [importing, setImporting] = useState(false)
  const [truncated, setTruncated] = useState(false)
  const [revision, setRevision] = useState(0)
  const [search, setSearch] = useState('')
  const [now, setNow] = useState(Date.now)
  const active = useRef(true)
  const visibleLayers = useMemo(() => new Set(layers), [layers])
  const selectFeature = useCallback((feature: EnergyFeature) => setSelected(feature), [])

  useEffect(() => {
    active.current = true
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        setNow(Date.now())
        setRevision(value => value + 1)
      }
    }, 60_000)
    const refreshVisible = () => {
      if (document.visibilityState === 'visible') setRevision(value => value + 1)
    }
    document.addEventListener('visibilitychange', refreshVisible)
    return () => {
      active.current = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refreshVisible)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setBusy(true)
      void queryEnergyMap(viewport, layers, controller.signal).then(data => {
        if (controller.signal.aborted) return
        setFeatures(data.features)
        setTruncated(data.truncated)
        setError(undefined)
      }).catch((reason: unknown) => {
        if (controller.signal.aborted) return
        console.error('Fabric energy map query failed.', reason)
        setError(reason instanceof Error ? reason.message : 'Energy map data is unavailable.')
      }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    }, 350)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [viewport, layers, revision])

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([queryEnergySourceStatus(controller.signal), queryUnplottedMarketMessages(controller.signal)])
      .then(([sourceStatus, messages]) => {
        if (controller.signal.aborted) return
        setStatuses(sourceStatus)
        setUnplotted(messages)
        setSourceError(undefined)
      }).catch((reason: unknown) => {
        if (controller.signal.aborted) return
        console.error('Fabric map source status failed.', reason)
        setSourceError(reason instanceof Error ? reason.message : 'Source status is unavailable.')
      })
    return () => controller.abort()
  }, [revision])

  const connect = async () => {
    try {
      await beginInteractiveConnect('telemetry')
      if (active.current) setRevision(value => value + 1)
    } catch (reason) {
      console.error('Map data sign-in failed.', reason)
      if (active.current) setError(reason instanceof Error ? reason.message : 'Map sign-in failed.')
    }
  }
  const importLatest = async () => {
    setImporting(true); setImportStatus('Starting Fabric import...')
    try {
      const result = await refreshEnergyMap(status => { if (active.current) setImportStatus(`Fabric import: ${status}`) })
      if (result !== 'Completed') throw new Error(`Fabric import ${result}. Check the pipeline run for details.`)
      if (active.current) { setImportStatus('Fabric import completed.'); setRevision(value => value + 1) }
    } catch (reason) {
      console.error('Energy data import failed.', reason)
      if (active.current) setImportStatus(reason instanceof Error ? reason.message : 'Import failed.')
    } finally {
      if (active.current) setImporting(false)
    }
  }
  const displayed = useMemo(() => features.filter(feature =>
    visibleLayers.has(feature.layerId)
    && viewport.zoom >= MAP_LAYERS.find(layer => layer.id === feature.layerId)!.minZoom), [features, visibleLayers, viewport.zoom])
  const listed = displayed.filter(feature => `${feature.label} ${feature.layerId}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())).slice(0, 100)
  const readyCount = statuses.filter(status => status.state === 'ready').length
  const staleCount = statuses.filter(status => sourceIsStale(status, now)).length

  return <div className="energy-map-page">
    <header className="energy-map-heading">
      <div><span className="v2-eyebrow">Operations Map</span><h1>Norwegian energy context</h1>
        <p>Infrastructure, hydropower, reservoir areas, power exchange and market messages imported into Fabric.</p></div>
      <div className="energy-map-actions">
        <button type="button" className="v2-primary-action" disabled={busy} onClick={() => setRevision(value => value + 1)}><RefreshCw size={15} />Reload map</button>
        <button type="button" className="v2-primary-action" disabled={importing} onClick={() => void importLatest()}><Database size={15} />{importing ? 'Importing...' : 'Import latest data'}</button>
      </div>
    </header>
    <div className="energy-map-summary">
      <span><Layers size={14} />{readyCount}/{MAP_LAYERS.length} layers ready</span>
      <span><MapPin size={14} />{displayed.length.toLocaleString()} features in view</span>
      <span>Fabric snapshots, not a live grid-control feed</span>
      {staleCount > 0 && <strong>{staleCount} layer{staleCount === 1 ? '' : 's'} need a fresh import</strong>}
    </div>
    {(error || sourceError) && <div className="energy-map-notice error" role="alert"><AlertTriangle size={17} /><span>{[...new Set([error, sourceError].filter(Boolean))].join(' ')} {features.length > 0 && 'Previously loaded features are still shown.'}</span><button type="button" className="v2-primary-action" onClick={() => void connect()}>Connect Fabric data</button></div>}
    {importStatus && <div className="energy-map-notice" role="status">{importStatus} {importing && 'This runs a cloud pipeline; changing tabs does not cancel it.'}</div>}
    {truncated && <div className="energy-map-notice" role="status">This viewport exceeds {FEATURE_LIMIT.toLocaleString()} features. Only the first {FEATURE_LIMIT.toLocaleString()} are displayed; zoom in or turn off dense layers.</div>}
    <div className="energy-map-layout">
      <aside className="energy-map-layers" aria-label="Energy map layers">
        <h2><Layers size={17} />Layers and freshness</h2>
        {MAP_LAYERS.map(layer => {
          const status = statuses.find(value => value.layerId === layer.id)
          const tooFar = viewport.zoom < layer.minZoom
          const stale = status && sourceIsStale(status, now)
          return <div key={layer.id} className="energy-map-layer">
            <label>
              <input type="checkbox" checked={layers.includes(layer.id)} onChange={event => setLayers(current =>
                event.target.checked ? [...current, layer.id] : current.filter(id => id !== layer.id))} />
              <i style={{ backgroundColor: layer.color }} /><strong>{layer.label}</strong>
            </label>
            <small>{layer.source}{tooFar ? ` - zoom to ${layer.minZoom}+` : ''}</small>
            <small className={status?.state === 'error' || stale ? 'energy-map-stale' : ''}>
              {status ? `${status.rowCount.toLocaleString()} imported - ${sourceAge(status.lastSuccessAt, now)}` : 'Not imported'}
              {status?.state === 'error' ? ' - import error' : stale ? ' - stale snapshot' : ''}
            </small>
            {!!status?.unmappedCount && <small>{status.unmappedCount.toLocaleString()} without map coordinates</small>}
            {!!status?.rejectedCount && <small>{status.rejectedCount.toLocaleString()} rejected; inspect ingestion report</small>}
            {status?.message && <details><summary>Source details</summary><p>{status.message}</p></details>}
          </div>
        })}
      </aside>
      <section className="energy-map-main">
        <Suspense fallback={<div className="energy-map-loading">Loading map renderer...</div>}>
          <EnergyMapCanvas features={displayed} onViewport={setViewport} onSelect={selectFeature} />
        </Suspense>
        {busy && <div className="energy-map-query-state" role="status">Loading this viewport from Fabric...</div>}
        <p className="energy-map-disclaimer">NVE public infrastructure can be incomplete or approximate. Reservoir markers represent area statistics; power-flow lines are schematic. UMM covers the last 30 publication days, not all older active notices. Importance is application-derived, not an official Nord Pool or grid-safety rating.</p>
      </section>
      <aside className="energy-map-details" aria-label="Selected feature and visible features">
        {selected ? <FeatureDetails feature={selected} onClose={() => setSelected(undefined)} /> : <div className="energy-map-empty"><MapPin size={23} /><h2>Explore a feature</h2><p>Select a line, plant or message on the map to see its source details.</p></div>}
        <div className="energy-map-search"><Search size={15} /><input aria-label="Search loaded map features" value={search} placeholder="Search this viewport" onChange={event => setSearch(event.target.value)} /></div>
        <div className="energy-map-feature-list">
          {listed.map(feature => <button type="button" key={`${feature.layerId}:${feature.id}`} onClick={() => setSelected(feature)}>
            <strong>{feature.label}</strong><small>{MAP_LAYERS.find(layer => layer.id === feature.layerId)?.label}</small>
          </button>)}
          {!listed.length && <p>No matching features loaded in this viewport.</p>}
          {displayed.length > 100 && <small>List shows up to 100 matching loaded features.</small>}
        </div>
        {unplotted.length > 0 && <details className="energy-map-unmapped"><summary>Messages not plotted ({unplotted.length > 100 ? '100+' : unplotted.length})</summary>
          <p>Unlocated, cancelled and undatable notices are retained here, not shown as active map events.</p>
          {unplotted.slice(0, 100).map(feature => <button type="button" key={feature.id} onClick={() => setSelected(feature)}>{feature.label}</button>)}
        </details>}
      </aside>
    </div>
  </div>
}

function FeatureDetails({ feature, onClose }: { feature: EnergyFeature; onClose: () => void }) {
  const url = safeSourceUrl(feature.sourceUrl)
  const fields = Object.entries(feature.properties).filter(([, value]) => value !== null && value !== undefined && value !== '')
  return <section className="energy-map-inspector">
    <button type="button" className="energy-map-close" aria-label="Close feature details" onClick={onClose}><X size={18} /></button>
    <span className="v2-eyebrow">{MAP_LAYERS.find(layer => layer.id === feature.layerId)?.label}</span>
    <h2>{feature.label}</h2>
    <p className="energy-map-id">{feature.id}</p>
    <p>Observed: {feature.observedAt ? new Date(feature.observedAt).toLocaleString() : 'Not supplied by source'}</p>
    <p>Imported: {new Date(feature.ingestedAt).toLocaleString()}</p>
    {!feature.geometry && <p className="energy-map-stale">No verified map coordinates are available.</p>}
    {feature.layerId === 'umm' && feature.properties.map_eligible !== true && <p className="energy-map-stale">This notice is not eligible for the active-event map.</p>}
    <dl>{fields.map(([name, value]) => <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
    {url && <a href={url} target="_blank" rel="noreferrer">Source information</a>}
  </section>
}
