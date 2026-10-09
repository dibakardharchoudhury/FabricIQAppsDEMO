import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parseRayfinYaml } from '@microsoft/rayfin-tools-common/_internal/config'
import { chatIntent, collectCatalogRows, collectOperationRows, collectWorkOrders, configuration, discover, parseKustoPayload, readAssetEntity, readFleetSnapshot, readStationPower, readTelemetry, readTelemetryQuery, requestJson, runtimeConfiguration, sourceToolContracts, stageWorkOrder, submitApprovedWorkOrder } from './local-fabric-sources.mjs'
import { fleetSnapshotQuery, shapeFleetSnapshot, shapeCatalogRows, TOOL_DEFINITIONS } from '../src/services/copilot/query.ts'
import { ASSET_ENTITIES, OPERATIONS_ENTITIES } from '../src/services/copilot/catalog.ts'

const id = '11111111-1111-1111-1111-111111111111'
const config = { workspace_id: id, ontology_id: id, database_id: id, eventhouse_id: id, appbackend_id: id,
  api_url: `https://${id.replaceAll('-', '')}.pbidedicated.windows.net/webapi/capacities/${id}/workloads/baas/baasservice/automatic/v1/workspaces/${id}/appbackends/${id}`,
  configuration_digest: 'a'.repeat(64) }
const metadata = { cluster: 'https://test.z.kusto.fabric.microsoft.com', database: 'test' }
const tokens = { graphql: 'test-graphql', kusto: 'test-kusto' }
const node = 'ns=2;s=T005.turbine_temp'
const table = (columns, rows) => ({ Columns: columns.map(ColumnName => ({ ColumnName })), Rows: rows })
const telemetry = () => table(['opcua_node_id', 'event_time', 'value', 'quality'],
  [[node, new Date(Date.now() - 60000).toISOString(), 75, 'BAD']])
const stid = () => ({ data: {
  equipment: { hasNextPage: false, items: [{ equipment_id: 'T005' }] },
  instruments: { hasNextPage: false, items: [{ equipment_id: 'T005', opcua_node_id: node, unit: 'degC' }] },
} })
const response = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
const discoveryFetch = (generation = 2, cluster = metadata.cluster) => async url => response(
  url.includes('/ontologies/') ? { id, properties: { generation } }
    : url.includes('/kqlDatabases/') ? { id, displayName: 'test', properties: { parentEventhouseItemId: id, queryServiceUri: cluster } }
      : { id, capacityId: id })

test('backend intent reuses canonical chart, native and human-priority semantics without source access', () => {
  assert.deepEqual(chatIntent('Which turbines are running bad right now?'),
    { charts_requested: false, native_sources: [], proposal_priority: 'Medium' })
  assert.equal(chatIntent('Show a chart of power.').charts_requested, true)
  assert.equal(chatIntent('Do not show a chart. Return the rows.').charts_requested, false)
  assert.equal(chatIntent('Draft a high-priority work order.').proposal_priority, 'High')
  assert.throws(() => chatIntent('Draft a high-priority work order and draft a low-priority work order.'), /conflicting/)
  assert.throws(() => chatIntent(' '), /nonempty/)
})
test('Rayfin enables delegated external exchange without replacing existing Fabric SSO', async () => {
  const yaml = await readFile(new URL('../rayfin/rayfin.yml', import.meta.url), 'utf8')
  const parsed = parseRayfinYaml(yaml)
  assert.equal(parsed.services.auth.fabric.enabled, true)
  assert.equal(parsed.services.auth.fabric.externalEntraExchange, true)
})

