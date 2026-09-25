export const FREQUENCY_POLL_MS = 5000
export const FREQUENCY_STALE_MS = 30_000
export const FREQUENCY_WINDOW_MS = 120_000
export type LiveFrequencyReading = { hz: number; observedAtMs: number; receivedAtMs: number }

export function buildLiveFrequencyQuery(now: number): string {
  if (!Number.isSafeInteger(now) || now < FREQUENCY_WINDOW_MS) throw new Error('Invalid live-frequency request time.')
  const url = `https://driftsdata.statnett.no/restapi/Frequency/BySecondWithXy?FromInTicks=${now - FREQUENCY_WINDOW_MS}&ToInTicks=${now}`
  return `externaldata(Measurements:dynamic)[${JSON.stringify(url)}]
with(format='multijson', ingestionMapping='[{"Column":"Measurements","Properties":{"Path":"$.Measurements"}}]')
| mv-expand measurement = Measurements
| project observed_at_ms = tolong(measurement[0]), frequency_hz = todouble(measurement[1])
| top 1 by observed_at_ms desc`
}

export function parseLiveFrequency(rows: Record<string, unknown>[], requestedAtMs: number, receivedAtMs: number): LiveFrequencyReading {
  if (rows.length !== 1) throw new Error('Statnett returned no recent frequency sample.')
  const { observed_at_ms: observed, frequency_hz: hz } = rows[0]
  if (typeof observed !== 'number' || !Number.isSafeInteger(observed)
    || observed < requestedAtMs - FREQUENCY_WINDOW_MS || observed > receivedAtMs + 5000
    || typeof hz !== 'number' || !Number.isFinite(hz) || hz <= 0 || hz >= 100) {
    throw new Error('Statnett returned an invalid frequency sample.')
  }
  return { hz, observedAtMs: observed, receivedAtMs }
}

export function frequencyRetryDelay(failures: number): number {
  return Math.min(60_000, FREQUENCY_POLL_MS * 2 ** Math.min(Math.max(failures, 0), 4))
}
