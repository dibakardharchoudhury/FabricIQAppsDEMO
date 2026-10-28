import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

function compile(source, dependencies) {
  const code = ts.transpileModule(source.replaceAll('import.meta.env', 'testEnv'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  const exports = {}
  new Function('exports', ...Object.keys(dependencies), code)(exports, ...Object.values(dependencies))
  return exports
}

function service(runJob) {
  const source = readFileSync(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
  const start = source.indexOf('const mapRefreshListeners')
  const end = source.indexOf('export async function resumeEnergyMapRefresh', start)
  assert.ok(start > 0 && end > start)
  return compile(source.slice(start, end), {
    runJob, resolveEnergyMapPipeline: async () => 'pipeline',
    testEnv: { VITE_RAYFIN_GEO_LAKEHOUSE_ID: 'current-geo' },
    requireWorkspaceId: () => 'current-workspace',
    mapDataAgentName: () => 'current-map-agent',
    eventhouseName: 'current-eventhouse', configuredMapAgentName: '',
  })
}

test('real map service sends distinct cached, operational and full-rebuild pipeline parameters', async () => {
  for (const [force, mode] of [[false, 'all'], [false, 'operational'], [true, 'all']]) {
    let options
    const api = service(async (_id, _kind, _progress, value) => { options = value; return 'Completed' })
    assert.equal(await api.refreshEnergyMap(undefined, force, undefined, mode), 'Completed')
    assert.deepEqual(options.pipelineParameters, {
      workspace_id: 'current-workspace', geo_lakehouse_id: 'current-geo',
      env_suffix: '', map_agent_name: 'current-map-agent', refresh_mode: mode, force_refresh: force,
    })
    assert.equal(options.reuseActive, true)
  }
})

test('concurrent refresh modes join the first run without upgrading it to a forced rebuild', async () => {
  let finish
  let starts = 0
  const api = service(async (_id, _kind, _progress, options) => {
    starts++
    assert.equal(options.pipelineParameters.refresh_mode, 'operational')
    assert.equal(options.pipelineParameters.force_refresh, false)
    return new Promise(resolve => { finish = resolve })
  })
  const first = api.refreshEnergyMap(undefined, false, undefined, 'operational')
  let attached = false
  const second = api.refreshEnergyMap(undefined, true, () => { attached = true })
  await Promise.resolve()
  assert.equal(starts, 1)
  assert.equal(attached, true)
  finish('Completed')
  assert.deepEqual(await Promise.all([first, second]), ['Completed', 'Completed'])
})

test('controller routes each action correctly and operational refresh does not claim initial setup', async () => {
  const source = readFileSync(new URL('../src/ui-shared/hooks/useHydroOperationsData.ts', import.meta.url), 'utf8')
  const tree = ts.createSourceFile('hook.ts', source, ts.ScriptTarget.Latest, true)
  let action
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'refreshMapData') {
      action = node.initializer.arguments[0].getText(tree)
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)
  assert.ok(action)
  for (const intent of ['prepare', 'operational', 'rebuild']) {
    let parameters
    let prepared = false
    const { refreshMapData } = compile(`export const refreshMapData = ${action}`, {
      ENERGY_MAP_ENABLED: true, jobs: {}, mapRefreshState: 'idle', MAP_REFRESH_MAX_AGE_MS: 1000,
      setMapRefreshState: () => {}, setMapRefreshMessage: () => {}, beginProgress: () => {},
      updateJob: () => {}, humanStatus: value => value, endJob: () => {},
      setMapPrepared: value => { prepared = value },
      refreshEnergyMap: async (...args) => { parameters = args; return 'Completed' },
    })
    await refreshMapData(intent)
    assert.equal(parameters[1], intent === 'rebuild')
    assert.equal(parameters[3], intent === 'operational' ? 'operational' : 'all')
    assert.equal(prepared, intent !== 'operational')
  }
})
