import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { collectLakehousePages, lakehouseConnectorConfig, lakehouseTimestamp,
  requireLakehouseNumber, requireLakehouseText } from '../src/services/lakehouseConnector'

test('connector exposes only scalar read selections, never server decorators', () => {
  assert.deepEqual(lakehouseConnectorConfig.operations, ['read'])
  assert.equal(lakehouseConnectorConfig.connector, 'fabric-sqlanalytics')
  assert.equal(Object.keys(lakehouseConnectorConfig.entities).length, 13)
  assert.ok(Object.values(lakehouseConnectorConfig.entities).every(fields => Array.isArray(fields) && fields.length))
})

test('ontology Lakehouse projections expose identity, declared joins and every primary scalar column', () => {
  const entities = lakehouseConnectorConfig.entities
  assert.deepEqual(entities.SilverSignalMaster, ['opcua_node_id', 'tag', 'instrument_id', 'equipment_id',
    'system_id', 'facility_id', 'unit', 'is_active', 'signal_type'])
  assert.deepEqual(entities.WeatherSources, ['source_id', 'source_name', 'source_type', 'provider',
    'license', 'endpoint', 'active', 'updated_at_utc'])
  assert.deepEqual(entities.WeatherIngestionRuns, ['run_id', 'source_id', 'source_item_id', 'data_kind',
    'reference_time_utc', 'started_at_utc', 'completed_at_utc', 'status', 'row_count', 'error_message'])
  assert.deepEqual(entities.InstrumentClassification, ['system_id', 'signal_type', 'signal_count', 'ontology_key'])
  assert.deepEqual(entities.WeatherVariables, ['variable_id', 'canonical_unit', 'canonical_name',
    'aggregation_kind', 'description', 'updated_at_utc'])
  assert.ok(entities.WeatherLocations.includes('metadata_json'))
  for (const fields of [entities.WeatherLocations, entities.WeatherAreas]) assert.ok(fields.includes('updated_at_utc'))
  for (const fields of [entities.WeatherLatestObservations, entities.WeatherLatestForecasts]) {
    assert.ok(fields.includes('ontology_key'))
    assert.ok(fields.includes('calculated_at_utc'))
  }
  const server = readFileSync(new URL('../rayfin/connectors/hydrolakehouse/schema.ts', import.meta.url), 'utf8')
  for (const [entity, fields] of Object.entries(entities)) {
    const body = server.match(new RegExp(`export class ${entity} extends Source\\([^]*?\\n\\}`))?.[0]
    assert.ok(body, entity)
    const columns = [...body.matchAll(/@\w+\([^]*?\)\s+(\w+)\?:/g)].map(match => match[1])
    assert.deepEqual(columns.sort(), [...fields].sort(), `${entity}: server/client scalar projections differ`)
    assert.match(body, /primaryKey: \[\]/)
  }
})

test('Administration has no browser seed action and connection numbering stays aligned across layouts', () => {
  const read = (file: string) => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')
  for (const file of ['App.tsx', 'ui-shared/pages/AdministrationPage.tsx', 'ui-shared/hooks/useHydroOperationsData.ts']) {
    const source = read(file)
    assert.doesNotMatch(source, /Seed operational demo data|Seed demo data|seedAndProvision|seedOperationalDataIfEmpty|runPostSeedNotebook/)
  }
  const page = read('ui-shared/pages/AdministrationPage.tsx')
  assert.deepEqual([...page.matchAll(/n: (\d+)/g)].map(match => Number(match[1])), [1, 2, 3, 4, 5])
  assert.match(page, /stream started in step 2/)
  for (const file of ['App.tsx', 'ui-v1/components/V1Shell.tsx', 'ui-v2/components/V2Shell.tsx']) {
    assert.match(read(file), /title="Step 3 · [^"]*Lakehouse/)
    assert.match(read(file), /title="Step 4 · [^"]*Eventhouse/)
  }
  assert.match(read('ui-shared/hooks/useHydroOperationsData.ts'), /resumePostSeedNotebook/)
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
