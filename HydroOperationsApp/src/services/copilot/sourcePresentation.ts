import Papa from 'papaparse'
import type { AgentStep } from '../agentSteps.ts'
import type { AgentVisualization } from '../assistantStream.ts'
import { datasetVisualizations, timeColumn, type AnswerDataset } from './answerPresentation.ts'

const titles: Record<string, string> = { query_telemetry: 'Telemetry', run_kql: 'Query results' }
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const scalar = (value: unknown) => value == null || typeof value === 'string'
  || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))

export function presentSourceRows(
  steps: readonly Pick<AgentStep, 'tool' | 'status' | 'result'>[], question: string,
): { datasets: AnswerDataset[]; visualizations: AgentVisualization[]; issues: string[]; summary: string; hasSourceResults: boolean; invalidated: boolean } {
  const datasets: AnswerDataset[] = [], visualizations: AgentVisualization[] = []
  const issues: string[] = [], summaries: string[] = []
  const lastFailure = steps.findLastIndex(step => step.status === 'error' && Object.hasOwn(titles, step.tool))
  const hasSourceResults = steps.some(step => Object.hasOwn(titles, step.tool) && ['done', 'error'].includes(step.status))
  if (lastFailure >= 0) issues.push('A source read failed. Earlier source results are not displayed; only subsequent successful reads can be shown.')
  for (const step of steps.slice(lastFailure + 1)) {
    if (step.status !== 'done' || !Object.hasOwn(titles, step.tool)) continue
    const title = titles[step.tool]
    let result: unknown
    try { result = JSON.parse(step.result ?? '') }
    catch (error) {
      if (!(error instanceof SyntaxError)) throw error
      issues.push(`${title}: the source receipt is unreadable; no values were reconstructed from agent prose.`)
      continue
    }
    if (!record(result) || !Array.isArray(result.rows) || result.rows.length > 500
      || !result.rows.every(record)) {
      issues.push(`${title}: structured source rows are unavailable; no values were reconstructed from agent prose.`)
      continue
    }
    const records = result.rows
    if (!records.length) {
      issues.push(`${title}: the source returned no rows. Missing values are not zero.`)
      continue
    }
    const keys = [...new Set(records.flatMap(row => Object.keys(row)))]
    const columns = keys.filter(key => records.every(row => scalar(row[key])))
    if (!columns.length || columns.length > 64) {
      issues.push(`${title}: the source does not contain a bounded scalar table.`)
      continue
    }
    if (columns.length !== keys.length) issues.push(`${title}: nested fields are retained in source receipts, not plotted as numbers.`)
    const rows = records.map(row => columns.map(column => row[column] == null ? '' : String(row[column])))
    const csv = Papa.unparse({ fields: columns, data: rows })
    const dataset: AnswerDataset = { title: `${title} - returned source rows`, format: 'csv', columns, rows, csv }
    datasets.push(dataset)
    const clock = typeof result.read_completed_at_utc === 'string' ? result.read_completed_at_utc : undefined
    const readTime = clock ? Date.parse(clock) : NaN
    const eventColumn = columns.find(timeColumn)
    const stale = eventColumn && Number.isFinite(readTime)
      ? records.filter(row => typeof row[eventColumn] === 'string'
        && readTime - Date.parse(row[eventColumn]) > 60_000).length : undefined
    summaries.push(`${title}: ${rows.length} returned rows.${Number.isFinite(readTime) ? ` Read completed (UTC): ${clock}.` : ' Read-completion time is unavailable.'}${stale === undefined ? '' : ` ${stale} readings are older than 60 seconds at that read.`}`)
    if (eventColumn) {
      const unknown = records.filter(row => typeof row[eventColumn] !== 'string' || !Number.isFinite(Date.parse(row[eventColumn]))).length
      const future = Number.isFinite(readTime) ? records.filter(row => typeof row[eventColumn] === 'string' && Date.parse(row[eventColumn]) > readTime).length : 0
      if (unknown || future) issues.push(`${title}: ${unknown} missing or invalid timestamps; ${future} timestamps after the source read. Their freshness cannot be established.`)
    }
    if (result.truncated === true) issues.push(`${title}: this is a truncated result. The table and charts show only the ${rows.length} returned rows, not the complete requested period or population.`)
    // Raw signals may have different units. Keep their axes separate without guessing conversions.
    const signal = columns.findIndex(column => /^(opcua_node_id|signal|series)$/i.test(column))
    const rawValue = columns.some(column => /^(value|avg_value|min_value|max_value)$/i.test(column))
    const unit = columns.findIndex(column => /^units?$/i.test(column))
    const groupColumns = [...(signal >= 0 && rawValue ? [signal] : []), ...(unit >= 0 ? [unit] : [])]
    const groupKey = (row: string[]) => JSON.stringify(groupColumns.map(index => row[index]))
    const groups = groupColumns.length ? [...new Set(rows.map(groupKey))] : []
    const chartDatasets = groups.length > 1 ? groups.map(group => {
      const selected = rows.filter(row => groupKey(row) === group)
      return { ...dataset, title: `${title} - ${groupColumns.map(index => selected[0][index] || 'Not supplied').join(' / ')}`, rows: selected,
        csv: Papa.unparse({ fields: columns, data: selected }) }
    }) : [dataset]
    for (const chartDataset of chartDatasets) {
      const charts = datasetVisualizations(chartDataset, question)
      const remaining = Math.max(0, 12 - visualizations.length)
      visualizations.push(...charts.slice(0, remaining))
      if (charts.length > remaining) {
        const limit = 'Chart display is limited to 12 panels. All returned rows remain available in the table and raw evidence.'
        if (!issues.includes(limit)) issues.push(limit)
      }
    }
    if (rawValue && !columns.some(column => /^units?$/i.test(column))) {
      issues.push(`${title}: measurement units were not supplied in these source rows; raw values are shown without conversion.`)
    }
  }
  return { datasets, visualizations, issues, summary: [...summaries, ...issues].join('\n\n'), hasSourceResults, invalidated: lastFailure >= 0 }
}
