import type { AgentVisualization } from '../assistantStream.ts'
import { queryStid, runKustoQuery, type StidData } from '../fabric.ts'
import { listInspections, listMaintenanceNotifications, listSpareParts, listWorkOrders } from '../rayfin.ts'
import { ASSET_ENTITIES, OPERATIONS_ENTITIES, type CatalogEntity } from './catalog.ts'
import { enabledKustoNames, isEntityEnabled, isToolEnabled, type CopilotSettings } from './settings.ts'
import {
  applyFilter, buildTelemetryQuery, FILTER_OPERATORS, kustoRowsToObjects, MAX_ROWS,
  projectColumns, truncateForModel, validateKql, type FilterCondition,
} from './query.ts'

export type ToolDefinition = {
  type: 'function'
  function: { name: string; description: string; parameters: Record<string, unknown> }
}

export type ToolOutcome = { result: unknown; visualization?: AgentVisualization; rowCount?: number; query?: string }

/** A short label of what a call asked for, shown on the collapsed trace row in the chat. */
export function describeToolCall(name: string, args: ToolArguments): string {
  const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`
  switch (name) {
    case 'query_assets':
    case 'query_operations':
      return [args.entity ?? '?', args.where?.length ? plural(args.where.length, 'filter') : ''].filter(Boolean).join(' · ')
    case 'query_telemetry':
      return [
        args.opcua_node_ids?.length ? plural(args.opcua_node_ids.length, 'signal') : 'all signals',
        args.lookback ?? '24h',
        `${args.aggregation ?? 'avg'}/${args.bin ?? '5m'}`,
      ].join(' · ')
    case 'run_kql':
      return (args.query ?? '').trim().split('\n')[0].slice(0, 72)
    case 'visualize_dataset':
      return [args.chart_type, args.title].filter(Boolean).join(' · ')
    default:
      return ''
  }
}

const whereSchema = {
  type: 'array',
  description: 'Optional filter. All conditions must match.',
  items: {
    type: 'object',
    properties: {
      column: { type: 'string' },
      op: { type: 'string', enum: FILTER_OPERATORS },
      value: { description: 'Comparison value. An array when op is "in". Omitted for is_null/not_null.' },
    },
    required: ['column', 'op'],
  },
}

export const TOOL_DEFINITIONS: ToolDefinition[] = [
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
      description: 'Aggregate OPC UA telemetry over a time window. Prefer this over run_kql for simple trends.',
      parameters: {
        type: 'object',
        properties: {
          opcua_node_ids: { type: 'array', items: { type: 'string' }, description: 'Signals to include. Omit for all signals.' },
          lookback: { type: 'string', description: 'Window ending now, e.g. 30m, 6h, 7d. Default 24h.' },
          bin: { type: 'string', description: 'Bucket size, e.g. 30s, 5m, 1h. Default 5m.' },
          aggregation: { type: 'string', enum: ['avg', 'min', 'max', 'sum', 'count'], description: 'Default avg.' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'run_kql',
      description: 'Run a read-only KQL query against the Eventhouse when the templated tools cannot express the question. The query must start with OPCUAEvents, AssetMaster or TelemetryEnriched.',
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
]

export type ToolArguments = {
  entity?: string
  where?: FilterCondition[]
  columns?: string[]
  limit?: number
  query?: string
  opcua_node_ids?: string[]
  lookback?: string
  bin?: string
  aggregation?: string
  chart_type?: string
  title?: string
  x_column?: string
  y_columns?: string[]
  x_axis_title?: string
  y_axis_title?: string
  inline_csv_data?: string
}

function entityOrThrow(entities: CatalogEntity[], key: string | undefined, settings: CopilotSettings): CatalogEntity {
  const available = entities.filter(entity => isEntityEnabled(settings, entity.key))
  const entity = available.find(candidate => candidate.key === key)
  if (!entity) {
    const names = available.map(candidate => candidate.key).join(', ')
    throw new Error(names
      ? `Unknown or disabled entity '${key}'. Use one of ${names}.`
      : 'No entities of this kind are enabled in Administration.')
  }
  return entity
}

/** The tool schemas the model sees, narrowed to whatever Administration currently has enabled. */
export function buildToolDefinitions(settings: CopilotSettings): ToolDefinition[] {
  const enabledEntities = (entities: CatalogEntity[]) => entities
    .filter(entity => isEntityEnabled(settings, entity.key))
    .map(entity => entity.key)
  return TOOL_DEFINITIONS
    .filter(tool => isToolEnabled(settings, tool.function.name))
    .filter(tool => {
      if (tool.function.name === 'query_assets') return enabledEntities(ASSET_ENTITIES).length > 0
      if (tool.function.name === 'query_operations') return enabledEntities(OPERATIONS_ENTITIES).length > 0
      if (tool.function.name === 'query_telemetry') return enabledKustoNames(settings).includes('OPCUAEvents')
      if (tool.function.name === 'run_kql') return enabledKustoNames(settings).length > 0
      return true
    })
    .map(tool => {
      const entities = tool.function.name === 'query_assets' ? enabledEntities(ASSET_ENTITIES)
        : tool.function.name === 'query_operations' ? enabledEntities(OPERATIONS_ENTITIES)
          : undefined
      if (!entities) {
        if (tool.function.name !== 'run_kql') return tool
        return {
          ...tool,
          function: {
            ...tool.function,
            description: `Run a read-only KQL query against the Eventhouse when the templated tools cannot express the question. The query must start with ${enabledKustoNames(settings).join(', ')}.`,
          },
        }
      }
      const parameters = tool.function.parameters as { properties: Record<string, unknown> }
      return {
        ...tool,
        function: {
          ...tool.function,
          parameters: {
            ...parameters,
            properties: { ...parameters.properties, entity: { type: 'string', enum: entities } },
          },
        },
      }
    })
}

/** Restrict to the columns declared in the catalog, then to the model's subset.
 *  Columns absent from the catalog (e.g. Entra object ids) are never returned. */
function shape(entity: CatalogEntity, rows: Record<string, unknown>[], args: ToolArguments): ToolOutcome {
  const allowed = entity.columns.map(column => column.name)
  const requested = args.columns?.filter(column => allowed.includes(column))
  const filtered = applyFilter(rows, args.where)
  const limited = filtered.slice(0, Math.min(args.limit ?? MAX_ROWS, MAX_ROWS))
  const projected = projectColumns(projectColumns(limited, allowed), requested)
  const { rows: capped, truncated } = truncateForModel(projected)
  return { result: { rows: capped, row_count: capped.length, total_matched: filtered.length, truncated }, rowCount: capped.length }
}

/** Per-turn caches so repeated tool calls in one answer do not refetch the same source. */
export function createToolRuntime(settings: CopilotSettings) {
  let stid: Promise<StidData | null> | undefined
  const operations = new Map<string, Promise<Record<string, unknown>[]>>()

  const loadOperations = (key: string): Promise<Record<string, unknown>[]> => {
    const existing = operations.get(key)
    if (existing) return existing
    const loaders: Record<string, () => Promise<unknown[]>> = {
      work_orders: listWorkOrders,
      inspections: listInspections,
      spare_parts: listSpareParts,
      notifications: listMaintenanceNotifications,
    }
    const loader = loaders[key]
    if (!loader) throw new Error(`Unknown entity '${key}'.`)
    const promise = loader().then(rows => rows as Record<string, unknown>[])
    operations.set(key, promise)
    return promise
  }

  return async function runTool(name: string, args: ToolArguments): Promise<ToolOutcome> {
    // Re-check here as well as in the schema: a model can still emit a disabled tool or entity.
    if (!isToolEnabled(settings, name)) throw new Error(`The tool '${name}' is disabled in Administration.`)
    switch (name) {
      case 'query_assets': {
        const entity = entityOrThrow(ASSET_ENTITIES, args.entity, settings)
        stid ??= queryStid()
        const data = await stid
        if (!data) throw new Error('Asset metadata is not connected. Connect the STID GraphQL source first.')
        const rows = (entity.key === 'facilities' ? data.facilities : entity.key === 'equipment' ? data.equipment : data.instruments) as unknown as Record<string, unknown>[]
        return shape(entity, rows, args)
      }
      case 'query_operations': {
        const entity = entityOrThrow(OPERATIONS_ENTITIES, args.entity, settings)
        return shape(entity, await loadOperations(entity.key), args)
      }
      case 'query_telemetry': {
        if (!enabledKustoNames(settings).includes('OPCUAEvents')) throw new Error('The OPCUAEvents table is disabled in Administration.')
        const csl = buildTelemetryQuery(args)
        const { columns, rows } = await runKustoQuery(csl, MAX_ROWS)
        const { rows: capped, truncated } = truncateForModel(kustoRowsToObjects(columns, rows))
        return { result: { rows: capped, row_count: capped.length, truncated }, rowCount: capped.length, query: csl }
      }
      case 'run_kql': {
        const csl = validateKql(args.query ?? '', enabledKustoNames(settings))
        const { columns, rows } = await runKustoQuery(csl, MAX_ROWS)
        const { rows: capped, truncated } = truncateForModel(kustoRowsToObjects(columns, rows))
        return { result: { rows: capped, row_count: capped.length, truncated }, rowCount: capped.length, query: csl }
      }
      case 'visualize_dataset': {
        if (!args.chart_type || !args.x_column || !args.y_columns?.length || !args.inline_csv_data) {
          throw new Error('chart_type, x_column, y_columns and inline_csv_data are all required.')
        }
        return {
          result: { rendered: true },
          visualization: {
            chartType: args.chart_type,
            title: args.title || 'Copilot visualization',
            xColumn: args.x_column,
            yColumns: args.y_columns,
            xAxisTitle: args.x_axis_title,
            yAxisTitle: args.y_axis_title,
            inlineCsvData: args.inline_csv_data,
          },
        }
      }
      default:
        throw new Error(`Unknown tool '${name}'.`)
    }
  }
}
