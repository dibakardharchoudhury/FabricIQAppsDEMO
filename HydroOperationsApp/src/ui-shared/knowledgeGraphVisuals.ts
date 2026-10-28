import type { KnowledgeEdge, KnowledgeNode } from './knowledgeGraphModel'
import type { Core, Css } from 'cytoscape'

export function layoutOntologyModel(graph: Core, layout: 'breadthfirst' | 'cose' | 'concentric') {
  if (graph.nodes().empty()) return
  const components = graph.elements().components().sort((a, b) =>
    b.nodes().length - a.nodes().length || a.nodes()[0].id().localeCompare(b.nodes()[0].id()))
  const connected = components.filter(component => component.edges().nonempty())
  const isolates = components.filter(component => component.edges().empty()).flatMap(component => component.nodes().toArray())
  const boxes = connected.map(component => {
    const roots = ['facility', 'system', 'equipment', 'instrument', 'signal']
      .map(type => component.nodes().filter(node => node.data('type') === type).map(node => node.id()))
      .find(ids => ids.length)
      ?? [component.nodes().sort((a, b) => b.degree() - a.degree() || a.id().localeCompare(b.id()))[0].id()]
    const options = { name: layout, fit: false, animate: false, nodeDimensionsIncludeLabels: true, padding: 32 }
    if (layout === 'breadthfirst') component.layout({ ...options, name: 'breadthfirst', roots, directed: false, spacingFactor: 1.35 }).run()
    else if (layout === 'concentric') component.layout({ ...options, name: 'concentric', minNodeSpacing: 65 }).run()
    else component.layout({ ...options, name: 'cose', idealEdgeLength: 180, nodeRepulsion: () => 9000 }).run()
    return { component, bounds: component.nodes().boundingBox({ includeLabels: true, includeOverlays: false }) }
  })
  const area = boxes.reduce((sum, { bounds }) => sum + (bounds.w + 100) * (bounds.h + 100), 0)
  const aspect = Math.max(1, graph.width() / Math.max(1, graph.height()))
  const pairWidth = boxes.length === 2 && graph.width() >= graph.height()
    ? boxes.reduce((sum, { bounds }) => sum + bounds.w, 110) : 0
  const width = Math.max(380, ...boxes.map(({ bounds }) => bounds.w), Math.sqrt(area * aspect), pairWidth)
  let x = 0
  let y = 0
  let rowHeight = 0
  for (const { component, bounds } of boxes) {
    if (x && x + bounds.w > width) { x = 0; y += rowHeight + 110; rowHeight = 0 }
    component.nodes().positions(node => ({ x: node.position('x') - bounds.x1 + x, y: node.position('y') - bounds.y1 + y }))
    x += bounds.w + 110
    rowHeight = Math.max(rowHeight, bounds.h)
  }
  const columns = Math.max(1, Math.min(Math.floor(width / 190), isolates.length))
  isolates.forEach((node, index) => node.position({
    x: (index % columns) * 190 + 60,
    y: y + rowHeight + (boxes.length ? 140 : 60) + Math.floor(index / columns) * 150,
  }))
  graph.fit(undefined, 56)
}

export const KNOWLEDGE_EDGE_INTERACTION_STYLE: Css.Edge = {
  'overlay-opacity': 0,
  'overlay-padding': 0,
}

export const KNOWLEDGE_TYPE_COLOR: Record<KnowledgeNode['type'], string> = {
  facility: '#22d3ee',
  system: '#60a5fa',
  equipment: '#c084fc',
  instrument: '#2dd4bf',
  signal: '#38bdf8',
  ontology: '#94a3b8',
  model: '#a78bfa',
  'work-order': '#fbbf24',
  inspection: '#fb923c',
  notification: '#fb7185',
}

export const KNOWLEDGE_STATUS_COLOR: Record<KnowledgeNode['status'], string> = {
  ok: '#34d399',
  warn: '#fbbf24',
  crit: '#fb4d6d',
  nodata: '#94a3b8',
}

export const KNOWLEDGE_EDGE_COLOR: Record<KnowledgeEdge['type'], string> = {
  contains: '#7dd3fc',
  'has-instrument': '#5eead4',
  'has-signal': '#38bdf8',
  'has-model': '#c4b5fd',
  affects: '#fbbf24',
  documents: '#cbd5e1',
  reports: '#fb7185',
}

export const KNOWLEDGE_NODE_SIZE: Record<KnowledgeNode['type'], number> = {
  facility: 58,
  system: 48,
  equipment: 44,
  instrument: 34,
  signal: 28,
  ontology: 36,
  model: 30,
  'work-order': 32,
  inspection: 28,
  notification: 32,
}

