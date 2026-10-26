import assert from 'node:assert/strict'
import test from 'node:test'
import { parseOntologyContract } from '../src/services/ontologyContract.ts'
import { readOntologySourceGraph, SOURCE_GRAPH_LIMITS, type OntologySourceReader } from '../src/services/ontologySourceGraph.ts'
import { buildKnowledgeGraph, loadNativeGraphSnapshot } from '../src/ui-shared/knowledgeGraphModel.ts'

const workspace = '11111111-1111-4111-8111-111111111111'
const item = '22222222-2222-4222-8222-222222222222'
const otherItem = '33333333-3333-4333-8333-333333333333'
const part = (path: string, text: string) => ({ path, payload: Buffer.from(text).toString('base64'), payloadType: 'InlineBase64' })
const definition = () => ({ definition: { parts: [
  part('expressions.tmdl', `expression 'Bound Lake' =
    let
      Source = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/${workspace}/${item}", [HierarchicalNavigation=true])
    in
      Source
  annotation ONT_WorkspaceId = ${workspace}
  annotation ONT_ItemId = ${item}
  annotation ONT_ItemKind = Lakehouse`),
  ...[['facilities', 'silver_facilities', ['facility_id']], ['systems', 'silver_systems', ['system_id', 'facility_id']]].flatMap(([name, physical, names]) => {
    const columns = names as string[]
    return [
      part(`tables/${name}.tmdl`, `table ${name}
${columns.map(column => `    column ${column}\n      dataType: string\n      sourceColumn: ${column === 'facility_id' ? 'facility_code' : column}`).join('\n')}
    partition ${name} = entity
      mode: directLake
      source
        entityName: ${physical}
        expressionSource: 'Bound Lake'`),
      part(`entities/${name}.tmdl`, `entity ${name}
    backingTable: ${name}
    keyProperty: ${columns[0]}
${columns.map(column => `    property ${column}\n      dataType: string\n      lineageTag: ${name}-${column}\n      backingConfiguration\n        type: default\n        valueColumn: ${name}.${column}`).join('\n')}`),
    ]
  }),
  part('relationships.tmdl', `relationship system_facility
    fromColumn: systems.facility_id
    toColumn: facilities.facility_id`),
  part('entityRelationships.tmdl', `entityRelationship systems_in_facilities
    fromEntity: systems
    toEntity: facilities
    backingConfiguration
      relationship: system_facility`),
] } })
const contract = () => parseOntologyContract('ontology', 'Verified live ontology', definition(), 2)
const readers = (): OntologySourceReader[] => [
  { workspaceId: workspace, itemId: item, itemKind: 'Lakehouse', schema: 'dbo', table: 'silver_facilities',
    columns: ['facility_code'], read: async () => ({ items: [{ facility_code: 'F1' }], hasNextPage: false }) },
  { workspaceId: workspace, itemId: item, itemKind: 'Lakehouse', schema: 'dbo', table: 'silver_systems',
    columns: ['system_id', 'facility_code'], read: async () => ({ items: [{ system_id: 'S1', facility_code: 'F1' }], hasNextPage: false }) },
]

test('maps physical aliases and only joins declared directed ontology relationships', async () => {
  const ontology = contract()
  assert.equal(ontology.sourceBindings?.[0].binding?.keyColumn, 'facility_code')
  const graph = await readOntologySourceGraph(ontology, readers())
  assert.equal(graph.execution, 'source-backed')
  assert.equal('graphModelId' in graph, false)
  assert.equal(graph.nodes.length, 2)
  assert.equal(graph.edges.length, 1)
  assert.equal(graph.nodes.find(node => node.oid === graph.edges[0].sourceOid)?.properties.system_id, 'S1')
  assert.equal(graph.nodes.find(node => node.oid === graph.edges[0].targetOid)?.properties.facility_id, 'F1')
  assert.equal(graph.evidence.relationships[0].count, 1)
  assert.equal(graph.evidence.consistency, 'non-atomic live reads')
  assert.equal(graph.evidence.sources.length, 2)
  assert.ok(graph.evidence.completedAt)
  ontology.relationshipTypes = []
  assert.equal((await readOntologySourceGraph(ontology, readers())).edges.length, 0)
})

test('live presentation does not append inferred SQL nodes or native provenance', async () => {
  const ontology = contract()
  ontology.entityTypes[1].localName = 'equipment'
  const topology = await readOntologySourceGraph(ontology, readers())
  const graph = buildKnowledgeGraph({
    ontology, ontologyGraph: topology, facilities: [], systems: [], equipment: [], instruments: [], telemetry: [],
    workOrders: [], inspections: [], notifications: [],
    models: [{ id: 'fabricated-overlay', equipmentId: 'S1', modelName: 'Not declared', modelUrl: 'ignored', format: 'glb', updatedAt: new Date(), updatedByOid: 'user' }],
  })
  assert.equal(graph.error, undefined)
  assert.equal(graph.nodes.length, 2)
  assert.equal(graph.edges.length, 1)
  assert.ok(graph.nodes.every(node => !node.nativeOid && !node.provenance.includes('materialized')))
  assert.ok(graph.edges.every(edge => !edge.nativeOid && edge.ontologyRelationshipId))
})

test('checks every source identity before starting any connector read', async () => {
  const sources = readers()
  let calls = 0
  sources[0].read = async () => { calls++; return { items: [], hasNextPage: false } }
  sources[1].itemId = otherItem
  // Remove all matching readers for the bound item instead of relying on a table name.
  sources[0].itemId = otherItem
  await assert.rejects(readOntologySourceGraph(contract(), sources), /source identities/)
  assert.equal(calls, 0)
})

