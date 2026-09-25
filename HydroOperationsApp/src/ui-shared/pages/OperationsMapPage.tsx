import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, Database, Layers, MapPin, MessageSquare, RefreshCw, Search, SlidersHorizontal, X, Zap } from 'lucide-react'
import { beginInteractiveConnect, refreshEnergyMap } from '../../services/fabric'
import {
  queryAssetMarketMessages, queryCountryPowerBalance, queryEnergyFeatureDetails, queryEnergyMap, queryEnergyPropertyOptions,
  queryEnergySourceStatus, queryReservoirAreas,
} from '../../services/energyMap'
import { EnergyPropertyFiltersPanel } from '../components/energyMap/EnergyPropertyFilters'
import { LiveGridFrequencyTile } from '../components/energyMap/LiveGridFrequencyTile'
import { CountryPowerBalanceTile } from '../components/energyMap/CountryPowerBalanceTile'
import { MapChatDrawer } from '../components/energyMap/MapChatDrawer'
import type { ResolvedMapPlace } from '../../services/mapChat'
import type { MapChatContext, MapFocusRequest } from '../mapChatModel'
import {
  createEnergyPropertyFilters, matchesEnergyPropertyFilters, propertyFilterCount,
  type EnergyPropertyFilters, type EnergyPropertyOptions,
} from '../energyMapFilters'
import {
  DEFAULT_LAYERS, FEATURE_LIMIT, formatCapacityPower, INITIAL_VIEW, isAssetLayer, isReservoirAreaCode,
  MAP_LAYERS, MAP_VISIBLE_LAYERS, RESERVOIR_AREAS, safeSourceUrl, sourceAge, sourceIsStale, visiblePlantCapacity,
  type EnergyFeature, type EnergyLayerId, type EnergySourceStatus, type MapViewport, type ReservoirAreaCode, type ReservoirAreaSelection,
} from '../energyMapModel'
import '../styles/energy-map.css'

const EnergyMapCanvas = lazy(() => import('../components/energyMap/EnergyMapCanvas').then(module => ({ default: module.EnergyMapCanvas })))

type FilterPanel = 'layers' | 'properties' | 'areas'
const panelStorageKey = (panel: FilterPanel) => `hydro.map.${panel}.expanded.v1`

function readPanelExpansion(panel: FilterPanel, defaultValue: boolean): boolean {
  try {
    const value = localStorage.getItem(panelStorageKey(panel))
    if (value === null) return defaultValue
    if (value === 'true' || value === 'false') return value === 'true'
    console.warn(`Invalid saved ${panel} panel preference; using the default.`)
  } catch (reason) {
    console.warn('Map panel preferences could not be read.', reason)
  }
  return defaultValue
}

export function OperationsMapPage() {
  const [viewport, setViewport] = useState<MapViewport>(INITIAL_VIEW)
  const [layers, setLayers] = useState<EnergyLayerId[]>(DEFAULT_LAYERS)
  const [panels, setPanels] = useState(() => ({
    layers: readPanelExpansion('layers', true), properties: readPanelExpansion('properties', false), areas: readPanelExpansion('areas', false),
  }))
  const [propertyFilters, setPropertyFilters] = useState(createEnergyPropertyFilters)
  const [propertyOptions, setPropertyOptions] = useState<EnergyPropertyOptions>()
  const [propertyError, setPropertyError] = useState<string>()
  const layersPanelId = useId()
  const propertiesPanelId = useId()
  const areasPanelId = useId()
  const [areaSelection, setAreaSelection] = useState<ReservoirAreaSelection>(null)
  const [loadedQueryKey, setLoadedQueryKey] = useState<string>()
  const [features, setFeatures] = useState<EnergyFeature[]>([])
  const [statuses, setStatuses] = useState<EnergySourceStatus[]>([])
  const [areas, setAreas] = useState<EnergyFeature[]>([])
  const [areaError, setAreaError] = useState<string>()
  const [powerBalance, setPowerBalance] = useState<EnergyFeature | null>()
  const [powerBalanceError, setPowerBalanceError] = useState<string>()
  const [selected, setSelected] = useState<EnergyFeature>()
  const [chatOpen, setChatOpen] = useState(false)
  const [focusRequest, setFocusRequest] = useState<MapFocusRequest>()
  const [navigationNotice, setNavigationNotice] = useState<string>()
  const chatButton = useRef<HTMLButtonElement>(null)
  const focusSequence = useRef(0)
  const [detailResult, setDetailResult] = useState<{ key: string; data?: EnergyFeature; error?: string }>()
  const [messageResult, setMessageResult] = useState<{ key: string; data?: EnergyFeature[]; error?: string }>()
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
  const queryKey = useMemo(() => JSON.stringify([viewport, layers, propertyFilters, areaSelection]), [viewport, layers, propertyFilters, areaSelection])
  const selectFeature = useCallback((feature: EnergyFeature) => setSelected(feature), [])
  const setLayerEnabled = useCallback((layer: EnergyLayerId, enabled: boolean) => {
    setLayers(current => enabled ? current.includes(layer) ? current : [...current, layer] : current.filter(id => id !== layer))
  }, [])
  const changeProperties = useCallback((value: EnergyPropertyFilters) => {
    setPropertyFilters(value)
    setSelected(undefined)
    setBusy(true)
  }, [])
  const changeAreas = useCallback((value: ReservoirAreaSelection) => {
    setAreaSelection(value)
    setSelected(undefined)
    setBusy(true)
  }, [])
  const toggleArea = (code: ReservoirAreaCode, checked: boolean) => {
    let current = areaSelection ?? RESERVOIR_AREAS.map(area => area.code)
    if (code.startsWith('NO') && code !== 'NO') current = current.filter(value => value !== 'NO')
    if (code === 'NO' && checked) current = current.filter(value => !value.startsWith('NO'))
    const next = checked ? [...new Set([...current, code])] : current.filter(value => value !== code)
    changeAreas(next.length === RESERVOIR_AREAS.length ? null : next)
  }
  const togglePanel = (panel: FilterPanel) => {
    const expanded = !panels[panel]
    setPanels(current => ({ ...current, [panel]: expanded }))
    try { localStorage.setItem(panelStorageKey(panel), String(expanded)) }
    catch (reason) { console.warn('Map panel preference could not be saved.', reason) }
  }

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
      void queryEnergyMap(viewport, layers, controller.signal, propertyFilters, areaSelection).then(data => {
        if (controller.signal.aborted) return
        setFeatures(data.features)
        setTruncated(data.truncated)
        setError(undefined)
        setLoadedQueryKey(queryKey)
      }).catch((reason: unknown) => {
        if (controller.signal.aborted) return
        console.error('Fabric energy map query failed.', reason)
        setError(reason instanceof Error ? reason.message : 'Energy map data is unavailable.')
      }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    }, 350)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [viewport, layers, revision, propertyFilters, areaSelection, queryKey])

  useEffect(() => {
    const controller = new AbortController()
    void queryEnergyPropertyOptions(controller.signal).then(options => {
      if (controller.signal.aborted) return
      setPropertyOptions(options)
      setPropertyError(undefined)
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return
      console.error('Fabric map property options failed.', reason)
      setPropertyError(reason instanceof Error ? reason.message : 'Property filter options are unavailable.')
    })
    return () => controller.abort()
  }, [revision])

  useEffect(() => {
    const controller = new AbortController()
    void queryEnergySourceStatus(controller.signal)
      .then(sourceStatus => {
        if (controller.signal.aborted) return
        setStatuses(sourceStatus)
        setSourceError(undefined)
      }).catch((reason: unknown) => {
        if (controller.signal.aborted) return
        console.error('Fabric map source status failed.', reason)
        setSourceError(reason instanceof Error ? reason.message : 'Source status is unavailable.')
      })
    return () => controller.abort()
  }, [revision])

  useEffect(() => {
    const controller = new AbortController()
    void queryReservoirAreas(controller.signal).then(data => {
      if (controller.signal.aborted) return
      setAreas(data); setAreaError(undefined)
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return
      console.error('Reservoir-area geometry failed.', reason)
      setAreaError(reason instanceof Error ? reason.message : 'Reservoir-area geometry is unavailable.')
    })
    void queryCountryPowerBalance(controller.signal).then(data => {
      if (controller.signal.aborted) return
      setPowerBalance(data); setPowerBalanceError(undefined)
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return
      console.error('Country power-balance snapshot failed.', reason)
      setPowerBalanceError(reason instanceof Error ? reason.message : 'Country power balance is unavailable.')
    })
    return () => controller.abort()
  }, [revision])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    const key = `${selected.layerId}:${selected.id}`
    void queryEnergyFeatureDetails(selected, controller.signal).then(data => {
      if (!controller.signal.aborted) setDetailResult({ key, data })
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return
      console.error('Selected map feature details failed.', reason)
      setDetailResult({ key, error: reason instanceof Error ? reason.message : 'Feature details are unavailable.' })
    })
    if (isAssetLayer(selected.layerId)) {
      void queryAssetMarketMessages(selected, controller.signal).then(data => {
        if (!controller.signal.aborted) setMessageResult({ key, data })
      }).catch((reason: unknown) => {
        if (controller.signal.aborted) return
        console.error('Selected asset market messages failed.', reason)
        setMessageResult({ key, error: reason instanceof Error ? reason.message : 'Asset market messages are unavailable.' })
      })
    }
    return () => controller.abort()
  }, [selected, revision])

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
    && viewport.zoom >= MAP_LAYERS.find(layer => layer.id === feature.layerId)!.minZoom
    && matchesEnergyPropertyFilters(feature, propertyFilters)), [features, visibleLayers, viewport.zoom, propertyFilters])
  const listed = displayed.filter(feature => `${feature.label} ${feature.layerId}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())).slice(0, 100)
  const mapStatuses = statuses.filter(status => MAP_VISIBLE_LAYERS.some(layer => layer.id === status.layerId))
  const readyCount = mapStatuses.filter(status => status.state === 'ready').length
  const staleCount = mapStatuses.filter(status => sourceIsStale(status, now)).length
  const propertyCount = propertyFilterCount(propertyFilters)
  const selectionKey = selected ? `${selected.layerId}:${selected.id}` : undefined
  const details = detailResult?.key === selectionKey ? detailResult : undefined
  const messages = messageResult?.key === selectionKey ? messageResult : undefined
  const shownAreas = layers.includes('reservoirs') ? areas.filter(area => areaSelection === null
    || (isReservoirAreaCode(area.properties.area_code) && areaSelection.includes(area.properties.area_code))) : []
  const capacity = useMemo(() => visiblePlantCapacity(displayed), [displayed])
  const viewPending = busy || loadedQueryKey !== queryKey
  const chatContext = useMemo<MapChatContext>(() => ({
    viewport, layers, propertyFilters, areaSelection, selected, visibleFeatures: displayed,
    statuses, pending: viewPending || Boolean(error), truncated,
  }), [viewport, layers, propertyFilters, areaSelection, selected, displayed, statuses, viewPending, error, truncated])
  const focusPlace = (target: ResolvedMapPlace): string => {
    const changes: string[] = []
    if (!layers.includes(target.feature.layerId)) {
      setLayerEnabled(target.feature.layerId, true)
      changes.push('enabled its layer')
    }
    if (!matchesEnergyPropertyFilters(target.feature, propertyFilters)) {
      const defaults = createEnergyPropertyFilters()
      setPropertyFilters(current => target.feature.layerId === 'hydro-plants'
        ? { ...current, hydro: defaults.hydro } : { ...current, transformers: defaults.transformers })
      changes.push('cleared conflicting property filters')
    }
    const outsideAreas = areaSelection !== null && (
      target.feature.layerId === 'reservoirs'
        ? !isReservoirAreaCode(target.feature.properties.area_code) || !areaSelection.includes(target.feature.properties.area_code)
        : target.areaCodes !== undefined && !areaSelection.some(code => target.areaCodes?.includes(code)))
    if (outsideAreas) {
      setAreaSelection(null)
      changes.push('cleared conflicting area filters')
    }
    if (search) { setSearch(''); changes.push('cleared the feature-list search') }
    setSelected(target.feature)
    setFocusRequest({ sequence: ++focusSequence.current, place: target.place, feature: target.feature })
    const message = `Showing ${target.place.label}${changes.length ? `; ${changes.join(', ')}` : ''}.`
    setNavigationNotice(message)
    return message
  }

  return <div className="energy-map-page">
    <header className="energy-map-heading">
      <div><span className="v2-eyebrow">Operations Map</span><h1>Norwegian energy context</h1>
        <p>Infrastructure, hydropower, reservoir areas, power exchange and market messages imported into Fabric.</p></div>
      <div className="energy-map-actions">
        <button ref={chatButton} type="button" className="v2-primary-action" aria-expanded={chatOpen} onClick={() => setChatOpen(value => !value)}><MessageSquare size={15} />Map chat</button>
        <button type="button" className="v2-primary-action" disabled={busy} onClick={() => setRevision(value => value + 1)}><RefreshCw size={15} />Reload map</button>
        <button type="button" className="v2-primary-action" disabled={importing} onClick={() => void importLatest()}><Database size={15} />{importing ? 'Importing...' : 'Import latest data'}</button>
      </div>
    </header>
    <div className="energy-map-summary">
      <span><Layers size={14} />{readyCount}/{MAP_VISIBLE_LAYERS.length} layers ready</span>
      <span><MapPin size={14} />{displayed.length.toLocaleString()} features in view{shownAreas.length ? ` + ${shownAreas.length} area overlays` : ''}</span>
      <span>Map layers use Fabric snapshots</span>
      {staleCount > 0 && <strong>{staleCount} layer{staleCount === 1 ? '' : 's'} need a fresh import</strong>}
    </div>
    {(error || sourceError || areaError) && <div className="energy-map-notice error" role="alert"><AlertTriangle size={17} /><span>{[...new Set([error, sourceError, areaError].filter(Boolean))].join(' ')} {features.length > 0 && 'Previously loaded features are still shown.'}</span><button type="button" className="v2-primary-action" onClick={() => void connect()}>Connect Fabric data</button></div>}
    {importStatus && <div className="energy-map-notice" role="status">{importStatus} {importing && 'This runs a cloud pipeline; changing tabs does not cancel it.'}</div>}
    {truncated && <div className="energy-map-notice" role="status">This viewport exceeds {FEATURE_LIMIT.toLocaleString()} features. Only the first {FEATURE_LIMIT.toLocaleString()} are displayed; zoom in or turn off dense layers.</div>}
    {navigationNotice && <div className="energy-map-notice" role="status"><MapPin size={16} /><span>{navigationNotice}</span><button type="button" aria-label="Dismiss map navigation notice" onClick={() => setNavigationNotice(undefined)}><X size={15} /></button></div>}
    <div className="energy-map-stat-tiles">
      <LiveGridFrequencyTile />
      <VisibleCapacityTile capacity={capacity} pending={viewPending} error={error} truncated={truncated} />
      <CountryPowerBalanceTile feature={powerBalance} error={powerBalanceError} now={now} />
    </div>
    <div className="energy-map-filter-toolbar" aria-label="Map filter groups">
      <button type="button" aria-controls={layersPanelId} aria-expanded={panels.layers} onClick={() => togglePanel('layers')}>
        <Layers size={16} />Layers <span>{layers.length} selected</span>{panels.layers ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      <button type="button" aria-controls={propertiesPanelId} aria-expanded={panels.properties} onClick={() => togglePanel('properties')}>
        <SlidersHorizontal size={16} />Properties <span>{propertyCount} active</span>{panels.properties ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      <button type="button" aria-controls={areasPanelId} aria-expanded={panels.areas} onClick={() => togglePanel('areas')}>
        <MapPin size={16} />Areas <span>{areaSelection === null ? 'All' : `${areaSelection.length} selected`}</span>{panels.areas ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {propertyCount > 0 && <button type="button" onClick={() => changeProperties(createEnergyPropertyFilters())}>Clear property filters</button>}
      {areaSelection !== null && <button type="button" onClick={() => changeAreas(null)}>Reset areas</button>}
    </div>
    <aside id={layersPanelId} className="energy-map-layers" aria-label="Energy map layers" hidden={!panels.layers}>
      <h2><Layers size={17} />Layers and freshness</h2>
      {MAP_VISIBLE_LAYERS.map(layer => {
        const status = statuses.find(value => value.layerId === layer.id)
        const tooFar = viewport.zoom < layer.minZoom
        const stale = status && sourceIsStale(status, now)
        return <div key={layer.id} className="energy-map-layer">
          <label>
            <input type="checkbox" checked={layers.includes(layer.id)} onChange={event => setLayerEnabled(layer.id, event.target.checked)} />
            <i style={{ backgroundColor: layer.color }} /><strong>{layer.label}</strong>
          </label>
          <details>
            <summary aria-label={`${layer.label} details`}>Details</summary>
            <small>{layer.source}{tooFar ? ` - zoom to ${layer.minZoom}+` : ''}</small>
            <small className={status?.state === 'error' || stale ? 'energy-map-stale' : ''}>
              {status ? `${status.rowCount.toLocaleString()} imported - ${sourceAge(status.lastSuccessAt, now)}` : 'Not imported'}
              {status?.state === 'error' ? ' - import error' : stale ? ' - stale snapshot' : ''}
            </small>
            {!!status?.unmappedCount && <small>{status.unmappedCount.toLocaleString()} without map coordinates</small>}
            {!!status?.rejectedCount && <small>{status.rejectedCount.toLocaleString()} rejected geometries; records retained</small>}
            {status?.message && <p>{status.message}</p>}
          </details>
        </div>
      })}
    </aside>
    <div id={propertiesPanelId} className="energy-map-property-panel" hidden={!panels.properties}>
      <EnergyPropertyFiltersPanel value={propertyFilters} options={propertyOptions} error={propertyError}
        layers={layers} zoom={viewport.zoom} onChange={changeProperties} onLayerChange={setLayerEnabled} />
    </div>
    <section id={areasPanelId} className="energy-map-area-panel" aria-label="Reservoir area selection" hidden={!panels.areas}>
      <div className="energy-map-area-heading"><h2>Reservoir areas</h2><button type="button" onClick={() => changeAreas(null)}>All areas</button><button type="button" onClick={() => changeAreas([])}>Select none</button></div>
      <div className="energy-map-area-choices">
        {RESERVOIR_AREAS.map(area => {
          const feature = areas.find(value => value.properties.area_code === area.code)
          return <label key={area.code}>
            <input type="checkbox" checked={areaSelection === null || areaSelection.includes(area.code)}
              disabled={!feature} onChange={event => toggleArea(area.code, event.target.checked)} />
            <i style={{ backgroundColor: area.color }} /><span>{area.label}</span>
            {feature?.properties.has_reservoir_data === false && <small>No reservoir figures</small>}
          </label>
        })}
      </div>
      <p>Plants and transformers must be inside at least one selected area. Selecting Norway replaces its NO1-NO5 subareas; selecting a subarea replaces Norway. Other selected layers stay visible. Colors identify areas, not filling levels.</p>
      {!layers.includes('reservoirs') && <p>Area filtering is active independently of overlay visibility. Enable the reservoir layer to see the colored boundaries.</p>}
    </section>
    <div className="energy-map-layout">
      <section className="energy-map-main">
        <Suspense fallback={<div className="energy-map-loading">Loading map renderer...</div>}>
          <EnergyMapCanvas features={displayed} areas={areas} capacityMaximum={propertyOptions?.hydro.capacityMax}
            showAreas={layers.includes('reservoirs')} areaSelection={areaSelection} focusRequest={focusRequest} onViewport={setViewport} onSelect={selectFeature} />
        </Suspense>
        {viewPending && !error && <div className="energy-map-query-state" role="status">Updating the filtered map from Fabric...</div>}
        <p className="energy-map-disclaimer">Area colors identify regions, not reservoir filling levels. Foreign reservoir figures are unavailable. Boundaries are generalized. Plant marker area scales with installed MW; transformer capacity is unknown and markers are uniform. Power-flow lines are schematic.</p>
      </section>
      <aside className="energy-map-details" aria-label="Selected feature and visible features">
        {selected ? <>
          <FeatureDetails feature={details?.data ?? selected} onClose={() => setSelected(undefined)} />
          {selected.layerId === 'reservoirs' && isReservoirAreaCode(selected.properties.area_code)
            && <button type="button" className="energy-map-area-focus" onClick={() => {
              if (isReservoirAreaCode(selected.properties.area_code)) changeAreas([selected.properties.area_code])
            }}>Filter to this area</button>}
          {!details && <p role="status">Loading full source details...</p>}
          {details?.error && <p className="energy-map-stale" role="alert">{details.error}</p>}
          {isAssetLayer(selected.layerId) && <AssetMarketMessages result={messages} />}
        </> : <div className="energy-map-empty"><MapPin size={23} /><h2>Explore a feature</h2><p>Select an asset or area. Market messages appear only when they are linked to the selected asset.</p></div>}
        <div className="energy-map-search"><Search size={15} /><input aria-label="Search loaded map features" value={search} placeholder="Search this viewport" onChange={event => setSearch(event.target.value)} /></div>
        <div className="energy-map-feature-list">
          {listed.map(feature => <button type="button" key={`${feature.layerId}:${feature.id}`} onClick={() => setSelected(feature)}>
            <strong>{feature.label}</strong><small>{MAP_LAYERS.find(layer => layer.id === feature.layerId)?.label}</small>
          </button>)}
          {!listed.length && <p>No matching features loaded in this viewport.</p>}
          {displayed.length > 100 && <small>List shows up to 100 matching loaded features.</small>}
        </div>
      </aside>
    </div>
    <MapChatDrawer open={chatOpen} context={chatContext} onFocus={focusPlace}
      onClose={() => { setChatOpen(false); chatButton.current?.focus() }} />
  </div>
}

function FeatureDetails({ feature, onClose }: { feature: EnergyFeature; onClose: () => void }) {
  const url = safeSourceUrl(feature.sourceUrl)
  const rawFields = ['source_properties', 'gis_properties', 'source_message']
  const fields = Object.entries(feature.properties).filter(([name, value]) =>
    !rawFields.includes(name) && value !== null && value !== undefined && value !== '')
  return <section className="energy-map-inspector">
    <button type="button" className="energy-map-close" aria-label="Close feature details" onClick={onClose}><X size={18} /></button>
    <span className="v2-eyebrow">{MAP_LAYERS.find(layer => layer.id === feature.layerId)?.label}</span>
    <h2>{feature.label}</h2>
    <p className="energy-map-id">{feature.id}</p>
    <p>Observed: {feature.observedAt ? new Date(feature.observedAt).toLocaleString() : 'Not supplied by source'}</p>
    <p>Imported: {new Date(feature.ingestedAt).toLocaleString()}</p>
    {!feature.geometry && <p className="energy-map-stale">No verified map coordinates are available.</p>}
    {feature.layerId === 'transformers' && <p>Capacity: unknown. Marker size is uniform; voltage is not used as a capacity proxy.</p>}
    <dl>{fields.map(([name, value]) => <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
    {url && <a href={url} target="_blank" rel="noreferrer">Source information</a>}
    {rawFields.map(name => feature.properties[name] !== undefined && feature.properties[name] !== null
      ? <details className="energy-map-raw-properties" key={name}><summary>{name === 'gis_properties' ? 'GIS properties' : name === 'source_properties' ? 'Source properties' : 'Source message'}</summary><pre>{JSON.stringify(feature.properties[name], null, 2)}</pre></details> : null)}
  </section>
}

function VisibleCapacityTile({ capacity, pending, error, truncated }: {
  capacity: ReturnType<typeof visiblePlantCapacity>; pending: boolean; error?: string; truncated: boolean
}) {
  const formatted = formatCapacityPower(capacity.totalMw)
  const unavailable = capacity.knownPlants === 0 && capacity.unknownPlants > 0
  return <section className="energy-map-frequency-tile energy-map-capacity-tile" aria-label="Visible plant capacity" aria-busy={pending && !error}>
    <div><Zap size={19} /><h2>Visible plant capacity</h2></div>
    <strong>{error ? 'Unavailable' : pending ? 'Updating...' : unavailable ? 'Unknown' : `${formatted.value} ${formatted.unit}`}</strong>
    <span>{error ? 'Filtered capacity is not available' : pending ? 'Waiting for the filtered map view' : `${capacity.knownPlants + capacity.unknownPlants} hydropower plants in the filtered map view`}</span>
    <small>Installed capacity, not current production. Updates with the viewport, layers and filters.</small>
    {capacity.unknownPlants > 0 && <small className="energy-map-stale">{capacity.unknownPlants} plants have missing or invalid capacity and are excluded from the sum.</small>}
    {capacity.transformers > 0 && <small>Transformer capacities are unknown and are not added.</small>}
    {truncated && <small className="energy-map-stale">Visible subset only: the map feature limit is applied.</small>}
    {error && <small role="alert">Capacity cannot be confirmed until the filtered map loads.</small>}
  </section>
}

function AssetMarketMessages({ result }: { result?: { data?: EnergyFeature[]; error?: string } }) {
  return <section className="energy-map-asset-messages" aria-label="Selected asset market messages">
    <h3>Related market messages</h3>
    {!result && <p role="status">Loading asset links from Fabric...</p>}
    {result?.error && <p className="energy-map-stale" role="alert">{result.error}</p>}
    {result?.data && <>
      {!result.data.length && <p>No linked messages in the imported publication window. Unmatched or ambiguous notices are not assigned to this asset.</p>}
      {result.data.slice(0, 100).map(message => <details key={message.id}>
        <summary>{message.label}</summary>
        <dl>{Object.entries(message.properties).filter(([name, value]) =>
          name !== 'source_message' && value !== null && value !== undefined && value !== '')
          .map(([name, value]) => <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
        {message.properties.source_message !== undefined && <details className="energy-map-raw-properties"><summary>Source message</summary><pre>{JSON.stringify(message.properties.source_message, null, 2)}</pre></details>}
      </details>)}
      {result.data.length > 100 && <p>Showing the latest 100 linked notices.</p>}
    </>}
    <small>UMM covers the last 30 publication days. Matches show their evidence; importance is application-derived, not an official severity rating.</small>
  </section>
}
