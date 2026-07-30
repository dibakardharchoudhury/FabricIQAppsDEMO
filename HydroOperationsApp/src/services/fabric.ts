import { PublicClientApplication } from '@azure/msal-browser'

const clientId = import.meta.env.VITE_RAYFIN_AAD_CLIENT_ID as string | undefined
const tenantId = (import.meta.env.VITE_FABRIC_TENANT_ID ?? import.meta.env.VITE_RAYFIN_TENANT_ID) as string | undefined
const workspaceId = (import.meta.env.VITE_FABRIC_WORKSPACE_ID ?? import.meta.env.VITE_RAYFIN_WORKSPACE_ID) as string | undefined ?? 'a79a4b7e-e508-4fa4-8b6f-15deadca0f34'
const agentUrl = import.meta.env.VITE_RAYFIN_DATA_AGENT_MCP_URL as string | undefined

// Artifact ids / URIs are DISCOVERED at runtime from the workspace; only stable display names are configured.
const pipelineName = (import.meta.env.VITE_RAYFIN_STREAM_PIPELINE_NAME as string | undefined) ?? '02_Pipe_Stream'
const postseedNotebookName = (import.meta.env.VITE_RAYFIN_POSTSEED_NOTEBOOK_NAME as string | undefined) ?? 'RTI_011_seed_sql_wire_graphql_agent'
const eventhouseName = (import.meta.env.VITE_RAYFIN_EVENTHOUSE_NAME as string | undefined) ?? 'RTI_Demo_Eventhouse_V6'
const graphqlUrlOverride = import.meta.env.VITE_RAYFIN_STID_GRAPHQL_URL as string | undefined

const msal = clientId && tenantId ? new PublicClientApplication({
  auth: { clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: location.origin },
  cache: { cacheLocation: 'localStorage' },
}) : undefined

const GRAPHQL_SCOPE = 'https://analysis.windows.net/powerbi/api/GraphQLApi.Execute.All'
const FABRIC_SCOPES = ['https://api.fabric.microsoft.com/Workspace.Read.All', 'https://api.fabric.microsoft.com/Item.Execute.All']

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
async function silentToken(scopes: string[]): Promise<string | null> {
  await ensureInit()
  const account = msal!.getAllAccounts()[0]
  if (!account) return null
  try {
    return (await msal!.acquireTokenSilent({ account, scopes })).accessToken
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
    console.warn('Fabric consent unavailable; falling back to configured values.', error)
    return null
  }
}

// ---- Workspace artifact discovery (resolve ids/URIs by display name, never hardcode) ----
type WorkspaceItem = { id: string; type: string; displayName: string }
type ResolvedConfig = { pipelineId?: string; postseedNotebookId?: string; eventhouseQueryUri?: string; kqlDatabase?: string; graphqlUrl?: string }
let configCache: ResolvedConfig | null = null

/** Last-known-good values injected at build time; used only when live discovery is unavailable. */
function envConfig(): ResolvedConfig {
  return {
    pipelineId: import.meta.env.VITE_RAYFIN_STREAM_PIPELINE_ID as string | undefined,
    postseedNotebookId: import.meta.env.VITE_RAYFIN_POSTSEED_NOTEBOOK_ID as string | undefined,
    eventhouseQueryUri: import.meta.env.VITE_RAYFIN_KQL_CLUSTER_URI as string | undefined,
    kqlDatabase: import.meta.env.VITE_RAYFIN_KQL_DATABASE as string | undefined,
    graphqlUrl: graphqlUrlOverride,
  }
}

async function listItems(token: string): Promise<WorkspaceItem[]> {
  const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) throw new Error(`Workspace listing failed (${res.status}).`)
  return ((await res.json()) as { value?: WorkspaceItem[] }).value ?? []
}

async function resolveGraphqlEndpoint(token: string, id: string): Promise<string | undefined> {
  try {
    const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items/${id}`, { headers: { Authorization: `Bearer ${token}` } })
    if (!res.ok) return undefined
    const props = ((await res.json()) as { properties?: Record<string, unknown> }).properties
    return (props?.graphQlEndpoint ?? props?.graphqlEndpoint ?? props?.endpoint) as string | undefined
  } catch { return undefined }
}

/** Discover artifact ids/URIs from the workspace by display name; fall back to build-time env values.
 *  Discovered values are cached for the session so no id can go stale. */
async function ensureConfig(interactive: boolean): Promise<ResolvedConfig | null> {
  if (configCache) return configCache
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
    const eh = find('Eventhouse', eventhouseName) ?? items.find(i => i.type === 'Eventhouse')
    let eventhouseQueryUri: string | undefined
    let kqlDatabase: string | undefined
    if (eh) {
      const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/eventhouses/${eh.id}`, { headers: { Authorization: `Bearer ${token}` } })
      if (res.ok) {
        const props = ((await res.json()) as { properties?: { queryServiceUri?: string; databasesItemIds?: string[] } }).properties
        eventhouseQueryUri = props?.queryServiceUri
        const dbId = props?.databasesItemIds?.[0]
        kqlDatabase = items.find(i => i.id === dbId)?.displayName ?? eh.displayName
      }
    }
    const gql = items.find(i => i.type === 'GraphQLApi')
    let graphqlUrl: string | undefined
    if (gql) {
      graphqlUrl = (await resolveGraphqlEndpoint(token, gql.id))
        ?? (graphqlUrlOverride ? graphqlUrlOverride.replace(/graphqlapis\/[0-9a-fA-F-]+/, `graphqlapis/${gql.id}`) : undefined)
    }
    configCache = {
      pipelineId: pipeline?.id ?? env.pipelineId,
      postseedNotebookId: notebook?.id ?? env.postseedNotebookId,
      eventhouseQueryUri: eventhouseQueryUri ?? env.eventhouseQueryUri,
      kqlDatabase: kqlDatabase ?? env.kqlDatabase,
      graphqlUrl: graphqlUrl ?? env.graphqlUrl,
    }
    return configCache
  } catch (error) {
    console.warn('Workspace discovery failed; using configured fallback values.', error)
    return env
  }
}

