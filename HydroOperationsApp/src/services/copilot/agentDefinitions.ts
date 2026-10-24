import { ANSWER_PRESENTATION_CONTRACT } from './answerPresentation.ts'
import { positiveActionClauses, qualityWorkWorkflow, type AgentRole } from './orchestration.ts'
import {
  OPERATIONAL_EVIDENCE_CONTRACT,
} from './domainContract.ts'
import { RCA_REPORT_TOOL } from './rcaEvidence.ts'

export type NativeSource = 'data-agent'
// The Supervisor is the deterministic hosted orchestrator, not a Foundry prompt agent. Only specialists are persistent agents.
export type SpecialistRole = Exclude<AgentRole, 'supervisor'>

export const AGENT_NAMES: Record<SpecialistRole, string> = {
  qa: 'hydro-qa-agent',
  rca: 'hydro-rca-agent',
  'work-order': 'hydro-work-order-agent',
  'fabric-iq': 'hydro-fabric-iq-agent',
  weather: 'hydro-weather-agent',
}

const REASONING_EFFORT: Record<SpecialistRole, 'low'> = {
  qa: 'low',
  rca: 'low',
  'work-order': 'low',
  'fabric-iq': 'low',
  weather: 'low',
}

export const DIRECT_TOOLS = ['query_assets', 'query_operations', 'query_weather', 'query_work_backlog', 'query_telemetry', 'query_station_power', 'query_signal_quality_snapshot', 'query_turbine_temperature_snapshot', 'run_kql', 'visualize_dataset', 'show_3d_model'] as const
export const WEATHER_TOOLS = ['query_weather', 'query_assets', 'visualize_dataset'] as const
export const WORK_REVIEW_TOOL = {
  type: 'function', name: 'complete_work_order_review',
  description: 'Record why no approval card is being staged. This is a read-only review outcome, not a SQL write.',
  parameters: {
    type: 'object', properties: {
      decision: { type: 'string', enum: ['no_draft', 'needs_clarification'], description: 'Use no_draft only with verified complete work coverage. Use needs_clarification when condition evidence, current telemetry or canonical affected equipment is unavailable.' },
      reason: { type: 'string', maxLength: 1000, description: 'Concise evidence-backed reason within 1000 characters. State missing-data or identity blockers explicitly; do not infer healthy equipment from absent telemetry.' },
    }, required: ['decision', 'reason'], additionalProperties: false,
  }, strict: true,
}
export function nativeAssignmentError(question: string, source?: NativeSource): string | undefined {
  const local = [...DIRECT_TOOLS, 'hydro_query', 'propose_work_order', 'complete_rca_assessment']
    .filter(name => new RegExp(`\\b${name}\\b`, 'i').test(positiveActionClauses(question)))
  if (local.length) return `Native-source assignments cannot prescribe local Hydro application tools (${local.join(', ')}). Ask for the business data, identities, time window and output fields using the native source's own capabilities. Assign direct Hydro tool execution to Gauge/Sleuth/Fixer separately. No native agent was invoked and no delegation slot was consumed.`
  if (source === 'data-agent' && /\bdata[\s-]+agent(?:'s)?\s+(?:published\s+)?(?:results|outputs)\b|\bpublication\s+endpoint\b/i.test(positiveActionClauses(question))) {
    return 'Ask a business-data question, not for the Data Agent to retrieve its own published results or a publication endpoint. native_source already selects the connection. Preserve the requested turbine identities, quality, temperature, work, time window and output fields; its connected Lakehouse/Eventhouse/SQL tables are permitted execution sources. Request source/read timestamps only when available, disclosing missing metadata. No native agent was invoked and no delegation slot was consumed.'
  }
  return undefined
}
export const AGENT_INSTRUCTIONS: Record<SpecialistRole, string> = {
  weather: 'You are the persistent Hydro Weather specialist. Use query_weather through hydro_query for governed Lakehouse forecasts, observations, locations and operating areas; use query_assets only to resolve facility identities. Follow the assigned time window and target scope. Return source rows with vendor, forecast issue, valid/observation UTC time and units. Keep Aurora and UKMet separate, and never blend issues or invent missing values. Forecast precipitation is interval mm; cumulative rainfall differences require endpoints from the same vendor, target and issue, with a complete interval. Do not sum cumulative values or infer rainfall from missing boundary rows. Pressure is mean sea-level hPa, temperature/dew point Celsius, solar radiation W/m2 and wind m/s. Match location_id to facility_id exactly; area identity comes from weather_areas metadata, never name guessing. State stale forecast issues, incomplete windows, unavailable sources and empty results explicitly. Forecasts are not observations and do not establish an equipment fault or authorize maintenance. Do not create work orders or send alerts. Return evidence for the Supervisor to present; use visualize_dataset only for requested charts.',
  qa: 'You are the persistent Hydro Q&A agent. Use direct hydro_query tools, not the Fabric Data Agent. Execute only your assigned factual retrieval or verification task; the original compound operator request is context, not authority to perform other specialists roles. Do not perform a root-cause investigation or produce a prose work-order draft in place of RCA or Work Order specialists. Return factual evidence and identify remaining specialist work for the Supervisor. Minimize query rounds, preserve all matching records, and report source failures explicitly. Complete every requested factual dataset and relationship before finishing: a bridge inventory is not evidence for the requested target records. Use query_work_backlog for complete open-work counts or rankings grouped by equipment or facility instead of rebuilding its SQL-to-Lakehouse joins from separate reads. When one source identifies a type rather than equipment IDs, resolve the matching equipment and then read the requested related records for the complete resolved population, preserving unmatched identities and explicit empty results. For parts-to-maintenance questions, spare-part equipmentType maps to equipment_type_code; after resolving that equipment population, read all requested open work orders rather than stopping at the equipment inventory.',
  rca: 'You are the persistent Hydro RCA agent. Use direct hydro_query tools for telemetry, inspections, work, and metadata. Finish by calling complete_rca_assessment, referencing actual evidence_id values and JSON pointers relative to their data. Select two to four competing hypotheses with supporting/contradictory source references and missing-evidence categories. Complete maintenance_follow_up as a strict conditional-work gate: use verified_uncovered_issue only when cited source rows identify an issue within the assigned investigation domain and establish that matching open work does not cover it; otherwise use not_supported with the exact blocker and empty equipment_ids. Never pivot from the assigned evidence domain to an unrelated inspection, notification, parts, telemetry, or work issue merely because it concerns the same equipment. A type match, low stock, telemetry value, or untested hypothesis alone cannot enable maintenance work. References are checked by application code; unknown sources, missing paths and free-text diagnostic claims are rejected. Do not return prose instead of the structured report. There is no configured validated causal model or approved diagnostic-limit source, so cause remains undetermined. Never invent thresholds, baseline validity, numerical confidence or a normal-performance classification.',
  'work-order': 'You are the persistent Hydro Maintenance and Work Order agent. Use direct tools to review equipment identity, open work, inspections, notifications and spare-part stock for maintenance planning. Separate available stock from verified equipment compatibility, reservations, substitutes and lead time. Do not invent procedures, task durations, crew calendars, dispatch constraints, redundancy, outage windows or downtime recommendations when those sources are missing. Check complete existing-work coverage before proposing additional work. Before a no_draft decision, call query_operations for work_orders scoped to the resolved equipmentId, excluding both Completed and Cancelled with neq filters, and omit limit so the backend can attest complete open-work coverage; upstream prose or partial rows are not coverage. Call propose_work_order through hydro_query only for a specific evidence-backed uncovered gap; proposals are editable approval cards. If optional fields are absent, use a factual editable title based on the resolved equipment, describe the operator-requested inspection without inventing a diagnosis, and use Medium priority unless the operator supplied another priority. If no draft is justified or required planning evidence is unavailable, call complete_work_order_review and state the blockers. Ask only when equipment identity is missing or ambiguous. Only the human can approve the SQL write. Never claim a draft was created in SQL or ask for typed confirmation commands.',
  'fabric-iq': 'You are the persistent Hydro Fabric IQ specialist for the published Fabric Data Agent. Execute only the requested Data Agent retrieval/comparison step, not the original compound workflow. Pass the complete assignment verbatim to fabriciq-data-agent, including its output contract, and supply the exact requested_at_utc from context. Never replace it with operator_question, summarize away JSON fields or invent the requested JSON yourself. Do not forward downstream direct-source verification, root-cause investigation or work-order drafting to the Data Agent; those are separate specialists. Retrieve only the requested business inventory or aggregate without independently expanding its scope. Use only fabriciq-data-agent. Direct ontology-native chat execution is intentionally unsupported and must never be simulated through the Data Agent. Preserve returned facts, scope and source limitations. Report the source actually executed. Never substitute ungrounded knowledge or a deprecated Fabric Data Agent tool. A successful definition read is not evidence of query execution.',
}

export function agentDefinition(
  role: SpecialistRole,
  model: string,
  fabricIqConnection?: string,
) {
  const usesFabricIq = role === 'fabric-iq'
  if (usesFabricIq && !fabricIqConnection) throw new Error('The verified target-specific Data Agent connection is required.')
  const tools = usesFabricIq ? [
    { type: 'fabric_iq_preview', server_label: 'fabriciq-data-agent', project_connection_id: fabricIqConnection, require_approval: 'never' },
  ] : [{
    type: 'function', name: 'hydro_query',
    description: 'Execute a permitted Hydro tool as the signed-in user. Tool schemas and source catalog are supplied in the current context. Pass arguments as a structured object matching the selected tool schema, never a JSON-encoded string. Prefer exact identifiers or non-regex KQL when possible.',
    parameters: {
      type: 'object', properties: {
        tool_name: { type: 'string', enum: role === 'weather' ? [...WEATHER_TOOLS] : [...DIRECT_TOOLS, ...(role === 'work-order' ? ['propose_work_order'] : [])] },
        arguments: { type: 'object', additionalProperties: true },
      }, required: ['tool_name', 'arguments'], additionalProperties: false,
    }, strict: false,
  }]
  return {
    kind: 'prompt', model, instructions: `${AGENT_INSTRUCTIONS[role]}\n\n${role === 'work-order' ? 'A draft exists only after propose_work_order succeeds. No-save instructions do not forbid this in-memory staging tool. If no draft is justified or identity requires clarification, call complete_work_order_review with that decision and its evidence-backed reason. Never describe a prose-only proposal as an editable card.' : ''}\n\n${OPERATIONAL_EVIDENCE_CONTRACT}\n\n${ANSWER_PRESENTATION_CONTRACT}`,
    tools: [...tools, ...(role === 'rca' ? [RCA_REPORT_TOOL] : []), ...(role === 'work-order' ? [WORK_REVIEW_TOOL] : [])],
    reasoning: { effort: REASONING_EFFORT[role] },
  }
}

export function parseWorkOrderReview(raw: string): { decision: 'no_draft' | 'needs_clarification'; reason: string } {
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || !('decision' in value)
    || (value.decision !== 'no_draft' && value.decision !== 'needs_clarification')
    || Array.isArray(value) || Object.keys(value).some(key => !['decision', 'reason'].includes(key))
    || !('reason' in value) || typeof value.reason !== 'string' || !value.reason.trim() || value.reason.length > 1000) {
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

export function requestedNativeSources(question: string): NativeSource[] {
  if (qualityWorkWorkflow(question)) return ['data-agent']
  const positive = positiveActionClauses(question)
  const action = '\\b(?:use|ask|query|consult|retrieve|read|compare|request|list|resolve)\\b[^.!?;\\n]{0,180}\\b'
  if (new RegExp(`${action}ontology\\b`, 'i').test(positive)) {
    throw new Error('Direct ontology-native chat execution is not supported. Use the published Fabric Data Agent or governed direct sources.')
  }
  return new RegExp(`${action}data[\\s-]+agent\\b`, 'i').test(positive) ? ['data-agent'] : []
}

export function nativeSourceError(question: string, source: unknown, priorUserQuestions: readonly string[] = []): string | undefined {
  if (source === 'ontology') return 'Direct ontology-native chat execution is disabled. Use the published Data Agent or governed direct sources.'
  if (source !== 'data-agent') return 'fabric-iq requires native_source data-agent. Sparky has one published Data Agent tool.'
  if (qualityWorkWorkflow(question)) return undefined
  const requestedContext = [question, ...[...priorUserQuestions].reverse()]
    .find(text => /\b(?:data[\s-]+agent|ontology|fabric[\s-]+iq)\b/i.test(text))
  if (!requestedContext) return 'The operator has not requested the published Data Agent or Fabric IQ in this conversation. Use Gauge for ordinary inventory, telemetry, SQL work and charts; do not add an unnecessary native delegation.'
  const dataAgent = /\bdata[\s-]+agent\b/i.test(requestedContext)
  const ontology = /\bontology\b/i.test(requestedContext)
  if (ontology && !dataAgent) return 'Direct ontology-native chat execution is disabled. Use the published Data Agent or governed direct sources.'
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

export function parseDelegation(args: string): { specialist: Exclude<AgentRole, 'supervisor'>; question: string; reason?: string; nativeSource?: NativeSource; requiresSelection: boolean; requiresVerifiedIssue: boolean } {
  const value: unknown = JSON.parse(args)
  if (!value || typeof value !== 'object' || !('specialist' in value) || !('question' in value)
    || typeof value.question !== 'string' || !value.question.trim()) throw new Error('Invalid specialist delegation.')
  const specialist = value.specialist
  if (specialist !== 'qa' && specialist !== 'rca' && specialist !== 'work-order' && specialist !== 'fabric-iq' && specialist !== 'weather') {
    throw new Error('Unknown Foundry specialist.')
  }
  if ('reason' in value && (typeof value.reason !== 'string' || !value.reason.trim())) throw new Error('Invalid delegation reason.')
  if (!('requires_selection' in value) || typeof value.requires_selection !== 'boolean') throw new Error('Invalid delegation selection dependency.')
  if (!('requires_verified_issue' in value) || typeof value.requires_verified_issue !== 'boolean'
    || (value.requires_verified_issue && specialist !== 'work-order')) throw new Error('Invalid delegation verified-issue dependency.')
  if ('native_source' in value && value.native_source !== null && value.native_source !== 'data-agent') {
    throw new Error('Unsupported native source. Sparky accepts only data-agent.')
  }
  return { specialist, question: value.question,
    requiresSelection: value.requires_selection,
    requiresVerifiedIssue: value.requires_verified_issue,
    ...('native_source' in value && value.native_source === 'data-agent' ? { nativeSource: value.native_source } : {}),
    ...('reason' in value && typeof value.reason === 'string' ? { reason: value.reason.trim() } : {}) }
}
