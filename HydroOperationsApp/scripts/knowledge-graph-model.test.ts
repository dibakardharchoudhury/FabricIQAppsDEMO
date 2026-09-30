import assert from 'node:assert/strict'
import test from 'node:test'
import { parseOntologyContract, type OntologyContract } from '../src/services/ontologyContract.ts'
import { parseOntologyGraph, type OntologyGraph } from '../src/services/ontologyGraph.ts'
import { buildKnowledgeGraph, isExactKnowledgeNodeMatch, knowledgeGraphScope, loadNativeGraphSnapshot, matchesKnowledgeNodeQuery, type KnowledgeGraphInput } from '../src/ui-shared/knowledgeGraphModel.ts'
import { selectOntology } from '../src/services/ontologyArtifactDiscovery.ts'
import { createOntologyCache } from '../src/services/ontologyCache.ts'

const part = (path: string, text: string) => ({ path, payload: Buffer.from(text).toString('base64'), payloadType: 'InlineBase64' })
const entity = (name: string, key: string, foreignKey?: string) => part(`entities/${name}.tmdl`, `entity ${name}
    backingTable: ${name}
    keyProperty: ${key}_id
    property ${key}_id
        dataType: string
        lineageTag: ${key}-id
${foreignKey ? `    property ${foreignKey}_id\n        dataType: string\n` : ''}`)

const ontology = parseOntologyContract('ontology-1', 'Hydro Ontology', { definition: { parts: [
  entity('facilities', 'facility'), entity('systems', 'system', 'facility'),
  entity('equipment', 'equipment', 'system'), entity('instruments', 'instrument', 'equipment'),
  entity('signal_master', 'opcua_node', 'instrument'), entity('engineering_documents', 'document', 'equipment'),
  part('relationships.tmdl', `relationship system_facility
    fromColumn: systems.facility_id
    toColumn: facilities.facility_id
relationship equipment_system
    fromColumn: equipment.system_id
    toColumn: systems.system_id
relationship instrument_equipment
    fromColumn: instruments.equipment_id
    toColumn: equipment.equipment_id`),
  part('entityRelationships.tmdl', `entityRelationship systems_in_facilities
    lineageTag: system-facility
    fromEntity: systems
    toEntity: facilities
    backingConfiguration
        relationship: system_facility
entityRelationship equipment_in_systems
    lineageTag: equipment-system
    fromEntity: equipment
    toEntity: systems
    backingConfiguration
        relationship: equipment_system
entityRelationship instruments_on_equipment
    lineageTag: instrument-equipment
    fromEntity: instruments
    toEntity: equipment
    backingConfiguration
        relationship: instrument_equipment`),
] } }, 2)

const serialized = (oid: string, labels: string[], properties: object) => JSON.stringify({ oid, labels, properties })
const nativeGraph = (): OntologyGraph => parseOntologyGraph('graph-1', 'Native Hydro graph', [
  { node: serialized('f1', ['facilities'], { facility_id: 'F1', facility_name: 'Native Station' }) },
  { node: serialized('f2', ['facilities'], { facility_id: 'F2', facility_name: 'Other Station' }) },
  { node: serialized('s1', ['systems'], { system_id: 'S1', facility_id: 'F2', system_name: 'Native System' }) },
  { node: serialized('e1', ['equipment'], { equipment_id: 'E1', facility_id: 'F2', system_id: 'STALE', tag: 'Native turbine' }) },
  { node: serialized('i1', ['instruments'], { instrument_id: 'I1', equipment_id: 'WRONG', facility_id: 'F2', opcua_node_id: 'node-1', tag: 'Bearing temperature', unit: 'C' }) },
  { node: serialized('v1', ['signal_master'], { opcua_node_id: 'node-1', instrument_id: 'WRONG', tag: 'Temperature signal', unit: 'C' }) },
  { node: serialized('d1', ['engineering_documents'], { document_id: 'D1', document_name: 'Turbine manual', equipment_id: 'WRONG' }) },
  { node: serialized('u1', ['future_entity'], { future_id: 'U1', future_name: 'Future native entity' }) },
], [
  ['sf', 's1', 'f1', 'systems_in_facilities'], ['es', 'e1', 's1', 'equipment_in_systems'],
  ['ie', 'i1', 'e1', 'instruments_on_equipment'], ['si', 'v1', 'i1', 'signals_from_instruments'],
  ['de', 'd1', 'e1', 'documents_equipment'], ['ue', 'u1', 'e1', 'future_relationship'],
].map(([oid, sourceOid, targetOid, label]) => ({
  source: serialized(sourceOid, [], {}), target: serialized(targetOid, [], {}),
  relationship: serialized(oid, [label], {}),
})))

