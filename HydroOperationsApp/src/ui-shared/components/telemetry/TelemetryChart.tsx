import type { TelemetryHistoryPoint, TelemetryHistoryRange } from '../../../services/fabric'
import { formatHistoryDuration, timeTicks, toChartPoints } from './chartGeometry'

const WIDTH = 1180
const HEIGHT = 300
const PAD = 34

export function TelemetryChart({ points, unit, range }: { points: TelemetryHistoryPoint[]; unit?: string; range: TelemetryHistoryRange }) {
  const ordered = toChartPoints(points)
  const values = ordered.map(point => point.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = Math.max(1, max - min)
  const firstMs = ordered[0]?.timeMs ?? 0
  const lastMs = ordered[ordered.length - 1]?.timeMs ?? firstMs
  const timeSpan = Math.max(1, lastMs - firstMs)
  const x = (timeMs: number) => ordered.length <= 1 ? WIDTH / 2 : PAD + ((timeMs - firstMs) / timeSpan) * (WIDTH - PAD * 2)
  const y = (value: number) => HEIGHT - PAD - ((value - min) / span) * (HEIGHT - PAD * 2)
  const line = ordered.map(point => `${x(point.timeMs)},${y(point.value)}`).join(' ')
  const badPoints = ordered.filter(point => point.quality.toLowerCase() === 'bad')
  const ticks = timeTicks(ordered)

  return <div className="v2-chart-wrap">
    <div className="v2-chart-coverage">
      <span>Selected range: {range}</span>
      <span>Available history: {formatHistoryDuration(firstMs, lastMs)}</span>
      <span>{ordered.length} aggregated points</span>
    </div>
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="v2-telemetry-chart" role="img" aria-label={`Telemetry history chart${unit ? ` in ${unit}` : ''}`}>
      <line className="axis" x1={PAD} y1={PAD} x2={PAD} y2={HEIGHT - PAD} />
      <line className="axis" x1={PAD} y1={HEIGHT - PAD} x2={WIDTH - PAD} y2={HEIGHT - PAD} />
      <text x={PAD} y={PAD - 10}>{max.toFixed(1)}{unit ? ` ${unit}` : ''}</text>
      <text x={PAD} y={HEIGHT - 8}>{min.toFixed(1)}{unit ? ` ${unit}` : ''}</text>
      {ticks.map(tick => <g key={tick.timeMs} className="tick">
        <line x1={x(tick.timeMs)} y1={HEIGHT - PAD} x2={x(tick.timeMs)} y2={HEIGHT - PAD + 5} />
        <text x={x(tick.timeMs)} y={HEIGHT - 14} textAnchor="middle">{tick.label}</text>
      </g>)}
      <polyline className="series" points={line} />
      {badPoints.map(point => <circle key={`bad-${point.eventTime}`} className="issue bad" cx={x(point.timeMs)} cy={y(point.value)} r={4.2}>
        <title>BAD quality issue - {new Date(point.eventTime).toLocaleString()}</title>
      </circle>)}
    </svg>
    <div className="v2-chart-foot"><span>{ordered.length} aggregated points</span><span>{badPoints.length} BAD interval{badPoints.length === 1 ? '' : 's'}</span></div>
    <div className="v2-chart-legend" aria-label="Telemetry quality legend">
      <span><i className="line" />Teal line = telemetry value</span>
      <span><i className="bad" />Red point = interval contained BAD reading</span>
    </div>
  </div>
}
