import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  beginInteractiveConnect, clearWorkspaceConfigCache, initAuth, isStidConfigured,
  queryLatestTelemetry, queryOntologyContract, queryStid, resetDataAgentConversation, resumePostSeedNotebook, resumeStreamingPipeline, resumeWeatherNotebooks,
  runWeatherNotebooks,
  refreshEnergyMap, resumeEnergyMapRefresh,
  startStreamingPipeline, type AgentArtifact, type AgentVisualization, type JobStatus, type OntologyContract, type StidData, type TelemetryHistoryRange, type TelemetryReading,
} from '../../services/fabric'
import {
  createWorkOrder, deleteWorkOrder, initializeRayfin, isRayfinConfigured, listAsset3DModels, listInspections,
  listMaintenanceNotifications, listSpareParts, listWorkOrders, signInToRayfin,
  subscribeWorkOrdersChanged, updateWorkOrderStatus, type AppUser, type Asset3DModelRecord, type InspectionRecord,
  type MaintenanceNotificationRecord, type SparePartRecord, type WorkOrderRecord,
} from '../../services/rayfin'
import { twinStatus, type TwinStatus } from '../../twin'
import { isFoundryConfigured, resetFoundryConversation, type AgentStep, type FoundryAnswer } from '../../services/copilot/foundry'
import { askCopilot } from '../../services/copilot/askCopilot'
import { ENERGY_MAP_ENABLED } from '../appFeatures'
import {
  failedExecutionEvents, uncertifiedFailureMessage,
  type OrchestrationEvent, type WorkOrderProposal,
} from '../../services/copilot/orchestration'

const openStatuses = new Set(['draft', 'approved', 'planned', 'scheduled', 'ready', 'in progress', 'in_progress', 'on hold', 'on_hold'])
const equipmentTagFromNode = (nodeId: string) => nodeId.match(/(?:^|;)s=([^.;]+)/)?.[1]?.trim() || undefined
const fmtSince = (ms: number) => { if (ms < 5000) return 'just now'; const s = Math.round(ms / 1000); if (s < 60) return `${s}s ago`; const m = Math.round(s / 60); if (m < 60) return `${m}m ago`; return `${Math.round(m / 60)}h ago` }
const fmtElapsed = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); const m = Math.floor(s / 60); const r = s % 60; return m ? `${m}m ${r}s` : `${r}s` }
const humanStatus = (status: JobStatus) => status === 'NotStarted' ? 'Queued' : status === 'InProgress' ? 'Running' : status

const deploymentStorageScope = [
  import.meta.env.VITE_FABRIC_WORKSPACE_ID ?? import.meta.env.VITE_RAYFIN_WORKSPACE_ID,
  import.meta.env.VITE_RAYFIN_EVENTHOUSE_ID,
  import.meta.env.VITE_RAYFIN_KQL_DATABASE_ID,
  import.meta.env.VITE_RAYFIN_LAKEHOUSE_ID,
  import.meta.env.VITE_RAYFIN_LAKEHOUSE_SQL_ENDPOINT_ID,
].filter(Boolean).join('.')
const scopedStorageKey = (key: string) => `${key}.${deploymentStorageScope || 'unconfigured'}`
const SETUP_STORAGE_KEY = scopedStorageKey('hydro.v2.setup.v1')
const LEGACY_DATA_CACHE_PREFIX = 'hydro.data-cache.v1.'
const JOBS_STORAGE_KEY = scopedStorageKey('hydro.jobs.v1')
const JOB_RESUME_MAX_AGE_MS = 30 * 60_000
const STREAM_ETA_MS = 5 * 60_000
const WEATHER_ETA_MS = 12 * 60_000
const MAP_REFRESH_MAX_AGE_MS = 3 * 60 * 60_000
const QUEUED_PCT_CAP = 15
const STID_READINESS_RETRIES = 12
const STID_READINESS_DELAY_MS = 5_000
const STID_POLL_MS = 30_000
const TELEMETRY_POLL_MS = 30_000
const TELEMETRY_AGE_TICK_MS = 15_000
const JOB_PROGRESS_TICK_MS = 1_000

