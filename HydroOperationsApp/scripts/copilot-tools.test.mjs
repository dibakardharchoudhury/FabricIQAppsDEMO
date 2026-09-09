import assert from 'node:assert/strict'
import test from 'node:test'
import { applyFilter, buildTelemetryQuery, escapeKqlString, kustoRowsToObjects, projectColumns, validateKql } from '../src/services/copilot/query.ts'
import { applyChunk, createStreamState, splitSseEvents } from '../src/services/copilot/chatStream.ts'

test('rejects KQL control commands and cross-cluster access', () => {
  assert.throws(() => validateKql('.drop table OPCUAEvents'), /control commands/)
  assert.throws(() => validateKql('OPCUAEvents | join cluster("other").database("db").T on x'), /cross-cluster/)
  assert.throws(() => validateKql('OPCUAEvents | take 1; OPCUAEvents | take 2'), /multiple statements/)
  assert.throws(() => validateKql('externaldata(x: string) [@"https://evil.example/x"]'), /externaldata/)
})

test('rejects tables outside the catalog', () => {
  assert.throws(() => validateKql('SecretTable | take 10'), /must start with/)
  assert.doesNotThrow(() => validateKql('AssetMaster() | take 10'))
  assert.doesNotThrow(() => validateKql('TelemetryEnriched(ago(1h), now(), dynamic(null), dynamic(null))'))
})

test('caps result size unless the query already ends with take', () => {
  assert.match(validateKql('OPCUAEvents | where value > 1'), /\| take 500$/)
  assert.equal(validateKql('OPCUAEvents | take 5'), 'OPCUAEvents | take 5')
})

test('escapes quotes so a node id cannot break out of the query', () => {
  const query = buildTelemetryQuery({ opcua_node_ids: ["ns=2;s=T001'"] })
  assert.match(query, /'ns=2;s=T001\\''/)
  assert.equal(escapeKqlString("a'b\\c"), "a\\'b\\\\c")
})

test('rejects malformed telemetry arguments instead of interpolating them', () => {
  assert.throws(() => buildTelemetryQuery({ lookback: '1h | drop' }), /Invalid lookback/)
  assert.throws(() => buildTelemetryQuery({ bin: 'evil' }), /Invalid bin/)
  assert.throws(() => buildTelemetryQuery({ aggregation: 'exfiltrate' }), /Invalid aggregation/)
})

test('filters rows with the structured predicate', () => {
  const rows = [
    { id: 'a', status: 'Open', criticality: 5 },
    { id: 'b', status: 'closed', criticality: 1 },
    { id: 'c', status: 'Open', criticality: 3 },
  ]
  assert.deepEqual(applyFilter(rows, [{ column: 'status', op: 'eq', value: 'open' }]).map(row => row.id), ['a', 'c'])
  assert.deepEqual(applyFilter(rows, [{ column: 'criticality', op: 'gte', value: 3 }]).map(row => row.id), ['a', 'c'])
  assert.deepEqual(applyFilter(rows, [{ column: 'id', op: 'in', value: ['b'] }]).map(row => row.id), ['b'])
  assert.deepEqual(applyFilter(rows, [{ column: 'missing', op: 'eq', value: 'x' }]), [])
})

test('projection drops columns outside the requested set', () => {
  const projected = projectColumns([{ id: '1', secretOid: 'x', title: 'T' }], ['id', 'title'])
  assert.deepEqual(projected, [{ id: '1', title: 'T' }])
})

test('folds Kusto column metadata into objects', () => {
  assert.deepEqual(kustoRowsToObjects(['a', 'b'], [[1, 2]]), [{ a: 1, b: 2 }])
})

test('merges streamed tool call fragments by index', () => {
  const state = createStreamState()
  applyChunk(state, { choices: [{ delta: { tool_calls: [{ index: 0, id: 'call_1', function: { name: 'query_', arguments: '{"ent' } }] } }] })
  applyChunk(state, { choices: [{ delta: { tool_calls: [{ index: 0, function: { name: 'assets', arguments: 'ity":"facilities"}' } }] } }] })
  applyChunk(state, { choices: [{ delta: { content: 'Hello' } }] })
  applyChunk(state, { choices: [{ finish_reason: 'tool_calls' }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } })

  assert.deepEqual(state.toolCalls, [{ id: 'call_1', name: 'query_assets', arguments: '{"entity":"facilities"}' }])
  assert.equal(state.content, 'Hello')
  assert.equal(state.finishReason, 'tool_calls')
  assert.equal(state.usage?.total, 15)
})

test('splits SSE events and keeps the incomplete tail', () => {
  const { events, rest } = splitSseEvents('data: {"a":1}\n\ndata: [DONE]\n\ndata: {"b"')
  assert.deepEqual(events, ['{"a":1}'])
  assert.equal(rest, 'data: {"b"')
})
