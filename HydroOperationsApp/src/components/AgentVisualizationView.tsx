import { useMemo } from 'react'
import Papa from 'papaparse'
import type { AgentVisualization } from '../services/fabric'

const COLORS = ['#2f9e8f', '#3978d4', '#e38b2c', '#d94f70', '#7667c5', '#218c5b', '#b65c9a', '#687487']

type Point = { label: string; x: number; value: number }
type Series = { name: string; points: Point[]; color: string }

type ParsedVisualization = {
  series: Series[]
  xIsTime: boolean
  xMinimum: number
  xMaximum: number
  yMinimum: number
  yMaximum: number
}

function parseVisualization(spec: AgentVisualization): ParsedVisualization | null {
  const result = Papa.parse<Record<string, string>>(spec.inlineCsvData, { header: true, skipEmptyLines: true })
  if (!result.data.length) return null
  const rawX = result.data.map(row => row[spec.xColumn] ?? '')
  const timestamps = rawX.map(value => Date.parse(value))
  const xIsTime = timestamps.every(Number.isFinite)
  const numericX = rawX.map(value => Number(value))
  const xIsNumeric = !xIsTime && numericX.every(Number.isFinite)
  const grouped = new Map<string, Point[]>()

  result.data.forEach((row, rowIndex) => {
    for (const yColumn of spec.yColumns) {
      const value = Number(row[yColumn])
      if (!Number.isFinite(value)) continue
      const group = spec.groupBy ? row[spec.groupBy] : undefined
      const name = group ? `${group}${spec.yColumns.length > 1 ? ` · ${yColumn}` : ''}` : yColumn
      const point: Point = {
        label: row[spec.xColumn] ?? '',
        x: xIsTime ? timestamps[rowIndex] : xIsNumeric ? numericX[rowIndex] : rowIndex,
        value,
      }
      grouped.set(name, [...(grouped.get(name) ?? []), point])
    }
  })

  const direction = spec.sortOrder?.toLowerCase() === 'desc' ? -1 : 1
  const series = [...grouped.entries()].map(([name, points], index) => ({
    name,
    color: COLORS[index % COLORS.length],
    points: [...points].sort((left, right) => (left.x - right.x) * direction),
  }))
  const allPoints = series.flatMap(value => value.points)
  if (!allPoints.length) return null
  const xValues = allPoints.map(point => point.x)
  const yValues = allPoints.map(point => point.value)
  const yMinimum = Math.min(0, ...yValues)
  const yMaximum = Math.max(...yValues)
  return {
    series,
    xIsTime,
    xMinimum: Math.min(...xValues),
    xMaximum: Math.max(...xValues),
    yMinimum,
    yMaximum: yMaximum === yMinimum ? yMaximum + 1 : yMaximum,
  }
}

