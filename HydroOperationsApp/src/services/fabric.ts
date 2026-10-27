import { PublicClientApplication } from '@azure/msal-browser'
import type { Client } from '@modelcontextprotocol/sdk/client/index.js'
import type { AgentStep } from './agentSteps'
import { type AgentAnswer } from './assistantStream'
import { invokeVerifiedDataAgent, mapDataAgentName, requireDataAgentEndpoint, selectDataAgent, selectNamedDataAgent } from './artifactDiscovery'
import { discoverOntology, type OntologyDiscovery } from './ontologyDiscovery'
import { createOntologyCache, OntologyCacheInvalidatedError } from './ontologyCache'
import { parseOntologyContract, type OntologyContract } from './ontologyContract'
import { waitForDefinitionResult } from './ontologyDefinition'
import type { OntologyGraph } from './ontologyGraph'
import { readOntologySourceGraph, type OntologySourceGraph } from './ontologySourceGraph'
import { parseGraphBinding, queryBoundOntologyGraph, readOntologyGraphReadiness, type GraphReader, type OntologyGraphReadiness } from './ontologyGraphQuery'
import { requireV2Generation } from './ontologyArtifactDiscovery'
import { createSingleFlight } from './singleFlight'
import { createEntraTokens } from './entraTokens'
import { applyDataAgentProgress } from './dataAgentProgress'
import { contextualizeDataAgentQuestion } from './dataAgentConversation'
import { extractDataAgentVisualizations } from './dataAgentVisualizations'
import { ANSWER_PRESENTATION_CONTRACT, OPERATIONAL_EVIDENCE_CONTRACT } from './copilot/answerPresentation'
import { connectedLakehouse, lakehouseConnectionKey, ontologySourceConnectionKey, ontologySourceReaders, signInToRayfin } from './rayfin'
import { collectLakehousePages, lakehouseConnectorConfig, lakehouseTimestamp,
  requireLakehouseNumber, requireLakehouseText } from './lakehouseConnector'

export type { AgentAnswer, AgentArtifact, AgentUsage, AgentVisualization } from './assistantStream'
export type { OntologyContract } from './ontologyContract'
export type { OntologyGraph } from './ontologyGraph'

const clientId = import.meta.env.VITE_RAYFIN_AAD_CLIENT_ID as string | undefined
const tenantId = (import.meta.env.VITE_FABRIC_TENANT_ID ?? import.meta.env.VITE_RAYFIN_TENANT_ID) as string | undefined
const workspaceId = (import.meta.env.VITE_FABRIC_WORKSPACE_ID ?? import.meta.env.VITE_RAYFIN_WORKSPACE_ID) as string | undefined

// Explicit deployment selections take precedence; otherwise discover by name or the legacy defaults.
const pipelineName = (import.meta.env.VITE_RAYFIN_STREAM_PIPELINE_NAME as string | undefined) ?? '02_Pipe_Stream'
const postseedNotebookName = (import.meta.env.VITE_RAYFIN_POSTSEED_NOTEBOOK_NAME as string | undefined) ?? 'RTI_011_seed_sql_wire_graphql_agent'
const weatherPipelineName = '03_Pipe_Weather'
const eventhouseName = (import.meta.env.VITE_RAYFIN_EVENTHOUSE_NAME as string | undefined) ?? 'RTI_Demo_Eventhouse_V6'
const eventhouseId = import.meta.env.VITE_RAYFIN_EVENTHOUSE_ID as string | undefined
const kqlDatabaseId = import.meta.env.VITE_RAYFIN_KQL_DATABASE_ID as string | undefined
const kqlDashboardName = (import.meta.env.VITE_RAYFIN_KQL_DASHBOARD_NAME as string | undefined) ?? 'RTI_Demo_OPCUA_TelemetryStats_V6'
const configuredOntologyName = import.meta.env.VITE_RAYFIN_ONTOLOGY_NAME as string | undefined
const configuredEventhouseQueryUri = import.meta.env.VITE_RAYFIN_KQL_CLUSTER_URI as string | undefined
const configuredKqlDatabase = import.meta.env.VITE_RAYFIN_KQL_DATABASE as string | undefined
const configuredMapAgentName = import.meta.env.VITE_RAYFIN_MAP_DATA_AGENT_NAME as string | undefined

const msal = clientId && tenantId ? new PublicClientApplication({
  auth: { clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: location.origin },
  cache: { cacheLocation: 'localStorage' },
}) : undefined

const CONNECTOR_SCOPE = 'https://analysis.windows.net/powerbi/api/GraphQLApi.Execute.All'
const POWER_BI_ITEM_EXECUTE_SCOPE = 'https://analysis.windows.net/powerbi/api/Item.Execute.All'
const HOSTED_RUN_TOKEN_LIFETIME_MS = 12 * 60_000
// A named scope, not `.default`: `.default` only returns permissions already statically configured
// for the exact cluster audience, so Entra escalates to "Need admin approval" instead of consenting.
const kustoScope = (clusterUri: string) => `${clusterUri.replace(/\/$/, '')}/user_impersonation`
// Item.Read.All authorizes the per-item detail GET (e.g. Get Eventhouse → queryServiceUri);
// Workspace.Read.All only covers List Items, and Item.Execute.All only covers running jobs.
const FABRIC_SCOPES = ['https://api.fabric.microsoft.com/Workspace.Read.All', 'https://api.fabric.microsoft.com/Item.Read.All']
const FABRIC_JOB_SCOPES = [...FABRIC_SCOPES, 'https://api.fabric.microsoft.com/Item.Execute.All']
const DATA_AGENT_SCOPES = [...FABRIC_SCOPES, 'https://api.fabric.microsoft.com/DataAgent.Execute.All']
// Fabric Embed needs its own delegated scope. Named, not `.default`, for the same reason as kustoScope.
const EMBED_SCOPES = ['https://api.fabric.microsoft.com/Fabric.Embed', 'https://api.fabric.microsoft.com/Item.Read.All']
// Azure AI Foundry data plane. Named scope again, not `.default` — the caller needs the
// `Foundry User` role on the project for agent execution.
const FOUNDRY_AGENT_SCOPES = ['https://ai.azure.com/user_impersonation']

export type ConnectTarget = 'stid' | 'telemetry' | 'stream'

let initialization: Promise<void> | undefined
const entraTokens = msal && tenantId ? createEntraTokens(msal, ensureInit, tenantId) : undefined

/** Initialize MSAL and process any redirect returning from Entra. */
export async function initAuth(): Promise<void> {
  if (!msal) return
  await ensureInit()
}

async function ensureInit() {
  if (!msal) throw new Error('Microsoft Entra client configuration is missing.')
  if (!initialization) {
    const current = (async () => {
      await msal.initialize()
      const response = await msal.handleRedirectPromise()
      if (response?.account) msal.setActiveAccount(response.account)
    })()
    initialization = current
    void current.catch(() => { if (initialization === current) initialization = undefined })
  }
  await initialization
}

/** Acquire a token silently. Returns null when interactive sign-in/consent is required. */
async function silentToken(scopes: string[], forceRefresh = false, minimumLifetimeMs = 0): Promise<string | null> {
  if (!entraTokens) throw new Error('Microsoft Entra client configuration is missing.')
  return entraTokens.silent(scopes, forceRefresh, minimumLifetimeMs)
}

/** Acquire a token interactively via popup (redirects are blocked inside the Fabric iframe).
 *  Must be invoked from a user gesture. */
async function popupToken(scopes: string[]): Promise<string> {
  if (!entraTokens) throw new Error('Microsoft Entra client configuration is missing.')
  return entraTokens.popup(scopes)
}

/** Fabric discovery/read token. Job execution explicitly requests its additional scope. */
async function fabricToken(interactive: boolean, scopes = FABRIC_SCOPES): Promise<string | null> {
  const silent = await silentToken(scopes)
  if (silent) return silent
  if (!interactive) return null
  return popupToken(scopes)
}

// ---- Workspace artifact discovery (resolve ids/URIs by display name, never hardcode) ----
type WorkspaceItem = { id: string; type: string; displayName: string; folderId?: string }
type ResolvedConfig = OntologyDiscovery & { pipelineId?: string; postseedNotebookId?: string; eventhouseQueryUri?: string; kqlDatabase?: string; dataAgentUrl?: string; dataAgentId?: string; kqlDashboardId?: string }
let configCache: ResolvedConfig | null = null
let configPromise: Promise<ResolvedConfig | null> | undefined
let configRevision = 0

function requireWorkspaceId(): string {
  if (!workspaceId) throw new Error('Fabric workspace configuration is missing. Rebuild the app with Rayfin environment injection.')
  return workspaceId
}

