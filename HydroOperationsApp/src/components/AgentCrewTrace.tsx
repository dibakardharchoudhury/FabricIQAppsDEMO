import { useState, useSyncExternalStore } from 'react'
import { Activity, Check, ClipboardCheck, ExternalLink, GitBranch, Search, Telescope, Workflow } from 'lucide-react'
import type { AgentRole, OrchestrationEvent, WorkOrderProposal } from '../services/copilot/orchestration'
import { workOrderApprovals } from '../services/copilot/workOrderApproval'
import { applicationInsightsLink, executionStatus, responseTraceQuery } from '../services/copilot/agentTrace'
import './AgentCrewTrace.css'

const CREW = [
  { role: 'supervisor', name: 'Chief', job: 'Supervisor', quip: 'One clipboard. One plan.', icon: GitBranch },
  { role: 'qa', name: 'Scout', job: 'Q&A', quip: 'Facts, not vibes.', icon: Telescope },
  { role: 'rca', name: 'Sleuth', job: 'Root cause', quip: 'Suspicious of coincidences.', icon: Search },
  { role: 'work-order', name: 'Fixer', job: 'Work orders', quip: 'You approve. Then we do.', icon: ClipboardCheck },
  { role: 'fabric-iq', name: 'Sparky', job: 'Fabric IQ', quip: 'Right plug. Right source.', icon: Workflow },
] as const

const STATUS_LABELS = {
  idle: 'Standing by', queued: 'Queued', running: 'On the job',
  completed: 'Finished', approval: 'Your call', error: 'Needs attention',
}

function CrewBot({ role, failed }: { role: AgentRole; failed: boolean }) {
  return <svg className={`crew-bot bot-${role}`} viewBox="0 0 100 114" aria-hidden="true">
    <ellipse className="crew-bot-shadow" cx="50" cy="105" rx="27" ry="5" />
    <g className="crew-bot-body">
      <path className="crew-bot-limb" d="M32 85v14h-8m44-14v14h8M23 66l-9 12m63-12 9 12" />
      <rect className="crew-bot-suit" x="24" y="52" width="52" height="37" rx="12" />
      <rect className="crew-bot-face" x="19" y="21" width="62" height="43" rx="18" />
      <path className="crew-bot-limb" d="M50 21v-9" /><circle className="crew-bot-lamp" cx="50" cy="9" r="5" />
      <rect className="crew-bot-visor" x="27" y="32" width="46" height="20" rx="9" />
      <g className="crew-bot-eyes">
        {failed ? <path d="m36 37 6 7m0-7-6 7m22-7 6 7m0-7-6 7" /> : <>
          <circle cx="39" cy="41" r="4" /><circle cx="61" cy="41" r="4" />
        </>}
      </g>
      <path className="crew-bot-smile" d={failed ? 'M44 58q6-5 12 0' : 'M43 55q7 7 14 0'} />
      {role === 'supervisor' && <path className="crew-bot-badge" d="m50 68 3 5 6 1-4 4 1 6-6-3-6 3 1-6-4-4 6-1z" />}
      {role === 'qa' && <path className="crew-bot-badge" d="M37 71h26v12H37zM50 71v12" />}
      {role === 'rca' && <g className="crew-bot-prop"><circle cx="74" cy="73" r="10" /><path d="m81 81 9 10" /></g>}
      {role === 'work-order' && <g className="crew-bot-clipboard"><rect x="60" y="65" width="22" height="28" rx="3" /><path d="m65 79 4 4 8-10" /></g>}
      {role === 'fabric-iq' && <path className="crew-bot-badge" d="m51 67-9 12h8l-1 8 9-13h-8z" />}
    </g>
  </svg>
}

