import assert from 'node:assert/strict'
import test from 'node:test'
import { parseOntologyContract, type OntologyContract } from '../src/services/ontologyContract.ts'
import { parseOntologyGraph, type OntologyGraph } from '../src/services/ontologyGraph.ts'
import { buildKnowledgeGraph, buildOntologyModel, isExactKnowledgeNodeMatch, knowledgeGraphFocusId, knowledgeGraphScope, loadNativeGraphSnapshot, matchesKnowledgeNodeQuery, NATIVE_GRAPH_LOAD_TIMEOUT_MS, type KnowledgeGraphInput } from '../src/ui-shared/knowledgeGraphModel.ts'
import { selectOntology } from '../src/services/ontologyArtifactDiscovery.ts'
import { createOntologyCache } from '../src/services/ontologyCache.ts'

const part = (path: string, text: string) => ({ path, payload: Buffer.from(text).toString('base64'), payloadType: 'InlineBase64' })

test('ontology model renders every declared type and relationship without native or source instances', () => {
  const before = JSON.stringify(ontology)
  const graph = buildOntologyModel(ontology)
  assert.equal(graph.error, undefined)
  assert.equal(graph.nodes.length, ontology.entityTypes.length)
  assert.equal(graph.edges.length, ontology.relationshipTypes.length)
  assert.ok(graph.nodes.every(node => !node.nativeOid && !node.equipmentId && !node.reading && node.status === 'nodata'))
  for (const relationship of ontology.relationshipTypes) {
    const edge = graph.edges.find(item => item.ontologyRelationshipId === relationship.id)!
    assert.equal(edge.source, `ontology-type:${relationship.sourceEntityTypeId}`)
    assert.equal(edge.target, `ontology-type:${relationship.targetEntityTypeId}`)
    assert.equal(edge.nativeOid, undefined)
  }
  assert.equal(JSON.stringify(ontology), before)
})

test('ontology model follows endpoint identities, keeps parallel declarations and does not invent isolate links', () => {
  const contract = structuredClone(ontology)
  const relationship = contract.relationshipTypes[0]
  relationship.sourceEntityName = 'deliberately misleading name'
  relationship.targetEntityName = relationship.sourceEntityName
  contract.relationshipTypes.push({ ...relationship, id: 'parallel', name: 'Another declared association', compatibilityUnsupported: true })
  const graph = buildOntologyModel(contract)
  assert.equal(graph.error, undefined)
  assert.equal(graph.edges.length, contract.relationshipTypes.length)
  const parallel = graph.edges.find(edge => edge.ontologyRelationshipId === 'parallel')!
  assert.equal(parallel.source, `ontology-type:${relationship.sourceEntityTypeId}`)
  assert.equal(parallel.target, `ontology-type:${relationship.targetEntityTypeId}`)
  const isolated = contract.entityTypes.find(entity => !contract.relationshipTypes.some(edge =>
    edge.sourceEntityTypeId === entity.id || edge.targetEntityTypeId === entity.id))!
  assert.ok(graph.nodes.some(node => node.ontologyEntityTypeId === isolated.id))
  assert.ok(!graph.edges.some(edge => edge.source === `ontology-type:${isolated.id}` || edge.target === `ontology-type:${isolated.id}`))
})

test('ontology model rejects ambiguous or dangling definitions without a partial diagram', () => {
  const dangling = structuredClone(ontology)
  dangling.relationshipTypes[0].targetEntityTypeId = 'absent'
  const duplicateEntity = structuredClone(ontology)
  duplicateEntity.entityTypes.push(duplicateEntity.entityTypes[0])
  const duplicateRelationship = structuredClone(ontology)
  duplicateRelationship.relationshipTypes.push(duplicateRelationship.relationshipTypes[0])
  for (const contract of [null, dangling, duplicateEntity, duplicateRelationship]) {
    const graph = buildOntologyModel(contract)
    assert.ok(graph.error)
    assert.deepEqual(graph.nodes, [])
    assert.deepEqual(graph.edges, [])
  }
})
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

