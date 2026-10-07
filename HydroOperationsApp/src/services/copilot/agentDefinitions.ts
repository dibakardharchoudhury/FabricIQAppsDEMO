import { ANSWER_PRESENTATION_CONTRACT, OPERATIONAL_EVIDENCE_CONTRACT } from './answerPresentation.ts'
import type { AgentRole } from './orchestration.ts'

export const AGENT_NAMES: Record<AgentRole, string> = {
  supervisor: 'hydro-supervisor-agent',
  qa: 'hydro-qa-agent',
  rca: 'hydro-rca-agent',
  'work-order': 'hydro-work-order-agent',
  'fabric-iq': 'hydro-fabric-iq-agent',
}

export const DIRECT_TOOLS = ['query_assets', 'query_operations', 'query_telemetry', 'query_signal_quality_snapshot', 'run_kql', 'visualize_dataset', 'show_3d_model'] as const
export const AGENT_INSTRUCTIONS: Record<AgentRole, string> = {
  supervisor: `You are the Hydro Operations Supervisor, a persistent Foundry agent.
Delegate operational requests using delegate_to_agent. Choose qa for factual questions, rca for root cause investigations, work-order for drafting new work, and fabric-iq only for explicit Fabric Data Agent or ontology-native semantic queries. Preserve which source the user requested. Do not route ordinary Q&A through the Data Agent or ontology. Fabric IQ is a tool, not a synonym for the Data Agent.
Reading existing work orders is Q&A, not a Work Order agent task. A compound question about turbine health and already-open work must go to qa as one complete request; its direct tools return both telemetry and related work. Use work-order only when the user explicitly requests drafting or creating new work.
The delegated question must preserve the user's exact scope, filters, time and requested format. You may use multiple specialists when the request genuinely needs them, but do not repeat successful requests. Never invent a specialist result or perform database writes. Return a specialist's answer faithfully, preserving its datasets and all material caveats. A draft is never a created work order.`,
  qa: 'You are the persistent Hydro Q&A agent. Use direct hydro_query tools, not the Fabric Data Agent. Answer from returned evidence. Minimize query rounds, preserve all matching records, and report source failures explicitly.',
  rca: 'You are the persistent Hydro RCA agent. Use direct hydro_query tools for telemetry, inspections, work, and metadata. Separate observed facts, hypotheses with supporting/contradicting evidence, confidence, evidence gaps, and recommended checks. Do not assert an unverified cause.',
  'work-order': 'You are the persistent Hydro Work Order agent. Resolve equipment and signal identity using direct tools, check all existing open work, then call propose_work_order through hydro_query for each requested draft. Proposals are shown as editable approval cards. Only the human can approve the SQL write. Never claim a draft was created in SQL. Do not ask for typed confirmation commands.',
  'fabric-iq': 'You are the persistent Hydro Fabric IQ specialist. Use fabriciq-data-agent only for explicit Data Agent requests and fabriciq-ontology for explicit ontology-native queries. These are separate source connections. The Data Agent queries underlying tables directly while its ontology query path has a temporary product limitation. Direct Ontology access is independent; do not infer its health from that limitation. Schema discovery does not answer a request for instance rows: continue with a read-only data query for the requested property values, without asking permission again for the already-requested read. If instance execution is unavailable, report that limitation explicitly. Preserve returned facts, scope and source limitations. Report the source actually executed. Never silently substitute one endpoint for the other, ungrounded knowledge, or a deprecated Fabric Data Agent tool. A successful definition read is not evidence of query execution.',
}

export function buildAgentInput(
  context: string,
  history: ReadonlyArray<{ role: 'user' | 'assistant'; content: string }>,
  question: string,
) {
  const messages: Array<{ role: 'developer' | 'user' | 'assistant'; content: string }> = [
    { role: 'developer', content: context },
    ...history,
    { role: 'user', content: question },
  ]
  return messages.map(message => ({
    type: 'message' as const,
    role: message.role,
    content: message.role === 'assistant'
      ? [{ type: 'output_text' as const, text: message.content, annotations: [] }]
      : [{ type: 'input_text' as const, text: message.content }],
  }))
}

export function agentDefinition(role: AgentRole, model: string, fabricIqConnection?: string, ontologyConnection?: string) {
  const usesFabricIq = role === 'fabric-iq'
  if (usesFabricIq && (!fabricIqConnection || !ontologyConnection)) throw new Error('Both verified target-specific Fabric IQ connections are required.')
  const tools = role === 'supervisor' ? [{
    type: 'function', name: 'delegate_to_agent',
    description: 'Invoke a separate persistent Foundry specialist and return its grounded result.',
    parameters: {
      type: 'object', properties: {
        specialist: { type: 'string', enum: ['qa', 'rca', 'work-order', 'fabric-iq'] },
        question: { type: 'string' },
      }, required: ['specialist', 'question'], additionalProperties: false,
    }, strict: true,
  }] : usesFabricIq ? [
    { type: 'fabric_iq_preview', server_label: 'fabriciq-data-agent', project_connection_id: fabricIqConnection, require_approval: 'never' },
    { type: 'fabric_iq_preview', server_label: 'fabriciq-ontology', project_connection_id: ontologyConnection, require_approval: 'never' },
  ] : [{
    type: 'function', name: 'hydro_query',
    description: 'Execute a permitted Hydro tool as the signed-in user. Tool schemas and source catalog are supplied in the current context. Pass arguments as a structured object matching the selected tool schema, never a JSON-encoded string. Prefer exact identifiers or non-regex KQL when possible.',
    parameters: {
      type: 'object', properties: {
        tool_name: { type: 'string', enum: [...DIRECT_TOOLS, ...(role === 'work-order' ? ['propose_work_order'] : [])] },
        arguments: { type: 'object', additionalProperties: true },
      }, required: ['tool_name', 'arguments'], additionalProperties: false,
    }, strict: false,
  }]
  return {
    kind: 'prompt', model, instructions: `${AGENT_INSTRUCTIONS[role]}\n\n${OPERATIONAL_EVIDENCE_CONTRACT}\n\n${ANSWER_PRESENTATION_CONTRACT}`,
    tools, reasoning: { effort: 'low' },
  }
}

export function parseHydroQuery(raw: string):
  | { ok: true; toolName: string; argumentsJson: string; args: Record<string, unknown> }
  | { ok: false; error: string } {
  const record = (value: unknown): value is Record<string, unknown> =>
    Boolean(value) && typeof value === 'object' && !Array.isArray(value)
  try {
    const value: unknown = JSON.parse(raw)
    if (!record(value) || typeof value.tool_name !== 'string' || !record(value.arguments)) {
      return { ok: false, error: 'hydro_query requires a tool_name string and an arguments object, not JSON encoded inside a string.' }
    }
    return { ok: true, toolName: value.tool_name, argumentsJson: JSON.stringify(value.arguments), args: value.arguments }
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
    return { ok: false, error: `Invalid tool JSON: ${error.message}. Serialize valid JSON, including escaped backslashes.` }
  }
}

export function parseDelegation(args: string): { specialist: Exclude<AgentRole, 'supervisor'>; question: string } {
  const value: unknown = JSON.parse(args)
  if (!value || typeof value !== 'object' || !('specialist' in value) || !('question' in value)
    || typeof value.question !== 'string' || !value.question.trim()) throw new Error('Invalid specialist delegation.')
  const specialist = value.specialist
  if (specialist !== 'qa' && specialist !== 'rca' && specialist !== 'work-order' && specialist !== 'fabric-iq') {
    throw new Error('Unknown Foundry specialist.')
  }
  return { specialist, question: value.question }
}
