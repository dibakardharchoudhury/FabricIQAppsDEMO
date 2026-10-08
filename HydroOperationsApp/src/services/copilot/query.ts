import { KUSTO_SOURCE_NAMES } from './catalog.ts'
import type { AgentVisualization } from '../assistantStream.ts'

export const MAX_ROWS = 500

// ---- Client-side row filtering (used by the Lakehouse and SQL tools) ----
// The model never writes GraphQL or SQL; it supplies this structured predicate instead,
// so those two paths have no query-injection surface at all.

export type FilterOperator = 'eq' | 'neq' | 'contains' | 'gt' | 'gte' | 'lt' | 'lte' | 'in' | 'is_null' | 'not_null'
export type FilterCondition = { column: string; op: FilterOperator; value?: unknown }

export const FILTER_OPERATORS: FilterOperator[] = ['eq', 'neq', 'contains', 'gt', 'gte', 'lt', 'lte', 'in', 'is_null', 'not_null']

const text = (value: unknown) => value instanceof Date ? value.toISOString() : String(value ?? '')
const lower = (value: unknown) => text(value).toLowerCase()

function compare(left: unknown, right: unknown): number {
  const leftNumber = typeof left === 'number' ? left : Number.parseFloat(text(left))
  const rightNumber = typeof right === 'number' ? right : Number.parseFloat(text(right))
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber
  return text(left).localeCompare(text(right))
}

function matches(row: Record<string, unknown>, condition: FilterCondition): boolean {
  const actual = row[condition.column]
  switch (condition.op) {
    case 'eq': return lower(actual) === lower(condition.value)
    case 'neq': return lower(actual) !== lower(condition.value)
    case 'contains': return lower(actual).includes(lower(condition.value))
    case 'gt': return compare(actual, condition.value) > 0
    case 'gte': return compare(actual, condition.value) >= 0
    case 'lt': return compare(actual, condition.value) < 0
    case 'lte': return compare(actual, condition.value) <= 0
    case 'in': return Array.isArray(condition.value) && condition.value.some(candidate => lower(candidate) === lower(actual))
    case 'is_null': return actual === null || actual === undefined || actual === ''
    case 'not_null': return !(actual === null || actual === undefined || actual === '')
    default: return true
  }
}

/** Apply the model-supplied predicate. Unknown columns yield no rows rather than silently matching all. */
export function applyFilter<T extends Record<string, unknown>>(rows: T[], where?: FilterCondition[]): T[] {
  if (!where?.length) return rows
  return rows.filter(row => where.every(condition => matches(row, condition)))
}

/** Keep only the requested columns; an empty or unknown selection returns the row unchanged. */
export function projectColumns<T extends Record<string, unknown>>(rows: T[], columns?: string[]): Record<string, unknown>[] {
  if (!columns?.length) return rows
  return rows.map(row => {
    const projected: Record<string, unknown> = {}
    for (const column of columns) if (column in row) projected[column] = row[column]
    return Object.keys(projected).length ? projected : row
  })
}

// ---- KQL ----

export function escapeKqlString(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
}

const KQL_TIMESPAN = /^\d+(\.\d+)?(s|m|h|d)$/
const KQL_BIN = KQL_TIMESPAN

function lookbackStart(lookback: string): string {
  if (lookback === 'today') return '>= startofday(now())'
  if (!KQL_TIMESPAN.test(lookback) || Number.parseFloat(lookback) <= 0) {
    throw new Error(`Invalid lookback '${lookback}'. Use today (UTC) or a positive duration such as 30m, 6h or 7d.`)
  }
  return `> ago(${lookback})`
}

export function buildStationPowerQuery(lookback = '24h'): string {
  return `OPCUAEvents
| where event_time ${lookbackStart(lookback)} and event_time <= now()
| where opcua_node_id endswith_cs '.power_output'
| join kind=leftouter (AssetMaster() | summarize mappings = count(), Station = take_any(Station), Unit = take_any(Unit) by opcua_node_id | extend Unit = iff(mappings == 1, Unit, '')) on opcua_node_id
| summarize average = avg(value), samples = count(), invalid_values = countif(isnull(value) or not(isfinite(value))), bad_samples = countif(toupper(quality) == 'BAD'), latest_event_time = max(event_time) by Station, Unit
| order by Station asc, Unit asc`
}

type StationPowerRow = { Station: string; average_power_MW: number; samples: number; bad_samples: number; latest_event_time: string }

