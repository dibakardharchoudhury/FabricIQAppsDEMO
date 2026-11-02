import assert from 'node:assert/strict'
import test from 'node:test'
import {
  asFeatureCollection, validateEnergyMapRequest, prepareAreaGeometry, formatCapacityPower, INITIAL_VIEW,
  parseEnergyFeature, RESERVOIR_AREAS, visiblePlantCapacity,
} from '../src/ui-shared/energyMapModel.ts'

function plant(id: string, capacity: unknown, layer = 'hydro-plants') {
  return parseEnergyFeature({
    feature_id: id, layer_id: layer, label: id, geometry_json: '{"type":"Point","coordinates":[10,60]}',
    properties_json: JSON.stringify({ installed_capacity_mw: capacity }), ingested_at: '2026-09-24T12:00:00Z',
  })
}

test('indexed ring containment preserves dense outer and hole boundaries across vertical buckets', () => {
  const rectangle = (minimum: number, maximum: number) => {
    const ring: number[][] = [], step = (maximum - minimum) / 64
    for (let i = 0; i < 64; i++) ring.push([minimum + i * step, minimum])
    for (let i = 0; i < 64; i++) ring.push([maximum, minimum + i * step])
    for (let i = 0; i < 64; i++) ring.push([maximum - i * step, maximum])
    for (let i = 0; i < 64; i++) ring.push([minimum, maximum - i * step])
    return [...ring, ring[0]]
  }
  const inside = prepareAreaGeometry({ type: 'Polygon', coordinates: [rectangle(-10, 10), rectangle(-2, 2)] })
  for (let x = -11; x <= 11; x += 0.5) {
    for (let y = -11; y <= 11; y += 0.5) {
      const expected = x >= -10 && x <= 10 && y >= -10 && y <= 10 && !(x > -2 && x < 2 && y > -2 && y < 2)
      assert.equal(inside([x, y]), expected, `Point ${x},${y}`)
    }
  }
  for (let bucket = 0; bucket <= 17; bucket++) {
    const y = -10 + bucket * 20 / 17
    assert.equal(inside([-10, y]), true)
    assert.equal(inside([10, y]), true)
    assert.equal(inside([11, y]), false)
  }
})

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

test('area geometry handles holes, boundaries and multiple polygons', () => {
  const outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
  const hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]
  const testPoint = prepareAreaGeometry({ type: 'MultiPolygon', coordinates: [[outer, hole], [[[20, 0], [21, 0], [21, 1], [20, 1], [20, 0]]]] })
  assert.equal(testPoint([1, 1]), true)
  assert.equal(testPoint([3, 3]), false)
  assert.equal(testPoint([2, 3]), true)
  assert.equal(testPoint([20.5, 0.5]), true)
  assert.equal(testPoint([15, 1]), false)
  assert.equal(testPoint([NaN, 1]), false)
})

test('all areas is unrestricted while no selected areas hides only the target assets', () => {
  assert.doesNotThrow(() => validateEnergyMapRequest(INITIAL_VIEW, ['hydro-plants', 'transmission'], undefined, null))
  assert.doesNotThrow(() => validateEnergyMapRequest(INITIAL_VIEW, ['hydro-plants', 'transmission'], undefined, []))
})

test('area codes are allowlisted and duplicates cannot inflate geometry coverage', () => {
  assert.doesNotThrow(() => validateEnergyMapRequest(INITIAL_VIEW, ['hydro-plants'], undefined, ['NO1', 'NO1']))
  assert.throws(() => validateEnergyMapRequest(INITIAL_VIEW, ['hydro-plants'], undefined, ['NO1" | take 999'] as never), /Invalid reservoir-area/)
})

test('invalid polygon topology fails rather than admitting or hiding assets', () => {
  for (const rings of [
    [[[0, 0], [1, 0], [1, 1]]],
    [[[0, 0], [4, 4], [0, 4], [4, 0], [0, 0]]],
    [[[0, 0], [2, 0], [1, 0], [2, 2], [0, 0]]],
    [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]], [[3, 3], [4, 3], [4, 4], [3, 4], [3, 3]]],
  ]) assert.throws(() => prepareAreaGeometry({ type: 'Polygon', coordinates: rings }), /invalid/)
  assert.throws(() => prepareAreaGeometry(null), /invalid/)
})

test('MultiPolygon components may touch at a point, but cannot overlap or share edges', () => {
  const square = (x: number, y: number) => [[x, y], [x + 2, y], [x + 2, y + 2], [x, y + 2], [x, y]]
  assert.equal(prepareAreaGeometry({ type: 'MultiPolygon', coordinates: [[square(0, 0)], [square(2, 2)]] })([3, 3]), true)
  assert.throws(() => prepareAreaGeometry({ type: 'MultiPolygon', coordinates: [[square(0, 0)], [square(1, 1)]] }), /invalid/)
  assert.throws(() => prepareAreaGeometry({ type: 'MultiPolygon', coordinates: [[square(0, 0)], [square(2, 0)]] }), /invalid/)
})
