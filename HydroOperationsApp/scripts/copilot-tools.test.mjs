import assert from 'node:assert/strict'
import test from 'node:test'
import { applyFilter, buildTelemetryQuery, escapeKqlString, kustoRowsToObjects, projectColumns, validateKql } from '../src/services/copilot/query.ts'
import { applyChunk, createStreamState, splitSseEvents } from '../src/services/copilot/chatStream.ts'
import { defaultCopilotSettings, mergeCopilotSettings } from '../src/services/copilot/settings.ts'
import { extractSuggestions, stripOptionsMarker } from '../src/services/copilot/suggestions.ts'

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

test('honours a narrowed source allow-list from Administration', () => {
  assert.throws(() => validateKql('OPCUAEvents | take 5', ['AssetMaster']), /must start with one of AssetMaster/)
  assert.throws(() => validateKql('OPCUAEvents | take 5', []), /no Kusto sources are enabled/)
  assert.doesNotThrow(() => validateKql('AssetMaster() | take 5', ['AssetMaster']))
})

test('settings default everything on and preserve stored opt-outs', () => {
  const defaults = defaultCopilotSettings()
  assert.equal(defaults.tools.run_kql, true)
  assert.equal(defaults.entities.work_orders, true)
  assert.equal(defaults.kustoSources.OPCUAEvents, true)

  // A key added after the settings were stored must default to enabled, not undefined.
  const merged = mergeCopilotSettings({ tools: { run_kql: false }, promptExtra: 'be terse' })
  assert.equal(merged.tools.run_kql, false)
  assert.equal(merged.tools.query_assets, true)
  assert.equal(merged.promptExtra, 'be terse')
  assert.deepEqual(mergeCopilotSettings(null), defaults)
})

test('prefers the declared options marker over the prose heuristic', () => {
  const answer = 'Here are the results.\n\nNext steps:\n- Guessed from prose\n\n<!--options: ["Show open work orders", "Chart power for T009"]-->'
  assert.deepEqual(extractSuggestions(answer), ['Show open work orders', 'Chart power for T009'])
  assert.equal(stripOptionsMarker(answer).includes('options:'), false)
})

test('falls back to the prose list when no marker is present', () => {
  const answer = 'No model found.\n\nNext steps (pick one):\n- Pull recent telemetry for power_output\n- **Search** for work orders on T009\n\nAnything else?'
  assert.deepEqual(extractSuggestions(answer), ['Pull recent telemetry for power_output', 'Search for work orders on T009'])
})

test('ignores data lists that no cue introduced, and malformed markers', () => {
  assert.deepEqual(extractSuggestions('Results:\n\n- 12\n- 14'), [])
  assert.deepEqual(extractSuggestions('Done.\n<!--options: not json-->'), [])
})

test('caps options at five', () => {
  const many = JSON.stringify(['one two', 'three four', 'five six', 'seven eight', 'nine ten', 'eleven twelve'])
  assert.equal(extractSuggestions(`Done.\n<!--options: ${many}-->`).length, 5)
})

test('splits SSE events and keeps the incomplete tail', () => {
  const { events, rest } = splitSseEvents('data: {"a":1}\n\ndata: [DONE]\n\ndata: {"b"')
  assert.deepEqual(events, ['{"a":1}'])
  assert.equal(rest, 'data: {"b"')
})

