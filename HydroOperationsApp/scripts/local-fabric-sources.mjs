import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { parseEnv } from 'node:util'
import { pathToFileURL } from 'node:url'
import { ConnectorsRayfinClient, RayfinClient } from '@microsoft/rayfin-client'
import { signInWithEntraToken } from '@microsoft/rayfin-auth-provider-fabric'
import { buildTelemetryQuery, buildStationPowerQuery, stationPowerEvidence, STATION_POWER_SEMANTICS, fleetSnapshotQuery, shapeFleetSnapshot, kustoRowsToObjects, MAX_ROWS, shapeCatalogRows, TOOL_DEFINITIONS, validateFilters, validateKql } from '../src/services/copilot/query.ts'
import { ASSET_ENTITIES, KUSTO_SOURCE_NAMES, OPERATIONS_ENTITIES, WEATHER_ENTITIES } from '../src/services/copilot/catalog.ts'
import { lakehouseConnectorConfig } from '../src/services/lakehouseConnector.ts'
import { operationalConnectorConfig } from '../src/services/operationalConnector.ts'
import { describeToolEvidence, DOMAIN_CONTRACT_VERSION, DOMAIN_DATASETS, DOMAIN_RELATIONSHIPS, DOMAIN_SEMANTICS, shapeWorkBacklog } from '../src/services/copilot/domainContract.ts'
import { parseRcaAssessment, RCA_REPORT_TOOL, RcaEvidenceError } from '../src/services/copilot/rcaEvidence.ts'
import { AGENT_NAMES, DIRECT_TOOLS, WEATHER_TOOLS, agentDefinition, parseDelegation, parseHydroQuery, parseWorkOrderReview, requestedNativeSources, verifyNativeReceipt, WORK_REVIEW_TOOL } from '../src/services/copilot/agentDefinitions.ts'
import { createWorkOrderProposal, equipmentSelectionForRequest, missingRequestedSpecialists, positiveActionClauses, qualityWorkWorkflow, refersToPriorData, requestedLookback, requestedQualityStates, requestedRankLimit, requestedWorkBacklogGroup, requestsSignalQuality, requestsWeather, requestsWorkOrderNeedAssessment, requiredIndependentQaSteps, requiredSpecialists, requiresChartOutput, workOrderPriorityForRequest } from '../src/services/copilot/orchestration.ts'
import { parseKustoPayload } from '../src/services/kustoResult.ts'
export { parseKustoPayload } from '../src/services/kustoResult.ts'

const fabric = 'https://api.fabric.microsoft.com/v1'
const guid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i

export function governedToolResult(tool, args, result) {
  return { ...result, _evidence_datasets: describeToolEvidence(tool, args, result) }
}

export function chatIntent(question, carriedKinds = []) {
  if (typeof question !== 'string' || !question.trim() || question.length > 8000) {
    throw new Error('Chat intent requires a bounded nonempty operator question.')
  }
  const nativeSources = requestedNativeSources(question)
  const specialists = requiredSpecialists(question, nativeSources.length > 0)
  // Required evidence always needs a reader: never compile an empty plan for a question that asks for data.
  if (!specialists.length && requiredEvidenceKinds(question, carriedKinds).some(kind => kind !== 'native_claims')) specialists.push('qa')
  return { charts_requested: requiresChartOutput(question), native_sources: nativeSources,
    proposal_priority: workOrderPriorityForRequest(question),
    equipment_selection: equipmentSelectionForRequest(question) ?? null,
    required_evidence_kinds: requiredEvidenceKinds(question, carriedKinds),
    permitted_evidence_kinds: permittedEvidenceKinds(question, carriedKinds),
    required_specialists: specialists,
    required_specialist_steps: Object.fromEntries(specialists.map(role => [
      role, role === 'qa' ? requiredIndependentQaSteps(question) : 1,
    ])) }
}

/** Intent for a follow-up in a conversation whose previous run already produced verified evidence of the given
 *  kinds. `reuse` is true only when the question points back at that data, every kind it needs is already held,
 *  and no specialist would still have to re-read it. Otherwise the intent is exactly the standalone one. */
export function chatFollowUp(question, carriedKinds) {
  const carried = [...new Set(carriedKinds)]
  const standalone = chatIntent(question)
  if (!carried.length || !refersToPriorData(question)) return { intent: standalone, reuse: false }
  const intent = chatIntent(question, carried)
  const needed = intent.required_evidence_kinds.filter(kind => kind !== 'native_claims')
  if (!needed.length || !needed.every(kind => carried.includes(kind)) || intent.native_sources.length) {
    return { intent: standalone, reuse: false }
  }
  const explicitQa = missingRequestedSpecialists(question, []).includes('qa')
  const others = intent.required_specialists.filter(role => role !== 'qa')
  if (!others.length || explicitQa) return { intent: standalone, reuse: false }
  return { reuse: true, intent: { ...intent, required_specialists: others,
    required_specialist_steps: Object.fromEntries(others.map(role => [role, 1])) } }
}

/** A "which work needs raising" question needs the condition evidence and the existing open-work cover. When the
 *  conversation already holds condition evidence it points back at, that is used instead of fresh defaults. */
function addNeedAssessmentKinds(question, kinds, carriedKinds) {
  kinds.add('work_orders')
  if (kinds.has('signal_quality') || kinds.has('temperature')) return
  const held = carriedKinds.filter(kind => kind === 'signal_quality' || kind === 'temperature')
  for (const kind of held.length && refersToPriorData(question) ? held : ['signal_quality', 'temperature']) kinds.add(kind)
}

export function requiredEvidenceKinds(question, carriedKinds = []) {
  if (qualityWorkWorkflow(question)) return ['native_claims']
  const text = positiveActionClauses(question)
  const kinds = new Set()
  const weather = requestsWeather(question)
  if (weather) {
    kinds.add('weather_locations')
    if (/\b(?:areas?|volume)\b/i.test(text)) kinds.add('weather_areas')
    const observations = /\b(?:observations?|observed|current weather|weather now)\b/i.test(text)
    if (observations) kinds.add('weather_observations')
    if (!observations || /\bforecasts?\b/i.test(text)) kinds.add('weather_forecasts')
  }
  if (requestedNativeSources(question).length) kinds.add('native_claims')
  if (requestsSignalQuality(question)) kinds.add('signal_quality')
  if ((!weather || /\b(?:turbine|equipment|telemetry|sensor)\b/i.test(text))
      && /\b(?:temperature|temperatures|hot|hottest)\b/i.test(text)) kinds.add('temperature')
  if (/\b(?:station power|power output per station)\b/i.test(text)) kinds.add('station_power')
  if (/\b(?:work[\s-]*orders?|wos?|open(?: operational SQL| SQL)? work|existing work|work records?|maintenance work)\b/i.test(text)
      || /\bwork\b[^.!?;\n]{0,40}\b(?:already\s+)?open\b/i.test(text)) {
    kinds.add('work_orders')
  }
  if (requestedWorkBacklogGroup(text)) kinds.add('work_backlog')
  if (/\b(?:list facility instances|direct (?:asset|facility)|asset records?)\b/i.test(text)
      || /\bresolve\b[^.!?;\n]{0,120}\bfacilit(?:y|ies)\b/i.test(text)) {
    kinds.add('facilities')
  }
  if (/\b(?:resolve|check)\b[^.!?;\n]{0,100}\b(?:equipment )?identity\b|\bdirect\b[^.!?;\n]{0,50}\b(?:asset|equipment)\b/i.test(text)) {
    kinds.add('equipment')
  }
  if (/\b(?:signal identity|signal identities|available signals?)\b/i.test(text)) kinds.add('instruments')
  if (!/\btelemetry snapshots?\b/i.test(text)
      && /\b(?:inspect|investigate|read|query|verify|using|use)\b[^.!?;\n]{0,180}\b(?:telemetry|raw readings?)\b/i.test(text)) {
    kinds.add('telemetry')
  }
  if (/\binspections\b|\binspection (?:evidence|history|records|results)\b/i.test(text)) kinds.add('inspections')
  if (/\bread\b[^.!?;\n]{0,80}\bnotifications?\b/i.test(text)) kinds.add('notifications')
  if (/\blist\b[^.!?;\n]{0,80}\bspare parts?\b/i.test(text)) kinds.add('spare_parts')
  if (/\b(?:show|read|list)\b[^.!?;\n]{0,80}\b(?:asset models?|3d models?)\b/i.test(text)) kinds.add('asset_models')
  if (requestsWorkOrderNeedAssessment(question)) addNeedAssessmentKinds(question, kinds, carriedKinds)
  return [...kinds]
}

export function permittedEvidenceKinds(question, carriedKinds = []) {
  const text = positiveActionClauses(question)
  const kinds = new Set()
  if (requestsWeather(question)) for (const entity of WEATHER_ENTITIES) kinds.add(entity.key)
  if (requestedNativeSources(question).length) kinds.add('native_claims')
  if (/\bfacilit(?:y|ies)\b/i.test(text)) kinds.add('facilities')
  if (/\b(?:assets?|equipment|turbines?|generators?|units?|T\d{1,3})\b/i.test(text)) kinds.add('equipment')
  if (/\b(?:instrument|signal identity|signal identities|available signals?)\b/i.test(text)) kinds.add('instruments')
  if (/\b(?:telemetry|readings?|signals?|power|temperature|quality)\b/i.test(text)) kinds.add('telemetry')
  if (requestsSignalQuality(question)
      || /\b(?:quality (?:BAD|GOOD|UNCERTAIN)|BAD signals?|UNCERTAIN signals?|BAD alone)\b/i.test(text)) {
    kinds.add('signal_quality')
  }
  if (/\b(?:RCA|investigate|diagnos(?:e|is)|physical fault|equipment condition|downtime)\b/i.test(text)) {
    kinds.add('telemetry')
  }
  if (/\b(?:temperature|temperatures|hot|hottest)\b/i.test(text)) kinds.add('temperature')
  if (/\b(?:station power|power output per station)\b/i.test(text)) kinds.add('station_power')
  if (/\b(?:work[\s-]*orders?|wos?|open(?: operational SQL| SQL)? work|existing work|work records?|maintenance work|backlog)\b/i.test(text)
      || /\bwork\b[^.!?;\n]{0,40}\b(?:already\s+)?open\b/i.test(text)) {
    kinds.add('work_orders')
  }
  if (requestedWorkBacklogGroup(text)) kinds.add('work_backlog')
  if (/\binspections\b|\binspection (?:evidence|history|records|results)\b/i.test(text)) kinds.add('inspections')
  if (/\bnotifications?\b/i.test(text)) kinds.add('notifications')
  if (/\b(?:spare parts?|parts stock|reorder level)\b/i.test(text)) kinds.add('spare_parts')
  if (/\b(?:asset models?|3d models?)\b/i.test(text)) kinds.add('asset_models')
  if (requestsWorkOrderNeedAssessment(question)) addNeedAssessmentKinds(question, kinds, carriedKinds)
  return [...kinds]
}

