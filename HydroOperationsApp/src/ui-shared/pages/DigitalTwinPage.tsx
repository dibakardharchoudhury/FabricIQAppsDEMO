import { lazy, Suspense, useMemo } from 'react'
import { Activity, Box, ExternalLink, Factory, Gauge, Maximize2, Minimize2 } from 'lucide-react'
import type { TwinSignal, TwinStatus } from '../../twin'
import { ageLabel, freshnessOf, twinStatus } from '../../twin'
import { FacilityContext } from '../components/FacilityContext'
import { DigitalTwinTree } from '../components/digitalTwin/DigitalTwinTree'
import { buildDigitalTwinTree, pathToAsset } from '../components/digitalTwin/digitalTwinTreeModel'
import { useDigitalTwinExplorerMode } from '../hooks/useDigitalTwinExplorerMode'
import { useExpandedView } from '../hooks/useExpandedView'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { useTreeExpansion } from '../hooks/useTreeExpansion'

const AssetModelViewer = lazy(() => import('../../components/AssetModelViewer').then(m => ({ default: m.AssetModelViewer })))
const canRenderModel = (format?: string) => Boolean(format && ['GLB', 'GLTF'].includes(format.toUpperCase()))

export function DigitalTwinPage() {
  const data = useHydroOperationsData()
  const { mode } = useDigitalTwinExplorerMode()
  const twinView = useExpandedView()
  const selectedAsset = data.selectedAsset
  const selectedModel = data.assetModels.find(item => item.equipmentId === selectedAsset?.equipment_id)
  const assetInstruments = useMemo(
    () => data.facilityInstruments.filter(item => item.equipment_id === selectedAsset?.equipment_id),
    [data.facilityInstruments, selectedAsset],
  )
  const readings = useMemo(() => new Map(data.facilityTelemetry.map(item => [item.opcuaNodeId, item])), [data.facilityTelemetry])
  const openOrderNodes = useMemo(() => new Set(data.openOrders.map(order => order.opcuaNodeId)), [data.openOrders])
  const treeStations = useMemo(() => data.stid ? buildDigitalTwinTree(data.stid) : [], [data.stid])
  const revealPath = useMemo(() => pathToAsset(treeStations, selectedAsset?.equipment_id), [treeStations, selectedAsset])
  const expansion = useTreeExpansion(revealPath)
  const assetStatuses = useMemo(() => {
    const statuses = new Map<string, TwinStatus>()
    const allReadings = new Map(data.telemetry.map(item => [item.opcuaNodeId, item]))
    const rank: Record<TwinStatus, number> = { nodata: 0, ok: 1, warn: 2, crit: 3 }
    for (const asset of data.stid?.equipment ?? []) statuses.set(asset.equipment_id, 'nodata')
    for (const instrument of data.stid?.instruments ?? []) {
      const reading = allReadings.get(instrument.opcua_node_id)
      const status = twinStatus({
        id: instrument.instrument_id,
        label: instrument.tag ?? instrument.instrument_id,
        nodeId: instrument.opcua_node_id,
        value: reading?.value,
        quality: reading?.quality,
        hasOpenIssue: openOrderNodes.has(instrument.opcua_node_id),
      })
      const current = statuses.get(instrument.equipment_id) ?? 'nodata'
      if (rank[status] > rank[current]) statuses.set(instrument.equipment_id, status)
    }
    return statuses
  }, [data.stid, data.telemetry, openOrderNodes])
  const twinSignals = useMemo<TwinSignal[]>(() => assetInstruments.map(instrument => {
    const reading = readings.get(instrument.opcua_node_id)
    return {
      id: instrument.instrument_id,
      label: instrument.tag ?? instrument.instrument_id,
      nodeId: instrument.opcua_node_id,
      value: reading?.value,
      unit: instrument.unit,
      quality: reading?.quality,
      hasOpenIssue: openOrderNodes.has(instrument.opcua_node_id),
      eventTime: reading?.eventTime,
    }
  }), [assetInstruments, readings, openOrderNodes])
  const twinHealth = useMemo<Record<TwinStatus, number>>(() => {
    const counts = { ok: 0, warn: 0, crit: 0, nodata: 0 }
    for (const signal of twinSignals) counts[twinStatus(signal)]++
    return counts
  }, [twinSignals])
  const eventTimes = twinSignals.map(signal => signal.eventTime ? Date.parse(signal.eventTime) : NaN).filter(time => !Number.isNaN(time)).sort((a, b) => a - b)
  const medianMs = eventTimes.length ? eventTimes[Math.floor((eventTimes.length - 1) / 2)] : 0
  const medianIso = medianMs ? new Date(medianMs).toISOString() : undefined
  const liveState = medianIso && ['live', 'recent'].includes(freshnessOf(medianIso)) ? 'Live telemetry' : medianIso ? 'Stale telemetry' : 'No current telemetry'
  const treeMode = mode === 'tree'
  const blocker = data.stidState !== 'connected'
    ? { title: 'STID not connected', text: 'Use Administration to connect STID before viewing the Digital Twin.' }
    : !(treeMode ? data.stid?.equipment.length : data.facilityEquipment.length)
      ? treeMode
        ? { title: 'No assets available', text: 'STID has no equipment records to display in the asset tree.' }
        : { title: 'No assets for this facility', text: 'The selected facility has no STID equipment records.' }
      : undefined

  const detail = <>
    <section className="v2-twin-summary">
      <div><span className="v2-eyebrow">Selected Asset</span><h1>{selectedAsset?.tag ?? selectedAsset?.equipment_id}</h1><p>{selectedAsset ? `${selectedAsset.equipment_id} · ${selectedAsset.equipment_type_name ?? 'Equipment'} · ${selectedAsset.status ?? 'Status unavailable'}` : 'Select an asset'}</p></div>
      <TwinMetric icon={Factory} label="Manufacturer / model" value={[selectedAsset?.manufacturer, selectedAsset?.model].filter(Boolean).join(' / ') || 'Unavailable'} />
      <TwinMetric icon={Activity} label="Live signals" value={String(twinSignals.filter(signal => signal.value !== undefined && signal.value !== null).length)} detail={`${assetInstruments.length} mapped instruments`} />
      <TwinMetric icon={Gauge} label="Current status" value={liveState} detail={medianIso ? `Median update ${ageLabel(medianIso)}` : 'No Eventhouse reading'} tone={liveState === 'Live telemetry' ? 'good' : medianIso ? 'warn' : 'muted'} />
    </section>

    <section className={`v2-twin-panel${twinView.expanded ? ' v2-expanded-view' : ''}`}>
      <div className="v2-panel-headline v2-panel-headline-action"><div><span className="v2-eyebrow">Digital Twin</span><h2>{selectedAsset?.tag ?? 'Asset model'}</h2></div><button className="v2-icon-action" type="button" title={twinView.expanded ? 'Restore digital twin view' : 'Expand digital twin view'} aria-label={twinView.expanded ? 'Restore digital twin view' : 'Expand digital twin view'} aria-pressed={twinView.expanded} onClick={twinView.toggleExpanded}>{twinView.expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button></div>
      {data.modelState !== 'connected' ? <EmptyTwin title="Model metadata unavailable" text="Sign in through Administration to load Rayfin 3D model metadata." compact />
        : !selectedModel ? <EmptyTwin title="No 3D model for selected asset" text="No Asset3DModel record matches this asset's equipment ID." compact />
          : !canRenderModel(selectedModel.format) ? <ModelFallback model={selectedModel} />
            : !assetInstruments.length ? <EmptyTwin title="No instruments for selected asset" text="This asset has no STID instruments to render as live hotspots." compact />
              : <>
                <Suspense fallback={<div className="twin-stage"><div className="twin-loading">Loading 3D model...</div></div>}>
                  <AssetModelViewer key={selectedModel.modelUrl} model={selectedModel} signals={twinSignals} assetLabel={selectedAsset?.tag ?? selectedAsset?.equipment_id} />
                </Suspense>
                <TwinLegend counts={twinHealth} />
                <div className="v2-twin-model-meta"><strong>{selectedModel.modelName}</strong><small>{selectedModel.format}{selectedModel.version ? ` · ${selectedModel.version}` : ''}{selectedModel.fileSizeMb ? ` · ${selectedModel.fileSizeMb} MB` : ''}</small><a href={selectedModel.modelUrl} target="_blank" rel="noreferrer"><ExternalLink size={13} />Open model</a></div>
              </>}
    </section>
  </>

  return <div className={`v2-domain-page v2-digital-twin-page${treeMode ? ' is-wide' : ''}`}>
    {!treeMode && <FacilityContext />}

    {blocker ? <EmptyTwin title={blocker.title} text={blocker.text} />
      : treeMode
        ? <div className="v2-twin-layout">
            <DigitalTwinTree
              stations={treeStations}
              selectedAssetId={selectedAsset?.equipment_id}
              handlers={{
                isExpanded: expansion.isExpanded,
                onToggle: expansion.toggle,
                onSelectAsset: data.actions.selectAsset,
                statusOf: assetId => assetStatuses.get(assetId) ?? 'nodata',
              }}
            />
            <div className="v2-twin-detail-column">{detail}</div>
          </div>
        : detail}
  </div>
}

function TwinMetric({ icon: Icon, label, value, detail, tone = 'muted' }: { icon: typeof Factory; label: string; value: string; detail?: string; tone?: 'good' | 'warn' | 'muted' }) {
  return <article className={`v2-signal-metric ${tone}`}><span><Icon size={16} /></span><div><small>{label}</small><strong>{value}</strong>{detail && <em>{detail}</em>}</div></article>
}

function TwinLegend({ counts }: { counts: Record<TwinStatus, number> }) {
  return <div className="v2-twin-health"><span><i className="ok" />OK {counts.ok}</span><span><i className="warn" />UNCERTAIN {counts.warn}</span><span><i className="crit" />BAD / open work order {counts.crit}</span><span><i className="nodata" />No data {counts.nodata}</span></div>
}

function ModelFallback({ model }: { model: { modelName: string; format: string; modelUrl: string; thumbnailUrl?: string; version?: string; fileSizeMb?: number } }) {
  return <div className="v2-twin-fallback"><div className="twin-thumb">{model.thumbnailUrl ? <img src={model.thumbnailUrl} alt={model.modelName} /> : <Box size={40} />}</div><div className="twin-meta"><strong>{model.modelName}</strong><small>{model.format}{model.version ? ` · ${model.version}` : ''}{model.fileSizeMb ? ` · ${model.fileSizeMb} MB` : ''}</small><a href={model.modelUrl} target="_blank" rel="noreferrer">Open model</a></div></div>
}

function EmptyTwin({ title, text, compact }: { title: string; text: string; compact?: boolean }) {
  return <section className={compact ? 'v2-twin-empty compact' : 'v2-placeholder-card'}><span className="v2-eyebrow">Digital Twin</span><h1>{title}</h1><p className="v2-empty-copy">{text}</p></section>
}