import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createWorkOrderProposal, isWorkOrderRequest, missingRequestedSpecialists,
} from '../src/services/copilot/orchestration.ts'
import { buildQualitySnapshotQuery, buildTemperatureSnapshotQuery, rankTemperatureRows, validateKql, KqlValidationError } from '../src/services/copilot/query.ts'

test('only explicit work-order requests leave the read-only Data Agent path', () => {
  assert.equal(isWorkOrderRequest('Which turbines are running hot right now?'), false)
  assert.equal(isWorkOrderRequest('Why did T004 temperature spike? Perform an RCA.'), false)
  assert.equal(isWorkOrderRequest('Create a work order for T004.'), true)
  assert.equal(isWorkOrderRequest('Ask the Data Agent for open work orders.'), false)
})

test('draft requests route to approval while reading Draft orders remains read-only', () => {
  for (const prompt of ['Prepare a Low priority Draft work order for T005.', 'Propose work orders for these assets.',
    'Draft a work order for T005.', 'Please open a new work order.', 'Draft work orders for T005 and T007.']) {
    assert.equal(isWorkOrderRequest(prompt), true, prompt)
  }
  for (const prompt of ['List Draft work orders.', 'What work is already open?', 'Do not create a work order. Show existing work.',
    "Don't draft a work order for T005."]) assert.equal(isWorkOrderRequest(prompt), false, prompt)
})

test('compound completion requires real specialist work, including verification after draft review', () => {
  const prompt = 'Investigate it. Prepare an editable inspection draft only for an evidence-backed gap. Finally, independently check identity and coverage. Do not save a work order.'
  assert.equal(isWorkOrderRequest(prompt), true)
  assert.deepEqual(missingRequestedSpecialists(prompt, ['qa']), ['rca', 'work-order', 'qa'])
  assert.deepEqual(missingRequestedSpecialists(prompt, ['qa', 'rca', 'work-order']), ['qa'])
  assert.deepEqual(missingRequestedSpecialists(prompt, ['qa', 'rca', 'work-order', 'qa']), [])
  assert.deepEqual(missingRequestedSpecialists('Show all open work orders.', ['qa']), [])
  assert.equal(isWorkOrderRequest('Do not create a work order, but prepare an editable inspection draft.'), true)
  assert.equal(isWorkOrderRequest('Inspect existing work orders. Prepare an editable follow-up draft only for a gap.'), true)
  assert.equal(isWorkOrderRequest('Prepare a table of Draft work orders.'), false)
  assert.deepEqual(missingRequestedSpecialists('Show work orders. Do not investigate or independently verify anything.', ['qa']), [])
})

test('today uses midnight UTC and local KQL rejection is distinct from runtime failures', () => {
  for (const query of [buildQualitySnapshotQuery('BAD', 'today'), buildTemperatureSnapshotQuery('today')]) {
    assert.match(query, /event_time >= startofday\(now\(\)\)/)
    assert.doesNotMatch(query, /ago\(today\)/)
  }
  assert.throws(() => validateKql('let x = 1; OPCUAEvents', ['OPCUAEvents']), KqlValidationError)
  assert.equal(new Error('Eventhouse HTTP 400') instanceof KqlValidationError, false)
})

test('work-order proposal validates required operational fields', () => {
  const proposal = createWorkOrderProposal({
    equipmentId: 'EQUIP_RTI_T004',
    title: 'Inspect turbine temperature',
    description: 'Latest temperature exceeded the operator-supplied threshold.',
    priority: 'High',
  })

  assert.equal(proposal.equipmentId, 'EQUIP_RTI_T004')
  assert.equal(proposal.priority, 'High')
  assert.match(proposal.id, /^wo-/)
  assert.throws(() => createWorkOrderProposal({
    equipmentId: 'EQUIP_RTI_T004',
    title: 'Inspect turbine temperature',
    priority: 'Urgent',
  }), /priority must be/)
})

test('quality snapshot selects latest rows before filtering quality without a result cap', () => {
  const query = buildQualitySnapshotQuery('BAD', '30m')
  const latest = query.indexOf('summarize arg_max(event_time, value, quality) by opcua_node_id')
  const quality = query.indexOf("where toupper(quality) == 'BAD'")
  assert.ok(latest >= 0)
  assert.ok(quality > latest)
  assert.doesNotMatch(query, /\b(top|take)\s+\d+/i)
  assert.throws(() => buildQualitySnapshotQuery('FAILED', '30m'), /Invalid quality/)
  assert.throws(() => buildQualitySnapshotQuery('BAD', '30 minutes'), /Invalid lookback/)
})

test('temperature snapshot selects raw latest readings without averages or quality exclusions', () => {
  const query = buildTemperatureSnapshotQuery('6h')
  assert.match(query, /event_time > ago\(6h\)/)
  assert.match(query, /summarize arg_max\(event_time, value, quality\) by opcua_node_id/)
  assert.match(query, /opcua_node_id endswith '\.turbine_temp'/)
  assert.doesNotMatch(query, /\b(avg|bin|top|take)\s*\(|where.*quality/i)
  assert.throws(() => buildTemperatureSnapshotQuery('6 hours'), /Invalid lookback/)
})

test('temperature ranking keeps the actual top five, including equipment with other BAD signals', () => {
  const rows = [
    { equipment_id: 'T005', value: 81.264, quality: 'GOOD' },
    { equipment_id: 'T015', value: 82.800, other_signal_quality: 'BAD' },
    { equipment_id: 'T012', value: 82.147, quality: 'GOOD' },
    { equipment_id: 'T002', value: 86.844, quality: 'GOOD' },
    { equipment_id: 'T007', value: 87.296, other_signal_quality: 'BAD' },
    { equipment_id: 'T010', value: 88.812, quality: 'UNCERTAIN' },
  ]
  assert.deepEqual(rankTemperatureRows(rows).map(row => row.equipment_id), ['T010', 'T007', 'T002', 'T015', 'T012'])
  assert.equal(rows[0].equipment_id, 'T005')
  assert.equal(rankTemperatureRows(rows, { limit: 2 }).length, 2)
  assert.equal(rankTemperatureRows(rows, { threshold: 80 }).length, 6)
  assert.equal(rankTemperatureRows(rows, { threshold: 82.8 }).length, 3)
  assert.equal(rankTemperatureRows(rows, { threshold: 82.8, threshold_operator: 'gte' }).length, 4)
})

test('temperature ranking rejects invalid inputs and never substitutes zero for missing readings', () => {
  assert.throws(() => rankTemperatureRows([{ equipment_id: 'T1', value: null }]), /non-numeric/)
  assert.throws(() => rankTemperatureRows([], { limit: 0 }), /positive integer/)
  assert.throws(() => rankTemperatureRows([], { limit: 1.5 }), /positive integer/)
  assert.throws(() => rankTemperatureRows([], { threshold: NaN }), /finite number/)
  assert.throws(() => rankTemperatureRows([], { threshold_operator: 'unknown' }), /gt or gte/)
  assert.deepEqual(rankTemperatureRows([]), [])
})
