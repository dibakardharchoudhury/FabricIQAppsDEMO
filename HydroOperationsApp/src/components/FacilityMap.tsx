import { useEffect, useMemo } from 'react'
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { LatLngBounds } from 'leaflet'
import type { TwinStatus } from '../twin'
import { ageLabel, freshnessOf } from '../twin'

export type MapHealth = { ok: number; warn: number; crit: number; nodata: number }

// A facility enriched with live health rolled up from its assets' signal quality.
export type FacilityStat = {
  facility_id: string
  facility_name: string
  type?: string
  country?: string
  lat: number
  lon: number
  assetCount: number
  instrumentCount: number
  openOrders: number
  health: MapHealth
  worst: TwinStatus
}

// One asset within the selected facility, placed on a ring around it.
export type AssetPin = {
  equipment_id: string
  tag: string
  worst: TwinStatus
  health: MapHealth
  openOrders: number
  signals: { label: string; value?: number | string; unit?: string; quality?: string; status: TwinStatus; eventTime?: string }[]
}

const STATUS_COLOR: Record<TwinStatus, string> = { ok: '#16a34a', warn: '#f59e0b', crit: '#dc2626', nodata: '#94a3b8' }
const STATUS_LABEL: Record<TwinStatus, string> = { ok: 'Healthy', warn: 'Uncertain', crit: 'Bad / open order', nodata: 'No data' }
const valueText = (value?: number | string, unit?: string) =>
  value === undefined || value === null || value === '' ? '—' : `${value}${unit ? ` ${unit}` : ''}`

function FitBounds({ points }: { points: FacilityStat[] }) {
  const map = useMap()
  const key = points.map(p => p.facility_id).join(',')
  useEffect(() => {
    if (!points.length) return
    if (points.length === 1) { map.setView([points[0].lat, points[0].lon], 7); return }
    const bounds = new LatLngBounds(points.map(p => [p.lat, p.lon] as [number, number]))
    map.fitBounds(bounds, { padding: [44, 44], maxZoom: 8 })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refit only when the set of facilities changes
  }, [map, key])
  return null
}

