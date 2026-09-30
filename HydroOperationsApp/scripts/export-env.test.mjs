import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { tsImport } from 'tsx/esm/api'
import { loadEnv } from 'vite'
import { canonicalGraphBinding, exportFrontendEnv, prepareFrontendEnv } from './export-env.mjs'

const appRoot = fileURLToPath(new URL('../', import.meta.url))
const require = createRequire(import.meta.url)
const cliRoot = path.dirname(require.resolve('@microsoft/rayfin-cli/package.json'))
const { readEnvMap, updateEnvVariables } = await import(pathToFileURL(path.join(cliRoot, 'dist/utils/env-file-utils.js')))
const { writeFrameworkEnvFile } = await import(pathToFileURL(path.join(cliRoot, 'dist/commands/env/env.js')))
const { parseGraphBinding } = await tsImport('../src/services/ontologyGraphQuery.ts', import.meta.url)
const binding = {
  workspaceId: '9c73201e-b2e5-48eb-81b9-3526d320faca',
  ontologyId: 'd0d041aa-13ea-4277-aa6e-1d3ab565a2d4',
  graphModelId: '87bb9ac2-4599-44b9-8014-b45c696332bd',
  nodeTypes: { 'namespace#instruments': 'hydro#instrument', "quoted#'alias": 'hydro#$name', 'escaped#"alias': 'hydro\\named\tinstrument' },
  edgeTypes: { 'namespace#contains': 'hydro#contains' },
}

test('binding survives real Rayfin rewrites and framework export into Vite', async () => {
  const root = await mkdtemp(path.join(appRoot, '.env-export-test-'))
  try {
    await mkdir(path.join(root, 'rayfin'))
    const primary = path.join(root, 'rayfin/.env')
    await writeFile(primary, [
      `RAYFIN_PUBLIC_WORKSPACE_ID=${binding.workspaceId}`,
      `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING='${canonicalGraphBinding(JSON.stringify(binding), binding.workspaceId)}'`,
      'RAYFIN_PUBLIC_KQL_DATABASE=RTI_Demo_Eventhouse_V3',
      'PRIVATE_TEST_SECRET=not-for-the-browser',
    ].join('\n') + '\n')
    for (let cycle = 0; cycle < 4; cycle++) {
      for (let rewrite = 0; rewrite < 3; rewrite++) {
        await updateEnvVariables(path.dirname(primary), [{ key: 'RAYFIN_PUBLIC_ITEM_ID', value: `backend-${cycle}` }])
      }
      await writeFrameworkEnvFile({ projectRoot: root, framework: 'vite', outputDir: '.' })
      assert.throws(() => parseGraphBinding(
        loadEnv('production', root, 'VITE_').VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING,
        binding.workspaceId, binding.ontologyId,
      ), /Invalid ontology graph binding JSON/)
      await exportFrontendEnv(root)
      const browserEnv = loadEnv('production', root, 'VITE_')
      assert.deepEqual(JSON.parse(browserEnv.VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING), binding)
      assert.deepEqual(parseGraphBinding(
        browserEnv.VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING, binding.workspaceId, binding.ontologyId,
      ), binding)
      assert.equal(browserEnv.VITE_RAYFIN_KQL_DATABASE, 'RTI_Demo_Eventhouse_V3')
      assert.equal(browserEnv.VITE_FABRIC_ITEM_ID, `backend-${cycle}`)
      assert.deepEqual(JSON.parse((await readEnvMap(path.dirname(primary))).get('RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING')), binding)
      assert.doesNotMatch(await readFile(path.join(root, '.env.local'), 'utf8'), /PRIVATE_TEST_SECRET|not-for-the-browser/)
    }
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('malformed JSON, unknown encodings, invalid IDs, aliases and workspace mismatch fail closed', () => {
  for (const value of ['{bad}', '"not JSON"', '[]', 'null', '"{\\"bad\\":NaN}"', 'x'.repeat(1024 * 1024 + 1)]) {
    assert.throws(() => canonicalGraphBinding(value, binding.workspaceId))
  }
  for (const changed of [{ workspaceId: 'bad' }, { graphModelId: 'bad' }, { nodeTypes: [] }, { edgeTypes: { alias: 1 } }]) {
    assert.throws(() => canonicalGraphBinding(JSON.stringify({ ...binding, ...changed }), binding.workspaceId))
  }
  assert.throws(() => canonicalGraphBinding(JSON.stringify(binding), binding.ontologyId), /configured workspace/)
  assert.throws(() => prepareFrontendEnv('RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING=\nRAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING=\n'), /Duplicate/)
})

test('absent binding is optional and exporting unrelated public fields does not mutate primary', () => {
  const source = '# primary comment\nPRIVATE_TEST_SECRET=private\nRAYFIN_PUBLIC_KQL_DATABASE=RTI_Demo_Eventhouse_V3\n'
  const result = prepareFrontendEnv(source)
  assert.equal(result.primary, source)
  assert.match(result.frontend, /VITE_RAYFIN_KQL_DATABASE='RTI_Demo_Eventhouse_V3'/)
  assert.doesNotMatch(result.frontend, /PRIVATE|ONTOLOGY_GRAPH_BINDING/)
  assert.equal(canonicalGraphBinding("''", binding.workspaceId), '')
})

test('prebuild and predev use the repository producer, not the lossy SDK framework writer', async () => {
  const { scripts } = JSON.parse(await readFile(path.join(appRoot, 'package.json'), 'utf8'))
  assert.equal(scripts.env, 'node ./scripts/export-env.mjs')
  assert.equal(scripts.prebuild, 'npm run validate-env && npm run env')
  assert.equal(scripts.predev, 'npm run validate-env && npm run env')
})
