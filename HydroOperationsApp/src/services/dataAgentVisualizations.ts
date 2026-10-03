import Papa from 'papaparse'
import type { AgentVisualization } from './assistantStream'

const CSV_BLOCK = /```csv\s*\r?\n([\s\S]*?)```/gi
const TIME_COLUMNS = ['event_time', 'timestamp', 'time', 'datetime', 'date']
const NON_VALUE_COLUMNS = new Set([...TIME_COLUMNS, 'quality', 'opcua_node_id', 'equipment_id', 'facility_id', 'system_id'])

function chartType(question: string, xColumn: string): string {
  if (TIME_COLUMNS.includes(xColumn.toLowerCase())) return 'line'
  if (/\bpie\b/i.test(question)) return 'pie'
  if (/\bscatter\b/i.test(question)) return 'scatter'
  return 'bar'
}

function visualizationFromCsv(csv: string, question: string): AgentVisualization | undefined {
  const parsed = Papa.parse<Record<string, string>>(csv, { header: true, skipEmptyLines: true })
  const headers = parsed.meta.fields ?? []
  if (!headers.length || !parsed.data.length) return undefined

  const xColumn = headers.find(header => TIME_COLUMNS.includes(header.toLowerCase())) ?? headers[0]
  const numericThreshold = Math.ceil(parsed.data.length / 2)
  const yColumns = headers.filter(header => {
    if (header === xColumn || NON_VALUE_COLUMNS.has(header.toLowerCase())) return false
    return parsed.data.filter(row => Number.isFinite(Number(row[header]))).length >= numericThreshold
  })
  if (!yColumns.length) return undefined

  const timeSeries = TIME_COLUMNS.includes(xColumn.toLowerCase())
  const requested = /\b(?:chart|plot|graph|visuali[sz]e|timeseries|time series)\b/i.test(question)
  if (!timeSeries && !requested) return undefined

  return {
    chartType: chartType(question, xColumn),
    title: timeSeries ? 'Data Agent time series' : 'Data Agent visualization',
    xColumn,
    yColumns,
    xAxisTitle: xColumn,
    yAxisTitle: yColumns.join(', '),
    inlineCsvData: csv.trim(),
  }
}

export function extractDataAgentVisualizations(text: string, question: string): AgentVisualization[] {
  const visualizations: AgentVisualization[] = []
  for (const match of text.matchAll(CSV_BLOCK)) {
    const visualization = visualizationFromCsv(match[1], question)
    if (visualization) visualizations.push(visualization)
  }
  return visualizations
}
