export type EvidenceReceipt = {
  id: string
  tool: string
  entity?: string
  completedAt: string
  result: unknown
}

type EvidenceReference = { evidence_id: string; path: string }
const HYPOTHESES = {
  sensor_or_ingestion: 'Sensor, communication or ingestion issue',
  operating_conditions: 'Differences in operating conditions or load',
  equipment_condition: 'Physical equipment condition',
  sampling_or_quality: 'Sampling gaps or measurement-quality effects',
}
const GAPS = {
  fresh_measurements: 'Fresh, timestamped measurements and ingestion-health evidence',
  independent_measurement: 'An independently obtained measurement',
  matched_baseline: 'A baseline matched for load/operating regime, with coverage and dispersion',
  approved_limits: 'Versioned, engineering-approved diagnostic limits',
  operating_context: 'Dispatch, control state and operating-condition records',
  inspection_evidence: 'Qualified inspection evidence relevant to the signal and time window',
  maintenance_scope: 'Verified scope and closure evidence for existing maintenance work',
  parts_compatibility: 'Authoritative BOM, compatibility, reservation and lead-time evidence',
}
type Hypothesis = {
  category: keyof typeof HYPOTHESES
  supporting: EvidenceReference[]
  contradicting: EvidenceReference[]
  missing: Array<keyof typeof GAPS>
}
export type RcaAssessment = { observations: EvidenceReference[]; hypotheses: Hypothesis[] }

const referenceSchema = {
  type: 'object', properties: { evidence_id: { type: 'string' }, path: { type: 'string' } },
  required: ['evidence_id', 'path'], additionalProperties: false,
}
export const RCA_REPORT_TOOL = {
  type: 'function', name: 'complete_rca_assessment',
  description: 'Complete a source-referenced investigation. References use a returned evidence_id and a JSON pointer relative to its data, e.g. /rows/0. The app checks every reference and renders the observations itself. Hypotheses remain untested; no configured causal model or approved diagnostic limits exist. Do not supply free-text diagnoses, thresholds, confidence scores or baseline claims.',
  parameters: {
    type: 'object', properties: {
      observations: { type: 'array', items: referenceSchema },
      hypotheses: { type: 'array', items: {
        type: 'object', properties: {
          category: { type: 'string', enum: Object.keys(HYPOTHESES) },
          supporting: { type: 'array', items: referenceSchema },
          contradicting: { type: 'array', items: referenceSchema },
          missing: { type: 'array', items: { type: 'string', enum: Object.keys(GAPS) } },
        }, required: ['category', 'supporting', 'contradicting', 'missing'], additionalProperties: false,
      } },
    }, required: ['observations', 'hypotheses'], additionalProperties: false,
  }, strict: true,
}

export class RcaEvidenceError extends Error {}
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

function exactKeys(value: Record<string, unknown>, keys: string[]) {
  if (Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) {
    throw new RcaEvidenceError(`RCA fields must be exactly: ${keys.join(', ')}. Unverified narrative and thresholds are not accepted.`)
  }
}

function references(value: unknown, receipts: readonly EvidenceReceipt[]): EvidenceReference[] {
  if (!Array.isArray(value) || value.length > 12) throw new RcaEvidenceError('Use at most twelve evidence references per field.')
  return value.map(item => {
    if (!record(item)) throw new RcaEvidenceError('An evidence reference must be an object.')
    exactKeys(item, ['evidence_id', 'path'])
    if (typeof item.evidence_id !== 'string' || typeof item.path !== 'string') throw new RcaEvidenceError('Evidence IDs and paths must be strings.')
    const reference = { evidence_id: item.evidence_id, path: item.path }
    evidenceValue(reference, receipts)
    return reference
  })
}

