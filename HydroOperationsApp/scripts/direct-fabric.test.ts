import assert from 'node:assert/strict'
import test from 'node:test'
import { DirectResponsesConversation as DirectFabricConversation, readNativeResponse } from '../src/services/directFabric.ts'
import { invokeVerifiedDataAgent, verifyPublishedAgentOntology } from '../src/services/artifactDiscovery.ts'

const workspaceId = '11111111-1111-1111-1111-111111111111'
const dataAgentId = '22222222-2222-2222-2222-222222222222'
const capacity = '33333333-3333-3333-3333-333333333333'
const ontologyId = '44444444-4444-4444-4444-444444444444'
const origin = `https://${capacity.replaceAll('-', '')}.pbidedicated.windows.net`
const nativeText = '  Native answer\r\n\n[Actual citation](https://example.com/evidence)\n'
const completed = (id = 'resp_1') => ({ id, status: 'completed', output: [
  { type: 'reasoning', summary: [{ text: 'Not operator evidence' }] },
  { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: nativeText, annotations: [
    { type: 'url_citation', title: 'Evidence', url: 'https://example.com/evidence' },
    { type: 'file_citation', filename: 'query-result.json', file_id: 'file_rows1' },
  ] }] },
] })

test('dedicated published ontology parts verify exact v2 identity without requiring a datasource type field', async () => {
  const part = (stage: string, value: unknown) => ({
    path: `Files/Config/${stage}/ontologies/arbitrary-display-name/ontology.json`, payloadType: 'InlineBase64',
    payload: Buffer.from(JSON.stringify(value)).toString('base64'),
  })
  const source = { artifactId: ontologyId, workspaceId, elements: [{ id: 'facilities', is_selected: true }] }
  const definition = { definition: { parts: [part('published', source)] } }
  verifyPublishedAgentOntology(definition, workspaceId, ontologyId)
  let called = false
  const result = await invokeVerifiedDataAgent({ generation: 2, workspaceId, ontologyId }, async () => definition, async () => { called = true; return 'native'; })
  assert.equal(result, 'native')
  assert.equal(called, true)
  for (const invalid of [
    { definition: { parts: [part('draft', source)] } },
    { definition: { parts: [part('published', { ...source, artifactId: dataAgentId })] } },
    { definition: { parts: [part('published', { ...source, workspaceId: dataAgentId })] } },
    { definition: { parts: [part('published', { ...source, type: 'lakehouse' })] } },
    { definition: { parts: [{ ...part('published', source), payload: Buffer.from('{broken').toString('base64') }] } },
  ]) assert.throws(() => verifyPublishedAgentOntology(invalid, workspaceId, ontologyId))
  await assert.rejects(invokeVerifiedDataAgent({ generation: '2', workspaceId, ontologyId }, async () => definition, async () => 'must not run'), /generation|v2/i)
})

function fixture(override?: (url: string, init?: RequestInit) => Response | undefined | Promise<Response | undefined>) {
  const requests: Array<{ url: string; init?: RequestInit }> = []
  let key = 'verified-source-1'
  let accessCount = 0
  const client = new DirectFabricConversation(async signal => {
    signal.throwIfAborted()
    accessCount++
    return { key, workspaceId, dataAgentId, fabricToken: 'fabric-test', token: async () => 'pbi-test' }
  }, async (input, init) => {
    const url = String(input)
    requests.push({ url, init })
    const custom = await override?.(url, init)
    if (custom) return custom
    if (url.endsWith(`/workspaces/${workspaceId}`)) return Response.json({ id: workspaceId, capacityId: capacity })
    if (url.endsWith('/clusterDetails')) return Response.json({ clusterUrl: 'https://test.analysis.windows.net/' })
    if (url.endsWith('/generatemwctokenv2')) return Response.json({ TargetUriHost: new URL(origin).hostname })
    if (url.includes('/files/')) return Response.json({ columns: ['asset'], rows: [['T1']] })
    if (url.includes('/responses')) return Response.json(completed())
    throw new Error(`Unexpected direct request: ${url}`)
  })
  return { client, requests, source: (value: string) => { key = value }, accessCount: () => accessCount }
}