type LoadState = 'idle' | 'loading' | 'connected' | 'unavailable' | 'error'
type ActionState = 'idle' | 'running' | 'complete' | 'error'
type TelemetryStatus = 'live' | 'delayed' | 'stale' | 'unavailable'
export type ProgressJob = { kind: 'seed' | 'stream' | 'weather' | 'map'; label: string; status: string; pct: number; startedAt: number; etaMs: number; endedAt?: number }
export type TelemetryExplorerSelection = { assetId?: string; signalId?: string; range: TelemetryHistoryRange }
export type ChatMessage = { role: 'user' | 'agent'; text: string; failed?: boolean; backendOwned?: boolean; artifacts?: AgentArtifact[]; visualizations?: AgentVisualization[]; models?: Asset3DModelRecord[]; steps?: AgentStep[]; orchestrationEvents?: OrchestrationEvent[]; proposals?: WorkOrderProposal[]; meta?: { elapsedMs: number; tokens?: number } }
type PersistedSetup = { provisioned?: boolean; stidConnected?: boolean; telemetryConnected?: boolean; selectedFacilityId?: string; selectedAssetIds?: Record<string, string> }

const INITIAL_MESSAGE: ChatMessage = { role: 'agent', text: 'Ask me about facilities, equipment, telemetry, weather, or maintenance work. The Supervisor coordinates the specialists your question needs. Sparky (Fabric IQ) calls the ontology-backed Fabric Data Agent with your original question and preserves its answer unchanged, without an extra rewriting model. Gauge handles precise direct reads and independent checks; Gale handles weather; Sleuth investigates competing explanations; Fixer reviews maintenance coverage and prepares explicitly requested approval-only drafts. No work order is saved without your review and approval.' }

function readPersistedSetup(): PersistedSetup {
  try { return JSON.parse(localStorage.getItem(SETUP_STORAGE_KEY) || '{}') as PersistedSetup }
  catch { return {} }
}

function writePersistedSetup(patch: PersistedSetup) {
  try { localStorage.setItem(SETUP_STORAGE_KEY, JSON.stringify({ ...readPersistedSetup(), ...patch })) }
  catch { /* storage unavailable */ }
}

function clearLegacyDataCaches() {
  try {
    const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
      .filter((key): key is string => Boolean(key?.startsWith(LEGACY_DATA_CACHE_PREFIX)))
    for (const key of keys) localStorage.removeItem(key)
  } catch { /* storage unavailable */ }
}