const input = (overrides: Partial<KnowledgeGraphInput> = {}): KnowledgeGraphInput => ({
  facilities: [{ facility_id: 'STID', facility_name: 'Not native' }],
  systems: [{ system_id: 'STALE', facility_id: 'STID', system_name: 'Not native' }],
  equipment: [{ equipment_id: 'E1', facility_id: 'STID', system_id: 'STALE', tag: 'Wrong STID turbine' }],
  instruments: [],
  telemetry: [{ opcuaNodeId: 'node-1', eventTime: '2026-09-14T08:00:00Z', value: 96.8, quality: 'Bad' }],
  workOrders: [{ id: 'W1', workOrderNumber: 'WO-1', equipmentId: 'E1', opcuaNodeId: 'node-1', title: 'Inspect bearing', priority: 'Critical', status: 'In progress', createdByOid: 'user', createdAt: new Date('2026-09-14T07:00:00Z') }],
  inspections: [{ id: 'IN1', equipmentId: 'E1', inspectionType: 'Bearing check', result: 'Pass', inspectorOid: 'user', inspectedAt: new Date() }],
  notifications: [{ id: 'N1', equipmentId: 'E1', summary: 'Bearing noise', severity: 'High', status: 'Open', reportedByOid: 'user', reportedAt: new Date() }],
  models: [{ id: 'M1', equipmentId: 'E1', modelName: 'Turbine model', format: 'glb', modelUrl: 'https://example.test/model.glb', updatedByOid: 'user', updatedAt: new Date() }],
  ontology, ontologyGraph: nativeGraph(),
  ...overrides,
})

test('preserves main presentation for one-to-one signals and the existing inspector', () => {
  const graph = buildKnowledgeGraph(input())
  assert.equal(graph.error, undefined)
  assert.ok(!graph.nodes.some(node => node.type === 'signal'))
  const instrument = graph.nodes.find(node => node.id === 'instrument:I1')!
  assert.deepEqual(instrument.properties, {
    'Instrument ID': 'I1', Type: undefined, 'OPC UA node': 'node-1', Unit: 'C', Active: undefined,
    'Latest value': 96.8, Quality: 'Bad', 'Event time': '2026-09-14T08:00:00Z', 'Signal entity': 'node-1',
  })
  assert.equal(instrument.provenance, 'Fabric Ontology · Native Hydro graph · combined one-to-one instruments + signal_master node with Eventhouse time-series binding')
  const system = graph.nodes.find(node => node.id === 'system:S1')!
  assert.equal(system.subtitle, '1 connected assets')
  assert.deepEqual(system.properties, { 'System ID': 'S1', 'OAG RDS code': undefined, 'Equipment count': 1 })
  assert.equal(graph.nodes.find(node => node.id === 'facility:F1')?.subtitle, 'Facility')
  assert.deepEqual(Object.keys(graph.nodes.find(node => node.id === 'equipment:E1')!.properties),
    ['Equipment ID', 'Type', 'Manufacturer', 'Model', 'Criticality', 'Status', 'Installed', 'Active'])
  assert.ok(!graph.edges.some(edge => edge.nativeOid === 'si'))
})

test('keeps non-bijective signals separate and redirects other edges of collapsed signals', () => {
  const multiple = nativeGraph()
  multiple.nodes.push({ ...multiple.nodes.find(node => node.oid === 'v1')!, oid: 'v2', properties: { opcua_node_id: 'node-2' } })
  multiple.edges.push({ oid: 'si2', labels: ['signals_from_instruments'], sourceOid: 'v2', targetOid: 'i1', properties: {} })
  const separate = buildKnowledgeGraph(input({ ontologyGraph: multiple }))
  assert.equal(separate.nodes.filter(node => node.type === 'signal').length, 2)
  assert.equal(separate.nodes.find(node => node.id === 'signal:node-1')?.label, 'Signal · Temperature signal')
  assert.equal(separate.nodes.find(node => node.id === 'instrument:I1')?.properties['Signal entity'], undefined)
  const single = nativeGraph()
  single.edges.push({ oid: 'signal-document', labels: ['documented_by'], sourceOid: 'v1', targetOid: 'd1', properties: {} })
  const collapsed = buildKnowledgeGraph(input({ ontologyGraph: single }))
  assert.equal(collapsed.edges.find(edge => edge.nativeOid === 'signal-document')?.source, 'instrument:I1')
  assert.equal(collapsed.edges.find(edge => edge.nativeOid === 'signal-document')?.target, 'ontology:d1')
})

