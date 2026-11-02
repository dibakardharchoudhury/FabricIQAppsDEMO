import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { buildKnowledgeGraph, knowledgeGraphScope, type KnowledgeGraph, type KnowledgeNode } from '../src/ui-shared/knowledgeGraphModel.ts'
import { projectFacilityWeather, weatherEntityKind } from '../src/ui-shared/knowledgeGraphWeather.ts'
import { knowledgeFocusElementIds, knowledgeNodeDisplayLabel, telemetryFlowEdgeIds } from '../src/ui-shared/knowledgeGraphVisuals.ts'
import { ontologyNodePresentation } from '../src/ui-shared/components/knowledgeGraph/ontologyModelPresentation.ts'

const NOW = Date.parse('2026-10-29T17:00:00Z')
const node = (id: string, type: KnowledgeNode['type'], properties: KnowledgeNode['properties'] = {}, weatherKind?: KnowledgeNode['weatherKind']): KnowledgeNode => ({
  id, entityId: id, type, weatherKind, label: id, subtitle: '', status: 'nodata', properties, provenance: `verified:${id}`, nativeOid: id,
})
const forecast = (id: string, properties: KnowledgeNode['properties'] = {}) => node(id, 'ontology', {
  source_id: 'met', target_kind: 'location', target_id: 'location',
  reference_time_utc: '2026-10-29T14:00:00Z', valid_time_utc: '2026-10-29T18:00:00Z',
  temperature: 7.5, precipitation: 0, wind_speed: 4, ...properties,
}, 'forecast')
const graph = (): KnowledgeGraph => {
  const nodes = [
    node('F1', 'facility'), node('F2', 'facility'),
    { ...node('E1', 'equipment'), facilityId: 'F1', equipmentId: 'E1' },
    node('location', 'weather', { latitude: 56.25, longitude: -4.7 }, 'location'),
    node('location2', 'weather', { latitude: 57, longitude: -4 }, 'location'),
    node('met', 'ontology', { source_name: 'Met Office', active: true }, 'source'),
    forecast('near'), forecast('later', { valid_time_utc: '2026-10-29T20:00:00Z' }),
    forecast('expired', { valid_time_utc: '2026-10-29T12:00:00Z' }),
    forecast('other', { target_id: 'location2' }),
    node('audit', 'ontology', {}, 'metadata'), node('custom-weather-named', 'ontology'),
  ]
  const links = [['location', 'F1'], ['location2', 'F2'], ['E1', 'F1'],
    ['near', 'location'], ['later', 'location'], ['expired', 'location'], ['other', 'location2'],
    ['near', 'met'], ['later', 'met'], ['other', 'met']]
  return { nodes, edges: links.map(([source, target], index) => ({ id: `edge-${index}`, source, target, type: 'contains', label: 'declared' })) }
}
const weather = (result: KnowledgeGraph, id = 'F1') => result.nodes.find(n => n.type === 'weather' && n.facilityId === id)!

test('one actual location per facility replaces weather table clusters without inventing or mutating topology', () => {
  const input = graph()
  const before = structuredClone(input)
  const output = projectFacilityWeather(input, NOW)
  assert.equal(output.nodes.filter(n => n.type === 'weather').length, 2)
  assert.equal(output.nodes.filter(n => n.weatherKind && n.weatherKind !== 'location').length, 0)
  assert.ok(output.nodes.some(n => n.id === 'custom-weather-named'))
  assert.ok(output.edges.every(e => input.edges.includes(e)))
  assert.equal(weather(output).nativeOid, 'location')
  assert.equal(weather(output).properties['Weather record'], 'near')
  assert.equal(weather(output).properties.Temperature, '7.5 °C')
  assert.equal(weather(output).properties.Precipitation, '0 mm')
  assert.deepEqual(JSON.parse(String(weather(output).properties.Geometry)), { type: 'Point', coordinates: [-4.7, 56.25] })
  assert.match(weather(output).subtitle, /Forecast.*7.5 °C/)
  assert.match(knowledgeNodeDisplayLabel(weather(output)), /7.5 °C/)
  assert.equal(ontologyNodePresentation(weather(output)).category, 'weather')
  assert.deepEqual(input, before)
})

