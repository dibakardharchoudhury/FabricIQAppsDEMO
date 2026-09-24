import { useCallback, useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, NavigationControl, ScaleControl, setWorkerUrl, type FilterSpecification, type GeoJSONSource } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import {
  asFeatureCollection, MAP_VISIBLE_LAYERS, renderLayerSignature,
  type EnergyFeature, type EnergyLayerId, type MapViewport, type ReservoirAreaSelection,
} from '../../energyMapModel'

setWorkerUrl(workerUrl)

export function EnergyMapCanvas({ features, areas, capacityMaximum, showAreas, areaSelection, onViewport, onSelect }: {
  features: EnergyFeature[]
  areas: EnergyFeature[]
  capacityMaximum?: number | null
  showAreas: boolean
  areaSelection: ReservoirAreaSelection
  onViewport: (viewport: MapViewport) => void
  onSelect: (feature: EnergyFeature) => void
}) {
  const dataRef = useRef({ features, areas, capacityMaximum, showAreas, areaSelection })
  const callbacks = useRef({ onViewport, onSelect })
  const updateSources = useRef<(() => void) | null>(null)
  const camera = useRef<{ center: [number, number]; zoom: number }>({ center: [12.8, 64], zoom: 4 })
  const [mapError, setMapError] = useState<string>()
  const [generation, setGeneration] = useState(0)

  useEffect(() => { callbacks.current = { onViewport, onSelect } }, [onViewport, onSelect])
  useEffect(() => {
    dataRef.current = { features, areas, capacityMaximum, showAreas, areaSelection }
    updateSources.current?.()
  }, [features, areas, capacityMaximum, showAreas, areaSelection])

  const initializeMap = useCallback((container: HTMLDivElement | null) => {
    if (!container) return
    let map: MapLibreMap
    let alive = true
    let styleReady = false
    let visible = true
    let resizeFrame = 0
    let previousViewport = ''
    const signatures = new Map<EnergyLayerId, string>()
    const interactiveLayers: string[] = ['reservoir-price-fill', 'reservoir-country-fill']
    try {
      map = new MapLibreMap({
        container, ...camera.current, minZoom: 3, maxZoom: 17,
        renderWorldCopies: false,
        // Keep the Fabric iframe's framebuffer allocation bounded on high-DPI displays.
        pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5), maxCanvasSize: [2048, 2048],
        canvasContextAttributes: { antialias: false, preserveDrawingBuffer: false },
        style: {
          version: 8,
          sources: {
            basemap: {
              type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
              tileSize: 256, attribution: '&copy; OpenStreetMap contributors', maxzoom: 19,
            },
          },
          layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
        },
      })
    } catch (error) {
      console.error('Energy map could not initialize.', error)
      setMapError('The map renderer could not start. Check WebGL support or retry the renderer.')
      return
    }
    const applyData = () => {
      if (!alive || !styleReady || !visible) return
      const data = dataRef.current
      for (const layer of MAP_VISIBLE_LAYERS) {
        const layerFeatures = layer.id === 'reservoirs' ? data.areas : data.features.filter(feature => feature.layerId === layer.id)
        const maximum = layer.id === 'hydro-plants' ? data.capacityMaximum : undefined
        const signature = renderLayerSignature(layerFeatures, maximum)
        if (signatures.get(layer.id) === signature) continue
        const source = map.getSource<GeoJSONSource>(`energy-${layer.id}`)
        if (!source) continue
        signatures.set(layer.id, signature)
        void source.setData(asFeatureCollection(layerFeatures, maximum)).catch((reason: unknown) => {
          if (!alive) return
          signatures.delete(layer.id)
          console.error(`Map layer ${layer.id} could not update.`, reason)
          setMapError('A map overlay could not update. The basemap is retained; retry the renderer if necessary.')
        })
      }
      for (const id of ['reservoir-country-fill', 'reservoir-price-fill', 'reservoir-borders']) {
        if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', data.showAreas ? 'visible' : 'none')
      }
      const areaFilter: FilterSpecification = data.areaSelection === null
        ? ['has', 'area_code'] : ['in', ['get', 'area_code'], ['literal', data.areaSelection]]
      if (map.getLayer('reservoir-country-fill')) map.setFilter('reservoir-country-fill', ['all', ['==', ['get', 'area_kind'], 'country'], areaFilter])
      if (map.getLayer('reservoir-price-fill')) map.setFilter('reservoir-price-fill', ['all', ['==', ['get', 'area_kind'], 'price_area'], areaFilter])
      if (map.getLayer('reservoir-borders')) map.setFilter('reservoir-borders', areaFilter)
    }
    updateSources.current = applyData
    const updateViewport = () => {
      if (!alive || container.clientWidth <= 0 || container.clientHeight <= 0) return
      const bounds = map.getBounds()
      const view = {
        west: Math.max(-180, bounds.getWest()), south: Math.max(-90, bounds.getSouth()),
        east: Math.min(180, bounds.getEast()), north: Math.min(90, bounds.getNorth()), zoom: map.getZoom(),
      }
      camera.current = { center: [map.getCenter().lng, map.getCenter().lat], zoom: view.zoom }
      const key = [view.west, view.south, view.east, view.north, view.zoom].map(value => value.toFixed(6)).join(':')
      if (key !== previousViewport) {
        previousViewport = key
        callbacks.current.onViewport(view)
      }
    }
    const resize = () => {
      cancelAnimationFrame(resizeFrame)
      resizeFrame = requestAnimationFrame(() => {
        if (!alive || !visible || container.clientWidth <= 0 || container.clientHeight <= 0) return
        map.resize()
        applyData()
        map.triggerRepaint()
      })
    }
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left')
    map.on('style.load', () => {
      for (const layer of MAP_VISIBLE_LAYERS) {
        if (!map.getSource(`energy-${layer.id}`)) {
          map.addSource(`energy-${layer.id}`, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
        }
      }
      if (!map.getLayer('reservoir-country-fill')) {
        map.addLayer({
          id: 'reservoir-country-fill', type: 'fill', source: 'energy-reservoirs',
          filter: ['==', ['get', 'area_kind'], 'country'],
          paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.15 },
        })
        map.addLayer({
          id: 'reservoir-price-fill', type: 'fill', source: 'energy-reservoirs',
          filter: ['==', ['get', 'area_kind'], 'price_area'],
          paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.32 },
        })
        map.addLayer({
          id: 'reservoir-borders', type: 'line', source: 'energy-reservoirs',
          paint: { 'line-color': '#475569', 'line-width': 1.1, 'line-opacity': 0.7 },
        })
      }
      for (const layer of MAP_VISIBLE_LAYERS.filter(item => item.id !== 'reservoirs')) {
        for (const kind of ['line', 'circle'] as const) {
          const id = `energy-${layer.id}-${kind}`
          if (!interactiveLayers.includes(id)) interactiveLayers.unshift(id)
          if (map.getLayer(id)) continue
          map.addLayer(kind === 'line' ? {
            id, type: 'line', source: `energy-${layer.id}`, filter: ['==', ['geometry-type'], 'LineString'],
            paint: { 'line-color': ['get', 'color'], 'line-width': layer.id === 'power-flows' ? 4 : 2, 'line-opacity': 0.85 },
          } : {
            id, type: 'circle', source: `energy-${layer.id}`, filter: ['==', ['geometry-type'], 'Point'],
            paint: { 'circle-color': ['get', 'color'], 'circle-radius': ['get', 'radius'], 'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff', 'circle-opacity': 0.88 },
          })
        }
      }
      styleReady = true
      signatures.clear()
      applyData()
      updateViewport()
    })
    map.on('moveend', updateViewport)
    map.on('click', event => {
      if (!styleReady) return
      const hits = map.queryRenderedFeatures(event.point, { layers: interactiveLayers.filter(id => map.getLayer(id)) })
      const hit = hits.find(feature => feature.properties.layer_id !== 'reservoirs') ?? hits[0]
      const data = dataRef.current
      const feature = [...data.features, ...data.areas].find(item =>
        item.id === hit?.properties.feature_id && item.layerId === hit?.properties.layer_id)
      if (feature) callbacks.current.onSelect(feature)
    })
    map.on('mousemove', event => {
      if (!styleReady) return
      const hits = map.queryRenderedFeatures(event.point, { layers: interactiveLayers.filter(id => map.getLayer(id)) })
      map.getCanvas().style.cursor = hits.length ? 'pointer' : ''
    })
    map.on('webglcontextlost', () => {
      styleReady = false
      signatures.clear()
      console.error('Energy map WebGL context was lost.')
      setMapError('Graphics context lost. Restoring the map; you can also retry the renderer.')
    })
    map.on('webglcontextrestored', () => { setMapError(undefined); resize() })
    map.on('error', event => {
      console.error('Energy map rendering error.', event.error)
      setMapError('Some map resources could not load. Check connectivity or retry the renderer.')
    })
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    const intersection = new IntersectionObserver(entries => {
      visible = entries.some(entry => entry.isIntersecting)
      if (visible) resize()
    })
    intersection.observe(container)
    const visibility = () => { if (document.visibilityState === 'visible') resize() }
    document.addEventListener('visibilitychange', visibility)
    return () => {
      alive = false
      updateSources.current = null
      cancelAnimationFrame(resizeFrame)
      observer.disconnect()
      intersection.disconnect()
      document.removeEventListener('visibilitychange', visibility)
      map.remove()
    }
  }, [])

  return <div className="energy-map-canvas-wrap">
    <div key={generation} ref={initializeMap} className="energy-map-canvas" role="region" aria-label="Norwegian energy infrastructure map" />
    {mapError && <div className="energy-map-canvas-error" role="alert">
      <span>{mapError}</span><button type="button" onClick={() => { setMapError(undefined); setGeneration(value => value + 1) }}>Retry renderer</button>
    </div>}
  </div>
}
