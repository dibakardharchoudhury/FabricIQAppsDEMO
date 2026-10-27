import type { AgentVisualization } from '../assistantStream.ts'
import type { AgentTraceEntry, OrchestrationEvent, WorkOrderProposal } from './orchestration.ts'

export type HostedSource = {
  tenant_id: string
  workspace_id: string
  ontology_id: string
  generation: 2
  configuration_digest: string
}
export type SourceTokens = Record<'fabric' | 'foundry' | 'graphql' | 'kusto', string>
export type HostedChat = {
  question: string
  charts_requested?: boolean
  native_sources?: Array<'data-agent' | 'ontology'>
  proposal_priority?: 'Low' | 'Medium' | 'High' | 'Critical'
}
export type HostedDecision = {
  proposal_id: string
  proposal_digest: string
  approved: boolean
  edits?: { title: string; description: string; priority: 'Low' | 'Medium' | 'High' | 'Critical' }
}
export function hostedDecisionEdits(value: Pick<WorkOrderProposal, 'title' | 'description' | 'priority'>): NonNullable<HostedDecision['edits']> {
  return { title: value.title, description: value.description, priority: value.priority }
}
export type HostedAnswer = {
  runId: string
  text: string
  visualizations: AgentVisualization[]
  receipt: Record<string, unknown>
  proposals: WorkOrderProposal[]
  executionEvents: OrchestrationEvent[]
}

export class HostedInvocationError extends Error {
  readonly detail: Record<string, unknown> | undefined
  readonly status: number
  constructor(status: number, detail?: Record<string, unknown>) {
    super(detail?.category === 'native_refusal'
      ? 'The selected Fabric Data Agent refused the request. Its owner must review the provider response before retrying. ' +
        'No direct-source fallback was used. No answer or write outcome is certified.' +
        (detail.audit_available === false ? ' The execution audit could not be saved; contact the service owner.' : '')
      : `Hosted invocation failed (HTTP ${status}). No answer or write outcome is certified.`)
    this.status = status
    this.detail = detail
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Hosted response is not an object.')
  return value as Record<string, unknown>
}

function visualization(value: unknown): AgentVisualization {
  const item = record(value)
  if (!['line', 'bar', 'scatter'].includes(String(item.chartType))
    || typeof item.title !== 'string' || typeof item.xColumn !== 'string'
    || !Array.isArray(item.yColumns) || !item.yColumns.length || !item.yColumns.every(key => typeof key === 'string')
    || typeof item.inlineCsvData !== 'string' || typeof item.xAxisTitle !== 'string') {
    throw new Error('Hosted chart does not match the presentation contract.')
  }
  return { chartType: String(item.chartType), title: item.title, xColumn: item.xColumn,
    yColumns: item.yColumns, inlineCsvData: item.inlineCsvData, xAxisTitle: item.xAxisTitle }
}

function executionEvent(value: unknown): OrchestrationEvent {
  const item = record(value)
  const role = item.role
  if ((role !== 'supervisor' && role !== 'qa' && role !== 'rca' && role !== 'work-order' && role !== 'fabric-iq' && role !== 'weather')
    || item.status !== 'completed' || typeof item.id !== 'string' || typeof item.label !== 'string'
    || typeof item.detail !== 'string' || typeof item.timestamp !== 'number' || !Number.isFinite(item.timestamp)
    || typeof item.agentName !== 'string' || !item.agentName || typeof item.responseId !== 'string' || !item.responseId) {
    throw new Error('Hosted specialist execution receipt is invalid.')
  }
  return { id: item.id, role, status: 'completed', label: item.label, detail: item.detail,
    timestamp: item.timestamp, agentName: item.agentName, responseId: item.responseId }
}

function streamedExecutionEvent(value: unknown): OrchestrationEvent {
  const item = record(value)
  const role = item.role
  const status = item.status
  if ((role !== 'supervisor' && role !== 'qa' && role !== 'rca' && role !== 'work-order' && role !== 'fabric-iq' && role !== 'weather')
    || (status !== 'queued' && status !== 'running' && status !== 'completed' && status !== 'error')
    || typeof item.id !== 'string' || !item.id || typeof item.label !== 'string'
    || typeof item.detail !== 'string' || typeof item.timestamp !== 'number' || !Number.isFinite(item.timestamp)
    || (item.agentName !== undefined && typeof item.agentName !== 'string')
    || (item.responseId !== undefined && typeof item.responseId !== 'string')
    || (item.parentId !== undefined && typeof item.parentId !== 'string')
    || (item.parentCallId !== undefined && typeof item.parentCallId !== 'string')) {
    throw new Error('Hosted streaming execution event is invalid.')
  }
  let trace: AgentTraceEntry[] | undefined
  if (item.trace !== undefined) {
    if (!Array.isArray(item.trace)) throw new Error('Hosted streaming execution trace is invalid.')
    trace = item.trace.map(value => {
      const entry = record(value)
      if (typeof entry.id !== 'string' || typeof entry.timestamp !== 'number' || !Number.isFinite(entry.timestamp)
        || (entry.source !== 'foundry' && entry.source !== 'application') || typeof entry.label !== 'string'
        || (entry.responseId !== undefined && typeof entry.responseId !== 'string')
        || (entry.callId !== undefined && typeof entry.callId !== 'string')
        || (entry.failed !== undefined && typeof entry.failed !== 'boolean')
        || (entry.activity !== undefined && entry.activity !== 'tool-start' && entry.activity !== 'tool-end'
          && entry.activity !== 'delegation-return' && entry.activity !== 'checked-presentation'
          && entry.activity !== 'validation')) {
        throw new Error('Hosted streaming execution trace entry is invalid.')
      }
      return entry as AgentTraceEntry
    })
  }
  return {
    id: item.id, role, status, label: item.label, detail: item.detail, timestamp: item.timestamp,
    ...(typeof item.agentName === 'string' ? { agentName: item.agentName } : {}),
    ...(typeof item.responseId === 'string' ? { responseId: item.responseId } : {}),
    ...(typeof item.parentId === 'string' ? { parentId: item.parentId } : {}),
    ...(typeof item.parentCallId === 'string' ? { parentCallId: item.parentCallId } : {}),
    ...(trace ? { trace } : {}),
  }
}

export class HostedTransport {
  private sessionId: string
  private readonly sessionKey: string | undefined
  private running = false
  private inFlight = 0
  private readonly endpoint: URL
  private readonly source: HostedSource
  private readonly credentials: (interactive: boolean) => Promise<SourceTokens>
  private readonly fetcher: typeof fetch
  private readonly streamLivenessMs: number
  private previousRunId: string | undefined
  private interruptedRunId: string | undefined
  private conversationVersion = 0
  private warmed = false

  constructor(
    projectEndpoint: string, invocationUrl: string, source: HostedSource,
    credentials: (interactive: boolean) => Promise<SourceTokens>,
    fetcher: typeof fetch = (input, init) => globalThis.fetch(input, init),
    streamLivenessMs = 45_000,
    sessionScope?: string,
  ) {
    const project = new URL(projectEndpoint)
    const endpoint = new URL(invocationUrl)
    if (project.protocol !== 'https:' || !project.hostname.endsWith('.services.ai.azure.com')
      || !/^\/api\/projects\/[A-Za-z0-9._-]+\/?$/.test(project.pathname)
      || project.username || project.password || project.search || project.hash
      || endpoint.origin !== project.origin
      || !endpoint.pathname.startsWith(project.pathname.replace(/\/$/, '') + '/agents/')
      || !endpoint.pathname.endsWith('/invocations')
      || endpoint.username || endpoint.password || endpoint.hash
      || [...endpoint.searchParams.keys()].some(key => key !== 'api-version')
      || source.generation !== 2 || !/^[a-f0-9]{64}$/.test(source.configuration_digest)
      || !Number.isInteger(streamLivenessMs) || streamLivenessMs < 1_000) {
      throw new Error('Hosted transport requires an explicit same-project invocation URL and verified v2 source identity.')
    }
    this.endpoint = endpoint
    this.source = { ...source }
    this.credentials = credentials
    this.fetcher = fetcher
    this.streamLivenessMs = streamLivenessMs
    // Only an opaque container-session id is kept (per tab, engine and app build) so a reload reuses the warm hosted
    // container. Hosted sessions stay pinned to the agent version they started on, so a new build must not inherit
    // an old session. No answer, telemetry, token or other analytical data is ever stored.
    this.sessionKey = sessionScope ? `hydro.hosted.session.${sessionScope}` : undefined
    this.sessionId = this.restoreSession() ?? this.newSession()
  }

