import assert from 'node:assert/strict'
import test from 'node:test'
import { InteractionRequiredAuthError } from '@azure/msal-browser'
import { readFileSync } from 'node:fs'
import { createEntraTokens } from '../src/services/entraTokens.ts'

const account = { homeAccountId: 'operator', tenantId: 'target-tenant' }
function fixture() {
  const state = { account, accounts: [account], popups: [], silent: [], tokens: new Map(), activePopups: 0, maxPopups: 0 }
  const client = {
    getActiveAccount: () => state.account,
    getAllAccounts: () => state.accounts,
    setActiveAccount: value => { state.account = value },
    acquireTokenSilent: async request => {
      state.silent.push(request)
      if (state.silentError) throw state.silentError
      const token = state.tokens.get(request.scopes.join(' '))
      if (!token) throw new InteractionRequiredAuthError('consent_required')
      return { accessToken: token, expiresOn: state.expiresOn }
    },
    acquireTokenPopup: async request => {
      state.popups.push(request)
      state.activePopups++
      state.maxPopups = Math.max(state.maxPopups, state.activePopups)
      await Promise.resolve()
      state.activePopups--
      if (state.popupError) throw state.popupError
      const token = `token-${request.scopes.join(' ')}`
      state.tokens.set(request.scopes.join(' '), token)
      return { account: state.popupAccount ?? account, accessToken: token }
    },
  }
  return { state, tokens: createEntraTokens(client, async () => {}, 'target-tenant') }
}

test('repeated requests and explicit connection actions reuse the cached token without a popup', async () => {
  const { state, tokens } = fixture()
  state.tokens.set('foundry', 'cached')
  for (let attempt = 0; attempt < 3; attempt++) {
    assert.equal(await tokens.silent(['foundry']), 'cached')
    assert.equal(await tokens.popup(['foundry']), 'cached')
  }
  assert.equal(state.popups.length, 0)
  assert.ok(state.silent.every(request => request.forceRefresh === false))
})

test('network and unexpected silent errors are not converted into repeated consent prompts', async () => {
  const { state, tokens } = fixture()
  state.silentError = new Error('network_error')
  await assert.rejects(tokens.silent(['foundry']), /network_error/)
  await assert.rejects(tokens.popup(['foundry']), /network_error/)
  assert.equal(state.popups.length, 0)
})

test('concurrent requests for the same resource coalesce into one interaction', async () => {
  const { state, tokens } = fixture()
  assert.equal(await tokens.silent(['foundry']), null)
  const results = await Promise.all([tokens.popup(['foundry']), tokens.popup(['foundry']), tokens.popup(['foundry'])])
  assert.deepEqual(results, ['token-foundry', 'token-foundry', 'token-foundry'])
  assert.equal(state.popups.length, 1)
  assert.equal(state.popups[0].prompt, undefined)
  assert.equal(await tokens.silent(['foundry']), 'token-foundry')
})

test('different resources serialize their required interactions and retry silently after waiting', async () => {
  const { state, tokens } = fixture()
  const results = await Promise.all([tokens.popup(['foundry']), tokens.popup(['fabric'])])
  assert.deepEqual(results, ['token-foundry', 'token-fabric'])
  assert.equal(state.maxPopups, 1)
  assert.equal(state.popups.length, 2)
})

test('cancelled consent propagates and does not poison a later explicitly requested sign-in', async () => {
  const { state, tokens } = fixture()
  state.popupError = new Error('user_cancelled')
  await assert.rejects(tokens.popup(['foundry']), /user_cancelled/)
  state.popupError = undefined
  assert.equal(await tokens.popup(['foundry']), 'token-foundry')
  assert.equal(state.popups.length, 2)
})

test('tokens never use an arbitrary first account or accept another tenant', async () => {
  const { state, tokens } = fixture()
  const foreign = { homeAccountId: 'foreign', tenantId: 'other-tenant' }
  state.account = foreign
  state.accounts = [foreign, account]
  state.tokens.set('foundry', 'cached')
  assert.equal(await tokens.silent(['foundry']), 'cached')
  assert.equal(state.silent[0].account, account)
  state.tokens.clear()
  state.popupAccount = foreign
  await assert.rejects(tokens.popup(['foundry']), /configured tenant/)
  assert.equal(state.account, foreign)
})

test('ambiguous accounts require selection without forcing fresh consent', async () => {
  const { state, tokens } = fixture()
  state.account = null
  state.accounts = [account, { ...account, homeAccountId: 'another-operator' }]
  assert.equal(await tokens.silent(['foundry']), null)
  await tokens.popup(['foundry'])
  assert.equal(state.popups[0].prompt, 'select_account')
  assert.equal(state.popups[0].account, undefined)
})