export function FacilityMap({ facilities, assets, selectedId, selectedAssetId, onSelect, onSelectAsset, updatedAt }: {
  facilities: FacilityStat[]
  assets?: AssetPin[]
  selectedId?: string
  selectedAssetId?: string
  onSelect?: (facilityId: string) => void
  onSelectAsset?: (equipmentId: string) => void
  updatedAt?: number
}) {
  const points = useMemo(
    () => facilities.filter(f => Number.isFinite(f.lat) && Number.isFinite(f.lon)),
    [facilities],
  )

  // Spread the selected facility's assets on a small ring around its coordinate so operators can see
  // — and click through to — the individual turbines without those assets carrying their own GPS.
  const ring = useMemo(() => {
    const home = points.find(p => p.facility_id === selectedId)
    if (!home || !assets?.length) return []
    const R = 0.13
    const latRad = (home.lat * Math.PI) / 180
    return assets.map((pin, index) => {
      const theta = (index / assets.length) * Math.PI * 2 - Math.PI / 2
      const lat = home.lat + R * Math.sin(theta)
      const lon = home.lon + (R * Math.cos(theta)) / Math.max(0.2, Math.cos(latRad))
      return { pin, lat, lon, home: [home.lat, home.lon] as [number, number] }
    })
  }, [points, assets, selectedId])

  if (!points.length) {
    return <div className="map-empty">Facility coordinates are not available in STID.</div>
  }

  const selectedName = points.find(p => p.facility_id === selectedId)?.facility_name

  return (
    <div className="facility-map-wrap">
      <MapContainer className="facility-map" center={[points[0].lat, points[0].lon]} zoom={6} minZoom={3} maxZoom={14} scrollWheelZoom>
        <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <FitBounds points={points} />

        {ring.map(({ pin, lat, lon, home }) => (
          <Polyline key={`link-${pin.equipment_id}`} positions={[home, [lat, lon]]} pathOptions={{ color: '#0f766e', weight: 1, opacity: 0.35, dashArray: '3 5' }} />
        ))}

        {points.map(point => {
          const active = point.facility_id === selectedId
          return (
            <CircleMarker
              key={point.facility_id}
              center={[point.lat, point.lon]}
              radius={active ? 15 : 10}
              pathOptions={{
                color: active ? '#0f766e' : '#334155',
                fillColor: STATUS_COLOR[point.worst],
                fillOpacity: active ? 0.9 : 0.7,
                weight: active ? 4 : 2,
                className: `fac-marker ${point.worst}${active ? ' active' : ''}`,
              }}
              eventHandlers={onSelect ? { click: () => onSelect(point.facility_id) } : undefined}
            >
              <Tooltip direction="top" offset={[0, -6]}>
                <strong>{point.facility_name}</strong> · {STATUS_LABEL[point.worst]}
              </Tooltip>
              <Popup>
                <div className="map-pop">
                  <strong>{point.facility_name}</strong>
                  <small>{point.facility_id}{point.type ? ` · ${point.type}` : ''}{point.country ? ` · ${point.country}` : ''}</small>
                  <div className="map-pop-stats">
                    <span>{point.assetCount} assets</span>
                    <span>{point.instrumentCount} signals</span>
                    <span>{point.openOrders} open WO</span>
                  </div>
                  <div className="map-health">
                    <span className="mh ok"><i />{point.health.ok}</span>
                    <span className="mh warn"><i />{point.health.warn}</span>
                    <span className="mh crit"><i />{point.health.crit}</span>
                    <span className="mh nodata"><i />{point.health.nodata}</span>
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          )
        })}

        {ring.map(({ pin, lat, lon }) => {
          const active = pin.equipment_id === selectedAssetId
          return (
            <CircleMarker
              key={pin.equipment_id}
              center={[lat, lon]}
              radius={active ? 9 : 6}
              pathOptions={{
                color: active ? '#0f766e' : '#1f2937',
                fillColor: STATUS_COLOR[pin.worst],
                fillOpacity: 0.92,
                weight: active ? 3 : 1.5,
                className: `asset-marker ${pin.worst}${active ? ' active' : ''}`,
              }}
              eventHandlers={onSelectAsset ? { click: () => onSelectAsset(pin.equipment_id) } : undefined}
            >
              <Tooltip direction="top" offset={[0, -4]}>
                <strong>{pin.tag}</strong> · {STATUS_LABEL[pin.worst]}
              </Tooltip>
              <Popup>
                <div className="map-pop">
                  <strong>{pin.tag}</strong>
                  <small>{pin.signals.length} signal{pin.signals.length === 1 ? '' : 's'}{pin.openOrders ? ` · ${pin.openOrders} open WO` : ''}</small>
                  <div className="map-siglist">
                    {pin.signals.slice(0, 8).map(sig => (
                      <div className="map-sig" key={sig.label}>
                        <span className={`dot ${sig.status}`} />
                        <span className="map-sig-name">{sig.label}</span>
                        <span className="map-sig-val">{valueText(sig.value, sig.unit)}</span>
                        <span className={`map-sig-age ${freshnessOf(sig.eventTime)}`} title={sig.eventTime ? new Date(sig.eventTime).toLocaleString() : undefined}>{ageLabel(sig.eventTime)}</span>
                      </div>
                    ))}
                    {!pin.signals.length && <div className="map-sig muted">No mapped signals.</div>}
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          )
        })}
      </MapContainer>

      <div className="map-legend">
        <div className="map-legend-scale">
          <span><i style={{ background: STATUS_COLOR.ok }} />Healthy</span>
          <span><i style={{ background: STATUS_COLOR.warn }} />Uncertain</span>
          <span><i style={{ background: STATUS_COLOR.crit }} />Bad</span>
        </div>
        <small>{assets?.length ? `${assets.length} assets · ${selectedName ?? 'facility'}` : 'Live signal quality'}{updatedAt ? ` · ${new Date(updatedAt).toLocaleTimeString()}` : ''}</small>
      </div>
    </div>
  )
}