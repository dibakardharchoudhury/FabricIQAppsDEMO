import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { parseEnv } from 'node:util'
import { pathToFileURL } from 'node:url'
import { RayfinClient } from '@microsoft/rayfin-client'
import { signInWithEntraToken } from '@microsoft/rayfin-auth-provider-fabric'
import { buildTelemetryQuery, buildStationPowerQuery, stationPowerEvidence, STATION_POWER_SEMANTICS, fleetSnapshotQuery, shapeFleetSnapshot, kustoRowsToObjects, MAX_ROWS, shapeCatalogRows, TOOL_DEFINITIONS, validateFilters, validateKql } from '../src/services/copilot/query.ts'
import { ASSET_ENTITIES, KUSTO_SOURCE_NAMES, OPERATIONS_ENTITIES } from '../src/services/copilot/catalog.ts'
import { parseRcaAssessment, RCA_REPORT_TOOL, RcaEvidenceError } from '../src/services/copilot/rcaEvidence.ts'
import { AGENT_NAMES, DIRECT_TOOLS, agentDefinition, parseDelegation, parseHydroQuery, parseWorkOrderReview, requestedNativeSources, verifyNativeReceipt, WORK_REVIEW_TOOL } from '../src/services/copilot/agentDefinitions.ts'
import { createWorkOrderProposal, requiresChartOutput, workOrderPriorityForRequest } from '../src/services/copilot/orchestration.ts'
import { parseKustoPayload } from '../src/services/kustoResult.ts'
export { parseKustoPayload } from '../src/services/kustoResult.ts'

const fabric = 'https://api.fabric.microsoft.com/v1'
const guid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i

export function chatIntent(question) {
  if (typeof question !== 'string' || !question.trim() || question.length > 8000) {
    throw new Error('Chat intent requires a bounded nonempty operator question.')
  }
  return { charts_requested: requiresChartOutput(question), native_sources: requestedNativeSources(question),
    proposal_priority: workOrderPriorityForRequest(question) }
}

class WorkOrderSubmissionError extends Error {
  constructor(message, writeAttempted) {
    super(message)
    this.writeAttempted = writeAttempted
  }
}

export function sourceToolContracts() {
  return {
    tools: TOOL_DEFINITIONS.map(({ function: definition }) => {
      const parameters = structuredClone({ ...definition.parameters, additionalProperties: false })
      const entities = definition.name === 'query_assets' ? ASSET_ENTITIES
        : definition.name === 'query_operations' ? OPERATIONS_ENTITIES : undefined
      if (entities) {
        parameters.allOf = entities.map(entity => ({
          if: { properties: { entity: { const: entity.key } }, required: ['entity'] },
          then: { properties: {
            columns: { minItems: 1, uniqueItems: true, items: { enum: entity.columns.map(column => column.name) } },
            where: { items: { properties: {
              column: { enum: entity.columns.map(column => column.name) },
              value_column: { enum: entity.columns.map(column => column.name) },
            } } },
          } },
        }))
        parameters.properties.where.items.additionalProperties = false
      }
      if (parameters.properties.limit) {
        parameters.properties.limit.minimum = 1
        parameters.properties.limit.maximum = MAX_ROWS
      }
      return { name: definition.name, parameters }
    }),
    context: {
      asset_entities: structuredClone(ASSET_ENTITIES),
      operations_entities: structuredClone(OPERATIONS_ENTITIES),
      kusto_source_names: [...KUSTO_SOURCE_NAMES],
    },
  }
}

export async function collectCatalogRows(entity, loadPage) {
  const rows = [], cursors = new Set(), identities = new Set()
  const identityColumn = entity.identityColumn
  if (!identityColumn || !entity.columns.some(column => column.name === identityColumn)) {
    throw new Error('Catalog entity has no valid declared identity column.')
  }
  let cursor
  for (let pageNumber = 0; pageNumber < MAX_ROWS / 100; pageNumber++) {
    const page = await loadPage(cursor)
    if (!page || !Array.isArray(page.items) || page.items.length > 100 || typeof page.hasNextPage !== 'boolean') {
      throw new Error('Catalog source returned an invalid page.')
    }
    for (const row of page.items) {
      const identity = row?.[identityColumn]
      if (!row || typeof row !== 'object' || Array.isArray(row)
        || typeof identity !== 'string' || !identity.trim() || identities.has(identity)) {
        throw new Error('Catalog source returned an invalid or duplicate row identity.')
      }
      identities.add(identity)
      rows.push(row)
    }
    if (!page.hasNextPage) return rows
    if (typeof page.endCursor !== 'string' || !page.endCursor || cursors.has(page.endCursor)) {
      throw new Error('Catalog source returned an invalid continuation.')
    }
    cursors.add(page.endCursor)
    cursor = page.endCursor
  }
  throw new Error('Catalog source exceeds its complete-read bound; partial evidence is not accepted.')
}

