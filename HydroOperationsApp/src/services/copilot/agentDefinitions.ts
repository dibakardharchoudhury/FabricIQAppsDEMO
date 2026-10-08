import { ANSWER_PRESENTATION_CONTRACT, OPERATIONAL_EVIDENCE_CONTRACT } from './answerPresentation.ts'
import { positiveActionClauses, type AgentRole } from './orchestration.ts'
import { RCA_REPORT_TOOL } from './rcaEvidence.ts'

export const AGENT_NAMES: Record<AgentRole, string> = {
  supervisor: 'hydro-supervisor-agent',
  qa: 'hydro-qa-agent',
  rca: 'hydro-rca-agent',
  'work-order': 'hydro-work-order-agent',
  'fabric-iq': 'hydro-fabric-iq-agent',
}

export const DIRECT_TOOLS = ['query_assets', 'query_operations', 'query_telemetry', 'query_station_power', 'query_signal_quality_snapshot', 'query_turbine_temperature_snapshot', 'run_kql', 'visualize_dataset', 'show_3d_model'] as const
export function nativeAssignmentError(question: string): string | undefined {
  const local = [...DIRECT_TOOLS, 'hydro_query', 'propose_work_order', 'complete_rca_assessment']
    .filter(name => new RegExp(`\\b${name}\\b`, 'i').test(positiveActionClauses(question)))
  return local.length ? `Native-source assignments cannot prescribe local Hydro application tools (${local.join(', ')}). Ask for the business data, identities, time window and output fields using the native source's own capabilities. Assign direct Hydro tool execution to Gauge/Sleuth/Fixer separately. No native agent was invoked and no delegation slot was consumed.` : undefined
}
export const AGENT_INSTRUCTIONS: Record<AgentRole, string> = {
  supervisor: `You are the Hydro Operations Supervisor, a persistent Foundry agent.
Delegate operational requests using delegate_to_agent. Choose qa for factual questions, rca for root cause investigations, work-order for drafting new work, and fabric-iq only for explicit Fabric Data Agent or ontology-native semantic queries. Preserve which source the user requested. Do not route ordinary Q&A through the Data Agent or ontology. Fabric IQ is a tool, not a synonym for the Data Agent.
Reading existing work orders is Q&A, not a Work Order agent task. A question only about turbine health and already-open work goes to qa as one factual task; its direct tools return both telemetry and related work. This does not override additional investigation, drafting or independent-verification requests. Split compound tasks by capability: factual triage to qa, investigation to rca, editable drafts to work-order, and final independent verification back to qa. Do not send the whole compound workflow to qa and let it simulate other specialists.
An explicit instruction not to save/create SQL records forbids writes, not preparation of an editable review card. A prose draft from qa is not a Work Order specialist result. If conditional drafting is requested, delegate the evidence-backed gap assessment to work-order; it may explicitly conclude no draft is justified.
For an explicit draft request, delegate to work-order immediately. Do not respond with an optional-field questionnaire or ask for typed confirmation. The specialist resolves equipment and produces the editable review card; only unresolved or ambiguous equipment identity needs clarification. For an explicit multi-step investigation, preserve the requested sequence (for example RCA, then Work Order draft, then Q&A verification), routing each handoff through you and carrying forward the verified findings.
The delegated question must preserve the assigned step's source, filters, time and requested format, not copy the entire compound request. In ontology-context workflows, ask Fabric IQ only for ontology identities/instances/relationships; send subsequent direct telemetry and SQL verification to qa, diagnosis to rca and drafting to work-order. For a Data Agent inventory followed by direct verification/investigation, ask Fabric IQ only for that inventory; do not also ask it to investigate telemetry or draft work. You may use multiple specialists when the request genuinely needs them, but do not repeat successful requests. Never invent a specialist result or perform database writes. Return a specialist's answer faithfully, preserving its datasets and all material caveats. A draft is never a created work order.`,
  qa: 'You are the persistent Hydro Q&A agent. Use direct hydro_query tools, not the Fabric Data Agent. Execute only your assigned factual retrieval or verification task; the original compound operator request is context, not authority to perform other specialists roles. Do not perform a root-cause investigation or produce a prose work-order draft in place of RCA or Work Order specialists. Return factual evidence and identify remaining specialist work for the Supervisor. Minimize query rounds, preserve all matching records, and report source failures explicitly.',
  rca: 'You are the persistent Hydro RCA agent. Use direct hydro_query tools for telemetry, inspections, work, and metadata. Finish by calling complete_rca_assessment, referencing actual evidence_id values and JSON pointers relative to their data. Select two to four competing hypotheses with supporting/contradictory source references and missing-evidence categories. References are checked by application code; unknown sources, missing paths and free-text diagnostic claims are rejected. Do not return prose instead of the structured report. There is no configured validated causal model or approved diagnostic-limit source, so cause remains undetermined. Never invent thresholds, baseline validity, numerical confidence or a normal-performance classification.',
  'work-order': 'You are the persistent Hydro Work Order agent. Resolve equipment and signal identity using direct tools, check all existing open work, then call propose_work_order through hydro_query for each requested draft. Proposals are shown as editable approval cards. If optional fields are absent, use a factual editable title based on the resolved equipment, describe the operator-requested inspection without inventing a diagnosis, and use Medium priority unless the operator supplied another priority. Do not ask for optional title, description, priority, due date or assignee before showing the card. Ask only when equipment identity is missing or ambiguous. Only the human can approve the SQL write. Never claim a draft was created in SQL. Do not ask for typed confirmation commands.',
  'fabric-iq': 'You are the persistent Hydro Fabric IQ specialist. Execute only the requested native-source retrieval/comparison step, not the original compound workflow. Do not forward downstream direct-source verification, root-cause investigation or work-order drafting to a remote tool; those are separate specialists. For ontology identity context, query only the requested instance values and available relationships. For a Data Agent work inventory, retrieve only that inventory, without independently expanding into telemetry/inspection investigation. Use fabriciq-data-agent only for explicit Data Agent requests and fabriciq-ontology for explicit ontology-native queries. These are separate source connections. The Data Agent queries underlying tables directly while its ontology query path has a temporary product limitation. Direct Ontology access is independent; do not infer its health from that limitation. Schema discovery does not answer a request for instance rows: continue with a read-only data query for the requested property values, without asking permission again for the already-requested read. If instance execution is unavailable, report that limitation explicitly. Preserve returned facts, scope and source limitations. Report the source actually executed. Never silently substitute one endpoint for the other, ungrounded knowledge, or a deprecated Fabric Data Agent tool. A successful definition read is not evidence of query execution.',
}