test('open maintenance priority contributes the correct equipment health', () => {
  const options = input({
    telemetry: [{ opcuaNodeId: 'node-1', eventTime: '2026-09-30T19:00:00Z', value: 326.702, quality: 'GOOD' }],
  })
  options.workOrders[0].priority = 'High'
  const graph = buildKnowledgeGraph(options)
  const instrument = graph.nodes.find(node => node.id === 'instrument:I1')!
  assert.equal(instrument.status, 'ok')
  assert.equal(instrument.reading?.quality, 'GOOD')
  assert.equal(instrument.reading?.value, 326.702)
  assert.equal(graph.nodes.find(node => node.id === 'equipment:E1')?.status, 'warn')
  assert.ok(!graph.nodes.some(node => node.id === 'work-order:W1'), 'health context cannot fabricate topology')
  options.workOrders[0].priority = 'Critical'
  assert.equal(buildKnowledgeGraph(options).nodes.find(node => node.id === 'equipment:E1')?.status, 'crit')
  options.telemetry = [{ opcuaNodeId: 'node-1', eventTime: '2026-09-30T19:00:30Z', value: 327.1, quality: 'UNCERTAIN' }]
  const refreshed = buildKnowledgeGraph(options).nodes.find(node => node.id === 'instrument:I1')!
  assert.equal(refreshed.status, 'warn')
  assert.equal(refreshed.reading?.eventTime, '2026-09-30T19:00:30Z')
  assert.equal(refreshed.reading?.value, 327.1)
})