test('direct execution dynamically routes verified workspace/capacity, preserves question/answer/citations/artifacts and follows up natively', async () => {
  const { client, requests, accessCount } = fixture()
  const question = '  What is BAD quality?\r\nDo not rewrite me.  '
  const answer = await client.ask(question, new AbortController().signal)
  assert.equal(answer.text, nativeText)
  assert.equal(answer.nativeResponseId, 'resp_1')
  assert.equal(answer.citations?.[0].url, 'https://example.com/evidence')
  assert.equal(answer.artifacts?.[0].name, 'query-result.json')
  const file = await fetch(answer.artifacts![0].url!)
  assert.deepEqual(await file.json(), { columns: ['asset'], rows: [['T1']] })
  await client.ask('What about that?', new AbortController().signal)
  const posts = requests.filter(request => request.url.includes('/responses?'))
  assert.equal(posts.length, 2)
  assert.deepEqual(JSON.parse(String(posts[0].init?.body)), { model: 'gpt-5.1', input: question })
  assert.deepEqual(JSON.parse(String(posts[1].init?.body)), { model: 'gpt-5.1', input: 'What about that?', previous_response_id: 'resp_1' })
  assert.ok(posts.every(request => request.url.startsWith(origin)))
  assert.ok(requests.every(request => !/foundry|services\.ai|agent=|stage=/.test(request.url)))
  assert.equal(accessCount(), 2)
  client.reset()
})

test('SSE completion ends immediately even when runtime keeps its stream open', async () => {
  let cancelled = false
  const progress: string[] = []
  const response = new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(
        'data: {"type":"response.output_text.delta","delta":"Native"}\r\n\r\n'
        + `data: ${JSON.stringify({ type: 'response.completed', response: completed() })}\r\n\r\n`,
      ))
    },
    cancel() { cancelled = true },
  }), { headers: { 'Content-Type': 'text/event-stream' } })
  const result = await readNativeResponse(response, text => progress.push(text))
  assert.equal(result.id, 'resp_1')
  assert.deepEqual(progress, ['Native'])
  assert.equal(cancelled, true)
})

test('changed verified source clears native continuation and reset revokes downloaded evidence', async () => {
  const { client, requests, source } = fixture()
  const first = await client.ask('first', new AbortController().signal)
  source('verified-source-2')
  await client.ask('second', new AbortController().signal)
  const post = requests.filter(request => request.url.includes('/responses?')).at(-1)!
  assert.equal(JSON.parse(String(post.init?.body)).previous_response_id, undefined)
  await assert.rejects(fetch(first.artifacts![0].url!))
  client.reset()
})

test('queued responses poll the same bounded native response identity, never replay the question', async () => {
  const { client, requests } = fixture(url => url.includes('/responses?')
    ? Response.json({ id: 'resp_poll', status: 'queued' }) : undefined)
  const answer = await client.ask('question', new AbortController().signal)
  assert.equal(answer.text, nativeText)
  assert.equal(requests.filter(request => request.init?.method === 'POST' && request.url.includes('/responses?')).length, 1)
  assert.ok(requests.some(request => request.url.includes('/responses/resp_poll?')))
  client.reset()
})

test('CORS/auth/provider/artifact failures are real errors without fallback or stale partial answers', async () => {
  for (const kind of ['cors', 'auth', 'provider', 'artifact'] as const) {
    const { client, requests } = fixture(url => {
      if (!url.startsWith(origin)) return
      if (kind === 'cors') throw new TypeError('Failed to fetch')
      if (kind === 'auth') return new Response('', { status: 403 })
      if (kind === 'provider') return Response.json({ id: 'resp_failed', status: 'failed', error: { message: 'private diagnostic' } })
      if (url.includes('/files/')) return new Response('', { status: 500 })
    })
    await assert.rejects(client.ask('question', new AbortController().signal), kind === 'cors'
      ? /CORS\/network[\s\S]*no Foundry fallback/i : kind === 'provider' ? /did not complete \(failed\)/ : /HTTP/)
    assert.equal(requests.filter(request => request.url.includes('/responses?')).length, 1)
    client.reset()
  }
})

test('regional ML routing preflight failure is an explicit platform boundary and cannot execute or downgrade the question', async () => {
  const { client, requests } = fixture(url => {
    if (url.endsWith('/metadata/v201606/generatemwctokenv2')) throw new TypeError('Failed to fetch')
  })
  await assert.rejects(client.ask('  Keep this native question unchanged.  ', new AbortController().signal), error => {
    assert.ok(error instanceof Error)
    assert.match(error.message, /regional ML routing failed \(POST https:\/\/test\.analysis\.windows\.net\/metadata\/v201606\/generatemwctokenv2\)/)
    assert.match(error.message, /valid delegated token cannot bypass provider CORS/)
    assert.match(error.message, /No answer was obtained; no Foundry fallback or proxy was used/)
    return true
  })
  assert.ok(requests.some(request => request.url.endsWith('/clusterDetails')))
  assert.equal(requests.filter(request => request.url.includes('/responses')).length, 0)
  assert.equal(requests.filter(request => request.url.includes('/mcp/') || request.url.includes('.services.ai.azure.com')).length, 0)
  assert.equal(requests.filter(request => request.url.endsWith('/generatemwctokenv2')).length, 1)
  client.reset()
})

test('cancelled direct requests stop before invocation and do not advance follow-up state', async () => {
  const { client, requests } = fixture()
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(client.ask('cancelled', controller.signal), { name: 'AbortError' })
  assert.equal(requests.length, 0)
})