export function buildAgentInput(
  context: string,
  history: ReadonlyArray<{ role: 'user' | 'assistant'; content: string }>,
  question: string,
  role: AgentRole = 'supervisor',
) {
  const messages: Array<{ role: 'developer' | 'user' | 'assistant'; content: string }> = [
    { role: 'developer', content: context },
    ...(role === 'supervisor' ? history : []),
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
        reason: { type: 'string', description: 'One short user-visible explanation of the selected capability and source. Not private reasoning.' },
        native_source: { type: ['string', 'null'], enum: ['data-agent', 'ontology', null], description: 'For fabric-iq, select exactly the native source requested by the operator. Use separate delegations when both are requested. For other specialists use null.' },
      }, required: ['specialist', 'question', 'reason', 'native_source'], additionalProperties: false,
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
    kind: 'prompt', model, instructions: `${AGENT_INSTRUCTIONS[role]}\n\n${role === 'work-order' ? 'A draft exists only after propose_work_order succeeds. No-save instructions do not forbid this in-memory staging tool. If no draft is justified or identity requires clarification, call complete_work_order_review with that decision and its evidence-backed reason. Never describe a prose-only proposal as an editable card.' : ''}\n\n${OPERATIONAL_EVIDENCE_CONTRACT}\n\n${ANSWER_PRESENTATION_CONTRACT}`,
    tools: [...tools, ...(role === 'rca' ? [RCA_REPORT_TOOL] : []), ...(role === 'work-order' ? [{
      type: 'function', name: 'complete_work_order_review',
      description: 'Record why no approval card is being staged. This is a read-only review outcome, not a SQL write.',
      parameters: {
        type: 'object', properties: {
          decision: { type: 'string', enum: ['no_draft', 'needs_clarification'] },
          reason: { type: 'string', description: 'Specific evidence-backed reason no draft is justified, or the unresolved identity question.' },
        }, required: ['decision', 'reason'], additionalProperties: false,
      }, strict: true,
    }] : [])], reasoning: { effort: 'low' },
  }
}

