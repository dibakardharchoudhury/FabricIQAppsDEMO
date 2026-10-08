import Papa from 'papaparse'
import type { AgentStep } from '../agentSteps.ts'
import type { AgentVisualization } from '../assistantStream.ts'
import { datasetVisualizations, readAnswerDatasets, timeColumn, type AnswerDataset } from './answerPresentation.ts'

const titles: Record<string, string> = {
  query_telemetry: 'Telemetry', run_kql: 'Query results',
  query_signal_quality_snapshot: 'Signal quality', query_turbine_temperature_snapshot: 'Turbine temperatures',
  query_assets: 'Assets', query_operations: 'Operational records', query_station_power: 'Station power',
}
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const scalar = (value: unknown) => value == null || typeof value === 'string'
  || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))

export function presentSourceRows(
  steps: readonly Pick<AgentStep, 'tool' | 'status' | 'result'>[], question: string,
): { datasets: AnswerDataset[]; visualizations: AgentVisualization[]; issues: string[]; summary: string; hasSourceResults: boolean; invalidated: boolean } {
  const datasets: AnswerDataset[] = [], visualizations: AgentVisualization[] = []
  const issues: string[] = [], summaries: string[] = []
  const addCharts = (charts: AgentVisualization[]) => {
    const remaining = Math.max(0, 12 - visualizations.length)
    visualizations.push(...charts.slice(0, remaining))
    const limit = 'Chart display is limited to 12 panels. All returned rows remain available in the table and raw evidence.'
    if (charts.length > remaining && !issues.includes(limit)) issues.push(limit)
  }
  const sourceTools = new Set(Object.keys(titles))
  for (const step of steps) {
    if (step.status !== 'done' || !step.result) continue
    try {
      const value: unknown = JSON.parse(step.result)
      if (Array.isArray(value) || (record(value) && Array.isArray(value.rows))) sourceTools.add(step.tool)
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error
      if (readAnswerDatasets(step.result).datasets.length) sourceTools.add(step.tool)
    }
  }
  const lastFailure = steps.findLastIndex(step => step.status === 'error' && sourceTools.has(step.tool))
  let hasSourceResults = steps.some(step => sourceTools.has(step.tool) && ['done', 'error'].includes(step.status))
  if (lastFailure >= 0) issues.push('A source read failed. Earlier source results are not displayed; only subsequent successful reads can be shown.')
  for (const [offset, step] of steps.slice(lastFailure + 1).entries()) {
    const sourceStep = lastFailure + 1 + offset
    if (step.status !== 'done' || ['visualize_dataset', 'show_3d_model', 'propose_work_order'].includes(step.tool)) continue
    let title = titles[step.tool] ?? step.tool.replaceAll('_', ' ')
    let result: unknown
    try { result = JSON.parse(step.result ?? '') }
    catch (error) {
      if (!(error instanceof SyntaxError)) throw error
      if (!Object.hasOwn(titles, step.tool)) {
        const parsed = readAnswerDatasets(step.result ?? '')
        if (parsed.datasets.length || parsed.issues.length) {
          hasSourceResults = true
          datasets.push(...parsed.datasets.map(dataset => ({ ...dataset, sourceStep })))
          issues.push(...parsed.issues)
          addCharts(parsed.datasets.flatMap(dataset => datasetVisualizations(dataset, question)))
        }
        continue
      }
      issues.push(`${title}: the source receipt is unreadable; no values were reconstructed from agent prose.`)
      continue
    }
    if (Array.isArray(result)) result = { rows: result }
    if (!Object.hasOwn(titles, step.tool) && (!record(result) || !Array.isArray(result.rows))) continue
    hasSourceResults = true
    if (!record(result) || !Array.isArray(result.rows) || result.rows.length > 500
      || !result.rows.every(record)) {
      issues.push(`${title}: structured source rows are unavailable; no values were reconstructed from agent prose.`)
      continue
    }
    const records: Record<string, unknown>[] = result.rows.map(row => Array.isArray(row.open_work_orders)
      ? { ...row, open_work_count: row.open_work_orders.length } : row)
    if (typeof result.quality_filter === 'string') title += ` - ${result.quality_filter}`
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
    if (keys.some(key => !columns.includes(key) && key !== 'open_work_orders')) issues.push(`${title}: nested fields are retained in source receipts, not plotted as numbers.`)
    const rows = records.map(row => columns.map(column => row[column] == null ? '' : String(row[column])))
    const csv = Papa.unparse({ fields: columns, data: rows })
    const dataset: AnswerDataset = { title: `${title} - returned source rows`, format: 'csv', columns, rows, csv, sourceStep }
    datasets.push(dataset)
    const work: Record<string, unknown>[] = records.flatMap(row => Array.isArray(row.open_work_orders) ? row.open_work_orders.filter(record).map(order => ({
      equipment_id: row.equipment_id, opcua_node_id: row.opcua_node_id, ...order,
    })) : [])
    if (work.length) {
      const workColumns = [...new Set(work.flatMap(row => Object.keys(row)))].filter(key => work.every(row => scalar(row[key])))
      const workRows = work.map(row => workColumns.map(column => row[column] == null ? '' : String(row[column])))
      datasets.push({ title: `${title} - open work`, format: 'table', columns: workColumns, rows: workRows, sourceStep,
        csv: Papa.unparse({ fields: workColumns, data: workRows }) })
    }
    const clock = typeof result.read_completed_at_utc === 'string' ? result.read_completed_at_utc : undefined
    const readTime = clock ? Date.parse(clock) : NaN
    const eventColumn = columns.find(timeColumn)
    const stale = eventColumn && Number.isFinite(readTime)
      ? records.filter(row => typeof row[eventColumn] === 'string'
        && readTime - Date.parse(row[eventColumn]) > 60_000).length : undefined
    summaries.push(`${title}: ${rows.length} returned rows.${Number.isFinite(readTime) ? ` Read completed (UTC): ${clock}.` : ' Read-completion time is unavailable.'}${stale === undefined ? '' : ` ${stale} readings are older than 60 seconds at that read.`}`)
    if (stale) issues.push(`${title}: ${stale} stale readings (older than 60 seconds).`)
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
    const groupColumns = unit >= 0 ? [unit] : signal >= 0 && rawValue ? [signal] : []
    const groupKey = (row: string[]) => JSON.stringify([
      ...groupColumns.map(index => row[index]),
      ...(unit >= 0 && !row[unit].trim() && signal >= 0 && rawValue ? [row[signal]] : []),
    ])
    const groups = groupColumns.length ? [...new Set(rows.map(groupKey))] : []
    const chartDatasets = groups.length > 1 ? groups.map(group => {
      const selected = rows.filter(row => groupKey(row) === group)
      return { ...dataset, title: `${title} - ${groupColumns.map(index => selected[0][index] || 'Not supplied').join(' / ')}`, rows: selected,
        csv: Papa.unparse({ fields: columns, data: selected }) }
    }) : [dataset]
    for (const chartDataset of chartDatasets) {
      addCharts(datasetVisualizations(chartDataset, question))
    }
    if (rawValue && !columns.some(column => /^units?$/i.test(column))) {
      issues.push(`${title}: measurement units were not supplied in these source rows; raw values are shown without conversion.`)
    }
  }
  return { datasets, visualizations, issues, summary: [...summaries, ...issues].join('\n\n'), hasSourceResults, invalidated: lastFailure >= 0 }
}