/** Deployment-verified selections and endpoints used before slower live workspace discovery. */
function envConfig(): ResolvedConfig {
  return {
    pipelineId: import.meta.env.VITE_RAYFIN_STREAM_PIPELINE_ID as string | undefined,
    postseedNotebookId: import.meta.env.VITE_RAYFIN_POSTSEED_NOTEBOOK_ID as string | undefined,
    eventhouseQueryUri: configuredEventhouseQueryUri,
    kqlDatabase: configuredKqlDatabase,
    kqlDashboardId: import.meta.env.VITE_RAYFIN_KQL_DASHBOARD_ID as string | undefined,
  }
}

async function listItems(token: string, signal?: AbortSignal): Promise<WorkspaceItem[]> {
  const items: WorkspaceItem[] = []
  let nextUrl: string | undefined = `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items`
  while (nextUrl) {
    const res = await fetch(nextUrl, { headers: { Authorization: `Bearer ${token}` }, signal })
    if (!res.ok) throw new Error(`Workspace listing failed (${res.status}).`)
    const page = await res.json() as { value?: WorkspaceItem[]; continuationUri?: string; continuationToken?: string }
    items.push(...(page.value ?? []))
    nextUrl = page.continuationUri
    if (!nextUrl && page.continuationToken) {
      nextUrl = `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items?continuationToken=${encodeURIComponent(page.continuationToken)}`
    }
  }
  return items
}

/** Discover artifact ids/URIs from the workspace by display name; fall back to build-time env values.
 *  Discovered values are cached for the session so no id can go stale. */
async function ensureConfig(interactive: boolean, forceRefresh = false): Promise<ResolvedConfig | null> {
  if (forceRefresh) {
    clearWorkspaceConfigCache()
  }
  if (configCache) return configCache
  const revision = configRevision
  const promise = configPromise ?? discoverConfig(interactive, revision)
  configPromise = promise
  try {
    const config = await promise
    return revision === configRevision ? config : ensureConfig(interactive)
  } finally {
    if (configPromise === promise) configPromise = undefined
  }
}

async function discoverConfig(interactive: boolean, revision: number): Promise<ResolvedConfig | null> {
  const env = envConfig()
  const token = await fabricToken(interactive)
  if (!token) {
    // Not signed in / no discovery consent — use env fallback when it carries anything usable.
    return (env.eventhouseQueryUri || env.pipelineId || env.postseedNotebookId) ? env : null
  }
  try {
    const items = await listItems(token)
    const find = (type: string, name: string) => items.find(i => i.type === type && i.displayName === name)
    const pipeline = find('DataPipeline', pipelineName)
    const notebook = find('Notebook', postseedNotebookName)
    const eh = eventhouseId
      ? items.find(i => i.type === 'Eventhouse' && i.id === eventhouseId)
      : find('Eventhouse', eventhouseName) ?? items.find(i => i.type === 'Eventhouse')
    if (eventhouseId && !eh) throw new Error('The configured Eventhouse was not found in this workspace. Refresh deployment configuration.')
    let eventhouseQueryUri: string | undefined
    let kqlDatabase: string | undefined
    if (eh) {
      const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/eventhouses/${eh.id}`, { headers: { Authorization: `Bearer ${token}` } })
      if (res.ok) {
        const props = ((await res.json()) as { properties?: { queryServiceUri?: string; databasesItemIds?: string[] } }).properties
        eventhouseQueryUri = props?.queryServiceUri
        const databaseIds = props?.databasesItemIds ?? []
        const database = kqlDatabaseId || env.kqlDatabase
          ? items.find(i => i.type === 'KQLDatabase' && databaseIds.includes(i.id)
            && (kqlDatabaseId ? i.id === kqlDatabaseId : i.displayName === env.kqlDatabase))
          : items.find(i => i.id === databaseIds[0])
        if ((kqlDatabaseId || env.kqlDatabase) && !database) {
          throw new Error('The configured KQL database does not belong to the selected Eventhouse. Refresh deployment configuration.')
        }
        kqlDatabase = database?.displayName ?? eh.displayName
      } else {
        // 401/403 here usually means the token lacks Item.Read.All / Eventhouse.Read.All.
        console.warn(`Get Eventhouse failed (${res.status}) — telemetry query URI unresolved.`, (await res.text()).slice(0, 300))
      }
    }
    // Published Data Agents use Fabric's official MCP-over-Streamable-HTTP endpoint.
    const dashboard = find('KQLDashboard', kqlDashboardName) ?? items.find(i => i.type === 'KQLDashboard')
    const semantic = await discoverOntology(items, configuredOntologyName, {
      metadata: async id => {
        const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/ontologies/${id}`, { headers: { Authorization: `Bearer ${token}` } })
        if (!response.ok) throw new Error(`Ontology metadata request failed (${response.status}). Check Fabric item read access and refresh.`)
        return response.json() as Promise<{ properties?: { generation?: number } }>
      },
      definition: async id => {
        const signal = AbortSignal.timeout(30_000)
        const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/ontologies/${id}/getDefinition`, {
          method: 'POST', headers: { Authorization: `Bearer ${token}` }, signal,
        })
        return waitForDefinitionResult(response, token, signal)
      },
    })
    const da = semantic.ontologyGeneration === 2 && !semantic.ontologyError ? selectDataAgent(items) : undefined
    const dataAgentUrl = da
      ? `https://api.fabric.microsoft.com/v1/mcp/workspaces/${requireWorkspaceId()}/dataagents/${da.id}/agent`
      : undefined
    if (revision !== configRevision) return null
    configCache = {
      pipelineId: pipeline?.id ?? env.pipelineId,
      postseedNotebookId: notebook?.id ?? env.postseedNotebookId,
      eventhouseQueryUri: eventhouseQueryUri ?? env.eventhouseQueryUri,
      kqlDatabase: kqlDatabase ?? env.kqlDatabase,
      dataAgentUrl,
      dataAgentId: da?.id,
      kqlDashboardId: dashboard?.id ?? env.kqlDashboardId,
      ...semantic,
    }
    return configCache
  } catch (error) {
    if (revision !== configRevision) return null
    console.warn('Workspace discovery failed; using configured fallback values.', error)
    return { ...env, ontologyError: error instanceof Error ? error.message : 'Workspace discovery failed. Sign in and refresh.' }
  }
}

// ---- Fabric Embed (Real-Time Dashboard) ----

export type DashboardEmbedTarget = { workspaceId: string; itemId: string }

/** Resolve the Real-Time Dashboard to embed; null when the workspace has none. */
export async function getDashboardEmbedTarget(interactive: boolean): Promise<DashboardEmbedTarget | null> {
  const config = await ensureConfig(interactive)
  if (!config?.kqlDashboardId) return null
  return { workspaceId: requireWorkspaceId(), itemId: config.kqlDashboardId }
}

// The embedded dashboard requests tokens per audience. A Kusto `.default` would escalate to
// "Need admin approval" for the same reason kustoScope() exists, so rewrite those too.
function embedScopes(requested?: string[]): string[] {
  if (!requested?.length) return EMBED_SCOPES
  return requested.map(scope => /\.kusto\.(fabric\.microsoft\.com|windows\.net)\/\.default$/i.test(scope)
    ? kustoScope(scope.replace(/\/\.default$/i, ''))
    : scope)
}

/** A token for the Fabric Embed iframe. Silent first; popup only when interactive is allowed. */
export async function fabricEmbedToken(interactive: boolean, requested?: string[]): Promise<string | null> {
  const scopes = embedScopes(requested)
  const silent = await silentToken(scopes)
  if (silent) return silent
  if (!interactive) return null
  return popupToken(scopes)
}

export async function foundryAgentToken(interactive: boolean): Promise<string | null> {
  const silent = await silentToken(FOUNDRY_AGENT_SCOPES)
  if (silent) return silent
  if (!interactive) return null
  return popupToken(FOUNDRY_AGENT_SCOPES)
}

export async function hostedSourceTokens() {
  const config = await ensureConfig(false)
  const cluster = configuredEventhouseQueryUri ?? config?.eventhouseQueryUri
  if (!cluster) throw new Error('The hosted source requires the authoritative Eventhouse endpoint.')
  // A hosted run can remain active for ten minutes, so never submit a lease that could expire during it. Cached
  // leases with enough remaining life are reused; only near-expiry ones pay an Entra round trip.
  const [fabric, foundry, graphql, kusto] = await Promise.all([
    silentToken([...new Set([...FABRIC_JOB_SCOPES, ...DATA_AGENT_SCOPES])], false, HOSTED_RUN_TOKEN_LIFETIME_MS),
    silentToken(FOUNDRY_AGENT_SCOPES, false, HOSTED_RUN_TOKEN_LIFETIME_MS),
    silentToken([CONNECTOR_SCOPE, POWER_BI_ITEM_EXECUTE_SCOPE], false, HOSTED_RUN_TOKEN_LIFETIME_MS),
    silentToken([kustoScope(cluster)], false, HOSTED_RUN_TOKEN_LIFETIME_MS),
  ])
  if (!fabric || !foundry || !graphql || !kusto) {
    throw new Error('Renew Fabric, Foundry, STID and telemetry access through the existing sign-in/connection controls.')
  }
  return { fabric, foundry, graphql, kusto }
}

