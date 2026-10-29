import type { OntologyEntityType } from '../services/ontologyContract'
import type { KnowledgeGraph, KnowledgeNode } from './knowledgeGraphModel'
import { WEATHER_VALUE_UNITS } from './weatherSummary'

export type WeatherEntityKind = 'location' | 'area' | 'observation' | 'forecast' | 'source' | 'metadata'
const kinds: Record<string, WeatherEntityKind> = {
  WeatherLocation: 'location', weather_locations: 'location',
  WeatherArea: 'area', weather_areas: 'area',
  WeatherLatestObservation: 'observation', weather_latest_observations: 'observation',
  WeatherLatestForecast: 'forecast', weather_latest_forecasts: 'forecast',
  WeatherSource: 'source', weather_sources: 'source',
  WeatherVariable: 'metadata', WeatherIngestionRun: 'metadata',
}

export function weatherEntityKind(entity?: OntologyEntityType): WeatherEntityKind | undefined {
  if (!entity) return undefined
  for (const key of [entity.localName ?? entity.name, entity.sourceTable ?? '']) {
    if (Object.hasOwn(kinds, key)) return kinds[key]
  }
  return undefined
}

const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : undefined
const coordinate = (value: unknown, limit: number) => {
  const parsed = number(value)
  return parsed !== undefined && Math.abs(parsed) <= limit ? parsed : undefined
}
const utcTime = (value: unknown) => typeof value === 'string'
  ? Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`) : NaN
const HOUR = 3_600_000
const variables = {
  temperature: 'Temperature', precipitation: 'Precipitation', wind_speed: 'Wind speed',
  wind_gust: 'Wind gust', wind_direction: 'Wind direction', relative_humidity: 'Humidity',
  pressure: 'Pressure', dew_point: 'Dew point', solar_radiation: 'Solar radiation',
}

function validAreaGeometry(node: KnowledgeNode): string | undefined {
  if (!['EPSG:4326', 'CRS84', 'OGC:CRS84'].includes(String(node.properties.crs))) return undefined
  const raw = node.properties.geometry_geojson
  if (typeof raw !== 'string') return undefined
  let geometry: unknown
  try { geometry = JSON.parse(raw) }
  catch (error) {
    if (!(error instanceof SyntaxError)) throw error
    console.warn(`Weather area ${node.entityId} has malformed GeoJSON; its forecast is not selected.`)
    return undefined
  }
  if (!geometry || typeof geometry !== 'object' || !('type' in geometry) || !('coordinates' in geometry)) return undefined
  const position = (point: unknown): point is number[] => Array.isArray(point) && point.length >= 2
    && coordinate(point[0], 180) !== undefined && coordinate(point[1], 90) !== undefined
  const ring = (points: unknown) => Array.isArray(points) && points.length >= 4 && points.every(position)
    && points[0][0] === points.at(-1)[0] && points[0][1] === points.at(-1)[1]
  const polygon = (rings: unknown) => Array.isArray(rings) && rings.length > 0 && rings.every(ring)
  const valid = geometry.type === 'Polygon' ? polygon(geometry.coordinates)
    : geometry.type === 'MultiPolygon' && Array.isArray(geometry.coordinates)
      && geometry.coordinates.length > 0 && geometry.coordinates.every(polygon)
  return valid ? JSON.stringify(geometry) : undefined
}

type WeatherSample = { row: KnowledgeNode; source?: KnowledgeNode; rank: number; time: number; issued: number; area?: KnowledgeNode; geometry?: string }
function compareSamples(a?: WeatherSample, b?: WeatherSample): number {
  if (!a || !b) return a ? -1 : b ? 1 : 0
  return a.rank - b.rank || (a.rank === 0 ? b.time - a.time : a.time - b.time)
    || b.issued - a.issued || a.row.id.localeCompare(b.row.id)
}

export function projectFacilityWeather(graph: KnowledgeGraph, nowMs: number): KnowledgeGraph {
  const byId = new Map(graph.nodes.map(node => [node.id, node]))
  const adjacent = new Map<string, KnowledgeNode[]>()
  for (const edge of graph.edges) {
    for (const [from, to] of [[edge.source, edge.target], [edge.target, edge.source]]) {
      const nodes = adjacent.get(from) ?? []
      nodes.push(byId.get(to)!)
      adjacent.set(from, nodes)
    }
  }
  const neighbours = (node: KnowledgeNode) => adjacent.get(node.id) ?? []
  const chosen = new Map<string, { location: KnowledgeNode; sample?: WeatherSample }>()
  for (const location of graph.nodes.filter(node => node.weatherKind === 'location')) {
    const facilities = neighbours(location).filter(node => node.type === 'facility')
    if (facilities.length !== 1) continue
    const samples: WeatherSample[] = []
    const consider = (row: KnowledgeNode, area?: KnowledgeNode) => {
      if (!['observation', 'forecast'].includes(row.weatherKind ?? '')) return
      if (!Object.keys(variables).some(key => number(row.properties[key]) !== undefined)) return
      if (!row.properties.source_id) return
      const source = neighbours(row).find(node => node.weatherKind === 'source')
      if (source && /^(false|0)$/i.test(String(source.properties.active))) return
      if (row.weatherKind === 'observation') {
        const time = utcTime(row.properties.observed_at_utc)
        if (time <= nowMs && time >= nowMs - 3 * HOUR) samples.push({ row, source, rank: 0, time, issued: time })
        return
      }
      if (row.properties.target_kind !== (area ? 'area' : 'location')
        || row.properties.target_id !== (area ?? location).entityId) return
      const time = utcTime(row.properties.valid_time_utc)
      const issued = utcTime(row.properties.reference_time_utc)
      const geometry = area && validAreaGeometry(area)
      if (area && !geometry) return
      if (issued <= nowMs && issued >= nowMs - 48 * HOUR && time >= issued
        && time >= nowMs && time <= nowMs + 6 * HOUR) {
        samples.push({ row, source, rank: area ? 2 : 1, time, issued, area, geometry })
      }
    }
    for (const node of neighbours(location)) {
      consider(node)
      if (node.weatherKind === 'area') for (const row of neighbours(node)) consider(row, node)
    }
    samples.sort(compareSamples)
    const candidate = { location, sample: samples[0] }
    const current = chosen.get(facilities[0].entityId)
    if (!current || compareSamples(candidate.sample, current.sample) < 0
      || (compareSamples(candidate.sample, current.sample) === 0 && location.id < current.location.id)) {
      chosen.set(facilities[0].entityId, candidate)
    }
  }
  const contexts = new Map<string, KnowledgeNode>()
  for (const [facilityId, { location, sample }] of chosen) {
    const latitude = coordinate(location.properties.latitude, 90)
    const longitude = coordinate(location.properties.longitude, 180)
    const properties: KnowledgeNode['properties'] = {
      'Weather status': sample ? sample.row.weatherKind === 'observation' ? 'Recent observation' : 'Forecast' : 'No current weather',
    }
    const values: string[] = []
    if (sample) {
      properties.Source = String(sample.source?.properties.source_name ?? sample.row.properties.source_id)
      properties['Valid time (UTC)'] = new Date(sample.time).toISOString()
      if (sample.row.weatherKind === 'forecast') properties['Forecast issued (UTC)'] = new Date(sample.issued).toISOString()
      for (const [key, label] of Object.entries(variables)) {
        const value = number(sample.row.properties[key])
        if (value === undefined) continue
        const formatted = `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${WEATHER_VALUE_UNITS[key]}`.trim()
        properties[label] = formatted
        if (['temperature', 'precipitation', 'wind_speed'].includes(key)) values.push(`${label}: ${formatted}`)
      }
      properties['Weather record'] = sample.row.entityId
      properties['Weather entity type'] = String(sample.row.properties['Entity type'])
      properties['Forecast area'] = sample.area?.label
      properties['Area geometry'] = sample.geometry
    }
    Object.assign(properties, {
      'Location ID': location.entityId, Latitude: latitude, Longitude: longitude,
      Geometry: latitude !== undefined && longitude !== undefined
        ? JSON.stringify({ type: 'Point', coordinates: [longitude, latitude] }) : undefined,
      'Geometry status': latitude !== undefined && longitude !== undefined ? 'WGS84 Point' : 'Location coordinates unavailable or invalid',
      Coverage: sample ? sample.area ? 'Declared area forecast fallback' : 'Facility location weather'
        : 'No observation within 3 hours or upcoming forecast within 6 hours (issued within 48 hours).',
    })
    contexts.set(location.id, {
      ...location, type: 'weather', facilityId, label: `${location.label} weather`,
      subtitle: sample ? `${sample.row.weatherKind === 'forecast' ? 'Forecast' : 'Observed'} · ${values.join(' · ') || 'See weather details'}` : 'No current weather',
      status: sample ? 'ok' : 'nodata', properties,
      provenance: `${location.provenance}${sample ? ` · ${sample.row.provenance} · record ${sample.row.entityId}` : ' · no valid recent observation or near-term forecast'}`,
    })
  }
  const nodes = graph.nodes.filter(node => !node.weatherKind || contexts.has(node.id)).map(node => contexts.get(node.id) ?? node)
  const ids = new Set(nodes.map(node => node.id))
  return { ...graph, nodes, edges: graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)) }
}
