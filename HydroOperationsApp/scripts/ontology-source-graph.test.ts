import assert from 'node:assert/strict'
import test from 'node:test'
import { parseOntologyContract } from '../src/services/ontologyContract.ts'
import { readOntologySourceGraph, SOURCE_GRAPH_LIMITS, type OntologySourceReader } from '../src/services/ontologySourceGraph.ts'
import { buildKnowledgeGraph, knowledgeGraphScope, loadNativeGraphSnapshot, loadOntologySourceSnapshot } from '../src/ui-shared/knowledgeGraphModel.ts'
import { serializeKnowledgeGraph } from '../src/services/knowledgeGraphExport.ts'

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

test('presents real source instances and declared links without a materialized graph or synthetic overlays', async () => {
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
  assert.ok(graph.nodes.every(node => node.provenance.includes('live source binding')))
  assert.ok(!graph.nodes.some(node => node.entityId === 'fabricated-overlay'))
  assert.ok(!graph.nodes.some(node => node.provenance.includes('materialized')))
  const exported = serializeKnowledgeGraph(ontology, topology, 'owl-turtle')
  assert.ok(exported.includes('source-backed'))
  assert.ok(!exported.includes(':graphModelId>'))
})

test('source snapshot verifies the exact contract used for instance reads and rejects changed bindings', async () => {
  const ontology = contract()
  const graph = await readOntologySourceGraph(ontology, readers())
  const snapshot = await loadOntologySourceSnapshot(async () => graph, async () => ontology)
  assert.equal(snapshot.ontologyGraph, graph)
  assert.equal(snapshot.ontology, ontology)
  await assert.rejects(loadOntologySourceSnapshot(async () => graph, async () => ({
    ...ontology, relationshipTypes: [],
  })), /definition changed/)
  await assert.rejects(loadOntologySourceSnapshot(async () => { throw new Error('Source unavailable') }, async () => {
    assert.fail('Failed source reads must not reuse an older contract or graph.')
  }), /Source unavailable/)
})

  test('diagnostic joins and synthetic native presentation retain distinct maintenance endpoints', async () => {
    const rows: Record<string, Array<Record<string, string>>> = {
      facilities: [{ facility_id: 'F1' }],
      systems: [{ system_id: 'S1', facility_id: 'F1' }],
      equipment: [{ equipment_id: 'E1', system_id: 'S1' }],
      instruments: [
        { instrument_id: 'I1', equipment_id: 'E1', opcua_node_id: 'power' },
        { instrument_id: 'I2', equipment_id: 'E1', opcua_node_id: 'temperature' },
      ],
      signal_master: [
        { opcua_node_id: 'power', instrument_id: 'I1' },
        { opcua_node_id: 'temperature', instrument_id: 'I2' },
      ],
      WorkOrder: [{ id: 'W1', equipmentId: 'E1', instrumentId: 'I1', opcuaNodeId: 'power' }],
      Inspection: [{ id: 'IN1', equipmentId: 'E1', opcuaNodeId: 'power' }],
      MaintenanceNotification: [{ id: 'N1', equipmentId: 'E1', opcuaNodeId: 'power' }],
    }
    const relationships = [
      ['systems_in_facilities', 'systems', 'facility_id', 'facilities', 'facility_id'],
      ['equipment_in_systems', 'equipment', 'system_id', 'systems', 'system_id'],
      ['instruments_on_equipment', 'instruments', 'equipment_id', 'equipment', 'equipment_id'],
      ['signals_from_instruments', 'signal_master', 'instrument_id', 'instruments', 'instrument_id'],
      ['WorkOrder_for_equipment', 'WorkOrder', 'equipmentId', 'equipment', 'equipment_id'],
      ['WorkOrder_for_instrument', 'WorkOrder', 'instrumentId', 'instruments', 'instrument_id'],
      ['WorkOrder_for_signal', 'WorkOrder', 'opcuaNodeId', 'signal_master', 'opcua_node_id'],
      ['Inspection_for_equipment', 'Inspection', 'equipmentId', 'equipment', 'equipment_id'],
      ['Inspection_for_signal', 'Inspection', 'opcuaNodeId', 'signal_master', 'opcua_node_id'],
      ['MaintenanceNotification_for_equipment', 'MaintenanceNotification', 'equipmentId', 'equipment', 'equipment_id'],
      ['MaintenanceNotification_for_signal', 'MaintenanceNotification', 'opcuaNodeId', 'signal_master', 'opcua_node_id'],
    ]
    const parts = [definition().definition.parts[0]]
    for (const [name, records] of Object.entries(rows)) {
      const columns = Object.keys(records[0])
      parts.push(
        part(`tables/${name}.tmdl`, [
          `table ${name}`,
          ...columns.map(column => `    column ${column}\n      dataType: string\n      sourceColumn: ${column}`),
          `    partition ${name} = entity`, '      mode: directLake', '      source',
          `        entityName: ${name}`, "        expressionSource: 'Bound Lake'",
        ].join('\n')),
        part(`entities/${name}.tmdl`, [
          `entity ${name}`, `    backingTable: ${name}`, `    keyProperty: ${columns[0]}`,
          ...columns.map(column => `    property ${column}\n      dataType: string\n      lineageTag: ${name}-${column}\n      backingConfiguration\n        type: default\n        valueColumn: ${name}.${column}`),
        ].join('\n')),
      )
    }
    parts.push(
      part('relationships.tmdl', relationships.map(([name, from, source, to, target]) =>
        `relationship ${name}\n    fromColumn: ${from}.${source}\n    toColumn: ${to}.${target}`).join('\n')),
      part('entityRelationships.tmdl', relationships.map(([name, from, , to]) =>
        `entityRelationship ${name}\n    lineageTag: ${name}\n    fromEntity: ${from}\n    toEntity: ${to}\n    backingConfiguration\n      relationship: ${name}`).join('\n')),
    )
    const ontology = parseOntologyContract('ontology', 'Maintenance join fixture', { definition: { parts } }, 2)
    const sources: OntologySourceReader[] = Object.entries(rows).map(([name, records]) => ({
      workspaceId: workspace, itemId: item, itemKind: 'Lakehouse', schema: 'dbo', table: name,
      columns: Object.keys(records[0]), read: async () => ({ items: records, hasNextPage: false }),
    }))
    const topology = await readOntologySourceGraph(ontology, sources)
    const native = {
      execution: 'native' as const, ontologyId: ontology.id, graphModelId: 'fixture-native', graphModelName: 'Fixture',
      nodes: topology.nodes, edges: topology.edges,
    }
    for (const ontologyGraph of [native]) {
      const graph = buildKnowledgeGraph({
        ontology, ontologyGraph, facilities: [], systems: [], equipment: [], instruments: [], telemetry: [],
        workOrders: [], inspections: [], notifications: [], models: [],
      })
      assert.equal(graph.error, undefined)
      for (const [name, source, target] of [
        ['WorkOrder_for_equipment', 'work-order:W1', 'equipment:E1'],
        ['WorkOrder_for_instrument', 'work-order:W1', 'instrument:I1'],
        ['WorkOrder_for_signal', 'work-order:W1', 'signal:power'],
        ['Inspection_for_signal', 'inspection:IN1', 'signal:power'],
        ['MaintenanceNotification_for_signal', 'notification:N1', 'signal:power'],
      ]) {
        const edge = graph.edges.find(edge => edge.ontologyRelationshipName === name)
        assert.equal(edge?.source, source, name)
        assert.equal(edge?.target, target, name)
        assert.equal(edge?.ontologyRelationshipId, name)
      }
      assert.ok(graph.edges.some(edge => edge.source === 'signal:power' && edge.target === 'instrument:I1'))
      assert.equal(graph.nodes.find(node => node.id === 'instrument:I1')?.properties['Signal entity'], undefined)
      assert.equal(graph.nodes.find(node => node.id === 'instrument:I2')?.properties['Signal entity'], 'temperature')
      assert.ok(!graph.nodes.some(node => node.id === 'signal:temperature'), 'unreferenced one-to-one leaves stay compact')
      const scope = knowledgeGraphScope(graph, 'asset', 'equipment:E1')!
      for (const id of ['work-order:W1', 'inspection:IN1', 'notification:N1', 'signal:power']) assert.ok(scope.has(id), id)
      const ids = new Set(graph.nodes.map(node => node.id))
      assert.ok(graph.edges.every(edge => ids.has(edge.source) && ids.has(edge.target)))
    }
    const renderFresh = async () => buildKnowledgeGraph({
      ontology, ontologyGraph: { ...native, ...await readOntologySourceGraph(ontology, sources), execution: 'native' },
      facilities: [], systems: [], equipment: [], instruments: [], telemetry: [],
      workOrders: [], inspections: [], notifications: [], models: [],
    })
    rows.WorkOrder.push({ id: 'W2', equipmentId: 'E1', instrumentId: '', opcuaNodeId: '' })
    const afterCreate = await renderFresh()
    assert.ok(afterCreate.nodes.some(node => node.id === 'work-order:W2'))
    assert.ok(afterCreate.edges.some(edge => edge.source === 'work-order:W2'
      && edge.target === 'equipment:E1' && edge.ontologyRelationshipName === 'WorkOrder_for_equipment'))
    assert.ok(!afterCreate.edges.some(edge => edge.source === 'work-order:W2' && edge.target === 'signal:power'))
    rows.WorkOrder.pop()
    const afterDelete = await renderFresh()
    assert.ok(!afterDelete.nodes.some(node => node.id === 'work-order:W2'))
    assert.ok(!afterDelete.edges.some(edge => edge.source === 'work-order:W2'))
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

test('native snapshot rejects source reconstruction before reading or relabelling its contract', async () => {
  const ontology = contract()
  const graph = await readOntologySourceGraph(ontology, readers())
  await assert.rejects(loadNativeGraphSnapshot(async () => graph, async () => {
    assert.fail('A source projection must not be certified as a native snapshot.')
  }), /Source-reconstructed topology cannot replace/)
})

test('bounded 2000-node diagnostic source projection assembles within one second', async () => {
  const sources = readers()
  sources[1].read = async () => ({
    items: Array.from({ length: 1999 }, (_, index) => ({ system_id: `S${index}`, facility_code: 'F1' })), hasNextPage: false,
  })
  const started = performance.now()
  const ontology = contract()
  const topology = await readOntologySourceGraph(ontology, sources)
  assert.equal(topology.nodes.length, 2000)
  assert.equal(topology.edges.length, 1999)
  assert.ok(performance.now() - started < 1000, '2000-node diagnostic source assembly must stay below 1000 ms')
})
