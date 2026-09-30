import { PublicClientApplication } from '@azure/msal-browser'
import { type AgentAnswer } from './assistantStream'
import { invokeVerifiedDataAgent, requireDataAgentEndpoint, selectDataAgent } from './artifactDiscovery'
import { discoverOntology, type OntologyDiscovery } from './ontologyDiscovery'
import { createOntologyCache, OntologyCacheInvalidatedError } from './ontologyCache'
import { parseOntologyContract, type OntologyContract } from './ontologyContract'
import { waitForDefinitionResult } from './ontologyDefinition'
import type { OntologyGraph } from './ontologyGraph'
import { parseGraphBinding, queryBoundOntologyGraph } from './ontologyGraphQuery'
import { requireV2Generation } from './ontologyArtifactDiscovery'
import { createSingleFlight } from './singleFlight'

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
const graphqlId = import.meta.env.VITE_RAYFIN_STID_GRAPHQL_ID as string | undefined
const graphqlName = import.meta.env.VITE_RAYFIN_STID_GRAPHQL_NAME as string | undefined
const kqlDashboardName = (import.meta.env.VITE_RAYFIN_KQL_DASHBOARD_NAME as string | undefined) ?? 'RTI_Demo_OPCUA_TelemetryStats_V6'
const configuredOntologyName = import.meta.env.VITE_RAYFIN_ONTOLOGY_NAME as string | undefined
const graphqlUrlOverride = import.meta.env.VITE_RAYFIN_STID_GRAPHQL_URL as string | undefined

const msal = clientId && tenantId ? new PublicClientApplication({
  auth: { clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: location.origin },
  cache: { cacheLocation: 'localStorage' },
}) : undefined

const GRAPHQL_SCOPE = 'https://analysis.windows.net/powerbi/api/GraphQLApi.Execute.All'
// A named scope, not `.default`: `.default` only returns permissions already statically configured
// for the exact cluster audience, so Entra escalates to "Need admin approval" instead of consenting.
const kustoScope = (clusterUri: string) => `${clusterUri.replace(/\/$/, '')}/user_impersonation`
// Item.Read.All authorizes the per-item detail GET (e.g. Get Eventhouse → queryServiceUri);
// Workspace.Read.All only covers List Items, and Item.Execute.All only covers running jobs.
const FABRIC_SCOPES = ['https://api.fabric.microsoft.com/Workspace.Read.All', 'https://api.fabric.microsoft.com/Item.Read.All', 'https://api.fabric.microsoft.com/Item.Execute.All']
// Fabric Embed needs its own delegated scope. Named, not `.default`, for the same reason as kustoScope.
const EMBED_SCOPES = ['https://api.fabric.microsoft.com/Fabric.Embed', 'https://api.fabric.microsoft.com/Item.Read.All']
// Azure AI Foundry data plane. Named scope again, not `.default` — the caller needs the
// `Cognitive Services OpenAI User` role on the Foundry resource for the token to be authorized.
const FOUNDRY_SCOPES = ['https://cognitiveservices.azure.com/user_impersonation']

export type ConnectTarget = 'stid' | 'telemetry' | 'stream'

let initialized = false

/** Initialize MSAL and process any redirect returning from Entra. */
export async function initAuth(): Promise<void> {
  if (!msal) return
  await msal.initialize()
  try {
    await msal.handleRedirectPromise()
  } catch (error) {
    console.warn('Entra redirect handling failed.', error)
  }
  initialized = true
}

async function ensureInit() {
  if (!msal) throw new Error('Microsoft Entra client configuration is missing.')
  if (initialized) return
  await msal.initialize()
  try { await msal.handleRedirectPromise() } catch (error) { console.warn('Entra redirect handling failed.', error) }
  initialized = true
}

/** Acquire a token silently. Returns null when interactive sign-in/consent is required. */
async function silentToken(scopes: string[], forceRefresh = false): Promise<string | null> {
  await ensureInit()
  const account = msal!.getAllAccounts()[0]
  if (!account) return null
  try {
    return (await msal!.acquireTokenSilent({ account, scopes, forceRefresh })).accessToken
  } catch (error) {
    console.warn('Silent token acquisition failed; interactive consent required.', error)
    return null
  }
}

/** Acquire a token interactively via popup (redirects are blocked inside the Fabric iframe).
 *  Must be invoked from a user gesture. */
