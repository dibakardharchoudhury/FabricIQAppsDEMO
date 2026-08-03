#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// setup-live-auth.mjs
//
// ONE idempotent command that makes the Hydro Operations app's live-data Entra
// (AAD) app self-configuring after a fresh deploy. The app reads TWO live
// stores directly from the browser, and each needs a delegated permission that
// interactive popup consent can fail to establish cleanly:
//
//   • Telemetry  → the Eventhouse (a Kusto cluster) at *.kusto.fabric.microsoft.com
//                  authenticates through **Azure Data Explorer**.
//   • STID       → the Lakehouse **GraphQL API** at *.graphql.fabric.microsoft.com
//                  authenticates through the **Power BI Service**
//                  (GraphQLApi.Execute.All).
//
// It performs the one-time Azure steps that path needs, in order:
//
//   STEP 1  Redirect URIs — register the current Fabric static-hosting origin +
//           localhost:5173 as **Single-page application** redirect URIs on the
//           app (RAYFIN_PUBLIC_AAD_CLIENT_ID). Without this the connect popup
//           fails with AADSTS50011 (redirect URI mismatch).
//
//   STEP 2  Delegated permissions + consent — add each required delegated scope
//           to the app AND create the tenant-wide consent grant:
//             - Azure Data Explorer  user_impersonation   (telemetry)
//             - Power BI Service      GraphQLApi.Execute.All (STID GraphQL)
//           Scope ids are resolved live from each resource service principal so
//           they stay correct across clouds/tenants. Without the permission you
//           get AADSTS650057 (Invalid resource); without consent, AADSTS65001
//           (not consented).
//
// The Fabric REST scopes the app uses for runtime discovery + job triggering are
// pre-granted here too (served by the Power BI Service resource behind
// api.fabric.microsoft.com): Workspace.Read.All (List Items), Item.Read.All
// (Get Eventhouse -> queryServiceUri, required for telemetry), Item.Execute.All
// (run pipelines/notebooks). Item.Read.All is essential: without it Get Eventhouse
// returns 403 InsufficientScopes and the app reports "No Eventhouse found".
//
// WHY THIS EXISTS:
//   A from-scratch deploy (new Rayfin item / workspace) gets a NEW random
//   `*.webapp.fabricapps.net` hostname, so these must be re-applied. This makes
//   that a single command instead of manual Entra-portal clicks. Re-running is
//   safe: every step checks current state and only writes what is missing.
//
// SOURCES (all local, no scraping of deploy output):
//   - rayfin/.env       → RAYFIN_PUBLIC_AAD_CLIENT_ID, RAYFIN_PUBLIC_TENANT_ID
//   - rayfin/rayfin.yml → services.auth.allowedRedirectUris (`rayfin up` records
//                         the current origin last; older origins are replaced)
//
// PREREQUISITE THAT CANNOT BE AUTOMATED AWAY:
//   Sign in to the Azure CLI (`az login`) as an identity allowed to update the
//   app registration AND grant admin consent — an owner + Application
//   Administrator / Cloud Application Administrator, plus Privileged Role
//   Administrator / Global Administrator for the consent grant. Where a step
//   needs a role you lack, it prints the exact manual action and continues.
//
// USAGE:
//   node ./scripts/setup-live-auth.mjs                 # both steps (apply)
//   node ./scripts/setup-live-auth.mjs --dry-run       # preview, write nothing
//   node ./scripts/setup-live-auth.mjs --redirect-only # STEP 1 only
//   node ./scripts/setup-live-auth.mjs --grant-only    # STEP 2 only
// ─────────────────────────────────────────────────────────────────────────────
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(scriptDir, '..')
const args = process.argv.slice(2)
const dryRun = args.includes('--dry-run')
const redirectOnly = args.includes('--redirect-only')
const grantOnly = args.includes('--grant-only')

const DEV_ORIGINS = ['http://localhost:5173']

