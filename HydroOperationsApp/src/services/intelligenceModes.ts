import type { CopilotMessage } from '../components/CopilotExperience'
import type { DirectAnswer } from './directFabric'
import type { FoundryAnswer } from './copilot/foundry'
import type { OrchestrationEvent } from './copilot/orchestration'

export const INTELLIGENCE_MODES = [
  { id: 'direct', label: 'Fabric IQ' },
  { id: 'multi', label: 'Multi Agent' },
  { id: 'battle', label: 'Battle of the Agents' },
] as const
export type IntelligenceMode = typeof INTELLIGENCE_MODES[number]['id']
export type IntelligenceLane = 'direct' | 'multi' | 'battleDirect' | 'battleMulti'
type Lane = { messages: CopilotMessage[]; busy: boolean }
type Snapshot = Record<IntelligenceLane, Lane>
export type ModeRequest = (question: string, context: {
  lane: IntelligenceLane; session: string; signal: AbortSignal
  progress: (text: string) => void; events: (events: OrchestrationEvent[]) => void
}) => Promise<DirectAnswer | FoundryAnswer>
export function routeIntelligenceRequest(direct: ModeRequest, multi: ModeRequest): ModeRequest {
  return (question, context) => (context.lane === 'direct' || context.lane === 'battleDirect' ? direct : multi)(question, context)
}

const empty = (): Snapshot => ({
  direct: { messages: [], busy: false }, multi: { messages: [], busy: false },
  battleDirect: { messages: [], busy: false }, battleMulti: { messages: [], busy: false },
})
const lanesFor = (mode: IntelligenceMode): IntelligenceLane[] =>
  mode === 'battle' ? ['battleDirect', 'battleMulti'] : [mode]

/** Separate in-memory conversations. Selecting a mode never submits a request. */
export class IntelligenceConversations {
  private snapshot = empty()
  private listeners = new Set<() => void>()
  private controllers = new Map<IntelligenceLane, AbortController>()
  private sessions = new Map<IntelligenceLane, string>()
  private revision = 0
  private identity?: string
  private readonly request: ModeRequest
  private readonly release: (session: string) => void
  constructor(request: ModeRequest, release: (session: string) => void = () => {}) {
    this.request = request
    this.release = release
  }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  getSnapshot = () => this.snapshot
  identityVersion = () => this.revision
  private emit() { for (const listener of this.listeners) listener() }
  private update(lane: IntelligenceLane, value: Lane) {
    this.snapshot = { ...this.snapshot, [lane]: value }
    this.emit()
  }
  setIdentity(key: string) {
    if (this.identity === key) return
    this.identity = key
    this.clear()
  }
  clear() {
    this.revision++
    for (const controller of this.controllers.values()) controller.abort()
    this.controllers.clear()
    for (const session of this.sessions.values()) this.release(session)
    this.sessions.clear()
    this.snapshot = empty()
    this.emit()
  }
  reset(mode: IntelligenceMode) {
    if (lanesFor(mode).some(lane => this.snapshot[lane].busy)) return
    for (const lane of lanesFor(mode)) {
      const session = this.sessions.get(lane)
      if (session) this.release(session)
      this.sessions.delete(lane)
      this.update(lane, { messages: [], busy: false })
    }
  }
  cancel(mode: IntelligenceMode) {
    for (const lane of lanesFor(mode)) this.controllers.get(lane)?.abort()
  }
  async send(mode: IntelligenceMode, question: string) {
    const lanes = lanesFor(mode)
    if (!question.trim() || lanes.some(lane => this.snapshot[lane].busy)) return
    await Promise.allSettled(lanes.map(lane => this.run(lane, question)))
  }
  private async run(lane: IntelligenceLane, question: string) {
    const revision = this.revision
    const controller = new AbortController()
    this.controllers.set(lane, controller)
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(lane === 'direct' || lane === 'battleDirect' ? 5 * 60_000 : 12 * 60_000)])
    const session = this.sessions.get(lane) ?? crypto.randomUUID()
    this.sessions.set(lane, session)
    const started = performance.now()
    const history = this.snapshot[lane].messages
    let message: CopilotMessage = { role: 'agent', text: '', backendOwned: true, readOnly: lane !== 'multi' }
    const paint = (busy: boolean) => {
      if (revision !== this.revision) return
      this.update(lane, { messages: [...history, { role: 'user', text: question }, message], busy })
    }
    paint(true)
    try {
      const answer = await this.request(question, {
        lane, session, signal,
        progress: text => { if (!signal.aborted) { message = { ...message, text }; paint(true) } },
        events: orchestrationEvents => { if (!signal.aborted) { message = { ...message, orchestrationEvents }; paint(true) } },
      })
      signal.throwIfAborted()
      message = { ...message, ...answer, role: 'agent', backendOwned: true,
        readOnly: lane !== 'multi', ...('proposals' in answer && lane !== 'multi' ? { proposals: undefined, workOrderReview: undefined } : {}),
        meta: { elapsedMs: performance.now() - started, tokens: answer.usage?.total } }
    } catch (error) {
      message = { role: 'agent', backendOwned: true, readOnly: lane !== 'multi', failed: true,
        text: signal.aborted ? 'Request cancelled or timed out locally. Remote execution may still complete; no answer or write outcome is certified.'
          : error instanceof Error ? error.message : 'The request failed. No fallback was used.',
        meta: { elapsedMs: performance.now() - started } }
    } finally {
      if (this.controllers.get(lane) === controller) this.controllers.delete(lane)
      paint(false)
    }
  }
}
