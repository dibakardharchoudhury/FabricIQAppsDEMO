import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import { renderToStaticMarkup } from 'react-dom/server'
import { defaultCopilotSettings, mergeCopilotSettings } from '../src/services/copilot/settings.ts'
import * as answerPresentation from '../src/services/copilot/answerPresentation.ts'
import * as sourcePresentation from '../src/services/copilot/sourcePresentation.ts'
import { requiresChartOutput } from '../src/services/copilot/orchestration.ts'

const require = createRequire(import.meta.url)
function component(name) {
  const source = readFileSync(new URL(`../src/components/${name}.tsx`, import.meta.url), 'utf8')
  const code = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText
  const exports = {}
  const componentRequire = specifier => {
    if (specifier === '../services/copilot/answerPresentation') return answerPresentation
    if (specifier === '../services/copilot/sourcePresentation') return sourcePresentation
    if (specifier === './AgentVisualizationView') return { AgentVisualizationView }
    return require(specifier)
  }
  new Function('require', 'exports', code)(componentRequire, exports)
  return exports[name]
}
const AgentVisualizationView = component('AgentVisualizationView')
const AgentElapsedTime = component('AgentElapsedTime')
const AnswerDashboard = component('AnswerDashboard')

const returnedRows = [
  { timestamp_utc: '2026-10-07T14:54:22.9270957Z', power_output_MW: 1823.09 },
  { timestamp_utc: '2026-10-07T14:54:37.5216687Z', power_output_MW: 1137.402 },
]
const sourceSteps = rows => [{ tool: 'query_telemetry', status: 'done', detail: 'Source query',
  summary: 'Returned rows', elapsedMs: 1,
  result: JSON.stringify({ rows, truncated: true, read_completed_at_utc: '2026-10-08T16:19:39.834Z' }) }]

test('native bullet records render real tables and charts without acquiring verified-source status', () => {
  const text = '## Latest readings\n- **T005** (`EQUIP_RTI_T005`)\n  - Raw reading: **91.156 C**\n  - Quality: **GOOD**\n- **T013** (`EQUIP_RTI_T013`)\n  - Raw reading: **94.101 C**\n  - Quality: **GOOD**'
  const html = renderToStaticMarkup(createElement(AnswerDashboard, { text, question: 'Show a chart.' }))
  assert.equal((html.match(/<table>/g) ?? []).length, 1)
  assert.equal((html.match(/<tr>/g) ?? []).length, 3)
  assert.match(html, /role="img"/)
  assert.match(html, /EQUIP_RTI_T005/)
  assert.match(html, /91\.156/)
  assert.match(html, /not independently verified/)
  assert.doesNotMatch(html, /application-checked findings/)
})

test('native source charts share the panel bound without losing tables', () => {
  const steps = Array.from({ length: 13 }, (_, index) => ({
    tool: 'native_reader', status: 'done',
    result: `### Result ${index}\n\`\`\`csv\nasset,count\nT001,1\nT002,2\n\`\`\``,
  }))
  const result = sourcePresentation.presentSourceRows(steps, '')
  assert.equal(result.datasets.length, 13)
  assert.equal(result.visualizations.length, 12)
  assert.match(result.issues.join(' '), /limited to 12 panels/)
})

test('empty unit cells do not combine unrelated raw signals', () => {
  const result = sourcePresentation.presentSourceRows(sourceSteps([
    { signal: 'temperature', value: 70, unit: '' },
    { signal: 'power', value: 10, unit: '' },
  ]), '')
  assert.equal(result.visualizations.length, 2)
  assert.ok(result.visualizations.every(chart => !(chart.inlineCsvData.includes('temperature') && chart.inlineCsvData.includes('power'))))
})

test('snapshot work counts stay in tables without repeating count charts for every unit', () => {
  const result = sourcePresentation.presentSourceRows(sourceSteps([
    { turbine: 'T1', signal: 'temperature', value: 70, unit: 'C', open_work_orders: [] },
    { turbine: 'T2', signal: 'temperature', value: 75, unit: 'C', open_work_orders: [] },
  ]), '')
  assert.ok(result.datasets[0].columns.includes('open_work_count'))
  assert.equal(result.visualizations.length, 1)
  assert.doesNotMatch(result.visualizations[0].inlineCsvData, /open_work_count/)
})

test('checked RCA and reconciliation tables survive alongside raw source tables', () => {
  const text = '### Competing hypotheses - untested\n| Hypothesis | Missing evidence |\n| --- | --- |\n| Sensor issue | Calibration |\n| Equipment issue | Approved limits |'
  const props = { text, steps: sourceSteps(returnedRows) }
  const unverified = renderToStaticMarkup(createElement(AnswerDashboard, props))
  assert.doesNotMatch(unverified, /Sensor issue|Equipment issue/)
  const checked = renderToStaticMarkup(createElement(AnswerDashboard, { ...props, checkedText: true }))
  assert.match(checked, /Sensor issue/)
  assert.match(checked, /Equipment issue/)
  assert.match(checked, /application-checked findings/)
  assert.equal((checked.match(/<table>/g) ?? []).length, 2)
  const failed = renderToStaticMarkup(createElement(AnswerDashboard, { ...props, checkedText: true,
    steps: [...props.steps, { tool: 'query_telemetry', status: 'error' }] }))
  assert.doesNotMatch(failed, /Sensor issue|Equipment issue/)
  const nativeClaim = '\n### Native-source retrieval claims\n> | Claim | Value | | --- | --- | | Invented | 999 |'
  const quoted = renderToStaticMarkup(createElement(AnswerDashboard, { ...props, text: text + nativeClaim, checkedText: true }))
  assert.doesNotMatch(quoted, /Invented|999/)
})

test('model charts require exact source projections and cannot invent labels or amplify rows', () => {
  const steps = sourceSteps([{ turbine: 'T1', value: 70, unit: 'C' }, { turbine: 'T2', value: 75, unit: 'C' }])
  const spec = { chartType: 'bar', title: 'A fault is proven', xColumn: 'turbine', yColumns: ['value'],
    xAxisTitle: 'Unsafe label', yAxisTitle: 'MW', inlineCsvData: 'turbine,value\nT1,70\nT2,75' }
  const checked = sourcePresentation.groundVisualization(spec, steps)
  assert.doesNotMatch(checked.title, /fault/)
  assert.match(checked.yAxisTitle, /\(C\)/)
  for (const csv of ['turbine,value\nT1,999', 'turbine,value\nT1,70\nT1,70', 'turbine,value\nT3,70']) {
    assert.throws(() => sourcePresentation.groundVisualization({ ...spec, inlineCsvData: csv }, steps), sourcePresentation.ChartGroundingError)
  }
  assert.throws(() => sourcePresentation.groundVisualization(spec, []), sourcePresentation.ChartGroundingError)
  const mixed = sourceSteps([{ turbine: 'T1', value: 70, unit: 'C' }, { turbine: 'T2', value: 75, unit: 'MW' }])
  assert.throws(() => sourcePresentation.groundVisualization(spec, mixed), sourcePresentation.ChartGroundingError)
  const unknown = sourceSteps([{ turbine: 'T1', signal: 'temperature', value: 70 }, { turbine: 'T2', signal: 'power', value: 75 }])
  assert.throws(() => sourcePresentation.groundVisualization(spec, unknown), sourcePresentation.ChartGroundingError)
  const native = [{ tool: 'native_agent', status: 'done', result: JSON.stringify({ rows: [{ turbine: 'T1', value: 70 }, { turbine: 'T2', value: 75 }] }) }]
  assert.throws(() => sourcePresentation.groundVisualization(spec, native), sourcePresentation.ChartGroundingError)
})

test('deterministic category counts are permitted chart sources', () => {
  const steps = [{ tool: 'query_operations', status: 'done', result: JSON.stringify({
    rows: [{ status: 'Draft' }, { status: 'Approved' }, { status: 'Draft' }],
  }) }]
  const spec = { chartType: 'bar', title: 'Backlog', xColumn: 'status', yColumns: ['record_count'],
    inlineCsvData: 'status,record_count\nDraft,2\nApproved,1' }
  assert.match(sourcePresentation.groundVisualization(spec, steps).title, /Source-backed/)
  const workSteps = [{ tool: 'query_operations', status: 'done', result: JSON.stringify({ rows: [
    { workOrderNumber: 'WO-1', equipmentId: 'T1', status: 'Draft' },
    { workOrderNumber: 'WO-2', equipmentId: 'T1', status: 'Draft' },
    { workOrderNumber: 'WO-3', equipmentId: 'T2', status: 'Draft' },
  ] }) }]
  assert.match(sourcePresentation.groundVisualization({ ...spec, xColumn: 'equipmentId',
    inlineCsvData: 'equipmentId,record_count\nT1,2\nT2,1' }, workSteps).title, /Source-backed/)
})

test('a requested explicit chart does not add unrelated automatic count charts', () => {
  const steps = [...sourceSteps([{ signal: 'temperature', value: 70, unit: 'C' }]), {
    tool: 'query_operations', status: 'done', result: JSON.stringify({ rows: [
      { workOrderNumber: 'WO-1', equipmentId: 'T1', status: 'Draft' },
      { workOrderNumber: 'WO-2', equipmentId: 'T2', status: 'Approved' },
    ] }),
  }]
  const explicit = sourcePresentation.presentSourceRows(steps.slice(0, 1), '').visualizations
  const html = renderToStaticMarkup(createElement(AnswerDashboard, { text: '', question: 'Show a temperature chart.', steps, visualizations: explicit }))
  assert.doesNotMatch(html, /returned records by equipmentId/)
  assert.doesNotMatch(html, /returned records by status/)
  assert.equal((html.match(/<svg/g) ?? []).length, 1)
  assert.match(html, /WO-1/)
  assert.match(html, /WO-2/)
})

test('invalid or streaming structured output is not dumped into narrative', () => {
  assert.equal(answerPresentation.hideRenderedData('Unverified provisional claim: all turbines are faulty.', true), '')
  for (const text of ['```csv\na,b\n"broken', '```json\n{"broken"', '| turbine | quality |']) {
    assert.equal(answerPresentation.hideRenderedData(text, true).trim(), '')
  }
  const text = '```csv\na,b\n"broken\n```'
  assert.equal(answerPresentation.hideRenderedData(text).trim(), '')
  const html = renderToStaticMarkup(createElement(AnswerDashboard, { text }))
  assert.match(html, /invalid CSV/)
  assert.match(html, /Unparsed agent output/)
})

test('checked Markdown evidence keeps literal identifiers and punctuation in real table cells', () => {
  const parsed = answerPresentation.readAnswerDatasets('| Ref | Returned value |\n| --- | --- |\n| E1 | EQUIP\\_RTI\\_T005 |\n| E2 | \\*\\*literal\\*\\* \\`field\\` a\\|b |')
  assert.deepEqual(parsed.datasets[0].rows, [['E1', 'EQUIP_RTI_T005'], ['E2', '**literal** `field` a|b']])
})

test('raw CSV prose cannot prevent an actual source-backed time-series chart', () => {
  const html = renderToStaticMarkup(createElement(AnswerDashboard, {
    text: 'Power output chart CSV (timestamp_utc,power_output_MW) timestamp_utc,power_output_MW 2026-10-07T14:54:22Z,999999',
    question: 'This is not a chart, just raw csv dump', steps: sourceSteps(returnedRows),
  }))
  assert.match(html, /<svg[^>]*role="img"/)
  assert.match(html, /<polyline/)
  assert.match(html, /1823\.09/)
  assert.doesNotMatch(html, /999999|No complete numeric dataset/)
  assert.match(html, /truncated result/)
  assert.match(html, /2 readings are older than 60 seconds/)
  assert.equal(requiresChartOutput('This is not a chart, just raw csv dump'), true)
  assert.equal(requiresChartOutput('Do not show a chart; return only a table.'), false)
  assert.equal(requiresChartOutput('This is not a chart request.'), false)
})

test('source-backed evidence renders a real table instead of flattened model CSV', () => {
  const html = renderToStaticMarkup(createElement(AnswerDashboard, {
    text: 'Malformed CSV prose', question: 'Show the returned readings as a table.', steps: sourceSteps(returnedRows),
  }))
  assert.match(html, /<table>/)
  assert.match(html, /<th scope="col">timestamp_utc<\/th>/)
  assert.match(html, /<td[^>]*>1,823\.09<\/td>/)
  assert.match(html, /Raw evidence CSV/)
})

test('non-numeric results remain a visible table when a chart cannot be made', () => {
  const html = renderToStaticMarkup(createElement(AnswerDashboard, {
    text: 'CSV', question: 'Show a chart.', steps: sourceSteps([{ signal: 'T005', quality: 'BAD' }]),
  }))
  assert.match(html, /<table>/)
  assert.match(html, /<td[^>]*>BAD<\/td>/)
  assert.match(html, /disabled=""[^>]*>Table \+ charts/)
  assert.doesNotMatch(html, /<svg/)
})

test('source display preserves missing values, separates units and excludes failed reads', () => {
  const records = [{ event_time: '2026-10-07T14:00:00Z', value: 10, unit: 'C', opcua_node_id: 'T005.temp' },
    { event_time: '2026-10-07T14:00:00Z', value: 100, unit: 'MW', opcua_node_id: 'T005.power' }]
  const presentation = sourcePresentation.presentSourceRows(sourceSteps(records), 'Show a chart.')
  assert.equal(presentation.visualizations.length, 2)
  assert.ok(presentation.visualizations.every(spec => !spec.inlineCsvData.includes('10,C') || !spec.inlineCsvData.includes('100,MW')))
  const missing = sourcePresentation.presentSourceRows(sourceSteps([{ timestamp: '2026-10-07T14:00:00Z', value: null }]), 'Show a chart.')
  assert.equal(missing.visualizations.length, 0)
  assert.equal(missing.datasets[0].rows[0][1], '')
  const failed = sourcePresentation.presentSourceRows([...sourceSteps(records), { tool: 'query_telemetry', status: 'error' }], 'Show a chart.')
  assert.equal(failed.datasets.length, 0)
  assert.equal(failed.visualizations.length, 0)
  const broken = sourcePresentation.presentSourceRows([{ tool: 'run_kql', status: 'done', result: '{partial' }], 'Show a chart.')
  assert.equal(broken.datasets.length, 0)
  assert.match(broken.issues[0], /unreadable/)
})

test('categorical bars retain actual asset labels instead of numeric row indices', () => {
  const html = renderToStaticMarkup(createElement(AgentVisualizationView, { spec: {
    chartType: 'bar', title: 'Open work orders', xColumn: 'asset', yColumns: ['count'],
    inlineCsvData: 'asset,count\nT008,2\nT001,1\nT015,1',
  } }))
  for (const label of ['T008', 'T001', 'T015']) assert.match(html, new RegExp(`>${label}<`))
  assert.match(html, /<title>T008: 2<\/title>/)
  assert.doesNotMatch(html, /text-anchor="middle">[01]\.5</)
})

test('dashboard uses unit-separated source charts, not a recombined numeric table', () => {
  const rows = [
    { signal: 'a / b', unit: 'c', value: 10 },
    { signal: 'a', unit: 'b / c', value: 100 },
  ]
  const source = sourcePresentation.presentSourceRows(sourceSteps(rows), 'Chart')
  assert.equal(source.visualizations.length, 2)
  const html = renderToStaticMarkup(createElement(AnswerDashboard, { text: '', question: 'Chart', steps: sourceSteps(rows) }))
  assert.ok((html.match(/<svg/g) ?? []).length >= 2)
  assert.equal((html.match(/truncated result/g) ?? []).length, 1)
})

test('failed source refresh cannot fall back to stale model tables or earlier charts', () => {
  const steps = [...sourceSteps(returnedRows), { tool: 'query_telemetry', status: 'error' }]
  const old = sourcePresentation.presentSourceRows(sourceSteps(returnedRows), 'Chart').visualizations
  for (const visualizations of [[], old]) {
    const html = renderToStaticMarkup(createElement(AnswerDashboard, {
      text: '```csv\nsignal,value\nstale,99999\n```', question: 'Chart', steps, visualizations,
    }))
    assert.doesNotMatch(html, /<svg|<table|99999|1823\.09/)
    assert.match(html, /source read failed/)
  }
})

test('chart panels are bounded without dropping table rows and timestamp uncertainty is explicit', () => {
  const rows = Array.from({ length: 20 }, (_, index) => ({ signal: `s${index}`, value: index, event_time: 'invalid' }))
  const source = sourcePresentation.presentSourceRows(sourceSteps(rows), 'Chart')
  assert.equal(source.visualizations.length, 12)
  assert.equal(source.datasets[0].rows.length, 20)
  assert.match(source.summary, /20 missing or invalid timestamps/)
  assert.match(source.summary, /limited to 12 panels/)
})

test('BAD and UNCERTAIN snapshots render tables without unrequested charts', () => {
  const steps = ['BAD', 'UNCERTAIN'].map((quality, index) => ({
    tool: 'query_signal_quality_snapshot', status: 'done',
    result: JSON.stringify({ quality_filter: quality, rows: [{
      turbine: `T00${index + 1}`, equipment_id: `E${index}`, opcua_node_id: `node${index}`,
      value: 75 + index, unit: 'C', quality, event_time: '2026-10-08T12:00:00Z',
      open_work_orders: index ? [] : [{ workOrderNumber: 'WO-1', title: 'Inspect', status: 'Planned', relation: 'same-signal' }],
    }], read_completed_at_utc: '2026-10-08T12:01:30Z' }),
  }))
  const html = renderToStaticMarkup(createElement(AnswerDashboard, {
    text: 'Detailed rows: | asset | quality | | --- | --- | | invented | BAD |',
    question: 'Which turbines had BAD or UNCERTAIN telemetry quality in the last 6 hours?', steps,
  }))
  assert.ok((html.match(/<table>/g) ?? []).length >= 3)
  assert.doesNotMatch(html, /<svg/)
  assert.match(html, /WO-1/)
  assert.match(html, /same-signal/)
  assert.doesNotMatch(html, /invented/)
  assert.ok(html.indexOf('<table>') < html.indexOf('Evidence audit'))
})

test('all row-returning tools get tables without unrequested charts, including new tools', () => {
  for (const tool of ['query_assets', 'query_operations', 'query_station_power', 'query_turbine_temperature_snapshot', 'new_inventory_tool']) {
    const html = renderToStaticMarkup(createElement(AnswerDashboard, {
      text: '', question: 'List the records.', steps: [{ tool, status: 'done',
        result: JSON.stringify({ rows: [{ asset: 'T005', available: 3 }, { asset: 'T008', available: 7 }] }) }],
    }))
    assert.match(html, /<table>/, tool)
    assert.doesNotMatch(html, /<svg/, tool)
    assert.match(html, /<td[^>]*>T005<\/td>/, tool)
  }
})

test('Data Agent Markdown and JSON records default to tables with no source steps', () => {
  for (const text of [
    'Findings: | Asset | Count | | --- | --- | | T005 | 3 | | T008 | 7 |',
    'Findings: | Asset | Count |\n| --- | --- |\n| T005 | 3 |\n| T008 | 7 |',
    '```json\n[{"Asset":"T005","Count":3},{"Asset":"T008","Count":7}]\n```',
  ]) {
    const html = renderToStaticMarkup(createElement(AnswerDashboard, { text, question: 'List open work.' }))
    assert.match(html, /<table>/)
    assert.doesNotMatch(html, /<svg/)
    assert.match(html, /T005/)
    const prose = answerPresentation.hideRenderedData(text)
    assert.doesNotMatch(prose, /\||T005|```json/)
  }
})

test('categorical data charts actual returned-record counts without inventing zero categories', () => {
  const text = '| Asset | quality |\n| --- | --- |\n| T001 | BAD |\n| T002 | UNCERTAIN |\n| T003 | BAD |'
  const dataset = answerPresentation.readAnswerDatasets(text).datasets[0]
  const charts = answerPresentation.datasetVisualizations(dataset, '')
  assert.equal(charts.length, 1)
  assert.match(charts[0].inlineCsvData, /BAD,2/)
  assert.match(charts[0].inlineCsvData, /UNCERTAIN,1/)
  assert.doesNotMatch(charts[0].inlineCsvData, /GOOD/)
  const html = renderToStaticMarkup(createElement(AnswerDashboard, { text, question: 'Show a quality chart.' }))
  assert.match(html, /<table>/)
  assert.match(html, /<svg/)
})

test('tables cite their exact receipt and unfamiliar tools invalidate earlier data on failure', () => {
  const steps = [{ tool: 'new_inventory_tool', status: 'done', result: '{"rows":[{"asset":"T005","count":2}]}' }]
  const html = renderToStaticMarkup(createElement(AnswerDashboard, { text: '', steps, receiptPrefix: 'turn-1' }))
  assert.match(html, /href="#turn-1-source-0"/)
  assert.match(html, /Source 1: new_inventory_tool/)
  const failed = sourcePresentation.presentSourceRows([...steps, { tool: 'new_inventory_tool', status: 'error' }], '')
  assert.equal(failed.datasets.length, 0)
  assert.equal(failed.invalidated, true)
})

test('ambiguous flattened tables are disclosed rather than guessing missing cells', () => {
  const text = 'Rows: | Asset | Count | | --- | --- | | T005 | | T008 | 7 |'
  const parsed = answerPresentation.readAnswerDatasets(text)
  assert.equal(parsed.datasets.length, 0)
  assert.match(parsed.issues.join(' '), /ambiguous/)
  assert.doesNotMatch(answerPresentation.hideRenderedData(text), /\|/)
  const html = renderToStaticMarkup(createElement(AnswerDashboard, { text }))
  assert.match(html, /Unparsed agent output/)
  assert.doesNotMatch(html, /<table>/)
})

test('elapsed timer distinguishes idle, running and completed states', () => {
  assert.match(renderToStaticMarkup(createElement(AgentElapsedTime, { running: false })), />--</)
  assert.match(renderToStaticMarkup(createElement(AgentElapsedTime, { running: false, elapsedMs: 34100 })), />34\.1s</)
  const html = renderToStaticMarkup(createElement(AgentElapsedTime, { running: true, startedAt: Date.now() - 2000 }))
  assert.match(html, /running/)
  assert.match(html, />2\.\ds</)
})

test('time and numeric axes retain their own scales and single-string tooltips', () => {
  const chart = inlineCsvData => renderToStaticMarkup(createElement(AgentVisualizationView, { spec: {
    chartType: 'line', title: 'Readings', xColumn: 'x', yColumns: ['value'], inlineCsvData,
  } }))
  const numeric = chart('x,value\n1,4\n2,8')
  assert.match(numeric, /<title>value: 4 at 1<\/title>/)
  assert.match(numeric, /<title>1\.5<\/title>/)
  const time = chart('x,value\n2026-10-03T10:00:00Z,4\n2026-10-03T11:00:00Z,8')
  assert.match(time, /<title>value: 8 at 2026-10-03T11:00:00Z<\/title>/)
  assert.match(time, /<title>\d{1,2}:\d{2}/)
})

test('saved shipped Foundry prompt upgrades without replacing custom instructions', () => {
  const current = defaultCopilotSettings().systemPrompt
  const historical = current.replace(
    '- Queries are read-only. Work-order proposals require human review and approval in the application before any SQL write.',
    '- You are read-only. You cannot create, modify or delete anything; say so if asked.',
  )
  const legacy = historical.split('\n\nOperational counts and rankings:')[0]
  assert.equal(mergeCopilotSettings({ systemPrompt: legacy }).systemPrompt, current)
  const countVersion = historical.split('\n\nAsset resolution and latest readings:')[0]
  assert.equal(mergeCopilotSettings({ systemPrompt: countVersion }).systemPrompt, current)
  assert.equal(mergeCopilotSettings({ systemPrompt: 'My custom policy' }).systemPrompt, 'My custom policy')
  assert.match(current, /two status ne filters/)
  assert.match(current, /Draft is open/)
  assert.match(current, /EQUIP_RTI_T003 belongs in equipment\.equipment_id/)
  assert.match(current, /arg_max\(event_time, value, quality\)/)
})
