import { useRef, useState, useSyncExternalStore } from 'react'
import { Check, ClipboardCheck, ShieldCheck, X } from 'lucide-react'
import type { WorkOrderProposal } from '../services/copilot/orchestration'
import { approveWorkOrder, workOrderApprovals } from '../services/copilot/workOrderApproval'
import type { ProposalEdits } from '../services/copilot/approvalStore'
import { AgentElapsedTime } from './AgentElapsedTime'
import { HostedInvocationError } from '../services/copilot/hostedTransport'

export function WorkOrderApprovalCard({ proposal }: { proposal: WorkOrderProposal }) {
  return proposal.backend ? <HostedApprovalCard proposal={proposal} /> : <BrowserApprovalCard proposal={proposal} />
}

function HostedApprovalCard({ proposal }: { proposal: WorkOrderProposal }) {
  const [draft, setDraft] = useState<ProposalEdits>(proposal)
  const [state, setState] = useState<'pending' | 'saving' | 'created' | 'rejected' | 'blocked' | 'disabled' | 'uncertain'>('pending')
  const [message, setMessage] = useState('')
  const submitted = useRef<{ approved: boolean; edits?: ProposalEdits } | undefined>(undefined)
  const decide = async (approved: boolean, reconcile = false) => {
    if ((reconcile ? state !== 'uncertain' : state !== 'pending') || !proposal.backend) return
    if (!reconcile) submitted.current = { approved, ...(approved ? { edits: { ...draft } } : {}) }
    const decision = submitted.current
    if (!decision) return
    setState('saving')
    setMessage('')
    try {
      const result = await (reconcile ? proposal.backend.reconcile : proposal.backend.decide)(decision.approved, decision.edits)
      if (!decision.approved && result.status === 'rejected' && result.production_write_executed === false) {
        setState('rejected')
        setMessage('Rejected by the backend. No work order was created.')
      } else if (decision.approved && result.status === 'created' && result.production_write_executed === true
        && result.record && typeof result.record === 'object' && 'workOrderNumber' in result.record
        && typeof result.record.workOrderNumber === 'string') {
        setState('created')
        setMessage(`Created ${result.record.workOrderNumber} after verified backend readback.`)
        window.dispatchEvent(new Event('hydro:work-orders-changed'))
      } else {
        throw new Error('The backend returned no verified decision outcome.')
      }
    } catch (cause) {
      const detail = cause instanceof HostedInvocationError ? cause.detail : undefined
      if (detail?.production_write_executed === false && (detail.status === 'blocked' || detail.status === 'disabled')) {
        setState(detail.status)
        setMessage(typeof detail.message === 'string' ? detail.message : 'No SQL write was attempted. Request fresh evidence before proceeding.')
      } else {
        setState('uncertain')
        setMessage(cause instanceof Error ? cause.message : 'The decision outcome could not be confirmed.')
      }
    }
  }
  return <section className="v2-work-order-approval" aria-label="Review backend work-order draft">
    <header className="wo-review-heading"><ClipboardCheck size={24} /><div><h3>Review work-order draft</h3><small>Backend-grounded human approval</small></div><span className={`wo-review-state ${state}`}>{state}</span></header>
    <div className="wo-review-identity"><span>Equipment<strong>{proposal.equipmentId}</strong></span>{proposal.opcuaNodeId && <span>Signal<strong>{proposal.opcuaNodeId}</strong></span>}<span>Expires<strong>{proposal.backend?.expiresAt}</strong></span></div>
    <fieldset disabled={state !== 'pending'}>
      <label>Title<input value={draft.title} maxLength={200} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label>Description<textarea value={draft.description} maxLength={4000} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
      <label>Priority<select value={draft.priority} onChange={event => {
        const priority = event.target.value
        if (priority === 'Low' || priority === 'Medium' || priority === 'High' || priority === 'Critical') setDraft({ ...draft, priority })
      }}>{['Low', 'Medium', 'High', 'Critical'].map(priority => <option key={priority}>{priority}</option>)}</select></label>
      <p className="wo-review-boundary"><ShieldCheck size={18} />Only your decision is sent. Identity, open-work checks and SQL writes run in the backend.</p>
      <div className="wo-review-actions"><button type="button" className="wo-approve" disabled={!draft.title.trim() || !draft.description.trim()} onClick={() => void decide(true)}><Check size={15} />Yes, create work order</button>
        <button type="button" onClick={() => void decide(false)}><X size={15} />No, reject</button></div>
    </fieldset>
    {state === 'saving' && <p role="status">Awaiting the authenticated backend decision. Do not submit again.</p>}
    {message && <p role={state === 'uncertain' ? 'alert' : 'status'}>{message}</p>}
    {state === 'uncertain' && <div><p>Do not request another write. Reconciliation checks the original submission and cannot create a new work order.</p>
      <button type="button" onClick={() => void decide(submitted.current?.approved ?? false, true)}>Check original submission</button></div>}
  </section>
}

