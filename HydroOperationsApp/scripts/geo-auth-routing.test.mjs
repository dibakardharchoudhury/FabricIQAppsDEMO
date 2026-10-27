import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/services/rayfin.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source.replaceAll('import.meta.env', 'testEnv'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function fixture(overrides = {}, signedIn = true) {
  const geo = { source: 'geo-context' }
  const core = { source: 'core' }
  let configured
  const exports = {}
  const dependencies = {
    '@microsoft/rayfin-auth-provider-fabric': {},
    '@microsoft/rayfin-client': { ConnectorsRayfinClient: class {
      constructor(options) {
        configured = options
        this.connectors = { hydrolakehouse: core, hydrogeo: geo }
        this.auth = { getSession: () => signedIn ? {
          isAuthenticated: true, user: { id: 'user', email: 'user@example.test' },
        } : null }
      }
    } },
    './lakehouseConnector': { lakehouseConnectorConfig: { source: 'core' } },
    './operationalConnector': { operationalConnectorConfig: { source: 'sql' } },
    './geoConnector': { geoConnectorConfig: { source: 'geo' } },
    './seedData': {},
  }
  new Function('require', 'exports', 'testEnv', compiled)(
    name => {
      assert.ok(name in dependencies, `Unexpected dependency ${name}`)
      return dependencies[name]
    }, exports, {
      VITE_RAYFIN_API_URL: 'https://backend.example.test',
      VITE_RAYFIN_PUBLISHABLE_KEY: 'pk-test',
      VITE_RAYFIN_WORKSPACE_ID: 'workspace',
      VITE_RAYFIN_ITEM_ID: 'appbackend',
      VITE_RAYFIN_LAKEHOUSE_ID: 'core-lakehouse',
      VITE_RAYFIN_LAKEHOUSE_SQL_ENDPOINT_ID: 'core-endpoint',
      VITE_RAYFIN_GEO_LAKEHOUSE_ID: 'geo-lakehouse',
      VITE_RAYFIN_GEO_SQL_ENDPOINT_ID: 'geo-endpoint',
      ...overrides,
    },
  )
  return { service: exports, geo, core, configured }
}

test('Core, GeoContext and SQL have separate connector registrations', () => {
  const { service, geo, core, configured } = fixture()
  assert.deepEqual(Object.keys(configured.connectors).sort(), ['hydrogeo', 'hydrolakehouse', 'hydrooperations'])
  assert.equal(service.connectedGeo(), geo)
  assert.equal(service.connectedLakehouse(), core)
})

test('missing GeoContext identity cannot silently use Core or Eventhouse', () => {
  for (const key of ['VITE_RAYFIN_GEO_LAKEHOUSE_ID', 'VITE_RAYFIN_GEO_SQL_ENDPOINT_ID']) {
    const { service } = fixture({ [key]: undefined })
    assert.throws(() => service.connectedGeo(), /GeoContext connector identity/)
  }
  assert.throws(() => fixture({}, false).service.connectedGeo(), /Sign in to Fabric/)
})

test('map connector reconnection does not request Eventhouse consent', () => {
  const page = readFileSync(new URL('../src/ui-shared/pages/OperationsMapPage.tsx', import.meta.url), 'utf8')
  assert.match(page, /await signInToRayfin\(\)/)
  assert.doesNotMatch(page, /beginInteractiveConnect\('telemetry'\)/)
})