export function analyticalEvidencePlan(question, answerKinds) {
  if (typeof question !== 'string' || !Array.isArray(answerKinds)) {
    throw new Error('Analytical evidence planning requires a question and evidence kinds.')
  }
  const kinds = new Set(answerKinds)
  const text = positiveActionClauses(question)
  const lookback = requestedLookback(question)
  const plan = []
  if (kinds.has('signal_quality')) {
    const qualities = [...new Set([
      ...requestedQualityStates(question),
      ...[...text.matchAll(/\b(BAD|GOOD|UNCERTAIN)\b/gi)].map(match => match[1].toUpperCase()),
    ])]
    for (const quality of qualities.length ? qualities : ['BAD']) {
      const arguments_ = { quality, ...(lookback ? { lookback } : {}),
        ...(/\bturbines?\b/i.test(text) ? { equipment_type: 'turbine' } : {}) }
      plan.push({ kind: 'signal_quality', tool: 'query_signal_quality_snapshot',
        arguments: arguments_,
        match_arguments: {
          ...(qualities.length ? { quality } : {}),
          ...(lookback ? { lookback } : {}),
          ...(/\bturbines?\b/i.test(text) ? { equipment_type: 'turbine' } : {}),
        } })
    }
  }
  if (kinds.has('temperature')) {
    const limit = requestedRankLimit(question)
    const arguments_ = { ...(lookback ? { lookback } : {}), ...(limit === undefined ? {} : { limit }) }
    plan.push({ kind: 'temperature', tool: 'query_turbine_temperature_snapshot',
      arguments: arguments_, match_arguments: arguments_ })
  }
  if (kinds.has('station_power')) {
    const arguments_ = { ...(lookback ? { lookback } : {}) }
    plan.push({ kind: 'station_power', tool: 'query_station_power',
      arguments: arguments_, match_arguments: arguments_ })
  }
  if (kinds.has('telemetry')) {
    const arguments_ = { ...(lookback ? { lookback } : {}), aggregation: 'latest' }
    plan.push({ kind: 'telemetry', tool: 'query_telemetry',
      arguments: arguments_, match_arguments: arguments_ })
  }
  if (kinds.has('work_backlog')) {
    const group = requestedWorkBacklogGroup(question)
    if (group) plan.push({ kind: 'work_backlog', tool: 'query_work_backlog',
      arguments: { group_by: group }, match_arguments: { group_by: group } })
  }
  return plan
}

function defaultEvidenceScope(kind, question) {
  const dataset = DOMAIN_DATASETS.find(candidate => candidate.key === kind)
  if (!dataset) throw new Error(`Cannot compile an ungoverned evidence kind: ${kind}.`)
  if (dataset.evidenceScopes.length === 1) return dataset.evidenceScopes[0]
  const text = positiveActionClauses(question)
  if (kind === 'native_claims') {
    return /\b(?:all|every|inventory|list facility instances|which turbines)\b/i.test(text)
      ? 'population' : 'focus'
  }
  if (kind === 'work_orders') {
    return /\b(?:every|inventory|backlog|on each|by equipment|facility[- ]level)\b/i.test(text)
      || /\b(?:for|on|against)\s+(?:these|those|them|all of them)\b/i.test(text)
      || requestsWorkOrderNeedAssessment(question)
      ? 'population' : 'focus'
  }
  if (kind === 'equipment') {
    return /\b(?:all|every|inventory|direct asset records?|facility[- ]level)\b/i.test(text)
      ? 'population' : 'focus'
  }
  return 'focus'
}

function chartRequestedForKind(kind, chartsRequested, question) {
  if (!chartsRequested) return false
  const chartText = positiveActionClauses(question)
    .split(/[.!?;\n]+/)
    .filter(clause => /\b(?:chart|plot|graph|histogram|dashboard|visuali[sz]e)\b/i.test(clause))
    .join(' ')
  if (kind === 'work_backlog') {
    return /\b(?:backlog|orders?[- ]per[- ]equipment|facility[- ]level)\b/i.test(chartText)
  }
  if (kind === 'station_power') return /\b(?:station power|power output per station)\b/i.test(chartText)
  if (kind === 'temperature') return /\b(?:temperature|temperatures|hot|hottest)\b/i.test(chartText)
  if (kind === 'telemetry') {
    // A chart request names a measure ("power output", "vibration", ...) rather than the word telemetry, so the
    // series is charted unless the clause belongs to another governed chartable dataset.
    const claimedElsewhere = ['work_backlog', 'station_power', 'temperature']
      .some(other => other !== kind && chartRequestedForKind(other, chartsRequested, question))
    return Boolean(chartText.trim()) && !claimedElsewhere
  }
  return false
}

function defaultPresentation(kind, authority, chartsRequested, question) {
  const dataset = DOMAIN_DATASETS.find(candidate => candidate.key === kind)
  if (!dataset) throw new Error(`Cannot compile an ungoverned evidence kind: ${kind}.`)
  let presentation = 'records_table'
  if (kind === 'work_backlog') {
    const group = requestedWorkBacklogGroup(question)
    if (!group) throw new Error('Required work backlog evidence has no governed grouping intent.')
    presentation = `grouped_by_${group}_${chartsRequested ? 'bar' : 'table'}`
  } else if (kind === 'station_power' && chartsRequested) {
    presentation = 'records_bar'
  } else if (kind === 'temperature' && chartsRequested) {
    presentation = 'records_bar'
  } else if (kind === 'telemetry' && chartsRequested) {
    presentation = 'records_line'
  }
  if (!dataset.presentations.includes(presentation)) {
    throw new Error(`No governed presentation can compile required ${kind} evidence.`)
  }
  return `${authority}_${presentation}`
}

export function compileAnswerContract(question, chartsRequested, requiredKinds, contract) {
  if (typeof question !== 'string' || typeof chartsRequested !== 'boolean'
      || !Array.isArray(requiredKinds) || !contract || typeof contract !== 'object'
      || !Array.isArray(contract.requirements) || !Array.isArray(contract.views)) {
    throw new Error('Answer contract compilation requires a governed request and typed contract.')
  }
  const requirements = structuredClone(contract.requirements)
  for (const kind of requiredKinds) {
    const dataset = DOMAIN_DATASETS.find(candidate => candidate.key === kind)
    if (!dataset) throw new Error(`Cannot compile an ungoverned evidence kind: ${kind}.`)
    const scope = defaultEvidenceScope(kind, question)
    const authority = dataset.evidenceAuthorities[0]
    let requirement = requirements.find(candidate =>
      typeof candidate?.evidence === 'string' && candidate.evidence.startsWith(`${kind}_`))
    if (!requirement) {
      requirement = {
        evidence: `${kind}_${scope}_${authority}`,
        purpose: ['equipment', 'facilities', 'instruments'].includes(kind) ? 'identity'
          : ['work_orders', 'work_backlog'].includes(kind) ? 'coverage' : 'facts',
      }
      requirements.push(requirement)
    } else {
      requirement.evidence = `${kind}_${scope}_${authority}`
    }
  }
  const views = structuredClone(contract.views).map(view => {
    if (view?.presentation === 'reconciliation_table'
        || typeof view?.kind !== 'string' || typeof view?.presentation !== 'string') {
      return view
    }
    const dataset = DOMAIN_DATASETS.find(candidate => candidate.key === view.kind)
    if (!dataset) return view
    const authority = view.presentation.startsWith('native_') ? 'native' : 'direct'
    const requirement = requirements.find(candidate =>
      typeof candidate?.evidence === 'string'
      && candidate.evidence.startsWith(`${view.kind}_`)
      && candidate.evidence.endsWith(`_${authority}`))
    if (!requirement) return view
    const match = /^([a-z][a-z0-9_]*)_(population|focus)_(direct|native)$/.exec(requirement.evidence)
    if (!match) throw new Error(`Required ${view.kind} evidence has an invalid governed identity.`)
    return {
      ...view,
      scope: match[2],
      presentation: defaultPresentation(
        view.kind,
        authority,
        chartRequestedForKind(view.kind, chartsRequested, question),
        question,
      ),
    }
  })
  for (const kind of requiredKinds) {
    const requirement = requirements.find(candidate =>
      typeof candidate?.evidence === 'string' && candidate.evidence.startsWith(`${kind}_`))
    if (!requirement) throw new Error(`Required ${kind} evidence was not compiled.`)
    if (!views.some(candidate => candidate?.kind === kind)) {
      const match = /^([a-z][a-z0-9_]*)_(population|focus)_(direct|native)$/.exec(requirement.evidence)
      if (!match) throw new Error(`Required ${kind} evidence has an invalid governed identity.`)
      views.push({
        id: `required-${kind.replaceAll('_', '-')}`,
        title: kind.split('_').map(value => value[0].toUpperCase() + value.slice(1)).join(' '),
        kind,
        scope: match[2],
        presentation: defaultPresentation(
          kind,
          match[3],
          chartRequestedForKind(kind, chartsRequested, question),
          question,
        ),
      })
    }
  }
  if (requirements.length > 16 || views.length > 8) {
    throw new Error('Compiled answer contract exceeds its bounded size.')
  }
  return { version: DOMAIN_CONTRACT_VERSION, requirements, views }
}

