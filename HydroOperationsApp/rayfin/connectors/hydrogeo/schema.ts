import { authenticated, decimal, entity, int, text } from '@microsoft/rayfin-core'
import { Source, type ConnectorConfig } from '@microsoft/rayfin-connectors'
import type { GraphQLBackedConnector } from '@microsoft/rayfin-connector-fabric-graphql'
import type { EntityClass } from '@microsoft/rayfin-core/schema'

@entity()
@authenticated('read')
export class GeoMapFeatures extends Source({ schema: 'dbo', table: 'geo_map_features', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true }) feature_id?: string
  @text({ optional: true }) source_id?: string
  @text({ optional: true }) layer_id?: string
  @text({ optional: true }) label?: string
  // Unbounded source JSON must not be converted to VARCHAR(8000).
  @text({ optional: true }) geometry_json?: string
  @text({ optional: true }) properties_json?: string
  @decimal({ optional: true }) min_lon?: number
  @decimal({ optional: true }) min_lat?: number
  @decimal({ optional: true }) max_lon?: number
  @decimal({ optional: true }) max_lat?: number
  @text({ optional: true }) observed_at?: string
  @text({ optional: true }) ingested_at?: string
  @text({ optional: true }) source_url?: string
  @text({ optional: true }) run_id?: string
}

@entity()
@authenticated('read')
export class GeoReservoirAreas extends Source({ schema: 'dbo', table: 'geo_reservoir_areas', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true }) feature_id?: string
  @text({ optional: true }) source_id?: string
  @text({ optional: true }) layer_id?: string
  @text({ optional: true }) label?: string
  @text({ optional: true }) geometry_json?: string
  @text({ optional: true }) properties_json?: string
  @decimal({ optional: true }) min_lon?: number
  @decimal({ optional: true }) min_lat?: number
  @decimal({ optional: true }) max_lon?: number
  @decimal({ optional: true }) max_lat?: number
  @text({ optional: true }) observed_at?: string
  @text({ optional: true }) ingested_at?: string
  @text({ optional: true }) source_url?: string
  @text({ optional: true }) run_id?: string
}

@entity()
@authenticated('read')
export class GeoSourceStatus extends Source({ schema: 'dbo', table: 'geo_source_status', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true }) layer_id?: string
  @text({ optional: true }) source_id?: string
  @text({ optional: true }) state?: string
  @text({ optional: true }) last_attempt_at?: string
  @text({ optional: true }) last_success_at?: string
  @int({ optional: true }) row_count?: number
  @int({ optional: true }) unmapped_count?: number
  @int({ optional: true }) rejected_count?: number
  @text({ optional: true }) message?: string
  @text({ optional: true }) source_url?: string
}

@entity()
@authenticated('read')
export class GeoMarketAssetLinks extends Source({ schema: 'dbo', table: 'geo_market_asset_links', primaryKey: [] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @text({ optional: true }) message_feature_id?: string
  @int({ optional: true }) message_version?: number
  @text({ optional: true }) asset_feature_id?: string
  @text({ optional: true }) asset_layer_id?: string
  @text({ optional: true }) match_method?: string
  @text({ optional: true }) match_evidence_json?: string
  @text({ optional: true }) linked_at?: string
  @text({ optional: true }) run_id?: string
}

export const connectorConfig = {
  connector: 'fabric-sqlanalytics', operations: ['read'],
  entities: { GeoMapFeatures, GeoReservoirAreas, GeoSourceStatus, GeoMarketAssetLinks },
} as const satisfies ConnectorConfig

export type HydrogeoSchema = GraphQLBackedConnector<typeof connectorConfig.entities, typeof connectorConfig>
