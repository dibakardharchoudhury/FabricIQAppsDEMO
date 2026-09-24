import { runKustoQuery, type KustoResult } from './fabric'
import {
  buildEnergyMapQuery, FEATURE_LIMIT, parseEnergyFeature, parseSourceStatus, UMM_MAP_PREDICATE,
  type EnergyLayerId, type MapViewport,
} from '../ui-shared/energyMapModel'
import { ENERGY_PROPERTY_OPTIONS_QUERY, parseEnergyPropertyOptions, type EnergyPropertyFilters } from '../ui-shared/energyMapFilters'

function rows(result: KustoResult): Record<string, unknown>[] {
  return result.rows.map(row => Object.fromEntries(result.columns.map((name, index) => [name, row[index]])))
}

export async function queryEnergyMap(viewport: MapViewport, layers: EnergyLayerId[], signal: AbortSignal, properties?: EnergyPropertyFilters) {
  const result = await runKustoQuery(buildEnergyMapQuery(viewport, layers, properties), FEATURE_LIMIT + 1, signal)
  const records = rows(result)
  return { features: records.slice(0, FEATURE_LIMIT).map(parseEnergyFeature), truncated: records.length > FEATURE_LIMIT }
}

export async function queryEnergyPropertyOptions(signal: AbortSignal) {
  return parseEnergyPropertyOptions(rows(await runKustoQuery(ENERGY_PROPERTY_OPTIONS_QUERY, 3, signal)))
}

export async function queryEnergySourceStatus(signal: AbortSignal) {
  return rows(await runKustoQuery("external_table('HydroGeoStatus') | order by layer_id asc | take 20", 20, signal))
    .map(parseSourceStatus)
}

export async function queryUnplottedMarketMessages(signal: AbortSignal) {
  return rows(await runKustoQuery(`external_table('HydroGeoFeatures')
| where layer_id == 'umm' and (isempty(geometry_json) or not(coalesce(${UMM_MAP_PREDICATE}, false)))
| order by observed_at desc
| take 101
| project feature_id, layer_id, label, geometry_json, properties_json, observed_at, ingested_at, source_url`, 101, signal))
    .map(parseEnergyFeature)
}