function sourceClause(question, source) {
  if (source !== 'data-agent') throw new Error('Sparky supports only the published Fabric Data Agent.')
  if (qualityWorkWorkflow(question)) {
    return 'Ask the published Fabric Data Agent bound to the selected generation-2 ontology which turbines had any BAD telemetry quality from midnight UTC today through requested_at_utc supplied in the context, and what work is already open for those same turbines. Use the ontology static and time-series bindings and the attached operational SQL source. Had BAD means any BAD sample in that window, not only signals whose latest sample is BAD. Return a JSON object with window_start_utc, window_end_utc, affected_signals, open_work_orders, and coverage. Each affected_signals row must include canonical equipment_id, opcua_node_id, bad_sample_count, first_bad_utc and last_bad_utc; include turbine tag and instrument_id when available. Each open_work_orders row must include equipment_id, work_order_number and status, plus title, priority and signal ID when available; exclude Completed and Cancelled work. coverage must include telemetry_complete and work_complete booleans plus affected_signal_count and open_work_order_count matching the complete returned arrays. Preserve affected turbines with no open work. Empty arrays are valid only after successful complete reads; never turn an unavailable source into zero rows or invent completeness. Use the exact requested_at_utc as window_end_utc. Report actual read times and source references when available; do not invent provenance or identifiers. Keep this delegation read-only: do not do RCA, recommend unrelated maintenance, or create/draft work orders. If source access, binding, identity stitching or coverage fails, report that failure instead of substituting another source.'
  }
  return positiveActionClauses(question)
    .split(/(?<=[.!?;\n])/)
    .map(value => value.trim())
    .find(value => /\bdata[\s-]+agent\b/i.test(value))
    ?? 'Use the requested published Fabric Data Agent.'
}

export function validateQualityWorkEvidence(question, result, requestedAt) {
  if (!qualityWorkWorkflow(question)) return result
  const candidates = new Map()
  const visit = value => {
    if (value && typeof value === 'object') {
      if (!Array.isArray(value) && 'affected_signals' in value && 'open_work_orders' in value) {
        candidates.set(JSON.stringify(value), value)
      } else {
        for (const child of Object.values(value)) visit(child)
      }
    } else if (typeof value === 'string') {
      const text = value.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, '$1')
      let parsed
      try { parsed = JSON.parse(text) } catch (error) {
        if (!(error instanceof SyntaxError)) throw error
        return
      }
      if (parsed && typeof parsed === 'object') visit(parsed)
    }
  }
  visit(result)
  if (candidates.size !== 1) throw new Error('Data Agent quality/work evidence requires one inspectable JSON bundle; no direct-source fallback was performed.')
  const bundle = [...candidates.values()][0]
  const signals = bundle.affected_signals
  const orders = bundle.open_work_orders
  const coverage = bundle.coverage
  const end = Date.parse(requestedAt)
  const start = new Date(end)
  start.setUTCHours(0, 0, 0, 0)
  if (!Number.isFinite(end) || Date.parse(bundle.window_start_utc) !== start.getTime()
      || Date.parse(bundle.window_end_utc) !== end) {
    throw new Error('Data Agent quality/work evidence does not match the requested UTC day and as-of time.')
  }
  if (!Array.isArray(signals) || !Array.isArray(orders) || signals.length > 500 || orders.length > 500
      || coverage?.telemetry_complete !== true || coverage?.work_complete !== true
      || coverage.affected_signal_count !== signals.length || coverage.open_work_order_count !== orders.length) {
    throw new Error('Data Agent quality/work coverage is incomplete, unbounded or inconsistent; missing data cannot be reported as zero.')
  }
  const identifier = value => typeof value === 'string' && Boolean(value.trim()) && value.length <= 200
  const equipment = new Set()
  const signalKeys = new Set()
  for (const row of signals) {
    const first = Date.parse(row?.first_bad_utc)
    const last = Date.parse(row?.last_bad_utc)
    if (!identifier(row?.equipment_id) || !identifier(row?.opcua_node_id)
        || !Number.isSafeInteger(row?.bad_sample_count) || row.bad_sample_count < 1
        || !Number.isFinite(first) || !Number.isFinite(last)
        || first < start.getTime() || last > end || first > last) {
      throw new Error('Data Agent BAD-quality rows require canonical IDs, positive sample counts and timestamps within the requested window.')
    }
    const key = JSON.stringify([row.equipment_id, row.opcua_node_id])
    if (signalKeys.has(key)) throw new Error('Data Agent BAD-quality evidence contains duplicate equipment/signal rows.')
    signalKeys.add(key)
    equipment.add(row.equipment_id)
  }
  const orderKeys = new Set()
  for (const row of orders) {
    if (!equipment.has(row?.equipment_id) || !identifier(row?.work_order_number)
        || !identifier(row?.status) || ['completed', 'cancelled', 'canceled'].includes(row.status.trim().toLowerCase())
        || orderKeys.has(row.work_order_number)) {
      throw new Error('Data Agent open work must have unique order IDs, open statuses and an affected equipment ID.')
    }
    orderKeys.add(row.work_order_number)
  }
  return { ...bundle, native_source_output: result }
}

function requiresFinalQa(question) {
  const text = positiveActionClauses(question)
  const verification = /\b(?:finally\s*,?\s*)?independently\s+(?:check|verify|reconcile)\b/i.exec(text)
  if (!verification) return false
  if (/^\s*finally\b/i.test(verification[0])) return true
  const priorWork = [...text.matchAll(/\b(?:investigate|diagnose|prepare|propose|draft)\b/gi)]
    .some(match => (match.index ?? text.length) < (verification.index ?? 0))
  return priorWork
}

function requiresVerifiedIssueGate(question) {
  const text = positiveActionClauses(question)
  return /\b(?:only\s+(?:for|if|when)|unless)\b[^.!?;\n]{0,180}\b(?:evidence[- ]backed|demonstrated|specific|verified|supported|warranted|uncovered)\b[^.!?;\n]{0,120}\b(?:gap|issue|work|draft|warrant)/i.test(text)
    || /\b(?:prepare|propose|draft)\b[^.!?;\n]{0,180}\bonly\s+if\b[^.!?;\n]{0,180}\b(?:warrant|support|verify|confirm|establish)/i.test(text)
}

function partitionEvidenceKinds(kinds, count) {
  const groups = Array.from({ length: count }, () => [])
  kinds.forEach((kind, index) => groups[index % count].push(kind))
  return groups
}

