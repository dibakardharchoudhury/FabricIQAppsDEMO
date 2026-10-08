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
  assert.match(html, /<th>timestamp_utc<\/th>/)
  assert.match(html, /<td[^>]*>1,823\.09<\/td>/)
  assert.match(html, /Raw evidence CSV/)
})

test('non-numeric results remain a visible table when a chart cannot be made', () => {
  const html = renderToStaticMarkup(createElement(AnswerDashboard, {
    text: 'CSV', question: 'Show a chart.', steps: sourceSteps([{ signal: 'T005', quality: 'BAD' }]),
  }))
  assert.match(html, /<table>/)
  assert.match(html, /<td[^>]*>BAD<\/td>/)
  assert.match(html, /disabled=""[^>]*>Charts/)
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
  assert.equal((html.match(/<svg/g) ?? []).length, 2)
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
  const rows = Array.from({ length: 20 }, (_, index) => ({ signal: `s${index}`, value: index, unit: 'C', event_time: 'invalid' }))
  const source = sourcePresentation.presentSourceRows(sourceSteps(rows), 'Chart')
  assert.equal(source.visualizations.length, 12)
  assert.equal(source.datasets[0].rows.length, 20)
  assert.match(source.summary, /20 missing or invalid timestamps/)
  assert.match(source.summary, /limited to 12 panels/)
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
