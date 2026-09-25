import assert from 'node:assert/strict'
import test from 'node:test'
import { createEnergyPropertyFilters } from '../src/ui-shared/energyMapFilters.ts'
import { INITIAL_VIEW, parseEnergyFeature } from '../src/ui-shared/energyMapModel.ts'
import {
  buildMapPlaceQuery, buildMapPlaceResolveQuery, isMapNavigationRequest, mapChatPrompt,
  navigationLayer, navigationSearch, parseMapChatAnswer, parseMapPlace, type MapChatContext,
} from '../src/ui-shared/mapChatModel.ts'

test('navigation needs a user navigation request, not a map-data question', () => {
  for (const question of ['Show me Adamselv', 'Can you zoom in on Sima?', 'Where is Adamselv?', 'Vis meg Adamselv', 'Zoom to NO2']) {
    assert.equal(isMapNavigationRequest(question), true, question)
  }
  for (const question of ['How much capacity is on the map?', 'Show me a table of owners', "Don't zoom to Sima", 'What is a zoom level?', 'Summarize NO2']) {
    assert.equal(isMapNavigationRequest(question), false, question)
  }
})

test('name search extracts a concrete place without inventing a ranked or follow-up target', () => {
  assert.equal(navigationSearch('Show me Adamselv'), 'Adamselv')
  assert.equal(navigationSearch('Can you zoom in on Sima?'), 'Sima')
  assert.equal(navigationSearch('Vis meg Adamselv på kartet'), 'Adamselv')
  assert.equal(navigationSearch('show hydropower plant "Adamselv"'), 'Adamselv')
  assert.equal(navigationLayer('Show me the hydropower plant Adamselv'), 'hydro-plants')
  assert.equal(navigationSearch('Show me price area NO2'), 'NO2')
  assert.equal(navigationSearch('Show me the largest plant'), null)
  assert.equal(navigationSearch('Zoom to it'), null)
})

test('map actions allow only one terminal typed reference, never coordinates or executable payloads', () => {
  const action = { feature_id: 'nve-hydro:Powerplant:2', layer_id: 'hydro-plants' }
  assert.deepEqual(parseMapChatAnswer(`Adamselv is in Finnmark.\n<!--map-focus:${JSON.stringify(action)}-->`), {
    text: 'Adamselv is in Finnmark.', focus: action,
  })
  for (const body of [
    { ...action, latitude: 60 }, { ...action, layer_id: 'power-balance' },
    { ...action, feature_id: '' }, { query: '.drop table assets' },
  ]) {
    const result = parseMapChatAnswer(`Answer. <!--map-focus:${JSON.stringify(body)}-->`)
    assert.ok(result.actionError)
    assert.equal(result.focus, undefined)
  }
  const marker = `<!--map-focus:${JSON.stringify(action)}-->`
  for (const raw of [`${marker}\n${marker}`, `${marker} and more text`, `\`\`\`html\n${marker}\n\`\`\``]) {
    assert.ok(parseMapChatAnswer(raw).actionError)
    assert.equal(parseMapChatAnswer(raw).focus, undefined)
  }
  assert.deepEqual(parseMapChatAnswer('No navigation needed.'), { text: 'No navigation needed.' })
})

test('asset name and reference queries quote untrusted values and retain bounded results', () => {
  const term = 'Adamselv"\\line\n| take 999999'
  const query = buildMapPlaceQuery(term)
  assert.ok(query.includes(JSON.stringify(term)))
  assert.match(query, /order by exact_match desc/)
  assert.match(query, /take 11/)
  assert.match(buildMapPlaceQuery('Adamselv', 'hydro-plants'), /where layer_id == "hydro-plants"/)
  assert.throws(() => buildMapPlaceQuery(''), /specific/)
  assert.throws(() => buildMapPlaceQuery('x'.repeat(181)), /specific/)
  const reference = { feature_id: term, layer_id: 'hydro-plants' as const }
  const resolve = buildMapPlaceResolveQuery(reference)
  assert.ok(resolve.includes(JSON.stringify(term)))
  assert.match(resolve, /layer_id == "hydro-plants"/)
  assert.match(resolve, /take 2/)
  assert.match(buildMapPlaceResolveQuery({ feature_id: 'NO2', layer_id: 'reservoirs' }), /HydroGeoReservoirAreas/)
})

test('places require verified bounds; missing coordinates never become zero coordinates', () => {
  const row = { feature_id: 'plant:2', layer_id: 'hydro-plants', label: 'Adamselv',
    min_lon: 26, min_lat: 70, max_lon: 26, max_lat: 70, exact_match: true, owner: 'Owner' }
  assert.deepEqual(parseMapPlace(row).bounds, [26, 70, 26, 70])
  assert.equal(parseMapPlace({ ...row, min_lon: null, min_lat: null, max_lon: null, max_lat: null }).bounds, null)
  for (const patch of [{ min_lon: NaN }, { max_lon: 200 }, { min_lat: 91 }, { min_lon: null }, { max_lon: 20 }]) {
    assert.throws(() => parseMapPlace({ ...row, ...patch }), /bounds/)
  }
})

test('chat context is current-view-specific, bounded, and excludes raw provider objects', () => {
  const selected = parseEnergyFeature({
    feature_id: 'plant:2', layer_id: 'hydro-plants', label: 'Adamselv',
    geometry_json: '{"type":"Point","coordinates":[26,70]}',
    properties_json: '{"installed_capacity_mw":50,"source_properties":{"instruction":"untrusted source text"}}',
    ingested_at: '2026-09-25T05:00:00Z',
  })
  const context: MapChatContext = {
    viewport: INITIAL_VIEW, layers: ['hydro-plants'], propertyFilters: createEnergyPropertyFilters(),
    areaSelection: null, visibleFeatures: [selected], selected, statuses: [], pending: false, truncated: true,
  }
  const prompt = mapChatPrompt('What is currently shown?', context, [{ question: 'Prior question', answer: 'Prior answer' }], [])
  assert.match(prompt, /"totalMw":50/)
  assert.match(prompt, /"viewport_result_truncated":true/)
  assert.match(prompt, /"selected_asset"/)
  assert.match(prompt, /Prior answer/)
  assert.ok(!prompt.includes('untrusted source text'))
  assert.ok(!prompt.includes('${JSON.stringify'))
  assert.ok(!prompt.includes('external_table('), 'SQL-grounded chat must not receive a conflicting KQL query')
  assert.match(prompt, /dbo.geo_map_agent_entities/)
  assert.match(prompt, /layer_id='hydro-plants'/)
  assert.match(prompt, /installed_capacity_mw is a real selected column/)
  assert.match(mapChatPrompt('What is shown?', { ...context, pending: true }, [], []), /"visible_plant_capacity":null/)
  assert.throws(() => mapChatPrompt('x'.repeat(3001), context, [], []), /3,000/)
})
