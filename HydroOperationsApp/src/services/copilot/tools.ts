import type { AgentVisualization } from '../assistantStream.ts'
import { queryStid, runKustoQuery, type StidData } from '../fabric.ts'
import { isRayfinConfigured, isRayfinSignedIn, listAsset3DModels, listInspections, listMaintenanceNotifications, listSpareParts, listWorkOrders, type Asset3DModelRecord } from '../rayfin.ts'
import { ASSET_ENTITIES, OPERATIONS_ENTITIES, type CatalogEntity } from './catalog.ts'
import { enabledKustoNames, isEntityEnabled, isToolEnabled, type CopilotSettings } from './settings.ts'
import { createWorkOrderProposal, type WorkOrderProposal } from './orchestration.ts'
import { validateWorkOrderTarget, workOrderApprovals } from './workOrderApproval.ts'
import {
  validateFilters, buildStationPowerQuery, stationPowerEvidence, stationPowerSummary, fleetSnapshotQuery, shapeFleetSnapshot, buildTelemetryQuery, kustoRowsToObjects, MAX_ROWS,
  shapeCatalogRows as shape, STATION_POWER_SEMANTICS, TOOL_DEFINITIONS, truncateForModel, validateKql, type FilterCondition, type ToolDefinition,
} from './query.ts'

export { TOOL_DEFINITIONS, type ToolDefinition } from './query.ts'


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
        const csl = fleetSnapshotQuery(temperature, args)
        const readStartedAt = new Date().toISOString()
        stid ??= queryStid()
        const [telemetryResult, data, workOrders] = await Promise.all([
          runKustoQuery(csl, MAX_ROWS),
          stid,
          loadOperations('work_orders'),
        ])
        if (!data) throw new Error('Asset metadata is not connected. Connect the STID GraphQL source first.')
        return { ...shapeFleetSnapshot(temperature, args, {
          equipment: data.equipment, instruments: data.instruments, inventoryComplete: data.inventoryComplete,
          telemetryRows: kustoRowsToObjects(telemetryResult.columns, telemetryResult.rows), workOrders,
          readStartedAt, readCompletedAt: new Date().toISOString(),
        }), query: csl }
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