async function popupToken(scopes: string[]): Promise<string> {
  await ensureInit()
  const account = msal!.getAllAccounts()[0]
  const result = await msal!.acquireTokenPopup({ scopes, account: account ?? undefined })
  return result.accessToken
}

/** A Fabric REST token (read + execute). Silent first, popup only when interactive is allowed. */
async function fabricToken(interactive: boolean): Promise<string | null> {
  const silent = await silentToken(FABRIC_SCOPES)
  if (silent) return silent
  if (!interactive) return null
  try {
    return await popupToken(FABRIC_SCOPES)
  } catch (error) {
    console.warn('Fabric permission consent did not complete.', error)
    throw new Error('Fabric permission consent is required before this action can run. Complete the consent popup and try again.', { cause: error })
  }
}

// ---- Workspace artifact discovery (resolve ids/URIs by display name, never hardcode) ----
type WorkspaceItem = { id: string; type: string; displayName: string; folderId?: string }
type ResolvedConfig = OntologyDiscovery & { pipelineId?: string; postseedNotebookId?: string; eventhouseQueryUri?: string; kqlDatabase?: string; graphqlUrl?: string; dataAgentUrl?: string; dataAgentId?: string; kqlDashboardId?: string }
let configCache: ResolvedConfig | null = null
let configPromise: Promise<ResolvedConfig | null> | undefined
let configRevision = 0

function requireWorkspaceId(): string {
  if (!workspaceId) throw new Error('Fabric workspace configuration is missing. Rebuild the app with Rayfin environment injection.')
  return workspaceId
}

/** Build-time selections and last-known-good endpoints for unavailable live discovery. */
function envConfig(): ResolvedConfig {
  return {
    pipelineId: import.meta.env.VITE_RAYFIN_STREAM_PIPELINE_ID as string | undefined,
    postseedNotebookId: import.meta.env.VITE_RAYFIN_POSTSEED_NOTEBOOK_ID as string | undefined,
    eventhouseQueryUri: import.meta.env.VITE_RAYFIN_KQL_CLUSTER_URI as string | undefined,
    kqlDatabase: import.meta.env.VITE_RAYFIN_KQL_DATABASE as string | undefined,
    graphqlUrl: graphqlUrlOverride,
    kqlDashboardId: import.meta.env.VITE_RAYFIN_KQL_DASHBOARD_ID as string | undefined,
  }
}

