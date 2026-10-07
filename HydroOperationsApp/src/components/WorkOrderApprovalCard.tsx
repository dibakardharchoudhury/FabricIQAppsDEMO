import { useState } from 'react'
import type { WorkOrderProposal } from '../services/copilot/orchestration'
import { approveWorkOrder, workOrderApprovals } from '../services/copilot/workOrderApproval'
import type { ProposalEdits } from '../services/copilot/approvalStore'

export function WorkOrderApprovalCard({ proposal }: { proposal: WorkOrderProposal }) {
  const [draft, setDraft] = useState<ProposalEdits>(proposal)
  const [state, setState] = useState(workOrderApprovals.get(proposal.id)?.state ?? 'rejected')
  const [message, setMessage] = useState(workOrderApprovals.get(proposal.id) ? '' : 'This draft is no longer available. Check existing work before requesting a new draft.')
  const [error, setError] = useState('')
  const approve = async () => {
    setState('saving')
    setError('')
    try {
      const order = await approveWorkOrder(proposal.id, draft)
      setState('created')
      setMessage(`Created ${order.workOrderNumber} - ${order.status}`)
    } catch (cause) {
      setState(workOrderApprovals.get(proposal.id)?.state ?? 'rejected')
      setError(cause instanceof Error ? cause.message : 'Work-order approval failed.')
    }
  }
  const reject = () => {
    try { workOrderApprovals.reject(proposal.id); setState('rejected'); setMessage('Rejected. No work order was created.') }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Rejection failed.') }
  }
  return <section className="v2-work-order-approval" aria-label="Review work-order draft">
    <h3>Review work-order draft</h3>
    <p>{proposal.equipmentId}{proposal.opcuaNodeId ? ` - ${proposal.opcuaNodeId}` : ''}</p>
    <fieldset disabled={state !== 'pending'}>
      <label>Title<input value={draft.title} maxLength={200} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label>Description<textarea value={draft.description} maxLength={4000} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
      <label>Priority<select value={draft.priority} onChange={event => {
        const priority = event.target.value
        if (priority === 'Low' || priority === 'Medium' || priority === 'High' || priority === 'Critical') setDraft({ ...draft, priority })
      }}>{['Low', 'Medium', 'High', 'Critical'].map(priority => <option key={priority}>{priority}</option>)}</select></label>
      <p>Review existing work in the evidence above. Approving creates a Draft in the operational SQL database.</p>
      <button type="button" disabled={!draft.title.trim() || !draft.description.trim()} onClick={() => void approve()}>Yes, create work order</button>
      <button type="button" onClick={reject}>No, reject</button>
    </fieldset>
    {state === 'saving' && <p role="status">Validating and creating...</p>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </section>
}
