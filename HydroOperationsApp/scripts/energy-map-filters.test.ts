import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildEnergyPropertyPredicate, createEnergyPropertyFilters, ENERGY_PROPERTY_OPTIONS_QUERY,
  matchesEnergyPropertyFilters, parseEnergyPropertyOptions, propertyFilterCount,
} from '../src/ui-shared/energyMapFilters.ts'
import { asFeatureCollection, buildEnergyMapQuery, INITIAL_VIEW, parseEnergyFeature } from '../src/ui-shared/energyMapModel.ts'

test('property filters default to unrestricted and resets do not share mutable selections', () => {
  const first = createEnergyPropertyFilters()
  const second = createEnergyPropertyFilters()
  assert.equal(buildEnergyPropertyPredicate(first), '')
  assert.equal(propertyFilterCount(first), 0)
  first.hydro.owners.push('Owner A')
  assert.deepEqual(second.hydro.owners, [])
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: {} }, second), true)
})

test('hydro filters are OR within a selection and AND across properties', () => {
  const filters = createEnergyPropertyFilters()
  filters.hydro = {
    owners: ['Owner A', 'Owner B'], capacity: { min: 100, max: 500 }, inOperation: 'true',
    priceAreas: ['NO1', 'NO2'], grossHead: { min: 10, max: 300 }, plantStatus: 'Idrift',
  }
  const feature = { layerId: 'hydro-plants' as const, properties: {
    owner: 'Owner B', installed_capacity_mw: 100, in_operation: true,
    price_area: 'NO2', gross_head_m: 300, plant_status: 'Idrift',
  } }
  assert.equal(matchesEnergyPropertyFilters(feature, filters), true)
  for (const mismatch of [
    { owner: 'Owner C' }, { installed_capacity_mw: 99.99 }, { installed_capacity_mw: 501 },
    { in_operation: false }, { price_area: 'NO5' }, { gross_head_m: 301 }, { plant_status: 'Under bygging' },
  ]) assert.equal(matchesEnergyPropertyFilters({ ...feature, properties: { ...feature.properties, ...mismatch } }, filters), false)
  assert.equal(propertyFilterCount(filters), 6)
})

test('each property group leaves other selected layer types unchanged', () => {
  const filters = createEnergyPropertyFilters()
  filters.hydro.owners = ['Plant owner']
  filters.transformers = { owners: ['Grid owner'], sourceLayers: ['5'], voltage: { min: 100, max: 420 }, networkLevels: ['1', '2'] }
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: { owner: 'Plant owner' } }, filters), true)
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'transformers', properties: { owner: 'Grid owner', source_layer: 5, voltage_kv: 132, network_level: '2' } }, filters), true)
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'transformers', properties: { owner: 'Grid owner', source_layer: 5, voltage_kv: 132, network_level: '3' } }, filters), false)
  for (const layerId of ['transmission', 'masts', 'umm', 'reservoirs'] as const) {
    assert.equal(matchesEnergyPropertyFilters({ layerId, properties: { owner: 'Unrelated owner' } }, filters), true)
  }
})

test('missing measurements are not interpreted as zero or false', () => {
  const filters = createEnergyPropertyFilters()
  filters.hydro.capacity = { min: 0, max: 20 }
  for (const installed_capacity_mw of [null, undefined, '', ' ', 'unknown', NaN]) {
    assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: { installed_capacity_mw } }, filters), false)
  }
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: { installed_capacity_mw: 0 } }, filters), true)
  filters.hydro.capacity = null
  filters.hydro.inOperation = 'false'
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: { in_operation: false } }, filters), true)
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: {} }, filters), false)
  filters.hydro.inOperation = 'all'
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: {} }, filters), true)
})

test('missing categorical values can be selected explicitly', () => {
  const filters = createEnergyPropertyFilters()
  filters.hydro.owners = ['']
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: {} }, filters), true)
  assert.equal(matchesEnergyPropertyFilters({ layerId: 'hydro-plants', properties: { owner: 'Owner A' } }, filters), false)
  assert.match(buildEnergyPropertyPredicate(filters), /owner\) in \(""\)/)
})