export function stationPowerEvidence(rows: Record<string, unknown>[], lookback: string): { rows: StationPowerRow[]; visualization?: AgentVisualization } {
  if (rows.length >= MAX_ROWS) throw new Error('Station power reached the source row limit; a complete chart cannot be verified.')
  const factors: Record<string, number> = { W: .000001, kW: .001, MW: 1, GW: 1000 }
  const stations = new Map<string, { sum: number; samples: number; bad: number; latest: string }>()
  for (const row of rows) {
    const factor = typeof row.Unit === 'string' ? factors[row.Unit.trim()] : undefined
    if (typeof row.Station !== 'string' || !row.Station.trim() || factor === undefined
      || typeof row.average !== 'number' || !Number.isFinite(row.average)
      || typeof row.samples !== 'number' || !Number.isSafeInteger(row.samples) || row.samples <= 0
      || row.invalid_values !== 0 || typeof row.bad_samples !== 'number'
      || !Number.isSafeInteger(row.bad_samples) || row.bad_samples < 0 || row.bad_samples > row.samples
      || typeof row.latest_event_time !== 'string' || !Number.isFinite(Date.parse(row.latest_event_time))) {
      throw new Error('Station power requires mapped station identity, supported W/kW/MW/GW units, finite measurements and complete sample counts. No partial chart was produced.')
    }
    const station = stations.get(row.Station) ?? { sum: 0, samples: 0, bad: 0, latest: row.latest_event_time }
    station.sum += row.average * factor * row.samples
    station.samples += row.samples
    station.bad += row.bad_samples
    if (Date.parse(row.latest_event_time) > Date.parse(station.latest)) station.latest = row.latest_event_time
    stations.set(row.Station, station)
  }
  const result = [...stations].map(([Station, value]) => ({
    Station, average_power_MW: value.sum / value.samples, samples: value.samples,
    bad_samples: value.bad, latest_event_time: value.latest,
  }))
  if (result.some(row => !Number.isFinite(row.average_power_MW))) throw new Error('Station power aggregation exceeded numeric limits.')
  const csv = ['Station,average_power_MW', ...result.map(row => `"${row.Station.replace(/"/g, '""')}",${row.average_power_MW}`)].join('\n')
  return { rows: result, visualization: result.length ? {
    chartType: 'bar', title: `Average power-output reading by station (${lookback})`,
    xColumn: 'Station', yColumns: ['average_power_MW'], xAxisTitle: 'Station', yAxisTitle: 'MW',
    inlineCsvData: csv,
  } : undefined }
}

export function stationPowerSummary(rows: StationPowerRow[], lookback: string, readCompletedAt: string): string {
  const readTime = Date.parse(readCompletedAt)
  if (!Number.isFinite(readTime)) throw new Error('Station power read-completion time is invalid.')
  const source = `Source: query_station_power, lookback ${lookback}; read completed ${readCompletedAt}.`
  if (!rows.length) return `No power-output readings were returned in the ${lookback} window. No chart was produced; this does not establish zero generation or healthy equipment.\n\n${source}`
  const table = [
    '| Station | Mean power reading (MW) | Samples | BAD samples | Latest event (UTC) | Latest-reading freshness |',
    '|---|---:|---:|---:|---|---|',
    ...rows.map(row => {
      const age = readTime - Date.parse(row.latest_event_time)
      const freshness = age < 0 ? 'Uncertain (future timestamp)' : age > 60_000 ? 'Stale (>60s)' : 'Within 60s'
      return `| ${row.Station.replace(/\|/g, '\\|').replace(/[\r\n]/g, ' ')} | ${row.average_power_MW} | ${row.samples} | ${row.bad_samples} | ${row.latest_event_time} | ${freshness} |`
    }),
  ].join('\n')
  return `**Average power-output reading by station (${lookback}), in MW.** The table and chart use the same source values.\n\n${table}\n\nThis is a sample-weighted arithmetic mean of individual readings, including all quality flags. It is not total station generation, a time-weighted mean or energy. BAD samples are included and counted above. Latest-event freshness does not establish complete coverage of the window or a performance fault.\n\n${source}`
}
const AGGREGATIONS: Record<string, string> = {
  avg: 'avg(value)', min: 'min(value)', max: 'max(value)', sum: 'sum(value)', count: 'count()',
}
export const TELEMETRY_AGGREGATIONS = ['none', 'latest', ...Object.keys(AGGREGATIONS)]