function validateCatalogArguments(entity, args) {
  validateFilters(args.where, entity.columns.map(column => column.name))
  if (args.columns !== undefined && (!Array.isArray(args.columns) || !args.columns.length
    || args.columns.some(column => typeof column !== 'string' || !entity.columns.some(item => item.name === column))
    || new Set(args.columns).size !== args.columns.length)) {
    throw new Error('Requested columns must be distinct declared catalog columns.')
  }
  if (args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > MAX_ROWS)) {
    throw new Error('Requested row limit is outside the catalog read bound.')
  }
}

export async function readAssetEntity(config, entityKey, args, token, fetcher = fetch) {
  const entity = ASSET_ENTITIES.find(candidate => candidate.key === entityKey)
  if (!entity) throw new Error('Unknown asset catalog entity.')
  validateCatalogArguments(entity, args)
  const rows = await readAssetInventory(config, entity, token, fetcher)
  const shaped = shapeCatalogRows(entity, rows, args, false, true)
  return { ...shaped.result, _row_identities: shaped.rowIdentities, read_completed_at_utc: new Date().toISOString() }
}

async function readAssetInventory(config, entity, token, fetcher) {
  const fields = entity.columns.map(column => column.name).join(' ')
  return collectCatalogRows(entity, async cursor => {
    const payload = await requestJson(`${fabric}/workspaces/${config.workspace_id}/graphqlapis/${config.graphql_id}/graphql`,
      token, {
        query: `query CatalogRead($cursor: String) {
          records: ${entity.physicalName}(first: 100, after: $cursor) {
            hasNextPage endCursor items { ${fields} }
          }
        }`,
        variables: { cursor: cursor ?? null },
      }, fetcher)
    if (!payload || payload.errors?.length || !payload.data?.records) {
      throw new Error('Asset catalog query failed or omitted its data.')
    }
    return payload.data.records
  })
}

export async function collectOperationRows(client, entityKey, args) {
  const entity = OPERATIONS_ENTITIES.find(candidate => candidate.key === entityKey)
  if (!entity) throw new Error('Unknown operational catalog entity.')
  validateCatalogArguments(entity, args)
  const rows = await collectOperationInventory(client, entity)
  const shaped = shapeCatalogRows(entity, rows, args, false, true)
  return { ...shaped.result, _row_identities: shaped.rowIdentities, read_completed_at_utc: new Date().toISOString() }
}

async function collectOperationInventory(client, entity) {
  return collectCatalogRows(entity, async cursor => {
    let query = client.data[entity.physicalName].select(entity.columns.map(column => column.name))
      .orderBy({ [entity.identityColumn]: 'asc' }).first(100)
    if (cursor) query = query.after(cursor)
    return query.executePaginated()
  })
}

export async function readTelemetryQuery(metadata, query, token, fetcher = fetch) {
  const payload = await requestJson(`${metadata.cluster}/v1/rest/query`, token, {
    db: metadata.database, csl: query,
    properties: { Options: { truncationmaxrecords: MAX_ROWS + 1, servertimeout: '00:00:25' } },
  }, fetcher)
  const table = parseKustoPayload(payload)
  const columns = table.Columns.map(column => column.ColumnName)
  if (table.Rows.length > MAX_ROWS) {
    throw new Error('Telemetry query exceeds its source row bound.')
  }
  const rows = kustoRowsToObjects(columns, table.Rows)
  return { rows, row_count: rows.length, truncated: false,
    read_completed_at_utc: new Date().toISOString() }
}

export async function readStationPower(metadata, args, token, fetcher = fetch) {
  const lookback = args.lookback ?? '24h'
  const reading = await readTelemetryQuery(metadata, buildStationPowerQuery(lookback), token, fetcher)
  const evidence = stationPowerEvidence(reading.rows, lookback)
  return { rows: evidence.rows, row_count: evidence.rows.length, truncated: false, lookback,
    semantics: STATION_POWER_SEMANTICS, read_completed_at_utc: reading.read_completed_at_utc }
}