const NAMED_EQUIPMENT = /\b(?:EQUIP_RTI_T?\d+|T\d{1,3}|(?:turbine|unit|equipment|asset|generator)\s*(?:number|no\.?|#)?\s*\d+)\b/i

export function compileOrchestrationPlan(question, intent = chatIntent(question)) {
  if (!intent || typeof intent !== 'object'
      || !Array.isArray(intent.required_specialists)
      || !intent.required_specialist_steps || typeof intent.required_specialist_steps !== 'object'
      || !Array.isArray(intent.native_sources)
      || !Array.isArray(intent.required_evidence_kinds)
      || typeof intent.charts_requested !== 'boolean') {
    throw new Error('Orchestration compilation requires one governed chat intent.')
  }
  const qualityWorkflow = qualityWorkWorkflow(question)
  const requiredKinds = [...intent.required_evidence_kinds]
  if (intent.native_sources.length && !requiredKinds.includes('native_claims')) {
    requiredKinds.push('native_claims')
  }
  if (!requiredKinds.length && intent.required_specialists.includes('rca')) requiredKinds.push('telemetry')
  if (!requiredKinds.length && intent.required_specialists.includes('work-order')) {
    requiredKinds.push('equipment', 'work_orders')
  }
  if (!requiredKinds.length && intent.required_specialists.includes('qa')) requiredKinds.push('telemetry')
  const directKinds = requiredKinds.filter(kind => kind !== 'native_claims')
  const reconciliationKind = /\breconcil(?:e|iation)\b/i.test(positiveActionClauses(question))
    && intent.native_sources.length
    ? directKinds.find(kind => defaultEvidenceScope(kind, question) === 'population')
    : undefined
  const seedContract = {
    version: DOMAIN_CONTRACT_VERSION,
    requirements: [],
    views: reconciliationKind ? [{
      id: 'source-reconciliation',
      title: 'Source reconciliation',
      kind: reconciliationKind,
      scope: defaultEvidenceScope(reconciliationKind, question),
      presentation: 'reconciliation_table',
    }] : [],
  }
  const answerContract = compileAnswerContract(
    question, intent.charts_requested, requiredKinds, seedContract)
  const qaCount = Number(intent.required_specialist_steps.qa
    ?? (intent.required_specialists.includes('qa') ? 1 : 0))
  const finalQa = qaCount > 0 && requiresFinalQa(question)
  const initialQaCount = qaCount - (finalQa ? 1 : 0)
  const weatherKinds = directKinds.filter(kind => kind.startsWith('weather_'))
  const qaGroups = partitionEvidenceKinds(directKinds.filter(kind => !kind.startsWith('weather_')), Math.max(1, initialQaCount))
  const selectedFocus = Boolean(intent.equipment_selection)
  const conditionalWork = requiresVerifiedIssueGate(question)
  const steps = []
  for (const source of intent.native_sources) {
    steps.push({
      specialist: 'fabric-iq',
      question: sourceClause(question, source),
      reason: qualityWorkflow
        ? 'Retrieve the shared BAD-quality and existing-work evidence once through the ontology-connected Data Agent.'
        : `Read only the operator-requested ${source} facts using the bound native source.`,
      native_source: source,
      requires_selection: false,
      requires_verified_issue: false,
    })
  }
  if (intent.required_specialists.includes('weather')) {
    steps.push({
      specialist: 'weather',
      question: `Read the requested ${weatherKinds.join(', ')} with the operator's UTC window and facility/area filters. Keep vendors and forecast issues separate, retain units and source timestamps, and report missing or stale coverage.`,
      reason: 'Retrieve weather evidence through the governed read-only Lakehouse connector.',
      native_source: null, requires_selection: false, requires_verified_issue: false,
    })
  }
  for (let index = 0; index < initialQaCount; index += 1) {
    const kinds = qaGroups[index]
    steps.push({
      specialist: 'qa',
      question: `Read and verify the requested direct ${kinds.join(', ')} evidence with the operator's filters, time window, identities, and completeness requirements.`,
      reason: initialQaCount > 1
        ? `Verify independent direct evidence population ${index + 1} of ${initialQaCount}.`
        : 'Read the requested current operational facts from governed direct sources.',
      native_source: null,
      requires_selection: false,
      requires_verified_issue: false,
    })
  }
  if (intent.required_specialists.includes('rca')) {
    steps.push({
      specialist: 'rca',
      question: 'Investigate the requested condition for the equipment the verified evidence identifies. Use the supplied receipts first (signal-quality history, inspections, notifications, spare parts, open work), read more only for those same equipment, and clearly separate observations, hypotheses, and missing evidence.',
      reason: 'Perform the requested bounded source-referenced investigation.',
      native_source: null,
      requires_selection: selectedFocus,
      requires_verified_issue: false,
    })
  }
  if (intent.required_specialists.includes('work-order')) {
    steps.push({
      specialist: 'work-order',
      question: qualityWorkflow
        ? 'Reuse the supplied Data Agent evidence for all affected canonical equipment IDs, and the RCA report when requested. Do not rerun fleet-wide telemetry retrieval or choose unrelated equipment. Check current complete open-work coverage in operational SQL for each candidate, matching equipment and signal/scope where available; Data Agent output alone does not certify SQL coverage. BAD telemetry quality is not proof of a physical equipment fault. Review whether a signal-quality investigation is justified and already covered; stage editable diagnostic/inspection drafts only for evidenced uncovered needs, with no invented physical repair or confirmed root cause. Return no_draft with covering work and reasons when appropriate. Do not execute production writes; every staged draft requires human approval.'
        : conditionalWork
        ? 'Review verified work coverage and stage an editable draft only for a verified uncovered equipment issue.'
        : (initialQaCount > 0 || intent.evidence_carried === true) && !NAMED_EQUIPMENT.test(positiveActionClauses(question))
          ? 'The operator named no equipment, so choose the target from the supplied verified evidence: the highest-priority equipment with a verified concern and no open work plausibly covering it. Confirm its identity and complete open-work coverage, then prepare the requested editable draft for it. If every affected equipment is already covered, conclude no_draft and name the covering work.'
          : 'Review verified identity and work coverage, then prepare the explicitly requested editable draft.',
      reason: 'Perform the requested governed work review without executing a production write.',
      native_source: null,
      requires_selection: conditionalWork,
      requires_verified_issue: conditionalWork,
    })
  }
  if (finalQa) {
    steps.push({
      specialist: 'qa',
      question: 'Independently verify the selected equipment and signal identity, source coverage, and any proposed draft against the immutable receipts.',
      reason: 'Perform the requested final independent verification after specialist work.',
      native_source: null,
      requires_selection: false,
      requires_verified_issue: false,
    })
  }
  if (!steps.length || steps.length > 8) {
    throw new Error('Compiled orchestration plan must contain between one and eight bounded steps.')
  }
  return { steps, answer_contract: answerContract }
}

export function validatePlanRequirements(required, requiredSteps, steps, nativeSources,
  question = '', answerKinds = [], requiredEvidenceKinds_ = [],
  permittedEvidenceKinds_ = permittedEvidenceKinds(question)) {
  if (!Array.isArray(required) || !requiredSteps || typeof requiredSteps !== 'object'
      || Array.isArray(requiredSteps) || !Array.isArray(steps) || !Array.isArray(nativeSources)
      || typeof question !== 'string' || !Array.isArray(answerKinds) || !Array.isArray(requiredEvidenceKinds_)
      || !Array.isArray(permittedEvidenceKinds_)
      || required.some(role => !['qa', 'rca', 'work-order', 'weather'].includes(role))) {
    throw new Error('Plan requirements need verified required specialists, steps and native sources.')
  }
  const roles = steps.map(step => step?.specialist).filter(role => typeof role === 'string')
  const missingSteps = Object.fromEntries(Object.entries(requiredSteps)
    .filter(([role, count]) => !['qa', 'rca', 'work-order', 'weather'].includes(role)
      || !Number.isInteger(count) || count < 1 || count > 8
      || roles.filter(candidate => candidate === role).length < count))
  const plannedNative = new Set(steps
    .filter(step => step?.specialist === 'fabric-iq')
    .map(step => step?.native_source)
    .filter(source => source === 'data-agent'))
  return {
    missing_specialists: required.filter(role => !roles.includes(role)),
    missing_specialist_steps: missingSteps,
    missing_native_sources: nativeSources.filter(source => !plannedNative.has(source)),
    missing_evidence_kinds: requiredEvidenceKinds_.filter(kind => !answerKinds.includes(kind)),
    unexpected_evidence_kinds: answerKinds.filter(kind => !permittedEvidenceKinds_.includes(kind)),
  }
}

class WorkOrderSubmissionError extends Error {
  constructor(message, writeAttempted) {
    super(message)
    this.writeAttempted = writeAttempted
  }
}

export function sourceToolContracts() {
  return {
    tools: TOOL_DEFINITIONS.map(({ function: definition }) => {
      const parameters = structuredClone({ ...definition.parameters, additionalProperties: false })
      const entities = definition.name === 'query_assets' ? ASSET_ENTITIES
        : definition.name === 'query_operations' ? OPERATIONS_ENTITIES
          : definition.name === 'query_weather' ? WEATHER_ENTITIES : undefined
      if (entities) {
        parameters.allOf = entities.map(entity => ({
          if: { properties: { entity: { const: entity.key } }, required: ['entity'] },
          then: { properties: {
            columns: { minItems: 1, uniqueItems: true, items: { enum: entity.columns.map(column => column.name) } },
            where: { items: { properties: {
              column: { enum: entity.columns.map(column => column.name) },
              value_column: { enum: entity.columns.map(column => column.name) },
            } } },
          } },
        }))
        parameters.properties.where.items.additionalProperties = false
      }
      if (parameters.properties.limit) {
        parameters.properties.limit.minimum = 1
        parameters.properties.limit.maximum = MAX_ROWS
      }
      return { name: definition.name, parameters }
    }),
    context: {
      asset_entities: structuredClone(ASSET_ENTITIES),
      operations_entities: structuredClone(OPERATIONS_ENTITIES),
      weather_entities: structuredClone(WEATHER_ENTITIES),
      kusto_source_names: [...KUSTO_SOURCE_NAMES],
      relationships: structuredClone(DOMAIN_RELATIONSHIPS),
      semantics: structuredClone(DOMAIN_SEMANTICS),
      domain_contract_version: DOMAIN_CONTRACT_VERSION,
      datasets: structuredClone(DOMAIN_DATASETS),
    },
  }
}

export async function collectCatalogRows(entity, loadPage) {
  const rows = [], cursors = new Set(), identities = new Set()
  const identityColumns = entity.identityColumns ?? [entity.identityColumn]
  if (!identityColumns.length || identityColumns.some(key => !key || !entity.columns.some(column => column.name === key))) {
    throw new Error('Catalog entity has no valid declared identity column.')
  }
  let cursor
  for (let pageNumber = 0; pageNumber < MAX_ROWS / 100; pageNumber++) {
    const page = await loadPage(cursor)
    if (!page || !Array.isArray(page.items) || page.items.length > 100 || typeof page.hasNextPage !== 'boolean') {
      throw new Error('Catalog source returned an invalid page.')
    }
    for (const row of page.items) {
      const values = identityColumns.map(key => row?.[key] instanceof Date ? row[key].toISOString() : row?.[key])
      const identity = JSON.stringify(values)
      if (!row || typeof row !== 'object' || Array.isArray(row)
        || values.some(value => typeof value !== 'string' || !value.trim()) || identities.has(identity)) {
        throw new Error('Catalog source returned an invalid or duplicate row identity.')
      }
      identities.add(identity)
      rows.push(row)
    }
    if (!page.hasNextPage) return rows
    if (typeof page.endCursor !== 'string' || !page.endCursor || cursors.has(page.endCursor)) {
      throw new Error('Catalog source returned an invalid continuation.')
    }
    cursors.add(page.endCursor)
    cursor = page.endCursor
  }
  throw new Error('Catalog source exceeds its complete-read bound; partial evidence is not accepted.')
}

function validateCatalogArguments(entity, args) {
  validateFilters(args.where, entity.columns.map(column => column.name))
  if (args.columns !== undefined && (!Array.isArray(args.columns) || !args.columns.length
    || args.columns.some(column => typeof column !== 'string' || !entity.columns.some(item => item.name === column))
    || new Set(args.columns).size !== args.columns.length)) {
    throw new Error('Requested columns must be distinct declared catalog columns.')
  }
  if (args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > MAX_ROWS)) {
    throw new Error('Requested row limit is outside the catalog read bound.')
  }
}

export async function readAssetEntity(config, entityKey, args, token) {
  const entity = ASSET_ENTITIES.find(candidate => candidate.key === entityKey)
  if (!entity) throw new Error('Unknown asset catalog entity.')
  validateCatalogArguments(entity, args)
  const rows = await readAssetInventory(config, entity, token)
  const shaped = shapeCatalogRows(entity, rows, args, false, true)
  return { ...shaped.result, _row_identities: shaped.rowIdentities, read_completed_at_utc: new Date().toISOString() }
}

const identityText = value => String(value ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')
const identityNumber = value => {
  const match = identityText(value).match(/(\d+)$/)
  return match ? Number(match[1]) : null
}
const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const facilityRoot = value => String(value ?? '').toLowerCase()
  .replace(/\b(?:hydro(?:electric)?|power|station|plant|facility)\b/g, ' ')
  .replace(/[^a-z0-9]+/g, ' ').trim()

export function resolveIdentityScope(question, equipmentRows, facilityRows, source, readCompletedAt) {
  if (typeof question !== 'string' || !question.trim() || question.length > 8000) {
    throw new Error('Semantic identity resolution requires one bounded operator question.')
  }
  const mentions = []
  const addMention = (kind, mention, numeric = null) => {
    const key = `${kind}:${identityText(mention)}`
    if (!mentions.some(item => item.key === key)) mentions.push({ key, kind, mention, numeric })
  }
  for (const match of question.matchAll(/\bEQUIP_RTI_T0*(\d+)\b/gi)) addMention('equipment', match[0], Number(match[1]))
  for (const match of question.matchAll(/\bT0*(\d+)\b/gi)) addMention('equipment', match[0], Number(match[1]))
  for (const match of question.matchAll(/\b(?:turbine|equipment|asset)\s*(?:number|no\.?|#)?\s*0*(\d+)\b/gi)) {
    addMention('equipment', match[0], Number(match[1]))
  }
  for (const match of question.matchAll(/\bFACILITY_RTI_0*(\d+)\b/gi)) addMention('facility', match[0], Number(match[1]))
  for (const match of question.matchAll(/\b(?:facility|station|power\s+plant)\s*(?:number|no\.?|#)?\s*0*(\d+)\b/gi)) {
    addMention('facility', match[0], Number(match[1]))
  }
  for (const row of facilityRows) {
    const root = facilityRoot(row.facility_name)
    const pattern = root && `\\b${escapePattern(root).replace(/\\ /g, '\\s+')}\\b`
    if (pattern && new RegExp(pattern, 'i').test(question)) addMention('facility', row.facility_name)
  }
  if (!mentions.length) return { matched: false }

  const matches = mentions.map(item => {
    const rows = item.kind === 'equipment' ? equipmentRows : facilityRows
    const candidates = rows.filter(row => {
      if (item.numeric !== null) {
        return item.kind === 'equipment'
          ? [row.equipment_id, row.tag].some(value => identityNumber(value) === item.numeric)
          : identityNumber(row.facility_id) === item.numeric
      }
      const root = facilityRoot(item.mention)
      return item.kind === 'facility' && root && facilityRoot(row.facility_name) === root
    })
    const canonical = new Map(candidates.map(row => [
      item.kind === 'equipment' ? row.equipment_id : row.facility_id, row,
    ]))
    if (canonical.size !== 1) {
      const ids = [...canonical.keys()].slice(0, 10)
      throw new Error(canonical.size
        ? `Identity '${item.mention}' is ambiguous across ${ids.join(', ')}. Use one canonical ID.`
        : `Identity '${item.mention}' does not match the authoritative asset or facility inventory.`)
    }
    const [canonicalId, row] = [...canonical.entries()][0]
    const canonicalMention = identityText(item.mention) === identityText(canonicalId)
    return {
      kind: item.kind,
      mention: item.mention,
      canonical_id: canonicalId,
      label: item.kind === 'equipment' ? String(row.tag || canonicalId) : String(row.facility_name || canonicalId),
      facility_id: item.kind === 'equipment' ? row.facility_id : canonicalId,
      matched_by: canonicalMention ? 'canonical_id'
        : item.numeric !== null && identityText(item.mention).startsWith('t') ? 'tag'
          : item.numeric !== null ? 'number' : 'name',
    }
  })
  return {
    source,
    matches: [...new Map(matches.map(match => [`${match.kind}:${match.canonical_id}`, match])).values()],
    read_completed_at: readCompletedAt,
  }
}

async function readAssetInventory(config, entity, token) {
  const connectorEntity = { facilities: 'SilverFacilities', equipment: 'SilverEquipment', instruments: 'SilverInstruments' }[entity.key]
  if (!connectorEntity) throw new Error('Unknown Lakehouse asset connector entity.')
  return operationalRead(config, token, 'Lakehouse asset connector', client =>
    collectCatalogRows(entity, async cursor => {
      let query = client.connectors.hydrolakehouse[connectorEntity]
        .select(entity.columns.map(column => column.name)).first(100)
      if (cursor) query = query.after(cursor)
      return query.executePaginated()
    }))
}

export async function collectOperationRows(client, entityKey, args) {
  const entity = OPERATIONS_ENTITIES.find(candidate => candidate.key === entityKey)
  if (!entity) throw new Error('Unknown operational catalog entity.')
  validateCatalogArguments(entity, args)
  const rows = await collectOperationInventory(client, entity)
  const shaped = shapeCatalogRows(entity, rows, args, false, true)
  return { ...shaped.result, _row_identities: shaped.rowIdentities, read_completed_at_utc: new Date().toISOString() }
}

async function collectOperationInventory(client, entity) {
  return collectCatalogRows(entity, async cursor => {
    let query = client.connectors.hydrooperations[entity.apiName ?? entity.physicalName].select(entity.columns.map(column => column.name))
      .orderBy({ [entity.identityColumn]: 'asc' }).first(100)
    if (cursor) query = query.after(cursor)
    return query.executePaginated()
  })
}

export async function readTelemetryQuery(metadata, query, token, fetcher = fetch) {
  const payload = await requestJson(`${metadata.cluster}/v1/rest/query`, token, {
    db: metadata.database, csl: query,
    properties: { Options: { truncationmaxrecords: MAX_ROWS + 1, servertimeout: '00:00:25' } },
  }, fetcher)
  const table = parseKustoPayload(payload)
  const columns = table.Columns.map(column => column.ColumnName)
  if (table.Rows.length > MAX_ROWS) {
    throw new Error('Telemetry query exceeds its source row bound.')
  }
  const rows = kustoRowsToObjects(columns, table.Rows)
  return { rows, row_count: rows.length, truncated: false,
    read_completed_at_utc: new Date().toISOString() }
}

/** Raw telemetry carries no unit. Attach the registered instrument unit to each row whose node maps to exactly one
 *  instrument with a nonempty unit; rows that cannot be attested are left untouched. */
export function attachInstrumentUnits(rows, instruments) {
  const units = new Map()
  const ambiguous = new Set()
  for (const instrument of instruments) {
    const node = instrument?.opcua_node_id
    if (typeof node !== 'string' || !node) continue
    if (units.has(node)) ambiguous.add(node)
    const unit = typeof instrument.unit === 'string' ? instrument.unit.trim() : ''
    if (unit) units.set(node, unit)
  }
  return rows.map(row => {
    const node = row?.opcua_node_id
    const unit = typeof node === 'string' && !ambiguous.has(node) ? units.get(node) : undefined
    return unit && !Object.hasOwn(row, 'unit') ? { ...row, unit } : row
  })
}

export async function readTelemetryWithUnits(config, metadata, query, tokens, fetcher = fetch) {
  const [reading, instruments] = await Promise.allSettled([
    readTelemetryQuery(metadata, query, tokens.kusto, fetcher),
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'instruments'), tokens.graphql),
  ])
  if (reading.status === 'rejected') throw reading.reason
  // Unit metadata is enrichment: a failed metadata read leaves the series valid but unattested.
  if (instruments.status === 'rejected') return reading.value
  return { ...reading.value, rows: attachInstrumentUnits(reading.value.rows, instruments.value) }
}

export async function readStationPower(metadata, args, token, fetcher = fetch) {
  const lookback = args.lookback ?? '24h'
  const reading = await readTelemetryQuery(metadata, buildStationPowerQuery(lookback), token, fetcher)
  const evidence = stationPowerEvidence(reading.rows, lookback)
  return { rows: evidence.rows, row_count: evidence.rows.length, truncated: false, lookback,
    presentation: { table_field: 'rows',
      chart: { kind: 'bar', x_key: 'Station', y_keys: ['average_power_MW'] } },
    semantics: STATION_POWER_SEMANTICS, read_completed_at_utc: reading.read_completed_at_utc }
}

export function runtimeConfiguration(input) {
  const keys = ['tenant_id', 'workspace_id', 'ontology_id', 'eventhouse_id', 'database_id', 'lakehouse_id', 'appbackend_id', 'operational_sql_database_id', 'api_url', 'publishable_key']
  if (!input || typeof input !== 'object' || Array.isArray(input)
    || Object.keys(input).length !== keys.length || Object.keys(input).some(key => !keys.includes(key))
    || keys.slice(0, 8).some(key => typeof input[key] !== 'string' || !guid.test(input[key]))
    || typeof input.publishable_key !== 'string' || !/^pk-\S+$/.test(input.publishable_key)
    || typeof input.api_url !== 'string') {
    throw new Error('Explicit runtime source configuration is missing or invalid.')
  }
  let api
  try { api = new URL(input.api_url) }
  catch { throw new Error('Explicit runtime AppBackend endpoint is invalid.') }
  if (api.protocol !== 'https:' || api.username || api.password || api.port || api.search || api.hash
    || !/^[0-9a-f]{32}\.pbidedicated\.windows\.net$/.test(api.hostname)) {
    throw new Error('Explicit runtime AppBackend endpoint is not a capacity endpoint.')
  }
  const config = Object.fromEntries(keys.map(key => [key, input[key]]))
  return { ...config, configuration_digest: createHash('sha256').update(JSON.stringify(config)).digest('hex') }
}

export async function configuration() {
  if (process.env.HYDRO_FABRIC_SOURCE_CONFIG !== undefined) {
    let input
    try { input = JSON.parse(process.env.HYDRO_FABRIC_SOURCE_CONFIG) }
    catch { throw new Error('Explicit runtime source configuration is not valid JSON.') }
    return runtimeConfiguration(input)
  }
  const env = parseEnv(await readFile(new URL('../rayfin/.env', import.meta.url), 'utf8'))
  const binding = JSON.parse(env.RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING)
  const identity = {
    tenant_id: env.RAYFIN_PUBLIC_TENANT_ID,
    workspace_id: env.RAYFIN_PUBLIC_WORKSPACE_ID,
    ontology_id: binding.ontologyId,
    eventhouse_id: env.RAYFIN_PUBLIC_EVENTHOUSE_ID,
    database_id: env.RAYFIN_PUBLIC_KQL_DATABASE_ID,
    lakehouse_id: env.RAYFIN_PUBLIC_LAKEHOUSE_ID,
    appbackend_id: env.RAYFIN_PUBLIC_ITEM_ID,
    operational_sql_database_id: env.RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID,
  }
  if (Object.values(identity).some(value => typeof value !== 'string' || !guid.test(value))
    || binding.workspaceId !== identity.workspace_id) throw new Error('Explicit source identities are missing or inconsistent.')
  const state = JSON.parse(await readFile(new URL('../rayfin/.deployments.json', import.meta.url), 'utf8'))
  const deployment = state.deployments?.[state.active]
  if (!env.RAYFIN_PUBLIC_API_URL || env.RAYFIN_PUBLIC_API_URL !== deployment?.fabricApiUrl
    || !env.RAYFIN_PUBLIC_PUBLISHABLE_KEY?.startsWith('pk-')
    || env.RAYFIN_PUBLIC_PUBLISHABLE_KEY !== deployment?.publishableKey) {
    throw new Error('Saved AppBackend endpoint/key do not match the active deployment.')
  }
  const config = { ...identity, api_url: env.RAYFIN_PUBLIC_API_URL, publishable_key: env.RAYFIN_PUBLIC_PUBLISHABLE_KEY }
  return { ...config, configuration_digest: createHash('sha256').update(JSON.stringify(config)).digest('hex') }
}

export async function requestJson(url, token, body, fetcher = fetch) {
  const response = await fetcher(url, {
    method: body ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
    redirect: 'error', signal: AbortSignal.timeout(30000),
  })
  if (!response.ok) throw new Error(`Source request failed: HTTP ${response.status} (${new URL(url).pathname}).`)
  return response.json()
}

export async function discover(config, token, fetcher = fetch) {
  const base = `${fabric}/workspaces/${config.workspace_id}`
  const [workspace, ontology, database, lakehouse, operationalStore] = await Promise.all([
    requestJson(base, token, undefined, fetcher),
    requestJson(`${base}/ontologies/${config.ontology_id}`, token, undefined, fetcher),
    requestJson(`${base}/kqlDatabases/${config.database_id}`, token, undefined, fetcher),
    requestJson(`${base}/lakehouses/${config.lakehouse_id}`, token, undefined, fetcher),
    requestJson(`${base}/sqlDatabases/${config.operational_sql_database_id}`, token, undefined, fetcher),
  ])
  if (workspace.id !== config.workspace_id || ontology.id !== config.ontology_id
    || ontology.properties?.generation !== 2 || database.id !== config.database_id
    || database.properties?.parentEventhouseItemId !== config.eventhouse_id
    || lakehouse.id !== config.lakehouse_id
    || (lakehouse.workspaceId && lakehouse.workspaceId !== config.workspace_id)
    || !guid.test(lakehouse.properties?.sqlEndpointProperties?.id ?? '')) {
    throw new Error('Live source identity or numeric ontology generation 2 verification failed.')
  }
  if (operationalStore.id !== config.operational_sql_database_id || operationalStore.type !== 'SQLDatabase'
    || (operationalStore.workspaceId && operationalStore.workspaceId !== config.workspace_id)) {
    throw new Error('Live operational SQL connector identity verification failed.')
  }
  if (!guid.test(workspace.capacityId ?? '')) throw new Error('Workspace has no verified capacity.')
  const api = new URL(config.api_url)
  const path = `/webapi/capacities/${workspace.capacityId}/workloads/baas/baasservice/automatic/v1/workspaces/${config.workspace_id}/appbackends/${config.appbackend_id}`
  if (api.protocol !== 'https:' || api.username || api.password || api.search || api.hash
    || api.hostname !== `${workspace.capacityId.replaceAll('-', '')}.pbidedicated.windows.net`.toLowerCase()
    || api.pathname.replace(/\/$/, '').toLowerCase() !== path.toLowerCase()) {
    throw new Error('Saved AppBackend endpoint does not match the live workspace capacity; use the canonical deployer.')
  }
  const cluster = new URL(database.properties.queryServiceUri)
  if (cluster.protocol !== 'https:' || cluster.username || cluster.password || cluster.search || cluster.hash
    || cluster.port || !['', '/'].includes(cluster.pathname)
    || !/\.(?:kusto\.fabric\.microsoft\.com|kusto\.windows\.net)$/.test(cluster.hostname)
    || typeof database.displayName !== 'string' || !database.displayName.trim()) {
    throw new Error('KQL metadata did not return a trusted query endpoint/database.')
  }
  return {
    source: { tenant_id: config.tenant_id, workspace_id: config.workspace_id,
      ontology_id: config.ontology_id, generation: 2, configuration_digest: config.configuration_digest },
    cluster: cluster.origin, database: database.displayName,
  }
}

export async function readTelemetry(config, metadata, equipmentId, tokens, fetcher = fetch) {
  const [equipmentInventory, instrumentInventory] = await Promise.all([
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'equipment'), tokens.graphql),
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'instruments'), tokens.graphql),
  ])
  const equipment = equipmentInventory.filter(row => row.equipment_id === equipmentId)
  const instruments = instrumentInventory.filter(row => row.equipment_id === equipmentId)
  if (equipment.length !== 1 || equipment[0].equipment_id !== equipmentId || !instruments.length
    || instruments.length > 100 || instruments.some(item => item.equipment_id !== equipmentId
      || typeof item.opcua_node_id !== 'string' || !item.opcua_node_id
      || typeof item.unit !== 'string' || !item.unit.trim())
    || new Set(instruments.map(item => item.opcua_node_id)).size !== instruments.length) {
    throw new Error('Equipment/signal identity or measurement units are missing, ambiguous or out of bounds.')
  }
  const nodes = instruments.map(item => item.opcua_node_id)
  const query = buildTelemetryQuery({ opcua_node_ids: nodes, aggregation: 'latest', lookback: '24h', limit: 101 })
  const queryStartedAt = Date.now()
  const result = await requestJson(`${metadata.cluster}/v1/rest/query`, tokens.kusto, {
    db: metadata.database, csl: query,
    properties: { Options: { truncationmaxrecords: 101, servertimeout: '00:00:25' } },
  }, fetcher)
  const table = parseKustoPayload(result)
  const columns = table.Columns.map(item => item.ColumnName)
  const required = ['opcua_node_id', 'event_time', 'value', 'quality']
  if (required.some(column => !columns.includes(column)) || new Set(columns).size !== columns.length
    || table.Rows.some(row => !Array.isArray(row) || row.length !== columns.length)) {
    throw new Error('KQL columns/rows do not match the telemetry contract.')
  }
  const rows = kustoRowsToObjects(columns, table.Rows)
  if (!rows.length || rows.length > nodes.length || new Set(rows.map(row => row.opcua_node_id)).size !== rows.length) {
    throw new Error('KQL returned no measurements or an invalid latest-signal result.')
  }
  const readTime = new Date()
  const observations = rows.map(row => {
    const instrument = instruments.find(item => item.opcua_node_id === row.opcua_node_id)
    if (!instrument || typeof row.value !== 'number' || !Number.isFinite(row.value)
      || !['GOOD', 'BAD', 'UNCERTAIN'].includes(row.quality) || typeof row.event_time !== 'string'
      || !/Z$/.test(row.event_time) || !Number.isFinite(Date.parse(row.event_time))
      || Date.parse(row.event_time) > readTime.getTime()
      || Date.parse(row.event_time) < queryStartedAt - 24 * 3600000) {
      throw new Error('Telemetry identity, value, quality or timestamp is invalid.')
    }
    return { evidence_id: row.opcua_node_id, metric: row.opcua_node_id, value: row.value,
      unit: instrument.unit, event_time: row.event_time, quality: row.quality }
  })
  const gaps = []
  if (observations.length !== nodes.length) gaps.push('measurements_for_some_mapped_signals')
  if (observations.some(row => Date.parse(row.event_time) < readTime.getTime() - 30 * 60000)) gaps.push('fresh_telemetry')
  return { observations, missing_sources: gaps, read_completed_at: readTime.toISOString() }
}

