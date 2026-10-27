import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  abortableGeoRead, collectGeoRows, compactGeoRow, createGeoEnergyService, geoConnectorConfig, geoPages,
  geoPlaceCandidates, GEO_FACET_ROW_LIMIT, GEO_SEARCH_ROW_LIMIT, type GeoApi,
} from '../src/services/geoConnector.ts'
import { createEnergyPropertyFilters } from '../src/ui-shared/energyMapFilters.ts'
import { FEATURE_LIMIT, INITIAL_VIEW } from '../src/ui-shared/energyMapModel.ts'

type Row = Record<string, unknown>
type Call = { entity: string; columns: string[]; filter?: Row; first: number; cursor?: string }
const signal = () => new AbortController().signal
const compare = (a: unknown, b: unknown) => a! < b! ? -1 : a! > b! ? 1 : 0
function matches(row: Row, filter: Row = {}): boolean {
  return Object.entries(filter).every(([key, value]) => {
    if (key === 'and') return (value as Row[]).every(child => matches(row, child))
    if (key === 'or') return (value as Row[]).some(child => matches(row, child))
    return Object.entries(value as Row).every(([op, operand]) => {
      const actual = row[key]
      if (op === 'eq') return actual === operand
      if (op === 'in') return (operand as unknown[]).includes(actual)
      if (op === 'contains') return typeof actual === 'string' && actual.includes(String(operand))
      if (op === 'gte') return actual !== null && actual !== undefined && compare(actual, operand) >= 0
      if (op === 'lte') return actual !== null && actual !== undefined && compare(actual, operand) <= 0
      throw new Error(`Unsupported mock operator ${op}`)
    })
  })
}
function connector(tables: Record<string, Row[]>, pageSize = 250) {
  const calls: Call[] = []
  const cache = new Map<string, Row[]>()
  const source = new Proxy({}, {
    get: (_, entity: string) => ({
      select(columns: string[]) {
        const call: Call = { entity, columns, first: 100 }
        let order: Row = {}
        const builder = {
          where(filter: Row) { call.filter = filter; return builder },
          orderBy(value: Row) { order = value; return builder },
          first(value: number) { call.first = value; return builder },
          after(value?: string) { call.cursor = value; return builder },
          async executePaginated() {
            calls.push({ ...call })
            const key = JSON.stringify([entity, call.filter, order])
            const rows = cache.get(key) ?? (tables[entity] ?? []).filter(row => matches(row, call.filter)).sort((a, b) => {
              for (const [field, direction] of Object.entries(order)) {
                const result = compare(a[field], b[field])
                if (result) return direction === 'desc' ? -result : result
              }
              return 0
            })
            cache.set(key, rows)
            const start = Number(call.cursor ?? 0), end = start + Math.min(call.first, pageSize)
            return { items: rows.slice(start, end).map(row => Object.fromEntries(columns.map(column => [column, row[column]]))),
              hasNextPage: rows.length > end, endCursor: rows.length > end ? String(end) : undefined }
          },
        }
        return builder
      },
    }),
  }) as GeoApi
  return { service: createGeoEnergyService(source), calls }
}
function feature(id: string, layer = 'hydro-plants', properties: Row = {}, longitude = 10): Row {
  return { feature_id: id, layer_id: layer, label: id,
    geometry_json: JSON.stringify({ type: 'Point', coordinates: [longitude, 60] }),
    properties_json: JSON.stringify(properties), min_lon: longitude, max_lon: longitude, min_lat: 60, max_lat: 60,
    observed_at: '2026-10-01T00:00:00Z', ingested_at: '2026-10-01T00:00:00Z', source_url: '' }
}
function areas() {
  return ['NO', 'SE', 'FI', 'DK', 'NO1', 'NO2', 'NO3', 'NO4', 'NO5'].map((code, i) => ({
    ...feature(code, 'reservoirs', { area_code: code }),
    geometry_json: JSON.stringify({ type: 'Polygon', coordinates: [[[9 + i, 59], [11 + i, 59], [11 + i, 61], [9 + i, 61], [9 + i, 59]]] }),
  }))
}

test('Geo connector is read-only and its browser helper imports without browser globals', () => {
  assert.deepEqual(geoConnectorConfig.operations, ['read'])
  assert.equal(geoConnectorConfig.connector, 'fabric-sqlanalytics')
  assert.equal(Object.keys(geoConnectorConfig.entities).length, 4)
  const schema = readFileSync(new URL('../rayfin/connectors/hydrogeo/schema.ts', import.meta.url), 'utf8')
  assert.ok(!schema.includes('max: 8000'))
  for (const table of ['geo_map_features', 'geo_reservoir_areas', 'geo_source_status', 'geo_market_asset_links']) assert.ok(schema.includes(`table: '${table}'`))
  const browser = readFileSync(new URL('../src/services/energyMap.ts', import.meta.url), 'utf8')
  assert.equal((browser.match(/runKustoQuery\(/g) ?? []).length, 1)
  assert.match(browser, /runKustoQuery\(buildLiveFrequencyQuery/)
  assert.ok(!browser.includes('HydroGeo'))
})

test('pagination accepts short pages and fails repeated, missing and empty continuation cursors', async () => {
  assert.deepEqual(await collectGeoRows(async cursor => cursor
    ? { items: [2], hasNextPage: false }
    : { items: [1], hasNextPage: true, endCursor: 'next' }, signal(), 2), [1, 2])
  for (const page of [
    { items: [1], hasNextPage: true }, { items: [], hasNextPage: true, endCursor: 'x' },
  ]) await assert.rejects(collectGeoRows(async () => page, signal(), 10), /pagination/)
  await assert.rejects(collectGeoRows(async () => ({ items: [1], hasNextPage: true, endCursor: 'x' }), signal(), 10), /pagination/)
  await assert.rejects(collectGeoRows(async () => ({ items: [1, 2], hasNextPage: false }), signal(), 1), /bound/)
  await assert.rejects(collectGeoRows(async () => { throw new Error('SQL unavailable') }, signal(), 1), /SQL unavailable/)
})

test('abort rejects an in-flight SDK read promptly and prevents further pages', async () => {
  const controller = new AbortController()
  const pending = abortableGeoRead(() => new Promise<void>(() => {}), controller.signal)
  controller.abort(new Error('cancelled'))
  await assert.rejects(pending, /cancelled/)
  let calls = 0
  const next = new AbortController()
  await assert.rejects(async () => {
    for await (const page of geoPages(async () => {
      calls++
      return { items: [1], hasNextPage: true, endCursor: 'next' }
    }, next.signal)) {
      assert.deepEqual(page, [1])
      next.abort()
    }
  }, { name: 'AbortError' })
  assert.equal(calls, 1)
})

test('viewport uses bbox and layer filters and filters properties before the 4000-feature cap', async () => {
  const rows = Array.from({ length: 8500 }, (_, i) => feature(String(i).padStart(5, '0'), 'hydro-plants', { owner: i < 4300 ? 'no' : 'yes' }))
  rows.push(feature('outside', 'hydro-plants', { owner: 'yes' }, 100), feature('hidden', 'masts'))
  const { service, calls } = connector({ GeoMapFeatures: rows })
  const properties = createEnergyPropertyFilters()
  properties.hydro.owners = ['yes']
  const result = await service.queryEnergyMap(INITIAL_VIEW, ['hydro-plants', 'masts'], signal(), properties)
  assert.equal(result.features.length, FEATURE_LIMIT)
  assert.equal(result.features[0].id, '04300')
  assert.equal(result.truncated, true)
  assert.ok(result.features.every(row => row.properties.owner === 'yes'))
  assert.ok(calls.every(call => call.filter?.layer_id && call.filter?.min_lon && call.filter?.max_lon && call.first === 250))
  assert.equal(calls.length, 34)
})

test('layer priority and feature IDs determine the cap independent of SDK pages', async () => {
  const rows = [feature('z', 'hydro-plants'), feature('a', 'transmission'), feature('flow', 'power-flows'), feature('a', 'hydro-plants')]
  const { service } = connector({ GeoMapFeatures: rows }, 1)
  const result = await service.queryEnergyMap(INITIAL_VIEW, ['transmission', 'hydro-plants', 'power-flows'], signal())
  assert.deepEqual(result.features.map(row => row.id), ['flow', 'a', 'z', 'a'])
  assert.equal(result.truncated, false)
})

test('viewport properties are compact, details preserve JSON much larger than 8000 characters', async () => {
  const raw = 'long source detail '.repeat(1000)
  const row = feature('plant', 'hydro-plants', { installed_capacity_mw: 123, source_properties: { raw } })
  assert.equal(compactGeoRow(row).properties_json, '{"installed_capacity_mw":123}')
  const { service } = connector({ GeoMapFeatures: [row] })
  assert.equal((await service.queryEnergyMap(INITIAL_VIEW, ['hydro-plants'], signal())).features[0].properties.source_properties, undefined)
  const detail = await service.queryEnergyFeatureDetails({ id: 'plant', layerId: 'hydro-plants' }, signal())
  assert.deepEqual(detail.properties.source_properties, { raw })
})

test('area selection filters only point plants and transformers and never context lines', async () => {
  const selectedAreas = areas()
  const rows = [feature('in', 'hydro-plants'), feature('out', 'hydro-plants', {}, 20),
    { ...feature('line-plant', 'hydro-plants'), geometry_json: '{"type":"LineString","coordinates":[[10,60],[11,60]]}' },
    feature('grid', 'transformers'), feature('context', 'transmission', {}, 20)]
  const { service } = connector({ GeoMapFeatures: rows, GeoReservoirAreas: selectedAreas }, 2)
  const viewport = { ...INITIAL_VIEW, zoom: 8 }
  assert.deepEqual((await service.queryEnergyMap(viewport, ['hydro-plants', 'transformers', 'transmission'], signal(), undefined, ['NO', 'NO']))
    .features.map(row => row.id), ['in', 'grid', 'context'])
  assert.deepEqual((await service.queryEnergyMap(viewport, ['hydro-plants', 'transformers', 'transmission'], signal(), undefined, []))
    .features.map(row => row.id), ['context'])
  assert.equal((await service.queryEnergyMap(viewport, ['hydro-plants', 'transformers', 'transmission'], signal())).features.length, 5)
  const broken = connector({ GeoMapFeatures: rows, GeoReservoirAreas: selectedAreas.slice(1) }).service
  await assert.rejects(broken.queryEnergyMap(viewport, ['hydro-plants'], signal(), undefined, ['NO']), /incomplete/)
})

test('area filtering scans beyond the cap of nonmatching rows', async () => {
  const rows = Array.from({ length: 4100 }, (_, i) => feature(`a${i}`, 'hydro-plants', {}, 20))
  rows.push(feature('z', 'hydro-plants'))
  const { service } = connector({ GeoMapFeatures: rows, GeoReservoirAreas: areas() })
  const result = await service.queryEnergyMap(INITIAL_VIEW, ['hydro-plants'], signal(), undefined, ['NO'])
  assert.deepEqual(result.features.map(row => row.id), ['z'])
  assert.equal(result.truncated, false)
})

test('facets page only full hydro/transformer properties, with no geometry or national context', async () => {
  const rows = [feature('a', 'hydro-plants', { owner: 'Hydro', installed_capacity_mw: 100 }),
    feature('b', 'hydro-plants', { owner: 'Hydro 2', installed_capacity_mw: '500' }),
    feature('c', 'transformers', { owner: 'Grid', voltage_kv: 420, source_layer: 5 }),
    feature('outside', 'distribution', { owner: 'ignored' })]
  const { service, calls } = connector({ GeoMapFeatures: rows }, 1)
  const options = await service.queryEnergyPropertyOptions(signal())
  assert.deepEqual(options.hydro.owners, ['Hydro', 'Hydro 2'])
  assert.equal(options.hydro.capacityMax, 500)
  assert.deepEqual(options.transformers.sourceLayers, ['5'])
  assert.ok(calls.every(call => !call.columns.includes('geometry_json') && call.filter?.layer_id))
})

test('facets explicitly reject row overflow rather than silently truncating', async () => {
  const row = feature('plant')
  const many = Array.from({ length: GEO_FACET_ROW_LIMIT + 1 }, (_, i) => ({ ...row, feature_id: String(i) }))
  await assert.rejects(connector({ GeoMapFeatures: many }).service.queryEnergyPropertyOptions(signal()), /100000-row bound/)
})

test('case-insensitive place search works under binary SQL collation and ranks exact names first', async () => {
  const rows = [
    { ...feature('1'), label: 'SIMA', properties_json: '{"owner":"A"}' },
    { ...feature('2'), label: 'a sima plant' },
    { ...feature('3'), label: 'SiMa' },
    { ...feature('4'), label: 'not a match' },
  ]
  const { service, calls } = connector({ GeoMapFeatures: rows, GeoReservoirAreas: areas() }, 1)
  const result = await service.searchMapPlaces('sima', signal())
  assert.deepEqual(result.places.map(row => row.feature_id), ['1', '3', '2'])
  assert.equal(result.places[0].exactMatch, true)
  assert.ok(calls.filter(call => call.entity === 'GeoMapFeatures').every(call => call.filter?.and && !call.columns.includes('geometry_json')))
  assert.deepEqual((await service.searchMapPlaces('no2', signal())).places.map(row => row.feature_id), ['NO2'])
})

test('search verifies full contiguous term and case-sensitive feature IDs after server candidate filtering', async () => {
  const rows = [{ ...feature('id'), label: 'ABC followed by DEF' }, { ...feature('ExactID'), label: 'unrelated' }]
  const { service } = connector({ GeoMapFeatures: rows })
  assert.deepEqual((await service.searchMapPlaces('abcdef', signal(), 'hydro-plants')).places, [])
  assert.equal((await service.searchMapPlaces('ExactID', signal(), 'hydro-plants')).places[0].feature_id, 'ExactID')
  assert.equal((await service.searchMapPlaces('exactid', signal(), 'hydro-plants')).places.length, 0)
  const injection = '"\\| take 999'
  assert.ok(JSON.stringify(geoPlaceCandidates(injection)).includes('contains'))
})

test('case-insensitive search includes Unicode titlecase and multi-character lowercase equivalents', async () => {
  for (const [label, term] of [['Kelvin', 'kelvin'], ['ǅuro', 'ǆuro'], ['İstanbul', 'i\u0307stanbul'],
    ['I\u0307stanbul', 'i\u0307stanbul'], ['E\u0301', 'e\u0301'], ['Ås', 'ås'], ['ΟΣ', 'ος']]) {
    const rows = [{ ...feature('plant'), label }]
    const result = await connector({ GeoMapFeatures: rows }).service.searchMapPlaces(term, signal(), 'hydro-plants')
    assert.equal(result.places[0]?.label, label)
    assert.equal(result.places[0]?.exactMatch, true)
  }
})

test('search has deterministic top-ten truncation and explicit candidate overflow failure', async () => {
  const rows = Array.from({ length: 12 }, (_, i) => ({ ...feature(String(i).padStart(3, '0')), label: `Target ${i}` }))
  const result = await connector({ GeoMapFeatures: rows }).service.searchMapPlaces('target', signal(), 'hydro-plants')
  assert.equal(result.places.length, 10)
  assert.equal(result.truncated, true)
  const many = Array.from({ length: GEO_SEARCH_ROW_LIMIT + 1 }, (_, i) => ({ ...rows[0], feature_id: String(i) }))
  await assert.rejects(connector({ GeoMapFeatures: many }).service.searchMapPlaces('target', signal(), 'hydro-plants'), /10000-row bound/)
})

test('resolve uses exact table identity, verifies bounds and computes point-area memberships', async () => {
  const { service } = connector({ GeoMapFeatures: [feature('plant')], GeoReservoirAreas: areas() }, 1)
  const result = await service.resolveMapPlace({ feature_id: 'plant', layer_id: 'hydro-plants' }, signal())
  assert.deepEqual(result.areaCodes, ['NO', 'SE'])
  assert.deepEqual(result.place.bounds, [10, 60, 10, 60])
  await assert.rejects(service.resolveMapPlace({ feature_id: 'missing', layer_id: 'hydro-plants' }, signal()), /not found uniquely/)
  await assert.rejects(service.resolveMapPlace({ feature_id: '', layer_id: 'hydro-plants' }, signal()), /Invalid/)
})

test('asset links batch exact message IDs and join only exact revisions with evidence', async () => {
  const links = Array.from({ length: 10 }, (_, i) => ({
    message_feature_id: `message${i}`, message_version: 2, asset_feature_id: 'plant', asset_layer_id: 'hydro-plants',
    match_method: 'unit-owner', match_evidence_json: JSON.stringify({ unit: 'plant', owner: `Owner ${i}` }),
  }))
  const rows = links.map((link, i) => feature(String(link.message_feature_id), 'umm', { version: i === 0 ? 1 : 2 }))
  const { service, calls } = connector({ GeoMapFeatures: rows, GeoMarketAssetLinks: links }, 2)
  const messages = await service.queryAssetMarketMessages({ id: 'plant', layerId: 'hydro-plants' }, signal())
  assert.equal(messages.length, 9)
  assert.ok(!messages.some(row => row.id === 'message0'))
  assert.equal(messages[0].properties.asset_match_method, 'unit-owner')
  assert.deepEqual(messages[0].properties.asset_match_evidence, { unit: 'plant', owner: 'Owner 1' })
  assert.ok(calls.filter(call => call.entity === 'GeoMapFeatures').every(call => (call.filter?.feature_id as Row).in))
  assert.equal(calls.filter(call => call.entity === 'GeoMapFeatures').length, 5)
})

test('sources and stored snapshots stay on Geo connector and duplicate snapshots fail', async () => {
  const { service } = connector({ GeoMapFeatures: [feature('frequency', 'grid-frequency'), feature('balance', 'power-balance', { country_code: 'NO' })],
    GeoSourceStatus: [{ layer_id: 'hydro-plants', state: 'ready', row_count: 1, unmapped_count: 0, rejected_count: 0 }] })
  assert.equal((await service.queryGridFrequency(signal()))?.id, 'frequency')
  assert.equal((await service.queryCountryPowerBalance(signal()))?.properties.country_code, 'NO')
  assert.equal((await service.queryEnergySourceStatus(signal()))[0].rowCount, 1)
  await assert.rejects(connector({ GeoMapFeatures: [feature('1', 'grid-frequency'), feature('2', 'grid-frequency')] })
    .service.queryGridFrequency(signal()), /duplicate/)
})
