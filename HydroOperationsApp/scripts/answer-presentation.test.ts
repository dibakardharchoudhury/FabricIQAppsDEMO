import assert from 'node:assert/strict'
import test from 'node:test'
import { readAnswerDatasets, datasetVisualizations } from '../src/services/copilot/answerPresentation.ts'
import { agentDefinition, buildAgentInput, parseDelegation } from '../src/services/copilot/agentDefinitions.ts'
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
    await assert.rejects(readResponsesStream(responseStream([{ type, response: { error: { message: 'Runtime failed' } } }])), /Runtime failed/)
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
  assert.ok(charts.every(chart => chart.chartType === 'line' && chart.yColumns.length === 1))
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
