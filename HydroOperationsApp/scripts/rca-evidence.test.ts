import assert from 'node:assert/strict'
import test from 'node:test'
import { parseRcaAssessment, renderRcaAssessment, renderInventoryEvidence, renderOpenWorkEvidence, renderUnsentNotification, type EvidenceReceipt } from '../src/services/copilot/rcaEvidence.ts'
import { agentDefinition } from '../src/services/copilot/agentDefinitions.ts'
import { isNotificationDraftRequest, requiresInspectionEvidence, workOrderPriorityForRequest } from '../src/services/copilot/orchestration.ts'
import { readAnswerDatasets } from '../src/services/copilot/answerPresentation.ts'

const receipts: EvidenceReceipt[] = [{
  id: 'call_measured', tool: 'query_station_power', completedAt: '2026-10-07T22:00:00Z',
  result: { rows: [{ Station: 'Sloy', average_power_MW: 1315.0626405438807, latest_event_time: '2026-10-07T20:20:15Z' }], truncated: true },
}]
const ref = { evidence_id: 'call_measured', path: '/rows/0' }
const assessment = {
  observations: [ref],
  hypotheses: [
    { category: 'sensor_or_ingestion', supporting: [ref], contradicting: [], missing: ['fresh_measurements', 'independent_measurement'] },
    { category: 'operating_conditions', supporting: [], contradicting: [ref], missing: ['matched_baseline', 'approved_limits'] },
  ],
}

test('RCA renders actual measurements and limitations, never an invented threshold or baseline claim', () => {
  const parsed = parseRcaAssessment(JSON.stringify(assessment), receipts)
  const text = renderRcaAssessment(parsed, receipts)
  assert.match(text, /Cause undetermined/)
  assert.match(text, /1315\.0626405438807/)
  assert.match(text, /20:20:15Z/)
  assert.match(text, /Incomplete evidence/)
  assert.match(text, /not proof of causation/)
  assert.doesNotMatch(text, /5%|normal performance is established|validated baseline/i)
  assert.equal(readAnswerDatasets(text).datasets.length, 2)
})

test('RCA rejects fabricated references, paths and unsupported diagnostic fields', () => {
  for (const change of [
    { ...assessment, threshold: 5 },
    { ...assessment, conclusion: 'normal performance' },
    { ...assessment, observations: [{ ...ref, evidence_id: 'invented' }] },
    { ...assessment, observations: [{ ...ref, path: '/rows/99' }] },
    { ...assessment, observations: [{ ...ref, path: '/constructor' }] },
    { ...assessment, observations: [{ ...ref, path: '/rows/0/~bad' }] },
    { ...assessment, observations: [{ ...ref, path: '/grounded_summary' }] },
    { ...assessment, observations: [] },
    { ...assessment, hypotheses: [{ ...assessment.hypotheses[0], confidence: .99 }, assessment.hypotheses[1]] },
    { ...assessment, hypotheses: [{ ...assessment.hypotheses[0], category: 'confirmed_failure' }, assessment.hypotheses[1]] },
    { ...assessment, hypotheses: [{ ...assessment.hypotheses[0], missing: [] }, assessment.hypotheses[1]] },
  ]) assert.throws(() => parseRcaAssessment(JSON.stringify(change), receipts))
  assert.throws(() => parseRcaAssessment('{broken', receipts), /Invalid RCA JSON/)
  assert.throws(() => parseRcaAssessment(JSON.stringify(assessment), [{ ...receipts[0], result: { rows: [{ large: 'x'.repeat(3000) }] } }]), /smaller source/)
})

test('evidence pointers retain null and escaped JSON keys without fabricating measurements', () => {
  const sources = [{ ...receipts[0], result: { 'a/b': { '~key': null } } }]
  const pointer = { evidence_id: ref.evidence_id, path: '/a~1b/~0key' }
  const parsed = parseRcaAssessment(JSON.stringify({ ...assessment, observations: [pointer],
    hypotheses: assessment.hypotheses.map(hypothesis => ({ ...hypothesis, supporting: [pointer], contradicting: [] })) }), sources)
  assert.match(renderRcaAssessment(parsed, sources), /\| null \|/)
})

