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

# PARAMETERS CELL ********************

table_prefix = "weather"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from pyspark.sql import functions as F

TABLES = {
    name: f"{table_prefix}_{name}"
    for name in ["areas", "forecasts", "area_metrics"]
}

for table_name in TABLES.values():
    if not spark.catalog.tableExists(table_name):
        raise RuntimeError(
            f"Weather table not found after Weather_001/002/003; cannot calculate areas: {table_name}"
        )

if "forecast_type" not in spark.table(TABLES["area_metrics"]).columns:
    spark.sql(f"ALTER TABLE {TABLES['area_metrics']} ADD COLUMNS (forecast_type STRING)")

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
        F.countDistinct("location_id").cast("int").alias("contributing_cell_count"),
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
        F.countDistinct("location_id").cast("int").alias("contributing_cell_count"),
        F.concat_ws(",", F.sort_array(F.collect_set("source_item_id"))).alias("source_item_id"),
        F.concat_ws(",", F.sort_array(F.collect_set("run_id"))).alias("run_id"),
    )
    .withColumn("area_weighted_value", F.pmod(F.col("signed_direction"), F.lit(360.0)))
    .drop("signed_direction")
    .withColumn("aggregation_method", F.lit("representative_location_circular_mean"))
)

area_metrics = (
    scalar_metrics.unionByName(direction_metrics)
    .withColumn("data_kind", F.lit("forecast"))
    .withColumn("area_coverage_fraction", F.lit(1.0))
    .withColumn(
        "rainfall_volume_m3",
        F.when(
            F.col("variable_id") == "precipitation",
            F.col("area_weighted_value") / F.lit(1000.0) * F.col("area_m2"),
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
        "area_coverage_fraction",
        "area_weighted_value",
        "unit",
        "rainfall_volume_m3",
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

display(area_metrics.orderBy("source_id", "forecast_type", "area_id", "valid_time_utc"))
print(f"Calculated {area_metrics.count()} area metrics for {area_metrics.select('source_id').distinct().count()} vendors")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }