import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { performance } from 'node:perf_hooks'
import test from 'node:test'
import type { KnowledgeEdge, KnowledgeNode } from '../src/ui-shared/knowledgeGraphModel.ts'
import { knowledgeEdgeRoutes, knowledgeFaultPulse, knowledgeHaloPadding, knowledgeTelemetryState, telemetryFlowEdgeIds } from '../src/ui-shared/knowledgeGraphVisuals.ts'

const NOW = Date.parse('2026-10-27T01:00:00Z')
const node = (id: string, type: KnowledgeNode['type'], reading = false): KnowledgeNode => ({
  id,
  entityId: id,
  type,
  label: id,
  subtitle: type,
  status: reading ? 'warn' : 'ok',
  properties: {},
  provenance: 'test',
  reading: reading ? { opcuaNodeId: id, eventTime: new Date(NOW - 5_000).toISOString(), value: 42, quality: 'Good' } : undefined,
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
  assert.deepEqual([...telemetryFlowEdgeIds(nodes, edges, NOW)].sort(), [
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
  ], edges, NOW)], ['binding'])
})

test('flow expires by event time without requiring another fetch and rejects invalid/future timestamps', () => {
  const instrument = node('instrument', 'instrument', true)
  const nodes = [instrument, node('equipment', 'equipment')]
  const edges: KnowledgeEdge[] = [
    { id: 'binding', source: 'instrument', target: 'equipment', type: 'has-instrument', label: 'binding' },
  ]
  for (const age of [60_000, 3_600_000, -1, NaN]) {
    instrument.reading!.eventTime = Number.isNaN(age) ? 'invalid' : new Date(NOW - age).toISOString()
    assert.equal(telemetryFlowEdgeIds(nodes, edges, NOW).size, 0, `age ${age}`)
  }
  instrument.reading!.eventTime = new Date(NOW - 59_999).toISOString()
  assert.equal(telemetryFlowEdgeIds(nodes, edges, NOW).size, 1)
  assert.equal(telemetryFlowEdgeIds(nodes, edges, NOW + 1).size, 0)
})

test('only fresh branches highlight, regardless of declared hierarchy direction; maintenance is never flow', () => {
  const fresh = node('fresh', 'instrument', true)
  const stale = node('stale', 'instrument', true)
  stale.reading!.eventTime = new Date(NOW - 3_600_000).toISOString()
  const nodes = [fresh, stale, node('equipment', 'equipment'), node('facility', 'facility'), node('work', 'work-order')]
  const edges: KnowledgeEdge[] = [
    { id: 'fresh-branch', source: 'equipment', target: 'fresh', type: 'has-instrument', label: 'reverse binding' },
    { id: 'stale-branch', source: 'stale', target: 'equipment', type: 'has-instrument', label: 'binding' },
    { id: 'parent', source: 'facility', target: 'equipment', type: 'contains', label: 'reverse hierarchy' },
    { id: 'work', source: 'fresh', target: 'work', type: 'has-instrument', label: 'maintenance' },
  ]
  assert.deepEqual([...telemetryFlowEdgeIds(nodes, edges, NOW)].sort(), ['fresh-branch', 'parent'])
})

test('freshness uses the shell live/delayed/stale boundaries and rejects missing event time', () => {
  for (const [age, expected] of [[0, 'live'], [59_999, 'live'], [60_000, 'delayed'], [299_999, 'delayed'], [300_000, 'stale'], [3_600_000, 'stale'], [-1, 'unavailable']] as const) {
    assert.equal(knowledgeTelemetryState(new Date(NOW - age).toISOString(), NOW), expected)
  }
  assert.equal(knowledgeTelemetryState(undefined, NOW), 'unavailable')
  assert.equal(knowledgeTelemetryState('', NOW), 'unavailable')
  assert.equal(knowledgeTelemetryState('invalid', NOW), 'unavailable')
})

test('both critical and warning halos visibly breathe without flashing or changing their health color', () => {
  for (const [status, period] of [['crit', 1600], ['warn', 2200]] as const) {
    const rest = knowledgeFaultPulse(status, 0)
    const peak = knowledgeFaultPulse(status, period / 2)
    assert.ok(peak.opacity - rest.opacity >= 0.21)
    assert.ok(peak.padding - rest.padding >= 10)
    assert.ok(peak.opacity < 0.5)
    assert.ok(period >= 1600)
    assert.deepEqual(knowledgeFaultPulse(status, period), rest)
  }
})

