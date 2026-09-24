import { runKustoQuery, type KustoResult } from './fabric'
import {
  buildAssetMarketMessagesQuery, buildEnergyMapQuery, FEATURE_LIMIT, isEnergyLayer, parseEnergyFeature, parseSourceStatus,
  type EnergyFeature, type EnergyLayerId, type MapViewport, type ReservoirAreaSelection,
} from '../ui-shared/energyMapModel'
import { ENERGY_PROPERTY_OPTIONS_QUERY, parseEnergyPropertyOptions, type EnergyPropertyFilters } from '../ui-shared/energyMapFilters'
import { buildLiveFrequencyQuery, parseLiveFrequency, type LiveFrequencyReading } from '../ui-shared/liveFrequencyModel'

function rows(result: KustoResult): Record<string, unknown>[] {
  return result.rows.map(row => Object.fromEntries(result.columns.map((name, index) => [name, row[index]])))
}

export async function queryEnergyMap(viewport: MapViewport, layers: EnergyLayerId[], signal: AbortSignal, properties?: EnergyPropertyFilters, areaSelection: ReservoirAreaSelection = null) {
  const result = await runKustoQuery(buildEnergyMapQuery(viewport, layers, properties, areaSelection), FEATURE_LIMIT + 1, signal)
  const records = rows(result)
  const invalidArea = records.find(row => typeof row.area_filter_error === 'string' && row.area_filter_error)
  if (invalidArea) throw new Error(String(invalidArea.area_filter_error))
  return { features: records.slice(0, FEATURE_LIMIT).map(parseEnergyFeature), truncated: records.length > FEATURE_LIMIT }
}

export async function queryEnergyPropertyOptions(signal: AbortSignal) {
  return parseEnergyPropertyOptions(rows(await runKustoQuery(ENERGY_PROPERTY_OPTIONS_QUERY, 3, signal)))
}

export async function queryEnergySourceStatus(signal: AbortSignal) {
  return rows(await runKustoQuery("external_table('HydroGeoStatus') | order by layer_id asc | take 20", 20, signal))
    .map(parseSourceStatus)
}

export async function queryReservoirAreas(signal: AbortSignal) {
  const result = rows(await runKustoQuery("external_table('HydroGeoReservoirAreas') | take 21", 21, signal))
  if (result.length > 20) throw new Error('Reservoir-area coverage exceeds the expected geometry contract.')
  const features = result.map(parseEnergyFeature)
  const codes = new Set(features.map(feature => feature.properties.area_code))
  if (features.length !== 9 || codes.size !== 9
    || !['NO', 'SE', 'FI', 'DK', 'NO1', 'NO2', 'NO3', 'NO4', 'NO5'].every(code => codes.has(code))
    || features.some(feature => feature.layerId !== 'reservoirs' || !feature.geometry
      || !['Polygon', 'MultiPolygon'].includes(feature.geometry.type))) {
    throw new Error('Reservoir-area geometry is incomplete. Check the Fabric import.')
  }
  return features
}

export async function queryGridFrequency(signal: AbortSignal): Promise<EnergyFeature | null> {
  const result = rows(await runKustoQuery("external_table('HydroGeoFeatures') | where layer_id == 'grid-frequency' | take 2", 2, signal))
  if (result.length > 1) throw new Error('The frequency snapshot contains duplicate records.')
  return result.length ? parseEnergyFeature(result[0]) : null
}

export async function queryLiveGridFrequency(signal: AbortSignal): Promise<LiveFrequencyReading> {
  const requestedAt = Date.now()
  const result = rows(await runKustoQuery(buildLiveFrequencyQuery(requestedAt), 2, signal))
  return parseLiveFrequency(result, requestedAt, Date.now())
}

export async function queryCountryPowerBalance(signal: AbortSignal): Promise<EnergyFeature | null> {
  const result = rows(await runKustoQuery("external_table('HydroGeoFeatures') | where layer_id == 'power-balance' | take 2", 2, signal))
  if (result.length > 1) throw new Error('The country power-balance snapshot contains duplicate records.')
  if (!result.length) return null
  const feature = parseEnergyFeature(result[0])
  if (feature.properties.country_code !== 'NO') throw new Error('Unexpected country in the power-balance snapshot.')
  return feature
}

export async function queryEnergyFeatureDetails(feature: EnergyFeature, signal: AbortSignal): Promise<EnergyFeature> {
  if (!isEnergyLayer(feature.layerId) || feature.id.length > 2048) throw new Error('Invalid asset selection.')
  const table = feature.layerId === 'reservoirs' ? 'HydroGeoReservoirAreas' : 'HydroGeoFeatures'
  const result = rows(await runKustoQuery(`external_table('${table}')
| where layer_id == ${JSON.stringify(feature.layerId)} and feature_id == ${JSON.stringify(feature.id)}
| take 2`, 2, signal))
  if (result.length !== 1) throw new Error('Selected feature details are unavailable or ambiguous. Reload the map.')
  return parseEnergyFeature(result[0])
}

export async function queryAssetMarketMessages(asset: EnergyFeature, signal: AbortSignal): Promise<EnergyFeature[]> {
  return rows(await runKustoQuery(buildAssetMarketMessagesQuery(asset), 101, signal)).map(parseEnergyFeature)
}
