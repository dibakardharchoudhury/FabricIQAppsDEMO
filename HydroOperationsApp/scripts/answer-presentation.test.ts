import assert from 'node:assert/strict'
import test from 'node:test'
import { readAnswerDatasets, datasetVisualizations, answerVisualizations, hideRenderedCsv, formatEvidenceCell, OPERATIONAL_EVIDENCE_CONTRACT } from '../src/services/copilot/answerPresentation.ts'
import { relatedSuggestions } from '../src/services/copilot/suggestions.ts'
import { agentDefinition, buildAgentInput, parseDelegation, parseHydroQuery } from '../src/services/copilot/agentDefinitions.ts'
import { createApprovalStore } from '../src/services/copilot/approvalStore.ts'
import { createWorkOrderProposal } from '../src/services/copilot/orchestration.ts'
import { readResponsesStream } from '../src/services/copilot/chatStream.ts'

function responseStream(events: unknown[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      controller.close()
    },
  })
}

test('every Foundry input message has explicit item and content types, including history', () => {
  const input = buildAgentInput('policy', [
    { role: 'user', content: 'previous question' },
    { role: 'assistant', content: 'previous answer' },
  ], 'current question')
  assert.deepEqual(input, [
    { type: 'message', role: 'developer', content: [{ type: 'input_text', text: 'policy' }] },
    { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'previous question' }] },
    { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'previous answer', annotations: [] }] },
    { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'current question' }] },
  ])
  assert.equal(buildAgentInput('policy', [], 'question').length, 2)
})

test('native continuation preserves output items and actual response identity', async () => {
  const output = [{ type: 'reasoning', encrypted_content: 'opaque' }, { type: 'function_call', call_id: 'call1', name: 'hydro_query', arguments: '{}' }]
  const state = await readResponsesStream(responseStream([
    { type: 'response.created', response: { id: 'resp1' } },
    { type: 'response.output_item.done', output_index: 1, item: output[1] },
    { type: 'response.completed', response: { id: 'resp1', output } },
  ]))
  assert.equal(state.responseId, 'resp1')
  assert.equal(state.completed, true)
  assert.deepEqual(state.output, output)
  assert.equal(state.toolCalls[1].id, 'call1')
})

test('failed or incomplete native streams never become successful answers', async () => {
  for (const type of ['response.failed', 'response.incomplete', 'error']) {
    const events: unknown[] = []
    const failure = { type, response: { error: { message: 'Runtime failed' } } }
    await assert.rejects(readResponsesStream(responseStream([failure]), undefined, event => events.push(event)), /Runtime failed/)
    assert.deepEqual(events, [failure])
  }
  assert.notEqual((await readResponsesStream(responseStream([]))).completed, true)
})

test('table and CSV use identical labels and every returned row', () => {
  const table = '| Asset | Open WOs |\n| --- | --- |\n' + Array.from({ length: 25 }, (_, i) => `| T${i} | ${i} |`).join('\n')
  const { datasets, issues } = readAnswerDatasets(table)
  assert.deepEqual(issues, [])
  assert.equal(datasets[0].rows.length, 25)
  const [chart] = datasetVisualizations(datasets[0], 'Show a dashboard')
  assert.equal(chart.xColumn, 'Asset')
  assert.equal(chart.inlineCsvData.split('\n').length, 26)
  assert.deepEqual(chart.yColumns, ['Open WOs'])
})

test('identifiers and missing values never become numeric chart measurements', () => {
  const { datasets } = readAnswerDatasets('| Asset | equipment_id | Temperature |\n| --- | --- | --- |\n| T1 | 123 | 80 |\n| T2 | 456 | |')
  assert.deepEqual(datasetVisualizations(datasets[0], 'Chart'), [])
})

test('malformed data produces an explicit limitation instead of a chart', () => {
  const result = readAnswerDatasets('```csv\nAsset,Value\nT1,2,extra\n```')
  assert.equal(result.datasets.length, 0)
  assert.equal(result.issues.length, 1)
})

test('separate measures produce separate charts rather than mixing units', () => {
  const { datasets } = readAnswerDatasets('```csv\ntimestamp,Temperature C,Speed rpm\n2026-10-07T12:00:00Z,80,1000\n```')
  const charts = datasetVisualizations(datasets[0], 'Dashboard')
  assert.equal(charts.length, 2)
  assert.ok(charts.every(chart => chart.chartType === 'bar' && chart.yColumns.length === 1))
})