test('overview zoom amplifies fault halos without enlarging normal-detail halos', () => {
  assert.equal(knowledgeHaloPadding(13, 1), 13)
  assert.equal(knowledgeHaloPadding(13, 2), 13)
  assert.equal(knowledgeHaloPadding(13, 0.25), 26)
  for (const [status, peakTime] of [['crit', 800], ['warn', 1100]] as const) {
    const zoom = 0.025
    const rest = knowledgeFaultPulse(status, 0, zoom)
    const peak = knowledgeFaultPulse(status, peakTime, zoom)
    assert.ok(peak.padding * zoom >= 3, 'overview halo remains visible in rendered pixels')
    assert.ok(peak.padding > rest.padding)
    assert.equal(peak.opacity, knowledgeFaultPulse(status, peakTime).opacity)
  }
})

test('parallel and opposite relationships retain their endpoints with deterministic separate curve lanes', () => {
  const edges: KnowledgeEdge[] = [
    { id: 'a', source: 'instrument', target: 'work', type: 'affects', label: 'for instrument' },
    { id: 'b', source: 'work', target: 'instrument', type: 'affects', label: 'for signal' },
    { id: 'c', source: 'equipment', target: 'work', type: 'affects', label: 'for equipment' },
  ]
  const original = structuredClone(edges)
  const routes = knowledgeEdgeRoutes(edges)
  assert.deepEqual(edges, original)
  assert.deepEqual(knowledgeEdgeRoutes([...edges].reverse()), routes)
  const canonicalLane = (edge: KnowledgeEdge) => routes.get(edge.id)!.distance * (edge.source > edge.target ? -1 : 1)
  assert.notEqual(canonicalLane(edges[0]), canonicalLane(edges[1]))
  assert.ok(Math.abs(canonicalLane(edges[0]) - canonicalLane(edges[1])) >= 128)
  assert.notEqual(routes.get('a')!.labelOffset, routes.get('b')!.labelOffset)
  assert.equal(routes.get('c')!.distance, 0)
})

test('compact graph presentation retains earlier fluid strokes and keeps diagnostics below the canvas', () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
  const page = read('../src/ui-shared/pages/KnowledgeGraphPage.tsx')
  const canvas = read('../src/ui-shared/components/knowledgeGraph/KnowledgeGraphCanvas.tsx')
  const styles = read('../src/ui-shared/styles/app.css')
  assert.ok(page.indexOf('<details className="kg-source-coverage">') > page.indexOf('<div className={`kg-workspace'))
  assert.doesNotMatch(page, /JSON · topology, ontology/)
  assert.match(page, /'Live ontology sources'/)
  assert.doesNotMatch(page, /not materialized/)
  assert.match(page, /Moving dashes require valid telemetry less than 60 seconds old/)
  assert.doesNotMatch(page, /The reading may be fresh or stale|Static dashed signal/)
  assert.match(canvas, /width: 3\.2, opacity: 0\.92/)
  assert.match(canvas, /'line-dash-pattern': \[10, 12\]/)
  assert.match(canvas, /% 22/)
  assert.match(canvas, /graph\.edges\(\)\.length > 150/)
  assert.match(canvas, /prefers-reduced-motion/)
  assert.match(canvas, /minZoom: 0\.001/)
  assert.match(canvas, /'text-overflow-wrap': 'anywhere'/)
  assert.match(canvas, /boundingBox: \{ x1: 0, y1: 0, w: graph\.width\(\), h: graph\.height\(\) \}/)
  assert.match(styles, /\.kg-source-state button \{[^}]*grid-row: 1 \/ span 2/)
})

test('maximum supported graph visual preparation stays within an interactive budget', () => {
  const nodes = Array.from({ length: 2000 }, (_, index) =>
    node(`node-${index}`, (['facility', 'system', 'equipment', 'instrument'] as const)[index % 4], index % 80 === 3))
  const edges: KnowledgeEdge[] = Array.from({ length: 4000 }, (_, index) => ({
    id: `edge-${index}`,
    source: `node-${index % nodes.length}`,
    target: `node-${(index + 1) % nodes.length}`,
    type: 'contains',
    label: 'contains',
  }))
  const started = performance.now()
  const flowing = telemetryFlowEdgeIds(nodes, edges, NOW)
  assert.equal(knowledgeEdgeRoutes(edges).size, edges.length)
  const elapsed = performance.now() - started
  assert.ok(flowing.size > 0)
  assert.ok(elapsed < 250, `visual preparation took ${elapsed.toFixed(1)} ms`)
})
