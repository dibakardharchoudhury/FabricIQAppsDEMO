import assert from 'node:assert/strict'
import test from 'node:test'
import { setGeoJSONSourceData } from '../src/ui-shared/components/energyMap/mapSourceUpdate.ts'

const emptyCollection = { type: 'FeatureCollection' as const, features: [] }

test('synchronous setData success does not throw or report an error', async () => {
  let received: unknown
  const errors: unknown[] = []

  assert.doesNotThrow(() => setGeoJSONSourceData({
    setData(data) {
      received = data
      return this
    },
  }, emptyCollection, reason => errors.push(reason)))

  await Promise.resolve()
  assert.equal(received, emptyCollection)
  assert.deepEqual(errors, [])
})

test('synchronous setData failure is surfaced to the error handler', () => {
  const failure = new Error('source update failed')
  const errors: unknown[] = []

  assert.doesNotThrow(() => setGeoJSONSourceData({
    setData() {
      throw failure
    },
  }, emptyCollection, reason => errors.push(reason)))

  assert.deepEqual(errors, [failure])
})

test('asynchronous setData failure is surfaced to the error handler', async () => {
  const failure = new Error('worker update failed')
  const errors: unknown[] = []

  setGeoJSONSourceData({
    setData: () => Promise.reject(failure),
  }, emptyCollection, reason => errors.push(reason))

  await Promise.resolve()
  assert.deepEqual(errors, [failure])
})

test('late asynchronous failures can still be ignored after cancellation', async () => {
  const failure = new Error('late source update failed')
  let rejectUpdate: (reason: unknown) => void = () => {}
  let active = true
  const errors: unknown[] = []

  setGeoJSONSourceData({
    setData: () => new Promise<void>((_, reject) => { rejectUpdate = reject }),
  }, emptyCollection, reason => {
    if (active) errors.push(reason)
  })

  active = false
  rejectUpdate(failure)
  await Promise.resolve()
  assert.deepEqual(errors, [])
})
