import assert from 'node:assert/strict'
import test from 'node:test'
import { stationSnapshot, stationPowerComparison } from '../src/services/copilot/stationComparison.ts'
import { STATION_POWER_SEMANTICS } from '../src/services/copilot/query.ts'

const source = (overrides = {}) => ({
  source_key: 'verified-workspace:cluster:database', lookback: '24h', semantics: STATION_POWER_SEMANTICS,
  read_completed_at_utc: '2026-10-08T06:00:00Z',
  rows: [{ Station: 'Sloy', average_power_MW: 123.45, samples: 4000, bad_samples: 20, latest_event_time: '2026-10-08T05:00:00.0000000Z' }],
  ...overrides,
})

test('comparison derives mean/count deltas and retains original clocks and semantics', () => {
  const previous = stationSnapshot(source())
  const current = stationSnapshot(source({
    read_completed_at_utc: '2026-10-08T06:01:00Z',
    rows: [{ ...source().rows[0], average_power_MW: 124, samples: 4010, bad_samples: 22 }],
  }))
  const text = stationPowerComparison(previous, current)
  assert.match(text, /\| Sloy \| 123\.45 \| 124 \| 0\.55 \| 4000 \/ 4010 \| 20 \/ 22 \|/)
  assert.match(text, /2026-10-08T05:00:00\.0000000Z/)
  assert.match(text, /Stale/)
  assert.match(text, /not total station output/)
  assert.match(text, /neither an arithmetic error nor a physical fault/)
})

test('unreturned and newly returned stations are never assumed to be zero', () => {
  const current = source({ rows: [{ ...source().rows[0], Station: 'Foyers' }] })
  const text = stationPowerComparison(stationSnapshot(source()), stationSnapshot(current))
  assert.match(text, /\| Foyers \| Not returned \| 123\.45 \| Not comparable/)
  assert.match(text, /\| Sloy \| 123\.45 \| Not returned \| Not comparable/)
})

test('different source identities, windows and reversed clocks reject numerical reconciliation', () => {
  for (const changed of [
    { source_key: 'other-workspace:cluster:database' }, { lookback: '7d' },
    { read_completed_at_utc: '2026-10-08T05:59:59Z' },
  ]) assert.throws(() => stationPowerComparison(stationSnapshot(source()), stationSnapshot(source(changed))))
})

test('snapshot requires complete source-bound metadata and distinct valid station rows', () => {
  for (const changed of [
    { source_key: undefined }, { semantics: 'Total station generation' },
    { read_completed_at_utc: 'unknown' }, { rows: [source().rows[0], source().rows[0]] },
    { rows: [{ ...source().rows[0], samples: 0 }] },
    { rows: [{ ...source().rows[0], bad_samples: 4001 }] },
    { rows: [{ ...source().rows[0], average_power_MW: NaN }] },
  ]) assert.throws(() => stationSnapshot(source(changed)))
})

test('captured rows do not change when the underlying source result is mutated', () => {
  const input = source()
  const captured = stationSnapshot(input)
  input.rows[0].average_power_MW = 999
  assert.equal(captured.rows[0].average_power_MW, 123.45)
})

test('non-finite subtraction cannot produce a successful difference table', () => {
  const previous = source({ rows: [{ ...source().rows[0], average_power_MW: -1e308 }] })
  const current = source({ rows: [{ ...source().rows[0], average_power_MW: 1e308 }] })
  assert.throws(() => stationPowerComparison(stationSnapshot(previous), stationSnapshot(current)), /finite range/)
})
