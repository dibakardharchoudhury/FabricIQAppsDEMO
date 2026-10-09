import { ASSET_ENTITIES, KUSTO_SOURCE_NAMES, OPERATIONS_ENTITIES, type CatalogEntity } from './catalog.ts'
import type { AgentVisualization } from '../assistantStream.ts'

export const MAX_ROWS = 500

// ---- Client-side row filtering (used by the Lakehouse and SQL tools) ----
// The model never writes GraphQL or SQL; it supplies this structured predicate instead,
// so those two paths have no query-injection surface at all.

export type FilterOperator = 'eq' | 'neq' | 'contains' | 'gt' | 'gte' | 'lt' | 'lte' | 'in' | 'is_null' | 'not_null'
export type FilterCondition = { column: string; op: FilterOperator; value?: unknown; value_column?: string }

export const FILTER_OPERATORS: FilterOperator[] = ['eq', 'neq', 'contains', 'gt', 'gte', 'lt', 'lte', 'in', 'is_null', 'not_null']

export type ToolDefinition = {
  type: 'function'
  function: { name: string; description: string; parameters: Record<string, unknown> }
}

const whereSchema = {
  type: 'array',
  description: 'Optional filter. All conditions must match.',
  items: {
    type: 'object',
    properties: {
      column: { type: 'string' },
      op: { type: 'string', enum: FILTER_OPERATORS },
      value: { description: 'Literal comparison value, never a column name. An array when op is "in". Omitted for is_null/not_null or value_column.' },
      value_column: { type: 'string', description: 'Compare with this catalog column on the same row, instead of a literal value. Only eq/neq/gt/gte/lt/lte. For low stock: column quantityOnHand, op lte, value_column reorderLevel.' },
    },
    required: ['column', 'op'],
  },
}



export class QueryInputValidationError extends Error {}

export function shapeCatalogRows(
  entity: CatalogEntity, rows: Record<string, unknown>[],
  args: { columns?: string[]; where?: FilterCondition[]; limit?: number },
  sourceTruncated = false,
  retainIdentities = false,
) {
  const allowed = entity.columns.map(column => column.name)
  const requested = args.columns?.filter(column => allowed.includes(column))
  const filtered = applyFilter(rows, args.where)
  const limited = filtered.slice(0, Math.min(args.limit ?? MAX_ROWS, MAX_ROWS))
  const projected = projectColumns(projectColumns(limited, allowed), requested)
  const { rows: capped, truncated } = truncateForModel(projected)
  const rowIdentities = retainIdentities ? Object.fromEntries(limited.slice(0, capped.length).map((row, index) => [
    `/rows/${index}`,
    Object.fromEntries(['equipment_id', 'equipmentId', 'opcua_node_id', 'opcuaNodeId']
      .filter(key => allowed.includes(key) && typeof row[key] === 'string' && row[key].trim())
      .map(key => [key, row[key]])),
  ])) : undefined
  return { result: { rows: capped, row_count: capped.length, total_matched: sourceTruncated ? null : filtered.length, truncated: truncated || sourceTruncated },
    rowCount: capped.length, ...(retainIdentities ? { rowIdentities } : {}) }
}

const text = (value: unknown) => value instanceof Date ? value.toISOString() : String(value ?? '')
const lower = (value: unknown) => text(value).toLowerCase()

function compare(left: unknown, right: unknown): number {
  if ([left, right].some(value => typeof value === 'number' && !Number.isFinite(value))) throw new QueryInputValidationError('Numeric comparisons require finite operands.')
  const leftText = text(left).trim()
  const rightText = text(right).trim()
  if (!leftText || !rightText) return Number.NaN
  if (/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(leftText) && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(rightText)) {
    const leftTime = Date.parse(leftText)
    const rightTime = Date.parse(rightText)
    if (!Number.isFinite(leftTime) || !Number.isFinite(rightTime)) throw new Error('Date filters require valid ISO dates or timestamps.')
    return leftTime - rightTime
  }
  const leftNumber = Number(leftText)
  const rightNumber = Number(rightText)
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber
  if (Number.isFinite(leftNumber) || Number.isFinite(rightNumber)) {
    throw new QueryInputValidationError('Numeric comparisons require numeric operands. For another source column, use value_column instead of a column name in value.')
  }
  return text(left).localeCompare(text(right))
}

