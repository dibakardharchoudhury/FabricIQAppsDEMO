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

# # Calculate weather area metrics
#
# Builds area aggregates from the canonical forecast table after every vendor has
# ingested. Results remain separate by source, variable, issue, and forecast type.
# Area metadata must identify its representative location with `location_id`.
#
# Rainfall cumulatives are computed per vendor and per issue, so a scheduled run
# restarts the running total for its own forecast rather than extending the previous one.
#
# This notebook also enforces retention on the long tables, rebuilds the wide serving
# projections that applications read, and reports vendor freshness.

# PARAMETERS CELL ********************

table_prefix = "weather"
retention_days = 7
observed_window_hours = 24
# One missed run on the six-hour schedule is 12 h, so warn a little beyond that.
staleness_hours = 13

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from pyspark.sql import Window, functions as F

TABLES = {
    name: f"{table_prefix}_{name}"
    for name in [
        "areas",
        "forecasts",
        "observations",
        "ingestion_runs",
        "area_metrics",
        "latest_forecasts",
        "latest_observations",
    ]
}

for table_name in TABLES.values():
    if not spark.catalog.tableExists(table_name):
        raise RuntimeError(
            f"Weather table not found after Weather_001/002/003; cannot calculate areas: {table_name}"
        )

retention_days = int(retention_days)
observed_window_hours = int(observed_window_hours)
staleness_hours = int(staleness_hours)
if min(retention_days, observed_window_hours, staleness_hours) < 1:
    raise ValueError("Retention, observed window, and staleness horizons must be positive")

if "interval_hours" not in spark.table(TABLES["forecasts"]).columns:
    raise RuntimeError(
        f"{TABLES['forecasts']} has no interval_hours column; rerun Weather_001 to upgrade the schema"
    )

# Columns added after the first release; existing lakehouses are upgraded in place.
metrics_columns = set(spark.table(TABLES["area_metrics"]).columns)
missing_metrics_columns = [
    f"{name} {sql_type}"
    for name, sql_type in {
        "forecast_type": "STRING",
        "interval_hours": "INT",
        "cumulative_value": "DOUBLE",
        "cumulative_rainfall_volume_m3": "DOUBLE",
    }.items()
    if name not in metrics_columns
]
if missing_metrics_columns:
    spark.sql(f"ALTER TABLE {TABLES['area_metrics']} ADD COLUMNS ({', '.join(missing_metrics_columns)})")

# Retention runs before the rebuild so the aggregation only ever scans the retained window.
spark.sql(
    f"DELETE FROM {TABLES['forecasts']} "
    f"WHERE reference_time_utc < current_timestamp() - INTERVAL {retention_days} DAYS"
)
spark.sql(
    f"DELETE FROM {TABLES['observations']} "
    f"WHERE observed_at_utc < current_timestamp() - INTERVAL {retention_days} DAYS"
)
spark.sql(
    f"DELETE FROM {TABLES['ingestion_runs']} "
    f"WHERE started_at_utc < current_timestamp() - INTERVAL {retention_days} DAYS"
)

area_locations = (
    spark.table(TABLES["areas"])
    .select(
        "area_id",
        F.get_json_object("metadata_json", "$.location_id").alias("location_id"),
        F.get_json_object("metadata_json", "$.geodesic_area_m2").cast("double").alias("area_m2"),
    )
)
if area_locations.filter(F.col("location_id").isNull() | F.col("area_m2").isNull()).limit(1).count():
    raise RuntimeError("Every weather area must include location_id and geodesic_area_m2 metadata")

forecasts = (
    spark.table(TABLES["forecasts"])
    .join(area_locations, "location_id", "inner")
    .withColumn("forecast_type", F.coalesce("ensemble_member", F.lit("deterministic")))
    .withColumn("interval_hours", F.coalesce("interval_hours", F.lit(0)))
)
if forecasts.limit(1).count() == 0:
    raise RuntimeError("No weather forecasts are available for area calculation")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

group_columns = [
    "source_id",
    "variable_id",
    "area_id",
    "forecast_type",
    "reference_time_utc",
    "valid_time_utc",
    "lead_hours",
    "interval_hours",
    "unit",
    "area_m2",
]

