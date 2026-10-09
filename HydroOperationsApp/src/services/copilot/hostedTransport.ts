import type { AgentVisualization } from '../assistantStream.ts'
import type { OrchestrationEvent, WorkOrderProposal } from './orchestration.ts'

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
  constructor(status: number, detail?: Record<string, unknown>) {
    super(`Hosted invocation failed (HTTP ${status}). No answer or write outcome is certified.`)
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
  if ((role !== 'supervisor' && role !== 'qa' && role !== 'rca' && role !== 'work-order' && role !== 'fabric-iq')
    || item.status !== 'completed' || typeof item.id !== 'string' || typeof item.label !== 'string'
    || typeof item.detail !== 'string' || typeof item.timestamp !== 'number' || !Number.isFinite(item.timestamp)
    || typeof item.agentName !== 'string' || !item.agentName || typeof item.responseId !== 'string' || !item.responseId) {
    throw new Error('Hosted specialist execution receipt is invalid.')
  }
  return { id: item.id, role, status: 'completed', label: item.label, detail: item.detail,
    timestamp: item.timestamp, agentName: item.agentName, responseId: item.responseId }
}

export class HostedTransport {
  private sessionId = crypto.randomUUID()
  private running = false
  private inFlight = 0
  private readonly endpoint: URL
  private readonly source: HostedSource
  private readonly credentials: () => Promise<SourceTokens>
  private readonly fetcher: typeof fetch
  private previousRunId: string | undefined

  constructor(
    projectEndpoint: string, invocationUrl: string, source: HostedSource,
    credentials: () => Promise<SourceTokens>, fetcher: typeof fetch = fetch,
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
      || source.generation !== 2 || !/^[a-f0-9]{64}$/.test(source.configuration_digest)) {
      throw new Error('Hosted transport requires an explicit same-project invocation URL and verified v2 source identity.')
    }
    this.endpoint = endpoint
    this.source = { ...source }
    this.credentials = credentials
    this.fetcher = fetcher
  }

  reset() {
    if (this.running || this.inFlight) throw new Error('Wait for the hosted request before resetting the conversation.')
    this.sessionId = crypto.randomUUID()
    this.previousRunId = undefined
  }

  private async invoke(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    this.inFlight++
    let tokens: SourceTokens | undefined
    try {
      tokens = await this.credentials()
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
        body: JSON.stringify({ ...body, tokens }), signal: AbortSignal.timeout(300_000),
      })
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

  async run(chat: HostedChat): Promise<HostedAnswer> {
    if (this.running) throw new Error('A hosted request is already running.')
    this.running = true
    try {
      const reply = await this.invoke({ operation: 'run', chat: { ...chat,
        ...(this.previousRunId ? { previous_run_id: this.previousRunId } : {}) } })
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
      const sessionId = this.sessionId
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
          if (sessionId !== this.sessionId) return Promise.reject(new Error('This approval belongs to a reset conversation. Request a fresh draft.'))
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
      const executionEvents = presentation.execution_events.map(executionEvent)
      this.previousRunId = runId
      return { runId, text: presentation.text, proposals, visualizations, executionEvents, receipt: reply }
    } catch (error) {
      this.previousRunId = undefined
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
