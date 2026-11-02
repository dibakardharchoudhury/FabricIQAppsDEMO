import { useId, useState } from 'react'
import type { WorkOrderProposal } from '../services/copilot/orchestration'
import { WorkOrderApprovalCard } from './WorkOrderApprovalCard'

export function WorkOrderDraftPager({ proposals }: { proposals: WorkOrderProposal[] }) {
  const headingId = useId()
  const [selectedId, setSelectedId] = useState(proposals[0]?.id)
  const activeIndex = Math.max(0, proposals.findIndex(proposal => proposal.id === selectedId))
  if (!proposals.length) return null

  return <section className="v2-work-order-drafts" aria-labelledby={headingId}>
    <h3 id={headingId} aria-live="polite">Work-order draft {activeIndex + 1} of {proposals.length}</h3>
    {proposals.map((proposal, index) =>
      // Keep edits and in-flight approval/reconciliation state when changing pages.
      <div key={proposal.id} hidden={index !== activeIndex}>
        <WorkOrderApprovalCard proposal={proposal} />
      </div>)}
    {proposals.length > 1 && <nav className="v2-work-order-pages" aria-label="Work-order draft pages">
      <button type="button" disabled={activeIndex === 0}
        onClick={() => setSelectedId(proposals[activeIndex - 1].id)}>Previous draft</button>
      {proposals.map((proposal, index) => <button type="button" key={proposal.id}
        aria-label={`Draft ${index + 1} of ${proposals.length}`}
        aria-current={index === activeIndex ? 'page' : undefined}
        onClick={() => setSelectedId(proposal.id)}>{index + 1}</button>)}
      <button type="button" disabled={activeIndex === proposals.length - 1}
        onClick={() => setSelectedId(proposals[activeIndex + 1].id)}>Next draft</button>
    </nav>}
  </section>
}
