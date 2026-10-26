import { readingFreshness, STATION_POWER_SEMANTICS, type StationPowerRow } from './query.ts'
import { cell } from './rcaEvidence.ts'

export class StationComparisonError extends Error {}

export type StationSnapshot = {
  sourceKey: string
  lookback: string
  readCompletedAt: string
  rows: StationPowerRow[]
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

export function stationSnapshot(result: unknown): StationSnapshot {
  if (!record(result) || typeof result.source_key !== 'string' || !result.source_key
    || typeof result.lookback !== 'string' || !result.lookback || result.semantics !== STATION_POWER_SEMANTICS
    || typeof result.read_completed_at_utc !== 'string' || !Number.isFinite(Date.parse(result.read_completed_at_utc))
    || !Array.isArray(result.rows) || result.rows.length > 500) {
    throw new StationComparisonError('A source-bound structured chart snapshot is unavailable; no historical delta is certified.')
  }
  const rows = result.rows.map((row: unknown): StationPowerRow => {
    if (!record(row) || typeof row.Station !== 'string' || !row.Station.trim()
      || typeof row.average_power_MW !== 'number' || !Number.isFinite(row.average_power_MW)
      || typeof row.samples !== 'number' || !Number.isSafeInteger(row.samples) || row.samples <= 0
      || typeof row.bad_samples !== 'number' || !Number.isSafeInteger(row.bad_samples)
      || row.bad_samples < 0 || row.bad_samples > row.samples
      || typeof row.latest_event_time !== 'string' || !Number.isFinite(Date.parse(row.latest_event_time))) {
      throw new StationComparisonError('Chart values, counts or measurement timestamps are incomplete; no historical delta is certified.')
    }
    return { Station: row.Station, average_power_MW: row.average_power_MW, samples: row.samples,
      bad_samples: row.bad_samples, latest_event_time: row.latest_event_time }
  })
  if (new Set(rows.map(row => row.Station)).size !== rows.length) {
    throw new StationComparisonError('Chart station identities are ambiguous; no historical delta is certified.')
  }
  return { sourceKey: result.source_key, lookback: result.lookback, readCompletedAt: result.read_completed_at_utc, rows }
}

export function stationPowerComparison(previous: StationSnapshot, current: StationSnapshot): string {
  if (previous.sourceKey !== current.sourceKey) throw new StationComparisonError('The source identity changed; previous chart values were not reused.')
  if (previous.lookback !== current.lookback) throw new StationComparisonError('The requested windows differ; a like-for-like historical delta is not certified.')
  if (Date.parse(current.readCompletedAt) < Date.parse(previous.readCompletedAt)) {
    throw new StationComparisonError('The new source clock precedes the previous read; chronological comparison is unavailable.')
  }
  const oldRows = new Map(previous.rows.map(row => [row.Station, row]))
  const newRows = new Map(current.rows.map(row => [row.Station, row]))
  const stations = [...new Set([...oldRows.keys(), ...newRows.keys()])].sort()
  const table = [
    '| Station | Previous mean (MW) | New mean (MW) | Change (MW) | Samples (previous / new) | BAD (previous / new) |',
    '|---|---:|---:|---:|---|---|',
    ...stations.map(station => {
      const old = oldRows.get(station), fresh = newRows.get(station)
      const delta = old && fresh ? fresh.average_power_MW - old.average_power_MW : undefined
      if (delta !== undefined && !Number.isFinite(delta)) throw new StationComparisonError('The numerical delta exceeds the finite range; no comparison is certified.')
      return `| ${cell(station)} | ${old?.average_power_MW ?? 'Not returned'} | ${fresh?.average_power_MW ?? 'Not returned'} | ${delta === undefined ? 'Not comparable' : Number(delta.toPrecision(12))} | ${old?.samples ?? '-'} / ${fresh?.samples ?? '-'} | ${old?.bad_samples ?? '-'} / ${fresh?.bad_samples ?? '-'} |`
    }),
  ].join('\n')
  const clocks = stations.map(station => {
    const old = oldRows.get(station), fresh = newRows.get(station)
    return `- ${cell(station)}: previous ${old ? `${cell(old.latest_event_time)} (${readingFreshness(old.latest_event_time, previous.readCompletedAt)})` : 'not returned'}; new ${fresh ? `${cell(fresh.latest_event_time)} (${readingFreshness(fresh.latest_event_time, current.readCompletedAt)})` : 'not returned'}.`
  }).join('\n')
  return [
    '### Chart verification against the previous answer',
    table,
    `Read clocks (UTC): previous ${cell(previous.readCompletedAt)}; new ${cell(current.readCompletedAt)}. Latest measurement clocks (UTC):\n${clocks}`,
    `Both reads use ${cell(current.lookback)} and MW: a sample-weighted mean of individual readings, including BAD samples, not total station output, a time-weighted mean or energy. The chart shows the new values. The earlier values are historical, not a current source cache. Advancing query windows can change values and counts; differences alone establish neither an arithmetic error nor a physical fault. Missing stations are not zero generation.`,
  ].join('\n\n')
}