test('does not render STID topology or SQL overlays before the native query completes', () => {
  const graph = buildKnowledgeGraph(input({
    ontologyGraph: null,
    facilities: [{ facility_id: 'F1', facility_name: 'Station' }],
    systems: [{ system_id: 'S1', facility_id: 'F1', system_name: 'System' }],
    equipment: [{ equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', tag: 'Turbine' }],
    instruments: [{ instrument_id: 'I1', equipment_id: 'E1', system_id: 'S1', facility_id: 'F1', opcua_node_id: 'node-1' }],
  }))
  assert.match(graph.error ?? '', /No verified native Ontology graph.*Manage graph/)
  assert.deepEqual(graph.nodes, [])
  assert.deepEqual(graph.edges, [])
})

test('keeps non-bijective and externally referenced signals separate without redirecting edges', () => {
  const multiple = nativeGraph()
  multiple.nodes.push({ ...multiple.nodes.find(node => node.oid === 'v1')!, oid: 'v2', properties: { opcua_node_id: 'node-2' } })
  multiple.edges.push({ oid: 'si2', labels: ['signals_from_instruments'], sourceOid: 'v2', targetOid: 'i1', properties: {} })
  const separate = buildKnowledgeGraph(input({ ontologyGraph: multiple }))
  assert.equal(separate.nodes.filter(node => node.type === 'signal').length, 2)
  assert.equal(separate.nodes.find(node => node.id === 'signal:node-1')?.label, 'Signal · Temperature signal')
  assert.equal(separate.nodes.find(node => node.id === 'instrument:I1')?.properties['Signal entity'], undefined)
  const single = nativeGraph()
  single.edges.push({ oid: 'signal-document', labels: ['documented_by'], sourceOid: 'v1', targetOid: 'd1', properties: {} })
  const referenced = buildKnowledgeGraph(input({ ontologyGraph: single }))
  assert.equal(referenced.edges.find(edge => edge.nativeOid === 'signal-document')?.source, 'signal:node-1')
  assert.equal(referenced.edges.find(edge => edge.nativeOid === 'signal-document')?.target, 'ontology:d1')
  assert.ok(referenced.nodes.some(node => node.id === 'signal:node-1'))
  assert.equal(referenced.nodes.find(node => node.id === 'instrument:I1')?.properties['Signal entity'], undefined)
  assert.ok(referenced.edges.some(edge => edge.nativeOid === 'si'))
})

test('native maintenance relationships preserve direction and identity without masquerading as signal topology', () => {
  const native = nativeGraph()
  native.nodes.push({
    oid: 'work', labels: ['WorkOrder'], properties: { id: 'NW1', workOrderNumber: 'WO-native', priority: 'High', status: 'Open' },
  })
  native.edges.push(
    { oid: 'for-instrument', labels: ['WorkOrder_for_instrument'], sourceOid: 'work', targetOid: 'i1', properties: {} },
    { oid: 'for-signal', labels: ['WorkOrder_for_signal'], sourceOid: 'work', targetOid: 'v1', properties: {} },
    { oid: 'for-equipment', labels: ['WorkOrder_for_equipment'], sourceOid: 'work', targetOid: 'e1', properties: {} },
  )
  const contract = { ...ontology, entityTypes: [...ontology.entityTypes, {
    ...ontology.entityTypes[0], id: 'work-entity', name: 'WorkOrder', localName: 'WorkOrder', sourceTable: 'WorkOrder', entityIdParts: ['id'],
  }] }
  const graph = buildKnowledgeGraph(input({ ontologyGraph: native, ontology: contract }))
  assert.equal(graph.error, undefined)
  const maintenance = graph.edges.filter(edge => ['for-instrument', 'for-signal'].includes(edge.nativeOid ?? ''))
  assert.equal(maintenance.length, 2)
  assert.ok(maintenance.every(edge => edge.type === 'affects' && edge.source === 'work-order:NW1'))
  assert.equal(maintenance.find(edge => edge.nativeOid === 'for-instrument')?.target, 'instrument:I1')
  assert.equal(maintenance.find(edge => edge.nativeOid === 'for-signal')?.target, 'signal:node-1')
  assert.equal(graph.edges.find(edge => edge.nativeOid === 'for-equipment')?.target, 'equipment:E1')
  assert.ok(graph.edges.some(edge => edge.nativeOid === 'si' && edge.source === 'signal:node-1' && edge.target === 'instrument:I1'))
  assert.notEqual(maintenance[0].id, maintenance[1].id)
})

test('missing native topology cannot expose SQL overlay nodes or guessed relationships', () => {
  const graph = buildKnowledgeGraph(input({
    ontologyGraph: null, ontology: { ...ontology, relationshipTypes: [] },
  }))
  assert.match(graph.error ?? '', /native Ontology graph/)
  assert.deepEqual(graph.nodes, [])
  assert.deepEqual(graph.edges, [])
})

test('a complete demo-sized STID inventory still cannot substitute for native topology', () => {
  const facilities = Array.from({ length: 3 }, (_, index) => ({ facility_id: `F${index}`, facility_name: `Station ${index}` }))
  const systems = Array.from({ length: 6 }, (_, index) => ({ system_id: `S${index}`, facility_id: `F${index % 3}` }))
  const equipment = Array.from({ length: 12 }, (_, index) => ({ equipment_id: `E${index}`, facility_id: `F${index % 3}`, system_id: `S${index % 6}` }))
  const instruments = Array.from({ length: 90 }, (_, index) => ({
    instrument_id: `I${index}`, equipment_id: `E${index % 12}`, facility_id: `F${index % 3}`,
    system_id: `S${index % 6}`, opcua_node_id: `node-${index}`,
  }))
  const graph = buildKnowledgeGraph(input({ ontologyGraph: null, facilities, systems, equipment, instruments }))
  assert.match(graph.error ?? '', /Lakehouse rows cannot substitute for native topology/)
  assert.deepEqual(graph.nodes, [])
  assert.deepEqual(graph.edges, [])
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

test('page snapshot allows a successful 55-second cold native graph query', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const graph = { ...nativeGraph(), ontologyId: ontology.id }
  const snapshot = loadNativeGraphSnapshot(
    () => new Promise(resolve => setTimeout(() => resolve(graph), 55_000)),
    async () => ontology,
  )
  context.mock.timers.tick(55_000)
  assert.deepEqual(await snapshot, { ontologyGraph: graph, ontology })
})

test('page snapshot still fails closed at its bounded discovery/query deadline', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  let contractReads = 0
  const snapshot = loadNativeGraphSnapshot(() => new Promise(() => undefined), async () => {
    contractReads++
    return ontology
  })
  const rejected = assert.rejects(snapshot, /Ontology graph loading exceeded 150 seconds/)
  assert.equal(NATIVE_GRAPH_LOAD_TIMEOUT_MS, 150_000)
  context.mock.timers.tick(NATIVE_GRAPH_LOAD_TIMEOUT_MS)
  await rejected
  assert.equal(contractReads, 0)
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

test('requires a verified v2 contract and rejects invalid native results rather than masking them', () => {
  for (const overrides of [
    { ontology: null },
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

test('retains KQL readings and maintenance health without appending undeclared SQL topology in native mode', () => {
  const options = input()
  options.workOrders.push({ ...options.workOrders[0], id: 'orphan', equipmentId: 'MISSING' })
  options.models.push({ ...options.models[0], id: 'orphan', equipmentId: 'MISSING' })
  options.inspections.push({ ...options.inspections[0], id: 'orphan', equipmentId: 'MISSING' })
  options.notifications.push({ ...options.notifications[0], id: 'orphan', equipmentId: 'MISSING' })
  options.telemetry.push({ opcuaNodeId: 'not-native', eventTime: '', value: 1, quality: 'Good' })
  const graph = buildKnowledgeGraph(options)
  assert.equal(graph.nodes.length, 7)
  assert.equal(graph.edges.length, 5)
  assert.ok(!graph.nodes.some(node => node.id.endsWith(':orphan') || node.entityId === 'not-native'))
  for (const id of ['instrument:I1']) {
    const node = graph.nodes.find(node => node.id === id)
    assert.equal(node?.reading?.value, 96.8)
    assert.equal(node?.status, 'crit')
    assert.match(node?.provenance ?? '', /Eventhouse time-series binding/)
  }
  for (const id of ['work-order:W1', 'inspection:IN1', 'notification:N1', 'model:M1']) {
    assert.ok(!graph.nodes.some(node => node.id === id))
  }
  assert.ok(graph.edges.every(edge => edge.nativeOid && !edge.provenance?.includes('SQL operational overlay')))
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
  for (const id of ['equipment:E1', 'system:S1', 'facility:F1', 'instrument:I1', 'ontology:d1']) assert.ok(selected.has(id), id)
  assert.ok(!selected.has('facility:F2'))
  const facility = knowledgeGraphScope(graph, 'facility', 'instrument:I1')!
  assert.ok(facility.has('facility:F1'))
  assert.ok(facility.has('ontology:d1'))
  assert.ok(!facility.has('facility:F2'))
  assert.equal(knowledgeGraphScope(graph, 'all'), undefined)
})

test('facility and all scopes do not dim nodes until the user explicitly selects one', () => {
  assert.equal(knowledgeGraphFocusId('asset', undefined, 'equipment:E1'), 'equipment:E1')
  assert.equal(knowledgeGraphFocusId('facility', undefined, 'equipment:E1'), undefined)
  assert.equal(knowledgeGraphFocusId('all', undefined, 'equipment:E1'), undefined)
  assert.equal(knowledgeGraphFocusId('facility', 'equipment:E2', 'equipment:E1'), 'equipment:E2')
  assert.equal(knowledgeGraphFocusId('all', 'facility:F2', 'equipment:E1'), 'facility:F2')
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
  for (const id of ['equipment:E1', 'system:S1', 'facility:F1', 'instrument:I1', 'ontology:d1']) {
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
