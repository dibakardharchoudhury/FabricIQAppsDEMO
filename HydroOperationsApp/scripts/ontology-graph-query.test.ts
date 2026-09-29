import assert from 'node:assert/strict'
import test from 'node:test'
import type { OntologyContract } from '../src/services/ontologyContract.ts'
import { parseOntologyGraph } from '../src/services/ontologyGraph.ts'
import { executeGraphRows, parseGraphBinding, queryBoundOntologyGraph, resolveGraphSchema } from '../src/services/ontologyGraphQuery.ts'

const workspaceId = '11111111-1111-1111-1111-111111111111'
const ontologyId = '22222222-2222-2222-2222-222222222222'
const graphModelId = '33333333-3333-3333-3333-333333333333'
const binding = { workspaceId, ontologyId, graphModelId }
const ontology: OntologyContract = {
  id: ontologyId, displayName: 'Hydro v2', generation: 2,
  entityTypes: [
    { id: 'facility-type', name: 'facilities', entityIdParts: ['facility_id'], properties: { facility_id: 'facility_id' } },
    { id: 'system-type', name: 'plant#systems', entityIdParts: ['system_id'], properties: { system_id: 'system_id' } },
  ],
  relationshipTypes: [{
    id: 'relationship-type', name: 'plant#systems_in_facilities',
    sourceEntityTypeId: 'system-type', targetEntityTypeId: 'facility-type',
    sourceEntityName: 'plant#systems', targetEntityName: 'facilities',
    sourceKeys: ['facility_id'], targetKeys: ['facility_id'],
  }],
}
const schema = {
  nodeTypes: [
    { alias: 'f', labels: ['facilities'], primaryKeyProperties: ['facility_id'] },
    { alias: 's', labels: ['plant#systems'], primaryKeyProperties: ['system_id'] },
  ],
  edgeTypes: [{ alias: 'r', labels: ['plant#systems_in_facilities'], sourceNodeType: { alias: 's' }, destinationNodeType: { alias: 'f' } }],
}
const element = (oid: string, labels: string[], properties = {}) => JSON.stringify({ oid, labels, properties })
const facility = element('f1', ['facilities'], { facility_id: 'F1' })
const system = element('s1', ['plant#systems'], { system_id: 'S1' })
const nodeRows = [{ node: facility }, { node: system }]
const edgeRows = [{ source: system, target: facility, relationship: element('r1', ['plant#systems_in_facilities']) }]
const table = (data: object[], nextPage?: string) => ({ status: { code: '00000' }, result: { kind: 'TABLE', data, ...(nextPage ? { nextPage } : {}) } })
const endpoint = `https://api.fabric.microsoft.com/v1/workspaces/${workspaceId}/graphModels/${graphModelId}/executeQuery?beta=true`

test('native graph requires explicit workspace/ontology/graph binding, never name guessing', () => {
  assert.deepEqual(parseGraphBinding(JSON.stringify(binding), workspaceId, ontologyId), { ...binding, nodeTypes: undefined, edgeTypes: undefined })
  for (const input of [undefined, '', 'invalid', '{}', JSON.stringify({ ...binding, graphModelId: 'not-a-guid' })]) {
    assert.throws(() => parseGraphBinding(input, workspaceId, ontologyId), /binding|Materialize/)
  }
  assert.throws(() => parseGraphBinding(JSON.stringify(binding), graphModelId, ontologyId), /different workspace or ontology/)
  assert.throws(() => parseGraphBinding(JSON.stringify(binding), workspaceId, graphModelId), /different workspace or ontology/)
  assert.throws(() => parseGraphBinding(JSON.stringify({ ...binding, nodeTypes: { s: 12 } }), workspaceId, ontologyId), /exact ontology names/)
})

test('queryable schema validates semantic endpoints and uses explicit aliases instead of guessing namespace separators', () => {
  assert.equal(resolveGraphSchema(schema, ontology, binding).nodes[1].entity.id, 'system-type')
  const prefixed = { ...schema, nodeTypes: [schema.nodeTypes[0], { ...schema.nodeTypes[1], labels: ['actual_projected_label'] }] }
  assert.throws(() => resolveGraphSchema(prefixed, ontology, binding), /namespace separators are not inferred/)
  assert.equal(resolveGraphSchema(prefixed, ontology, { ...binding, nodeTypes: { s: 'plant#systems' } }).nodes[1].entity.id, 'system-type')
  assert.throws(() => resolveGraphSchema(schema, ontology, { ...binding, nodeTypes: { nonexistent: 'facilities' } }), /alias absent/)
  assert.throws(() => resolveGraphSchema({ ...schema, edgeTypes: [{ ...schema.edgeTypes[0], sourceNodeType: { alias: 'f' } }] }, ontology, binding), /endpoints disagree/)
  assert.throws(() => resolveGraphSchema({ nodeTypes: [], edgeTypes: [] }, ontology, binding), /Materialize/)
})

test('native queries follow opaque continuation tokens with unchanged GQL request bodies', async () => {
  const calls: Array<{ url: string; body?: BodyInit | null }> = []
  const token = 'opaque+a/b%20='
  const rows = await executeGraphRows(endpoint, 'MATCH (n) RETURN n', async (url, init) => {
    calls.push({ url, body: init?.body })
    return Response.json(calls.length === 1 ? table([{ n: 1 }], token) : table([{ n: 2 }]))
  }, 2)
  assert.deepEqual(rows, [{ n: 1 }, { n: 2 }])
  assert.equal(new URL(calls[1].url).searchParams.get('continuationToken'), token)
  assert.equal(new URL(calls[1].url).searchParams.get('beta'), 'true')
  assert.equal(calls[0].body, calls[1].body)
})

