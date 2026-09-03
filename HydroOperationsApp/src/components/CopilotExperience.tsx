import { useMemo, useState } from 'react'
import { BarChart3, Bot, LineChart, PieChart, Send, SquarePen } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CopilotStreamCursor, CopilotThinking } from './CopilotThinking'

export type CopilotMessage = {
  role: 'user' | 'agent'
  text: string
  meta?: { elapsedMs: number; tokens?: number }
}

type CopilotExperienceProps = {
  messages: CopilotMessage[]
  busy: boolean
  onSend: (question: string) => void
  onReset: () => void
}

export function CopilotExperience({ messages, busy, onSend, onReset }: CopilotExperienceProps) {
  const [question, setQuestion] = useState('')
  const prompts = useMemo(() => [
    'Show all open work orders as a table with the affected asset, priority, and status.',
    'List all equipment with manufacturer, model, and criticality.',
    'Which assets have the most open work orders? Give a ranked table and chart.',
    'Summarize the facilities with their type, country, and number of assets.',
  ], [])

  const send = (value = question) => {
    if (!value.trim() || busy) return
    setQuestion('')
    onSend(value)
  }

  return <div className="v2-domain-page v2-copilot-page">
    <section className="v2-page-head"><div><span className="v2-eyebrow">Fabric Data Agent</span><h1>Operations Copilot</h1><p>Ask grounded questions across facilities, equipment, signals, and operational work.</p></div><Bot size={28} /></section>
    <section className="v2-copilot"><header><span><Bot size={17} /><strong>Hydro Operations</strong><small>Connected Fabric data</small></span><button className="v2-icon-action" type="button" title="New chat" disabled={busy || messages.length === 1} onClick={onReset}><SquarePen size={16} /></button></header>
      <div className="v2-messages">{messages.map((message, index) => <div className={`v2-message ${message.role}`} key={index} aria-busy={message.role === 'agent' && busy && index === messages.length - 1}>{message.role === 'agent' ? message.text ? <><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>{!busy && <ChartFromMarkdown markdown={message.text} />}{busy && index === messages.length - 1 && <CopilotStreamCursor />}</> : <CopilotThinking /> : <p>{message.text}</p>}{message.meta && <small>{formatDuration(message.meta.elapsedMs)}{message.meta.tokens ? ` · ${message.meta.tokens.toLocaleString()} tokens` : ''}</small>}</div>)}{messages.length === 1 && <div className="v2-suggestions">{prompts.map(prompt => <button type="button" key={prompt} onClick={() => send(prompt)}>{prompt}</button>)}</div>}</div>
      <footer><textarea value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }} placeholder="Ask about connected Fabric data" /><button type="button" title="Send" disabled={busy || !question.trim()} onClick={() => send()}><Send size={17} /></button></footer>
    </section>
  </div>
}