export type TelemetryQueryArgs = {
  opcua_node_ids?: string[]
  lookback?: string
  bin?: string
  aggregation?: string
  limit?: number
}

export function buildQualitySnapshotQuery(quality = 'BAD', lookback = '30m'): string {
  const normalizedQuality = quality.trim().toUpperCase()
  if (!['GOOD', 'UNCERTAIN', 'BAD'].includes(normalizedQuality)) {
    throw new Error("Invalid quality. Use GOOD, UNCERTAIN, or BAD.")
  }
  return `OPCUAEvents
| where event_time ${lookbackStart(lookback)}
| summarize arg_max(event_time, value, quality) by opcua_node_id
| where toupper(quality) == '${normalizedQuality}'
| project event_time, opcua_node_id, value, quality
| order by opcua_node_id asc`
}

export function buildTemperatureSnapshotQuery(lookback = '30m'): string {
  return `OPCUAEvents
| where event_time ${lookbackStart(lookback)}
| summarize arg_max(event_time, value, quality) by opcua_node_id
| where opcua_node_id endswith '.turbine_temp'
| project event_time, opcua_node_id, value, quality
| order by opcua_node_id asc`
}

export function rankTemperatureRows<T extends { value: unknown; equipment_id: string }>(
  rows: T[], options: { limit?: number; threshold?: number; threshold_operator?: string } = {},
): T[] {
  const { threshold, threshold_operator = 'gt' } = options
  const limit = options.limit ?? (threshold === undefined ? 5 : rows.length)
  if (options.limit !== undefined && (!Number.isInteger(options.limit) || options.limit < 1)) throw new Error('Temperature result limit must be a positive integer.')
  if (threshold !== undefined && (typeof threshold !== 'number' || !Number.isFinite(threshold))) throw new Error('Temperature threshold must be a finite number.')
  if (!['gt', 'gte'].includes(threshold_operator)) throw new Error('Temperature threshold operator must be gt or gte.')
  if (rows.some(row => typeof row.value !== 'number' || !Number.isFinite(row.value))) throw new Error('Temperature snapshot contains a non-numeric reading; ranking is unavailable.')
  return rows.filter(row => threshold === undefined || (threshold_operator === 'gte' ? Number(row.value) >= threshold : Number(row.value) > threshold))
    .sort((a, b) => Number(b.value) - Number(a.value) || a.equipment_id.localeCompare(b.equipment_id))
    .slice(0, limit)
}

/** Build the telemetry query from validated fragments — no model text reaches the query body.
 *  Always keeps the NEWEST rows so "the last N readings" is answerable. */
export function buildTelemetryQuery(args: TelemetryQueryArgs): string {
  const lookback = args.lookback ?? '24h'
  const aggregation = args.aggregation ?? 'avg'
  if (!TELEMETRY_AGGREGATIONS.includes(aggregation)) {
    throw new Error(`Invalid aggregation '${args.aggregation}'. Use one of ${TELEMETRY_AGGREGATIONS.join(', ')}.`)
  }
  const requested = Math.trunc(Number(args.limit ?? MAX_ROWS))
  const limit = Math.min(Math.max(Number.isFinite(requested) && requested > 0 ? requested : MAX_ROWS, 1), MAX_ROWS)
  const nodes = (args.opcua_node_ids ?? []).filter(node => typeof node === 'string' && node.trim())
  const nodeFilter = nodes.length
    ? `\n| where opcua_node_id in (${nodes.map(node => `'${escapeKqlString(node)}'`).join(', ')})`
    : ''
  let shape = '| project event_time, opcua_node_id, value, quality'
  if (aggregation === 'latest') {
    shape = '| summarize arg_max(event_time, value, quality) by opcua_node_id'
  } else if (aggregation !== 'none') {
    const bin = args.bin ?? '5m'
    if (!KQL_BIN.test(bin)) throw new Error(`Invalid bin '${bin}'. Use a value like 30s, 5m or 1h.`)
    shape = `| summarize value = ${AGGREGATIONS[aggregation]}, bad = countif(tolower(quality) == 'bad') by opcua_node_id, event_time = bin(event_time, ${bin})`
  }
  return `OPCUAEvents
| where event_time ${lookbackStart(lookback)}${aggregation === 'latest' ? ' and event_time <= now()' : ''}${nodeFilter}
${shape}
| top ${limit} by event_time desc
| order by event_time asc`
}

