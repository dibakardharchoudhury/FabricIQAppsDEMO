import { useEffect, useMemo } from 'react'
import { geoJSON, LatLngBounds } from 'leaflet'
import { CircleMarker, GeoJSON, MapContainer, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { GeoJsonObject } from 'geojson'
import type { WeatherArea, WeatherLocation } from '../../../services/fabric'

export type WeatherSelection =
  | { kind: 'location'; id: string }
  | { kind: 'area'; id: string }

function FitWeatherBounds({ locations, areas }: { locations: WeatherLocation[]; areas: WeatherArea[] }) {
  const map = useMap()
  useEffect(() => {
    const bounds = new LatLngBounds([])
    for (const location of locations) bounds.extend([Number(location.latitude), Number(location.longitude)])
    for (const area of areas) {
      try {
        const geometry = JSON.parse(area.geometry_geojson) as GeoJsonObject
        bounds.extend(geoJSON(geometry).getBounds())
      } catch { /* malformed areas are omitted below */ }
    }
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [36, 36], maxZoom: 8 })
  }, [areas, locations, map])
  return null
}

function ResizeMap() {
  const map = useMap()
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }))
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map])
  return null
}

export function WeatherMap({ locations, areas, selection, onSelect }: {
  locations: WeatherLocation[]
  areas: WeatherArea[]
  selection?: WeatherSelection
  onSelect: (selection: WeatherSelection) => void
}) {
  const validLocations = useMemo(
    () => locations.filter(location => Number.isFinite(Number(location.latitude)) && Number.isFinite(Number(location.longitude))),
    [locations],
  )
  const validAreas = useMemo(() => areas.flatMap(area => {
    try { return [{ area, geometry: JSON.parse(area.geometry_geojson) as GeoJsonObject }] }
    catch { return [] }
  }), [areas])

  if (!validLocations.length && !validAreas.length) return <div className="weather-map-empty">No weather locations or areas are available.</div>

  const center: [number, number] = validLocations.length
    ? [Number(validLocations[0].latitude), Number(validLocations[0].longitude)]
    : [56.7, -4.3]

  return <MapContainer className="weather-map" center={center} zoom={7} minZoom={3} maxZoom={14} scrollWheelZoom>
    <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    <ResizeMap />
    <FitWeatherBounds locations={validLocations} areas={areas} />
    {validAreas.map(({ area, geometry }) => {
      const active = selection?.kind === 'area' && selection.id === area.area_id
      return <GeoJSON
        key={area.area_id}
        data={geometry}
        style={{ color: active ? '#0f766e' : '#2563eb', fillColor: active ? '#14b8a6' : '#60a5fa', fillOpacity: active ? 0.28 : 0.14, weight: active ? 4 : 2 }}
        eventHandlers={{ click: () => onSelect({ kind: 'area', id: area.area_id }) }}
      ><Tooltip sticky><strong>{area.area_name}</strong><br />Weather area</Tooltip></GeoJSON>
    })}
    {validLocations.map(location => {
      const active = selection?.kind === 'location' && selection.id === location.location_id
      return <CircleMarker
        key={location.location_id}
        center={[Number(location.latitude), Number(location.longitude)]}
        radius={active ? 11 : 8}
        pathOptions={{ color: active ? '#0f766e' : '#1f2937', fillColor: active ? '#14b8a6' : '#f8fafc', fillOpacity: 1, weight: active ? 4 : 2 }}
        eventHandlers={{ click: () => onSelect({ kind: 'location', id: location.location_id }) }}
      ><Tooltip direction="top"><strong>{location.location_name}</strong><br />Weather station</Tooltip></CircleMarker>
    })}
  </MapContainer>
}