test('page snapshot reads the native query-refreshed contract, never the earlier cached definition', async () => {
  const cache = createOntologyCache<OntologyContract>(60_000)
  const stale = { ...ontology, relationshipTypes: [] }
  const fresh = { ...ontology, displayName: 'Fresh governed contract' }
  await cache.read(ontology.id, false, async () => stale)
  const events: string[] = []
  const snapshot = await loadNativeGraphSnapshot(async () => {
    events.push('native query started')
    await cache.read(ontology.id, true, async () => fresh)
    events.push('native query finished')
    return { ...nativeGraph(), ontologyId: ontology.id }
  }, async () => {
    events.push('page reads refreshed contract')
    return cache.read(ontology.id, false, async () => { throw new Error('Fresh native contract should already be cached') })
  })
  assert.deepEqual(events, ['native query started', 'native query finished', 'page reads refreshed contract'])
  assert.equal(snapshot.ontology, fresh)
  assert.equal(buildKnowledgeGraph(input(snapshot)).error, undefined)
})

test('page snapshot does not reuse cached contracts after graph failures or selection changes', async () => {
  let contractReads = 0
  await assert.rejects(loadNativeGraphSnapshot(async () => { throw new Error('Native query failed') }, async () => {
    contractReads++
    return ontology
  }), /Native query failed/)
  assert.equal(contractReads, 0)
  const missing = await loadNativeGraphSnapshot(async () => null, async () => { contractReads++; return ontology })
  assert.deepEqual(missing, { ontologyGraph: null, ontology: null })
  assert.equal(contractReads, 0)
  await assert.rejects(loadNativeGraphSnapshot(async () => ({ ...nativeGraph(), ontologyId: 'changed' }), async () => ontology), /selection changed/)
})

test('native entities, directed relationships, and context win over conflicting STID and native FK properties', () => {
  const graph = buildKnowledgeGraph(input())
  assert.equal(graph.error, undefined)
  assert.equal(graph.nodes.find(node => node.id === 'equipment:E1')?.label, 'Native turbine')
  assert.ok(!graph.nodes.some(node => node.entityId === 'STID' || node.entityId === 'STALE'))
  assert.equal(graph.nodes.find(node => node.id === 'equipment:E1')?.facilityId, 'F1')
  assert.equal(graph.nodes.find(node => node.id === 'instrument:I1')?.equipmentId, 'E1')
  const native = graph.edges.filter(edge => edge.nativeOid)
  assert.equal(native.length, 5)
  assert.ok(native.some(edge => edge.source === 'equipment:E1' && edge.target === 'system:S1' && edge.nativeOid === 'es'))
  assert.ok(!native.some(edge => edge.source === 'system:S1' && edge.target === 'equipment:E1'))
  assert.equal(native.find(edge => edge.nativeOid === 'es')?.ontologyRelationshipId, 'equipment-system')
  assert.equal(native.find(edge => edge.nativeOid === 'es')?.ontologyRelationshipName, 'equipment_in_systems')
  assert.match(native.find(edge => edge.nativeOid === 'es')?.provenance ?? '', /Ontology equipment_in_systems/)
})

test('requires both a verified v2 contract and native results; never substitutes STID or SQL nodes', () => {
  for (const overrides of [
    { ontologyGraph: null }, { ontology: null },
    { ontology: { ...ontology, generation: 1 } as unknown as OntologyContract },
    { ontologyGraph: { ...nativeGraph(), nodes: [], edges: [] } },
    { ontologyGraph: { ...nativeGraph(), ontologyId: 'different-ontology' } },
    { ontologyGraph: { ...nativeGraph(), graphModelId: '' } },
  ]) {
    const graph = buildKnowledgeGraph(input(overrides))
    assert.deepEqual(graph.nodes, [])
    assert.deepEqual(graph.edges, [])
    assert.ok(graph.error)
  }
})

