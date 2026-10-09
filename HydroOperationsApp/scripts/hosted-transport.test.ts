import test from 'node:test'
import assert from 'node:assert/strict'
import { HostedTransport, HostedInvocationError, type SourceTokens } from '../src/services/copilot/hostedTransport.ts'

const project = 'https://test.services.ai.azure.com/api/projects/test'
const endpoint = `${project}/agents/coordinator/endpoint/protocols/invocations`
const source = { tenant_id: 'tenant', workspace_id: 'workspace', ontology_id: 'ontology',
  generation: 2 as const, configuration_digest: 'a'.repeat(64) }
const tokens = (): SourceTokens => ({ fabric: 'test-fabric', foundry: 'test-foundry',
  graphql: 'test-graphql', kusto: 'test-kusto' })
const reply = () => ({ run_id: 'run-1', source, production_write_executed: false,
  proposals: [], proposal_digests: {},
  presentation: { schema_version: 1, text: 'Backend-provided answer.', visualizations: [], execution_events: [] } })
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body),
  { status, headers: { 'Content-Type': 'application/json' } })

test('default fetch preserves the browser receiver', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async function (this: typeof globalThis, input, init) {
    assert.equal(this, globalThis)
    void input
    void init
    return response(reply())
  }
  try {
    const client = new HostedTransport(project, endpoint, source, async () => tokens())
    assert.equal((await client.run({ question: 'Browser request.' })).text, 'Backend-provided answer.')
  } finally {
    globalThis.fetch = original
  }
})

test('thin transport preserves backend output, renews leases and keeps session affinity without routing', async () => {
  const calls: Array<{ url: URL; body: Record<string, unknown> }> = []
  const leases: SourceTokens[] = []
  const client = new HostedTransport(project, endpoint, source, async () => {
    const lease = tokens()
    leases.push(lease)
    return lease
  }, async (input, init) => {
    calls.push({ url: new URL(String(input)), body: JSON.parse(String(init?.body)) })
    assert.equal(init?.cache, 'no-store')
    assert.equal(init?.redirect, 'error')
    return response(calls.at(-1)?.body.operation === 'decide'
      ? { run_id: 'run-1', proposal_id: 'draft-1', status: 'rejected', production_write_executed: false }
      : reply())
  })
  const answer = await client.run({ question: 'Operator question.' })
  assert.equal(answer.text, 'Backend-provided answer.')
  assert.deepEqual(answer.visualizations, [])
  await client.evidence(answer.runId)
  await client.decide(answer.runId, { proposal_id: 'draft-1', proposal_digest: 'b'.repeat(64), approved: false })
  assert.equal(new Set(calls.map(call => call.url.searchParams.get('agent_session_id'))).size, 1)
  assert.deepEqual(calls.map(call => call.body.operation), ['run', 'evidence', 'decide'])
  assert.equal(leases.length, 3)
  assert.ok(leases.every(lease => Object.values(lease).every(value => value === '')))
  client.reset()
  await client.run({ question: 'New chat.' })
  assert.notEqual(calls[0].url.searchParams.get('agent_session_id'), calls[3].url.searchParams.get('agent_session_id'))
})

test('streaming transport paints only backend execution events and returns the certified final answer', async () => {
  const snapshots: Array<Array<{ id: string; status: string }>> = []
  let body: Record<string, unknown> | undefined
  const chief = { id: 'run-1:chief', role: 'supervisor', status: 'running', label: 'Chief',
    detail: 'Coordinating.', timestamp: 1, agentName: 'hydro-supervisor-agent', trace: [] }
  const gauge = { id: 'run-1:gauge', role: 'qa', status: 'running', label: 'Gauge',
    detail: 'Verifying.', timestamp: 2, agentName: 'hydro-qa-agent',
    parentId: chief.id, parentCallId: 'call-1' }
  const lines = [
    { type: 'run', run_id: 'run-1', source },
    { type: 'event', event: chief },
    { type: 'event', event: gauge },
    { type: 'event', event: { ...gauge, status: 'completed', responseId: 'resp_gauge' } },
    { type: 'event', event: { ...chief, status: 'completed', responseId: 'resp_chief' } },
    { type: 'answer', answer: reply() },
  ]
  const client = new HostedTransport(project, endpoint, source, async () => tokens(), async (_input, init) => {
    body = JSON.parse(String(init?.body))
    return new Response(lines.map(line => JSON.stringify(line)).join('\n') + '\n',
      { headers: { 'Content-Type': 'application/x-ndjson' } })
  })
  const answer = await client.run({ question: 'Verify.' }, events => {
    snapshots.push(events.map(event => ({ id: event.id, status: event.status })))
  })
  assert.equal(body?.stream, true)
  assert.equal(answer.text, 'Backend-provided answer.')
  assert.deepEqual(answer.executionEvents.map(event => [event.id, event.status]),
    [['run-1:chief', 'completed'], ['run-1:gauge', 'completed']])
  assert.ok(snapshots.some(snapshot => snapshot.some(event => event.status === 'running')))
  assert.deepEqual(snapshots.at(-1), [
    { id: 'run-1:chief', status: 'completed' },
    { id: 'run-1:gauge', status: 'completed' },
  ])
})

