import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  askDataAgent, beginInteractiveConnect, clearWorkspaceConfigCache, initAuth, isPostSeedConfigured, isStidConfigured,
  queryLatestTelemetry, queryStid, resumePostSeedNotebook, resumeStreamingPipeline, runPostSeedNotebook,
  startStreamingPipeline, type JobStatus, type StidData, type TelemetryHistoryRange, type TelemetryReading,
} from '../../services/fabric'
import {
  createWorkOrder, deleteWorkOrder, initializeRayfin, isRayfinConfigured, listAsset3DModels, listInspections,
  listMaintenanceNotifications, listSpareParts, listWorkOrders, seedOperationalDataIfEmpty, signInToRayfin,
  updateWorkOrderStatus, type AppUser, type Asset3DModelRecord, type InspectionRecord,
  type MaintenanceNotificationRecord, type SparePartRecord, type WorkOrderRecord,
} from '../../services/rayfin'
import { twinStatus, type TwinStatus } from '../../twin'

const openStatuses = new Set(['draft', 'approved', 'planned', 'scheduled', 'ready', 'in progress', 'in_progress', 'on hold', 'on_hold'])
const equipmentTagFromNode = (nodeId: string) => nodeId.match(/(?:^|;)s=([^.;]+)/)?.[1]?.trim() || undefined
const fmtSince = (ms: number) => { if (ms < 5000) return 'just now'; const s = Math.round(ms / 1000); if (s < 60) return `${s}s ago`; const m = Math.round(s / 60); if (m < 60) return `${m}m ago`; return `${Math.round(m / 60)}h ago` }
const fmtElapsed = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); const m = Math.floor(s / 60); const r = s % 60; return m ? `${m}m ${r}s` : `${r}s` }
const humanStatus = (status: JobStatus) => status === 'NotStarted' ? 'Queued' : status === 'InProgress' ? 'Running' : status

const SETUP_STORAGE_KEY = 'hydro.v2.setup.v1'
const JOBS_STORAGE_KEY = 'hydro.jobs.v1'
const JOB_RESUME_MAX_AGE_MS = 30 * 60_000
const SEED_ETA_MS = 6 * 60_000
const STREAM_ETA_MS = 5 * 60_000
const QUEUED_PCT_CAP = 15

type LoadState = 'idle' | 'loading' | 'connected' | 'unavailable' | 'error'
type ActionState = 'idle' | 'running' | 'complete' | 'error'
type TelemetryStatus = 'live' | 'delayed' | 'stale' | 'unavailable'
export type ProgressJob = { kind: 'seed' | 'stream'; label: string; status: string; pct: number; startedAt: number; etaMs: number; endedAt?: number }
export type TelemetryExplorerSelection = { assetId?: string; signalId?: string; range: TelemetryHistoryRange }
export type ChatMessage = { role: 'user' | 'agent'; text: string; chart?: boolean; meta?: { elapsedMs: number; tokens?: number } }
type PersistedSetup = { provisioned?: boolean; stidConnected?: boolean; telemetryConnected?: boolean; selectedFacilityId?: string; selectedAssetIds?: Record<string, string> }

const INITIAL_MESSAGE: ChatMessage = { role: 'agent', text: 'Ask me about the operation — facilities, equipment, instruments, live signal quality, or work orders. I query the published Fabric Data Agent across its connected sources and answer with tables where it helps.' }
const wantsChart = (text: string) => /\b(chart|graph|plot|visuali[sz]e?|visual|trend(?:ing|s|line)?|bar\s*chart|pie|line\s*chart|histogram)\b/i.test(text)

function readPersistedSetup(): PersistedSetup {
  try { return JSON.parse(localStorage.getItem(SETUP_STORAGE_KEY) || '{}') as PersistedSetup }
  catch { return {} }
}

function writePersistedSetup(patch: PersistedSetup) {
  try { localStorage.setItem(SETUP_STORAGE_KEY, JSON.stringify({ ...readPersistedSetup(), ...patch })) }
  catch { /* storage unavailable */ }
}

