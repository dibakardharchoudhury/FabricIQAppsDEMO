import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { ImportedTerrain } from '../terrainModel'

export function TerrainCanvas({ terrain, onError }: { terrain: ImportedTerrain; onError: (message: string) => void }) {
  const host = useRef<HTMLDivElement>(null)
  const actions = useRef<{ fit: () => void; wireframe: (enabled: boolean) => void }>({ fit: () => {}, wireframe: () => {} })

  useEffect(() => {
    const element = host.current
    if (!element) return
    let renderer: THREE.WebGLRenderer | undefined
    let controls: OrbitControls | undefined
    let observer: ResizeObserver | undefined
    const geometry = new THREE.BufferGeometry()
    const material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, side: THREE.DoubleSide })
    const contextLost = (event: Event) => { event.preventDefault(); onError('The 3D graphics context was lost. Re-import the terrain to retry.') }
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true })
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
      renderer.domElement.tabIndex = 0
      renderer.domElement.setAttribute('aria-label', `${terrain.manifest.catchmentName} terrain. Drag to orbit, scroll to zoom, right-drag or arrow keys to pan.`)
      renderer.domElement.addEventListener('webglcontextlost', contextLost)
      element.append(renderer.domElement)
      const scene = new THREE.Scene()
      scene.background = new THREE.Color('#102435')
      geometry.setAttribute('position', new THREE.BufferAttribute(terrain.positions, 3))
      geometry.setIndex(new THREE.BufferAttribute(terrain.indices, 1))
      geometry.computeVertexNormals()
      geometry.computeBoundingSphere()
      const colours = new Float32Array(terrain.positions.length)
      const heightRange = terrain.bounds.max[2] - terrain.bounds.min[2]
      const colour = new THREE.Color()
      for (let i = 0; i < terrain.positions.length; i += 3) {
        const height = heightRange ? terrain.positions[i + 1] / heightRange : 0.5
        colour.setHSL(0.35 - height * 0.25, 0.3, 0.25 + height * 0.45)
        colours[i] = colour.r
        colours[i + 1] = colour.g
        colours[i + 2] = colour.b
      }
      geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3))
      scene.add(new THREE.Mesh(geometry, material))
      scene.add(new THREE.HemisphereLight(0xffffff, 0x607080, 1.8))
      const sun = new THREE.DirectionalLight(0xffffff, 1.5)
      sun.position.set(-1, 3, 2)
      scene.add(sun)
      const radius = Math.max(geometry.boundingSphere?.radius ?? 1, 0.01)
      const center = geometry.boundingSphere?.center ?? new THREE.Vector3()
      const camera = new THREE.PerspectiveCamera(45, 1, radius / 10_000, radius * 100)
      controls = new OrbitControls(camera, renderer.domElement)
      controls.target.copy(center)
      controls.minDistance = radius * 0.01
      controls.maxDistance = radius * 20
      controls.listenToKeyEvents(renderer.domElement)
      const render = () => {
        try { renderer?.render(scene, camera) }
        catch (reason) { console.error('Terrain render failed.', reason); onError('The terrain could not be rendered. Try a smaller mesh or a WebGL-capable browser.') }
      }
      const fit = () => {
        const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) / Math.min(camera.aspect, 1) * 1.15
        camera.position.copy(center).add(new THREE.Vector3(0, 0.85, 1).normalize().multiplyScalar(distance))
        controls?.target.copy(center)
        controls?.update()
        render()
      }
      actions.current = { fit, wireframe: enabled => { material.wireframe = enabled; render() } }
      controls.addEventListener('change', render)
      observer = new ResizeObserver(() => {
        const width = Math.max(element.clientWidth, 1)
        const height = Math.max(element.clientHeight, 1)
        renderer?.setSize(width, height)
        camera.aspect = width / height
        camera.updateProjectionMatrix()
        fit()
      })
      observer.observe(element)
    } catch (reason) {
      console.error('Terrain viewer initialization failed.', reason)
      onError('The 3D viewer could not start. Use a WebGL-capable browser and re-import the terrain.')
    }
    return () => {
      observer?.disconnect()
      controls?.dispose()
      geometry.dispose()
      material.dispose()
      renderer?.domElement.removeEventListener('webglcontextlost', contextLost)
      renderer?.dispose()
      renderer?.forceContextLoss()
      renderer?.domElement.remove()
      actions.current = { fit: () => {}, wireframe: () => {} }
    }
  }, [terrain, onError])

  return <section className="terrain-viewer" aria-label="Interactive terrain viewer">
    <div className="terrain-toolbar">
      <button type="button" onClick={() => actions.current.fit()}>Reset view</button>
      <label><input type="checkbox" onChange={event => actions.current.wireframe(event.target.checked)} />Wireframe</label>
      <span>North is away from the initial camera · vertical scale 1:1</span>
    </div>
    <div className="terrain-stage" ref={host} />
    <p className="terrain-controls">Drag to orbit · scroll to zoom · right-drag to pan. Focus the canvas and use arrow keys to pan. Colours show relative elevation, not land cover.</p>
  </section>
}
