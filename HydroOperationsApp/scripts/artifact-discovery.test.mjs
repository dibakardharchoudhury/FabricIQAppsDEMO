import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { tsImport } from 'tsx/esm/api'
import { invokeVerifiedDataAgent, requireDataAgentEndpoint, selectDataAgent, verifyPublishedAgentOntology } from '../src/services/artifactDiscovery.ts'

test('selects the latest natural RTI Data Agent version', () => {
  const selected = selectDataAgent([
    { id: 'other', type: 'DataAgent', displayName: 'Another Agent' },
    { id: 'v9', type: 'DataAgent', displayName: 'RTI_Demo_Agent_V9' },
    { id: 'v10', type: 'DataAgent', displayName: 'RTI_Demo_Agent_V10' },
  ])

  assert.equal(selected?.id, 'v10')
})

test('uses the only Data Agent when it has a custom name', () => {
  const selected = selectDataAgent([
    { id: 'custom', type: 'DataAgent', displayName: 'Customer Hydro Agent' },
  ])

  assert.equal(selected?.id, 'custom')
})

test('does not guess between multiple unrelated Data Agents', () => {
  const selected = selectDataAgent([
    { id: 'first', type: 'DataAgent', displayName: 'First Agent' },
    { id: 'second', type: 'DataAgent', displayName: 'Second Agent' },
  ])

  assert.equal(selected, undefined)
})

test('absent optional agents throw actionable errors instead of returning successful answers', () => {
  assert.throws(() => requireDataAgentEndpoint(undefined, 1), /Replace the existing v1 Ontology/)
  assert.throws(() => requireDataAgentEndpoint(undefined, 2), /Ontology v2 source onboarding may be blocked/)
  assert.throws(() => requireDataAgentEndpoint(undefined, 2), /data_agent_deployment_status and data_agent_deployment_reason/)
})

test('a present endpoint cannot bypass v2 generation verification', () => {
  const endpoint = 'https://api.fabric.microsoft.com/v1/mcp/workspaces/workspace/dataagents/agent/agent'
  assert.throws(() => requireDataAgentEndpoint(endpoint, 1), /Replace the existing v1 Ontology/)
  for (const generation of [undefined, null, '2', 3]) assert.throws(() => requireDataAgentEndpoint(endpoint, generation), /could not be verified/)
  assert.equal(requireDataAgentEndpoint(endpoint, 2), endpoint)
})

const datasource = (source, stage = 'published') => ({
  path: `Files/Config/${stage}/ontology-Hydro/datasource.json`,
  payload: Buffer.from(JSON.stringify(source)).toString('base64'),
  payloadType: 'InlineBase64',
})
const verify = parts => verifyPublishedAgentOntology({ definition: { parts } }, 'workspace', 'v2-item')
const matchingSource = { type: 'ontology', workspaceId: 'workspace', artifactId: 'v2-item' }

test('published matching ontology IDs verify configured source identity only', () => {
  assert.doesNotThrow(() => verify([datasource(matchingSource)]))
  assert.doesNotThrow(() => verify([datasource({ ...matchingSource, workspaceId: 'WORKSPACE', artifactId: 'V2-ITEM' })]))
  for (const parts of [[], [datasource(matchingSource, 'draft')], [datasource({ type: 'lakehouse' })]]) {
    assert.throws(() => verify(parts), /source is unverified/)
  }
  assert.throws(() => verify([datasource({ ...matchingSource, artifactId: 'legacy-item' })]), /Replace legacy sources/)
  assert.throws(() => verify([datasource({ ...matchingSource, workspaceId: 'another-workspace' })]), /different or unverified Ontology/)
  assert.throws(() => verify([datasource(matchingSource), datasource({ ...matchingSource, artifactId: 'legacy-item' })]), /Replace legacy sources/)
})

test('verified published v2 identity invokes MCP exactly once after reading its definition', async () => {
  const calls = []
  const answer = { text: 'Actual MCP response' }
  const result = await invokeVerifiedDataAgent(
    { generation: 2, workspaceId: 'workspace', ontologyId: 'v2-item' },
    async () => {
      calls.push('definition')
      return { definition: { parts: [datasource(matchingSource)] } }
    },
    async () => { calls.push('mcp'); return answer },
  )
  assert.deepEqual(calls, ['definition', 'mcp'])
  assert.equal(result, answer)
})

test('verified published v2 identity propagates runtime product errors unchanged', async () => {
  let mcpCalls = 0
  const runtimeError = new Error('Ontology v2 execution is not supported by this backend')
  await assert.rejects(invokeVerifiedDataAgent(
    { generation: 2, workspaceId: 'workspace', ontologyId: 'v2-item' },
    async () => ({ definition: { parts: [datasource(matchingSource)] } }),
    async () => { mcpCalls++; throw runtimeError },
  ), error => error === runtimeError)
  assert.equal(mcpCalls, 1)
})

test('v2 ontology plus legacy, unrelated, draft-only or unverified agent never invokes MCP', async () => {
  const candidate = selectDataAgent([
    { id: 'older', type: 'DataAgent', displayName: 'RTI_Demo_Agent_V9' },
    { id: 'legacy', type: 'DataAgent', displayName: 'RTI_Demo_Agent_V10' },
  ])
  assert.equal(candidate.id, 'legacy')
  const identity = { generation: 2, workspaceId: 'workspace', ontologyId: 'v2-item' }
  for (const parts of [
    [datasource({ ...matchingSource, artifactId: 'old-v1-ontology' })],
    [datasource({ ...matchingSource, workspaceId: 'unrelated-workspace' })],
    [datasource({ type: 'graph', artifactId: 'legacy-graph', workspaceId: 'workspace' })],
    [datasource(matchingSource, 'draft')],
    [datasource({ ...matchingSource, type: 'unknown' })],
    [datasource({ type: 'ontology' })],
    [{ ...datasource(matchingSource), payload: '!!!!' }],
    [datasource(matchingSource), datasource({ ...matchingSource, artifactId: 'old-v1-ontology' })],
    [],
  ]) {
    let mcpCalls = 0
    let definitionReads = 0
    await assert.rejects(invokeVerifiedDataAgent(identity, async () => {
      definitionReads++
      return { definition: { parts } }
    }, async () => { mcpCalls++; return 'must not return an answer' }), /source verification failed/)
    assert.equal(definitionReads, 1)
    assert.equal(mcpCalls, 0)
  }
})

test('generation and published-definition read failures fail closed before MCP', async () => {
  let mcpCalls = 0
  let definitionReads = 0
  const invoke = async () => { mcpCalls++; return 'must not run' }
  await assert.rejects(invokeVerifiedDataAgent(
    { generation: 1, workspaceId: 'workspace', ontologyId: 'legacy' },
    async () => { definitionReads++; return { definition: { parts: [] } } },
    invoke,
  ), /Replace the existing v1 Ontology/)
  assert.equal(definitionReads, 0)
  for (const error of [new Error('403 definition denied'), new Error('Network failure')]) {
    await assert.rejects(invokeVerifiedDataAgent(
      { generation: 2, workspaceId: 'workspace', ontologyId: 'v2-item' },
      async () => { throw error },
      invoke,
    ), caught => caught.cause === error)
  }
  assert.equal(mcpCalls, 0)
})

test('malformed published source definitions fail explicitly rather than permitting an agent call', () => {
  assert.throws(() => verify([datasource({ artifactId: 'v2-item' })]), /has no type/)
  assert.throws(() => verify([datasource({ type: 'ontology' })]), /unverified Ontology/)
  assert.throws(() => verify([{ ...datasource(matchingSource), payload: '!!!!' }]), /not a valid published datasource/)
  assert.throws(() => verify([datasource([])]), /not a valid published datasource/)
  assert.throws(() => verify([{ ...datasource(matchingSource), payload: Buffer.from('{broken').toString('base64') }]), /not a valid published datasource/)
})