export function parseWorkOrderReview(raw: string): { decision: 'no_draft' | 'needs_clarification'; reason: string } {
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || !('decision' in value)
    || (value.decision !== 'no_draft' && value.decision !== 'needs_clarification')
    || !('reason' in value) || typeof value.reason !== 'string' || !value.reason.trim()) {
    throw new Error('A no-draft review requires an explicit decision and evidence-backed reason.')
  }
  return { decision: value.decision, reason: value.reason.trim() }
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
    if (Object.hasOwn(value.arguments, 'arguments')) {
      const corrected = record(value.arguments.arguments)
        ? JSON.stringify({ tool_name: value.tool_name, arguments: value.arguments.arguments }) : undefined
      return { ok: false, error: `Unexpected arguments.arguments wrapper. Put the selected tool's fields directly inside arguments, with no second arguments object.${corrected && corrected.length <= 2400 ? ` Retry this flat payload: ${corrected}` : ''}` }
    }
    return { ok: true, toolName: value.tool_name, argumentsJson: JSON.stringify(value.arguments), args: value.arguments }
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
    return { ok: false, error: `Invalid tool JSON: ${error.message}. Serialize valid JSON, including escaped backslashes.` }
  }
}

export type NativeSource = 'data-agent' | 'ontology'

export function requestedNativeSources(question: string): NativeSource[] {
  const positive = positiveActionClauses(question)
  const action = '\\b(?:use|ask|query|consult|retrieve|read|compare|request|list|resolve)\\b[^.!?;\\n]{0,180}\\b'
  return [
    ...(new RegExp(`${action}data[\\s-]+agent\\b`, 'i').test(positive) ? ['data-agent' as const] : []),
    ...(new RegExp(`${action}ontology\\b`, 'i').test(positive) ? ['ontology' as const] : []),
  ]
}

export function nativeSourceError(question: string, source: unknown, priorUserQuestions: readonly string[] = []): string | undefined {
  if (source !== 'data-agent' && source !== 'ontology') return 'fabric-iq requires native_source data-agent or ontology. Select one requested source per delegation.'
  const requestedContext = [question, ...[...priorUserQuestions].reverse()]
    .find(text => /\b(?:data[\s-]+agent|ontology|fabric[\s-]+iq)\b/i.test(text))
  if (!requestedContext) return 'The operator has not requested a native Data Agent, Ontology or Fabric IQ source in this conversation. Use Gauge for ordinary inventory, telemetry, SQL work and charts; do not add an unnecessary native delegation.'
  const dataAgent = /\bdata[\s-]+agent\b/i.test(requestedContext)
  const ontology = /\bontology\b/i.test(requestedContext)
  if (dataAgent && !ontology && source !== 'data-agent') return 'The operator requested the Data Agent, not direct Ontology. Set native_source to data-agent; no substitution is permitted.'
  if (ontology && !dataAgent && source !== 'ontology') return 'The operator requested Ontology, not the Data Agent. Set native_source to ontology; no substitution is permitted.'
  return undefined
}

export function nativeToolChoice(source: NativeSource) {
  return { type: 'allowed_tools', mode: 'required', tools: [{ type: 'mcp', server_label: `fabriciq-${source}` }] }
}

export function verifyNativeReceipt(item: unknown, source: NativeSource): boolean {
  if (!item || typeof item !== 'object' || !('type' in item) || item.type !== 'mcp_call') return false
  if (!('server_label' in item) || item.server_label !== `fabriciq-${source}`) {
    throw new Error(`Native source mismatch: expected fabriciq-${source}. The response was not accepted and no source substitution was performed.`)
  }
  if (!('status' in item) || item.status !== 'completed' || ('error' in item && item.error)) {
    throw new Error(`Native source fabriciq-${source} did not return a successful execution receipt.`)
  }
  return true
}

export function parseDelegation(args: string): { specialist: Exclude<AgentRole, 'supervisor'>; question: string; reason?: string; nativeSource?: NativeSource } {
  const value: unknown = JSON.parse(args)
  if (!value || typeof value !== 'object' || !('specialist' in value) || !('question' in value)
    || typeof value.question !== 'string' || !value.question.trim()) throw new Error('Invalid specialist delegation.')
  const specialist = value.specialist
  if (specialist !== 'qa' && specialist !== 'rca' && specialist !== 'work-order' && specialist !== 'fabric-iq') {
    throw new Error('Unknown Foundry specialist.')
  }
  if ('reason' in value && (typeof value.reason !== 'string' || !value.reason.trim())) throw new Error('Invalid delegation reason.')
  return { specialist, question: value.question,
    ...('native_source' in value && (value.native_source === 'data-agent' || value.native_source === 'ontology') ? { nativeSource: value.native_source } : {}),
    ...('reason' in value && typeof value.reason === 'string' ? { reason: value.reason.trim() } : {}) }
}
