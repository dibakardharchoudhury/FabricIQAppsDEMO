import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { after, test } from 'node:test'

const harness = { reads: [], requests: [], responses: [], verifiedSources: [], options: undefined }
globalThis.__rcaRuntimeTest = harness
const stubs = {
  '../fabric.ts': 'export const foundryAgentToken = async () => "test-token"; export const verifyDataAgentForFoundry = async () => { globalThis.__rcaRuntimeTest.verifiedSources.push("data-agent"); }; export const verifyOntologyForFoundry = async () => { globalThis.__rcaRuntimeTest.verifiedSources.push("ontology"); }; export const queryStid = async () => { throw new Error("Unexpected STID read"); }; export const runKustoQuery = async () => { throw new Error("Unexpected Kusto read"); };',
  '../rayfin.ts': 'export const isRayfinConfigured = () => true; export const isRayfinSignedIn = () => true; export const listWorkOrders = async () => []; export const listAsset3DModels = listWorkOrders; export const listInspections = listWorkOrders; export const listMaintenanceNotifications = listWorkOrders; export const listSpareParts = listWorkOrders;',
  './settings.ts': 'export const loadCopilotSettings = () => ({projectEndpoint:"https://test.services.ai.azure.com/api/projects/test"}); export const renderCoordinatorPrompt = () => ""; export const renderSystemPrompt = () => "";',
  './catalog.ts': 'export const catalogPrompt = () => "";',
  './workOrderApproval.ts': `import { createApprovalStore } from ${JSON.stringify(new URL('../src/services/copilot/approvalStore.ts', import.meta.url).href)}; export const workOrderApprovals = createApprovalStore(); export const validateWorkOrderTarget = async () => {};`,
  './tools.ts': `export const buildToolDefinitions = () => [];
    export const describeToolCall = name => name;
    export const createToolRuntime = (_settings, options) => {
      globalThis.__rcaRuntimeTest.options = options;
      return async (name, args) => {
        const value = globalThis.__rcaRuntimeTest.reads.shift();
        if (value instanceof Error) throw value;
        if (typeof value === "function") return value(options, name, args);
        if (!value) throw new Error("Unexpected source read");
        return value;
      };
    };`,
}
const hooks = registerHooks({
  resolve(specifier, context, next) {
    const sourceBoundary = context.parentURL?.endsWith('/copilot/tools.ts')
      && ['../fabric.ts', '../rayfin.ts', './workOrderApproval.ts'].includes(specifier)
    if ((context.parentURL?.endsWith('/copilot/foundry.ts') || sourceBoundary) && Object.hasOwn(stubs, specifier)) {
      return { url: `data:text/javascript,${encodeURIComponent(stubs[specifier])}`, shortCircuit: true }
    }
    return next(specifier, context)
  },
})
const { askFoundryCopilot, resetFoundryConversation } = await import('../src/services/copilot/foundry.ts')
const { createToolRuntime } = await import('../src/services/copilot/tools.ts')
const { defaultCopilotSettings } = await import('../src/services/copilot/settings.ts')
const { WorkOrderProposalValidationError } = await import('../src/services/copilot/orchestration.ts')
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
const delegate = (role, source = 'data-agent') => ({ role: 'supervisor', calls: [call(`delegate_${role}`, 'delegate_to_agent', { specialist: role, question: 'Read the assigned evidence.', native_source: role === 'fabric-iq' ? source : null })] })
const nativeReply = (text, source = 'data-agent') => ({ role: 'fabric-iq', text,
  calls: [{ type: 'mcp_call', id: 'native_receipt', server_label: `fabriciq-${source}`, name: source === 'ontology' ? 'ask_ontology' : 'PublishedDataAgent', status: 'completed', output: text }] })
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
  harness.verifiedSources.length = 0
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

