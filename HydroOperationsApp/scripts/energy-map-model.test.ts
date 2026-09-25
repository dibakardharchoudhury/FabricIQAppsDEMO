import assert from 'node:assert/strict'
import test from 'node:test'
import {
  asFeatureCollection, buildAssetMarketMessagesQuery, buildEnergyMapQuery, capacityRadius, DEFAULT_LAYERS, FEATURE_LIMIT, INITIAL_VIEW,
  isMapGeometry, MAP_LAYERS, MAP_VISIBLE_LAYERS, parseEnergyFeature, renderLayerSignature, safeSourceUrl, sourceIsStale, visibleLayerIds,
} from '../src/ui-shared/energyMapModel.ts'

test('the layer contract includes all five energy sources and no aviation layers', () => {
  assert.equal(MAP_LAYERS.length, 12)
  assert.deepEqual(MAP_LAYERS.slice(0, 6).map(layer => layer.id),
    ['transmission', 'regional', 'distribution', 'sea-cables', 'masts', 'transformers'])
  assert.equal(MAP_LAYERS.some(layer => /air|ogn|safesky/i.test(layer.id)), false)
})

test('dense nationwide layers are only queried after zooming in', () => {
  const all = MAP_LAYERS.map(layer => layer.id)
  assert.equal(visibleLayerIds(all, 4).includes('masts'), false)
  assert.equal(visibleLayerIds(all, 10).includes('distribution'), false)
  assert.equal(visibleLayerIds(all, 12).includes('masts'), true)
  const query = buildEnergyMapQuery(INITIAL_VIEW, all)
  assert.ok(query.includes(`take ${FEATURE_LIMIT + 1}`))
  assert.ok(!query.includes("'masts'"))
  assert.ok(query.includes('max_lon >= 3'))
  assert.ok(query.includes('min_lat <= 72'))
})

test('map queries reject malformed or injected bounds', () => {
  for (const view of [
    { ...INITIAL_VIEW, west: NaN }, { ...INITIAL_VIEW, east: 200 },
    { ...INITIAL_VIEW, north: 100 }, { ...INITIAL_VIEW, zoom: Infinity },
    { ...INITIAL_VIEW, west: 40 },
  ]) assert.throws(() => buildEnergyMapQuery(view, DEFAULT_LAYERS), /Invalid/)
  assert.match(buildEnergyMapQuery(INITIAL_VIEW, []), /where false/)
})

test('map geometry preserves longitude-latitude order and rejects invalid coordinates', () => {
  assert.equal(isMapGeometry({ type: 'Point', coordinates: [5.32, 60.39] }), true)
  assert.equal(isMapGeometry({ type: 'MultiLineString', coordinates: [[[5, 60], [6, 61]]] }), true)
  assert.equal(isMapGeometry({ type: 'Point', coordinates: [185, 60] }), false)
  assert.equal(isMapGeometry({ type: 'Point', coordinates: [5, 100] }), false)
  assert.equal(isMapGeometry({ type: 'Point', coordinates: [5, null] }), false)
  assert.equal(isMapGeometry({ type: 'Point', coordinates: [[5, 60]] }), false)
  assert.equal(isMapGeometry({ type: 'MultiLineString', coordinates: [[5, 60]] }), false)
})

test('unmapped events remain records rather than being turned into fake coordinates', () => {
  const feature = parseEnergyFeature({
    feature_id: 'message:1:1', layer_id: 'umm', label: 'Outage',
    geometry_json: '', properties_json: '{"importance_bucket":null}',
    observed_at: '2026-09-23T10:00:00Z', ingested_at: '2026-09-23T10:01:00Z', source_url: '',
  })
  assert.equal(feature.geometry, null)
  assert.equal(asFeatureCollection([feature]).features.length, 0)
})

test('frequency, country balance and messages are not global map markers', () => {
  const row = {
    feature_id: 'message', layer_id: 'umm', label: 'Market event',
    geometry_json: '{"type":"Point","coordinates":[5,60]}',
    properties_json: '{"map_eligible":true}', ingested_at: '2026-09-23T10:00:00Z',
  }
  const message = parseEnergyFeature(row)
  const frequency = parseEnergyFeature({ ...row, layer_id: 'grid-frequency' })
  const balance = parseEnergyFeature({ ...row, layer_id: 'power-balance' })
  assert.equal(asFeatureCollection([message, frequency, balance]).features.length, 0)
  assert.equal(MAP_VISIBLE_LAYERS.length, 9)
  assert.ok(!DEFAULT_LAYERS.includes('umm') && !DEFAULT_LAYERS.includes('grid-frequency'))
  assert.ok(!visibleLayerIds(['power-balance'], 12).length)
  assert.match(buildEnergyMapQuery(INITIAL_VIEW, ['power-balance']), /where false/)
})