test('foreign source, changed write boundary and malformed display never become connected answers', async () => {
  for (const changed of [
    { ...reply(), source: { ...source, workspace_id: 'foreign' } },
    { ...reply(), production_write_executed: true },
    { ...reply(), presentation: { schema_version: 2, text: 'Wrong contract.', visualizations: [] } },
  ]) {
    const lease = tokens()
    const client = new HostedTransport(project, endpoint, source, async () => lease, async () => response(changed))
    await assert.rejects(client.run({ question: 'Read.' }))
    assert.ok(Object.values(lease).every(value => value === ''))
  }
})

test('transport rejects foreign invocation targets before acquiring source credentials', () => {
  for (const url of ['http://test.services.ai.azure.com/invocations',
    'https://foreign.services.ai.azure.com/api/projects/test/agents/x/invocations',
    `${endpoint}?unexpected=value`, `${endpoint}#fragment`]) {
    assert.throws(() => new HostedTransport(project, url, source, async () => tokens()))
  }
})

test('failed or ambiguous decisions are not automatically submitted again', async () => {
  let calls = 0
  const lease = tokens()
  const client = new HostedTransport(project, endpoint, source, async () => lease, async () => {
    calls++
    throw new Error('Simulated lost acknowledgement.')
  })
  await assert.rejects(client.decide('run-1', { proposal_id: 'draft-1', proposal_digest: 'b'.repeat(64),
    approved: true, edits: { title: 'Approved', description: 'Human scope.', priority: 'Medium' } }))
  assert.equal(calls, 1)
  assert.ok(Object.values(lease).every(value => value === ''))
})

const card = () => ({ id: 'draft-1', run_id: 'run-1', source, equipment_id: 'equipment-1',
  instrument_id: null, opcua_node_id: 'node-1', title: 'Inspect signal', description: 'Human-reviewed scope.',
  priority: 'Medium', work_read_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString() })
const cardReply = () => ({ ...reply(), proposals: [card()], proposal_digests: { 'draft-1': 'b'.repeat(64) } })

test('backend cards preserve targets and send only reviewed edits or explicit rejection', async () => {
  const bodies: Record<string, unknown>[] = []
  const client = new HostedTransport(project, endpoint, source, async () => tokens(), async (_input, init) => {
    const body = JSON.parse(String(init?.body))
    bodies.push(body)
    return response(body.operation === 'decide' || body.operation === 'reconcile'
      ? { run_id: 'run-1', proposal_id: 'draft-1', status: 'rejected', production_write_executed: false }
      : cardReply())
  })
  const proposal = (await client.run({ question: 'Draft.' })).proposals[0]
  assert.equal(proposal.equipmentId, 'equipment-1')
  assert.equal(proposal.instrumentId, undefined)
  assert.equal(proposal.opcuaNodeId, 'node-1')
  await proposal.backend!.decide(false, { title: 'Ignored', description: 'Ignored', priority: 'High' })
  assert.deepEqual(bodies[1].decision,
    { proposal_id: 'draft-1', proposal_digest: 'b'.repeat(64), approved: false })
  const edits = { title: 'Reviewed', description: 'Scope', priority: 'High' as const }
  await proposal.backend!.decide(true, edits)
  assert.deepEqual(bodies[2].decision,
    { proposal_id: 'draft-1', proposal_digest: 'b'.repeat(64), approved: true, edits })
  await proposal.backend!.reconcile(true, edits)
  assert.equal(bodies[3].operation, 'reconcile')
  assert.deepEqual(bodies[3].decision, bodies[2].decision)
  client.reset()
  await assert.rejects(proposal.backend!.decide(true, edits), /reset conversation/)
  assert.equal(bodies.length, 4)
})

