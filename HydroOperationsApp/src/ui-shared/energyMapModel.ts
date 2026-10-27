import type { Feature, FeatureCollection, Geometry } from 'geojson'
import { validateEnergyPropertyFilters, createEnergyPropertyFilters, type EnergyPropertyFilters } from './energyMapFilters'
import { lakehouseTimestamp } from '../services/lakehouseConnector'

export const MAP_LAYERS = [
  { id: 'transmission', label: 'Transmission lines', source: 'NVE grid', color: '#0f766e', minZoom: 3, defaultVisible: true },
  { id: 'regional', label: 'Regional lines', source: 'NVE grid', color: '#b45309', minZoom: 6, defaultVisible: true },
  { id: 'distribution', label: 'Distribution lines', source: 'NVE grid', color: '#475569', minZoom: 11, defaultVisible: false },
  { id: 'sea-cables', label: 'Subsea cables', source: 'NVE grid', color: '#7c3aed', minZoom: 6, defaultVisible: true },
  { id: 'masts', label: 'Masts and poles', source: 'NVE grid', color: '#ca8a04', minZoom: 12, defaultVisible: false },
  { id: 'transformers', label: 'Transformer substations', source: 'NVE grid', color: '#334155', minZoom: 8, defaultVisible: false },
  { id: 'hydro-plants', label: 'Hydropower plants', source: 'NVE hydropower', color: '#0284c7', minZoom: 3, defaultVisible: true },
  { id: 'reservoirs', label: 'Reservoir area statistics', source: 'NVE reservoirs', color: '#0891b2', minZoom: 3, defaultVisible: true },
  { id: 'power-balance', label: 'Country power balance', source: 'Statnett', color: '#16a34a', minZoom: 3, defaultVisible: false },
  { id: 'power-flows', label: 'Power exchange', source: 'Statnett', color: '#c026d3', minZoom: 3, defaultVisible: true },
  { id: 'grid-frequency', label: 'Grid frequency', source: 'Statnett', color: '#4f46e5', minZoom: 3, defaultVisible: false },
  { id: 'umm', label: 'Market messages', source: 'Nord Pool UMM', color: '#dc2626', minZoom: 3, defaultVisible: true },
] as const

export type EnergyLayerId = (typeof MAP_LAYERS)[number]['id']
export type MapViewport = { west: number; south: number; east: number; north: number; zoom: number }
export type EnergyFeature = {
  id: string
  layerId: EnergyLayerId
  label: string
  geometry: Geometry | null
  properties: Record<string, unknown>
  observedAt: string | null
  ingestedAt: string
  sourceUrl: string
}
export type EnergySourceStatus = {
  layerId: EnergyLayerId
  state: 'ready' | 'error'
  lastAttemptAt: string | null
  lastSuccessAt: string | null
  rowCount: number
  unmappedCount: number
  rejectedCount: number
  message: string
  sourceUrl: string
}
export const FEATURE_LIMIT = 4000
export const INITIAL_VIEW: MapViewport = { west: 3, south: 56, east: 33, north: 72, zoom: 4 }
export const MAP_VISIBLE_LAYERS = MAP_LAYERS.filter(layer =>
  layer.id !== 'grid-frequency' && layer.id !== 'power-balance' && layer.id !== 'umm')
export const DEFAULT_LAYERS: EnergyLayerId[] = MAP_VISIBLE_LAYERS.filter(layer => layer.defaultVisible).map(layer => layer.id)
export const RESERVOIR_AREAS = [
  { code: 'NO1', label: 'NO1 - East Norway', color: '#2563eb' },
  { code: 'NO2', label: 'NO2 - South Norway', color: '#ea580c' },
  { code: 'NO3', label: 'NO3 - Central Norway', color: '#16a34a' },
  { code: 'NO4', label: 'NO4 - North Norway', color: '#9333ea' },
  { code: 'NO5', label: 'NO5 - West Norway', color: '#db2777' },
  { code: 'NO', label: 'Norway - country', color: '#0891b2' },
  { code: 'SE', label: 'Sweden', color: '#ca8a04' },
  { code: 'FI', label: 'Finland', color: '#4f46e5' },
  { code: 'DK', label: 'Denmark', color: '#dc2626' },
] as const
export type ReservoirAreaCode = (typeof RESERVOIR_AREAS)[number]['code']
export type ReservoirAreaSelection = ReservoirAreaCode[] | null

