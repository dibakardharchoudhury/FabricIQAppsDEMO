import assert from 'node:assert/strict'
import test from 'node:test'

import { synchronizeRedirectUris } from './setup-live-auth.mjs'

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