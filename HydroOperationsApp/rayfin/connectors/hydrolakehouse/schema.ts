import { authenticated, boolean, date, decimal, entity, int, text } from '@microsoft/rayfin-core'
import { Source } from '@microsoft/rayfin-connectors'
import type { GraphQLBackedConnector } from '@microsoft/rayfin-connector-fabric-graphql'
import type { ConnectorConfig } from '@microsoft/rayfin-connectors'
import type { EntityClass } from '@microsoft/rayfin-core/schema'

// SQL discovery exposes no PK/FK metadata. Keep these read-only, keyless and
// relationship-free; retain source column names used by the existing app.
@entity()
@authenticated('read')
export class SilverFacilities extends Source({ schema: 'dbo', table: 'silver_facilities', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) facility_id?: string
  @text({ optional: true, max: 8000 }) facility_name?: string
  @text({ optional: true, max: 8000 }) type?: string
  @text({ optional: true, max: 8000 }) country?: string
  @text({ optional: true, max: 8000 }) lat?: string
  @text({ optional: true, max: 8000 }) lon?: string
  @text({ optional: true, max: 8000 }) commissioned_date?: string
}

@entity()
@authenticated('read')
export class SilverSystems extends Source({ schema: 'dbo', table: 'silver_systems', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) system_id?: string
  @text({ optional: true, max: 8000 }) facility_id?: string
  @text({ optional: true, max: 8000 }) system_name?: string
  @text({ optional: true, max: 8000 }) oag_rds_system_code?: string
}

@entity()
@authenticated('read')
export class SilverEquipment extends Source({ schema: 'dbo', table: 'silver_equipment', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) equipment_id?: string
  @text({ optional: true, max: 8000 }) facility_id?: string
  @text({ optional: true, max: 8000 }) system_id?: string
  @text({ optional: true, max: 8000 }) equipment_type_code?: string
  @text({ optional: true, max: 8000 }) equipment_type_name?: string
  @text({ optional: true, max: 8000 }) tag?: string
  @text({ optional: true, max: 8000 }) manufacturer?: string
  @text({ optional: true, max: 8000 }) model?: string
  @int({ optional: true }) criticality?: number
  @date({ optional: true }) install_date?: Date
  @text({ optional: true, max: 8000 }) status?: string
  @boolean({ optional: true }) is_active?: boolean
}

@entity()
@authenticated('read')
export class SilverInstruments extends Source({ schema: 'dbo', table: 'silver_instruments', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) opcua_node_id?: string
  @text({ optional: true, max: 8000 }) tag?: string
  @text({ optional: true, max: 8000 }) instrument_id?: string
  @text({ optional: true, max: 8000 }) equipment_id?: string
  @text({ optional: true, max: 8000 }) system_id?: string
  @text({ optional: true, max: 8000 }) facility_id?: string
  @text({ optional: true, max: 8000 }) unit?: string
  @text({ optional: true, max: 8000 }) instrument_type?: string
  @boolean({ optional: true }) is_active?: boolean
}

@entity()
@authenticated('read')
export class WeatherLocations extends Source({ schema: 'dbo', table: 'weather_locations', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) location_id?: string
  @text({ optional: true, max: 8000 }) location_name?: string
  @decimal({ optional: true }) latitude?: number
  @decimal({ optional: true }) longitude?: number
  @decimal({ optional: true }) elevation_m?: number
  @text({ optional: true, max: 8000 }) metadata_json?: string
  @date({ optional: true }) updated_at_utc?: Date
}

@entity()
@authenticated('read')
export class WeatherAreas extends Source({ schema: 'dbo', table: 'weather_areas', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) area_id?: string
  @text({ optional: true, max: 8000 }) area_name?: string
  @text({ optional: true, max: 8000 }) geometry_geojson?: string
  @text({ optional: true, max: 8000 }) crs?: string
  @text({ optional: true, max: 8000 }) location_id?: string
  @text({ optional: true, max: 8000 }) metadata_json?: string
  @date({ optional: true }) updated_at_utc?: Date
}

@entity()
@authenticated('read')
export class WeatherVariables extends Source({ schema: 'dbo', table: 'weather_variables', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) variable_id?: string
  @text({ optional: true, max: 8000 }) canonical_unit?: string
  @text({ optional: true, max: 8000 }) canonical_name?: string
  @text({ optional: true, max: 8000 }) aggregation_kind?: string
  @text({ optional: true, max: 8000 }) description?: string
  @date({ optional: true }) updated_at_utc?: Date
}

@entity()
@authenticated('read')
export class WeatherLatestObservations extends Source({ schema: 'dbo', table: 'weather_latest_observations', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) source_id?: string
  @text({ optional: true, max: 8000 }) location_id?: string
  @date({ optional: true }) observed_at_utc?: Date
  @decimal({ optional: true }) precipitation?: number
  @decimal({ optional: true }) temperature?: number
  @decimal({ optional: true }) pressure?: number
  @decimal({ optional: true }) relative_humidity?: number
  @decimal({ optional: true }) dew_point?: number
  @decimal({ optional: true }) solar_radiation?: number
  @decimal({ optional: true }) wind_speed?: number
  @decimal({ optional: true }) wind_gust?: number
  @decimal({ optional: true }) wind_direction?: number
  @date({ optional: true }) calculated_at_utc?: Date
  @text({ optional: true, max: 8000 }) ontology_key?: string
}

