import { useEffect } from 'react'
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from 'react-leaflet'
import { LatLngBounds } from 'leaflet'
import type { Facility } from '../services/fabric'

type Located = Facility & { latitude: number; longitude: number }

function locate(facilities: Facility[]): Located[] {
  return facilities
    .map(facility => ({ ...facility, latitude: Number(facility.lat), longitude: Number(facility.lon) }))
    .filter(facility => Number.isFinite(facility.latitude) && Number.isFinite(facility.longitude))
}

function FitBounds({ points }: { points: Located[] }) {
  const map = useMap()
  useEffect(() => {
    if (!points.length) return
    if (points.length === 1) {
      map.setView([points[0].latitude, points[0].longitude], 7)
      return
    }
    const bounds = new LatLngBounds(points.map(point => [point.latitude, point.longitude] as [number, number]))
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 8 })
  }, [map, points])
  return null
}

export function FacilityMap({ facilities, selectedId, onSelect }: {
  facilities: Facility[]
  selectedId?: string
  onSelect?: (facilityId: string) => void
}) {
  const points = locate(facilities)
  if (!points.length) {
    return <div className="map-empty">Facility coordinates are not available in STID.</div>
  }
  const center: [number, number] = [points[0].latitude, points[0].longitude]
  return <MapContainer className="facility-map" center={center} zoom={6} minZoom={3} maxZoom={14} scrollWheelZoom>
    <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    <FitBounds points={points} />
    {points.map(point => {
      const active = point.facility_id === selectedId
      return <CircleMarker
        key={point.facility_id}
        center={[point.latitude, point.longitude]}
        radius={active ? 13 : 9}
        pathOptions={{
          color: active ? '#0f766e' : '#475569',
          fillColor: active ? '#14b8a6' : '#94a3b8',
          fillOpacity: active ? 0.95 : 0.75,
          weight: active ? 4 : 2,
        }}
        eventHandlers={onSelect ? { click: () => onSelect(point.facility_id) } : undefined}
      >
        <Popup>
          <strong>{point.facility_name}</strong><br />{point.facility_id}<br />
          {point.latitude.toFixed(2)}, {point.longitude.toFixed(2)}
        </Popup>
      </CircleMarker>
    })}
  </MapContainer>
}