const topologyEdges = new Set<KnowledgeEdge['type']>(['contains', 'has-instrument', 'has-signal'])
const topologyRanks: Partial<Record<KnowledgeNode['type'], number>> = {
  facility: 0, system: 1, equipment: 2, instrument: 3, signal: 4,
}

export function knowledgeTelemetryState(eventTime: string | undefined, nowMs = Date.now()): 'live' | 'delayed' | 'stale' | 'unavailable' {
  const age = eventTime ? nowMs - Date.parse(eventTime) : NaN
  if (!Number.isFinite(age) || age < 0) return 'unavailable'
  return age < 60_000 ? 'live' : age < 300_000 ? 'delayed' : 'stale'
}

export function knowledgeHaloPadding(padding: number, zoom: number): number {
  return padding / Math.sqrt(Math.min(1, zoom))
}

export function knowledgeFaultPulse(status: 'crit' | 'warn', timeMs: number, zoom = 1): { opacity: number; padding: number } {
  const phase = (1 - Math.cos(timeMs * Math.PI * 2 / (status === 'crit' ? 1600 : 2200))) / 2
  return status === 'crit'
    ? { opacity: 0.14 + phase * 0.3, padding: knowledgeHaloPadding(9 + phase * 13, zoom) }
    : { opacity: 0.12 + phase * 0.22, padding: knowledgeHaloPadding(9 + phase * 10, zoom) }
}

export function knowledgeEdgeRoutes(edges: KnowledgeEdge[]): Map<string, { distance: number; labelOffset: number }> {
  const groups = new Map<string, KnowledgeEdge[]>()
  for (const edge of edges) {
    const key = JSON.stringify([edge.source, edge.target].sort())
    const group = groups.get(key)
    if (group) group.push(edge)
    else groups.set(key, [edge])
  }
  const routes = new Map<string, { distance: number; labelOffset: number }>()
  for (const group of groups.values()) {
    group.sort((a, b) => a.id.localeCompare(b.id))
    group.forEach((edge, index) => {
      const lane = index - (group.length - 1) / 2
      routes.set(edge.id, {
        distance: lane * 208 * (edge.source > edge.target ? -1 : 1),
        labelOffset: group.length > 1 ? (lane < 0 ? -7 : 7) : -4,
      })
    })
  }
  return routes
}

function topologyAncestors(nodes: KnowledgeNode[], edges: KnowledgeEdge[], seeds: Iterable<string>) {
  const edgeIds = new Set<string>()
  const byId = new Map(nodes.map(node => [node.id, node]))
  const queue = [...new Set(seeds)]
  const visited = new Set(queue)
  const parents = new Map<string, Array<{ edge: KnowledgeEdge; parentId: string }>>()
  for (const edge of edges) {
    if (!topologyEdges.has(edge.type)) continue
    const source = byId.get(edge.source)
    const target = byId.get(edge.target)
    const sourceRank = source && topologyRanks[source.type]
    const targetRank = target && topologyRanks[target.type]
    if (sourceRank === undefined || targetRank === undefined || sourceRank === targetRank) continue
    // Trace ancestors without changing the Ontology's directed relationship endpoints.
    const childId = sourceRank > targetRank ? edge.source : edge.target
    const parentId = childId === edge.source ? edge.target : edge.source
    const group = parents.get(childId)
    if (group) group.push({ edge, parentId })
    else parents.set(childId, [{ edge, parentId }])
  }
  for (let index = 0; index < queue.length; index++) {
    const nodeId = queue[index]
    for (const { edge, parentId } of parents.get(nodeId) ?? []) {
      edgeIds.add(edge.id)
      if (visited.has(parentId)) continue
      visited.add(parentId)
      queue.push(parentId)
    }
  }
  return { nodeIds: visited, edgeIds }
}

export function knowledgeFocusElementIds(nodes: KnowledgeNode[], edges: KnowledgeEdge[], selectedId: string): Set<string> {
  const neighborIds = new Set([selectedId])
  const relationshipIds = new Set<string>()
  for (const edge of edges) {
    if (edge.source !== selectedId && edge.target !== selectedId) continue
    neighborIds.add(edge.source)
    neighborIds.add(edge.target)
    relationshipIds.add(edge.id)
  }
  const ancestors = topologyAncestors(nodes, edges, neighborIds)
  return new Set([...ancestors.nodeIds, ...ancestors.edgeIds, ...relationshipIds])
}

export function telemetryFlowEdgeIds(nodes: KnowledgeNode[], edges: KnowledgeEdge[], nowMs = Date.now()): Set<string> {
  const fresh = nodes.filter(node => node.reading && knowledgeTelemetryState(node.reading.eventTime, nowMs) === 'live')
  return topologyAncestors(nodes, edges, fresh.map(node => node.id)).edgeIds
}