export async function collectWorkOrders(client, equipmentId) {
  const items = []
  const cursors = new Set()
  let cursor
  for (let pageNumber = 0; pageNumber < 5; pageNumber++) {
    let query = client.connectors.hydrooperations.WorkOrder.select(['id', 'workOrderNumber', 'equipmentId', 'instrumentId', 'opcuaNodeId', 'title', 'description', 'status', 'priority'])
      .where({ equipmentId: { eq: equipmentId } }).orderBy({ id: 'asc' }).first(100)
    if (cursor) query = query.after(cursor)
    const page = await query.executePaginated()
    if (!Array.isArray(page.items) || page.items.length > 100 || typeof page.hasNextPage !== 'boolean') throw new Error('Invalid work-order page.')
    items.push(...page.items)
    if (items.some(item => item.equipmentId !== equipmentId || typeof item.status !== 'string' || !item.status.trim()
      || typeof item.workOrderNumber !== 'string' || !item.workOrderNumber || typeof item.id !== 'string' || !item.id)
      || new Set(items.map(item => item.id)).size !== items.length
      || new Set(items.map(item => item.workOrderNumber)).size !== items.length) throw new Error('Invalid or duplicate work-order identity.')
    if (!page.hasNextPage) return {
      rows: items.filter(item => !['completed', 'cancelled'].includes(item.status.trim().toLowerCase())),
      read_completed_at: new Date().toISOString(),
    }
    if (typeof page.endCursor !== 'string' || !page.endCursor || cursors.has(page.endCursor)) throw new Error('Invalid work-order continuation.')
    cursors.add(page.endCursor)
    cursor = page.endCursor
  }
  throw new Error('Work-order read exceeds 500 rows; partial coverage is not accepted.')
}