scalar_metrics = (
    forecasts.filter(F.col("variable_id") != "wind_direction")
    .groupBy(*group_columns)
    .agg(
        F.when(
            F.col("variable_id") == "wind_gust",
            F.max("value"),
        ).otherwise(F.avg("value")).alias("area_weighted_value"),
        F.concat_ws(",", F.sort_array(F.collect_set("source_item_id"))).alias("source_item_id"),
        F.concat_ws(",", F.sort_array(F.collect_set("run_id"))).alias("run_id"),
    )
    .withColumn(
        "aggregation_method",
        F.when(F.col("variable_id") == "wind_gust", F.lit("representative_location_max"))
        .otherwise(F.lit("representative_location_mean")),
    )
)

direction_metrics = (
    forecasts.filter(F.col("variable_id") == "wind_direction")
    .withColumn("direction_radians", F.radians("value"))
    .groupBy(*group_columns)
    .agg(
        F.degrees(F.atan2(F.avg(F.sin("direction_radians")), F.avg(F.cos("direction_radians"))))
        .alias("signed_direction"),
        F.concat_ws(",", F.sort_array(F.collect_set("source_item_id"))).alias("source_item_id"),
        F.concat_ws(",", F.sort_array(F.collect_set("run_id"))).alias("run_id"),
    )
    .withColumn("area_weighted_value", F.pmod(F.col("signed_direction"), F.lit(360.0)))
    .drop("signed_direction")
    .withColumn("aggregation_method", F.lit("representative_location_circular_mean"))
)

# Running totals are scoped to one vendor issue, so Aurora and UKMet are never combined
# and a new scheduled run restarts the accumulation instead of extending the previous one.
issue_window = (
    Window.partitionBy("source_id", "variable_id", "area_id", "forecast_type", "reference_time_utc")
    .orderBy("valid_time_utc")
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)

area_metrics = (
    scalar_metrics.unionByName(direction_metrics)
    .withColumn("data_kind", F.lit("forecast"))
    # This is a representative-point calculation, so real areal coverage is unknown.
    .withColumn("area_coverage_fraction", F.lit(None).cast("double"))
    .withColumn("contributing_cell_count", F.lit(None).cast("int"))
    .withColumn(
        "rainfall_volume_m3",
        F.when(
            F.col("variable_id") == "precipitation",
            F.col("area_weighted_value") / F.lit(1000.0) * F.col("area_m2"),
        ).cast("double"),
    )
    .withColumn(
        "cumulative_value",
        F.when(
            F.col("variable_id") == "precipitation",
            F.sum("area_weighted_value").over(issue_window),
        ).cast("double"),
    )
    .withColumn(
        "cumulative_rainfall_volume_m3",
        F.when(
            F.col("variable_id") == "precipitation",
            F.sum("rainfall_volume_m3").over(issue_window),
        ).cast("double"),
    )
    .withColumn("calculated_at_utc", F.current_timestamp())
    .select(
        "source_id",
        "variable_id",
        "area_id",
        "data_kind",
        "forecast_type",
        "reference_time_utc",
        "valid_time_utc",
        "lead_hours",
        "interval_hours",
        "area_coverage_fraction",
        "area_weighted_value",
        "unit",
        "rainfall_volume_m3",
        "cumulative_value",
        "cumulative_rainfall_volume_m3",
        "contributing_cell_count",
        "aggregation_method",
        "source_item_id",
        "run_id",
        "calculated_at_utc",
    )
)

(
    area_metrics.write.mode("overwrite")
    .option("replaceWhere", "data_kind = 'forecast'")
    .saveAsTable(TABLES["area_metrics"])
)

# Rainfall only tiles a window when interval_hours matches the spacing between valid times.
coverage = (
    spark.table(TABLES["area_metrics"])
    .filter((F.col("data_kind") == "forecast") & (F.col("variable_id") == "precipitation"))
    .groupBy("source_id")
    .agg(
        F.min("interval_hours").alias("min_interval_hours"),
        F.max("interval_hours").alias("max_interval_hours"),
        F.min("lead_hours").alias("min_lead_hours"),
        F.max("lead_hours").alias("max_lead_hours"),
        F.max("cumulative_value").alias("max_cumulative_mm"),
    )
)
display(coverage)
display(area_metrics.orderBy("source_id", "forecast_type", "area_id", "valid_time_utc"))
print(f"Calculated {area_metrics.count()} area metrics for {area_metrics.select('source_id').distinct().count()} vendors")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Serving projections
#
# Applications need one coherent forecast, not the full issue history. These tables hold
# only the newest issue per vendor, pivoted so a single row carries every variable. That is
# roughly a hundredth of the long-table row count, while silver keeps the source-neutral
# shape that lets a new vendor add variables without a schema change.

# CELL ********************