test('Rayfin operations use the Power BI delegated token required by direct exchange', async () => {
  const source = await readFile(new URL('./local-fabric-sources.mjs', import.meta.url), 'utf8')
  assert.match(source, /operationalRead\(config, tokens\.graphql, 'work-order snapshot'/)
  assert.match(source, /readOperationEntity\(config, args\.entity, args, request\.tokens\.graphql\)/)
  assert.match(source, /signInWithEntraToken\(client\.auth, \{ entraToken: tokens\.graphql \}\)/)
  assert.doesNotMatch(source, /signInWithEntraToken\(client\.auth, \{ entraToken: tokens\.fabric \}\)/)
})

test('backend drafts freshly verify target and complete work without a SQL write or model priority escalation', async () => {
  const data = fleetData()
  const args = { equipment_id: 'T2', instrument_id: 'I2', title: 'Inspect signal',
    description: 'Operator-requested signal inspection.', priority: 'Critical' }
  const metadataFetch = async (_url, options) => {
    const query = JSON.parse(options.body).query
    return response({ data: { records: { hasNextPage: false,
      items: query.includes('silver_equipments') ? data.equipment : data.instruments } } })
  }
  const existing = { rows: [{ id: 'W1', equipmentId: 'T2', workOrderNumber: 'WO1',
    title: 'Other work', status: 'Draft' }], read_completed_at: new Date().toISOString() }
  const result = await stageWorkOrder(config, args, 'Medium', tokens, metadataFetch, async () => existing)
  assert.equal(result.proposal.priority, 'Medium')
  assert.equal(result.proposal.opcuaNodeId, data.instruments[1].opcua_node_id)
  assert.deepEqual(result.existing_work, existing.rows)
  assert.equal(result.confirmation_required, true)
  assert.equal(result.production_write_executed, false)
  for (const target of [
    { ...args, equipment_id: 'not-in-workspace' },
    { ...args, instrument_id: 'I1' },
    { ...args, opcua_node_id: data.instruments[0].opcua_node_id },
  ]) await assert.rejects(stageWorkOrder(config, target, 'Medium', tokens, metadataFetch, async () => existing))
  await assert.rejects(stageWorkOrder(config, args, 'Medium', tokens, metadataFetch,
    async () => { throw new Error('EXCHANGE_NOT_ENABLED') }), /EXCHANGE_NOT_ENABLED/)
  await assert.rejects(stageWorkOrder(config, args, 'Medium', tokens, metadataFetch,
    async () => ({ rows: [], read_completed_at: 'not-a-time' })), /complete work-source read/)
})

test('approved SQL submissions pin identity, check duplicates and reconcile lost acknowledgements without another write', async () => {
  const data = fleetData()
  const metadataFetch = async (_url, options) => response({ data: { records: { hasNextPage: false,
    items: JSON.parse(options.body).query.includes('silver_equipments') ? data.equipment : data.instruments } } })
  const draft = { equipment_id: 'T2', instrument_id: 'I2', opcua_node_id: data.instruments[1].opcua_node_id }
  const edits = { title: 'Inspect signal', description: 'Operator-reviewed inspection.', priority: 'High',
    equipmentId: 'must-not-override-target' }
  const creationId = '22222222-2222-2222-2222-222222222222'
  function sql({ loseAck = false, failWrite = false, open = [], corrupt = false } = {}) {
    let saved, writes = 0
    const client = { data: { WorkOrder: {
      select() {
        return { where() { return this }, first() { return this }, orderBy() { return this },
          async execute() { return saved ? [{ ...saved, ...(corrupt ? { createdByOid: creationId } : {}) }] : [] },
          async executePaginated() { return { items: open, hasNextPage: false } } }
      },
      async create(value) {
        writes++
        if (failWrite) throw new Error('Simulated lost write response')
        saved = { ...value, instrumentId: value.instrumentId ?? null, opcuaNodeId: value.opcuaNodeId ?? null }
        if (loseAck) throw new Error('Response lost after commit')
      },
    } } }
    return { client, writes: () => writes }
  }
  const invoke = (fixture, allowCreate) => submitApprovedWorkOrder(config, draft, edits, id, creationId,
    tokens, allowCreate, metadataFetch, async () => fixture.client)
  const normal = sql()
  const first = await invoke(normal, true)
  assert.equal(first.record.equipmentId, 'T2')
  assert.equal(first.record.createdByOid, id)
  assert.equal(first.record.id, creationId)
  assert.equal(first.record.title, edits.title)
  assert.equal(first.reconciled, false)
  assert.equal((await invoke(normal, false)).reconciled, true)
  assert.equal((await invoke(normal, true)).reconciled, true)
  assert.equal(normal.writes(), 1)
  const lost = sql({ loseAck: true })
  assert.equal((await invoke(lost, true)).reconciled, true)
  assert.equal(lost.writes(), 1)
  const failed = sql({ failWrite: true })
  await assert.rejects(invoke(failed, true), /could not be confirmed/)
  await assert.rejects(invoke(failed, false), /will not repeat a SQL write/)
  assert.equal(failed.writes(), 1)
  const duplicate = sql({ open: [{ id: 'W1', workOrderNumber: 'WO1', equipmentId: 'T2',
    title: ' INSPECT SIGNAL ', status: 'Draft' }] })
  await assert.rejects(invoke(duplicate, true), /already exists/)
  assert.equal(duplicate.writes(), 0)
  const corrupt = sql({ corrupt: true })
  await assert.rejects(invoke(corrupt, true), /readback does not match/)
  assert.equal(corrupt.writes(), 1)
})

test('backend station power reuses metadata conversions and weighted samples without automatic charts', async () => {
  const latest = new Date(Date.now() - 1000).toISOString()
  const payload = rows => ({ Tables: [table(
    ['Station', 'Unit', 'average', 'samples', 'invalid_values', 'bad_samples', 'latest_event_time'], rows)] })
  const rows = [
    ['North', 'kW', 2000, 2, 0, 1, latest],
    ['North', 'MW', 4, 1, 0, 0, latest],
    ['South', 'W', 1000000, 1, 0, 0, latest],
  ]
  let query
  const result = await readStationPower(metadata, { lookback: '6h' }, 'fixture-token', async (_url, options) => {
    query = JSON.parse(options.body).csl
    return response(payload(rows))
  })
  assert.match(query, /event_time > ago\(6h\)/)
  assert.match(query, /AssetMaster\(\)/)
  assert.deepEqual(result.rows, [
    { Station: 'North', average_power_MW: 8 / 3, samples: 3, bad_samples: 1, latest_event_time: latest },
    { Station: 'South', average_power_MW: 1, samples: 1, bad_samples: 0, latest_event_time: latest },
  ])
  assert.equal(result.row_count, 2)
  assert.equal(result.truncated, false)
  assert.match(result.semantics, /not total station output/)
  assert.equal('chart' in result, false)
  assert.equal('grounded_summary' in result, false)
  for (const badRows of [
    [['North', '', 1, 1, 0, 0, latest]],
    [['North', 'MW', 1, 1, 1, 0, latest]],
    [[null, 'MW', 1, 1, 0, 0, latest]],
    [['North', 'MW', 1, Number.MAX_SAFE_INTEGER, 0, 0, latest], ['North', 'MW', 1, 1, 0, 0, latest]],
    [['North', 'GW', Number.MAX_VALUE, 1, 0, 0, latest]],
    Array.from({ length: 500 }, () => rows[0]),
  ]) await assert.rejects(readStationPower(metadata, {}, 'fixture-token', async () => response(payload(badRows))))
  await assert.rejects(readStationPower(metadata, { lookback: '6h; drop' }, 'fixture-token',
    async () => { assert.fail('Invalid windows must fail before source I/O') }), /Invalid lookback/)
  const empty = await readStationPower(metadata, {}, 'fixture-token', async () => response(payload([])))
  assert.deepEqual(empty.rows, [])
  assert.equal(empty.lookback, '24h')
})

test('private row identities stay aligned after filter, projection and truncation without changing browser output', () => {
  const entity = OPERATIONS_ENTITIES.find(value => value.key === 'work_orders')
  const rows = [
    { id: 'W1', equipmentId: 'T1', status: 'Completed', opcuaNodeId: 'T1.temp' },
    { id: 'W2', equipmentId: 'T2', status: 'Draft', opcuaNodeId: 'T2.temp' },
    { id: 'W3', equipmentId: 'T3', status: 'Draft', opcuaNodeId: null },
  ]
  const args = { columns: ['status'], where: [{ column: 'status', op: 'eq', value: 'Draft' }], limit: 1 }
  const browser = shapeCatalogRows(entity, rows, args)
  assert.equal('rowIdentities' in browser, false)
  const backend = shapeCatalogRows(entity, rows, args, false, true)
  assert.deepEqual(backend.result, browser.result)
  assert.deepEqual(backend.result.rows, [{ status: 'Draft' }])
  assert.deepEqual(backend.rowIdentities, { '/rows/0': { equipmentId: 'T2', opcuaNodeId: 'T2.temp' } })
  assert.equal('rowIdentities' in backend.result, false)
})

test('explicit runtime configuration is canonical, identity-bound and has no deployment-file dependency', async () => {
  const input = { tenant_id: id, workspace_id: id, ontology_id: id, eventhouse_id: id,
    database_id: id, graphql_id: id, appbackend_id: id, api_url: config.api_url, publishable_key: 'pk-test' }
  const parsed = runtimeConfiguration(input)
  const reversed = Object.fromEntries(Object.entries(input).reverse())
  assert.equal(runtimeConfiguration(reversed).configuration_digest, parsed.configuration_digest)
  const previous = process.env.HYDRO_FABRIC_SOURCE_CONFIG
  try {
    process.env.HYDRO_FABRIC_SOURCE_CONFIG = JSON.stringify(input)
    assert.deepEqual(await configuration(), parsed)
    process.env.HYDRO_FABRIC_SOURCE_CONFIG = '{'
    await assert.rejects(configuration(), /not valid JSON/)
    process.env.HYDRO_FABRIC_SOURCE_CONFIG = ''
    await assert.rejects(configuration(), /not valid JSON/)
  } finally {
    if (previous === undefined) delete process.env.HYDRO_FABRIC_SOURCE_CONFIG
    else process.env.HYDRO_FABRIC_SOURCE_CONFIG = previous
  }
  for (const invalid of [
    { ...input, tenant_id: 'not-a-guid' },
    { ...input, token: 'must-not-be-accepted' },
    { ...input, publishable_key: 'pk-' },
    { ...input, api_url: 'https://untrusted.test' },
    { ...input, api_url: config.api_url.replace('https:', 'http:') },
    { ...input, api_url: config.api_url + '?secret=untrusted' },
  ]) assert.throws(() => runtimeConfiguration(invalid), /runtime/)
  const stale = runtimeConfiguration({ ...input, api_url: config.api_url.replaceAll(id, '22222222-2222-2222-2222-222222222222') })
  await assert.rejects(discover(stale, 'fixture-token', discoveryFetch()), /live workspace capacity/)
})

function fleetData() {
  const readStartedAt = new Date().toISOString()
  const equipment = Array.from({ length: 7 }, (_, i) => ({
    equipment_id: `T${i + 1}`, tag: `Turbine ${i + 1}`, equipment_type_code: 'TURBINE', is_active: true,
  }))
  const instruments = equipment.map((asset, i) => ({
    equipment_id: asset.equipment_id, instrument_id: `I${i + 1}`,
    opcua_node_id: `ns=2;s=${asset.equipment_id}.turbine_temp`, unit: 'degC', is_active: true,
  }))
  const telemetryRows = instruments.slice(0, 6).map((instrument, i) => ({
    opcua_node_id: instrument.opcua_node_id, value: [40, 90, 80, 70, 60, 50][i],
    quality: i === 1 ? 'BAD' : i === 2 ? 'UNCERTAIN' : 'GOOD',
    event_time: new Date(Date.parse(readStartedAt) - 1000).toISOString(),
  }))
  const statuses = ['Draft', 'Approved', 'In Progress', 'Blocked', 'Awaiting parts', 'Completed', ' cancelled ']
  const workOrders = statuses.map((status, i) => ({
    id: `W${i}`, workOrderNumber: `WO${i}`, equipmentId: 'T2', status, title: `Work ${i}`, priority: 'High',
    instrumentId: i === 0 ? 'I2' : null, opcuaNodeId: i === 1 ? instruments[1].opcua_node_id : null,
  }))
  workOrders.push({ id: 'other', workOrderNumber: 'OTHER', equipmentId: 'T1', status: 'Open',
    opcuaNodeId: instruments[1].opcua_node_id })
  return { equipment, instruments, telemetryRows, workOrders, inventoryComplete: true,
    workInventoryComplete: true, readStartedAt, readCompletedAt: readStartedAt }
}

test('shared fleet hot/BAD semantics retain exact raw values, identities and all open work relations', () => {
  const data = fleetData()
  const hot = shapeFleetSnapshot(true, {}, data).result
  assert.deepEqual(hot.rows.map(row => [row.equipment_id, row.value, row.quality, row.unit]), [
    ['T2', 90, 'BAD', 'degC'], ['T3', 80, 'UNCERTAIN', 'degC'], ['T4', 70, 'GOOD', 'degC'],
    ['T5', 60, 'GOOD', 'degC'], ['T6', 50, 'GOOD', 'degC'],
  ])
  assert.equal(hot.requested_limit, 5)
  assert.equal(hot.population.expected_signal_count, 7)
  assert.deepEqual(hot.population.signals_without_readings, [data.instruments[6].opcua_node_id])
  assert.deepEqual(hot.population.work_coverage_equipment_ids, data.equipment.map(row => row.equipment_id))
  const work = hot.rows[0].open_work_orders
  assert.deepEqual(work.map(row => row.workOrderNumber), ['WO0', 'WO1', 'WO2', 'WO3', 'WO4'])
  assert.deepEqual(work.map(row => row.relation), ['same-signal', 'same-signal', 'equipment-level', 'equipment-level', 'equipment-level'])
  assert.equal(hot.rows[0].instrument_id, 'I2')
  assert.equal(hot.rows[0].opcua_node_id, data.instruments[1].opcua_node_id)
  const bad = shapeFleetSnapshot(false, {}, data).result
  assert.deepEqual(bad.rows, [hot.rows[0]])
  assert.equal(bad.latest_per_signal_then_quality_filter, true)
  assert.equal(bad.quality_filter, 'BAD')
  assert.equal(bad.latest_quality_node_count, 1)
  assert.deepEqual(shapeFleetSnapshot(true, { threshold: 60 }, data).result.rows.map(row => row.value), [90, 80, 70])
  assert.deepEqual(shapeFleetSnapshot(true, { threshold: 60, threshold_operator: 'gte' }, data).result.rows.map(row => row.value), [90, 80, 70, 60])
  assert.equal(shapeFleetSnapshot(true, { threshold: 0 }, data).result.rows.length, 6)
  assert.deepEqual(shapeFleetSnapshot(true, { equipment_ids: ['T1', 'T7'] }, data).result.requested_equipment_without_readings, ['T7'])
  const browser = shapeFleetSnapshot(true, {}, { ...data, workInventoryComplete: undefined }).result
  assert.deepEqual(browser.rows, hot.rows)
  assert.equal(browser.population.work_inventory_complete, false)
  assert.deepEqual(browser.population.work_coverage_equipment_ids, [])
  assert.match(browser.provenance.work_orders, /not attested/)
})

test('snapshot queries select latest before quality filtering and temperature ranking, never averages or quality exclusions', () => {
  const bad = fleetSnapshotQuery(false, {})
  const hot = fleetSnapshotQuery(true, {})
  assert.match(bad, /summarize arg_max\(event_time, value, quality\) by opcua_node_id/)
  assert.match(hot, /summarize arg_max\(event_time, value, quality\) by opcua_node_id/)
  assert.match(hot, /endswith '\.turbine_temp'/)
  assert.doesNotMatch(bad, /where.*quality|avg\(|take/)
  assert.doesNotMatch(hot, /where.*quality|avg\(|take/)
  for (const args of [{ equipment_ids: [] }, { equipment_ids: ['T1', 'T1'] }, { threshold: '60' },
    { threshold: Infinity }, { limit: 0 }, { threshold_operator: 'lt' }]) {
    assert.throws(() => fleetSnapshotQuery(true, args))
  }
  assert.throws(() => fleetSnapshotQuery(false, { quality: 'mechanically-bad' }))
})

test('snapshots explicitly reject unsafe raw data, ambiguous metadata/units and invalid work instead of empty defaults', () => {
  const patches = [
    data => { data.inventoryComplete = false },
    data => { data.instruments.push({ ...data.instruments[0], instrument_id: 'another' }) },
    data => { data.equipment.push({ ...data.equipment[0] }) },
    data => { data.instruments[0].equipment_id = 'missing' },
    data => { data.equipment[0].equipment_type_code = null },
    data => { data.instruments[0].unit = '' },
    data => { data.instruments[6].unit = 'degF' }, // Missing-reading metadata still determines comparable population units.
    data => { data.telemetryRows.push({ ...data.telemetryRows[0] }) },
    data => { data.telemetryRows[0].value = '40' },
    data => { data.telemetryRows[0].value = NaN },
    data => { data.telemetryRows[0].quality = 'unverified' },
    data => { data.telemetryRows[0].event_time = '2026-02-30T12:00:00Z' },
    data => { data.telemetryRows[0].event_time = new Date(Date.now() + 60000).toISOString() },
    data => { data.telemetryRows[0].event_time = new Date(Date.now() - 3600000).toISOString() },
    data => { data.telemetryRows[0].event_time = 12345 },
    data => { data.workOrders[0].status = null },
    data => { data.workOrders.push({ ...data.workOrders[0] }) },
    data => { data.workOrders[0].instrumentId = 7 },
    data => { data.telemetryRows = Array.from({ length: 500 }, () => data.telemetryRows[0]) },
  ]
  for (const patch of patches) {
    const data = fleetData()
    patch(data)
    assert.throws(() => shapeFleetSnapshot(true, {}, data))
  }
  assert.throws(() => shapeFleetSnapshot(true, { equipment_ids: ['unresolved'] }, fleetData()))
  const mixed = fleetData()
  mixed.instruments[2].unit = 'bar'
  mixed.telemetryRows[2].quality = 'BAD'
  assert.deepEqual(shapeFleetSnapshot(false, {}, mixed).result.rows.map(row => row.unit), ['degC', 'bar'])
})

test('complete population includes only active mapped signals while preserving explicit unresolved telemetry', () => {
  const data = fleetData()
  data.equipment[0].is_active = false
  data.instruments[1].is_active = false
  data.telemetryRows.push({ ...data.telemetryRows[0], opcua_node_id: 'ns=2;s=unknown.turbine_temp' })
  const result = shapeFleetSnapshot(true, {}, data).result
  assert.equal(result.population.expected_signal_count, 5)
  assert.deepEqual(result.rows.map(row => row.equipment_id), ['T3', 'T4', 'T5', 'T6'])
  assert.deepEqual(result.unresolved_nodes, [data.instruments[0].opcua_node_id, data.instruments[1].opcua_node_id, 'ns=2;s=unknown.turbine_temp'])
  assert.equal(result.truncated, false)
})

test('backend fleet reader completes metadata pagination, uses authoritative Kusto, and never substitutes empty SQL work', async () => {
  const data = fleetData()
  let workReads = 0
  const fetcher = async (url, options) => {
    const body = JSON.parse(options.body)
    if (url.includes('/graphql')) {
      const equipment = body.query.includes('silver_equipments')
      const rows = equipment ? data.equipment : data.instruments
      const next = !body.variables.cursor
      return response({ data: { records: { items: next ? rows.slice(0, 2) : rows.slice(2),
        hasNextPage: next, endCursor: next ? 'next' : null } } })
    }
    assert.match(body.csl, /arg_max\(event_time, value, quality\)/)
    assert.doesNotMatch(body.csl, /avg\(|where.*quality/)
    // Older BAD and hotter rows would change quality membership and averages, but arg_max returns only latest raw cells.
    const history = [
      ...data.telemetryRows.map(row => ({ ...row, value: 999, quality: 'BAD',
        event_time: new Date(Date.parse(row.event_time) - 60000).toISOString() })),
      ...data.telemetryRows,
    ]
    const latest = new Map()
    for (const row of history) {
      if (!latest.has(row.opcua_node_id) || latest.get(row.opcua_node_id).event_time < row.event_time) latest.set(row.opcua_node_id, row)
    }
    return response({ Tables: [table(['opcua_node_id', 'event_time', 'value', 'quality'],
      [...latest.values()].map(row => [row.opcua_node_id, row.event_time, row.value, row.quality]))] })
  }
  const workReader = async () => { workReads++; return data.workOrders }
  const hot = await readFleetSnapshot(config, metadata, true, {}, tokens, fetcher, workReader)
  const bad = await readFleetSnapshot(config, metadata, false, {}, tokens, fetcher, workReader)
  assert.deepEqual(hot.rows.map(row => row.value), [90, 80, 70, 60, 50])
  assert.deepEqual(bad.rows.map(row => row.equipment_id), ['T2'])
  assert.equal(workReads, 2)
  assert.equal(hot.population.work_inventory_complete, true)
  assert.equal(hot.population.expected_signal_count, 7)
  assert.match(hot.provenance.work_orders, /complete paginated/)
  await assert.rejects(readFleetSnapshot(config, metadata, true, {}, tokens, fetcher,
    async () => { throw new Error('EXCHANGE_NOT_ENABLED') }), /EXCHANGE_NOT_ENABLED/)
  await assert.rejects(readFleetSnapshot(config, metadata, true, {}, tokens,
    async () => response({ errors: [{ message: 'metadata failed' }] }), workReader), /failed|envelope/)
  await assert.rejects(readFleetSnapshot(config, metadata, true, {}, tokens,
    async (url, options) => url.includes('/graphql') ? fetcher(url, options)
      : response({ Tables: [], HasErrors: true }), workReader), /error|partial/)
})

test('backend contracts reuse the canonical tool schemas without browser initialization or mutation', () => {
  const contracts = sourceToolContracts()
  assert.deepEqual(contracts.tools.map(tool => tool.name), TOOL_DEFINITIONS.map(tool => tool.function.name))
  for (const tool of contracts.tools) {
    const canonical = TOOL_DEFINITIONS.find(definition => definition.function.name === tool.name)
    assert.equal(tool.parameters.type, canonical.function.parameters.type)
    assert.equal(tool.parameters.additionalProperties, false)
    assert.deepEqual(Object.keys(tool.parameters.properties), Object.keys(canonical.function.parameters.properties))
    assert.deepEqual(tool.parameters.required, canonical.function.parameters.required)
  }
  assert.equal(contracts.tools.find(tool => tool.name === 'query_operations').parameters.allOf.length, OPERATIONS_ENTITIES.length)
  assert.equal(contracts.tools.find(tool => tool.name === 'query_assets').parameters.properties.limit.maximum, 500)
  const work = contracts.tools.find(tool => tool.name === 'propose_work_order')
  assert.equal(work.parameters.properties.title.maxLength, 200)
  work.parameters.properties.title.maxLength = 1
  contracts.context.operations_entities[0].columns.length = 0
  assert.equal(sourceToolContracts().tools.find(tool => tool.name === 'propose_work_order').parameters.properties.title.maxLength, 200)
  assert.ok(sourceToolContracts().context.operations_entities[0].columns.length > 0)
})

test('shared row shaping preserves typed cells and filters while excluding undeclared source fields', () => {
  const entity = OPERATIONS_ENTITIES.find(candidate => candidate.key === 'work_orders')
  const rows = [
    { id: '1', equipmentId: 'T1', status: 'Approved', priority: 'High', privateUserId: 'do-not-expose' },
    { id: '2', equipmentId: 'T2', status: 'Completed', priority: 'Low', privateUserId: 'do-not-expose' },
  ]
  const result = shapeCatalogRows(entity, rows, {
    where: [{ column: 'status', op: 'neq', value: 'Completed' }],
    columns: ['id', 'status'], limit: 1,
  })
  assert.deepEqual(result.result.rows, [{ id: '1', status: 'Approved' }])
  assert.equal(result.result.total_matched, 1)
  assert.equal(result.result.truncated, false)
  assert.equal(shapeCatalogRows(entity, rows, {}, true).result.total_matched, null)
  assert.equal(shapeCatalogRows(entity, rows, {}, true).result.truncated, true)
  assert.ok(shapeCatalogRows(entity, rows, {}).result.rows.every(row => !Object.hasOwn(row, 'privateUserId')))
})

test('asset catalog reads every page before filtering and preserves exact column identity', async () => {
  let reads = 0
  const result = await readAssetEntity(config, 'equipment', {
    where: [{ column: 'equipment_id', op: 'eq', value: 'T2' }],
    columns: ['equipment_id', 'criticality'],
  }, 'test-graphql', async (url, options) => {
    assert.ok(url.endsWith('/graphql'))
    const body = JSON.parse(options.body)
    assert.match(body.query, /records: silver_equipments/)
    assert.match(body.query, /hasNextPage endCursor/)
    reads++
    if (reads === 1) {
      assert.equal(body.variables.cursor, null)
      return response({ data: { records: { hasNextPage: true, endCursor: 'next',
        items: [{ equipment_id: 'T1', criticality: 2, privateUserId: 'not-public' }] } } })
    }
    assert.equal(body.variables.cursor, 'next')
    return response({ data: { records: { hasNextPage: false,
      items: [{ equipment_id: 'T2', criticality: 4, privateUserId: 'not-public' }] } } })
  })
  assert.equal(reads, 2)
  assert.deepEqual(result.rows, [{ equipment_id: 'T2', criticality: 4 }])
  assert.equal(typeof result.rows[0].criticality, 'number')
  assert.equal(result.total_matched, 1)
  assert.equal(result.truncated, false)
})

test('catalog input and upstream errors fail before returning source-shaped defaults', async () => {
  for (const [entity, args] of [
    ['not-a-table', {}], ['equipment', { columns: ['privateUserId'] }],
    ['equipment', { columns: ['equipment_id', 'equipment_id'] }],
    ['equipment', { limit: 0 }], ['equipment', { limit: 501 }],
    ['equipment', { where: [{ column: 'privateUserId', op: 'eq', value: 'secret' }] }],
  ]) {
    await assert.rejects(readAssetEntity(config, entity, args, 'test', async () => {
      assert.fail('Invalid catalog arguments must be rejected before source access.')
    }))
  }
  await assert.rejects(readAssetEntity(config, 'equipment', {}, 'test', async () =>
    response({ errors: [{ message: 'upstream error' }], data: { records: { items: [] } } })), /query failed/)
})

test('complete catalog reads reject duplicate identity, malformed pagination and over-bound populations', async () => {
  const entity = ASSET_ENTITIES.find(candidate => candidate.key === 'equipment')
  for (const page of [
    { items: [{ equipment_id: 'T1' }, { equipment_id: 'T1' }], hasNextPage: false },
    { items: [{}], hasNextPage: false },
    { items: [], hasNextPage: true },
    { items: [], hasNextPage: 'false' },
  ]) {
    await assert.rejects(collectCatalogRows(entity, async () => page), /invalid|duplicate/)
  }
  let reads = 0
  await assert.rejects(collectCatalogRows(entity, async () => ({
    items: [{ equipment_id: `T${++reads}` }], hasNextPage: true, endCursor: `cursor-${reads}`,
  })), /complete-read bound/)
  assert.equal(reads, 5)
  await assert.rejects(collectCatalogRows(entity, async () => ({
    items: [], hasNextPage: true, endCursor: 'repeated',
  })), /invalid continuation/)
  const instruments = ASSET_ENTITIES.find(candidate => candidate.key === 'instruments')
  const versions = [
    { instrument_id: 'I1', opcua_node_id: 'same-node', is_active: false },
    { instrument_id: 'I2', opcua_node_id: 'same-node', is_active: true },
  ]
  assert.deepEqual(await collectCatalogRows(instruments, async () => ({
    items: versions, hasNextPage: false,
  })), versions)
})

test('operational catalog executes read-only SDK pages with canonical fields and shared filtering', async () => {
  let reads = 0, cursor
  const columns = OPERATIONS_ENTITIES.find(entity => entity.key === 'work_orders').columns.map(column => column.name)
  const query = {
    select(selected) { assert.deepEqual(selected, columns); return this },
    orderBy(order) { assert.deepEqual(order, { id: 'asc' }); return this },
    first(count) { assert.equal(count, 100); return this },
    after(value) { cursor = value; return this },
    async executePaginated() {
      reads++
      if (reads === 1) return { items: [{ id: '1', status: 'Completed', equipmentId: 'T1' }],
        hasNextPage: true, endCursor: 'second' }
      assert.equal(cursor, 'second')
      return { items: [{ id: '2', status: 'Approved', equipmentId: 'T2', privateUserId: 'not-public' }],
        hasNextPage: false }
    },
  }
  const result = await collectOperationRows({ data: { WorkOrder: query } }, 'work_orders', {
    where: [{ column: 'status', op: 'neq', value: 'Completed' }],
    columns: ['id', 'equipmentId', 'status'],
  })
  assert.equal(reads, 2)
  assert.deepEqual(result.rows, [{ id: '2', equipmentId: 'T2', status: 'Approved' }])
  assert.equal(result.total_matched, 1)
  assert.equal(result.truncated, false)
})

test('live discovery verifies generation, ownership and current capacity', async () => {
  assert.equal((await discover(config, 'test', discoveryFetch())).source.generation, 2)
  for (const generation of [1, '2', true, null]) {
    await assert.rejects(discover(config, 'test', discoveryFetch(generation)), /generation 2/)
  }
  await assert.rejects(discover({ ...config, api_url: config.api_url.replace('appbackends', 'other') }, 'test', discoveryFetch()), /capacity/)
  await assert.rejects(discover(config, 'test', discoveryFetch(2, 'https://example.com')), /trusted/)
  await assert.rejects(discover(config, 'test', discoveryFetch(2, `${metadata.cluster}?redirect=1`)), /trusted/)
})

test('transport rejects non-success status and does not follow redirects', async () => {
  await assert.rejects(requestJson('https://api.fabric.microsoft.com/test', 'secret', undefined, async (_url, options) => {
    assert.equal(options.redirect, 'error')
    assert.ok(options.signal instanceof AbortSignal)
    return new Response('private upstream details', { status: 403 })
  }), error => error.message.includes('403') && !error.message.includes('private'))
})

const extendedPayload = () => ({ Tables: [
  telemetry(), table(['Value'], ['{}'].map(value => [value])),
  table(['Severity', 'StatusCode'], [[4, 0], [6, 0]]),
  table(['Ordinal', 'Kind', 'Name'], [
    [0, 'QueryResult', 'PrimaryResult'], [1, 'QueryProperties', '@ExtendedProperties'], [2, 'QueryStatus', 'QueryStatus'],
  ]),
] })

test('Kusto uses authoritative table-of-contents and successful completion', () => {
  const payload = extendedPayload()
  assert.deepEqual(parseKustoPayload(payload), payload.Tables[0])
  assert.deepEqual(parseKustoPayload({ Tables: [payload.Tables[0]] }), payload.Tables[0])
  const reordered = extendedPayload()
  ;[reordered.Tables[0], reordered.Tables[1]] = [reordered.Tables[1], reordered.Tables[0]]
  reordered.Tables[3].Rows[0][0] = 1
  reordered.Tables[3].Rows[1][0] = 0
  assert.deepEqual(parseKustoPayload(reordered), reordered.Tables[1])
})

test('Kusto rejects partial errors, warning status, duplicate/missing ordinals and malformed rows', () => {
  for (const alter of [
    payload => { payload.Exceptions = ['partial failure'] },
    payload => { payload.Tables[2].Rows[0] = [2, -1] },
    payload => { payload.Tables[2].Rows[0] = [3, 0] },
    payload => { payload.Tables[2].Rows = [[6, 0]] },
    payload => { payload.Tables[3].Rows[1][0] = 0 },
    payload => { payload.Tables[3].Rows.pop() },
    payload => { payload.Tables[3].Rows[0][1] = 'Unknown' },
    payload => { payload.Tables[0].Rows[0].pop() },
  ]) {
    const payload = extendedPayload()
    alter(payload)
    assert.throws(() => parseKustoPayload(payload), /KQL/)
  }
})

test('generic telemetry reads preserve source columns and reject ambiguous or over-bound results', async () => {
  const payload = extendedPayload()
  const result = await readTelemetryQuery(metadata, 'OPCUAEvents | take 1', 'test-kusto', async (url, options) => {
    assert.equal(url, `${metadata.cluster}/v1/rest/query`)
    const body = JSON.parse(options.body)
    assert.equal(body.db, metadata.database)
    assert.equal(body.csl, 'OPCUAEvents | take 1')
    assert.equal(body.properties.Options.truncationmaxrecords, 501)
    return response(payload)
  })
  assert.deepEqual(result.rows, [{
    opcua_node_id: node, event_time: payload.Tables[0].Rows[0][1], value: 75, quality: 'BAD',
  }])
  assert.equal(result.truncated, false)
  for (const primary of [
    table(['value', 'value'], [[1, 2]]),
    table(['value'], Array.from({ length: 501 }, () => [1])),
  ]) {
    await assert.rejects(readTelemetryQuery(metadata, 'OPCUAEvents', 'test', async () =>
      response({ Tables: [primary] })), /ambiguous|source row bound/)
  }
})

test('source read binds STID nodes, keeps raw BAD samples and uses shared latest query', async () => {
  const result = await readTelemetry(config, metadata, 'T005', tokens, async (url, options) => {
    const body = JSON.parse(options.body)
    if (url.endsWith('/graphql')) {
      assert.equal(body.variables.id, 'T005')
      return response(stid())
    }
    assert.match(body.csl, /arg_max\(event_time, value, quality\)/)
    assert.ok(body.csl.includes(node))
    assert.equal(body.db, 'test')
    assert.equal(body.properties.Options.truncationmaxrecords, 101)
    return response(extendedPayload())
  })
  assert.equal(result.observations[0].quality, 'BAD')
  assert.equal(result.observations[0].unit, 'degC')
  assert.deepEqual(result.missing_sources, [])
})

test('stale and missing measurements remain explicit instead of healthy defaults', async () => {
  const assets = stid()
  assets.data.instruments.items.push({ equipment_id: 'T005', opcua_node_id: 'other', unit: 'rpm' })
  const measurement = telemetry()
  measurement.Rows[0][1] = new Date(Date.now() - 3600000).toISOString()
  const result = await readTelemetry(config, metadata, 'T005', tokens,
    async url => response(url.endsWith('/graphql') ? assets : { Tables: [measurement] }))
  assert.deepEqual(result.missing_sources, ['measurements_for_some_mapped_signals', 'fresh_telemetry'])
})

test('ambiguous metadata and incomplete GraphQL reads cannot reach KQL', async () => {
  for (const alter of [
    assets => { assets.errors = [{ message: 'failed' }] },
    assets => { assets.data.instruments.hasNextPage = true },
    assets => { assets.data.instruments.items[0].unit = null },
    assets => { assets.data.instruments.items[0].equipment_id = 'another' },
    assets => { assets.data.instruments.items.push(assets.data.instruments.items[0]) },
    assets => { assets.data.equipment.items = [] },
  ]) {
    const assets = stid()
    alter(assets)
    let calls = 0
    await assert.rejects(readTelemetry(config, metadata, 'T005', tokens, async () => { calls++; return response(assets) }))
    assert.equal(calls, 1)
  }
})

test('telemetry rejects empty, unknown, duplicate, non-numeric and future data', async () => {
  for (const alter of [
    data => { data.Rows = [] },
    data => { data.Rows[0][0] = 'unmapped-node' },
    data => { data.Rows.push(data.Rows[0]) },
    data => { data.Rows[0][2] = null },
    data => { data.Rows[0][3] = 'NO_STATUS' },
    data => { data.Rows[0][1] = new Date(Date.now() + 60000).toISOString() },
  ]) {
    const data = telemetry()
    alter(data)
    await assert.rejects(readTelemetry(config, metadata, 'T005', tokens,
      async url => response(url.endsWith('/graphql') ? stid() : { Tables: [data] })))
  }
})

function workClient(pages) {
  const requested = []
  let index = 0
  const query = {
    where(filter) { assert.deepEqual(filter, { equipmentId: { eq: 'T005' } }); return this },
    orderBy() { return this },
    first(size) { assert.equal(size, 100); return this },
    after(cursor) { requested.push(cursor); return this },
    async executePaginated() { return pages[index++] },
  }
  return { data: { WorkOrder: { select() { return query } } }, requested }
}
const order = (id, status = 'Draft') => ({ id, equipmentId: 'T005', workOrderNumber: `WO-${id}`, status })

test('work reads every page, preserve all open statuses and exclude completed/cancelled', async () => {
  const client = workClient([
    { items: [order('1'), order('2', 'Completed')], hasNextPage: true, endCursor: 'next' },
    { items: [order('3', 'Cancelled'), order('4', 'Approved')], hasNextPage: false },
  ])
  assert.deepEqual((await collectWorkOrders(client, 'T005')).rows.map(item => item.id), ['1', '4'])
  assert.deepEqual(client.requested, ['next'])
})

test('work coverage keeps its own completion clock while other sources are still running', async t => {
  const clock = Date.parse('2026-10-08T16:00:00Z')
  t.mock.timers.enable({ apis: ['Date'], now: clock })
  const work = await collectWorkOrders(workClient([{ items: [], hasNextPage: false }]), 'T005')
  t.mock.timers.tick(120_000)
  assert.equal(work.read_completed_at, '2026-10-08T16:00:00.000Z')
  assert.equal(Date.now() - Date.parse(work.read_completed_at), 120_000)
  assert.deepEqual(work.rows, [])
})

test('work rejects duplicate identities, missing/repeated cursors and over-bound reads', async () => {
  for (const pages of [
    [{ items: [order('1'), order('1')], hasNextPage: false }],
    [{ items: [order('1', '')], hasNextPage: false }],
    [{ items: [{ ...order('1'), equipmentId: 'other' }], hasNextPage: false }],
    [{ items: [], hasNextPage: true }],
    [{ items: Array.from({ length: 101 }, (_, index) => order(String(index))), hasNextPage: false }],
    [{ items: [], hasNextPage: true, endCursor: 'same' }, { items: [], hasNextPage: true, endCursor: 'same' }],
    Array.from({ length: 5 }, (_, index) => ({ items: [order(String(index))], hasNextPage: true, endCursor: String(index) })),
  ]) await assert.rejects(collectWorkOrders(workClient(pages), 'T005'))
})