test('RCA cannot finish before explicitly requested connected inspection evidence is read', async () => {
  reset()
  harness.reads.push({ result: { rows: [], total_matched: 0, truncated: false }, rowCount: 0 })
  harness.responses.push(delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('premature', 'complete_rca_assessment', report)] },
    { role: 'rca', calls: [call('inspection', 'hydro_query', { tool_name: 'query_operations', arguments: { entity: 'inspections' } })] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'Evidence checked.' })
  const result = await askFoundryCopilot('Investigate using telemetry and inspections.')
  assert.match(result.text, /Cause undetermined/)
  assert.ok(harness.requests.some(request => request.input.some(item => /Requested inspection evidence has not been read/.test(item.output ?? ''))))
  assert.equal(result.steps.filter(step => step.tool === 'query_operations' && step.status === 'done').length, 1)
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

test('reassessing an investigation requires Sleuth even when Chief tries to finish with prose', async () => {
  reset()
  harness.responses.push({ role: 'supervisor', text: 'Confirmed fault based on my own threshold.' },
    delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'Confirmed fault.' })
  const progress = []
  const result = await askFoundryCopilot('Reassess the investigation.', text => progress.push(text))
  assert.match(result.text, /Cause undetermined/)
  assert.doesNotMatch(result.text, /Confirmed fault/)
  assert.deepEqual(progress, [])
  assert.equal(result.orchestrationEvents.find(event => event.role === 'rca').status, 'completed')
})

test('RCA corrections identify envelope and oversized-reference mistakes without accepting them', async () => {
  reset()
  harness.reads[0].result.rows[0].context = 'x'.repeat(2500)
  const withPath = path => ({
    ...report, observations: [{ evidence_id: 'source', path }],
    hypotheses: report.hypotheses.map(hypothesis => ({ ...hypothesis, supporting: [] })),
  })

  harness.responses.push(delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('envelope', 'complete_rca_assessment', withPath('/data/rows'))] },
    { role: 'rca', calls: [call('oversized', 'complete_rca_assessment', withPath('/rows'))] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', withPath('/rows/0/average_power_MW'))] },
    { role: 'supervisor', text: 'Finished.' })
  const result = await askFoundryCopilot('Investigate station power.')
  assert.match(result.text, /Cause undetermined/)
  const feedback = harness.requests.flatMap(request => request.input).filter(item => item.type === 'function_call_output')
  assert.ok(feedback.some(item => /omit the \/data envelope/.test(item.output)))
  assert.ok(feedback.some(item => /source at \/rows.*maximum 2400/.test(item.output)))
  assert.equal(result.orchestrationEvents.find(event => event.role === 'rca').status, 'completed')
})

test('source-checked RCA retains inventory rows that the assessment did not cite', async () => {
  reset()
  harness.reads[0] = { result: { rows: [{ partNumber: 'P-1' }, { partNumber: 'P-2' }] }, rowCount: 2 }
  harness.responses.push(delegate('rca'),
    { role: 'rca', calls: [call('source', 'hydro_query', { tool_name: 'query_operations', arguments: { entity: 'spare_parts' } })] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'Only P-1 matters.' })
  const result = await askFoundryCopilot('Investigate the returned spare parts.')
  assert.match(result.text, /P-2/)
  assert.match(result.text, /All 2 returned rows/)
  assert.doesNotMatch(result.text, /Only P-1 matters/)
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
    nativeReply('Native inventory returned.'),
    { role: 'supervisor', text: 'Notification draft not sent.' })
  await askFoundryCopilot('Use the Data Agent for work inventory, then draft a notification for PRIVATE_DOWNSTREAM_CONTEXT.')
  const native = harness.requests.find(request => request.agent_reference.name === 'hydro-fabric-iq-agent')
  assert.match(JSON.stringify(native.input), /complete assigned native-source task/)
  assert.doesNotMatch(JSON.stringify(native.input), /PRIVATE_DOWNSTREAM_CONTEXT/)
  assert.equal(harness.reads.length, 1, 'Native reads never run the direct-source substitute')
})

