import test from 'node:test'
import assert from 'node:assert/strict'
import { applicationInsightsLink, captureApplicationEvent, captureFoundryEvent, crewCommunications, executionStatus, responseTraceQuery } from '../src/services/copilot/agentTrace.ts'
import { createOrchestrationEvent } from '../src/services/copilot/orchestration.ts'

test('crew status never turns a failed or unstarted invocation into success', () => {
  assert.equal(executionStatus([]), 'idle')
  const event = createOrchestrationEvent('supervisor', 'queued', 'waiting')
  assert.equal(executionStatus([event]), 'running')
  event.status = 'error'
  assert.equal(executionStatus([event]), 'error')
  event.status = 'approval'
  assert.equal(executionStatus([event]), 'approval')
})

test('trace retains every real response identity without recording hidden reasoning or data payloads', () => {
  const event = createOrchestrationEvent('qa', 'queued', 'waiting')
  assert.equal(captureFoundryEvent(event, { type: 'response.created', response: { id: 'resp1' } }, 10), true)
  captureFoundryEvent(event, { type: 'response.created', response: { id: 'resp2' } }, 20)
  assert.deepEqual(event.responseIds, ['resp1', 'resp2'])
  assert.equal(captureFoundryEvent(event, { type: 'response.reasoning_text.delta', delta: 'private' }), false)
  assert.equal(captureFoundryEvent(event, { type: 'response.output_text.delta', delta: 'answer' }), false)
  captureFoundryEvent(event, { type: 'response.output_item.done', item: {
    type: 'mcp_call', name: 'query', server_label: 'fabriciq-ontology', output: 'sensitive rows',
  } }, 30)
  assert.doesNotMatch(JSON.stringify(event.trace), /private|sensitive rows/)
  assert.equal(event.trace?.length, 3)
  assert.equal(event.trace?.[2].source, 'foundry')
})

test('client execution is labeled distinctly from Foundry execution', () => {
  const event = createOrchestrationEvent('qa', 'running', 'running')
  captureApplicationEvent(event, 'query_assets completed: 15 rows', 'call1')
  captureFoundryEvent(event, { type: 'response.failed' })
  assert.equal(event.trace?.[0].source, 'application')
  assert.equal(event.trace?.[0].callId, 'call1')
  assert.equal(event.trace?.[1].source, 'foundry')
  assert.equal(event.trace?.[1].failed, true)
})

test('telemetry links require a resource identity and queries only include real response IDs', () => {
  assert.equal(applicationInsightsLink('https://untrusted.example'), undefined)
  assert.match(applicationInsightsLink('/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/demo/providers/microsoft.insights/components/demo') ?? '', /^https:\/\/portal.azure.com\/#resource\//)
  assert.equal(responseTraceQuery(['not-an-id']), undefined)
  const query = responseTraceQuery(['resp_real', 'resp_real', 'resp_"injected'])
  assert.match(query ?? '', /in \("resp_real"\)/)
  assert.doesNotMatch(query ?? '', /injected/)
})

test('communication flow requires a real parent and matching returned call, never inferred success', () => {
  const supervisor = createOrchestrationEvent('supervisor', 'running', 'Delegating')
  const qa = { ...createOrchestrationEvent('qa', 'running', 'Querying'), parentId: supervisor.id, parentCallId: 'call1' }
  const unused = createOrchestrationEvent('rca', 'queued', 'No parent')
  assert.deepEqual(crewCommunications([supervisor, unused]), [])
  assert.equal(crewCommunications([supervisor, qa])[0].waiting, true)
  qa.status = 'completed'
  assert.equal(crewCommunications([supervisor, qa])[0].receivedAt, undefined)
  captureApplicationEvent(supervisor, 'Unrelated result', 'call2', false, 'delegation-return')
  assert.equal(crewCommunications([supervisor, qa])[0].receivedAt, undefined)
  captureApplicationEvent(supervisor, 'Received result', 'call1', false, 'delegation-return')
  const [flow] = crewCommunications([supervisor, qa])
  assert.equal(flow.waiting, false)
  assert.equal(flow.failed, false)
  assert.equal(typeof flow.receivedAt, 'number')
  supervisor.trace!.at(-1)!.failed = true
  assert.equal(crewCommunications([supervisor, qa])[0].failed, true)
})
