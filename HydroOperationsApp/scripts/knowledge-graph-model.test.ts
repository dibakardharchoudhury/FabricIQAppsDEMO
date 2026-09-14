import assert from 'node:assert/strict'
import test from 'node:test'
import { parseOntologyContract } from '../src/services/ontologyContract.ts'
import { parseOntologyGraph } from '../src/services/ontologyGraph.ts'
import { buildKnowledgeGraph, isExactKnowledgeNodeMatch, matchesKnowledgeNodeQuery } from '../src/ui-shared/knowledgeGraphModel.ts'
import { selectGraphModel, selectOntology } from '../src/services/ontologyArtifactDiscovery.ts'

const part = (path: string, payload: object) => ({ path, payload: Buffer.from(JSON.stringify(payload)).toString('base64'), payloadType: 'InlineBase64' })

const ontology = parseOntologyContract('ontology-1', 'Hydro Ontology', { definition: { parts: [
  part('EntityTypes/facilities/definition.json', { id: 'facilities', name: 'facilities', entityIdParts: ['facility-id'], properties: [{ id: 'facility-id', name: 'facility_id' }] }),
  part('EntityTypes/systems/definition.json', { id: 'systems', name: 'systems', entityIdParts: ['system-id'], properties: [{ id: 'system-id', name: 'system_id' }] }),
  part('EntityTypes/equipment/definition.json', { id: 'equipment', name: 'equipment', entityIdParts: ['equipment-id'], properties: [{ id: 'equipment-id', name: 'equipment_id' }] }),
  part('EntityTypes/instruments/definition.json', { id: 'instruments', name: 'instruments', entityIdParts: ['instrument-id'], properties: [{ id: 'instrument-id', name: 'instrument_id' }] }),
  part('RelationshipTypes/system-facility/definition.json', { id: 'system-facility', name: 'systems_in_facilities', source: { entityTypeId: 'systems' }, target: { entityTypeId: 'facilities' } }),
  part('RelationshipTypes/system-facility/Contextualizations/context.json', { sourceKeyRefBindings: [{ sourceColumnName: 'system_id' }], targetKeyRefBindings: [{ sourceColumnName: 'facility_id' }] }),
  part('RelationshipTypes/equipment-system/definition.json', { id: 'equipment-system', name: 'equipment_in_systems', source: { entityTypeId: 'equipment' }, target: { entityTypeId: 'systems' } }),
  part('RelationshipTypes/instrument-equipment/definition.json', { id: 'instrument-equipment', name: 'instruments_on_equipment', source: { entityTypeId: 'instruments' }, target: { entityTypeId: 'equipment' } }),
] } })