test('compound RCA retains native claims separately from validated source references', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq', 'ontology'), nativeReply('Ontology instance: EQUIP_RTI_T005 at Foyers.', 'ontology'),
    delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'An unsupported diagnostic conclusion.' })
  const result = await askFoundryCopilot('Use ontology identity context, then investigate using direct readings.')
  assert.match(result.text, /Native-source retrieval claims/)
  assert.match(result.text, /> Ontology instance: EQUIP_RTI_T005 at Foyers/)
  assert.match(result.text, /not a validated diagnosis/)
  assert.doesNotMatch(result.text, /unsupported diagnostic conclusion/)
})

test('native requests restrict tools and reject a different source or prose-only completion', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq'), nativeReply('Wrong source.', 'ontology'))
  await assert.rejects(askFoundryCopilot('Ask the Data Agent for inventory.'), /Native source mismatch/)
  const request = harness.requests.find(request => request.agent_reference.name === 'hydro-fabric-iq-agent')
  assert.deepEqual(request.tool_choice, { type: 'allowed_tools', mode: 'required', tools: [{ type: 'mcp', server_label: 'fabriciq-data-agent' }] })
  assert.deepEqual(harness.verifiedSources, ['data-agent'])
  reset()
  harness.responses.push(delegate('fabric-iq'), { role: 'fabric-iq', text: 'Invented native answer.' })
  await assert.rejects(askFoundryCopilot('Ask the Data Agent for inventory.'), /without a matching native-source/)
})

test('incorrect native delegation is rejected before a source is invoked', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq', 'ontology'), delegate('fabric-iq'),
    nativeReply('Correct source.'), { role: 'supervisor', text: 'Correct source.' })
  await askFoundryCopilot('Ask the published Fabric Data Agent for work.')
  assert.equal(harness.requests.filter(request => request.agent_reference.name === 'hydro-fabric-iq-agent').length, 1)
  assert.ok(harness.requests[1].input.some(item => /operator requested the Data Agent/.test(item.output ?? '')))
})

test('unrequested native inventory delegation returns to Chief before invoking or verifying a source', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq'), delegate('qa'),
    { role: 'qa', calls: [read] }, { role: 'qa', text: 'Direct inventory evidence.' },
    { role: 'supervisor', text: 'Direct inventory evidence.' })
  await askFoundryCopilot('List spare parts at or below reorder level.')
  assert.deepEqual(harness.verifiedSources, [])
  assert.equal(harness.requests.some(request => request.agent_reference.name === 'hydro-fabric-iq-agent'), false)
  assert.ok(harness.requests[1].input.some(item => /not requested a native/.test(item.output ?? '')))
})

test('a repeated factual-read assignment cannot consume slots reserved for RCA and work review', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq'), nativeReply('Native inventory.'),
    delegate('qa'), { role: 'qa', calls: [read] }, { role: 'qa', text: 'Verified inventory.' },
    delegate('qa'),
    delegate('rca'), { role: 'rca', calls: [call('assessment', 'complete_rca_assessment', report)] },
    delegate('work-order'), { role: 'work-order', calls: [call('decision', 'complete_work_order_review', { decision: 'no_draft', reason: 'No verified uncovered issue.' })] },
    { role: 'supervisor', text: 'Investigation and work review completed.' })
  const result = await askFoundryCopilot('Ask the published Data Agent for open work, verify it, investigate the selected equipment and prepare a work-order draft only if justified.')
  assert.deepEqual(result.orchestrationEvents.map(event => event.role), ['supervisor', 'fabric-iq', 'qa', 'rca', 'work-order'])
  assert.ok(harness.requests.some(request => request.input.some(item => /Reserve the remaining 2 delegations for rca -> work-order/.test(item.output ?? ''))))
  assert.match(result.text, /no_draft/)
  assert.equal(harness.responses.length, 0)
})