export function isReservoirAreaCode(value: unknown): value is ReservoirAreaCode {
  return typeof value === 'string' && RESERVOIR_AREAS.some(area => area.code === value)
}

export function isAssetLayer(layer: EnergyLayerId): boolean {
  return ['hydro-plants', 'transformers', 'transmission', 'regional', 'distribution', 'sea-cables', 'masts'].includes(layer)
}

export function isEnergyLayer(value: unknown): value is EnergyLayerId {
  return typeof value === 'string' && MAP_LAYERS.some(layer => layer.id === value)
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function validCoordinates(value: unknown, depth = 0): boolean {
  if (!Array.isArray(value) || !value.length || depth > 5) return false
  if (typeof value[0] === 'number') {
    return value.length >= 2 && value.every(item => typeof item === 'number' && Number.isFinite(item))
      && Math.abs(value[0]) <= 180 && Math.abs(value[1]) <= 90
  }
  return value.every(item => validCoordinates(item, depth + 1))
}

export function isMapGeometry(value: unknown): value is Geometry {
  if (!record(value) || typeof value.type !== 'string') return false
  const depth = { Point: 1, MultiPoint: 2, LineString: 2, MultiLineString: 3, Polygon: 3, MultiPolygon: 4 }[value.type]
  if (!depth || !validCoordinates(value.coordinates)) return false
  const hasDepth = (coordinates: unknown, remaining: number): boolean => Array.isArray(coordinates)
    && coordinates.length > 0 && (remaining === 1 ? typeof coordinates[0] === 'number'
      : coordinates.every(child => hasDepth(child, remaining - 1)))
  return hasDepth(value.coordinates, depth)
}

type XY = number[]
type Ring = XY[]
type Edge = { a: XY; b: XY; polygon: number; ring: number; index: number; length: number; minX: number; maxX: number; minY: number; maxY: number }
const cross = (a: XY, b: XY, p: XY) => (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
const samePoint = (a: XY, b: XY) => a[0] === b[0] && a[1] === b[1]
function onSegment(a: XY, b: XY, p: XY): boolean {
  return Math.abs(cross(a, b, p)) <= 1e-12
    && p[0] >= Math.min(a[0], b[0]) && p[0] <= Math.max(a[0], b[0])
    && p[1] >= Math.min(a[1], b[1]) && p[1] <= Math.max(a[1], b[1])
}
function ringContains(ring: Ring, point: XY): -1 | 0 | 1 {
  let inside = false
  for (let i = 1; i < ring.length; i++) {
    const a = ring[i - 1], b = ring[i]
    if (onSegment(a, b, point)) return 0
    if ((a[1] > point[1]) !== (b[1] > point[1])
      && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside
  }
  return inside ? 1 : -1
}

export function prepareAreaGeometry(geometry: Geometry | null): (point: XY) => boolean {
  const invalid = () => { throw new Error('Selected area geometry is unavailable or invalid. Reload the map or reset areas.') }
  if (!geometry || !isMapGeometry(geometry) || (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon')) return invalid()
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  const edges: Edge[] = []
  let ringNumber = 0
  for (let polygonNumber = 0; polygonNumber < polygons.length; polygonNumber++) {
    const polygon = polygons[polygonNumber]
    for (const ring of polygon) {
      if (ring.length < 4 || !samePoint(ring[0], ring[ring.length - 1])) return invalid()
      let area = 0
      for (let i = 1; i < ring.length; i++) {
        const a = ring[i - 1], b = ring[i]
        if (samePoint(a, b)) return invalid()
        const next = ring[i === ring.length - 1 ? 1 : i + 1]
        if (cross(a, b, next) === 0
          && (b[0] - a[0]) * (next[0] - b[0]) + (b[1] - a[1]) * (next[1] - b[1]) < 0) return invalid()
        area += (a[0] - ring[0][0]) * (b[1] - ring[0][1]) - (b[0] - ring[0][0]) * (a[1] - ring[0][1])
        edges.push({ a, b, polygon: polygonNumber, ring: ringNumber, index: i - 1, length: ring.length - 1,
          minX: Math.min(a[0], b[0]), maxX: Math.max(a[0], b[0]), minY: Math.min(a[1], b[1]), maxY: Math.max(a[1], b[1]) })
      }
      if (!Number.isFinite(area) || Math.abs(area) < 1e-12) return invalid()
      ringNumber++
    }
    for (let i = 1; i < polygon.length; i++) {
      if (ringContains(polygon[0], polygon[i][0]) !== 1) return invalid()
      for (let j = 1; j < i; j++) {
        if (ringContains(polygon[j], polygon[i][0]) !== -1 || ringContains(polygon[i], polygon[j][0]) !== -1) return invalid()
      }
    }
  }
  edges.sort((a, b) => a.minX - b.minX)
  for (let i = 0; i < edges.length; i++) {
    const a = edges[i]
    for (let j = i + 1; j < edges.length && edges[j].minX <= a.maxX; j++) {
      const b = edges[j]
      if (b.minY > a.maxY || b.maxY < a.minY) continue
      if (a.ring === b.ring && (Math.abs(a.index - b.index) === 1 || Math.abs(a.index - b.index) === a.length - 1)) continue
      const ab = cross(a.a, a.b, b.a), ac = cross(a.a, a.b, b.b)
      const ba = cross(b.a, b.b, a.a), bc = cross(b.a, b.b, a.b)
      const crossing = ab * ac < 0 && ba * bc < 0
      const touching = onSegment(a.a, a.b, b.a) || onSegment(a.a, a.b, b.b)
        || onSegment(b.a, b.b, a.a) || onSegment(b.a, b.b, a.b)
      const sharedLine = ab === 0 && ac === 0
        && (Math.min(a.maxX, b.maxX) > Math.max(a.minX, b.minX)
          || Math.min(a.maxY, b.maxY) > Math.max(a.minY, b.minY))
      if (crossing || (touching && (a.polygon === b.polygon || sharedLine))) return invalid()
    }
  }
  const contains = (polygon: Ring[], point: XY) => ringContains(polygon[0], point) >= 0
    && !polygon.slice(1).some(hole => ringContains(hole, point) === 1)
  const interior = (polygon: Ring[], point: XY) => ringContains(polygon[0], point) === 1
    && polygon.slice(1).every(hole => ringContains(hole, point) === -1)
  const samples = (ring: Ring) => ring.slice(1).flatMap((point, index) =>
    [point, [(point[0] + ring[index][0]) / 2, (point[1] + ring[index][1]) / 2]])
  const bounds = polygons.map(polygon => polygon[0].reduce((box, point) => [
    Math.min(box[0], point[0]), Math.min(box[1], point[1]), Math.max(box[2], point[0]), Math.max(box[3], point[1]),
  ], [Infinity, Infinity, -Infinity, -Infinity]))
  for (let i = 0; i < polygons.length; i++) {
    for (let j = 0; j < i; j++) {
      const a = bounds[i], b = bounds[j]
      if (a[0] >= b[2] || b[0] >= a[2] || a[1] >= b[3] || b[1] >= a[3]) continue
      if (samples(polygons[i][0]).some(point => interior(polygons[j], point))
        || samples(polygons[j][0]).some(point => interior(polygons[i], point))) return invalid()
    }
  }
  return point => point.length >= 2 && point.every(Number.isFinite) && polygons.some(polygon => contains(polygon, point))
}

function parseObject(value: unknown, name: string): Record<string, unknown> {
  const parsed: unknown = typeof value === 'string' ? JSON.parse(value) : value
  if (!record(parsed)) throw new Error(`Invalid ${name} in Fabric map data.`)
  return parsed
}

export function parseEnergyFeature(row: Record<string, unknown>): EnergyFeature {
  if (!isEnergyLayer(row.layer_id) || typeof row.feature_id !== 'string'
    || typeof row.label !== 'string') {
    throw new Error('Fabric returned an invalid energy map feature.')
  }
  let geometry: Geometry | null = null
  if (row.geometry_json) {
    const parsed: unknown = JSON.parse(String(row.geometry_json))
    if (!isMapGeometry(parsed)) throw new Error(`Invalid geometry for ${row.layer_id}/${row.feature_id}.`)
    geometry = parsed
  }
  return {
    id: row.feature_id, layerId: row.layer_id, label: row.label, geometry,
    properties: parseObject(row.properties_json, 'feature properties'),
    observedAt: row.observed_at == null || row.observed_at === '' ? null : lakehouseTimestamp(row.observed_at, 'observed_at'),
    ingestedAt: lakehouseTimestamp(row.ingested_at, 'ingested_at'),
    sourceUrl: typeof row.source_url === 'string' ? row.source_url : '',
  }
}

export function parseSourceStatus(row: Record<string, unknown>): EnergySourceStatus {
  if (!isEnergyLayer(row.layer_id) || !['ready', 'error'].includes(String(row.state))) {
    throw new Error('Fabric returned an invalid map source status.')
  }
  const count = (value: unknown) => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      throw new Error('Fabric returned an invalid source feature count.')
    }
    return value
  }
  return {
    layerId: row.layer_id, state: row.state as 'ready' | 'error',
    lastAttemptAt: row.last_attempt_at == null || row.last_attempt_at === '' ? null : lakehouseTimestamp(row.last_attempt_at, 'last_attempt_at'),
    lastSuccessAt: row.last_success_at == null || row.last_success_at === '' ? null : lakehouseTimestamp(row.last_success_at, 'last_success_at'),
    rowCount: count(row.row_count), unmappedCount: count(row.unmapped_count),
    rejectedCount: count(row.rejected_count),
    message: typeof row.message === 'string' ? row.message : '',
    sourceUrl: typeof row.source_url === 'string' ? row.source_url : '',
  }
}

export function visibleLayerIds(selected: EnergyLayerId[], zoom: number): EnergyLayerId[] {
  return MAP_VISIBLE_LAYERS.filter(layer => selected.includes(layer.id) && zoom >= layer.minZoom).map(layer => layer.id)
}

export function validateEnergyMapRequest(view: MapViewport, layers: EnergyLayerId[], properties: EnergyPropertyFilters = createEnergyPropertyFilters(), areaSelection: ReservoirAreaSelection = null): EnergyLayerId[] {
  const values = [view.west, view.south, view.east, view.north, view.zoom]
  if (!values.every(Number.isFinite) || view.west >= view.east || view.south >= view.north
    || view.west < -180 || view.east > 180 || view.south < -90 || view.north > 90
    || view.zoom < 0 || view.zoom > 24 || layers.some(layer => !isEnergyLayer(layer))) {
    throw new Error('Invalid map viewport or layer selection.')
  }
  const selected = visibleLayerIds(layers, view.zoom).filter(layer => layer !== 'reservoirs')
  validateEnergyPropertyFilters(properties)
  if (areaSelection !== null && (!Array.isArray(areaSelection) || areaSelection.some(code => !isReservoirAreaCode(code)))) {
    throw new Error('Invalid reservoir-area selection.')
  }
  return selected
}

export function visiblePlantCapacity(features: EnergyFeature[]) {
  const seen = new Set<string>()
  let totalMw = 0
  let compensation = 0
  let knownPlants = 0
  let unknownPlants = 0
  let transformers = 0
  for (const feature of features) {
    if (!feature.geometry || (feature.layerId !== 'hydro-plants' && feature.layerId !== 'transformers')) continue
    const key = `${feature.layerId}:${feature.id}`
    if (seen.has(key)) continue
    seen.add(key)
    if (feature.layerId === 'transformers') { transformers++; continue }
    const capacity = feature.properties.installed_capacity_mw
    if (typeof capacity !== 'number' || !Number.isFinite(capacity) || capacity < 0) { unknownPlants++; continue }
    knownPlants++
    const increment = capacity - compensation
    const next = totalMw + increment
    compensation = (next - totalMw) - increment
    totalMw = next
  }
  return { totalMw, knownPlants, unknownPlants, transformers }
}

export function formatCapacityPower(totalMw: number): { value: string; unit: 'MW' | 'GW' | 'TW' } {
  if (!Number.isFinite(totalMw) || totalMw < 0) throw new Error('Invalid visible capacity total.')
  const divisor = totalMw >= 1_000_000 ? 1_000_000 : totalMw >= 1000 ? 1000 : 1
  return {
    value: (totalMw / divisor).toLocaleString(undefined, { maximumFractionDigits: 3 }),
    unit: divisor === 1_000_000 ? 'TW' : divisor === 1000 ? 'GW' : 'MW',
  }
}

export function validateMarketMessageAsset(asset: Pick<EnergyFeature, 'id' | 'layerId'>): void {
  if (!isAssetLayer(asset.layerId) || typeof asset.id !== 'string' || !asset.id || asset.id.length > 2048) {
    throw new Error('Invalid market-message asset selection.')
  }
}

export function capacityRadius(capacity: unknown, maximum: number | null | undefined): number {
  if (typeof capacity !== 'number' || !Number.isFinite(capacity) || capacity < 0
    || typeof maximum !== 'number' || !Number.isFinite(maximum) || maximum <= 0) return 5
  return Math.sqrt(3 ** 2 + (18 ** 2 - 3 ** 2) * Math.min(capacity / maximum, 1))
}

export function renderLayerSignature(features: EnergyFeature[], capacityMaximum?: number | null): string {
  return JSON.stringify([capacityMaximum ?? null, features.map(feature => [
    feature.id, feature.ingestedAt, feature.observedAt, feature.label,
  ])])
}

export function asFeatureCollection(features: EnergyFeature[], capacityMaximum?: number | null): FeatureCollection {
  const mapped: Feature[] = features.flatMap(feature => {
    if (!feature.geometry || feature.layerId === 'umm' || feature.layerId === 'grid-frequency' || feature.layerId === 'power-balance') return []
    const layer = MAP_LAYERS.find(item => item.id === feature.layerId)!
    const filling = feature.properties.filling_fraction
    const hasReservoirData = feature.properties.has_reservoir_data === true && typeof filling === 'number' && Number.isFinite(filling)
    const color = feature.layerId === 'reservoirs'
      ? RESERVOIR_AREAS.find(area => area.code === feature.properties.area_code)?.color ?? '#94a3b8'
      : layer.color
    const radius = feature.layerId === 'hydro-plants'
      ? capacityRadius(feature.properties.installed_capacity_mw, capacityMaximum) : feature.layerId === 'masts' ? 3 : 6
    return [{
      type: 'Feature' as const, id: `${feature.layerId}:${feature.id}`,
      geometry: feature.geometry,
      properties: {
        feature_id: feature.id, layer_id: feature.layerId, label: feature.label, color, radius,
        area_kind: feature.properties.area_kind ?? '', area_code: feature.properties.area_code ?? '',
        has_reservoir_data: hasReservoirData,
      },
    }]
  })
  return { type: 'FeatureCollection', features: mapped }
}

export function sourceAge(iso: string | null, now = Date.now()): string {
  if (!iso) return 'Not imported'
  const minutes = Math.max(0, Math.floor((now - Date.parse(iso)) / 60_000))
  if (!Number.isFinite(minutes)) return 'Unknown time'
  return minutes < 1 ? 'Just now' : minutes < 60 ? `${minutes}m ago`
    : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago` : `${Math.floor(minutes / 1440)}d ago`
}

export function sourceIsStale(status: EnergySourceStatus, now = Date.now()): boolean {
  if (!status.lastSuccessAt) return true
  const limit = status.layerId === 'reservoirs' ? 8 * 86400_000
    : status.layerId === 'grid-frequency' ? 2 * 60_000
      : ['power-balance', 'power-flows', 'umm'].includes(status.layerId) ? 30 * 60_000 : 2 * 86400_000
  const time = Date.parse(status.lastSuccessAt)
  return !Number.isFinite(time) || now - time > limit
}

export function safeSourceUrl(value: string): string | undefined {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined
  } catch {
    return undefined
  }
}
