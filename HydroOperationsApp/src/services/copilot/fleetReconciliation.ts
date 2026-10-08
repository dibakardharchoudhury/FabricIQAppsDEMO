import { readAnswerDatasets, type AnswerDataset } from './answerPresentation.ts'
import { positiveActionClauses } from './orchestration.ts'
import { cell, type EvidenceReceipt } from './rcaEvidence.ts'
import { readingFreshness } from './query.ts'

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

export type FleetComparisonScope = { quality: boolean; temperature: boolean; lookback?: string; temperatureLimit?: number }
export type NativeComparisonReceipt = { id: string; source: string; completedAt: string; output: unknown }

export function fleetComparisonScope(question: string): FleetComparisonScope | undefined {
  const text = positiveActionClauses(question)
  if (!/\b(?:compare|comparison|reconcile|reconciliation|verify|verification|independently)\b/i.test(text)
    || !/\b(?:native|ontology|data agent|fabric iq)\b/i.test(text)
    || !/\b(?:which turbines|all turbines|fleet|hottest turbines)\b/i.test(text)) return undefined
  const quality = /\bBAD\b/i.test(text)
  const temperature = /\b(?:hot|hottest|temperature|temperatures)\b/i.test(text)
  const window = text.match(/\btoday\b|\b(?:last|past)\s+(\d+)\s*(minutes?|hours?|days?|m|h|d)\b/i)
  const count = text.match(/\btop\s+(\d+|five|ten)\b|\b(\d+|five|ten)\s+(?:hottest|highest)\b/i)
  const rank = (count?.[1] ?? count?.[2])?.toLowerCase()
  return quality || temperature ? { quality, temperature,
    ...(window ? { lookback: window[0].toLowerCase() === 'today' ? 'today' : `${window[1]}${window[2][0].toLowerCase()}` } : {}),
    ...(rank ? { temperatureLimit: rank === 'five' ? 5 : rank === 'ten' ? 10 : Number(rank) } : {}),
  } : undefined
}

function snapshot(receipts: readonly EvidenceReceipt[], temperature: boolean, scope: FleetComparisonScope) {
  return receipts.findLast(receipt => {
    const data = receipt.result
    if (receipt.tool !== (temperature ? 'query_turbine_temperature_snapshot' : 'query_signal_quality_snapshot')
      || !record(data) || !record(data.population)) return false
    return data.population.inventory_complete === true && data.population.equipment_type === 'turbine'
      && data.population.equipment_ids === null && data.truncated === false
      && (temperature ? data.threshold === undefined : data.quality_filter === 'BAD')
      && (!scope.lookback || data.lookback === scope.lookback)
      && (!temperature || scope.temperatureLimit === undefined || data.requested_limit === scope.temperatureLimit)
      && Array.isArray(data.rows) && data.rows.every(record)
  })
}

export function missingFleetSnapshots(scope: FleetComparisonScope, receipts: readonly EvidenceReceipt[]): string[] {
  return [
    ...(scope.quality && !snapshot(receipts, false, scope) ? [`query_signal_quality_snapshot (quality BAD, equipment_type turbine${scope.lookback ? `, lookback ${scope.lookback}` : ''})`] : []),
    ...(scope.temperature && !snapshot(receipts, true, scope) ? [`query_turbine_temperature_snapshot (no equipment_ids or threshold${scope.lookback ? `, lookback ${scope.lookback}` : ''}${scope.temperatureLimit !== undefined ? `, limit ${scope.temperatureLimit}` : '; preserve the requested rank limit'})`] : []),
  ]
}

export function nativeComparisonText(output: unknown): string | undefined {
  if (record(output)) return typeof output.response === 'string' ? output.response : undefined
  if (typeof output !== 'string') return undefined
  if (!output.trim().startsWith('{')) return output
  try {
    const parsed: unknown = JSON.parse(output)
    return record(parsed) && typeof parsed.response === 'string' ? parsed.response : undefined
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
    return undefined
  }
}

