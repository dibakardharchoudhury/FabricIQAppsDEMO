import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { performance } from 'node:perf_hooks'
import test from 'node:test'
import cytoscape from 'cytoscape'
import type { KnowledgeEdge, KnowledgeNode } from '../src/ui-shared/knowledgeGraphModel.ts'
import { summarizeKnowledgeGraph } from '../src/ui-shared/knowledgeGraphModel.ts'
import { KNOWLEDGE_EDGE_INTERACTION_STYLE, knowledgeEdgeRoutes, knowledgeFaultPulse, knowledgeFocusElementIds, knowledgeHaloPadding, knowledgeTelemetryState, layoutOntologyModel, telemetryFlowEdgeIds } from '../src/ui-shared/knowledgeGraphVisuals.ts'

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

test('ontology layout packs real components and isolates without altering endpoints or direction', () => {
  for (const layout of ['breadthfirst', 'concentric', 'cose'] as const) {
    const graph = cytoscape({
      headless: true,
      styleEnabled: true,
      elements: [
        ...['facility', 'equipment', 'weather', 'forecast', ...Array.from({ length: 12 }, (_, index) => `isolate-${index}`)]
          .map(id => ({ data: { id, type: id === 'facility' ? 'facility' : 'ontology', label: id } })),
        { data: { id: 'asset-facility', source: 'equipment', target: 'facility' } },
        { data: { id: 'weather-forecast', source: 'weather', target: 'forecast' } },
      ],
    })
    const before = graph.elements().map(element => ({ id: element.id(), source: element.data('source'), target: element.data('target') }))
    layoutOntologyModel(graph, layout)
    assert.deepEqual(graph.elements().map(element => ({ id: element.id(), source: element.data('source'), target: element.data('target') })), before)
    assert.ok(graph.nodes().every(item => Number.isFinite(item.position('x')) && Number.isFinite(item.position('y'))))
    const isolateY = new Set(graph.nodes().filter(item => item.id().startsWith('isolate-')).map(item => item.position('y')))
    assert.ok(isolateY.size > 1, 'unrelated types must form a compact grid, not a long single row')
    const first = graph.getElementById('facility').closedNeighborhood().boundingBox()
    const second = graph.getElementById('weather').closedNeighborhood().boundingBox()
    assert.ok(first.x2 < second.x1 || second.x2 < first.x1 || first.y2 < second.y1 || second.y2 < first.y1, 'component bounds must not overlap')
    graph.destroy()
  }
})

