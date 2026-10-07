import Papa from 'papaparse'
import type { AgentVisualization } from '../assistantStream.ts'

export const OPERATIONAL_EVIDENCE_CONTRACT = `Operational evidence contract:
"Operational SQL work orders" means records from the operational SQL data source, not a work-order type or category. Do not invent a SQL/type/category filter. Open means status neither Completed nor Cancelled.
For each affected equipment ID, include EVERY open work order and its number, title, status and priority. Same-signal work requires an exact instrument ID or OPC UA node match to the signal being discussed. EVERY other open order on that equipment is equipment-level work, including orders linked to a different signal; equipment-level does not mean only orders with null signal IDs. These two groups must account for all open orders on that equipment.
Compare telemetry event timestamps with the supplied current UTC time, not with the newest event as an invented query time. Explicitly label readings older than 60 seconds as stale. Never describe a reading as fresh merely because it falls inside the lookback window.
When forwarding a request to another agent or remote tool, preserve these source and matching rules, the current time, and the original scope.`

export const ANSWER_PRESENTATION_CONTRACT = `Response presentation contract:
Start with a concise direct answer. Use these headings only when relevant: Findings, Open work, Recommendations, Limitations, Sources.
For multiple comparable records, return a compact Markdown table with a header separator and one record per row. Use human-readable labels, explicit units, UTC timestamps, and canonical identifiers where needed. Do not replace a complete result with selected examples.
For charts or dashboards, provide the exact supporting rows as fenced csv with a header. Use a descriptive heading immediately before each dataset. Numeric measures must be plain numbers; put units in column names. Use timestamp for the time axis and series for multiple signals. Never mix incompatible units in one measure column. Tables and charts must use the same rows, filters and labels, not independent recounts.
If a specific chart is requested, emit CSV only for that chart's requested labels and measures, not unrelated numeric columns or the full inventory behind a top-N table. A top-five temperature chart must contain exactly the same five turbines as its table.
Do not invent chart images, links, KPI totals or zeroes. Distinguish no matching records from unavailable, failed, stale or truncated sources. State material time/population scope and source limitations. End with concise Sources. Separate observations from hypotheses and recommendations.`

export type AnswerDataset = {
  format: 'table' | 'csv'
  title: string
  columns: string[]
  rows: string[][]
  csv: string
}

const numeric = (value: string) => value.trim() !== '' && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())
const timeColumn = (name: string) => /^(timestamp|event_time|time|datetime|date)(?: \(utc\))?$/i.test(name)
const identifierColumn = (name: string) => /(?:^|[_\s])(id|rank|index|number)(?:$|[_\s])/i.test(name)

export function readAnswerDatasets(text: string): { datasets: AnswerDataset[]; issues: string[] } {
  const datasets: AnswerDataset[] = []
  const issues: string[] = []
  const lines = text.split(/\r?\n/)
  let title = 'Findings'
  const add = (columns: string[], rows: string[][], format: AnswerDataset['format']) => {
    if (!columns.length || !rows.length || new Set(columns).size !== columns.length || rows.some(row => row.length !== columns.length)) {
      issues.push(`${title}: the dataset has missing, duplicate or inconsistent columns; no chart was generated.`)
      return
    }
    const csv = Papa.unparse({ fields: columns, data: rows })
    const existing = datasets.find(dataset => dataset.csv === csv)
    if (existing && format === 'csv') existing.format = 'csv'
    else if (!existing) datasets.push({ title, columns, rows, csv, format })
  }
  for (let index = 0; index < lines.length; index++) {
    if (/^#{1,6}\s/.test(lines[index])) title = lines[index].replace(/^#{1,6}\s+/, '').trim()
    if (/^```csv\s*$/i.test(lines[index].trim())) {
      const csv: string[] = []
      while (++index < lines.length && !/^```\s*$/.test(lines[index].trim())) csv.push(lines[index])
      const parsed = Papa.parse<string[]>(csv.join('\n'), { skipEmptyLines: 'greedy' })
      if (parsed.errors.length) issues.push(`${title}: invalid CSV; no chart was generated.`)
      else add(parsed.data[0] ?? [], parsed.data.slice(1), 'csv')
    } else if (/^\s*\|/.test(lines[index]) && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[index + 1] ?? '')) {
      const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '')
        .split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|').replace(/\*\*|`/g, ''))
      const columns = cells(lines[index])
      index += 2
      const rows: string[][] = []
      while (index < lines.length && /^\s*\|/.test(lines[index])) rows.push(cells(lines[index++]))
      index--
      add(columns, rows, 'table')
    }
  }
  return { datasets, issues }
}

export function answerVisualizations(datasets: AnswerDataset[], question: string): AgentVisualization[] {
  const explicit = datasets.filter(dataset => dataset.format === 'csv')
  return (explicit.length ? explicit : datasets).flatMap(dataset => datasetVisualizations(dataset, question))
}

export function datasetVisualizations(dataset: AnswerDataset, question: string): AgentVisualization[] {
  const timeIndex = dataset.columns.findIndex(timeColumn)
  const labelIndex = timeIndex >= 0 ? timeIndex : dataset.columns.findIndex((name, index) =>
    !identifierColumn(name) && dataset.rows.some(row => !numeric(row[index])))
  if (labelIndex < 0) return []
  const groupBy = dataset.columns.find(name => /^(series|signal|opcua_node_id)$/i.test(name))
  return dataset.columns.flatMap((column, index) => {
    if (index === labelIndex || column === groupBy || identifierColumn(column)
      || !dataset.rows.every(row => numeric(row[index]))) return []
    return [{
      title: `${dataset.title} - ${column}`,
      chartType: timeIndex >= 0 ? 'line' : /\bpie\b/i.test(question) ? 'pie' : 'bar',
      xColumn: dataset.columns[labelIndex],
      yColumns: [column],
      groupBy,
      inlineCsvData: dataset.csv,
    }]
  })
}