test('retains KQL readings and every SQL overlay only for actual native equipment', () => {
  const options = input()
  options.workOrders.push({ ...options.workOrders[0], id: 'orphan', equipmentId: 'MISSING' })
  options.models.push({ ...options.models[0], id: 'orphan', equipmentId: 'MISSING' })
  options.inspections.push({ ...options.inspections[0], id: 'orphan', equipmentId: 'MISSING' })
  options.notifications.push({ ...options.notifications[0], id: 'orphan', equipmentId: 'MISSING' })
  options.telemetry.push({ opcuaNodeId: 'not-native', eventTime: '', value: 1, quality: 'Good' })
  const graph = buildKnowledgeGraph(options)
  assert.equal(graph.nodes.length, 11)
  assert.equal(graph.edges.length, 9)
  assert.ok(!graph.nodes.some(node => node.id.endsWith(':orphan') || node.entityId === 'not-native'))
  for (const id of ['instrument:I1']) {
    const node = graph.nodes.find(node => node.id === id)
    assert.equal(node?.reading?.value, 96.8)
    assert.equal(node?.status, 'crit')
    assert.match(node?.provenance ?? '', /Eventhouse time-series binding/)
  }
  for (const id of ['work-order:W1', 'inspection:IN1', 'notification:N1', 'model:M1']) {
    const node = graph.nodes.find(node => node.id === id)
    assert.equal(node?.facilityId, 'F1')
    assert.match(node?.provenance ?? '', /Rayfin operational database/)
  }
  const ids = new Set(graph.nodes.map(node => node.id))
  assert.ok(graph.edges.every(edge => ids.has(edge.source) && ids.has(edge.target)))
})

test('preserves unknown entities, parallel edges, and self-loops with native provenance', () => {
  const ontologyGraph = nativeGraph()
  ontologyGraph.edges.push(
    { oid: 'parallel', labels: ['equipment_in_systems'], sourceOid: 'e1', targetOid: 's1', properties: {} },
    { oid: 'loop', labels: ['future_loop'], sourceOid: 'u1', targetOid: 'u1', properties: {} },
  )
  const graph = buildKnowledgeGraph(input({ ontologyGraph }))
  assert.equal(graph.error, undefined)
  assert.ok(!graph.nodes.some(node => node.id === 'signal:node-1'))
  assert.ok(graph.nodes.some(node => node.nativeOid === 'u1' && node.type === 'ontology' && node.label === 'Future native entity'))
  assert.ok(graph.nodes.some(node => node.nativeOid === 'd1' && node.label === 'Turbine manual'))
  assert.equal(graph.edges.filter(edge => edge.source === 'equipment:E1' && edge.target === 'system:S1').length, 2)
  assert.ok(graph.edges.some(edge => edge.nativeOid === 'loop' && edge.source === edge.target))
  assert.equal(new Set(graph.edges.map(edge => edge.id)).size, graph.edges.length)
})

test('enriches actual native instruments when signal_master is not materialized', () => {
  const ontologyGraph = nativeGraph()
  ontologyGraph.nodes = ontologyGraph.nodes.filter(node => node.oid !== 'v1')
  ontologyGraph.edges = ontologyGraph.edges.filter(edge => edge.sourceOid !== 'v1' && edge.targetOid !== 'v1')
  const graph = buildKnowledgeGraph(input({ ontologyGraph }))
  assert.equal(graph.error, undefined)
  assert.ok(!graph.nodes.some(node => node.type === 'signal'))
  const instrument = graph.nodes.find(node => node.id === 'instrument:I1')
  assert.equal(instrument?.reading?.value, 96.8)
  assert.equal(instrument?.equipmentId, 'E1')
  assert.equal(instrument?.status, 'crit')
  assert.equal(instrument?.provenance, 'Fabric Ontology · Native Hydro graph · materialized graph node')
})

test('does not synthesize missing native relationships from contract or foreign keys', () => {
  const graph = buildKnowledgeGraph(input({ ontologyGraph: { ...nativeGraph(), edges: [] }, workOrders: [], models: [], inspections: [], notifications: [] }))
  assert.equal(graph.error, undefined)
  assert.equal(graph.nodes.length, 8)
  assert.equal(graph.edges.length, 0)
  assert.equal(graph.nodes.find(node => node.id === 'equipment:E1')?.facilityId, undefined)
  assert.equal(graph.nodes.find(node => node.id === 'instrument:I1')?.equipmentId, undefined)
})

test('surfaces incomplete native edges and contract direction mismatches instead of hiding them', () => {
  for (const relationship of [
    { oid: 'broken', labels: ['future_relationship'], sourceOid: 'absent', targetOid: 'e1', properties: {} },
    { oid: 'reversed', labels: ['equipment_in_systems'], sourceOid: 's1', targetOid: 'e1', properties: {} },
  ]) {
    const graph = buildKnowledgeGraph(input({ ontologyGraph: { ...nativeGraph(), edges: [relationship] } }))
    assert.ok(graph.error)
    assert.deepEqual(graph.nodes, [])
    assert.deepEqual(graph.edges, [])
  }
})

