import { runKustoQuery, type KustoResult } from './fabric'
import { connectedGeo } from './rayfin'
import { createGeoEnergyService, readGeoPayloads } from './geoConnector'
import type { EnergyFeature, EnergyLayerId, MapViewport, ReservoirAreaSelection } from '../ui-shared/energyMapModel'
import type { EnergyPropertyFilters } from '../ui-shared/energyMapFilters'
import { buildLiveFrequencyQuery, parseLiveFrequency, type LiveFrequencyReading } from '../ui-shared/liveFrequencyModel'
import type { MapPlaceReference } from '../ui-shared/mapChatModel'

function rows(result: KustoResult): Record<string, unknown>[] {
  return result.rows.map(row => Object.fromEntries(result.columns.map((name, index) => [name, row[index]])))
}

const geo = () => createGeoEnergyService(connectedGeo(), (metadata, area, signal) =>
  readGeoPayloads(metadata, area, signal, async (query, maximumRows, requestSignal) =>
    rows(await runKustoQuery(query, maximumRows, requestSignal))))

export function queryEnergyMap(viewport: MapViewport, layers: EnergyLayerId[], signal: AbortSignal, properties?: EnergyPropertyFilters, areaSelection: ReservoirAreaSelection = null) {
  return geo().queryEnergyMap(viewport, layers, signal, properties, areaSelection)
}
export function queryEnergyPropertyOptions(signal: AbortSignal) { return geo().queryEnergyPropertyOptions(signal) }
export function queryEnergySourceStatus(signal: AbortSignal) { return geo().queryEnergySourceStatus(signal) }
export function queryReservoirAreas(signal: AbortSignal) { return geo().queryReservoirAreas(signal) }
export function queryGridFrequency(signal: AbortSignal) { return geo().queryGridFrequency(signal) }
export function queryCountryPowerBalance(signal: AbortSignal) { return geo().queryCountryPowerBalance(signal) }
export function queryEnergyFeatureDetails(feature: EnergyFeature, signal: AbortSignal) { return geo().queryEnergyFeatureDetails(feature, signal) }
export function queryAssetMarketMessages(asset: EnergyFeature, signal: AbortSignal) { return geo().queryAssetMarketMessages(asset, signal) }
export function searchMapPlaces(search: string, signal: AbortSignal, layer?: EnergyLayerId) { return geo().searchMapPlaces(search, signal, layer) }
export function resolveMapPlace(reference: MapPlaceReference, signal: AbortSignal) { return geo().resolveMapPlace(reference, signal) }

export async function queryLiveGridFrequency(signal: AbortSignal): Promise<LiveFrequencyReading> {
  const requestedAt = Date.now()
  const result = rows(await runKustoQuery(buildLiveFrequencyQuery(requestedAt), 2, signal))
  return parseLiveFrequency(result, requestedAt, Date.now())
}