let tenantIdFromEnv = null

// Delegated permissions the app's two live-data paths need. Scope ids are
// resolved from each resource SP at runtime (by `value`), so only the stable
// resource app ids + human-readable scope values are hardcoded here.
const REQUIRED_DELEGATED = [
  {
    label: 'Azure Data Explorer (telemetry / Eventhouse)',
    // Resource behind *.kusto.fabric.microsoft.com.
    resourceAppId: '2746ea77-4702-4b45-80ca-3c97e680e8b7',
    scopeValues: ['user_impersonation'],
  },
  {
    label: 'Power BI Service / Microsoft Fabric (STID GraphQL + workspace discovery)',
    // Resource behind *.graphql.fabric.microsoft.com and api.fabric.microsoft.com.
    resourceAppId: '00000009-0000-0000-c000-000000000000',
    // GraphQLApi.Execute.All = STID GraphQL query. Workspace.Read.All = List Items.
    // Item.Read.All = Get Eventhouse (telemetry queryServiceUri). Item.Execute.All = run jobs.
    scopeValues: ['GraphQLApi.Execute.All', 'Workspace.Read.All', 'Item.Read.All', 'Item.Execute.All'],
  },
]

const files = {
  rayfinEnv: path.join(rootDir, 'rayfin', '.env'),
  rayfinYml: path.join(rootDir, 'rayfin', 'rayfin.yml'),
}

// ─── Shared helpers ──────────────────────────────────────────────────────────

function fail(message) {
  console.error(`\nERROR: ${message}\n`)
  process.exit(1)
}

/** Minimal .env parser: KEY=VALUE lines, ignores comments/blanks. */
function parseEnv(file) {
  if (!fs.existsSync(file)) fail(`${path.relative(rootDir, file)} not found. Copy rayfin/.env.example to rayfin/.env first.`)
  const out = {}
  for (const raw of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq === -1) continue
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim()
  }
  return out
}

function az(argv) {
  // az is a .cmd shim on Windows; execFileSync needs shell:true to resolve it.
  return execFileSync('az', argv, { encoding: 'utf8', shell: true, stdio: ['pipe', 'pipe', 'pipe'] })
}

const MSAL_CACHE_FILES = ['msal_token_cache.bin', 'msal_http_cache.bin']

function azErrorText(err) {
  return [err?.message, err?.stderr?.toString?.(), err?.stdout?.toString?.()].filter(Boolean).join('\n')
}

/** CAE challenge: a token minted before a tenant policy change still passes
 *  `az account show` but fails every Graph call. */
function isStaleTokenChallenge(err) {
  return /TokenCreatedWithOutdatedPolicies|Continuous access evaluation|InteractionRequired|AADSTS50076|AADSTS50079|AADSTS50173/i.test(
    azErrorText(err),
  )
}

function clearTokenCache() {
  const dir = path.join(os.homedir(), '.azure')
  for (const f of MSAL_CACHE_FILES) fs.rmSync(path.join(dir, f), { force: true })
}

function recoverStaleToken(tenantId) {
  console.warn(
    '\n\u26a0 Azure CLI token rejected by Continuous Access Evaluation ' +
      '(TokenCreatedWithOutdatedPolicies) \u2014 the cached token predates a tenant ' +
      'policy change. Clearing the token cache and re-authenticating\u2026',
  )
  if (dryRun) {
    console.warn('(dry run \u2014 not clearing cache or logging in; re-run without --dry-run)')
    return
  }
  clearTokenCache()
  const loginArgs = ['login', '--only-show-errors']
  if (tenantId) loginArgs.push('--tenant', tenantId)
  try {
    execFileSync('az', loginArgs, { stdio: 'inherit', shell: true })
  } catch {
    fail(
      'Re-authentication via `az login` failed. Recover manually:\n' +
        `   az account clear && az login${tenantId ? ` --tenant ${tenantId}` : ''}\n` +
        'then re-run this script.',
    )
  }
}

