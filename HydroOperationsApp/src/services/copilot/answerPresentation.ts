import Papa from 'papaparse'
import type { AgentVisualization } from '../assistantStream.ts'
import type { AgentStep } from '../agentSteps.ts'

export const OPERATIONAL_EVIDENCE_CONTRACT = `Operational evidence contract:
"Operational SQL work orders" means records from the operational SQL data source, not a work-order type or category. Do not invent a SQL/type/category filter. Open means status neither Completed nor Cancelled.
For each affected equipment ID, include EVERY open work order and its number, title, status and priority. Same-signal work requires an exact instrument ID or OPC UA node match to the signal being discussed. EVERY other open order on that equipment is equipment-level work, including orders linked to a different signal; equipment-level does not mean only orders with null signal IDs. These two groups must account for all open orders on that equipment.
When a compound request selects one turbine for investigation, preserve the requested work inventory for ALL initially affected turbines in the final answer, not only the selected turbine.
Compare telemetry event timestamps with the tool's read_completed_at_utc when provided; it is the actual read-completion clock, not the request-start clock. For remote telemetry queries, request the query's UTC clock with the evidence when supported. Otherwise identify the supplied current UTC time as the request-start reference, not an invented query time. A reading received after request start does not establish source clock skew. Never use the newest event as the current clock. Explicitly label readings older than 60 seconds as stale; readings ahead of the actual read-completion clock have uncertain freshness. Never describe a reading as fresh merely because it falls inside the lookback window.
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

export function appendOmittedSnapshotWork(text: string, steps: readonly Pick<AgentStep, 'tool' | 'status' | 'result'>[]): string {
  const missing = new Map<string, string[]>()
  const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
  const cell = (value: unknown) => (typeof value === 'string' && value ? value : 'Not supplied')
    .replace(/[\\|`*_[\]<>]/g, '\\$&').replace(/\r?\n/g, ' ')
  for (const step of steps) {
    if (step.status !== 'done' || !['query_signal_quality_snapshot', 'query_turbine_temperature_snapshot'].includes(step.tool)) continue
    if (!step.result) throw new Error('Completed snapshot omitted its evidence.')
    const snapshot: unknown = JSON.parse(step.result)
    if (!record(snapshot) || !Array.isArray(snapshot.rows)) throw new Error('Snapshot evidence has no rows array.')
    for (const row of snapshot.rows) {
      if (!record(row) || typeof row.equipment_id !== 'string' || typeof row.opcua_node_id !== 'string'
        || !Array.isArray(row.open_work_orders)) throw new Error('Snapshot work coverage has an invalid equipment/signal identity.')
      for (const order of row.open_work_orders) {
        if (!record(order) || typeof order.workOrderNumber !== 'string' || !order.workOrderNumber) throw new Error('Snapshot work coverage has no work-order identity.')
        const number = order.workOrderNumber
        const mentioned = new RegExp(`(?<![\\w-])${number.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w-])`, 'i').test(text)
        if (!mentioned) missing.set(JSON.stringify([row.equipment_id, row.opcua_node_id, number]), [
          row.equipment_id, row.opcua_node_id, number, order.title, order.status, order.priority,
          order.relation, snapshot.read_completed_at_utc,
        ].map(cell))
      }
    }
  }
  if (!missing.size) return text
  return `${text}\n\n### Additional verified open work\n\nThe application preserved these orders from the direct snapshot because the narrative omitted their numbers. Relations are relative to the signal shown; timestamps identify the source read, not a new live query.\n\n| Equipment | Signal | Work order | Title | Status | Priority | Relation | Read completed (UTC) |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n${[...missing.values()].map(row => `| ${row.join(' | ')} |`).join('\n')}`
}

const numeric = (value: string) => value.trim() !== '' && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())
const timeColumn = (name: string) => /^(timestamp|eventtime|time|datetime|date)(iso)?(utc)?$/.test(name.toLowerCase().replace(/[^a-z]/g, ''))
const identifierColumn = (name: string) => /(?:^|[_\s])(id|rank|index|number)(?:$|[_\s])/i.test(name)

