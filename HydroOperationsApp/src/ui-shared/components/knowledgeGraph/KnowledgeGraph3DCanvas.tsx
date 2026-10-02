import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { useEffect, useEffectEvent, useRef } from 'react'
import type { AppTheme } from '../../hooks/useTheme'
import type { KnowledgeEdge, KnowledgeNode } from '../../knowledgeGraphModel'
import {
  buildKnowledgeGraph3DLayout,
  KNOWLEDGE_EDGE_COLOR,
  KNOWLEDGE_NODE_SIZE,
  KNOWLEDGE_STATUS_COLOR,
  KNOWLEDGE_TYPE_COLOR,
  telemetryFlowEdgeIds,
  type KnowledgeGraphPoint3D,
} from '../../knowledgeGraphVisuals'
import type { GraphLayout } from './KnowledgeGraphCanvas'

export type KnowledgeGraph3DController = {
  zoomBy: (factor: number) => void
  fit: () => void
  focus: (nodeId: string) => void
  resize: () => void
}

export function KnowledgeGraph3DCanvas({ nodes, edges, selectedId, layout, theme, onSelect, controllerRef }: {
  nodes: KnowledgeNode[]
  edges: KnowledgeEdge[]
  selectedId?: string
  layout: GraphLayout
  theme: AppTheme
  onSelect: (nodeId: string) => void
  controllerRef: React.MutableRefObject<KnowledgeGraph3DController | null>
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const positionsRef = useRef<Map<string, KnowledgeGraphPoint3D>>(new Map())
  const selectionRef = useRef<THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial> | null>(null)
  const handleSelect = useEffectEvent(onSelect)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: nodes.length < 1200, alpha: true, powerPreference: 'high-performance' })
    } catch (error) {
      const message = document.createElement('div')
      message.className = 'kg-3d-error'
      message.setAttribute('role', 'alert')
      message.textContent = error instanceof Error ? `3D rendering is unavailable: ${error.message}` : '3D rendering is unavailable in this browser.'
      container.appendChild(message)
      return () => message.remove()
    }
    const { positions, radius } = buildKnowledgeGraph3DLayout(nodes, layout)
    positionsRef.current = positions
    const scene = new THREE.Scene()
    scene.fog = new THREE.FogExp2(theme === 'dark' ? 0x07111f : 0xeef4fa, 0.00055)
    const camera = new THREE.PerspectiveCamera(46, 1, 1, Math.max(8000, radius * 8))
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    controls.dampingFactor = 0.075
    controls.minDistance = 70
    controls.maxDistance = Math.max(2400, radius * 4)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, nodes.length > 1000 ? 1.15 : 1.6))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    container.appendChild(renderer.domElement)

    const ambient = new THREE.AmbientLight(0xffffff, theme === 'dark' ? 1.15 : 1.65)
    const keyLight = new THREE.DirectionalLight(0x99e8ff, theme === 'dark' ? 2.2 : 1.5)
    keyLight.position.set(400, 600, 800)
    scene.add(ambient, keyLight)

    const sphere = new THREE.SphereGeometry(1, nodes.length > 1000 ? 8 : 14, nodes.length > 1000 ? 6 : 10)
    const nodeMaterial = new THREE.MeshStandardMaterial({ roughness: 0.28, metalness: 0.18 })
    const nodeMesh = new THREE.InstancedMesh(sphere, nodeMaterial, nodes.length)
    nodeMesh.instanceMatrix.setUsage(THREE.StaticDrawUsage)
    const haloMaterial = new THREE.MeshBasicMaterial({ transparent: true, opacity: theme === 'dark' ? 0.24 : 0.2, depthWrite: false })
    const haloMesh = new THREE.InstancedMesh(sphere, haloMaterial, nodes.length)
    const matrix = new THREE.Matrix4()
    nodes.forEach((node, index) => {
      const point = positions.get(node.id)!
      const scale = KNOWLEDGE_NODE_SIZE[node.type] / 5.8
      matrix.compose(
        new THREE.Vector3(point.x, point.y, point.z),
        new THREE.Quaternion(),
        new THREE.Vector3(scale, scale, scale),
      )
      nodeMesh.setMatrixAt(index, matrix)
      nodeMesh.setColorAt(index, new THREE.Color(KNOWLEDGE_TYPE_COLOR[node.type]))
      matrix.compose(
        new THREE.Vector3(point.x, point.y, point.z),
        new THREE.Quaternion(),
        new THREE.Vector3(scale * 1.38, scale * 1.38, scale * 1.38),
      )
      haloMesh.setMatrixAt(index, matrix)
      haloMesh.setColorAt(index, new THREE.Color(KNOWLEDGE_STATUS_COLOR[node.status]))
    })
    scene.add(haloMesh, nodeMesh)

    const validEdges = edges.filter(edge => positions.has(edge.source) && positions.has(edge.target))
    const linePositions = new Float32Array(validEdges.length * 6)
    const lineColors = new Float32Array(validEdges.length * 6)
    validEdges.forEach((edge, index) => {
      const source = positions.get(edge.source)!
      const target = positions.get(edge.target)!
      linePositions.set([source.x, source.y, source.z, target.x, target.y, target.z], index * 6)
      const color = new THREE.Color(KNOWLEDGE_EDGE_COLOR[edge.type])
      lineColors.set([color.r, color.g, color.b, color.r, color.g, color.b], index * 6)
    })
    const lineGeometry = new THREE.BufferGeometry()
    lineGeometry.setAttribute('position', new THREE.BufferAttribute(linePositions, 3))
    lineGeometry.setAttribute('color', new THREE.BufferAttribute(lineColors, 3))
    const lineMaterial = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: theme === 'dark' ? 0.58 : 0.5 })
    const lines = new THREE.LineSegments(lineGeometry, lineMaterial)
    scene.add(lines)

    const cone = new THREE.ConeGeometry(2.4, 8, 7)
    const arrowMaterial = new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.86 })
    const arrowMesh = new THREE.InstancedMesh(cone, arrowMaterial, validEdges.length)
    const up = new THREE.Vector3(0, 1, 0)
    validEdges.forEach((edge, index) => {
      const source = positions.get(edge.source)!
      const target = positions.get(edge.target)!
      const start = new THREE.Vector3(source.x, source.y, source.z)
      const end = new THREE.Vector3(target.x, target.y, target.z)
      const direction = end.clone().sub(start).normalize()
      const position = start.lerp(end, 0.82)
      matrix.compose(position, new THREE.Quaternion().setFromUnitVectors(up, direction), new THREE.Vector3(1, 1, 1))
      arrowMesh.setMatrixAt(index, matrix)
      arrowMesh.setColorAt(index, new THREE.Color(KNOWLEDGE_EDGE_COLOR[edge.type]))
    })
    scene.add(arrowMesh)

    const flowingIds = telemetryFlowEdgeIds(nodes, validEdges)
    const flowingEdges = validEdges.filter(edge => flowingIds.has(edge.id)).slice(0, 120)
    const particleCount = flowingEdges.length * 2
    const particlePositions = new Float32Array(particleCount * 3)
    const particlePhases = Array.from({ length: particleCount }, (_, index) => (index % 2) * 0.5 + (index / Math.max(1, particleCount)) * 0.35)
    const particleGeometry = new THREE.BufferGeometry()
    particleGeometry.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3))
    const particleMaterial = new THREE.PointsMaterial({
      color: 0x67e8f9,
      size: nodes.length > 1000 ? 3.8 : 5.2,
      transparent: true,
      opacity: 0.95,
      sizeAttenuation: true,
      depthWrite: false,
    })
    const particles = new THREE.Points(particleGeometry, particleMaterial)
    scene.add(particles)

    const selectionMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: 0.72, depthWrite: false })
    const selection = new THREE.Mesh(new THREE.SphereGeometry(1, 14, 10), selectionMaterial)
    selection.visible = false
    selectionRef.current = selection
    scene.add(selection)

    const render = () => renderer.render(scene, camera)
    const fit = () => {
      controls.target.set(0, 0, 0)
      camera.position.set(radius * 1.3, radius * 0.82, radius * 1.8)
      camera.lookAt(0, 0, 0)
      controls.update()
      render()
    }
    const resize = () => {
      const width = Math.max(1, container.clientWidth)
      const height = Math.max(1, container.clientHeight)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height, false)
      render()
    }
    const focus = (nodeId: string) => {
      const point = positions.get(nodeId)
      if (!point) return
      controls.target.set(point.x, point.y, point.z)
      const offset = camera.position.clone().sub(controls.target).normalize().multiplyScalar(Math.max(120, radius * 0.38))
      camera.position.set(point.x + offset.x, point.y + offset.y, point.z + offset.z)
      controls.update()
      render()
    }
    controllerRef.current = {
      zoomBy: factor => {
        const offset = camera.position.clone().sub(controls.target).multiplyScalar(1 / factor)
        camera.position.copy(controls.target).add(offset)
        controls.update()
        render()
      },
      fit,
      focus,
      resize,
    }
    fit()
    resize()

    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()
    let hovered = -1
    const pointerMove = (event: PointerEvent) => {
      const bounds = renderer.domElement.getBoundingClientRect()
      pointer.set(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1)
      raycaster.setFromCamera(pointer, camera)
      const hit = raycaster.intersectObject(nodeMesh, false)[0]
      hovered = hit?.instanceId ?? -1
      renderer.domElement.style.cursor = hovered >= 0 ? 'pointer' : 'grab'
      const tooltip = tooltipRef.current
      if (!tooltip) return
      if (hovered < 0) {
        tooltip.hidden = true
        return
      }
      const node = nodes[hovered]
      tooltip.hidden = false
      tooltip.style.transform = `translate(${event.clientX - bounds.left + 14}px, ${event.clientY - bounds.top + 14}px)`
      tooltip.textContent = `${node.label} · ${node.type.replace('-', ' ')}`
    }
    const click = () => {
      if (hovered >= 0) handleSelect(nodes[hovered].id)
    }
    renderer.domElement.addEventListener('pointermove', pointerMove)
    renderer.domElement.addEventListener('click', click)
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(container)
    let intersecting = true
    const intersectionObserver = typeof IntersectionObserver === 'undefined' ? undefined : new IntersectionObserver(entries => {
      intersecting = entries[0]?.isIntersecting ?? true
    })
    intersectionObserver?.observe(container)

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame = 0
    let previousTime = 0
    const animate = (time: number) => {
      frame = window.requestAnimationFrame(animate)
      if (!intersecting || document.hidden || time - previousTime < (nodes.length > 1000 ? 33 : 22)) return
      const delta = Math.min(50, time - previousTime)
      previousTime = time
      if (!reducedMotion && flowingEdges.length) {
        for (let index = 0; index < particleCount; index++) {
          particlePhases[index] = (particlePhases[index] + delta * 0.00022) % 1
          const edge = flowingEdges[Math.floor(index / 2)]
          const source = positions.get(edge.source)!
          const target = positions.get(edge.target)!
          const phase = particlePhases[index]
          particlePositions[index * 3] = source.x + (target.x - source.x) * phase
          particlePositions[index * 3 + 1] = source.y + (target.y - source.y) * phase
          particlePositions[index * 3 + 2] = source.z + (target.z - source.z) * phase
        }
        particleGeometry.attributes.position.needsUpdate = true
      }
      controls.update()
      render()
    }
    frame = window.requestAnimationFrame(animate)

    return () => {
      window.cancelAnimationFrame(frame)
      resizeObserver.disconnect()
      intersectionObserver?.disconnect()
      renderer.domElement.removeEventListener('pointermove', pointerMove)
      renderer.domElement.removeEventListener('click', click)
      controls.dispose()
      sphere.dispose()
      nodeMaterial.dispose()
      haloMaterial.dispose()
      lineGeometry.dispose()
      lineMaterial.dispose()
      cone.dispose()
      arrowMaterial.dispose()
      particleGeometry.dispose()
      particleMaterial.dispose()
      selection.geometry.dispose()
      selectionMaterial.dispose()
      renderer.dispose()
      renderer.domElement.remove()
      selectionRef.current = null
      controllerRef.current = null
    }
  }, [controllerRef, edges, layout, nodes, theme])

  useEffect(() => {
    const selection = selectionRef.current
    if (!selection) return
    const point = selectedId ? positionsRef.current.get(selectedId) : undefined
    selection.visible = Boolean(point)
    if (!point || !selectedId) return
    const node = nodes.find(item => item.id === selectedId)
    const scale = node ? KNOWLEDGE_NODE_SIZE[node.type] / 4.35 : 12
    selection.position.set(point.x, point.y, point.z)
    selection.scale.setScalar(scale)
  }, [nodes, selectedId])

  return <div ref={containerRef} className="kg-canvas kg-canvas-3d" role="application" aria-label="Interactive three-dimensional operational knowledge graph">
    <div ref={tooltipRef} className="kg-3d-tooltip" hidden />
    <div className="kg-3d-hint">Drag to orbit · Scroll to zoom · Select a node to inspect</div>
  </div>
}
