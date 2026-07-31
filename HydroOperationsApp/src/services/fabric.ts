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
type ResolvedConfig = { pipelineId?: string; postseedNotebookId?: string; eventhouseQueryUri?: string; kqlDatabase?: string; graphqlUrl?: string; dataAgentUrl?: string }
let configCache: ResolvedConfig | null = null

/** Last-known-good values injected at build time; used only when live discovery is unavailable. */
function envConfig(): ResolvedConfig {
  return {
    pipelineId: import.meta.env.VITE_RAYFIN_STREAM_PIPELINE_ID as string | undefined,
    postseedNotebookId: import.meta.env.VITE_RAYFIN_POSTSEED_NOTEBOOK_ID as string | undefined,
    eventhouseQueryUri: import.meta.env.VITE_RAYFIN_KQL_CLUSTER_URI as string | undefined,
    kqlDatabase: import.meta.env.VITE_RAYFIN_KQL_DATABASE as string | undefined,
    graphqlUrl: graphqlUrlOverride,
    dataAgentUrl: agentUrl,
  }
}

async function listItems(token: string): Promise<WorkspaceItem[]> {
  const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) throw new Error(`Workspace listing failed (${res.status}).`)
  return ((await res.json()) as { value?: WorkspaceItem[] }).value ?? []
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
    // Fabric doesn't expose a GraphQL item's endpoint, but api.fabric.microsoft.com serves
    // queries directly at this deterministic path — no per-capacity cluster host or env override needed.
    const gql = items.find(i => i.type === 'GraphQLApi')
    const graphqlUrl = gql
      ? `https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/graphqlapis/${gql.id}/graphql`
      : undefined
    // The published Data Agent exposes an OpenAI Assistants-compatible endpoint under its item id.
    const da = items.find(i => i.type === 'DataAgent')
    const dataAgentUrl = da
      ? `https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/dataAgents/${da.id}/aiassistant/openai`
      : undefined
    configCache = {
      pipelineId: pipeline?.id ?? env.pipelineId,
      postseedNotebookId: notebook?.id ?? env.postseedNotebookId,
      eventhouseQueryUri: eventhouseQueryUri ?? env.eventhouseQueryUri,
      kqlDatabase: kqlDatabase ?? env.kqlDatabase,
      graphqlUrl: graphqlUrl ?? env.graphqlUrl,
      dataAgentUrl: dataAgentUrl ?? env.dataAgentUrl,
    }
    return configCache
  } catch (error) {
    console.warn('Workspace discovery failed; using configured fallback values.', error)
    return env
  }
}

/** Force a fresh workspace discovery on the next call (e.g. after RTI_011 provisions new items). */
export function clearWorkspaceConfigCache() { configCache = null }

// ---- Fabric item jobs: trigger + poll for live progress ----
export type JobStatus = 'NotStarted' | 'InProgress' | 'Completed' | 'Failed' | 'Cancelled' | 'Deduped'
export type JobProgress = (status: JobStatus) => void
const TERMINAL_STATUSES: JobStatus[] = ['Completed', 'Failed', 'Cancelled', 'Deduped']
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

type JobInstance = { id?: string; status?: JobStatus; startTimeUtc?: string; failureReason?: { message?: string } }
const ACTIVE_STATUSES: JobStatus[] = ['NotStarted', 'InProgress']

/** Newest job instance started at/after `sinceIso` — used when the 202 Location header is not CORS-exposed. */
async function latestInstance(token: string, itemId: string, sinceIso: string): Promise<JobInstance | undefined> {
  const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items/${itemId}/jobs/instances`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) return undefined
  const list = ((await res.json()) as { value?: JobInstance[] }).value ?? []
  return list
    .filter(i => !i.startTimeUtc || i.startTimeUtc >= sinceIso)
    .sort((a, b) => (b.startTimeUtc ?? '').localeCompare(a.startTimeUtc ?? ''))[0]
}

/** Newest still-running (NotStarted/InProgress) instance for an item, so callers can reattach
 *  instead of starting a duplicate run after a refresh or repeated clicks. */
async function activeInstance(token: string, itemId: string): Promise<JobInstance | undefined> {
  const res = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items/${itemId}/jobs/instances`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) return undefined
  const list = ((await res.json()) as { value?: JobInstance[] }).value ?? []
  return list
    .filter(i => i.status && ACTIVE_STATUSES.includes(i.status))
    .sort((a, b) => (b.startTimeUtc ?? '').localeCompare(a.startTimeUtc ?? ''))[0]
}

