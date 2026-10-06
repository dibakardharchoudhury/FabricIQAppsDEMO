import assert from 'node:assert/strict'
import test from 'node:test'
import {
  asFeatureCollection, buildEnergyMapQuery, formatCapacityPower, INITIAL_VIEW,
  parseEnergyFeature, RESERVOIR_AREAS, visiblePlantCapacity,
} from '../src/ui-shared/energyMapModel.ts'

function plant(id: string, capacity: unknown, layer = 'hydro-plants') {
  return parseEnergyFeature({
    feature_id: id, layer_id: layer, label: id, geometry_json: '{"type":"Point","coordinates":[10,60]}',
    properties_json: JSON.stringify({ installed_capacity_mw: capacity }), ingested_at: '2026-09-24T12:00:00Z',
  })
}

test('each reservoir area has a distinct stable color, including no-data countries', () => {
  assert.equal(RESERVOIR_AREAS.length, 9)
  assert.equal(new Set(RESERVOIR_AREAS.map(area => area.color)).size, 9)
  for (const area of RESERVOIR_AREAS) {
    const feature = parseEnergyFeature({
      feature_id: area.code, layer_id: 'reservoirs', label: area.label,
      geometry_json: '{"type":"Polygon","coordinates":[[[9,59],[11,59],[11,61],[9,61],[9,59]]]}',
      properties_json: JSON.stringify({ area_code: area.code, has_reservoir_data: false, filling_fraction: null }),
      ingested_at: '2026-09-24T12:00:00Z',
    })
    const rendered = asFeatureCollection([feature]).features[0]
    assert.equal(rendered.properties?.color, area.color)
    assert.equal(rendered.properties?.area_code, area.code)
    assert.equal(rendered.properties?.has_reservoir_data, false)
  }
})

test('visible capacity sums unique plotted hydropower only and distinguishes missing values', () => {
  const first = plant('A', 100)
  const result = visiblePlantCapacity([
    first, { ...first }, plant('B', 200.125), plant('C', null), plant('D', -1),
    plant('station', 9999, 'transformers'), plant('flow', 9999, 'power-flows'),
    { ...plant('unmapped', 9999), geometry: null },
  ])
  assert.deepEqual(result, { totalMw: 300.125, knownPlants: 2, unknownPlants: 2, transformers: 1 })
  assert.deepEqual(visiblePlantCapacity([]), { totalMw: 0, knownPlants: 0, unknownPlants: 0, transformers: 0 })
})

test('capacity totals use power units, not energy units', () => {
  assert.equal(formatCapacityPower(999).unit, 'MW')
  assert.deepEqual(formatCapacityPower(1000), { value: '1', unit: 'GW' })
  assert.deepEqual(formatCapacityPower(1_000_000), { value: '1', unit: 'TW' })
  assert.throws(() => formatCapacityPower(NaN), /Invalid/)
  assert.throws(() => formatCapacityPower(-1), /Invalid/)
})

test('multi-area spatial filtering happens before the feature limit and retains context layers', () => {
  const query = buildEnergyMapQuery({ ...INITIAL_VIEW, zoom: 8 }, ['hydro-plants', 'transformers', 'transmission'], undefined, ['NO1', 'NO2'])
  assert.match(query, /geo_union_polygons_array/)
  assert.match(query, /area_code\) in \("NO1", "NO2"\)/)
  assert.match(query, /layer_id !in \('hydro-plants', 'transformers'\) or/)
  assert.ok(query.indexOf('geo_point_in_polygon') < query.indexOf('| take 4001'))
  assert.match(query, /area_filter_error/)
  assert.match(query, /where region_valid/)
})

test('all areas is unrestricted while no selected areas hides only the target assets', () => {
  const all = buildEnergyMapQuery(INITIAL_VIEW, ['hydro-plants', 'transmission'], undefined, null)
  assert.ok(!all.includes('selected_region'))
  const none = buildEnergyMapQuery(INITIAL_VIEW, ['hydro-plants', 'transmission'], undefined, [])
  assert.match(none, /where layer_id !in \('hydro-plants', 'transformers'\)/)
  assert.ok(!none.includes('geo_union_polygons_array'))
})

test('area codes are allowlisted and duplicates cannot inflate geometry coverage', () => {
  const query = buildEnergyMapQuery(INITIAL_VIEW, ['hydro-plants'], undefined, ['NO1', 'NO1'])
  assert.match(query, /selected_area_rows \| count\) == 1/)
  assert.throws(() => buildEnergyMapQuery(INITIAL_VIEW, ['hydro-plants'], undefined, JSON.parse('["NO1\\\" | take 999"]')), /Invalid reservoir-area/)
})
