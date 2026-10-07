import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createWorkOrderProposal, isWorkOrderRequest,
} from '../src/services/copilot/orchestration.ts'
import { buildQualitySnapshotQuery } from '../src/services/copilot/query.ts'

test('only explicit work-order requests leave the read-only Data Agent path', () => {
  assert.equal(isWorkOrderRequest('Which turbines are running hot right now?'), false)
  assert.equal(isWorkOrderRequest('Why did T004 temperature spike? Perform an RCA.'), false)
  assert.equal(isWorkOrderRequest('Create a work order for T004.'), true)
  assert.equal(isWorkOrderRequest('Ask the Data Agent for open work orders.'), false)
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