test('selected-asset, facility and all scopes retain only the facility-specific weather', () => {
  const output = projectFacilityWeather(graph(), NOW)
  for (const scope of ['asset', 'facility'] as const) {
    const ids = knowledgeGraphScope(output, scope, 'E1', 'E1')!
    assert.ok(ids.has('location'))
    assert.ok(!ids.has('location2'))
  }
  assert.equal(knowledgeGraphScope(output, 'all'), undefined)
  const focus = knowledgeFocusElementIds(output.nodes, output.edges, 'E1')
  assert.ok(focus.has('location'), 'facility weather must not be dimmed when its turbine is selected')
  assert.ok(!focus.has('location2'))
  assert.equal(telemetryFlowEdgeIds(output.nodes, output.edges, NOW).size, 0)
})

test('multiple declared locations still yield only one best weather context for the facility', () => {
  const input = graph()
  input.nodes.push(node('extra-location', 'weather', { latitude: 56, longitude: -4 }, 'location'))
  input.edges.push({ id: 'extra', source: 'extra-location', target: 'F1', type: 'contains', label: 'declared' })
  const output = projectFacilityWeather(input, NOW)
  assert.equal(output.nodes.filter(n => n.type === 'weather' && n.facilityId === 'F1').length, 1)
  assert.equal(weather(output).id, 'location', 'a point with valid current data wins over an empty point')
})

test('recent actual observation wins over forecasts; UTC timestamps without offsets are interpreted as UTC', () => {
  const input = graph()
  input.nodes.push(node('obs', 'ontology', { source_id: 'met', observed_at_utc: '2026-10-29T16:30:00', temperature: 6 }, 'observation'))
  input.edges.push({ id: 'obs-location', source: 'obs', target: 'location', type: 'contains', label: 'declared' })
  const actual = weather(projectFacilityWeather(input, NOW))
  assert.equal(actual.properties['Weather record'], 'obs')
  assert.equal(actual.properties['Weather status'], 'Recent observation')
  assert.equal(actual.properties['Valid time (UTC)'], '2026-10-29T16:30:00.000Z')
})

test('invalid, future, stale, inactive, wrong-target and empty weather never become current data', () => {
  for (const invalid of [
    { temperature: NaN, precipitation: undefined, wind_speed: undefined },
    { valid_time_utc: '2026-10-29T12:00:00Z' },
    { reference_time_utc: '2026-10-30T00:00:00Z' },
    { reference_time_utc: '2026-10-20T00:00:00Z' },
    { valid_time_utc: 'invalid' }, { target_id: 'someone-else' },
    { valid_time_utc: '2026-11-01T00:00:00Z' },
  ]) {
    const input = graph()
    input.nodes = input.nodes.filter(n => n.weatherKind !== 'forecast')
    input.edges = input.edges.filter(e => input.nodes.some(n => n.id === e.source) && input.nodes.some(n => n.id === e.target))
    input.nodes.push(forecast('invalid', invalid))
    input.edges.push({ id: 'invalid-location', source: 'invalid', target: 'location', type: 'contains', label: 'declared' })
    const actual = weather(projectFacilityWeather(input, NOW))
    assert.equal(actual.status, 'nodata')
    assert.equal(actual.properties.Temperature, undefined)
    assert.equal(actual.subtitle, 'No current weather')
  }
  const input = graph()
  input.nodes.find(n => n.id === 'met')!.properties.active = false
  assert.equal(weather(projectFacilityWeather(input, NOW)).status, 'nodata')
})

test('unlinked or ambiguous weather locations never gain a fabricated facility relationship', () => {
  const input = graph()
  input.edges = input.edges.filter(e => e.source !== 'location')
  assert.ok(!projectFacilityWeather(input, NOW).nodes.some(n => n.id === 'location'))
  input.edges.push({ id: 'a', source: 'location', target: 'F1', type: 'contains', label: 'declared' },
    { id: 'b', source: 'location', target: 'F2', type: 'contains', label: 'declared' })
  assert.ok(!projectFacilityWeather(input, NOW).nodes.some(n => n.id === 'location'))
})