test('default ontology model remains distinct from native asset-instance execution', () => {
  const page = readFileSync(new URL('../src/ui-shared/pages/KnowledgeGraphPage.tsx', import.meta.url), 'utf8')
  const model = readFileSync(new URL('../src/ui-shared/pages/OntologyModelView.tsx', import.meta.url), 'utf8')
  assert.match(page, /useState<'ontology' \| 'native'>\('ontology'\)/)
  assert.match(model, /queryOntologyContract\(force\)/)
  assert.doesNotMatch(model, /\b(?:queryOntologyGraph|queryOntologySourceGraph|GraphModel|graphModelId)\b/)
  assert.match(model, /Schema only - not individual turbines/)
  assert.match(model, /Search filters this list, not the diagram/)
})
test('topology diagnostics distinguish disconnected components from isolates without adding links', () => {
  const nodes = [node('facility', 'facility'), node('equipment', 'equipment'), node('weather', 'ontology'), node('isolated', 'ontology')]
  const edges: KnowledgeEdge[] = [
    { id: 'ef', source: 'equipment', target: 'facility', type: 'contains', label: 'facility' },
    { id: 'self', source: 'weather', target: 'weather', type: 'contains', label: 'self' },
  ]
  const graph = { nodes, edges }
  const before = structuredClone(graph)
  assert.deepEqual(summarizeKnowledgeGraph(graph), { components: 3, isolates: 1 })
  assert.deepEqual(graph, before)
  assert.deepEqual(summarizeKnowledgeGraph({ nodes: [], edges: [] }), { components: 0, isolates: 0 })
  assert.throws(() => summarizeKnowledgeGraph({ nodes: [], edges }), /missing endpoint/)
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

test('selection keeps the full governed ancestor path readable without highlighting sibling branches', () => {
  const nodes = [
    node('facility', 'facility'), node('system', 'system'), node('equipment', 'equipment'),
    node('instrument', 'instrument'), node('signal', 'signal'), node('work', 'work-order'),
    node('sibling', 'equipment'),
  ]
  const edges: KnowledgeEdge[] = [
    { id: 'sf', source: 'system', target: 'facility', type: 'contains', label: 'system' },
    { id: 'es', source: 'equipment', target: 'system', type: 'contains', label: 'equipment' },
    { id: 'ie', source: 'instrument', target: 'equipment', type: 'has-instrument', label: 'instrument' },
    { id: 'si', source: 'signal', target: 'instrument', type: 'has-signal', label: 'signal' },
    { id: 'ws', source: 'work', target: 'signal', type: 'affects', label: 'work order' },
    { id: 'sibling-system', source: 'sibling', target: 'system', type: 'contains', label: 'sibling' },
  ]
  for (const selected of ['work', 'signal', 'instrument', 'equipment']) {
    const focused = knowledgeFocusElementIds(nodes, edges, selected)
    for (const id of ['facility', 'system', 'equipment', 'sf', 'es']) assert.ok(focused.has(id), `${selected}: ${id}`)
    assert.ok(!focused.has('sibling'))
    assert.ok(!focused.has('sibling-system'))
    assert.deepEqual(knowledgeFocusElementIds(nodes, edges.map(edge => ({ ...edge, source: edge.target, target: edge.source })), selected), focused)
  }
  assert.deepEqual(telemetryFlowEdgeIds(nodes, edges, NOW), new Set(), 'selection cannot manufacture live telemetry')
})

test('active and selected Cytoscape edges have no black overlay and retain semantic color and direction', () => {
  const graph = cytoscape({
    headless: true, styleEnabled: true,
    elements: [
      { data: { id: 'work' } }, { data: { id: 'signal' } },
      { data: { id: 'relationship', source: 'work', target: 'signal', color: '#fbbf24' } },
    ],
    style: [
      { selector: 'edge', style: { 'line-color': 'data(color)', 'target-arrow-color': 'data(color)', 'target-arrow-shape': 'triangle', width: 1.9 } },
      { selector: 'edge:active, edge:selected', style: KNOWLEDGE_EDGE_INTERACTION_STYLE },
    ],
  })
  try {
    const edge = graph.getElementById('relationship')
    for (const state of ['active', 'selected'] as const) {
      if (state === 'active') edge.activate()
      else { edge.unactivate(); edge.select() }
      assert.equal(edge.style('overlay-opacity'), '0', state)
      assert.equal(edge.style('overlay-padding'), '0px', state)
      assert.equal(edge.style('line-color'), 'rgb(251,191,36)', state)
      assert.equal(edge.style('target-arrow-color'), 'rgb(251,191,36)', state)
      assert.equal(edge.style('target-arrow-shape'), 'triangle', state)
      assert.equal(edge.source().id(), 'work')
      assert.equal(edge.target().id(), 'signal')
    }
  } finally {
    graph.destroy()
  }
  const canvas = readFileSync(new URL('../src/ui-shared/components/knowledgeGraph/KnowledgeGraphCanvas.tsx', import.meta.url), 'utf8')
  assert.match(canvas, /selector: 'edge:active, edge:selected', style: KNOWLEDGE_EDGE_INTERACTION_STYLE/)
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
  assert.match(page, /'Native graph unavailable'/)
  assert.doesNotMatch(page, /'Live ontology sources'/)
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

test('loading and errors stay inside the graph workspace without a blocking placeholder page', () => {
  const page = readFileSync(new URL('../src/ui-shared/pages/KnowledgeGraphPage.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /v2-placeholder-card|Loading the Ontology graph/)
  assert.match(page, /className="kg-stage" aria-label="Knowledge Graph canvas" aria-busy=\{graphLoading\}/)
  assert.match(page, /kg-loading-skeleton" aria-hidden="true"/)
  assert.match(page, /Entities appear after verification/)
  assert.match(page, /className="kg-no-results" role="alert"/)
  assert.match(page, /Connect Fabric and retry/)
  assert.match(page, /Refreshing · showing last verified graph/)
  assert.match(page, /hasVerifiedGraph && graphQueriedAt/)
  assert.match(page, /disabled=\{!hasVerifiedGraph\} onClick=\{exportNativeGraph\}/)
  assert.match(page, /<summary>Advanced graph details/)
  assert.doesNotMatch(page, /executionControl|queryOntologySourceGraph|setExecution/)
  assert.match(page, /\(\) => queryOntologyGraph\(force\), queryOntologyContract/)
})

test('loading and unavailable headers do not display the live-ready treatment', () => {
  const styles = readFileSync(new URL('../src/ui-shared/styles/app.css', import.meta.url), 'utf8')
  assert.match(styles, /\.kg-source-state\[data-state="loading"\][^}]*var\(--cp-text-muted\)/)
  assert.match(styles, /\.kg-source-state\[data-state="unavailable"\][^}]*var\(--cp-danger\)/)
  assert.match(styles, /\.kg-source-state:not\(\[data-state="ready"\]\) \.kg-live-dot[^}]*box-shadow: none/)
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
  assert.ok(knowledgeFocusElementIds(nodes, edges, 'node-83').size > 0)
  const elapsed = performance.now() - started
  assert.ok(flowing.size > 0)
  assert.ok(elapsed < 250, `visual preparation took ${elapsed.toFixed(1)} ms`)
})