  private restoreSession(): string | undefined {
    if (!this.sessionKey) return undefined
    try {
      const stored = globalThis.sessionStorage?.getItem(this.sessionKey)
      return stored && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(stored) ? stored : undefined
    } catch { return undefined }
  }

  private newSession(): string {
    const id = crypto.randomUUID()
    if (this.sessionKey) {
      try { globalThis.sessionStorage?.setItem(this.sessionKey, id) } catch { /* storage is optional */ }
    }
    return id
  }

  clearConversation() {
    if (this.running || this.inFlight) throw new Error('Wait for the hosted request before resetting the conversation.')
    this.conversationVersion++
    this.previousRunId = undefined
    this.interruptedRunId = undefined
  }

  reset() {
    this.clearConversation()
    this.sessionId = this.newSession()
    this.warmed = false
  }

  /** Boots the hosted container for this session before the first question. The backend rejects the empty body
   *  immediately, so no tokens are sent and no work runs; only the cold start is absorbed. */
  async warm(): Promise<void> {
    if (this.warmed || this.running || this.inFlight) return
    this.warmed = true
    try {
      const token = (await this.credentials(false)).foundry
      if (typeof token !== 'string' || !token) { this.warmed = false; return }
      const endpoint = new URL(this.endpoint)
      endpoint.searchParams.set('agent_session_id', this.sessionId)
      await this.fetcher(endpoint, {
        method: 'POST', redirect: 'error', cache: 'no-store',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: '{}',
      })
    } catch { this.warmed = false }
  }