function readPersistedJobs(): Record<string, ProgressJob> {
  try {
    const saved = JSON.parse(localStorage.getItem(JOBS_STORAGE_KEY) || '{}') as Record<string, ProgressJob>
    const fresh: Record<string, ProgressJob> = {}
    for (const [key, job] of Object.entries(saved)) {
      if (job && (job.kind === 'seed' || job.kind === 'stream') && typeof job.startedAt === 'number'
        && Date.now() - job.startedAt < JOB_RESUME_MAX_AGE_MS) fresh[key] = job
    }
    return fresh
  } catch { return {} }
}

const isQueuedStatus = (status: string) => status === 'Queued' || status === 'NotStarted'
const isDoneStatus = (status: string) => status === 'Completed'
const isTerminalJobStatus = (status: string) => isDoneStatus(status) || status === 'Failed' || status === 'Cancelled'

function useHydroOperationsDataController() {
  const persisted = useMemo(() => readPersistedSetup(), [])
  const [user, setUser] = useState<AppUser | null>(null)
  const [stid, setStid] = useState<StidData | null>(null)
  const [telemetry, setTelemetry] = useState<TelemetryReading[]>([])
  const [orders, setOrders] = useState<WorkOrderRecord[]>([])
  const [inspections, setInspections] = useState<InspectionRecord[]>([])
  const [spareParts, setSpareParts] = useState<SparePartRecord[]>([])
  const [notifications, setNotifications] = useState<MaintenanceNotificationRecord[]>([])
  const [assetModels, setAssetModels] = useState<Asset3DModelRecord[]>([])
  const [selectedFacilityId, setSelectedFacilityIdState] = useState<string | undefined>(persisted.selectedFacilityId)
  const [selectedAssetIds, setSelectedAssetIds] = useState<Record<string, string>>(() => persisted.selectedAssetIds ?? {})
  const [stidState, setStidState] = useState<LoadState>(persisted.stidConnected ? 'loading' : 'idle')
  const [telemetryState, setTelemetryState] = useState<LoadState>(persisted.telemetryConnected ? 'loading' : 'idle')
  const [operationsState, setOperationsState] = useState<LoadState>('idle')
  const [modelState, setModelState] = useState<LoadState>('idle')
  const [provisionState, setProvisionState] = useState<ActionState>(persisted.provisioned ? 'complete' : 'idle')
  const [streamState, setStreamState] = useState<ActionState>('idle')
  const [notice, setNotice] = useState<string>()
  const [jobs, setJobs] = useState<Record<string, ProgressJob>>(() => readPersistedJobs())
  const [now, setNow] = useState(() => Date.now())
  const [telemetrySelections, setTelemetrySelections] = useState<Record<string, TelemetryExplorerSelection>>({})
  const [messages, setMessages] = useState<ChatMessage[]>([INITIAL_MESSAGE])
  const [copilotBusy, setCopilotBusy] = useState(false)
  const [mutationKey, setMutationKey] = useState<string>()

  const setSelectedFacilityId = useCallback((facilityId: string) => {
    setSelectedFacilityIdState(facilityId)
    writePersistedSetup({ selectedFacilityId: facilityId })
  }, [])

  const applyStid = useCallback((data: StidData) => {
    setStid(data)
    setSelectedFacilityIdState(current => {
      const next = current && data.facilities.some(facility => facility.facility_id === current)
        ? current
        : data.facilities[0]?.facility_id
      if (next) writePersistedSetup({ selectedFacilityId: next, stidConnected: true })
      return next
    })
  }, [])

  const loadOperationalData = useCallback(async () => {
    const [loadedOrders, loadedModels, loadedInspections, loadedParts, loadedNotifications] = await Promise.allSettled([
      listWorkOrders(), listAsset3DModels(), listInspections(), listSpareParts(), listMaintenanceNotifications(),
    ])
    if (loadedOrders.status === 'fulfilled') {
      setOrders(loadedOrders.value)
      setOperationsState('connected')
    } else {
      setOperationsState('error')
    }
    if (loadedModels.status === 'fulfilled') {
      setAssetModels(loadedModels.value)
      setModelState('connected')
    } else {
      setModelState('error')
    }
    if (loadedInspections.status === 'fulfilled') setInspections(loadedInspections.value)
    if (loadedParts.status === 'fulfilled') setSpareParts(loadedParts.value)
    if (loadedNotifications.status === 'fulfilled') setNotifications(loadedNotifications.value)
    return { orders: loadedOrders.status === 'fulfilled' ? loadedOrders.value : [], models: loadedModels.status === 'fulfilled' ? loadedModels.value : [] }
  }, [])

  const authenticate = useCallback(async (): Promise<AppUser | null> => {
    let current: AppUser
    try {
      setNotice(undefined)
      setOperationsState('loading')
      current = await signInToRayfin()
      setUser(current)
    } catch (error) {
      setOperationsState('error')
      setNotice(error instanceof Error ? error.message : 'Fabric sign-in did not establish a session.')
      return null
    }

    try {
      await loadOperationalData()
      return current
    } catch (error) {
      setOperationsState('error')
      setNotice(error instanceof Error ? error.message : 'Operational data is unavailable.')
      return null
    }
  }, [loadOperationalData])

  const loadTelemetry = useCallback(async (interactive: boolean) => {
    let data = await queryLatestTelemetry()
    if (data === null && interactive) {
      await beginInteractiveConnect('telemetry')
      data = await queryLatestTelemetry()
    }
    if (data) {
      setTelemetry(data)
      setTelemetryState('connected')
      writePersistedSetup({ telemetryConnected: data.length > 0 })
    } else {
      setTelemetryState('unavailable')
    }
    return data
  }, [])

  const connectStid = useCallback(async (opts?: { retries?: number; interactive?: boolean }) => {
    setStidState('loading'); setNotice(undefined)
    const retries = opts?.retries ?? 0
    const interactive = opts?.interactive ?? true
    clearWorkspaceConfigCache()
    try {
      let data = await queryStid()
      for (let attempt = 0; !data && attempt < retries; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 8_000))
        clearWorkspaceConfigCache()
        data = await queryStid()
      }
      if (!data && interactive) { await beginInteractiveConnect('stid'); data = await queryStid() }
      if (data) { applyStid(data); setStidState('connected'); writePersistedSetup({ stidConnected: true }) }
      else {
        setStidState('unavailable')
        if (!interactive) setNotice('Fabric provisioning completed. The STID GraphQL API is still publishing or needs permission. Wait a moment, then click Connect STID.')
      }
    } catch (error) {
      setStidState('error')
      const message = error instanceof Error ? error.message : 'STID data is unavailable.'
      setNotice(provisionState === 'complete' && /No GraphQL API found/i.test(message)
        ? 'STID GraphQL API is not queryable yet. Wait a moment, then run Connect STID again.'
        : message)
    }
  }, [applyStid, provisionState])

  const connectTelemetry = useCallback(async () => {
    setTelemetryState('loading'); setNotice(undefined)
    clearWorkspaceConfigCache()
    try { await loadTelemetry(true) }
    catch (error) { setTelemetryState('error'); setNotice(error instanceof Error ? error.message : 'Telemetry is unavailable.') }
  }, [loadTelemetry])

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

  const awaitProvision = useCallback(async (poll: () => Promise<JobStatus>) => {
    let completed = false
    try {
      const status = await poll()
      if (status === 'Completed') {
        completed = true
        setProvisionState('complete')
        writePersistedSetup({ provisioned: true })
        await loadOperationalData()
        setNotice('Operational data re-seeded and Fabric provisioning complete. Checking STID publication...')
      } else {
        setNotice(`Fabric provisioning ${humanStatus(status).toLowerCase()}.`)
      }
    } catch (error) {
      setProvisionState('error')
      setNotice(`Fabric provisioning failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally { endJob('seed') }
    if (completed) await connectStid({ retries: 5, interactive: false })
  }, [connectStid, loadOperationalData])

  const seedAndProvision = useCallback(async () => {
    if (provisionState === 'running' || jobs.seed) return
    setProvisionState('running'); setNotice(undefined)
    try {
      const activeUser = user ?? await authenticate()
      if (!activeUser) { setProvisionState('idle'); setNotice('Sign in with Fabric to seed operational data.'); return }
      if (isPostSeedConfigured()) {
        beginProgress('seed', 'Provisioning SQL, the STID GraphQL API and the Data Agent source (RTI_011)...', SEED_ETA_MS)
        await awaitProvision(() => runPostSeedNotebook(status => updateJob('seed', humanStatus(status))))
      } else {
        const result = await seedOperationalDataIfEmpty(activeUser)
        await loadOperationalData()
        setProvisionState('complete')
        writePersistedSetup({ provisioned: true })
        const created = result.filter(item => !item.skipped)
        setNotice(created.length ? `Seeded ${created.map(item => `${item.created} ${item.entity}`).join(', ')}.` : 'Operational data already present.')
      }
    } catch (error) {
      setProvisionState('error')
      setNotice(error instanceof Error ? error.message : 'Seed and provision failed.')
    }
  }, [authenticate, awaitProvision, jobs.seed, loadOperationalData, provisionState, user])

  const refreshOperationalData = useCallback(async () => {
    setOperationsState('loading'); setNotice(undefined)
    try {
      const current = user ?? await authenticate()
      if (!current) { setOperationsState('unavailable'); return }
      await loadOperationalData()
    } catch (error) {
      setOperationsState('error')
      setNotice(error instanceof Error ? error.message : 'Operational data is unavailable.')
    }
  }, [authenticate, loadOperationalData, user])

  const waitForStreamData = useCallback(async (timeoutMs: number, sinceMs: number): Promise<TelemetryReading[] | null> => {
    const deadline = Date.now() + timeoutMs
    const cutoff = sinceMs - 120_000
    const isFresh = (rows: TelemetryReading[] | null) => !!rows && rows.some(row => Date.parse(row.eventTime) >= cutoff)
    let data = await loadTelemetry(true)
    while (!isFresh(data) && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 10_000))
      try { data = await queryLatestTelemetry() } catch { data = null }
    }
    if (isFresh(data)) {
      setTelemetry(data!)
      setTelemetryState('connected')
      writePersistedSetup({ telemetryConnected: true })
      return data
    }
    return null
  }, [loadTelemetry])

  const awaitStream = useCallback(async (poll: () => Promise<unknown>, sinceMs: number) => {
    try {
      await poll()
      updateJob('stream', 'Generating signals')
      const data = await waitForStreamData(2 * STREAM_ETA_MS, sinceMs)
      if (data && data.length) {
        setStreamState('complete')
        writePersistedSetup({ telemetryConnected: true })
      } else {
        setStreamState('idle')
        setNotice('The telemetry pipeline is running, but fresh OPC UA signals have not landed in the Eventhouse yet. Give it a moment, then start the stream again to check.')
      }
    } catch (error) {
      setStreamState('error')
      setNotice(error instanceof Error ? error.message : 'Telemetry stream startup failed.')
    } finally { endJob('stream') }
  }, [waitForStreamData])

  const startStream = useCallback(async () => {
    if (streamState === 'running' || jobs.stream) return
    setStreamState('running'); setNotice(undefined)
    const sinceMs = Date.now()
    beginProgress('stream', 'Starting OPC UA telemetry stream...', STREAM_ETA_MS)
    await awaitStream(() => startStreamingPipeline(status => updateJob('stream', humanStatus(status))), sinceMs)
  }, [awaitStream, jobs.stream, streamState])

  const resumeJob = useCallback(async (job: ProgressJob) => {
    const sinceIso = new Date(job.startedAt - 60_000).toISOString()
    if (job.kind === 'seed') {
      setProvisionState('running')
      await awaitProvision(() => resumePostSeedNotebook(status => updateJob('seed', humanStatus(status)), sinceIso))
    } else {
      setStreamState('running')
      await awaitStream(() => resumeStreamingPipeline(status => updateJob('stream', humanStatus(status)), sinceIso), job.startedAt)
    }
  }, [awaitProvision, awaitStream])

  useEffect(() => {
    let cancelled = false
    const initialize = async () => {
      if (isRayfinConfigured()) {
        setOperationsState('loading')
        try {
          const current = await initializeRayfin()
          if (cancelled) return
          setUser(current)
          if (current) await loadOperationalData()
          else { setOperationsState('unavailable'); setModelState('unavailable') }
        } catch (error) {
          if (cancelled) return
          setOperationsState('error')
          setNotice(error instanceof Error ? error.message : 'Operations data is unavailable.')
        }
      } else { setOperationsState('unavailable'); setModelState('unavailable') }

      try { await initAuth() }
      catch (error) { if (!cancelled) setNotice(error instanceof Error ? error.message : 'Fabric authentication is unavailable.') }

      if (isStidConfigured()) {
        setStidState('loading')
        try {
          const data = await queryStid()
          if (cancelled) return
          if (data) { applyStid(data); setStidState('connected'); writePersistedSetup({ stidConnected: true }) }
          else setStidState('unavailable')
        } catch (error) {
          if (cancelled) return
          setStidState('error')
          setNotice(error instanceof Error ? error.message : 'STID data is unavailable.')
        }
      } else setStidState('unavailable')

      setTelemetryState('loading')
      try { await loadTelemetry(false) }
      catch (error) { if (!cancelled) { setTelemetryState('error'); setNotice(error instanceof Error ? error.message : 'Telemetry is unavailable.') } }

      if (!cancelled) await Promise.all(Object.values(readPersistedJobs()).map(job => resumeJob(job)))
    }
    void initialize()
    return () => { cancelled = true }
  }, [applyStid, loadOperationalData, loadTelemetry, resumeJob])

  const telemetryLive = telemetry.length > 0
  useEffect(() => {
    if (!telemetryLive) return
    let inFlight = false
    const poll = async () => {
      if (inFlight || document.hidden) return
      inFlight = true
      try { await loadTelemetry(false) } catch { /* keep last good readings */ }
      finally { inFlight = false }
    }
    const id = window.setInterval(poll, 10_000)
    return () => window.clearInterval(id)
  }, [telemetryLive, loadTelemetry])

  useEffect(() => {
    if (!telemetryLive) return
    const id = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(id)
  }, [telemetryLive])

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
            const pct = isDoneStatus(job.status) ? 100 : job.pct
            const endedAt = job.endedAt ?? Date.now()
            if (pct !== job.pct || job.endedAt == null) { changed = true; next[key] = { ...job, pct, endedAt } }
            else next[key] = job
            continue
          }
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

  useEffect(() => {
    try {
      if (Object.keys(jobs).length) localStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(jobs))
      else localStorage.removeItem(JOBS_STORAGE_KEY)
    } catch { /* storage unavailable */ }
  }, [jobs])

  const facilities = useMemo(() => stid?.facilities ?? [], [stid])
  const selectedFacility = facilities.find(item => item.facility_id === selectedFacilityId) ?? facilities[0]
  const facilityEquipment = useMemo(
    () => (stid?.equipment ?? []).filter(asset => !selectedFacility || asset.facility_id === selectedFacility.facility_id),
    [stid, selectedFacility],
  )
  const selectedAsset = facilityEquipment.find(asset => asset.equipment_id === selectedAssetIds[selectedFacility?.facility_id ?? '']) ?? facilityEquipment[0]
  const selectedAssetId = selectedAsset?.equipment_id
  const setSelectedAssetId = useCallback((assetId: string) => {
    const facilityId = selectedFacility?.facility_id
    if (!facilityId || !facilityEquipment.some(asset => asset.equipment_id === assetId)) return
    setSelectedAssetIds(current => {
      const next = { ...current, [facilityId]: assetId }
      writePersistedSetup({ selectedAssetIds: next })
      return next
    })
  }, [facilityEquipment, selectedFacility])
  const facilityInstruments = useMemo(
    () => stid?.instruments.filter(item => !selectedFacility || item.facility_id === selectedFacility.facility_id) ?? [],
    [stid, selectedFacility],
  )
  const readings = useMemo(() => new Map(telemetry.map(item => [item.opcuaNodeId, item])), [telemetry])
  const instrumentByNode = useMemo(() => new Map((stid?.instruments ?? []).map(item => [item.opcua_node_id, item])), [stid])
  const equipmentById = useMemo(() => new Map((stid?.equipment ?? []).map(item => [item.equipment_id, item])), [stid])
  const facilityTelemetry = useMemo(() => {
    if (!stid || !selectedFacility) return telemetry
    const facilityTags = new Set(facilityEquipment.map(asset => asset.tag).filter(Boolean))
    return telemetry.filter(reading => {
      const instrument = instrumentByNode.get(reading.opcuaNodeId)
      if (instrument?.facility_id) return instrument.facility_id === selectedFacility.facility_id
      const tag = equipmentTagFromNode(reading.opcuaNodeId)
      return tag ? facilityTags.has(tag) : false
    })
  }, [telemetry, stid, selectedFacility, facilityEquipment, instrumentByNode])
  const mappedTelemetry = useMemo(() => facilityTelemetry.map(reading => ({ reading, instrument: instrumentByNode.get(reading.opcuaNodeId) })), [facilityTelemetry, instrumentByNode])
  const openOrders = useMemo(() => orders.filter(order => openStatuses.has(order.status.toLowerCase())), [orders])
  const facilityOpenOrders = useMemo(() => selectedFacility
    ? openOrders.filter(order => equipmentById.get(order.equipmentId)?.facility_id === selectedFacility.facility_id)
    : openOrders,
    [selectedFacility, openOrders, equipmentById],
  )
  const qualityIssueCount = useMemo(() => facilityTelemetry.filter(reading => ['bad', 'uncertain'].includes((reading.quality ?? '').toLowerCase())).length, [facilityTelemetry])
  const eventTimesSorted = useMemo(() => telemetry.map(row => Date.parse(row.eventTime)).filter(time => !Number.isNaN(time)).sort((a, b) => a - b), [telemetry])
  const medianEventMs = eventTimesSorted.length ? eventTimesSorted[Math.floor((eventTimesSorted.length - 1) / 2)] : 0
  const dataAgeMs = medianEventMs ? now - medianEventMs : Infinity
  const telemetryStatus: TelemetryStatus = !telemetry.length ? 'unavailable' : dataAgeMs < 60_000 ? 'live' : dataAgeMs < 300_000 ? 'delayed' : 'stale'
  const telemetryStatusLabel = telemetryStatus === 'live' ? 'Live' : telemetryStatus === 'delayed' ? 'Delayed' : telemetryStatus === 'stale' ? 'Stale' : 'Not connected'
  const telemetryAgeLabel = medianEventMs ? fmtSince(dataAgeMs) : ''
  const facilityHealth = useMemo<Record<TwinStatus, number>>(() => {
    const health = { ok: 0, warn: 0, crit: 0, nodata: 0 }
    for (const instrument of facilityInstruments) {
      const reading = readings.get(instrument.opcua_node_id)
      health[twinStatus({
        id: instrument.instrument_id,
        label: instrument.tag ?? instrument.instrument_id,
        nodeId: instrument.opcua_node_id,
        value: reading?.value,
        quality: reading?.quality,
        hasOpenIssue: openOrders.some(order => order.opcuaNodeId === instrument.opcua_node_id),
      })]++
    }
    return health
  }, [facilityInstruments, readings, openOrders])

  const telemetryExplorerSelection = useMemo<TelemetryExplorerSelection>(() => {
    const facilityId = selectedFacility?.facility_id
    const saved = facilityId ? telemetrySelections[facilityId] : undefined
    const assetSignals = stid?.instruments.filter(item => item.equipment_id === selectedAssetId) ?? []
    const signal = assetSignals.find(item => item.instrument_id === saved?.signalId) ?? assetSignals[0]
    return { assetId: selectedAssetId, signalId: signal?.instrument_id, range: saved?.range ?? '24h' }
  }, [selectedAssetId, selectedFacility, stid, telemetrySelections])

  const updateTelemetryExplorerSelection = useCallback((patch: Partial<TelemetryExplorerSelection>) => {
    if (!selectedFacility?.facility_id) return
    if (patch.assetId) setSelectedAssetId(patch.assetId)
    setTelemetrySelections(current => {
      const previous = current[selectedFacility.facility_id] ?? { range: '24h' as TelemetryHistoryRange }
      return { ...current, [selectedFacility.facility_id]: { ...previous, ...patch, range: patch.range ?? previous.range } }
    })
  }, [selectedFacility, setSelectedAssetId])

  const addWorkOrder = useCallback(async (equipmentId: string, instrumentId?: string, opcuaNodeId?: string) => {
    setMutationKey(opcuaNodeId ?? equipmentId)
    setNotice(undefined)
    try {
      const activeUser = user ?? await authenticate()
      if (!activeUser) return
      const record = await createWorkOrder(activeUser, equipmentId, instrumentId, opcuaNodeId)
      setOrders(current => [record, ...current])
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Work order creation failed.')
    } finally { setMutationKey(undefined) }
  }, [authenticate, user])

  const changeWorkOrderStatus = useCallback(async (id: string, status: string) => {
    const previous = orders
    setMutationKey(id)
    setOrders(current => current.map(order => order.id === id ? { ...order, status } : order))
    try { await updateWorkOrderStatus(id, status) }
    catch (error) {
      setOrders(previous)
      setNotice(error instanceof Error ? error.message : 'Work order update failed.')
    } finally { setMutationKey(undefined) }
  }, [orders])

  const removeWorkOrder = useCallback(async (id: string) => {
    const previous = orders
    setMutationKey(id)
    setOrders(current => current.filter(order => order.id !== id))
    try { await deleteWorkOrder(id) }
    catch (error) {
      setOrders(previous)
      setNotice(error instanceof Error ? error.message : 'Work order deletion failed.')
    } finally { setMutationKey(undefined) }
  }, [orders])

  const sendCopilotQuestion = useCallback(async (question: string) => {
    const text = question.trim()
    if (!text || copilotBusy) return
    const startedAt = Date.now()
    const chart = wantsChart(text)
    setCopilotBusy(true)
    setMessages(current => [...current, { role: 'user', text }, { role: 'agent', text: '', chart }])
    const setLastAgent = (value: string, meta?: ChatMessage['meta']) => setMessages(current => {
      const next = current.slice()
      next[next.length - 1] = { role: 'agent', text: value, chart, meta }
      return next
    })
    try {
      const answer = await askDataAgent(text, partial => setLastAgent(partial))
      setLastAgent(answer.text, { elapsedMs: Date.now() - startedAt, tokens: answer.usage?.total })
    } catch (error) {
      setLastAgent(error instanceof Error ? error.message : 'The Data Agent request failed.', { elapsedMs: Date.now() - startedAt })
    } finally { setCopilotBusy(false) }
  }, [copilotBusy])

  const resetCopilot = useCallback(() => {
    if (!copilotBusy) setMessages([INITIAL_MESSAGE])
  }, [copilotBusy])

  return {
    user,
    notice,
    jobs,
    now,
    stidState,
    telemetryState,
    operationsState,
    modelState,
    provisionState,
    streamState,
    stid,
    telemetry,
    facilities,
    selectedFacility,
    selectedFacilityId: selectedFacility?.facility_id,
    setSelectedFacilityId,
    selectedAsset,
    selectedAssetId,
    setSelectedAssetId,
    facilityEquipment,
    facilityInstruments,
    facilityTelemetry,
    mappedTelemetry,
    orders,
    inspections,
    spareParts,
    notifications,
    assetModels,
    openOrders,
    facilityOpenOrders,
    telemetryStatus,
    telemetryStatusLabel,
    telemetryAgeLabel,
    telemetryExplorerSelection,
    facilityHealth,
    messages,
    copilotBusy,
    mutationKey,
    counts: {
      facilities: facilities.length,
      assets: facilityEquipment.length,
      instruments: facilityInstruments.length,
      liveSignals: facilityTelemetry.length,
      qualityIssues: qualityIssueCount,
      openWorkOrders: facilityOpenOrders.length,
    },
    actions: {
      authenticate,
      refreshOperationalData,
      seedAndProvision,
      startStream,
      connectStid,
      connectTelemetry,
      updateTelemetryExplorerSelection,
      addWorkOrder,
      changeWorkOrderStatus,
      removeWorkOrder,
      sendCopilotQuestion,
      resetCopilot,
      refreshDiscovery: clearWorkspaceConfigCache,
    },
  }
}

export type HydroOperationsData = ReturnType<typeof useHydroOperationsDataController>

const HydroOperationsDataContext = createContext<HydroOperationsData | null>(null)

export function HydroOperationsDataProvider({ children }: { children: ReactNode }) {
  const value = useHydroOperationsDataController()
  return createElement(HydroOperationsDataContext.Provider, { value }, children)
}

export function useHydroOperationsData() {
  const value = useContext(HydroOperationsDataContext)
  if (!value) throw new Error('useHydroOperationsData must be used inside HydroOperationsDataProvider.')
  return value
}

export { fmtElapsed }