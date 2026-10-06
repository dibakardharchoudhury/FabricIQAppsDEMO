import assert from 'node:assert/strict'
import test from 'node:test'
import { mapDataAgentName, selectDataAgent, selectNamedDataAgent } from '../src/services/artifactDiscovery.ts'

test('selects the latest natural RTI Data Agent version', () => {
  const selected = selectDataAgent([
    { id: 'other', type: 'DataAgent', displayName: 'Another Agent' },
    { id: 'v9', type: 'DataAgent', displayName: 'RTI_Demo_Agent_V9' },
    { id: 'v10', type: 'DataAgent', displayName: 'RTI_Demo_Agent_V10' },
  ])

  assert.equal(selected?.id, 'v10')
})

test('uses the only Data Agent when it has a custom name', () => {
  const selected = selectDataAgent([
    { id: 'custom', type: 'DataAgent', displayName: 'Customer Hydro Agent' },
  ])

  assert.equal(selected?.id, 'custom')
})

test('does not guess between multiple unrelated Data Agents', () => {
  const selected = selectDataAgent([
    { id: 'first', type: 'DataAgent', displayName: 'First Agent' },
    { id: 'second', type: 'DataAgent', displayName: 'Second Agent' },
  ])

  assert.equal(selected, undefined)
})

test('map chat resolves a separate exact agent without redirecting Hydro Intelligence', () => {
  const items = [
    { id: 'baseline', type: 'DataAgent', displayName: 'RTI_Demo_Agent_V6' },
    { id: 'map', type: 'DataAgent', displayName: 'Hydro_Map_Agent_V6' },
  ]
  assert.equal(selectDataAgent(items)?.id, 'baseline')
  assert.equal(selectNamedDataAgent(items, mapDataAgentName('RTI_Demo_Eventhouse_V6')).id, 'map')
  assert.equal(mapDataAgentName('Custom Eventhouse', 'Custom Map Agent'), 'Custom Map Agent')
  assert.throws(() => mapDataAgentName('Custom Eventhouse'), /exact map Data Agent/)
  assert.throws(() => selectNamedDataAgent(items, 'Missing Map Agent'), /Expected one/)
  assert.throws(() => selectNamedDataAgent([...items, items[1]], 'Hydro_Map_Agent_V6'), /Expected one/)
})