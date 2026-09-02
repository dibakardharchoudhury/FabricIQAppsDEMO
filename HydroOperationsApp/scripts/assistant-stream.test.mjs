import assert from 'node:assert/strict'
import test from 'node:test'
import { readAssistantStream } from '../src/services/assistantStream.ts'

test('streams cumulative assistant text across chunk and event boundaries', async () => {
  const encoder = new TextEncoder()
  const chunks = [
    'data: {"delta":{"content":[{"text":{"value":"Hyd',
    'ro"}}]}}\r\n\r\ndata: {"delta":{"content":[{"text":{"value":" power"}}]}}\r\n\r\n',
    'data: {"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
  ]
  const body = new ReadableStream({
    start(controller) {
      chunks.forEach(chunk => controller.enqueue(encoder.encode(chunk)))
      controller.close()
    },
  })
  const updates = []

  const answer = await readAssistantStream(body, text => updates.push(text))

  assert.deepEqual(updates, ['Hydro', 'Hydro power'])
  assert.deepEqual(answer, { text: 'Hydro power', usage: { prompt: 3, completion: 2, total: 5 } })
})