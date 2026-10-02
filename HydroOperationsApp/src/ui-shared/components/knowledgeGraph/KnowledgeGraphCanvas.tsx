import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from 'cytoscape'
import { useEffect, useEffectEvent, useRef } from 'react'
import type { KnowledgeEdge, KnowledgeNode } from '../../knowledgeGraphModel'
import {
  KNOWLEDGE_EDGE_COLOR,
  KNOWLEDGE_NODE_SIZE,
  KNOWLEDGE_STATUS_COLOR,
  KNOWLEDGE_TYPE_COLOR,
  telemetryFlowEdgeIds,
} from '../../knowledgeGraphVisuals'
import type { AppTheme } from '../../hooks/useTheme'

export type GraphLayout = 'breadthfirst' | 'cose' | 'concentric'

const iconPath: Record<KnowledgeNode['type'], string> = {
  facility: '<path d="M4 20V7l8-4 8 4v13M8 20v-5h8v5M8 9h.01M12 9h.01M16 9h.01M8 12h.01M12 12h.01M16 12h.01"/>',
  system: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="m8.3 10.8 7.4-3.6M8.3 13.2l7.4 3.6"/>',
  equipment: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="4"/>',
  instrument: '<path d="M5 19a8 8 0 1 1 14 0M12 12l4-3M8 19h8"/><circle cx="12" cy="12" r="1"/>',
  signal: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  ontology: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4.5 7.8 7.5 4.3 7.5-4.3M12 12v9"/>',
  model: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4.5 7.8 7.5 4.3 7.5-4.3M12 12v9"/>',
  'work-order': '<path d="M14.7 6.3a4 4 0 0 0-5 5L4 17v3h3l5.7-5.7a4 4 0 0 0 5-5l-2.4 2.4-3-3 2.4-2.4Z"/>',
  inspection: '<path d="M9 5H6a2 2 0 0 0-2 2v12h16V7a2 2 0 0 0-2-2h-3M9 3h6v4H9zM8 13l2.5 2.5L16 10"/>',
  notification: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
}

