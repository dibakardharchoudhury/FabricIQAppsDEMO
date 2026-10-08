import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { after, test } from 'node:test'

const harness = { reads: [], requests: [], responses: [], verifiedSources: [], options: undefined }
globalThis.__rcaRuntimeTest = harness
const stubs = {
  '../fabric.ts': 'export const foundryAgentToken = async () => "test-token"; export const verifyDataAgentForFoundry = async () => { globalThis.__rcaRuntimeTest.verifiedSources.push("data-agent"); }; export const verifyOntologyForFoundry = async () => { globalThis.__rcaRuntimeTest.verifiedSources.push("ontology"); }; export const queryStid = async () => { if (!globalThis.__rcaRuntimeTest.stid) throw new Error("Unexpected STID read"); return globalThis.__rcaRuntimeTest.stid; }; export const runKustoQuery = async (...args) => { if (!globalThis.__rcaRuntimeTest.kusto) throw new Error("Unexpected Kusto read"); return globalThis.__rcaRuntimeTest.kusto(...args); };',
  '../rayfin.ts': 'export const isRayfinConfigured = () => true; export const isRayfinSignedIn = () => true; export const listWorkOrders = async () => []; export const listAsset3DModels = listWorkOrders; export const listInspections = listWorkOrders; export const listMaintenanceNotifications = listWorkOrders; export const listSpareParts = listWorkOrders;',
  './settings.ts': 'export const loadCopilotSettings = () => ({projectEndpoint:"https://test.services.ai.azure.com/api/projects/test"}); export const enabledKustoNames = () => ["OPCUAEvents"]; export const renderCoordinatorPrompt = () => ""; export const renderSystemPrompt = () => "";',
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
  harness.stid = harness.kusto = undefined
  harness.reads.push({ result: { rows: [{ Station: 'Sloy', average_power_MW: 123.45 }] }, rowCount: 1, groundedSummary: 'Measured mean: 123.45 MW.' })
}

test('full-fleet snapshots enumerate all quality states and disclose inventory signals without readings', async () => {
  reset()
  harness.stid = {
    inventoryComplete: true, facilities: [], systems: [],
    equipment: ['T001', 'T002', 'T003'].map(id => ({ equipment_id: `EQUIP_RTI_${id}`, tag: id, equipment_type_code: 'turbine' })),
    instruments: ['T001', 'T002', 'T003'].map(id => ({
      equipment_id: `EQUIP_RTI_${id}`, instrument_id: `I_${id}`, opcua_node_id: `ns=2;s=${id}.turbine_temp`, unit: 'C',
    })),
  }
  harness.kusto = async query => {
    assert.doesNotMatch(query, /where.*quality/)
    return { columns: ['opcua_node_id', 'value', 'event_time', 'quality'],
      rows: [['ns=2;s=T001.turbine_temp', 80, '2026-10-08T06:00:00Z', 'BAD'],
        ['ns=2;s=T002.turbine_temp', 70, '2026-10-08T06:00:00Z', 'GOOD']] }
  }
  const run = createToolRuntime(defaultCopilotSettings())
  const result = await run('query_signal_quality_snapshot', { quality: 'BAD', equipment_type: 'turbine', lookback: 'today' })
  assert.equal(result.result.rows.length, 1)
  assert.equal(result.result.population.expected_signal_count, 3)
  assert.deepEqual(result.result.population.signals_without_readings, ['ns=2;s=T003.turbine_temp'])
  assert.equal(result.result.latest_quality_node_count, 1)
  harness.stid.inventoryComplete = false
  await assert.rejects(run('query_signal_quality_snapshot', { quality: 'BAD', equipment_type: 'turbine' }), /pagination/)
  const partial = await run('query_assets', { entity: 'equipment', where: [{ column: 'tag', op: 'eq', value: 'T999' }] })
  assert.equal(partial.result.truncated, true)
  assert.equal(partial.result.total_matched, null)
  assert.deepEqual(partial.result.rows, [])
  harness.stid.inventoryComplete = true
  harness.kusto = async () => ({ columns: ['opcua_node_id', 'value', 'event_time', 'quality'],
    rows: Array.from({ length: 500 }, () => ['unknown', 80, '2026-10-08T06:00:00Z', 'BAD']) })
  await assert.rejects(run('query_signal_quality_snapshot', { quality: 'BAD', equipment_type: 'turbine' }), /source row limit/)
})

test('coordinator rejects selected-node verification and preserves a deterministic comparison instead of Chief claims', async () => {
  reset()
  const snapshot = temperature => ({ result: {
    rows: [{ equipment_id: 'EQUIP_RTI_T001', opcua_node_id: 'ns=2;s=T001.turbine_temp', value: 80,
      quality: 'BAD', unit: 'C', event_time: '2026-10-08T06:00:00Z', open_work_orders: [] }],
    population: { equipment_type: 'turbine', equipment_ids: null, inventory_complete: true,
      expected_signal_count: 1, signals_without_readings: [] },
    truncated: false, quality_filter: 'BAD', lookback: 'today', unresolved_nodes: [],
    read_completed_at_utc: '2026-10-08T10:00:00Z',
    ...(temperature ? { requested_limit: 5 } : {}),
  }, rowCount: 1 })
  harness.reads.push(snapshot(false), snapshot(true))
  harness.responses.push(delegate('fabric-iq'), nativeReply('No comparable table returned.'), delegate('rca'),
    delegate('qa'), { role: 'qa', calls: [read] },
    { role: 'qa', text: 'All fleet members verified using this unrelated station query.' },
    { role: 'qa', calls: [
      call('bad', 'hydro_query', { tool_name: 'query_signal_quality_snapshot', arguments: { quality: 'BAD', equipment_type: 'turbine', lookback: 'today' } }),
      call('hot', 'hydro_query', { tool_name: 'query_turbine_temperature_snapshot', arguments: { lookback: 'today', limit: 5 } }),
    ] },
    { role: 'qa', text: 'Native and direct results match completely.' },
    { role: 'supervisor', text: 'All turbines are healthy and every comparison passed.' })
  const result = await askFoundryCopilot('Ask the Data Agent which turbines have BAD signals and hottest temperatures today; independently verify both populations.')
  assert.match(result.text, /Source-checked fleet comparison/)
  assert.match(result.text, /Comparison incomplete/)
  assert.doesNotMatch(result.text, /All turbines are healthy|every comparison passed|match completely/)
  assert.ok(harness.requests.filter(request => request.agent_reference.name === 'hydro-qa-agent').slice(0, 3)
    .every(request => request.tool_choice === 'required'))
  assert.ok(result.orchestrationEvents.flatMap(event => event.trace ?? [])
    .some(trace => /Rejected incomplete fleet/.test(trace.label)))
  assert.ok(harness.requests.some(request => request.input.some(item => /independent direct population evidence first/.test(item.output ?? ''))))
  assert.equal(result.orchestrationEvents.filter(event => event.role === 'rca').length, 0)
})

test('typed-query execution cannot be presented as validation of the supplied invalid KQL', async () => {
  reset()
  const query = 'OPCUAEvents | summarize arg_max(event_time, value, quality) by opcua_node_id | project value=arg_max_value'
  harness.reads.length = 0
  harness.reads.push({ result: { rows: [{ value: 75.335, quality: 'GOOD', event_time: '2026-10-08T06:05:01.359187Z' }],
    read_completed_at_utc: '2026-10-08T10:00:00Z', truncated: false },
  rowCount: 1, query: 'OPCUAEvents | summarize arg_max(event_time, value, quality) by opcua_node_id' })
  harness.responses.push(delegate('qa'), { role: 'qa', calls: [call('typed', 'hydro_query', {
    tool_name: 'query_telemetry', arguments: { opcua_node_ids: ['ns=2;s=T003.turbine_temp'], lookback: 'today', aggregation: 'latest' },
  })] }, { role: 'qa', text: 'The original KQL is valid.' }, { role: 'supervisor', text: 'The original KQL is valid and executed successfully.' })
  const result = await askFoundryCopilot(`Check this read-only query. Correct invalid columns or use the equivalent typed tool. Query: ${query}`)
  assert.match(result.text, /Rejected by local validation/)
  assert.match(result.text, /Supplied query executed through the bounded guard: \*\*no\*\*/)
  assert.match(result.text, /Executed query\\_telemetry/)
  assert.match(result.text, /75\.335/)
  assert.match(result.text, /Stale/)
  assert.doesNotMatch(result.text, /The original KQL is valid/)
})

test('native handoffs reject local tool prescriptions before invoking or consuming a delegation', async () => {
  reset()
  harness.responses.push({ role: 'supervisor', calls: [call('bad_boundary', 'delegate_to_agent', {
    specialist: 'fabric-iq', native_source: 'data-agent',
    question: 'Run query_signal_quality_snapshot and query_turbine_temperature_snapshot today.',
  })] }, delegate('fabric-iq'), nativeReply('Native inventory returned.'), { role: 'supervisor', text: 'Native inventory returned.' })
  const result = await askFoundryCopilot('Ask the published Data Agent for the turbine inventory.')
  assert.equal(result.orchestrationEvents.filter(event => event.role === 'fabric-iq').length, 1)
  assert.deepEqual(harness.verifiedSources, ['data-agent'])
  assert.ok(harness.requests.some(request => request.input.some(item => /cannot prescribe local Hydro/.test(item.output ?? ''))))
  assert.equal(harness.responses.length, 0)
})

test('two native retrievals cannot replace requested direct asset and work-order reconciliation', async () => {
  reset()
  harness.reads.length = 0
  harness.reads.push({ result: { rows: [{ equipment_id: 'EQUIP_RTI_T001', facility_id: 'FACILITY_1' }], truncated: false }, rowCount: 1 },
    { result: { rows: [{ workOrderNumber: 'WO-1', equipmentId: 'EQUIP_RTI_T001', status: 'Draft' }], truncated: false }, rowCount: 1 })
  harness.responses.push(delegate('fabric-iq', 'ontology'), nativeReply('Facility inventory.', 'ontology'),
    delegate('fabric-iq'), nativeReply('Work inventory.'),
    { role: 'supervisor', text: 'Direct reconciliation passed without any direct reads.' },
    delegate('qa'), { role: 'qa', text: 'The native records already verify everything.' },
    { role: 'qa', calls: [
      call('assets', 'hydro_query', { tool_name: 'query_assets', arguments: { entity: 'equipment' } }),
      call('orders', 'hydro_query', { tool_name: 'query_operations', arguments: { entity: 'work_orders' } }),
    ] }, { role: 'qa', text: 'Direct equipment and SQL work rows retrieved.' },
    { role: 'supervisor', text: 'Completed with the direct reads.' })
  const progress = []
  const result = await askFoundryCopilot('Use the selected ontology directly to list facilities and ask the published Data Agent for open work orders. Reconcile those results with direct asset and work-order records.', text => progress.push(text))
  assert.equal(result.orchestrationEvents.filter(event => event.role === 'qa').length, 1)
  assert.equal(result.steps.filter(step => step.status === 'done').length, 2)
  assert.ok(harness.requests.some(request => request.input.some(item => /Direct verification remains incomplete/.test(item.content?.[0]?.text ?? ''))))
  assert.deepEqual(progress, [])
  assert.equal(harness.responses.length, 0)
})

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

test('RCA requires actual tool use and gives one completion-only repair for a final-round invalid pointer', async () => {
  reset()
  harness.responses.push(delegate('rca'))
  for (let index = 0; index < 7; index++) {
    if (index) harness.reads.push({ result: { rows: [{ Station: 'Sloy', average_power_MW: 123.45 }] }, rowCount: 1 })
    harness.responses.push({ role: 'rca', calls: [{ ...read, call_id: `source_${index}` }] })
  }
  const corrected = { observations: [{ evidence_id: 'source_0', path: '/rows/0' }],
    hypotheses: report.hypotheses.map(hypothesis => ({ ...hypothesis, supporting: [{ evidence_id: 'source_0', path: '/rows/0' }] })) }
  harness.responses.push(
    { role: 'rca', calls: [call('invalid_final', 'complete_rca_assessment', { ...corrected,
      observations: [{ evidence_id: 'source_0', path: '/data/rows/0' }] })] },
    { role: 'rca', calls: [call('fixed_final', 'complete_rca_assessment', corrected)] },
    { role: 'supervisor', text: 'Investigation completed.' })
  const result = await askFoundryCopilot('Investigate the source measurements.')
  assert.match(result.text, /Cause undetermined/)
  const requests = harness.requests.filter(request => request.agent_reference.name === 'hydro-rca-agent')
  assert.equal(requests.length, 9)
  assert.ok(requests.slice(0, 8).every(request => request.tool_choice === 'required'))
  assert.deepEqual(requests[8].tool_choice, { type: 'function', name: 'complete_rca_assessment' })
  assert.equal(harness.reads.length, 0)
})

test('The final RCA correction cannot read more sources or grant itself another correction', async () => {
  for (const retryRead of [true, false]) {
    reset()
    const invalid = { ...report, observations: [{ ...ref, path: '/data/rows/0' }] }
    harness.responses.push(delegate('rca'), { role: 'rca', calls: [read] })
    for (let index = 0; index < 7; index++) harness.responses.push({
      role: 'rca', calls: [call(`invalid_${index}`, 'complete_rca_assessment', invalid)],
    })
    harness.responses.push({ role: 'rca', calls: [retryRead
      ? { ...read, call_id: 'forbidden_read' }
      : call('still_invalid', 'complete_rca_assessment', invalid)] })
    await assert.rejects(askFoundryCopilot('Investigate the measurements.'), retryRead
      ? /final RCA correction permits only complete_rca_assessment/
      : /exceeded its 9-round execution budget/)
    assert.equal(harness.requests.filter(request => request.agent_reference.name === 'hydro-rca-agent').length, 9)
    assert.equal(harness.responses.length, 0)
    assert.equal(harness.reads.length, 0)
  }
})

test('Gauge corrects the reproduced arg_max alias error locally before executing its source query', async () => {
  reset()
  harness.reads.splice(0, 1, (options, name, args) => createToolRuntime(defaultCopilotSettings(), options)(name, args))
  harness.reads.push({ result: { rows: [{ opcua_node_id: 'ns=2;s=T003.turbine_temp',
    event_time: '2026-10-08T09:00:00Z', value: 76, quality: 'GOOD' }] }, rowCount: 1 })
  harness.responses.push(delegate('qa'),
    { role: 'qa', calls: [call('invalid_kql', 'hydro_query', { tool_name: 'run_kql', arguments: {
      query: 'OPCUAEvents | summarize arg_max(event_time, value, quality) by opcua_node_id | project value=arg_max_value',
    } })] },
    { role: 'qa', calls: [call('latest', 'hydro_query', { tool_name: 'query_telemetry', arguments: {
      opcua_node_ids: ['ns=2;s=T003.turbine_temp'], lookback: 'today', aggregation: 'latest',
    } })] },
    { role: 'qa', text: 'The requested latest raw value is 76.' },
    { role: 'supervisor', text: 'The requested latest raw value is 76.' })
  const result = await askFoundryCopilot('Read the latest raw temperature for the specified signal today.')
  assert.match(result.text, /76/)
  assert.ok(harness.requests.some(request => request.input.some(item =>
    item.type === 'function_call_output' && item.call_id === 'invalid_kql'
    && JSON.parse(item.output).executed === false)))
  assert.equal(harness.responses.length, 0)
  assert.equal(harness.reads.length, 0)
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
