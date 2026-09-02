import { useMemo, useState } from 'react'
import { Activity, AlertTriangle, Factory, Gauge, MapPin, Plus, Radio, Wrench } from 'lucide-react'
import { FacilityMap, type FacilityStat } from '../../components/FacilityMap'
import { twinStatus, type TwinStatus } from '../../twin'
import { FacilityContext } from '../components/FacilityContext'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function OverviewPage() {
  const data = useHydroOperationsData()
  const [selectedAssetId, setSelectedAssetId] = useState<string>()
  const facility = data.selectedFacility
  const telemetryTone = data.telemetryStatus === 'live' ? 'good' : data.telemetryStatus === 'delayed' ? 'warn' : data.telemetryStatus === 'stale' ? 'bad' : 'muted'
  const qualityTone = data.counts.qualityIssues ? 'warn' : data.counts.liveSignals ? 'good' : 'muted'
  const issueSignals = data.mappedTelemetry.filter(item => ['bad', 'uncertain'].includes((item.reading.quality ?? '').toLowerCase()))
  const telemetryByNode = useMemo(() => new Map(data.telemetry.map(reading => [reading.opcuaNodeId, reading])), [data.telemetry])
  const facilityStats = useMemo<FacilityStat[]>(() => (data.stid?.facilities ?? []).map(item => {
    const equipment = data.stid?.equipment.filter(asset => asset.facility_id === item.facility_id) ?? []
    const instruments = data.stid?.instruments.filter(instrument => instrument.facility_id === item.facility_id) ?? []
    const equipmentIds = new Set(equipment.map(asset => asset.equipment_id))
    const health = { ok: 0, warn: 0, crit: 0, nodata: 0 }
    for (const instrument of instruments) {
      const reading = telemetryByNode.get(instrument.opcua_node_id)
      health[twinStatus({ id: instrument.instrument_id, label: instrument.tag ?? instrument.instrument_id, nodeId: instrument.opcua_node_id, value: reading?.value, quality: reading?.quality, hasOpenIssue: data.openOrders.some(order => order.opcuaNodeId === instrument.opcua_node_id) })]++
    }
    const worst: TwinStatus = health.crit ? 'crit' : health.warn ? 'warn' : health.ok ? 'ok' : 'nodata'
    return { ...item, lat: Number(item.lat), lon: Number(item.lon), assetCount: equipment.length, instrumentCount: instruments.length, openOrders: data.openOrders.filter(order => equipmentIds.has(order.equipmentId)).length, health, worst }
  }), [data.openOrders, data.stid, telemetryByNode])

  return <div className="v2-overview">
    {data.notice && <div className="v2-notice"><AlertTriangle size={15} /><span>{data.notice}</span></div>}

    <section className="v2-facility-banner">
      <div>
        <span className="v2-eyebrow">Facility Overview</span>
        <h1>{facility?.facility_name ?? 'Hydropower operations'}</h1>
        <p>{facility ? `${facility.facility_id} · ${facility.type ?? 'Facility'} · ${facility.country ?? 'Location unavailable'}` : 'STID metadata is not connected yet.'}</p>
      </div>
    </section>

    <FacilityContext />

    <section className="v2-summary-grid" aria-label="Facility summary">
      <SummaryCard icon={MapPin} label="Facilities" value={data.stidState === 'connected' ? data.counts.facilities : '—'} detail="Lakehouse STID" />
      <SummaryCard icon={Factory} label="Assets" value={data.stidState === 'connected' ? data.counts.assets : '—'} detail="Selected facility" />
      <SummaryCard icon={Gauge} label="Instruments" value={data.stidState === 'connected' ? data.counts.instruments : '—'} detail="Mapped OPC UA nodes" />
      <SummaryCard icon={Activity} label="Live signals" value={data.counts.liveSignals || '—'} detail="Eventhouse · 24h" />
      <SummaryCard icon={Wrench} label="Open work orders" value={data.operationsState === 'connected' ? data.counts.openWorkOrders : '—'} detail="Rayfin SQL" />
    </section>

    <section className="v2-health-panel">
      <div className="v2-panel-headline">
        <span className="v2-eyebrow">Operational Health</span>
        <h2>{data.telemetryStatus === 'live' && !data.counts.qualityIssues ? 'Operational data is healthy' : 'Operational data needs attention'}</h2>
      </div>
      <div className="v2-health-grid">
        <HealthItem tone={telemetryTone} icon={Radio} label="Telemetry freshness" value={data.telemetryStatusLabel} detail={data.telemetryAgeLabel ? `Median event age ${data.telemetryAgeLabel}` : 'No recent telemetry loaded'} />
        <HealthItem tone={qualityTone} icon={AlertTriangle} label="BAD / UNCERTAIN quality" value={String(data.counts.qualityIssues)} detail={`${data.counts.liveSignals || 0} signal${data.counts.liveSignals === 1 ? '' : 's'} in selected facility`} />
        <HealthItem tone={data.counts.openWorkOrders ? 'warn' : data.operationsState === 'connected' ? 'good' : 'muted'} icon={Wrench} label="Open maintenance work" value={data.operationsState === 'connected' ? String(data.counts.openWorkOrders) : 'Not connected'} detail="Work orders scoped to this facility" />
      </div>
    </section>

    <section className="v2-overview-workspace">
      <article className="v2-data-panel v2-map-panel"><div className="v2-panel-headline"><span className="v2-eyebrow">Facility Network</span><h2>{facilityStats.length} governed site{facilityStats.length === 1 ? '' : 's'}</h2></div>{facilityStats.length ? <FacilityMap facilities={facilityStats} selectedId={facility?.facility_id} onSelect={data.setSelectedFacilityId} /> : <div className="v2-inline-empty">Connect STID to load facility coordinates.</div>}</article>
      <article className="v2-data-panel"><div className="v2-panel-headline"><span className="v2-eyebrow">Asset Registry</span><h2>{data.facilityEquipment.length} assets</h2></div><div className="v2-asset-list">{data.facilityEquipment.map(asset => <button type="button" className={selectedAssetId === asset.equipment_id ? 'active' : ''} key={asset.equipment_id} onClick={() => setSelectedAssetId(asset.equipment_id)}><span>{asset.tag?.replace(/\D/g, '').padStart(2, '0') || '—'}</span><span><strong>{asset.tag ?? asset.equipment_id}</strong><small>{asset.manufacturer ?? 'Manufacturer unavailable'} · {asset.model ?? 'Model unavailable'}</small></span><em>{asset.status ?? 'Unknown'}</em></button>)}{!data.facilityEquipment.length && <div className="v2-inline-empty">No assets loaded for this facility.</div>}</div></article>
    </section>

    {issueSignals.length > 0 && <section className="v2-data-panel v2-alert-panel"><div className="v2-panel-headline"><span className="v2-eyebrow">Quality Alerts</span><h2>{issueSignals.length} signals need attention</h2></div><div className="v2-alert-list">{issueSignals.slice(0, 8).map(item => {
      const equipment = data.facilityEquipment.find(asset => asset.equipment_id === item.instrument?.equipment_id)
      const hasOrder = data.openOrders.some(order => order.opcuaNodeId === item.reading.opcuaNodeId)
      return <div key={item.reading.opcuaNodeId}><span className={`v2-quality ${item.reading.quality.toLowerCase()}`}>{item.reading.quality}</span><span><strong>{item.instrument?.tag ?? item.reading.opcuaNodeId}</strong><small>{equipment?.tag ?? item.instrument?.equipment_id ?? 'Unmapped asset'} · {item.reading.value}{item.instrument?.unit ? ` ${item.instrument.unit}` : ''}</small></span><span>{new Date(item.reading.eventTime).toLocaleString()}</span><button type="button" disabled={hasOrder || data.mutationKey === item.reading.opcuaNodeId || !item.instrument?.equipment_id} onClick={() => item.instrument?.equipment_id && void data.actions.addWorkOrder(item.instrument.equipment_id, item.instrument.instrument_id, item.reading.opcuaNodeId)}>{hasOrder ? 'WO open' : <><Plus size={13} />Work order</>}</button></div>
    })}</div></section>}
  </div>
}

function SummaryCard({ icon: Icon, label, value, detail }: { icon: typeof MapPin; label: string; value: string | number; detail: string }) {
  return <article className="v2-summary-card">
    <Icon size={18} />
    <span>{label}</span>
    <strong>{value}</strong>
    <small>{detail}</small>
  </article>
}

function HealthItem({ tone, icon: Icon, label, value, detail }: { tone: 'good' | 'warn' | 'bad' | 'muted'; icon: typeof Radio; label: string; value: string; detail: string }) {
  return <article className={`v2-health-item ${tone}`}>
    <span className="v2-health-icon"><Icon size={17} /></span>
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  </article>
}