/** Force a fresh workspace discovery on the next call (e.g. after RTI_011 provisions new items). */
export function clearWorkspaceConfigCache() {
  configRevision++
  configCache = null
  configPromise = undefined
  invalidateDataAgentVerification()
  invalidateDataAgentMcpSession()
  dataAgentMcpTool = undefined
  dataAgentUserQuestions = []
  ontologySourceGraphCache.clear()
  lastOntologySourceConnection = undefined
}

// ---- Fabric item jobs: trigger + poll for live progress ----
export type JobStatus = 'NotStarted' | 'InProgress' | 'Completed' | 'Failed' | 'Cancelled' | 'Deduped'
export type JobProgress = (status: JobStatus) => void
const TERMINAL_STATUSES: JobStatus[] = ['Completed', 'Failed', 'Cancelled', 'Deduped']
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

type JobInstance = { id?: string; status?: JobStatus; startTimeUtc?: string; failureReason?: { message?: string } }
const ACTIVE_STATUSES: JobStatus[] = ['NotStarted', 'InProgress']
function jobStartUtc(value: string): string {
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`
  if (!Number.isFinite(Date.parse(normalized))) throw new Error('Fabric returned an invalid job start time.')
  return new Date(normalized).toISOString()
}

/** Read all job-history pages before deciding whether a new run is safe. */
async function jobInstances(token: string, itemId: string): Promise<JobInstance[]> {
  const path = `/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances`
  const base = `https://api.fabric.microsoft.com${path}`
  let url: string | undefined = base
  const seen = new Set<string>()
  const items: JobInstance[] = []
  while (url) {
    const parsed = new URL(url, base)
    if (parsed.origin !== 'https://api.fabric.microsoft.com' || parsed.pathname !== path
      || parsed.username || parsed.password || parsed.hash || seen.has(parsed.href) || seen.size >= 100) {
      throw new Error('Fabric returned an invalid job-history continuation; no new job was started.')
    }
    seen.add(parsed.href)
    const res = await fetch(parsed.href, { headers: { Authorization: `Bearer ${token}` } })
    if (!res.ok) throw new Error(`Cannot read Fabric job history (${res.status}); no new job was started.`)
    const page = await res.json() as { value?: JobInstance[]; continuationUri?: string; continuationToken?: string }
    if (!Array.isArray(page.value)) throw new Error('Fabric job history did not return an instance list.')
    items.push(...page.value)
    url = page.continuationUri ?? (page.continuationToken ? `${base}?continuationToken=${encodeURIComponent(page.continuationToken)}` : undefined)
  }
  return items
}

async function latestInstance(token: string, itemId: string, sinceIso: string): Promise<JobInstance | undefined> {
  return (await jobInstances(token, itemId))
    .filter(i => !i.startTimeUtc || jobStartUtc(i.startTimeUtc) >= sinceIso)
    .sort((a, b) => (b.startTimeUtc ?? '').localeCompare(a.startTimeUtc ?? ''))[0]
}

/** Newest still-running (NotStarted/InProgress) instance for an item, so callers can reattach
 *  instead of starting a duplicate run after a refresh or repeated clicks. */
async function activeInstance(token: string, itemId: string): Promise<JobInstance | undefined> {
  return (await jobInstances(token, itemId))
    .filter(i => i.status && ACTIVE_STATUSES.includes(i.status))
    .sort((a, b) => (b.startTimeUtc ?? '').localeCompare(a.startTimeUtc ?? ''))[0]
}

/** Trigger a Fabric item job and poll until a terminal (or requested `stopAt`) status, reporting progress. */
async function runJob(
  itemId: string,
  jobType: string,
  onStatus?: JobProgress,
  opts?: {
    stopAt?: JobStatus[]
    timeoutMs?: number
    reuseActive?: boolean
    pipelineParameters?: Record<string, string | number | boolean>
    onReuseActive?: (startedAt?: string) => void
    parameters?: Array<{
      name: string
      value: string | number | boolean
      type: 'Text' | 'Boolean' | 'Integer' | 'Number' | 'DateTime' | 'Guid' | 'Automatic' | 'VariableReference'
    }>
  },
): Promise<JobStatus> {
  const token = await fabricToken(true, FABRIC_JOB_SCOPES)
  if (!token) throw new Error('Fabric sign-in is required.')
  let startedAt = new Date(Date.now() - 5000).toISOString()
  let location: string | null = null
  let pollDelayMs = 4000
  // Reattach to an already-running instance instead of starting a duplicate (survives refresh / repeat clicks).
  const active = opts?.reuseActive ? await activeInstance(token, itemId) : undefined
  if (active) {
    const activeStarted = active.startTimeUtc ? jobStartUtc(active.startTimeUtc) : undefined
    opts?.onReuseActive?.(activeStarted)
    if (active.id) location = `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances/${active.id}`
    if (activeStarted) startedAt = new Date(Date.parse(activeStarted) - 5000).toISOString()
    if (!location && !activeStarted) throw new Error('A Fabric job is active but its identity cannot be resolved; no duplicate was started.')
    if (active.status) onStatus?.(active.status)
  } else {
    const isNotebookRun = jobType === 'RunNotebook'
    const triggerUrl = isNotebookRun
      ? `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/notebooks/${itemId}/jobs/execute/instances?beta=false`
      : `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances?jobType=${jobType}`

    const requestBody = isNotebookRun && opts?.parameters?.length
      ? JSON.stringify({ parameters: opts.parameters })
      : opts?.pipelineParameters ? JSON.stringify({ executionData: { parameters: opts.pipelineParameters } }) : undefined

    const trigger = await fetch(triggerUrl, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        ...(requestBody ? { 'Content-Type': 'application/json' } : {}),
      },
      body: requestBody,
    })
    if (!trigger.ok && trigger.status !== 202) {
      const detail = await trigger.text().catch(() => '')
      throw new Error(`Job start failed (${trigger.status})${detail ? `: ${detail.slice(0, 500)}` : '.'}`)
    }
    const retryAfter = Number(trigger.headers.get('Retry-After'))
    if (Number.isFinite(retryAfter) && retryAfter > 0) pollDelayMs = Math.max(pollDelayMs, retryAfter * 1000)
    const candidate = trigger.headers.get('Location')
    if (candidate) {
      const parsed = new URL(candidate, 'https://api.fabric.microsoft.com')
      const prefix = `/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances/`
      if (parsed.origin === 'https://api.fabric.microsoft.com' && !parsed.username && !parsed.password
        && !parsed.search && !parsed.hash && parsed.pathname.startsWith(prefix)
        && /^[0-9a-f-]{36}$/i.test(parsed.pathname.slice(prefix.length))) location = parsed.href
    }
  }
  const stopAt = opts?.stopAt ?? TERMINAL_STATUSES
  const deadline = Date.now() + (opts?.timeoutMs ?? 10 * 60_000)
  let last: JobStatus = active?.status ?? 'NotStarted'
  let readFailures = 0
  onStatus?.(last)
  while (Date.now() < deadline) {
    await delay(pollDelayMs)
    pollDelayMs = 4000
    const pollToken = await fabricToken(false)
    if (!pollToken) throw new Error('Fabric sign-in expired while monitoring the job. The cloud run is not cancelled; sign in and resume monitoring.')
    let status: JobStatus | undefined
    let failure: string | undefined
    try {
      if (location) {
        const res = await fetch(location, { headers: { Authorization: `Bearer ${pollToken}` } })
        if (!res.ok) throw new Error(`Fabric job status could not be read (${res.status}).`)
        const body = await res.json() as JobInstance
        status = body.status; failure = body.failureReason?.message
      } else {
        const inst = await latestInstance(pollToken, itemId, startedAt)
        status = inst?.status; failure = inst?.failureReason?.message
      }
      readFailures = 0
    } catch (error) {
      console.warn('Fabric job monitoring read failed.', error)
      if (++readFailures >= 3) throw new Error('Fabric job monitoring was interrupted. The cloud run is not cancelled; reconnect to resume progress.', { cause: error })
      continue
    }
    if (!status) continue
    if (status !== last) { last = status; onStatus?.(status) }
    if (status === 'Failed') throw new Error(failure || 'Fabric job failed.')
    if (stopAt.includes(status)) return status
  }
  return last
}

