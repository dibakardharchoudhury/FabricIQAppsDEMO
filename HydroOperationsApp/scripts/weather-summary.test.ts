import assert from 'node:assert/strict'
import test from 'node:test'
import { summarizePrecipitation } from '../src/ui-shared/weatherSummary.ts'

const anchor = Date.parse('2026-09-16T09:20:00Z')
const at = (hours: number) => new Date(anchor + hours * 3_600_000).toISOString()

test('excludes partial leading buckets and differences from their cumulative predecessor', () => {
  const summary = summarizePrecipitation([
    { valid_time_utc: at(2), precipitation_interval_hours: 6, precipitation: 5, cumulative_precipitation: 5, rainfall_volume_m3: 50, cumulative_rainfall_volume_m3: 50 },
    { valid_time_utc: at(8), precipitation_interval_hours: 6, precipitation: 6, cumulative_precipitation: 11, rainfall_volume_m3: 60, cumulative_rainfall_volume_m3: 110 },
    { valid_time_utc: at(14), precipitation_interval_hours: 6, precipitation: 4, cumulative_precipitation: 15, rainfall_volume_m3: 40, cumulative_rainfall_volume_m3: 150 },
    { valid_time_utc: at(20), precipitation_interval_hours: 6, precipitation: 3, cumulative_precipitation: 18, rainfall_volume_m3: 30, cumulative_rainfall_volume_m3: 180 },
  ], anchor, 24)
  assert.deepEqual(summary, { amount: 13, volume: 130, coveredHours: 18 })
})

test('ignores missing and zero intervals and never differences the first bucket from zero', () => {
  const summary = summarizePrecipitation([
    { valid_time_utc: at(6), precipitation_interval_hours: 6, precipitation: 3, cumulative_precipitation: 10 },
    { valid_time_utc: at(12), precipitation_interval_hours: 0, precipitation: 7, cumulative_precipitation: 17 },
    { valid_time_utc: at(18), precipitation_interval_hours: null, precipitation: 9, cumulative_precipitation: 26 },
  ], anchor, 24)
  assert.deepEqual(summary, { amount: 3, volume: undefined, coveredHours: 6 })
})
