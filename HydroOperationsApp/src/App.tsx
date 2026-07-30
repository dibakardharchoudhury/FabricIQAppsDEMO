import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Bot, Box, Check, ClipboardCheck, Database, Factory, Gauge, MapPin, Package, Plus, Radio, Send, Wrench, X } from 'lucide-react'
import './App.css'
import { FacilityMap } from './components/FacilityMap'
// Lazy so three.js / model-viewer only load when a GLB is actually shown.
const AssetModelViewer = lazy(() => import('./components/AssetModelViewer').then(m => ({ default: m.AssetModelViewer })))
import { askDataAgent, beginInteractiveConnect, clearWorkspaceConfigCache, initAuth, isPostSeedConfigured, isStidConfigured, type JobStatus, queryLatestTelemetry, queryStid, resumePostSeedNotebook, resumeStreamingPipeline, runPostSeedNotebook, startStreamingPipeline, type StidData, type TelemetryReading } from './services/fabric'
import {
  createWorkOrder, initializeRayfin, isRayfinConfigured, listAsset3DModels, listInspections,
  listMaintenanceNotifications, listSpareParts, listWorkOrders, seedOperationalDataIfEmpty, signInToRayfin,
  type AppUser, type Asset3DModelRecord, type InspectionRecord, type MaintenanceNotificationRecord,
  type SparePartRecord, type WorkOrderRecord,
} from './services/rayfin'

const openStatuses = new Set(['draft', 'approved', 'planned', 'scheduled', 'ready', 'in progress', 'in_progress', 'on hold', 'on_hold'])
// OPC UA node ids encode the equipment tag (ns=2;s=T004.inlet_pressure -> T004) so a work order
// can be raised even before STID metadata maps the signal to an asset.
const equipmentTagFromNode = (nodeId: string) => nodeId.split('s=')[1]?.split(/[.;]/)[0]?.trim() || undefined
// model-viewer only renders glTF/GLB; other formats keep the thumbnail/link fallback.
const canRenderModel = (format?: string) => Boolean(format && ['GLB', 'GLTF'].includes(format.toUpperCase()))
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Unknown error'
const humanStatus = (status: JobStatus) => status === 'NotStarted' ? 'Queued' : status === 'InProgress' ? 'Running' : status
// Compact absolute + relative timestamps for live telemetry readings.
const fmtClock = (iso: string) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
const fmtAgo = (iso: string) => { const ms = Date.now() - new Date(iso).getTime(); if (Number.isNaN(ms)) return ''; const m = Math.round(ms / 60000); if (m < 1) return 'just now'; if (m < 60) return `${m}m ago`; const h = Math.round(m / 60); if (h < 24) return `${h}h ago`; return `${Math.round(h / 24)}d ago` }

// One entry per concurrently-running Fabric job so each keeps its own progress bar.
// `kind` lets a reloaded page re-attach to the right long-running Fabric job.
type ProgressJob = { kind: 'seed' | 'stream'; label: string; status: string; pct: number; startedAt: number; etaMs: number; endedAt?: number }
// A live telemetry signal whose OPC UA quality is Bad/Uncertain, resolved back to its asset.
type FlaggedSignal = { reading: TelemetryReading; instrument?: StidData['instruments'][number]; asset?: StidData['equipment'][number] }

// Running jobs are mirrored to localStorage so a page refresh can resume their progress bars.
const JOBS_STORAGE_KEY = 'hydro.jobs.v1'
// A Fabric job started longer ago than this is assumed finished; don't try to resume it.
const JOB_RESUME_MAX_AGE_MS = 30 * 60_000
// ETAs the progress bar eases toward — set to the upper end of observed run times so the
// bar keeps moving through the whole window instead of stalling at its 95% cap too early.
const SEED_ETA_MS = 6 * 60_000    // RTI_011 provision: ~5-6 min
const STREAM_ETA_MS = 5 * 60_000  // 02_Pipe_Stream: pipeline start + generator cell warmup until events flow (~5 min)
// While a Fabric job is still "Queued" (cold-starting a Spark session) the bar only crawls to
// this cap; it catches up toward 95% once the job flips to "Running".
const QUEUED_PCT_CAP = 15
const isQueuedStatus = (status: string) => status === 'Queued' || status === 'NotStarted'
// Completed fills the bar to 100%; a failed/cancelled job just stops where it is.
const isDoneStatus = (status: string) => status === 'Completed'
const isTerminalJobStatus = (status: string) => isDoneStatus(status) || status === 'Failed' || status === 'Cancelled'
// Compact elapsed-time label for a running or finished job (e.g. "3m 12s").
const fmtElapsed = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); const m = Math.floor(s / 60); const r = s % 60; return m ? `${m}m ${r}s` : `${r}s` }

