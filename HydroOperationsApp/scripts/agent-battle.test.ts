import assert from 'node:assert/strict'
import test from 'node:test'
import { runAgentBattle } from '../src/services/copilot/agentBattle.ts'

test('passes the same normalized prompt to both agents', async () => {
  const calls: Array<[string, string]> = []
  const results = await runAgentBattle('  Show T005 power  ', 'sequential', {
    'data-agent': async prompt => { calls.push(['data-agent', prompt]); return 'data' },
    foundry: async prompt => { calls.push(['foundry', prompt]); return 'foundry' },
  })

  assert.deepEqual(calls, [
    ['data-agent', 'Show T005 power'],
    ['foundry', 'Show T005 power'],
  ])
  assert.equal(results['data-agent'].value, 'data')
  assert.equal(results.foundry.value, 'foundry')
})

test('preserves one result when the other agent fails', async () => {
  const settled: string[] = []
  const results = await runAgentBattle('Compare open work orders', 'parallel', {
    'data-agent': async () => { throw new Error('Data Agent unavailable') },
    foundry: async () => 'Foundry answer',
  }, result => settled.push(result.engine))

  assert.equal(results['data-agent'].ok, false)
  assert.equal(results['data-agent'].error, 'Data Agent unavailable')
  assert.equal(results.foundry.ok, true)
  assert.equal(results.foundry.value, 'Foundry answer')
  assert.deepEqual(new Set(settled), new Set(['data-agent', 'foundry']))
})
