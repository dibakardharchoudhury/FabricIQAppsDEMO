import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MapDataRefreshPanel } from '../src/components/MapDataRefreshPanel.tsx'
import { AdministrationExperience, type AdministrationStep } from '../src/components/AdministrationExperience.tsx'

const render = (state: 'idle' | 'running' | 'complete' | 'error', message?: string) =>
  renderToStaticMarkup(createElement(MapDataRefreshPanel, { state, message, status: 'Running', elapsed: '5m 10s', onRebuild: () => {} }))

test('map maintenance is compact step content, not a second map section or refresh action', () => {
  const html = render('idle')
  assert.doesNotMatch(html, /<section|<header|<h2|MAP MAINTENANCE|Ready to update|Refresh operational snapshots/)
  assert.match(html, /<details><summary>Maintenance and source details<\/summary>/)
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
  assert.equal((html.match(/disabled=""/g) ?? []).length, 1)
  assert.match(html, /<progress aria-label="Map data update is running"><\/progress>/)
  assert.ok(!html.includes('value="'))
  assert.match(html, /5m 10s/)
  assert.ok(html.indexOf('role="status"') < html.indexOf('<details>'))
})

test('failure states are explicit and completed refreshes can be run again', () => {
  const failed = render('error', 'NVE import failed')
  assert.match(failed, /role="alert"/)
  assert.match(failed, /NVE import failed/)
  assert.ok(failed.indexOf('role="alert"') < failed.indexOf('<details>'))
  const complete = render('complete', 'All sources updated')
  assert.match(complete, /Completed/)
  assert.ok(!complete.includes('disabled=""'))
  assert.match(complete, /Synthetic demo assets and telemetry are not reset/)
})

const mapStep = (overrides: Partial<AdministrationStep> = {}): AdministrationStep => ({
  n: 6,
  title: 'Prepare Norwegian Energy Map',
  why: 'Prepare once, then refresh operational snapshots here.',
  done: false,
  busy: false,
  action: 'Prepare map',
  run: () => {},
  repeatable: true,
  details: createElement(MapDataRefreshPanel, { state: 'idle', onRebuild: () => {} }),
  ...overrides,
})

test('step 6 owns preparation and maintenance without a standalone energy-map section', () => {
  const html = renderToStaticMarkup(createElement(AdministrationExperience, { steps: [mapStep()] }))
  assert.equal((html.match(/<section\b/g) ?? []).length, 1)
  assert.equal((html.match(/Prepare Norwegian Energy Map/g) ?? []).length, 1)
  assert.match(html, /class="step-action"[^>]*>Prepare map<\/button>/)
  assert.match(html, /class="setup-step"[\s\S]*Maintenance and source details[\s\S]*<\/li>/)
  assert.doesNotMatch(html, /<details open|disabled=""/)
})

test('a prepared map retains an enabled refresh action while ordinary completed steps stay disabled', () => {
  const html = renderToStaticMarkup(createElement(AdministrationExperience, { steps: [
    { n: 1, title: 'Sign in', why: '', done: true, busy: false, action: 'Sign in', run: () => {} },
    mapStep({ done: true, action: 'Refresh snapshots' }),
  ] }))
  assert.match(html, /disabled="">Done<\/button>/)
  assert.match(html, /class="step-action">Refresh snapshots<\/button>/)
  assert.equal((html.match(/disabled=""/g) ?? []).length, 1)
})

test('an active map run disables both refresh and rebuild while keeping progress visible in step 6', () => {
  const html = renderToStaticMarkup(createElement(AdministrationExperience, { steps: [mapStep({
    done: true,
    busy: true,
    action: 'Refresh snapshots',
    details: createElement(MapDataRefreshPanel, { state: 'running', status: 'Checking map chat', onRebuild: () => {} }),
  })] }))
  assert.equal((html.match(/disabled=""/g) ?? []).length, 2)
  assert.match(html, /Working…/)
  assert.match(html, /Checking map chat/)
  assert.match(html, /<progress aria-label="Map data update is running"><\/progress>/)
  assert.ok(html.indexOf('role="status"') < html.indexOf('<details>'))
})
