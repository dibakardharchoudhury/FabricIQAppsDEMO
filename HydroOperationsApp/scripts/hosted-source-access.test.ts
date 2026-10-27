import assert from 'node:assert/strict'
import test from 'node:test'
import { createHostedSourceAccess, HostedSourceAccessError, type HostedAccessRequirement } from '../src/services/hostedSourceAccess.ts'
import { uncertifiedFailureMessage } from '../src/services/copilot/orchestration.ts'

const requirements: HostedAccessRequirement[] = [
  { kind: 'fabric', label: 'Fabric', scopes: ['fabric'] },
  { kind: 'foundry', label: 'Foundry', scopes: ['foundry'] },
  { kind: 'graphql', label: 'Lakehouse/SQL', scopes: ['sql'] },
  { kind: 'kusto', label: 'Eventhouse telemetry', scopes: ['kusto'] },
]

function fixture(authorized = ['fabric', 'sql', 'kusto']) {
  const tokens = new Map(authorized.map(scope => [scope, `token-${scope}`]))
  const popups: string[] = []
  const access = createHostedSourceAccess(requirements, {
    silent: async scopes => tokens.get(scopes[0]) ?? null,
    popup: async scopes => {
      popups.push(scopes[0])
      tokens.set(scopes[0], `token-${scopes[0]}`)
      return `token-${scopes[0]}`
    },
  })
  return { access, tokens, popups }
}

test('preflight names the missing audience without opening consent or certifying a run', async () => {
  const { access, popups } = fixture()
  await assert.rejects(access.acquire(), error => {
    assert.ok(error instanceof HostedSourceAccessError)
    assert.deepEqual(error.resources, ['Foundry'])
    assert.equal(uncertifiedFailureMessage(error), error.message)
    assert.match(error.message, /No agent request was sent/)
    return true
  })
  assert.deepEqual(popups, [])
})

test('each explicit connection authorizes only one resource, then verifies remaining access', async () => {
  const { access, popups } = fixture([])
  assert.deepEqual(await access.connectNext(), ['Foundry', 'Lakehouse/SQL', 'Eventhouse telemetry'])
  assert.deepEqual(popups, ['fabric'])
  assert.deepEqual(await access.connectNext(), ['Lakehouse/SQL', 'Eventhouse telemetry'])
  assert.deepEqual(await access.connectNext(), ['Eventhouse telemetry'])
  assert.deepEqual(await access.connectNext(), [])
  assert.deepEqual(popups, ['fabric', 'foundry', 'sql', 'kusto'])
  assert.deepEqual(await access.acquire(), {
    fabric: 'token-fabric', foundry: 'token-foundry', graphql: 'token-sql', kusto: 'token-kusto',
  })
  assert.deepEqual(await access.connectNext(), [])
  assert.equal(popups.length, 4)
})

test('cancelled consent and network failures propagate without trying another audience', async () => {
  const cancelled = new Error('user_cancelled')
  const access = createHostedSourceAccess(requirements, {
    silent: async () => null,
    popup: async () => { throw cancelled },
  })
  await assert.rejects(access.connectNext(), error => error === cancelled)
  const offline = createHostedSourceAccess(requirements, {
    silent: async () => { throw new Error('network_error') },
    popup: async () => { assert.fail('Unexpected popup') },
  })
  await assert.rejects(offline.acquire(), /network_error/)
})

test('a completed popup is not proof of usable source authorization', async () => {
  const access = createHostedSourceAccess(requirements, {
    silent: async () => null,
    popup: async () => 'unusable-token',
  })
  assert.deepEqual(await access.connectNext(), requirements.map(item => item.label))
})