function ensureAzLogin(expectedTenant) {
  let account
  try {
    account = JSON.parse(az(['account', 'show', '-o', 'json']))
  } catch {
    fail(
      'Azure CLI is not logged in (or not installed). Run `az login` as an ' +
        'identity allowed to update the app registration, then re-run this script.',
    )
  }
  if (expectedTenant && account.tenantId && account.tenantId !== expectedTenant) {
    console.warn(
      `\u26a0 Logged-in tenant (${account.tenantId}) differs from RAYFIN_PUBLIC_TENANT_ID ` +
        `(${expectedTenant}). If the app lives in the latter, run ` +
        `\`az login --tenant ${expectedTenant}\` first.`,
    )
  }
  try {
    az(['rest', '--method', 'GET', '--uri', 'https://graph.microsoft.com/v1.0/me', '--query', 'id', '-o', 'tsv'])
  } catch (err) {
    if (isStaleTokenChallenge(err)) recoverStaleToken(expectedTenant)
    else fail('Microsoft Graph readiness probe failed.\nUnderlying error: ' + azErrorText(err))
  }
  return account
}

/** Pull `allowedRedirectUris:` out of rayfin.yml without a YAML dependency. */
function readAllowedRedirectUris(file) {
  if (!fs.existsSync(file)) return []
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/)
  const uris = []
  let inBlock = false
  let keyIndent = 0
  for (const line of lines) {
    if (!inBlock) {
      const m = line.match(/^(\s*)allowedRedirectUris:\s*$/)
      if (m) {
        inBlock = true
        keyIndent = m[1].length
      }
      continue
    }
    if (line.trim() === '') continue
    const indent = line.length - line.trimStart().length
    const item = line.match(/^\s*-\s+(\S+)\s*$/)
    if (item && indent > keyIndent) uris.push(item[1])
    else if (indent <= keyIndent) break
  }
  return uris
}

/** Origin only (scheme://host[:port]), no path/trailing slash. */
function toOrigin(value) {
  try {
    return new URL(value).origin
  } catch {
    return value.replace(/\/+$/, '')
  }
}

function isFabricHostingOrigin(value) {
  try {
    return new URL(value).hostname.endsWith('.webapp.fabricapps.net')
  } catch {
    return false
  }
}

export function synchronizeRedirectUris(current, desired) {
  const retained = current.filter((uri) => !isFabricHostingOrigin(uri) || desired.includes(toOrigin(uri)))
  return [...new Set([...retained, ...desired])]
}

// ─── STEP 1: SPA redirect URIs ───────────────────────────────────────────────

