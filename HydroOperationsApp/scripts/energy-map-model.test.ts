import assert from 'node:assert/strict'
import test from 'node:test'
import {
  asFeatureCollection, buildEnergyMapQuery, DEFAULT_LAYERS, FEATURE_LIMIT, INITIAL_VIEW,
  isMapGeometry, MAP_LAYERS, parseEnergyFeature, safeSourceUrl, sourceIsStale, visibleLayerIds,
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

test('unranked market messages are visually distinct from low importance', () => {
  const row = {
    feature_id: 'message', layer_id: 'umm', label: 'Market event',
    geometry_json: '{"type":"Point","coordinates":[5,60]}',
    properties_json: '{"map_eligible":true}', ingested_at: '2026-09-23T10:00:00Z',
  }
  const unranked = parseEnergyFeature(row)
  const low = parseEnergyFeature({ ...row, properties_json: '{"importance_bucket":"low","map_eligible":true}' })
  assert.notEqual(asFeatureCollection([unranked]).features[0].properties?.color,
    asFeatureCollection([low]).features[0].properties?.color)
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
  assert.match(buildEnergyMapQuery(INITIAL_VIEW, ['umm']), /where layer_id != 'umm' or tobool\(parse_json\(properties_json\)\.map_eligible\) == true/)
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