  private async invoke(
    body: Record<string, unknown>, onEvent?: (event: OrchestrationEvent) => void,
  ): Promise<Record<string, unknown>> {
    this.inFlight++
    let tokens: SourceTokens | undefined
    try {
      tokens = await this.credentials(body.operation === 'run' || body.operation === 'decide' || body.operation === 'reconcile')
      if ((['fabric', 'foundry', 'graphql', 'kusto'] as const).some(kind => {
        const token = tokens?.[kind]
        return typeof token !== 'string' || !token
      })) {
        throw new Error('Renew source access through the existing sign-in session.')
      }
      const endpoint = new URL(this.endpoint)
      endpoint.searchParams.set('agent_session_id', this.sessionId)
      const response = await this.fetcher(endpoint, {
        method: 'POST', redirect: 'error', cache: 'no-store',
        headers: { Authorization: `Bearer ${tokens.foundry}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, ...(onEvent ? { stream: true } : {}), tokens }),
      })
      if (onEvent && response.headers.get('content-type')?.includes('application/x-ndjson')) {
          if (!response.body) throw new Error('Hosted streaming invocation returned no response body.')
          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          let answer: Record<string, unknown> | undefined
          while (true) {
            let timer: ReturnType<typeof setTimeout> | undefined
            const stalled = new Promise<never>((_resolve, reject) => {
              timer = setTimeout(
                () => reject(new Error('Hosted streaming invocation heartbeat was interrupted.')),
                this.streamLivenessMs,
              )
            })
            let chunk: ReadableStreamReadResult<Uint8Array>
            try {
              chunk = await Promise.race([reader.read(), stalled])
            } catch (error) {
              await reader.cancel().catch(() => undefined)
              throw error
            } finally {
              if (timer !== undefined) clearTimeout(timer)
            }
            const { value, done } = chunk
            buffer += decoder.decode(value, { stream: !done })
            const lines = buffer.split('\n')
            buffer = lines.pop() ?? ''
            for (const line of lines) {
              if (!line) continue
              const message = record(JSON.parse(line))
              if (message.type === 'run') {
                if (typeof message.run_id !== 'string' || !message.run_id) {
                  throw new Error('Hosted streaming invocation returned no recoverable run identity.')
                }
                const streamSource = record(message.source)
                if (Object.entries(this.source).some(([key, value]) => streamSource[key] !== value)) {
                  throw new Error('Hosted streaming invocation changed its verified source identity.')
                }
                this.interruptedRunId = message.run_id
              } else if (message.type === 'event') onEvent(streamedExecutionEvent(message.event))
              else if (message.type === 'answer') answer = record(message.answer)
              else if (message.type === 'error') {
                const status = typeof message.status === 'number' ? message.status : 500
                throw new HostedInvocationError(status,
                  message.detail && typeof message.detail === 'object' && !Array.isArray(message.detail)
                    ? record(message.detail) : undefined)
              } else {
                throw new Error('Hosted streaming invocation returned an unknown message type.')
              }
            }
            if (done) break
          }
          if (buffer.trim()) throw new Error('Hosted streaming invocation ended with an incomplete message.')
          if (!answer) throw new Error('Hosted streaming invocation returned no certified answer.')
          return answer
      }
      if (!response.ok) {
        if (!response.headers.get('content-type')?.includes('application/json')) {
          throw new HostedInvocationError(response.status)
        }
        const error = record(await response.json())
        const detail = error.detail
        throw new HostedInvocationError(response.status,
          detail && typeof detail === 'object' && !Array.isArray(detail) ? record(detail) : undefined)
      }
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Hosted invocation returned no JSON contract; hosting/sign-in success is not agent execution.')
      }
      return record(await response.json())
    } finally {
      if (tokens) for (const kind of ['fabric', 'foundry', 'graphql', 'kusto'] as const) tokens[kind] = ''
      this.inFlight--
    }
  }

  private async recoverInterruptedRun(): Promise<Record<string, unknown>> {
    const runId = this.interruptedRunId
    if (!runId) throw new Error('The interrupted hosted request has no server-assigned run identity.')
    let consecutiveTransportFailures = 0
    while (true) {
      try {
        const result = await this.invoke({ operation: 'result', run_id: runId })
        consecutiveTransportFailures = 0
        if (result.status === 'completed') return record(result.answer)
        if (result.status === 'failed') {
          const failure = result.failure
          throw new HostedInvocationError(500,
            failure && typeof failure === 'object' && !Array.isArray(failure) ? record(failure) : undefined)
        }
        if (result.status !== 'running' || result.run_id !== runId) {
          throw new Error('Hosted run recovery returned an invalid status contract.')
        }
      } catch (error) {
        if (error instanceof HostedInvocationError) throw error
        consecutiveTransportFailures++
        if (consecutiveTransportFailures >= 3) {
          throw new Error(
            `The hosted stream was interrupted and run ${runId} could not be recovered after three consecutive transport attempts.`,
            { cause: error },
          )
        }
      }
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
  }

  async run(chat: HostedChat, onEvents?: (events: OrchestrationEvent[]) => void): Promise<HostedAnswer> {
    if (this.running) throw new Error('A hosted request is already running.')
    this.running = true
    try {
      const streamedEvents: OrchestrationEvent[] = []
      this.interruptedRunId = undefined
      let reply: Record<string, unknown>
      try {
        reply = await this.invoke({ operation: 'run', chat: { ...chat,
          ...(this.previousRunId ? { previous_run_id: this.previousRunId } : {}) } }, event => {
            const index = streamedEvents.findIndex(item => item.id === event.id)
            if (index < 0) streamedEvents.push(event)
            else streamedEvents[index] = event
            onEvents?.([...streamedEvents])
          })
      } catch (error) {
        if (!this.interruptedRunId || error instanceof HostedInvocationError) throw error
        reply = await this.recoverInterruptedRun()
      }
      const source = record(reply.source)
      if (Object.entries(this.source).some(([key, value]) => source[key] !== value)
        || reply.production_write_executed !== false || typeof reply.run_id !== 'string') {
        throw new Error('Hosted answer changed its verified source identity or read-only execution boundary.')
      }
      const presentation = record(reply.presentation)
      if (presentation.schema_version !== 1 || typeof presentation.text !== 'string'
        || !Array.isArray(presentation.visualizations) || !Array.isArray(presentation.execution_events)) {
        throw new Error('Hosted answer has no supported backend-owned presentation.')
      }
      const runId = reply.run_id
      if (!Array.isArray(reply.proposals)) throw new Error('Hosted answer has no typed approval-card collection.')
      const digests = record(reply.proposal_digests)
      const cardIds = new Set<string>()
      const conversationVersion = this.conversationVersion
      const proposals = reply.proposals.map(value => {
        const draft = record(value)
        if (typeof draft.id !== 'string' || draft.run_id !== runId
          || typeof draft.equipment_id !== 'string' || typeof draft.title !== 'string'
          || typeof draft.description !== 'string' || typeof draft.expires_at !== 'string'
          || typeof draft.work_read_at !== 'string' || !Number.isFinite(Date.parse(draft.work_read_at))
          || !Number.isFinite(Date.parse(draft.expires_at))
          || (draft.priority !== 'Low' && draft.priority !== 'Medium' && draft.priority !== 'High' && draft.priority !== 'Critical')
          || (draft.instrument_id != null && typeof draft.instrument_id !== 'string')
          || (draft.opcua_node_id != null && typeof draft.opcua_node_id !== 'string')) {
          throw new Error('Hosted approval card does not match its run or typed target.')
        }
        const cardSource = record(draft.source)
        if (Object.entries(this.source).some(([key, value]) => cardSource[key] !== value)) {
          throw new Error('Hosted approval card changed its verified source identity.')
        }
        const digest = digests[draft.id]
        if (typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) {
          throw new Error('Hosted approval card has no bound approval digest.')
        }
        const proposalId = draft.id
        if (cardIds.has(proposalId)) throw new Error('Hosted answer repeated an approval-card identity.')
        cardIds.add(proposalId)
        const decide = (approved: boolean, edits: HostedDecision['edits'], reconcile: boolean) => {
          if (conversationVersion !== this.conversationVersion) return Promise.reject(new Error('This approval belongs to a reset conversation. Request a fresh draft.'))
          return this.decide(runId, { proposal_id: proposalId, proposal_digest: digest, approved,
            ...(approved ? { edits } : {}) }, reconcile)
        }
        return {
          id: proposalId, equipmentId: draft.equipment_id, title: draft.title, description: draft.description,
          priority: draft.priority, createdAt: Date.parse(draft.work_read_at),
          instrumentId: typeof draft.instrument_id === 'string' ? draft.instrument_id : undefined,
          opcuaNodeId: typeof draft.opcua_node_id === 'string' ? draft.opcua_node_id : undefined,
          backend: { expiresAt: draft.expires_at,
            decide: (approved: boolean, edits?: HostedDecision['edits']) => decide(approved, edits, false),
            reconcile: (approved: boolean, edits?: HostedDecision['edits']) => decide(approved, edits, true) },
        } satisfies WorkOrderProposal
      })
      const visualizations = presentation.visualizations.map(visualization)
      const committedEvents = presentation.execution_events.map(executionEvent)
      const mergedEvents = new Map(streamedEvents.map(event => [event.id, event]))
      for (const event of committedEvents) {
        mergedEvents.set(event.id, { ...mergedEvents.get(event.id), ...event })
      }
      const executionEvents = [...mergedEvents.values()]
      this.previousRunId = runId
      this.interruptedRunId = undefined
      return { runId, text: presentation.text, proposals, visualizations, executionEvents, receipt: reply }
    } catch (error) {
      // A failed turn must not erase the conversation. Only an unusable earlier run (the backend answers 404/409
      // for it) is dropped, so the next question does not keep failing on it.
      if (error instanceof HostedInvocationError && (error.status === 404 || error.status === 409)) this.previousRunId = undefined
      throw error
    } finally { this.running = false }
  }

  evidence(runId: string) {
    return this.invoke({ operation: 'evidence', run_id: runId })
  }

  async decide(runId: string, decision: HostedDecision, reconcile = false) {
    const matches = (reply: Record<string, unknown>) =>
      reply.run_id === runId && reply.proposal_id === decision.proposal_id
    try {
      const reply = await this.invoke({ operation: reconcile ? 'reconcile' : 'decide', run_id: runId, decision })
      if (!matches(reply)) throw new Error('Hosted decision returned a different run or approval-card identity.')
      return reply
    } catch (error) {
      if (error instanceof HostedInvocationError && error.detail && !matches(error.detail)) {
        throw new Error('Hosted decision failed without a matching approval receipt. No write outcome is certified.', { cause: error })
      }
      throw error
    }
  }
}