test('CSV presentation preserves invalid/raw evidence and rounds display only', () => {
  const valid = '```csv\nAsset,Temperature C\nT1,80.123456789\n```'
  assert.equal(hideRenderedCsv(`Findings\n${valid}\nSources`), 'Findings\n\nSources')
  for (const invalid of ['```csv\nAsset,Value\nT1,2,extra\n```', '```csv\nAsset,Value\nT1,2']) {
    assert.equal(hideRenderedCsv(invalid), invalid)
  }
  assert.equal(formatEvidenceCell('80.123456789', 'Temperature C'), '80.123')
  assert.equal(formatEvidenceCell('0.000000123456', 'Power'), '0.0000001235')
  assert.equal(formatEvidenceCell('001234', 'equipment_id'), '001234')
  assert.equal(formatEvidenceCell('', 'Power'), 'Not supplied')
  assert.equal(readAnswerDatasets(valid).datasets[0].rows[0][1], '80.123456789')
})

test('hourly station power is a grouped trend; latest turbine snapshots are bars', () => {
  const trend = readAnswerDatasets('```csv\ntimestamp (ISO UTC),station,Power\n2026-10-07T01:00:00Z,Foyers,100\n2026-10-07T02:00:00Z,Foyers,101\n2026-10-07T01:00:00Z,Sloy,90\n```').datasets[0]
  const [chart] = datasetVisualizations(trend, 'Chart power per station')
  assert.equal(chart.chartType, 'line')
  assert.equal(chart.xColumn, 'timestamp (ISO UTC)')
  assert.equal(chart.groupBy, 'station')
  const snapshot = readAnswerDatasets('```csv\ntimestamp_utc,turbine,Temperature C\n2026-10-07T01:00:00Z,T1,85\n2026-10-07T01:00:00Z,T2,90\n```').datasets[0]
  const [bar] = datasetVisualizations(snapshot, 'Chart hottest turbines')
  assert.equal(bar.chartType, 'bar')
  assert.equal(bar.xColumn, 'turbine')
  assert.equal(bar.groupBy, undefined)
})

test('related suggestions use real equipment and never imply voice/text approval', () => {
  const suggestions = relatedSuggestions('EQUIP_RTI_T005 temperature is stale.')
  assert.ok(suggestions.every(suggestion => suggestion.includes('EQUIP_RTI_T005')))
  assert.match(suggestions[1], /editable.*human review/)
  const review = relatedSuggestions('Draft offered.', ['EQUIP_RTI_T002'])
  assert.match(review[0], /open work.*EQUIP_RTI_T002/)
  assert.doesNotMatch(review.join('\n'), /confirm|approve|create/i)
  assert.ok(relatedSuggestions('Telemetry result.').some(suggestion => /freshness/.test(suggestion)))
})

test('Supervisor requests a visible routing reason without breaking older agent versions', () => {
  assert.match(JSON.stringify(agentDefinition('supervisor', 'test').tools), /"required":\["specialist","question","reason"\]/)
  assert.equal(parseDelegation('{"specialist":"fabric-iq","question":"Read ontology instances","reason":"Ontology-native instance data"}').reason, 'Ontology-native instance data')
  assert.throws(() => parseDelegation('{"specialist":"qa","question":"Read work","reason":false}'), /reason/)
})

test('explicit chart CSV excludes unrelated table measures without dropping table evidence', () => {
  const { datasets } = readAnswerDatasets(
    '| Turbine | Temperature C | Open WOs |\n| --- | --- | --- |\n| T1 | 80 | 2 |\n| T2 | 70 | 1 |\n'
    + '### Latest temperatures\n```csv\nTurbine,Temperature C\nT1,80\nT2,70\n```')
  assert.equal(datasets.length, 2)
  const charts = answerVisualizations(datasets, 'Show open work and a temperature chart')
  assert.equal(charts.length, 1)
  assert.deepEqual(charts[0].yColumns, ['Temperature C'])
  assert.equal(charts[0].inlineCsvData, 'Turbine,Temperature C\r\nT1,80\r\nT2,70')
})

test('identical table and CSV retain explicit chart intent without duplicate datasets', () => {
  const { datasets } = readAnswerDatasets('| Asset | Count |\n| --- | --- |\n| T1 | 2 |\n```csv\nAsset,Count\nT1,2\n```')
  assert.equal(datasets.length, 1)
  assert.equal(datasets[0].format, 'csv')
  assert.equal(answerVisualizations(datasets, 'chart').length, 1)
})