test('map property predicates run before ordering and the feature cap', () => {
  const filters = createEnergyPropertyFilters()
  filters.hydro.owners = ['Owner A']
  filters.hydro.capacity = { min: 0.001, max: 1240 }
  filters.hydro.inOperation = 'false'
  filters.transformers.voltage = { min: 22, max: 420 }
  const query = buildEnergyMapQuery({ ...INITIAL_VIEW, zoom: 12 }, ['hydro-plants', 'transformers', 'masts'], filters)
  assert.match(query, /layer_id != 'hydro-plants' or/)
  assert.match(query, /layer_id != 'transformers' or/)
  assert.match(query, /installed_capacity_mw\) between \(0.001 \.\. 1240\)/)
  assert.match(query, /in_operation\) == false/)
  assert.ok(query.indexOf('voltage_kv') < query.indexOf('| order by'))
  assert.ok(query.indexOf('voltage_kv') < query.indexOf('| take 4001'))
  assert.ok(!buildEnergyMapQuery(INITIAL_VIEW, ['hydro-plants']).includes('map_properties'))
})

test('categorical values are quoted as data and cannot inject KQL', () => {
  const filters = createEnergyPropertyFilters()
  const name = 'Owner "A"\\branch\n| take 999999 //'
  filters.hydro.owners = [name, "O'Brien"]
  const predicate = buildEnergyPropertyPredicate(filters)
  assert.ok(predicate.includes(JSON.stringify(name)))
  assert.ok(predicate.includes(JSON.stringify("O'Brien")))
  assert.equal(predicate.split('\n').length, 1)
  filters.hydro.owners = ['x'.repeat(2049)]
  assert.throws(() => buildEnergyPropertyPredicate(filters), /Invalid/)
})

test('malformed numeric intervals never reach KQL', () => {
  for (const range of [{ min: -1, max: 2 }, { min: NaN, max: 2 }, { min: 2, max: 1 }, { min: 0, max: Infinity }]) {
    const filters = createEnergyPropertyFilters()
    filters.hydro.capacity = range
    assert.throws(() => buildEnergyMapQuery(INITIAL_VIEW, ['hydro-plants'], filters), /Invalid map property range/)
  }
})

const facetRows = () => [
  {
    layer_id: 'hydro-plants', owners: '["Owner B","Owner A",""]', price_areas: ['NO2', 'NO1'],
    plant_statuses: ['Idrift', 'Under bygging'], capacity_max: 1240, gross_head_max: 1163,
  },
  { layer_id: 'transformers', owners: ['Grid owner'], source_layers: ['5'], network_levels: ['7', '2', '1'], voltage_max: 420 },
]

test('facet metadata uses full imported layers rather than the current viewport', () => {
  assert.ok(ENERGY_PROPERTY_OPTIONS_QUERY.includes("layer_id in ('hydro-plants', 'transformers')"))
  assert.ok(!ENERGY_PROPERTY_OPTIONS_QUERY.includes('min_lon'))
  assert.ok(!ENERGY_PROPERTY_OPTIONS_QUERY.includes('geometry_json'))
  assert.ok(!ENERGY_PROPERTY_OPTIONS_QUERY.includes('| take'))
  const options = parseEnergyPropertyOptions(facetRows())
  assert.deepEqual(options.hydro.owners, ['', 'Owner A', 'Owner B'])
  assert.equal(options.hydro.capacityMax, 1240)
  assert.equal(options.hydro.grossHeadMax, 1163)
  assert.equal(options.transformers.voltageMax, 420)
  assert.deepEqual(options.transformers.networkLevels, ['1', '2', '7'])
})

test('invalid or truncated facet metadata surfaces an error rather than incomplete dropdowns', () => {
  assert.throws(() => parseEnergyPropertyOptions([]), /incomplete/)
  const excessive = facetRows()
  excessive[0].owners = JSON.stringify(Array.from({ length: 5001 }, (_, i) => `Owner ${i}`))
  assert.throws(() => parseEnergyPropertyOptions(excessive), /limit/)
  const invalid = facetRows()
  invalid[0].capacity_max = NaN
  assert.throws(() => parseEnergyPropertyOptions(invalid), /numeric limits/)
  const missing = facetRows().map(row => ({ ...row, capacity_max: null, gross_head_max: null, voltage_max: null }))
  assert.equal(parseEnergyPropertyOptions(missing).hydro.capacityMax, null)
})

test('hydro marker sizing reads the same installed-capacity field as the filters', () => {
  const feature = parseEnergyFeature({
    feature_id: 'plant', layer_id: 'hydro-plants', label: 'Plant', ingested_at: '2026-09-24T08:00:00Z',
    geometry_json: '{"type":"Point","coordinates":[5,60]}', properties_json: '{"installed_capacity_mw":400}',
  })
  assert.ok(Number(asFeatureCollection([feature]).features[0].properties?.radius) > 6)
})