test('Chief cannot trap Sleuth in a plan-only assignment that prohibits the required evidence reads', async () => {
  reset()
  harness.responses.push({ role: 'supervisor', calls: [call('plan', 'delegate_to_agent', {
    specialist: 'rca', question: 'Return only the verification plan and prioritized queries; do not run them.', native_source: null,
  })] }, delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('assessment', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'Investigation completed.' })
  const result = await askFoundryCopilot('Investigate the discrepancy using actual source observations.')
  assert.equal(result.orchestrationEvents.filter(event => event.role === 'rca').length, 1)
  assert.ok(harness.requests[1].input.some(item => /not a prose-only query plan/.test(item.output ?? '')))
  assert.match(result.text, /Cause undetermined/)
})

test('Gauge must render an explicitly requested chart rather than offer it after returning the data', async () => {
  reset()
  harness.reads.push((options, name, args) => createToolRuntime(defaultCopilotSettings(), options)(name, args))
  harness.responses.push(delegate('qa'), { role: 'qa', calls: [read] },
    { role: 'qa', text: 'I can produce the chart if you want.' },
    { role: 'qa', calls: [call('chart', 'hydro_query', { tool_name: 'visualize_dataset', arguments: {
      chart_type: 'bar', title: 'Measured station power', x_column: 'Station', y_columns: ['MW'], inline_csv_data: 'Station,MW\nSloy,123.45',
    } })] }, { role: 'qa', text: 'Chart rendered.' }, { role: 'supervisor', text: 'Chart rendered.' })
  const result = await askFoundryCopilot('Show a chart of the returned station measurements.')
  assert.equal(result.visualizations.length, 1)
  assert.equal(result.visualizations[0].inlineCsvData, 'Station,MW\nSloy,123.45')
  assert.ok(harness.requests.some(request => request.input.some(item => JSON.stringify(item).includes('no chart has been rendered'))))
})

test('A chart correction is bounded and cannot force a chart from unrelated source rows', async () => {
  reset()
  harness.responses.push(delegate('qa'), { role: 'qa', calls: [read] },
    { role: 'qa', text: 'No requested downtime data is available.' },
    { role: 'qa', text: 'The station power rows do not establish downtime.' },
    { role: 'supervisor', text: 'Downtime data is unavailable.' })
  const result = await askFoundryCopilot('Show a chart of downtime.')
  assert.equal(result.visualizations.length, 0)
  assert.match(result.text, /Requested chart incomplete/)
  assert.equal(harness.responses.length, 0)
  assert.equal(harness.requests.filter(request => request.agent_reference.name === 'hydro-qa-agent').length, 3)
})

test('Empty chart source results do not trigger extra model rounds or fabricated zero values', async () => {
  reset()
  harness.reads.splice(0, 1, { result: { rows: [] }, rowCount: 0 })
  harness.responses.push(delegate('qa'), { role: 'qa', calls: [read] },
    { role: 'qa', text: 'No measurements were returned.' },
    { role: 'supervisor', text: 'No measurements were returned.' })
  const result = await askFoundryCopilot('Show a chart of station power.')
  assert.equal(result.visualizations.length, 0)
  assert.match(result.text, /Requested chart incomplete/)
  assert.equal(harness.responses.length, 0)
})

test('both native sources can receive the same retrieval assignment without substitution', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq'), nativeReply('Data Agent evidence.'),
    delegate('fabric-iq', 'ontology'), nativeReply('Ontology evidence.', 'ontology'),
    { role: 'supervisor', text: 'Both sources returned evidence.' })
  await askFoundryCopilot('Compare the Data Agent and ontology results.')
  assert.deepEqual(harness.verifiedSources, ['data-agent', 'ontology'])
  assert.deepEqual(harness.requests.filter(request => request.agent_reference.name === 'hydro-fabric-iq-agent')
    .map(request => request.tool_choice.tools[0].server_label), ['fabriciq-data-agent', 'fabriciq-ontology'])
})

