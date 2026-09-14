#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REQUIRED = [
  'FABRIC_WORKSPACE_NAME',
  'RAYFIN_PUBLIC_WORKSPACE_ID',
  'RAYFIN_PUBLIC_AAD_CLIENT_ID',
  'RAYFIN_PUBLIC_TENANT_ID',
]
const GUID_KEYS = new Set([
  'RAYFIN_PUBLIC_WORKSPACE_ID',
  'RAYFIN_PUBLIC_AAD_CLIENT_ID',
  'RAYFIN_PUBLIC_TENANT_ID',
])
const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function parseEnv(content) {
  const values = {}
  for (const raw of content.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const separator = line.indexOf('=')
    if (separator === -1) continue
    values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim()
  }
  return values
}

export function validateEnv(content) {
  const values = parseEnv(content)
  const problems = []
  for (const key of REQUIRED) {
    const value = values[key]
    if (!value || /^<.*>$/.test(value)) {
      problems.push(`${key} is missing or empty`)
    } else if (GUID_KEYS.has(key) && !GUID.test(value)) {
      problems.push(`${key} must be a GUID`)
    }
  }
  return problems
}

export function validateEnvFile(file) {
  if (!fs.existsSync(file)) return [`${file} does not exist`]
  return validateEnv(fs.readFileSync(file, 'utf8'))
}

function main() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const file = path.join(root, 'rayfin', '.env')
  const problems = validateEnvFile(file)
  if (problems.length) {
    console.error('ERROR: rayfin/.env is not deployable:')
    for (const problem of problems) console.error(`  - ${problem}`)
    console.error('Fill the required values from rayfin/.env.example before building or deploying.')
    process.exitCode = 1
    return
  }
  console.log('Validated required deployment configuration in rayfin/.env.')
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