function registerRedirectUris(clientId) {
  console.log('\n\u2500\u2500 STEP 1: SPA redirect URIs \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500')

  const recordedHostingOrigins = readAllowedRedirectUris(files.rayfinYml)
    .map(toOrigin)
    .filter(isFabricHostingOrigin)
  const currentHostingOrigin = recordedHostingOrigins.at(-1)
  if (!currentHostingOrigin) {
    console.warn(
      '\u26a0 No allowedRedirectUris found in rayfin/rayfin.yml. Deploy once with ' +
        '`npx rayfin up` so it records the hosting origin, then re-run this script.',
    )
  }
  const desired = [...new Set([...(currentHostingOrigin ? [currentHostingOrigin] : []), ...DEV_ORIGINS])]

  console.log('Desired SPA origins   :')
  for (const o of desired) console.log('  -', o)

  let app
  try {
    app = JSON.parse(
      az(['ad', 'app', 'show', '--id', clientId, '--query', '{objectId:id,spa:spa.redirectUris}', '-o', 'json']),
    )
  } catch (err) {
    if (isStaleTokenChallenge(err)) {
      recoverStaleToken(tenantIdFromEnv)
      fail('Re-authenticated \u2014 please re-run the script to apply changes.')
    }
    fail(
      `Could not read app registration '${clientId}'. Either it does not exist in ` +
        'the signed-in tenant, or your account lacks directory read permission. ' +
        'Sign in as an owner / Application Administrator and retry.\n' +
        `Underlying error: ${azErrorText(err)}`,
    )
  }

  const current = Array.isArray(app.spa) ? app.spa : []
  const synchronized = synchronizeRedirectUris(current, desired)
  const added = synchronized.filter((uri) => !current.includes(uri))
  const removed = current.filter((uri) => !synchronized.includes(uri))

  if (added.length === 0 && removed.length === 0) {
    console.log('\u2713 All required SPA redirect URIs are already registered. Nothing to do.')
    return
  }

  if (added.length > 0) console.log('Will ADD these SPA redirect URIs:')
  for (const u of added) console.log('  +', u)
  if (removed.length > 0) console.log('Will REMOVE stale Fabric SPA redirect URIs:')
  for (const u of removed) console.log('  -', u)

  if (dryRun) {
    console.log('(dry run \u2014 no changes written)')
    return
  }

  const bodyFile = path.join(os.tmpdir(), `spa-redirect-${process.pid}.json`)
  fs.writeFileSync(bodyFile, JSON.stringify({ spa: { redirectUris: synchronized } }), 'utf8')
  try {
    az([
      'rest',
      '--method', 'PATCH',
      '--uri', `https://graph.microsoft.com/v1.0/applications/${app.objectId}`,
      '--headers', 'Content-Type=application/json',
      '--body', `@${bodyFile}`,
    ])
  } catch (err) {
    fs.rmSync(bodyFile, { force: true })
    fail(
      'Graph PATCH failed \u2014 your account is likely not allowed to update this app ' +
        'registration. Ask an owner / Application Administrator to add the origins, ' +
        `or grant yourself the role. Underlying error: ${err.message ?? err}`,
    )
  }
  fs.rmSync(bodyFile, { force: true })

  const after = JSON.parse(az(['ad', 'app', 'show', '--id', clientId, '--query', 'spa.redirectUris', '-o', 'json']))
  console.log('\u2713 Registered. SPA redirect URIs are now:')
  for (const u of after) console.log('  -', u)
}

// ─── STEP 2: delegated permissions + consent ─────────────────────────────────

/** Resolve requested scope values on a resource SP to their scope ids. */
function resolveScopeIds(resourceAppId, scopeValues) {
  let scopes
  try {
    scopes = JSON.parse(
      az(['ad', 'sp', 'show', '--id', resourceAppId, '--query', 'oauth2PermissionScopes', '-o', 'json']),
    )
  } catch (err) {
    throw new Error(
      `Could not read the resource service principal ${resourceAppId} (it may not ` +
        `exist in this tenant yet). Underlying error: ${azErrorText(err)}`,
    )
  }
  const byValue = new Map((scopes ?? []).map((s) => [s.value, s.id]))
  return scopeValues.map((v) => {
    const id = byValue.get(v)
    if (!id) throw new Error(`Scope '${v}' not found on resource ${resourceAppId}.`)
    return { value: v, id }
  })
}