test('cancelled and undatable UMMs are never plotted even when their area is known', () => {
  const row = {
    feature_id: 'message', layer_id: 'umm', label: 'Cancelled market event',
    geometry_json: '{"type":"Point","coordinates":[5,60]}',
    properties_json: '{"map_eligible":false}', ingested_at: '2026-09-23T10:00:00Z',
  }
  for (const properties of ['{}', '{"map_eligible":false}', '{"map_eligible":"true"}']) {
    assert.equal(asFeatureCollection([parseEnergyFeature({ ...row, properties_json: properties })]).features.length, 0)
  }
  assert.match(buildEnergyMapQuery(INITIAL_VIEW, ['umm']), /where false/)
})

test('plant capacity scaling stays monotonic through the global maximum', () => {
  const values = [0.001, 20, 100, 441, 500, 900, 1240].map(value => capacityRadius(value, 1240))
  assert.ok(values.every((value, i) => i === 0 || value > values[i - 1]))
  assert.equal(values.at(-1), 18)
  assert.equal(capacityRadius(null, 1240), 5)
  assert.equal(capacityRadius(50, null), 5)
})

test('transformer markers remain uniform when capacity is unavailable', () => {
  const row = { feature_id: 'station', layer_id: 'transformers', label: 'Station',
    geometry_json: '{"type":"Point","coordinates":[5,60]}', ingested_at: '2026-09-24T09:00:00Z' }
  const low = parseEnergyFeature({ ...row, properties_json: '{"voltage_kv":22}' })
  const high = parseEnergyFeature({ ...row, properties_json: '{"voltage_kv":420}' })
  assert.equal(asFeatureCollection([low]).features[0].properties?.radius, asFeatureCollection([high]).features[0].properties?.radius)
})

test('unchanged source snapshots do not require full GeoJSON worker rebuilds', () => {
  const feature = parseEnergyFeature({ feature_id: 'line', layer_id: 'transmission', label: 'Line',
    geometry_json: '{"type":"LineString","coordinates":[[5,60],[6,61]]}', properties_json: '{}', ingested_at: '2026-09-24T09:00:00Z' })
  assert.equal(renderLayerSignature([feature]), renderLayerSignature([{ ...feature }]))
  assert.notEqual(renderLayerSignature([feature]), renderLayerSignature([]))
  assert.notEqual(renderLayerSignature([feature]), renderLayerSignature([{ ...feature, ingestedAt: '2026-09-25T09:00:00Z' }]))
})

test('viewport payloads defer bulky raw properties to selected-feature reads', () => {
  const query = buildEnergyMapQuery(INITIAL_VIEW, DEFAULT_LAYERS)
  assert.match(query, /bag_remove_keys/)
  assert.ok(query.indexOf('bag_remove_keys') > query.indexOf('| take 4001'))
})

test('asset messages require an explicit asset and join the exact UMM revision', () => {
  const id = 'asset"\\quoted'
  const query = buildAssetMarketMessagesQuery({ id, layerId: 'hydro-plants' })
  assert.ok(query.includes(`asset_feature_id == ${JSON.stringify(id)}`))
  assert.ok(query.includes('asset_layer_id == "hydro-plants"'))
  assert.match(query, /join kind=inner links on \$left.feature_id == \$right.message_feature_id, message_version/)
  assert.throws(() => buildAssetMarketMessagesQuery({ id: '', layerId: 'hydro-plants' }), /Invalid/)
  assert.throws(() => buildAssetMarketMessagesQuery({ id: 'NO1', layerId: 'reservoirs' }), /Invalid/)
})

test('source age does not call old snapshots live', () => {
  const state = {
    layerId: 'grid-frequency' as const, state: 'ready' as const, lastAttemptAt: null,
    lastSuccessAt: '2026-09-23T10:00:00Z', rowCount: 1, rejectedCount: 0, unmappedCount: 0,
    message: '', sourceUrl: '',
  }
  assert.equal(sourceIsStale(state, Date.parse('2026-09-23T10:03:00Z')), true)
  assert.equal(sourceIsStale({ ...state, layerId: 'reservoirs' }, Date.parse('2026-09-24T10:00:00Z')), false)
})

test('upstream links cannot inject script or credentialed URLs', () => {
  assert.equal(safeSourceUrl('javascript:alert(1)'), undefined)
  assert.equal(safeSourceUrl('https://user:password@example.com'), undefined)
  assert.equal(safeSourceUrl('https://www.nve.no/'), 'https://www.nve.no/')
})
