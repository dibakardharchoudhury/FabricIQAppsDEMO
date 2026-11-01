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
    popup: async (...args) => {
      const [scopes] = args
      popups.push(scopes[0])
      consents.push(args.flat())
      assert.equal(args.length, 1, 'Each consent request must contain only its own resource scopes.')
      for (const scope of scopes) tokens.set(scope, `token-${scope}`)
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

test('one operator action authorizes missing audiences separately and verifies each token silently', async () => {
  const { access, popups, consents } = fixture([])
  assert.deepEqual(await access.acquire(true), {
    fabric: 'token-fabric', foundry: 'token-foundry', graphql: 'token-sql', kusto: 'token-kusto',
  })
  assert.deepEqual(popups, ['fabric', 'foundry', 'sql', 'kusto'])
  assert.deepEqual(consents, [['fabric'], ['foundry'], ['sql'], ['kusto']])
  assert.deepEqual(await access.acquire(), {
    fabric: 'token-fabric', foundry: 'token-foundry', graphql: 'token-sql', kusto: 'token-kusto',
  })
  await access.acquire(true)
  assert.equal(popups.length, 4)
})

test('normal requests reuse an authorized session without interaction and recover only genuinely missing access', async () => {
  const { access, popups, consents } = fixture()
  await access.acquire(true)
  assert.deepEqual(popups, ['foundry'])
  assert.deepEqual(consents, [['foundry']])
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
  let popups = 0
  const access = createHostedSourceAccess(requirements, {
    silent: async () => null,
    popup: async () => { popups++; return 'unusable-token' },
  })
  await assert.rejects(access.acquire(true), error => {
    assert.ok(error instanceof HostedSourceAccessError)
    assert.deepEqual(error.resources, requirements.map(item => item.label))
    return true
  })
  assert.equal(popups, 1)
})

test('real multi-resource scopes never enter the same Entra consent request', async () => {
  const actual: HostedAccessRequirement[] = [
    { kind: 'fabric', label: 'Fabric', scopes: [
      'https://api.fabric.microsoft.com/Workspace.Read.All',
      'https://api.fabric.microsoft.com/Item.Read.All',
      'https://api.fabric.microsoft.com/Item.Execute.All',
      'https://api.fabric.microsoft.com/DataAgent.Execute.All',
    ] },
    { kind: 'foundry', label: 'Foundry', scopes: ['https://ai.azure.com/user_impersonation'] },
    { kind: 'graphql', label: 'Lakehouse/SQL', scopes: [
      'https://analysis.windows.net/powerbi/api/GraphQLApi.Execute.All',
      'https://analysis.windows.net/powerbi/api/Item.Execute.All',
    ] },
    { kind: 'kusto', label: 'Eventhouse telemetry', scopes: ['https://example.kusto.fabric.microsoft.com/user_impersonation'] },
  ]
  const authorized = new Set<string>()
  let active = 0
  const requests: string[][] = []
  const access = createHostedSourceAccess(actual, {
    silent: async scopes => authorized.has(scopes.join(' ')) ? 'verified-token' : null,
    popup: async scopes => {
      assert.equal(active++, 0, 'Consent interactions must be serial.')
      assert.equal(new Set(scopes.map(scope => scope.slice(0, scope.lastIndexOf('/')))).size, 1)
      requests.push(scopes)
      await Promise.resolve()
      authorized.add(scopes.join(' '))
      active--
      return 'popup-token'
    },
  })
  await access.acquire(true)
  assert.deepEqual(requests, actual.map(item => item.scopes))
})

test('a later cancelled consent preserves earlier authorization and submits no partial token set', async () => {
  const authorized = new Set<string>()
  const popups: string[] = []
  const cancelled = new Error('user_cancelled')
  const access = createHostedSourceAccess(requirements, {
    silent: async scopes => authorized.has(scopes[0]) ? 'verified-token' : null,
    popup: async scopes => {
      popups.push(scopes[0])
      if (scopes[0] === 'foundry') throw cancelled
      authorized.add(scopes[0])
      return 'popup-token'
    },
  })
  await assert.rejects(access.acquire(true), error => error === cancelled)
  assert.deepEqual(popups, ['fabric', 'foundry'])
  assert.deepEqual([...authorized], ['fabric'])
})

test('chat has no separate access setup button or connected banner', () => {
  const source = readFileSync(new URL('../src/components/CopilotExperience.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /AgentAccessControl|Connect agent access|Agent access is connected/)
})

test('hosted source admission uses the deployment-verified endpoint before optional workspace discovery', () => {
  const source = readFileSync(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
  assert.match(source, /const cluster = configuredEventhouseQueryUri \?\? \(await ensureConfig\(false\)\)\?\.eventhouseQueryUri/)
})