/** Re-attach to the newest instance of an already-triggered job — used to resume progress after a page reload. */
async function pollLatestInstance(itemId: string, sinceIso: string, onStatus?: JobProgress, opts?: { stopAt?: JobStatus[]; timeoutMs?: number; instanceId?: string }): Promise<JobStatus> {
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  const stopAt = opts?.stopAt ?? TERMINAL_STATUSES
  const deadline = Date.now() + (opts?.timeoutMs ?? 10 * 60_000)
  let last: JobStatus = 'NotStarted'
  let readFailures = 0
  onStatus?.(last)
  while (Date.now() < deadline) {
    const pollToken = await fabricToken(false)
    if (!pollToken) throw new Error('Fabric sign-in expired while monitoring the job. The cloud run is not cancelled; sign in and resume monitoring.')
    let status: JobStatus | undefined
    let failure: string | undefined
    try {
      let inst: JobInstance | undefined
      if (opts?.instanceId) {
        const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances/${opts.instanceId}`, {
          headers: { Authorization: `Bearer ${pollToken}` },
        })
        if (!response.ok) throw new Error(`Fabric job status could not be read (${response.status}).`)
        inst = await response.json() as JobInstance
      } else inst = await latestInstance(pollToken, itemId, sinceIso)
      status = inst?.status; failure = inst?.failureReason?.message
      readFailures = 0
    } catch (error) {
      console.warn('Fabric job monitoring read failed.', error)
      if (++readFailures >= 3) throw new Error('Fabric job monitoring was interrupted. The cloud run is not cancelled; reconnect to resume progress.', { cause: error })
      await delay(4000); continue
    }
    if (status) {
      if (status !== last) { last = status; onStatus?.(status) }
      if (status === 'Failed') throw new Error(failure || 'Fabric job failed.')
      if (stopAt.includes(status)) return status
    }
    await delay(4000)
  }
  return last
}

/** Sign in / consent for a resource using a popup; the caller then retries its query. */
export async function beginInteractiveConnect(target: ConnectTarget) {
  if (target === 'stid') {
    lakehouseConnectionKey()
    await signInToRayfin()
    return
  }
  if (!msal) throw new Error('Microsoft Entra client configuration is missing.')
  const knownConfig = configCache ?? envConfig()
  if (target === 'telemetry') {
    if (knownConfig.eventhouseQueryUri) {
      await popupToken([kustoScope(knownConfig.eventhouseQueryUri)])
      return
    }
  }

  const config = await ensureConfig(true)
  if (target === 'telemetry') {
    if (!config?.eventhouseQueryUri) throw new Error('No Eventhouse found in the workspace.')
    throw new Error('Fabric access is connected. Click Connect telemetry again to authorize the Eventhouse.')
  }
  // 'stream' needs only the Fabric token acquired by workspace discovery.
}

export type Facility = {
  facility_id: string
  facility_name: string
  type?: string
  country?: string
  lat?: string | number
  lon?: string | number
  commissioned_date?: string
}

export type Equipment = {
  equipment_id: string
  facility_id: string
  system_id: string
  equipment_type_code?: string
  equipment_type_name?: string
  tag?: string
  manufacturer?: string
  model?: string
  criticality?: number
  install_date?: string
  status?: string
  is_active?: boolean
}

export type Instrument = {
  opcua_node_id: string
  tag?: string
  instrument_id: string
  equipment_id: string
  system_id: string
  facility_id: string
  unit?: string
  instrument_type?: string
  is_active?: boolean
}

export type System = {
  system_id: string
  facility_id: string
  system_name?: string
  oag_rds_system_code?: string
}

const ONTOLOGY_CONTRACT_TTL_MS = 15 * 60_000
const ontologyContractCache = createOntologyCache<OntologyContract>(ONTOLOGY_CONTRACT_TTL_MS)
const ontologyGraphCache = createOntologyCache<OntologyGraph>(5 * 60_000)
const ontologyGraphReadinessCache = createOntologyCache<OntologyGraphReadiness>(5 * 60_000)
const ontologySourceGraphCache = createOntologyCache<OntologySourceGraph>(30_000)
let lastOntologySourceConnection: string | undefined
const configuredGraphBindingValue = import.meta.env.VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING as string | undefined

async function withCurrentConfig<T>(load: (config: ResolvedConfig | null) => Promise<T>): Promise<T> {
  for (;;) {
    const config = await ensureConfig(false)
    const revision = configRevision
    try {
      const result = await load(config)
      if (revision === configRevision) return result
    } catch (error) {
      if (revision === configRevision && !(error instanceof OntologyCacheInvalidatedError)) throw error
    }
  }
}

async function readBoundOntologyContract(ontologyId: string, displayName: string, force: boolean): Promise<OntologyContract | null> {
  return ontologyContractCache.read(`${requireWorkspaceId()}:${ontologyId}`, force, async () => {
    const token = await fabricToken(false)
    if (!token) throw new Error('Sign in with Fabric item read access to refresh the Ontology definition.')
    const signal = AbortSignal.timeout(30_000)
    const headers = { Authorization: `Bearer ${token}` }
    const metadataResponse = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/ontologies/${ontologyId}`, {
      headers, signal,
    })
    if (!metadataResponse.ok) throw new Error(`Live ontology generation verification failed (${metadataResponse.status}).`)
    const metadata = await metadataResponse.json() as { properties?: { generation?: unknown } }
    requireV2Generation(metadata.properties?.generation)
    const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/ontologies/${ontologyId}/getDefinition`, {
      method: 'POST', headers, signal,
    })
    const definition = await waitForDefinitionResult(response, token, signal)
    return parseOntologyContract(ontologyId, displayName, definition, metadata.properties?.generation)
  })
}

export async function queryOntologyContract(force = false): Promise<OntologyContract | null> {
  if (configuredGraphBindingValue?.trim()) {
    const binding = parseGraphBinding(configuredGraphBindingValue, requireWorkspaceId())
    return readBoundOntologyContract(binding.ontologyId, configuredOntologyName ?? 'Fabric Ontology', force)
  }
  return withCurrentConfig(async config => {
    if (config?.ontologyError) {
      ontologyContractCache.clear()
      configCache = null
      throw new Error(config.ontologyError)
    }
    if (!config?.ontologyId) { ontologyContractCache.clear(); throw new Error('Sign in and select a verified Ontology v2 before loading governed topology.') }
    if (!force && config.ontologyContract && config.ontologyReadAt !== undefined
      && Date.now() - config.ontologyReadAt < ONTOLOGY_CONTRACT_TTL_MS) return config.ontologyContract
    config.ontologyContract = undefined
    config.ontologyReadAt = undefined
    return ontologyContractCache.read(`${requireWorkspaceId()}:${config.ontologyId}`, force, async () => {
      const token = await fabricToken(false)
      if (!token) throw new Error('Sign in with Fabric item read access to refresh the Ontology definition.')
      const signal = AbortSignal.timeout(30_000)
      const metadataResponse = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/ontologies/${config.ontologyId}`, {
        headers: { Authorization: `Bearer ${token}` }, signal,
      })
      if (!metadataResponse.ok) throw new Error(`Live ontology generation verification failed (${metadataResponse.status}).`)
      const metadata = await metadataResponse.json() as { properties?: { generation?: unknown } }
      requireV2Generation(metadata.properties?.generation)
      const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/ontologies/${config.ontologyId}/getDefinition`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }, signal,
      })
      const definition = await waitForDefinitionResult(response, token, signal)
      const contract = parseOntologyContract(config.ontologyId!, config.ontologyName ?? 'Fabric Ontology', definition, metadata.properties?.generation)
      config.ontologyContract = contract
      config.ontologyReadAt = Date.now()
      return contract
    })
  })
}

async function readBoundOntologyGraph(binding: ReturnType<typeof parseGraphBinding>, force: boolean): Promise<OntologyGraph | null> {
  let request: GraphReader | undefined
  const getRequest = async (): Promise<GraphReader> => {
    if (request) return request
    const token = await fabricToken(false)
    if (!token) throw new Error('Sign in with Fabric graph read access before checking ontology graph readiness.')
    request = (url, init) => {
      const headers = new Headers(init?.headers)
      headers.set('Authorization', `Bearer ${token}`)
      return fetch(url, { ...init, headers, signal: init?.signal ?? AbortSignal.timeout(120_000) })
    }
    return request
  }
  const readiness = await ontologyGraphReadinessCache.read(JSON.stringify(binding), force, async () =>
    readOntologyGraphReadiness(binding, await getRequest())).catch(error => {
    ontologyGraphCache.clear()
    throw error
  })
  if (!readiness) {
    ontologyGraphCache.clear()
    throw new Error('Graph readiness could not be verified. Materialize and refresh the selected ontology graph, then retry.')
  }
  const ontology = await queryOntologyContract(force)
  if (!ontology || ontology.id.toLowerCase() !== binding.ontologyId.toLowerCase()) {
    throw new Error('Ontology selection changed during graph loading. Refresh discovery.')
  }
  return ontologyGraphCache.read(JSON.stringify([binding, ontology]), force, async () => {
    return queryBoundOntologyGraph(binding, ontology, await getRequest(), readiness)
  })
}

export async function queryOntologyGraph(force = false): Promise<OntologyGraph | null> {
  return readBoundOntologyGraph(parseGraphBinding(configuredGraphBindingValue, requireWorkspaceId()), force)
}

export async function connectOntologyAccess(): Promise<void> {
  ontologySourceGraphCache.clear()
  ontologyGraphCache.clear()
  ontologyGraphReadinessCache.clear()
  ontologyContractCache.clear()
  if (!await fabricToken(true)) throw new Error('Fabric ontology-read sign-in did not establish access. Retry from the graph connection control.')
}

export async function queryOntologySourceGraph(force = false): Promise<OntologySourceGraph | null> {
  const connection = ontologySourceConnectionKey()
  const changedUserOrTarget = lastOntologySourceConnection !== connection
  if (force || changedUserOrTarget) ontologySourceGraphCache.clear()
  lastOntologySourceConnection = connection
  const ontology = await queryOntologyContract(force || changedUserOrTarget)
  if (!ontology) throw new Error('No verified Ontology is selected for the live graph.')
  return ontologySourceGraphCache.read(JSON.stringify([connection, ontology]), force, async () => {
    const graph = await readOntologySourceGraph(ontology, ontologySourceReaders())
    if (ontologySourceConnectionKey() !== connection) throw new Error('The signed-in user or connector target changed while reading the graph. Refresh to query the current identity.')
    return graph
  })
}

export type StidData = {
  facilities: Facility[]; systems: System[]; equipment: Equipment[]; instruments: Instrument[]
  inventoryComplete: boolean
}

export type WeatherLocation = {
  location_id: string
  location_name: string
  latitude: number
  longitude: number
  elevation_m?: number
}

export type WeatherArea = {
  area_id: string
  area_name: string
  geometry_geojson: string
  crs: string
  metadata_json?: string
}

export const WEATHER_VARIABLES = [
  'temperature', 'precipitation', 'pressure', 'relative_humidity', 'dew_point',
  'solar_radiation', 'wind_speed', 'wind_gust', 'wind_direction',
] as const
export type WeatherVariableId = typeof WEATHER_VARIABLES[number]

/** The serving tables are pivoted, so one row carries every variable for a single valid time. */
type WeatherValues = Partial<Record<WeatherVariableId, number | null>>

export type WeatherForecast = WeatherValues & {
  source_id: string
  target_kind: 'location' | 'area'
  target_id: string
  reference_time_utc: string
  valid_time_utc: string
  lead_hours: number
  precipitation_interval_hours?: number
  cumulative_precipitation?: number
  rainfall_volume_m3?: number
  cumulative_rainfall_volume_m3?: number
}

export type WeatherObservation = WeatherValues & {
  source_id: string
  location_id: string
  observed_at_utc: string
}

export type WeatherVariable = {
  variable_id: string
  canonical_unit: string
}

export type WeatherData = {
  locations: WeatherLocation[]
  areas: WeatherArea[]
  variables: WeatherVariable[]
  observations: WeatherObservation[]
  forecasts: WeatherForecast[]
}

const WEATHER_DATA_CACHE_TTL_MS = 5 * 60_000
let weatherDataCache: { key: string; expiresAt: number; value: WeatherData } | undefined
let weatherDataPromise: { key: string; promise: Promise<WeatherData> } | undefined

export function isStidConfigured() {
  return Boolean(import.meta.env.VITE_RAYFIN_API_URL && import.meta.env.VITE_RAYFIN_LAKEHOUSE_SQL_ENDPOINT_ID)
}

export async function queryStid(): Promise<StidData | null> {
  const key = lakehouseConnectionKey()
  const source = connectedLakehouse()
  if (!source) return null
  const columns = lakehouseConnectorConfig.entities
  const [facilities, systems, equipment, instruments] = await Promise.all([
    collectLakehousePages(cursor => source.SilverFacilities.select([...columns.SilverFacilities]).first(500).after(cursor).executePaginated()),
    collectLakehousePages(cursor => source.SilverSystems.select([...columns.SilverSystems]).first(500).after(cursor).executePaginated()),
    collectLakehousePages(cursor => source.SilverEquipment.select([...columns.SilverEquipment]).first(500).after(cursor).executePaginated()),
    collectLakehousePages(cursor => source.SilverInstruments.select([...columns.SilverInstruments]).first(500).after(cursor).executePaginated()),
  ])
  if (lakehouseConnectionKey() !== key) throw new Error('Lakehouse connector identity changed during the STID read.')
  return {
    facilities: facilities.map(row => ({ ...row,
      facility_id: requireLakehouseText(row.facility_id, 'facility_id'),
      facility_name: requireLakehouseText(row.facility_name, 'facility_name') })),
    systems: systems.map(row => ({ ...row, system_id: requireLakehouseText(row.system_id, 'system_id'),
      facility_id: requireLakehouseText(row.facility_id, 'facility_id') })),
    equipment: equipment.map(row => ({ ...row,
      equipment_id: requireLakehouseText(row.equipment_id, 'equipment_id'),
      facility_id: requireLakehouseText(row.facility_id, 'facility_id'),
      system_id: requireLakehouseText(row.system_id, 'system_id'),
      install_date: row.install_date ? lakehouseTimestamp(row.install_date, 'install_date') : undefined })),
    instruments: instruments.map(row => ({ ...row,
      opcua_node_id: requireLakehouseText(row.opcua_node_id, 'opcua_node_id'),
      instrument_id: requireLakehouseText(row.instrument_id, 'instrument_id'),
      equipment_id: requireLakehouseText(row.equipment_id, 'equipment_id'),
      facility_id: requireLakehouseText(row.facility_id, 'facility_id'),
      system_id: requireLakehouseText(row.system_id, 'system_id') })),
    inventoryComplete: true,
  }
}

export async function queryWeatherData(forceRefresh = false): Promise<WeatherData | null> {
  const key = lakehouseConnectionKey()
  const source = connectedLakehouse()
  if (!source) {
    weatherDataCache = undefined
    weatherDataPromise = undefined
    return null
  }
  if (forceRefresh) weatherDataCache = undefined
  if (!forceRefresh && weatherDataCache?.key === key && weatherDataCache.expiresAt > Date.now()) {
    return weatherDataCache.value
  }
  if (!forceRefresh && weatherDataPromise?.key === key) return weatherDataPromise.promise
  const promise = (async () => {
  const columns = lakehouseConnectorConfig.entities
  const [locations, areas, variables, observations, forecasts] = await Promise.all([
    collectLakehousePages(cursor => source.WeatherLocations.select([...columns.WeatherLocations]).first(500).after(cursor).executePaginated()),
    collectLakehousePages(cursor => source.WeatherAreas.select([...columns.WeatherAreas]).first(500).after(cursor).executePaginated()),
    collectLakehousePages(cursor => source.WeatherVariables.select([...columns.WeatherVariables]).first(500).after(cursor).executePaginated()),
    collectLakehousePages(cursor => source.WeatherLatestObservations.select([...columns.WeatherLatestObservations]).first(500).after(cursor).executePaginated()),
    collectLakehousePages(cursor => source.WeatherLatestForecasts.select([...columns.WeatherLatestForecasts]).first(500).after(cursor).executePaginated()),
  ])
  const value: WeatherData = {
    locations: locations.map(row => ({ ...row, location_id: requireLakehouseText(row.location_id, 'location_id'),
      location_name: requireLakehouseText(row.location_name, 'location_name'),
      latitude: requireLakehouseNumber(row.latitude, 'latitude'), longitude: requireLakehouseNumber(row.longitude, 'longitude') })),
    areas: areas.map(row => ({ ...row, area_id: requireLakehouseText(row.area_id, 'area_id'),
      area_name: requireLakehouseText(row.area_name, 'area_name'), geometry_geojson: requireLakehouseText(row.geometry_geojson, 'geometry_geojson'),
      crs: requireLakehouseText(row.crs, 'crs') })),
    variables: variables.map(row => ({ ...row, variable_id: requireLakehouseText(row.variable_id, 'variable_id'),
      canonical_unit: requireLakehouseText(row.canonical_unit, 'canonical_unit') })),
    observations: observations.map(row => ({ ...row, source_id: requireLakehouseText(row.source_id, 'source_id'),
      location_id: requireLakehouseText(row.location_id, 'location_id'), observed_at_utc: lakehouseTimestamp(row.observed_at_utc, 'observed_at_utc') })),
    forecasts: forecasts.map(row => {
      if (row.target_kind !== 'location' && row.target_kind !== 'area') throw new Error('Lakehouse forecast target_kind is invalid.')
      return { ...row, source_id: requireLakehouseText(row.source_id, 'source_id'),
        target_kind: row.target_kind, target_id: requireLakehouseText(row.target_id, 'target_id'),
        reference_time_utc: lakehouseTimestamp(row.reference_time_utc, 'reference_time_utc'),
        valid_time_utc: lakehouseTimestamp(row.valid_time_utc, 'valid_time_utc'),
        lead_hours: requireLakehouseNumber(row.lead_hours, 'lead_hours') }
    }),
  }
  if (lakehouseConnectionKey() !== key) throw new Error('Lakehouse connector identity changed during the weather read.')
  weatherDataCache = { key, expiresAt: Date.now() + WEATHER_DATA_CACHE_TTL_MS, value }
  return value
  })().catch(error => {
    weatherDataCache = undefined
    throw error
  }).finally(() => {
    if (weatherDataPromise?.promise === promise) weatherDataPromise = undefined
  })
  weatherDataPromise = { key, promise }
  return promise
}

export async function startStreamingPipeline(onStatus?: JobProgress) {
  const config = await ensureConfig(true)
  if (!config?.pipelineId) throw new Error(`Streaming pipeline (${pipelineName}) was not found in the workspace.`)
  // The streaming pipeline keeps running; stop polling once it is confirmed live (InProgress).
  await runJob(config.pipelineId, 'Pipeline', onStatus, { stopAt: ['InProgress', ...TERMINAL_STATUSES], timeoutMs: 6 * 60_000, reuseActive: true })
}

/** Resume tracking a stream pipeline that was already started before a page reload. */
export async function resumeStreamingPipeline(onStatus: JobProgress | undefined, sinceIso: string) {
  const config = await ensureConfig(true)
  if (!config?.pipelineId) throw new Error(`Streaming pipeline (${pipelineName}) was not found in the workspace.`)
  await pollLatestInstance(config.pipelineId, sinceIso, onStatus, { stopAt: ['InProgress', ...TERMINAL_STATUSES], timeoutMs: 6 * 60_000 })
}

export function isPostSeedConfigured() { return Boolean(msal) }

async function resolveNotebookId(displayName: string): Promise<string> {
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  const items = await listItems(token)
  const notebook = items.find(item => item.type === 'Notebook' && item.displayName === displayName)
  if (!notebook) throw new Error(`The ${displayName} notebook was not found in the workspace.`)
  return notebook.id
}

async function resolvePostseedNotebookId(): Promise<string> {
  return resolveNotebookId(postseedNotebookName)
}

/** Status of the newest run of `itemId` started since `sinceIso`, or undefined if it never ran. */
async function statusSince(itemId: string, sinceIso: string): Promise<JobStatus | undefined> {
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  return (await latestInstance(token, itemId, sinceIso))?.status
}

async function resolveWeatherPipelineId(): Promise<string> {
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  const items = await listItems(token)
  const pipeline = items.find(item => (item.type === 'DataPipeline' || item.type === 'Pipeline') && item.displayName === weatherPipelineName)
  if (!pipeline) throw new Error(`The ${weatherPipelineName} pipeline was not found in the workspace.`)
  return pipeline.id
}

async function runWeatherSequence(onStatus?: JobProgress, resumeSinceIso?: string): Promise<JobStatus> {
  const pipelineId = await resolveWeatherPipelineId()
  const alreadyCompleted = resumeSinceIso && (await statusSince(pipelineId, resumeSinceIso)) === 'Completed'
  if (alreadyCompleted) return 'Completed'
  return runJob(pipelineId, 'Pipeline', onStatus, { timeoutMs: 80 * 60_000, reuseActive: true })
}

/** Prepare shared areas, ingest/serve UKMet, then run Aurora only when explicitly enabled. */
export const runWeatherNotebooks = createSingleFlight(
  async (onStatus?: JobProgress): Promise<JobStatus> => runWeatherSequence(onStatus),
)

/** Resume the coordinated weather pipeline started before a page reload. */
export async function resumeWeatherNotebooks(onStatus: JobProgress | undefined, sinceIso: string): Promise<JobStatus> {
  return runWeatherSequence(onStatus, sinceIso)
}

/** Run the RTI_011 post-seed notebook (seed shared SQL + Data Agent SQL source),
 *  polling to completion so the caller can show progress. Rediscovers new items on success. */
export const runPostSeedNotebook = createSingleFlight(async (onStatus?: JobProgress): Promise<JobStatus> => {
  const postseedNotebookId = await resolvePostseedNotebookId()
  const status = await runJob(
    postseedNotebookId,
    'RunNotebook',
    onStatus,
    {
      timeoutMs: 15 * 60_000,
      // RTI_011 writes shared Delta tables. Reattach to any active instance rather than
      // starting a competing run, even if that run originated outside this browser tab.
      reuseActive: true,
    },
  )
  // Refresh the Data Agent source publication after the explicit seed operation.
  if (status === 'Completed') clearWorkspaceConfigCache()
  return status
})

/** Resume tracking a post-seed notebook run that was already started before a page reload. */
export async function resumePostSeedNotebook(onStatus: JobProgress | undefined, sinceIso: string): Promise<JobStatus> {
  const postseedNotebookId = await resolvePostseedNotebookId()
  const status = await pollLatestInstance(postseedNotebookId, sinceIso, onStatus, { timeoutMs: 15 * 60_000 })
  if (status === 'Completed') clearWorkspaceConfigCache()
  return status
}

type McpContent = {
  type: string
  text?: string
  data?: string
  mimeType?: string
  resource?: { text?: string; blob?: string; mimeType?: string; uri?: string }
}

type DataAgentMcpSession = {
  endpoint: string
  token: string
  client: Client
  toolName: string
  questionArgument: string
}
let dataAgentMcpSession: DataAgentMcpSession | undefined
let dataAgentMcpSessionPromise: Promise<DataAgentMcpSession> | undefined
type DataAgentMcpTool = Pick<DataAgentMcpSession, 'endpoint' | 'toolName' | 'questionArgument'>
let dataAgentMcpTool: DataAgentMcpTool | undefined
let dataAgentUserQuestions: string[] = []
type DataAgentVerification = { key: string }
let dataAgentVerification: DataAgentVerification | undefined
let dataAgentVerificationPromise: { key: string; value: Promise<void> } | undefined

export function resetDataAgentConversation() {
  dataAgentUserQuestions = []
}

function invalidateDataAgentMcpSession() {
  const session = dataAgentMcpSession
  dataAgentMcpSession = undefined
  dataAgentMcpSessionPromise = undefined
  if (session) void session.client.close().catch(() => undefined)
}

function isDisconnectedDataAgentMcpError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)
  return /not connected|connection (?:is )?closed|transport (?:is )?closed|failed to fetch|network error|socket hang up|econnreset|connection reset|terminated/i.test(message)
}

function invalidateDataAgentVerification() {
  dataAgentVerification = undefined
  dataAgentVerificationPromise = undefined
}

async function verifyDataAgentSource(dataAgentId: string, ontologyId: string, generation: unknown, token: string): Promise<void> {
  const key = `${requireWorkspaceId()}:${dataAgentId}:${ontologyId}:${String(generation)}`
  if (dataAgentVerification?.key === key) return
  if (dataAgentVerificationPromise?.key === key) {
    return dataAgentVerificationPromise.value
  }
  const value = invokeVerifiedDataAgent(
    { generation, workspaceId: requireWorkspaceId(), ontologyId },
    async () => {
      const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/dataAgents/${dataAgentId}/getDefinition`, {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + token },
      })
      return waitForDefinitionResult(response, token)
    },
    async () => undefined,
  ).then(() => {
    dataAgentVerification = { key }
  })
  dataAgentVerificationPromise = { key, value }
  try {
    await value
  } finally {
    if (dataAgentVerificationPromise?.value === value) dataAgentVerificationPromise = undefined
  }
}

export async function askDataAgent(question: string, onProgress?: (text: string) => void, onSteps?: (steps: AgentStep[]) => void): Promise<AgentAnswer> {
  const config = await ensureConfig(true)
  if (config?.ontologyError) throw new Error(config.ontologyError)
  const endpoint = requireDataAgentEndpoint(config?.dataAgentUrl, config?.ontologyGeneration)
  if (!config?.ontologyId || !config.dataAgentId) throw new Error('Data Agent source identity is unavailable. Refresh Ontology v2 discovery before asking the agent.')
  const token = await fabricToken(true, DATA_AGENT_SCOPES)
  if (!token) throw new Error('Fabric sign-in is required.')
  const verification = verifyDataAgentSource(config.dataAgentId, config.ontologyId, config.ontologyGeneration, token)
  try {
    await Promise.all([verification, getDataAgentMcpSession(endpoint, token)])
  } catch (error) {
    invalidateDataAgentMcpSession()
    throw error
  }

  const contextualizedQuestion = contextualizeDataAgentQuestion(question, dataAgentUserQuestions)
  const answer = await callDataAgentMcp(endpoint, token, `${contextualizedQuestion}\n\nCurrent UTC time: ${new Date().toISOString()}\n\n${OPERATIONAL_EVIDENCE_CONTRACT}\n\n${ANSWER_PRESENTATION_CONTRACT}`, onProgress, onSteps)
  dataAgentUserQuestions.push(question)
  if (dataAgentUserQuestions.length > 4) dataAgentUserQuestions = dataAgentUserQuestions.slice(-4)
  const visualizations = extractDataAgentVisualizations(answer.text, question)
  return visualizations.length ? { ...answer, visualizations } : answer
}

