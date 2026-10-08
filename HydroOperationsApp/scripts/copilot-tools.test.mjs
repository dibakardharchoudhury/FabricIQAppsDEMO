import assert from 'node:assert/strict'
import test from 'node:test'
import { applyFilter, buildStationPowerQuery, stationPowerEvidence, stationPowerSummary, buildTelemetryQuery, escapeKqlString, kustoRowsToObjects, projectColumns, validateKql } from '../src/services/copilot/query.ts'

test('inspection date filters compare complete timestamps, not their shared year prefix', () => {
  const rows = [
    { id: 'old', inspectedAt: '2026-07-21T09:00:00.000Z' },
    { id: 'boundary', inspectedAt: '2026-09-08T08:26:40.953Z' },
    { id: 'equivalent-offset', inspectedAt: '2026-09-08T10:26:40.953+02:00' },
    { id: 'missing', inspectedAt: null },
  ]
  assert.deepEqual(applyFilter(rows, [{ column: 'inspectedAt', op: 'gte', value: '2026-09-08T08:26:40.953Z' }]).map(row => row.id), ['boundary', 'equivalent-offset'])
  assert.deepEqual(applyFilter(rows, [{ column: 'inspectedAt', op: 'lt', value: '2026-09-08' }]).map(row => row.id), ['old'])
  assert.deepEqual(applyFilter([{ value: '12.5' }, { value: 2 }], [{ column: 'value', op: 'gte', value: 10 }]), [{ value: '12.5' }])
  assert.throws(() => applyFilter(rows, [{ column: 'inspectedAt', op: 'gte', value: '2026-99-99' }]), /valid ISO/)
})

test('invalid filter envelopes and operators cannot silently return the unfiltered inventory', () => {
  for (const where of [
    { column: 'status', op: 'eq', value: 'Draft' },
    [{ column: 'status', op: 'unsupported', value: 'Draft' }],
    [{ column: 'status', op: 'eq' }],
    [{ column: 'status', op: 'in', value: 'Draft' }],
  ]) assert.throws(() => applyFilter([], where))
})
import { applyChunk, applyResponsesEvent, createStreamState, readResponsesStream, splitSseEvents } from '../src/services/copilot/chatStream.ts'
import { catalogPrompt } from '../src/services/copilot/catalog.ts'
import { appendCompletedTurn, buildResponsesInput, buildResponsesRequest } from '../src/services/copilot/responsesProtocol.ts'
import { defaultCopilotSettings, DEFAULT_SYSTEM_PROMPT, mergeCopilotSettings } from '../src/services/copilot/settings.ts'
import { extractSuggestions, stripOptionsMarker } from '../src/services/copilot/suggestions.ts'

test('station power uses exact power-output nodes and authoritative units, never fuzzy power signals', () => {
  const query = buildStationPowerQuery()
  assert.match(query, /ago\(24h\)/)
  assert.match(query, /endswith_cs '\.power_output'/)
  assert.match(query, /AssetMaster\(\)/)
  assert.doesNotMatch(query, /contains|where.*quality/)
  assert.doesNotThrow(() => validateKql(query))
  assert.throws(() => buildStationPowerQuery('0h'), /Invalid lookback/)
  assert.throws(() => buildStationPowerQuery('24h); drop'), /Invalid lookback/)
})

test('station chart uses sample-weighted MW conversions and preserves exact chart values', () => {
  const base = { Station: 'Site,"A"', average: 1000, samples: 2, invalid_values: 0, bad_samples: 1, latest_event_time: '2026-10-07T20:00:00Z', Unit: 'kW' }
  const evidence = stationPowerEvidence([base, { ...base, Unit: 'MW', average: 3, samples: 6 }], '24h')
  assert.equal(evidence.rows[0].average_power_MW, 2.5)
  assert.equal(evidence.rows[0].samples, 8)
  assert.equal(evidence.rows[0].bad_samples, 2)
  assert.match(evidence.visualization.inlineCsvData, /"Site,""A""",2\.5/)
  assert.equal(evidence.visualization.yAxisTitle, 'MW')
  for (const patch of [{ Unit: 'kWh' }, { Unit: '' }, { Station: '' }, { average: NaN }, { invalid_values: 1 }, { samples: 0 }]) {
    assert.throws(() => stationPowerEvidence([{ ...base, ...patch }], '24h'), /No partial chart/)
  }
  assert.equal(stationPowerEvidence([], '24h').visualization, undefined)
})

test('station answer preserves exact chart values, MW units, source times and freshness', () => {
  const evidence = stationPowerEvidence([{ Station: 'Sloy', Unit: 'MW', average: 1315.0626405438807,
    samples: 4045, bad_samples: 212, invalid_values: 0, latest_event_time: '2026-10-07T20:20:15.102362Z' }], '24h')
  const text = stationPowerSummary(evidence.rows, '24h', '2026-10-07T22:25:08.713Z')
  assert.match(text, /1315\.0626405438807/)
  assert.match(text, /20:20:15\.102362Z/)
  assert.match(text, /Stale \(>60s\)/)
  assert.doesNotMatch(text, /kW|02:20:/)
  assert.ok(evidence.visualization.inlineCsvData.includes(String(evidence.rows[0].average_power_MW)))
  assert.match(stationPowerSummary(evidence.rows, '24h', '2026-10-07T20:20:16Z'), /Within 60s/)
  assert.match(stationPowerSummary(evidence.rows, '24h', '2026-10-07T20:00:00Z'), /Uncertain/)
  assert.match(stationPowerSummary([], '24h', '2026-10-07T22:00:00Z'), /does not establish zero generation/)
  assert.throws(() => stationPowerSummary([], '24h', 'invalid'), /time is invalid/)
})

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

