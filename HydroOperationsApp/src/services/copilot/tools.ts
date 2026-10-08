import type { AgentVisualization } from '../assistantStream.ts'
import { queryStid, runKustoQuery, type StidData } from '../fabric.ts'
import { isRayfinConfigured, isRayfinSignedIn, listAsset3DModels, listInspections, listMaintenanceNotifications, listSpareParts, listWorkOrders, type Asset3DModelRecord } from '../rayfin.ts'
import { ASSET_ENTITIES, OPERATIONS_ENTITIES, type CatalogEntity } from './catalog.ts'
import { enabledKustoNames, isEntityEnabled, isToolEnabled, type CopilotSettings } from './settings.ts'
import { createWorkOrderProposal, type WorkOrderProposal } from './orchestration.ts'
import { validateWorkOrderTarget, workOrderApprovals } from './workOrderApproval.ts'
import {
  applyFilter, validateFilters, buildStationPowerQuery, stationPowerEvidence, stationPowerSummary, buildLatestSignalSnapshotQuery, buildQualitySnapshotQuery, buildTemperatureSnapshotQuery, rankTemperatureRows, buildTelemetryQuery, FILTER_OPERATORS, kustoRowsToObjects, MAX_ROWS,
  projectColumns, STATION_POWER_SEMANTICS, TELEMETRY_AGGREGATIONS, truncateForModel, validateKql, type FilterCondition,
} from './query.ts'

export type ToolDefinition = {
  type: 'function'
  function: { name: string; description: string; parameters: Record<string, unknown> }
}

export type ToolOutcome = { result: unknown; visualization?: AgentVisualization; model3d?: Asset3DModelRecord; rowCount?: number; query?: string; groundedSummary?: string }

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
        args.aggregation === 'latest' ? 'latest raw row per signal' : args.aggregation === 'none' ? `latest ${args.limit ?? MAX_ROWS} readings` : `${args.aggregation ?? 'avg'}/${args.bin ?? '5m'}`,
      ].join(' · ')
    case 'query_signal_quality_snapshot':
      return [args.quality ?? 'BAD', args.lookback ?? '30m', args.equipment_type ?? 'all equipment'].join(' · ')
    case 'query_turbine_temperature_snapshot':
      return [args.lookback ?? '30m', args.threshold === undefined ? `top ${args.limit ?? 5}` : `${args.threshold_operator ?? 'gt'} ${args.threshold}`, 'latest raw temperatures + open work'].join(' · ')
    case 'query_station_power':
      return `${args.lookback ?? '24h'} · mean power-output reading by station · MW`
    case 'run_kql':
      return (args.query ?? '').trim().split('\n')[0].slice(0, 72)
    case 'visualize_dataset':
      return [args.chart_type, args.title].filter(Boolean).join(' · ')
    case 'show_3d_model':
      return args.equipment_id ?? args.model_id ?? ''
    case 'propose_work_order':
      return [args.equipment_id, args.priority, args.title].filter(Boolean).join(' · ')
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
  equipment_id?: string
  model_id?: string
  instrument_id?: string
  opcua_node_id?: string
  description?: string
  priority?: string
  quality?: string
  equipment_type?: string
  equipment_ids?: string[]
  threshold?: number
  threshold_operator?: string
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
      if (tool.function.name === 'query_station_power') return (['OPCUAEvents', 'AssetMaster'] as const).every(name => enabledKustoNames(settings).includes(name))
      if (tool.function.name === 'query_signal_quality_snapshot' || tool.function.name === 'query_turbine_temperature_snapshot') {
        return enabledKustoNames(settings).includes('OPCUAEvents')
          && enabledEntities(ASSET_ENTITIES).includes('equipment')
          && enabledEntities(ASSET_ENTITIES).includes('instruments')
          && enabledEntities(OPERATIONS_ENTITIES).includes('work_orders')
      }
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
            description: `Run one read-only KQL statement against the Eventhouse when the templated tools cannot express the question. One statement only: no let statements and no semicolon outside a string literal (a node id like 'ns=2;s=T004.power_output' is fine). The query must start with ${enabledKustoNames(settings).join(', ')}.`,
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
function shape(entity: CatalogEntity, rows: Record<string, unknown>[], args: ToolArguments, sourceTruncated = false): ToolOutcome {
  const allowed = entity.columns.map(column => column.name)
  const requested = args.columns?.filter(column => allowed.includes(column))
  const filtered = applyFilter(rows, args.where)
  const limited = filtered.slice(0, Math.min(args.limit ?? MAX_ROWS, MAX_ROWS))
  const projected = projectColumns(projectColumns(limited, allowed), requested)
  const { rows: capped, truncated } = truncateForModel(projected)
  return { result: { rows: capped, row_count: capped.length, total_matched: sourceTruncated ? null : filtered.length, truncated: truncated || sourceTruncated }, rowCount: capped.length }
}