async function operationalRead(config, token, label, read) {
  const client = new ConnectorsRayfinClient({ baseUrl: `${config.api_url.replace(/\/$/, '')}/`,
    publishableKey: config.publishable_key, authStorage: false,
    connectors: { hydrolakehouse: lakehouseConnectorConfig, hydrooperations: operationalConnectorConfig } })
  try {
    await signInWithEntraToken(client.auth, { entraToken: token })
    return await read(client)
  } catch (error) {
    const code = typeof error?.code === 'string' && /^[A-Z_0-9]+$/.test(error.code) ? error.code : 'READ_FAILED'
    throw new Error(`Rayfin ${label} read failed (${code}). No empty-work fallback or proposal is permitted.`)
  } finally {
    client.auth.destroy()
  }
}

export async function readWork(config, equipmentId, token) {
  return operationalRead(config, token, 'work-order', client => collectWorkOrders(client, equipmentId))
}

export async function readOperationEntity(config, entityKey, args, token) {
  const entity = OPERATIONS_ENTITIES.find(candidate => candidate.key === entityKey)
  if (!entity) throw new Error('Unknown operational catalog entity.')
  validateCatalogArguments(entity, args)
  return operationalRead(config, token, entityKey, client => collectOperationRows(client, entityKey, args))
}

export async function collectWeatherRows(client, entityKey, args) {
  const entity = WEATHER_ENTITIES.find(candidate => candidate.key === entityKey)
  if (!entity) throw new Error('Unknown weather catalog entity.')
  validateCatalogArguments(entity, args)
  const connectorEntity = {
    weather_locations: 'WeatherLocations', weather_areas: 'WeatherAreas',
    weather_forecasts: 'WeatherLatestForecasts', weather_observations: 'WeatherLatestObservations',
  }[entityKey]
  const rows = await collectCatalogRows(entity, async cursor => {
    let query = client.connectors.hydrolakehouse[connectorEntity]
      .select(entity.columns.map(column => column.name)).first(100)
    if (cursor) query = query.after(cursor)
    return query.executePaginated()
  })
  const columns = args.columns
    ? [...new Set([...(entity.identityColumns ?? [entity.identityColumn]), ...args.columns])]
    : undefined
  return { ...shapeCatalogRows(entity, rows, { ...args, columns }).result, read_completed_at_utc: new Date().toISOString() }
}

export async function readWeatherEntity(config, entityKey, args, token) {
  return operationalRead(config, token, 'weather connector', client => collectWeatherRows(client, entityKey, args))
}