test('station-power chart CSV is presentation metadata, not an RCA source observation', () => {
  const sources = [{ ...receipts[0], result: {
    rows: [{ Station: 'Sloy', average_power_MW: 1315.0626405438807 }],
    chart: { inlineCsvData: 'Station,average_power_MW\nSloy,1315.0626405438807' },
  } }]
  for (const path of ['/chart', '/chart/inlineCsvData']) {
    const pointer = { ...ref, path }
    assert.throws(() => parseRcaAssessment(JSON.stringify({ ...assessment, observations: [pointer] }), sources), /visualization metadata/)
    assert.throws(() => parseRcaAssessment(JSON.stringify({ ...assessment, hypotheses: [
      { ...assessment.hypotheses[0], supporting: [pointer] }, assessment.hypotheses[1],
    ] }), sources), /visualization metadata/)
  }
  const parsed = parseRcaAssessment(JSON.stringify(assessment), sources)
  assert.match(renderRcaAssessment(parsed, sources), /### Sources\n\ncall_measured\/rows\/0/)
})

test('reproduced slashless pointers receive exact repair guidance and are constrained by the tool schema', () => {
  for (const path of ['rows/1', 'rows/1/event_time', 'read_completed_at_utc']) {
    assert.throws(() => parseRcaAssessment(JSON.stringify({ ...assessment, observations: [{ ...ref, path }] }), receipts), /leading "\/" is missing/)
  }
  const schema = JSON.stringify(agentDefinition('rca', 'test').tools)
  assert.match(schema, /"pattern":"\^\/"/)
  assert.match(schema, /"maxLength":200/)
  assert.equal(requiresInspectionEvidence('Investigate it using available telemetry, inspections and existing work.'), true)
  assert.equal(requiresInspectionEvidence('Investigate whether recent telemetry and inspections justify additional work.'), true)
  assert.equal(requiresInspectionEvidence('Investigate telemetry and prepare an inspection draft.'), false)
  assert.equal(requiresInspectionEvidence('Investigate telemetry. Do not query inspections.'), false)
})

test('a pointer containing JSON separators receives actual sibling paths, without automatic repair', () => {
  const sources = [{ ...receipts[0], result: { rows: [{ value: 1 }, { value: 2 }] } }]
  assert.throws(() => parseRcaAssessment(JSON.stringify({
    ...assessment, observations: [{ ...ref, path: '/rows/1},{' }],
  }), sources), /Invalid segment: "1},\{"\. Valid paths here: "\/rows\/0", "\/rows\/1"/)
})

test('only Sleuth can submit the structured RCA completion tool', () => {
  assert.match(JSON.stringify(agentDefinition('rca', 'test').tools), /complete_rca_assessment/)
  for (const role of ['qa', 'work-order', 'supervisor'] as const) assert.doesNotMatch(JSON.stringify(agentDefinition(role, 'test').tools), /complete_rca_assessment/)
})

test('work-order priority comes from an explicit operator directive or defaults to Medium', () => {
  assert.equal(workOrderPriorityForRequest('Investigate BAD readings and prepare an inspection draft.'), 'Medium')
  assert.equal(workOrderPriorityForRequest('List high-priority open orders, then prepare an inspection draft.'), 'Medium')
  assert.equal(workOrderPriorityForRequest('List open orders where priority is High; then prepare an inspection draft.'), 'Medium')
  assert.equal(workOrderPriorityForRequest('Prepare an inspection draft after checking high-priority existing work.'), 'Medium')
  assert.equal(workOrderPriorityForRequest('Create a work order for T005 with priority High.'), 'High')
  assert.equal(workOrderPriorityForRequest('Create a work order and set its priority to Low.'), 'Low')
  assert.equal(workOrderPriorityForRequest('List Draft work orders of priority High; then prepare an inspection draft.'), 'Medium')
  assert.equal(workOrderPriorityForRequest('Prepare a report of orders where priority is High, then create a work order.'), 'Medium')
  assert.equal(workOrderPriorityForRequest('Prepare one editable Low-priority inspection work-order draft.'), 'Low')
  assert.equal(workOrderPriorityForRequest('Create a work order. Priority: Critical.'), 'Critical')
  assert.equal(workOrderPriorityForRequest('Do not create a high-priority work order.'), 'Medium')
  assert.throws(() => workOrderPriorityForRequest('Create a Low-priority work order. Priority: High.'), /conflicting priorities/)
})

test('source-rendered workflow preserves open Draft coverage and an explicitly unsent message', () => {
  const source: EvidenceReceipt = { id: 'call_work', tool: 'query_operations', entity: 'work_orders', completedAt: receipts[0].completedAt,
    result: { rows: [
      { workOrderNumber: 'WO-1', equipmentId: 'EQUIP_RTI_T005', title: 'Inspect | sensor', status: 'Draft', priority: 'Medium' },
      { workOrderNumber: 'WO-2', equipmentId: 'EQUIP_RTI_T005', title: 'Old work', status: 'Completed', priority: 'Low' },
    ] } }
  const text = renderOpenWorkEvidence([source, source])
  assert.equal((text.match(/WO-1/g) ?? []).length, 1)
  assert.doesNotMatch(text, /WO-2/)
  assert.match(text, /Draft/)
  assert.equal(renderOpenWorkEvidence([source, { ...source, result: { rows: [
    { workOrderNumber: 'WO-1', status: 'Completed' },
  ] } }]), '')
  assert.match(renderUnsentNotification([source]), /EQUIP\\_RTI\\_T005/)
  assert.match(renderUnsentNotification([source]), /not been sent/)
  assert.equal(isNotificationDraftRequest('Investigate T005, then draft a notification. Do not send.'), true)
  assert.equal(isNotificationDraftRequest('Prepare an editable inspection draft only if an uncovered issue is supported. Do not save or send a notification.'), false)
  assert.equal(isNotificationDraftRequest('Verify coverage. Draft a short notification, but do not send it.'), true)
})

test('RCA preserves every snapshot row and measures freshness against the source read clock', () => {
  const snapshot: EvidenceReceipt = { id: 'snapshot', tool: 'query_signal_quality_snapshot', completedAt: '2026-10-08T06:10:00Z',
    result: { read_completed_at_utc: '2026-10-08T06:06:00Z', rows: [
      { equipment_id: 'T001', value: 0, event_time: '2026-10-08T06:04:00Z', open_work_orders: [] },
      { equipment_id: 'T002', value: null, event_time: '2026-10-08T06:05:00Z', open_work_orders: [{ workOrderNumber: 'WO-2', status: 'Draft' }] },
      { equipment_id: 'T003', value: 10, event_time: '2026-10-08T06:06:01Z', open_work_orders: [] },
    ] } }
  const text = renderInventoryEvidence([snapshot, snapshot])
  for (const id of ['T001', 'T002', 'T003', 'WO-2']) assert.equal(text.split(id).length - 1, 1)
  assert.match(text, /Stale/)
  assert.match(text, /Within 60s/)
  assert.match(text, /Uncertain/)
  assert.match(text, /\| null \|/)
  assert.match(text, /All 3 returned rows/)
  assert.throws(() => renderInventoryEvidence([{ ...snapshot, result: { rows: [{ event_time: 'bad' }] } }]), /clock/)
})

test('RCA preserves inventory records not selected as observations and discloses truncation and emptiness', () => {
  const sources: EvidenceReceipt[] = [
    { id: 'parts', tool: 'query_operations', entity: 'spare_parts', completedAt: receipts[0].completedAt,
      result: { rows: [{ partNumber: 'P-1', quantityOnHand: 0, reorderLevel: 2 }, { partNumber: 'P-2', quantityOnHand: 3, reorderLevel: 5 }], truncated: true } },
    { id: 'notices', tool: 'query_operations', entity: 'notifications', completedAt: receipts[0].completedAt, result: { rows: [] } },
  ]
  const text = renderInventoryEvidence(sources)
  assert.match(text, /P-1/)
  assert.match(text, /P-2/)
  assert.match(text, /Truncated source/)
  assert.match(text, /No rows returned/)
  assert.match(text, /broader reads can include records outside/)
  assert.throws(() => renderInventoryEvidence([{ ...sources[0], result: { rows: [null] } }]), /valid source rows/)
})
