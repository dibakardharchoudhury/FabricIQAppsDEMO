import assert from 'node:assert/strict'
import { test } from 'node:test'
import { collectLakehousePages, lakehouseConnectorConfig, lakehouseTimestamp,
  requireLakehouseNumber, requireLakehouseText } from '../src/services/lakehouseConnector'

test('connector exposes only scalar read selections, never server decorators', () => {
  assert.deepEqual(lakehouseConnectorConfig.operations, ['read'])
  assert.equal(lakehouseConnectorConfig.connector, 'fabric-sqlanalytics')
  assert.equal(Object.keys(lakehouseConnectorConfig.entities).length, 9)
  assert.ok(Object.values(lakehouseConnectorConfig.entities).every(fields => Array.isArray(fields) && fields.length))
})

test('Lakehouse reader returns every page including more than the old STID cap', async () => {
  const cursors: Array<string | undefined> = []
  const rows = await collectLakehousePages(async cursor => {
    cursors.push(cursor)
    return cursor ? { items: [501], hasNextPage: false }
      : { items: Array.from({ length: 500 }, (_, index) => index + 1), hasNextPage: true, endCursor: 'next' }
  })
  assert.equal(rows.length, 501)
  assert.deepEqual(cursors, [undefined, 'next'])
})

test('empty source is a real empty result; missing and cycling cursors reject partial rows', async () => {
  assert.deepEqual(await collectLakehousePages(async () => ({ items: [], hasNextPage: false })), [])
  await assert.rejects(collectLakehousePages(async () => ({ items: [1], hasNextPage: true })), /pagination/)
  await assert.rejects(collectLakehousePages(async () => ({ items: [1], hasNextPage: true, endCursor: 'same' })), /pagination/)
})

test('a failed later page never returns a success-shaped partial inventory', async () => {
  await assert.rejects(collectLakehousePages(async cursor => {
    if (cursor) throw new Error('source unavailable')
    return { items: [1], hasNextPage: true, endCursor: 'next' }
  }), /source unavailable/)
})

test('required identities, numeric coordinates and timestamps reject invalid source values', () => {
  assert.equal(requireLakehouseText('FACILITY_1', 'facility_id'), 'FACILITY_1')
  assert.equal(requireLakehouseNumber(0, 'latitude'), 0)
  assert.throws(() => requireLakehouseText(null, 'facility_id'), /facility_id/)
  assert.throws(() => requireLakehouseNumber('0', 'latitude'), /latitude/)
  assert.throws(() => requireLakehouseNumber(NaN, 'latitude'), /latitude/)
  assert.equal(lakehouseTimestamp(new Date('2026-10-23T00:00:00Z'), 'time'), '2026-10-23T00:00:00.000Z')
  assert.throws(() => lakehouseTimestamp('not-a-date', 'time'), /timestamp/)
})