@entity()
@authenticated('read')
export class WeatherLatestForecasts extends Source({ schema: 'dbo', table: 'weather_latest_forecasts', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) source_id?: string
  @text({ optional: true, max: 8000 }) target_kind?: string
  @text({ optional: true, max: 8000 }) target_id?: string
  @date({ optional: true }) reference_time_utc?: Date
  @date({ optional: true }) valid_time_utc?: Date
  @int({ optional: true }) lead_hours?: number
  @int({ optional: true }) precipitation_interval_hours?: number
  @decimal({ optional: true }) cumulative_precipitation?: number
  @decimal({ optional: true }) rainfall_volume_m3?: number
  @decimal({ optional: true }) cumulative_rainfall_volume_m3?: number
  @decimal({ optional: true }) precipitation?: number
  @decimal({ optional: true }) temperature?: number
  @decimal({ optional: true }) pressure?: number
  @decimal({ optional: true }) relative_humidity?: number
  @decimal({ optional: true }) dew_point?: number
  @decimal({ optional: true }) solar_radiation?: number
  @decimal({ optional: true }) wind_speed?: number
  @decimal({ optional: true }) wind_gust?: number
  @decimal({ optional: true }) wind_direction?: number
  @date({ optional: true }) calculated_at_utc?: Date
  @text({ optional: true, max: 8000 }) location_id?: string
  @text({ optional: true, max: 8000 }) area_id?: string
  @text({ optional: true, max: 8000 }) ontology_key?: string
}

@entity()
@authenticated('read')
export class OntologyEquipmentTypes extends Source({ schema: 'dbo', table: 'ontology_equipment_types', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) equipment_type_code?: string
  @text({ optional: true, max: 8000 }) equipment_type_name?: string
}

@entity()
@authenticated('read')
export class SilverSignalMaster extends Source({ schema: 'dbo', table: 'silver_signal_master', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) opcua_node_id?: string
  @text({ optional: true, max: 8000 }) tag?: string
  @text({ optional: true, max: 8000 }) instrument_id?: string
  @text({ optional: true, max: 8000 }) equipment_id?: string
  @text({ optional: true, max: 8000 }) system_id?: string
  @text({ optional: true, max: 8000 }) facility_id?: string
  @text({ optional: true, max: 8000 }) unit?: string
  @boolean({ optional: true }) is_active?: boolean
  @text({ optional: true, max: 8000 }) signal_type?: string
}

@entity()
@authenticated('read')
export class WeatherSources extends Source({ schema: 'dbo', table: 'weather_sources', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) source_id?: string
  @text({ optional: true, max: 8000 }) source_name?: string
  @text({ optional: true, max: 8000 }) source_type?: string
  @text({ optional: true, max: 8000 }) provider?: string
  @text({ optional: true, max: 8000 }) license?: string
  @text({ optional: true, max: 8000 }) endpoint?: string
  @boolean({ optional: true }) active?: boolean
  @date({ optional: true }) updated_at_utc?: Date
}

@entity()
@authenticated('read')
export class WeatherIngestionRuns extends Source({ schema: 'dbo', table: 'weather_ingestion_runs', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) run_id?: string
  @text({ optional: true, max: 8000 }) source_id?: string
  @text({ optional: true, max: 8000 }) source_item_id?: string
  @text({ optional: true, max: 8000 }) data_kind?: string
  @date({ optional: true }) reference_time_utc?: Date
  @date({ optional: true }) started_at_utc?: Date
  @date({ optional: true }) completed_at_utc?: Date
  @text({ optional: true, max: 8000 }) status?: string
  @int({ optional: true }) row_count?: number
  @text({ optional: true, max: 8000 }) error_message?: string
}

@entity()
@authenticated('read')
export class InstrumentClassification extends Source({ schema: 'dbo', table: 'gold_instrument_classification', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true, max: 8000 }) system_id?: string
  @text({ optional: true, max: 8000 }) signal_type?: string
  @int({ optional: true }) signal_count?: number
  @text({ optional: true, max: 8000 }) ontology_key?: string
}

export const connectorConfig = {
  connector: 'fabric-sqlanalytics',
  operations: ['read'],
  entities: { SilverFacilities, SilverSystems, SilverEquipment, SilverInstruments,
    WeatherLocations, WeatherAreas, WeatherVariables, WeatherLatestObservations, WeatherLatestForecasts,
    SilverSignalMaster, OntologyEquipmentTypes, WeatherSources, WeatherIngestionRuns, InstrumentClassification },
} as const satisfies ConnectorConfig

export type HydrolakehouseSchema = GraphQLBackedConnector<typeof connectorConfig.entities, typeof connectorConfig>
