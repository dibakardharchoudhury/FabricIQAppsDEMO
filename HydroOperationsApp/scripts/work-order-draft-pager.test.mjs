import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'
import * as answerPresentation from '../src/services/copilot/answerPresentation.ts'

const require = createRequire(import.meta.url)
const Empty = () => null
const ApprovalCard = ({ proposal }) => createElement('article', {
  'aria-label': 'Review backend work-order draft',
}, proposal.title)
function loadComponent(file, symbol) {
  const source = readFileSync(new URL(`../src/components/${file}.tsx`, import.meta.url), 'utf8')
  const code = ts.transpileModule(`${source}\nexport { ${symbol} as TestedComponent }`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText
  const exports = {}
  const componentRequire = specifier => {
    if (specifier === './WorkOrderApprovalCard') return { WorkOrderApprovalCard: ApprovalCard }
    if (specifier === './WorkOrderDraftPager') return { WorkOrderDraftPager }
    if (specifier === './AgentCrewTrace') return { AgentCrewTrace: Empty }
    if (specifier === './AnswerDashboard') return { AnswerDashboard: Empty }
    if (specifier === './AgentVisualizationView') return { AgentVisualizationView: Empty }
    if (specifier === './CopilotThinking') return { CopilotThinking: () => createElement('p', null, 'Thinking'), CopilotStreamCursor: Empty }
    if (specifier === '../ui-shared/hooks/useExpandedView') return { useExpandedView: () => ({ expanded: false, toggleExpanded: Empty }) }
    if (specifier === '../services/copilot/answerPresentation') return answerPresentation
    if (specifier === '../services/copilot/suggestions') return {}
    if (specifier === 'react-markdown') return { __esModule: true, default: ({ children }) => createElement('div', null, children) }
    if (specifier === 'remark-gfm') return { __esModule: true, default: Empty }
    return require(specifier)
  }
  new Function('require', 'exports', code)(componentRequire, exports)
  return exports.TestedComponent
}
const WorkOrderDraftPager = loadComponent('WorkOrderDraftPager', 'WorkOrderDraftPager')
const AgentMessage = loadComponent('CopilotExperience', 'AgentMessage')
const CopilotExperience = loadComponent('CopilotExperience', 'CopilotExperience')
const drafts = count => Array.from({ length: count }, (_, index) => ({
  id: `draft-${index + 1}`, equipmentId: `EQUIP_T00${index + 1}`,
  title: `Diagnostic draft ${index + 1}`, description: 'Investigate observed quality.',
  priority: 'Medium', createdAt: 0,
}))

test('Hydro Intelligence renders one Supervisor-led experience with no direct-agent or comparison selector', () => {
  const html = renderToStaticMarkup(createElement(CopilotExperience, {
    messages: [], busy: false, onSend: Empty, onReset: Empty,
  }))
  assert.equal((html.match(/<h1>Hydro Intelligence<\/h1>/g) ?? []).length, 1)
  assert.match(html, /Supervisor.*Sparky \/ Fabric IQ/)
  assert.doesNotMatch(html, /Direct Data Agent|Foundry orchestration|Copilot options|Battle|v2-engine-toggle/)
  assert.match(html, /Ask about connected Fabric data/)
})

test('no drafts render no pager; one draft needs no page controls', () => {
  assert.equal(renderToStaticMarkup(createElement(WorkOrderDraftPager, { proposals: [] })), '')
  const html = renderToStaticMarkup(createElement(WorkOrderDraftPager, { proposals: drafts(1) }))
  assert.match(html, /Work-order draft 1 of 1/)
  assert.match(html, /Diagnostic draft 1/)
  assert.doesNotMatch(html, /<nav|hidden=/)
})

test('multiple drafts have one visible card, numbered pages and bounded navigation', () => {
  const html = renderToStaticMarkup(createElement(WorkOrderDraftPager, { proposals: drafts(5) }))
  assert.match(html, /Work-order draft 1 of 5/)
  assert.equal((html.match(/<article /g) ?? []).length, 5)
  assert.equal((html.match(/hidden=""/g) ?? []).length, 4)
  assert.match(html, /aria-label="Work-order draft pages"/)
  for (let page = 1; page <= 5; page++) assert.match(html, new RegExp(`aria-label="Draft ${page} of 5"`))
  assert.match(html, /disabled="">Previous draft/)
  assert.match(html, /aria-label="Draft 1 of 5" aria-current="page"/)
  assert.doesNotMatch(html, /disabled="">Next draft/)
})

test('only cards paginate and the original backend answer follows the pager unchanged', () => {
  const text = '## Original Fabric answer\n\nT005: **BAD** quality.\n\nNo physical fault is established.'
  const html = renderToStaticMarkup(createElement(AgentMessage, {
    message: { role: 'agent', text, backendOwned: true, proposals: drafts(2) },
    streaming: false, showCrew: false,
  }))
  assert.ok(html.indexOf('</nav></section>') < html.indexOf(text))
  assert.ok(html.includes(text))
  assert.equal((html.match(/Original Fabric answer/g) ?? []).length, 1)
})

test('a proposal-only response renders cards rather than an endless thinking indicator', () => {
  const html = renderToStaticMarkup(createElement(AgentMessage, {
    message: { role: 'agent', text: '', backendOwned: true, proposals: drafts(1) },
    streaming: false, showCrew: false,
  }))
  assert.match(html, /Diagnostic draft 1/)
  assert.doesNotMatch(html, /Thinking/)
})