// Read still-running jobs saved before a reload, dropping anything too old to still be live.
function readPersistedJobs(): Record<string, ProgressJob> {
  try {
    const raw = localStorage.getItem(JOBS_STORAGE_KEY)
    if (!raw) return {}
    const saved = JSON.parse(raw) as Record<string, ProgressJob>
    const fresh: Record<string, ProgressJob> = {}
    for (const [key, job] of Object.entries(saved)) {
      if (job && (job.kind === 'seed' || job.kind === 'stream') && typeof job.startedAt === 'number'
        && Date.now() - job.startedAt < JOB_RESUME_MAX_AGE_MS) fresh[key] = job
    }
    return fresh
  } catch { return {} }
}

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
  const [jobs, setJobs] = useState<Record<string, ProgressJob>>({})
  const [now, setNow] = useState(() => Date.now())
  const [provisioned, setProvisioned] = useState(false)
  const [setupHidden, setSetupHidden] = useState(false)
  const [copilotOpen, setCopilotOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [raising, setRaising] = useState<string>()
  const [messages, setMessages] = useState([{ role: 'agent', text: 'Ask me about the operation — assets, work orders, ontology relationships or the operational SQL database. I query the published Fabric Data Agent across all its connected sources.' }])

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
  // OPC UA quality flags: surface Bad/Uncertain live signals and let the operator raise a work order.
  const instrumentByNode = useMemo(() => new Map((stid?.instruments ?? []).map(item => [item.opcua_node_id, item])), [stid])
  const equipmentById = useMemo(() => new Map((stid?.equipment ?? []).map(item => [item.equipment_id, item])), [stid])
  const flaggedSignals = useMemo<FlaggedSignal[]>(() => telemetry
    .filter(reading => ['bad', 'uncertain'].includes((reading.quality ?? '').toLowerCase()))
    .sort((a, b) => new Date(b.eventTime).getTime() - new Date(a.eventTime).getTime())
    .map(reading => {
      const instrument = instrumentByNode.get(reading.opcuaNodeId)
      return { reading, instrument, asset: instrument ? equipmentById.get(instrument.equipment_id) : undefined }
    }), [telemetry, instrumentByNode, equipmentById])
  const nodesWithOpenOrder = useMemo(() => new Set(openOrders.map(order => order.opcuaNodeId)), [openOrders])

  useEffect(() => {
    // Restore bars for any job still running when the page was last open (before auth) so a
    // refresh visibly keeps the in-flight work instead of dropping it.
    const resumable = readPersistedJobs()
    if (Object.keys(resumable).length) setJobs(resumable)

    const initialize = async () => {
      if (isRayfinConfigured()) {
        try {
          const current = await initializeRayfin()
          setUser(current)
          if (current) await loadOperationalData()
        } catch (error) { setNotice(`Operations data: ${errorMessage(error)}`) }
      }
      await initAuth()
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
      // Fabric auth is ready — re-attach to the restored jobs and drive them to completion.
      await Promise.all(Object.values(resumable).map(job => resumeJob(job)))
    }
    void initialize()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, [])

  // Long Fabric jobs (notebook/pipeline runs) don't report real percentages, so estimate
  // each from its own elapsed time — eases monotonically toward ~95% over the expected duration.
  const jobKeys = Object.keys(jobs).sort().join(',')
  useEffect(() => {
    if (!jobKeys) return
    const tick = () => {
      setNow(Date.now())
      setJobs(prev => {
        let changed = false
        const next: Record<string, ProgressJob> = {}
        for (const [key, job] of Object.entries(prev)) {
          const elapsed = Date.now() - job.startedAt
          if (isTerminalJobStatus(job.status)) {
            // Finished: fill to 100% on success and freeze the elapsed clock once.
            const pct = isDoneStatus(job.status) ? 100 : job.pct
            const endedAt = job.endedAt ?? Date.now()
            if (pct !== job.pct || job.endedAt == null) { changed = true; next[key] = { ...job, pct, endedAt } }
            else next[key] = job
            continue
          }
          // Queued: crawl very slowly toward a low cap. Running: ease toward ~95% (catches up).
          const target = isQueuedStatus(job.status)
            ? Math.min(QUEUED_PCT_CAP, Math.round((1 - Math.exp(-elapsed / Math.max(1, job.etaMs))) * QUEUED_PCT_CAP))
            : Math.min(95, Math.round((1 - Math.exp((-2.5 * elapsed) / Math.max(1, job.etaMs))) * 100))
          const pct = Math.max(job.pct, target)
          if (pct !== job.pct) changed = true
          next[key] = pct === job.pct ? job : { ...job, pct }
        }
        return changed ? next : prev
      })
    }
    tick()
    const id = window.setInterval(tick, 500)
    return () => window.clearInterval(id)
  }, [jobKeys])

  // Mirror running jobs to localStorage so a refresh can restore their bars and resume polling.
  useEffect(() => {
    try {
      if (Object.keys(jobs).length) localStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(jobs))
      else localStorage.removeItem(JOBS_STORAGE_KEY)
    } catch { /* storage unavailable (private mode / iframe) — progress just won't survive reload */ }
  }, [jobs])

  function beginProgress(key: 'seed' | 'stream', label: string, etaMs: number) {
    setJobs(prev => ({ ...prev, [key]: { kind: key, label, status: 'Starting', pct: 3, startedAt: Date.now(), etaMs } }))
  }

  function updateJob(key: string, status: string) {
    setJobs(prev => prev[key] ? { ...prev, [key]: { ...prev[key], status } } : prev)
  }

  function endJob(key: string) {
    setJobs(prev => {
      if (!prev[key]) return prev
      const next = { ...prev }; delete next[key]; return next
    })
  }

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

  async function connectStid(opts?: { retries?: number }) {
    setSourceState('Connecting...'); setNotice(undefined)
    const retries = opts?.retries ?? 0
    try {
      let data = await queryStid()
      // Right after Seed & provision the GraphQL API can take a moment to become queryable —
      // rediscover the workspace and retry a few times before surfacing an error.
      for (let attempt = 0; !data && attempt < retries; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 8_000))
        clearWorkspaceConfigCache()
        data = await queryStid()
      }
      if (!data) { await beginInteractiveConnect('stid'); data = await queryStid() }
      if (data) { applyStid(data); setSourceState('STID connected') }
      else setSourceState('Connect STID')
    } catch (error) {
      setSourceState('Connect STID')
      const message = errorMessage(error)
      // Don't tell the operator to "run RTI_011" when Seed & provision has already completed.
      if (provisioned && /No GraphQL API found/i.test(message)) {
        setNotice('STID GraphQL API isn’t queryable yet — it can take a minute to finish publishing after Seed & provision. Wait a moment, then click Connect STID again.')
      } else setNotice(message)
    }
  }

  async function connectTelemetry() {
    setTelemetryState('Connecting...'); setNotice(undefined)
    try {
      let data = await queryLatestTelemetry()
      if (data === null) { await beginInteractiveConnect('telemetry'); data = await queryLatestTelemetry() }
      if (data) { setTelemetry(data); setTelemetryState(data.length ? `${data.length} signals` : 'No recent events') }
      else setTelemetryState('Connect telemetry')
    } catch (error) { setTelemetryState('Connect telemetry'); setNotice(errorMessage(error)) }
  }

  async function authenticate(): Promise<AppUser | null> {
    try {
      const current = await signInToRayfin()
      setUser(current); await loadOperationalData()
      return current
    } catch (error) { setNotice(errorMessage(error)); return null }
  }

  // Shared completion for the seed/provision job whether it was just started or resumed after a reload.
  async function awaitProvision(poll: () => Promise<JobStatus>) {
    try {
      const status = await poll()
      if (status === 'Completed') {
        setProvisioned(true)
        await loadOperationalData()
        setNotice('Operational data re-seeded and Fabric provisioning complete — connecting STID…')
        await connectStid({ retries: 5 })
      } else {
        setNotice(`Fabric provisioning ${humanStatus(status).toLowerCase()}.`)
      }
    } catch (error) { setNotice(`Fabric provisioning failed: ${errorMessage(error)}`) }
    finally { endJob('seed') }
  }

  // After the pipeline reports 'running', the generator notebook cell still needs a few minutes
  // before events land in the Eventhouse — poll until the first readings appear (or we give up).
  async function waitForStreamData(timeoutMs: number): Promise<TelemetryReading[] | null> {
    const deadline = Date.now() + timeoutMs
    let data = await queryLatestTelemetry()
    if (data === null) { await beginInteractiveConnect('telemetry'); data = await queryLatestTelemetry() }
    while (Date.now() < deadline) {
      if (data && data.length) return data
      await new Promise(resolve => setTimeout(resolve, 10_000))
      try { data = await queryLatestTelemetry() } catch { data = null }
    }
    return data && data.length ? data : null
  }

  // Shared completion for the stream job whether it was just started or resumed after a reload.
  async function awaitStream(poll: () => Promise<unknown>) {
    try {
      await poll()
      // The pipeline reaching 'running' doesn't mean signals are flowing yet — keep the progress
      // bar up until the generator cell actually produces telemetry, so it never completes early.
      updateJob('stream', 'Generating signals')
      const data = await waitForStreamData(STREAM_ETA_MS)
      setStreamState('started')
      if (data && data.length) { setTelemetry(data); setTelemetryState(`${data.length} signals`) }
      else setTelemetryState('No recent events')
    } catch (error) { setStreamState('error'); setNotice(errorMessage(error)) }
    finally { endJob('stream') }
  }

  // Re-attach to a Fabric job that was still running when the page was last open.
  async function resumeJob(job: ProgressJob) {
    const sinceIso = new Date(job.startedAt - 60_000).toISOString()
    if (job.kind === 'seed') {
      setSeeding(true)
      try { await awaitProvision(() => resumePostSeedNotebook(s => updateJob('seed', humanStatus(s)), sinceIso)) }
      finally { setSeeding(false) }
    } else {
      setStreamState('starting')
      await awaitStream(() => resumeStreamingPipeline(s => updateJob('stream', humanStatus(s)), sinceIso))
    }
  }

  async function seedDemo() {
    if (seeding || jobs.seed) return
    setSeeding(true); setNotice(undefined)
    try {
      const activeUser = user ?? await authenticate()
      if (!activeUser) { setNotice('Sign in with Fabric to seed operational data.'); return }
      if (isPostSeedConfigured()) {
        // RTI_011 is the authoritative seeder: its SQL MERGE upserts (re-seeds/updates)
        // every run, so skip the client-side insert-if-empty pre-seed.
        const label = 'Provisioning SQL, the STID GraphQL API and the Data Agent source (RTI_011)…'
        beginProgress('seed', label, SEED_ETA_MS)
        await awaitProvision(() => runPostSeedNotebook(s => updateJob('seed', humanStatus(s))))
      } else {
        // Fallback when RTI_011 isn't configured: client-side insert-if-empty seed.
        const result = await seedOperationalDataIfEmpty(activeUser)
        const created = result.filter(item => !item.skipped)
        await loadOperationalData()
        setNotice(created.length
          ? `Seeded ${created.map(item => `${item.created} ${item.entity}`).join(', ')}.`
          : 'Operational data already present.')
      }
    } catch (error) { setNotice(errorMessage(error)) }
    finally { setSeeding(false) }
  }

  async function addWorkOrder() {
    if (!selected) return
    const activeUser = user ?? await authenticate()
    if (!activeUser) { setNotice('Sign in with Fabric to create a work order.'); return }
    const instrument = instruments[0]
    try {
      const record = await createWorkOrder(activeUser, selected.equipment_id, instrument?.instrument_id, instrument?.opcua_node_id)
      setOrders(current => [record, ...current])
    } catch (error) { setNotice(errorMessage(error)) }
  }

  // Raise a work order straight from a Bad/Uncertain telemetry signal.
  async function raiseWorkOrderForSignal(flagged: FlaggedSignal) {
    // Prefer the STID-mapped asset; otherwise derive the equipment from the OPC UA node id so the
    // button always works, even before STID metadata is connected.
    const tag = equipmentTagFromNode(flagged.reading.opcuaNodeId)
    const matched = tag ? stid?.equipment.find(item => item.equipment_id.includes(tag) || (item.tag ?? '').includes(tag)) : undefined
    const equipmentId = flagged.instrument?.equipment_id ?? flagged.asset?.equipment_id ?? matched?.equipment_id ?? (tag ? `EQUIP_RTI_${tag}` : undefined)
    if (!equipmentId) { setNotice(`Could not identify an asset for ${flagged.reading.opcuaNodeId}; cannot raise a work order.`); return }
    const activeUser = user ?? await authenticate()
    if (!activeUser) { setNotice('Sign in with Fabric to create a work order.'); return }
    setRaising(flagged.reading.opcuaNodeId)
    try {
      const record = await createWorkOrder(activeUser, equipmentId, flagged.instrument?.instrument_id, flagged.reading.opcuaNodeId)
      setOrders(current => [record, ...current])
      setNotice(`Work order ${record.workOrderNumber} raised for ${flagged.instrument?.tag ?? flagged.reading.opcuaNodeId} (${flagged.reading.quality} quality).`)
    } catch (error) { setNotice(errorMessage(error)) }
    finally { setRaising(undefined) }
  }

  async function startStream() {
    if (streamState === 'starting' || jobs.stream) return
    setStreamState('starting'); setNotice(undefined)
    const label = 'Starting the OPC UA telemetry pipeline (02_Pipe_Stream)…'
    beginProgress('stream', label, STREAM_ETA_MS)
    await awaitStream(() => startStreamingPipeline(s => updateJob('stream', humanStatus(s))))
  }

  async function sendQuestion(override?: string) {
    const text = (override ?? question).trim(); if (!text || busy) return
    setQuestion(''); setBusy(true)
    // Append the user turn plus an empty agent bubble that streamed tokens fill in place.
    setMessages(current => [...current, { role: 'user', text }, { role: 'agent', text: '' }])
    const setLastAgent = (value: string) => setMessages(current => {
      const copy = current.slice()
      for (let i = copy.length - 1; i >= 0; i--) { if (copy[i].role === 'agent') { copy[i] = { role: 'agent', text: value }; break } }
      return copy
    })
    try {
      const answer = await askDataAgent(text, partial => setLastAgent(partial))
      setLastAgent(answer)
    }
    catch (error) { setLastAgent(errorMessage(error)) }
    setBusy(false)
  }

  // Ordered first-run setup. Each step explains why it exists and unlocks the next.
  const steps = [
    { n: 1, title: 'Sign in to Fabric', why: 'Authenticate with your Microsoft Fabric identity — required for operational data and to run setup.', done: Boolean(user), busy: false, action: 'Sign in', run: () => void authenticate() },
    { n: 2, title: 'Seed & provision', why: 'Loads demo work orders/inspections into SQL and publishes the STID GraphQL API + Data Agent (runs the RTI_011 notebook). Do this before Connect STID.', done: provisioned, busy: seeding, action: 'Seed & provision', run: () => void seedDemo() },
    { n: 3, title: 'Start telemetry stream', why: 'Starts the OPC UA pipeline so live signals flow into the Eventhouse. Independent of step 2 — run it in parallel. Takes ~5 min to warm up before signals appear.', done: streamState === 'started', busy: streamState === 'starting', action: 'Start stream', run: () => void startStream() },
    { n: 4, title: 'Connect STID', why: 'Loads governed facility & asset metadata from the Lakehouse GraphQL API published in step 2.', done: Boolean(stid), busy: sourceState === 'Connecting...', action: 'Connect STID', run: () => void connectStid() },
    { n: 5, title: 'Connect telemetry', why: 'Reads the latest OPC UA signal values from the Eventhouse stream started in step 3.', done: telemetry.length > 0, busy: telemetryState === 'Connecting...', action: 'Connect telemetry', run: () => void connectTelemetry() },
  ]
  const setupComplete = steps.every(step => step.done)
  // Starter prompts grounded in the loaded data so users click questions the agent can actually answer.
  const primaryFacilityId = facility?.facility_id ?? facilities[0]?.facility_id
  const suggestedPrompts = [
    'Summarize the open work orders and which assets they affect.',
    primaryFacilityId ? `Which turbines in ${primaryFacilityId} report Bad or Uncertain signal quality?` : 'Which turbines report Bad or Uncertain signal quality?',
    'List the equipment and their instruments from the ontology.',
  ]

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark"><Factory size={18} /></span><div><strong>Hydro Operations</strong><small>Microsoft Fabric</small></div></div>
      <div className="source-actions">
        <button className={stid ? 'source-chip connected' : 'source-chip'} onClick={() => void connectStid()} title="Step 4 · Load governed facility & asset metadata from the Lakehouse GraphQL API (publish it first via Seed & provision).">4 · <Database size={14} />{sourceState}</button>
        <button className={telemetry.length ? 'source-chip connected' : 'source-chip'} onClick={() => void connectTelemetry()} title="Step 5 · Read the latest OPC UA signals from the Eventhouse (start the stream first).">5 · <Radio size={14} />{telemetryState}</button>
      </div>
      <div className="top-actions"><button className="avatar" onClick={() => void authenticate()} title={user?.email ?? 'Step 1 · Sign in with your Microsoft Fabric identity'}>{user?.name.slice(0, 2).toUpperCase() ?? 'ID'}</button></div>
    </header>

    <main>
      {notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice(undefined)}><X size={15} /></button></div>}
      {Object.entries(jobs).map(([key, job]) => <div key={key} className="progress"><div className="progress-head"><span>{job.label}</span><em>{job.status} · {job.pct}% · {fmtElapsed((job.endedAt ?? now) - job.startedAt)}</em></div><div className="progress-track"><div className="progress-bar" style={{ width: `${job.pct}%`, marginLeft: 0, animation: 'none' }} /></div></div>)}
      {!setupHidden && !setupComplete && <section className="setup">
        <div className="setup-head"><span className="eyebrow">GUIDED SETUP</span><p>First time here? Steps 2 and 3 are independent — you can start them together, then finish 4 and 5.</p><button className="icon-button" onClick={() => setSetupHidden(true)} title="Hide guided setup"><X size={16} /></button></div>
        <ol className="setup-steps">{steps.map(step => <li key={step.n} className={step.done ? 'setup-step done' : 'setup-step'}>
          <span className="step-num">{step.done ? <Check size={14} /> : step.n}</span>
          <div className="step-body"><strong>{step.title}</strong><small>{step.why}</small></div>
          <button className="step-action" onClick={step.run} disabled={step.busy || step.done}>{step.done ? 'Done' : step.busy ? 'Working…' : step.action}</button>
        </li>)}</ol>
      </section>}
      <section className="page-head"><div><span className="eyebrow">FACILITY OPERATIONS</span><h1>{facility?.facility_name ?? 'Hydropower operations'}</h1><p>{facility ? `${facility.facility_id} · ${facility.type ?? 'Facility'} · ${facility.country ?? 'Location unavailable'}` : 'Connect STID to load governed facility and asset metadata.'}</p></div><button className="copilot-button" onClick={() => setCopilotOpen(true)}><Bot size={16} /> Copilot</button></section>

      {facilities.length > 1 && <section className="facility-strip">{facilities.map(item => <button key={item.facility_id} className={item.facility_id === facility?.facility_id ? 'facility-chip active' : 'facility-chip'} onClick={() => selectFacility(item.facility_id)}><Factory size={14} /><span><strong>{item.facility_name}</strong><small>{item.facility_id}</small></span></button>)}</section>}

      <section className="metrics">
        <div><MapPin size={17} /><span>Facilities<strong>{stid ? facilities.length : '—'}</strong><small>Lakehouse STID</small></span></div>
        <div><Factory size={17} /><span>Assets<strong>{stid ? equipment.length : '—'}</strong><small>This facility</small></span></div>
        <div><Gauge size={17} /><span>Instruments<strong>{stid ? facilityInstruments.length : '—'}</strong><small>Mapped OPC UA nodes</small></span></div>
        <div><Activity size={17} /><span>Live signals<strong>{telemetry.length || '—'}</strong><small>Eventhouse · latest per node · last 24h</small></span></div>
        <div><Wrench size={17} /><span>Open work orders<strong>{user ? openOrders.length : '—'}</strong><small>Rayfin SQL</small></span></div>
      </section>

      {flaggedSignals.length > 0 && <section className="quality-alert">
        <div className="quality-alert-head"><AlertTriangle size={16} /><div><strong>{flaggedSignals.length} live signal{flaggedSignals.length > 1 ? 's' : ''} reporting Bad / Uncertain quality</strong><small>OPC UA quality flags from the Eventhouse stream over the last 24h, most recent event first — raise a work order to investigate the affected asset.</small></div></div>
        <ul className="quality-alert-list">{flaggedSignals.slice(0, 6).map(flagged => {
          const hasOrder = nodesWithOpenOrder.has(flagged.reading.opcuaNodeId)
          const quality = (flagged.reading.quality ?? '').toLowerCase()
          const derivedTag = equipmentTagFromNode(flagged.reading.opcuaNodeId)
          return <li key={flagged.reading.opcuaNodeId}>
            <span className={`q-badge ${quality}`}>{flagged.reading.quality}</span>
            <span className="q-sig"><strong>{flagged.instrument?.tag ?? flagged.reading.opcuaNodeId}</strong><small>{(flagged.asset?.tag ?? flagged.instrument?.equipment_id ?? (derivedTag ? `Turbine ${derivedTag}` : 'Unmapped signal'))} · {flagged.reading.value}{flagged.instrument?.unit ? ` ${flagged.instrument.unit}` : ''}</small></span>
            <span className="q-time" title={new Date(flagged.reading.eventTime).toLocaleString()}><strong>{fmtAgo(flagged.reading.eventTime)}</strong><small>{fmtClock(flagged.reading.eventTime)}</small></span>
            <button className="q-action" disabled={hasOrder || raising === flagged.reading.opcuaNodeId} onClick={() => void raiseWorkOrderForSignal(flagged)}>{hasOrder ? 'WO open' : raising === flagged.reading.opcuaNodeId ? 'Raising…' : <><Plus size={12} /> Work order</>}</button>
          </li>
        })}</ul>
      </section>}

      <div className="workspace-grid">
        <section className="map-panel panel"><div className="panel-head"><div><h2>Facility network</h2><p>{facilities.length > 1 ? `${facilities.length} facilities from silver_facilities` : 'Facility coordinates from silver_facilities'}</p></div><MapPin size={18} /></div>{facilities.length ? <FacilityMap facilities={facilities} selectedId={facility?.facility_id} onSelect={selectFacility} /> : <EmptyState title="No facility loaded" action="Connect STID" onClick={() => void connectStid()} />}</section>
        <section className="assets-panel panel"><div className="panel-head"><div><h2>Asset registry</h2><p>{stid ? `${equipment.length} equipment records` : 'Authoritative STID source'}</p></div></div><div className="asset-list">{equipment.map(asset => <button key={asset.equipment_id} className={selected?.equipment_id === asset.equipment_id ? 'asset-row selected' : 'asset-row'} onClick={() => setSelectedId(asset.equipment_id)}><span className="asset-index">{asset.tag?.replace(/\D/g, '').padStart(2, '0') || '—'}</span><span><strong>{asset.tag ?? asset.equipment_id}</strong><small>{asset.manufacturer ?? 'Manufacturer unavailable'} · {asset.model ?? 'Model unavailable'}</small></span><em>{asset.status ?? 'Unknown'}</em></button>)}{!equipment.length && <EmptyState title="No assets loaded" action="Connect STID" onClick={() => void connectStid()} />}</div></section>
      </div>

      <div className="detail-grid">
        <section className="signals-panel panel"><div className="panel-head"><div><h2>{selected?.tag ?? 'Asset signals'}</h2><p>{selected ? `${selected.equipment_id} · ${selected.equipment_type_name ?? 'Equipment'}` : 'Select an asset'}</p></div><span className="provenance">STID + Eventhouse</span></div><div className="signal-table"><div className="table-head"><span>Signal</span><span>Latest value</span><span>Quality</span></div>{instruments.map(instrument => { const reading = readings.get(instrument.opcua_node_id); return <div className="signal-row" key={instrument.instrument_id}><span><strong>{instrument.tag ?? instrument.instrument_id}</strong><small>{instrument.opcua_node_id}</small></span><span>{reading ? <>{reading.value}<small>{instrument.unit ? ` ${instrument.unit}` : ''}</small></> : 'No event'}</span><em className={reading?.quality ? reading.quality.toLowerCase() : ''}>{reading?.quality ?? '—'}</em></div>})}{selected && !instruments.length && <div className="inline-empty">No instruments are mapped to this asset.</div>}</div></section>
        <section className="orders-panel panel"><div className="panel-head"><div><h2>Work orders</h2><p>{selected ? `${selectedOrders.length} open for this asset` : 'Rayfin operational SQL'}</p></div><button className="icon-button" title="Create work order" onClick={() => void addWorkOrder()} disabled={!selected}><Plus size={18} /></button></div><div className="order-list">{selectedOrders.map(order => <article className="order" key={order.id}><span className={`priority ${order.priority.toLowerCase()}`}><Wrench size={14} /></span><div><strong>{order.title}</strong><small>{order.workOrderNumber}</small><p>{order.status} · {order.priority}</p></div></article>)}{!user && <EmptyState title="Operational records are protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !selectedOrders.length && <div className="inline-empty">No open work orders for this asset.</div>}</div></section>
      </div>

      <div className="detail-grid">
        <section className="twin-panel panel"><div className="panel-head"><div><h2>Digital twin</h2><p>{selected ? `3D model for ${selected.tag ?? selected.equipment_id}` : 'Asset 3D model'}</p></div><span className="provenance">Rayfin SQL</span></div><div className="twin-body">{!user ? <EmptyState title="3D models are protected" action="Connect operations" onClick={() => void authenticate()} /> : selectedModel ? <><div className={canRenderModel(selectedModel.format) ? 'twin-thumb twin-thumb-3d' : 'twin-thumb'}>{canRenderModel(selectedModel.format) ? <Suspense fallback={<Box size={40} />}><AssetModelViewer model={selectedModel} /></Suspense> : selectedModel.thumbnailUrl ? <img src={selectedModel.thumbnailUrl} alt={selectedModel.modelName} /> : <Box size={40} />}</div><div className="twin-meta"><strong>{selectedModel.modelName}</strong><small>{selectedModel.format}{selectedModel.version ? ` · ${selectedModel.version}` : ''}{selectedModel.fileSizeMb ? ` · ${selectedModel.fileSizeMb} MB` : ''}</small><a href={selectedModel.modelUrl} target="_blank" rel="noreferrer">Open model ↗</a></div></> : <div className="inline-empty">No 3D model registered for this asset.</div>}</div></section>
        <section className="inspections-panel panel"><div className="panel-head"><div><h2>Inspections</h2><p>{selected ? `${selectedInspections.length} record(s) for this asset` : 'Condition inspections'}</p></div><ClipboardCheck size={18} /></div><div className="order-list">{selectedInspections.map(item => <article className="order" key={item.id}><span className={`insp-result ${item.result.toLowerCase()}`}><ClipboardCheck size={14} /></span><div><strong>{item.inspectionType}</strong><small>{new Date(item.inspectedAt).toLocaleDateString()} · {item.result}</small><p>{item.findings ?? 'No findings recorded.'}</p></div></article>)}{!user && <EmptyState title="Inspection records are protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !selectedInspections.length && <div className="inline-empty">No inspections for this asset.</div>}</div></section>
      </div>

      <div className="detail-grid">
        <section className="spares-panel panel"><div className="panel-head"><div><h2>Spare parts inventory</h2><p>{user ? `${spareParts.length} SKUs · ${lowStockParts.length} below reorder` : 'Maintenance readiness'}</p></div><Package size={18} /></div><div className="spares-table">{spareParts.map(part => { const low = part.quantityOnHand <= part.reorderLevel; return <div className={low ? 'spare-row low' : 'spare-row'} key={part.id}><span><strong>{part.name}</strong><small>{part.partNumber} · {part.category}</small></span><span className="spare-loc">{part.storageLocation}</span><em>{part.quantityOnHand}{low && <AlertTriangle size={13} />}<small>/ {part.reorderLevel}</small></em></div>})}{!user && <EmptyState title="Inventory is protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !spareParts.length && <div className="inline-empty">No spare parts in inventory.</div>}</div></section>
        <section className="notifications-panel panel"><div className="panel-head"><div><h2>Maintenance notifications</h2><p>{selected ? `${selectedNotifications.length} for this asset` : 'Operational alerts'}</p></div><AlertTriangle size={18} /></div><div className="order-list">{selectedNotifications.map(item => <article className="order" key={item.id}><span className={`priority ${item.severity.toLowerCase()}`}><AlertTriangle size={14} /></span><div><strong>{item.summary}</strong><small>{new Date(item.reportedAt).toLocaleDateString()} · {item.status}</small><p>{item.severity}</p></div></article>)}{!user && <EmptyState title="Notifications are protected" action="Connect operations" onClick={() => void authenticate()} />}{user && !selectedNotifications.length && <div className="inline-empty">No notifications for this asset.</div>}</div></section>
      </div>
    </main>

    {copilotOpen && <aside className="copilot-panel"><div className="copilot-head"><span className="copilot-icon"><Bot size={18} /></span><div><strong>Operations Copilot</strong><small>Data Agent preview</small></div><button className="icon-button" onClick={() => setCopilotOpen(false)} title="Close"><X size={18} /></button></div><div className="messages">{messages.map((item, index) => <div className={`message ${item.role}`} key={index}><p className={!item.text && item.role === 'agent' ? 'thinking' : undefined}>{item.text || (item.role === 'agent' ? 'Thinking' : '')}</p></div>)}{messages.length === 1 && !busy && <div className="copilot-suggestions">{suggestedPrompts.map(s => <button key={s} className="suggestion" onClick={() => void sendQuestion(s)}>{s}</button>)}</div>}</div><div className="prompt-box"><textarea value={question} onChange={event => setQuestion(event.target.value)} placeholder="Ask about connected Fabric data" /><button onClick={() => void sendQuestion()} title="Send"><Send size={16} /></button></div></aside>}
  </div>
}

function EmptyState({ title, action, onClick }: { title: string; action: string; onClick: () => void }) {
  return <div className="empty-state"><Database size={20} /><p>{title}</p><button onClick={onClick}>{action}</button></div>
}