/** Trigger a Fabric item job and poll until a terminal (or requested `stopAt`) status, reporting progress. */
async function runJob(itemId: string, jobType: string, onStatus?: JobProgress, opts?: { stopAt?: JobStatus[]; timeoutMs?: number; reuseActive?: boolean }): Promise<JobStatus> {
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
    const trigger = await fetch(`https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/items/${itemId}/jobs/instances?jobType=${jobType}`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}` },
    })
    if (!trigger.ok && trigger.status !== 202) throw new Error(`Job start failed (${trigger.status}).`)
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

// Fabric API for GraphQL exposes each Lakehouse table under its own name; app-side keys are
// pinned via GraphQL aliases so the client stays stable regardless of table naming.
type StidPayload = {
  data?: {
    facilities?: { items?: Facility[] }
    equipment?: { items?: Equipment[] }
    instruments?: { items?: Instrument[] }
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
  // Aliases (facilities/equipment/instruments) map to the real Lakehouse tables exposed by the
  // GraphQL API. Fabric auto-pluralizes the root field, so the equipment table is `silver_equipments`.
  const query = `query HydroStid {
    facilities: silver_facilities(first: 20) { items { facility_id facility_name type country lat lon commissioned_date } }
    equipment: silver_equipments(first: 100) { items { equipment_id facility_id system_id equipment_type_code equipment_type_name tag manufacturer model criticality install_date status is_active } }
    instruments: silver_instruments(first: 500) { items { opcua_node_id tag instrument_id equipment_id system_id facility_id unit instrument_type is_active } }
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
    facilities: payload.data?.facilities?.items ?? [],
    equipment: payload.data?.equipment?.items ?? [],
    instruments: payload.data?.instruments?.items ?? [],
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

/** Run the RTI_011 post-seed notebook (seed SQL + publish GraphQL API + Data Agent SQL source),
 *  polling to completion so the caller can show progress. Rediscovers new items on success. */
export async function runPostSeedNotebook(onStatus?: JobProgress): Promise<JobStatus> {
  const config = await ensureConfig(true)
  if (!config?.postseedNotebookId) throw new Error(`The ${postseedNotebookName} notebook was not found in the workspace.`)
  const status = await runJob(config.postseedNotebookId, 'RunNotebook', onStatus, { timeoutMs: 15 * 60_000, reuseActive: true })
  // The notebook publishes new items (GraphQL API, Data Agent source) — force a fresh discovery.
  if (status === 'Completed') clearWorkspaceConfigCache()
  return status
}

/** Resume tracking a post-seed notebook run that was already started before a page reload. */
export async function resumePostSeedNotebook(onStatus: JobProgress | undefined, sinceIso: string): Promise<JobStatus> {
  const config = await ensureConfig(true)
  if (!config?.postseedNotebookId) throw new Error(`The ${postseedNotebookName} notebook was not found in the workspace.`)
  const status = await pollLatestInstance(config.postseedNotebookId, sinceIso, onStatus, { timeoutMs: 15 * 60_000 })
  if (status === 'Completed') clearWorkspaceConfigCache()
  return status
}

// The published Fabric Data Agent exposes an OpenAI Assistants-compatible endpoint
// (.../dataAgents/{id}/aiassistant/openai) that already fans out to its SQL DB + ontology sources.
// Runtime guidance so answers come back as clean, professional, tabular Markdown.
const AGENT_FORMAT_INSTRUCTIONS = 'You are the Operations Copilot for a hydropower operations team. Answer in concise, professional GitHub-flavored Markdown. Whenever you return more than one record (facilities, equipment/assets, instruments, signals, or work orders), present them as a Markdown table with clear human-readable column headers and include units where known. Lead with a one-line summary, then the table. Use short ISO-like dates. If the connected sources cannot answer, say so briefly and name the data that would be needed.'

// Fabric Data Agents apply their own system prompt and largely ignore the assistant-level
// `instructions`, so the formatting contract is also injected into the user turn to force it.
const FORMAT_DIRECTIVE = [
  'FORMATTING CONTRACT — you MUST obey every rule below when answering:',
  '1. Reply ONLY in GitHub-flavored Markdown.',
  '2. If the answer has more than ONE record, or compares fields across items (equipment, facilities, instruments, signals, work orders, etc.), you MUST render it as a Markdown table — NEVER a bulleted or numbered list. Tabular data as bullets is not acceptable.',
  '3. Table format: a bold, human-readable header row (e.g. **Equipment ID | Manufacturer | Model | Criticality**), a separator row, then one row per record. Right-size columns; include units in the header where known.',
  '4. Put a single short summary sentence ABOVE the table (e.g. "15 turbines across 3 facilities:"). No prose after the table unless a caveat is needed.',
  '5. For a single scalar answer, reply in one short bolded sentence — no table.',
  '6. Use short ISO-like dates (YYYY-MM-DD). Keep language crisp and professional.',
  '',
  'QUESTION: ',
].join('\n')
export type AgentUsage = { prompt: number; completion: number; total: number }
export type AgentAnswer = { text: string; usage?: AgentUsage }
// Parses an OpenAI Assistants SSE stream, surfacing incremental assistant text via onProgress.
type StreamEvent = { object?: string; delta?: { content?: Array<{ text?: { value?: string } }> }; content?: Array<{ text?: { value?: string } }>; usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }
async function readAssistantStream(body: ReadableStream<Uint8Array>, onProgress?: (text: string) => void): Promise<AgentAnswer> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let answer = ''
  let usage: AgentUsage | undefined
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    for (const block of blocks) {
      const dataLine = block.split('\n').find(line => line.startsWith('data:'))
      if (!dataLine) continue
      const data = dataLine.slice(5).trim()
      if (!data || data === '[DONE]') continue
      let event: StreamEvent
      try { event = JSON.parse(data) as StreamEvent } catch { continue }
      if (event.usage && typeof event.usage.total_tokens === 'number') {
        usage = { prompt: event.usage.prompt_tokens ?? 0, completion: event.usage.completion_tokens ?? 0, total: event.usage.total_tokens }
      }
      const deltas = event.delta?.content
      if (Array.isArray(deltas)) {
        for (const part of deltas) { const chunk = part.text?.value; if (chunk) { answer += chunk; onProgress?.(answer) } }
      } else if (event.object === 'thread.message' && Array.isArray(event.content) && !answer) {
        const full = event.content.map(part => part.text?.value ?? '').filter(Boolean).join('\n')
        if (full) { answer = full; onProgress?.(answer) }
      }
    }
  }
  return { text: answer, usage }
}

