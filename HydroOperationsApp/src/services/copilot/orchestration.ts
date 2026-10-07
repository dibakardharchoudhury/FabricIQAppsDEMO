export type AgentRole = 'supervisor' | 'qa' | 'work-order' | 'rca' | 'data-agent'
export type OrchestrationStatus = 'queued' | 'running' | 'completed' | 'error' | 'approval'

export type OrchestrationEvent = {
  id: string
  role: AgentRole
  status: OrchestrationStatus
  label: string
  detail: string
  timestamp: number
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

const MUTATION_INTENT = /\b(create|raise|open|submit|log|make|generate)\b.{0,40}\b(work\s*order|wo)\b|\b(work\s*order|wo)\b.{0,40}\b(create|raise|open|submit|log|make|generate)\b/i
const RCA_INTENT = /\b(root cause|rca|diagnos(?:e|is|tic)|why (?:is|did|has|are)|cause of|contributing factor)\b/i
const DATA_AGENT_INTENT = /\b(?:ask|use|query|delegate to|send to)\b.{0,30}\bdata agent\b/i
const CONFIRMATION = /^\s*confirm\s+work\s+order\s+([a-z0-9-]+)\s*\.?\s*$/i

export function routeAgent(question: string): Exclude<AgentRole, 'supervisor'> {
  if (CONFIRMATION.test(question) || MUTATION_INTENT.test(question)) return 'work-order'
  if (RCA_INTENT.test(question)) return 'rca'
  if (DATA_AGENT_INTENT.test(question)) return 'data-agent'
  return 'qa'
}

export function confirmationProposalId(question: string): string | undefined {
  return question.match(CONFIRMATION)?.[1]
}

export function createOrchestrationEvent(
  role: AgentRole,
  status: OrchestrationStatus,
  detail: string,
  label = role === 'qa' ? 'Q&A Agent' : role === 'work-order' ? 'Work Order Agent' : role === 'rca' ? 'RCA Agent' : role === 'data-agent' ? 'Data Agent Bridge' : 'Supervisor',
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

export function specialistInstructions(role: Exclude<AgentRole, 'supervisor'>): string {
  switch (role) {
    case 'work-order':
      return 'You are the Work Order Agent. Gather live evidence and check existing open work first. To request a new work order, call propose_work_order exactly once with a complete draft. This only stages an approval; never claim that a work order was created until the tool reports a created workOrderNumber.'
    case 'rca':
      return 'You are the RCA Agent. Correlate telemetry, asset metadata, inspections, notifications, and open work. Separate observed facts from hypotheses, rank hypotheses by evidence, identify missing evidence, and never present a hypothesis as a confirmed root cause.'
    case 'data-agent':
      return 'You are the Data Agent Bridge. Return the Fabric Data Agent result without changing its facts or scope, and clearly identify any source limitation.'
    default:
      return 'You are the Q&A Agent. Answer the operational question from live tool evidence and preserve the shared semantic and response contracts.'
  }
}
