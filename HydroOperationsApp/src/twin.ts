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