test('malformed, duplicate and foreign cards are rejected before human approval is offered', async () => {
  for (const changed of [
    { ...cardReply(), proposals: [{ ...card(), source: { ...source, ontology_id: 'foreign' } }] },
    { ...cardReply(), proposals: [{ ...card(), instrument_id: 1 }] },
    { ...cardReply(), proposals: [{ ...card(), run_id: 'foreign-run' }] },
    { ...cardReply(), proposal_digests: { 'draft-1': 'malformed' } },
    { ...cardReply(), proposals: [card(), card()] },
  ]) {
    const client = new HostedTransport(project, endpoint, source, async () => tokens(), async () => response(changed))
    await assert.rejects(client.run({ question: 'Draft.' }))
  }
})

test('decision receipts must match both identities; explicit no-write failures remain distinguishable', async () => {
  const decision = { proposal_id: 'draft-1', proposal_digest: 'b'.repeat(64), approved: true }
  for (const changed of [
    { run_id: 'foreign-run', proposal_id: 'draft-1', status: 'created' },
    { run_id: 'run-1', proposal_id: 'foreign-card', status: 'created' },
  ]) {
    const client = new HostedTransport(project, endpoint, source, async () => tokens(), async () => response(changed))
    await assert.rejects(client.decide('run-1', decision), /different run/)
  }
  for (const status of ['blocked', 'disabled', 'uncertain']) {
    const detail = { run_id: 'run-1', proposal_id: 'draft-1', status,
      ...(status === 'uncertain' ? {} : { production_write_executed: false }) }
    const client = new HostedTransport(project, endpoint, source, async () => tokens(),
      async () => response({ detail }, status === 'disabled' ? 503 : 409))
    await assert.rejects(client.decide('run-1', decision), error =>
      error instanceof HostedInvocationError && error.detail?.status === status)
  }
})

test('reset cannot change session affinity during credential renewal or a human decision', async () => {
  let release: ((value: SourceTokens) => void) | undefined
  const client = new HostedTransport(project, endpoint, source,
    () => new Promise(resolve => { release = resolve }), async () => response({
      run_id: 'run-1', proposal_id: 'draft-1', status: 'rejected', production_write_executed: false,
    }))
  const pending = client.decide('run-1', { proposal_id: 'draft-1', proposal_digest: 'b'.repeat(64), approved: false })
  assert.throws(() => client.reset(), /Wait/)
  release!(tokens())
  await pending
  assert.doesNotThrow(() => client.reset())
})

test('only accepted answers provide follow-up run IDs; failure and reset clear historical context', async () => {
  const bodies: Array<{ chat: Record<string, unknown> }> = []
  const client = new HostedTransport(project, endpoint, source, async () => tokens(), async (_input, init) => {
    bodies.push(JSON.parse(String(init?.body)))
    return response(bodies.length === 2 ? { ...reply(), production_write_executed: true } : reply())
  })
  await client.run({ question: 'First.' })
  await assert.rejects(client.run({ question: 'Follow up.' }))
  assert.equal(bodies[1].chat.previous_run_id, 'run-1')
  await client.run({ question: 'After failure.' })
  assert.equal(bodies[2].chat.previous_run_id, undefined)
  client.reset()
  await client.run({ question: 'New chat.' })
  assert.equal(bodies[3].chat.previous_run_id, undefined)
})

test('specialist display preserves server response identities and rejects invented execution roles', async () => {
  const event = { id: 'run-1:response-1', role: 'qa', status: 'completed', label: 'Verified receipt',
    detail: 'Version 14; measured duration 123 ms.', timestamp: Date.now(),
    agentName: 'existing-agent-fixture', responseId: 'response-1' }
  const result = reply()
  const client = new HostedTransport(project, endpoint, source, async () => tokens(), async () =>
    response({ ...result, presentation: { ...result.presentation, execution_events: [event] } }))
  assert.deepEqual((await client.run({ question: 'Read.' })).executionEvents, [event])
  const invalid = new HostedTransport(project, endpoint, source, async () => tokens(), async () =>
    response({ ...result, presentation: { ...result.presentation, execution_events: [{ ...event, role: 'made-up' }] } }))
  await assert.rejects(invalid.run({ question: 'Read.' }), /execution receipt/)
})
