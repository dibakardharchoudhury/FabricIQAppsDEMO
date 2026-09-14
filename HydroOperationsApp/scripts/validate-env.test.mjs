import assert from 'node:assert/strict'
import test from 'node:test'

import { parseEnv, validateEnv } from './validate-env.mjs'

const valid = `
FABRIC_WORKSPACE_NAME=Demo Workspace
RAYFIN_PUBLIC_WORKSPACE_ID=a79a4b7e-e508-4fa4-8b6f-15deadca0f34
RAYFIN_PUBLIC_AAD_CLIENT_ID=22dedc54-8b7e-442c-929d-497c4df086e6
RAYFIN_PUBLIC_TENANT_ID=ad340c84-1886-4202-a483-2da2cb9168eb
`

test('accepts the complete deployment contract', () => {
  assert.deepEqual(validateEnv(valid), [])
})

test('rejects the empty SPA client ID that produces a broken deployed bundle', () => {
  const problems = validateEnv(valid.replace(
    'RAYFIN_PUBLIC_AAD_CLIENT_ID=22dedc54-8b7e-442c-929d-497c4df086e6',
    'RAYFIN_PUBLIC_AAD_CLIENT_ID=',
  ))

  assert.deepEqual(problems, ['RAYFIN_PUBLIC_AAD_CLIENT_ID is missing or empty'])
})

test('rejects template placeholders and malformed identifiers', () => {
  const problems = validateEnv(valid
    .replace('Demo Workspace', '<your Fabric workspace display name>')
    .replace('ad340c84-1886-4202-a483-2da2cb9168eb', 'not-a-guid'))

  assert.deepEqual(problems, [
    'FABRIC_WORKSPACE_NAME is missing or empty',
    'RAYFIN_PUBLIC_TENANT_ID must be a GUID',
  ])
})

test('parses values containing equals signs without truncating them', () => {
  assert.equal(parseEnv('SETTING=https://example.test?a=b').SETTING, 'https://example.test?a=b')
})
