import { useState, useSyncExternalStore } from 'react'
import { Activity, Check, ClipboardCheck, ExternalLink, GitBranch, Search, Telescope, Workflow } from 'lucide-react'
import type { AgentRole, OrchestrationEvent, WorkOrderProposal } from '../services/copilot/orchestration'
import { AGENT_DISPLAY_NAMES } from '../services/copilot/orchestration'
import { workOrderApprovals } from '../services/copilot/workOrderApproval'
import { applicationInsightsLink, automaticCrewRole, crewCommunications, crewHandoffs, executionStatus, responseTraceQuery } from '../services/copilot/agentTrace'
import './AgentCrewTrace.css'
import { CopilotHelper } from './CopilotHelper'

const CREW = [
  { role: 'supervisor', job: 'Supervisor', quip: 'One clipboard. One plan.', icon: GitBranch },
  { role: 'qa', job: 'Q&A', quip: 'Facts, not vibes.', icon: Telescope },
  { role: 'work-order', job: 'Work orders', quip: 'You approve. Then we do.', icon: ClipboardCheck },
  { role: 'rca', job: 'Root cause', quip: 'Suspicious of coincidences.', icon: Search },
  { role: 'fabric-iq', job: 'Fabric IQ', quip: 'Right plug. Right source.', icon: Workflow },
] as const

const STATUS_LABELS = {
  idle: 'Standing by', queued: 'Queued', running: 'On the job',
  completed: 'Finished', approval: 'Your call', error: 'Needs attention',
}

function CommunicationPacket({ path, timestamp, reverse = false, failed = false }: { path: string; timestamp: number; reverse?: boolean; failed?: boolean }) {
  const [mountedAt] = useState(Date.now)
  const age = Math.max(0, mountedAt - timestamp)
  if (age >= 2400) return null
  return <circle r="5" className={`crew-packet${failed ? ' failed' : ''}${reverse ? ' returning' : ''}`}
    style={{ offsetPath: `path("${path}")`, animationDelay: `${-age}ms` }} />
}

