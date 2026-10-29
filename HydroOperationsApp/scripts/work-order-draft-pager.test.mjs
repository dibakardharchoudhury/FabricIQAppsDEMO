import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'
import * as answerPresentation from '../src/services/copilot/answerPresentation.ts'
import * as orchestration from '../src/services/copilot/orchestration.ts'
import * as suggestions from '../src/services/copilot/suggestions.ts'
const { isWorkOrderRequest } = orchestration

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
    if (specifier === '../services/copilot/orchestration') return orchestration
    if (specifier === '../services/copilot/suggestions') return suggestions
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
  assert.match(html, /Supervisor-led multi-agent team/)
  assert.doesNotMatch(html, /Supervisor · Sparky \/ Fabric IQ · Specialist collaboration/)
  assert.doesNotMatch(html, /Direct Data Agent|Foundry orchestration|Copilot options|Battle|v2-engine-toggle/)
  assert.match(html, /Ask about connected Fabric data/)
})

test('team capabilities include all roles without pretending unused specialists executed', () => {
  const html = renderToStaticMarkup(createElement(CopilotExperience, {
    messages: [], busy: false, onSend: Empty, onReset: Empty,
  }))
  assert.match(html, /<summary[^>]*aria-label="Team capabilities"/)
  assert.match(html, /Available capabilities, not a list of agents running on every request/)
  for (const member of orchestration.AGENT_CAPABILITIES) {
    assert.ok(html.includes(orchestration.AGENT_DISPLAY_NAMES[member.role]))
    assert.ok(html.includes(member.specialty.replaceAll('&', '&amp;')))
  }
  assert.equal(new Set(orchestration.AGENT_CAPABILITIES.map(member => member.role)).size, 6)
  assert.match(html, /without a specialist model call/)
  assert.match(html, /Saving requires your separate review and approval/)
  assert.doesNotMatch(html, /Conversation agent flow|On the job|Finished/)
})

test('simple factual starter does not force a native model call', () => {
  const html = renderToStaticMarkup(createElement(CopilotExperience, {
    messages: [{ role: 'agent', text: 'Ready.' }], busy: false, onSend: Empty, onReset: Empty,
  }))
  assert.match(html, /<button[^>]*>List every facility with its identifier and name\.<\/button>/)
  assert.doesNotMatch(html, /Ask Sparky \(Fabric IQ\) to list every facility/)
  assert.match(html, /Use the Fabric Data Agent for the facts/)
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

test('a completed draft request without typed cards has an explicit visual outcome', () => {
  const question = 'Which turbines had BAD telemetry quality today, and what work is already open for these? '
    + 'Based on this data, do a RCA and tell me which WOs need to be created and help me with the drafts.'
  const html = renderToStaticMarkup(createElement(AgentMessage, {
    question, message: { role: 'agent', text: 'Existing work already covers these concerns.', backendOwned: true, proposals: [] },
    streaming: false, showCrew: false,
  }))
  assert.match(html, /aria-label="Work-order draft outcome"/)
  assert.match(html, /No approval-ready work-order drafts returned/)
  assert.match(html, /No work order was created/)
  assert.doesNotMatch(html, /Yes, create work order|Review backend work-order draft/)
  const empty = renderToStaticMarkup(createElement(AgentMessage, {
    question, message: { role: 'agent', text: '', backendOwned: true, proposals: [] },
    streaming: false, showCrew: false,
  }))
  assert.match(empty, /No approval-ready work-order drafts returned/)
  assert.doesNotMatch(empty, /Thinking/)
})

test('facts, failed responses and in-flight requests do not display a completed draft outcome', () => {
  for (const props of [
    { question: 'Which work orders are already open?', streaming: false },
    { question: 'Prepare work-order drafts.', streaming: true },
    { question: 'Prepare work-order drafts.', streaming: false, failed: true },
  ]) {
    const html = renderToStaticMarkup(createElement(AgentMessage, {
      ...props, message: { role: 'agent', text: 'Current answer', failed: props.failed, backendOwned: true },
      showCrew: false,
    }))
    assert.doesNotMatch(html, /Work-order draft outcome/)
  }
})

for (const withRca of [false, true]) {
  const question = 'Which turbines had BAD telemetry quality today, and what work is already open for these? '
    + `Based on this data, ${withRca ? 'do a RCA and ' : ''}`
    + 'tell me which WOs need to be created and help me with the drafts.'
  test(`historical quality ${withRca ? 'with' : 'without'} RCA renders returned draft cards`, () => {
    const html = renderToStaticMarkup(createElement(AgentMessage, {
      question, message: { role: 'agent', text: 'Original historical quality answer.', proposals: drafts(2) },
      streaming: false, showCrew: false,
    }))
    assert.equal((html.match(/Review backend work-order draft/g) ?? []).length, 2)
    assert.doesNotMatch(html, /Work-order draft outcome|No approval-ready/)
  })
  test(`historical quality ${withRca ? 'with' : 'without'} RCA shows the precise review blocker first`, () => {
    for (const decision of ['no_draft', 'needs_clarification']) {
      const reason = decision === 'no_draft'
        ? 'WO-42 already covers the telemetry-quality investigation.'
        : 'The work-order source is unavailable; complete open-work coverage cannot be verified.'
      const text = '## Original native answer\n\nFull historical BAD telemetry answer.'
      const html = renderToStaticMarkup(createElement(AgentMessage, {
        question, message: { role: 'agent', text, workOrderReview: { decision, reason }, proposals: [] },
        streaming: false, showCrew: false,
      }))
      assert.ok(html.includes(reason))
      assert.ok(html.indexOf(reason) < html.indexOf(text))
      assert.doesNotMatch(html, /No approval-ready work-order drafts returned|Review backend work-order draft/)
      assert.match(html, /No work order was created/)
    }
  })
}