function matches(row: Record<string, unknown>, condition: FilterCondition): boolean {
  const actual = row[condition.column]
  const expected = condition.value_column === undefined ? condition.value : row[condition.value_column]
  if (condition.value_column !== undefined && [actual, expected].some(value => value === null || value === undefined || value === '')) return false
  switch (condition.op) {
    case 'eq': return lower(actual) === lower(expected)
    case 'neq': return lower(actual) !== lower(expected)
    case 'contains': return lower(actual).includes(lower(expected))
    case 'gt': return compare(actual, expected) > 0
    case 'gte': return compare(actual, expected) >= 0
    case 'lt': return compare(actual, expected) < 0
    case 'lte': return compare(actual, expected) <= 0
    case 'in': return Array.isArray(condition.value) && condition.value.some(candidate => lower(candidate) === lower(actual))
    case 'is_null': return actual === null || actual === undefined || actual === ''
    case 'not_null': return !(actual === null || actual === undefined || actual === '')
    default: throw new Error(`Unsupported filter operator: ${condition.op}.`)
  }
}

export function validateFilters(where?: FilterCondition[], allowedColumns?: readonly string[]): void {
  if (where === undefined) return
  if (!Array.isArray(where)) throw new QueryInputValidationError('where must be an array of filter conditions.')
  for (const condition of where) {
    if (!condition || typeof condition.column !== 'string' || !condition.column.trim()
      || !FILTER_OPERATORS.includes(condition.op)) throw new QueryInputValidationError(`Each filter requires a column and a supported operator: ${FILTER_OPERATORS.join(', ')}.`)
    if (allowedColumns && !allowedColumns.includes(condition.column)) throw new QueryInputValidationError(`Unknown filter column '${condition.column}'. Use ${allowedColumns.join(', ')}.`)
    if (Object.hasOwn(condition, 'value_column')) {
      if (typeof condition.value_column !== 'string' || !condition.value_column.trim()
        || Object.hasOwn(condition, 'value') || !['eq', 'neq', 'gt', 'gte', 'lt', 'lte'].includes(condition.op)) {
        throw new QueryInputValidationError('value_column requires one source column, no literal value, and an eq/neq/gt/gte/lt/lte operator.')
      }
      if (allowedColumns && !allowedColumns.includes(condition.value_column)) throw new QueryInputValidationError(`Unknown comparison column '${condition.value_column}'. Use ${allowedColumns.join(', ')}.`)
    } else if (!['is_null', 'not_null'].includes(condition.op) && !Object.hasOwn(condition, 'value')) {
      throw new QueryInputValidationError('This filter operator requires a literal value or value_column.')
    }
    if (['gt', 'gte', 'lt', 'lte'].includes(condition.op) && typeof condition.value === 'string' && allowedColumns?.includes(condition.value)) {
      throw new QueryInputValidationError(`'${condition.value}' is a source column. Use value_column: "${condition.value}" instead of value for a column comparison.`)
    }
    if (condition.op === 'in' && !Array.isArray(condition.value)) throw new QueryInputValidationError('The in filter requires an array value.')
  }
}

