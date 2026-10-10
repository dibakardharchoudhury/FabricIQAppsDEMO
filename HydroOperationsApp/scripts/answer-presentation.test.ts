import assert from 'node:assert/strict'
import test from 'node:test'
import { readAnswerDatasets, datasetVisualizations, answerVisualizations, hideRenderedCsv, hideRenderedData, formatEvidenceCell, appendOmittedSnapshotWork, answerSections, operatorNarrative, operatorDatasets, showRequestedCharts, OPERATIONAL_EVIDENCE_CONTRACT, ANSWER_PRESENTATION_CONTRACT } from '../src/services/copilot/answerPresentation.ts'
import { relatedSuggestions } from '../src/services/copilot/suggestions.ts'
import { agentDefinition, buildAgentInput, nativeSourceError, requestedNativeSources, parseDelegation, parseHydroQuery, parseWorkOrderReview } from '../src/services/copilot/agentDefinitions.ts'
import { APPROVAL_PHASE_TIMEOUT_MS, createApprovalStore } from '../src/services/copilot/approvalStore.ts'
import { createWorkOrderProposal, delegationOrderError, isWorkOrderRequest, missingRequestedSpecialists } from '../src/services/copilot/orchestration.ts'
import { readResponsesStream } from '../src/services/copilot/chatStream.ts'

test('Chief routes post-draft physical-fault follow-ups only to RCA', () => {
  const instructions = agentDefinition('supervisor', 'test-model').instructions
  assert.match(instructions, /physical fault was established.*delegate only to rca/is)
  assert.match(instructions, /Never repeat the Work Order delegation or stage another draft/is)
})

test('agent definitions use role-specific reasoning and least-privilege tools', () => {
  const chief = agentDefinition('supervisor', 'test-model')
  const gauge = agentDefinition('qa', 'test-model')
  const sleuth = agentDefinition('rca', 'test-model')
  const fixer = agentDefinition('work-order', 'test-model')
  const sparky = agentDefinition('fabric-iq', 'test-model', 'data-agent-iq', 'ontology-iq')
  assert.equal(chief.reasoning.effort, 'medium')
  assert.equal(gauge.reasoning.effort, 'low')
  assert.equal(sleuth.reasoning.effort, 'medium')
  assert.equal(fixer.reasoning.effort, 'low')
  assert.equal(sparky.reasoning.effort, 'low')
  assert.match(fixer.instructions, /Before a no_draft decision, call query_operations/)
  assert.match(fixer.instructions, /omit limit/)
  assert.deepEqual(chief.tools.map(tool => tool.name), ['plan_orchestration'])
  assert.deepEqual(gauge.tools.map(tool => tool.name), ['hydro_query'])
  assert.deepEqual(sleuth.tools.map(tool => tool.name), ['hydro_query', 'complete_rca_assessment'])
  assert.deepEqual(fixer.tools.map(tool => tool.name), ['hydro_query', 'complete_work_order_review'])
  assert.deepEqual(sparky.tools.map(tool => tool.server_label), ['fabriciq-data-agent', 'fabriciq-ontology'])
})

test('operator narrative excludes technical inventories and pointer ledgers but keeps limitations', () => {
  const text = '### Investigation\nCause undetermined.\n### Source observations\n52 fields from call_123/rows/0.\n### Sources\ncall_123\n### Returned source inventory: instruments\nRaw IDs.\n### Limitations\nMeasurements are stale.'
  const result = operatorNarrative(text)
  assert.match(result, /Cause undetermined|Measurements are stale/)
  assert.doesNotMatch(result, /52 fields|call_123|Raw IDs|Source observations|Returned source/)
})

test('table-only requests override charts and routine numeric questions do not request them', () => {
  for (const question of ['List the stock.', 'Which turbines are BAD?', 'Show a chart, but only a table.', 'Show a dashboard; no charts.', 'Return table-only.', 'Do not show charts.']) {
    assert.equal(showRequestedCharts(question), false, question)
  }
  assert.equal(showRequestedCharts('Show a temperature chart.'), true)
})

