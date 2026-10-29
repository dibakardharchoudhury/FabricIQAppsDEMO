import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

function loadService(name, imports, env = {}) {
  const source = readFileSync(new URL(`../src/services/copilot/${name}.ts`, import.meta.url), 'utf8')
  const code = ts.transpileModule(source.replaceAll('import.meta', '__moduleMeta'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  const exports = {}
  new Function('require', 'exports', '__moduleMeta', '__APP_VERSION__', code)(
    specifier => {
      assert.ok(Object.hasOwn(imports, specifier), `Unexpected dependency: ${specifier}`)
      return imports[specifier]
    }, exports, { env }, 'test-build',
  )
  return exports
}

test('Hydro Intelligence always enters Supervisor without forcing a native bypass or changing the question', async () => {
  const calls = []
  const instances = []
  const events = []
  const result = {
    text: 'Original Fabric answer\r\n',
    visualizations: [], proposals: [], executionEvents: events,
  }
  class Transport {
    constructor(...args) { instances.push(args) }
    async run(body, onEvents) { calls.push({ body, onEvents }); return result }
    async warm() { calls.push({ warm: true }) }
    clearConversation() { calls.push({ clear: true }) }
  }
  const projectEndpoint = 'https://test.services.ai.azure.com/api/projects/hydro'
  const foundry = loadService('foundry', {
    './settings.ts': { loadCopilotSettings: () => ({ projectEndpoint }) },
    './hostedTransport.ts': { HostedTransport: Transport },
  }, {
    VITE_RAYFIN_FOUNDRY_INVOCATIONS_URL: `${projectEndpoint}/agents/hydro/endpoint/protocols/invocations`,
    VITE_FABRIC_TENANT_ID: 'test-tenant',
    VITE_FABRIC_WORKSPACE_ID: 'test-workspace',
    VITE_RAYFIN_ORCHESTRATOR_SOURCE_DIGEST: 'a'.repeat(64),
    VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING: JSON.stringify({ ontologyId: 'test-ontology', workspaceId: 'test-workspace' }),
  })
  const entrypoint = loadService('askCopilot', { './foundry': foundry })
  const question = '  Ask Sparky for facility IDs.\r\nKeep the original answer.  '
  const progress = []
  const onEvents = () => {}
  const answer = await entrypoint.askCopilot(question, text => progress.push(text), undefined, onEvents)
  assert.deepEqual(calls, [{ body: { question }, onEvents }])
  assert.equal(Object.hasOwn(calls[0].body, 'native_sources'), false)
  assert.equal(answer.text, result.text)
  assert.equal(answer.backendOwned, true)
  assert.deepEqual(progress, [result.text])
  assert.equal(instances.length, 1)
  assert.equal(instances[0].at(-1), 'foundry.test-build')
  foundry.warmHostedCopilot()
  foundry.resetFoundryConversation()
  assert.equal(instances.length, 1)
  assert.deepEqual(calls.slice(1), [{ warm: true }, { clear: true }])
})

test('legacy comparison settings cannot restore a direct-agent entry on Hydro Intelligence', () => {
  const page = readFileSync(new URL('../src/ui-shared/pages/CopilotPage.tsx', import.meta.url), 'utf8')
  const settings = readFileSync(new URL('../src/components/CopilotSettingsPanel.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /AgentBattleExperience|isAgentBattleEnabled|copilotEngine|onEngineChange/)
  assert.doesNotMatch(settings, /battleEnabled|Battle of the Agents|agent comparison/i)
  for (const path of ['components/AgentBattleExperience.tsx', 'services/copilot/agentBattle.ts']) {
    assert.equal(existsSync(new URL(`../src/${path}`, import.meta.url)), false)
  }
  const styles = readFileSync(new URL('../src/ui-shared/styles/app.css', import.meta.url), 'utf8')
  assert.doesNotMatch(styles, /agent-battle-/)
})