export function runtimeConfiguration(input) {
  const keys = ['tenant_id', 'workspace_id', 'ontology_id', 'eventhouse_id', 'database_id', 'graphql_id', 'appbackend_id', 'api_url', 'publishable_key']
  if (!input || typeof input !== 'object' || Array.isArray(input)
    || Object.keys(input).length !== keys.length || Object.keys(input).some(key => !keys.includes(key))
    || keys.slice(0, 7).some(key => typeof input[key] !== 'string' || !guid.test(input[key]))
    || typeof input.publishable_key !== 'string' || !/^pk-\S+$/.test(input.publishable_key)
    || typeof input.api_url !== 'string') {
    throw new Error('Explicit runtime source configuration is missing or invalid.')
  }
  let api
  try { api = new URL(input.api_url) }
  catch { throw new Error('Explicit runtime AppBackend endpoint is invalid.') }
  if (api.protocol !== 'https:' || api.username || api.password || api.port || api.search || api.hash
    || !/^[0-9a-f]{32}\.pbidedicated\.windows\.net$/.test(api.hostname)) {
    throw new Error('Explicit runtime AppBackend endpoint is not a capacity endpoint.')
  }
  const config = Object.fromEntries(keys.map(key => [key, input[key]]))
  return { ...config, configuration_digest: createHash('sha256').update(JSON.stringify(config)).digest('hex') }
}

export async function configuration() {
  if (process.env.HYDRO_FABRIC_SOURCE_CONFIG !== undefined) {
    let input
    try { input = JSON.parse(process.env.HYDRO_FABRIC_SOURCE_CONFIG) }
    catch { throw new Error('Explicit runtime source configuration is not valid JSON.') }
    return runtimeConfiguration(input)
  }
  const env = parseEnv(await readFile(new URL('../rayfin/.env', import.meta.url), 'utf8'))
  const binding = JSON.parse(env.RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING)
  const identity = {
    tenant_id: env.RAYFIN_PUBLIC_TENANT_ID,
    workspace_id: env.RAYFIN_PUBLIC_WORKSPACE_ID,
    ontology_id: binding.ontologyId,
    eventhouse_id: env.RAYFIN_PUBLIC_EVENTHOUSE_ID,
    database_id: env.RAYFIN_PUBLIC_KQL_DATABASE_ID,
    graphql_id: env.RAYFIN_PUBLIC_STID_GRAPHQL_ID,
    appbackend_id: env.RAYFIN_PUBLIC_ITEM_ID,
  }
  if (Object.values(identity).some(value => typeof value !== 'string' || !guid.test(value))
    || binding.workspaceId !== identity.workspace_id) throw new Error('Explicit source identities are missing or inconsistent.')
  const state = JSON.parse(await readFile(new URL('../rayfin/.deployments.json', import.meta.url), 'utf8'))
  const deployment = state.deployments?.[state.active]
  if (!env.RAYFIN_PUBLIC_API_URL || env.RAYFIN_PUBLIC_API_URL !== deployment?.fabricApiUrl
    || !env.RAYFIN_PUBLIC_PUBLISHABLE_KEY?.startsWith('pk-')
    || env.RAYFIN_PUBLIC_PUBLISHABLE_KEY !== deployment?.publishableKey) {
    throw new Error('Saved AppBackend endpoint/key do not match the active deployment.')
  }
  const config = { ...identity, api_url: env.RAYFIN_PUBLIC_API_URL, publishable_key: env.RAYFIN_PUBLIC_PUBLISHABLE_KEY }
  return { ...config, configuration_digest: createHash('sha256').update(JSON.stringify(config)).digest('hex') }
}

export async function requestJson(url, token, body, fetcher = fetch) {
  const response = await fetcher(url, {
    method: body ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
    redirect: 'error', signal: AbortSignal.timeout(30000),
  })
  if (!response.ok) throw new Error(`Source request failed: HTTP ${response.status} (${new URL(url).pathname}).`)
  return response.json()
}

