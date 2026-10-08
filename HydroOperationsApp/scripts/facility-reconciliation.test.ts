import assert from 'node:assert/strict'
import { test } from 'node:test'
import { missingFacilityEvidence, renderFacilityReconciliation } from '../src/services/copilot/facilityReconciliation.ts'
import { missingRequestedSpecialists } from '../src/services/copilot/orchestration.ts'
import type { EvidenceReceipt } from '../src/services/copilot/rcaEvidence.ts'

const receipt = (entity: string, rows: Record<string, unknown>[]): EvidenceReceipt => ({
  id: entity, tool: entity === 'work_orders' ? 'query_operations' : 'query_assets', entity,
  arguments: {}, completedAt: '2026-10-08T11:00:00Z',
  result: { rows, total_matched: rows.length, truncated: false },
})
const evidence = () => [
  receipt('work_orders', [
    { workOrderNumber: 'WO-1', equipmentId: 'E1', title: 'Inspection', status: 'Draft', priority: 'Low' },
    { workOrderNumber: 'WO-2', equipmentId: 'E2', title: 'Cooling', status: 'Ready', priority: 'Medium' },
    { workOrderNumber: 'WO-3', equipmentId: 'UNKNOWN', title: 'Unmapped', status: 'Approved', priority: 'Low' },
    { workOrderNumber: 'WO-4', equipmentId: 'E1', title: 'Closed', status: 'Completed', priority: 'Low' },
  ]),
  receipt('equipment', [{ equipment_id: 'E1', facility_id: 'F1' }, { equipment_id: 'E2', facility_id: 'F2' }]),
  receipt('facilities', [{ facility_id: 'F1' }, { facility_id: 'F2' }, { facility_id: 'EMPTY' }]),
]

test('native-selected filters, projected fields, truncated and capped inventories cannot verify a facility backlog', () => {
  for (const arguments_ of [{ where: [{}] }, { columns: ['equipment_id'] }]) {
    const rows = evidence()
    rows[1].arguments = arguments_
    assert.equal(missingFacilityEvidence(rows).length, 1)
    assert.throws(() => renderFacilityReconciliation(rows, []), /incomplete/)
  }
  const rows = evidence()
  rows[0].result = { rows: [], total_matched: 1, truncated: false }
  assert.equal(missingFacilityEvidence(rows).length, 1)
  rows[0].result = { rows: [], total_matched: 0, truncated: true }
  assert.equal(missingFacilityEvidence(rows).length, 1)
})

test('table and chart share exact mappings, retain unmatched work and include empty facilities', () => {
  const result = renderFacilityReconciliation(evidence(), [])
  assert.match(result.text, /3 open work orders/)
  assert.match(result.text, /\| F2 \| 1 \| 1 \|/)
  assert.match(result.text, /\| WO-2 \| E2 \| F2 \| Cooling/)
  assert.match(result.text, /WO-3: equipment UNKNOWN/)
  assert.match(result.visualization.inlineCsvData, /"EMPTY","0","0"/)
  assert.match(result.visualization.inlineCsvData, /"UNMAPPED","1","1"/)
  assert.doesNotMatch(result.text, /WO-4/)
})

test('ambiguous source identities fail instead of fabricating mappings', () => {
  const rows = evidence()
  rows[1] = receipt('equipment', [{ equipment_id: 'E1', facility_id: 'F1' }, { equipment_id: 'E1', facility_id: 'F2' }])
  assert.throws(() => renderFacilityReconciliation(rows, []), /duplicate equipment_id/)
})

test('unrecognized ontology output is not interpreted as an empty facility population', () => {
  const result = renderFacilityReconciliation(evidence(), [{ id: 'native', source: 'ontology', completedAt: 'now', output: 'No useful table' }])
  assert.match(result.text, /comparison incomplete/)
  assert.match(result.text, /not evidence of zero facilities/)
})

test('verification before disagreement investigation does not impose an unrequested second QA pass', () => {
  const question = 'Independently verify both sets using direct telemetry snapshots and SQL work records. Investigate any disagreement.'
  assert.deepEqual(missingRequestedSpecialists(question, ['fabric-iq', 'qa', 'rca']), [])
  assert.deepEqual(missingRequestedSpecialists('Investigate differences, then independently verify the findings.', ['qa', 'rca']), ['qa'])
  assert.deepEqual(missingRequestedSpecialists('Independently verify records, then prepare a work-order draft.', ['qa', 'work-order']), ['qa'])
})
