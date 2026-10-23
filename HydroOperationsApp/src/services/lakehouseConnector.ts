import type { ConnectorConfig } from '@microsoft/rayfin-connectors'
import type { HydrolakehouseSchema } from '../../rayfin/connectors/hydrolakehouse/schema'

export type LakehouseConnectors = { hydrolakehouse: HydrolakehouseSchema }

// Browser code carries scalar selections, not decorated server entity classes.
export const lakehouseConnectorConfig = {
  connector: 'fabric-sqlanalytics',
  operations: ['read'],
  entities: {
    SilverFacilities: ['facility_id', 'facility_name', 'type', 'country', 'lat', 'lon', 'commissioned_date'],
    SilverSystems: ['system_id', 'facility_id', 'system_name', 'oag_rds_system_code'],
    SilverEquipment: ['equipment_id', 'facility_id', 'system_id', 'equipment_type_code', 'equipment_type_name',
      'tag', 'manufacturer', 'model', 'criticality', 'install_date', 'status', 'is_active'],
    SilverInstruments: ['opcua_node_id', 'tag', 'instrument_id', 'equipment_id', 'system_id', 'facility_id',
      'unit', 'instrument_type', 'is_active'],
    WeatherLocations: ['location_id', 'location_name', 'latitude', 'longitude', 'elevation_m'],
    WeatherAreas: ['area_id', 'area_name', 'geometry_geojson', 'crs', 'metadata_json'],
    WeatherVariables: ['variable_id', 'canonical_unit'],
    WeatherLatestObservations: ['source_id', 'location_id', 'observed_at_utc', 'precipitation', 'temperature',
      'pressure', 'relative_humidity', 'dew_point', 'solar_radiation', 'wind_speed', 'wind_gust', 'wind_direction'],
    WeatherLatestForecasts: ['source_id', 'target_kind', 'target_id', 'reference_time_utc', 'valid_time_utc',
      'lead_hours', 'precipitation_interval_hours', 'cumulative_precipitation', 'rainfall_volume_m3',
      'cumulative_rainfall_volume_m3', 'precipitation', 'temperature', 'pressure', 'relative_humidity',
      'dew_point', 'solar_radiation', 'wind_speed', 'wind_gust', 'wind_direction'],
  },
} as const satisfies ConnectorConfig

export type LakehousePage<T> = { items: T[]; hasNextPage: boolean; endCursor?: string | null }

export async function collectLakehousePages<T>(
  read: (cursor?: string) => Promise<LakehousePage<T>>,
): Promise<T[]> {
  const rows: T[] = []
  const cursors = new Set<string>()
  let cursor: string | undefined
  for (;;) {
    const page = await read(cursor)
    if (!Array.isArray(page.items) || typeof page.hasNextPage !== 'boolean') {
      throw new Error('Lakehouse connector returned an invalid page; partial data is not accepted.')
    }
    rows.push(...page.items)
    if (!page.hasNextPage) return rows
    if (!page.endCursor || cursors.has(page.endCursor)) {
      throw new Error('Lakehouse connector pagination did not advance; partial data is not accepted.')
    }
    cursors.add(page.endCursor)
    cursor = page.endCursor
  }
}

export function requireLakehouseText(value: unknown, column: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Lakehouse column ${column} is missing or invalid.`)
  return value
}

export function requireLakehouseNumber(value: unknown, column: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`Lakehouse column ${column} is missing or invalid.`)
  return value
}

export function lakehouseTimestamp(value: unknown, column: string): string {
  const text = value instanceof Date ? value.toISOString() : requireLakehouseText(value, column)
  if (!Number.isFinite(Date.parse(text))) throw new Error(`Lakehouse column ${column} is not a valid timestamp.`)
  return text
}