function readPersistedJobs(): Record<string, ProgressJob> {
  try {
    const saved = JSON.parse(localStorage.getItem(JOBS_STORAGE_KEY) || '{}') as Record<string, ProgressJob>
    const fresh: Record<string, ProgressJob> = {}
    for (const [key, job] of Object.entries(saved)) {
      if (job && (job.kind === 'seed' || job.kind === 'stream' || job.kind === 'weather' || job.kind === 'map') && typeof job.startedAt === 'number'
        && (job.kind !== 'map' || ENERGY_MAP_ENABLED)
        && Date.now() - job.startedAt < (job.kind === 'map' ? MAP_REFRESH_MAX_AGE_MS : JOB_RESUME_MAX_AGE_MS)) fresh[key] = job
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
  const [ontology, setOntology] = useState<OntologyContract | null>(null)
  const [ontologyError, setOntologyError] = useState<string>()
  const [ontologyLoading, setOntologyLoading] = useState(false)
  const ontologyRefreshVersion = useRef(0)
  const [stidSyncedAt, setStidSyncedAt] = useState<number>()
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
  const [weatherState, setWeatherState] = useState<ActionState>('idle')
  const [mapRefreshState, setMapRefreshState] = useState<ActionState>('idle')
  const [mapPrepared, setMapPrepared] = useState(false)
  const [mapRefreshMessage, setMapRefreshMessage] = useState<string>()
  const [notice, setNotice] = useState<string>()
  const [jobs, setJobs] = useState<Record<string, ProgressJob>>(() => readPersistedJobs())
  const [now, setNow] = useState(() => Date.now())
  const [telemetrySelections, setTelemetrySelections] = useState<Record<string, TelemetryExplorerSelection>>({})
  const [messages, setMessages] = useState<ChatMessage[]>(() => [INITIAL_MESSAGE])
  const [copilotBusy, setCopilotBusy] = useState(false)
  const [mutationKey, setMutationKey] = useState<string>()

  const setSelectedFacilityId = useCallback((facilityId: string) => {
    setSelectedFacilityIdState(facilityId)
    writePersistedSetup({ selectedFacilityId: facilityId })
  }, [])

  const applyStid = useCallback((data: StidData) => {
    setStid(data)
    setStidSyncedAt(Date.now())
    setSelectedFacilityIdState(current => {
      const next = current && data.facilities.some(facility => facility.facility_id === current)
        ? current
        : data.facilities[0]?.facility_id
      if (next) writePersistedSetup({ selectedFacilityId: next, stidConnected: true })
      return next
    })
  }, [])

  const clearStid = useCallback(() => {
    setStid(null)
    setStidSyncedAt(undefined)
    writePersistedSetup({ stidConnected: false })
  }, [])

  const clearTelemetry = useCallback(() => {
    setTelemetry([])
    writePersistedSetup({ telemetryConnected: false })
  }, [])

  const refreshOntology = useCallback(async (force = false) => {
    const version = ++ontologyRefreshVersion.current
    setOntologyLoading(true)
    try {
      const contract = await queryOntologyContract(force)
      if (version === ontologyRefreshVersion.current) {
        setOntology(contract)
        setOntologyError(undefined)
      }
      return contract
    } catch (error) {
      if (version === ontologyRefreshVersion.current) {
        setOntology(null)
        setOntologyError(error instanceof Error ? error.message : 'Ontology definition is unavailable. Refresh and check Fabric access.')
      }
      return null
    } finally {
      if (version === ontologyRefreshVersion.current) setOntologyLoading(false)
    }
  }, [])

  const refreshStid = useCallback(async (forceOntology = false) => {
    const contractRequest = refreshOntology(forceOntology)
    const [data] = await Promise.all([queryStid(), contractRequest])
    if (data) {
      applyStid(data)
      setStidState('connected')
      writePersistedSetup({ stidConnected: true })
    } else {
      clearStid()
      setStidState('unavailable')
    }
    return data
  }, [applyStid, clearStid, refreshOntology])

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
      clearTelemetry()
      setTelemetryState('unavailable')
    }
    return data
  }, [clearTelemetry])

  const connectStid = useCallback(async (opts?: { retries?: number; interactive?: boolean }) => {
    clearStid(); setStidState('loading'); setNotice(undefined)
    const retries = opts?.retries ?? STID_READINESS_RETRIES
    const interactive = opts?.interactive ?? true
    clearWorkspaceConfigCache()
    try {
      let data: StidData | null = null
      let lastError: unknown
      for (let attempt = 0; attempt <= retries && !data; attempt++) {
        try {
          data = await queryStid()
          lastError = undefined
        } catch (error) {
          lastError = error
        }
        if (data || attempt === retries) break
        // Retry source failures silently; open sign-in only when no Rayfin session exists.
        if (interactive && attempt === 0 && !lastError) {
          try {
            await beginInteractiveConnect('stid')
          } catch (error) {
            lastError = error
          }
        }
        await new Promise(resolve => setTimeout(resolve, STID_READINESS_DELAY_MS))
        clearWorkspaceConfigCache()
      }
      if (data) {
        applyStid(data)
        setStidState('connected')
        writePersistedSetup({ stidConnected: true })
        void refreshOntology()
      }
      else {
        clearStid()
        setStidState('unavailable')
        const detail = lastError instanceof Error ? ` ${lastError.message}` : ''
        setNotice(`The Rayfin Lakehouse connector is unavailable or needs source permission. Deploy the app and try Connect STID again.${detail}`)
      }
    } catch (error) {
      clearStid()
      setStidState('error')
      const message = error instanceof Error ? error.message : 'STID data is unavailable.'
      setNotice(message)
    }
  }, [applyStid, clearStid, refreshOntology])

  const connectTelemetry = useCallback(async () => {
    clearTelemetry(); setTelemetryState('loading'); setNotice(undefined)
    clearWorkspaceConfigCache()
    try { await loadTelemetry(true) }
    catch (error) {
      clearTelemetry()
      setTelemetryState('error')
      setNotice(error instanceof Error ? error.message : 'Telemetry is unavailable.')
    }
  }, [clearTelemetry, loadTelemetry])

  function beginProgress(key: ProgressJob['kind'], label: string, etaMs: number) {
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
        setNotice('Operational data re-seeded and Fabric provisioning complete. Connecting Lakehouse STID...')
      } else {
        setNotice(`Fabric provisioning ${humanStatus(status).toLowerCase()}.`)
      }
    } catch (error) {
      setProvisionState('error')
      setNotice(`Fabric provisioning failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally { endJob('seed') }
    if (completed) await connectStid({ interactive: false })
  }, [connectStid, loadOperationalData])

  const connectWeather = useCallback(async () => {
    if (weatherState === 'running' || jobs.weather) return
    setWeatherState('running'); setNotice(undefined)
    try {
      const activeUser = user ?? await authenticate()
      if (!activeUser) { setWeatherState('idle'); setNotice('Sign in with Fabric to load weather data.'); return }
      beginProgress('weather', 'Preparing operating areas, loading UKMet weather and rebuilding serving metrics. Aurora is opt-in and disabled by default...', WEATHER_ETA_MS)
      const status = await runWeatherNotebooks(update => updateJob('weather', humanStatus(update)))
      if (status === 'Completed') {
        setWeatherState('complete')
      } else {
        setWeatherState('error')
        setNotice(`Weather load ${humanStatus(status).toLowerCase()}.`)
      }
    } catch (error) {
      setWeatherState('error')
      setNotice(error instanceof Error ? error.message : 'Weather load failed.')
    } finally { endJob('weather') }
  }, [authenticate, jobs.weather, user, weatherState])

  const refreshMapData = useCallback(async (intent: 'prepare' | 'operational' | 'rebuild' = 'operational') => {
    if (!ENERGY_MAP_ENABLED) {
      setMapRefreshState('error')
      setMapRefreshMessage('Energy map provisioning is disabled for this deployment.')
      return
    }
    if (mapRefreshState === 'running' || jobs.map) return
    setMapRefreshState('running')
    const description = intent === 'prepare' ? 'Preparing the map using cached national sources'
      : intent === 'rebuild' ? 'Rebuilding all map sources, bypassing the source cache'
        : 'Updating operational map snapshots; national network and plant imports are skipped'
    setMapRefreshMessage(`${description} in Fabric...`)
    beginProgress('map', description, MAP_REFRESH_MAX_AGE_MS)
    let attached = false
    try {
      const status = await refreshEnergyMap(
        value => updateJob('map', humanStatus(value)),
        intent === 'rebuild',
        startedAt => {
          attached = true
          setMapRefreshMessage('A map refresh is already running. Monitoring that run instead of starting a duplicate; its original refresh mode is unchanged.')
          if (startedAt) setJobs(current => current.map
            ? { ...current, map: { ...current.map, startedAt: Date.parse(startedAt), label: 'Monitoring existing map refresh' } } : current)
        },
        intent === 'operational' ? 'operational' : 'all',
      )
      if (status === 'Completed') {
        setMapRefreshState('complete')
        if (!attached && intent !== 'operational') setMapPrepared(true)
        setMapRefreshMessage(attached
          ? 'The existing map refresh completed using its original refresh mode.'
          : `${description}. Map pipeline completed ${new Date().toLocaleString()}.`)
      } else {
        setMapPrepared(false)
        setMapRefreshState('error')
        setMapRefreshMessage(`Map refresh ${humanStatus(status).toLowerCase()}. A queued or running job continues in Fabric; starting again will reattach rather than duplicate it.`)
      }
    } catch (error) {
      console.error('Map data refresh failed.', error)
      setMapPrepared(false)
      setMapRefreshState('error')
      setMapRefreshMessage(error instanceof Error ? error.message : 'Map data refresh failed.')
    } finally { endJob('map') }
  }, [jobs.map, mapRefreshState])

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

  useEffect(() => subscribeWorkOrdersChanged(() => {
    void loadOperationalData().catch(error => {
      setNotice(error instanceof Error ? error.message : 'Work order changed, but refreshing operational data failed.')
    })
  }), [loadOperationalData])

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
    } else if (job.kind === 'weather') {
      setWeatherState('running')
      try {
        const status = await resumeWeatherNotebooks(update => updateJob('weather', humanStatus(update)), sinceIso)
        setWeatherState(status === 'Completed' ? 'complete' : 'error')
      } catch (error) {
        setWeatherState('error')
        setNotice(error instanceof Error ? error.message : 'Weather load failed.')
      } finally { endJob('weather') }
    } else if (job.kind === 'map') {
      setMapRefreshState('running')
      setMapRefreshMessage('Resuming progress for the map refresh already started in Fabric.')
      try {
        const status = await resumeEnergyMapRefresh(value => updateJob('map', humanStatus(value)), sinceIso)
        setMapRefreshState(status === 'Completed' ? 'complete' : 'error')
        setMapRefreshMessage(status === 'Completed'
          ? 'The configured map-data refresh completed.'
          : `Map refresh ${humanStatus(status).toLowerCase()}. The cloud run is not cancelled by closing this app.`)
      } catch (error) {
        console.error('Map refresh monitoring failed.', error)
        setMapRefreshState('error')
        setMapRefreshMessage(error instanceof Error ? error.message : 'Map refresh monitoring failed.')
      } finally { endJob('map') }
    } else {
      setStreamState('running')
      await awaitStream(() => resumeStreamingPipeline(status => updateJob('stream', humanStatus(status)), sinceIso), job.startedAt)
    }
  }, [awaitProvision, awaitStream])

  useEffect(() => {
    let cancelled = false
    clearLegacyDataCaches()
    const initialize = async () => {
      const initializeOperations = async () => {
        if (!isRayfinConfigured()) {
          setOperationsState('unavailable'); setModelState('unavailable')
          return
        }
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
      }

      const initializeFabricData = async () => {
        try { await initAuth() }
        catch (error) { if (!cancelled) setNotice(error instanceof Error ? error.message : 'Fabric authentication is unavailable.') }
        const initializeStid = async () => {
          if (!isStidConfigured()) { clearStid(); setStidState('unavailable'); return }
          setStidState(current => current === 'connected' ? current : 'loading')
          try {
            void refreshOntology()
            const data = await queryStid()
            if (cancelled) return
            if (data) { applyStid(data); setStidState('connected'); writePersistedSetup({ stidConnected: true }) }
            else { clearStid(); setStidState('unavailable') }
          } catch (error) {
            if (cancelled) return
            clearStid()
            setStidState('error')
            setNotice(error instanceof Error ? error.message : 'STID data is unavailable.')
          }
        }

        const initializeTelemetry = async () => {
          setTelemetryState(current => current === 'connected' ? current : 'loading')
          try { await loadTelemetry(false) }
          catch (error) {
            if (!cancelled) {
              clearTelemetry()
              setTelemetryState('error')
              setNotice(error instanceof Error ? error.message : 'Telemetry is unavailable.')
            }
          }
        }

        await Promise.all([initializeStid(), initializeTelemetry()])
      }

      await Promise.all([initializeOperations(), initializeFabricData()])

      if (!cancelled) await Promise.all(Object.values(readPersistedJobs()).map(job => resumeJob(job)))
    }
    void initialize()
    return () => { cancelled = true }
  }, [applyStid, clearStid, clearTelemetry, loadOperationalData, loadTelemetry, refreshOntology, resumeJob])

  const telemetryLive = telemetry.length > 0
  useEffect(() => {
    if (stidState !== 'connected') return
    let inFlight = false
    const refresh = async () => {
      if (inFlight || document.hidden) return
      inFlight = true
      try { await refreshStid() }
      catch (error) {
        clearStid()
        setStidState('error')
        setNotice(error instanceof Error ? error.message : 'STID data is unavailable.')
      }
      finally { inFlight = false }
    }
    const handleVisibility = () => { if (!document.hidden) void refresh() }
    const id = window.setInterval(refresh, STID_POLL_MS)
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [clearStid, refreshStid, stidState])

  useEffect(() => {
    if (!telemetryLive) return
    let inFlight = false
    const poll = async () => {
      if (inFlight || document.hidden) return
      inFlight = true
      try { await loadTelemetry(false) }
      catch (error) {
        clearTelemetry()
        setTelemetryState('error')
        setNotice(error instanceof Error ? error.message : 'Telemetry is unavailable.')
      }
      finally { inFlight = false }
    }
    const id = window.setInterval(poll, TELEMETRY_POLL_MS)
    return () => window.clearInterval(id)
  }, [clearTelemetry, telemetryLive, loadTelemetry])

  useEffect(() => {
    if (!telemetryLive) return
    const id = window.setInterval(() => setNow(Date.now()), TELEMETRY_AGE_TICK_MS)
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
          if (job.kind === 'map') { next[key] = job; continue }
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
    const id = window.setInterval(tick, JOB_PROGRESS_TICK_MS)
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
  const selectAsset = useCallback((facilityId: string, assetId: string) => {
    if (!stid?.facilities.some(facility => facility.facility_id === facilityId)) return
    if (!stid.equipment.some(asset => asset.facility_id === facilityId && asset.equipment_id === assetId)) return
    setSelectedFacilityIdState(facilityId)
    setSelectedAssetIds(current => {
      const next = { ...current, [facilityId]: assetId }
      writePersistedSetup({ selectedFacilityId: facilityId, selectedAssetIds: next })
      return next
    })
  }, [stid])
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
      const nodeOrders = openOrders.filter(order => order.opcuaNodeId === instrument.opcua_node_id)
      health[twinStatus({
        id: instrument.instrument_id,
        label: instrument.tag ?? instrument.instrument_id,
        nodeId: instrument.opcua_node_id,
        value: reading?.value,
        quality: reading?.quality,
        hasOpenIssue: nodeOrders.length > 0,
        hasCriticalIssue: nodeOrders.some(order => order.priority.toLowerCase() === 'critical'),
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

  // Tree browsing can jump across facilities, so facility/asset/signal must move together rather
  // than through the facility-scoped setters, which would still be keyed to the previous facility.
  const selectTelemetrySignal = useCallback((facilityId: string, assetId: string, signalId: string) => {
    setSelectedFacilityIdState(facilityId)
    setSelectedAssetIds(current => {
      const next = { ...current, [facilityId]: assetId }
      writePersistedSetup({ selectedFacilityId: facilityId, selectedAssetIds: next })
      return next
    })
    setTelemetrySelections(current => ({
      ...current,
      [facilityId]: { ...(current[facilityId] ?? { range: '24h' as TelemetryHistoryRange }), assetId, signalId },
    }))
  }, [])

  const addWorkOrder = useCallback(async (equipmentId: string, instrumentId?: string, opcuaNodeId?: string) => {
    setMutationKey(opcuaNodeId ?? equipmentId)
    setNotice(undefined)
    try {
      const activeUser = user ?? await authenticate()
      if (!activeUser) return
      const record = await createWorkOrder(activeUser, equipmentId, instrumentId, opcuaNodeId)
      setOrders(current => [record, ...current.filter(order => order.id !== record.id)])
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
    const text = question
    if (!text.trim() || copilotBusy) return
    const startedAt = Date.now()
    setCopilotBusy(true)
    setMessages(current => [...current, { role: 'user', text }, { role: 'agent', text: '' }])
    let liveText = ''
    let liveSteps: AgentStep[] | undefined
    let liveEvents: OrchestrationEvent[] | undefined
    let proposals: WorkOrderProposal[] | undefined
    let backendOwned: boolean | undefined
    let failed = false
    const paint = (meta?: ChatMessage['meta'], artifacts?: AgentArtifact[], visualizations?: AgentVisualization[], models?: Asset3DModelRecord[]) => setMessages(current => {
      const next = current.slice()
      next[next.length - 1] = { role: 'agent', text: liveText, failed, steps: liveSteps, orchestrationEvents: liveEvents, proposals, backendOwned, artifacts, visualizations, models, meta }
      return next
    })
    try {
      const answer: FoundryAnswer = await askCopilot(
          text,
          partial => { liveText = partial; paint() },
          steps => { liveSteps = steps; paint() },
          events => { liveEvents = events; paint() },
        )
      liveText = answer.text
      backendOwned = answer.backendOwned
      proposals = answer.proposals
      liveSteps = answer.steps ?? liveSteps
      liveEvents = answer.orchestrationEvents ?? liveEvents
      paint({ elapsedMs: Date.now() - startedAt, tokens: answer.usage?.total }, answer.artifacts, answer.visualizations, answer.models)
    } catch (error) {
      const detail = uncertifiedFailureMessage(error)
      failed = true
      backendOwned = true
      proposals = undefined
      liveText = detail
      liveEvents = failedExecutionEvents(liveEvents, detail)
      paint({ elapsedMs: Date.now() - startedAt })
    } finally { setCopilotBusy(false) }
  }, [copilotBusy])

  const resetCopilot = useCallback(() => {
    if (copilotBusy) return
    resetDataAgentConversation()
    resetFoundryConversation()
    setMessages([INITIAL_MESSAGE])
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
    weatherState,
    mapRefreshState,
    mapPrepared,
    mapRefreshMessage,
    stid,
    ontology,
    ontologyError,
    ontologyLoading,
    stidSyncedAt,
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
    foundryAvailable: isFoundryConfigured(),
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
      startStream,
      connectWeather,
      refreshMapData,
      connectStid,
      refreshStid,
      connectTelemetry,
      selectAsset,
      updateTelemetryExplorerSelection,
      selectTelemetrySignal,
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