export async function verifyDataAgentForFoundry(): Promise<void> {
  const config = await ensureConfig(true)
  if (config?.ontologyError) throw new Error(config.ontologyError)
  requireDataAgentEndpoint(config?.dataAgentUrl, config?.ontologyGeneration)
  if (!config?.ontologyId || !config.dataAgentId) throw new Error('Verified Data Agent source identity is unavailable.')
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  await verifyDataAgentSource(config.dataAgentId, config.ontologyId, config.ontologyGeneration, token)
}

export async function verifyOntologyForFoundry(): Promise<void> {
  const config = await ensureConfig(true)
  if (config?.ontologyError) throw new Error(config.ontologyError)
  if (!config?.ontologyId) throw new Error('Selected Ontology identity is unavailable.')
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/ontologies/${config.ontologyId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) throw new Error(`Live ontology generation verification failed (${response.status}).`)
  const metadata = await response.json() as { properties?: { generation?: unknown } }
  requireV2Generation(metadata.properties?.generation)
}

export async function warmDataAgentMcp(): Promise<void> {
  const config = await ensureConfig(false)
  if (!config || config.ontologyError || !config.dataAgentUrl || !config.dataAgentId || !config.ontologyId) return
  const endpoint = requireDataAgentEndpoint(config.dataAgentUrl, config.ontologyGeneration)
  const token = await fabricToken(false, DATA_AGENT_SCOPES)
  if (!token) return
  try {
    await Promise.all([
      verifyDataAgentSource(config.dataAgentId, config.ontologyId, config.ontologyGeneration, token),
      getDataAgentMcpSession(endpoint, token),
    ])
  } catch (error) {
    invalidateDataAgentMcpSession()
    throw error
  }
}