export async function discover(config, token, fetcher = fetch) {
  const base = `${fabric}/workspaces/${config.workspace_id}`
  const [workspace, ontology, database] = await Promise.all([
    requestJson(base, token, undefined, fetcher),
    requestJson(`${base}/ontologies/${config.ontology_id}`, token, undefined, fetcher),
    requestJson(`${base}/kqlDatabases/${config.database_id}`, token, undefined, fetcher),
  ])
  if (workspace.id !== config.workspace_id || ontology.id !== config.ontology_id
    || ontology.properties?.generation !== 2 || database.id !== config.database_id
    || database.properties?.parentEventhouseItemId !== config.eventhouse_id) {
    throw new Error('Live source identity or numeric ontology generation 2 verification failed.')
  }
  if (!guid.test(workspace.capacityId ?? '')) throw new Error('Workspace has no verified capacity.')
  const api = new URL(config.api_url)
  const path = `/webapi/capacities/${workspace.capacityId}/workloads/baas/baasservice/automatic/v1/workspaces/${config.workspace_id}/appbackends/${config.appbackend_id}`
  if (api.protocol !== 'https:' || api.username || api.password || api.search || api.hash
    || api.hostname !== `${workspace.capacityId.replaceAll('-', '')}.pbidedicated.windows.net`.toLowerCase()
    || api.pathname.replace(/\/$/, '').toLowerCase() !== path.toLowerCase()) {
    throw new Error('Saved AppBackend endpoint does not match the live workspace capacity; use the canonical deployer.')
  }
  const cluster = new URL(database.properties.queryServiceUri)
  if (cluster.protocol !== 'https:' || cluster.username || cluster.password || cluster.search || cluster.hash
    || cluster.port || !['', '/'].includes(cluster.pathname)
    || !/\.(?:kusto\.fabric\.microsoft\.com|kusto\.windows\.net)$/.test(cluster.hostname)
    || typeof database.displayName !== 'string' || !database.displayName.trim()) {
    throw new Error('KQL metadata did not return a trusted query endpoint/database.')
  }
  return {
    source: { tenant_id: config.tenant_id, workspace_id: config.workspace_id,
      ontology_id: config.ontology_id, generation: 2, configuration_digest: config.configuration_digest },
    cluster: cluster.origin, database: database.displayName,
  }
}

function pageItems(page, label) {
  if (!page || page.hasNextPage !== false || !Array.isArray(page.items)) {
    throw new Error(`${label} read is missing or incomplete; partial evidence is not accepted.`)
  }
  return page.items
}

export async function readTelemetry(config, metadata, equipmentId, tokens, fetcher = fetch) {
  const payload = await requestJson(`${fabric}/workspaces/${config.workspace_id}/graphqlapis/${config.graphql_id}/graphql`,
    tokens.graphql, {
      query: `query MaintenanceIdentity($id: String!) {
        equipment: silver_equipments(first: 2, filter: { equipment_id: { eq: $id } }) {
          hasNextPage items { equipment_id }
        }
        instruments: silver_instruments(first: 101, filter: { equipment_id: { eq: $id } }) {
          hasNextPage items { equipment_id opcua_node_id unit }
        }
      }`,
      variables: { id: equipmentId },
    }, fetcher)
  if (payload.errors?.length) throw new Error('STID GraphQL returned errors; no evidence accepted.')
  const equipment = pageItems(payload.data?.equipment, 'Equipment')
  const instruments = pageItems(payload.data?.instruments, 'Instruments')
  if (equipment.length !== 1 || equipment[0].equipment_id !== equipmentId || !instruments.length
    || instruments.length > 100 || instruments.some(item => item.equipment_id !== equipmentId
      || typeof item.opcua_node_id !== 'string' || !item.opcua_node_id
      || typeof item.unit !== 'string' || !item.unit.trim())
    || new Set(instruments.map(item => item.opcua_node_id)).size !== instruments.length) {
    throw new Error('Equipment/signal identity or measurement units are missing, ambiguous or out of bounds.')
  }
  const nodes = instruments.map(item => item.opcua_node_id)
  const query = buildTelemetryQuery({ opcua_node_ids: nodes, aggregation: 'latest', lookback: '24h', limit: 101 })
  const queryStartedAt = Date.now()
  const result = await requestJson(`${metadata.cluster}/v1/rest/query`, tokens.kusto, {
    db: metadata.database, csl: query,
    properties: { Options: { truncationmaxrecords: 101, servertimeout: '00:00:25' } },
  }, fetcher)
  const table = parseKustoPayload(result)
  const columns = table.Columns.map(item => item.ColumnName)
  const required = ['opcua_node_id', 'event_time', 'value', 'quality']
  if (required.some(column => !columns.includes(column)) || new Set(columns).size !== columns.length
    || table.Rows.some(row => !Array.isArray(row) || row.length !== columns.length)) {
    throw new Error('KQL columns/rows do not match the telemetry contract.')
  }
  const rows = kustoRowsToObjects(columns, table.Rows)
  if (!rows.length || rows.length > nodes.length || new Set(rows.map(row => row.opcua_node_id)).size !== rows.length) {
    throw new Error('KQL returned no measurements or an invalid latest-signal result.')
  }
  const readTime = new Date()
  const observations = rows.map(row => {
    const instrument = instruments.find(item => item.opcua_node_id === row.opcua_node_id)
    if (!instrument || typeof row.value !== 'number' || !Number.isFinite(row.value)
      || !['GOOD', 'BAD', 'UNCERTAIN'].includes(row.quality) || typeof row.event_time !== 'string'
      || !/Z$/.test(row.event_time) || !Number.isFinite(Date.parse(row.event_time))
      || Date.parse(row.event_time) > readTime.getTime()
      || Date.parse(row.event_time) < queryStartedAt - 24 * 3600000) {
      throw new Error('Telemetry identity, value, quality or timestamp is invalid.')
    }
    return { evidence_id: row.opcua_node_id, metric: row.opcua_node_id, value: row.value,
      unit: instrument.unit, event_time: row.event_time, quality: row.quality }
  })
  const gaps = []
  if (observations.length !== nodes.length) gaps.push('measurements_for_some_mapped_signals')
  if (observations.some(row => Date.parse(row.event_time) < readTime.getTime() - 30 * 60000)) gaps.push('fresh_telemetry')
  return { observations, missing_sources: gaps, read_completed_at: readTime.toISOString() }
}