test('guided setup retains its original copy and layout without migration panels', async () => {
  const { AdministrationExperience } = await tsImport('../src/components/AdministrationExperience.tsx', {
    parentURL: import.meta.url,
    tsconfig: fileURLToPath(new URL('../tsconfig.app.json', import.meta.url)),
  })
  for (const done of [false, true]) {
    const html = renderToStaticMarkup(createElement(AdministrationExperience, {
      steps: [{ n: 2, title: 'Seed & provision', why: 'Provision the configured data sources', done, busy: false, action: 'Seed', run: () => {} }],
    }))
    assert.ok(html.includes(done
      ? 'All setup steps are complete. Use the actions below to verify each connection.'
      : 'Steps 2 and 3 are independent — you can start them together, then finish 4 and 5.'))
    const heading = html.slice(html.indexOf('class="setup-head"'), html.indexOf('<ol'))
    assert.equal((heading.match(/<p>/g) ?? []).length, 1)
    assert.match(html, /class="setup-steps"/)
    assert.doesNotMatch(html, /data_agent_deployment_status|ops_agent_deployment_status|Agents are enabled by default/)
  }
})

test('Knowledge Graph page displays available bound entities while native loading is pending', async () => {
  const pageSource = await readFile(new URL('../src/ui-shared/pages/KnowledgeGraphPage.tsx', import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(pageSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  })
  const ontology = {
    id: 'ontology', displayName: 'Hydro v2', generation: 2,
    entityTypes: [{ id: 'facility', name: 'facilities', sourceTable: 'silver_facilities', entityIdParts: ['facility_id'], properties: {} }],
    relationshipTypes: [],
  }
  const data = {
    ontology, stid: { facilities: [{ facility_id: 'F1', facility_name: 'Station' }], systems: [], equipment: [], instruments: [] },
    telemetry: [], orders: [], inspections: [], notifications: [], assetModels: [], actions: {},
  }
  let canvasNodes = []
  const dependencies = {
    react: await import('react'), 'react/jsx-runtime': await import('react/jsx-runtime'),
    'lucide-react': await import('lucide-react'),
    '../../services/fabric': {
      queryOntologyGraph: () => { throw new Error('Rendering must not wait for a graph request') },
      queryOntologyContract: () => { throw new Error('Rendering must use the available contract') },
    },
    '../knowledgeGraphModel': await import('../src/ui-shared/knowledgeGraphModel.ts'),
    '../components/digitalTwin/digitalTwinTreeModel': await import('../src/ui-shared/components/digitalTwin/digitalTwinTreeModel.ts'),
    '../components/digitalTwin/DigitalTwinTree': { DigitalTwinTree: () => null },
    '../components/knowledgeGraph/KnowledgeGraphCanvas': { KnowledgeGraphCanvas: ({ nodes }) => {
      canvasNodes = nodes
      return createElement('div', { 'data-testid': 'canvas' })
    } },
    '../hooks/useHydroOperationsData': { useHydroOperationsData: () => data },
    '../hooks/useTheme': { useTheme: () => ({ theme: 'light' }) },
    '../hooks/useTreeExpansion': { useTreeExpansion: () => ({ isExpanded: () => true, toggle: () => {} }) },
  }
  const exports = {}
  new Function('require', 'exports', outputText)(name => {
    assert.ok(dependencies[name], `Unexpected page dependency: ${name}`)
    return dependencies[name]
  }, exports)
  const html = renderToStaticMarkup(createElement(exports.KnowledgeGraphPage))
  assert.match(html, /Operational Knowledge Graph/)
  assert.match(html, /data-testid="canvas"/)
  assert.doesNotMatch(html, /<h1>Loading the Ontology graph<\/h1>/)
  assert.equal(canvasNodes.length, 1)
  assert.equal(canvasNodes[0].label, 'Station')
  assert.equal(canvasNodes[0].nativeOid, undefined)
})