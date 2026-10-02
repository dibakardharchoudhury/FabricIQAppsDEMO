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

export type KnowledgeGraphPoint3D = { x: number; y: number; z: number }
export type KnowledgeGraph3DLayout = {
  positions: Map<string, KnowledgeGraphPoint3D>
  radius: number
}

const depth: Record<KnowledgeNode['type'], number> = {
  facility: 0,
  system: 1,
  equipment: 2,
  instrument: 3,
  signal: 4,
  ontology: 2,
  model: 3,
  'work-order': 3,
  inspection: 3,
  notification: 3,
}

function stableHash(value: string): number {
  let hash = 2166136261
  for (let index = 0; index < value.length; index++) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

export function buildKnowledgeGraph3DLayout(
  nodes: KnowledgeNode[],
  mode: 'breadthfirst' | 'cose' | 'concentric',
): KnowledgeGraph3DLayout {
  const positions = new Map<string, KnowledgeGraphPoint3D>()
  if (!nodes.length) return { positions, radius: 240 }
  const ordered = [...nodes].sort((left, right) =>
    depth[left.type] - depth[right.type] || left.id.localeCompare(right.id))
  if (mode === 'cose') {
    const radius = Math.max(260, Math.sqrt(ordered.length) * 34)
    const golden = Math.PI * (3 - Math.sqrt(5))
    ordered.forEach((node, index) => {
      const y = 1 - (index / Math.max(1, ordered.length - 1)) * 2
      const ring = Math.sqrt(Math.max(0, 1 - y * y))
      const angle = golden * index
      positions.set(node.id, {
        x: Math.cos(angle) * ring * radius,
        y: y * radius,
        z: Math.sin(angle) * ring * radius,
      })
    })
    return { positions, radius }
  }
  const groups = new Map<number, KnowledgeNode[]>()
  for (const node of ordered) groups.set(depth[node.type], [...(groups.get(depth[node.type]) ?? []), node])
  let extent = 240
  for (const [level, group] of groups) {
    group.forEach((node, index) => {
      const angle = (index / Math.max(1, group.length)) * Math.PI * 2
      if (mode === 'concentric') {
        const ring = 90 + level * 105 + Math.max(0, group.length - 8) * 4
        positions.set(node.id, {
          x: Math.cos(angle) * ring,
          y: (2 - level) * 72,
          z: Math.sin(angle) * ring,
        })
        extent = Math.max(extent, ring + 80)
        return
      }
      const columns = Math.ceil(Math.sqrt(group.length))
      const row = Math.floor(index / columns)
      const column = index % columns
      const spacing = 88
      const jitter = ((stableHash(node.id) % 1000) / 1000 - 0.5) * 52
      positions.set(node.id, {
        x: (column - (Math.min(columns, group.length) - 1) / 2) * spacing,
        y: (2 - level) * 145,
        z: (row - (Math.ceil(group.length / columns) - 1) / 2) * spacing + jitter,
      })
      extent = Math.max(extent, columns * spacing, Math.abs((2 - level) * 145) + 100)
    })
  }
  return { positions, radius: extent }
}