/** Force a fresh workspace discovery on the next call (e.g. after RTI_011 provisions new items). */
export function clearWorkspaceConfigCache() { configCache = null }

/** Sign in / consent for a resource using a popup; the caller then retries its query. */
export async function beginInteractiveConnect(target: ConnectTarget) {
  if (!msal) throw new Error('Microsoft Entra client configuration is missing.')
  const config = await ensureConfig(true)
  if (target === 'stid') {
    if (!config?.graphqlUrl) throw new Error('No GraphQL API found in the workspace. Publish the STID GraphQL API (run RTI_011) and try again.')
    await popupToken([GRAPHQL_SCOPE])
  } else if (target === 'telemetry') {
    if (!config?.eventhouseQueryUri) throw new Error('No Eventhouse found in the workspace.')
    await popupToken([`${config.eventhouseQueryUri.replace(/\/$/, '')}/.default`])
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

type StidPayload = {
  data?: {
    silver_facilities?: { items?: Facility[] }
    silver_equipments?: { items?: Equipment[] }
    silver_instruments?: { items?: Instrument[] }
  }
  errors?: Array<{ message?: string }>
}

export type StidData = { facilities: Facility[]; equipment: Equipment[]; instruments: Instrument[] }

export function isStidConfigured() { return Boolean(msal) }

export async function queryStid(): Promise<StidData | null> {
  const config = await ensureConfig(false)
  if (!config?.graphqlUrl) return null
  const token = await silentToken([GRAPHQL_SCOPE])
  if (!token) return null
  const query = `query HydroStid {
    silver_facilities(first: 20) { items { facility_id facility_name type country lat lon commissioned_date } }
    silver_equipments(first: 100) { items { equipment_id facility_id system_id equipment_type_code equipment_type_name tag manufacturer model criticality install_date status is_active } }
    silver_instruments(first: 500) { items { opcua_node_id tag instrument_id equipment_id system_id facility_id unit instrument_type is_active } }
  }`
  const response = await fetch(config.graphqlUrl, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  const text = await response.text()
  if (!response.ok) {
    console.error('STID GraphQL request failed.', response.status, text.slice(0, 500))
    throw new Error(`STID query failed (${response.status}).`)
  }
  const payload = JSON.parse(text) as StidPayload
  if (payload.errors?.length) throw new Error(payload.errors.map(error => error.message).filter(Boolean).join('; '))
  return {
    facilities: payload.data?.silver_facilities?.items ?? [],
    equipment: payload.data?.silver_equipments?.items ?? [],
    instruments: payload.data?.silver_instruments?.items ?? [],
  }
}

export async function startStreamingPipeline() {
  const config = await ensureConfig(true)
  if (!config?.pipelineId) throw new Error(`Streaming pipeline (${pipelineName}) was not found in the workspace.`)
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required to start the stream.')
  const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items/${config.pipelineId}/jobs/instances?jobType=Pipeline`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok && response.status !== 202) throw new Error(`Pipeline start failed (${response.status}).`)
}

export function isPostSeedConfigured() { return Boolean(msal) }

/** Fire the RTI_011 post-seed notebook (seed SQL + GraphQL + Data Agent SQL source + republish).
 *  Fire-and-forget: Fabric returns 202 with the job location; we don't wait for completion. */
export async function runPostSeedNotebook() {
  const config = await ensureConfig(true)
  if (!config?.postseedNotebookId) throw new Error(`The ${postseedNotebookName} notebook was not found in the workspace.`)
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items/${config.postseedNotebookId}/jobs/instances?jobType=RunNotebook`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok && response.status !== 202) throw new Error(`Post-seed notebook run failed (${response.status}).`)
}

export async function askDataAgent(question: string) {
  if (!agentUrl) return 'The Fabric Data Agent is not published to an MCP endpoint. This panel is available after an endpoint is configured.'
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  const response = await fetch(agentUrl, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: crypto.randomUUID(), method: 'tools/call', params: { name: 'ask', arguments: { question } } }),
  })
  if (!response.ok) throw new Error(`Data Agent request failed (${response.status}).`)
  const payload = await response.json() as { result?: { content?: Array<{ text?: string }> } }
  return payload.result?.content?.map(item => item.text).filter(Boolean).join('\n') || 'The Data Agent returned no text.'
}

export type TelemetryReading = { opcuaNodeId: string; eventTime: string; value: number; quality: string }

export function isTelemetryConfigured() { return Boolean(msal) }

export async function queryLatestTelemetry(): Promise<TelemetryReading[] | null> {
  const config = await ensureConfig(false)
  if (!config?.eventhouseQueryUri || !config.kqlDatabase) return null
  const cluster = config.eventhouseQueryUri.replace(/\/$/, '')
  const token = await silentToken([`${cluster}/.default`])
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
