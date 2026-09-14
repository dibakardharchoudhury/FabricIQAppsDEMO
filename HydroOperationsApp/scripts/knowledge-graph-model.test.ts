import assert from 'node:assert/strict'
import test from 'node:test'
import { buildKnowledgeGraph } from '../src/ui-shared/knowledgeGraphModel.ts'

test('builds a connected semantic graph and enriches instruments with bound readings', () => {
  const graph = buildKnowledgeGraph({
    facilities: [{ facility_id: 'F1', facility_name: 'Hydro Station' }],
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
  assert.ok(graph.nodes.some(node => node.id === 'system:F1:S1' && node.properties.Inferred === true))
  assert.ok(graph.edges.some(edge => edge.source === 'facility:F1' && edge.target === 'system:F1:S1'))
  assert.ok(graph.edges.some(edge => edge.source === 'equipment:E1' && edge.target === 'instrument:I1'))
  const instrument = graph.nodes.find(node => node.id === 'instrument:I1')
  assert.equal(instrument?.reading?.value, 96.8)
  assert.equal(instrument?.status, 'crit')
  assert.match(instrument?.provenance ?? '', /time-series binding/i)
})

test('drops relationships whose source entity is unavailable', () => {
  const graph = buildKnowledgeGraph({
    facilities: [], equipment: [], instruments: [], telemetry: [], inspections: [], notifications: [], models: [],
    workOrders: [{ id: 'W1', workOrderNumber: 'WO-1', equipmentId: 'missing', title: 'Orphan', priority: 'Low', status: 'Draft', createdByOid: 'user', createdAt: new Date() }],
  })
  assert.equal(graph.nodes.length, 1)
  assert.equal(graph.edges.length, 0)
})