test('tool arguments are structured objects, with no nested JSON serialization', () => {
  const invalid = parseHydroQuery(String.raw`{"tool_name":"run_kql","arguments":{"query":"OPCUAEvents | where opcua_node_id matches regex '\.turbine_temp$'"}}`)
  assert.equal(invalid.ok, false)
  if (invalid.ok === false) assert.match(invalid.error, /Invalid tool JSON.*escaped backslashes/)
  for (const raw of ['{', '{}', '{"tool_name":"run_kql","arguments":null}',
    '{"tool_name":"run_kql","arguments":[]}', '{"tool_name":"run_kql","arguments":"{}"}',
    '{"tool_name":"run_kql","arguments_json":"{}"}']) assert.equal(parseHydroQuery(raw).ok, false)
  const args = { query: String.raw`OPCUAEvents | where opcua_node_id matches regex '\.turbine_temp$'` }
  const corrected = parseHydroQuery(JSON.stringify({ tool_name: 'run_kql', arguments: args }))
  assert.equal(corrected.ok, true)
  if (corrected.ok) assert.deepEqual(corrected.args, args)
  const tool = agentDefinition('qa', 'test').tools[0]
  assert.match(JSON.stringify(tool), /"arguments":\{"type":"object"/)
  assert.doesNotMatch(JSON.stringify(tool), /arguments_json/)
})

test('persistent agents share source semantics, complete equipment work and real freshness time', () => {
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /not a work-order type or category/)
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /including orders linked to a different signal/)
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /older than 60 seconds as stale/)
  for (const role of ['supervisor', 'qa', 'rca', 'work-order', 'fabric-iq'] as const) {
    assert.ok(agentDefinition(role, 'test', 'data', 'ontology').instructions.includes(OPERATIONAL_EVIDENCE_CONTRACT))
  }
})

test('native specialists do not depend on Fabric; only IQ has the IQ tool', () => {
  for (const role of ['qa', 'rca', 'work-order'] as const) {
    const definition = agentDefinition(role, 'test-model')
    assert.equal(definition.tools[0].type, 'function')
    assert.doesNotMatch(JSON.stringify(definition.tools), /fabric_dataagent|fabric_iq/)
  }
  const iq = agentDefinition('fabric-iq', 'test-model', 'data-agent-iq', 'ontology-iq')
  assert.equal(iq.tools.length, 2)
  assert.equal(iq.tools[0].type, 'fabric_iq_preview')
  assert.match(JSON.stringify(iq.tools[0]), /data-agent-iq/)
  assert.match(JSON.stringify(iq.tools[1]), /ontology-iq/)
  assert.throws(() => agentDefinition('fabric-iq', 'test-model', 'data-agent-only'), /connection/)
  assert.equal(parseDelegation('{"specialist":"fabric-iq","question":"query ontology"}').specialist, 'fabric-iq')
  assert.throws(() => parseDelegation('{"specialist":"arbitrary-agent","question":"query"}'), /Unknown/)
})

const proposal = () => createWorkOrderProposal({ equipmentId: 'E1', title: 'Inspect bearing', description: 'Observed vibration', priority: 'High' })

test('approval is editable, explicit and prevents duplicate concurrent writes', async () => {
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  let writes = 0
  const edits = { title: 'Inspect cooling', description: 'Operator-reviewed observation', priority: 'Medium' as const }
  const first = store.approve(draft.id, edits, async () => {}, async value => { writes++; assert.equal(value.title, edits.title); return 'WO-123' })
  await assert.rejects(store.approve(draft.id, edits, async () => {}, async () => 'duplicate'), /saving|created/)
  assert.equal(await first, 'WO-123')
  assert.equal(writes, 1)
  assert.equal(store.get(draft.id)?.state, 'created')
})

test('rejection, stale drafts and unknown write outcomes cannot mutate again', async () => {
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  store.reject(draft.id)
  await assert.rejects(store.approve(draft.id, draft, async () => {}, async () => 'bad'), /rejected/)
  const uncertain = proposal()
  store.stage(uncertain)
  await assert.rejects(store.approve(uncertain.id, uncertain, async () => {}, async () => { throw new Error('Network lost') }), /could not be confirmed/)
  await assert.rejects(store.approve(uncertain.id, uncertain, async () => {}, async () => 'bad'), /uncertain/)
  const stale = { ...proposal(), createdAt: Date.now() - 31 * 60_000 }
  store.stage(stale)
  await assert.rejects(store.approve(stale.id, stale, async () => {}, async () => 'bad'), /30 minutes/)
})

test('validation failure remains retryable without ever calling the writer', async () => {
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  await assert.rejects(store.approve(draft.id, draft, async () => { throw new Error('Invalid asset') }, async () => { assert.fail('must not write') }), /Invalid asset/)
  assert.equal(store.get(draft.id)?.state, 'pending')
})

test('approval observers receive transitions and stop receiving updates after unsubscribe', async () => {
  const store = createApprovalStore<string>()
  const draft = proposal()
  const states: (string | undefined)[] = []
  const unsubscribe = store.subscribe(() => states.push(store.get(draft.id)?.state))
  store.stage(draft)
  await store.approve(draft.id, draft, async () => {}, async () => 'WO-123')
  assert.deepEqual(states, ['pending', 'saving', 'created'])
  store.clear()
  assert.equal(states.at(-1), undefined)
  unsubscribe()
  store.stage(draft)
  assert.equal(states.length, 4)
})