const nodeIcons = Object.fromEntries(Object.entries(iconPath).map(([type, path]) => [type,
  `data:image/svg+xml;utf8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`)}`,
])) as Record<KnowledgeNode['type'], string>

function applyGraphFocus(graph: Core, selectedId?: string, animate = false) {
  graph.elements().removeClass('focus-dimmed focus-neighbor focus-selected')
  graph.$(':selected').unselect()
  if (!selectedId) return
  const selected = graph.getElementById(selectedId)
  if (selected.empty()) return
  selected.select()
  graph.elements().addClass('focus-dimmed')
  selected.closedNeighborhood().removeClass('focus-dimmed').addClass('focus-neighbor')
  selected.removeClass('focus-neighbor').addClass('focus-selected')
  if (animate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    graph.animate({ center: { eles: selected } }, { duration: 360 })
  }
}

function layoutOptions(layout: GraphLayout): LayoutOptions {
  const animate = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (layout === 'breadthfirst') return { name: 'breadthfirst', directed: true, spacingFactor: 1.55, padding: 64, animate, animationDuration: 420 }
  if (layout === 'concentric') return { name: 'concentric', minNodeSpacing: 66, spacingFactor: 1.35, padding: 64, animate, animationDuration: 420 }
  return { name: 'cose', idealEdgeLength: 145, nodeRepulsion: () => 7800, padding: 64, animate, animationDuration: 620 }
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
            'background-blacken': -0.04, 'background-image': 'data(icon)', 'background-fit': 'none',
            'background-width': '44%', 'background-height': '44%',
            'border-color': 'data(ring)', 'border-width': 3, label: 'data(label)', color: color('--cp-text'),
            'font-family': 'Inter, Segoe UI, Aptos, sans-serif', 'font-size': 10.5, 'font-weight': 650,
            'text-valign': 'bottom', 'text-margin-y': 11, 'text-wrap': 'wrap', 'text-max-width': '118px',
            'text-background-color': color('--kg-label-bg'), 'text-background-opacity': 0.94, 'text-background-padding': '4px',
            'text-background-shape': 'roundrectangle', 'text-border-color': color('--kg-label-border'), 'text-border-width': 1,
            'underlay-color': 'data(ring)', 'underlay-opacity': 0.14, 'underlay-padding': 9,
            'transition-property': 'opacity, border-width, underlay-opacity, underlay-padding',
            'transition-duration': 220,
          },
        },
        { selector: 'node:selected, node.focus-selected', style: { 'border-width': 4, 'border-color': 'data(ring)', 'underlay-opacity': 0.32, 'underlay-padding': 18, 'z-index': 20 } },
        { selector: 'node:active', style: { 'overlay-color': 'data(ring)', 'overlay-opacity': 0.12, 'overlay-padding': 8 } },
        { selector: 'node.status-pulse', style: { 'underlay-opacity': 0.28, 'underlay-padding': 14 } },
        { selector: 'node.focus-neighbor', style: { 'underlay-opacity': 0.18, 'underlay-padding': 10 } },
        { selector: '.focus-dimmed', style: { opacity: 0.16, 'text-opacity': 0.12 } },
        {
          selector: 'edge',
          style: {
            width: 1.9, 'line-color': 'data(color)', 'target-arrow-color': 'data(color)', 'target-arrow-shape': 'triangle',
            'curve-style': 'bezier', 'control-point-step-size': 44, label: 'data(label)', color: color('--cp-text-muted'),
            'font-size': 7.5, 'font-weight': 650, opacity: 0.58, 'text-opacity': 0.15,
            'text-background-color': color('--kg-label-bg'), 'text-background-opacity': 0.94, 'text-background-padding': '3px',
            'text-background-shape': 'roundrectangle', 'text-border-color': color('--kg-label-border'), 'text-border-width': 1,
            'text-rotation': 'autorotate', 'arrow-scale': 0.9,
            'transition-property': 'opacity, width, text-opacity', 'transition-duration': 220,
          },
        },
        { selector: 'edge.focus-neighbor, edge:selected', style: { width: 2.8, opacity: 0.94, 'text-opacity': 1, 'z-index': 10 } },
        { selector: 'edge[type = "has-signal"]', style: { 'line-style': 'dashed', 'line-dash-pattern': [7, 5] } },
        {
          selector: 'edge[flow = 1]',
          style: {
            width: 3.2, opacity: 0.92, 'line-color': 'data(flowColor)', 'target-arrow-color': 'data(flowColor)',
            'line-style': 'dashed', 'line-dash-pattern': [10, 12], 'arrow-scale': 1.05, 'z-index': 8,
          },
        },
      ],
    })
    graph.on('tap', 'node', event => handleNodeSelect(event.target.id()))
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let animationFrame: number | undefined
    let visible = true
    let previousFrame = 0
    let offset = 0
    const animate = (time: number) => {
      if (visible && time - previousFrame >= 34) {
        previousFrame = time
        offset = (offset + 1.35) % 44
        graph.edges('[flow = 1]').style('line-dash-offset', -offset)
        graph.nodes('[status = "crit"]').toggleClass('status-pulse', Math.floor(time / 900) % 2 === 0)
      }
      animationFrame = window.requestAnimationFrame(animate)
    }
    const observer = typeof IntersectionObserver === 'undefined' ? undefined : new IntersectionObserver(entries => {
      visible = entries[0]?.isIntersecting ?? true
    })
    observer?.observe(containerRef.current)
    if (!reducedMotion) animationFrame = window.requestAnimationFrame(animate)
    controllerRef.current = graph
    return () => {
      observer?.disconnect()
      if (animationFrame !== undefined) window.cancelAnimationFrame(animationFrame)
      graph.nodes().forEach(node => { positionCache.set(node.id(), node.position()) })
      controllerRef.current = null
      initializedRef.current = false
      graph.destroy()
    }
  }, [controllerRef, theme])

  useEffect(() => {
    const graph = controllerRef.current
    if (!graph) return
    const flowingEdges = telemetryFlowEdgeIds(nodes, edges)
    const nodesById = new Map(nodes.map(node => [node.id, node]))
    const elements: ElementDefinition[] = [
      ...nodes.map(node => ({
        data: {
          ...node,
          color: KNOWLEDGE_TYPE_COLOR[node.type],
          size: KNOWLEDGE_NODE_SIZE[node.type],
          ring: KNOWLEDGE_STATUS_COLOR[node.status],
          icon: nodeIcons[node.type],
        },
      })),
      ...edges.map(item => ({
        data: {
          ...item,
          color: KNOWLEDGE_EDGE_COLOR[item.type],
          flow: flowingEdges.has(item.id) ? 1 : 0,
          flowColor: KNOWLEDGE_STATUS_COLOR[nodesById.get(item.source)?.status ?? 'nodata'],
        },
      })),
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
    applyGraphFocus(graph, selectedId)
    if (!wasInitialized && graph.nodes().nonempty()) {
      initializedRef.current = true
      previousLayoutRef.current = layout
      graph.layout(layoutOptions(layout)).run()
    }
  }, [controllerRef, edges, layout, nodes, selectedId])

  useEffect(() => {
    const graph = controllerRef.current
    if (!graph || !initializedRef.current || previousLayoutRef.current === layout) return
    previousLayoutRef.current = layout
    graph.layout(layoutOptions(layout)).run()
  }, [controllerRef, layout])

  useEffect(() => {
    const graph = controllerRef.current
    if (!graph) return
    applyGraphFocus(graph, selectedId, true)
  }, [controllerRef, selectedId])

  return <div ref={containerRef} className="kg-canvas" role="application" aria-label="Interactive operational knowledge graph" />
}