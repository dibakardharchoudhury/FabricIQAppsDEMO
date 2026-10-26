export type AgentStepStatus = 'running' | 'done' | 'error'

export type AgentStep = {
  tool: string
  status: AgentStepStatus
  detail: string
  summary: string
  query?: string
  args?: string
  result?: string
  elapsedMs: number
  startedAt?: number
  timingSource?: 'notification'
  error?: string
}