/** Apply the model-supplied predicate. Unknown columns yield no rows rather than silently matching all. */
export function applyFilter<T extends Record<string, unknown>>(rows: T[], where?: FilterCondition[]): T[] {
  validateFilters(where)
  if (where === undefined) return rows
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

export type StationPowerRow = { Station: string; average_power_MW: number; samples: number; bad_samples: number; latest_event_time: string }
export const STATION_POWER_SEMANTICS = 'Sample-weighted arithmetic mean of individual power_output readings across turbines, converted to MW using metadata. All qualities included; not total station output, time-weighted mean or energy.'

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
    if (!Number.isFinite(station.sum) || !Number.isSafeInteger(station.samples) || !Number.isSafeInteger(station.bad)) {
      throw new Error('Station power aggregation exceeded numeric limits; sample counts and means cannot be attested.')
    }
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

export function readingFreshness(eventTime: unknown, readCompletedAt: unknown): string {
  const age = typeof eventTime === 'string' && typeof readCompletedAt === 'string'
    ? Date.parse(readCompletedAt) - Date.parse(eventTime) : NaN
  if (!Number.isFinite(age)) return 'Uncertain (missing/invalid timestamp)'
  return age < 0 ? 'Uncertain (future timestamp)' : age > 60_000 ? 'Stale (>60s)' : 'Within 60s'
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
      const freshness = readingFreshness(row.latest_event_time, readCompletedAt)
      return `| ${row.Station.replace(/\|/g, '\\|').replace(/[\r\n]/g, ' ')} | ${row.average_power_MW} | ${row.samples} | ${row.bad_samples} | ${row.latest_event_time} | ${freshness} |`
    }),
  ].join('\n')
  return `**Average power-output reading by station (${lookback}), in MW.** The table and chart use the same source values.\n\n${table}\n\nThis is a sample-weighted arithmetic mean of individual readings, including all quality flags. It is not total station generation, a time-weighted mean or energy. BAD samples are included and counted above. Latest-event freshness does not establish complete coverage of the window or a performance fault.\n\n${source}`
}
const AGGREGATIONS: Record<string, string> = {
  avg: 'avg(value)', min: 'min(value)', max: 'max(value)', sum: 'sum(value)', count: 'count()',
}
export const TELEMETRY_AGGREGATIONS = ['none', 'latest', ...Object.keys(AGGREGATIONS)]

