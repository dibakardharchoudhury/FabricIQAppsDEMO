import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
import { applyDataAgentProgress } from '../src/services/dataAgentProgress.ts'

const source = await readFile(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
const hydroDataHookSource = await readFile(new URL('../src/ui-shared/hooks/useHydroOperationsData.ts', import.meta.url), 'utf8')
const serviceDependencies = Object.fromEntries(await Promise.all([
  'ontologyDiscovery', 'ontologyCache', 'ontologyContract', 'ontologyDefinition',
  'ontologyGraphQuery', 'ontologyArtifactDiscovery', 'singleFlight', 'dataAgentProgress',
].map(async name => [`./${name}`, await import(`../src/services/${name}.ts`)])))
const serviceCode = ts.transpileModule(source.replaceAll('import.meta.env', 'testEnv'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const workspaceId = '11111111-1111-1111-1111-111111111111'
const ontologyId = '22222222-2222-2222-2222-222222222222'
const graphModelId = '33333333-3333-3333-3333-333333333333'
const graphDefinition = { definition: { parts: [{
  path: 'entities/facilities.tmdl', payloadType: 'InlineBase64',
  payload: Buffer.from('entity facilities\n    backingTable: silver_facilities\n    keyProperty: facility_id\n    property facility_id\n        dataType: string\n').toString('base64'),
}] } }
const mcpDependencies = {
  '@modelcontextprotocol/sdk/client/index.js': { Client: class {} },
  '@modelcontextprotocol/sdk/client/streamableHttp.js': { StreamableHTTPClientTransport: class {} },
}

function graphService(onRequest = async () => undefined) {
  const requests = []
  const dependencies = {
    ...serviceDependencies,
    ...mcpDependencies,
    './artifactDiscovery': { selectDataAgent: () => undefined },
    '@azure/msal-browser': { PublicClientApplication: class {
      async initialize() {}
      async handleRedirectPromise() {}
      getAllAccounts() { return [{}] }
      async acquireTokenSilent() { return { accessToken: 'test-token' } }
    } },
  }
  const exports = {}
  new Function('require', 'exports', 'testEnv', 'fetch', 'location', serviceCode)(
    name => {
      assert.ok(dependencies[name], `Unexpected dependency: ${name}`)
      return dependencies[name]
    }, exports, {
      VITE_RAYFIN_AAD_CLIENT_ID: 'test-client', VITE_RAYFIN_TENANT_ID: 'test-tenant',
      VITE_RAYFIN_WORKSPACE_ID: workspaceId,
      VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING: JSON.stringify({ workspaceId, ontologyId, graphModelId }),
    }, async (url, init) => {
      requests.push(String(url))
      const override = await onRequest(String(url), init)
      if (override) return override
      if (url.endsWith('/items')) return Response.json({ value: [
        { id: ontologyId, type: 'Ontology', displayName: 'Hydro v2' },
      ] })
      if (url.endsWith(`/ontologies/${ontologyId}`)) return Response.json({ properties: { generation: 2 } })
      if (url.endsWith('/getDefinition')) return Response.json(graphDefinition)
      if (url.endsWith(`/graphModels/${graphModelId}`)) return Response.json({
        id: graphModelId, type: 'GraphModel', displayName: 'Hydro native graph',
      })
      if (url.includes('/getQueryableGraphType')) return Response.json({
        nodeTypes: [{ alias: 'f', labels: ['facilities'] }], edgeTypes: [],
      })
      if (url.includes('/executeQuery')) return Response.json({
        status: { code: '00000' }, result: { kind: 'TABLE', data:
          JSON.parse(init.body).query.startsWith('MATCH (n)') ? [{
            node: JSON.stringify({ oid: 'f1', labels: ['facilities'], properties: { facility_id: 'F1' } }),
          }] : [],
        },
      })
      throw new Error(`Unexpected request: ${url}`)
    }, { origin: 'https://app.example.test' },
  )
  return { service: exports, requests }
}

function weatherService() {
  const requests = []
  const dependencies = {
    ...serviceDependencies,
    ...mcpDependencies,
    './artifactDiscovery': { selectDataAgent: () => undefined },
    '@azure/msal-browser': { PublicClientApplication: class {
      async initialize() {}
      async handleRedirectPromise() {}
      getAllAccounts() { return [{}] }
      async acquireTokenSilent() { return { accessToken: 'test-token' } }
    } },
  }
  const exports = {}
  new Function('require', 'exports', 'testEnv', 'fetch', 'location', serviceCode)(
    name => {
      assert.ok(dependencies[name], `Unexpected dependency: ${name}`)
      return dependencies[name]
    }, exports, {
      VITE_RAYFIN_AAD_CLIENT_ID: 'test-client', VITE_RAYFIN_TENANT_ID: 'test-tenant',
      VITE_RAYFIN_WORKSPACE_ID: workspaceId,
      VITE_RAYFIN_STID_GRAPHQL_ID: 'weather-api',
    }, async (url) => {
      requests.push(String(url))
      if (url.endsWith('/graphqlapis/weather-api/graphql')) {
        return Response.json({
          data: {
            locations: { items: [{ location_id: 'L1', location_name: 'Station 1', latitude: 1, longitude: 2 }] },
            areas: { items: [] },
            variables: { items: [{ variable_id: 'temperature', canonical_unit: 'C' }] },
            observations: { items: [] },
            forecasts: { items: [] },
          },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    }, { origin: 'https://app.example.test' },
  )
  return { service: exports, requests }
}

test('Data Agent MCP runtime sends unchanged questions and keeps memory in the MCP session', () => {
  assert.match(source, /import \{ Client \} from '@modelcontextprotocol\/sdk\/client\/index\.js'/)
  assert.match(source, /import \{ StreamableHTTPClientTransport \} from '@modelcontextprotocol\/sdk\/client\/streamableHttp\.js'/)
  assert.doesNotMatch(source, /import\('@modelcontextprotocol\/sdk\/client\//)
  assert.match(source, /arguments: \{ \[session\.questionArgument\]: question \}/)
  assert.doesNotMatch(source, /dataAgentQuestion|dataAgentConversation/)
  assert.match(source, /resetDataAgentConversation\(\) \{\s+invalidateDataAgentMcpSession\(\)/)
  assert.match(source, /dataAgentMcpSession\?\.endpoint === endpoint/)
  assert.match(source, /if \(dataAgentMcpSession === session\) invalidateDataAgentMcpSession\(\)/)
  assert.match(source, /client\.onclose = \(\) =>/)
  assert.match(source, /isDisconnectedDataAgentMcpError\(error\)/)
  assert.match(source, /callDataAgentMcp\(endpoint, token, question, onProgress, onSteps, false\)/)
  assert.match(source, /Promise\.all\(\[verification, getDataAgentMcpSession\(endpoint, token\)\]\)/)
  assert.match(source, /dataAgentVerification\?\.key === key/)
  assert.match(source, /dataAgentMcpTool\?\.endpoint === endpoint/)
  assert.match(source, /dataAgentMcpTool = tool/)
  assert.match(source, /dataAgentMcpTool = undefined/)
  assert.match(source, /invalidateDataAgentVerification\(\)\s+invalidateDataAgentMcpSession\(\)/)
  assert.match(source, /onprogress: progress/)
  assert.doesNotMatch(source, /selectedFacility|selectedAsset/)
  assert.match(hydroDataHookSource, /await initAuth\(\)[\s\S]+void warmDataAgentMcp\(\)/)
})

test('Data Agent progress maps real Fabric tool lifecycle without inventing arguments', () => {
  const steps = []
  assert.equal(applyDataAgentProgress(steps, 'Run step created: tool_calls (Tools: trace.analyze_sql_database)', 100), true)
  assert.equal(applyDataAgentProgress(steps, 'Run step in progress: tool_calls (Tools: trace.analyze_sql_database)', 110), false)
  assert.equal(applyDataAgentProgress(steps, 'Run step completed: tool_calls (Tools: trace.analyze_sql_database)', 160), true)
  assert.deepEqual(steps, [{
    tool: 'trace.analyze_sql_database',
    status: 'done',
    detail: 'Fabric Data Agent internal tool',
    summary: 'completed',
    elapsedMs: 60,
  }])
  assert.equal(applyDataAgentProgress(steps, 'Message completed msg-1', 170), false)
})

test('weather loading uses the deployment-verified GraphQL endpoint without workspace discovery', async () => {
  const { service, requests } = weatherService()
  const weather = await service.queryWeatherData()
  assert.equal(weather.locations[0].location_name, 'Station 1')
  assert.deepEqual(requests, [
    `https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/graphqlapis/weather-api/graphql`,
  ])
})

test('graph loading reuses one definition and coalesces concurrent native requests', async () => {
  const { service, requests } = graphService()
  const graphs = await Promise.all([service.queryOntologyGraph(), service.queryOntologyGraph()])
  assert.equal(graphs[0].nodes.length, 1)
  assert.deepEqual(graphs[0], graphs[1])
  assert.equal(requests.filter(url => url.endsWith('/getDefinition')).length, 1)
  assert.equal(requests.filter(url => url.endsWith('/items')).length, 0, 'Explicit graph lineage must bypass workspace discovery')
  assert.equal(requests.filter(url => url.includes('/executeQuery')).length, 2)
  const coldRequests = requests.length
  await service.queryOntologyGraph()
  await service.queryOntologyContract()
  assert.equal(requests.length, coldRequests, 'Warm graph reads must not issue more network requests')
})

test('discovery refresh during a graph read does not surface an obsolete-request error', async () => {
  let started
  const waiting = new Promise(resolve => { started = resolve })
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  let definitions = 0
  const { service } = graphService(async url => {
    if (url.endsWith('/getDefinition') && ++definitions === 1) {
      started()
      await pending
    }
  })
  const graph = service.queryOntologyGraph()
  await waiting
  service.clearWorkspaceConfigCache()
  finish()
  assert.equal((await graph).nodes.length, 1)
})

test('workspace refresh does not invalidate a same-ontology contract request in flight', async () => {
  let started
  const waiting = new Promise(resolve => { started = resolve })
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  let definitions = 0
  const { service } = graphService(async url => {
    if (url.endsWith('/getDefinition') && ++definitions === 2) {
      started()
      await pending
    }
  })
  await service.queryOntologyGraph()
  const contract = service.queryOntologyContract(true)
  await waiting
  service.clearWorkspaceConfigCache()
  const graph = service.queryOntologyGraph()
  finish()
  const [current, topology] = await Promise.all([contract, graph])
  assert.equal(current.id, topology.ontologyId)
})

test('periodic native refresh bypasses only graph cache, not the valid ontology definition', async () => {
  const { service, requests } = graphService()
  await service.queryOntologyGraph()
  await service.queryOntologyGraph(true)
  assert.equal(requests.filter(url => url.endsWith('/getDefinition')).length, 1)
  assert.equal(requests.filter(url => url.includes('/executeQuery')).length, 4)
})

test('native failures are propagated and a later read retries instead of returning stale graph data', async () => {
  let rejectQuery = false
  const { service } = graphService(async url =>
    rejectQuery && url.includes('/executeQuery') ? new Response('denied', { status: 403 }) : undefined)
  await service.queryOntologyGraph()
  rejectQuery = true
  await assert.rejects(service.queryOntologyGraph(true), /403/)
  await assert.rejects(service.queryOntologyGraph(), /403/)
  rejectQuery = false
  assert.equal((await service.queryOntologyGraph()).nodes.length, 1)
})

test('failed contract refresh prevents reuse of both discovery data and cached native results', async () => {
  let denied = false
  const { service } = graphService(async url =>
    denied && url.endsWith(`/ontologies/${ontologyId}`) ? new Response('denied', { status: 403 }) : undefined)
  await service.queryOntologyGraph()
  denied = true
  await assert.rejects(service.queryOntologyContract(true), /403/)
  await assert.rejects(service.queryOntologyGraph(), /403/)
  denied = false
  assert.equal((await service.queryOntologyGraph()).nodes.length, 1)
})

test('workspace refresh cannot replace the explicit ontology binding with discovery', async () => {
  const replacement = '44444444-4444-4444-4444-444444444444'
  let changed = false
  const { service, requests } = graphService(async url => {
    if (changed && url.endsWith('/items')) return Response.json({
      value: [{ id: replacement, type: 'Ontology', displayName: 'Replacement v2' }],
    })
    if (url.endsWith(`/ontologies/${replacement}`)) return Response.json({ properties: { generation: 2 } })
  })
  await service.queryOntologyGraph()
  changed = true
  service.clearWorkspaceConfigCache()
  assert.equal((await service.queryOntologyGraph()).nodes.length, 1)
  assert.equal((await service.queryOntologyContract()).id, ontologyId)
  assert.equal(requests.filter(url => url.endsWith('/items')).length, 0)
})
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
