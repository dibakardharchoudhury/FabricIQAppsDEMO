import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet'
import type { Facility } from '../services/fabric'

export function FacilityMap({ facility }: { facility: Facility }) {
  const latitude = Number(facility.lat)
  const longitude = Number(facility.lon)
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    return <div className="map-empty">Facility coordinates are not available in STID.</div>
  }
  return <MapContainer className="facility-map" center={[latitude, longitude]} zoom={7} minZoom={3} maxZoom={14} scrollWheelZoom>
    <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    <CircleMarker center={[latitude, longitude]} radius={11} pathOptions={{ color: '#0f766e', fillColor: '#14b8a6', fillOpacity: 0.9, weight: 3 }}>
      <Popup><strong>{facility.facility_name}</strong><br />{facility.facility_id}<br />{latitude.toFixed(2)}, {longitude.toFixed(2)}</Popup>
    </CircleMarker>
  </MapContainer>
}