export async function collectWorkOrders(client, equipmentId) {
  const items = []
  const cursors = new Set()
  let cursor
  for (let pageNumber = 0; pageNumber < 5; pageNumber++) {
    let query = client.data.WorkOrder.select(['id', 'workOrderNumber', 'equipmentId', 'instrumentId', 'opcuaNodeId', 'title', 'description', 'status', 'priority'])
      .where({ equipmentId: { eq: equipmentId } }).orderBy({ id: 'asc' }).first(100)
    if (cursor) query = query.after(cursor)
    const page = await query.executePaginated()
    if (!Array.isArray(page.items) || page.items.length > 100 || typeof page.hasNextPage !== 'boolean') throw new Error('Invalid work-order page.')
    items.push(...page.items)
    if (items.some(item => item.equipmentId !== equipmentId || typeof item.status !== 'string' || !item.status.trim()
      || typeof item.workOrderNumber !== 'string' || !item.workOrderNumber || typeof item.id !== 'string' || !item.id)
      || new Set(items.map(item => item.id)).size !== items.length
      || new Set(items.map(item => item.workOrderNumber)).size !== items.length) throw new Error('Invalid or duplicate work-order identity.')
    if (!page.hasNextPage) return {
      rows: items.filter(item => !['completed', 'cancelled'].includes(item.status.trim().toLowerCase())),
      read_completed_at: new Date().toISOString(),
    }
    if (typeof page.endCursor !== 'string' || !page.endCursor || cursors.has(page.endCursor)) throw new Error('Invalid work-order continuation.')
    cursors.add(page.endCursor)
    cursor = page.endCursor
  }
  throw new Error('Work-order read exceeds 500 rows; partial coverage is not accepted.')
}

async function operationalRead(config, token, label, read) {
  const client = new RayfinClient({ baseUrl: `${config.api_url.replace(/\/$/, '')}/`,
    publishableKey: config.publishable_key, authStorage: false })
  try {
    await signInWithEntraToken(client.auth, { entraToken: token })
    return await read(client)
  } catch (error) {
    const code = typeof error?.code === 'string' && /^[A-Z_0-9]+$/.test(error.code) ? error.code : 'READ_FAILED'
    throw new Error(`Rayfin ${label} read failed (${code}). No empty-work fallback or proposal is permitted.`)
  } finally {
    client.auth.destroy()
  }
}

export async function readWork(config, equipmentId, token) {
  return operationalRead(config, token, 'work-order', client => collectWorkOrders(client, equipmentId))
}

export async function readOperationEntity(config, entityKey, args, token) {
  const entity = OPERATIONS_ENTITIES.find(candidate => candidate.key === entityKey)
  if (!entity) throw new Error('Unknown operational catalog entity.')
  validateCatalogArguments(entity, args)
  return operationalRead(config, token, entityKey, client => collectOperationRows(client, entityKey, args))
}

export async function readFleetSnapshot(config, metadata, temperature, args, tokens, fetcher = fetch,
  readWorkInventory = () => operationalRead(config, tokens.graphql, 'work-order snapshot',
    client => collectOperationInventory(client, OPERATIONS_ENTITIES.find(entity => entity.key === 'work_orders')))) {
  const query = fleetSnapshotQuery(temperature, args)
  const readStartedAt = new Date().toISOString()
  const [equipment, instruments, workOrders, telemetry] = await Promise.all([
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'equipment'), tokens.graphql, fetcher),
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'instruments'), tokens.graphql, fetcher),
    readWorkInventory(),
    readTelemetryQuery(metadata, query, tokens.kusto, fetcher),
  ])
  return shapeFleetSnapshot(temperature, args, {
    equipment, instruments, inventoryComplete: true, telemetryRows: telemetry.rows, workOrders,
    workInventoryComplete: true, readStartedAt, readCompletedAt: new Date().toISOString(),
  }).result
}

