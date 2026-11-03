import assert from 'node:assert/strict'
import test from 'node:test'
import { DirectFabricConversation } from '../src/services/directFabric.ts'

const workspaceId = '11111111-1111-1111-1111-111111111111'
const dataAgentId = '22222222-2222-2222-2222-222222222222'
const endpoint = `https://api.fabric.microsoft.com/v1/mcp/workspaces/${workspaceId}/dataagents/${dataAgentId}/agent`
const question = '  List every facility with its facility_id and facility_name.\r\n  '
const answer = '  Native answer\r\n\nSource: `result_0.json` (1 row).  '
const csv = 'facility_id,facility_name\r\nF1,Native facility\r\n'
const nativeResult = () => ({ content: [
  { type: 'text', text: answer },
  { type: 'resource', resource: { uri: 'file:///result_0.csv', mimeType: 'text/csv', text: csv,
    _meta: { title: 'Original result title' } } },
] })

function fixture(override?: (request: Record<string, unknown>, signal?: AbortSignal | null) => unknown | Promise<unknown>) {
  const calls: Array<{ url: string; request: Record<string, unknown> }> = []
  let powerBiTokenRequests = 0
  let source = 'verified-1'
  const client = new DirectFabricConversation(async signal => {
    signal.throwIfAborted()
    return { key: source, workspaceId, dataAgentId, fabricToken: 'test-fabric',
      token: async () => { powerBiTokenRequests++; return 'unused-powerbi' } }
  }, async function (this: unknown, input, init) {
    assert.equal(this, globalThis, 'Browser fetch requires the global receiver, not the conversation instance.')
    const url = String(input)
    assert.equal(url, endpoint)
    assert.equal(init?.cache, 'no-store')
    assert.equal(init?.redirect, 'error')
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer test-fabric')
    const request = init?.body ? JSON.parse(String(init.body)) : {}
    calls.push({ url, request })
    const custom = await override?.(request, init?.signal)
    if (custom instanceof Response) return custom
    if (custom !== undefined) return Response.json({ jsonrpc: '2.0', id: request.id, result: custom })
    if (request.method === 'initialize') return Response.json({ jsonrpc: '2.0', id: request.id,
      result: { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'Published Data Agent', version: '1' } } })
    if (request.method === 'notifications/initialized') return new Response(null, { status: 202 })
    if (request.method === 'tools/list') return Response.json({ jsonrpc: '2.0', id: request.id,
      result: { tools: [{ name: 'Published_tool', inputSchema: { type: 'object', properties: { userQuestion: { type: 'string' } }, required: ['userQuestion'] } }] } })
    if (request.method === 'tools/call') return Response.json({ jsonrpc: '2.0', id: request.id, result: nativeResult() })
    return new Response(null, { status: 405 })
  })
  return { client, calls, powerBiTokenRequests: () => powerBiTokenRequests, source: (key: string) => { source = key } }
}

test('MCP requests preserve the browser global receiver for injected native fetch', async () => {
  const { client } = fixture()
  await client.ask(question, new AbortController().signal)
  client.reset()
})

test('active Direct entry uses official published MCP protocol only and preserves native text, CSV and evidence digest', async () => {
  const { client, calls, powerBiTokenRequests } = fixture()
  const result = await client.ask(question, new AbortController().signal)
  assert.equal(result.text, answer)
  assert.equal(result.continuationSupported, false)
  assert.equal(result.nativeResponseId, undefined)
  const hash = Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(answer))).toString('hex')
  assert.equal(result.nativeTextSha256, hash)
  assert.deepEqual(calls.map(call => call.request.method).filter(Boolean), ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])
  assert.deepEqual(calls.at(-1)?.request.params, { name: 'Published_tool', arguments: { userQuestion: question } })
  assert.equal(powerBiTokenRequests(), 0)
  assert.ok(calls.every(call => call.url === endpoint))
  const artifact = result.artifacts!.find(file => file.name === 'result_0.csv')!
  assert.equal(await (await fetch(artifact.url!)).text(), csv)
  const evidence = result.artifacts!.find(file => file.name === 'native-mcp-evidence.json')!
  assert.deepEqual(await (await fetch(evidence.url!)).json(), nativeResult())
  client.reset()
})