function grantDelegatedPermissions(clientId) {
  console.log('\n\u2500\u2500 STEP 2: delegated permissions + consent \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500')

  // Resolve every required scope id up front so a bad resource fails loudly.
  const resolved = []
  for (const req of REQUIRED_DELEGATED) {
    try {
      resolved.push({ ...req, scopes: resolveScopeIds(req.resourceAppId, req.scopeValues) })
      console.log(`  ${req.label}: ${req.scopeValues.join(', ')}`)
    } catch (err) {
      console.warn(`\u26a0 Skipping ${req.label}: ${err.message}`)
    }
  }
  if (resolved.length === 0) fail('No delegated permissions could be resolved. Check `az login` and tenant.')

  let current
  try {
    current = JSON.parse(az(['ad', 'app', 'show', '--id', clientId, '--query', 'requiredResourceAccess', '-o', 'json']))
  } catch (err) {
    if (isStaleTokenChallenge(err)) {
      recoverStaleToken(tenantIdFromEnv)
      fail('Re-authenticated \u2014 please re-run the script to apply changes.')
    }
    fail(`Could not read app registration '${clientId}'.\nUnderlying error: ${azErrorText(err)}`)
  }
  if (!Array.isArray(current)) current = []

  // MERGE into requiredResourceAccess so existing permissions are preserved.
  const merged = current.map((r) => ({ resourceAppId: r.resourceAppId, resourceAccess: r.resourceAccess ?? [] }))
  let changed = false
  for (const req of resolved) {
    let block = merged.find((r) => r.resourceAppId === req.resourceAppId)
    if (!block) {
      block = { resourceAppId: req.resourceAppId, resourceAccess: [] }
      merged.push(block)
    }
    for (const s of req.scopes) {
      if (!block.resourceAccess.some((a) => a.id === s.id)) {
        block.resourceAccess.push({ id: s.id, type: 'Scope' })
        changed = true
      }
    }
  }

  if (!changed) {
    console.log('\u2713 All required delegated permissions are already present.')
  } else if (dryRun) {
    console.log('Would ADD the missing delegated permissions above.')
    console.log('(dry run \u2014 no changes written; skipping admin consent)')
    return
  } else {
    const bodyFile = path.join(os.tmpdir(), `rra-${process.pid}.json`)
    fs.writeFileSync(bodyFile, JSON.stringify(merged), 'utf8')
    try {
      az(['ad', 'app', 'update', '--id', clientId, '--required-resource-accesses', `@${bodyFile}`])
    } catch (err) {
      fs.rmSync(bodyFile, { force: true })
      fail(`Updating the app registration failed.\nUnderlying error: ${err.message ?? err}`)
    }
    fs.rmSync(bodyFile, { force: true })
    console.log('\u2713 Added the missing delegated permissions.')
  }

  if (dryRun) {
    console.log('(dry run \u2014 skipping admin consent)')
    return
  }

  // Blanket admin consent first (Graph + Power BI + ADX). It can silently skip
  // some resources, so we ALSO create each delegated grant directly below.
  console.log('Granting admin consent for the app...')
  try {
    az(['ad', 'app', 'permission', 'admin-consent', '--id', clientId])
    console.log('\u2713 Admin consent requested for the app.')
  } catch (err) {
    console.warn(
      '\u26a0 Could not grant blanket admin consent (need Privileged Role ' +
        'Administrator / Global Administrator). Will still try the targeted grants ' +
        `below. (Underlying error: ${err.message ?? err})`,
    )
  }

  // Ensure each delegated grant exists specifically (admin-consent can skip them).
  let clientSpId
  try {
    clientSpId = az(['ad', 'sp', 'show', '--id', clientId, '--query', 'id', '-o', 'tsv']).trim()
  } catch (err) {
    console.warn(`\u26a0 Could not resolve the app service principal; grant consent manually. (${err.message ?? err})`)
    return
  }

  for (const req of resolved) {
    console.log(`Ensuring consent grant: ${req.label}...`)
    try {
      const resourceSpId = az(['ad', 'sp', 'show', '--id', req.resourceAppId, '--query', 'id', '-o', 'tsv']).trim()
      const existing = JSON.parse(
        az([
          'rest', '--method', 'GET',
          '--uri', `https://graph.microsoft.com/v1.0/servicePrincipals/${clientSpId}/oauth2PermissionGrants`,
          '--query', 'value', '-o', 'json',
        ]),
      )
      const wantScopes = req.scopeValues
      // A per-user (Principal) grant proves only that one operator consented. It
      // must not be mistaken for the tenant-wide AllPrincipals grant promised by
      // this script and required for an enterprise rollout with no user prompts.
      const grant = (Array.isArray(existing) ? existing : []).find(
        (g) => g.resourceId === resourceSpId && g.consentType === 'AllPrincipals',
      )
      const haveScopes = new Set((grant?.scope ?? '').split(/\s+/).filter(Boolean))
      const missing = wantScopes.filter((s) => !haveScopes.has(s))

      if (missing.length === 0) {
        console.log(`\u2713 ${req.label}: consent already present.`)
        continue
      }

      if (grant) {
        // Extend the existing grant's scope string.
        const nextScope = [...haveScopes, ...missing].join(' ')
        const patchFile = path.join(os.tmpdir(), `grant-patch-${process.pid}-${resourceSpId}.json`)
        fs.writeFileSync(patchFile, JSON.stringify({ scope: nextScope }), 'utf8')
        try {
          az([
            'rest', '--method', 'PATCH',
            '--uri', `https://graph.microsoft.com/v1.0/oauth2PermissionGrants/${grant.id}`,
            '--headers', 'Content-Type=application/json',
            '--body', `@${patchFile}`,
          ])
          console.log(`\u2713 ${req.label}: extended consent (${missing.join(', ')}).`)
        } finally {
          fs.rmSync(patchFile, { force: true })
        }
      } else {
        const grantFile = path.join(os.tmpdir(), `grant-${process.pid}-${resourceSpId}.json`)
        fs.writeFileSync(
          grantFile,
          JSON.stringify({ clientId: clientSpId, consentType: 'AllPrincipals', resourceId: resourceSpId, scope: wantScopes.join(' ') }),
          'utf8',
        )
        try {
          az([
            'rest', '--method', 'POST',
            '--uri', 'https://graph.microsoft.com/v1.0/oauth2PermissionGrants',
            '--headers', 'Content-Type=application/json',
            '--body', `@${grantFile}`,
          ])
          console.log(`\u2713 ${req.label}: created consent grant.`)
        } finally {
          fs.rmSync(grantFile, { force: true })
        }
      }
    } catch (err) {
      console.warn(
        `\u26a0 Could not verify/create the consent grant for ${req.label} ` +
          '(need Privileged Role Administrator / Global Administrator). Grant it manually:\n' +
          `   Entra portal \u2192 App registrations \u2192 ${clientId} \u2192 API permissions \u2192\n` +
          '   "Grant admin consent for <tenant>".\n' +
          `   (Underlying error: ${err.message ?? err})`,
      )
    }
  }
}

