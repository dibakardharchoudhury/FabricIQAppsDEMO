import assert from 'node:assert/strict'
import test from 'node:test'
import { fleetComparisonScope, missingFleetSnapshots, nativeComparisonText, renderFleetReconciliation } from '../src/services/copilot/fleetReconciliation.ts'
import type { EvidenceReceipt } from '../src/services/copilot/rcaEvidence.ts'

const scope = { quality: true, temperature: true }
const row = (id: string, value: number) => ({
  equipment_id: `EQUIP_RTI_${id}`, opcua_node_id: `ns=2;s=${id}.turbine_temp`,
  value, unit: 'C', quality: 'BAD', event_time: '2026-10-08T06:05:01.123456Z',
  open_work_orders: [{ workOrderNumber: 'WO-123', status: 'Draft', priority: 'Low', relation: 'same-signal' }],
})
const receipt = (temperature: boolean): EvidenceReceipt => ({
  id: temperature ? 'hot' : 'bad',
  tool: temperature ? 'query_turbine_temperature_snapshot' : 'query_signal_quality_snapshot',
  completedAt: '2026-10-08T10:00:00Z',
  result: {
    rows: [row('T001', 85), row('T002', 80)], quality_filter: 'BAD', truncated: false,
    population: { inventory_complete: true, equipment_type: 'turbine', equipment_ids: null,
      expected_signal_count: 3, signals_without_readings: ['ns=2;s=T003.turbine_temp'] },
    lookback: 'today', read_completed_at_utc: '2026-10-08T10:00:00Z', unresolved_nodes: [],
  },
})
const text = `### Latest BAD
| Turbine ID | Signal ID | Value | Quality | Event time (UTC) |
|---|---|---|---|---|
| EQUIP_RTI_T001 (T001) | ns=2;s=T001.turbine_temp | 85 | BAD | 2026-10-08 06:05:01 |
### Highest temperatures
| Rank | Turbine ID | Signal ID | Reading | Unit | Event time (UTC) |
|---|---|---|---|---|---|
| 1 | EQUIP_RTI_T001 | ns=2;s=T001.turbine_temp | 90 | C | 2026-10-08 06:05:01 |
| 2 | EQUIP_RTI_T004 | ns=2;s=T004.turbine_temp | 79 | C | 2026-10-08 06:05:01 |`
const native = [{ id: 'native', source: 'data-agent', completedAt: '2026-10-08T09:59:00Z', output: JSON.stringify({ response: text }) }]

test('comparison requires real unscoped population receipts, not selected-node telemetry', () => {
  assert.deepEqual(fleetComparisonScope('Ask the Data Agent which turbines have BAD signals and hottest temperatures today. Independently verify both sets.'), { ...scope, lookback: 'today' })
  assert.equal(fleetComparisonScope('Show turbine temperatures.'), undefined)
  assert.equal(fleetComparisonScope('Ask the Data Agent for turbine BAD signals. Do not verify them.'), undefined)
  assert.equal(missingFleetSnapshots(scope, []).length, 2)
  assert.equal(missingFleetSnapshots(scope, [{ ...receipt(false), tool: 'query_telemetry' }]).length, 2)
  for (const patch of [
    { truncated: true }, { quality_filter: 'GOOD' },
    { population: { inventory_complete: true, equipment_type: 'pump', equipment_ids: null } },
    { population: { inventory_complete: true, equipment_type: 'turbine', equipment_ids: ['EQUIP_RTI_T001'] } },
    { population: { inventory_complete: false, equipment_type: 'turbine', equipment_ids: null } },
  ]) {
    const base = receipt(false)
    assert.equal(missingFleetSnapshots(scope, [{ ...base, result: { ...Object(base.result), ...patch } }]).length, 2)
  }
})

test('explicit UTC-today and top-five scope cannot be satisfied by another window or rank limit', () => {
  const requested = fleetComparisonScope('Ask the Data Agent which turbines are BAD and the five hottest turbines today. Independently verify.')
  assert.deepEqual(requested, { ...scope, lookback: 'today', temperatureLimit: 5 })
  const bad = receipt(false)
  const hot = receipt(true)
  assert.equal(missingFleetSnapshots(requested!, [bad, hot]).length, 1)
  const validHot = { ...hot, result: { ...Object(hot.result), requested_limit: 5 } }
  assert.equal(missingFleetSnapshots(requested!, [bad, validHot]).length, 0)
  assert.equal(missingFleetSnapshots(requested!, [
    { ...bad, result: { ...Object(bad.result), lookback: '30m' } }, validHot,
  ]).length, 1)
})

test('comparison uses native tool rows and exposes omitted fleet members, different values and precision', () => {
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], native)
  assert.match(output, /Not in returned native table/)
  assert.match(output, /Not in direct selected population/)
  assert.match(output, /value differs/)
  assert.match(output, /timestamp\/precision differs/)
  assert.match(output, /T003\.turbine\\_temp/)
  assert.match(output, /WO-123: Title not returned; Draft, Low, same-signal/)
  assert.match(output, /Stale/)
  assert.match(output, /no work order was created/)
  assert.match(output, /Native signal linkage is not attested/)
  assert.doesNotMatch(output, /Returned fields match/)
})

test('work comparison preserves all direct orders and detects missing native work without claiming it does not exist', () => {
  const orders = `\n### Open work\n| Turbine ID | Work order number | Title | Status | Priority |
|---|---|---|---|---|
| EQUIP_RTI_T001 | WO-123 | Inspect signal | Draft | Low |
| EQUIP_RTI_T004 | WO-native-only | Other work | Planned | High |`
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [
    { ...native[0], output: { response: text + orders } },
  ])
  assert.match(output, /Open-work reconciliation/)
  assert.match(output, /Text differs: title/)
  assert.match(output, /WO-native-only/)
  assert.match(output, /not proven closed or nonexistent/)
  assert.match(output, /EQUIP\\_RTI\\_T002:WO-123/)
})

test('unrecognized, duplicated and absent native rows never produce a matching or empty-population claim', () => {
  for (const output of [undefined, { content: 'unrecognized' }, '{invalid',
    'No BAD data', text.replace(/^(\| EQUIP_RTI_T001 \(T001\).*)$/m, '$1\n$1')]) {
    const report = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output }])
    assert.match(report, /Comparison incomplete/)
    assert.match(report, /Native population not comparable/)
    assert.doesNotMatch(report, /Returned fields match/)
  }
  assert.equal(nativeComparisonText({ response: text }), text)
  assert.throws(() => renderFleetReconciliation(scope, [], native), /missing full-population/)
  assert.throws(() => renderFleetReconciliation(scope, [receipt(false), receipt(true)], []), /no verified native/)
})
