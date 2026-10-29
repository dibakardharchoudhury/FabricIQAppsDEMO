import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile, rm, writeFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { loadPreviewDefinition, parsePreviewDefinition } from './knowledge-graph-preview-definition.mjs'

const envelope = () => ({
  metadata: { id: '00000000-0000-0000-0000-000000000001', displayName: 'Saved schema test', type: 'Ontology', properties: { generation: 2 } },
  definition: { parts: [
    { path: 'entities/Test.tmdl', payloadType: 'InlineBase64', payload: Buffer.from('entity Test\n    keyProperty: id\n    property id\n        dataType: string\n').toString('base64') },
  ] },
})

test('preview CLI rejects invalid ports before starting a server', () => {
  for (const port of ['0', '65536', '1.5', 'not-a-port', '']) {
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('./knowledge-graph-preview.mjs', import.meta.url)), '--port', port], { encoding: 'utf8' })
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /--port must be an integer from 1 to 65535/)
  }
})

test('saved preview parses real TMDL using authoritative metadata without mutating its envelope', async () => {
  const input = envelope()
  const before = structuredClone(input)
  const contract = await parsePreviewDefinition(input)
  assert.equal(contract.id, input.metadata.id)
  assert.equal(contract.displayName, input.metadata.displayName)
  assert.equal(contract.generation, 2)
  assert.equal(contract.entityTypes[0].name, 'Test')
  assert.deepEqual(contract.relationshipTypes, [])
  assert.deepEqual(input, before)
})

test('saved preview reads a supplied file unchanged and propagates invalid JSON or missing files', async () => {
  const path = new URL(`./.knowledge-graph-preview-test-${process.pid}.json`, import.meta.url)
  const json = JSON.stringify(envelope())
  try {
    await writeFile(path, json, { flag: 'wx' })
    assert.equal((await loadPreviewDefinition(path)).generation, 2)
    assert.equal(await readFile(path, 'utf8'), json)
    await writeFile(path, '{invalid')
    await assert.rejects(loadPreviewDefinition(path), SyntaxError)
  } finally {
    await rm(path, { force: true })
  }
  await assert.rejects(loadPreviewDefinition(path), /ENOENT/)
})

test('saved preview rejects nonnumeric/v1 generations and absent, malformed or conflicting item identities', async () => {
  for (const generation of [undefined, null, '2', 'v2', 1, 3]) {
    const input = envelope()
    await assert.rejects(parsePreviewDefinition({ ...input, metadata: { ...input.metadata, properties: { generation } } }), /numeric/)
  }
  for (const id of [undefined, '', 'not-a-guid', ' 00000000-0000-0000-0000-000000000001']) {
    const input = envelope()
    await assert.rejects(parsePreviewDefinition({ ...input, metadata: { ...input.metadata, id } }), /GUID/)
  }
  await assert.rejects(parsePreviewDefinition({ ...envelope(), id: '00000000-0000-0000-0000-000000000002' }), /conflicting/)
  const input = envelope()
  await assert.rejects(parsePreviewDefinition({ ...input, definition: { ...input.definition, id: 'other' } }), /conflicting/)
  await assert.rejects(parsePreviewDefinition({ ...input, metadata: { ...input.metadata, type: 'GraphModel' } }), /must be Ontology/)
  await assert.rejects(parsePreviewDefinition({ ...input, metadata: { ...input.metadata, displayName: '' } }), /displayName/)
})

test('saved preview propagates missing, malformed, duplicate and generation-1 definition failures', async () => {
  await assert.rejects(parsePreviewDefinition({ ...envelope(), definition: { parts: [] } }), /definition.parts/)
  const input = envelope()
  await assert.rejects(parsePreviewDefinition({ ...input, definition: { parts: [input.definition.parts[0], input.definition.parts[0]] } }), /duplicate/)
  await assert.rejects(parsePreviewDefinition({ ...input, definition: { parts: [{ ...input.definition.parts[0], payloadType: 'InlineText' }] } }), /InlineBase64/)
  await assert.rejects(parsePreviewDefinition({ ...input, definition: { parts: [{ ...input.definition.parts[0], path: 'EntityTypes/Test.json' }] } }), /v1|generation-1|replacement/i)
  await assert.rejects(parsePreviewDefinition({ ...input, definition: { parts: [
    ...input.definition.parts,
    { path: 'entityRelationships.tmdl', payloadType: 'InlineBase64', payload: Buffer.from('entityRelationship dangling\n    fromEntity: Test\n    toEntity: Missing\n').toString('base64') },
  ] } }), /Missing|missing|endpoint|references|resolve/)
})
