export type AgentRole = 'supervisor' | 'qa' | 'work-order' | 'rca' | 'fabric-iq'
export type OrchestrationStatus = 'queued' | 'running' | 'completed' | 'error' | 'approval'

export type AgentTraceEntry = {
  id: string
  timestamp: number
  source: 'foundry' | 'application'
  label: string
  responseId?: string
  callId?: string
  failed?: boolean
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
  responseIds?: string[]
  requestId?: string
  finishedAt?: number
  trace?: AgentTraceEntry[]
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

const MUTATION_INTENT = /\b(create|raise|submit|log|make|generate)\b.{0,40}\b(work\s*order|wo)\b|\b(work\s*order|wo)\b.{0,40}\b(create|raise|submit|log|make|generate)\b|^\s*(?:please\s+)?open\s+(?:(?:a|an|new)\s+)*(?:work\s*order|wo)\b/i
export function isWorkOrderRequest(question: string): boolean {
  return MUTATION_INTENT.test(question)
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
    label,
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