export async function stageWorkOrder(config, args, priority, tokens, fetcher = fetch,
  readExisting = () => readWork(config, args.equipment_id, tokens.graphql)) {
  const proposal = createWorkOrderProposal({
    equipmentId: args.equipment_id, instrumentId: args.instrument_id, opcuaNodeId: args.opcua_node_id,
    title: args.title, description: args.description, priority,
  })
  const [equipment, instruments, work] = await Promise.all([
    readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'equipment'), tokens.graphql, fetcher),
    proposal.instrumentId || proposal.opcuaNodeId
      ? readAssetInventory(config, ASSET_ENTITIES.find(entity => entity.key === 'instruments'), tokens.graphql, fetcher)
      : Promise.resolve([]),
    readExisting(),
  ])
  const matches = equipment.filter(row => row.equipment_id === proposal.equipmentId && row.is_active === true)
  if (matches.length !== 1) throw new Error('Draft equipment is not uniquely verified as active in this workspace.')
  if (proposal.instrumentId || proposal.opcuaNodeId) {
    const signals = instruments.filter(row => row.equipment_id === proposal.equipmentId && row.is_active === true
      && (!proposal.instrumentId || row.instrument_id === proposal.instrumentId)
      && (!proposal.opcuaNodeId || row.opcua_node_id === proposal.opcuaNodeId))
    if (signals.length !== 1) throw new Error('Draft signal is not uniquely bound to the active selected equipment.')
    proposal.instrumentId = signals[0].instrument_id
    proposal.opcuaNodeId = signals[0].opcua_node_id
  }
  if (!work || !Array.isArray(work.rows) || typeof work.read_completed_at !== 'string'
    || !Number.isFinite(Date.parse(work.read_completed_at))) throw new Error('Draft requires a complete work-source read.')
  if (work.rows.some(row => row.equipmentId !== proposal.equipmentId || typeof row.id !== 'string' || !row.id
    || typeof row.workOrderNumber !== 'string' || !row.workOrderNumber
    || typeof row.title !== 'string' || !row.title.trim() || typeof row.status !== 'string' || !row.status.trim()
    || ['completed', 'cancelled'].includes(row.status.trim().toLowerCase()))
    || new Set(work.rows.map(row => row.id)).size !== work.rows.length) {
    throw new Error('Draft work coverage contains invalid or unrelated open work.')
  }
  return { proposal, existing_work: work.rows, work_read_at: work.read_completed_at,
    staged: true, confirmation_required: true, production_write_executed: false,
    read_completed_at_utc: new Date().toISOString() }
}

