import type { ConnectorConfig } from '@microsoft/rayfin-connectors'
import type { FilterInput } from '@microsoft/rayfin-data'
import type { HydrogeoSchema } from '../../rayfin/connectors/hydrogeo/schema'
import {
  FEATURE_LIMIT, isAssetLayer, isEnergyLayer, isReservoirAreaCode, MAP_LAYERS, parseEnergyFeature, parseSourceStatus,
  prepareAreaGeometry, validateEnergyMapRequest, validateMarketMessageAsset,
  type EnergyFeature, type EnergyLayerId, type MapViewport, type ReservoirAreaSelection,
} from '../ui-shared/energyMapModel'
import {
  createEnergyPropertyFilters, matchesEnergyPropertyFilters, parseEnergyPropertyOptions, type EnergyPropertyFilters,
} from '../ui-shared/energyMapFilters'
import { parseMapPlace, validReference, validateMapPlaceSearch, type MapPlaceReference } from '../ui-shared/mapChatModel'

export type GeoConnectors = { hydrogeo: HydrogeoSchema }
export type GeoApi = NonNullable<HydrogeoSchema['__client']>
export type GeoRow = {
  feature_id?: string; layer_id?: string; label?: string; geometry_json?: string; properties_json?: string
  observed_at?: string; ingested_at?: string; source_url?: string
  min_lon?: number; min_lat?: number; max_lon?: number; max_lat?: number
}
export type GeoFilter = FilterInput<GeoRow>

export const GEO_FEATURE_COLUMNS = ['feature_id', 'layer_id', 'label', 'geometry_json', 'properties_json',
  'observed_at', 'ingested_at', 'source_url', 'min_lon', 'min_lat', 'max_lon', 'max_lat'] as const
export const GEO_PLACE_COLUMNS = ['feature_id', 'layer_id', 'label', 'properties_json',
  'min_lon', 'min_lat', 'max_lon', 'max_lat'] as const
export const geoConnectorConfig = {
  connector: 'fabric-sqlanalytics', operations: ['read'],
  entities: {
    GeoMapFeatures: GEO_FEATURE_COLUMNS,
    GeoReservoirAreas: GEO_FEATURE_COLUMNS,
    GeoSourceStatus: ['layer_id', 'source_id', 'state', 'last_attempt_at', 'last_success_at',
      'row_count', 'unmapped_count', 'rejected_count', 'message', 'source_url'],
    GeoMarketAssetLinks: ['message_feature_id', 'message_version', 'asset_feature_id', 'asset_layer_id',
      'match_method', 'match_evidence_json', 'linked_at', 'run_id'],
  },
} as const satisfies ConnectorConfig

export const GEO_PAGE_SIZE = 250
export const GEO_FACET_ROW_LIMIT = 100_000
export const GEO_SEARCH_ROW_LIMIT = 10_000
export const GEO_LINK_ROW_LIMIT = 10_000
type GeoPage<T> = { items: T[]; hasNextPage: boolean; endCursor?: string | null }

// The SDK has no signal argument. Reject promptly on abort and never start a
// subsequent page; the already-started SDK HTTP request may still finish.
export function abortableGeoRead<T>(read: () => Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted()
  return new Promise((resolve, reject) => {
    const aborted = () => reject(signal.reason)
    signal.addEventListener('abort', aborted, { once: true })
    Promise.resolve().then(() => { signal.throwIfAborted(); return read() }).then(resolve, reject)
      .finally(() => signal.removeEventListener('abort', aborted))
  })
}

export async function* geoPages<T>(
  read: (cursor?: string) => Promise<GeoPage<T>>, signal: AbortSignal,
): AsyncGenerator<T[]> {
  const cursors = new Set<string>()
  let cursor: string | undefined
  for (let pageNumber = 0; pageNumber < 10_000; pageNumber++) {
    const page = await abortableGeoRead(() => read(cursor), signal)
    signal.throwIfAborted()
    if (!Array.isArray(page.items) || page.items.length > GEO_PAGE_SIZE || typeof page.hasNextPage !== 'boolean'
      || (page.hasNextPage && (!page.items.length || !page.endCursor || cursors.has(page.endCursor)))) {
      throw new Error('Geo connector pagination is invalid or did not advance; partial data is not accepted.')
    }
    yield page.items
    if (!page.hasNextPage) return
    cursor = page.endCursor!
    cursors.add(cursor)
  }
  throw new Error('Geo connector exceeded the pagination safety limit; narrow the map selection.')
}