export function AgentCrewTrace({ events, proposals = [] }: { events?: OrchestrationEvent[]; proposals?: WorkOrderProposal[] }) {
  const [selected, setSelected] = useState<AgentRole>()
  const [paused, setPaused] = useState(false)
  const approvalSnapshot = useSyncExternalStore(workOrderApprovals.subscribe,
    () => proposals.map(proposal => workOrderApprovals.get(proposal.id)?.state ?? 'expired').join(','), () => '')
  const approvals = approvalSnapshot ? approvalSnapshot.split(',') : []
  const reviewStatus: OrchestrationEvent['status'] | undefined = !approvals.length ? undefined : approvals.some(state => state === 'uncertain' || state === 'expired') ? 'error'
    : approvals.some(state => state === 'saving') ? 'running'
    : approvals.some(state => state === 'pending') ? 'approval' : 'completed'
  if (!events?.length) return null
  const displayedEvents = events.map<OrchestrationEvent>(event => event.status === 'approval' && reviewStatus ? { ...event, status: reviewStatus } : event)
  const status = executionStatus(displayedEvents)
  const active = [...events].reverse().find(event => event.status === 'running')?.role
  const focused = selected ?? active ?? events.find(event => event.status === 'approval')?.role ?? events[0].role
  const invoked = new Set(events.map(event => event.role)).size
  const focusedEvents = events.filter(event => event.role === focused)
  const trace = focusedEvents.flatMap(event => event.trace ?? []).sort((a, b) => a.timestamp - b.timestamp)
  const start = Math.min(...events.map(event => event.timestamp))
  const latest = focusedEvents[focusedEvents.length - 1]
  const insightsLink = applicationInsightsLink(import.meta.env.VITE_RAYFIN_FOUNDRY_APP_INSIGHTS_RESOURCE_ID)
  const traceQuery = responseTraceQuery(focusedEvents.flatMap(event => event.responseIds ?? (event.responseId ? [event.responseId] : [])))
  return <section className={`agent-crew status-${status}${paused ? ' motion-paused' : ''}`} aria-label="Hydro agent crew execution">
    <header className="agent-crew-heading">
      <span className="crew-heading-mark"><Activity size={19} /></span>
      <div><strong>Small crew. Serious work.</strong><span>Hydro Operations mission control</span></div>
      <span className="crew-run-status" role="status">{STATUS_LABELS[status]}</span>
    </header>
    <div className="crew-stage">
      {CREW.map(member => {
        const runs = events.filter(event => event.role === member.role)
        const event = runs[runs.length - 1]
        const state = event?.status === 'approval' && reviewStatus ? reviewStatus : event?.status ?? 'idle'
        const Icon = member.icon
        return <button type="button" key={member.role} className={`crew-station state-${state}${focused === member.role ? ' selected' : ''}`}
          aria-pressed={focused === member.role} aria-label={`${member.job}: ${STATUS_LABELS[state]}. Show execution details.`}
          onClick={() => setSelected(member.role)}>
          <span className="crew-speech">{state === 'error' ? 'No pretending that worked.' : state === 'approval' ? 'Clipboard needs your autograph.' : member.quip}</span>
          <CrewBot role={member.role} failed={state === 'error'} />
          <span className="crew-name">{member.name}{state === 'completed' && <Check size={12} />}</span>
          <span className="crew-job"><Icon size={12} />{member.job}</span>
          <span className="crew-station-status">{STATUS_LABELS[state]}</span>
        </button>
      })}
    </div>
    <div className="crew-activity" aria-live="polite">
      <span className="crew-activity-dot" aria-hidden="true" />
      <span>{latest?.detail ?? 'Not invoked for this request. No work or progress is inferred.'}</span>
    </div>
    <div className="crew-controls">
      <span>{invoked} of {CREW.length} agents invoked</span>
      <button type="button" onClick={() => setPaused(value => !value)} aria-pressed={paused}>{paused ? 'Resume animations' : 'Pause animations'}</button>
    </div>
    {approvals.length > 0 && <p className="crew-review-status" role="status">Human review (application): {approvals.filter(state => state === 'created').length} created, {approvals.filter(state => state === 'rejected').length} rejected, {approvals.filter(state => state === 'pending').length} awaiting decision.{reviewStatus === 'error' && ' A draft expired or a SQL write outcome is uncertain. Check existing work before retrying.'}{approvals.includes('saving') && ' Validating or writing the approved draft.'}</p>}
    <details className="crew-diagnostics">
      <summary>Execution receipts <span>{trace.length} events for {CREW.find(member => member.role === focused)?.job}</span></summary>
      <p>Foundry stream events and browser-executed tools are labeled separately. These are execution records, not private reasoning or an Application Insights span export.</p>
      {insightsLink && <a className="crew-insights-link" href={insightsLink} target="_blank" rel="noreferrer">Open linked Application Insights <ExternalLink size={12} /></a>}
      {traceQuery && <details className="crew-query"><summary>Find these responses in Foundry telemetry</summary><p>Use this query in the linked Application Insights Logs. No matching rows means trace ingestion is unverified, not that execution succeeded or failed.</p><pre><code>{traceQuery}</code></pre></details>}
      {focusedEvents.map(event => <div className="crew-identities" key={event.id}>
        <strong>{event.agentName ?? event.label}</strong>
        {event.parentId && <span>Delegated by {events.find(parent => parent.id === event.parentId)?.label ?? 'Supervisor'}</span>}
        {event.finishedAt && <span>{((event.finishedAt - event.timestamp) / 1000).toFixed(1)}s invocation elapsed</span>}
        {(event.responseIds ?? (event.responseId ? [event.responseId] : [])).map(id => <span key={id}>Response <code>{id}</code></span>)}
        {event.requestId && <span>Service request <code>{event.requestId}</code></span>}
      </div>)}
      {trace.length > 0 ? <ol className="crew-timeline">{trace.map(entry => <li className={entry.failed ? 'failed' : ''} key={entry.id}>
        <time>+{((entry.timestamp - start) / 1000).toFixed(1)}s</time>
        <span><b>{entry.source === 'foundry' ? 'Foundry' : 'App tool / coordination'}</b>{entry.label}{entry.callId && <code>{entry.callId}</code>}</span>
      </li>)}</ol> : <p>No execution events recorded for this agent.</p>}
    </details>
  </section>
}
