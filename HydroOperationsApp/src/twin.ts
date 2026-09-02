// Digital-twin signal model + health mapping. Kept dependency-free (no three.js /
// model-viewer) so App.tsx can compute twin health without eager-loading the 3D viewer.

export type TwinSignal = {
  id: string
  label: string
  nodeId: string
  value?: number | string
  unit?: string
  quality?: string
  hasOpenIssue?: boolean
  // ISO time of the reading itself (from the Eventhouse), used to show how stale the value is.
  eventTime?: string
}

export type TwinStatus = 'crit' | 'warn' | 'ok' | 'nodata'

export function twinStatus(signal: TwinSignal): TwinStatus {
  const quality = (signal.quality ?? '').toLowerCase()
  if (quality === 'bad' || signal.hasOpenIssue) return 'crit'
  if (quality === 'uncertain') return 'warn'
  if (signal.value === undefined || signal.value === null || signal.value === '') return 'nodata'
  return 'ok'
}

export const twinValueText = (signal: TwinSignal): string =>
  signal.value === undefined || signal.value === null || signal.value === ''
    ? '—'
    : `${signal.value}${signal.unit ? ` ${signal.unit}` : ''}`

// Freshness of a live reading, measured against the reading's own event time (NOT the fetch time)
// so a dead stream reads as stale even while the app keeps polling. Tiers are tuned to a ~10s poll:
// live = arriving now, recent = last couple minutes, stale = up to 15 min, dead = older/none.
export type Freshness = 'live' | 'recent' | 'stale' | 'dead'
export function freshnessOf(eventTime: string | undefined, nowMs: number = Date.now()): Freshness {
  if (!eventTime) return 'dead'
  const ms = nowMs - Date.parse(eventTime)
  if (Number.isNaN(ms)) return 'dead'
  if (ms < 30_000) return 'live'
  if (ms < 120_000) return 'recent'
  if (ms < 900_000) return 'stale'
  return 'dead'
}

// Compact "how long ago" label with seconds granularity while fresh (e.g. "8s ago", "36m ago").
export function ageLabel(eventTime: string | undefined, nowMs: number = Date.now()): string {
  if (!eventTime) return 'no data'
  const ms = nowMs - Date.parse(eventTime)
  if (Number.isNaN(ms)) return 'no data'
  if (ms < 5000) return 'just now'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}