function evidenceValue(reference: EvidenceReference, receipts: readonly EvidenceReceipt[]): unknown {
  const receipt = receipts.find(item => item.id === reference.evidence_id)
  if (!receipt) throw new RcaEvidenceError(`Unknown evidence ID: ${reference.evidence_id}. Retrieve actual source evidence first.`)
  if (!reference.path.startsWith('/') || reference.path.length > 200 || /~(?![01])/.test(reference.path)) {
    throw new RcaEvidenceError('Use an explicit JSON pointer to an observation, not the entire evidence response.')
  }
  let value: unknown = receipt.result
  for (const segment of reference.path.slice(1).split('/')) {
    const key = segment.replace(/~1/g, '/').replace(/~0/g, '~')
    if ((!record(value) && !Array.isArray(value)) || !Object.hasOwn(value, key)) {
      throw new RcaEvidenceError(`Evidence path ${reference.path} does not exist in ${reference.evidence_id}.`)
    }
    value = Object.getOwnPropertyDescriptor(value, key)?.value
  }
  const encoded = JSON.stringify(value)
  if (encoded === undefined || encoded.length > 2400) throw new RcaEvidenceError('Reference a smaller source row or field (maximum 2400 characters).')
  return value
}

export function parseRcaAssessment(raw: string, receipts: readonly EvidenceReceipt[]): RcaAssessment {
  let value: unknown
  try { value = JSON.parse(raw) } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
    throw new RcaEvidenceError(`Invalid RCA JSON: ${error.message}`)
  }
  if (!record(value)) throw new RcaEvidenceError('An RCA assessment must be an object.')
  exactKeys(value, ['observations', 'hypotheses'])
  const observations = references(value.observations, receipts)
  if (!observations.length) throw new RcaEvidenceError('At least one real source observation is required.')
  if (!Array.isArray(value.hypotheses) || value.hypotheses.length < 2 || value.hypotheses.length > 4) {
    throw new RcaEvidenceError('Consider two to four competing hypotheses, including missing evidence.')
  }
  const hypotheses = value.hypotheses.map(item => {
    if (!record(item)) throw new RcaEvidenceError('Hypotheses must be structured objects.')
    exactKeys(item, ['category', 'supporting', 'contradicting', 'missing'])
    if (typeof item.category !== 'string' || !Object.hasOwn(HYPOTHESES, item.category)) throw new RcaEvidenceError('Unknown hypothesis category.')
    if (!Array.isArray(item.missing) || !item.missing.length || item.missing.length > Object.keys(GAPS).length
      || item.missing.some(gap => typeof gap !== 'string' || !Object.hasOwn(GAPS, gap))) {
      throw new RcaEvidenceError('List the missing evidence using supported gap categories.')
    }
    return {
      category: item.category as keyof typeof HYPOTHESES,
      supporting: references(item.supporting, receipts),
      contradicting: references(item.contradicting, receipts),
      missing: item.missing as Array<keyof typeof GAPS>,
    }
  })
  if (new Set(hypotheses.map(item => item.category)).size !== hypotheses.length) throw new RcaEvidenceError('Hypothesis categories must be distinct.')
  return { observations, hypotheses }
}