export async function collectGeoRows<T>(
  read: (cursor?: string) => Promise<GeoPage<T>>, signal: AbortSignal, limit: number,
): Promise<T[]> {
  const rows: T[] = []
  for await (const page of geoPages(read, signal)) {
    rows.push(...page)
    if (rows.length > limit) throw new Error(`Geo connector exceeded the supported ${limit}-row bound; partial data is not accepted.`)
  }
  return rows
}

export function geoProperties(row: GeoRow): Record<string, unknown> {
  const value: unknown = JSON.parse(row.properties_json ?? '')
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid feature properties in Geo data.')
  return value as Record<string, unknown>
}

export function compactGeoRow(row: GeoRow): GeoRow {
  const properties = { ...geoProperties(row) }
  for (const key of ['source_properties', 'gis_properties', 'source_message']) delete properties[key]
  return { ...row, properties_json: JSON.stringify(properties) }
}

const ordinal = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0
const isPointAsset = (layer: string) => layer === 'hydro-plants' || layer === 'transformers'

export function createGeoEnergyService(source: GeoApi) {
  async function featureRows(filter: GeoFilter, signal: AbortSignal, area = false) {
    const entity = area ? source.GeoReservoirAreas : source.GeoMapFeatures
    return collectGeoRows(cursor => entity.select([...GEO_FEATURE_COLUMNS]).where(filter)
      .orderBy({ feature_id: 'asc' }).first(2).after(cursor).executePaginated(), signal, 2)
  }

  async function queryReservoirAreas(signal: AbortSignal) {
    const rows = await collectGeoRows(cursor => source.GeoReservoirAreas.select([...GEO_FEATURE_COLUMNS])
      .orderBy({ feature_id: 'asc' }).first(21).after(cursor).executePaginated(), signal, 20)
    const features = rows.map(parseEnergyFeature)
    const codes = new Set(features.map(feature => feature.properties.area_code))
    if (features.length !== 9 || codes.size !== 9
      || !['NO', 'SE', 'FI', 'DK', 'NO1', 'NO2', 'NO3', 'NO4', 'NO5'].every(code => codes.has(code))
      || features.some(feature => feature.layerId !== 'reservoirs')) {
      throw new Error('Reservoir-area geometry is incomplete. Check the Fabric import.')
    }
    for (const feature of features) prepareAreaGeometry(feature.geometry)
    return features
  }

  async function queryEnergyMap(viewport: MapViewport, layers: EnergyLayerId[], signal: AbortSignal,
    properties: EnergyPropertyFilters = createEnergyPropertyFilters(), areaSelection: ReservoirAreaSelection = null) {
    signal.throwIfAborted()
    const selected = validateEnergyMapRequest(viewport, layers, properties, areaSelection)
    const areaTests = areaSelection?.length
      ? (await queryReservoirAreas(signal)).filter(area => areaSelection.some(code => code === area.properties.area_code))
        .map(area => prepareAreaGeometry(area.geometry))
      : []
    const priority = (id: string) => id === 'power-flows' ? 2 : id === 'hydro-plants' ? 5 : id === 'transformers' ? 6 : 7
    selected.sort((a, b) => priority(a) - priority(b) || ordinal(a, b))
    const features: EnergyFeature[] = []
    for (const layer of selected) {
      if (areaSelection?.length === 0 && isPointAsset(layer)) continue
      const seen = new Set<string>()
      const filter: GeoFilter = { layer_id: { eq: layer }, max_lon: { gte: viewport.west },
        min_lon: { lte: viewport.east }, max_lat: { gte: viewport.south }, min_lat: { lte: viewport.north } }
      for await (const page of geoPages(cursor => source.GeoMapFeatures.select([...GEO_FEATURE_COLUMNS]).where(filter)
        .orderBy({ feature_id: 'asc' }).first(GEO_PAGE_SIZE).after(cursor).executePaginated(), signal)) {
        for (const row of page) {
          if (seen.has(row.feature_id ?? '')) throw new Error('Geo viewport contains duplicate features; partial data is not accepted.')
          seen.add(row.feature_id ?? '')
          const feature = parseEnergyFeature(row)
          if (feature.layerId !== layer) throw new Error('Geo connector returned an unexpected layer.')
          if (!matchesEnergyPropertyFilters(feature, properties)) continue
          const point = feature.geometry?.type === 'Point' ? feature.geometry.coordinates : null
          if (areaSelection !== null && isPointAsset(layer) && (!point || !areaTests.some(test => test(point)))) continue
          features.push(parseEnergyFeature(compactGeoRow(row)))
          if (features.length > FEATURE_LIMIT) return { features: features.slice(0, FEATURE_LIMIT), truncated: true }
        }
      }
    }
    return { features, truncated: false }
  }

  async function queryEnergyPropertyOptions(signal: AbortSignal) {
    const rows = new Map<string, Record<string, unknown>>()
    for (const layer of ['hydro-plants', 'transformers']) {
      const values = { owners: new Set<string>(), price_areas: new Set<string>(), plant_statuses: new Set<string>(),
        source_layers: new Set<string>(), network_levels: new Set<string>() }
      const maximums: Record<string, number | null> = { capacity_max: null, gross_head_max: null, voltage_max: null }
      let count = 0
      for await (const page of geoPages(cursor => source.GeoMapFeatures.select(['feature_id', 'layer_id', 'properties_json'])
        .where({ layer_id: { eq: layer } }).orderBy({ feature_id: 'asc' }).first(GEO_PAGE_SIZE).after(cursor).executePaginated(), signal)) {
        count += page.length
        if (count > GEO_FACET_ROW_LIMIT) throw new Error(`Property metadata exceeds the ${GEO_FACET_ROW_LIMIT}-row bound; incomplete facets are not accepted.`)
        for (const row of page) {
          if (row.layer_id !== layer) throw new Error('Unexpected Geo property layer.')
          const p = geoProperties(row)
          for (const [facet, field] of Object.entries({ owners: 'owner', price_areas: 'price_area', plant_statuses: 'plant_status',
            source_layers: 'source_layer', network_levels: 'network_level' })) {
            const set = values[facet as keyof typeof values]
            set.add(p[field] === null || p[field] === undefined ? '' : String(p[field]))
            if (set.size > 5000) throw new Error('Property filter options exceed the supported limit.')
          }
          for (const [key, field] of Object.entries({ capacity_max: 'installed_capacity_mw', gross_head_max: 'gross_head_m', voltage_max: 'voltage_kv' })) {
            const value = p[field]
            if ((typeof value === 'number' || typeof value === 'string') && String(value).trim() && Number.isFinite(Number(value))) {
              maximums[key] = maximums[key] === null ? Number(value) : Math.max(maximums[key]!, Number(value))
            }
          }
        }
      }
      if (count) rows.set(layer, { layer_id: layer, ...Object.fromEntries(Object.entries(values).map(([key, set]) => [key, [...set]])), ...maximums })
    }
    return parseEnergyPropertyOptions([...rows.values()])
  }

  async function queryEnergySourceStatus(signal: AbortSignal) {
    return (await collectGeoRows(cursor => source.GeoSourceStatus.select([...geoConnectorConfig.entities.GeoSourceStatus])
      .orderBy({ layer_id: 'asc' }).first(21).after(cursor).executePaginated(), signal, 20)).map(parseSourceStatus)
  }

  async function snapshot(layer: 'grid-frequency' | 'power-balance', signal: AbortSignal) {
    const rows = await featureRows({ layer_id: { eq: layer } }, signal)
    if (rows.length > 1) throw new Error(`The ${layer} snapshot contains duplicate records.`)
    if (!rows.length) return null
    const feature = parseEnergyFeature(rows[0])
    if (feature.layerId !== layer || (layer === 'power-balance' && feature.properties.country_code !== 'NO')) {
      throw new Error('Unexpected country or layer in the power-balance/frequency snapshot.')
    }
    return feature
  }

  async function queryEnergyFeatureDetails(feature: Pick<EnergyFeature, 'id' | 'layerId'>, signal: AbortSignal) {
    if (!isEnergyLayer(feature.layerId) || typeof feature.id !== 'string' || !feature.id || feature.id.length > 2048) throw new Error('Invalid asset selection.')
    const rows = await featureRows({ layer_id: { eq: feature.layerId }, feature_id: { eq: feature.id } }, signal, feature.layerId === 'reservoirs')
    // SQL equality may be case-insensitive; identities are always exact.
    const matches = rows.filter(row => row.layer_id === feature.layerId && row.feature_id === feature.id)
    if (matches.length !== 1) throw new Error('Selected feature details are unavailable or ambiguous. Reload the map.')
    return parseEnergyFeature(matches[0])
  }

  async function queryAssetMarketMessages(asset: Pick<EnergyFeature, 'id' | 'layerId'>, signal: AbortSignal) {
    validateMarketMessageAsset(asset)
    const links = await collectGeoRows(cursor => source.GeoMarketAssetLinks.select([...geoConnectorConfig.entities.GeoMarketAssetLinks])
      .where({ asset_feature_id: { eq: asset.id }, asset_layer_id: { eq: asset.layerId } })
      .orderBy({ message_feature_id: 'asc', message_version: 'asc' }).first(GEO_PAGE_SIZE).after(cursor).executePaginated(), signal, GEO_LINK_ROW_LIMIT)
    const byId = new Map<string, typeof links>()
    const keys = new Set<string>()
    for (const link of links) {
      if (link.asset_feature_id !== asset.id || link.asset_layer_id !== asset.layerId) continue
      if (typeof link.message_feature_id !== 'string' || !link.message_feature_id
        || !Number.isSafeInteger(link.message_version) || typeof link.match_method !== 'string' || !link.match_method
        || typeof link.match_evidence_json !== 'string') throw new Error('Invalid Geo market-message match evidence.')
      const key = JSON.stringify([link.message_feature_id, link.message_version])
      if (keys.has(key)) throw new Error('Duplicate Geo market-message asset link.')
      keys.add(key)
      JSON.parse(link.match_evidence_json)
      const matches = byId.get(link.message_feature_id) ?? []
      matches.push(link)
      byId.set(link.message_feature_id, matches)
    }
    const ids = [...byId.keys()]
    const messages: EnergyFeature[] = []
    for (let i = 0; i < ids.length; i += 100) {
      const batch = ids.slice(i, i + 100)
      const rows = await collectGeoRows(cursor => source.GeoMapFeatures.select([...GEO_FEATURE_COLUMNS])
        .where({ layer_id: { eq: 'umm' }, feature_id: { in: batch } })
        .orderBy({ feature_id: 'asc' }).first(GEO_PAGE_SIZE).after(cursor).executePaginated(), signal, GEO_LINK_ROW_LIMIT)
      for (const row of rows) {
        const feature = parseEnergyFeature(row)
        if (feature.layerId !== 'umm') throw new Error('Unexpected market-message layer.')
        const rawVersion = feature.properties.version
        const version = (typeof rawVersion === 'number' || (typeof rawVersion === 'string' && rawVersion.trim()))
          ? Number(rawVersion) : NaN
        if (!Number.isSafeInteger(version)) continue
        const match = byId.get(feature.id)?.find(link => link.message_version === version)
        if (!match) continue
        messages.push({ ...feature, properties: { ...feature.properties, asset_match_method: match.match_method,
          asset_match_evidence: JSON.parse(match.match_evidence_json!) } })
      }
    }
    return messages.sort((a, b) => ordinal(b.observedAt ?? '', a.observedAt ?? '') || ordinal(a.id, b.id)).slice(0, 101)
  }

  async function searchMapPlaces(search: string, signal: AbortSignal, layer?: EnergyLayerId) {
    const term = validateMapPlaceSearch(search, layer)
    const folded = term.toLowerCase()
    const candidates: GeoRow[] = []
    if (layer !== 'reservoirs') {
      const layers = layer ? [layer] : MAP_LAYERS.filter(item => isAssetLayer(item.id)).map(item => item.id)
      candidates.push(...await collectGeoRows(cursor => source.GeoMapFeatures.select([...GEO_PLACE_COLUMNS])
        .where({ and: [{ layer_id: { in: layers } }, geoPlaceCandidates(term)] })
        .orderBy({ label: 'asc', feature_id: 'asc', layer_id: 'asc' }).first(GEO_PAGE_SIZE).after(cursor).executePaginated(), signal, GEO_SEARCH_ROW_LIMIT))
    }
    if (!layer || layer === 'reservoirs') {
      candidates.push(...await collectGeoRows(cursor => source.GeoReservoirAreas.select([...GEO_PLACE_COLUMNS])
        .orderBy({ feature_id: 'asc' }).first(21).after(cursor).executePaginated(), signal, 20))
    }
    const seen = new Set<string>()
    const places = candidates.flatMap(row => {
      const p = geoProperties(row)
      const label = row.label ?? '', area = String(p.area_code ?? '')
      if (!label.toLowerCase().includes(folded) && row.feature_id !== term && area.toLowerCase() !== folded) return []
      const key = JSON.stringify([row.layer_id, row.feature_id])
      if (seen.has(key)) throw new Error('Geo name search contains duplicate identities.')
      seen.add(key)
      return [parseMapPlace({ ...row, owner: p.owner, price_area: p.price_area,
        exact_match: label.toLowerCase() === folded || row.feature_id === term || area.toLowerCase() === folded })]
    }).sort((a, b) => Number(b.exactMatch) - Number(a.exactMatch) || ordinal(a.label, b.label) || ordinal(a.feature_id, b.feature_id))
    return { places: places.slice(0, 10), truncated: places.length > 10 }
  }

  async function resolveMapPlace(reference: MapPlaceReference, signal: AbortSignal) {
    if (!validReference(reference)) throw new Error('Invalid map asset reference.')
    const rows = await featureRows({ layer_id: { eq: reference.layer_id }, feature_id: { eq: reference.feature_id } }, signal, reference.layer_id === 'reservoirs')
    const matches = rows.filter(row => row.layer_id === reference.layer_id && row.feature_id === reference.feature_id)
    if (matches.length !== 1) throw new Error('The requested place was not found uniquely in the current map data.')
    const feature = parseEnergyFeature(compactGeoRow(matches[0]))
    const place = parseMapPlace(matches[0])
    if (!place.bounds || !feature.geometry) throw new Error(`${place.label} has no verified map location.`)
    let areaCodes: string[] | undefined
    if (isPointAsset(feature.layerId) && feature.geometry.type === 'Point') {
      const point = feature.geometry.coordinates
      areaCodes = (await queryReservoirAreas(signal)).filter(area => prepareAreaGeometry(area.geometry)(point)).map(area => {
        if (!isReservoirAreaCode(area.properties.area_code)) throw new Error('Area membership could not be verified for navigation.')
        return area.properties.area_code
      })
    }
    return { place, feature, areaCodes }
  }

  return { queryEnergyMap, queryEnergyPropertyOptions, queryEnergySourceStatus, queryReservoirAreas,
    queryGridFrequency: (signal: AbortSignal) => snapshot('grid-frequency', signal),
    queryCountryPowerBalance: (signal: AbortSignal) => snapshot('power-balance', signal),
    queryEnergyFeatureDetails, queryAssetMarketMessages, searchMapPlaces, resolveMapPlace }
}

