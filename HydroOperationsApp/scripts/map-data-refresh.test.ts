import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MapDataRefreshPanel } from '../src/components/MapDataRefreshPanel.tsx'

const render = (state: 'idle' | 'running' | 'complete' | 'error', message?: string) =>
  renderToStaticMarkup(createElement(MapDataRefreshPanel, { state, message, status: 'Running', elapsed: '5m 10s', onStart: () => {}, onRebuild: () => {} }))

test('Administration separates initial setup, operational refresh and advanced national rebuild', () => {
  const html = render('idle')
  assert.match(html, /Refresh operational snapshots/)
  assert.match(html, /Initial preparation is step 6/)
  assert.match(html, /skips the large national network/)
  assert.match(html, /04_Pipe_EnergyMap/)
  assert.match(html, /Agent configuration is published only when changed/)
  assert.match(html, /<summary>Advanced: full national source rebuild<\/summary>/)
  assert.match(html, /Rebuild all map sources/)
  for (const source of ['NVE Nettanlegg4', 'NVE hydropower', 'NVE reservoir', 'Statnett', 'Nord Pool UMM']) {
    assert.ok(html.includes(source), source)
  }
  assert.match(html, /bypasses the 24-hour source cache/)
  assert.ok(!html.includes('disabled=""'))
})

test('active runs disable a duplicate start without inventing percentage progress', () => {
  const html = render('running', 'Refreshing map data')
  assert.match(html, /disabled=""/)
  assert.equal((html.match(/disabled=""/g) ?? []).length, 2)
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