type Claim = { node: string; equipment: string; value: string; time: string; quality?: string; unit?: string; rank?: string }
export function readNativeDatasets(text: string) {
  const parsed = readAnswerDatasets(text)
  const datasets: Array<Pick<AnswerDataset, 'title' | 'columns' | 'rows'>> = [...parsed.datasets]
  const issues = [...parsed.issues]
  for (const match of text.matchAll(/```json\s*\r?\n([\s\S]*?)\r?\n```/gi)) {
    let value: unknown
    try { value = JSON.parse(match[1]) } catch (error) {
      if (!(error instanceof SyntaxError)) throw error
      issues.push('Native JSON dataset is malformed; no rows were accepted.')
      continue
    }
    if (!record(value) || !Object.hasOwn(value, 'rows')) continue
    const rows = value.rows
    if (!Array.isArray(rows) || !rows.length || !rows.every(record)) {
      issues.push('Native JSON rows require a nonempty array of flat records; an unrecognized or schema-free empty set is not a verified empty population.')
      continue
    }
    const columns = Object.keys(rows[0])
    if (!columns.length || rows.some(row => Object.keys(row).length !== columns.length
      || columns.some(column => !Object.hasOwn(row, column)
        || (row[column] !== null && !['string', 'number', 'boolean'].includes(typeof row[column]))))) {
      issues.push('Native JSON rows have nested or inconsistent columns; no rows were accepted.')
      continue
    }
    if (Object.hasOwn(value, 'row_count') && value.row_count !== rows.length) {
      issues.push('Native JSON row_count does not match the returned rows; no rows were accepted.')
      continue
    }
    datasets.push({ title: typeof value.set === 'string' ? value.set : 'Native JSON rows', columns,
      rows: rows.map(row => columns.map(column => row[column] === null ? '' : String(row[column]))) })
  }
  return { datasets, issues }
}

const aliases = {
  node: ['opcua_node_id', 'Signal ID', 'OPC UA node', 'Node ID', 'instrument_id / opcua_node_id', 'signal_id_opc_node'],
  equipment: ['equipment_id', 'Turbine ID', 'Equipment', 'Linked equipment_id'],
  value: ['value', 'Reading', 'latest_temp', 'Temperature', 'latest_value', 'Raw value', 'Latest temperature'],
  time: ['event_time', 'Event time (UTC)', 'latest_event_time', 'Timestamp (UTC)', 'timestamp_utc', 'Latest timestamp (UTC)'],
  quality: ['quality'], unit: ['unit'], rank: ['rank'],
}
const normalized = (text: string) => text.toLowerCase().replace(/[^a-z0-9]/g, '')

function claims(text: string, temperature: boolean): { rows: Claim[]; issues: string[] } {
  const parsed = readNativeDatasets(text)
  const rows: Claim[] = []
  const issues = [...parsed.issues]
  let recognized = 0
  for (const dataset of parsed.datasets) {
    const indices = Object.fromEntries(Object.entries(aliases).map(([key, names]) => [
      key, dataset.columns.findIndex(column => names.some(name => normalized(name) === normalized(column))),
    ]))
    if (['node', 'equipment', 'value', 'time'].some(key => indices[key] < 0)) continue
    if (temperature ? indices.rank < 0 && !/temperature|hottest/i.test(dataset.title) : indices.quality < 0) continue
    if (!temperature && indices.rank >= 0) continue
    recognized++
    for (const row of dataset.rows) {
      const rawNode = row[indices.node]
      const combined = normalized(dataset.columns[indices.node]) === normalized('instrument_id / opcua_node_id')
      const node = combined ? rawNode.match(/^INST_[A-Za-z0-9_]+\s+\/\s+(ns=\d+;s=[^\s/]+)$/)?.[1] : rawNode
      const equipment = row[indices.equipment].match(/\bEQUIP_[A-Za-z0-9_]+\b/)?.[0]
      if (!node || !equipment || (temperature && !node.endsWith('.turbine_temp'))) {
        issues.push(`Native ${dataset.title}: identity is not comparable.`)
        continue
      }
      if (!temperature && row[indices.quality].toUpperCase() !== 'BAD') continue
      rows.push({ node, equipment, value: row[indices.value], time: row[indices.time],
        quality: row[indices.quality], unit: row[indices.unit], rank: row[indices.rank] })
    }
  }
  if (recognized !== 1) issues.push(`Expected one native ${temperature ? 'ranked temperature' : 'BAD-quality'} table; found ${recognized}. Missing/unrecognized tables are not evidence of an empty population.`)
  if (new Set(rows.map(row => row.node)).size !== rows.length) issues.push('Native table repeats a signal; duplicate claims are not a unique population.')
  return { rows, issues }
}

