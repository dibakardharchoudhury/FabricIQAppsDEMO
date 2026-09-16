# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse_name": "",
# META       "default_lakehouse_workspace_id": "",
# META       "known_lakehouses": []
# META     }
# META   }
# META }

# MARKDOWN ********************

# # Weather Lakehouse schema
#
# Attach a Fabric Lakehouse before running. This notebook creates idempotent,
# source-neutral Delta tables for observations, forecasts, locations, enclosed
# areas, ingestion audit, and area metrics. It stores no credentials.

# PARAMETERS CELL ********************

table_prefix = "weather"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

TABLES = {
    name: f"{table_prefix}_{name}"
    for name in [
        "sources",
        "variables",
        "locations",
        "areas",
        "ingestion_runs",
        "observations",
        "forecasts",
        "area_metrics",
    ]
}

ddl_statements = [
    f"""CREATE TABLE IF NOT EXISTS {TABLES['sources']} (
      source_id STRING, source_name STRING, source_type STRING, provider STRING,
      license STRING, endpoint STRING, active BOOLEAN, updated_at_utc TIMESTAMP
    ) USING DELTA""",
    f"""CREATE TABLE IF NOT EXISTS {TABLES['variables']} (
      variable_id STRING, canonical_name STRING, canonical_unit STRING,
      aggregation_kind STRING, description STRING, updated_at_utc TIMESTAMP
    ) USING DELTA""",
    f"""CREATE TABLE IF NOT EXISTS {TABLES['locations']} (
      location_id STRING, location_name STRING, latitude DOUBLE, longitude DOUBLE,
      elevation_m DOUBLE, metadata_json STRING, updated_at_utc TIMESTAMP
    ) USING DELTA""",
    f"""CREATE TABLE IF NOT EXISTS {TABLES['areas']} (
      area_id STRING, area_name STRING, geometry_geojson STRING, crs STRING,
      metadata_json STRING, updated_at_utc TIMESTAMP
    ) USING DELTA""",
    f"""CREATE TABLE IF NOT EXISTS {TABLES['ingestion_runs']} (
      run_id STRING, source_id STRING, source_item_id STRING, data_kind STRING,
      reference_time_utc TIMESTAMP, started_at_utc TIMESTAMP,
      completed_at_utc TIMESTAMP, status STRING, row_count BIGINT, error_message STRING
    ) USING DELTA""",
    f"""CREATE TABLE IF NOT EXISTS {TABLES['observations']} (
      source_id STRING, variable_id STRING, location_id STRING,
      latitude DOUBLE, longitude DOUBLE, observed_at_utc TIMESTAMP,
      observed_date DATE, value DOUBLE, unit STRING, quality STRING,
      source_item_id STRING, run_id STRING, ingested_at_utc TIMESTAMP
    ) USING DELTA PARTITIONED BY (observed_date)""",
    f"""CREATE TABLE IF NOT EXISTS {TABLES['forecasts']} (
      source_id STRING, variable_id STRING, location_id STRING,
      latitude DOUBLE, longitude DOUBLE, reference_time_utc TIMESTAMP,
      valid_time_utc TIMESTAMP, valid_date DATE, lead_hours INT,
      value DOUBLE, unit STRING, ensemble_member STRING,
      source_item_id STRING, run_id STRING, ingested_at_utc TIMESTAMP
    ) USING DELTA PARTITIONED BY (valid_date)""",
    f"""CREATE TABLE IF NOT EXISTS {TABLES['area_metrics']} (
      source_id STRING, variable_id STRING, area_id STRING, data_kind STRING,
      forecast_type STRING,
      reference_time_utc TIMESTAMP, valid_time_utc TIMESTAMP, lead_hours INT,
      area_coverage_fraction DOUBLE, area_weighted_value DOUBLE, unit STRING,
      rainfall_volume_m3 DOUBLE, contributing_cell_count INT,
      aggregation_method STRING, source_item_id STRING, run_id STRING,
      calculated_at_utc TIMESTAMP
    ) USING DELTA""",
]

for ddl in ddl_statements:
    spark.sql(ddl)

if "forecast_type" not in spark.table(TABLES["area_metrics"]).columns:
    spark.sql(f"ALTER TABLE {TABLES['area_metrics']} ADD COLUMNS (forecast_type STRING)")

print(f"Created or verified {len(ddl_statements)} weather tables")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

source_rows = [
  (
    "aurora",
    "Aurora 1.5",
    "forecast",
    "Microsoft AI",
    "CC-BY-4.0",
    "https://mai-weather-api.azure-api.net/stac/collections/aurora-1p5-zarr-staging",
    True,
  ),
  (
    "gridhd",
    "GridHD",
    "observation_or_forecast",
    "Microsoft AI",
    None,
    "https://mai-weather-api.azure-api.net/stac/collections/mai-gridhd-eu-core-v1.2",
    True,
  ),
  (
    "ukmet",
    "UK Met Office Weather DataHub",
    "forecast_and_observation",
    "UK Met Office",
    "Weather DataHub terms",
    "https://data.hub.api.metoffice.gov.uk",
    True,
  ),
]
variable_rows = [
    ("precipitation", "precipitation", "mm", "sum", "Precipitation depth over the source interval"),
  ("pressure", "surface_air_pressure", "hPa", "mean", "Surface pressure at terrain elevation"),
    ("temperature", "air_temperature", "degC", "mean", "Near-surface air temperature"),
  ("relative_humidity", "relative_humidity", "%", "mean", "Relative humidity derived from temperature and dew point"),
  ("dew_point", "dew_point_temperature", "degC", "mean", "Near-surface dew point temperature"),
  ("solar_radiation", "surface_solar_radiation", "W/m2", "mean", "Average downward solar irradiance over the source interval"),
    ("wind_speed", "wind_speed", "m/s", "mean", "Near-surface wind speed"),
  ("wind_gust", "wind_gust", "m/s", "max", "Maximum near-surface wind gust over the source interval"),
  ("wind_direction", "wind_from_direction", "degree", "circular_mean", "Meteorological direction from which wind originates"),
]

spark.createDataFrame(
    source_rows,
    "source_id string, source_name string, source_type string, provider string, license string, endpoint string, active boolean",
).selectExpr("*", "current_timestamp() AS updated_at_utc").createOrReplaceTempView(
    "incoming_weather_sources"
)
spark.createDataFrame(
    variable_rows,
    "variable_id string, canonical_name string, canonical_unit string, aggregation_kind string, description string",
).selectExpr("*", "current_timestamp() AS updated_at_utc").createOrReplaceTempView(
    "incoming_weather_variables"
)

spark.sql(f"""
MERGE INTO {TABLES['sources']} target
USING incoming_weather_sources source ON target.source_id = source.source_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")
spark.sql(f"""
MERGE INTO {TABLES['variables']} target
USING incoming_weather_variables source ON target.variable_id = source.variable_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Idempotency keys
#
# - Observations: source, variable, location, observed time.
# - Forecasts: source, variable, location, issue time, valid time, ensemble member.
# - Area metrics: source, variable, area, data kind, forecast type, issue time, valid time.

# CELL ********************

display(spark.sql(f"SHOW TABLES LIKE '{table_prefix}_*'"))
for table_name in (TABLES["sources"], TABLES["variables"]):
    print(table_name, spark.table(table_name).count())

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }