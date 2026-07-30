import { PublicClientApplication } from '@azure/msal-browser'

const clientId = import.meta.env.VITE_RAYFIN_AAD_CLIENT_ID as string | undefined
const tenantId = (import.meta.env.VITE_FABRIC_TENANT_ID ?? import.meta.env.VITE_RAYFIN_TENANT_ID) as string | undefined
const workspaceId = (import.meta.env.VITE_FABRIC_WORKSPACE_ID ?? import.meta.env.VITE_RAYFIN_WORKSPACE_ID) as string | undefined ?? 'a79a4b7e-e508-4fa4-8b6f-15deadca0f34'
const pipelineId = import.meta.env.VITE_RAYFIN_STREAM_PIPELINE_ID as string | undefined
const agentUrl = import.meta.env.VITE_RAYFIN_DATA_AGENT_MCP_URL as string | undefined
const kqlCluster = import.meta.env.VITE_RAYFIN_KQL_CLUSTER_URI as string | undefined
const kqlDatabase = import.meta.env.VITE_RAYFIN_KQL_DATABASE as string | undefined
const stidGraphqlUrl = import.meta.env.VITE_RAYFIN_STID_GRAPHQL_URL as string | undefined
const msal = clientId && tenantId ? new PublicClientApplication({
  auth: { clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: location.origin },
  cache: { cacheLocation: 'localStorage' },
}) : undefined

const GRAPHQL_SCOPE = 'https://analysis.windows.net/powerbi/api/GraphQLApi.Execute.All'
const FABRIC_SCOPE = 'https://api.fabric.microsoft.com/Item.Execute.All'

export type ConnectTarget = 'stid' | 'telemetry' | 'stream'

let initialized = false

function kqlScope() { return `${(kqlCluster ?? '').replace(/\/$/, '')}/.default` }

function scopeFor(target: ConnectTarget) {
  if (target === 'stid') return GRAPHQL_SCOPE
  if (target === 'telemetry') return kqlScope()
  return FABRIC_SCOPE
}

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
async function silentToken(scope: string): Promise<string | null> {
  await ensureInit()
  const account = msal!.getAllAccounts()[0]
  if (!account) return null
  try {
    return (await msal!.acquireTokenSilent({ account, scopes: [scope] })).accessToken
  } catch (error) {
    console.warn('Silent token acquisition failed; interactive consent required.', error)
    return null
  }
}

/** Acquire a token interactively via popup (redirects are blocked inside the Fabric iframe).
 *  Must be invoked from a user gesture. */
async function popupToken(scope: string): Promise<string> {
  await ensureInit()
  const account = msal!.getAllAccounts()[0]
  const result = await msal!.acquireTokenPopup({ scopes: [scope], account: account ?? undefined })
  return result.accessToken
}

/** Sign in / consent for a resource using a popup; the caller then retries its query. */
export async function beginInteractiveConnect(target: ConnectTarget) {
  if (!msal) throw new Error('Microsoft Entra client configuration is missing.')
  await popupToken(scopeFor(target))
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

export function isStidConfigured() { return Boolean(stidGraphqlUrl) }

export async function queryStid(): Promise<StidData | null> {
  if (!stidGraphqlUrl) {
    console.warn('STID GraphQL endpoint is not configured.')
    return null
  }
  const token = await silentToken(GRAPHQL_SCOPE)
  if (!token) return null
  const query = `query HydroStid {
    silver_facilities(first: 20) { items { facility_id facility_name type country lat lon commissioned_date } }
    silver_equipments(first: 100) { items { equipment_id facility_id system_id equipment_type_code equipment_type_name tag manufacturer model criticality install_date status is_active } }
    silver_instruments(first: 500) { items { opcua_node_id tag instrument_id equipment_id system_id facility_id unit instrument_type is_active } }
  }`
  const response = await fetch(stidGraphqlUrl, {
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
  if (!pipelineId) throw new Error('Streaming pipeline is not configured.')
  const token = await silentToken(FABRIC_SCOPE) ?? await popupToken(FABRIC_SCOPE)
  const response = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items/${pipelineId}/jobs/instances?jobType=Pipeline`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) throw new Error(`Pipeline start failed (${response.status}).`)
}

export async function askDataAgent(question: string) {
  if (!agentUrl) return 'The Fabric Data Agent is not published to an MCP endpoint. This panel is available after an endpoint is configured.'
  const token = await silentToken(FABRIC_SCOPE) ?? await popupToken(FABRIC_SCOPE)
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

export function isTelemetryConfigured() { return Boolean(kqlCluster && kqlDatabase) }

export async function queryLatestTelemetry(): Promise<TelemetryReading[] | null> {
  if (!kqlCluster || !kqlDatabase) {
    console.warn('Eventhouse connection is not configured.')
    return []
  }
  const token = await silentToken(kqlScope())
  if (!token) return null
  const response = await fetch(`${kqlCluster.replace(/\/$/, '')}/v1/rest/query`, {
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
