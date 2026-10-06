import type { Feature, FeatureCollection, Geometry } from 'geojson'
import { buildEnergyPropertyPredicate, createEnergyPropertyFilters, type EnergyPropertyFilters } from './energyMapFilters'

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

function parseObject(value: unknown, name: string): Record<string, unknown> {
  const parsed: unknown = typeof value === 'string' ? JSON.parse(value) : value
  if (!record(parsed)) throw new Error(`Invalid ${name} in Fabric map data.`)
  return parsed
}

export function parseEnergyFeature(row: Record<string, unknown>): EnergyFeature {
  if (!isEnergyLayer(row.layer_id) || typeof row.feature_id !== 'string'
    || typeof row.label !== 'string' || typeof row.ingested_at !== 'string') {
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
    observedAt: typeof row.observed_at === 'string' && row.observed_at ? row.observed_at : null,
    ingestedAt: row.ingested_at,
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
    lastAttemptAt: typeof row.last_attempt_at === 'string' ? row.last_attempt_at : null,
    lastSuccessAt: typeof row.last_success_at === 'string' ? row.last_success_at : null,
    rowCount: count(row.row_count), unmappedCount: count(row.unmapped_count),
    rejectedCount: count(row.rejected_count),
    message: typeof row.message === 'string' ? row.message : '',
    sourceUrl: typeof row.source_url === 'string' ? row.source_url : '',
  }
}

export function visibleLayerIds(selected: EnergyLayerId[], zoom: number): EnergyLayerId[] {
  return MAP_VISIBLE_LAYERS.filter(layer => selected.includes(layer.id) && zoom >= layer.minZoom).map(layer => layer.id)
}

export const UMM_MAP_PREDICATE = "tobool(parse_json(properties_json).map_eligible) == true"

export function buildEnergyMapQuery(view: MapViewport, layers: EnergyLayerId[], properties: EnergyPropertyFilters = createEnergyPropertyFilters(), areaSelection: ReservoirAreaSelection = null): string {
  const values = [view.west, view.south, view.east, view.north, view.zoom]
  if (!values.every(Number.isFinite) || view.west >= view.east || view.south >= view.north
    || view.west < -180 || view.east > 180 || view.south < -90 || view.north > 90
    || view.zoom < 0 || view.zoom > 24 || layers.some(layer => !isEnergyLayer(layer))) {
    throw new Error('Invalid map viewport or layer selection.')
  }
  const selected = visibleLayerIds(layers, view.zoom).filter(layer => layer !== 'reservoirs')
  const filter = selected.length ? `layer_id in (${selected.map(id => `'${id}'`).join(',')})` : 'false'
  const propertyPredicate = buildEnergyPropertyPredicate(properties)
  if (areaSelection !== null && (!Array.isArray(areaSelection) || areaSelection.some(code => !isReservoirAreaCode(code)))) {
    throw new Error('Invalid reservoir-area selection.')
  }
  const codes = areaSelection === null ? null : [...new Set(areaSelection)]
  const areaPrefix = codes?.length ? `let selected_area_rows = materialize(external_table('HydroGeoReservoirAreas')
| where tostring(parse_json(properties_json).area_code) in (${codes.map(code => JSON.stringify(code)).join(', ')})
| project polygon = parse_json(geometry_json));
let selected_region = toscalar(selected_area_rows | summarize polygons = make_list(polygon) | project geo_union_polygons_array(polygons));
let region_valid = toscalar(selected_area_rows | count) == ${codes.length} and isnotnull(selected_region);
` : ''
  const areaPredicate = codes === null ? '' : codes.length
    ? "| where region_valid\n| where layer_id !in ('hydro-plants', 'transformers') or (tostring(parse_json(geometry_json).type) == 'Point' and geo_point_in_polygon(min_lon, min_lat, selected_region))\n"
    : "| where layer_id !in ('hydro-plants', 'transformers')\n"
  const query = `external_table('HydroGeoFeatures')
| where ${filter}
| where max_lon >= ${view.west} and min_lon <= ${view.east} and max_lat >= ${view.south} and min_lat <= ${view.north}
${propertyPredicate ? `| extend map_properties = parse_json(iff(layer_id in ('hydro-plants', 'transformers'), properties_json, '{}'))\n| where ${propertyPredicate}\n` : ''}${areaPredicate}| extend layer_order = case(layer_id == 'umm', 0, layer_id == 'reservoirs', 1, layer_id == 'power-flows', 2, layer_id == 'power-balance', 3, layer_id == 'grid-frequency', 4, layer_id == 'hydro-plants', 5, layer_id == 'transformers', 6, 7)
| order by layer_order asc, layer_id asc, feature_id asc
| take ${FEATURE_LIMIT + 1}
| project feature_id, layer_id, label, geometry_json,
    properties_json = tostring(bag_remove_keys(parse_json(properties_json), dynamic(['source_properties', 'gis_properties', 'source_message']))),
    observed_at, ingested_at, source_url`
  return areaPrefix ? `${areaPrefix}union (${query}),
(print area_filter_error = 'Selected area geometry is unavailable or invalid. Reload the map or reset areas.' | where not(region_valid))` : query
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

export function buildAssetMarketMessagesQuery(asset: Pick<EnergyFeature, 'id' | 'layerId'>): string {
  if (!isAssetLayer(asset.layerId) || typeof asset.id !== 'string' || !asset.id || asset.id.length > 2048) {
    throw new Error('Invalid market-message asset selection.')
  }
  return `let links = external_table('HydroGeoMarketAssetLinks')
| where asset_feature_id == ${JSON.stringify(asset.id)} and asset_layer_id == ${JSON.stringify(asset.layerId)}
| project message_feature_id, message_version, match_method, match_evidence_json;
external_table('HydroGeoFeatures')
| where layer_id == 'umm'
| extend message_version = tolong(parse_json(properties_json).version)
| join kind=inner links on $left.feature_id == $right.message_feature_id, message_version
| order by observed_at desc
| take 101
| project feature_id, layer_id, label, geometry_json,
    properties_json = tostring(bag_merge(parse_json(properties_json), bag_pack('asset_match_method', match_method, 'asset_match_evidence', parse_json(match_evidence_json)))),
    observed_at, ingested_at, source_url`
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
