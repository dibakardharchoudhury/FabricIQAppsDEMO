import { KUSTO_SOURCE_NAMES } from './catalog.ts'

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
const AGGREGATIONS: Record<string, string> = {
  avg: 'avg(value)', min: 'min(value)', max: 'max(value)', sum: 'sum(value)', count: 'count()',
}

export type TelemetryQueryArgs = {
  opcua_node_ids?: string[]
  lookback?: string
  bin?: string
  aggregation?: string
}

/** Build the binned telemetry query from validated fragments — no model text reaches the query body. */
export function buildTelemetryQuery(args: TelemetryQueryArgs): string {
  const lookback = args.lookback ?? '24h'
  if (!KQL_TIMESPAN.test(lookback)) throw new Error(`Invalid lookback '${lookback}'. Use a value like 30m, 6h or 7d.`)
  const bin = args.bin ?? '5m'
  if (!KQL_BIN.test(bin)) throw new Error(`Invalid bin '${bin}'. Use a value like 30s, 5m or 1h.`)
  const aggregation = AGGREGATIONS[args.aggregation ?? 'avg']
  if (!aggregation) throw new Error(`Invalid aggregation '${args.aggregation}'. Use one of ${Object.keys(AGGREGATIONS).join(', ')}.`)
  const nodes = (args.opcua_node_ids ?? []).filter(node => typeof node === 'string' && node.trim())
  const nodeFilter = nodes.length
    ? `\n| where opcua_node_id in (${nodes.map(node => `'${escapeKqlString(node)}'`).join(', ')})`
    : ''
  return `OPCUAEvents
| where event_time > ago(${lookback})${nodeFilter}
| summarize value = ${aggregation}, bad = countif(tolower(quality) == 'bad') by opcua_node_id, event_time = bin(event_time, ${bin})
| order by event_time asc
| take ${MAX_ROWS}`
}

const FORBIDDEN_KQL = [
  { pattern: /^\s*\./, reason: 'control commands (a leading dot) are not allowed' },
  { pattern: /\bexternaldata\b/i, reason: 'externaldata is not allowed' },
  { pattern: /\bcluster\s*\(/i, reason: 'cross-cluster queries are not allowed' },
  { pattern: /\bdatabase\s*\(/i, reason: 'cross-database queries are not allowed' },
  { pattern: /\b(ingest|set|append|drop|alter|delete)\b/i, reason: 'only read-only queries are allowed' },
  { pattern: /;/, reason: 'multiple statements are not allowed' },
]

/** Validate a model-authored KQL query against the catalog allow-list and cap its result size.
 *  Throws with a message the model can act on; the thrown text is fed back as the tool result. */
export function validateKql(query: string, allowedSources: string[] = KUSTO_SOURCE_NAMES): string {
  const trimmed = (query ?? '').trim()
  if (!trimmed) throw new Error('The query was empty.')
  for (const rule of FORBIDDEN_KQL) {
    if (rule.pattern.test(trimmed)) throw new Error(`Rejected: ${rule.reason}.`)
  }
  if (!allowedSources.length) throw new Error('Rejected: no Kusto sources are enabled.')
  const leading = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)/)?.[1]
  if (!leading || !allowedSources.includes(leading)) {
    throw new Error(`Rejected: the query must start with one of ${allowedSources.join(', ')}.`)
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
