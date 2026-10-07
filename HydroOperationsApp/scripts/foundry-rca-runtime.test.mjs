import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { after, test } from 'node:test'

const harness = { reads: [], requests: [], responses: [], options: undefined }
globalThis.__rcaRuntimeTest = harness
const stubs = {
  '../fabric.ts': 'export const foundryAgentToken = async () => "test-token"; export const verifyDataAgentForFoundry = async () => {}; export const verifyOntologyForFoundry = async () => {};',
  './settings.ts': 'export const loadCopilotSettings = () => ({projectEndpoint:"https://test.services.ai.azure.com/api/projects/test"}); export const renderCoordinatorPrompt = () => ""; export const renderSystemPrompt = () => "";',
  './catalog.ts': 'export const catalogPrompt = () => "";',
  './workOrderApproval.ts': `import { createApprovalStore } from ${JSON.stringify(new URL('../src/services/copilot/approvalStore.ts', import.meta.url).href)}; export const workOrderApprovals = createApprovalStore();`,
  './tools.ts': `export const buildToolDefinitions = () => [];
    export const describeToolCall = name => name;
    export const createToolRuntime = (_settings, options) => {
      globalThis.__rcaRuntimeTest.options = options;
      return async () => {
        const value = globalThis.__rcaRuntimeTest.reads.shift();
        if (value instanceof Error) throw value;
        if (!value) throw new Error("Unexpected source read");
        return value;
      };
    };`,
}
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (context.parentURL?.endsWith('/copilot/foundry.ts') && Object.hasOwn(stubs, specifier)) {
      return { url: `data:text/javascript,${encodeURIComponent(stubs[specifier])}`, shortCircuit: true }
    }
    return next(specifier, context)
  },
})
const { askFoundryCopilot, resetFoundryConversation } = await import('../src/services/copilot/foundry.ts')
const originalFetch = globalThis.fetch
globalThis.fetch = async (_url, init) => {
  const request = JSON.parse(init.body)
  harness.requests.push(request)
  const next = harness.responses.shift()
  assert.ok(next, 'No unexpected extra model round')
  assert.equal(request.agent_reference.name, `hydro-${next.role}-agent`)
  const output = next.calls ?? [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: next.text }] }]
  const events = [
    ...(next.calls ?? []).map((item, output_index) => ({ type: 'response.output_item.done', output_index, item })),
    ...(next.text ? [{ type: 'response.output_text.delta', delta: next.text }] : []),
    { type: 'response.completed', response: { id: `response_${harness.requests.length}`, status: 'completed', output } },
  ]
  return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''), { status: 200 })
}
after(() => {
  globalThis.fetch = originalFetch
  hooks.deregister()
  delete globalThis.__rcaRuntimeTest
})
const call = (id, name, args) => ({ type: 'function_call', call_id: id, name, arguments: JSON.stringify(args) })
const delegate = role => ({ role: 'supervisor', calls: [call(`delegate_${role}`, 'delegate_to_agent', { specialist: role, question: 'Read the assigned evidence.' })] })
const read = call('source', 'hydro_query', { tool_name: 'query_station_power', arguments: { lookback: '24h' } })
const ref = { evidence_id: 'source', path: '/rows/0' }
const report = {
  observations: [ref],
  hypotheses: ['sensor_or_ingestion', 'equipment_condition'].map(category => ({
    category, supporting: [ref], contradicting: [], missing: ['fresh_measurements', 'approved_limits'],
  })),
}
function reset() {
  resetFoundryConversation()
  harness.requests.length = harness.responses.length = harness.reads.length = 0
  harness.reads.push({ result: { rows: [{ Station: 'Sloy', average_power_MW: 123.45 }] }, rowCount: 1, groundedSummary: 'Measured mean: 123.45 MW.' })
}

test('RCA rejects prose and invalid reports, finishes immediately on checked completion, and excludes Chief claims', async () => {
  reset()
  harness.responses.push(delegate('rca'),
    { role: 'rca', calls: [read] },
    { role: 'rca', text: 'Normal within an invented 5% threshold.' },
    { role: 'rca', calls: [call('invalid', 'complete_rca_assessment', { ...report, threshold: 5 })] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'Confirmed equipment fault at 5%.' })
  const progress = []
  const result = await askFoundryCopilot('Investigate station power, then draft a notification. Do not send.', text => progress.push(text))
  assert.match(result.text, /Cause undetermined/)
  assert.match(result.text, /123\.45/)
  assert.match(result.text, /not been sent/)
  assert.doesNotMatch(result.text, /5%|Confirmed equipment fault/)
  assert.deepEqual(progress, [])
  assert.equal(harness.responses.length, 0)
  assert.equal(result.orchestrationEvents.find(event => event.role === 'rca').status, 'completed')
  assert.ok(result.orchestrationEvents.flatMap(event => event.trace ?? []).some(entry => entry.label.includes('RCA report rejected')))
})

test('a source failure after a report still fails, clears busy, and records only one failed turn', async () => {
  reset()
  harness.reads.push(new Error('Source unavailable'))
  harness.responses.push(delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report), { ...read, call_id: 'failed_read' }] })
  await assert.rejects(askFoundryCopilot('Investigate station power.'), /Source unavailable/)
  harness.responses.push({ role: 'supervisor', text: 'The prior investigation failed.' })
  await askFoundryCopilot('What happened?')
  const followup = harness.requests.at(-1).input
  assert.equal(followup.filter(item => item.role === 'user' && JSON.stringify(item).includes('Investigate station power.')).length, 1)
})

test('conflicting priority does not leave chat busy and explicit priority reaches the tool runtime', async () => {
  reset()
  await assert.rejects(askFoundryCopilot('Create a Low-priority work order. Priority: High.'), /conflicting priorities/)
  harness.responses.push(delegate('work-order'),
    { role: 'work-order', calls: [call('no_draft', 'complete_work_order_review', { decision: 'no_draft', reason: 'Existing work already covers the signal.' })] },
    { role: 'supervisor', text: 'No duplicate draft.' })
  await askFoundryCopilot('Prepare a Low-priority work-order draft.')
  assert.equal(harness.options.proposalPriority, 'Low')
})

test('native retrieval gets a self-contained assignment, not unrelated operator workflow text', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq'),
    { role: 'fabric-iq', text: 'Native inventory returned.' },
    { role: 'supervisor', text: 'Notification draft not sent.' })
  await askFoundryCopilot('Use the Data Agent for work inventory, then draft a notification for PRIVATE_DOWNSTREAM_CONTEXT.')
  const native = harness.requests.find(request => request.agent_reference.name === 'hydro-fabric-iq-agent')
  assert.match(JSON.stringify(native.input), /complete assigned native-source task/)
  assert.doesNotMatch(JSON.stringify(native.input), /PRIVATE_DOWNSTREAM_CONTEXT/)
  assert.equal(harness.reads.length, 1, 'Native reads never run the direct-source substitute')
})

test('compound RCA retains native claims separately from validated source references', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq'), { role: 'fabric-iq', text: 'Ontology instance: EQUIP_RTI_T005 at Foyers.' },
    delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'An unsupported diagnostic conclusion.' })
  const result = await askFoundryCopilot('Use ontology identity context, then investigate using direct readings.')
  assert.match(result.text, /Native-source retrieval claims/)
  assert.match(result.text, /> Ontology instance: EQUIP_RTI_T005 at Foyers/)
  assert.match(result.text, /not a validated diagnosis/)
  assert.doesNotMatch(result.text, /unsupported diagnostic conclusion/)
})
