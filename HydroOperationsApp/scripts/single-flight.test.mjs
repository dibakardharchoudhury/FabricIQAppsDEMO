import assert from 'node:assert/strict'
import test from 'node:test'
import { createSingleFlight } from '../src/services/singleFlight.ts'

test('shares one in-flight operation across simultaneous calls', async () => {
  let calls = 0
  let release
  const operation = createSingleFlight(async () => {
    calls += 1
    await new Promise(resolve => { release = resolve })
    return 'Completed'
  })

  const first = operation()
  const second = operation()
  assert.equal(first, second)
  assert.equal(calls, 1)

  release()
  assert.equal(await first, 'Completed')
  assert.equal(await second, 'Completed')
})

test('allows a new operation after a failure', async () => {
  let calls = 0
  const operation = createSingleFlight(async () => {
    calls += 1
    if (calls === 1) throw new Error('failed')
    return 'Completed'
  })

  await assert.rejects(operation(), /failed/)
  assert.equal(await operation(), 'Completed')
  assert.equal(calls, 2)
})