const FORBIDDEN_KQL = [
  { pattern: /^\s*\./, reason: 'control commands (a leading dot) are not allowed' },
  { pattern: /\bexternaldata\b/i, reason: 'externaldata is not allowed' },
  { pattern: /\bcluster\s*\(/i, reason: 'cross-cluster queries are not allowed' },
  { pattern: /\bdatabase\s*\(/i, reason: 'cross-database queries are not allowed' },
  { pattern: /\b(ingest|set|append|drop|alter|delete)\b/i, reason: 'only read-only queries are allowed' },
  { pattern: /\blet\b/i, reason: 'let statements are not allowed — inline the value instead' },
  { pattern: /;/, reason: 'multiple statements are not allowed' },
]

/** Blank out string literals before the forbidden-pattern scan. An OPC UA node id such as
 *  'ns=2;s=T004.power_output' contains a semicolon that would otherwise read as a statement break. */
export class KqlValidationError extends Error {
  override name = 'KqlValidationError'
}

function withoutStringLiterals(query: string): string {
  let output = ''
  let index = 0
  while (index < query.length) {
    const char = query[index]
    const verbatim = char === '@' && (query[index + 1] === "'" || query[index + 1] === '"')
    const quote = verbatim ? query[index + 1] : (char === "'" || char === '"' ? char : '')
    if (!quote) { output += char; index += 1; continue }
    let cursor = index + (verbatim ? 2 : 1)
    while (cursor < query.length && query[cursor] !== quote) cursor += !verbatim && query[cursor] === '\\' ? 2 : 1
    if (cursor >= query.length) throw new KqlValidationError('Rejected: the query has an unterminated string literal.')
    output += ' '
    index = cursor + 1
  }
  return output
}

/** Validate a model-authored KQL query against the catalog allow-list and cap its result size.
 *  Throws with a message the model can act on; the thrown text is fed back as the tool result. */
export function validateKql(query: string, allowedSources: string[] = KUSTO_SOURCE_NAMES): string {
  if (typeof query !== 'string') throw new KqlValidationError('The query must be a string.')
  const trimmed = (query ?? '').trim()
  if (!trimmed) throw new KqlValidationError('The query was empty.')
  const scanned = withoutStringLiterals(trimmed)
  if (/^TelemetryEnriched\s*\(\s*(?:start|startTime|end|endTime|stations|turbines)\s*:/i.test(scanned)) {
    throw new KqlValidationError('Rejected: TelemetryEnriched arguments are positional. Call TelemetryEnriched(startTime, endTime, stations, turbines) without parameter names or colons; for example TelemetryEnriched(ago(6h), now(), dynamic(null), dynamic(null)).')
  }
  if (/^TelemetryEnriched\b[^|]*\|\s*where\b[^|]*\bopcua_node_id\b/i.test(scanned)) {
    throw new KqlValidationError('Rejected: TelemetryEnriched does not return opcua_node_id. Use query_telemetry with verified opcua_node_ids and aggregation latest for the latest raw row per signal, or query OPCUAEvents directly. Do not invent a node column on the enriched function.')
  }
  for (const rule of FORBIDDEN_KQL) {
    if (rule.pattern.test(scanned)) throw new KqlValidationError(`Rejected: ${rule.reason}.`)
  }
  if (!allowedSources.length) throw new Error('Rejected: no Kusto sources are enabled.')
  const leading = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)/)?.[1]
  if (!leading || !allowedSources.includes(leading)) {
    throw new KqlValidationError(`Rejected: the query must start with one of ${allowedSources.join(', ')}.`)
  }
  return /\|\s*take\s+\d+\s*$/i.test(trimmed) ? trimmed : `${trimmed}\n| take ${MAX_ROWS}`
}

/** Kusto returns column metadata separately; fold it into plain objects for the model. */
export function kustoRowsToObjects(columns: string[], rows: unknown[][]): Record<string, unknown>[] {
  return rows.map(row => Object.fromEntries(columns.map((column, index) => [column, row[index]])))
}

/** Keep tool results small enough that a wide table cannot exhaust the model's context. */
export function truncateForModel(rows: Record<string, unknown>[], limit = MAX_ROWS): { rows: Record<string, unknown>[]; truncated: boolean } {
  const capped = rows.slice(0, limit)
  let serialized = JSON.stringify(capped)
  let output = capped
  while (serialized.length > 40_000 && output.length > 1) {
    output = output.slice(0, Math.floor(output.length / 2))
    serialized = JSON.stringify(output)
  }
  return { rows: output, truncated: output.length < rows.length }
}
