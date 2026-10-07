export type AgentRole = 'supervisor' | 'qa' | 'work-order' | 'rca' | 'fabric-iq'
export const AGENT_DISPLAY_NAMES: Record<AgentRole, string> = {
  supervisor: 'Chief', qa: 'Gauge', 'work-order': 'Fixer', rca: 'Sleuth', 'fabric-iq': 'Sparky',
}
export type OrchestrationStatus = 'queued' | 'running' | 'completed' | 'error' | 'approval'

export type AgentTraceEntry = {
  id: string
  timestamp: number
  source: 'foundry' | 'application'
  label: string
  responseId?: string
  callId?: string
  failed?: boolean
  activity?: 'tool-start' | 'tool-end' | 'delegation-return'
}

export type OrchestrationEvent = {
  id: string
  role: AgentRole
  status: OrchestrationStatus
  label: string
  detail: string
  timestamp: number
  agentName?: string
  responseId?: string
  parentId?: string
  parentCallId?: string
  responseIds?: string[]
  requestId?: string
  finishedAt?: number
  trace?: AgentTraceEntry[]
  proposalIds?: string[]
}

export type WorkOrderProposal = {
  id: string
  equipmentId: string
  instrumentId?: string
  opcuaNodeId?: string
  title: string
  description: string
  priority: 'Low' | 'Medium' | 'High' | 'Critical'
  createdAt: number
}

const MUTATION_INTENT = /\b(create|raise|submit|log|make|generate|prepare|propose)\b.{0,80}\b(work[\s-]*orders?|wos?|(?:inspection|maintenance|work)[ -]drafts?)\b|\b(work[\s-]*orders?|wos?)\b.{0,40}\b(create|raise|submit|log|make|generate|prepare|propose)\b|^\s*(?:please\s+)?(?:open|draft)\s+(?:(?:a|an|new)\s+)*(?:work[\s-]*orders?|wos?)\b|\bdraft\s+(?:a|an|new|the|these|those)\b.{0,40}\b(work[\s-]*orders?|wos?)\b/i
const positiveActionClauses = (question: string) => question.replace(
  /\b(?:do not|don't|never)\s+(?:independently\s+)?(?:create|raise|submit|log|make|generate|prepare|propose|draft|investigate|diagnose|perform|verify|check)\b(?:(?!\bbut\b)[^.!?;\n])*/gi, '')

export function isWorkOrderRequest(question: string): boolean {
  const positiveClauses = positiveActionClauses(question).replace(
    /\b(?:prepare|generate|make)\s+(?:(?:a|an|the|new)\s+)?(?:table|chart|report|summary|list|dashboard|comparison)\b/gi,
    match => match.replace(/^\w+/, 'show'))
  return MUTATION_INTENT.test(positiveClauses)
    || (/\b(?:prepare|propose)\b.{0,80}\bdrafts?\b/i.test(positiveClauses) && /\bwork[\s-]*orders?\b/i.test(positiveClauses))
}

export function missingRequestedSpecialists(question: string, completed: readonly AgentRole[]): AgentRole[] {
  const missing: AgentRole[] = []
  const positive = positiveActionClauses(question)
  if (/\b(?:investigate|diagnose|root[- ]cause analysis|perform (?:an? )?RCA)\b/i.test(positive) && !completed.includes('rca')) missing.push('rca')
  const drafting = isWorkOrderRequest(question)
  if (drafting && !completed.includes('work-order')) missing.push('work-order')
  const verification = /\b(?:independently (?:check|verify)|independent (?:check|verification)|verify\b.{0,80}\bagain)\b/i.test(positive)
  if (verification && (!completed.includes('qa') || (drafting
    && (!completed.includes('work-order') || completed.lastIndexOf('qa') < completed.lastIndexOf('work-order'))))) missing.push('qa')
  return missing
}

export function createOrchestrationEvent(
  role: AgentRole,
  status: OrchestrationStatus,
  detail: string,
  label = role === 'qa' ? 'Q&A Agent' : role === 'work-order' ? 'Work Order Agent' : role === 'rca' ? 'RCA Agent' : role === 'fabric-iq' ? 'Fabric IQ Agent' : 'Supervisor',
): OrchestrationEvent {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    status,
    label: `${AGENT_DISPLAY_NAMES[role]} - ${label}`,
    detail,
    timestamp: Date.now(),
  }
}

export function createWorkOrderProposal(input: {
  equipmentId?: string
  instrumentId?: string
  opcuaNodeId?: string
  title?: string
  description?: string
  priority?: string
}): WorkOrderProposal {
  const equipmentId = input.equipmentId?.trim()
  if (!equipmentId) throw new Error('equipment_id is required before a work order can be proposed.')
  const title = input.title?.trim()
  if (!title) throw new Error('title is required before a work order can be proposed.')
  if (title.length > 200 || (input.description?.length ?? 0) > 4000) throw new Error('Work-order title or description exceeds the allowed length.')
  const priority = input.priority?.trim()
  if (!priority || !['Low', 'Medium', 'High', 'Critical'].includes(priority)) {
    throw new Error('priority must be Low, Medium, High, or Critical.')
  }
  return {
    id: `wo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
    equipmentId,
    instrumentId: input.instrumentId?.trim() || undefined,
    opcuaNodeId: input.opcuaNodeId?.trim() || undefined,
    title,
    description: input.description?.trim() || `Operator-approved work requested from Hydro Operations chat for ${equipmentId}.`,
    priority: priority as WorkOrderProposal['priority'],
    createdAt: Date.now(),
  }
}
