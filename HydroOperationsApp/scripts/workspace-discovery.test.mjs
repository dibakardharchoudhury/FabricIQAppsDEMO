import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
// Execute the production discovery and environment readers without browser MSAL or service writes.
const constants = source.slice(source.indexOf('const clientId ='), source.indexOf('const msal ='))
const discovery = source.slice(source.indexOf('type WorkspaceItem ='), source.indexOf('// ---- Fabric Embed'))
assert.ok(constants && discovery)
const { outputText } = ts.transpileModule((constants + discovery).replaceAll('import.meta.env', 'testEnv'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
})
const runDiscovery = new Function(
  'testEnv', 'fabricToken', 'fetch', 'discoverOntology', 'selectDataAgent', 'console',
  `${outputText}\nreturn discoverConfig(false, 0)`,
)

const items = [
  { id: 'managed', type: 'Eventhouse', displayName: 'Ontology managed' },
  { id: 'managed-db', type: 'KQLDatabase', displayName: 'Telemetry' },
  { id: 'main', type: 'Eventhouse', displayName: 'RTI_Demo_Eventhouse_V3' },
  { id: 'first-db', type: 'KQLDatabase', displayName: 'Other database' },
  { id: 'main-db', type: 'KQLDatabase', displayName: 'Telemetry' },
  { id: 'other-api', type: 'GraphQLApi', displayName: 'Other API' },
  { id: 'stid', type: 'GraphQLApi', displayName: 'Hydro_STID_API' },
  { id: 'stream', type: 'DataPipeline', displayName: '02_Pipe_Stream' },
  { id: 'seed', type: 'Notebook', displayName: 'RTI_011_seed_sql_wire_graphql_agent' },
]
const baseEnv = {
  VITE_RAYFIN_WORKSPACE_ID: 'workspace',
  VITE_RAYFIN_EVENTHOUSE_NAME: 'RTI_Demo_Eventhouse_V3',
}
const graphql = id => `https://api.fabric.microsoft.com/v1/workspaces/workspace/graphqlapis/${id}/graphql`

async function discover(env = {}, workspaceItems = items) {
  const warnings = []
  const requests = []
  const config = await runDiscovery(
    { ...baseEnv, ...env }, async () => 'test-token',
    async url => {
      requests.push(url)
      if (url.endsWith('/items')) return Response.json({ value: workspaceItems })
      if (url.endsWith('/eventhouses/main')) return Response.json({
        properties: { queryServiceUri: 'https://main.example', databasesItemIds: ['first-db', 'main-db'] },
      })
      if (url.endsWith('/eventhouses/managed')) return Response.json({
        properties: { queryServiceUri: 'https://managed.example', databasesItemIds: ['managed-db'] },
      })
      throw new Error(`Unexpected request: ${url}`)
    },
    async () => ({}), () => undefined, { warn: (...args) => warnings.push(args) },
  )
  return { config, warnings, requests }
}

test('verified database and GraphQL IDs win over first items, including managed databases', async () => {
  const { config, warnings } = await discover({
    VITE_RAYFIN_EVENTHOUSE_ID: 'main',
    VITE_RAYFIN_KQL_DATABASE_ID: 'main-db',
    VITE_RAYFIN_KQL_DATABASE: 'Telemetry',
    VITE_RAYFIN_STID_GRAPHQL_ID: 'stid',
    VITE_RAYFIN_STID_GRAPHQL_NAME: 'Hydro_STID_API',
  })
  assert.equal(config.eventhouseQueryUri, 'https://main.example')
  assert.equal(config.kqlDatabase, 'Telemetry')
  assert.equal(config.graphqlUrl, graphql('stid'))
  assert.deepEqual(warnings, [])
})

test('explicit names resolve only within the selected Eventhouse and select the STID API', async () => {
  const { config, warnings } = await discover({
    VITE_RAYFIN_KQL_DATABASE: 'Telemetry',
    VITE_RAYFIN_STID_GRAPHQL_NAME: 'Hydro_STID_API',
  })
  assert.equal(config.kqlDatabase, 'Telemetry')
  assert.equal(config.graphqlUrl, graphql('stid'))
  assert.deepEqual(warnings, [])
})

test('explicit GraphQL URL is retained rather than replaced by an unrelated first API', async () => {
  const { config } = await discover({ VITE_RAYFIN_STID_GRAPHQL_URL: graphql('stid') })
  assert.equal(config.graphqlUrl, graphql('stid'))
})

test('verified Eventhouse ID wins over a missing name instead of choosing the managed Eventhouse', async () => {
  const { config, requests, warnings } = await discover({
    VITE_RAYFIN_EVENTHOUSE_ID: 'main',
    VITE_RAYFIN_EVENTHOUSE_NAME: 'Old Eventhouse name',
  })
  assert.equal(config.eventhouseQueryUri, 'https://main.example')
  assert.ok(!requests.some(url => url.endsWith('/eventhouses/managed')))
  assert.deepEqual(warnings, [])
})

test('missing explicit API selection reports failure without switching to the first API', async () => {
  const { config, warnings } = await discover({ VITE_RAYFIN_STID_GRAPHQL_ID: 'missing' })
  assert.equal(config.graphqlUrl, undefined)
  assert.equal(warnings.length, 1)
  assert.match(config.ontologyError, /configured STID GraphQL API was not found/)
})

test('absent explicit selections preserve main first-database and first-API defaults', async () => {
  const { config, warnings } = await discover()
  assert.equal(config.eventhouseQueryUri, 'https://main.example')
  assert.equal(config.kqlDatabase, 'Other database')
  assert.equal(config.graphqlUrl, graphql('other-api'))
  assert.equal(config.pipelineId, 'stream')
  assert.equal(config.postseedNotebookId, 'seed')
  assert.deepEqual(warnings, [])
})

test('a database pinned to another Eventhouse cannot replace scoped fallback telemetry', async () => {
  const { config, warnings } = await discover({
    VITE_RAYFIN_KQL_DATABASE_ID: 'managed-db',
    VITE_RAYFIN_KQL_DATABASE: 'Telemetry',
    VITE_RAYFIN_KQL_CLUSTER_URI: 'https://verified.example',
  })
  assert.equal(config.eventhouseQueryUri, 'https://verified.example')
  assert.equal(config.kqlDatabase, 'Telemetry')
  assert.equal(warnings.length, 1)
})

test('producer defaults discover pipeline and notebook created after deployment', async () => {
  const env = {
    VITE_RAYFIN_STREAM_PIPELINE_NAME: '02_Pipe_Stream',
    VITE_RAYFIN_STREAM_PIPELINE_ID: '',
    VITE_RAYFIN_POSTSEED_NOTEBOOK_NAME: 'RTI_011_seed_sql_wire_graphql_agent',
    VITE_RAYFIN_POSTSEED_NOTEBOOK_ID: '',
  }
  const before = await discover(env, items.filter(item => !['DataPipeline', 'Notebook'].includes(item.type)))
  assert.equal(before.config.pipelineId, '')
  assert.equal(before.config.postseedNotebookId, '')
  const after = await discover(env)
  assert.equal(after.config.pipelineId, 'stream')
  assert.equal(after.config.postseedNotebookId, 'seed')
})