test('today keeps midnight UTC semantics in telemetry and station charts', () => {
  assert.match(buildTelemetryQuery({ lookback: 'today', aggregation: 'none' }), /event_time >= startofday\(now\(\)\)/)
  assert.match(buildStationPowerQuery('today'), /event_time >= startofday\(now\(\)\)/)
  assert.throws(() => buildTelemetryQuery({ lookback: '0h' }), /Invalid lookback/)
})

test('latest telemetry returns raw values per node without binning or quality exclusions', () => {
  const query = buildTelemetryQuery({ opcua_node_ids: ['ns=2;s=T005.power_output', 'ns=2;s=T005.vibration_a'],
    lookback: '6h', aggregation: 'latest', bin: 'unused', limit: 100 })
  assert.match(query, /event_time > ago\(6h\) and event_time <= now\(\)/)
  assert.match(query, /opcua_node_id in \('ns=2;s=T005.power_output', 'ns=2;s=T005.vibration_a'\)/)
  assert.match(query, /summarize arg_max\(event_time, value, quality\) by opcua_node_id/)
  assert.match(query, /\| top 100 by event_time desc/)
  assert.doesNotMatch(query, /avg\(|bin\(|where.*quality/)
  assert.doesNotThrow(() => validateKql(query))
})

test('the reproduced missing enriched node-column filter is rejected before source execution', () => {
  assert.throws(() => validateKql('TelemetryEnriched(ago(6h), now(), dynamic(null), dynamic(null))\n| where opcua_node_id in ("ns=2;s=T005.power_output")'),
    /does not return opcua_node_id.*aggregation latest/)
  assert.doesNotThrow(() => validateKql('TelemetryEnriched(ago(6h), now(), dynamic(null), dynamic(null)) | where Turbine == "T005"'))
  assert.doesNotThrow(() => validateKql('TelemetryEnriched(ago(6h), now(), dynamic(null), dynamic(null)) | where Signal == "opcua_node_id"'))
})

test('rejects tables outside the catalog', () => {
  assert.throws(() => validateKql('SecretTable | take 10'), /must start with/)
  assert.doesNotThrow(() => validateKql('AssetMaster() | take 10'))
  assert.doesNotThrow(() => validateKql('TelemetryEnriched(ago(1h), now(), dynamic(null), dynamic(null))'))
  assert.throws(
    () => validateKql('TelemetryEnriched(start: ago(1h), end: now(), stations: dynamic(null), turbines: dynamic(null))'),
    /arguments are positional.*without parameter names or colons/i,
  )
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

test('advertises the deployed case-sensitive Kusto function columns', () => {
  const prompt = catalogPrompt(defaultCopilotSettings())
  assert.match(prompt, /AssetMaster.*opcua_node_id, Station, Turbine, Signal, SignalGroup, Unit/)
  assert.match(prompt, /TelemetryEnriched.*event_time, Station, Turbine, Signal, SignalGroup, Unit, value, quality/)
  assert.match(prompt, /TelemetryEnriched\(ago\(6h\), now\(\), dynamic\(null\), dynamic\(null\)\).*Never include parameter names or colons/)
  assert.doesNotMatch(prompt, /station, turbine, sensor_group/)
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

test('prompt gives running bad a stable cross-agent meaning', () => {
  assert.match(DEFAULT_SYSTEM_PROMPT, /literal telemetry quality BAD/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /all of its active instruments/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /do not silently narrow.*temperature/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /30-minute lookback/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /query_signal_quality_snapshot exactly once with quality BAD, lookback 30m/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /single latest raw row per node before filtering quality/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /Do not prefilter a signal type, apply a top\/result limit/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /status is neither Completed nor Cancelled/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /same-signal only when opcuaNodeId or instrumentId matches/)
})

test('prompt gives running hot a temperature-only cross-agent meaning', () => {
  assert.match(DEFAULT_SYSTEM_PROMPT, /running hot.*turbine temperature/s)
  assert.match(DEFAULT_SYSTEM_PROMPT, /active turbine_temp instrument/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /30-minute lookback/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /five hottest turbines/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /do not call a value abnormal, overheating, or unsafe/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /same-signal only when opcuaNodeId or instrumentId matches that turbine's temperature signal/)
})

test('prompt uses an adaptive generic response contract', () => {
  assert.match(DEFAULT_SYSTEM_PROMPT, /presentation that fits the evidence/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /Never force every answer into one fixed table or template/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /Distinguish zero results, missing data, stale data, truncation, and source failure/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /Separate facts returned by tools from interpretation/)
  assert.match(DEFAULT_SYSTEM_PROMPT, /preserve the same facts and scope across the Data Agent, Foundry, and Battle panes/)
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
