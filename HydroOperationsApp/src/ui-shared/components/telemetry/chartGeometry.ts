import type { TelemetryHistoryPoint } from '../../../services/fabric'

export type ChartPoint = TelemetryHistoryPoint & { timeMs: number }

export function toChartPoints(points: TelemetryHistoryPoint[]): ChartPoint[] {
  return points
    .map(point => ({ ...point, timeMs: Date.parse(point.eventTime) }))
    .filter(point => !Number.isNaN(point.timeMs))
    .sort((a, b) => a.timeMs - b.timeMs)
}

export function timeTicks(points: ChartPoint[]) {
  if (!points.length) return []
  const count = Math.min(4, points.length)
  const indexes = Array.from({ length: count }, (_, index) => Math.round(index * (points.length - 1) / Math.max(1, count - 1)))
  const unique = [...new Set(indexes)]
  const crossesDate = new Date(points[0].timeMs).toDateString() !== new Date(points[points.length - 1].timeMs).toDateString()
  return unique.map(index => {
    const point = points[index]
    const date = new Date(point.timeMs)
    return {
      timeMs: point.timeMs,
      label: date.toLocaleString([], crossesDate ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' } : { hour: '2-digit', minute: '2-digit' }),
    }
  })
}

export function formatHistoryDuration(firstMs: number, lastMs: number) {
  if (!firstMs || !lastMs || lastMs <= firstMs) return 'single point'
  const minutes = Math.round((lastMs - firstMs) / 60000)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`
}