const SIGN_IN_HINT = 'Not signed in to the operational database. Open Administration and complete step 1, “Sign in to Fabric”, then ask again. This is a sign-in step, not a permissions problem.'

/** Per-turn caches so repeated tool calls in one answer do not refetch the same source. */
export function createToolRuntime(
  settings: CopilotSettings,
  options?: { onWorkOrderProposal?: (proposal: WorkOrderProposal) => void; proposalPriority?: WorkOrderProposal['priority'] },
) {
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
      asset_models: listAsset3DModels,
    }
    const loader = loaders[key]
    if (!loader) throw new Error(`Unknown entity '${key}'.`)
    if (!isRayfinConfigured()) throw new Error('The operational database is not configured in this build.')
    if (!isRayfinSignedIn()) throw new Error(SIGN_IN_HINT)
    const promise = loader()
      .then(rows => rows as Record<string, unknown>[])
      .catch((error: unknown) => {
        // The backend answers 401 when the Rayfin session has expired mid-conversation.
        const message = error instanceof Error ? error.message : String(error)
        throw new Error(/401|unauthor/i.test(message) ? SIGN_IN_HINT : message)
      })
    operations.set(key, promise)
    return promise
  }

  return async function runTool(name: string, args: ToolArguments): Promise<ToolOutcome> {
    // Re-check here as well as in the schema: a model can still emit a disabled tool or entity.
    if (!isToolEnabled(settings, name)) throw new Error(`The tool '${name}' is disabled in Administration.`)
    switch (name) {
      case 'query_station_power': {
        if (!(['OPCUAEvents', 'AssetMaster'] as const).every(source => enabledKustoNames(settings).includes(source))) throw new Error('Station power requires enabled OPCUAEvents and AssetMaster sources.')
        const lookback = args.lookback ?? '24h'
        const query = buildStationPowerQuery(lookback)
        const data = await runKustoQuery(query, MAX_ROWS)
        const evidence = stationPowerEvidence(kustoRowsToObjects(data.columns, data.rows), lookback)
        const readCompletedAt = new Date().toISOString()
        const groundedSummary = stationPowerSummary(evidence.rows, lookback, readCompletedAt)
        return {
          result: { rows: evidence.rows, row_count: evidence.rows.length, lookback,
            grounded_summary: groundedSummary,
            chart_rendered: Boolean(evidence.visualization),
            chart: evidence.visualization,
            semantics: STATION_POWER_SEMANTICS, source_key: data.sourceKey,
            read_completed_at_utc: readCompletedAt },
          groundedSummary,
          rowCount: evidence.rows.length, visualization: evidence.visualization, query,
        }
      }
      case 'query_assets': {
        const entity = entityOrThrow(ASSET_ENTITIES, args.entity, settings)
        validateFilters(args.where, entity.columns.map(column => column.name))
        stid ??= queryStid()
        const data = await stid
        if (!data) throw new Error('Asset metadata is not connected. Connect the STID GraphQL source first.')
        const rows = (entity.key === 'facilities' ? data.facilities : entity.key === 'equipment' ? data.equipment : data.instruments) as unknown as Record<string, unknown>[]
        return shape(entity, rows, args, !data.inventoryComplete)
      }
      case 'query_operations': {
        const entity = entityOrThrow(OPERATIONS_ENTITIES, args.entity, settings)
        validateFilters(args.where, entity.columns.map(column => column.name))
        return shape(entity, await loadOperations(entity.key), args)
      }
      case 'query_telemetry': {
        if (!enabledKustoNames(settings).includes('OPCUAEvents')) throw new Error('The OPCUAEvents table is disabled in Administration.')
        const csl = buildTelemetryQuery(args)
        const { columns, rows } = await runKustoQuery(csl, MAX_ROWS)
        const { rows: capped, truncated } = truncateForModel(kustoRowsToObjects(columns, rows))
        return { result: { rows: capped, row_count: capped.length, truncated, read_completed_at_utc: new Date().toISOString() }, rowCount: capped.length, query: csl }
      }
      case 'query_signal_quality_snapshot':
      case 'query_turbine_temperature_snapshot': {
        const temperature = name === 'query_turbine_temperature_snapshot'
        if (!enabledKustoNames(settings).includes('OPCUAEvents')) throw new Error('The OPCUAEvents table is disabled in Administration.')
        entityOrThrow(ASSET_ENTITIES, 'equipment', settings)
        entityOrThrow(ASSET_ENTITIES, 'instruments', settings)
        entityOrThrow(OPERATIONS_ENTITIES, 'work_orders', settings)
        if (!isRayfinConfigured()) throw new Error('The operational database is not configured in this build.')
        if (!isRayfinSignedIn()) throw new Error(SIGN_IN_HINT)
        if (temperature && args.equipment_ids !== undefined && (!Array.isArray(args.equipment_ids) || !args.equipment_ids.length || args.equipment_ids.some(id => typeof id !== 'string' || !id.trim()))) throw new Error('Equipment scope must be a nonempty array of exact equipment IDs.')
        if (!temperature) buildQualitySnapshotQuery(args.quality, args.lookback)
        const csl = temperature ? buildTemperatureSnapshotQuery(args.lookback) : buildLatestSignalSnapshotQuery(args.lookback)
        stid ??= queryStid()
        const [telemetryResult, data, workOrders] = await Promise.all([
          runKustoQuery(csl, MAX_ROWS),
          stid,
          loadOperations('work_orders'),
        ])
        if (!data) throw new Error('Asset metadata is not connected. Connect the STID GraphQL source first.')
        if (!data.inventoryComplete) throw new Error('Asset inventory pagination did not attest a complete equipment/instrument population. Fleet verification is incomplete; no partial snapshot was returned.')
        if (telemetryResult.rows.length >= MAX_ROWS) throw new Error('Fleet snapshot reached the source row limit; complete membership and ranking cannot be verified.')
        const instruments = new Map(data.instruments
          .filter(instrument => instrument.is_active !== false)
          .map(instrument => [instrument.opcua_node_id, instrument]))
        const equipment = new Map(data.equipment
          .filter(asset => asset.is_active !== false)
          .map(asset => [asset.equipment_id, asset]))
        const wantedType = temperature ? 'turbine' : args.equipment_type?.trim().toLowerCase()
        const expectedNodes = [...instruments.values()].filter(instrument => {
          const asset = equipment.get(instrument.equipment_id)
          if (!asset || (wantedType && !`${asset.equipment_type_code ?? ''} ${asset.equipment_type_name ?? ''}`.toLowerCase().includes(wantedType))) return false
          return !temperature || (instrument.opcua_node_id.endsWith('.turbine_temp')
            && (!args.equipment_ids || args.equipment_ids.includes(asset.equipment_id)))
        }).map(instrument => instrument.opcua_node_id)
        const sourceRows = kustoRowsToObjects(telemetryResult.columns, telemetryResult.rows)
        const observedNodes = new Set(sourceRows.map(row => String(row.opcua_node_id ?? '')))
        const unresolvedNodes: string[] = []
        const open = (status: unknown) => !['completed', 'cancelled'].includes(String(status ?? '').trim().toLowerCase())
        const rows = sourceRows.flatMap(reading => {
          const node = String(reading.opcua_node_id ?? '')
          const instrument = instruments.get(node)
          const asset = instrument ? equipment.get(instrument.equipment_id) : undefined
          if (!instrument || !asset) { unresolvedNodes.push(node); return [] }
          const assetType = `${asset.equipment_type_code ?? ''} ${asset.equipment_type_name ?? ''}`.trim()
          if (wantedType && !assetType.toLowerCase().includes(wantedType)) return []
          if (temperature && args.equipment_ids && !args.equipment_ids.includes(asset.equipment_id)) return []
          if (!temperature && String(reading.quality).toUpperCase() !== (args.quality ?? 'BAD').trim().toUpperCase()) return []
          const relatedWork = workOrders
            .filter(order => order.equipmentId === asset.equipment_id && open(order.status))
            .map(order => ({
              workOrderNumber: order.workOrderNumber,
              title: order.title,
              priority: order.priority,
              status: order.status,
              instrumentId: order.instrumentId,
              opcuaNodeId: order.opcuaNodeId,
              relation: order.opcuaNodeId === node || order.instrumentId === instrument.instrument_id
                ? 'same-signal'
                : 'equipment-level',
            }))
          return [{
            turbine: asset.tag,
            equipment_id: asset.equipment_id,
            equipment_type: assetType,
            instrument_id: instrument.instrument_id,
            signal: instrument.instrument_type ?? instrument.tag,
            opcua_node_id: node,
            value: reading.value,
            unit: instrument.unit,
            quality: reading.quality,
            event_time: reading.event_time,
            open_work_orders: relatedWork,
          }]
        })
        if (temperature && new Set(rows.map(row => row.unit)).size > 1) throw new Error('Temperature instruments use different units; a comparable ranking requires explicit unit conversion.')
        const selected = temperature ? rankTemperatureRows(rows, args) : rows
        const { rows: capped, truncated } = truncateForModel(selected)
        return {
          result: {
            read_completed_at_utc: new Date().toISOString(),
            lookback: args.lookback ?? '30m',
            population: {
              equipment_type: wantedType ?? null,
              equipment_ids: temperature ? args.equipment_ids ?? null : null,
              inventory_complete: true,
              expected_signal_count: expectedNodes.length,
              signals_without_readings: expectedNodes.filter(node => !observedNodes.has(node)),
            },
            rows: capped,
            row_count: capped.length,
            ...(temperature ? {
              latest_raw_temperature_ranked_descending: true,
              threshold: args.threshold,
              threshold_operator: args.threshold_operator ?? 'gt',
              requested_limit: args.limit ?? (args.threshold === undefined ? 5 : null),
              requested_equipment_without_readings: (args.equipment_ids ?? []).filter(id => !rows.some(row => row.equipment_id === id)),
            } : { latest_per_signal_then_quality_filter: true, quality_filter: (args.quality ?? 'BAD').trim().toUpperCase(), latest_quality_node_count: sourceRows.filter(row => String(row.quality).toUpperCase() === (args.quality ?? 'BAD').trim().toUpperCase()).length }),
            returned_active_equipment_signal_count: rows.length,
            unresolved_nodes: unresolvedNodes,
            truncated: truncated || telemetryResult.rows.length >= MAX_ROWS,
          },
          rowCount: capped.length,
          query: csl,
        }
      }
      case 'run_kql': {
        const csl = validateKql(args.query ?? '', enabledKustoNames(settings))
        const { columns, rows } = await runKustoQuery(csl, MAX_ROWS)
        const { rows: capped, truncated } = truncateForModel(kustoRowsToObjects(columns, rows))
        return { result: { rows: capped, row_count: capped.length, truncated, read_completed_at_utc: new Date().toISOString() }, rowCount: capped.length, query: csl }
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
      case 'show_3d_model': {
        if (!args.equipment_id && !args.model_id) throw new Error('Give equipment_id or model_id.')
        const models = await loadOperations('asset_models') as unknown as Asset3DModelRecord[]
        const wanted = (value?: string) => (value ?? '').trim().toLowerCase()
        const model = args.model_id
          ? models.find(candidate => wanted(candidate.id) === wanted(args.model_id))
          : models.find(candidate => wanted(candidate.equipmentId) === wanted(args.equipment_id))
        if (!model) {
          const available = [...new Set(models.map(candidate => candidate.equipmentId))].slice(0, 20).join(', ')
          throw new Error(available
            ? `No 3D model matches that asset. Models exist for: ${available}.`
            : 'No 3D models are available. Seed the operational data first.')
        }
        return {
          result: {
            rendered: true,
            model_name: model.modelName,
            format: model.format,
            equipment_id: model.equipmentId,
          },
          model3d: model,
        }
      }
      case 'propose_work_order': {
        const proposal = createWorkOrderProposal({
          equipmentId: args.equipment_id,
          instrumentId: args.instrument_id,
          opcuaNodeId: args.opcua_node_id,
          title: args.title,
          description: args.description,
          priority: options?.proposalPriority ?? 'Medium',
        })
        await validateWorkOrderTarget(proposal)
        const existingWork = (await loadOperations('work_orders')).filter(order =>
          order.equipmentId === proposal.equipmentId && !['completed', 'cancelled'].includes(String(order.status).toLowerCase()))
        workOrderApprovals.stage(proposal)
        options?.onWorkOrderProposal?.(proposal)
        return {
          result: {
            staged: true,
            proposal,
            existing_work: existingWork,
            confirmation_required: true,
            confirmation_method: 'Review the editable approval card and choose Yes or No.',
            priority_policy: 'Priority comes from the explicit operator request, otherwise Medium. The operator can edit it on the card; the model cannot escalate it.',
          },
        }
      }
      default:
        throw new Error(`Unknown tool '${name}'.`)
    }
  }
}
