import assert from 'node:assert/strict'
import { performance } from 'node:perf_hooks'
import test from 'node:test'
import type { KnowledgeEdge, KnowledgeNode } from '../src/ui-shared/knowledgeGraphModel.ts'
import { buildKnowledgeGraph3DLayout, telemetryFlowEdgeIds } from '../src/ui-shared/knowledgeGraphVisuals.ts'

const node = (id: string, type: KnowledgeNode['type'], reading = false): KnowledgeNode => ({
  id,
  entityId: id,
  type,
  label: id,
  subtitle: type,
  status: reading ? 'warn' : 'ok',
  properties: {},
  provenance: 'test',
  reading: reading ? { opcuaNodeId: id, eventTime: '2026-10-02T00:00:00Z', value: 42, quality: 'Good' } : undefined,
})

test('telemetry flow follows only governed topology toward the facility', () => {
  const nodes = [
    node('facility', 'facility'),
    node('system', 'system'),
    node('equipment', 'equipment'),
    node('instrument', 'instrument', true),
    node('work-order', 'work-order'),
  ]
  const edges: KnowledgeEdge[] = [
    { id: 'instrument-equipment', source: 'instrument', target: 'equipment', type: 'has-instrument', label: 'instrument' },
    { id: 'equipment-system', source: 'equipment', target: 'system', type: 'contains', label: 'equipment' },
    { id: 'system-facility', source: 'system', target: 'facility', type: 'contains', label: 'system' },
    { id: 'equipment-work-order', source: 'equipment', target: 'work-order', type: 'affects', label: 'work order' },
  ]
  assert.deepEqual([...telemetryFlowEdgeIds(nodes, edges)].sort(), [
    'equipment-system',
    'instrument-equipment',
    'system-facility',
  ])
})

test('3D layouts are deterministic, finite, and distinct', () => {
  const nodes = Array.from({ length: 40 }, (_, index) =>
    node(`node-${index}`, (['facility', 'system', 'equipment', 'instrument'] as const)[index % 4]))
  const hierarchy = buildKnowledgeGraph3DLayout(nodes, 'breadthfirst')
  const repeated = buildKnowledgeGraph3DLayout(nodes, 'breadthfirst')
  const network = buildKnowledgeGraph3DLayout(nodes, 'cose')
  assert.deepEqual([...hierarchy.positions], [...repeated.positions])
  assert.notDeepEqual([...hierarchy.positions], [...network.positions])
  assert.ok(hierarchy.radius > 0)
  for (const point of hierarchy.positions.values()) {
    assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z))
  }
})

test('maximum supported graph visual preparation stays within an interactive budget', () => {
  const nodes = Array.from({ length: 2000 }, (_, index) =>
    node(`node-${index}`, (['facility', 'system', 'equipment', 'instrument'] as const)[index % 4], index % 80 === 0))
  const edges: KnowledgeEdge[] = Array.from({ length: 4000 }, (_, index) => ({
    id: `edge-${index}`,
    source: `node-${index % nodes.length}`,
    target: `node-${(index + 1) % nodes.length}`,
    type: 'contains',
    label: 'contains',
  }))
  const started = performance.now()
  const layout = buildKnowledgeGraph3DLayout(nodes, 'concentric')
  const flowing = telemetryFlowEdgeIds(nodes, edges)
  const elapsed = performance.now() - started
  assert.equal(layout.positions.size, 2000)
  assert.ok(flowing.size > 0)
  assert.ok(elapsed < 250, `visual preparation took ${elapsed.toFixed(1)} ms`)
})
