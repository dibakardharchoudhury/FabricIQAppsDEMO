import assert from 'node:assert/strict'
import test from 'node:test'
import { waitForDefinitionResult } from '../src/services/ontologyDefinition.ts'

const definition = { definition: { parts: [{ path: 'database.tmdl', payloadType: 'InlineBase64', payload: 'ZGF0YWJhc2U=' }] } }
const operation = 'https://api.fabric.microsoft.com/v1/operations/op'
const accepted = (url: string, header = 'Location') => new Response(null, { status: 202, headers: { [header]: url } })

for (const [title, url, expected, header] of [
  ['plain Location', operation, `${operation}/result`, 'Location'],
  ['query-bearing Location', `${operation}?tenant=test`, `${operation}/result?tenant=test`, 'Location'],
  ['trailing slash and encoded query', `${operation}/?tenant=test&cursor=a%2Fb`, `${operation}/result?tenant=test&cursor=a%2Fb`, 'Location'],
  ['Operation-Location fallback', `${operation}?tenant=test`, `${operation}/result?tenant=test`, 'Operation-Location'],
]) {
  test(`definition LRO follows ${title} without corrupting the result URL`, async context => {
    const calls: string[] = []
    context.mock.method(globalThis, 'fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(url)
      assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer test-token')
      if (calls.length === 1) return Response.json({ status: 'Succeeded' })
      assert.equal(url, expected)
      return Response.json(definition)
    })
    assert.deepEqual(await waitForDefinitionResult(accepted(url, header), 'test-token'), definition)
    assert.deepEqual(calls, [url, expected])
  })
}

test('synchronous definition does not poll', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => { throw new Error('unexpected fetch') })
  assert.deepEqual(await waitForDefinitionResult(Response.json(definition), 'test-token'), definition)
  assert.equal(fetch.mock.callCount(), 0)
})

test('initial HTTP failure and missing operation location reject without polling', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => { throw new Error('unexpected fetch') })
  await assert.rejects(waitForDefinitionResult(new Response(null, { status: 403 }), 'test-token'), /request failed \(403\)/)
  await assert.rejects(waitForDefinitionResult(new Response(null, { status: 202 }), 'test-token'), /did not return a location/)
  assert.equal(fetch.mock.callCount(), 0)
})

for (const status of ['Failed', 'Cancelled']) {
  test(`terminal operation ${status} rejects without requesting a result`, async context => {
    const fetch = context.mock.method(globalThis, 'fetch', async () => Response.json({ status }))
    await assert.rejects(waitForDefinitionResult(accepted(`${operation}?tenant=test`), 'test-token'), new RegExp(`operation ${status}`))
    assert.equal(fetch.mock.callCount(), 1)
  })
}

test('operation HTTP and network failures are not returned as an empty definition', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 403 }))
  await assert.rejects(waitForDefinitionResult(accepted(operation), 'test-token'), /operation failed \(403\)/)
  fetch.mock.mockImplementation(async () => { throw new Error('network unavailable') })
  await assert.rejects(waitForDefinitionResult(accepted(operation), 'test-token'), /network unavailable/)
})

test('result HTTP failure and invalid result JSON propagate from the real helper', async context => {
  let calls = 0
  context.mock.method(globalThis, 'fetch', async () => {
    calls++
    return calls % 2 ? Response.json({ status: 'Completed' }) : new Response(null, { status: 500 })
  })
  await assert.rejects(waitForDefinitionResult(accepted(operation), 'test-token'), /result failed \(500\)/)
  context.mock.restoreAll()
  calls = 0
  context.mock.method(globalThis, 'fetch', async () => {
    calls++
    return calls % 2 ? Response.json({ status: 'Succeeded' }) : new Response('{invalid JSON', { status: 200 })
  })
  await assert.rejects(waitForDefinitionResult(accepted(operation), 'test-token'), SyntaxError)
})
