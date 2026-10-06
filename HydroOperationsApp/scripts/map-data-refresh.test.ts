import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MapDataRefreshPanel } from '../src/components/MapDataRefreshPanel.tsx'

const render = (state: 'idle' | 'running' | 'complete' | 'error', message?: string) =>
  renderToStaticMarkup(createElement(MapDataRefreshPanel, { state, message, status: 'Running', elapsed: '5m 10s', onStart: () => {} }))

test('Administration documents the existing all-source notebook and serial chat refresh', () => {
  const html = render('idle')
  assert.match(html, /Update all map data/)
  assert.match(html, /Geo_001_ingest_energy_context/)
  assert.match(html, /04_Pipe_EnergyMap/)
  assert.match(html, /Geo_002_publish_map_agent/)
  for (const source of ['NVE Nettanlegg4', 'NVE hydropower', 'NVE reservoir', 'Statnett', 'Nord Pool UMM']) {
    assert.ok(html.includes(source), source)
  }
  assert.match(html, /bypasses the 24-hour source cache/)
  assert.ok(!html.includes('disabled=""'))
})

test('active runs disable a duplicate start without inventing percentage progress', () => {
  const html = render('running', 'Refreshing map data')
  assert.match(html, /disabled=""/)
  assert.match(html, /<progress aria-label="Map data update is running"><\/progress>/)
  assert.ok(!html.includes('value="'))
  assert.match(html, /5m 10s/)
})

test('failure states are explicit and completed refreshes can be run again', () => {
  const failed = render('error', 'NVE import failed')
  assert.match(failed, /role="alert"/)
  assert.match(failed, /NVE import failed/)
  const complete = render('complete', 'All sources updated')
  assert.match(complete, /Completed/)
  assert.ok(!complete.includes('disabled=""'))
  assert.match(complete, /Synthetic demo assets and telemetry are not reset/)
})
