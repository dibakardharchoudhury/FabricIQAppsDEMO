import test from 'node:test'
import assert from 'node:assert/strict'
import { collectWorkOrders, discover, parseKustoPayload, readTelemetry, requestJson } from './local-fabric-sources.mjs'

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
  assert.deepEqual((await collectWorkOrders(client, 'T005')).map(item => item.id), ['1', '4'])
  assert.deepEqual(client.requested, ['next'])
})

test('work rejects duplicate identities, missing/repeated cursors and over-bound reads', async () => {
  for (const pages of [
    [{ items: [order('1'), order('1')], hasNextPage: false }],
    [{ items: [order('1', '')], hasNextPage: false }],
    [{ items: [{ ...order('1'), equipmentId: 'other' }], hasNextPage: false }],
    [{ items: [], hasNextPage: true }],
    [{ items: [], hasNextPage: true, endCursor: 'same' }, { items: [], hasNextPage: true, endCursor: 'same' }],
    Array.from({ length: 5 }, (_, index) => ({ items: [order(String(index))], hasNextPage: true, endCursor: String(index) })),
  ]) await assert.rejects(collectWorkOrders(workClient(pages), 'T005'))
})