async function getDataAgentMcpSession(endpoint: string, token: string): Promise<DataAgentMcpSession> {
  if (dataAgentMcpSession?.endpoint === endpoint && dataAgentMcpSession.token === token) return dataAgentMcpSession
  if (dataAgentMcpSession && (dataAgentMcpSession.endpoint !== endpoint || dataAgentMcpSession.token !== token)) {
    invalidateDataAgentMcpSession()
  }
  if (!dataAgentMcpSessionPromise) {
    dataAgentMcpSessionPromise = (async () => {
      const [{ Client }, { StreamableHTTPClientTransport }] = await Promise.all([
        import('@modelcontextprotocol/sdk/client/index.js'),
        import('@modelcontextprotocol/sdk/client/streamableHttp.js'),
      ])
      const client = new Client({ name: 'hydro-operations-app', version: __APP_VERSION__ })
      const transport = new StreamableHTTPClientTransport(new URL(endpoint), {
        requestInit: { headers: { Authorization: 'Bearer ' + token, ActivityId: crypto.randomUUID() } },
      })
      try {
        await client.connect(transport)
        let tool = dataAgentMcpTool?.endpoint === endpoint ? dataAgentMcpTool : undefined
        if (!tool) {
          const discovered = (await client.listTools()).tools[0]
          if (!discovered) throw new Error('The published Data Agent exposes no MCP tool.')
          const questionArgument = Object.keys(discovered.inputSchema?.properties ?? {})[0]
          if (!questionArgument) throw new Error('The Data Agent MCP tool has no question argument.')
          tool = { endpoint, toolName: discovered.name, questionArgument }
          dataAgentMcpTool = tool
        }
        const session = { endpoint, token, client, toolName: tool.toolName, questionArgument: tool.questionArgument }
        client.onclose = () => {
          if (dataAgentMcpSession === session) {
            dataAgentMcpSession = undefined
          }
        }
        return session
      } catch (error) {
        await client.close().catch(() => undefined)
        throw error
      }
    })()
  }
  const pending = dataAgentMcpSessionPromise
  try {
    const session = await pending
    if (dataAgentMcpSessionPromise !== pending) {
      await session.client.close().catch(() => undefined)
      return getDataAgentMcpSession(endpoint, token)
    }
    dataAgentMcpSession = session
    return session
  } finally {
    if (dataAgentMcpSessionPromise === pending) dataAgentMcpSessionPromise = undefined
  }
}

export async function askMapDataAgent(question: string, signal: AbortSignal, onProgress?: (text: string) => void): Promise<AgentAnswer> {
  signal.throwIfAborted()
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required for map chat.')
  const agent = selectNamedDataAgent(await listItems(token, signal), mapDataAgentName(eventhouseName, configuredMapAgentName))
  const endpoint = `https://api.fabric.microsoft.com/v1/mcp/workspaces/${requireWorkspaceId()}/dataagents/${agent.id}/agent`
  return callDataAgentMcp(endpoint, token, question, onProgress, undefined, true, signal)
}

export async function ensureMapChatConnection(signal: AbortSignal): Promise<void> {
  signal.throwIfAborted()
  const config = await ensureConfig(true)
  signal.throwIfAborted()
  if (!config?.eventhouseQueryUri) throw new Error('No map Eventhouse was found in this workspace.')
  const scopes = [kustoScope(config.eventhouseQueryUri)]
  if (!await silentToken(scopes)) {
    signal.throwIfAborted()
    await popupToken(scopes)
  }
  signal.throwIfAborted()
}

async function callDataAgentMcp(
  endpoint: string,
  token: string,
  question: string,
  onProgress?: (text: string) => void,
  onSteps?: (steps: AgentStep[]) => void,
  reconnectOnDisconnect = true,
  signal?: AbortSignal,
): Promise<AgentAnswer> {
  signal?.throwIfAborted()
  const session = await getDataAgentMcpSession(endpoint, token)
  signal?.throwIfAborted()
  const startedAt = Date.now()
  const steps: AgentStep[] = [{
    tool: session.toolName,
    status: 'running',
    detail: 'Published Fabric Data Agent MCP tool',
    summary: 'running...',
    elapsedMs: 0,
  }]
  const publishSteps = () => onSteps?.(steps.map(step => ({ ...step })))
  publishSteps()
  try {
    const result = await session.client.callTool({
      name: session.toolName,
      arguments: { [session.questionArgument]: question },
    }, undefined, {
      timeout: 5 * 60_000,
      maxTotalTimeout: 5 * 60_000,
      resetTimeoutOnProgress: true,
      signal,
      onprogress: progress => {
        if (applyDataAgentProgress(steps, progress.message, Date.now())) publishSteps()
      },
    })
    const content = result.content as McpContent[]
    const text = content
      .flatMap(part => [part.text, part.resource?.text])
      .filter((value): value is string => Boolean(value))
      .join('\n')
      .trim()
    if (result.isError) throw new Error(text || 'The Data Agent MCP tool returned an error.')
    const artifacts = content.flatMap((part, index) => {
      const data = part.data ?? part.resource?.blob
      const mimeType = part.mimeType ?? part.resource?.mimeType
      if (!data || !mimeType?.startsWith('image/')) return []
      const bytes = Uint8Array.from(atob(data), character => character.charCodeAt(0))
      return [{
        fileId: `mcp-image-${index}`,
        name: `data-agent-image-${index + 1}.${mimeType.split('/')[1] || 'png'}`,
        kind: 'image' as const,
        url: URL.createObjectURL(new Blob([bytes], { type: mimeType })),
      }]
    })
    const answer = text || 'The Data Agent returned no answer.'
    Object.assign(steps[0], { status: 'done', summary: 'completed', elapsedMs: Date.now() - startedAt })
    publishSteps()
    onProgress?.(answer)
    return { text: answer, artifacts, steps }
  } catch (error) {
    Object.assign(steps[0], {
      status: 'error',
      summary: 'failed',
      elapsedMs: Date.now() - startedAt,
      error: error instanceof Error ? error.message : 'The Data Agent MCP call failed.',
    })
    publishSteps()
    if (dataAgentMcpSession === session) invalidateDataAgentMcpSession()
    if (reconnectOnDisconnect && isDisconnectedDataAgentMcpError(error)) {
      return callDataAgentMcp(endpoint, token, question, onProgress, onSteps, false, signal)
    }
    throw error
  }
}