test('subsequent MCP questions remain exact independent requests without invented conversation IDs or context envelopes', async () => {
  const { client, calls } = fixture()
  await client.ask(question, new AbortController().signal)
  const followup = 'What about that same facility?'
  const result = await client.ask(followup, new AbortController().signal)
  assert.equal(result.continuationSupported, false)
  const invocations = calls.filter(call => call.request.method === 'tools/call')
  assert.equal(invocations.length, 2)
  assert.deepEqual(invocations[1].request.params, { name: 'Published_tool', arguments: { userQuestion: followup } })
  assert.equal(calls.filter(call => call.request.method === 'initialize').length, 2)
  client.reset()
})

test('native MCP errors and malformed question schemas never replay or route through a different backend', async () => {
  const failed = fixture(request => request.method === 'tools/call'
    ? { isError: true, content: [{ type: 'text', text: 'Original provider refusal' }] } : undefined)
  await assert.rejects(failed.client.ask(question, new AbortController().signal), /Original provider refusal/)
  assert.equal(failed.calls.filter(call => call.request.method === 'tools/call').length, 1)
  const changed = fixture(request => request.method === 'tools/list'
    ? { tools: [{ name: 'Changed', inputSchema: { type: 'object', properties: { userQuestion: { type: 'string' }, unknown: { type: 'string' } } } }] } : undefined)
  await assert.rejects(changed.client.ask(question, new AbortController().signal), /schema is unsupported/)
  assert.equal(changed.calls.filter(call => call.request.method === 'tools/call').length, 0)
})

test('Direct MCP cancellation is bounded and source changes revoke old native evidence URLs', async () => {
  const controller = new AbortController()
  const cancelled = fixture(request => {
    if (request.method === 'tools/call') { controller.abort(); throw new DOMException('Aborted', 'AbortError') }
  })
  await assert.rejects(cancelled.client.ask(question, controller.signal), { name: 'AbortError' })
  assert.equal(cancelled.calls.filter(call => call.request.method === 'tools/call').length, 1)
  const { client, source } = fixture()
  const first = await client.ask(question, new AbortController().signal)
  source('verified-2')
  await client.ask(question, new AbortController().signal)
  await assert.rejects(fetch(first.artifacts![0].url!))
  client.reset()
})

test('fetch failures identify the failed MCP phase without claiming a proven CORS block or replaying the question', async () => {
  for (const method of ['initialize', 'tools/call']) {
    const original = new TypeError('Failed to fetch')
    const { client, calls } = fixture(request => {
      if (request.method === method) throw original
    })
    await assert.rejects(client.ask(question, new AbortController().signal), error => {
      assert.ok(error instanceof Error)
      assert.equal(error.cause, original)
      assert.match(error.message, method === 'initialize' ? /initialization/ : /question/)
      assert.match(error.message, /before an HTTP response/)
      assert.doesNotMatch(error.message, /CORS.*is blocked|https:\/\//)
      return true
    })
    assert.equal(calls.filter(call => call.request.method === method).length, 1)
  }
})

test('SDK cancellation and non-network failures retain their real cause instead of becoming CORS errors', async () => {
  for (const original of [new DOMException('SDK request cancelled', 'AbortError'), new Error('Unexpected transport invariant')]) {
    const { client } = fixture(() => { throw original })
    await assert.rejects(client.ask(question, new AbortController().signal), error => {
      assert.equal(error, original)
      return true
    })
  }
})

test('visible provider HTTP failures remain HTTP failures, not speculative browser-policy errors', async () => {
  const { client } = fixture(() => new Response('Provider denied this request', { status: 403 }))
  await assert.rejects(client.ask(question, new AbortController().signal), error => {
    assert.ok(error instanceof Error)
    assert.match(error.message, /403/)
    assert.doesNotMatch(error.message, /CORS.*is blocked/)
    return true
  })
})
