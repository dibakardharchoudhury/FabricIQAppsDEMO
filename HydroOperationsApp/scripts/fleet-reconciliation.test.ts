import assert from 'node:assert/strict'
import test from 'node:test'
import { fleetComparisonScope, missingFleetSnapshots, nativeComparisonText, readNativeDatasets, renderFleetReconciliation } from '../src/services/copilot/fleetReconciliation.ts'
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

test('native bullets compare literal BAD/work identities but do not fabricate missing temperature nodes', () => {
  const bullets = `## Latest BAD
- **T001** (\`EQUIP_RTI_T001\`)
  - OPC node: \`ns=2;s=T001.turbine_temp\`
  - Latest value: **85 C**
  - Quality: **BAD**
  - Signal timestamp: **2026-10-08 06:05:01 UTC**

## Highest temperatures
1. **T001** (\`EQUIP_RTI_T001\`)
  - Temperature signal: \`INST_T001_TURBINE_TEMP\`
  - Raw reading: **85 C**
  - Quality: **BAD**
  - Reading timestamp: **2026-10-08 06:05:01 UTC**

## Open work
- **T003** (\`EQUIP_RTI_T003\`): **None**
- **T001** (\`EQUIP_RTI_T001\`)
  - \`WO-123\` \u2014 **Inspect signal**; Status: **Draft**; Priority: **Low**`
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: bullets }])
  assert.match(output, /Native table rows: 1; direct selected rows: 2/)
  assert.match(output, /timestamp\/precision differs/)
  assert.match(output, /Highest temperatures: identity is not comparable/)
  assert.doesNotMatch(output, /Expected one native BAD-quality table|Work comparison incomplete/)
  assert.match(output, /EQUIP\\_RTI\\_T001:WO-123/)
  assert.match(renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{
    ...native[0], output: bullets.replace('**T001** (`EQUIP_RTI_T001`)', '**T001** (`EQUIP_RTI_T001` / `EQUIP_RTI_T002`)'),
  }]), /identity is not comparable/)
})

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

test('explicit combined instrument/node cells preserve the literal node without inferring its identity', () => {
  const combined = text.replaceAll('Signal ID', 'instrument_id / opcua_node_id')
    .replace(/\| (ns=\d+;s=[^ |]+)/g, '| INST_EXPLICIT / $1')
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: combined }])
  assert.match(output, /Native table rows: 1; direct selected rows: 2/)
  assert.match(output, /Native table rows: 2; direct selected rows: 2/)
  assert.match(output, /timestamp\/precision differs/)
  assert.doesNotMatch(output, /Expected one native BAD-quality table/)
  const ambiguous = combined.replaceAll('INST_EXPLICIT /', 'INST_EXPLICIT / ns=2;s=Other /')
  assert.match(renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: ambiguous }]), /identity is not comparable/)
})

test('native JSON sets preserve actual identities, raw values and timestamp precision for comparison', () => {
  const signal = {
    equipment_id: 'EQUIP_RTI_T001', signal_id_opc_node: 'ns=2;s=T001.turbine_temp',
    latest_value: 85, unit: 'C', quality: 'BAD', timestamp_utc: '2026-10-08T06:05:01Z',
  }
  const sets = [
    { set: 'latest_bad_signals', row_count: 1, rows: [signal] },
    { set: 'top_5_latest_raw_temperature', row_count: 1, rows: [{ rank: 1, ...signal }] },
    { set: 'open_work_orders_for_returned_turbines', row_count: 1, rows: [{
      equipment_id: 'EQUIP_RTI_T001', work_order_number: 'WO-123', title: 'Inspect signal', status: 'Draft', priority: 'Low',
    }] },
  ].map(set => '```json\n' + JSON.stringify(set) + '\n```').join('\n')
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: { response: sets } }])
  assert.doesNotMatch(output, /comparison incomplete|population not comparable/i)
  assert.match(output, /timestamp\/precision differs/)
  assert.match(output, /Not in returned native table/)
  assert.match(output, /Text differs: title/)
  assert.match(output, /Stale/)
  assert.doesNotMatch(output, /Returned fields match/)
})