export async function submitApprovedWorkOrder(config, draft, edits, principalId, creationId, tokens, allowCreate,
  fetcher = fetch, useClient) {
  if (!guid.test(principalId) || !guid.test(creationId) || typeof allowCreate !== 'boolean') {
    throw new Error('Work-order submission requires verified principal and creation identities.')
  }
  const checked = createWorkOrderProposal({
    equipmentId: draft.equipment_id, instrumentId: draft.instrument_id ?? undefined,
    opcuaNodeId: draft.opcua_node_id ?? undefined,
    title: edits.title, description: edits.description, priority: edits.priority,
  })
  const expected = { id: creationId, workOrderNumber: `WO-${creationId}`, equipmentId: checked.equipmentId,
    instrumentId: checked.instrumentId ?? null, opcuaNodeId: checked.opcuaNodeId ?? null,
    title: checked.title, description: checked.description, priority: checked.priority,
    status: 'Draft', createdByOid: principalId }
  let writeAttempted = false
  const execute = async client => {
    const readBack = async () => {
      const rows = await client.data.WorkOrder.select(Object.keys(expected))
        .where({ id: { eq: creationId } }).first(2).execute()
      if (!Array.isArray(rows) || rows.length > 1) throw new Error('Work-order creation identity is ambiguous.')
      if (!rows.length) return undefined
      const row = rows[0]
      if (Object.entries(expected).some(([key, value]) => (row[key] ?? null) !== value)) {
        throw new Error('Work-order readback does not match the approved fields and signed-in principal.')
      }
      return row
    }
    const existing = await readBack()
    if (existing) return { record: existing, reconciled: true, read_completed_at_utc: new Date().toISOString() }
    if (!allowCreate) throw new Error('Prior submission remains unconfirmed; reconciliation will not repeat a SQL write.')
    const fresh = await stageWorkOrder(config, {
      equipment_id: checked.equipmentId, instrument_id: checked.instrumentId, opcua_node_id: checked.opcuaNodeId,
      title: checked.title, description: checked.description,
    }, checked.priority, tokens, fetcher, () => collectWorkOrders(client, checked.equipmentId))
    if (fresh.existing_work.some(row => row.title?.trim().toLowerCase() === checked.title.trim().toLowerCase())) {
      throw new WorkOrderSubmissionError('An open work order with this title already exists for this equipment. No SQL write was attempted.', false)
    }
    try {
      writeAttempted = true
      await client.data.WorkOrder.create({ ...expected, instrumentId: checked.instrumentId,
        opcuaNodeId: checked.opcuaNodeId, createdAt: new Date() })
    } catch (error) {
      const reconciled = await readBack()
      if (reconciled) return { record: reconciled, reconciled: true, read_completed_at_utc: new Date().toISOString() }
      throw new Error('SQL creation could not be confirmed; do not submit another write without reconciliation.', { cause: error })
    }
    const record = await readBack()
    if (!record) throw new Error('SQL creation returned without matching readback; its outcome is unconfirmed.')
    return { record, reconciled: false, read_completed_at_utc: new Date().toISOString() }
  }
  if (useClient) return execute(await useClient())
  const client = new RayfinClient({ baseUrl: `${config.api_url.replace(/\/$/, '')}/`,
    publishableKey: config.publishable_key, authStorage: false })
  try {
    await signInWithEntraToken(client.auth, { entraToken: tokens.graphql })
    return await execute(client)
  } catch (error) {
    if (error instanceof WorkOrderSubmissionError) throw error
    const code = typeof error?.code === 'string' && /^[A-Z_0-9]+$/.test(error.code) ? error.code : 'SUBMISSION_UNCONFIRMED'
    throw new WorkOrderSubmissionError(writeAttempted
      ? `Work-order submission could not be confirmed (${code}); reconcile before any new write.`
      : `Work-order source validation failed (${code}); no SQL write was attempted.`, writeAttempted)
  } finally {
    client.auth.destroy()
  }
}