export function AgentVisualizationView({ spec }: { spec: AgentVisualization }) {
  const parsed = useMemo(() => parseVisualization(spec), [spec])
  if (!parsed) return null

  const width = 900
  const height = 390
  const left = 72
  const right = 24
  const top = 28
  const bottom = 72
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const xRange = parsed.xMaximum - parsed.xMinimum || 1
  const yRange = parsed.yMaximum - parsed.yMinimum || 1
  const x = (value: number) => left + (value - parsed.xMinimum) / xRange * plotWidth
  const y = (value: number) => top + plotHeight - (value - parsed.yMinimum) / yRange * plotHeight
  const chartType = spec.chartType.toLowerCase()
  const isArea = chartType.includes('area')
  const isScatter = chartType.includes('scatter')
  const isColumn = chartType.includes('column') || chartType.includes('bar')
  const isPie = chartType.includes('pie')
  const formatX = (value: number) => parsed.xIsTime
    ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : Number.isInteger(value) ? String(value) : value.toFixed(1)

  if (isPie) {
    const totals = parsed.series.map(series => ({ ...series, total: series.points.reduce((sum, point) => sum + Math.max(0, point.value), 0) }))
    const total = totals.reduce((sum, series) => sum + series.total, 0)
    if (!total) return null
    const sweeps = totals.map(series => series.total / total * Math.PI * 2)
    const slices = totals.map((series, index) => {
      const start = -Math.PI / 2 + sweeps.slice(0, index).reduce((sum, sweep) => sum + sweep, 0)
      const sweep = series.total / total * Math.PI * 2
      return { ...series, start, end: start + sweep, sweep }
    })
    const centerX = 280
    const centerY = 190
    const radius = 135
    const position = (angle: number) => `${centerX + Math.cos(angle) * radius} ${centerY + Math.sin(angle) * radius}`
    return <figure className="v2-agent-native-chart"><figcaption>{spec.title}</figcaption><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
      {slices.map(slice => <path key={slice.name} d={slice.sweep >= Math.PI * 1.999 ? `M ${centerX - radius} ${centerY} A ${radius} ${radius} 0 1 1 ${centerX + radius} ${centerY} A ${radius} ${radius} 0 1 1 ${centerX - radius} ${centerY}` : `M ${centerX} ${centerY} L ${position(slice.start)} A ${radius} ${radius} 0 ${slice.sweep > Math.PI ? 1 : 0} 1 ${position(slice.end)} Z`} fill={slice.color} />)}
      {slices.map((slice, index) => <g key={`legend-${slice.name}`} transform={`translate(500 ${70 + index * 28})`}><rect width="12" height="12" rx="2" fill={slice.color} /><text x="20" y="11">{slice.name} ({Math.round(slice.total / total * 100)}%)</text></g>)}
    </svg></figure>
  }

  return <figure className="v2-agent-native-chart"><figcaption>{spec.title}</figcaption><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
    {[0, .25, .5, .75, 1].map(fraction => { const value = parsed.yMinimum + yRange * fraction; const position = y(value); return <g key={`y-${fraction}`}><line x1={left} x2={width - right} y1={position} y2={position} className="grid" /><text x={left - 10} y={position + 4} textAnchor="end">{value.toLocaleString(undefined, { maximumFractionDigits: 1 })}</text></g> })}
    {[0, .25, .5, .75, 1].map(fraction => { const value = parsed.xMinimum + xRange * fraction; const position = x(value); return <g key={`x-${fraction}`}><line x1={position} x2={position} y1={top} y2={top + plotHeight} className="grid" /><text x={position} y={top + plotHeight + 20} textAnchor="middle">{formatX(value)}</text></g> })}
    {parsed.series.map((series, seriesIndex) => {
      const points = series.points.map(point => `${x(point.x)},${y(point.value)}`).join(' ')
      if (isColumn) {
        const barWidth = Math.max(3, Math.min(18, plotWidth / Math.max(series.points.length * parsed.series.length, 1) * .7))
        return <g key={series.name}>{series.points.map(point => <rect key={`${point.x}-${point.value}`} x={x(point.x) - barWidth / 2 + seriesIndex * barWidth} y={y(point.value)} width={barWidth} height={y(parsed.yMinimum) - y(point.value)} fill={series.color} />)}</g>
      }
      return <g key={series.name}>{isArea && <polygon points={`${x(series.points[0].x)},${y(parsed.yMinimum)} ${points} ${x(series.points.at(-1)!.x)},${y(parsed.yMinimum)}`} fill={series.color} opacity=".16" />}{!isScatter && <polyline points={points} fill="none" stroke={series.color} strokeWidth="2.5" />}{series.points.map(point => <circle key={`${point.x}-${point.value}`} cx={x(point.x)} cy={y(point.value)} r={isScatter ? 4 : 3} fill={series.color}><title>{series.name}: {point.value} at {point.label}</title></circle>)}</g>
    })}
    <text x={left + plotWidth / 2} y={height - 8} textAnchor="middle" className="axis-title">{spec.xAxisTitle || spec.xColumn}</text>
    <text x="16" y={top + plotHeight / 2} textAnchor="middle" transform={`rotate(-90 16 ${top + plotHeight / 2})`} className="axis-title">{spec.yAxisTitle || spec.yColumns.join(', ')}</text>
    {parsed.series.map((series, index) => <g key={`legend-${series.name}`} transform={`translate(${left + index % 4 * 190} ${height - 40 + Math.floor(index / 4) * 18})`}><line x2="16" y1="6" y2="6" stroke={series.color} strokeWidth="3" /><text x="22" y="10">{series.name}</text></g>)}
  </svg></figure>
}