function BrowserApprovalCard({ proposal }: { proposal: WorkOrderProposal }) {
  const [draft, setDraft] = useState<ProposalEdits>(proposal)
  const state = useSyncExternalStore(workOrderApprovals.subscribe, () => workOrderApprovals.get(proposal.id)?.state ?? 'rejected')
  const [message, setMessage] = useState(workOrderApprovals.get(proposal.id) ? '' : 'This draft is no longer available. Check existing work before requesting a new draft.')
  const [error, setError] = useState('')
  const entry = workOrderApprovals.get(proposal.id)
  const approve = async () => {
    setError('')
    try {
      await approveWorkOrder(proposal.id, draft)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Work-order approval failed.')
    }
  }
  const reject = () => {
    try { workOrderApprovals.reject(proposal.id); setMessage('Rejected. No work order was created.') }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Rejection failed.') }
  }
  return <section className="v2-work-order-approval" aria-label="Review work-order draft">
    <header className="wo-review-heading"><ClipboardCheck size={24} /><div><h3>Review work-order draft</h3><small>Fixer - Work Orders</small></div><span className={`wo-review-state ${state}`}>{state === 'pending' ? 'Awaiting your approval' : state}</span></header>
    <div className="wo-review-identity"><span>Equipment<strong>{proposal.equipmentId}</strong></span>{proposal.opcuaNodeId && <span>Signal<strong>{proposal.opcuaNodeId}</strong></span>}<span>Proposed SQL status<strong>Draft</strong></span></div>
    <fieldset disabled={state !== 'pending'}>
      <label>Title<input aria-label="Work-order title" value={draft.title} maxLength={200} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label>Description<textarea aria-label="Work-order description" value={draft.description} maxLength={4000} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
      <label>Priority<select value={draft.priority} onChange={event => {
        const priority = event.target.value
        if (priority === 'Low' || priority === 'Medium' || priority === 'High' || priority === 'Critical') setDraft({ ...draft, priority })
      }}>{['Low', 'Medium', 'High', 'Critical'].map(priority => <option key={priority}>{priority}</option>)}</select></label>
      <p className="wo-review-boundary"><ShieldCheck size={18} />Check existing work in Supporting findings and sources. Only your approval saves this Draft.</p>
      <div className="wo-review-actions"><button type="button" className="wo-approve" disabled={!draft.title.trim() || !draft.description.trim()} onClick={() => void approve()}><Check size={15} />Yes, create work order</button>
      <button type="button" onClick={reject}><X size={15} />No, reject</button></div>
    </fieldset>
    {state === 'validating' && <p role="status">Checking sign-in, asset identity and existing work. No SQL write yet. <AgentElapsedTime startedAt={entry?.startedAt} running /></p>}
    {state === 'saving' && <p role="status">Creating the approved SQL draft. Do not submit again. <AgentElapsedTime startedAt={entry?.startedAt} running /></p>}
    {state === 'created' && entry?.result && <p role="status">Created {entry.result.workOrderNumber} - {entry.result.status}</p>}
    {message && <p role="status">{message}</p>}
    {state !== 'created' && (error || entry?.error) && <p role="alert">{error || entry?.error}</p>}
  </section>
}