test('one native source cannot satisfy a request for both ontology and Data Agent', async () => {
  reset()
  harness.responses.push(delegate('fabric-iq', 'ontology'), nativeReply('Ontology facilities.', 'ontology'),
    { role: 'supervisor', text: 'Both native sources were checked.' },
    delegate('fabric-iq'), nativeReply('Data Agent backlog.'),
    { role: 'supervisor', text: 'Both native results are available.' })
  await askFoundryCopilot('Use the selected ontology to list facilities. Ask the published Fabric Data Agent for open work.')
  assert.deepEqual(harness.verifiedSources, ['ontology', 'data-agent'])
  assert.ok(harness.requests.some(request => request.input.some(item => JSON.stringify(item).includes('Remaining specialists, in order: fabric-iq (data-agent)'))))
  assert.ok(harness.requests.some(request => request.input.some(item => /Verified native execution source: ontology/.test(item.output ?? ''))))
  assert.equal(harness.responses.length, 0)
})

test('oversized proposals are locally repairable without staging or weakening field limits', async () => {
  reset()
  const invalid = { equipment_id: 'T005', title: 'Acceptance', description: 'x'.repeat(4001) }
  const staged = []
  const realTool = createToolRuntime(defaultCopilotSettings(), { onWorkOrderProposal: proposal => staged.push(proposal) })
  await assert.rejects(realTool('propose_work_order', invalid), WorkOrderProposalValidationError)
  await assert.rejects(realTool('propose_work_order', { ...invalid, description: 17 }), /description must be a string/)
  assert.equal(staged.length, 0)
  harness.reads.length = 0
  const executeRealTool = (options, name, args) => createToolRuntime(defaultCopilotSettings(), options)(name, args)
  harness.reads.push(executeRealTool, executeRealTool)
  const propose = (id, args) => call(id, 'hydro_query', { tool_name: 'propose_work_order', arguments: args })
  harness.responses.push(delegate('work-order'), { role: 'work-order', calls: [propose('too_long', invalid)] },
    { role: 'work-order', calls: [propose('repaired', { ...invalid, description: 'Source-grounded summary.' })] }, { role: 'work-order', text: 'Card staged.' },
    { role: 'supervisor', text: 'Review the card.' })
  const progress = []
  const result = await askFoundryCopilot('Prepare a Low-priority work order.', text => progress.push(text))
  assert.equal(result.proposals.length, 1)
  assert.equal(result.proposals[0].description, 'Source-grounded summary.')
  assert.equal(result.proposals[0].priority, 'Low')
  assert.deepEqual(progress, [], 'Draft narrative is not streamed ahead of the editable cards')
  assert.ok(harness.requests.some(request => request.input.some(item => /"staged":false,"sql_writes":0/.test(item.output ?? ''))))
})

test('requested unsent notification is rendered even when Chief asks for unnecessary authorization', async () => {
  reset()
  harness.reads[0] = { result: { rows: [{ equipment_id: 'EQUIP_RTI_T005' }] }, rowCount: 1 }
  harness.reads.push({ result: { rows: [], total_matched: 0, truncated: false }, rowCount: 0 })
  harness.responses.push(delegate('qa'), { role: 'qa', calls: [
    call('identity', 'hydro_query', { tool_name: 'query_assets', arguments: { entity: 'equipment' } }),
    call('work', 'hydro_query', { tool_name: 'query_operations', arguments: { entity: 'work_orders' } }),
  ] }, { role: 'qa', text: 'No matching work.' }, { role: 'supervisor', text: 'Authorize a notification before I can write it.' })
  const progress = []
  const result = await askFoundryCopilot('Verify coverage and draft a short notification. Do not send.', text => progress.push(text))
  assert.match(result.text, /Notification draft - not sent/)
  assert.match(result.text, /EQUIP\\_RTI\\_T005/)
  assert.match(result.text, /zero rows returned/)
  assert.doesNotMatch(result.text, /Authorize/)
  assert.deepEqual(progress, [])
  assert.equal(result.proposals.length, 0)
})

test('nested WO arguments cannot be mislabeled as an operator clarification and recover to a real card', async () => {
  reset()
  harness.reads.length = 0
  harness.reads.push((options, name, args) => createToolRuntime(defaultCopilotSettings(), options)(name, args))
  const fields = { equipment_id: 'EQUIP_RTI_T005', title: 'Acceptance', description: 'Inspect only.', priority: 'Low' }
  harness.responses.push(delegate('work-order'),
    { role: 'work-order', calls: [call('nested', 'hydro_query', { tool_name: 'propose_work_order', arguments: { arguments: fields } })] },
    { role: 'work-order', calls: [call('false_clarification', 'complete_work_order_review', { decision: 'needs_clarification', reason: 'The tool failed, ask the operator to fix it.' })] },
    { role: 'work-order', calls: [call('corrected', 'hydro_query', { tool_name: 'propose_work_order', arguments: fields })] },
    { role: 'work-order', text: 'Card ready.' }, { role: 'supervisor', text: 'Review the card.' })
  const result = await askFoundryCopilot('Prepare a Low-priority work order for T005.')
  assert.equal(result.proposals.length, 1)
  assert.equal(result.proposals[0].equipmentId, fields.equipment_id)
  assert.ok(harness.requests.some(request => request.input.some(item => /not an operator clarification/.test(item.output ?? ''))))
  assert.equal(harness.reads.length, 0, 'Malformed envelopes never reach the source or staging tool')
})

test('independent verification retains both receipts but deduplicates identical charts and source summaries', async () => {
  reset()
  const visualization = { graphicType: 'barchart', title: '24h', inlineCsvData: 'Station,MW\nSloy,123.45' }
  harness.reads[0].visualization = visualization
  harness.reads.push({ ...harness.reads[0], groundedSummary: 'Latest verified mean: 123.45 MW.' })
  harness.responses.push(delegate('rca'), { role: 'rca', calls: [read] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    delegate('qa'), { role: 'qa', calls: [{ ...read, call_id: 'verification' }] },
    { role: 'qa', text: 'Same values in independent verification.' },
    { role: 'supervisor', text: 'Investigation complete.' })
  const result = await askFoundryCopilot('Investigate station power, then independently verify it.')
  assert.equal(result.visualizations.length, 1)
  assert.equal(result.steps.length, 2)
  assert.equal((result.text.match(/### Source-derived station summary/g) ?? []).length, 1)
  assert.match(result.text, /Latest verified mean/)
})

test('different station windows remain distinct even when their values are equal', async () => {
  reset()
  harness.reads.push({ ...harness.reads[0], groundedSummary: 'Seven-day mean: 123.45 MW.' })
  harness.responses.push(delegate('rca'), { role: 'rca', calls: [read,
    call('seven_days', 'hydro_query', { tool_name: 'query_station_power', arguments: { lookback: '7d' } })] },
    { role: 'rca', calls: [call('valid', 'complete_rca_assessment', report)] },
    { role: 'supervisor', text: 'Comparison remains unvalidated.' })
  const result = await askFoundryCopilot('Investigate station power over two windows.')
  assert.equal((result.text.match(/### Source-derived station summary/g) ?? []).length, 2)
})

test('actual proposal tool cannot escalate priority from model arguments', async () => {
  reset()
  for (const explicit of [undefined, 'Low', 'Critical']) {
    const staged = []
    const run = createToolRuntime(defaultCopilotSettings(), {
      proposalPriority: explicit, onWorkOrderProposal: proposal => staged.push(proposal),
    })
    const result = await run('propose_work_order', {
      equipment_id: 'EQUIP_RTI_T005', title: 'Inspect reported measurement',
      description: 'Operator-requested evidence review; no established physical fault.', priority: 'High',
    })
    assert.equal(result.result.proposal.priority, explicit ?? 'Medium')
    assert.equal(staged.length, 1)
    assert.equal(staged[0].priority, explicit ?? 'Medium')
    assert.equal(result.result.confirmation_required, true)
  }
})