export async function askDataAgent(question: string, onProgress?: (text: string) => void): Promise<AgentAnswer> {
  const config = await ensureConfig(true)
  const base = config?.dataAgentUrl
  if (!base) return { text: 'No published Fabric Data Agent was found in this workspace. Publish the Data Agent (run RTI_011), then try again.' }
  const token = await fabricToken(true)
  if (!token) throw new Error('Fabric sign-in is required.')
  let usage: AgentUsage | undefined

  const baseHeaders: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/json',
    'Content-Type': 'application/json',
    ActivityId: crypto.randomUUID(),
  }
  const version = 'api-version=2024-05-01-preview'
  const url = (path: string) => `${base}${path}${path.includes('?') ? '&' : '?'}${version}`
  const oai = async (path: string, init?: RequestInit) => {
    const res = await fetch(url(path), { ...init, headers: { ...baseHeaders, 'OpenAI-Beta': 'assistants=v2' } })
    if (!res.ok) throw new Error(`Data Agent request failed (${res.status}).`)
    return res.json()
  }

  const assistant = await oai('/assistants', { method: 'POST', body: JSON.stringify({ model: 'not used', instructions: AGENT_FORMAT_INSTRUCTIONS }) }) as { id: string }
  // Threads are created through Fabric's private endpoint (get-or-create by tag), then driven by id.
  const privateBase = base.replace('/aiassistant/openai', '/__private/aiassistant')
  const threadRes = await fetch(`${privateBase}/threads/fabric?tag="hydro-ops-${crypto.randomUUID()}"`, { headers: baseHeaders })
  if (!threadRes.ok) throw new Error(`Data Agent thread failed (${threadRes.status}).`)
  const thread = await threadRes.json() as { id: string }

  await oai(`/threads/${thread.id}/messages`, { method: 'POST', body: JSON.stringify({ role: 'user', content: `${FORMAT_DIRECTIVE}${question}` }) })

  // Ask for a streamed run so tokens surface as they're generated; fall back to polling if unsupported.
  const runRes = await fetch(url(`/threads/${thread.id}/runs`), {
    method: 'POST',
    headers: { ...baseHeaders, Accept: 'text/event-stream', 'OpenAI-Beta': 'assistants=v2' },
    body: JSON.stringify({ assistant_id: assistant.id, stream: true }),
  })
  if (!runRes.ok) throw new Error(`Data Agent run failed (${runRes.status}).`)

  if ((runRes.headers.get('content-type') ?? '').includes('text/event-stream') && runRes.body) {
    const streamed = await readAssistantStream(runRes.body, onProgress)
    usage = streamed.usage ?? usage
    if (streamed.text) return { text: streamed.text, usage }
    // Stream closed without assistant text — fall through to fetch the final message list below.
  } else {
    // Endpoint ignored streaming and returned the run object as JSON — poll it to completion.
    let run = await runRes.json() as { id: string; status: string; usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }
    const deadline = Date.now() + 120_000
    while (['queued', 'in_progress', 'cancelling', 'requires_action'].includes(run.status) && Date.now() < deadline) {
      await delay(2000)
      run = await oai(`/threads/${thread.id}/runs/${run.id}`) as { id: string; status: string; usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }
    }
    if (run.status !== 'completed') throw new Error(`The Data Agent run ${run.status === 'failed' ? 'failed' : `did not finish (${run.status})`}.`)
    if (run.usage && typeof run.usage.total_tokens === 'number') usage = { prompt: run.usage.prompt_tokens ?? 0, completion: run.usage.completion_tokens ?? 0, total: run.usage.total_tokens }
  }

  const list = await oai(`/threads/${thread.id}/messages?order=desc`) as { data?: Array<{ role?: string; content?: Array<{ text?: { value?: string } }> }> }
  const answer = list.data?.find(message => message.role === 'assistant')
    ?.content?.map(part => part.text?.value ?? '').filter(Boolean).join('\n')
  return { text: answer || 'The Data Agent returned no answer.', usage }
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
