import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { CountryPowerBalanceTile } from '../src/ui-shared/components/energyMap/CountryPowerBalanceTile.tsx'
import { parseEnergyFeature } from '../src/ui-shared/energyMapModel.ts'

const now = Date.parse('2026-09-24T14:00:00Z')
const feature = parseEnergyFeature({
  feature_id: 'statnett:ProductionConsumption:NO', layer_id: 'power-balance', label: 'Norway',
  geometry_json: '', observed_at: '2026-09-24T09:45:00Z', ingested_at: '2026-09-24T11:12:30Z',
  properties_json: JSON.stringify({
    country_code: 'NO', production_mw: 16017, consumption_mw: 14213, net_exchange_mw: -1804,
    hydro_mw: 15248, wind_mw: 485, thermal_mw: 215, nuclear_mw: null,
    production_observed_at: null, consumption_observed_at: '2026-09-24T10:30:00Z', net_exchange_observed_at: null,
  }),
})

test('country balance preserves source signs and labels the country snapshot', () => {
  const html = renderToStaticMarkup(createElement(CountryPowerBalanceTile, { feature, now }))
  assert.match(html, /Country power balance/)
  assert.match(html, /Norway/)
  assert.match(html, /16,017 MW/)
  assert.match(html, /14,213 MW/)
  assert.match(html, /-1,804 MW/)
  assert.match(html, /stale snapshot/)
  assert.match(html, /independent of map filters/)
  assert.match(html, /Metric observation times may differ/)
})

test('missing balance values are not displayed as zero', () => {
  const html = renderToStaticMarkup(createElement(CountryPowerBalanceTile, {
    feature: { ...feature, properties: { country_code: 'NO', production_mw: null, consumption_mw: 0, net_exchange_mw: null } },
    now,
  }))
  assert.match(html, /<dt>Production<\/dt><dd>Unavailable<\/dd>/)
  assert.match(html, /<dt>Consumption<\/dt><dd>0 MW<\/dd>/)
  assert.match(html, /<dt>Net exchange<\/dt><dd>Unavailable<\/dd>/)
})

test('loading and failed reads have explicit distinct states', () => {
  assert.match(renderToStaticMarkup(createElement(CountryPowerBalanceTile, { now })), /Loading/)
  const html = renderToStaticMarkup(createElement(CountryPowerBalanceTile, { now, error: 'Read failed' }))
  assert.match(html, /Unavailable/)
  assert.match(html, /role="alert"/)
  assert.match(html, /Read failed/)
})
