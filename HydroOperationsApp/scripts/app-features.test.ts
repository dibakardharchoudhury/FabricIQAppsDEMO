import assert from 'node:assert/strict'
import test from 'node:test'
import { mapPublicEnvForFramework } from '@microsoft/rayfin-tools-common/_internal/config'
import {
  ENERGY_MAP_ENABLED,
  featureFlagEnabled,
  filterEnergyMapTabs,
  resolveVisibleTab,
} from '../src/ui-shared/appFeatures.ts'

const tabs = [{ id: 'overview' }, { id: 'map' }, { id: 'telemetry' }]

test('energy map is opt-in and only the exact generated true value enables it', () => {
  assert.equal(ENERGY_MAP_ENABLED, false)
  assert.equal(featureFlagEnabled(undefined), false)
  assert.equal(featureFlagEnabled('false'), false)
  assert.equal(featureFlagEnabled('TRUE'), false)
  assert.equal(featureFlagEnabled('true'), true)
})

test('disabled map tabs are filtered before URL resolution', () => {
  const visible = filterEnergyMapTabs(tabs, false)
  assert.deepEqual(visible.map(tab => tab.id), ['overview', 'telemetry'])
  assert.equal(resolveVisibleTab(visible, 'map').id, 'overview')
  assert.equal(resolveVisibleTab(visible, 'telemetry').id, 'telemetry')
})

test('feature deployments retain the map tab', () => {
  assert.deepEqual(filterEnergyMapTabs(tabs, true).map(tab => tab.id), ['overview', 'map', 'telemetry'])
})

test('Rayfin projects the source flag to the Vite key consumed by the app', () => {
  const generated = mapPublicEnvForFramework(new Map([
    ['RAYFIN_PUBLIC_ENABLE_ENERGY_MAP', 'true'],
  ]), 'vite')

  assert.equal(generated.get('VITE_RAYFIN_ENABLE_ENERGY_MAP'), 'true')
  assert.equal(featureFlagEnabled(generated.get('VITE_RAYFIN_ENABLE_ENERGY_MAP')), true)
})
