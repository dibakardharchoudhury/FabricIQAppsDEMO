import assert from 'node:assert/strict'
import test from 'node:test'
import {
  confirmationProposalId, createWorkOrderProposal, routeAgent,
} from '../src/services/copilot/orchestration.ts'
import { buildQualitySnapshotQuery } from '../src/services/copilot/query.ts'

test('supervisor routes operational questions to bounded specialists', () => {
  assert.equal(routeAgent('Which turbines are running hot right now?'), 'qa')
  assert.equal(routeAgent('Why did T004 temperature spike? Perform an RCA.'), 'rca')
  assert.equal(routeAgent('Create a work order for T004.'), 'work-order')
  assert.equal(routeAgent('Ask the Data Agent for open work orders.'), 'data-agent')
})

test('work-order confirmation must use the exact proposal command', () => {
  assert.equal(confirmationProposalId('Confirm work order wo-abc-123'), 'wo-abc-123')
  assert.equal(confirmationProposalId('yes create it'), undefined)
  assert.equal(routeAgent('Confirm work order wo-abc-123'), 'work-order')
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