test('operator tables retain every work order and missing value while moving forensic ledgers to audit', () => {
  const text = '### Source observations\n| Pointer | Value |\n| --- | --- |\n| /rows/0 | 7 |\n### Open work\n| workOrderNumber | status |\n| --- | --- |\n| WO-1 | Planned |\n| WO-2 | |\n### Competing hypotheses - untested\n| Hypothesis | Missing evidence |\n| --- | --- |\n| Calibration | Approved limits |'
  const datasets = readAnswerDatasets(text).datasets
  const findings = operatorDatasets(datasets)
  assert.equal(findings.length, 2)
  assert.deepEqual(findings[0].rows, [['WO-1', 'Planned'], ['WO-2', '']])
  assert.equal(datasets.length, 3)
})

test('presentation preserves every source column and row without client-side projections', () => {
  const original = { ...readAnswerDatasets('```csv\nequipmentId,status,internal_metadata\nE1,Planned,opaque\nE2,Approved,opaque2\n```').datasets[0], sourceStep: 0 }
  const findings = operatorDatasets([original])
  assert.deepEqual(findings[0].columns, original.columns)
  assert.equal(findings[0].rows.length, original.rows.length)
  assert.equal(findings[0], original)
})

function responseStream(events: unknown[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      controller.close()
    },
  })
}

test('native labeled bullet records become exact tables and unit-separated charts, not inferred identities', () => {
  const text = `## Latest BAD signals

- **T010** (\`EQUIP_RTI_T010\`)
  - OPC node: \`ns=2;s=T010.power_output\`
  - Latest value: **1133.19 MW**
  - Quality: **BAD**

- **T009** (\`EQUIP_RTI_T009\`)
  - OPC node: \`ns=2;s=T009.vibration_a\`
  - Latest value: **11.034 mm_s**
  - Quality: **BAD**

## Hottest

1. **T013** (\`EQUIP_RTI_T013\`)
   - Temperature signal: \`INST_T013_TURBINE_TEMP\`
   - Raw reading: **94.101 C**
   - Quality: **GOOD**

### Caveats
The measurements are stale.`
  const parsed = readAnswerDatasets(text)
  assert.deepEqual(parsed.issues, [])
  assert.equal(parsed.datasets.length, 2)
  assert.deepEqual(parsed.datasets[0].columns, ['Record', 'OPC node', 'Latest value', 'Quality'])
  assert.deepEqual(parsed.datasets[0].rows[0], ['T010 (EQUIP_RTI_T010)', 'ns=2;s=T010.power_output', '1133.19 MW', 'BAD'])
  assert.equal(parsed.datasets[1].rows[0][0], '1. T013 (EQUIP_RTI_T013)')
  assert.ok(!parsed.datasets[1].columns.includes('OPC node'))
  const charts = answerVisualizations(parsed.datasets, '')
  assert.deepEqual(charts.map(chart => chart.yAxisTitle), ['MW', 'mm_s', 'C'])
  assert.match(charts[0].inlineCsvData, /1133\.19/)
  assert.doesNotMatch(charts[0].inlineCsvData, /11\.034/)
  const narrative = hideRenderedData(text)
  assert.match(narrative, /measurements are stale/)
  assert.doesNotMatch(narrative, /1133\.19|EQUIP_RTI_T010/)
})

test('native work bullets preserve every order, explicit None and exact punctuation in tables', () => {
  const text = `### Open work
- **T005** (\`EQUIP_RTI_T005\`): **None**
- **T008** (\`EQUIP_RTI_T008\`)
  - \`WO-2851\` — **Inspect A|B**; Status: **Planned**; Priority: **Medium**
  - \`WO-471038\` — **Inspect EQUIP_RTI_T008**; Status: **Draft**; Priority: **High**`
  const dataset = readAnswerDatasets(text).datasets[0]
  assert.deepEqual(dataset.columns, ['Record', 'workOrderNumber', 'Title', 'Status', 'Priority'])
  assert.equal(dataset.rows.length, 3)
  assert.equal(dataset.rows[0][0], 'T005 (EQUIP_RTI_T005): None')
  assert.deepEqual(dataset.rows[1], ['T008 (EQUIP_RTI_T008)', 'WO-2851', 'Inspect A|B', 'Planned', 'Medium'])
  assert.deepEqual(dataset.rows[2], ['T008 (EQUIP_RTI_T008)', 'WO-471038', 'Inspect EQUIP_RTI_T008', 'Draft', 'High'])
  const charts = datasetVisualizations(dataset, '')
  assert.equal(charts.length, 2)
  assert.match(charts[0].inlineCsvData, /Planned,1/)
  assert.match(charts[0].inlineCsvData, /Draft,1/)
  assert.ok(charts.every(chart => !chart.inlineCsvData.includes('None')))
})

test('ordinary and fenced lists stay prose; ambiguous records surface an explicit parsing failure', () => {
  for (const text of [
    '- Investigate quality\n- Review current work',
    '```text\n- T005\n  - Value: 2\n  - Unit: C\n```',
  ]) {
    assert.deepEqual(readAnswerDatasets(text).datasets, [])
    assert.equal(hideRenderedData(text), text)
  }
  for (const text of [
    '- T005\n  - Value: 2\n  - Value: 3\n  - Unit: C',
    '- T005\n  - Value: 2\n    - Unit: C',
    '- T005\n  - Value: 2\n  - Unit: C\n    continuation text',
  ]) {
    assert.deepEqual(readAnswerDatasets(text).datasets, [])
    assert.match(readAnswerDatasets(text).issues.join(' '), /No records were guessed/)
    assert.equal(hideRenderedData(text), '')
  }
})

test('inline-unit charts do not zero-fill missing values or interpret prose as measurements', () => {
  for (const values of [['1 C', ''], ['1 C', 'about 2 C'], ['1 C', 'Infinity C']]) {
    const dataset = readAnswerDatasets(`| Asset | Reading |\n| --- | --- |\n| A | ${values[0]} |\n| B | ${values[1]} |`).datasets[0]
    assert.deepEqual(datasetVisualizations(dataset, ''), [])
  }
})

test('ordinary parts queries cannot invent a native-source request; follow-ups retain explicit user source scope', () => {
  assert.match(nativeSourceError('List spare parts at or below reorder level and related open maintenance work.', 'data-agent') ?? '', /not requested/)
  assert.equal(nativeSourceError('Use Fabric IQ to retrieve inventory.', 'data-agent'), undefined)
  assert.equal(nativeSourceError('Recheck that source discrepancy.', 'data-agent', ['Ask the published Data Agent for open work.']), undefined)
  assert.match(nativeSourceError('Recheck that source discrepancy.', 'ontology', ['Ask the published Data Agent for open work.']) ?? '', /not direct Ontology/)
  assert.equal(nativeSourceError('Now use the selected ontology.', 'ontology', ['Ask the Data Agent for open work.']), undefined)
  assert.match(nativeSourceError('Recheck that source discrepancy.', 'data-agent', ['Ask the Data Agent.', 'Now use the ontology.']) ?? '', /not the Data Agent/)
  assert.deepEqual(requestedNativeSources('Use the selected ontology directly to list facility instances. Ask the published Fabric Data Agent for open operational SQL work.'), ['data-agent', 'ontology'])
  assert.deepEqual(requestedNativeSources('Ask the Data Agent for open work. Do not use the ontology.'), ['data-agent'])
  assert.deepEqual(requestedNativeSources('What is the Data Agent?'), [])
})

test('Responses stream surfaces native tool errors and incomplete reasons instead of a generic failure', async () => {
  const message = "An error occurred invoking 'list_ontology_entities': Parameter 'entityName' does not match the required pattern."
  for (const event of [
    { type: 'error', message },
    { type: 'error', error: { message } },
    { type: 'response.failed', response: { error: { message } } },
  ]) await assert.rejects(readResponsesStream(responseStream([event])), /list_ontology_entities.*entityName/)
  await assert.rejects(readResponsesStream(responseStream([
    { type: 'response.incomplete', response: { incomplete_details: { reason: 'max_output_tokens' } } },
  ])), /incomplete: max_output_tokens/)
})

test('supporting sections collapse without removing evidence or hiding limitation sections', () => {
  const text = 'Cause undetermined.\n\n### Source observations\n\n| ID | Value |\n| --- | --- |\n| T005 | 12 |\n\n### Limitations\n\nTelemetry is stale.\n\n### Sources\n\nEventhouse receipt 12.'
  const sections = answerSections(text)
  assert.equal(sections.map(section => section.markdown).join('\n'), text)
  assert.equal(sections.find(section => section.title === 'Source observations')?.collapsed, true)
  assert.equal(sections.find(section => section.title === 'Sources')?.collapsed, true)
  assert.equal(sections.find(section => section.title === 'Limitations')?.collapsed, false)
})

test('section folding preserves fenced headings and document-wide references', () => {
  const text = 'Result.\n\n```text\n### Sources\nnot a section\n```\n\n### Sources\nActual source.'
  assert.equal(answerSections(text).filter(section => section.collapsed).length, 1)
  assert.equal(answerSections(text).map(section => section.markdown).join('\n'), text)
  const references = 'See [source][1].\n\n### Sources\n[1]: https://example.com'
  assert.deepEqual(answerSections(references), [{ markdown: references, collapsed: false }])
  assert.match(ANSWER_PRESENTATION_CONTRACT, /cards are the only draft presentation/)
})

test('nested hydro_query envelopes are rejected with the exact flat retry payload', () => {
  const fields = { equipment_id: 'EQUIP_RTI_T005', title: 'Acceptance', description: 'Inspect only.', priority: 'Low' }
  const parsed = parseHydroQuery(JSON.stringify({ tool_name: 'propose_work_order', arguments: { arguments: fields } }))
  assert.equal(parsed.ok, false)
  if (!parsed.ok) {
    assert.match(parsed.error, /arguments\.arguments wrapper/)
    assert.ok(parsed.error.includes(JSON.stringify({ tool_name: 'propose_work_order', arguments: fields })))
  }
  const corrected = parseHydroQuery(JSON.stringify({ tool_name: 'propose_work_order', arguments: fields }))
  assert.equal(corrected.ok, true)
})

test('independent final verification cannot consume the slot needed for draft review', () => {
  const prompt = 'Read T005 telemetry, investigate its condition, prepare an editable work-order draft and finally independently verify identity and coverage. Do not save.'
  assert.equal(delegationOrderError(prompt, 'qa', []), undefined)
  assert.match(delegationOrderError(prompt, 'qa', ['qa', 'rca']), /work-order first/)
  assert.equal(delegationOrderError(prompt, 'work-order', ['qa', 'rca']), undefined)
  assert.equal(delegationOrderError(prompt, 'qa', ['qa', 'rca', 'work-order']), undefined)
  assert.equal(delegationOrderError('Read T005 work. No drafting requested.', 'qa', ['qa']), undefined)
  const investigationOnly = 'Investigate T005 and then independently verify the evidence. Do not create a work order.'
  assert.deepEqual(missingRequestedSpecialists(investigationOnly, ['qa', 'rca']), ['qa'])
  assert.match(delegationOrderError(investigationOnly, 'qa', ['qa']), /rca first/)
  assert.deepEqual(missingRequestedSpecialists(investigationOnly, ['qa', 'rca', 'qa']), [])
})

test('an unsent notification draft is not delegated to the work-order proposal specialist', () => {
  const prompt = 'Investigate inconsistencies, then draft a short notification with unknown facts marked. Do not send it.'
  assert.equal(isWorkOrderRequest(prompt), false)
  assert.match(delegationOrderError(prompt, 'work-order', ['qa', 'rca']), /prose, not an editable work-order/)
  assert.equal(delegationOrderError(prompt, 'rca', ['qa']), undefined)
  assert.equal(delegationOrderError('Prepare a work order and draft a notification. Do not save.', 'work-order', []), undefined)
})

test('failed native tool items propagate even when the enclosing response completes', async () => {
  await assert.rejects(readResponsesStream(responseStream([
    { type: 'response.output_item.done', item: { type: 'mcp_call', name: 'fabric-query', status: 'failed', error: { message: 'Output moderation failed' } } },
    { type: 'response.completed', response: { output: [] } },
  ])), /native tool fabric-query failed.*Output moderation failed/)
  await assert.rejects(readResponsesStream(responseStream([
    { type: 'response.output_item.done', item: { type: 'mcp_call', name: 'fabric-query', status: 'completed', error: 'Source unavailable' } },
  ])), /Source unavailable/)
})

test('inventory freshness is distinct from telemetry age and unconnected sources are not tools', () => {
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /Do not declare inventory stale from an old restock date alone/)
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /evidence requirements, not callable tools/)
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /Do not justify a second conditional draft merely because the existing order is Draft/)
})

test('only Work Orders can record a structured no-draft review, never a pretend staged card', () => {
  assert.deepEqual(parseWorkOrderReview('{"decision":"no_draft","reason":" Existing order covers the signal. "}'),
    { decision: 'no_draft', reason: 'Existing order covers the signal.' })
  assert.equal(parseWorkOrderReview('{"decision":"needs_clarification","reason":"Two matching equipment IDs."}').decision, 'needs_clarification')
  assert.throws(() => parseWorkOrderReview('{"decision":"staged","reason":"Prose draft"}'), /explicit decision/)
  assert.throws(() => parseWorkOrderReview('{"decision":"no_draft","reason":""}'), /explicit decision/)
  assert.match(JSON.stringify(agentDefinition('work-order', 'test').tools), /complete_work_order_review/)
  for (const role of ['supervisor', 'qa', 'rca'] as const) assert.doesNotMatch(JSON.stringify(agentDefinition(role, 'test').tools), /complete_work_order_review/)
})

test('hyphenated work-order prompts and generated draft suggestions reach the approval workflow', () => {
  const prompt = 'Prepare one editable Low-priority inspection work-order draft for EQUIP_RTI_T005 titled "Acceptance battle review - T005 - DO NOT DISPATCH". Check existing work first. Do not save a SQL record.'
  assert.equal(isWorkOrderRequest(prompt), true)
  assert.deepEqual(missingRequestedSpecialists(prompt, ['qa']), ['work-order'])
  const draftSuggestion = relatedSuggestions('EQUIP_RTI_T005 temperature is stale.').find(suggestion => /draft/i.test(suggestion))
  assert.ok(draftSuggestion)
  assert.equal(isWorkOrderRequest(draftSuggestion), true)
  assert.equal(isWorkOrderRequest('List existing Draft work-orders for T005.'), false)
  assert.equal(isWorkOrderRequest('Do not create a work-order for T005.'), false)
})

test('compound summaries preserve omitted snapshot work without inventing or duplicating orders', () => {
  const result = JSON.stringify({ read_completed_at_utc: '2026-10-07T20:25:19Z', rows: [
    { equipment_id: 'EQUIP_RTI_T002', opcua_node_id: 'ns=2;s=T002.power_output', open_work_orders: [
      { workOrderNumber: 'WO-2836', title: 'Inlet pressure', status: 'Scheduled', priority: 'High', relation: 'equipment-level' },
    ] },
    { equipment_id: 'EQUIP_RTI_T008', opcua_node_id: 'ns=2;s=T008.vibration_a', open_work_orders: [
      { workOrderNumber: 'WO-28', title: 'Inspect | signal\nchain', status: 'Draft', priority: 'High', relation: 'equipment-level' },
      { workOrderNumber: 'WO-2851', title: 'Power output dip', status: 'Planned', priority: 'Medium', relation: 'equipment-level' },
    ] },
    { equipment_id: 'EQUIP_RTI_T011', opcua_node_id: 'ns=2;s=T011.power_output', open_work_orders: [
      { workOrderNumber: 'WO-2857', title: 'Cooling inspection', status: 'In progress', priority: 'High', relation: 'equipment-level' },
    ] },
  ] })
  const step = { tool: 'query_signal_quality_snapshot', status: 'done' as const, result }
  const text = appendOmittedSnapshotWork('Selected T002 has WO-2836. Not WO-280.', [step, step])
  const datasets = readAnswerDatasets(text).datasets
  assert.equal(datasets.length, 1)
  assert.deepEqual(datasets[0].rows.map(row => row[2]), ['WO-28', 'WO-2851', 'WO-2857'])
  assert.equal(datasets[0].rows[0][3], 'Inspect | signal chain')
  assert.ok(datasets[0].rows.every(row => row[6] === 'equipment-level' && row[7] === '2026-10-07T20:25:19Z'))
  assert.equal(appendOmittedSnapshotWork(text, [step]), text)
  assert.equal(appendOmittedSnapshotWork('No results.', [{ ...step, status: 'error' }]), 'No results.')
  assert.equal(appendOmittedSnapshotWork('No results.', [{ ...step, result: '{"rows":[]}' }]), 'No results.')
  assert.throws(() => appendOmittedSnapshotWork('Incomplete', [{ ...step, result: '{}' }]), /no rows array/)
})

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
  const history = [{ role: 'user' as const, content: 'Unrelated old inventory'.repeat(100) }]
  for (const role of ['qa', 'rca', 'work-order', 'fabric-iq'] as const) {
    const scoped = buildAgentInput('policy', history, 'Assigned task with verified equipment ID', role)
    assert.equal(scoped.length, 2)
    assert.doesNotMatch(JSON.stringify(scoped), /Unrelated old inventory/)
    assert.match(JSON.stringify(scoped), /verified equipment ID/)
  }
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

test('native CSV headings render only actual rectangular datasets, not flattened prose', () => {
  for (const heading of ['Average power per station (CSV)', '### CSV dataset (station, MW)']) {
    const { datasets } = readAnswerDatasets(`${heading}\n\nStation,average_power_MW\nSite A,1.2\nSite B,2.3`)
    assert.equal(datasets.length, 1)
    assert.deepEqual(datasets[0].rows, [['Site A', '1.2'], ['Site B', '2.3']])
    assert.equal(answerVisualizations(datasets, 'Chart average power').length, 1)
    const flattened = `${heading}\nStation,average_power_MW Site A,1.2 Site B,2.3`
    assert.equal(readAnswerDatasets(flattened).datasets.length, 0)
    assert.equal(hideRenderedCsv(flattened), flattened)
  }
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

test('live labelled CSV without fences renders a five-turbine chart without inventing rows', () => {
  const text = 'CSV: Latest temperature points for top-5 turbines\n'
    + 'timestamp_UTC,turbine,temperature_C\n'
    + '2026-10-07T19:59:25Z,T014,94.51\n2026-10-07T19:59:32Z,T003,92.277\n'
    + '2026-10-07T19:59:33Z,T004,88.844\n2026-10-07T19:59:35Z,T007,77.692\n2026-10-07T19:59:34Z,T005,74.871'
  const { datasets } = readAnswerDatasets(text)
  const [chart] = answerVisualizations(datasets, 'temperature chart')
  assert.equal(datasets[0].rows.length, 5)
  assert.equal(chart.chartType, 'bar')
  assert.equal(chart.xColumn, 'turbine')
  assert.deepEqual(chart.yColumns, ['temperature_C'])
  assert.doesNotMatch(hideRenderedCsv(text), /94\.51/)
  assert.equal(datasets[0].rows[0][2], '94.51')
  const malformed = 'CSV: Broken\nA,B\nT1,2,3'
  assert.equal(hideRenderedCsv(malformed), malformed)
  assert.deepEqual(readAnswerDatasets(`\`\`\`text\n${text}\n\`\`\``).datasets, [])
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

test('a draft from an incomplete workflow is withdrawn, not treated as human rejection', async () => {
  const store = createApprovalStore<string>()
  const proposal = createWorkOrderProposal({ equipmentId: 'EQUIP_RTI_T005', title: 'Incomplete investigation', priority: 'Low' })
  store.stage(proposal)
  store.withdraw(proposal.id)
  assert.equal(store.get(proposal.id)?.state, 'withdrawn')
  let writes = 0
  await assert.rejects(store.approve(proposal.id, proposal, async () => {}, async () => { writes++; return 'created' }), /withdrawn/)
  assert.equal(writes, 0)
  assert.throws(() => store.withdraw(proposal.id), /Only a pending/)
})

test('Supervisor requests visible routing and conditional-selection contracts', () => {
  assert.match(JSON.stringify(agentDefinition('supervisor', 'test').tools), /"required":\["specialist","question","reason","native_source","requires_selection"\]/)
  const delegation = parseDelegation('{"specialist":"fabric-iq","question":"Read ontology instances","reason":"Ontology-native instance data","requires_selection":false}')
  assert.equal(delegation.reason, 'Ontology-native instance data')
  assert.equal(delegation.requiresSelection, false)
  assert.throws(() => parseDelegation('{"specialist":"qa","question":"Read work","reason":"Direct read"}'), /selection dependency/)
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
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /read_completed_at_utc/)
  assert.match(OPERATIONAL_EVIDENCE_CONTRACT, /received after request start does not establish source clock skew/)
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
  assert.equal(parseDelegation('{"specialist":"fabric-iq","question":"query ontology","requires_selection":false}').specialist, 'fabric-iq')
  assert.throws(() => parseDelegation('{"specialist":"arbitrary-agent","question":"query","requires_selection":false}'), /Unknown/)
})

const proposal = () => createWorkOrderProposal({ equipmentId: 'E1', title: 'Inspect bearing', description: 'Observed vibration', priority: 'High' })

test('approval is editable, explicit and prevents duplicate concurrent writes', async () => {
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  let writes = 0
  const edits = { title: 'Inspect cooling', description: 'Operator-reviewed observation', priority: 'Medium' as const }
  const first = store.approve(draft.id, edits, async () => {}, async value => { writes++; assert.equal(value.title, edits.title); return 'WO-123' })
  await assert.rejects(store.approve(draft.id, edits, async () => {}, async () => 'duplicate'), /validating|saving|created/)
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

test('a validation timeout cannot start a late SQL write', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  let finishValidation!: () => void
  const validation = new Promise<void>(resolve => { finishValidation = resolve })
  let writes = 0
  const approval = store.approve(draft.id, draft, () => validation, async () => { writes++; return 'unexpected' })
  const rejected = assert.rejects(approval, /No SQL write was attempted/)
  assert.equal(store.get(draft.id)?.state, 'validating')
  context.mock.timers.tick(APPROVAL_PHASE_TIMEOUT_MS + 1)
  await rejected
  assert.equal(store.get(draft.id)?.state, 'pending')
  assert.equal(await store.approve(draft.id, draft, async () => {}, async () => 'WO-revalidated'), 'WO-revalidated')
  finishValidation()
  await validation
  await Promise.resolve()
  assert.equal(writes, 0)
  assert.equal(store.get(draft.id)?.result, 'WO-revalidated')
})

test('a late failed write remains uncertain and cannot trigger a retry', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  let failWrite!: (reason: Error) => void
  let markStarted!: () => void
  const writing = new Promise<string>((_, reject) => { failWrite = reject })
  const started = new Promise<void>(resolve => { markStarted = resolve })
  const approval = store.approve(draft.id, draft, async () => {}, () => { markStarted(); return writing })
  const rejected = assert.rejects(approval, /could not be confirmed/)
  await started
  context.mock.timers.tick(APPROVAL_PHASE_TIMEOUT_MS + 1)
  await rejected
  const lateFailure = assert.rejects(writing, /late connection failure/)
  failWrite(new Error('late connection failure'))
  await lateFailure
  assert.equal(store.get(draft.id)?.state, 'uncertain')
  assert.equal(store.get(draft.id)?.result, undefined)
  await assert.rejects(store.approve(draft.id, draft, async () => {}, async () => 'duplicate'), /uncertain/)
})

test('write timeout blocks replay and a late database acknowledgement updates shared state', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  let finishWrite!: (value: string) => void
  let markStarted!: () => void
  const writing = new Promise<string>(resolve => { finishWrite = resolve })
  const started = new Promise<void>(resolve => { markStarted = resolve })
  const approval = store.approve(draft.id, draft, async () => {}, () => { markStarted(); return writing })
  const rejected = assert.rejects(approval, /could not be confirmed/)
  await started
  assert.equal(store.get(draft.id)?.state, 'saving')
  context.mock.timers.tick(APPROVAL_PHASE_TIMEOUT_MS + 1)
  await rejected
  assert.equal(store.get(draft.id)?.state, 'uncertain')
  await assert.rejects(store.approve(draft.id, draft, async () => {}, async () => 'duplicate'), /uncertain/)
  finishWrite('WO-late-confirmation')
  await writing
  assert.equal(store.get(draft.id)?.state, 'created')
  assert.equal(store.get(draft.id)?.result, 'WO-late-confirmation')
  assert.equal(store.get(draft.id)?.error, undefined)
})

test('approval observers receive transitions and stop receiving updates after unsubscribe', async () => {
  const store = createApprovalStore<string>()
  const draft = proposal()
  const states: (string | undefined)[] = []
  const unsubscribe = store.subscribe(() => states.push(store.get(draft.id)?.state))
  store.stage(draft)
  await store.approve(draft.id, draft, async () => {}, async () => 'WO-123')
  assert.deepEqual(states, ['pending', 'validating', 'saving', 'created'])
  store.clear()
  assert.equal(states.at(-1), undefined)
  unsubscribe()
  store.stage(draft)
  assert.equal(states.length, 5)
})

test('a write acknowledgement racing the deadline is never overwritten as uncertain', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const store = createApprovalStore<string>()
  const draft = proposal()
  store.stage(draft)
  let finish!: (value: string) => void
  let started!: () => void
  const writing = new Promise<string>(resolve => { finish = resolve })
  const start = new Promise<void>(resolve => { started = resolve })
  const approval = store.approve(draft.id, draft, async () => {}, () => { started(); return writing })
  await start
  setTimeout(() => finish('WO-deadline'), APPROVAL_PHASE_TIMEOUT_MS)
  context.mock.timers.tick(APPROVAL_PHASE_TIMEOUT_MS)
  assert.equal(await approval, 'WO-deadline')
  assert.equal(store.get(draft.id)?.state, 'created')
  assert.equal(store.get(draft.id)?.result, 'WO-deadline')
})