test('native business column labels preserve raw values, latest timestamps and linked work identities', () => {
  const observed = text.replaceAll('Value', 'Raw value').replaceAll('Reading', 'Latest temperature')
    .replaceAll('Event time (UTC)', 'Latest timestamp (UTC)')
    + '\n### Open work\n| Order number | Title | Status | Priority | Linked equipment_id |\n'
    + '|---|---|---|---|---|\n| WO-123 | Inspect signal | Draft | Low | EQUIP_RTI_T001 |'
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: observed }])
  assert.doesNotMatch(output, /comparison incomplete|population not comparable/i)
  assert.match(output, /timestamp\/precision differs/)
  assert.match(output, /value differs/)
  assert.match(output, /Text differs: title/)
  assert.match(output, /Not in returned native table/)
})

test('instrument IDs cannot override literal node columns and inline units remain explicit', () => {
  const observed = `### BAD
| Equipment ID | Signal ID | Signal node | Last value | Quality | Reading time (UTC) |
|---|---|---|---|---|---|
| EQUIP_RTI_T001 | INST_T001_TURBINE_TEMP | ns=2;s=T001.turbine_temp | 85 | BAD | 2026-10-08 06:05:01 |
### Hottest temperatures
| Rank | Equipment ID | Signal ID | Signal node | Latest temperature | Reading time (UTC) |
|---|---|---|---|---|---|
| 1 | EQUIP_RTI_T001 | INST_T001_TURBINE_TEMP | ns=2;s=T001.turbine_temp | 85 C | 2026-10-08 06:05:01 |`
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: observed }])
  assert.doesNotMatch(output, /Expected one native (BAD-quality|ranked temperature)|Native population not comparable|value not comparable|unit differs/)
  assert.match(output, /timestamp\/precision differs/)
  const ambiguous = observed.replaceAll('INST_T001_TURBINE_TEMP', 'ns=2;s=Different.turbine_temp')
  assert.match(renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: ambiguous }]), /Comparison incomplete/)
  const instrumentOnly = observed.replaceAll('ns=2;s=T001.turbine_temp', 'INST_T001_TURBINE_TEMP')
  assert.match(renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: instrumentOnly }]), /Comparison incomplete/)
})

test('contradictory inline and column units are not silently reconciled', () => {
  const conflicting = text.replace('| 90 | C |', '| 90 F | C |')
  const output = renderFleetReconciliation(scope, [receipt(false), receipt(true)], [{ ...native[0], output: conflicting }])
  assert.match(output, /inline value unit conflicts/)
  assert.match(output, /Native population not comparable/)
})

test('native JSON malformed, nested, inconsistent, empty and count-mismatched sets stay unverified', () => {
  for (const content of [
    '{"rows":',
    JSON.stringify({ rows: [{ value: { nested: 1 } }] }),
    JSON.stringify({ rows: [{ value: 1 }, { different: 2 }] }),
    JSON.stringify({ rows: [] }),
    JSON.stringify({ rows: [{ value: 1 }], row_count: 2 }),
  ]) {
    const parsed = readNativeDatasets('```json\n' + content + '\n```')
    assert.equal(parsed.datasets.length, 0)
    assert.equal(parsed.issues.length, 1)
  }
  const parsed = readNativeDatasets('```json\n{"rows":[{"id":"T001","value":null}]}\n```')
  assert.equal(parsed.datasets.length, 1)
  assert.deepEqual(parsed.datasets[0].rows, [['T001', '']])
  const incomplete = readNativeDatasets('```json\n{"rows":[{"id":"T001","value":12}]}')
  assert.deepEqual(incomplete.datasets, [])
  assert.match(incomplete.issues.join(' '), /incomplete/)
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
