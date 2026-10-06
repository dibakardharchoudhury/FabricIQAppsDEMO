import assert from 'node:assert/strict'
import test from 'node:test'
import { buildLiveFrequencyQuery, FREQUENCY_POLL_MS, frequencyRetryDelay, parseLiveFrequency } from '../src/ui-shared/liveFrequencyModel.ts'

const now = Date.parse('2026-09-24T12:30:00Z')

test('live frequency queries Statnett through Fabric rather than the stored snapshot', () => {
  const query = buildLiveFrequencyQuery(now)
  assert.match(query, /externaldata\(Measurements:dynamic\)/)
  assert.ok(query.includes(`FromInTicks=${now - 120_000}&ToInTicks=${now}`))
  assert.match(query, /ingestionMapping=.*Measurements/)
  assert.match(query, /mv-expand measurement = Measurements/)
  assert.match(query, /top 1 by observed_at_ms desc/)
  assert.ok(!query.includes('HydroGeoFeatures'))
  assert.throws(() => buildLiveFrequencyQuery(NaN), /Invalid/)
  assert.throws(() => buildLiveFrequencyQuery(-1), /Invalid/)
})

test('live readings preserve provider observation time and do not manufacture freshness', () => {
  assert.deepEqual(parseLiveFrequency([{ observed_at_ms: now - 1000, frequency_hz: 50.013 }], now, now + 500), {
    hz: 50.013, observedAtMs: now - 1000, receivedAtMs: now + 500,
  })
  assert.equal(parseLiveFrequency([{ observed_at_ms: now - 60_000, frequency_hz: 49.99 }], now, now).observedAtMs, now - 60_000)
})

test('empty, stale-outside-window, future and malformed samples fail explicitly', () => {
  assert.throws(() => parseLiveFrequency([], now, now), /no recent/)
  for (const row of [
    { observed_at_ms: now - 121_000, frequency_hz: 50 },
    { observed_at_ms: now + 6000, frequency_hz: 50 },
    { observed_at_ms: now, frequency_hz: null },
    { observed_at_ms: now, frequency_hz: 0 },
    { observed_at_ms: now, frequency_hz: Infinity },
    { observed_at_ms: 'now', frequency_hz: 50 },
  ]) assert.throws(() => parseLiveFrequency([row], now, now), /invalid/)
})

test('polling backs off on failures rather than hammering the source', () => {
  assert.equal(FREQUENCY_POLL_MS, 5000)
  assert.deepEqual([0, 1, 2, 3, 4, 10].map(frequencyRetryDelay), [5000, 10000, 20000, 40000, 60000, 60000])
})