async function main() {
  let input = ''
  for await (const chunk of process.stdin) {
    input += chunk
    if (input.length > 262144) throw new Error('Local adapter request exceeds its input bound.')
  }
  const request = JSON.parse(input)
  if (request.action === 'chat_intent') return chatIntent(request.question)
  if (request.action === 'source_contracts') return sourceToolContracts()
  if (request.action === 'rca_contract') return { tool: RCA_REPORT_TOOL }
  if (request.action === 'validate_rca') return parseRcaAssessment(request.report, request.receipts)
  if (request.action === 'work_review_contract') return { tool: WORK_REVIEW_TOOL }
  if (request.action === 'validate_work_review') return parseWorkOrderReview(request.report)
  if (request.action === 'agent_contracts') return {
    names: AGENT_NAMES,
    tools: Object.fromEntries(['supervisor', 'qa', 'rca', 'work-order']
      .map(role => [role, agentDefinition(role, 'contract-only').tools])),
  }
  if (request.action === 'validate_delegation') return parseDelegation(request.report)
  if (request.action === 'validate_native_receipt') {
    if (!verifyNativeReceipt(request.receipt, request.source)) throw new Error('Native execution receipt is missing.')
    return { accepted: true }
  }
  if (request.action === 'validate_hydro_query') {
    const parsed = parseHydroQuery(request.report)
    if (!parsed.ok) throw new Error(parsed.error)
    const allowed = request.role === 'work-order' ? [...DIRECT_TOOLS, 'propose_work_order'] : DIRECT_TOOLS
    if (!allowed.includes(parsed.toolName)) throw new Error('Tool is not permitted for this specialist.')
    return parsed
  }
  const config = await configuration()
  if (request.action === 'configuration') {
    const { api_url: _url, publishable_key: _key, ...identity } = config
    return identity
  }
  if (request.configuration_digest !== config.configuration_digest) throw new Error('Source configuration changed; restart the local source adapter.')
  const metadata = await discover(config, request.tokens.fabric)
  if (request.action === 'discover') return metadata
  if (request.action === 'approve_work_order') {
    if (request.human_approved !== true) throw new Error('A human approval is required outside agent tool execution.')
    const result = await submitApprovedWorkOrder(config, request.draft, request.edits, request.principal_id,
      request.creation_id, request.tokens, request.allow_create)
    return { source: metadata.source, result, completed_at: result.read_completed_at_utc }
  }
  if (request.action === 'tool_read') {
    const args = request.arguments
    const definition = TOOL_DEFINITIONS.find(tool => tool.function.name === request.tool)?.function
    if (!definition || !args || typeof args !== 'object' || Array.isArray(args)
      || Object.keys(args).some(key => !Object.hasOwn(definition.parameters.properties, key))) {
      throw new Error('Invalid source tool arguments.')
    }
    let result
    switch (request.tool) {
      case 'query_assets':
        result = await readAssetEntity(config, args.entity, args, request.tokens.graphql)
        break
      case 'query_operations':
        result = await readOperationEntity(config, args.entity, args, request.tokens.graphql)
        break
      case 'query_telemetry':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readTelemetryQuery(metadata, buildTelemetryQuery(args), request.tokens.kusto)
        break
      case 'query_station_power':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readStationPower(metadata, args, request.tokens.kusto)
        break
      case 'propose_work_order':
        result = await stageWorkOrder(config, args, request.proposal_priority, request.tokens)
        break
      case 'run_kql':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readTelemetryQuery(metadata, validateKql(args.query, KUSTO_SOURCE_NAMES), request.tokens.kusto)
        break
      case 'query_signal_quality_snapshot':
      case 'query_turbine_temperature_snapshot':
        if (request.cluster !== metadata.cluster) throw new Error('The KQL endpoint changed.')
        result = await readFleetSnapshot(config, metadata, request.tool === 'query_turbine_temperature_snapshot',
          args, request.tokens)
        break
      default:
        throw new Error('This source tool has no configured backend implementation.')
    }
    return { source: metadata.source, result, completed_at: result.read_completed_at_utc }
  }
  if (!['read', 'probe', 'telemetry_only'].includes(request.action) || typeof request.equipment_id !== 'string'
    || !request.equipment_id.trim() || request.equipment_id.length > 200 || request.cluster !== metadata.cluster) {
    throw new Error('Invalid local source request or changed KQL endpoint.')
  }
  if (request.action === 'telemetry_only') {
    const telemetry = await readTelemetry(config, metadata, request.equipment_id, request.tokens)
    return { ...telemetry, source: metadata.source, equipment_id: request.equipment_id, open_work_numbers: [],
      missing_sources: [...telemetry.missing_sources, 'work_orders_not_requested', 'inspections_not_requested'] }
  }
  const results = await Promise.allSettled([
    readTelemetry(config, metadata, request.equipment_id, request.tokens),
    readWork(config, request.equipment_id, request.tokens.graphql),
  ])
  const failures = results.flatMap((result, index) => result.status === 'rejected'
    ? [{ source: index === 0 ? 'stid_telemetry' : 'work_orders', message: result.reason.message }] : [])
  if (request.action === 'probe') {
    return { ready: failures.length === 0, source: metadata.source, failures,
      checks: results.map((result, index) => ({ source: index === 0 ? 'stid_telemetry' : 'work_orders',
        status: result.status === 'fulfilled' ? 'passed' : 'failed',
        ...(result.status === 'fulfilled' && index === 0
          ? { observation_count: result.value.observations.length, missing_sources: result.value.missing_sources } : {}) })) }
  }
  if (failures.length) throw new Error(failures.map(item => `${item.source}: ${item.message}`).join('; '))
  const [telemetry, work] = results.map(result => result.value)
  const completedAt = new Date().toISOString()
  return { ...telemetry, source: metadata.source, equipment_id: request.equipment_id,
    read_completed_at: completedAt, work_orders_read_at: work.read_completed_at,
    open_work_numbers: work.rows.map(item => item.workOrderNumber) }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.stdout.write(`\n__HYDRO_RESULT__=${JSON.stringify({ ok: true, result: await main() })}`)
  } catch (error) {
    // Do not serialize SDK exceptions, request headers, tokens or response bodies.
    const message = error instanceof RcaEvidenceError || error instanceof WorkOrderSubmissionError
      || (error instanceof Error && error.constructor === Error)
      ? error.message : 'Local source adapter failed; inspect source/authentication prerequisites.'
    process.stdout.write(`\n__HYDRO_RESULT__=${JSON.stringify({ ok: false, error: message,
      ...(error instanceof WorkOrderSubmissionError ? { write_attempted: error.writeAttempted } : {}) })}`)
    process.exitCode = 1
  }
}
