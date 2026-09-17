import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  activateFreshTenantAzureCliCache,
  selectCurrentHostingOrigin,
  synchronizeRedirectUris,
} from './setup-live-auth.mjs'

test('selects only the latest configured Fabric host for addition', () => {
  const historical = 'https://historical.webapp.fabricapps.net'
  const current = 'https://current.webapp.fabricapps.net'

  assert.equal(
    selectCurrentHostingOrigin([historical, 'http://localhost:5173', current]),
    current,
  )
})

test('preserves existing SPA redirects and appends only missing redirects', () => {
  const existing = [
    'https://workspace-a.webapp.fabricapps.net',
    'https://workspace-b.webapp.fabricapps.net',
    'https://example.com/callback',
    'https://workspace-a.webapp.fabricapps.net',
  ]
  const desired = [
    'https://workspace-c.webapp.fabricapps.net',
    'http://localhost:5173',
  ]

  assert.deepEqual(synchronizeRedirectUris(existing, desired), [
    'https://workspace-a.webapp.fabricapps.net',
    'https://workspace-b.webapp.fabricapps.net',
    'https://example.com/callback',
    'https://workspace-c.webapp.fabricapps.net',
    'http://localhost:5173',
  ])
})

test('does not duplicate an existing desired redirect', () => {
  const existing = ['http://localhost:5173']

  assert.deepEqual(synchronizeRedirectUris(existing, ['http://localhost:5173']), existing)
})

test('rotates only the tenant-scoped Azure CLI cache', (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'setup-live-auth-test-'))
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }))

  const defaultCache = path.join(tempDir, 'home', '.azure', 'msal_token_cache.bin')
  const sessionRoot = path.join(tempDir, 'sessions')
  const tenantCache = path.join(sessionRoot, 'tenant-id')
  fs.mkdirSync(path.dirname(defaultCache), { recursive: true })
  fs.writeFileSync(defaultCache, 'default-session')
  fs.mkdirSync(tenantCache, { recursive: true })
  fs.writeFileSync(path.join(tenantCache, 'msal_token_cache.bin'), 'stale-session')
  const environment = {}

  const result = activateFreshTenantAzureCliCache('TENANT-ID', {
    sessionRoot,
    environment,
    now: new Date('2026-09-17T06:00:00.000Z'),
    processId: 42,
  })

  assert.equal(environment.AZURE_CONFIG_DIR, tenantCache)
  assert.equal(fs.readFileSync(defaultCache, 'utf8'), 'default-session')
  assert.equal(fs.existsSync(tenantCache), true)
  assert.equal(fs.readdirSync(tenantCache).length, 0)
  assert.equal(
    fs.readFileSync(path.join(result.backupDir, 'msal_token_cache.bin'), 'utf8'),
    'stale-session',
  )
})

test('requires an explicit tenant for Azure CLI cache recovery', () => {
  assert.throws(
    () => activateFreshTenantAzureCliCache(null),
    /RAYFIN_PUBLIC_TENANT_ID is required/,
  )
})