test('does not evaluate dynamic M and exposes unsupported entity/relationship coverage', async () => {
  const ontology = contract()
  ontology.sourceBindings![1] = { entityTypeId: 'systems', unsupportedReason: 'Dynamic M is not executed.' }
  const graph = await readOntologySourceGraph(ontology, readers())
  assert.equal(graph.nodes.length, 1)
  assert.equal(graph.edges.length, 0)
  assert.match(graph.evidence.entities[1].reason!, /Dynamic M/)
  assert.equal(graph.evidence.relationships[0].included, false)
  const dynamic = definition()
  const expression = dynamic.definition.parts[0]
  expression.payload = Buffer.from(Buffer.from(expression.payload, 'base64').toString().replace('in\n      Source', 'in\n      Table.FirstN(Source, 1)')).toString('base64')
  const parsed = parseOntologyContract('ontology', 'Dynamic binding', dynamic, 2)
  await assert.rejects(readOntologySourceGraph(parsed, readers()), /No ontology entities/)
})

test('rejects conflicting source annotations and source/schema mismatches', async () => {
  const changed = definition()
  changed.definition.parts[0].payload = Buffer.from(Buffer.from(changed.definition.parts[0].payload, 'base64').toString()
    .replace(`annotation ONT_ItemId = ${item}`, `annotation ONT_ItemId = ${otherItem}`)).toString('base64')
  assert.throws(() => parseOntologyContract('ontology', 'Conflicting binding', changed, 2), /conflicts/)
  const sources = readers()
  sources.forEach(source => { source.itemKind = 'SQLDatabase' })
  await assert.rejects(readOntologySourceGraph(contract(), sources), /source kind/)
  sources.forEach(source => { source.itemKind = 'Lakehouse'; source.schema = 'private' })
  await assert.rejects(readOntologySourceGraph(contract(), sources), /No ontology entities/)
})

test('paginates completely, rejects repeated cursors and never substitutes denied sources', async () => {
  const sources = readers()
  sources[0].read = async cursor => ({
    items: [{ facility_code: cursor ? 'F2' : 'F1' }], hasNextPage: !cursor, endCursor: cursor ? null : 'next',
  })
  assert.equal((await readOntologySourceGraph(contract(), sources)).nodes.length, 3)
  sources[0].read = async () => ({ items: [], hasNextPage: true, endCursor: 'same' })
  await assert.rejects(readOntologySourceGraph(contract(), sources), /pagination did not advance/)
  sources[0].read = async () => { throw new Error('403 denied') }
  await assert.rejects(readOntologySourceGraph(contract(), sources), /403 denied/)
})

test('fails closed for duplicate, invalid, dangling and type-mismatched identities', async () => {
  for (const items of [[{ facility_code: 'F1' }, { facility_code: 'F1' }], [{ facility_code: '' }], [{ facility_code: 1 }]]) {
    const sources = readers()
    sources[0].read = async () => ({ items, hasNextPage: false })
    await assert.rejects(readOntologySourceGraph(contract(), sources), /duplicate|nonempty scalar|unresolved source/)
  }
  const sources = readers()
  sources[1].read = async () => ({ items: [{ system_id: 'S1', facility_code: 'MISSING' }], hasNextPage: false })
  await assert.rejects(readOntologySourceGraph(contract(), sources), /unresolved source reference/)
})

test('rejects node overflow rather than truncating source topology', async () => {
  const sources = readers()
  sources[0].read = async () => ({
    items: Array.from({ length: SOURCE_GRAPH_LIMITS.nodes + 1 }, (_, index) => ({ facility_code: `F${index}` })), hasNextPage: false,
  })
  await assert.rejects(readOntologySourceGraph(contract(), sources), /node\/payload budget/)
})

test('enforces explicitly declared cardinality rather than inventing ambiguous links', async () => {
  const ontology = contract()
  ontology.relationshipTypes[0].sourceCardinality = 'one'
  const sources = readers()
  sources[1].read = async () => ({
    items: [{ system_id: 'S1', facility_code: 'F1' }, { system_id: 'S2', facility_code: 'F1' }], hasNextPage: false,
  })
  await assert.rejects(readOntologySourceGraph(ontology, sources), /source cardinality/)
})

test('rejects a newer contract snapshot instead of relabelling a previously read source graph', async () => {
  const ontology = contract()
  const graph = await readOntologySourceGraph(ontology, readers())
  assert.equal((await loadNativeGraphSnapshot(async () => graph, async () => ontology)).ontologyGraph, graph)
  ontology.entityTypes[0].name = 'Changed live definition'
  await assert.rejects(loadNativeGraphSnapshot(async () => graph, async () => ontology), /contract changed/)
})

test('bounded 2000-node governed projection builds and presents within one second', async () => {
  const sources = readers()
  sources[1].read = async () => ({
    items: Array.from({ length: 1999 }, (_, index) => ({ system_id: `S${index}`, facility_code: 'F1' })), hasNextPage: false,
  })
  const started = performance.now()
  const ontology = contract()
  const topology = await readOntologySourceGraph(ontology, sources)
  const graph = buildKnowledgeGraph({
    ontology, ontologyGraph: topology, facilities: [], systems: [], equipment: [], instruments: [], telemetry: [],
    workOrders: [], inspections: [], notifications: [], models: [],
  })
  assert.equal(graph.nodes.length, 2000)
  assert.equal(graph.edges.length, 1999)
  assert.ok(performance.now() - started < 1000, '2000-node source assembly and presentation must stay below 1000 ms')
})