function compare(claim: Claim, row: Record<string, unknown>, rank: number, temperature: boolean): string {
  const differences: string[] = []
  if (claim.equipment !== row.equipment_id) differences.push('equipment differs')
  const numeric = claim.value.trim() !== '' && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(claim.value)
  if (!numeric || typeof row.value !== 'number' || !Number.isFinite(row.value)) differences.push('value not comparable')
  else if (Number(claim.value) !== row.value) differences.push('value differs')
  // Preserve precision: a native second-rounded timestamp is not an exact raw-time match.
  const nativeTime = claim.time.replace(' ', 'T').replace(/ UTC$/, 'Z')
  const explicitTime = /(?:Z|[+-]\d{2}:\d{2})$/.test(nativeTime) ? nativeTime : `${nativeTime}Z`
  const directTime = String(row.event_time)
  if (explicitTime !== directTime) differences.push('timestamp/precision differs')
  if (!temperature && claim.quality !== row.quality) differences.push('quality differs')
  if (temperature) {
    if (claim.rank === undefined || Number(claim.rank) !== rank) differences.push('rank differs/unreported')
    if (claim.unit !== row.unit) differences.push('unit differs/unreported; no conversion assumed')
  }
  return differences.length ? differences.join('; ') : 'Returned fields match; not a freshness or scope attestation'
}

export function compareWork(text: string | undefined, directRows: Record<string, unknown>[], fullInventory = false): string {
  const direct = new Map<string, { equipment: string; order: Record<string, unknown>; relations: Set<string> }>()
  for (const row of directRows) {
    if (typeof row.equipment_id !== 'string' || !Array.isArray(row.open_work_orders)) {
      throw new Error('Snapshot omitted work-order coverage or equipment identity.')
    }
    for (const order of row.open_work_orders) {
      if (!record(order) || typeof order.workOrderNumber !== 'string') throw new Error('Snapshot omitted work-order identity.')
      const key = `${row.equipment_id}:${order.workOrderNumber}`
      const value = direct.get(key) ?? { equipment: row.equipment_id, order, relations: new Set<string>() }
      value.relations.add(row.opcua_node_id ? `${row.opcua_node_id}: ${order.relation}` : 'Equipment-level inventory; signal relation not compared')
      direct.set(key, value)
    }
  }
  const fields = { number: ['workOrderNumber', 'Work order number', 'Work order', 'Order number'],
    equipment: aliases.equipment, title: ['Title'], status: ['Status'], priority: ['Priority'] }
  const native = new Map<string, Record<keyof typeof fields, string>>()
  const parsed = readNativeDatasets(text ?? '')
  const issues = [...parsed.issues]
  let recognized = 0
  for (const table of parsed.datasets) {
    const indices = Object.fromEntries(Object.entries(fields).map(([key, names]) =>
      [key, table.columns.findIndex(column => names.some(name => normalized(name) === normalized(column)))]))
    if (Object.values(indices).some(index => index < 0)) continue
    recognized++
    for (const row of table.rows) {
      const equipment = row[indices.equipment].match(/\bEQUIP_[A-Za-z0-9_]+\b/)?.[0]
      if (!equipment || !row[indices.number]) { issues.push('Native work-order identity is not comparable.'); continue }
      const key = `${equipment}:${row[indices.number]}`
      if (native.has(key)) issues.push(`Duplicate native work-order claim: ${key}.`)
      native.set(key, { equipment, number: row[indices.number], title: row[indices.title],
        status: row[indices.status], priority: row[indices.priority] })
    }
  }
  if (recognized !== 1) issues.push(`Expected one native work-order table; found ${recognized}. An absent table is not proof of no open work.`)
  const rows = [...new Set([...direct.keys(), ...native.keys()])].map(key => {
    const claim = native.get(key)
    const observation = direct.get(key)
    const changes = claim && observation
      ? (['title', 'status', 'priority'] as const).filter(field => claim[field] !== observation.order[field])
      : []
    const status = issues.length ? 'Native work population not comparable'
      : !claim ? 'Not in returned native table' : !observation ? `Not in direct ${fullInventory ? 'open-work inventory' : 'selected population'}`
        : changes.length ? `Text differs: ${changes.join(', ')}` : 'Number, equipment, title, status and priority match'
    return `| ${[key, claim ? `${claim.title}; ${claim.status}; ${claim.priority}` : 'Not returned',
      observation ? `${observation.order.title}; ${observation.order.status}; ${observation.order.priority}` : 'Not selected',
      status, observation ? [...observation.relations].join('; ') : 'Not evaluated'].map(cell).join(' | ')} |`
  })
  return ['### Open-work reconciliation',
    ...issues.map(issue => `**Work comparison incomplete:** ${cell(issue)}`),
    ['| Equipment / work order | Native claim | Direct record | Comparison | Direct signal relations |',
      '|---|---|---|---|---|', ...rows].join('\n'),
    fullInventory
      ? 'Reads are not atomic. Native-only records were not returned by the complete direct open-work read; this does not prove their present closure or nonexistence. Signal linkage is not compared here.'
      : 'Native-only records are outside the direct selected population, not proven closed or nonexistent. Native signal linkage is not attested; direct relations use actual SQL instrument/node identifiers, never title similarity.',
  ].join('\n\n')
}