test('matches namespaced contract entities and relationship labels by exact semantic identity', () => {
  const namespaced = {
    ...ontology,
    entityTypes: ontology.entityTypes.map(entity => ({ ...entity, name: `hydro#${entity.name}`, namespace: 'hydro', localName: entity.name })),
    relationshipTypes: ontology.relationshipTypes.map(relationship => ({ ...relationship, name: `hydro#${relationship.name}`, label: 'Governed relationship' })),
  }
  const ontologyGraph = nativeGraph()
  ontologyGraph.nodes = ontologyGraph.nodes.map(node => ({ ...node, labels: node.labels.map(label => `hydro#${label}`) }))
  ontologyGraph.edges = ontologyGraph.edges.map(edge => ({ ...edge, labels: edge.labels.map(label => `hydro#${label}`) }))
  const graph = buildKnowledgeGraph(input({ ontology: namespaced, ontologyGraph }))
  assert.equal(graph.error, undefined)
  assert.ok(graph.nodes.some(node => node.id === 'equipment:E1'))
  assert.equal(graph.edges.find(edge => edge.nativeOid === 'es')?.label, 'GOVERNED RELATIONSHIP')
  assert.ok(graph.nodes.some(node => node.nativeOid === 'u1'))
  const localLabels = buildKnowledgeGraph(input({ ontology: namespaced }))
  assert.equal(localLabels.error, undefined)
  assert.ok(localLabels.nodes.some(node => node.id === 'equipment:E1'))
  assert.equal(localLabels.edges.find(edge => edge.nativeOid === 'es')?.ontologyRelationshipName, undefined)
})

test('authoritative schema type IDs take precedence over opaque or misleading native labels', () => {
  const ontologyGraph = nativeGraph()
  ontologyGraph.nodes = ontologyGraph.nodes.map(node => ({
    ...node,
    entityTypeId: ontology.entityTypes.find(entity => node.labels.includes(entity.name))?.id,
    labels: node.oid === 'e1' ? ['facilities'] : [`opaque-alias-${node.oid}`],
  }))
  ontologyGraph.edges = ontologyGraph.edges.map(edge => ({
    ...edge,
    relationshipTypeId: ontology.relationshipTypes.find(relationship => edge.labels.includes(relationship.name))?.id,
    labels: [`opaque-edge-alias-${edge.oid}`],
  }))
  const graph = buildKnowledgeGraph(input({ ontologyGraph }))
  assert.equal(graph.error, undefined)
  assert.equal(graph.nodes.find(node => node.nativeOid === 'e1')?.type, 'equipment')
  assert.equal(graph.nodes.find(node => node.nativeOid === 'e1')?.ontologyEntityTypeId, ontology.entityTypes.find(entity => entity.name === 'equipment')?.id)
  assert.equal(graph.edges.find(edge => edge.nativeOid === 'es')?.ontologyRelationshipName, 'equipment_in_systems')
  assert.equal(graph.nodes.find(node => node.nativeOid === 'i1')?.reading?.value, 96.8)
  assert.equal(graph.nodes.find(node => node.nativeOid === 'u1')?.type, 'ontology')
})

test('does not infer graph namespace separators or aliases and rejects unknown authoritative IDs', () => {
  for (const delimiter of ['#', '_', '.']) {
    const ontologyGraph = nativeGraph()
    ontologyGraph.nodes = ontologyGraph.nodes.map(node => ({ ...node, labels: [`prefix${delimiter}${node.labels[0]}`] }))
    ontologyGraph.edges = ontologyGraph.edges.map(edge => ({ ...edge, labels: [`prefix${delimiter}${edge.labels[0]}`] }))
    const graph = buildKnowledgeGraph(input({ ontologyGraph }))
    assert.equal(graph.error, undefined)
    assert.ok(graph.nodes.every(node => node.type === 'ontology'))
    assert.ok(graph.edges.every(edge => edge.ontologyRelationshipId === undefined))
  }
  const unknownEntity = nativeGraph()
  unknownEntity.nodes[0].entityTypeId = 'not-in-contract'
  assert.match(buildKnowledgeGraph(input({ ontologyGraph: unknownEntity })).error ?? '', /entity type ID/)
  const unknownRelationship = nativeGraph()
  unknownRelationship.edges[0].relationshipTypeId = 'not-in-contract'
  assert.match(buildKnowledgeGraph(input({ ontologyGraph: unknownRelationship })).error ?? '', /relationship type ID/)
})

