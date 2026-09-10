import assert from 'node:assert/strict'
import test from 'node:test'
import { applyFilter, buildTelemetryQuery, escapeKqlString, kustoRowsToObjects, projectColumns, validateKql } from '../src/services/copilot/query.ts'
import { applyChunk, applyResponsesEvent, createStreamState, readResponsesStream, splitSseEvents } from '../src/services/copilot/chatStream.ts'
import { appendCompletedTurn, buildResponsesInput, buildResponsesRequest } from '../src/services/copilot/responsesProtocol.ts'
import { defaultCopilotSettings, DEFAULT_SYSTEM_PROMPT, mergeCopilotSettings } from '../src/services/copilot/settings.ts'
import { extractSuggestions, stripOptionsMarker } from '../src/services/copilot/suggestions.ts'

test('rejects KQL control commands and cross-cluster access', () => {
  assert.throws(() => validateKql('.drop table OPCUAEvents'), /control commands/)
  assert.throws(() => validateKql('OPCUAEvents | join cluster("other").database("db").T on x'), /cross-cluster/)
  assert.throws(() => validateKql('OPCUAEvents | take 1; OPCUAEvents | take 2'), /multiple statements/)
  assert.throws(() => validateKql('let x = 1 | OPCUAEvents | take 1'), /let statements/)
  assert.throws(() => validateKql('externaldata(x: string) [@"https://evil.example/x"]'), /externaldata/)
})

test('allows the semicolon inside an OPC UA node id literal', () => {
  const query = "OPCUAEvents | where opcua_node_id == 'ns=2;s=T004.power_output' | top 100 by event_time desc"
  assert.match(validateKql(query), /ns=2;s=T004\.power_output/)
  assert.throws(() => validateKql(`${query}; OPCUAEvents | take 1`), /multiple statements/)
  assert.throws(() => validateKql("OPCUAEvents | where opcua_node_id == 'ns=2;s=T004"), /unterminated string/)
})

test('telemetry returns the most recent rows, raw when aggregation is none', () => {
  const raw = buildTelemetryQuery({ opcua_node_ids: ['ns=2;s=T004.power_output'], lookback: '7d', aggregation: 'none', limit: 100 })
  assert.match(raw, /\| project event_time, opcua_node_id, value, quality/)
  assert.match(raw, /\| top 100 by event_time desc/)
  assert.doesNotMatch(raw, /summarize/)
  const binned = buildTelemetryQuery({ bin: '1m', limit: 10_000 })
  assert.match(binned, /summarize value = avg\(value\)/)
  assert.match(binned, /\| top 500 by event_time desc/)
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
  const rows = [{ id: 'a', status: 'Open', criticality: 5 }, { id: 'b', status: 'closed', criticality: 1 }, { id: 'c', status: 'Open', criticality: 3 }]
  assert.deepEqual(applyFilter(rows, [{ column: 'status', op: 'eq', value: 'open' }]).map(row => row.id), ['a', 'c'])
  assert.deepEqual(applyFilter(rows, [{ column: 'criticality', op: 'gte', value: 3 }]).map(row => row.id), ['a', 'c'])
  assert.deepEqual(applyFilter(rows, [{ column: 'id', op: 'in', value: ['b'] }]).map(row => row.id), ['b'])
  assert.deepEqual(applyFilter(rows, [{ column: 'missing', op: 'eq', value: 'x' }]), [])
})

test('projection drops columns outside the requested set', () => {
  assert.deepEqual(projectColumns([{ id: '1', secretOid: 'x', title: 'T' }], ['id', 'title']), [{ id: '1', title: 'T' }])
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

test('folds Responses API text, function calls and usage into the agent state', () => {
  const state = createStreamState()
  applyResponsesEvent(state, { type: 'response.output_text.delta', delta: 'Station ' })
  applyResponsesEvent(state, { type: 'response.output_text.delta', delta: 'A' })
  applyResponsesEvent(state, { type: 'response.output_item.added', output_index: 0, item: { type: 'function_call', call_id: 'call_1', name: 'query_assets', arguments: '' } })
  applyResponsesEvent(state, { type: 'response.function_call_arguments.delta', output_index: 0, delta: '{"entity":' })
  applyResponsesEvent(state, { type: 'response.function_call_arguments.delta', output_index: 0, delta: '"facilities"}' })
  applyResponsesEvent(state, { type: 'response.output_item.done', output_index: 0, item: { type: 'function_call', call_id: 'call_1', name: 'query_assets', arguments: '{"entity":"facilities"}' } })
  applyResponsesEvent(state, { type: 'response.completed', response: { usage: { input_tokens: 20, output_tokens: 8, total_tokens: 28 } } })

  assert.equal(state.content, 'Station A')
  assert.deepEqual(state.toolCalls, [{ id: 'call_1', name: 'query_assets', arguments: '{"entity":"facilities"}' }])
  assert.deepEqual(state.usage, { prompt: 20, completion: 8, total: 28 })
})

test('preserves conversation history and in-turn tool context for Responses', () => {
  const input = buildResponsesInput([
    { role: 'system', content: 'Use only governed data.' },
    { role: 'user', content: 'Which station is highest?' },
    { role: 'assistant', content: 'Station A.' },
    { role: 'user', content: 'Chart its power.' },
    { role: 'assistant', content: null, tool_calls: [{ id: 'call_1', type: 'function', function: { name: 'query_telemetry', arguments: '{"opcua_node_ids":["A.power"]}' } }] },
    { role: 'tool', tool_call_id: 'call_1', content: '[{"mw":42}]' },
  ])

  assert.deepEqual(input, [
    { role: 'system', content: 'Use only governed data.' },
    { role: 'user', content: 'Which station is highest?' },
    { role: 'assistant', content: 'Station A.' },
    { role: 'user', content: 'Chart its power.' },
    { type: 'function_call', call_id: 'call_1', name: 'query_telemetry', arguments: '{"opcua_node_ids":["A.power"]}' },
    { type: 'function_call_output', call_id: 'call_1', output: '[{"mw":42}]' },
  ])
})

test('builds a streaming Responses request for the configured deployment', () => {
  const request = buildResponsesRequest('gpt-5-mini', [{ role: 'user', content: 'Status?' }], [{
    function: { name: 'query_assets', description: 'Read assets', parameters: { type: 'object' } },
  }])

  assert.deepEqual(request, {
    model: 'gpt-5-mini',
    input: [{ role: 'user', content: 'Status?' }],
    tools: [{ type: 'function', name: 'query_assets', description: 'Read assets', parameters: { type: 'object' } }],
    tool_choice: 'auto',
    stream: true,
  })
  assert.equal('temperature' in request, false)
})

test('keeps only the last eight completed user and assistant messages', () => {
  let history = []
  for (let index = 1; index <= 5; index++) {
    history = appendCompletedTurn(history, `question ${index}`, `answer ${index}`)
  }

  assert.equal(history.length, 8)
  assert.deepEqual(history[0], { role: 'user', content: 'question 2' })
  assert.deepEqual(history.at(-1), { role: 'assistant', content: 'answer 5' })
  assert.equal(history.some(message => 'tool_call_id' in message || 'tool_calls' in message), false)
})

test('streams fragmented Responses events progressively and preserves final tool arguments', async () => {
  const encoder = new TextEncoder()
  const payload = [
    'data: {"type":"response.output_text.delta","delta":"Station "}\n\n',
    'data: {"type":"response.output_text.delta","delta":"A"}\n\n',
    'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"call_1","name":"query_assets","arguments":""}}\n\n',
    'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{\\"entity\\":"}\n\n',
    'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"\\"facilities\\"}"}\n\n',
    'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"function_call","call_id":"call_1","name":"query_assets","arguments":"{\\"entity\\":\\"facilities\\"}"}}\n\n',
    'data: {"type":"response.completed","response":{"usage":{"input_tokens":20,"output_tokens":8,"total_tokens":28}}}\n\n',
  ].join('')
  const chunks = [payload.slice(0, 37), payload.slice(37, 211), payload.slice(211)]
  const body = new ReadableStream({
    start(controller) {
      chunks.forEach(chunk => controller.enqueue(encoder.encode(chunk)))
      controller.close()
    },
  })
  const progress = []

  const state = await readResponsesStream(body, text => progress.push(text))

  assert.deepEqual(progress, ['Station ', 'Station A'])
  assert.equal(state.content, 'Station A')
  assert.deepEqual(state.toolCalls, [{ id: 'call_1', name: 'query_assets', arguments: '{"entity":"facilities"}' }])
  assert.deepEqual(state.usage, { prompt: 20, completion: 8, total: 28 })
})

test('surfaces a streamed Responses failure', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"type":"response.failed","response":{"error":{"message":"quota exceeded"}}}\n\n'))
      controller.close()
    },
  })

  await assert.rejects(() => readResponsesStream(body), /quota exceeded/)
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
  const merged = mergeCopilotSettings({ tools: { run_kql: false }, promptExtra: 'be terse' })
  assert.equal(merged.tools.run_kql, false)
  assert.equal(merged.tools.query_assets, true)
  assert.equal(merged.promptExtra, 'be terse')
  assert.deepEqual(mergeCopilotSettings(null), defaults)
})

test('prompt tells the model to stop when it has enough data', () => {
  assert.match(DEFAULT_SYSTEM_PROMPT, /As soon as the returned data answers the question, stop calling tools/)
  assert.doesNotMatch(DEFAULT_SYSTEM_PROMPT, /files\.- When/)
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