test('workspace reads do not request job execution or Data Agent execution scopes', () => {
  const source = readFileSync(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
  const readScopes = source.match(/const FABRIC_SCOPES = \[([^\]]+)\]/)?.[1]
  assert.ok(readScopes)
  assert.match(readScopes, /Workspace\.Read\.All/)
  assert.match(readScopes, /Item\.Read\.All/)
  assert.doesNotMatch(readScopes, /Execute/)
  assert.match(source, /const FABRIC_JOB_SCOPES = \[\.\.\.FABRIC_SCOPES, 'https:\/\/api\.fabric\.microsoft\.com\/Item\.Execute\.All'\]/)
  assert.match(source, /const DATA_AGENT_SCOPES = \[\.\.\.FABRIC_SCOPES, 'https:\/\/api\.fabric\.microsoft\.com\/DataAgent\.Execute\.All'\]/)
  assert.match(source, /const token = await fabricToken\(true, FABRIC_JOB_SCOPES\)/)
})

test('hosted Rayfin exchange never submits a lease that can expire during a bounded run', () => {
  const source = readFileSync(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
  assert.match(source,
    /const POWER_BI_ITEM_EXECUTE_SCOPE = 'https:\/\/analysis\.windows\.net\/powerbi\/api\/Item\.Execute\.All'/)
  assert.match(source, /const HOSTED_RUN_TOKEN_LIFETIME_MS = 12 \* 60_000/)
  assert.match(source, /silentToken\(\[\.\.\.new Set\(\[\.\.\.FABRIC_JOB_SCOPES, \.\.\.DATA_AGENT_SCOPES\]\)\], false, HOSTED_RUN_TOKEN_LIFETIME_MS\)/)
  assert.match(source, /silentToken\(FOUNDRY_AGENT_SCOPES, false, HOSTED_RUN_TOKEN_LIFETIME_MS\)/)
  assert.match(source, /silentToken\(\[GRAPHQL_SCOPE, POWER_BI_ITEM_EXECUTE_SCOPE\], false, HOSTED_RUN_TOKEN_LIFETIME_MS\)/)
  assert.match(source, /silentToken\(\[kustoScope\(cluster\)\], false, HOSTED_RUN_TOKEN_LIFETIME_MS\)/)
})

test('a cached lease is reused only while it outlives the bounded operation, otherwise it is refreshed once', async () => {
  const { state, tokens } = fixture()
  const minutes = count => new Date(Date.now() + count * 60_000)
  state.tokens.set('foundry', 'cached')
  state.expiresOn = minutes(45)
  assert.equal(await tokens.silent(['foundry'], false, 12 * 60_000), 'cached')
  assert.equal(state.silent.length, 1)
  assert.equal(state.silent[0].forceRefresh, false)
  state.silent.length = 0
  state.expiresOn = minutes(6)
  assert.equal(await tokens.silent(['foundry'], false, 12 * 60_000), 'cached')
  assert.deepEqual(state.silent.map(request => request.forceRefresh), [false, true])
  state.silent.length = 0
  state.expiresOn = undefined
  assert.equal(await tokens.silent(['foundry'], false, 12 * 60_000), 'cached')
  assert.deepEqual(state.silent.map(request => request.forceRefresh), [false, true])
  state.silent.length = 0
  assert.equal(await tokens.silent(['foundry']), 'cached')
  assert.deepEqual(state.silent.map(request => request.forceRefresh), [false])
})

test('explicit source connection preserves the user gesture for deployment-bound resource consent', () => {
  const source = readFileSync(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
  const connect = source.slice(
    source.indexOf('export async function beginInteractiveConnect'),
    source.indexOf('export type Facility'),
  )
  const knownConfig = connect.indexOf('const knownConfig = configCache ?? envConfig()')
  const stidPopup = connect.indexOf('await popupToken([GRAPHQL_SCOPE])')
  const telemetryPopup = connect.indexOf('await popupToken([kustoScope(knownConfig.eventhouseQueryUri)])')
  const discovery = connect.indexOf('const config = await ensureConfig(true)')
  assert.ok(knownConfig >= 0)
  assert.ok(stidPopup > knownConfig && stidPopup < discovery)
  assert.ok(telemetryPopup > knownConfig && telemetryPopup < discovery)
  assert.match(connect, /Click Connect STID again to authorize the GraphQL API/)
  assert.match(connect, /Click Connect telemetry again to authorize the Eventhouse/)
})