export type TelemetryReading = { opcuaNodeId: string; eventTime: string; value: number; quality: string }
export type TelemetryHistoryRange = '1h' | '6h' | '24h'
export type TelemetryHistoryPoint = TelemetryReading

const TELEMETRY_HISTORY_WINDOWS: Record<TelemetryHistoryRange, { ago: string; bin: string }> = {
  // The simulator emits approximately every 5s per signal; keep ~720 chart points per range.
  '1h': { ago: '1h', bin: '5s' },
  '6h': { ago: '6h', bin: '30s' },
  '24h': { ago: '24h', bin: '2m' },
}

function kqlString(value: string): string {
  return value.replace(/'/g, "''")
}

export function isTelemetryConfigured() { return Boolean(msal) }

export async function queryLatestTelemetry(): Promise<TelemetryReading[] | null> {
  const discovered = configuredEventhouseQueryUri && configuredKqlDatabase
    ? undefined
    : await ensureConfig(false)
  const eventhouseQueryUri = configuredEventhouseQueryUri ?? discovered?.eventhouseQueryUri
  const kqlDatabase = configuredKqlDatabase ?? discovered?.kqlDatabase
  if (!eventhouseQueryUri || !kqlDatabase) return null
  const cluster = eventhouseQueryUri.replace(/\/$/, '')
  const token = await silentToken([kustoScope(cluster)])
  if (!token) return null
  const response = await fetch(`${cluster}/v1/rest/query`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ db: kqlDatabase, csl: 'OPCUAEvents | where event_time > ago(24h) | summarize arg_max(event_time, value, quality) by opcua_node_id | take 500' }),
  })
  const text = await response.text()
  if (!response.ok) {
    console.error('Eventhouse query failed.', response.status, text.slice(0, 500))
    throw new Error(`Eventhouse query failed (${response.status}).`)
  }
  const payload = JSON.parse(text) as { Tables?: Array<{ Rows?: Array<[string, string, number, string]> }> }
  return (payload.Tables?.[0]?.Rows ?? []).map(([opcuaNodeId, eventTime, value, quality]) => ({ opcuaNodeId, eventTime, value, quality }))
}

export async function queryTelemetryHistory(opcuaNodeId: string, range: TelemetryHistoryRange): Promise<TelemetryHistoryPoint[] | null> {
  const config = await ensureConfig(false)
  if (!config?.eventhouseQueryUri || !config.kqlDatabase) return null
  const window = TELEMETRY_HISTORY_WINDOWS[range]
  if (!window) throw new Error(`Unsupported telemetry range: ${range}`)
  const cluster = config.eventhouseQueryUri.replace(/\/$/, '')
  const token = await silentToken([kustoScope(cluster)])
  if (!token) return null
  const node = kqlString(opcuaNodeId)
  const response = await fetch(`${cluster}/v1/rest/query`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      db: config.kqlDatabase,
      csl: `OPCUAEvents
| where opcua_node_id == '${node}'
| where event_time > ago(${window.ago})
    | summarize value = avg(value), bad = countif(tolower(quality) == 'bad'), uncertain = countif(tolower(quality) == 'uncertain') by event_time = bin(event_time, ${window.bin}), opcua_node_id
    | extend quality = case(bad > 0, 'BAD', uncertain > 0, 'UNCERTAIN', 'GOOD')
| project event_time, opcua_node_id, value, quality
| order by event_time asc`,
    }),
  })
  const text = await response.text()
  if (!response.ok) {
    console.error('Eventhouse history query failed.', response.status, text.slice(0, 500))
    throw new Error(`Eventhouse history query failed (${response.status}).`)
  }
  const payload = JSON.parse(text) as { Tables?: Array<{ Rows?: Array<[string, string, number, string]> }> }
  return (payload.Tables?.[0]?.Rows ?? []).map(([eventTime, opcuaNodeId, value, quality]) => ({ opcuaNodeId, eventTime, value, quality }))
}

export type KustoResult = { columns: string[]; rows: unknown[][]; sourceKey?: string }

/** Run an already-validated KQL query against the Eventhouse as the signed-in user.
 *  Callers outside the telemetry views must validate the query text first — see copilot/query.ts. */
export async function runKustoQuery(csl: string, maxRows: number, signal?: AbortSignal): Promise<KustoResult> {
  const config = await ensureConfig(false)
  if (!config?.eventhouseQueryUri || !config.kqlDatabase) throw new Error('No Eventhouse is connected in this workspace.')
  const cluster = config.eventhouseQueryUri.replace(/\/$/, '')
  const token = await silentToken([kustoScope(cluster)])
  if (!token) throw new Error('Eventhouse consent is required. Connect telemetry first.')
  const response = await fetch(`${cluster}/v1/rest/query`, {
    method: 'POST',
    signal,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    // Server-side caps back up the client-side `| take`, so a runaway query cannot return a huge payload.
    body: JSON.stringify({
      db: config.kqlDatabase,
      csl,
      properties: { Options: { truncationmaxrecords: maxRows, servertimeout: '00:01:00' } },
    }),
  })
  const text = await response.text()
  if (!response.ok) throw new Error(`Eventhouse query failed (${response.status}): ${text.slice(0, 300)}`)
  const { parseKustoPayload } = await import('./kustoResult')
  const table = parseKustoPayload(JSON.parse(text))
  return {
    columns: table.Columns.map(column => column.ColumnName),
    rows: table.Rows,
    sourceKey: JSON.stringify([requireWorkspaceId(), cluster, config.kqlDatabase]),
  }
}

async function resolveEnergyMapPipeline(): Promise<string> {
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required to refresh map data.')
  const matches = (await listItems(token)).filter(item =>
    item.type === 'DataPipeline' && item.displayName === '04_Pipe_EnergyMap')
  if (matches.length !== 1) throw new Error('The energy-map import pipeline is missing or ambiguous. Run map provisioning.')
  return matches[0].id
}

const mapRefreshListeners = new Set<JobProgress>()
let mapRefreshInFlight: Promise<JobStatus> | undefined
let mapRefreshStatus: JobStatus = 'NotStarted'
let mapRefreshStartedAt: string | undefined

export function refreshEnergyMap(onStatus?: JobProgress, forceRefresh = false, onExistingRun?: (startedAt?: string) => void): Promise<JobStatus> {
  if (onStatus) mapRefreshListeners.add(onStatus)
  if (mapRefreshInFlight) {
    onExistingRun?.(mapRefreshStartedAt)
    onStatus?.(mapRefreshStatus)
  } else {
    mapRefreshStatus = 'NotStarted'
    mapRefreshStartedAt = new Date().toISOString()
    const publish = (status: JobStatus) => {
      mapRefreshStatus = status
      mapRefreshListeners.forEach(listener => listener(status))
    }
    const current = (async () => {
      const pipelineId = await resolveEnergyMapPipeline()
      return runJob(pipelineId, 'Pipeline', publish, {
        reuseActive: true, timeoutMs: 3 * 60 * 60_000,
        pipelineParameters: { refresh_mode: 'all', force_refresh: forceRefresh },
        onReuseActive: startedAt => {
          mapRefreshStartedAt = startedAt ?? mapRefreshStartedAt
          onExistingRun?.(mapRefreshStartedAt)
        },
      })
    })()
    mapRefreshInFlight = current
    void current.finally(() => { if (mapRefreshInFlight === current) mapRefreshInFlight = undefined }).catch(() => undefined)
  }
  return mapRefreshInFlight.finally(() => { if (onStatus) mapRefreshListeners.delete(onStatus) })
}

export async function resumeEnergyMapRefresh(onStatus: JobProgress | undefined, sinceIso: string): Promise<JobStatus> {
  if (mapRefreshInFlight) return refreshEnergyMap(onStatus)
  const pipelineId = await resolveEnergyMapPipeline()
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required to resume map refresh progress.')
  const instances = (await jobInstances(token, pipelineId))
    .sort((a, b) => (b.startTimeUtc ?? '').localeCompare(a.startTimeUtc ?? ''))
  const instance = instances.find(item => item.status && ACTIVE_STATUSES.includes(item.status))
    ?? instances.find(item => item.startTimeUtc && jobStartUtc(item.startTimeUtc) >= sinceIso)
  if (!instance?.id) throw new Error('The recorded map refresh was not found. Check Fabric run history before starting another update.')
  return pollLatestInstance(pipelineId, sinceIso, onStatus, { timeoutMs: 3 * 60 * 60_000, instanceId: instance.id })
}
