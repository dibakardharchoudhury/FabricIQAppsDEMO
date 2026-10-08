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
  type: 'object', properties: {
    evidence_id: { type: 'string', description: 'The exact evidence_id returned by the source tool.' },
    path: { type: 'string', pattern: '^/', minLength: 1, maxLength: 200, description: 'Absolute JSON pointer inside data: MUST start with /. Correct: /rows/0. Incorrect: rows/0 or /data/rows/0. Cite one existing source row or smaller field. Referenced JSON must be at most 2400 characters; never a whole large rows array.' },
  },
  required: ['evidence_id', 'path'], additionalProperties: false,
}
export const RCA_REPORT_TOOL = {
  type: 'function', name: 'complete_rca_assessment',
  description: 'Complete a source-referenced investigation. References use a returned evidence_id and a JSON pointer relative to its data, e.g. /rows/0. The app checks every reference and renders the observations itself. Hypotheses remain untested; no configured causal model or approved diagnostic limits exist. Do not supply free-text diagnoses, thresholds, confidence scores or baseline claims.',
  parameters: {
    type: 'object', properties: {
      observations: { type: 'array', minItems: 1, maxItems: 12, items: referenceSchema },
      hypotheses: { type: 'array', minItems: 2, maxItems: 4, items: {
        type: 'object', properties: {
          category: { type: 'string', enum: Object.keys(HYPOTHESES) },
          supporting: { type: 'array', maxItems: 12, items: referenceSchema },
          contradicting: { type: 'array', maxItems: 12, items: referenceSchema },
          missing: { type: 'array', minItems: 1, items: { type: 'string', enum: Object.keys(GAPS) } },
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
  if (reference.path && !reference.path.startsWith('/')) {
    throw new RcaEvidenceError(`Invalid JSON pointer "${reference.path.slice(0, 200)}": the leading "/" is missing. Use "/rows/0", not "rows/0". Every observation, supporting and contradicting path must begin with "/"; paths are relative to data, without a /data prefix.`)
  }
  if (!reference.path.startsWith('/') || reference.path.length > 200 || /~(?![01])/.test(reference.path)) {
    throw new RcaEvidenceError('Use an explicit JSON pointer to an observation, not the entire evidence response.')
  }
  if (/^\/(?:grounded_summary|visualization|chart)(?:\/|$)/.test(reference.path)) {
    throw new RcaEvidenceError('Reference source rows or fields, not formatted summaries or visualization metadata.')
  }
  let value: unknown = receipt.result
  for (const segment of reference.path.slice(1).split('/')) {
    const key = segment.replace(/~1/g, '/').replace(/~0/g, '~')
    if ((!record(value) && !Array.isArray(value)) || !Object.hasOwn(value, key)) {
      throw new RcaEvidenceError(`Evidence path ${reference.path} does not exist in ${reference.evidence_id}. Paths are relative to data: omit the /data envelope and reference an actual row such as /rows/0 or an existing field. Do not invent a path.`)
    }
    value = Object.getOwnPropertyDescriptor(value, key)?.value
  }
  const encoded = JSON.stringify(value)
  if (encoded === undefined || encoded.length > 2400) throw new RcaEvidenceError(`Evidence ${reference.evidence_id} at ${reference.path} is too large or not serializable. Reference a smaller source row or field (maximum 2400 characters), such as /rows/0 rather than the whole /rows array.`)
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
    '### Sources',
    unique.map(ref => `${ref.evidence_id}${ref.path}`).join('; ') + '.',
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
  if (!open.length) {
    const emptyReads = receipts.filter(receipt => receipt.tool === 'query_operations' && receipt.entity === 'work_orders'
      && record(receipt.result) && Array.isArray(receipt.result.rows) && receipt.result.rows.length === 0)
    return emptyReads.length ? '\n\n### Work-order query coverage\n\n' + emptyReads.map(receipt =>
      `- Source ${cell(receipt.id)}, completed ${cell(receipt.completedAt)}: zero rows returned for the filters recorded in its execution receipt. This is not a claim about records outside those filters.`).join('\n') : ''
  }
  return '\n\n### Open work returned by direct queries\n\n' + [
    '| Work order | Equipment | Title | Status | Priority | Instrument | OPC UA node |',
    '|---|---|---|---|---|---|---|',
    ...open.map(row => `| ${['workOrderNumber', 'equipmentId', 'title', 'status', 'priority', 'instrumentId', 'opcuaNodeId']
      .map(key => cell(row[key] ?? 'Not returned')).join(' | ')} |`),
  ].join('\n')
}

export function renderInventoryEvidence(receipts: readonly EvidenceReceipt[]): string {
  const inventories = new Map<string, { receipt: EvidenceReceipt; rows: Record<string, unknown>[]; truncated: boolean }>()
  for (const receipt of receipts) {
    const snapshot = ['query_signal_quality_snapshot', 'query_turbine_temperature_snapshot'].includes(receipt.tool)
    if (!snapshot && !(receipt.tool === 'query_operations' && ['spare_parts', 'notifications'].includes(receipt.entity ?? ''))) continue
    const result = receipt.result
    if (!record(result) || !Array.isArray(result.rows) || !result.rows.every(record)) {
      throw new RcaEvidenceError(`Inventory ${receipt.id} did not return valid source rows.`)
    }
    const rows = result.rows.map(row => {
      if (!snapshot) return row
      const readTime = Date.parse(String(result.read_completed_at_utc))
      const eventTime = Date.parse(String(row.event_time))
      if (!Number.isFinite(readTime) || !Number.isFinite(eventTime)) {
        throw new RcaEvidenceError(`Snapshot ${receipt.id} has no valid measurement/read clock for freshness.`)
      }
      return { ...row, freshness: eventTime > readTime ? 'Uncertain (future timestamp)' : readTime - eventTime > 60_000 ? 'Stale (>60s)' : 'Within 60s' }
    })
    inventories.set(JSON.stringify([receipt.tool, receipt.entity, result.rows]), {
      receipt, rows, truncated: result.truncated === true,
    })
  }
  return [...inventories.values()].map(({ receipt, rows, truncated }) => {
    const columns = [...new Set(rows.flatMap(row => Object.keys(row)))]
    return [
      `### Returned source inventory: ${cell(receipt.entity ?? receipt.tool)}`,
      `Source ${cell(receipt.id)}; tool completed ${cell(receipt.completedAt)}. All ${rows.length} returned rows are preserved, not just observations selected for RCA. Source filters still apply; broader reads can include records outside the requested subset. ${truncated ? '**Truncated source: this is not the complete matching inventory.**' : ''}`,
      rows.length ? [
        `| ${columns.map(cell).join(' | ')} |`,
        `| ${columns.map(() => '---').join(' | ')} |`,
        ...rows.map(row => `| ${columns.map(column => cell(Object.hasOwn(row, column) ? row[column] : 'Not returned')).join(' | ')} |`),
      ].join('\n') : 'No rows returned for this read. This does not establish equipment health or the absence of records outside its filters.',
    ].join('\n\n')
  }).join('\n\n')
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
