import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Bot, Box, ClipboardCheck, Database, Factory, Gauge, MapPin, Package, Play, Plus, Radio, Send, Wrench, X } from 'lucide-react'
import './App.css'
import { FacilityMap } from './components/FacilityMap'
import { askDataAgent, beginInteractiveConnect, initAuth, isStidConfigured, queryLatestTelemetry, queryStid, startStreamingPipeline, type StidData, type TelemetryReading } from './services/fabric'
import {
  createWorkOrder, initializeRayfin, isRayfinConfigured, listAsset3DModels, listInspections,
  listMaintenanceNotifications, listSpareParts, listWorkOrders, seedOperationalDataIfEmpty, signInToRayfin,
  type AppUser, type Asset3DModelRecord, type InspectionRecord, type MaintenanceNotificationRecord,
  type SparePartRecord, type WorkOrderRecord,
} from './services/rayfin'

const openStatuses = new Set(['draft', 'approved', 'planned', 'scheduled', 'ready', 'in progress', 'in_progress', 'on hold', 'on_hold'])
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Unknown error'

export default function App() {
  const [user, setUser] = useState<AppUser | null>(null)
  const [orders, setOrders] = useState<WorkOrderRecord[]>([])
  const [inspections, setInspections] = useState<InspectionRecord[]>([])
  const [spareParts, setSpareParts] = useState<SparePartRecord[]>([])
  const [models, setModels] = useState<Asset3DModelRecord[]>([])
  const [notifications, setNotifications] = useState<MaintenanceNotificationRecord[]>([])
  const [stid, setStid] = useState<StidData | null>(null)
  const [telemetry, setTelemetry] = useState<TelemetryReading[]>([])
  const [selectedFacilityId, setSelectedFacilityId] = useState<string>()
  const [selectedId, setSelectedId] = useState<string>()
  const [sourceState, setSourceState] = useState('Connect STID')
  const [telemetryState, setTelemetryState] = useState('Connect telemetry')
  const [streamState, setStreamState] = useState<'idle' | 'starting' | 'started' | 'error'>('idle')
  const [notice, setNotice] = useState<string>()
  const [copilotOpen, setCopilotOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [messages, setMessages] = useState([{ role: 'agent', text: 'The Data Agent is preview-only until a published MCP endpoint is configured.' }])

  const facilities = useMemo(() => stid?.facilities ?? [], [stid])
  const facility = facilities.find(item => item.facility_id === selectedFacilityId) ?? facilities[0]
  const equipment = useMemo(
    () => (stid?.equipment ?? []).filter(asset => !facility || asset.facility_id === facility.facility_id),
    [stid, facility],
  )
  const selected = equipment.find(asset => asset.equipment_id === selectedId) ?? equipment[0]
  const instruments = useMemo(() => stid?.instruments.filter(item => item.equipment_id === selected?.equipment_id) ?? [], [stid, selected])
  const facilityInstruments = useMemo(() => stid?.instruments.filter(item => !facility || item.facility_id === facility.facility_id) ?? [], [stid, facility])
  const readings = useMemo(() => new Map(telemetry.map(item => [item.opcuaNodeId, item])), [telemetry])
  const openOrders = orders.filter(order => openStatuses.has(order.status.toLowerCase()))
  const selectedOrders = openOrders.filter(order => order.equipmentId === selected?.equipment_id)
  const selectedInspections = inspections.filter(item => item.equipmentId === selected?.equipment_id)
  const selectedModel = models.find(item => item.equipmentId === selected?.equipment_id)
  const selectedNotifications = notifications.filter(item => item.equipmentId === selected?.equipment_id)
  const lowStockParts = spareParts.filter(part => part.quantityOnHand <= part.reorderLevel)

  useEffect(() => {
    const initialize = async () => {
      if (isRayfinConfigured()) {
        try {
          const current = await initializeRayfin()
          setUser(current)
          if (current) await loadOperationalData()
        } catch (error) { setNotice(`Operations data: ${errorMessage(error)}`) }
      }
      const pending = await initAuth()
      if (isStidConfigured()) {
        try {
          const data = await queryStid()
          if (data) { applyStid(data); setSourceState('STID connected') }
        } catch (error) { setNotice(`STID: ${errorMessage(error)}`) }
      }
      try {
        const data = await queryLatestTelemetry()
        if (data) { setTelemetry(data); setTelemetryState(data.length ? `${data.length} signals` : 'No recent events') }
      } catch (error) { setNotice(`Telemetry: ${errorMessage(error)}`) }
      if (pending === 'stid') void connectStid()
      else if (pending === 'telemetry') void connectTelemetry()
      else if (pending === 'stream') void startStream()
    }
    void initialize()
  }, [])

  function applyStid(data: StidData) {
    setStid(data)
    const firstFacility = data.facilities[0]?.facility_id
    setSelectedFacilityId(firstFacility)
    setSelectedId(data.equipment.find(asset => asset.facility_id === firstFacility)?.equipment_id ?? data.equipment[0]?.equipment_id)
  }

  function selectFacility(facilityId: string) {
    setSelectedFacilityId(facilityId)
    setSelectedId(stid?.equipment.find(asset => asset.facility_id === facilityId)?.equipment_id)
  }

  async function loadOperationalData() {
    // Each entity loads independently so a not-yet-migrated table never blocks the others.
    const [wo, ins, sp, md, mn] = await Promise.allSettled([
      listWorkOrders(), listInspections(), listSpareParts(), listAsset3DModels(), listMaintenanceNotifications(),
    ])
    if (wo.status === 'fulfilled') setOrders(wo.value)
    if (ins.status === 'fulfilled') setInspections(ins.value)
    if (sp.status === 'fulfilled') setSpareParts(sp.value)
    if (md.status === 'fulfilled') setModels(md.value)
    if (mn.status === 'fulfilled') setNotifications(mn.value)
  }

  async function connectStid() {
    setSourceState('Connecting...'); setNotice(undefined)
    try {
      const data = await queryStid()
      if (!data) { await beginInteractiveConnect('stid'); return }
      applyStid(data); setSourceState('STID connected')
    } catch (error) { setSourceState('Connect STID'); setNotice(errorMessage(error)) }
  }

  async function connectTelemetry() {
    setTelemetryState('Connecting...'); setNotice(undefined)
    try {
      const data = await queryLatestTelemetry()
      if (data === null) { await beginInteractiveConnect('telemetry'); return }
      setTelemetry(data); setTelemetryState(data.length ? `${data.length} signals` : 'No recent events')
    } catch (error) { setTelemetryState('Connect telemetry'); setNotice(errorMessage(error)) }
  }

  async function authenticate() {
    try {
      const current = await signInToRayfin()
      setUser(current); await loadOperationalData()
    } catch (error) { setNotice(errorMessage(error)) }
  }

  async function seedDemo() {
    if (seeding) return
    if (!user) { await authenticate(); return }
    setSeeding(true); setNotice(undefined)
    try {
      const result = await seedOperationalDataIfEmpty(user)
      const created = result.filter(item => !item.skipped)
      await loadOperationalData()
      setNotice(created.length
        ? `Seeded ${created.map(item => `${item.created} ${item.entity}`).join(', ')}.`
        : 'Operational data already present — nothing to seed.')
    } catch (error) { setNotice(errorMessage(error)) }
    finally { setSeeding(false) }
  }

  async function addWorkOrder() {
    if (!selected) return
    if (!user) { await authenticate(); return }
    const instrument = instruments[0]
    try {
      const record = await createWorkOrder(user, selected.equipment_id, instrument?.instrument_id, instrument?.opcua_node_id)
      setOrders(current => [record, ...current])
    } catch (error) { setNotice(errorMessage(error)) }
  }

  async function startStream() {
    setStreamState('starting'); setNotice(undefined)
    try { await startStreamingPipeline(); setStreamState('started'); await connectTelemetry() }
    catch (error) { setStreamState('error'); setNotice(errorMessage(error)) }
  }

  async function sendQuestion() {
    const text = question.trim(); if (!text || busy) return
    setQuestion(''); setBusy(true); setMessages(current => [...current, { role: 'user', text }])
    try {
      const answer = await askDataAgent(text)
      setMessages(current => [...current, { role: 'agent', text: answer }])
    }
    catch (error) { setMessages(current => [...current, { role: 'agent', text: errorMessage(error) }]) }
    setBusy(false)
  }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark"><Factory size={18} /></span><div><strong>Hydro Operations</strong><small>Microsoft Fabric</small></div></div>
      <div className="source-actions">
        <button className={stid ? 'source-chip connected' : 'source-chip'} onClick={() => void connectStid()}><Database size={14} />{sourceState}</button>
        <button className={telemetry.length ? 'source-chip connected' : 'source-chip'} onClick={() => void connectTelemetry()}><Radio size={14} />{telemetryState}</button>
      </div>
      <div className="top-actions"><button className="stream-button" onClick={() => void startStream()} disabled={streamState === 'starting'}><Play size={14} fill="currentColor" />{streamState === 'starting' ? 'Starting' : streamState === 'started' ? 'Stream started' : 'Start stream'}</button><button className="seed-button" onClick={() => void seedDemo()} disabled={seeding} title="Populate the SQL database with demo operational data (only when empty)"><Database size={14} />{seeding ? 'Seeding…' : 'Seed demo data'}</button><button className="avatar" onClick={() => void authenticate()} title={user?.email ?? 'Connect operational data'}>{user?.name.slice(0, 2).toUpperCase() ?? 'ID'}</button></div>
    </header>

    <main>
      {notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice(undefined)}><X size={15} /></button></div>}
      <section className="page-head"><div><span className="eyebrow">FACILITY OPERATIONS</span><h1>{facility?.facility_name ?? 'Hydropower operations'}</h1><p>{facility ? `${facility.facility_id} · ${facility.type ?? 'Facility'} · ${facility.country ?? 'Location unavailable'}` : 'Connect STID to load governed facility and asset metadata.'}</p></div><button className="copilot-button" onClick={() => setCopilotOpen(true)}><Bot size={16} /> Copilot</button></section>

      {facilities.length > 1 && <section className="facility-strip">{facilities.map(item => <button key={item.facility_id} className={item.facility_id === facility?.facility_id ? 'facility-chip active' : 'facility-chip'} onClick={() => selectFacility(item.facility_id)}><Factory size={14} /><span><strong>{item.facility_name}</strong><small>{item.facility_id}</small></span></button>)}</section>}

      <section className="metrics">
        <div><MapPin size={17} /><span>Facilities<strong>{stid ? facilities.length : '—'}</strong><small>Lakehouse STID</small></span></div>
        <div><Factory size={17} /><span>Assets<strong>{stid ? equipment.length : '—'}</strong><small>This facility</small></span></div>
        <div><Gauge size={17} /><span>Instruments<strong>{stid ? facilityInstruments.length : '—'}</strong><small>Mapped OPC UA nodes</small></span></div>
        <div><Activity size={17} /><span>Recent signals<strong>{telemetry.length || '—'}</strong><small>Eventhouse · latest</small></span></div>
        <div><Wrench size={17} /><span>Open work orders<strong>{user ? openOrders.length : '—'}</strong><small>Rayfin SQL</small></span></div>
      </section>

      <div className="workspace-grid">
        <section className="map-panel panel"><div className="panel-head"><div><h2>Facility network</h2><p>{facilities.length > 1 ? `${facilities.length} facilities from silver_facilities` : 'Facility coordinates from silver_facilities'}</p></div><MapPin size={18} /></div>{facilities.length ? <FacilityMap facilities={facilities} selectedId={facility?.facility_id} onSelect={selectFacility} /> : <EmptyState title="No facility loaded" action="Connect STID" onClick={() => void connectStid()} />}</section>
        <section className="assets-panel panel"><div className="panel-head"><div><h2>Asset registry</h2><p>{stid ? `${equipment.length} equipment records` : 'Authoritative STID source'}</p></div></div><div className="asset-list">{equipment.map(asset => <button key={asset.equipment_id} className={selected?.equipment_id === asset.equipment_id ? 'asset-row selected' : 'asset-row'} onClick={() => setSelectedId(asset.equipment_id)}><span className="asset-index">{asset.tag?.replace(/\D/g, '').padStart(2, '0') || '—'}</span><span><strong>{asset.tag ?? asset.equipment_id}</strong><small>{asset.manufacturer ?? 'Manufacturer unavailable'} · {asset.model ?? 'Model unavailable'}</small></span><em>{asset.status ?? 'Unknown'}</em></button>)}{!equipment.length && <EmptyState title="No assets loaded" action="Connect STID" onClick={() => void connectStid()} />}</div></section>
      </div>

      <div className="detail-grid">
        <section className="signals-panel panel"><div className="panel-head"><div><h2>{selected?.tag ?? 'Asset signals'}</h2><p>{selected ? `${selected.equipment_id} · ${selected.equipment_type_name ?? 'Equipment'}` : 'Select an asset'}</p></div><span className="provenance">STID + Eventhouse</span></div><div className="signal-table"><div className="table-head"><span>Signal</span><span>Latest value</span><span>Quality</span></div>{instruments.map(instrument => { const reading = readings.get(instrument.opcua_node_id); return <div className="signal-row" key={instrument.instrument_id}><span><strong>{instrument.tag ?? instrument.instrument_id}</strong><small>{instrument.opcua_node_id}</small></span><span>{reading ? <>{reading.value}<small>{instrument.unit ? ` ${instrument.unit}` : ''}</small></> : 'No event'}</span><em className={reading?.quality?.toLowerCase() === 'good' ? 'good' : ''}>{reading?.quality ?? '—'}</em></div>})}{selected && !instruments.length && <div className="inline-empty">No instruments are mapped to this asset.</div>}</div></section>
        <section className="orders-panel panel"><div className="panel-head"><div><h2>Work orders</h2><p>{selected ? `${selectedOrders.length} open for this asset` : 'Rayfin operational SQL'}</p></div><button className="icon-button" title="Create work order" onClick={() => void addWorkOrder()} disabled={!selected}><Plus size={18} /></button></div><div className="order-list">{selectedOrders.map(order => <article className="order" key={order.id}><span className={`priority ${order.priority.toLowerCase()}`}><Wrench size={14} /></span><div><strong>{order.title}</strong><small>{order.workOrderNumber}</small><p>{order.status} · {order.priority}</p></div></article>)}{!user && <EmptyState title="Operational records are protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !selectedOrders.length && <div className="inline-empty">No open work orders for this asset.</div>}</div></section>
      </div>

      <div className="detail-grid">
        <section className="twin-panel panel"><div className="panel-head"><div><h2>Digital twin</h2><p>{selected ? `3D model for ${selected.tag ?? selected.equipment_id}` : 'Asset 3D model'}</p></div><span className="provenance">Rayfin SQL</span></div><div className="twin-body">{!user ? <EmptyState title="3D models are protected" action="Connect operations" onClick={() => void authenticate()} /> : selectedModel ? <><div className="twin-thumb">{selectedModel.thumbnailUrl ? <img src={selectedModel.thumbnailUrl} alt={selectedModel.modelName} /> : <Box size={40} />}</div><div className="twin-meta"><strong>{selectedModel.modelName}</strong><small>{selectedModel.format}{selectedModel.version ? ` · ${selectedModel.version}` : ''}{selectedModel.fileSizeMb ? ` · ${selectedModel.fileSizeMb} MB` : ''}</small><a href={selectedModel.modelUrl} target="_blank" rel="noreferrer">Open model</a></div></> : <div className="inline-empty">No 3D model registered for this asset.</div>}</div></section>
        <section className="inspections-panel panel"><div className="panel-head"><div><h2>Inspections</h2><p>{selected ? `${selectedInspections.length} record(s) for this asset` : 'Condition inspections'}</p></div><ClipboardCheck size={18} /></div><div className="order-list">{selectedInspections.map(item => <article className="order" key={item.id}><span className={`insp-result ${item.result.toLowerCase()}`}><ClipboardCheck size={14} /></span><div><strong>{item.inspectionType}</strong><small>{new Date(item.inspectedAt).toLocaleDateString()} · {item.result}</small><p>{item.findings ?? 'No findings recorded.'}</p></div></article>)}{!user && <EmptyState title="Inspection records are protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !selectedInspections.length && <div className="inline-empty">No inspections for this asset.</div>}</div></section>
      </div>

      <div className="detail-grid">
        <section className="spares-panel panel"><div className="panel-head"><div><h2>Spare parts inventory</h2><p>{user ? `${spareParts.length} SKUs · ${lowStockParts.length} below reorder` : 'Maintenance readiness'}</p></div><Package size={18} /></div><div className="spares-table">{spareParts.map(part => { const low = part.quantityOnHand <= part.reorderLevel; return <div className={low ? 'spare-row low' : 'spare-row'} key={part.id}><span><strong>{part.name}</strong><small>{part.partNumber} · {part.category}</small></span><span className="spare-loc">{part.storageLocation}</span><em>{part.quantityOnHand}{low && <AlertTriangle size={13} />}<small>/ {part.reorderLevel}</small></em></div>})}{!user && <EmptyState title="Inventory is protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !spareParts.length && <div className="inline-empty">No spare parts in inventory.</div>}</div></section>
        <section className="notifications-panel panel"><div className="panel-head"><div><h2>Maintenance notifications</h2><p>{selected ? `${selectedNotifications.length} for this asset` : 'Operational alerts'}</p></div><AlertTriangle size={18} /></div><div className="order-list">{selectedNotifications.map(item => <article className="order" key={item.id}><span className={`priority ${item.severity.toLowerCase()}`}><AlertTriangle size={14} /></span><div><strong>{item.summary}</strong><small>{new Date(item.reportedAt).toLocaleDateString()} · {item.status}</small><p>{item.severity}</p></div></article>)}{!user && <EmptyState title="Notifications are protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !selectedNotifications.length && <div className="inline-empty">No notifications for this asset.</div>}</div></section>
      </div>
    </main>

    {copilotOpen && <aside className="copilot-panel"><div className="copilot-head"><span className="copilot-icon"><Bot size={18} /></span><div><strong>Operations Copilot</strong><small>Data Agent preview</small></div><button className="icon-button" onClick={() => setCopilotOpen(false)} title="Close"><X size={18} /></button></div><div className="messages">{messages.map((item, index) => <div className={`message ${item.role}`} key={index}><p>{item.text}</p></div>)}{busy && <small>Waiting for Data Agent...</small>}</div><div className="prompt-box"><textarea value={question} onChange={event => setQuestion(event.target.value)} placeholder="Ask about connected Fabric data" /><button onClick={() => void sendQuestion()} title="Send"><Send size={16} /></button></div></aside>}
  </div>
}

function EmptyState({ title, action, onClick }: { title: string; action: string; onClick: () => void }) {
  return <div className="empty-state"><Database size={20} /><p>{title}</p><button onClick={onClick}>{action}</button></div>
}