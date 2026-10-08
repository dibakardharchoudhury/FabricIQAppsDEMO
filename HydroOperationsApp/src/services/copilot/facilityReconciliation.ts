import type { AgentVisualization } from '../assistantStream.ts'
import { readAnswerDatasets } from './answerPresentation.ts'
import { compareWork, nativeComparisonText, type NativeComparisonReceipt } from './fleetReconciliation.ts'
import { cell, type EvidenceReceipt } from './rcaEvidence.ts'

const sources = [
  { tool: 'query_operations', entity: 'work_orders' },
  { tool: 'query_assets', entity: 'equipment' },
  { tool: 'query_assets', entity: 'facilities' },
]
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

function complete(receipts: readonly EvidenceReceipt[], source: typeof sources[number]) {
  return receipts.findLast(receipt => {
    const data = receipt.result
    return receipt.tool === source.tool && receipt.entity === source.entity
      && receipt.arguments !== undefined && !receipt.arguments.where?.length && !receipt.arguments.columns?.length
      && record(data) && data.truncated === false && Array.isArray(data.rows)
      && data.total_matched === data.rows.length && data.rows.every(record)
  })
}

export function missingFacilityEvidence(receipts: readonly EvidenceReceipt[]): string[] {
  return sources.filter(source => !complete(receipts, source))
    .map(source => `${source.tool} (${source.entity}), no where or columns, limit 500; complete inventory independent of native IDs`)
}

export function renderFacilityReconciliation(
  receipts: readonly EvidenceReceipt[], native: readonly NativeComparisonReceipt[],
): { text: string; visualization: AgentVisualization } {
  const missing = missingFacilityEvidence(receipts)
  if (missing.length) throw new Error(`Facility reconciliation incomplete: ${missing.join('; ')}`)
  const data = sources.map(source => {
    const receipt = complete(receipts, source)!
    if (!record(receipt.result) || !Array.isArray(receipt.result.rows)) throw new Error('Facility source omitted rows.')
    return { receipt, rows: receipt.result.rows.filter(record) }
  })
  const id = (row: Record<string, unknown>, key: string): string => {
    if (typeof row[key] !== 'string' || !row[key].trim()) throw new Error(`Facility reconciliation source omitted ${key}.`)
    return row[key]
  }
  const index = (rows: Record<string, unknown>[], key: string) => {
    const result = new Map<string, Record<string, unknown>>()
    for (const row of rows) {
      const value = id(row, key)
      if (result.has(value)) throw new Error(`Facility reconciliation has duplicate ${key}: ${value}.`)
      result.set(value, row)
    }
    return result
  }
  const equipment = index(data[1].rows, 'equipment_id')
  const facilities = index(data[2].rows, 'facility_id')
  const orders = [...index(data[0].rows, 'workOrderNumber').values()].filter(row =>
    !['completed', 'cancelled'].includes(id(row, 'status').toLowerCase()))
  const counts = new Map([...facilities.keys()].map(key => [key, { orders: 0, equipment: new Set<string>() }]))
  const unmatched: string[] = []
  const workRows: string[] = []
  for (const order of orders) {
    const equipmentId = id(order, 'equipmentId')
    const facilityId = equipment.get(equipmentId)?.facility_id
    const matched = typeof facilityId === 'string' && facilities.has(facilityId)
    const key = matched ? facilityId : 'UNMAPPED'
    const bucket = counts.get(key) ?? { orders: 0, equipment: new Set<string>() }
    bucket.orders++
    bucket.equipment.add(equipmentId)
    counts.set(key, bucket)
    if (!matched) unmatched.push(`${id(order, 'workOrderNumber')}: equipment ${equipmentId}, facility ${String(facilityId ?? 'not returned')}`)
    workRows.push(`| ${[order.workOrderNumber, equipmentId, key, order.title, order.status, order.priority].map(cell).join(' | ')} |`)
  }
  const rows = [...counts].map(([facility, count]) => [facility, count.equipment.size, count.orders])
  const csv = [['facility_id', 'equipment_with_open_work', 'open_work_order_count'], ...rows]
    .map(row => row.map(value => `"${String(value).replace(/"/g, '""')}"`).join(',')).join('\n')
  const comparisons = native.map(source => {
    const text = nativeComparisonText(source.output)
    if (!source.source.includes('ontology')) {
      const grouped = [...new Set(orders.map(order => id(order, 'equipmentId')))].map(equipmentId => ({
        equipment_id: equipmentId, open_work_orders: orders.filter(order => order.equipmentId === equipmentId),
      }))
      return compareWork(text, grouped, true)
    }
    const parsed = readAnswerDatasets(text ?? '')
    const tables = parsed.datasets.filter(table => table.columns.some(column => column.toLowerCase().replace(/[^a-z]/g, '') === 'facilityid'))
    if (tables.length !== 1 || parsed.issues.length) return 'Ontology facility comparison incomplete: no single recognized facility_id table. An unrecognized response is not evidence of zero facilities; consult the native receipt.'
    const table = tables[0]
    const column = table.columns.findIndex(name => name.toLowerCase().replace(/[^a-z]/g, '') === 'facilityid')
    const nativeIds = new Set(table.rows.map(row => row[column]))
    return ['### Ontology facility membership',
      '| Facility | Native table | Direct Lakehouse inventory |', '|---|---|---|',
      ...[...new Set([...facilities.keys(), ...nativeIds])].map(key => `| ${cell(key)} | ${nativeIds.has(key) ? 'Returned' : 'Not returned'} | ${facilities.has(key) ? 'Returned' : 'Not returned'} |`),
      'Membership comparison only; native query scope and execution provenance are not certified.',
    ].join('\n')
  })
  return {
    text: ['## Source-derived facility backlog',
      `${orders.length} open work orders. Counts and the chart use the same complete direct SQL work inventory and Lakehouse equipment-to-facility mapping, independently of native IDs. Completed and Cancelled orders are excluded.`,
      ['| Facility | Equipment with open work | Open orders |', '|---|---:|---:|', ...rows.map(row => `| ${row.map(cell).join(' | ')} |`)].join('\n'),
      unmatched.length ? `Unmatched records (retained in UNMAPPED): ${unmatched.map(cell).join('; ')}` : 'No unmatched IDs in the complete direct inventory.',
      ...comparisons,
      ['### Direct work and facility mapping', '| Work order | Equipment | Facility | Title | Status | Priority |', '|---|---|---|---|---|---|', ...workRows].join('\n'),
      `Read completion: ${data.map(({ receipt }) => `${receipt.entity}: ${receipt.completedAt}`).join('; ')}. Separate reads are not an atomic cross-service snapshot.`,
    ].join('\n\n'),
    visualization: { chartType: 'bar', title: 'Open work orders by facility', xColumn: 'facility_id',
      yColumns: ['open_work_order_count'], xAxisTitle: 'Facility', yAxisTitle: 'Open orders', inlineCsvData: csv },
  }
}