test('builds a connected semantic graph and enriches instruments with bound readings', () => {
  const graph = buildKnowledgeGraph({
    facilities: [{ facility_id: 'F1', facility_name: 'Hydro Station' }],
    systems: [{ system_id: 'S1', facility_id: 'F1', system_name: 'Turbine System' }],
    equipment: [{ equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', tag: 'T001', equipment_type_name: 'Turbine' }],
    instruments: [{ instrument_id: 'I1', equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', opcua_node_id: 'ns=2;s=T001.temp', tag: 'Bearing temperature', unit: '°C' }],
    telemetry: [{ opcuaNodeId: 'ns=2;s=T001.temp', eventTime: '2026-09-14T08:00:00Z', value: 96.8, quality: 'Bad' }],
    workOrders: [{ id: 'W1', workOrderNumber: 'WO-1', equipmentId: 'E1', opcuaNodeId: 'ns=2;s=T001.temp', title: 'Inspect bearing', priority: 'Critical', status: 'In progress', createdByOid: 'user', createdAt: new Date('2026-09-14T07:00:00Z') }],
    inspections: [],
    notifications: [],
    models: [],
  })

  assert.equal(graph.nodes.length, 5)
  assert.equal(graph.edges.length, 4)
  assert.ok(graph.nodes.some(node => node.id === 'system:S1' && node.label === 'Turbine System'))
  assert.ok(graph.edges.some(edge => edge.source === 'facility:F1' && edge.target === 'system:S1'))
  assert.ok(graph.edges.some(edge => edge.source === 'equipment:E1' && edge.target === 'instrument:I1'))
  const instrument = graph.nodes.find(node => node.id === 'instrument:I1')
  assert.equal(instrument?.reading?.value, 96.8)
  assert.equal(instrument?.status, 'crit')
  assert.match(instrument?.provenance ?? '', /time-series binding/i)
})

test('drops relationships whose source entity is unavailable', () => {
  const graph = buildKnowledgeGraph({
    facilities: [], systems: [], equipment: [], instruments: [], telemetry: [], inspections: [], notifications: [], models: [],
    workOrders: [{ id: 'W1', workOrderNumber: 'WO-1', equipmentId: 'missing', title: 'Orphan', priority: 'Low', status: 'Draft', createdByOid: 'user', createdAt: new Date() }],
  })
  assert.equal(graph.nodes.length, 1)
  assert.equal(graph.edges.length, 0)
})

test('uses the decoded Ontology relationship contract for core topology', () => {
  const graph = buildKnowledgeGraph({
    facilities: [{ facility_id: 'F1', facility_name: 'Hydro Station' }],
    systems: [{ system_id: 'S1', facility_id: 'F1', system_name: 'Turbine System' }],
    equipment: [{ equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', tag: 'T001' }],
    instruments: [{ instrument_id: 'I1', equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', opcua_node_id: 'node-1' }],
    telemetry: [], workOrders: [], inspections: [], notifications: [], models: [], ontology,
  })

  assert.equal(ontology.relationshipTypes[0]?.sourceKeys[0], 'system_id')
  assert.equal(ontology.relationshipTypes[0]?.targetKeys[0], 'facility_id')
  assert.ok(graph.edges.some(item => item.ontologyRelationshipId === 'system-facility' && item.label === 'SYSTEMS IN FACILITIES'))
  assert.ok(graph.edges.some(item => item.ontologyRelationshipId === 'equipment-system'))
  assert.ok(graph.edges.some(item => item.ontologyRelationshipId === 'instrument-equipment'))
})

test('does not invent a core relationship when an Ontology contract omits it', () => {
  const graph = buildKnowledgeGraph({
    facilities: [{ facility_id: 'F1', facility_name: 'Hydro Station' }],
    systems: [{ system_id: 'S1', facility_id: 'F1' }],
    equipment: [], instruments: [], telemetry: [], workOrders: [], inspections: [], notifications: [], models: [],
    ontology: { ...ontology, relationshipTypes: [] },
  })
  assert.equal(graph.edges.length, 0)
})

test('uses materialized Ontology topology and combines a one-to-one bound signal with its instrument', () => {
  const serialized = (oid: string, labels: string[], properties: object) => JSON.stringify({ oid, labels, properties })
  const ontologyGraph = parseOntologyGraph('graph-1', 'Hydro Ontology graph', [
    { node: serialized('facility-oid', ['facilities'], { facility_id: 'F1', facility_name: 'Hydro Station' }) },
    { node: serialized('system-oid', ['systems'], { system_id: 'S1', facility_id: 'F1', system_name: 'Turbine System' }) },
    { node: serialized('equipment-oid', ['equipment'], { equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', tag: 'T001' }) },
    { node: serialized('instrument-oid', ['instruments'], { instrument_id: 'I1', equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', opcua_node_id: 'node-1' }) },
    { node: serialized('signal-oid', ['signal_master'], { instrument_id: 'I1', equipment_id: 'E1', facility_id: 'F1', system_id: 'S1', opcua_node_id: 'node-1', tag: 'Bearing temperature', unit: 'C' }) },
    { node: serialized('document-oid', ['engineering_documents'], { document_id: 'D1', document_name: 'Turbine manual', equipment_id: 'E1' }) },
  ], [
    { source: serialized('system-oid', ['systems'], {}), relationship: JSON.stringify({ oid: 'r1', labels: ['systems_in_facilities'], properties: {}, ends: [{ oid: 'system-oid' }, { oid: 'facility-oid' }] }), target: serialized('facility-oid', ['facilities'], {}) },
    { source: serialized('equipment-oid', ['equipment'], {}), relationship: JSON.stringify({ oid: 'r2', labels: ['equipment_in_systems'], properties: {}, ends: [{ oid: 'equipment-oid' }, { oid: 'system-oid' }] }), target: serialized('system-oid', ['systems'], {}) },
    { source: serialized('instrument-oid', ['instruments'], {}), relationship: JSON.stringify({ oid: 'r3', labels: ['instruments_on_equipment'], properties: {}, ends: [{ oid: 'instrument-oid' }, { oid: 'equipment-oid' }] }), target: serialized('equipment-oid', ['equipment'], {}) },
    { source: serialized('signal-oid', ['signal_master'], {}), relationship: JSON.stringify({ oid: 'r4', labels: ['signals_from_instruments'], properties: {}, ends: [{ oid: 'signal-oid' }, { oid: 'instrument-oid' }] }), target: serialized('instrument-oid', ['instruments'], {}) },
    { source: serialized('document-oid', ['engineering_documents'], {}), relationship: JSON.stringify({ oid: 'r5', labels: ['documents_equipment'], properties: {}, ends: [{ oid: 'document-oid' }, { oid: 'equipment-oid' }] }), target: serialized('equipment-oid', ['equipment'], {}) },
  ])
  const graph = buildKnowledgeGraph({
    facilities: [], systems: [], equipment: [], instruments: [], ontologyGraph, ontology,
    telemetry: [{ opcuaNodeId: 'node-1', eventTime: '2026-09-14T08:00:00Z', value: 96.8, quality: 'Bad' }],
    workOrders: [{ id: 'W1', workOrderNumber: 'WO-1', equipmentId: 'E1', title: 'Inspect bearing', priority: 'Critical', status: 'In progress', createdByOid: 'user', createdAt: new Date() }],
    inspections: [], notifications: [], models: [],
  })

  assert.equal(ontologyGraph.nodes.length, 6)
  assert.equal(ontologyGraph.edges.length, 5)
  assert.ok(graph.edges.some(item => item.source === 'equipment:E1' && item.target === 'system:S1' && item.ontologyRelationshipName === 'equipment_in_systems'))
  assert.ok(!graph.edges.some(item => item.type === 'has-signal'))
  assert.ok(!graph.nodes.some(item => item.id === 'signal:node-1'))
  assert.equal(graph.nodes.find(item => item.id === 'instrument:I1')?.reading?.value, 96.8)
  assert.equal(graph.nodes.find(item => item.id === 'instrument:I1')?.properties['Signal entity'], 'node-1')
  assert.match(graph.nodes.find(item => item.id === 'instrument:I1')?.provenance ?? '', /combined one-to-one instruments \+ signal_master/i)
  assert.ok(graph.edges.some(item => item.source === 'work-order:W1' && item.target === 'equipment:E1'))
  assert.ok(graph.nodes.some(item => item.type === 'ontology' && item.label === 'Turbine manual'))
  assert.ok(graph.edges.some(item => item.label === 'DOCUMENTS EQUIPMENT'))
  assert.match(graph.nodes.find(item => item.id === 'equipment:E1')?.provenance ?? '', /materialized graph node/i)
})

test('discovers renamed Ontology and Graph Model artifacts without version or generated-name assumptions', () => {
  const items = [
    { id: 'ontology-deployment-b', type: 'Ontology', displayName: 'Plant semantics release 2031' },
    { id: 'unrelated-graph', type: 'GraphModel', displayName: 'Supply chain' },
    { id: 'materialized-graph', type: 'GraphModel', displayName: 'Opaque generated title' },
  ]
  const ontology = selectOntology(items)
  const graph = selectGraphModel(items, ['facilities', 'equipment'], new Map([
    ['unrelated-graph', new Set(['supplier', 'purchase_order'])],
    ['materialized-graph', new Set(['facilities', 'equipment'])],
  ]))

  assert.equal(ontology?.id, 'ontology-deployment-b')
  assert.equal(graph?.id, 'materialized-graph')
})

test('finds an asset globally by tag or entity id regardless of selected graph scope', () => {
  const node = {
    id: 'equipment:EQUIP_RTI_T009', entityId: 'EQUIP_RTI_T009', type: 'equipment' as const,
    label: 'T009', subtitle: 'Turbine', status: 'ok' as const, facilityId: 'FAC_RTI_001',
    equipmentId: 'EQUIP_RTI_T009', properties: { Manufacturer: 'Voith' }, provenance: 'test',
  }

  assert.equal(matchesKnowledgeNodeQuery(node, 't009'), true)
  assert.equal(matchesKnowledgeNodeQuery(node, 'equip_rti_t009'), true)
  assert.equal(matchesKnowledgeNodeQuery(node, 'voith'), true)
  assert.equal(isExactKnowledgeNodeMatch(node, 'T009'), true)
})