export function renderFleetReconciliation(
  scope: FleetComparisonScope, receipts: readonly EvidenceReceipt[], native: readonly NativeComparisonReceipt[],
): string {
  const missing = missingFleetSnapshots(scope, receipts)
  if (missing.length) throw new Error(`Fleet comparison incomplete: missing full-population evidence from ${missing.join(', ')}.`)
  if (!native.length) throw new Error('Fleet comparison has no verified native execution receipt.')
  const sections = ['## Source-checked fleet comparison',
    'Native claims below come from actual tool output, not specialist paraphrases. Direct snapshots enumerate the active inventory independently of the native list. Matching returned fields does not establish equivalent native query scope, freshness, physical health or causation.']
  for (const source of native) {
    const text = nativeComparisonText(source.output)
    const directRows: Record<string, unknown>[] = []
    sections.push(`### Native ${cell(source.source)} vs direct reads`,
      `Native tool receipt: ${cell(source.id)}; completed ${cell(source.completedAt)}. Completion is not measurement time.`)
    for (const temperature of [false, true]) {
      if (!(temperature ? scope.temperature : scope.quality)) continue
      const receipt = snapshot(receipts, temperature, scope)
      if (!receipt) throw new Error('Full-population snapshot is missing.')
      const data = receipt.result
      if (!record(data) || !Array.isArray(data.rows) || !data.rows.every(record) || !record(data.population)) {
        throw new Error('Fleet snapshot lost its validated result shape.')
      }
      directRows.push(...data.rows)
      const nativeClaims = text === undefined
        ? { rows: [], issues: ['Native output envelope could not be parsed; no comparison pass is claimed.'] }
        : claims(text, temperature)
      const direct = new Map(data.rows.map((row, index) => [String(row.opcua_node_id), { row, rank: index + 1 }]))
      const returned = new Map(nativeClaims.rows.map(row => [row.node, row]))
      const nodes = [...new Set([...direct.keys(), ...returned.keys()])]
      const comparisonRows = nodes.map(node => {
        const claim = returned.get(node)
        const observation = direct.get(node)
        const finding = nativeClaims.issues.length ? 'Native population not comparable'
          : !claim ? 'Not in returned native table'
            : !observation ? 'Not in direct selected population'
              : compare(claim, observation.row, observation.rank, temperature)
        const work = observation && Array.isArray(observation.row.open_work_orders)
          ? observation.row.open_work_orders.map(order => {
            if (!record(order)) throw new Error('Invalid snapshot work-order row.')
            return `${order.workOrderNumber}: ${order.title ?? 'Title not returned'}; ${order.status}, ${order.priority}, ${order.relation}`
          }).join('; ') || 'None returned' : 'Not evaluated for this native-only signal'
        return `| ${[node, claim ? `${claim.value}${claim.unit ? ` ${claim.unit}` : ''}; ${claim.time}${claim.rank ? `; rank ${claim.rank}` : ''}` : 'Not returned',
          observation ? `${observation.row.value} ${observation.row.unit}; ${observation.row.quality}; ${observation.row.event_time}; ${readingFreshness(observation.row.event_time, data.read_completed_at_utc)}${temperature ? `; rank ${observation.rank}` : ''}` : 'Not selected',
          finding, work].map(cell).join(' | ')} |`
      })
      sections.push(`### ${temperature ? 'Hottest latest raw temperatures' : 'Latest BAD-quality signals'}`,
        `Window: ${cell(data.lookback)}. Expected active signals: ${cell(data.population.expected_signal_count)}. Native table rows: ${nativeClaims.rows.length}; direct selected rows: ${data.rows.length}. Source: ${cell(receipt.id)}; read completed ${cell(data.read_completed_at_utc)}.`,
        ...nativeClaims.issues.map(issue => `**Comparison incomplete:** ${cell(issue)}`),
        `Signals without readings in this window: ${cell(JSON.stringify(data.population.signals_without_readings))}. Unresolved source nodes: ${cell(JSON.stringify(data.unresolved_nodes))}. Missing signals and stale measurements cannot establish current fleet health.`,
        ['| Signal | Native claim | Direct observation | Comparison | Open work: direct signal relation |',
          '|---|---|---|---|---|', ...comparisonRows].join('\n'))
    }
    sections.push(compareWork(text, directRows))
  }
  sections.push('Reads are not atomic across services; no work order was created.')
  return sections.join('\n\n')
}