export function AgentCrewTrace({ events, proposals = [], currentEventIds, pending = false }: { events?: OrchestrationEvent[]; proposals?: WorkOrderProposal[]; currentEventIds?: string[]; pending?: boolean }) {
  const [selected, setSelected] = useState<AgentRole>()
  const [paused, setPaused] = useState(false)
  const approvalSnapshot = useSyncExternalStore(workOrderApprovals.subscribe,
    () => proposals.map(proposal => workOrderApprovals.get(proposal.id)?.state ?? 'expired').join(','), () => '')
  const approvals = approvalSnapshot ? approvalSnapshot.split(',') : []
  const reviewStatus: OrchestrationEvent['status'] | undefined = !approvals.length ? undefined : approvals.some(state => state === 'uncertain' || state === 'expired') ? 'error'
    : approvals.some(state => state === 'saving') ? 'running'
    : approvals.some(state => state === 'pending') ? 'approval' : 'completed'
  if (!events?.length) return null
  const displayedEvents = events.map<OrchestrationEvent>(event => {
    if (event.status !== 'approval') return event
    const states = event.proposalIds?.map(id => workOrderApprovals.get(id)?.state ?? 'expired')
    if (!states?.length) return reviewStatus ? { ...event, status: reviewStatus } : event
    const status = states.some(state => state === 'uncertain' || state === 'expired') ? 'error'
      : states.some(state => state === 'saving') ? 'running'
      : states.some(state => state === 'pending') ? 'approval' : 'completed'
    return { ...event, status }
  })
  const currentEvents = currentEventIds ? displayedEvents.filter(event => currentEventIds.includes(event.id)) : displayedEvents
  const status = pending && !currentEvents.length ? 'queued' : executionStatus(currentEvents)
  const focused = selected ?? automaticCrewRole(currentEvents) ?? events[0].role
  const invoked = new Set(events.map(event => event.role)).size
  const focusedEvents = displayedEvents.filter(event => event.role === focused)
  const trace = focusedEvents.flatMap(event => event.trace ?? []).sort((a, b) => a.timestamp - b.timestamp)
  const start = Math.min(...events.map(event => event.timestamp))
  const latest = focusedEvents[focusedEvents.length - 1]
  const member = CREW.find(member => member.role === focused)
  const communications = crewCommunications(events)
  const currentCommunications = crewCommunications(currentEvents)
  const handoffs = crewHandoffs(events)
  const recentActivity = events.flatMap(event => (event.trace ?? []).map(entry => ({ ...entry, role: event.role })))
    .sort((a, b) => a.timestamp - b.timestamp).slice(-3)
  const insightsLink = applicationInsightsLink(import.meta.env.VITE_RAYFIN_FOUNDRY_APP_INSIGHTS_RESOURCE_ID)
  const traceQuery = responseTraceQuery(focusedEvents.flatMap(event => event.responseIds ?? (event.responseId ? [event.responseId] : [])))
  return <section className={`agent-crew status-${status}${paused ? ' motion-paused' : ''}`} aria-label="Hydro agent crew execution">
    <header className="agent-crew-heading">
      <Activity size={14} />
      <strong>Agent crew</strong>
      <span className="crew-run-status" role="status">{STATUS_LABELS[status]}</span>
    </header>
    <div className="crew-stage">
      <svg className="crew-wires" viewBox="0 0 800 56" preserveAspectRatio="none" aria-hidden="true">
        {communications.map(flow => {
          const index = CREW.findIndex(member => member.role === flow.role) - 1
          const x = 100 + index * 200
          const path = `M400 0 C400 28 ${x} 28 ${x} 56`
          return <g key={flow.id} className={`crew-wire${currentEventIds && !currentEventIds.includes(flow.id) ? ' historical' : ''}${flow.waiting ? ' waiting' : ''}${flow.failed ? ' failed' : ''}`}>
            <path d={path} />
            <CommunicationPacket path={path} timestamp={flow.sentAt} />
            {flow.receivedAt !== undefined && <CommunicationPacket key={`${flow.id}:return`} path={path} timestamp={flow.receivedAt} reverse failed={flow.failed} />}
          </g>
        })}
      </svg>
      <span className="crew-wire-label">{currentCommunications.some(flow => flow.waiting) ? 'Task dispatched · awaiting specialist' : currentCommunications.some(flow => flow.failed) ? 'Specialist failure returned' : currentCommunications.length && currentCommunications.every(flow => flow.receivedAt !== undefined) ? 'Results returned through Supervisor' : status === 'running' ? 'Supervisor coordinating' : 'See execution receipts below'}</span>
      {CREW.map(member => {
        const runs = currentEvents.filter(event => event.role === member.role)
        const event = runs[runs.length - 1]
        const state = event?.status ?? 'idle'
        const Icon = member.icon
        const awaiting = member.role === 'supervisor' && currentCommunications.some(flow => flow.waiting)
        return <button type="button" key={member.role} className={`crew-station role-${member.role} state-${state}${awaiting ? ' awaiting-specialist' : ''}${focused === member.role ? ' selected' : ''}`}
          aria-pressed={focused === member.role} aria-label={`${AGENT_DISPLAY_NAMES[member.role]} - ${member.job}: ${STATUS_LABELS[state]}. Show execution details.`}
          title={`${AGENT_DISPLAY_NAMES[member.role]} - ${member.job}: ${member.quip}`}
          onClick={() => setSelected(member.role)}>
          <span className="crew-helper-slot"><span className="crew-work-ring" /><CopilotHelper /><span className="crew-role-badge"><Icon size={12} /></span></span>
          <strong className="crew-name">{AGENT_DISPLAY_NAMES[member.role]}</strong>
          <span className="crew-job">{member.job}{state === 'completed' && <Check size={11} />}</span>
          <span className="crew-station-status">{awaiting ? 'Coordinating' : STATUS_LABELS[state]}</span>
        </button>
      })}
    </div>
    {handoffs.length > 0 && <ol className="crew-route" aria-label="Recorded handoff sequence">
      {handoffs.map((handoff, index) => <li key={handoff.id} className={handoff.failed ? 'failed' : ''}>
        <span>{index + 1}</span>{handoff.from} <span aria-hidden="true">&rarr;</span> {handoff.to}{handoff.failed ? ' (failed)' : ''}
      </li>)}
    </ol>}
    {recentActivity.length > 0 && <ol className="crew-live-feed" aria-label="Recent execution activity">
      {recentActivity.map(entry => <li key={entry.id} className={entry.failed ? 'failed' : ''}>
        <span className={`crew-feed-icon${entry.activity === 'tool-start' ? ' tool-start' : ''}`} aria-hidden="true">{entry.failed ? '!' : entry.activity === 'delegation-return' ? '\u2190' : '\u2192'}</span>
        <span><strong>{AGENT_DISPLAY_NAMES[entry.role]} - {CREW.find(member => member.role === entry.role)?.job}</strong> {entry.label}<small>{entry.source === 'foundry' ? 'Foundry event' : 'Browser coordination / tool'}</small></span>
        <time>+{((entry.timestamp - start) / 1000).toFixed(1)}s</time>
      </li>)}
    </ol>}
    <div className="crew-activity" aria-live="polite">
      <span className="crew-activity-dot" aria-hidden="true" />
      <span><strong>{member?.job}</strong> · {latest?.detail ?? 'Standing by; not invoked for this request.'}</span>
    </div>
    <div className="crew-controls">
      <span>{invoked} agents invoked · packets = recorded handoffs</span>
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
