export type AgentRole = 'supervisor' | 'qa' | 'work-order' | 'rca' | 'fabric-iq'
export const AGENT_DISPLAY_NAMES: Record<AgentRole, string> = {
  supervisor: 'Supervisor', qa: 'Gauge', 'work-order': 'Fixer', rca: 'Sleuth', 'fabric-iq': 'Sparky',
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
  activity?: 'tool-start' | 'tool-end' | 'delegation-return' | 'checked-presentation' | 'validation'
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

export function failedExecutionEvents(
  events: OrchestrationEvent[] | undefined, detail: string, timestamp = Date.now(),
): OrchestrationEvent[] {
  const values = events?.map(event => event.status === 'queued' || event.status === 'running'
    ? { ...event, status: 'error' as const, detail, timestamp }
    : event) ?? []
  if (values.some(event => event.status === 'error')) return values
  return [...values, {
    id: `transport-failure:${timestamp}`,
    role: 'supervisor',
    status: 'error',
    label: 'Agent request failed',
    detail,
    timestamp,
    agentName: 'Hydro Operations',
  }]
}

export function uncertifiedFailureMessage(error: unknown): string {
  const technical = error instanceof Error && error.message.trim()
    ? error.message.trim()
    : 'The agent transport failed.'
  return `The request failed before a certified answer was returned. No answer or write outcome is certified. ` +
    `Do not repeat this request or any write decision until connectivity recovers and the prior run audit is checked. ` +
    `Technical detail: ${technical}`
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
  backend?: {
    expiresAt: string
    decide: (approved: boolean, edits?: Pick<WorkOrderProposal, 'title' | 'description' | 'priority'>) => Promise<Record<string, unknown>>
    reconcile: (approved: boolean, edits?: Pick<WorkOrderProposal, 'title' | 'description' | 'priority'>) => Promise<Record<string, unknown>>
  }
}

const MUTATION_INTENT = /\b(create|raise|submit|log|make|generate|prepare|propose)\b.{0,80}\b(work[\s-]*orders?|wos?|(?:inspection|maintenance|work)[ -]drafts?)\b|\b(work[\s-]*orders?|wos?)\b.{0,40}\b(create|raise|submit|log|make|generate|prepare|propose)\b|^\s*(?:please\s+)?(?:open|draft)\s+(?:(?:a|an|new)\s+)*(?:work[\s-]*orders?|wos?)\b|\bdraft\s+(?:a|an|new|the|these|those)\b.{0,40}\b(work[\s-]*orders?|wos?)\b/i
// Single normalisation point: every detector reads the question through this function, so abbreviations resolve
// once instead of in each pattern.
export const positiveActionClauses = (question: string) => question
  .replace(/\bWOs?\b(?!-\d)/gi, match => (match.toLowerCase() === 'wo' ? 'work order' : 'work orders'))
  .replace(
  /\b(?:do not|don't|never)\s+(?:independently\s+)?(?:create|raise|submit|log|make|generate|prepare|propose|draft|investigate|diagnose|perform|verify|check|reassess|review|continue|read|query|retrieve|inspect|use|ask|show|display|chart|plot|visualize)\b(?:(?!\bbut\b)[^.!?;\n])*/gi, '')

export function requiresChartOutput(question: string): boolean {
  const positive = positiveActionClauses(question)
  return /(?:^|[.!?]\s*)(?:please\s+)?(?:chart|plot|visualize)\b|\b(?:include|show|produce|return|provide|draw|create|display)\b[^.!?;\n]{0,160}\b(?:chart|plot|histogram|dashboard)\b/i.test(positive)
    || /\b(?:this|that|it|the (?:answer|output|result)) (?:is|was) not (?:a |the )?chart\b(?!\s+(?:request|question))|\b(?:no charts?|no dashboards?)\b[^.!?;\n]{0,100}\b(?:csv|dump)\b/i.test(positive)
}

export function requiresInspectionEvidence(question: string): boolean {
  return /\b(?:investigate|inspect|review|verify|check|compare|read|query|retrieve|using)\b[^.!?;\n]{0,160}\b(?:inspections|inspection (?:evidence|history|records|results))\b/i.test(positiveActionClauses(question))
}

export function requiresDirectSourceVerification(question: string): boolean {
  return /\b(?:verify|check|compare|reconcile|reconciliation|verification)\b[^.!?;\n]{0,240}\bdirect\b/i.test(positiveActionClauses(question))
}

export function requiredIndependentQaSteps(question: string): number {
  const positive = positiveActionClauses(question)
  const request = /\bindependently\s+(?:check|verify|reconcile)\b[^.!?;\n]{0,80}/i.exec(positive)?.[0]
  if (!request) return 1
  const finalIndependentPhase = /\bfinally\s*,?\s*independently\s+(?:check|verify|reconcile)\b/i.test(positive)
  const quantified = /\b(both|two|three|four|five|six|seven|eight|[2-8])\b/i.exec(request)?.[1]?.toLowerCase()
  if (!quantified) return finalIndependentPhase ? 2 : 1
  const counts: Record<string, number> = {
    both: 2, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  }
  return Math.max(finalIndependentPhase ? 2 : 1, counts[quantified] ?? Number(quantified))
}

export function requestedLookback(question: string): string | undefined {
  const match = positiveActionClauses(question)
    .match(/\btoday\b|\b(?:last|past|within the last)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*(minutes?|hours?|days?|m|h|d)\b/i)
  if (!match) return undefined
  const counts: Record<string, number> = {
    one: 1, two: 2, three: 3, four: 4, five: 5,
    six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  }
  return match[0].toLowerCase() === 'today'
    ? 'today'
    : `${counts[match[1].toLowerCase()] ?? Number(match[1])}${match[2][0].toLowerCase()}`
}

export function requestedRankLimit(question: string): number | undefined {
  const match = positiveActionClauses(question)
    .match(/\btop\s+(\d+|five|ten)\b|\b(\d+|five|ten)\s+(?:hottest|highest)\b/i)
  const rank = (match?.[1] ?? match?.[2])?.toLowerCase()
  return rank === 'five' ? 5 : rank === 'ten' ? 10 : rank ? Number(rank) : undefined
}

export function requestedWorkBacklogGroup(question: string): 'facility' | 'equipment' | undefined {
  const normalized = positiveActionClauses(question).toLowerCase().replace(/[\s_-]+/g, ' ')
  if (!/\b(?:work|orders?|backlog)\b/.test(normalized)) return undefined
  if (/\b(?:facility level|facility backlog|by facility|per facility)\b/.test(normalized)) return 'facility'
  if (/\b(?:orders per equipment|work by equipment|(?:orders|backlog) by equipment|(?:orders|work|backlog) per equipment|most open orders)\b/.test(normalized)
      || /\b(?:what )?(?:open )?(?:work|orders?)\b[^.!?;\n]{0,40}\b(?:on|for) each(?: (?:equipment|turbine|unit))?\b/.test(normalized)) {
    return 'equipment'
  }
  return undefined
}

export type EquipmentSelection = {
  metric: 'signal_quality_count' | 'open_work_order_count'
  quality?: 'BAD' | 'UNCERTAIN'
  tie_break: 'equipment_id_asc'
}

export function equipmentSelectionForRequest(question: string): EquipmentSelection | undefined {
  const positive = positiveActionClauses(question)
  const selection = /\b(?:select|choose|pick)\b[^.!?;\n]{0,200}\b(?:equipment|turbine|unit)\b[^.!?;\n]{0,120}\bmost\b[^.!?;\n]{0,120}/i
    .exec(positive)?.[0]
  if (!selection || !/\bbreak(?:ing)? ties?\b[^.!?;\n]{0,80}\bequipment id\b/i.test(positive)) return undefined
  const quality = /\b(BAD|UNCERTAIN)\b/i.exec(selection)?.[1]?.toUpperCase()
  if (quality && /\bsignals?\b/i.test(selection)) {
    return { metric: 'signal_quality_count', quality: quality as 'BAD' | 'UNCERTAIN', tie_break: 'equipment_id_asc' }
  }
  if (/\b(?:open )?(?:work(?: orders?)?|orders?|backlog)\b/i.test(selection)) {
    return { metric: 'open_work_order_count', tie_break: 'equipment_id_asc' }
  }
  return undefined
}

export function directVerificationSources(question: string): Array<{ tool: string; entity?: string }> {
  const text = positiveActionClauses(question)
  return [
    ...(/\b(?:work[\s-]*orders?|open work|work records|backlog)\b/i.test(text)
      ? [{ tool: 'query_operations', entity: 'work_orders' }] : []),
    ...(/\bdirect\s+(?:asset|equipment|facility)|\bfacility[- ]level\b/i.test(text)
      ? [{ tool: 'query_assets', entity: 'equipment' }] : []),
    ...(/\bfacility[- ]level\b/i.test(text)
      ? [{ tool: 'query_assets', entity: 'facilities' }] : []),
  ]
}

export function rcaAssignmentError(task: string): string | undefined {
  if (/\breturn only (?:the )?(?:verification )?(?:plan|queries)\b|\b(?:do not|don't|never) (?:run|execute) (?:them|(?:any|the|these) queries)\b/i.test(task)) {
    return 'Sleuth completes an actual source-referenced investigation, not a prose-only query plan. Assign factual verification to Gauge or allow Sleuth to read the required evidence and complete_rca_assessment. Do not prohibit the evidence reads needed by this specialist. No agent was invoked.'
  }
  return undefined
}

// Surface vocabulary only. The concept detector below combines these terms per clause, in any order, so that no
// operator phrasing is encoded.
const WORK_ORDER_TERM = String.raw`(?:work[\s-]*orders?|wos?|maintenance\s+(?:tasks?|jobs?|requests?|orders?)|inspection\s+(?:orders?|tasks?|jobs?))`
const EQUIPMENT_TERM = String.raw`(?:turbines?|equipment|units?|assets?|generators?|machines?|T\d{1,3}|EQUIP_RTI_T?\d+)`
const ADVISORY_MODAL = String.raw`(?:need(?:s|ed)?|should|must|ought|have to|has to|require[sd]?|warrant(?:s|ed)?|call(?:s|ed)? for|worth|recommend(?:ed)?|advis(?:e|able)|appropriate|justified|deserve[sd]?)`
const WORK_ACTION = String.raw`(?:open(?:ed)?|creat(?:e|ed|ion)|rais(?:e|ed)|submit(?:ted)?|log(?:ged)?|generat(?:e|ed)|initiat(?:e|ed)|schedul(?:e|ed)|issu(?:e|ed)|plann?(?:ed)?)`
const IDENTIFY_CUE = String.raw`(?:which|what|who|where|any|are there|is there|do any|does any|identify|determine|decide|find|tell me|show me|list|check (?:if|whether)|assess|evaluate)`
const OBJECT_MODAL = String.raw`(?:need(?:s|ed)?|require[sd]?|warrant(?:s|ed)?|deserve[sd]?|justif(?:y|ies|ied))`
const clauseHas = (clause: string, term: string) => new RegExp(String.raw`\b${term}\b`, 'i').test(clause)

/** True when the operator asks the system to decide which work (or which equipment) needs maintenance action, as
 *  opposed to listing existing records or creating a named one. Such a question is answered by comparing condition
 *  evidence with the existing open-work cover. */
export function requestsWorkOrderNeedAssessment(question: string): boolean {
  return positiveActionClauses(question).split(/[.!?;\n]+/).some(clause => {
    if (!clauseHas(clause, IDENTIFY_CUE) || !clauseHas(clause, ADVISORY_MODAL)) return false
    const aboutWork = clauseHas(clause, WORK_ORDER_TERM)
      && (clauseHas(clause, WORK_ACTION) || clauseHas(clause, OBJECT_MODAL))
    const aboutEquipment = clauseHas(clause, EQUIPMENT_TERM)
      && /\b(?:maintenance|inspection|attention|servicing|repairs?)\b/i.test(clause)
    return aboutWork || aboutEquipment
  })
}

export function isWorkOrderRequest(question: string): boolean {
  const positiveClauses = positiveActionClauses(question).replace(
    /\b(?:prepare|generate|make)\s+(?:(?:a|an|the|new)\s+)?(?:table|chart|report|summary|list|dashboard|comparison)\b/gi,
    match => match.replace(/^\w+/, 'show'))
  // A question asking WHICH work should be raised is advice; it only becomes a draft request when a draft is asked for.
  if (requestsWorkOrderNeedAssessment(question)) return /\b(?:drafts?|stage|propose|prepare)\b/i.test(positiveClauses)
  return MUTATION_INTENT.test(positiveClauses)
    || (/\b(?:prepare|propose)\b.{0,80}\bdrafts?\b/i.test(positiveClauses) && /\bwork[\s-]*orders?\b/i.test(positiveClauses))
    || /\b(?:propose|prepare|create|stage)\b[^.!?;\n]{0,80}\b(?:inspection|maintenance)(?:\s+(?:work|task|follow-up))?\b/i.test(positiveClauses)
}

export function workOrderPriorityForRequest(question: string): WorkOrderProposal['priority'] {
  const values = new Set<string>()
  const positive = positiveActionClauses(question)
  const patterns = [
    /\b(low|medium|high|critical)[ -]priority\b/gi,
    /\bpriority\s*(?::|=|of|is|to)?\s*["']?(low|medium|high|critical)\b/gi,
  ]
  for (const clause of positive.split(/[.!?;\n]|\b(?:and|then)\b/i)) {
    const field = clause.match(/^\s*(?:priority\s*[:=]|(?:set|assign)\s+(?:(?:its|the)\s+)?priority\s*(?::|=|to|as)?)\s*["']?(low|medium|high|critical)\b/i)
    if (field) values.add(field[1].toLowerCase())
    const action = clause.match(/\b(?:create|raise|submit|log|make|generate|prepare|propose|draft)\b/i)
    if (!action) continue
    if (/\b(?:list|show|find|read|query|count|compare|check|review)\b/i.test(clause.slice(0, action.index))) continue
    const assignment = clause.slice(action.index).split(/\b(?:after|before|checking|reviewing|where|unless|if)\b/i)[0]
    if (!isWorkOrderRequest(assignment)) continue
    for (const pattern of patterns) for (const match of assignment.matchAll(pattern)) values.add(match[1].toLowerCase())
  }
  if (values.size > 1) throw new Error('The work-order request specifies conflicting priorities. Specify one priority for this draft.')
  const value = [...values][0]
  return value === 'low' ? 'Low' : value === 'high' ? 'High' : value === 'critical' ? 'Critical' : 'Medium'
}

export function isNotificationDraftRequest(question: string): boolean {
  return /\b(?:draft|prepare|compose|write)\b[^.!?;\n]{0,100}\b(?:notification|email|message)\b|\b(?:notification|email|message)\s+draft\b/i.test(positiveActionClauses(question))
}

export function requiredSpecialists(question: string, hasNativeRequest = false): AgentRole[] {
  const positive = positiveActionClauses(question)
  const factualRead = /\b(?:read|retrieve|inspect|list|show|return|query|check|verify|reconcile|compare|chart|plot|visualize|which|what)\b[^.!?;\n]{0,220}\b(?:telemetry|signals?|readings?|work(?:[\s-]+orders?)?|wos?|inspections?|notifications?|spare parts?|inventory|assets?|equipment|turbines?|generators?|facilit(?:y|ies)|relationships?|power|temperature|quality)\b/i.test(positive)
  const downstreamRead = requiresDirectSourceVerification(question)
    || /\busing\b[^.!?;\n]{0,100}\b(?:verified|resolved|returned|those|these)\b[^.!?;\n]{0,100}\b(?:ids?|identit(?:y|ies)|instances?|results?)\b[^.!?;\n]{0,120}\b(?:read|retrieve|inspect|query|check|verify)\b/i.test(positive)
    || /\b(?:then|also|separately|independently)\b[^.!?;\n]{0,160}\b(?:read|retrieve|inspect|query|check|verify|reconcile)\b/i.test(positive)
  const required = missingRequestedSpecialists(question, [])
  if ((factualRead || requestsWorkOrderNeedAssessment(question)) && (!hasNativeRequest || downstreamRead) && !required.includes('qa')) required.push('qa')
  return (['qa', 'rca', 'work-order'] as const).filter(role => required.includes(role))
}

export function missingRequestedSpecialists(question: string, completed: readonly AgentRole[]): AgentRole[] {
  const missing: AgentRole[] = []
  const positive = positiveActionClauses(question)
  const investigationMatch = /\b(?:investigate|diagnose|RCA|root[- ]cause analysis|perform (?:an? )?RCA)\b|\b(?:reassess|review|continue)\b.{0,60}\b(?:investigation|diagnosis|RCA)\b/i.exec(positive)
  const investigation = Boolean(investigationMatch)
  if (investigation && !completed.includes('rca')) missing.push('rca')
  const drafting = isWorkOrderRequest(question)
  if (drafting && !completed.includes('work-order')) missing.push('work-order')
  const verification = /\b(?:independently (?:check|verify)|independent (?:check|verification)|verify\b.{0,80}\bagain)\b/i.exec(positive)
  const verifyAfterInvestigation = verification && investigationMatch
    && (verification.index > investigationMatch.index || /\bfinal (?:independent )?verification\b/i.test(positive))
  const reviews: AgentRole[] = [...(verifyAfterInvestigation ? ['rca' as const] : []), ...(drafting ? ['work-order' as const] : [])]
  if (verification && (!completed.includes('qa') || reviews.some(role =>
    !completed.includes(role) || completed.lastIndexOf('qa') < completed.lastIndexOf(role)))) missing.push('qa')
  if (requiresDirectSourceVerification(question) && !completed.includes('qa') && !missing.includes('qa')) missing.push('qa')
  return missing
}

export function delegationOrderError(question: string, next: AgentRole, completed: readonly AgentRole[], remainingDelegations?: number): string | undefined {
  if (remainingDelegations !== undefined && remainingDelegations < 1) {
    return 'No unreserved delegation slots remain. Complete the outstanding explicitly requested native sources, or synthesize the completed workflow without another delegation.'
  }
  if (next === 'work-order' && !isWorkOrderRequest(question) && isNotificationDraftRequest(question)) {
    return 'The requested notification/email/message draft is prose, not an editable work-order request. Obtain factual evidence from qa/rca as needed, then compose the requested unsent message. No notification delivery tool is available. This rejected delegation did not consume a slot.'
  }
  const missing = missingRequestedSpecialists(question, completed)
  if (next === 'qa' && completed.includes('qa') && missing.includes('qa')) {
    const prerequisites = missing.filter(role => role !== 'qa')
    if (prerequisites.length) return `Independent final verification is premature. Complete ${prerequisites.join(' -> ')} first, then delegate verification to qa. This rejected delegation did not invoke an agent or consume a delegation slot.`
  }
  const stillRequired = missingRequestedSpecialists(question, [...completed, next])
  if (remainingDelegations !== undefined && stillRequired.length && stillRequired.length > remainingDelegations - 1) {
    return `This delegation would exhaust slots needed for requested work. Reserve the remaining ${remainingDelegations} delegations for ${missing.join(' -> ')}. Sleuth can retrieve missing investigation evidence itself; do not insert another factual-read delegation first. This rejected delegation did not invoke an agent or consume a slot.`
  }
  return undefined
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

export class WorkOrderProposalValidationError extends Error {}

export function createWorkOrderProposal(input: {
  equipmentId?: string
  instrumentId?: string
  opcuaNodeId?: string
  title?: string
  description?: string
  priority?: string
}): WorkOrderProposal {
  for (const field of ['equipmentId', 'instrumentId', 'opcuaNodeId', 'title', 'description', 'priority'] as const) {
    const value = input[field]
    if (value !== undefined && typeof value !== 'string') {
      throw new WorkOrderProposalValidationError(`${field} must be a string when supplied.`)
    }
  }
  const equipmentId = input.equipmentId?.trim()
  if (!equipmentId) throw new WorkOrderProposalValidationError('equipment_id is required before a work order can be proposed.')
  const title = input.title?.trim()
  if (!title) throw new WorkOrderProposalValidationError('title is required before a work order can be proposed.')
  if (title.length > 200 || (input.description?.length ?? 0) > 4000) throw new WorkOrderProposalValidationError('Work-order title or description exceeds the allowed length: title maximum 200 characters, description maximum 4000 characters. Summarize evidence instead of copying raw rows; preserve the operator title.')
  const priority = input.priority?.trim()
  if (!priority || !['Low', 'Medium', 'High', 'Critical'].includes(priority)) {
    throw new WorkOrderProposalValidationError('priority must be Low, Medium, High, or Critical.')
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
