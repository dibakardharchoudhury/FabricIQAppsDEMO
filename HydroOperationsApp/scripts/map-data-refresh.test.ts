import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MapDataRefreshPanel } from '../src/components/MapDataRefreshPanel.tsx'
import { AdministrationExperience, type AdministrationStep } from '../src/components/AdministrationExperience.tsx'

const render = (state: 'idle' | 'running' | 'complete' | 'error', message?: string) =>
  renderToStaticMarkup(createElement(MapDataRefreshPanel, { state, message, onRebuild: () => {} }))

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

test('active runs disable rebuild and leave progress to the full-width banner', () => {
  const html = render('running', 'Refreshing map data')
  assert.match(html, /disabled=""/)
  assert.equal((html.match(/disabled=""/g) ?? []).length, 1)
  assert.doesNotMatch(html, /<progress|role="status"|Refreshing map data/)
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

test('an active map run disables both refresh and rebuild without duplicate inline progress', () => {
  const html = renderToStaticMarkup(createElement(AdministrationExperience, { steps: [mapStep({
    done: true,
    busy: true,
    action: 'Refresh snapshots',
    details: createElement(MapDataRefreshPanel, { state: 'running', onRebuild: () => {} }),
  })] }))
  assert.equal((html.match(/disabled=""/g) ?? []).length, 2)
  assert.match(html, /Working…/)
  assert.doesNotMatch(html, /<progress|role="status"/)
})

const pageSource = readFileSync(new URL('../src/ui-shared/pages/AdministrationPage.tsx', import.meta.url), 'utf8')
const pageCode = ts.transpileModule(pageSource, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
}).outputText
const require = createRequire(import.meta.url)
const job = (kind: 'stream' | 'weather' | 'map') =>
  ({ kind, label: `${kind} job`, status: 'Queued', pct: 3, startedAt: 0, etaMs: 1000 })

function renderPage(state: 'running' | 'complete' | 'error', now = 310000, endedAt?: number) {
  const data = {
    user: {}, operationsState: 'connected', streamState: 'running', stidState: 'connected',
    telemetryState: 'connected', counts: { liveSignals: 1 }, weatherState: 'running',
    mapPrepared: state === 'complete', mapRefreshState: state,
    mapRefreshMessage: state === 'error' ? 'NVE import failed' : 'Monitoring existing map refresh',
    now, jobs: {
      stream: job('stream'), weather: job('weather'),
      ...(state === 'error' ? {} : { map: { ...job('map'), endedAt } }),
    },
    actions: {},
  }
  const exports: { AdministrationPage?: () => ReturnType<typeof createElement> } = {}
  new Function('require', 'exports', pageCode)((name: string) => {
    if (name.endsWith('AdministrationExperience')) return { AdministrationExperience }
    if (name.endsWith('MapDataRefreshPanel')) return { MapDataRefreshPanel }
    if (name.endsWith('CopilotSettingsPanel')) return { CopilotSettingsPanel: () => null }
    if (name.endsWith('useHydroOperationsData')) return {
      useHydroOperationsData: () => data, fmtElapsed: (ms: number) => `${ms / 1000}s`,
    }
    if (name.endsWith('appFeatures')) return { ENERGY_MAP_ENABLED: true }
    if (name.endsWith('.css')) return {}
    return require(name)
  }, exports)
  assert.ok(exports.AdministrationPage)
  return renderToStaticMarkup(createElement(exports.AdministrationPage))
}

test('all three concurrent jobs share full-width banners above Guided Setup', () => {
  const html = renderPage('running')
  assert.equal((html.match(/class="v2-progress"/g) ?? []).length, 3)
  assert.equal((html.match(/role="progressbar"/g) ?? []).length, 3)
  assert.ok(html.lastIndexOf('class="v2-progress"') < html.indexOf('class="setup-steps"'))
  assert.equal((html.match(/Monitoring existing map refresh/g) ?? []).length, 1)
  assert.match(html, /Queued · 310s/)
  assert.match(html, /v2-progress-bar--indeterminate/)
  assert.match(html, /aria-label="map job" aria-valuemin="0" aria-valuemax="100">/)
  assert.equal((html.match(/aria-valuenow="3"/g) ?? []).length, 2)
  assert.equal((html.match(/Queued · 3%/g) ?? []).length, 2)
  assert.doesNotMatch(html, /<progress/)
})

test('map elapsed time advances, freezes at completion, and failures remain explicit', () => {
  assert.match(renderPage('running', 311000), /Queued · 311s/)
  const completed = renderPage('complete', 400000, 315000)
  assert.match(completed, /Queued · 315s/)
  assert.match(completed, /Completed/)
  const failed = renderPage('error')
  assert.equal((failed.match(/class="v2-progress"/g) ?? []).length, 2)
  assert.match(failed, /role="alert"[\s\S]*NVE import failed/)
})
