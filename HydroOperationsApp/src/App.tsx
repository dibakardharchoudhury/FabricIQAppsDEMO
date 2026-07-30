import { useEffect, useMemo, useState } from 'react'
import { Activity, Bot, Database, Factory, Gauge, MapPin, Play, Plus, Radio, Send, Wrench, X } from 'lucide-react'
import './App.css'
import { FacilityMap } from './components/FacilityMap'
import { askDataAgent, isStidConfigured, queryLatestTelemetry, queryStid, startStreamingPipeline, type StidData, type TelemetryReading } from './services/fabric'
import { createWorkOrder, initializeRayfin, isRayfinConfigured, listWorkOrders, signInToRayfin, type AppUser, type WorkOrderRecord } from './services/rayfin'

const openStatuses = new Set(['draft', 'approved', 'planned', 'scheduled', 'ready', 'in progress', 'in_progress', 'on hold', 'on_hold'])
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Unknown error'

export default function App() {
  const [user, setUser] = useState<AppUser | null>(null)
  const [orders, setOrders] = useState<WorkOrderRecord[]>([])
  const [stid, setStid] = useState<StidData | null>(null)
  const [telemetry, setTelemetry] = useState<TelemetryReading[]>([])
  const [selectedId, setSelectedId] = useState<string>()
  const [sourceState, setSourceState] = useState('Connect STID')
  const [telemetryState, setTelemetryState] = useState('Connect telemetry')
  const [streamState, setStreamState] = useState<'idle' | 'starting' | 'started' | 'error'>('idle')
  const [notice, setNotice] = useState<string>()
  const [copilotOpen, setCopilotOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [messages, setMessages] = useState([{ role: 'agent', text: 'The Data Agent is preview-only until a published MCP endpoint is configured.' }])

  const equipment = stid?.equipment ?? []
  const facility = stid?.facilities[0]
  const selected = equipment.find(asset => asset.equipment_id === selectedId) ?? equipment[0]
  const instruments = useMemo(() => stid?.instruments.filter(item => item.equipment_id === selected?.equipment_id) ?? [], [stid, selected])
  const readings = useMemo(() => new Map(telemetry.map(item => [item.opcuaNodeId, item])), [telemetry])
  const openOrders = orders.filter(order => openStatuses.has(order.status.toLowerCase()))
  const selectedOrders = openOrders.filter(order => order.equipmentId === selected?.equipment_id)

  useEffect(() => {
    const initialize = async () => {
      if (isRayfinConfigured()) {
        try {
          const current = await initializeRayfin()
          setUser(current)
          if (current) setOrders(await listWorkOrders())
        } catch (error) { setNotice(`Operations data: ${errorMessage(error)}`) }
      }
      if (isStidConfigured()) {
        try {
          const data = await queryStid(false)
          if (data) { setStid(data); setSelectedId(data.equipment[0]?.equipment_id); setSourceState('STID connected') }
        } catch (error) { setNotice(`STID: ${errorMessage(error)}`) }
      }
      try {
        const data = await queryLatestTelemetry(false)
        if (data) { setTelemetry(data); setTelemetryState(data.length ? `${data.length} signals` : 'No recent events') }
      } catch (error) { setNotice(`Telemetry: ${errorMessage(error)}`) }
    }
    void initialize()
  }, [])

  async function connectStid() {
    setSourceState('Connecting...'); setNotice(undefined)
    try {
      const data = await queryStid(true)
      if (!data) return
      setStid(data); setSelectedId(data.equipment[0]?.equipment_id); setSourceState('STID connected')
    } catch (error) { setSourceState('Connect STID'); setNotice(errorMessage(error)) }
  }

  async function connectTelemetry() {
    setTelemetryState('Connecting...'); setNotice(undefined)
    try {
      const data = await queryLatestTelemetry(true) ?? []
      setTelemetry(data); setTelemetryState(data.length ? `${data.length} signals` : 'No recent events')
    } catch (error) { setTelemetryState('Connect telemetry'); setNotice(errorMessage(error)) }
  }

  async function authenticate() {
    try {
      const current = await signInToRayfin()
      setUser(current); setOrders(await listWorkOrders())
    } catch (error) { setNotice(errorMessage(error)) }
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
      <div className="top-actions"><button className="stream-button" onClick={() => void startStream()} disabled={streamState === 'starting'}><Play size={14} fill="currentColor" />{streamState === 'starting' ? 'Starting' : streamState === 'started' ? 'Stream started' : 'Start stream'}</button><button className="avatar" onClick={() => void authenticate()} title={user?.email ?? 'Connect operational data'}>{user?.name.slice(0, 2).toUpperCase() ?? 'ID'}</button></div>
    </header>

    <main>
      {notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice(undefined)}><X size={15} /></button></div>}
      <section className="page-head"><div><span className="eyebrow">FACILITY OPERATIONS</span><h1>{facility?.facility_name ?? 'Hydropower operations'}</h1><p>{facility ? `${facility.facility_id} · ${facility.type ?? 'Facility'} · ${facility.country ?? 'Location unavailable'}` : 'Connect STID to load governed facility and asset metadata.'}</p></div><button className="copilot-button" onClick={() => setCopilotOpen(true)}><Bot size={16} /> Copilot</button></section>

      <section className="metrics">
        <div><Factory size={17} /><span>Assets<strong>{stid ? equipment.length : '—'}</strong><small>Lakehouse STID</small></span></div>
        <div><Gauge size={17} /><span>Instruments<strong>{stid ? stid.instruments.length : '—'}</strong><small>Mapped OPC UA nodes</small></span></div>
        <div><Activity size={17} /><span>Recent signals<strong>{telemetry.length || '—'}</strong><small>Eventhouse · 15 min</small></span></div>
        <div><Wrench size={17} /><span>Open work orders<strong>{user ? openOrders.length : '—'}</strong><small>Rayfin SQL</small></span></div>
      </section>

      <div className="workspace-grid">
        <section className="map-panel panel"><div className="panel-head"><div><h2>Facility location</h2><p>Facility-level coordinates from silver_facilities</p></div><MapPin size={18} /></div>{facility ? <FacilityMap facility={facility} /> : <EmptyState title="No facility loaded" action="Connect STID" onClick={() => void connectStid()} />}</section>
        <section className="assets-panel panel"><div className="panel-head"><div><h2>Asset registry</h2><p>{stid ? `${equipment.length} equipment records` : 'Authoritative STID source'}</p></div></div><div className="asset-list">{equipment.map(asset => <button key={asset.equipment_id} className={selected?.equipment_id === asset.equipment_id ? 'asset-row selected' : 'asset-row'} onClick={() => setSelectedId(asset.equipment_id)}><span className="asset-index">{asset.tag?.replace(/\D/g, '').padStart(2, '0') || '—'}</span><span><strong>{asset.tag ?? asset.equipment_id}</strong><small>{asset.manufacturer ?? 'Manufacturer unavailable'} · {asset.model ?? 'Model unavailable'}</small></span><em>{asset.status ?? 'Unknown'}</em></button>)}{!equipment.length && <EmptyState title="No assets loaded" action="Connect STID" onClick={() => void connectStid()} />}</div></section>
      </div>

      <div className="detail-grid">
        <section className="signals-panel panel"><div className="panel-head"><div><h2>{selected?.tag ?? 'Asset signals'}</h2><p>{selected ? `${selected.equipment_id} · ${selected.equipment_type_name ?? 'Equipment'}` : 'Select an asset'}</p></div><span className="provenance">STID + Eventhouse</span></div><div className="signal-table"><div className="table-head"><span>Signal</span><span>Latest value</span><span>Quality</span></div>{instruments.map(instrument => { const reading = readings.get(instrument.opcua_node_id); return <div className="signal-row" key={instrument.instrument_id}><span><strong>{instrument.tag ?? instrument.instrument_id}</strong><small>{instrument.opcua_node_id}</small></span><span>{reading ? <>{reading.value}<small>{instrument.unit ? ` ${instrument.unit}` : ''}</small></> : 'No event'}</span><em className={reading?.quality?.toLowerCase() === 'good' ? 'good' : ''}>{reading?.quality ?? '—'}</em></div>})}{selected && !instruments.length && <div className="inline-empty">No instruments are mapped to this asset.</div>}</div></section>
        <section className="orders-panel panel"><div className="panel-head"><div><h2>Work orders</h2><p>{selected ? `${selectedOrders.length} open for this asset` : 'Rayfin operational SQL'}</p></div><button className="icon-button" title="Create work order" onClick={() => void addWorkOrder()} disabled={!selected}><Plus size={18} /></button></div><div className="order-list">{selectedOrders.map(order => <article className="order" key={order.id}><span className={`priority ${order.priority.toLowerCase()}`}><Wrench size={14} /></span><div><strong>{order.title}</strong><small>{order.workOrderNumber}</small><p>{order.status} · {order.priority}</p></div></article>)}{!user && <EmptyState title="Operational records are protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !selectedOrders.length && <div className="inline-empty">No open work orders for this asset.</div>}</div></section>
      </div>
    </main>

    {copilotOpen && <aside className="copilot-panel"><div className="copilot-head"><span className="copilot-icon"><Bot size={18} /></span><div><strong>Operations Copilot</strong><small>Data Agent preview</small></div><button className="icon-button" onClick={() => setCopilotOpen(false)} title="Close"><X size={18} /></button></div><div className="messages">{messages.map((item, index) => <div className={`message ${item.role}`} key={index}><p>{item.text}</p></div>)}{busy && <small>Waiting for Data Agent...</small>}</div><div className="prompt-box"><textarea value={question} onChange={event => setQuestion(event.target.value)} placeholder="Ask about connected Fabric data" /><button onClick={() => void sendQuestion()} title="Send"><Send size={16} /></button></div></aside>}
  </div>
}

function EmptyState({ title, action, onClick }: { title: string; action: string; onClick: () => void }) {
  return <div className="empty-state"><Database size={20} /><p>{title}</p><button onClick={onClick}>{action}</button></div>
}