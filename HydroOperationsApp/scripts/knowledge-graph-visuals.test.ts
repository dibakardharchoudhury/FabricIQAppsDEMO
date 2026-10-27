import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { performance } from 'node:perf_hooks'
import test from 'node:test'
import type { KnowledgeEdge, KnowledgeNode } from '../src/ui-shared/knowledgeGraphModel.ts'
import { telemetryFlowEdgeIds } from '../src/ui-shared/knowledgeGraphVisuals.ts'

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

test('flow starts when telemetry arrives, not when topology is loaded', () => {
  const edges: KnowledgeEdge[] = [
    { id: 'binding', source: 'instrument', target: 'equipment', type: 'has-instrument', label: 'instrument' },
  ]
  assert.equal(telemetryFlowEdgeIds([node('instrument', 'instrument'), node('equipment', 'equipment')], edges).size, 0)
  assert.deepEqual([...telemetryFlowEdgeIds([
    node('instrument', 'instrument', true), node('equipment', 'equipment'),
  ], edges)], ['binding'])
})

test('compact graph presentation retains earlier fluid strokes and keeps diagnostics below the canvas', () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
  const page = read('../src/ui-shared/pages/KnowledgeGraphPage.tsx')
  const canvas = read('../src/ui-shared/components/knowledgeGraph/KnowledgeGraphCanvas.tsx')
  const styles = read('../src/ui-shared/styles/app.css')
  assert.ok(page.indexOf('<details className="kg-source-coverage">') > page.indexOf('<div className={`kg-workspace'))
  assert.doesNotMatch(page, /JSON · topology, ontology/)
  assert.match(page, /Live ontology sources · not materialized/)
  assert.match(page, /Moving dashes appear only after telemetry readings are loaded/)
  assert.match(canvas, /width: 3\.2, opacity: 0\.92/)
  assert.match(canvas, /'line-dash-pattern': \[10, 12\]/)
  assert.match(canvas, /% 22/)
  assert.match(canvas, /graph\.edges\(\)\.length > 150/)
  assert.match(canvas, /prefers-reduced-motion/)
  assert.match(styles, /\.kg-source-state button \{[^}]*grid-row: 1 \/ span 2/)
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
  const flowing = telemetryFlowEdgeIds(nodes, edges)
  const elapsed = performance.now() - started
  assert.ok(flowing.size > 0)
  assert.ok(elapsed < 250, `visual preparation took ${elapsed.toFixed(1)} ms`)
})
