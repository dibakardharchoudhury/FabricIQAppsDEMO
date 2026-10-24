import assert from 'node:assert/strict'
import test from 'node:test'
import { agentDefinition, AGENT_NAMES, WEATHER_TOOLS, parseDelegation } from '../src/services/copilot/agentDefinitions.ts'
import { requiredSpecialists } from '../src/services/copilot/orchestration.ts'
import { WEATHER_ENTITIES } from '../src/services/copilot/catalog.ts'
import { DOMAIN_DATASETS, describeToolEvidence } from '../src/services/copilot/domainContract.ts'
import { collectWeatherRows, requiredEvidenceKinds, permittedEvidenceKinds, compileOrchestrationPlan, validatePlanRequirements, handle } from './local-fabric-sources.mjs'

test('weather is a distinct read-only specialist, not the legacy single-model copilot', () => {
  assert.equal(AGENT_NAMES.weather, 'hydro-weather-agent')
  const definition = agentDefinition('weather', 'test-model', 'test-connection')
  const query = definition.tools.find(tool => 'name' in tool && tool.name === 'hydro_query')
  assert.ok(query && 'parameters' in query)
  assert.deepEqual(query.parameters.properties.tool_name.enum.sort(), [...WEATHER_TOOLS].sort())
  assert.equal(parseDelegation(JSON.stringify({ specialist: 'weather', question: 'Read the forecast.', reason: 'Weather request.',
    requires_selection: false, requires_verified_issue: false })).specialist, 'weather')
})

test('weather role restrictions are enforced by the runtime, not only the model schema', async () => {
  const request = (tool_name: string) => ({ action: 'validate_hydro_query', role: 'weather',
    report: JSON.stringify({ tool_name, arguments: { entity: 'weather_forecasts' } }) })
  assert.equal((await handle(request('query_weather'))).toolName, 'query_weather')
  for (const name of ['query_operations', 'propose_work_order', 'run_kql']) {
    await assert.rejects(handle(request(name)), /not permitted/)
  }
})
test('forecast temperature stays weather while operational context retains Q&A', () => {
  const question = 'Show the weather forecast temperature and rainfall for the next 24 hours.'
  assert.deepEqual(requiredSpecialists(question), ['weather'])
  const kinds = requiredEvidenceKinds(question)
  assert.deepEqual(kinds, ['weather_locations', 'weather_forecasts'])
  const intent = {
    required_specialists: ['weather'], required_specialist_steps: { weather: 1 },
    native_sources: [], required_evidence_kinds: kinds, charts_requested: false,
  }
  const plan = compileOrchestrationPlan(question, intent)
  assert.deepEqual(plan.steps.map(step => step.specialist), ['weather'])
  const validation = validatePlanRequirements(intent.required_specialists,
    intent.required_specialist_steps, plan.steps, [], question, kinds, kinds)
  assert.deepEqual(validation.missing_specialist_steps, {})
  assert.ok(permittedEvidenceKinds(question).includes('weather_forecasts'))
  assert.ok(requiredSpecialists('Show the weather forecast and open work orders.').includes('qa'))
  assert.ok(requiredSpecialists('Show the weather forecast and open work orders.').includes('weather'))
  assert.ok(!requiredSpecialists('Show turbine temperature.').includes('weather'))
})

test('weather connector pages retain distinct vendors and issue/target identities', async () => {
  const row = { source_id: 'aurora', target_kind: 'location', target_id: 'F001',
    reference_time_utc: '2026-10-23T00:00:00Z', valid_time_utc: '2026-10-23T06:00:00Z', precipitation: 4 }
  const pages = [
    { items: [row], hasNextPage: true, endCursor: 'next' },
    { items: [{ ...row, source_id: 'ukmet', precipitation: 8 }], hasNextPage: false },
  ]
  const calls: unknown[] = []
  const builder = {
    select(columns: string[]) { assert.ok(columns.includes('reference_time_utc')); return this },
    first(size: number) { assert.equal(size, 100); return this },
    after(cursor: unknown) { calls.push(cursor); return this },
    async executePaginated() { return pages.shift() },
  }
  const client = { connectors: { hydrolakehouse: { WeatherLatestForecasts: builder } } }
  const result = await collectWeatherRows(client, 'weather_forecasts', {
    where: [{ column: 'valid_time_utc', op: 'gte', value: '2026-10-23T05:00:00Z' }],
    columns: ['source_id', 'precipitation'],
  })
  assert.deepEqual(calls, ['next'])
  assert.equal(result.total_matched, 2)
  assert.equal(result.rows.length, 2)
  assert.equal(result.rows[0].target_id, 'F001')
  assert.equal(result.rows[0].reference_time_utc, row.reference_time_utc)
  const descriptors = describeToolEvidence('query_weather', { entity: 'weather_forecasts' }, result)
  assert.equal(descriptors[0].kind, 'weather_forecasts')
  assert.equal(descriptors[0].identity_fields.length, 5)
  assert.equal(DOMAIN_DATASETS.find(item => item.key === 'weather_forecasts')?.identityFields.length, 5)
  assert.equal(WEATHER_ENTITIES.find(item => item.key === 'weather_forecasts')?.identityColumns?.length, 5)
})

test('weather connector failures and duplicate identities never become empty or partial success', async () => {
  const location = { location_id: 'F001' }
  const builder = {
    select() { return this }, first() { return this }, after() { return this },
    async executePaginated() { return { items: [location, location], hasNextPage: false } },
  }
  const client = { connectors: { hydrolakehouse: { WeatherLocations: builder } } }
  await assert.rejects(collectWeatherRows(client, 'weather_locations', {}), /duplicate row identity/)
  builder.executePaginated = async () => { throw new Error('Connector unavailable') }
  await assert.rejects(collectWeatherRows(client, 'weather_locations', {}), /Connector unavailable/)
})
