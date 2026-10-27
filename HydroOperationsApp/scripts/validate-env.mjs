#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REQUIRED = [
  'FABRIC_WORKSPACE_NAME',
  'RAYFIN_PUBLIC_WORKSPACE_ID',
  'RAYFIN_PUBLIC_AAD_CLIENT_ID',
  'RAYFIN_PUBLIC_TENANT_ID',
  'RAYFIN_PUBLIC_ENABLE_ENERGY_MAP',
]
const GUID_KEYS = new Set([
  'RAYFIN_PUBLIC_WORKSPACE_ID',
  'RAYFIN_PUBLIC_AAD_CLIENT_ID',
  'RAYFIN_PUBLIC_TENANT_ID',
])
const BOOLEAN_KEYS = new Set([
  'RAYFIN_PUBLIC_ENABLE_ENERGY_MAP',
])
const ENERGY_MAP_SOURCE_KEY = 'RAYFIN_PUBLIC_ENABLE_ENERGY_MAP'
const ENERGY_MAP_VITE_KEY = 'VITE_RAYFIN_ENABLE_ENERGY_MAP'
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
  for (const key of BOOLEAN_KEYS) {
    if (key in values && !/^(true|false)$/.test(values[key])) {
      problems.push(`${key} must be true or false`)
    }
  }
  if (values.RAYFIN_PUBLIC_ENABLE_ENERGY_MAP === 'true') {
    for (const key of ['RAYFIN_PUBLIC_GEO_LAKEHOUSE_ID', 'RAYFIN_PUBLIC_GEO_SQL_ENDPOINT_ID']) {
      if (!GUID.test(values[key] ?? '')) problems.push(`${key} must be a verified target-workspace GUID when energy maps are enabled`)
    }
    if (values.RAYFIN_PUBLIC_GEO_SQL_ENDPOINT_ID
      && values.RAYFIN_PUBLIC_GEO_SQL_ENDPOINT_ID.toLowerCase() === values.RAYFIN_PUBLIC_LAKEHOUSE_SQL_ENDPOINT_ID?.toLowerCase()) {
      problems.push('GeoContext must use its own SQL analytics endpoint, not the Core Lakehouse endpoint')
    }
  }
  return problems
}

export function validateEnvFile(file) {
  if (!fs.existsSync(file)) return [`${file} does not exist`]
  return validateEnv(fs.readFileSync(file, 'utf8'))
}

export function validateGeneratedEnv(sourceContent, generatedContent) {
  const source = parseEnv(sourceContent)
  const generated = parseEnv(generatedContent)
  const expected = source[ENERGY_MAP_SOURCE_KEY]
  const actual = generated[ENERGY_MAP_VITE_KEY]
  const problems = []
  if (expected === undefined) {
    problems.push(`${ENERGY_MAP_SOURCE_KEY} is missing from rayfin/.env`)
  }
  if (actual === undefined) {
    problems.push(`${ENERGY_MAP_VITE_KEY} is missing from generated .env.local`)
  } else if (expected !== undefined && actual !== expected) {
    problems.push(`${ENERGY_MAP_VITE_KEY} must match ${ENERGY_MAP_SOURCE_KEY}`)
  }
  return problems
}

function main() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const sourceFile = path.join(root, 'rayfin', '.env')
  const generatedFile = path.join(root, '.env.local')
  const validateGenerated = process.argv.includes('--generated')
  const problems = validateEnvFile(sourceFile)
  if (validateGenerated) {
    if (!fs.existsSync(generatedFile)) {
      problems.push(`${generatedFile} does not exist`)
    } else if (fs.existsSync(sourceFile)) {
      problems.push(...validateGeneratedEnv(
        fs.readFileSync(sourceFile, 'utf8'),
        fs.readFileSync(generatedFile, 'utf8'),
      ))
    }
  }
  if (problems.length) {
    console.error('ERROR: Rayfin build environment is not deployable:')
    for (const problem of new Set(problems)) console.error(`  - ${problem}`)
    console.error('Fill the required values from rayfin/.env.example before building or deploying.')
    process.exitCode = 1
    return
  }
  console.log(validateGenerated
    ? 'Validated rayfin/.env and its generated Vite projection.'
    : 'Validated required deployment configuration in rayfin/.env.')
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