async function listItems(token: string): Promise<WorkspaceItem[]> {
  const items: WorkspaceItem[] = []
  let nextUrl: string | undefined = `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items`
  while (nextUrl) {
    const res = await fetch(nextUrl, { headers: { Authorization: `Bearer ${token}` } })
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
    return (env.eventhouseQueryUri || env.pipelineId || env.graphqlUrl || env.postseedNotebookId) ? env : null
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
    // Fabric doesn't expose a GraphQL item's endpoint, but api.fabric.microsoft.com serves
    // queries directly at this deterministic path when no explicit URL is configured.
    const gql = graphqlId
      ? items.find(i => i.type === 'GraphQLApi' && i.id === graphqlId)
      : graphqlName ? find('GraphQLApi', graphqlName) : items.find(i => i.type === 'GraphQLApi')
    if ((graphqlId || graphqlName) && !gql && !graphqlUrlOverride) {
      throw new Error('The configured STID GraphQL API was not found in this workspace. Run SQL/GraphQL provisioning and refresh discovery.')
    }
    const graphqlUrl = graphqlUrlOverride || (gql
      ? `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/graphqlapis/${gql.id}/graphql`
      : undefined)
    // Published Data Agents are invoked through Fabric's MCP endpoint. The retired Assistants
    // endpoint can route or execute agent tools differently from the Fabric Data Agent UI.
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
      graphqlUrl: graphqlUrl ?? env.graphqlUrl,
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

/** A token for the Azure AI Foundry data plane. Silent first; popup only when interactive is allowed. */
export async function foundryToken(interactive: boolean): Promise<string | null> {
  // Consent may have been granted after this page loaded. Bypass MSAL's cached token so the
  // first Foundry turn immediately observes the new grant instead of requiring a hard refresh.
  const silent = await silentToken(FOUNDRY_SCOPES, true)
  if (silent) return silent
  if (!interactive) return null
  return popupToken(FOUNDRY_SCOPES)
}

/** Force a fresh workspace discovery on the next call (e.g. after RTI_011 provisions new items). */
export function clearWorkspaceConfigCache() {
  configRevision++
  configCache = null
  configPromise = undefined
}

// ---- Fabric item jobs: trigger + poll for live progress ----
export type JobStatus = 'NotStarted' | 'InProgress' | 'Completed' | 'Failed' | 'Cancelled' | 'Deduped'
export type JobProgress = (status: JobStatus) => void
const TERMINAL_STATUSES: JobStatus[] = ['Completed', 'Failed', 'Cancelled', 'Deduped']
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

type JobInstance = { id?: string; status?: JobStatus; startTimeUtc?: string; failureReason?: { message?: string } }
const ACTIVE_STATUSES: JobStatus[] = ['NotStarted', 'InProgress']

/** Newest job instance started at/after `sinceIso` — used when the 202 Location header is not CORS-exposed. */
async function latestInstance(token: string, itemId: string, sinceIso: string): Promise<JobInstance | undefined> {
  const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) return undefined
  const list = ((await res.json()) as { value?: JobInstance[] }).value ?? []
  return list
    .filter(i => !i.startTimeUtc || i.startTimeUtc >= sinceIso)
    .sort((a, b) => (b.startTimeUtc ?? '').localeCompare(a.startTimeUtc ?? ''))[0]
}

/** Newest still-running (NotStarted/InProgress) instance for an item, so callers can reattach
 *  instead of starting a duplicate run after a refresh or repeated clicks. */
async function activeInstance(token: string, itemId: string): Promise<JobInstance | undefined> {
  const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) return undefined
  const list = ((await res.json()) as { value?: JobInstance[] }).value ?? []
  return list
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
    parameters?: Array<{
      name: string
      value: string | number | boolean
      type: 'Text' | 'Boolean' | 'Integer' | 'Number' | 'DateTime' | 'Guid' | 'Automatic' | 'VariableReference'
    }>
  },
): Promise<JobStatus> {
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  let startedAt = new Date(Date.now() - 5000).toISOString()
  let location: string | null = null
  // Reattach to an already-running instance instead of starting a duplicate (survives refresh / repeat clicks).
  const active = opts?.reuseActive ? await activeInstance(token, itemId) : undefined
  if (active?.startTimeUtc) {
    startedAt = new Date(Date.parse(active.startTimeUtc) - 5000).toISOString()
    if (active.status) onStatus?.(active.status)
  } else {
    const isNotebookRun = jobType === 'RunNotebook'
    const triggerUrl = isNotebookRun
      ? `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/notebooks/${itemId}/jobs/execute/instances?beta=false`
      : `https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/items/${itemId}/jobs/instances?jobType=${jobType}`

    const requestBody = isNotebookRun && opts?.parameters?.length
      ? JSON.stringify({ parameters: opts.parameters })
      : undefined

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
    location = trigger.headers.get('Location')
  }
  const stopAt = opts?.stopAt ?? TERMINAL_STATUSES
  const deadline = Date.now() + (opts?.timeoutMs ?? 10 * 60_000)
  let last: JobStatus = 'NotStarted'
  onStatus?.(last)
  while (Date.now() < deadline) {
    await delay(4000)
    let status: JobStatus | undefined
    let failure: string | undefined
    try {
      if (location) {
        const res = await fetch(location, { headers: { Authorization: `Bearer ${token}` } })
        if (res.ok) { const body = await res.json() as JobInstance; status = body.status; failure = body.failureReason?.message }
      } else {
        const inst = await latestInstance(token, itemId, startedAt)
        status = inst?.status; failure = inst?.failureReason?.message
      }
    } catch { continue }
    if (!status) continue
    if (status !== last) { last = status; onStatus?.(status) }
    if (status === 'Failed') throw new Error(failure || 'Fabric job failed.')
    if (stopAt.includes(status)) return status
  }
  return last
}

/** Re-attach to the newest instance of an already-triggered job — used to resume progress after a page reload. */
async function pollLatestInstance(itemId: string, sinceIso: string, onStatus?: JobProgress, opts?: { stopAt?: JobStatus[]; timeoutMs?: number }): Promise<JobStatus> {
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  const stopAt = opts?.stopAt ?? TERMINAL_STATUSES
  const deadline = Date.now() + (opts?.timeoutMs ?? 10 * 60_000)
  let last: JobStatus = 'NotStarted'
  onStatus?.(last)
  while (Date.now() < deadline) {
    let status: JobStatus | undefined
    let failure: string | undefined
    try {
      const inst = await latestInstance(token, itemId, sinceIso)
      status = inst?.status; failure = inst?.failureReason?.message
    } catch { await delay(4000); continue }
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
  if (!msal) throw new Error('Microsoft Entra client configuration is missing.')
  const config = await ensureConfig(true)
  if (target === 'stid') {
    if (!config?.graphqlUrl) throw new Error('No GraphQL API found in the workspace. Publish the STID GraphQL API (run RTI_011) and try again.')
    await popupToken([GRAPHQL_SCOPE])
  } else if (target === 'telemetry') {
    if (!config?.eventhouseQueryUri) throw new Error('No Eventhouse found in the workspace.')
    await popupToken([kustoScope(config.eventhouseQueryUri)])
  }
  // 'stream' needs only the Fabric token already acquired by ensureConfig.
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
const ontologyGraphCache = createOntologyCache<OntologyGraph>(ONTOLOGY_CONTRACT_TTL_MS)

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

export async function queryOntologyContract(force = false): Promise<OntologyContract | null> {
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

export async function queryOntologyGraph(force = false): Promise<OntologyGraph | null> {
  return withCurrentConfig(async config => {
    if (config?.ontologyError) throw new Error(config.ontologyError)
    if (!config?.ontologyId) throw new Error('Sign in and select an Ontology v2 before querying its graph.')
    const binding = parseGraphBinding(
      import.meta.env.VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING as string | undefined,
      requireWorkspaceId(), config.ontologyId,
    )
    const ontology = await queryOntologyContract()
    if (!ontology || ontology.id.toLowerCase() !== binding.ontologyId.toLowerCase()) throw new Error('Ontology selection changed during graph loading. Refresh discovery.')
    return ontologyGraphCache.read(JSON.stringify([binding, ontology]), force, async () => {
      const token = await fabricToken(false)
      if (!token) throw new Error('Sign in with Fabric graph read access before querying the ontology graph.')
      const request = (url: string, init?: RequestInit) => {
        const headers = new Headers(init?.headers)
        headers.set('Authorization', `Bearer ${token}`)
        return fetch(url, { ...init, headers, signal: init?.signal ?? AbortSignal.timeout(120_000) })
      }
      return queryBoundOntologyGraph(binding, ontology, request)
    })
  })
}

// Fabric API for GraphQL exposes each Lakehouse table under its own name; app-side keys are
// pinned via GraphQL aliases so the client stays stable regardless of table naming.
type StidPayload = {
  data?: {
    facilities?: { items?: Facility[] }
    systems?: { items?: System[] }
    equipment?: { items?: Equipment[] }
    instruments?: { items?: Instrument[] }
  }
  errors?: Array<{ message?: string }>
}

export type StidData = { facilities: Facility[]; systems: System[]; equipment: Equipment[]; instruments: Instrument[] }

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

type WeatherPayload = {
  data?: {
    locations?: { items?: WeatherLocation[] }
    areas?: { items?: WeatherArea[] }
    variables?: { items?: WeatherVariable[] }
    observations?: { items?: WeatherObservation[] }
    forecasts?: { items?: WeatherForecast[] }
  }
  errors?: Array<{ message?: string }>
}

export function isStidConfigured() { return Boolean(msal) }

export async function queryStid(): Promise<StidData | null> {
  const config = await ensureConfig(false)
  if (!config?.graphqlUrl) return null
  const token = await silentToken([GRAPHQL_SCOPE])
  if (!token) return null
  // Aliases map to the real Lakehouse tables exposed by the
  // GraphQL API. Fabric auto-pluralizes the root field, so the equipment table is `silver_equipments`.
  const coreQuery = `facilities: silver_facilities(first: 20) { items { facility_id facility_name type country lat lon commissioned_date } }
    equipment: silver_equipments(first: 100) { items { equipment_id facility_id system_id equipment_type_code equipment_type_name tag manufacturer model criticality install_date status is_active } }
    instruments: silver_instruments(first: 500) { items { opcua_node_id tag instrument_id equipment_id system_id facility_id unit instrument_type is_active } }`
  const execute = async (query: string) => {
    const response = await fetch(config.graphqlUrl!, {
      method: 'POST', cache: 'no-store',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    })
    const text = await response.text()
    if (!response.ok) {
      console.error('STID GraphQL request failed.', response.status, text.slice(0, 500))
      throw new Error(`STID query failed (${response.status}).`)
    }
    return JSON.parse(text) as StidPayload
  }
  let payload = await execute(`query HydroStid { systems: silver_systems(first: 50) { items { system_id facility_id system_name oag_rds_system_code } } ${coreQuery} }`)
  if (payload.errors?.some(error => /silver_systems/i.test(error.message ?? ''))) {
    console.warn('The GraphQL definition does not expose silver_systems; rerun RTI_011 to publish the Ontology systems binding.')
    payload = await execute(`query HydroStidLegacy { ${coreQuery} }`)
  }
  if (payload.errors?.length) throw new Error(payload.errors.map(error => error.message).filter(Boolean).join('; '))
  const equipment = payload.data?.equipment?.items ?? []
  const systems = payload.data?.systems?.items ?? Array.from(new Map(equipment.map(asset => [asset.system_id, {
    system_id: asset.system_id, facility_id: asset.facility_id, system_name: asset.system_id,
  }])).values())
  return {
    facilities: payload.data?.facilities?.items ?? [],
    systems,
    equipment,
    instruments: payload.data?.instruments?.items ?? [],
  }
}

export async function queryWeatherData(forceRefresh = false): Promise<WeatherData | null> {
  const config = await ensureConfig(forceRefresh, forceRefresh)
  if (!config?.graphqlUrl) return null
  const token = await silentToken([GRAPHQL_SCOPE], forceRefresh) ?? (forceRefresh ? await popupToken([GRAPHQL_SCOPE]) : null)
  if (!token) return null
  // The serving tables hold only the newest issue, pivoted one row per valid time, so the
  // whole page is a few hundred rows instead of the long tables' unbounded issue history.
  const values = 'precipitation temperature pressure relative_humidity dew_point solar_radiation wind_speed wind_gust wind_direction'
  const query = `query HydroWeather {
    locations: weather_locations(first: 100000) { items { location_id location_name latitude longitude elevation_m } }
    areas: weather_areas(first: 100000) { items { area_id area_name geometry_geojson crs metadata_json } }
    variables: weather_variables(first: 100000) { items { variable_id canonical_unit } }
    observations: weather_latest_observations(first: 100000) { items { source_id location_id observed_at_utc ${values} } }
    forecasts: weather_latest_forecasts(first: 100000) { items { source_id target_kind target_id reference_time_utc valid_time_utc lead_hours precipitation_interval_hours cumulative_precipitation rainfall_volume_m3 cumulative_rainfall_volume_m3 ${values} } }
  }`
  const response = await fetch(config.graphqlUrl, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  const text = await response.text()
  if (!response.ok) throw new Error(`Weather query failed (${response.status}).`)
  const payload = JSON.parse(text) as WeatherPayload
  if (payload.errors?.length) throw new Error(payload.errors.map(error => error.message).filter(Boolean).join('; '))
  return {
    locations: payload.data?.locations?.items ?? [],
    areas: payload.data?.areas?.items ?? [],
    variables: payload.data?.variables?.items ?? [],
    observations: payload.data?.observations?.items ?? [],
    forecasts: payload.data?.forecasts?.items ?? [],
  }
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

/** Run the coordinated Weather_002 -> Weather_003 -> Weather_020 pipeline. */
export const runWeatherNotebooks = createSingleFlight(
  async (onStatus?: JobProgress): Promise<JobStatus> => runWeatherSequence(onStatus),
)

/** Resume the coordinated weather pipeline started before a page reload. */
export async function resumeWeatherNotebooks(onStatus: JobProgress | undefined, sinceIso: string): Promise<JobStatus> {
  return runWeatherSequence(onStatus, sinceIso)
}

/** Run the RTI_011 post-seed notebook (seed SQL + publish GraphQL API + Data Agent SQL source),
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
      parameters: [
        {
          name: 'sql_db_item_name',
          value: 'hydro-operations-ui',
          type: 'Text',
        },
      ],
    },
  )
  // The notebook publishes new items (GraphQL API, Data Agent source) — force a fresh discovery.
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

// MCP intentionally has no server-side conversation threads. Keep a bounded transcript locally
// and include it only after the first turn; a new conversation still sends the question unchanged.
type DataAgentTurn = { question: string; answer: string }
const dataAgentConversation: DataAgentTurn[] = []

export function resetDataAgentConversation() {
  dataAgentConversation.length = 0
}

type McpContent = {
  type: string
  text?: string
  data?: string
  mimeType?: string
  resource?: { text?: string; blob?: string; mimeType?: string; uri?: string }
}

function dataAgentQuestion(question: string): string {
  if (!dataAgentConversation.length) return question
  const transcript = dataAgentConversation
    .slice(-4)
    .map(turn => `User: ${turn.question}\nAssistant: ${turn.answer}`)
    .join('\n\n')
    .slice(-12_000)
  return `Use this recent conversation only to resolve follow-up references. Re-query the live data when needed.\n\n${transcript}\n\nUser's latest question: ${question}`
}

export async function askDataAgent(question: string, onProgress?: (text: string) => void): Promise<AgentAnswer> {
  const config = await ensureConfig(true)
  if (config?.ontologyError) throw new Error(config.ontologyError)
  const endpoint = requireDataAgentEndpoint(config?.dataAgentUrl, config?.ontologyGeneration)
  if (!config?.ontologyId || !config.dataAgentId) throw new Error('Data Agent source identity is unavailable. Refresh Ontology v2 discovery before asking the agent.')
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  return invokeVerifiedDataAgent(
    { generation: config.ontologyGeneration, workspaceId: requireWorkspaceId(), ontologyId: config.ontologyId },
    async () => {
      const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${requireWorkspaceId()}/dataAgents/${config.dataAgentId}/getDefinition`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
      })
      return waitForDefinitionResult(response, token)
    },
    () => callDataAgentMcp(endpoint, token, question, onProgress),
  )
}

async function callDataAgentMcp(endpoint: string, token: string, question: string, onProgress?: (text: string) => void): Promise<AgentAnswer> {
  const [{ Client }, { StreamableHTTPClientTransport }] = await Promise.all([
    import('@modelcontextprotocol/sdk/client/index.js'),
    import('@modelcontextprotocol/sdk/client/streamableHttp.js'),
  ])
  const client = new Client({ name: 'hydro-operations-app', version: '1.0.0' })
  const transport = new StreamableHTTPClientTransport(new URL(endpoint), {
    requestInit: { headers: { Authorization: `Bearer ${token}`, ActivityId: crypto.randomUUID() } },
  })
  try {
    await client.connect(transport)
    const tool = (await client.listTools()).tools[0]
    if (!tool) throw new Error('The published Data Agent exposes no MCP tool.')
    const questionArgument = Object.keys(tool.inputSchema?.properties ?? {})[0]
    if (!questionArgument) throw new Error('The Data Agent MCP tool has no question argument.')
    const result = await client.callTool({
      name: tool.name,
      arguments: { [questionArgument]: dataAgentQuestion(question) },
    }, undefined, { timeout: 5 * 60_000, maxTotalTimeout: 5 * 60_000 })
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
    dataAgentConversation.push({ question, answer })
    if (dataAgentConversation.length > 4) dataAgentConversation.splice(0, dataAgentConversation.length - 4)
    onProgress?.(answer)
    return { text: answer, artifacts }
  } finally {
    await client.close().catch(() => undefined)
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
  const config = await ensureConfig(false)
  if (!config?.eventhouseQueryUri || !config.kqlDatabase) return null
  const cluster = config.eventhouseQueryUri.replace(/\/$/, '')
  const token = await silentToken([kustoScope(cluster)])
  if (!token) return null
  const response = await fetch(`${cluster}/v1/rest/query`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ db: config.kqlDatabase, csl: 'OPCUAEvents | where event_time > ago(24h) | summarize arg_max(event_time, value, quality) by opcua_node_id | take 500' }),
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

export type KustoResult = { columns: string[]; rows: unknown[][] }

/** Run an already-validated KQL query against the Eventhouse as the signed-in user.
 *  Callers outside the telemetry views must validate the query text first — see copilot/query.ts. */
export async function runKustoQuery(csl: string, maxRows: number): Promise<KustoResult> {
  const config = await ensureConfig(false)
  if (!config?.eventhouseQueryUri || !config.kqlDatabase) throw new Error('No Eventhouse is connected in this workspace.')
  const cluster = config.eventhouseQueryUri.replace(/\/$/, '')
  const token = await silentToken([kustoScope(cluster)])
  if (!token) throw new Error('Eventhouse consent is required. Connect telemetry first.')
  const response = await fetch(`${cluster}/v1/rest/query`, {
    method: 'POST',
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
  const payload = JSON.parse(text) as { Tables?: Array<{ Columns?: Array<{ ColumnName?: string }>; Rows?: unknown[][] }> }
  const table = payload.Tables?.[0]
  return {
    columns: (table?.Columns ?? []).map((column, index) => column.ColumnName ?? `column_${index}`),
    rows: table?.Rows ?? [],
  }
}