test('area fallback requires its declared location link and a valid closed WGS84 shape', () => {
  const input = graph()
  input.nodes = input.nodes.filter(n => n.weatherKind !== 'forecast')
  input.edges = input.edges.filter(e => input.nodes.some(n => n.id === e.source) && input.nodes.some(n => n.id === e.target))
  const geometry = JSON.stringify({ type: 'Polygon', coordinates: [[[-5, 56], [-4, 56], [-4, 57], [-5, 56]]] })
  input.nodes.push(node('area', 'ontology', { geometry_geojson: geometry, crs: 'EPSG:4326' }, 'area'),
    forecast('area-forecast', { target_kind: 'area', target_id: 'area' }))
  input.edges.push({ id: 'al', source: 'area', target: 'location', type: 'contains', label: 'declared' },
    { id: 'fa', source: 'area-forecast', target: 'area', type: 'contains', label: 'declared' })
  assert.equal(weather(projectFacilityWeather(input, NOW)).properties['Weather record'], 'area-forecast')
  assert.equal(weather(projectFacilityWeather(input, NOW)).properties['Area geometry'], geometry)
  input.nodes.find(n => n.id === 'area')!.properties.geometry_geojson = JSON.stringify({ type: 'Polygon', coordinates: [[[0, 0], [1, 1]]] })
  assert.equal(weather(projectFacilityWeather(input, NOW)).status, 'nodata')
})

test('expired weather is cleared on clock refresh and invalid coordinates never produce a GeoJSON point', () => {
  const input = graph()
  input.nodes.find(n => n.id === 'location')!.properties.latitude = 100
  assert.equal(weather(projectFacilityWeather(input, NOW)).properties.Geometry, undefined)
  assert.match(String(weather(projectFacilityWeather(input, NOW)).properties['Geometry status']), /invalid/)
  assert.equal(weather(projectFacilityWeather(input, NOW + 24 * 3_600_000)).status, 'nodata')
})

test('the production graph builder classifies actual weather identities and includes them in facility scope', () => {
  const entity = (name: string, key: string) => ({
    id: name, name, localName: name, entityIdParts: [key], properties: { [key]: key },
    propertyMetadata: { [key]: { id: key, dataType: 'string' } },
  })
  const result = buildKnowledgeGraph({
    nowMs: NOW, facilities: [], systems: [], equipment: [], instruments: [], telemetry: [],
    workOrders: [], inspections: [], notifications: [], models: [],
    ontology: { id: 'onto', displayName: 'Test', generation: 2,
      entityTypes: [entity('facilities', 'facility_id'), entity('WeatherLocation', 'location_id')],
      relationshipTypes: [{ id: 'r', name: 'WeatherLocation_for_facility', sourceEntityTypeId: 'WeatherLocation',
        targetEntityTypeId: 'facilities', sourceKeys: ['location_id'], targetKeys: ['facility_id'] }] },
    ontologyGraph: { ontologyId: 'onto', graphModelId: 'graph', graphModelName: 'Test',
      nodes: [{ oid: 'f', labels: ['facilities'], entityTypeId: 'facilities', properties: { facility_id: 'F1', facility_name: 'Station' } },
        { oid: 'w', labels: ['WeatherLocation'], entityTypeId: 'WeatherLocation', properties: { location_id: 'F1', location_name: 'Station', latitude: 56, longitude: -4 } }],
      edges: [{ oid: 'r1', labels: ['WeatherLocation_for_facility'], relationshipTypeId: 'r', sourceOid: 'w', targetOid: 'f', properties: {} }] },
  })
  assert.equal(result.error, undefined)
  assert.equal(weather(result).id, 'weather:F1')
  assert.ok(knowledgeGraphScope(result, 'facility', 'facility:F1')!.has('weather:F1'))
  assert.equal(weatherEntityKind({ ...entity('WeatherUnknownCustomType', 'id') }), undefined)
})

test('the badge is removed without removing signal freshness or live weather clock updates', () => {
  const page = readFileSync(new URL('../src/ui-shared/pages/KnowledgeGraphPage.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /kg-telemetry-state|graph-bound signals|OWL 2|RDF 1\.1/)
  assert.match(page, /nowMs: data\.now/)
  assert.match(page, /selectedReadingState === 'stale'/)
})