function formatDuration(ms: number) { return ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s` }

type ParsedTable = { headers: string[]; rows: string[][] }
type ChartSeries = { valueHeader: string; points: { label: string; value: number }[] }
type ChartKind = 'bar' | 'line' | 'pie'

function parseFirstTable(markdown: string): ParsedTable | null {
  const lines = markdown.split('\n')
  const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
  for (let index = 0; index < lines.length - 1; index++) {
    const separator = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[index + 1])
    if (!/^\s*\|.*\|\s*$/.test(lines[index]) || !separator) continue
    const headers = cells(lines[index])
    const rows: string[][] = []
    for (let row = index + 2; row < lines.length && /^\s*\|.*\|\s*$/.test(lines[row]); row++) rows.push(cells(lines[row]))
    if (headers.length >= 2 && rows.length) return { headers, rows }
  }
  return null
}

const toNumber = (raw: string) => {
  const value = Number.parseFloat(raw.replace(/[^0-9.eE+-]/g, ''))
  return Number.isFinite(value) ? value : Number.NaN
}

function extractSeries(table: ParsedTable): ChartSeries | null {
  const threshold = Math.ceil(table.rows.length / 2)
  const numericScore = (column: number) => table.rows.filter(row => Number.isFinite(toNumber(row[column] ?? ''))).length
  let valueColumn = -1
  for (let column = table.headers.length - 1; column >= 0; column--) {
    if (numericScore(column) >= threshold) { valueColumn = column; break }
  }
  if (valueColumn < 0) return null
  const labelColumn = table.headers.findIndex((_, column) => column !== valueColumn && numericScore(column) < threshold)
  if (labelColumn < 0) return null
  const points = table.rows
    .map(row => ({ label: row[labelColumn] ?? '', value: toNumber(row[valueColumn] ?? '') }))
    .filter(point => point.label && Number.isFinite(point.value))
    .slice(0, 16)
  return points.length ? { valueHeader: table.headers[valueColumn] ?? 'Value', points } : null
}

function ChartFromMarkdown({ markdown }: { markdown: string }) {
  const series = useMemo(() => { const table = parseFirstTable(markdown); return table ? extractSeries(table) : null }, [markdown])
  const [kind, setKind] = useState<ChartKind>('bar')
  if (!series) return null
  return <div className="v2-agent-chart" aria-label="Visualization generated from the response table">
    <div className="v2-chart-tabs" role="group" aria-label="Chart type">
      <ChartButton active={kind === 'bar'} label="Bar chart" onClick={() => setKind('bar')}><BarChart3 size={14} /></ChartButton>
      <ChartButton active={kind === 'line'} label="Line chart" onClick={() => setKind('line')}><LineChart size={14} /></ChartButton>
      <ChartButton active={kind === 'pie'} label="Pie chart" onClick={() => setKind('pie')}><PieChart size={14} /></ChartButton>
    </div>
    <ChartSvg series={series} kind={kind} />
  </div>
}

function ChartButton({ active, label, onClick, children }: { active: boolean; label: string; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" className={active ? 'on' : ''} title={label} aria-label={label} aria-pressed={active} onClick={onClick}>{children}</button>
}

const CHART_COLORS = ['#2f9e8f', '#4c8bf5', '#f5a623', '#e0554e', '#7b61ff', '#12a150', '#e879a6', '#8a94a6']

function ChartSvg({ series, kind }: { series: ChartSeries; kind: ChartKind }) {
  const width = 420, height = 230, left = 42, right = 16, top = 12, bottom = 58
  const maximum = Math.max(...series.points.map(point => point.value), 0) || 1
  const plotWidth = width - left - right, plotHeight = height - top - bottom
  const shorten = (text: string) => text.length > 12 ? `${text.slice(0, 11)}…` : text
  if (kind === 'pie') {
    const total = series.points.reduce((sum, point) => sum + Math.max(point.value, 0), 0)
    if (!total) return null
    const centerX = 120, centerY = height / 2, radius = 82
    const fractions = series.points.map(point => Math.max(point.value, 0) / total)
    const slices = series.points.map((point, index) => {
      const fraction = fractions[index]
      const start = -Math.PI / 2 + fractions.slice(0, index).reduce((sum, value) => sum + value, 0) * Math.PI * 2
      const end = start + fraction * Math.PI * 2
      const position = (value: number) => `${centerX + radius * Math.cos(value)} ${centerY + radius * Math.sin(value)}`
      const path = fraction >= .9999 ? `M ${centerX - radius} ${centerY} A ${radius} ${radius} 0 1 1 ${centerX + radius} ${centerY} A ${radius} ${radius} 0 1 1 ${centerX - radius} ${centerY} Z` : `M ${centerX} ${centerY} L ${position(start)} A ${radius} ${radius} 0 ${end - start > Math.PI ? 1 : 0} 1 ${position(end)} Z`
      return { point, fraction, path, color: CHART_COLORS[index % CHART_COLORS.length] }
    })
    return <svg viewBox={`0 0 ${width} ${height}`} className="v2-chart-svg" role="img" aria-label={`Pie chart of ${series.valueHeader}`}>
      {slices.map(slice => <path key={slice.point.label} d={slice.path} fill={slice.color} stroke="var(--cp-surface)" />)}
      {slices.map((slice, index) => <g key={`legend-${slice.point.label}`} transform={`translate(235 ${top + index * 20})`}><rect width="10" height="10" rx="2" fill={slice.color} /><text x="16" y="9" className="v2-chart-label">{shorten(slice.point.label)} {Math.round(slice.fraction * 100)}%</text></g>)}
    </svg>
  }
  const count = series.points.length
  const x = (index: number) => left + (count === 1 ? plotWidth / 2 : index * plotWidth / (count - 1))
  const y = (value: number) => top + plotHeight - value / maximum * plotHeight
  return <svg viewBox={`0 0 ${width} ${height}`} className="v2-chart-svg" role="img" aria-label={`${kind} chart of ${series.valueHeader}`}>
    <line x1={left} y1={top} x2={left} y2={top + plotHeight} className="v2-chart-axis" /><line x1={left} y1={top + plotHeight} x2={width - right} y2={top + plotHeight} className="v2-chart-axis" />
    <text x={left - 7} y={top + 4} textAnchor="end" className="v2-chart-label">{maximum.toLocaleString()}</text><text x={left - 7} y={top + plotHeight} textAnchor="end" className="v2-chart-label">0</text>
    {kind === 'bar' && series.points.map((point, index) => { const barWidth = Math.max(7, Math.min(34, plotWidth / count * .62)); const center = left + plotWidth / count * (index + .5); const barHeight = point.value / maximum * plotHeight; return <rect key={point.label} x={center - barWidth / 2} y={top + plotHeight - barHeight} width={barWidth} height={barHeight} rx="3" fill={CHART_COLORS[index % CHART_COLORS.length]} /> })}
    {kind === 'line' && <><polyline points={series.points.map((point, index) => `${x(index)},${y(point.value)}`).join(' ')} className="v2-chart-line" />{series.points.map((point, index) => <circle key={point.label} cx={x(index)} cy={y(point.value)} r="3" fill={CHART_COLORS[0]} />)}</>}
    {series.points.map((point, index) => { const center = kind === 'bar' ? left + plotWidth / count * (index + .5) : x(index); return <text key={`label-${point.label}`} x={center} y={top + plotHeight + 15} textAnchor="end" transform={`rotate(-35 ${center} ${top + plotHeight + 15})`} className="v2-chart-label">{shorten(point.label)}</text> })}
    <text x={left + plotWidth / 2} y={height - 4} textAnchor="middle" className="v2-chart-title">{series.valueHeader}</text>
  </svg>
}