function normalizeLabeledCsv(text: string): string {
  const lines = text.split(/\r?\n/)
  const output: string[] = []
  let fenced = false
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]
    if (/^\s*```/.test(line)) fenced = !fenced
    if (!fenced && /^\s*CSV:\s*\S/i.test(line)) {
      let start = index + 1
      while (start < lines.length && !lines[start].trim()) start++
      let end = start
      while (end < lines.length && lines[end].includes(',') && !/^\s*(?:```|#|\|)/.test(lines[end])) end++
      const parsed = Papa.parse<string[]>(lines.slice(start, end).join('\n'), { skipEmptyLines: 'greedy' })
      const columns = parsed.data[0] ?? []
      if (!parsed.errors.length && parsed.data.length > 1 && columns.length > 1
        && columns.every(column => column.trim()) && new Set(columns).size === columns.length
        && parsed.data.every(row => row.length === columns.length)) {
        output.push(`### ${line.replace(/^\s*CSV:\s*/i, '')}`, '```csv', ...lines.slice(start, end), '```')
        index = end - 1
        continue
      }
    }
    output.push(line)
  }
  return output.join('\n')
}

export function readAnswerDatasets(text: string): { datasets: AnswerDataset[]; issues: string[] } {
  const datasets: AnswerDataset[] = []
  const issues: string[] = []
  const lines = normalizeLabeledCsv(text).split(/\r?\n/)
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

export function hideRenderedCsv(text: string): string {
  return normalizeLabeledCsv(text).replace(/```csv[^\S\r\n]*\r?\n[\s\S]*?```/gi, block => {
    const parsed = readAnswerDatasets(block)
    return parsed.datasets.length && !parsed.issues.length ? '' : block
  })
}

export function formatEvidenceCell(value: string, column: string): string {
  if (!value.trim()) return 'Not supplied'
  if (numeric(value) && !identifierColumn(column)) {
    const number = Number(value)
    return Number.isFinite(number) ? new Intl.NumberFormat('en-GB', number !== 0 && Math.abs(number) < .001
      ? { maximumSignificantDigits: 4 } : { maximumFractionDigits: 3 }).format(number) : value
  }
  return value
}

export function datasetVisualizations(dataset: AnswerDataset, question: string): AgentVisualization[] {
  const timeIndex = dataset.columns.findIndex(timeColumn)
  const seriesIndex = dataset.columns.findIndex(name => /^(series|signal|opcua_node_id|station(?:_id|_name)?|facility(?:_id|_name)?|turbine(?:_tag)?|asset)$/i.test(name))
  const multipleTimes = timeIndex >= 0 && new Set(dataset.rows.map(row => `${seriesIndex < 0 ? '' : row[seriesIndex]}\0${row[timeIndex]}`)).size
    > (seriesIndex < 0 ? 1 : new Set(dataset.rows.map(row => row[seriesIndex])).size)
  const labelIndex = timeIndex >= 0 && multipleTimes ? timeIndex : seriesIndex >= 0 ? seriesIndex : dataset.columns.findIndex((name, index) =>
    !identifierColumn(name) && dataset.rows.some(row => !numeric(row[index])))
  if (labelIndex < 0) return []
  const groupBy = labelIndex === timeIndex && seriesIndex >= 0 ? dataset.columns[seriesIndex] : undefined
  return dataset.columns.flatMap((column, index) => {
    if (index === labelIndex || column === groupBy || identifierColumn(column)
      || !dataset.rows.every(row => numeric(row[index]))) return []
    return [{
      title: `${dataset.title} - ${column}`,
      chartType: labelIndex === timeIndex && multipleTimes ? 'line' : /\bpie\b/i.test(question) ? 'pie' : 'bar',
      xColumn: dataset.columns[labelIndex],
      yColumns: [column],
      groupBy,
      inlineCsvData: dataset.csv,
    }]
  })
}