// ─── Main ────────────────────────────────────────────────────────────────────

function main() {
  if (redirectOnly && grantOnly) {
    fail('Pass at most one of --redirect-only / --grant-only (omit both to run both steps).')
  }

  const env = parseEnv(files.rayfinEnv)
  const clientId = env.RAYFIN_PUBLIC_AAD_CLIENT_ID
  const tenantId = env.RAYFIN_PUBLIC_TENANT_ID
  tenantIdFromEnv = tenantId ?? null

  if (!clientId) {
    fail('RAYFIN_PUBLIC_AAD_CLIENT_ID is not set in rayfin/.env. Add the Entra SPA app (client) id first.')
  }

  const steps = redirectOnly ? ['redirect'] : grantOnly ? ['grant'] : ['redirect', 'grant']
  console.log('Entra app (client) id :', clientId)
  console.log('Steps                 :', steps.join(' + '), dryRun ? '(dry run)' : '')

  ensureAzLogin(tenantId)

  if (steps.includes('redirect')) registerRedirectUris(clientId)
  if (steps.includes('grant')) grantDelegatedPermissions(clientId)

  console.log(
    '\nNote: Entra can edge-cache app config for a minute or two. Hard-refresh ' +
      '(Ctrl+F5), then click the app\u2019s Connect buttons once to consent to the ' +
      'cluster / GraphQL scopes. Live panels should populate within ~15s.',
  )
}

if (path.resolve(process.argv[1] ?? '') === fileURLToPath(import.meta.url)) main()
