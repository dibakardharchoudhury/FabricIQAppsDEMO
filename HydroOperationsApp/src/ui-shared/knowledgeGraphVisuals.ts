import type { KnowledgeEdge, KnowledgeNode } from './knowledgeGraphModel'

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

export function telemetryFlowEdgeIds(nodes: KnowledgeNode[], edges: KnowledgeEdge[]): Set<string> {
  const flowing = new Set<string>()
  const queue = nodes.filter(node => node.reading).map(node => node.id)
  const visited = new Set(queue)
  const outgoing = new Map<string, KnowledgeEdge[]>()
  for (const edge of edges) {
    if (!topologyEdges.has(edge.type)) continue
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge])
  }
  while (queue.length) {
    const nodeId = queue.shift()!
    for (const edge of outgoing.get(nodeId) ?? []) {
      flowing.add(edge.id)
      if (visited.has(edge.target)) continue
      visited.add(edge.target)
      queue.push(edge.target)
    }
  }
  return flowing
}
