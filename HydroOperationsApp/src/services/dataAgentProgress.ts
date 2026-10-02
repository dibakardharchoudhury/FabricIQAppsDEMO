import type { AgentStep } from './agentSteps'

const TOOL_PROGRESS = /^Run step (created|in progress|completed): tool_calls \(Tools: ([^)]+)\)$/

export function applyDataAgentProgress(steps: AgentStep[], message: string | undefined, now: number): boolean {
  const match = message?.match(TOOL_PROGRESS)
  if (!match) return false
  const [, phase, tool] = match
  const existing = steps.find(step => step.tool === tool)
  if (existing) {
    const status = phase === 'completed' ? 'done' : 'running'
    if (existing.status === status) return false
    existing.status = status
    existing.summary = status === 'done' ? 'completed' : 'running...'
    existing.elapsedMs = status === 'done' ? Math.max(0, now - existing.elapsedMs) : existing.elapsedMs
    return true
  }
  steps.push({
    tool,
    status: phase === 'completed' ? 'done' : 'running',
    detail: 'Fabric Data Agent internal tool',
    summary: phase === 'completed' ? 'completed' : 'running...',
    elapsedMs: phase === 'completed' ? 0 : now,
  })
  return true
}
