import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
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
  const consents: string[][] = []
  const access = createHostedSourceAccess(requirements, {
    silent: async scopes => tokens.get(scopes[0]) ?? null,
    popup: async (scopes, extraScopes) => {
      popups.push(scopes[0])
      consents.push(extraScopes)
      for (const scope of [...scopes, ...extraScopes]) tokens.set(scope, `token-${scope}`)
      return `token-${scopes[0]}`
    },
  })
  return { access, tokens, popups, consents }
}

test('background preflight remains silent and names missing access without certifying a run', async () => {
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

test('one operator action authorizes missing audiences together and verifies each token silently', async () => {
  const { access, popups, consents } = fixture([])
  assert.deepEqual(await access.acquire(true), {
    fabric: 'token-fabric', foundry: 'token-foundry', graphql: 'token-sql', kusto: 'token-kusto',
  })
  assert.deepEqual(popups, ['fabric'])
  assert.deepEqual(consents, [['foundry', 'sql', 'kusto']])
  assert.deepEqual(await access.acquire(), {
    fabric: 'token-fabric', foundry: 'token-foundry', graphql: 'token-sql', kusto: 'token-kusto',
  })
  await access.acquire(true)
  assert.equal(popups.length, 1)
})

test('normal requests reuse an authorized session without interaction and recover only genuinely missing access', async () => {
  const { access, popups, consents } = fixture()
  await access.acquire(true)
  assert.deepEqual(popups, ['foundry'])
  assert.deepEqual(consents, [[]])
  await access.acquire(true)
  assert.deepEqual(popups, ['foundry'])
  const ready = fixture(['fabric', 'foundry', 'sql', 'kusto'])
  await ready.access.acquire(true)
  assert.deepEqual(ready.popups, [])
})

test('cancelled consent and network failures propagate without trying another audience', async () => {
  const cancelled = new Error('user_cancelled')
  const access = createHostedSourceAccess(requirements, {
    silent: async () => null,
    popup: async () => { throw cancelled },
  })
  await assert.rejects(access.acquire(true), error => error === cancelled)
  const offline = createHostedSourceAccess(requirements, {
    silent: async () => { throw new Error('network_error') },
    popup: async () => { assert.fail('Unexpected popup') },
  })
  await assert.rejects(offline.acquire(true), /network_error/)
})

test('a completed popup is not proof of usable source authorization', async () => {
  const access = createHostedSourceAccess(requirements, {
    silent: async () => null,
    popup: async () => 'unusable-token',
  })
  await assert.rejects(access.acquire(true), error => {
    assert.ok(error instanceof HostedSourceAccessError)
    assert.deepEqual(error.resources, requirements.map(item => item.label))
    return true
  })
})

test('chat and comparison have no separate access setup button or connected banner', () => {
  for (const component of ['CopilotExperience', 'AgentBattleExperience']) {
    const source = readFileSync(new URL(`../src/components/${component}.tsx`, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /AgentAccessControl|Connect agent access|Agent access is connected/)
  }
})