function cell(value: unknown): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  if (text === undefined) throw new RcaEvidenceError('An undefined evidence value cannot be rendered.')
  return text.replace(/[\\`*_[\]<>]/g, '\\$&').replace(/\|/g, '\\|').replace(/[\r\n]/g, ' ')
}

export function renderRcaAssessment(report: RcaAssessment, receipts: readonly EvidenceReceipt[]): string {
  const all = [...report.observations, ...report.hypotheses.flatMap(item => [...item.supporting, ...item.contradicting])]
  const unique = [...new Map(all.map(ref => [`${ref.evidence_id}:${ref.path}`, ref])).values()]
  const label = (ref: EvidenceReference) => `E${unique.findIndex(item => item.evidence_id === ref.evidence_id && item.path === ref.path) + 1}`
  const sourceRows = unique.map(ref => {
    const receipt = receipts.find(item => item.id === ref.evidence_id)
    if (!receipt) throw new RcaEvidenceError(`Missing evidence receipt: ${ref.evidence_id}.`)
    return `| ${label(ref)} | ${cell(receipt.tool)} | ${cell(ref.path)} | ${cell(evidenceValue(ref, receipts))} | ${cell(receipt.completedAt)} |`
  })
  const hypothesisRows = report.hypotheses.map(item =>
    `| ${HYPOTHESES[item.category]} | ${item.supporting.map(label).join(', ') || 'None selected'} | ${item.contradicting.map(label).join(', ') || 'None selected; not proof of absence'} | ${item.missing.map(gap => GAPS[gap]).join('; ')} |`)
  const limited = receipts.filter(receipt => unique.some(ref => ref.evidence_id === receipt.id)
    && record(receipt.result) && receipt.result.truncated === true)
  return [
    '## Evidence-checked investigation',
    '**Cause undetermined.** Source references below were checked against actual tool responses. Their selection and relevance are agent judgements, not proof of causation. No approved diagnostic thresholds or validated causal model are configured; normal performance, a physical fault, and a numerical diagnostic confidence are not established.',
    '### Source observations',
    ['| Ref | Tool | Source field | Returned value | Tool completion (UTC) |',
      '|---|---|---|---|---|', ...sourceRows].join('\n'),
    'Tool completion is not measurement time or synchronization freshness. Use the returned event timestamps and source clocks; a historical window is not automatically a matched baseline.',
    ...(limited.length ? ['**Incomplete evidence:** at least one referenced source response was truncated. No completeness claim is made.'] : []),
    '### Competing hypotheses - untested',
    ['| Candidate | Selected supporting observations | Selected contradictory observations | Evidence still required |',
      '|---|---|---|---|', ...hypothesisRows].join('\n'),
    'The references are inspectable observations, not validated causal links. Missing contradictory observations do not confirm a hypothesis.',
    '### Next checks',
    'Obtain the missing records above, establish measurement quality/freshness and a matched operating baseline, and have qualified engineering personnel review the evidence before selecting physical inspections or changing operations. No equipment controls, notifications or SQL writes were performed by this assessment.',
    `Sources: ${unique.map(ref => `${ref.evidence_id}${ref.path}`).join('; ')}.`,
  ].join('\n\n')
}

export function renderOpenWorkEvidence(receipts: readonly EvidenceReceipt[]): string {
  const orders = new Map<string, Record<string, unknown>>()
  for (const receipt of receipts) {
    if (receipt.tool !== 'query_operations' || receipt.entity !== 'work_orders'
      || !record(receipt.result) || !Array.isArray(receipt.result.rows)) continue
    for (const row of receipt.result.rows) {
      if (!record(row) || typeof row.workOrderNumber !== 'string' || typeof row.status !== 'string') continue
      orders.set(row.workOrderNumber, row)
    }
  }
  const open = [...orders.values()].filter(row => !['completed', 'cancelled'].includes(String(row.status).toLowerCase()))
  if (!open.length) return ''
  return '\n\n### Open work returned by direct queries\n\n' + [
    '| Work order | Equipment | Title | Status | Priority | Instrument | OPC UA node |',
    '|---|---|---|---|---|---|---|',
    ...open.map(row => `| ${['workOrderNumber', 'equipmentId', 'title', 'status', 'priority', 'instrumentId', 'opcuaNodeId']
      .map(key => cell(row[key] ?? 'Not returned')).join(' | ')} |`),
  ].join('\n')
}

export function renderUnsentNotification(receipts: readonly EvidenceReceipt[]): string {
  const equipment = new Set<string>()
  for (const receipt of receipts) {
    if (!record(receipt.result) || !Array.isArray(receipt.result.rows)) continue
    for (const row of receipt.result.rows) {
      if (!record(row)) continue
      const id = row.equipment_id ?? row.equipmentId
      if (typeof id === 'string') equipment.add(id)
    }
  }
  return [
    '### Notification draft - not sent',
    '**Subject:** Maintenance evidence review requested',
    `**Equipment in the returned evidence:** ${equipment.size ? [...equipment].map(cell).join(', ') : 'Unresolved; confirm the equipment identity before sending.'}`,
    'Please review the source observations and existing work listed above. Current physical condition and root cause are **unresolved**. Fresh measurements, relevant inspection evidence and an engineering-approved baseline/limits are required before a diagnosis or operational change.',
    'This message has not been sent. No recipient or delivery route has been assumed.',
  ].join('\n\n')
}