test('ambiguous local entity labels and duplicate business identities fail closed', () => {
  const base = ontology.entityTypes.find(entity => entity.name === 'equipment')!
  const ambiguous = { ...ontology, entityTypes: [
    ...ontology.entityTypes.filter(entity => entity !== base),
    { ...base, id: 'a', name: 'a.equipment', localName: 'equipment', namespace: 'a' },
    { ...base, id: 'b', name: 'b.equipment', localName: 'equipment', namespace: 'b' },
  ] }
  const graph = buildKnowledgeGraph(input({ ontology: ambiguous }))
  assert.match(graph.error ?? '', /ambiguous Ontology labels/)
  assert.equal(graph.nodes.length, 0)
  const duplicate = nativeGraph()
  duplicate.nodes.push({ ...duplicate.nodes.find(node => node.oid === 'e1')!, oid: 'duplicate' })
  assert.match(buildKnowledgeGraph(input({ ontologyGraph: duplicate })).error ?? '', /ambiguous equipment identity/)
})

test('native scopes work without any GraphQL instance data and follow graph rather than FK containment', () => {
  const graph = buildKnowledgeGraph(input({ facilities: [], systems: [], equipment: [], instruments: [] }))
  const selected = knowledgeGraphScope(graph, 'asset', 'equipment:E1')!
  for (const id of ['equipment:E1', 'system:S1', 'facility:F1', 'instrument:I1', 'ontology:d1', 'work-order:W1']) assert.ok(selected.has(id), id)
  assert.ok(!selected.has('facility:F2'))
  const facility = knowledgeGraphScope(graph, 'facility', 'instrument:I1')!
  assert.ok(facility.has('facility:F1'))
  assert.ok(facility.has('model:M1'))
  assert.ok(!facility.has('facility:F2'))
  assert.equal(knowledgeGraphScope(graph, 'all'), undefined)
})

test('discovers renamed Ontology without selecting a Graph Model', () => {
  const items = [
    { id: 'ontology-deployment-b', type: 'Ontology', displayName: 'Plant semantics release 2031' },
    { id: 'unrelated-graph', type: 'GraphModel', displayName: 'Supply chain' },
    { id: 'materialized-graph', type: 'GraphModel', displayName: 'Opaque generated title' },
  ]
  assert.equal(selectOntology(items)?.id, 'ontology-deployment-b')
})

test('selecting a parent keeps the existing shared asset scope and tree context', () => {
  const graph = buildKnowledgeGraph(input())
  const selected = knowledgeGraphScope(graph, 'asset', 'system:S1', 'E1')!
  for (const id of ['equipment:E1', 'system:S1', 'facility:F1', 'instrument:I1', 'work-order:W1']) {
    assert.ok(selected.has(id), id)
  }
  const facility = knowledgeGraphScope(graph, 'facility', 'facility:F2', 'E1')!
  assert.ok(facility.has('equipment:E1'))
  assert.ok(!facility.has('facility:F2'))
})

test('resolves v2 key property identities without exposing internal metadata in the inspector', () => {
  const keyed = {
    ...ontology,
    entityTypes: ontology.entityTypes.map(entity => ({
      ...entity, entityIdParts: entity.entityIdParts.map(name => entity.propertyMetadata?.[name]?.id ?? name),
    })),
  }
  const graph = buildKnowledgeGraph(input({ ontology: keyed }))
  assert.equal(graph.error, undefined)
  assert.equal(graph.nodes.find(node => node.nativeOid === 'd1')?.entityId, 'D1')
  assert.equal(graph.nodes.find(node => node.nativeOid === 'e1')?.id, 'equipment:E1')
  assert.ok(!Object.hasOwn(graph.nodes.find(node => node.nativeOid === 'e1')!.properties, 'Native OID'))
})

test('finds an asset globally by tag or entity id regardless of selected graph scope', () => {
  const node = buildKnowledgeGraph(input()).nodes.find(node => node.id === 'equipment:E1')!
  assert.equal(matchesKnowledgeNodeQuery(node, 'native turbine'), true)
  assert.equal(matchesKnowledgeNodeQuery(node, 'e1'), true)
  assert.equal(isExactKnowledgeNodeMatch(node, 'Native turbine'), true)
})
