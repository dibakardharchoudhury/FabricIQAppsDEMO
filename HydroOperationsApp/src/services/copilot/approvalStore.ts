import { createWorkOrderProposal, type WorkOrderProposal } from './orchestration.ts'

export type ProposalEdits = Pick<WorkOrderProposal, 'title' | 'description' | 'priority'>
export type ApprovalState = 'pending' | 'saving' | 'created' | 'rejected' | 'uncertain'

export function createApprovalStore<T>() {
  const entries = new Map<string, { proposal: WorkOrderProposal; state: ApprovalState; result?: T }>()
  return {
    stage(proposal: WorkOrderProposal) {
      if (entries.has(proposal.id)) throw new Error('This proposal has already been staged.')
      entries.set(proposal.id, { proposal: { ...proposal }, state: 'pending' })
    },
    clear() { entries.clear() },
    get(id: string) { return entries.get(id) },
    reject(id: string) {
      const entry = entries.get(id)
      if (!entry || entry.state !== 'pending') throw new Error('This proposal is no longer awaiting approval.')
      entry.state = 'rejected'
    },
    async approve(id: string, edits: ProposalEdits, validate: (proposal: WorkOrderProposal) => Promise<void>, write: (proposal: WorkOrderProposal) => Promise<T>) {
      const entry = entries.get(id)
      if (!entry) throw new Error('This proposal expired. Ask for a new draft.')
      if (entry.state !== 'pending') throw new Error(`This proposal is ${entry.state}; it cannot be submitted again.`)
      if (Date.now() - entry.proposal.createdAt > 30 * 60_000) throw new Error('This draft is over 30 minutes old. Request a fresh evidence check.')
      const checked = createWorkOrderProposal({ ...entry.proposal, ...edits })
      const proposal = { ...checked, id, createdAt: entry.proposal.createdAt }
      if (!proposal.description.trim()) throw new Error('A description is required.')
      entry.state = 'saving'
      try { await validate(proposal) }
      catch (error) { entry.state = 'pending'; throw error }
      try {
        entry.result = await write(proposal)
        entry.state = 'created'
        return entry.result
      } catch (error) {
        // A failed response does not prove the database rolled back the write.
        entry.state = 'uncertain'
        throw new Error('Creation could not be confirmed. Check the work-order list before requesting another draft; this submission will not be retried.', { cause: error })
      }
    },
  }
}