// SQL analytics normally uses binary collation. AND-ed short case-variant
// chunks provide a server-side superset independent of its collation. Verify
// the complete term locally, and fail rather than truncate excess candidates.
let lowerCaseEquivalents: Map<string, string[]> | undefined
function caseVariants(value: string): string[] {
  if (!lowerCaseEquivalents) {
    lowerCaseEquivalents = new Map()
    // Include titlecase and compatibility letters (e.g. Kelvin sign), not
    // only upper/lower pairs. This is independent of SQL endpoint collation.
    for (let code = 0; code <= 0x10ffff; code++) {
      const character = String.fromCodePoint(code), lower = character.toLowerCase()
      if (character === lower) continue
      const variants = lowerCaseEquivalents.get(lower) ?? []
      variants.push(character)
      lowerCaseEquivalents.set(lower, variants)
    }
  }
  let variants = ['']
  for (const character of Array.from(value)) {
    const equivalents = [character, ...(lowerCaseEquivalents.get(character) ?? []), ...(character === 'ς' ? ['Σ'] : [])]
    variants = variants.flatMap(prefix => equivalents.map(item => prefix + item))
  }
  return [...new Set([...variants, ...(lowerCaseEquivalents.get(value) ?? [])])]
}

export function geoPlaceCandidates(term: string): GeoFilter {
  const characters = [...new Intl.Segmenter('und', { granularity: 'grapheme' }).segment(term.toLowerCase())].map(item => item.segment)
  const chunks: GeoFilter[] = []
  for (let i = 0; i < characters.length; i += 3) {
    let variants = ['']
    for (const character of characters.slice(i, i + 3)) {
      const cases = caseVariants(character)
      variants = variants.flatMap(prefix => cases.map(value => prefix + value))
    }
    chunks.push({ or: variants.map(value => ({ label: { contains: value } })) })
  }
  return { or: [{ feature_id: { eq: term } }, { and: chunks }] }
}