export const TOOL_DEFINITIONS: ToolDefinition[] = [
  {
    type: 'function',
    function: {
      name: 'query_station_power',
      description: 'Return and chart mean power-output readings per station over a window (default 24h). Uses exact power_output node suffix, authoritative station/unit metadata, sample-weighted means converted to MW. Includes all qualities and reports BAD sample counts. Not total station generation or energy.',
      parameters: { type: 'object', properties: { lookback: { type: 'string', description: 'Positive duration, e.g. 24h or 7d, or today (since midnight UTC).' } } },
    },
  },
  {
    type: 'function',
    function: {
      name: 'query_assets',
      description: 'Read asset metadata from the Lakehouse: facilities, equipment and instruments.',
      parameters: {
        type: 'object',
        properties: {
          entity: { type: 'string', enum: ASSET_ENTITIES.map(entity => entity.key) },
          where: whereSchema,
          columns: { type: 'array', items: { type: 'string' }, description: 'Optional subset of columns to return.' },
          limit: { type: 'integer', description: `Maximum rows to return (default ${MAX_ROWS}).` },
        },
        required: ['entity'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'query_operations',
      description: 'Read operational records from the app database: work orders, inspections, spare parts and maintenance notifications.',
      parameters: {
        type: 'object',
        properties: {
          entity: { type: 'string', enum: OPERATIONS_ENTITIES.map(entity => entity.key) },
          where: whereSchema,
          columns: { type: 'array', items: { type: 'string' } },
          limit: { type: 'integer' },
        },
        required: ['entity'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'query_telemetry',
      description: 'Read OPC UA telemetry over a time window. Prefer this over run_kql for trends, latest-per-signal snapshots and "the last N readings". Use aggregation "latest" for one latest raw row per verified node; "none" returns individual readings.',
      parameters: {
        type: 'object',
        properties: {
          opcua_node_ids: { type: 'array', items: { type: 'string' }, description: 'Signals to include. Omit for all signals.' },
          lookback: { type: 'string', description: 'Window ending now: today (since midnight UTC) or a positive duration such as 30m, 6h, 7d. Default 24h.' },
          bin: { type: 'string', description: 'Bucket size when aggregating, e.g. 30s, 5m, 1h. Default 5m. Ignored when aggregation is "none" or "latest".' },
          aggregation: { type: 'string', enum: TELEMETRY_AGGREGATIONS, description: 'Default avg. Use "none" for individual readings or "latest" for the latest raw value, event_time and quality per opcua_node_id in the window, without averaging or quality filtering.' },
          limit: { type: 'integer', description: `How many of the most recent rows to return (default ${MAX_ROWS}, max ${MAX_ROWS}).` },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'run_kql',
      description: 'Run one read-only KQL statement against the Eventhouse when the templated tools cannot express the question. One statement only: no let statements and no semicolon outside a string literal (a node id like \'ns=2;s=T004.power_output\' is fine). The query must start with OPCUAEvents, AssetMaster or TelemetryEnriched.',
      parameters: {
        type: 'object',
        properties: { query: { type: 'string', description: 'A single read-only KQL statement.' } },
        required: ['query'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'query_signal_quality_snapshot',
      description: 'Get every active signal whose single latest raw reading in the window has the requested quality, resolved to equipment with all open work. Use this for "running bad", current quality, and equivalent fleet-health questions instead of assembling multiple inventory/telemetry/work-order calls.',
      parameters: {
        type: 'object',
        properties: {
          quality: { type: 'string', enum: ['GOOD', 'UNCERTAIN', 'BAD'], description: 'Requested latest quality. Default BAD.' },
          lookback: { type: 'string', description: 'Window ending now: 30m, 6h, or today for since midnight UTC. Default 30m.' },
          equipment_type: { type: 'string', description: 'Optional equipment type substring, e.g. turbine.' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'query_turbine_temperature_snapshot',
      description: 'Get the hottest active turbines by their latest raw turbine_temp reading, with all open work joined and labeled by exact signal or equipment-level relation. Use for running hot questions instead of inventing KQL. Default top five over 30m; an explicit threshold returns all matches unless a limit is supplied. No quality-based exclusions or averages.',
      parameters: {
        type: 'object',
        properties: {
          lookback: { type: 'string', description: 'Window ending now: 30m, 6h, or today for since midnight UTC. Default 30m.' },
          limit: { type: 'integer', minimum: 1, description: 'Explicit requested count; default five without a threshold, otherwise all matches.' },
          threshold: { type: 'number', description: 'Optional temperature threshold, in the returned instrument unit.' },
          threshold_operator: { type: 'string', enum: ['gt', 'gte'], description: 'gt means above; gte means at least. Default gt.' },
          equipment_ids: { type: 'array', items: { type: 'string' }, minItems: 1, description: 'Optional exact equipment IDs resolved from metadata for an explicitly requested scope. Omit for all turbines.' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'visualize_dataset',
      description: 'Render a chart in the chat. Call this after retrieving data when a chart helps; still summarize the finding in your reply.',
      parameters: {
        type: 'object',
        properties: {
          chart_type: { type: 'string', enum: ['bar', 'line', 'pie'] },
          title: { type: 'string' },
          x_column: { type: 'string' },
          y_columns: { type: 'array', items: { type: 'string' } },
          x_axis_title: { type: 'string' },
          y_axis_title: { type: 'string' },
          inline_csv_data: { type: 'string', description: 'The data to plot as CSV, including a header row.' },
        },
        required: ['chart_type', 'title', 'x_column', 'y_columns', 'inline_csv_data'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'show_3d_model',
      description: 'Render an asset\u2019s 3D model in the chat. Call this directly with equipment_id \u2014 it resolves the model itself, so no lookup is needed first. If no model exists the tool says so and lists the equipment that do have one. Never claim an asset has no 3D model without calling this.',
      parameters: {
        type: 'object',
        properties: {
          equipment_id: { type: 'string', description: 'Equipment the model belongs to, e.g. an equipment_id from query_assets.' },
          model_id: { type: 'string', description: 'Exact Asset3DModel id, when known.' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'propose_work_order',
      description: 'Stage a complete work-order draft for human approval. This does not create or modify data. Use only for an explicit request to create work, after checking relevant open work.',
      parameters: {
        type: 'object',
        properties: {
          equipment_id: { type: 'string', description: 'Canonical equipment_id resolved from asset metadata.' },
          instrument_id: { type: 'string', description: 'Optional directly affected instrument_id.' },
          opcua_node_id: { type: 'string', description: 'Optional directly affected telemetry node.' },
          title: { type: 'string', maxLength: 200, description: 'Specific action-oriented work title, maximum 200 characters. Preserve an explicit operator title.' },
          description: { type: 'string', maxLength: 4000, description: 'Two to four short sentences: observed condition, source IDs, scope and operator-requested action. Maximum 4000 characters. Do not paste raw rows or invent repair procedures, acceptance thresholds or a diagnosis.' },
          priority: { type: 'string', enum: ['Low', 'Medium', 'High', 'Critical'] },
        },
        required: ['equipment_id', 'title', 'description', 'priority'],
      },
    },
  },
]

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
  return `${buildLatestSignalSnapshotQuery(lookback)}
| where toupper(quality) == '${normalizedQuality}'`
}

export function buildLatestSignalSnapshotQuery(lookback = '30m'): string {
  return `OPCUAEvents
| where event_time ${lookbackStart(lookback)}
| summarize arg_max(event_time, value, quality) by opcua_node_id
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

export type FleetSnapshotArgs = {
  lookback?: string
  quality?: string
  equipment_type?: string
  equipment_ids?: string[]
  limit?: number
  threshold?: number
  threshold_operator?: string
}

export function fleetSnapshotQuery(temperature: boolean, args: FleetSnapshotArgs): string {
  if (temperature) {
    if (args.equipment_ids !== undefined && (!Array.isArray(args.equipment_ids) || !args.equipment_ids.length
      || args.equipment_ids.some(id => typeof id !== 'string' || !id.trim())
      || new Set(args.equipment_ids).size !== args.equipment_ids.length)) {
      throw new Error('Equipment scope must be a nonempty array of distinct exact equipment IDs.')
    }
    rankTemperatureRows([], args)
    return buildTemperatureSnapshotQuery(args.lookback)
  }
  if (args.equipment_type !== undefined && (typeof args.equipment_type !== 'string' || !args.equipment_type.trim())) {
    throw new Error('Equipment type must be a nonempty string.')
  }
  buildQualitySnapshotQuery(args.quality, args.lookback)
  return buildLatestSignalSnapshotQuery(args.lookback)
}

/** Shared browser/backend join. Coverage describes the actual reader, not a presumed SQL population. */
export function shapeFleetSnapshot(temperature: boolean, args: FleetSnapshotArgs, data: {
  equipment: { equipment_id: string; tag?: string; equipment_type_code?: string; equipment_type_name?: string; is_active?: boolean }[]
  instruments: { instrument_id: string; equipment_id: string; opcua_node_id: string; unit?: string; tag?: string; instrument_type?: string; is_active?: boolean }[]
  inventoryComplete: boolean
  telemetryRows: Record<string, unknown>[]
  workOrders: Record<string, unknown>[]
  workInventoryComplete?: boolean
  readStartedAt: string
  readCompletedAt: string
}) {
  fleetSnapshotQuery(temperature, args)
  if (!data.inventoryComplete) throw new Error('Asset inventory pagination did not attest a complete equipment/instrument population. Fleet verification is incomplete; no partial snapshot was returned.')
  if (data.telemetryRows.length >= MAX_ROWS) throw new Error('Fleet snapshot reached the source row limit; complete membership and ranking cannot be verified.')
  const nonempty = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
  const started = Date.parse(data.readStartedAt), completed = Date.parse(data.readCompletedAt)
  if (!Number.isFinite(started) || !Number.isFinite(completed) || completed < started) throw new Error('Snapshot read clock is invalid.')
  const lookback = args.lookback ?? '30m'
  const duration = lookback === 'today' ? 0 : Number.parseFloat(lookback)
    * ({ s: 1000, m: 60000, h: 3600000, d: 86400000 }[lookback.slice(-1)] ?? Number.NaN)
  const windowStart = lookback === 'today' ? Date.parse(`${data.readStartedAt.slice(0, 10)}T00:00:00Z`) : started - duration
  if (!Number.isFinite(windowStart)) throw new Error('Snapshot lookback is invalid.')
  const equipment = new Map<string, (typeof data.equipment)[number]>()
  const equipmentIdentities = new Set<string>()
  for (const asset of data.equipment) {
    if (!nonempty(asset.equipment_id) || equipmentIdentities.has(asset.equipment_id)
      || (asset.is_active !== undefined && typeof asset.is_active !== 'boolean')
      || [asset.equipment_type_code, asset.equipment_type_name, asset.tag].some(value => value != null && typeof value !== 'string')) {
      throw new Error('Asset metadata has invalid or ambiguous equipment identity/type.')
    }
    equipmentIdentities.add(asset.equipment_id)
    if (asset.is_active !== false) equipment.set(asset.equipment_id, asset)
  }
  const instruments = new Map<string, (typeof data.instruments)[number]>()
  const instrumentIdentities = new Set<string>()
  for (const instrument of data.instruments) {
    if (!nonempty(instrument.instrument_id) || instrumentIdentities.has(instrument.instrument_id)
      || !nonempty(instrument.opcua_node_id) || !nonempty(instrument.equipment_id)
      || (instrument.is_active !== undefined && typeof instrument.is_active !== 'boolean')) {
      throw new Error('Asset metadata has invalid or ambiguous instrument identity.')
    }
    instrumentIdentities.add(instrument.instrument_id)
    if (instrument.is_active === false) continue
    if (!equipmentIdentities.has(instrument.equipment_id)) throw new Error('Active instrument references unresolved equipment metadata.')
    if (instruments.has(instrument.opcua_node_id)) throw new Error('Multiple active instruments map to the same telemetry node.')
    instruments.set(instrument.opcua_node_id, instrument)
  }
  const wantedType = temperature ? 'turbine' : args.equipment_type?.trim().toLowerCase()
  const inScope = (instrument: (typeof data.instruments)[number]) => {
    const asset = equipment.get(instrument.equipment_id)
    if (!asset) return false
    const assetType = `${asset.equipment_type_code ?? ''} ${asset.equipment_type_name ?? ''}`.trim()
    if (wantedType && !assetType) throw new Error('Active equipment type metadata is missing; fleet membership cannot be verified.')
    return (!wantedType || assetType.toLowerCase().includes(wantedType))
      && (!temperature || (instrument.opcua_node_id.endsWith('.turbine_temp')
        && (!args.equipment_ids || args.equipment_ids.includes(asset.equipment_id))))
  }
  if (temperature && args.equipment_ids?.some(id => !equipment.has(id))) throw new Error('Requested equipment scope is unresolved or inactive.')
  const expectedInstruments = [...instruments.values()].filter(inScope)
  for (const instrument of expectedInstruments) {
    if (!nonempty(instrument.unit)) throw new Error('Instrument unit metadata is missing or invalid; snapshot readings cannot be attested.')
  }
  if (temperature && new Set(expectedInstruments.map(instrument => instrument.unit)).size > 1) {
    throw new Error('Temperature instruments use different units; a comparable ranking requires explicit unit conversion.')
  }
  const workIdentities = new Set<string>(), workNumbers = new Set<string>()
  for (const order of data.workOrders) {
    if (!nonempty(order.id) || workIdentities.has(order.id) || !nonempty(order.workOrderNumber)
      || workNumbers.has(order.workOrderNumber) || !nonempty(order.equipmentId) || !nonempty(order.status)
      || [order.instrumentId, order.opcuaNodeId].some(value => value != null && typeof value !== 'string')) {
      throw new Error('Work-order source has invalid or duplicate identity, status or signal metadata.')
    }
    workIdentities.add(order.id)
    workNumbers.add(order.workOrderNumber)
  }
  const observedNodes = new Set<string>(), unresolvedNodes: string[] = []
  const qualityFilter = (args.quality ?? 'BAD').trim().toUpperCase()
  const rows = data.telemetryRows.flatMap(reading => {
    const node = reading.opcua_node_id
    if (!nonempty(node) || observedNodes.has(node) || typeof reading.value !== 'number' || !Number.isFinite(reading.value)
      || !nonempty(reading.quality) || !['GOOD', 'BAD', 'UNCERTAIN'].includes(reading.quality.toUpperCase())
      || typeof reading.event_time !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z$/.test(reading.event_time)
      || !Number.isFinite(Date.parse(reading.event_time)) || Date.parse(reading.event_time) > completed
      || new Date(reading.event_time).toISOString().slice(0, 19) !== reading.event_time.slice(0, 19)
      || Date.parse(reading.event_time) < windowStart
      || (temperature && !node.endsWith('.turbine_temp'))) {
      throw new Error('Latest raw telemetry identity, value, quality or timestamp is invalid or ambiguous.')
    }
    observedNodes.add(node)
    const instrument = instruments.get(node)
    const asset = instrument ? equipment.get(instrument.equipment_id) : undefined
    if (!instrument || !asset) { unresolvedNodes.push(node); return [] }
    if (!inScope(instrument) || (!temperature && reading.quality.toUpperCase() !== qualityFilter)) return []
    const relatedWork = data.workOrders
      .filter(order => order.equipmentId === asset.equipment_id
        && !['completed', 'cancelled'].includes(String(order.status).trim().toLowerCase()))
      .map(order => ({
        workOrderNumber: order.workOrderNumber, title: order.title, priority: order.priority, status: order.status,
        instrumentId: order.instrumentId, opcuaNodeId: order.opcuaNodeId,
        relation: order.opcuaNodeId === node || order.instrumentId === instrument.instrument_id ? 'same-signal' : 'equipment-level',
      }))
    return [{
      turbine: asset.tag, equipment_id: asset.equipment_id,
      equipment_type: `${asset.equipment_type_code ?? ''} ${asset.equipment_type_name ?? ''}`.trim(),
      instrument_id: instrument.instrument_id, signal: instrument.instrument_type ?? instrument.tag,
      opcua_node_id: node, value: reading.value, unit: instrument.unit, quality: reading.quality,
      event_time: reading.event_time, open_work_orders: relatedWork,
    }]
  })
  const selected = temperature ? rankTemperatureRows(rows, args) : rows
  const { rows: capped, truncated } = truncateForModel(selected)
  const expectedNodes = expectedInstruments.map(instrument => instrument.opcua_node_id)
  return {
    result: {
      read_completed_at_utc: data.readCompletedAt, lookback,
      provenance: {
        metadata: 'Lakehouse equipment/instruments; complete inventory',
        telemetry: 'Eventhouse OPCUAEvents; latest raw row per node in window',
        work_orders: data.workInventoryComplete ? 'SQL WorkOrder; complete paginated inventory' : 'SQL WorkOrder; returned records only, population completeness not attested',
      },
      population: {
        equipment_type: wantedType ?? null, equipment_ids: temperature ? args.equipment_ids ?? null : null,
        inventory_complete: true, expected_signal_count: expectedNodes.length,
        signals_without_readings: expectedNodes.filter(node => !observedNodes.has(node)),
        work_inventory_complete: data.workInventoryComplete === true,
        work_coverage_equipment_ids: data.workInventoryComplete ? [...new Set(expectedInstruments.map(instrument => instrument.equipment_id))] : [],
      },
      rows: capped, row_count: capped.length,
      ...(temperature ? {
        latest_raw_temperature_ranked_descending: true, threshold: args.threshold,
        threshold_operator: args.threshold_operator ?? 'gt', requested_limit: args.limit ?? (args.threshold === undefined ? 5 : null),
        requested_equipment_without_readings: (args.equipment_ids ?? []).filter(id => !rows.some(row => row.equipment_id === id)),
      } : {
        latest_per_signal_then_quality_filter: true, quality_filter: qualityFilter,
        latest_quality_node_count: data.telemetryRows.filter(row => String(row.quality).toUpperCase() === qualityFilter).length,
      }),
      returned_active_equipment_signal_count: rows.length, unresolved_nodes: unresolvedNodes, truncated,
    },
    rowCount: capped.length,
  }
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
  const latestProjection = scanned.match(/\|\s*summarize\s+arg_max\s*\(\s*event_time\s*,\s*value\s*,\s*quality\s*\)\s+by\s+opcua_node_id\s*\|\s*project\b([^|]*)/i)?.[1]
  if (latestProjection && /\barg_max_(?:event_time|value|quality)\b(?!\s*=(?!=))/i.test(latestProjection)) {
    throw new KqlValidationError('Rejected: unaliased arg_max(event_time, value, quality) returns event_time, value and quality, not arg_max_event_time, arg_max_value or arg_max_quality. Project the original names, or use query_telemetry with aggregation latest and the verified node IDs/window.')
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