export async function readWorkBacklog(config, args, token,
  readWorkInventory = () => operationalRead(config, token, 'work backlog',
    client => collectOperationInventory(client, OPERATIONS_ENTITIES.find(entity => entity.key === 'work_orders')))) {
  const readStartedAt = new Date().toISOString()
  const reads = await Promise.allSettled([
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'facilities'), token),
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'equipment'), token),
    readWorkInventory(),
  ])
  const labels = ['facility inventory', 'equipment inventory', 'work-order inventory']
  const failures = reads.flatMap((read, index) => {
    if (read.status === 'fulfilled') return []
    const code = typeof read.reason?.code === 'string' && /^[A-Z_0-9]+$/.test(read.reason.code)
      ? read.reason.code : 'READ_FAILED'
    return [`${labels[index]} failed (${code})`]
  })
  if (failures.length) throw new Error(`${failures.join('; ')}. No partial facility backlog is permitted.`)
  const [facilities, equipment, workOrders] = reads.map(read => read.value)
  return {
    ...shapeWorkBacklog(args.group_by, facilities, equipment, workOrders),
    presentation: {
      table_field: 'rows',
      chart: {
        kind: 'bar',
        x_key: args.group_by === 'facility' ? 'facility_id' : 'equipment_id',
        y_keys: ['open_work_order_count'],
      },
    },
    read_started_at_utc: readStartedAt,
    read_completed_at_utc: new Date().toISOString(),
  }
}

export async function readFleetSnapshot(config, metadata, temperature, args, tokens, fetcher = fetch,
  readWorkInventory = () => operationalRead(config, tokens.graphql, 'work-order snapshot',
    client => collectOperationInventory(client, OPERATIONS_ENTITIES.find(entity => entity.key === 'work_orders')))) {
  const query = fleetSnapshotQuery(temperature, args)
  const readStartedAt = new Date().toISOString()
  const labels = ['equipment metadata', 'instrument metadata', 'work-order inventory', 'telemetry']
  const reads = await Promise.allSettled([
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'equipment'), tokens.graphql),
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'instruments'), tokens.graphql),
    readWorkInventory(),
    readTelemetryQuery(metadata, query, tokens.kusto, fetcher),
  ])
  const failures = reads.flatMap((read, index) => {
    if (read.status === 'fulfilled') return []
    const code = typeof read.reason?.code === 'string' && /^[A-Z_0-9]+$/.test(read.reason.code)
      ? read.reason.code : 'READ_FAILED'
    return [`${labels[index]} failed (${code})`]
  })
  if (failures.length) throw new Error(`${failures.join('; ')}. No partial fleet snapshot is permitted.`)
  const [equipment, instruments, workOrders, telemetry] = reads.map(read => read.value)
  return shapeFleetSnapshot(temperature, args, {
    equipment, instruments, inventoryComplete: true, telemetryRows: telemetry.rows, workOrders,
    workInventoryComplete: true, readStartedAt, readCompletedAt: new Date().toISOString(),
  }).result
}

export async function stageWorkOrder(config, args, priority, tokens,
  readExisting = () => readWork(config, args.equipment_id, tokens.graphql)) {
  const proposal = createWorkOrderProposal({
    equipmentId: args.equipment_id, instrumentId: args.instrument_id, opcuaNodeId: args.opcua_node_id,
    title: args.title, description: args.description, priority,
  })
  const [equipment, instruments, work] = await Promise.all([
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'equipment'), tokens.graphql),
    proposal.instrumentId || proposal.opcuaNodeId
      ? readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'instruments'), tokens.graphql)
      : Promise.resolve([]),
    readExisting(),
  ])
  const matches = equipment.filter(row => row.equipment_id === proposal.equipmentId && row.is_active === true)
  if (matches.length !== 1) throw new Error('Draft equipment is not uniquely verified as active in this workspace.')
  if (proposal.instrumentId || proposal.opcuaNodeId) {
    const signals = instruments.filter(row => row.equipment_id === proposal.equipmentId && row.is_active === true
      && (!proposal.instrumentId || row.instrument_id === proposal.instrumentId)
      && (!proposal.opcuaNodeId || row.opcua_node_id === proposal.opcuaNodeId))
    if (signals.length !== 1) throw new Error('Draft signal is not uniquely bound to the active selected equipment.')
    proposal.instrumentId = signals[0].instrument_id
    proposal.opcuaNodeId = signals[0].opcua_node_id
  }
  if (!work || !Array.isArray(work.rows) || typeof work.read_completed_at !== 'string'
    || !Number.isFinite(Date.parse(work.read_completed_at))) throw new Error('Draft requires a complete work-source read.')
  if (work.rows.some(row => row.equipmentId !== proposal.equipmentId || typeof row.id !== 'string' || !row.id
    || typeof row.workOrderNumber !== 'string' || !row.workOrderNumber
    || typeof row.title !== 'string' || !row.title.trim() || typeof row.status !== 'string' || !row.status.trim()
    || ['completed', 'cancelled'].includes(row.status.trim().toLowerCase()))
    || new Set(work.rows.map(row => row.id)).size !== work.rows.length) {
    throw new Error('Draft work coverage contains invalid or unrelated open work.')
  }
  return { proposal, existing_work: work.rows, work_read_at: work.read_completed_at,
    staged: true, confirmation_required: true, production_write_executed: false,
    read_completed_at_utc: new Date().toISOString() }
}

export async function submitApprovedWorkOrder(config, draft, edits, principalId, creationId, tokens, allowCreate,
  useClient) {
  if (!guid.test(principalId) || !guid.test(creationId) || typeof allowCreate !== 'boolean') {
    throw new Error('Work-order submission requires verified principal and creation identities.')
  }
  const checked = createWorkOrderProposal({
    equipmentId: draft.equipment_id, instrumentId: draft.instrument_id ?? undefined,
    opcuaNodeId: draft.opcua_node_id ?? undefined,
    title: edits.title, description: edits.description, priority: edits.priority,
  })
  const expected = { id: creationId, workOrderNumber: `WO-${creationId}`, equipmentId: checked.equipmentId,
    instrumentId: checked.instrumentId ?? null, opcuaNodeId: checked.opcuaNodeId ?? null,
    title: checked.title, description: checked.description, priority: checked.priority,
    status: 'Draft', createdByOid: principalId }
  let writeAttempted = false
  const execute = async client => {
    const readBack = async () => {
      const rows = await client.connectors.hydrooperations.WorkOrder.select(Object.keys(expected))
        .where({ id: { eq: creationId } }).first(2).execute()
      if (!Array.isArray(rows) || rows.length > 1) throw new Error('Work-order creation identity is ambiguous.')
      if (!rows.length) return undefined
      const row = rows[0]
      if (Object.entries(expected).some(([key, value]) => (row[key] ?? null) !== value)) {
        throw new Error('Work-order readback does not match the approved fields and signed-in principal.')
      }
      return row
    }
    const existing = await readBack()
    if (existing) return { record: existing, reconciled: true, read_completed_at_utc: new Date().toISOString() }
    if (!allowCreate) throw new Error('Prior submission remains unconfirmed; reconciliation will not repeat a SQL write.')
    const fresh = await stageWorkOrder(config, {
      equipment_id: checked.equipmentId, instrument_id: checked.instrumentId, opcua_node_id: checked.opcuaNodeId,
      title: checked.title, description: checked.description,
    }, checked.priority, tokens, () => collectWorkOrders(client, checked.equipmentId))
    if (fresh.existing_work.some(row => row.title?.trim().toLowerCase() === checked.title.trim().toLowerCase())) {
      throw new WorkOrderSubmissionError('An open work order with this title already exists for this equipment. No SQL write was attempted.', false)
    }
    try {
      writeAttempted = true
      await client.connectors.hydrooperations.WorkOrder.create({ ...expected, instrumentId: checked.instrumentId,
        opcuaNodeId: checked.opcuaNodeId, createdAt: new Date() })
    } catch (error) {
      const reconciled = await readBack()
      if (reconciled) return { record: reconciled, reconciled: true, read_completed_at_utc: new Date().toISOString() }
      throw new Error('SQL creation could not be confirmed; do not submit another write without reconciliation.', { cause: error })
    }
    const record = await readBack()
    if (!record) throw new Error('SQL creation returned without matching readback; its outcome is unconfirmed.')
    return { record, reconciled: false, read_completed_at_utc: new Date().toISOString() }
  }
  if (useClient) return execute(await useClient())
  const client = new RayfinClient({ baseUrl: `${config.api_url.replace(/\/$/, '')}/`,
    publishableKey: config.publishable_key, authStorage: false })
  try {
    await signInWithEntraToken(client.auth, { entraToken: tokens.graphql })
    return await execute(client)
  } catch (error) {
    if (error instanceof WorkOrderSubmissionError) throw error
    const code = typeof error?.code === 'string' && /^[A-Z_0-9]+$/.test(error.code) ? error.code : 'SUBMISSION_UNCONFIRMED'
    throw new WorkOrderSubmissionError(writeAttempted
      ? `Work-order submission could not be confirmed (${code}); reconcile before any new write.`
      : `Work-order source validation failed (${code}); no SQL write was attempted.`, writeAttempted)
  } finally {
    client.auth.destroy()
  }
}

async function main() {
  let input = ''
  for await (const chunk of process.stdin) {
    input += chunk
    if (input.length > 262144) throw new Error('Local adapter request exceeds its input bound.')
  }
  return handle(JSON.parse(input))
}

const DISCOVERY_TTL_MS = 60_000
const discoveryCache = new Map()

// Source-identity verification (workspace, ontology generation, KQL database) is re-verified at most once per
// TTL for the same configuration and the same bearer token. Failures are evicted so they are never replayed, and
// no analytical data is held here.
function discoverCached(config, token) {
  const now = Date.now()
  for (const [key, entry] of discoveryCache) if (entry.expires <= now) discoveryCache.delete(key)
  const key = createHash('sha256').update(`${config.configuration_digest}\0${token}`).digest('hex')
  const hit = discoveryCache.get(key)
  if (hit) return hit.promise
  const promise = discover(config, token)
  const entry = { expires: now + DISCOVERY_TTL_MS, promise }
  discoveryCache.set(key, entry)
  if (discoveryCache.size > 16) discoveryCache.delete(discoveryCache.keys().next().value)
  promise.catch(() => { if (discoveryCache.get(key) === entry) discoveryCache.delete(key) })
  return promise
}

