import assert from 'node:assert/strict'
import test from 'node:test'
import { selectDataAgent } from '../src/services/artifactDiscovery.ts'

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