SERVING_VARIABLES = [
    "precipitation", "temperature", "pressure", "relative_humidity", "dew_point",
    "solar_radiation", "wind_speed", "wind_gust", "wind_direction",
]


def latest_issue_only(frame):
    """Keep the newest issue per vendor so cumulatives stay valid within one forecast."""
    newest = frame.groupBy("source_id").agg(F.max("reference_time_utc").alias("reference_time_utc"))
    return frame.join(newest, ["source_id", "reference_time_utc"], "inner")


def widen(frame, target_kind, target_column, value_column, extra_columns):
    keys = ["source_id", "reference_time_utc", "valid_time_utc", "lead_hours", target_column]
    wide = (
        frame.groupBy(*keys)
        .pivot("variable_id", SERVING_VARIABLES)
        .agg(F.first(value_column, ignorenulls=True))
    )
    precipitation = frame.filter(F.col("variable_id") == "precipitation").select(*keys, *extra_columns)
    return (
        wide.join(precipitation, keys, "left")
        .withColumn("target_kind", F.lit(target_kind))
        .withColumnRenamed(target_column, "target_id")
    )


location_window = (
    Window.partitionBy("source_id", "variable_id", "location_id", "reference_time_utc")
    .orderBy("valid_time_utc")
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)
location_latest = (
    latest_issue_only(spark.table(TABLES["forecasts"]))
    .withColumn("precipitation_interval_hours", F.coalesce("interval_hours", F.lit(0)))
    .withColumn(
        "cumulative_precipitation",
        F.when(
            F.col("variable_id") == "precipitation",
            F.sum("value").over(location_window),
        ).cast("double"),
    )
)
location_wide = widen(
    location_latest,
    "location",
    "location_id",
    "value",
    ["precipitation_interval_hours", "cumulative_precipitation"],
)

area_latest = (
    latest_issue_only(spark.table(TABLES["area_metrics"]).filter(F.col("data_kind") == "forecast"))
    .withColumn("precipitation_interval_hours", F.coalesce("interval_hours", F.lit(0)))
    .withColumnRenamed("cumulative_value", "cumulative_precipitation")
)
area_wide = widen(
    area_latest,
    "area",
    "area_id",
    "area_weighted_value",
    [
        "precipitation_interval_hours",
        "cumulative_precipitation",
        "rainfall_volume_m3",
        "cumulative_rainfall_volume_m3",
    ],
)

serving = (
    location_wide.unionByName(area_wide, allowMissingColumns=True)
    .withColumn("calculated_at_utc", F.current_timestamp())
    .select(
        "source_id", "target_kind", "target_id", "reference_time_utc", "valid_time_utc",
        "lead_hours", "precipitation_interval_hours", "cumulative_precipitation",
        "rainfall_volume_m3", "cumulative_rainfall_volume_m3",
        *SERVING_VARIABLES, "calculated_at_utc",
    )
)
serving.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLES["latest_forecasts"])

observed_serving = (
    spark.table(TABLES["observations"])
    .filter(F.col("observed_at_utc") >= F.current_timestamp() - F.expr(f"INTERVAL {observed_window_hours} HOURS"))
    .groupBy("source_id", "location_id", "observed_at_utc")
    .pivot("variable_id", SERVING_VARIABLES)
    .agg(F.first("value", ignorenulls=True))
    .withColumn("calculated_at_utc", F.current_timestamp())
)
observed_serving.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLES["latest_observations"])

print(
    f"Serving projections rebuilt: {serving.count()} forecast rows, "
    f"{observed_serving.count()} observed rows"
)
display(serving.orderBy("source_id", "target_kind", "target_id", "valid_time_utc"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# The pipeline continues past a failed vendor, so freshness is the only signal that one stopped.
health = (
    spark.table(TABLES["ingestion_runs"])
    .groupBy("source_id")
    .agg(
        F.max(F.when(F.col("status") == "succeeded", F.col("completed_at_utc"))).alias("last_success_utc"),
        F.sum(F.when(F.col("status") == "started", 1).otherwise(0)).cast("int").alias("incomplete_runs"),
    )
)
display(health)

stale = health.filter(
    F.col("last_success_utc").isNull()
    | (F.col("last_success_utc") < F.current_timestamp() - F.expr(f"INTERVAL {staleness_hours} HOURS"))
).collect()
for row in stale:
    print(
        f"WARNING: vendor '{row['source_id']}' has no successful run in the last "
        f"{staleness_hours}h (last success: {row['last_success_utc']})"
    )

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }