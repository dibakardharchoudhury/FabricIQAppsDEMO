import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import { renderToStaticMarkup } from 'react-dom/server'
import { defaultCopilotSettings, mergeCopilotSettings } from '../src/services/copilot/settings.ts'

const require = createRequire(import.meta.url)
function component(name) {
  const source = readFileSync(new URL(`../src/components/${name}.tsx`, import.meta.url), 'utf8')
  const code = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText
  const exports = {}
  new Function('require', 'exports', code)(require, exports)
  return exports[name]
}
const AgentVisualizationView = component('AgentVisualizationView')
const AgentElapsedTime = component('AgentElapsedTime')

test('categorical bars retain actual asset labels instead of numeric row indices', () => {
  const html = renderToStaticMarkup(createElement(AgentVisualizationView, { spec: {
    chartType: 'bar', title: 'Open work orders', xColumn: 'asset', yColumns: ['count'],
    inlineCsvData: 'asset,count\nT008,2\nT001,1\nT015,1',
  } }))
  for (const label of ['T008', 'T001', 'T015']) assert.match(html, new RegExp(`>${label}<`))
  assert.match(html, /<title>T008: 2<\/title>/)
  assert.doesNotMatch(html, /text-anchor="middle">[01]\.5</)
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
  const legacy = current.split('\n\nOperational counts and rankings:')[0]
  assert.equal(mergeCopilotSettings({ systemPrompt: legacy }).systemPrompt, current)
  assert.equal(mergeCopilotSettings({ systemPrompt: 'My custom policy' }).systemPrompt, 'My custom policy')
  assert.match(current, /two status ne filters/)
  assert.match(current, /Draft is open/)
})
