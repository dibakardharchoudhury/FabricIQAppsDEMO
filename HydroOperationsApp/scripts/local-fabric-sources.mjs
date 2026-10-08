import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { parseEnv } from 'node:util'
import { pathToFileURL } from 'node:url'
import { RayfinClient } from '@microsoft/rayfin-client'
import { signInWithEntraToken } from '@microsoft/rayfin-auth-provider-fabric'
import { buildTelemetryQuery, kustoRowsToObjects } from '../src/services/copilot/query.ts'

const fabric = 'https://api.fabric.microsoft.com/v1'
const guid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i

export async function configuration() {
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

export function parseKustoPayload(result) {
  const tables = result.Tables
  if (result.error || result.HasErrors === true || result.Exceptions?.length || !Array.isArray(tables) || !tables.length
    || tables.some(table => !Array.isArray(table.Columns) || !Array.isArray(table.Rows)
      || table.Columns.some(column => typeof column.ColumnName !== 'string')
      || new Set(table.Columns.map(column => column.ColumnName)).size !== table.Columns.length
      || table.Rows.some(row => !Array.isArray(row) || row.length !== table.Columns.length))) {
    throw new Error('KQL returned an error, partial result or an unexpected result envelope.')
  }
  if (tables.length === 1) return tables[0]
  const contents = tables.filter(table => ['Ordinal', 'Kind', 'Name'].every(name => table.Columns.some(c => c.ColumnName === name)))
  if (contents.length !== 1) throw new Error('KQL table-of-contents is missing or ambiguous.')
  const entries = kustoRowsToObjects(contents[0].Columns.map(c => c.ColumnName), contents[0].Rows)
  if (entries.length !== tables.length - 1 || new Set(entries.map(entry => entry.Ordinal)).size !== entries.length
    || entries.some(entry => !Number.isInteger(entry.Ordinal) || entry.Ordinal < 0 || entry.Ordinal >= tables.length
      || tables[entry.Ordinal] === contents[0] || !['QueryResult', 'QueryProperties', 'QueryStatus'].includes(entry.Kind))) {
    throw new Error('KQL table-of-contents is invalid or contains unsupported result types.')
  }
  const primary = entries.filter(entry => entry.Kind === 'QueryResult' && entry.Name === 'PrimaryResult')
  const status = entries.filter(entry => entry.Kind === 'QueryStatus')
  if (primary.length !== 1 || status.length !== 1 || entries.filter(entry => entry.Kind === 'QueryResult').length !== 1) {
    throw new Error('KQL must return one primary result and one completion status.')
  }
  const statusTable = tables[status[0].Ordinal]
  const statuses = kustoRowsToObjects(statusTable.Columns.map(c => c.ColumnName), statusTable.Rows)
  if (!statuses.some(row => row.Severity === 4 && row.StatusCode === 0)
    || statuses.some(row => !Number.isInteger(row.Severity) || row.Severity < 4 || row.StatusCode !== 0)) {
    throw new Error('KQL completion reports failure, warning or incomplete execution; partial rows rejected.')
  }
  return tables[primary[0].Ordinal]
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
    let query = client.data.WorkOrder.select(['id', 'workOrderNumber', 'equipmentId', 'title', 'description', 'status', 'priority'])
      .where({ equipmentId: { eq: equipmentId } }).orderBy({ id: 'asc' }).first(100)
    if (cursor) query = query.after(cursor)
    const page = await query.executePaginated()
    if (!Array.isArray(page.items) || typeof page.hasNextPage !== 'boolean') throw new Error('Invalid work-order page.')
    items.push(...page.items)
    if (items.some(item => item.equipmentId !== equipmentId || typeof item.status !== 'string' || !item.status.trim()
      || typeof item.workOrderNumber !== 'string' || !item.workOrderNumber || typeof item.id !== 'string' || !item.id)
      || new Set(items.map(item => item.id)).size !== items.length
      || new Set(items.map(item => item.workOrderNumber)).size !== items.length) throw new Error('Invalid or duplicate work-order identity.')
    if (!page.hasNextPage) return items.filter(item => !['completed', 'cancelled'].includes(item.status.trim().toLowerCase()))
    if (typeof page.endCursor !== 'string' || !page.endCursor || cursors.has(page.endCursor)) throw new Error('Invalid work-order continuation.')
    cursors.add(page.endCursor)
    cursor = page.endCursor
  }
  throw new Error('Work-order read exceeds 500 rows; partial coverage is not accepted.')
}

export async function readWork(config, equipmentId, token) {
  const client = new RayfinClient({ baseUrl: `${config.api_url.replace(/\/$/, '')}/`,
    publishableKey: config.publishable_key, authStorage: false })
  try {
    await signInWithEntraToken(client.auth, { entraToken: token })
    return await collectWorkOrders(client, equipmentId)
  } catch (error) {
    const code = typeof error.code === 'string' && /^[A-Z_0-9]+$/.test(error.code) ? error.code : 'READ_FAILED'
    throw new Error(`Rayfin work-order read failed (${code}). No empty-work fallback or proposal is permitted.`)
  } finally {
    client.auth.destroy()
  }
}

async function main() {
  let input = ''
  for await (const chunk of process.stdin) {
    input += chunk
    if (input.length > 65536) throw new Error('Local adapter request exceeds its input bound.')
  }
  const request = JSON.parse(input)
  const config = await configuration()
  if (request.action === 'configuration') {
    const { api_url: _url, publishable_key: _key, ...identity } = config
    return identity
  }
  if (request.configuration_digest !== config.configuration_digest) throw new Error('Source configuration changed; restart the local source adapter.')
  const metadata = await discover(config, request.tokens.fabric)
  if (request.action === 'discover') return metadata
  if (!['read', 'probe'].includes(request.action) || typeof request.equipment_id !== 'string'
    || !request.equipment_id.trim() || request.equipment_id.length > 200 || request.cluster !== metadata.cluster) {
    throw new Error('Invalid local source request or changed KQL endpoint.')
  }
  const results = await Promise.allSettled([
    readTelemetry(config, metadata, request.equipment_id, request.tokens),
    readWork(config, request.equipment_id, request.tokens.fabric),
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
  return { ...telemetry, source: metadata.source, equipment_id: request.equipment_id,
    open_work_numbers: work.map(item => item.workOrderNumber) }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.stdout.write(JSON.stringify({ ok: true, result: await main() }))
  } catch (error) {
    // Do not serialize SDK exceptions, request headers, tokens or response bodies.
    const message = error instanceof Error && error.constructor === Error ? error.message : 'Local source adapter failed; inspect source/authentication prerequisites.'
    process.stdout.write(JSON.stringify({ ok: false, error: message }))
    process.exitCode = 1
  }
}
