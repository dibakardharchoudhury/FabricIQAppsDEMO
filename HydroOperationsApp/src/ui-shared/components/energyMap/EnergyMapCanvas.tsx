import { useCallback, useEffect, useRef, useState } from 'react'
import { Map, NavigationControl, ScaleControl, setWorkerUrl, type GeoJSONSource } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { asFeatureCollection, type EnergyFeature, type MapViewport } from '../../energyMapModel'

setWorkerUrl(workerUrl)

export function EnergyMapCanvas({ features, onViewport, onSelect }: {
  features: EnergyFeature[]
  onViewport: (viewport: MapViewport) => void
  onSelect: (feature: EnergyFeature) => void
}) {
  const mapRef = useRef<Map | null>(null)
  const featureRef = useRef(features)
  const [mapError, setMapError] = useState<string>()

  useEffect(() => {
    featureRef.current = features
    const source = mapRef.current?.getSource<GeoJSONSource>('energy-features')
    source?.setData(asFeatureCollection(features))
  }, [features])

  const initializeMap = useCallback((container: HTMLDivElement | null) => {
    if (!container) return
    let map: Map
    try {
      map = new Map({
        container,
        center: [12.8, 64], zoom: 4, minZoom: 3, maxZoom: 17,
        maxBounds: [[-15, 45], [40, 80]], renderWorldCopies: false,
        style: {
          version: 8,
          sources: {
            basemap: {
              type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
              tileSize: 256, attribution: '&copy; OpenStreetMap contributors',
              maxzoom: 19,
            },
          },
          layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
        },
      })
    } catch (error) {
      console.error('Energy map could not initialize.', error)
      setMapError('The map requires WebGL support. The source status and feature list remain available.')
      return
    }
    mapRef.current = map
    const updateViewport = () => {
      const bounds = map.getBounds()
      onViewport({
        west: Math.max(-180, bounds.getWest()), south: Math.max(-90, bounds.getSouth()),
        east: Math.min(180, bounds.getEast()), north: Math.min(90, bounds.getNorth()), zoom: map.getZoom(),
      })
    }
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left')
    map.on('load', () => {
      map.addSource('energy-features', { type: 'geojson', data: asFeatureCollection(featureRef.current) })
      map.addLayer({
        id: 'energy-lines', type: 'line', source: 'energy-features',
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['==', ['get', 'layer_id'], 'power-flows'], 4, 2],
          'line-opacity': 0.85,
        },
      })
      map.addLayer({
        id: 'energy-points', type: 'circle', source: 'energy-features',
        filter: ['==', ['geometry-type'], 'Point'],
        paint: {
          'circle-color': ['get', 'color'], 'circle-radius': ['get', 'radius'],
          'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff', 'circle-opacity': 0.88,
        },
      })
      updateViewport()
    })
    map.on('moveend', updateViewport)
    map.on('click', ['energy-lines', 'energy-points'], event => {
      const hit = event.features?.[0]?.properties
      const feature = featureRef.current.find(item => item.id === hit?.feature_id && item.layerId === hit?.layer_id)
      if (feature) onSelect(feature)
    })
    map.on('mouseenter', ['energy-lines', 'energy-points'], () => { map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', ['energy-lines', 'energy-points'], () => { map.getCanvas().style.cursor = '' })
    map.on('error', event => {
      console.error('Energy map rendering error.', event.error)
      setMapError('Some map resources could not load. Check network access; imported data remains available in the feature list.')
    })
    const observer = new ResizeObserver(() => map.resize())
    observer.observe(container)
    return () => {
      observer.disconnect()
      map.remove()
      mapRef.current = null
    }
  }, [onSelect, onViewport])

  return <div className="energy-map-canvas-wrap">
    <div ref={initializeMap} className="energy-map-canvas" role="region" aria-label="Norwegian energy infrastructure map" />
    {mapError && <div className="energy-map-canvas-error" role="alert">{mapError}</div>}
  </div>
}