export async function handle(request, deps = {}) {
  const submitApproved = deps.submitApprovedWorkOrder ?? submitApprovedWorkOrder
  if (request.action === 'chat_intent') {
    if (request.carried_kinds === undefined) return chatIntent(request.question)
    if (!Array.isArray(request.carried_kinds) || request.carried_kinds.length > 40
        || request.carried_kinds.some(kind => typeof kind !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(kind))) {
      throw new Error('Carried evidence kinds must be a bounded list of governed identifiers.')
    }
    const { intent, reuse } = chatFollowUp(request.question, request.carried_kinds)
    return { ...intent, reuse_prior_evidence: reuse }
  }
  if (request.action === 'analytical_evidence_plan') {
    return { entries: analyticalEvidencePlan(request.question, request.answer_kinds) }
  }
  if (request.action === 'compile_answer_contract') {
    return compileAnswerContract(
      request.question, request.charts_requested, request.required_evidence_kinds, request.answer_contract)
  }
  if (request.action === 'compile_orchestration_plan') {
    return compileOrchestrationPlan(request.question, request.intent)
  }
  if (request.action === 'work_backlog_group') {
    return { group_by: requestedWorkBacklogGroup(request.question) ?? null }
  }
  if (request.action === 'validate_plan_requirements') {
    return validatePlanRequirements(
      request.required_specialists, request.required_specialist_steps, request.steps, request.native_sources,
      request.question, request.answer_kinds, request.required_evidence_kinds, request.permitted_evidence_kinds)
  }
  if (request.action === 'source_contracts') return sourceToolContracts()
  if (request.action === 'rca_contract') return { tool: RCA_REPORT_TOOL }
  if (request.action === 'validate_rca') return parseRcaAssessment(request.report, request.receipts)
  if (request.action === 'work_review_contract') return { tool: WORK_REVIEW_TOOL }
  if (request.action === 'validate_work_review') return parseWorkOrderReview(request.report)
  if (request.action === 'agent_contracts') return {
    names: AGENT_NAMES,
    tools: Object.fromEntries(['qa', 'rca', 'work-order', 'weather']
      .map(role => [role, agentDefinition(role, 'contract-only').tools])),
  }
  if (request.action === 'validate_delegation') return parseDelegation(request.report)
  if (request.action === 'validate_native_receipt') {
    if (!verifyNativeReceipt(request.receipt, request.source)) throw new Error('Native execution receipt is missing.')
    return { accepted: true }
  }
  if (request.action === 'validate_quality_work_evidence') {
    return { result: validateQualityWorkEvidence(request.question, request.result, request.requested_at_utc) }
  }
  if (request.action === 'validate_hydro_query') {
    const parsed = parseHydroQuery(request.report)
    if (!parsed.ok) throw new Error(parsed.error)
    const allowed = request.role === 'weather' ? WEATHER_TOOLS
      : request.role === 'work-order' ? [...DIRECT_TOOLS, 'propose_work_order'] : DIRECT_TOOLS
    if (!allowed.includes(parsed.toolName)) throw new Error('Tool is not permitted for this specialist.')
    return parsed
  }
  const config = await configuration()
  if (request.action === 'configuration') {
    const { api_url: _url, publishable_key: _key, ...identity } = config
    return identity
  }
  if (request.configuration_digest !== config.configuration_digest) throw new Error('Source configuration changed; restart the local source adapter.')
  const metadata = await discoverCached(config, request.tokens.fabric)
  if (request.action === 'discover') return metadata
  if (request.action === 'verify_lakehouse_connector') {
    const counts = Object.fromEntries(await Promise.all(ASSET_ENTITIES.map(async entity => {
      const rows = await readAssetInventory(config, entity, request.tokens.graphql)
      if (!rows.length) throw new Error(`Lakehouse connector returned no ${entity.key}; seeded source readiness failed.`)
      return [entity.key, rows.length]
    })))
    return { source: metadata.source, counts }
  }
  if (request.action === 'resolve_identity_scope') {
    const equipmentEntity = ASSET_ENTITIES.find(entity => entity.key === 'equipment')
    const facilityEntity = ASSET_ENTITIES.find(entity => entity.key === 'facilities')
    if (!equipmentEntity || !facilityEntity || typeof request.question !== 'string'
      || typeof request.tokens.graphql !== 'string' || !request.tokens.graphql) {
      throw new Error('Semantic identity resolver configuration is incomplete.')
    }
    const [equipment, facilities] = await Promise.all([
      readAssetInventory(config, equipmentEntity, request.tokens.graphql),
      readAssetInventory(config, facilityEntity, request.tokens.graphql),
    ])
    return resolveIdentityScope(request.question, equipment, facilities, metadata.source, new Date().toISOString())
  }
  if (request.action === 'approve_work_order') {
    if (request.human_approved !== true) throw new Error('A human approval is required outside agent tool execution.')
    const result = await submitApproved(config, request.draft, request.edits, request.principal_id,
      request.creation_id, request.tokens, request.allow_create)
    return {
      source: metadata.source,
      result,
      completed_at: result.read_completed_at_utc,
    }
  }
  if (request.action === 'tool_read') {
    const args = request.arguments
    const definition = TOOL_DEFINITIONS.find(tool => tool.function.name === request.tool)?.function
    if (!definition || !args || typeof args !== 'object' || Array.isArray(args)
      || Object.keys(args).some(key => !Object.hasOwn(definition.parameters.properties, key))) {
      throw new Error('Invalid source tool arguments.')
    }
    let result
    switch (request.tool) {
      case 'query_work_backlog':
        result = await readWorkBacklog(config, args, request.tokens.graphql)
        break
      case 'query_assets':
        result = await readAssetEntity(config, args.entity, args, request.tokens.graphql)
        break
      case 'query_operations':
        result = await readOperationEntity(config, args.entity, args, request.tokens.graphql)
        break
      case 'query_weather':
        result = await readWeatherEntity(config, args.entity, args, request.tokens.graphql)
        break
      case 'query_telemetry':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readTelemetryWithUnits(config, metadata, buildTelemetryQuery(args), request.tokens)
        break
      case 'query_station_power':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readStationPower(metadata, args, request.tokens.kusto)
        break
      case 'propose_work_order':
        result = await stageWorkOrder(config, args, request.proposal_priority, request.tokens)
        break
      case 'run_kql':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readTelemetryQuery(metadata, validateKql(args.query, KUSTO_SOURCE_NAMES), request.tokens.kusto)
        break
      case 'query_signal_quality_snapshot':
      case 'query_turbine_temperature_snapshot':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readFleetSnapshot(config, metadata, request.tool === 'query_turbine_temperature_snapshot',
          args, request.tokens)
        break
      default:
        throw new Error('This source tool has no configured backend implementation.')
    }
    return {
      source: metadata.source,
      result: governedToolResult(request.tool, args, result),
      completed_at: result.read_completed_at_utc,
    }
  }
  if (!['read', 'probe', 'telemetry_only'].includes(request.action) || typeof request.equipment_id !== 'string'
    || !request.equipment_id.trim() || request.equipment_id.length > 200 || request.cluster !== metadata.cluster) {
    throw new Error('Invalid local source request or changed KQL endpoint.')
  }
  if (request.action === 'telemetry_only') {
    const telemetry = await readTelemetry(config, metadata, request.equipment_id, request.tokens)
    return { ...telemetry, source: metadata.source, equipment_id: request.equipment_id, open_work_numbers: [],
      missing_sources: [...telemetry.missing_sources, 'work_orders_not_requested', 'inspections_not_requested'] }
  }
  const results = await Promise.allSettled([
    readTelemetry(config, metadata, request.equipment_id, request.tokens),
    readWork(config, request.equipment_id, request.tokens.graphql),
  ])
  const failures = results.flatMap((result, index) => result.status === 'rejected'
    ? [{ source: index === 0 ? 'stid_telemetry' : 'work_orders', message: result.reason.message }] : [])
  if (request.action === 'probe') {
    return { ready: failures.length === 0, source: metadata.source, failures,
      checks: results.map((result, index) => ({ source: index === 0 ? 'stid_telemetry' : 'work_orders',
        status: result.status === 'fulfilled' ? 'passed' : 'failed',
        ...(result.status === 'fulfilled' && index === 0
          ? { observation_count: result.value.observations.length, missing_sources: result.value.missing_sources } : {}) })) }
  }
  if (failures.length) throw new Error(failures.map(item => `${item.source}: ${item.message}`).join('; '))
  const [telemetry, work] = results.map(result => result.value)
  const completedAt = new Date().toISOString()
  return { ...telemetry, source: metadata.source, equipment_id: request.equipment_id,
    read_completed_at: completedAt, work_orders_read_at: work.read_completed_at,
    open_work_numbers: work.rows.map(item => item.workOrderNumber) }
}

function errorEnvelope(error) {
  // Do not serialize SDK exceptions, request headers, tokens or response bodies.
  const message = error instanceof RcaEvidenceError || error instanceof WorkOrderSubmissionError
    || (error instanceof Error && error.constructor === Error)
    ? error.message : 'Local source adapter failed; inspect source/authentication prerequisites.'
  return { ok: false, error: message,
    ...(error instanceof WorkOrderSubmissionError ? { write_attempted: error.writeAttempted } : {}) }
}

const RESULT_MARKER = '__HYDRO_RESULT__='

// Long-lived worker: one JSON request per line, answered by an id-tagged marker line. Requests run concurrently,
// so a slow source read never blocks pure contract validation.
async function serve() {
  const { createInterface } = await import('node:readline')
  const pending = new Set()
  const reply = (id, envelope) => process.stdout.write(`\n${RESULT_MARKER}${JSON.stringify({ id, ...envelope })}\n`)
  for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    if (!line.trim()) continue
    let id = null
    let work
    try {
      if (line.length > 262144) throw new Error('Local adapter request exceeds its input bound.')
      const message = JSON.parse(line)
      id = message.id
      if (!Number.isInteger(id) || !message.request || typeof message.request !== 'object') {
        throw new Error('Local adapter request envelope is invalid.')
      }
      work = handle(message.request)
    } catch (error) {
      reply(id, errorEnvelope(error))
      continue
    }
    const task = work.then(result => reply(id, { ok: true, result }), error => reply(id, errorEnvelope(error)))
      .finally(() => pending.delete(task))
    pending.add(task)
  }
  await Promise.allSettled(pending)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.includes('--serve')) {
    await serve()
  } else {
    try {
      process.stdout.write(`\n${RESULT_MARKER}${JSON.stringify({ ok: true, result: await main() })}`)
    } catch (error) {
      process.stdout.write(`\n${RESULT_MARKER}${JSON.stringify(errorEnvelope(error))}`)
      process.exitCode = 1
    }
  }
}
