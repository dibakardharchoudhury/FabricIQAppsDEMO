import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from 'cytoscape'
import { useEffect, useEffectEvent, useRef } from 'react'
import type { KnowledgeEdge, KnowledgeNode } from '../../knowledgeGraphModel'
import type { AppTheme } from '../../hooks/useTheme'

export type GraphLayout = 'breadthfirst' | 'cose' | 'concentric'

const typeColor: Record<KnowledgeNode['type'], string> = {
  facility: '#0e7490',
  system: '#2563eb',
  equipment: '#b11f4b',
  instrument: '#16a34a',
  signal: '#0891b2',
  ontology: '#475569',
  model: '#7c3aed',
  'work-order': '#d97706',
  inspection: '#64748b',
  notification: '#dc2626',
}

const typeSize: Record<KnowledgeNode['type'], number> = {
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

const statusColor: Record<KnowledgeNode['status'], string> = {
  ok: '#16a34a',
  warn: '#f59e0b',
  crit: '#dc2626',
  nodata: '#919191',
}

const edgeColor: Record<KnowledgeEdge['type'], string> = {
  contains: '#64748b',
  'has-instrument': '#16a34a',
  'has-signal': '#0891b2',
  'has-model': '#7c3aed',
  affects: '#d97706',
  documents: '#64748b',
  reports: '#dc2626',
}

function layoutOptions(layout: GraphLayout): LayoutOptions {
  if (layout === 'breadthfirst') return { name: 'breadthfirst', directed: true, spacingFactor: 1.4, padding: 48, animate: true, animationDuration: 350 }
  if (layout === 'concentric') return { name: 'concentric', minNodeSpacing: 55, spacingFactor: 1.25, padding: 48, animate: true, animationDuration: 350 }
  return { name: 'cose', idealEdgeLength: 120, nodeRepulsion: () => 6500, padding: 48, animate: true, animationDuration: 500 }
}

export function KnowledgeGraphCanvas({ nodes, edges, selectedId, layout, theme, onSelect, controllerRef }: {
  nodes: KnowledgeNode[]
  edges: KnowledgeEdge[]
  selectedId?: string
  layout: GraphLayout
  theme: AppTheme
  onSelect: (nodeId: string) => void
  controllerRef: React.MutableRefObject<Core | null>
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const positionCacheRef = useRef(new Map<string, { x: number; y: number }>())
  const initializedRef = useRef(false)
  const previousLayoutRef = useRef(layout)
  const handleNodeSelect = useEffectEvent(onSelect)

  useEffect(() => {
    if (!containerRef.current) return
    const positionCache = positionCacheRef.current
    const css = getComputedStyle(document.documentElement)
    const color = (token: string) => css.getPropertyValue(token).trim()
    const graph = cytoscape({
      container: containerRef.current,
      elements: [],
      minZoom: 0.25,
      maxZoom: 2.5,
      style: [
        {
          selector: 'node',
          style: {
            shape: 'ellipse', width: 'data(size)', height: 'data(size)', 'background-color': 'data(color)',
            'border-color': 'data(ring)', 'border-width': 4, label: 'data(label)', color: color('--cp-text'),
            'font-family': 'Segoe UI, Aptos, sans-serif', 'font-size': 11, 'font-weight': 600,
            'text-valign': 'bottom', 'text-margin-y': 8, 'text-wrap': 'wrap', 'text-max-width': '112px',
            'text-background-color': color('--cp-surface'), 'text-background-opacity': 0.9, 'text-background-padding': '2px',
          },
        },
        { selector: 'node:selected', style: { 'border-width': 7, 'border-color': '#b11f4b', 'overlay-color': '#b11f4b', 'overlay-opacity': 0.1, 'overlay-padding': 10 } },
        { selector: 'node:active', style: { 'overlay-color': '#b11f4b', 'overlay-opacity': 0.12, 'overlay-padding': 8 } },
        {
          selector: 'edge',
          style: {
            width: 1.6, 'line-color': 'data(color)', 'target-arrow-color': 'data(color)', 'target-arrow-shape': 'triangle',
            'curve-style': 'bezier', label: 'data(label)', color: color('--cp-text-muted'), 'font-size': 8, 'font-weight': 600,
            'text-background-color': color('--cp-bg-elevated'), 'text-background-opacity': 0.92, 'text-background-padding': '2px',
            'text-rotation': 'autorotate', 'arrow-scale': 0.75,
          },
        },
        { selector: 'edge:selected', style: { width: 3, 'line-color': '#b11f4b', 'target-arrow-color': '#b11f4b' } },
      ],
    })
    graph.on('tap', 'node', event => handleNodeSelect(event.target.id()))
    controllerRef.current = graph
    return () => {
      graph.nodes().forEach(node => { positionCache.set(node.id(), node.position()) })
      controllerRef.current = null
      initializedRef.current = false
      graph.destroy()
    }
  }, [controllerRef, theme])

  useEffect(() => {
    const graph = controllerRef.current
    if (!graph) return
    const elements: ElementDefinition[] = [
      ...nodes.map(node => ({ data: { ...node, color: typeColor[node.type], size: typeSize[node.type], ring: statusColor[node.status] } })),
      ...edges.map(item => ({ data: { ...item, color: edgeColor[item.type] } })),
    ]
    const nextIds = new Set(elements.map(element => String(element.data.id)))
    const wasInitialized = initializedRef.current
    graph.batch(() => {
      graph.nodes().forEach(node => { positionCacheRef.current.set(node.id(), node.position()) })
      graph.elements().filter(element => !nextIds.has(element.id())).remove()
      for (const element of elements) {
        const id = String(element.data.id)
        const current = graph.getElementById(id)
        if (current.nonempty()) {
          current.data(element.data)
          continue
        }
        const added = graph.add(element)
        const position = positionCacheRef.current.get(id)
        if (position && added.isNode()) added.position(position)
      }
    })
    if (!wasInitialized && graph.nodes().nonempty()) {
      initializedRef.current = true
      previousLayoutRef.current = layout
      graph.layout(layoutOptions(layout)).run()
    }
  }, [controllerRef, edges, layout, nodes])

  useEffect(() => {
    const graph = controllerRef.current
    if (!graph || !initializedRef.current || previousLayoutRef.current === layout) return
    previousLayoutRef.current = layout
    graph.layout(layoutOptions(layout)).run()
  }, [controllerRef, layout])

  useEffect(() => {
    const graph = controllerRef.current
    if (!graph) return
    graph.$(':selected').unselect()
    if (selectedId) graph.getElementById(selectedId).select()
  }, [controllerRef, selectedId])

  return <div ref={containerRef} className="kg-canvas" role="application" aria-label="Interactive operational knowledge graph" />
}