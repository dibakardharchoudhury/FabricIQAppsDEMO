import { createWorkOrderProposal, type WorkOrderProposal } from './orchestration.ts'

export type ProposalEdits = Pick<WorkOrderProposal, 'title' | 'description' | 'priority'>
export type ApprovalState = 'pending' | 'validating' | 'saving' | 'created' | 'rejected' | 'withdrawn' | 'uncertain'

export const APPROVAL_PHASE_TIMEOUT_MS = 90_000

async function bounded<T>(operation: Promise<T>, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([operation, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), APPROVAL_PHASE_TIMEOUT_MS)
    })])
  } finally { clearTimeout(timer) }
}

export function createApprovalStore<T>() {
  const entries = new Map<string, { proposal: WorkOrderProposal; state: ApprovalState; result?: T; startedAt?: number; error?: string }>()
  const listeners = new Set<() => void>()
  const notify = () => listeners.forEach(listener => listener())
  return {
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    stage(proposal: WorkOrderProposal) {
      if (entries.has(proposal.id)) throw new Error('This proposal has already been staged.')
      entries.set(proposal.id, { proposal: { ...proposal }, state: 'pending' })
      notify()
    },
    clear() { entries.clear(); notify() },
    get(id: string) { return entries.get(id) },
    reject(id: string) {
      const entry = entries.get(id)
      if (!entry || entry.state !== 'pending') throw new Error('This proposal is no longer awaiting approval.')
      entry.state = 'rejected'
      notify()
    },
    withdraw(id: string) {
      const entry = entries.get(id)
      if (!entry || entry.state !== 'pending') throw new Error('Only a pending proposal can be withdrawn after an incomplete workflow.')
      entry.state = 'withdrawn'
      notify()
    },
    async approve(id: string, edits: ProposalEdits, validate: (proposal: WorkOrderProposal) => Promise<void>, write: (proposal: WorkOrderProposal) => Promise<T>) {
      const entry = entries.get(id)
      if (!entry) throw new Error('This proposal expired. Ask for a new draft.')
      if (entry.state !== 'pending') throw new Error(`This proposal is ${entry.state}; it cannot be submitted again.`)
      if (Date.now() - entry.proposal.createdAt > 30 * 60_000) throw new Error('This draft is over 30 minutes old. Request a fresh evidence check.')
      const checked = createWorkOrderProposal({ ...entry.proposal, ...edits })
      const proposal = { ...checked, id, createdAt: entry.proposal.createdAt }
      if (!proposal.description.trim()) throw new Error('A description is required.')
      entry.state = 'validating'
      entry.startedAt = Date.now()
      entry.error = undefined
      notify()
      try {
        await bounded(validate(proposal), 'Validation did not finish within 90 seconds. No SQL write was attempted. Check source connectivity before trying this review again.')
      } catch (error) {
        entry.state = 'pending'
        entry.error = error instanceof Error ? error.message : 'Work-order validation failed. No SQL write was attempted.'
        notify()
        throw error
      }
      entry.state = 'saving'
      entry.startedAt = Date.now()
      notify()
      let confirmed: { result: T } | undefined
      try {
        const writing = write(proposal).then(result => {
          confirmed = { result }
          entry.result = result
          entry.state = 'created'
          entry.error = undefined
          notify()
          return result
        })
        return await bounded(writing, 'The SQL write did not return within 90 seconds.')
      } catch (error) {
        if (confirmed) return confirmed.result
        // A failed response does not prove the database rolled back the write.
        entry.state = 'uncertain'
        entry.error = 'Creation could not be confirmed. Check the work-order list before requesting another draft; this submission will not be retried.'
        notify()
        throw new Error(entry.error, { cause: error })
      }
    },
  }
}