test('02000 pending response is polled, not mistaken for an empty successful graph', async () => {
  let count = 0
  const rows = await executeGraphRows(endpoint, 'query', async () => Response.json(++count === 1
    ? { status: { code: '02000' }, result: { kind: 'TABLE', data: [], nextPage: 'pending' } }
    : table([{ ready: true }])), 1)
  assert.equal(count, 2)
  assert.deepEqual(rows, [{ ready: true }])
})

test('native result errors, truncation warnings, invalid envelopes and limits never become empty success', async () => {
  const payloads = [
    {},
    { status: { code: '42000', description: 'bad query' } },
    { status: { code: '50000' } },
    { status: { code: '00000' }, result: { kind: 'OTHER', data: [] } },
    { status: { code: '00000' }, result: { kind: 'TABLE', data: [null] } },
    { ...table([]), additionalStatuses: [{ code: '01000', diagnostics: { _graphaneGqlStatus: { gqlType: 'STRING', value: '01M11' } } }] },
    { ...table([]), result: { kind: 'TABLE', data: [], nextPage: { token: 'invalid' } } },
    table([{ n: 1 }, { n: 2 }]),
  ]
  for (const payload of payloads) {
    await assert.rejects(executeGraphRows(endpoint, 'query', async () => Response.json(payload), 1))
  }
  await assert.rejects(executeGraphRows(endpoint, 'query', async () => new Response('denied', { status: 403 }), 1), /403/)
  await assert.rejects(executeGraphRows(endpoint, 'query', async () => new Response(null, { status: 204 }), 1), /not ready.*Materialize/)
  await assert.rejects(executeGraphRows(endpoint, 'query', async () => new Response('invalid json'), 1), /invalid JSON/)
  assert.deepEqual(await executeGraphRows(endpoint, 'query', async () => Response.json({ status: { code: '02000' }, result: { kind: 'TABLE', data: [] } }), 1), [])
})

test('graph decoding rejects malformed elements, duplicate identities and missing endpoints', () => {
  for (const row of [{ node: 'broken' }, { node: '{}' }, { node: element('', []) }, { node: JSON.stringify({ oid: 'x', labels: [12] }) }]) {
    assert.throws(() => parseOntologyGraph(graphModelId, 'graph', [row], []))
  }
  assert.throws(() => parseOntologyGraph(graphModelId, 'graph', [nodeRows[0], nodeRows[0]], []), /duplicate node/)
  assert.throws(() => parseOntologyGraph(graphModelId, 'graph', [nodeRows[0]], edgeRows), /endpoint missing/)
  assert.throws(() => parseOntologyGraph(graphModelId, 'graph', nodeRows, [edgeRows[0], edgeRows[0]]), /duplicate relationship/)
})

test('bound query validates graph metadata/schema, reads native nodes/edges and attaches ontology provenance', async () => {
  const calls: string[] = []
  const graph = await queryBoundOntologyGraph(binding, ontology, async (url, init) => {
    calls.push(url)
    if (url.endsWith(graphModelId)) return Response.json({ id: graphModelId, type: 'GraphModel', displayName: 'Verified graph' })
    if (url.includes('getQueryableGraphType')) return Response.json(schema)
    const query = JSON.parse(String(init?.body)).query as string
    return Response.json(table(query.includes('AS `node`') ? nodeRows : edgeRows))
  })
  assert.equal(calls.length, 4)
  assert.equal(graph.ontologyId, ontologyId)
  assert.equal(graph.nodes.length, 2)
  assert.equal(graph.nodes[1].entityTypeId, 'system-type')
  assert.equal(graph.edges[0].relationshipTypeId, 'relationship-type')
  assert.equal(graph.edges[0].sourceOid, 's1')
  assert.equal(graph.edges[0].targetOid, 'f1')
  assert.ok(calls.every(url => url.includes(`/workspaces/${workspaceId}/graphModels/${graphModelId}`)))
})

test('wrong graph metadata and ontology identities fail before graph execution', async () => {
  let calls = 0
  const request = async () => { calls++; return Response.json({ id: ontologyId, type: 'GraphModel', displayName: 'Wrong' }) }
  await assert.rejects(queryBoundOntologyGraph({ ...binding, ontologyId: graphModelId }, ontology, request), /does not match/)
  assert.equal(calls, 0)
  await assert.rejects(queryBoundOntologyGraph(binding, ontology, request), /does not match/)
  assert.equal(calls, 1)
})

test('visualization limits accept exactly 2000 nodes or 4000 edges and reject one extra row', async () => {
  for (const limit of [2000, 4000]) {
    const rows = Array.from({ length: limit }, (_, id) => ({ id }))
    assert.equal((await executeGraphRows(endpoint, 'query', async () => Response.json(table(rows)), limit)).length, limit)
    await assert.rejects(executeGraphRows(endpoint, 'query', async () => Response.json(table([...rows, { id: limit }])), limit), new RegExp(`${limit}-row`))
  }
})

test('repeated continuation rows and additional execution errors fail closed', async () => {
  await assert.rejects(executeGraphRows(endpoint, 'query', async () => Response.json(table([{ n: 1 }], 'same-token')), 2000), /repeated continuation/)
  await assert.rejects(executeGraphRows(endpoint, 'query', async () => Response.json({
    ...table([]), status: { code: '00000', additionalStatuses: [{ code: '01000' }] },
  }), 2000), /warnings\/errors/)
})
