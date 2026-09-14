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

# # Fetch UKMet forecasts and observations
#
# Reads Global Spot hourly forecasts and recent Land Observations from the UK
# Met Office Weather DataHub. Both products are normalized into the source-neutral
# Weather Lakehouse tables created by Weather_001.
#
# The API key value is read from Azure Key Vault at runtime. Only its secret name
# is configured here; no credential value is stored in this notebook.

# PARAMETERS CELL ********************

import json

table_prefix = "weather"
global_spot_endpoint = "https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point/hourly"
land_observations_endpoint = "https://data.hub.api.metoffice.gov.uk/observation-land/1"
key_vault_uri = ""
global_spot_api_key_secret_name = "ukmet-global-spot-api-key"
land_observations_api_key_secret_name = "ukmet-land-observations-api-key"

facilities_table = "silver_facilities"
equipment_table = "silver_equipment"
require_active_equipment = True
facility_ids_json = "[]"
max_lead_hours = 72
forecast_interval_hours = 6
observation_lookback_hours = 24

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from uuid import uuid4

import notebookutils
import pandas as pd
import requests
from pyspark.sql import functions as F
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SOURCE_ID = "ukmet"
TABLES = {
    name: f"{table_prefix}_{name}"
    for name in ["locations", "ingestion_runs", "observations", "forecasts"]
}
FORECAST_FIELDS = {
    "precipitation": (("totalPrecipAmount", "precipitationAmount"), "mm"),
    "pressure": (("mslp",), "hPa"),
    "temperature": (("screenTemperature",), "degC"),
    "relative_humidity": (("screenRelativeHumidity",), "%"),
    "dew_point": (("screenDewPointTemperature",), "degC"),
    "wind_speed": (("windSpeed10m",), "m/s"),
    "wind_gust": (("max10mWindGust", "windGustSpeed10m"), "m/s"),
    "wind_direction": (("windDirectionFrom10m",), "degree"),
}
OBSERVATION_FIELDS = {
    "pressure": ("mslp", "hPa"),
    "temperature": ("temperature", "degC"),
    "relative_humidity": ("humidity", "%"),
    "wind_speed": ("wind_speed", "m/s"),
    "wind_gust": ("wind_gust", "m/s"),
    "wind_direction": ("wind_direction", "degree"),
}
COMPASS_DEGREES = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "SWW": 247.5, "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}


def retry_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session: requests.Session, url: str, api_key: str, params=None):
    response = session.get(
        url,
        headers={"accept": "application/json", "apikey": api_key},
        params=params,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def utc_datetime(value: str) -> datetime:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC").to_pydatetime()


def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) and number not in (float("inf"), float("-inf")) else None


def field_value(record: dict, names: tuple[str, ...]):
    for name in names:
        value = finite_number(record.get(name))
        if value is not None:
            return value
    return None


def time_series(payload: dict) -> list[dict]:
    features = payload.get("features") or []
    if not features:
        raise RuntimeError("Global Spot returned no forecast features")
    series = (features[0].get("properties") or {}).get("timeSeries")
    if isinstance(series, list):
        return series
    if isinstance(series, dict):
        return [
            {"time": timestamp, **values}
            for timestamp, values in series.items()
            if isinstance(values, dict)
        ]
    raise RuntimeError("Global Spot response has no usable timeSeries")


def write_raw(kind: str, location_id: str, run_id: str, payload) -> None:
    safe_location = "".join(char if char.isalnum() or char in "-_" else "_" for char in location_id)
    path = PurePosixPath("Files/weather/bronze/ukmet") / kind / run_id / f"{safe_location}.json"
    notebookutils.fs.put(str(path), json.dumps(payload, indent=2), True)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

def load_station_points(selected_ids: list[str], active_only: bool) -> list[dict]:
    if not spark.catalog.tableExists(facilities_table):
        raise RuntimeError(f"Station inventory table not found: {facilities_table}")

    facilities = spark.table(facilities_table).select(
        F.col("facility_id").cast("string").alias("location_id"),
        F.coalesce(F.col("facility_name"), F.col("facility_id")).cast("string").alias("location_name"),
        F.col("lat").cast("double").alias("latitude"),
        F.col("lon").cast("double").alias("longitude"),
    )
    if active_only:
        if not spark.catalog.tableExists(equipment_table):
            raise RuntimeError(f"Equipment inventory table not found: {equipment_table}")
        equipment = spark.table(equipment_table)
        if "is_active" in equipment.columns:
            equipment = equipment.filter(F.col("is_active") == F.lit(True))
        elif "status" in equipment.columns:
            equipment = equipment.filter(F.upper(F.col("status")) == F.lit("ACTIVE"))
        else:
            raise RuntimeError(f"{equipment_table} has neither is_active nor status")
        facilities = facilities.join(
            equipment.select(F.col("facility_id").cast("string").alias("location_id")).distinct(),
            "location_id",
            "inner",
        )
    if selected_ids:
        facilities = facilities.filter(F.col("location_id").isin(selected_ids))

    invalid = facilities.filter(
        F.col("location_id").isNull()
        | F.col("latitude").isNull()
        | F.col("longitude").isNull()
        | ~F.col("latitude").between(-85.0, 85.0)
        | ~F.col("longitude").between(-180.0, 180.0)
    )
    if invalid.limit(1).count():
        raise ValueError(f"Invalid station inventory coordinates: {[row.asDict() for row in invalid.limit(10).collect()]}")

    points = [row.asDict() for row in facilities.dropDuplicates(["location_id"]).orderBy("location_id").collect()]
    if not points:
        raise RuntimeError("Station inventory contains no eligible UKMet locations")
    return points


if not key_vault_uri.strip():
    vault_rows = (
        spark.table("rti_demo_settings")
        .filter(F.col("setting_name") == F.lit("key_vault_uri"))
        .select("setting_value")
        .limit(2)
        .collect()
    )
    if len(vault_rows) != 1 or not str(vault_rows[0]["setting_value"]).strip():
        raise ValueError("rti_demo_settings must contain one non-empty key_vault_uri setting")
    key_vault_uri = str(vault_rows[0]["setting_value"]).strip()

selected_facility_ids = json.loads(facility_ids_json)
if not isinstance(selected_facility_ids, list) or not all(isinstance(value, str) for value in selected_facility_ids):
    raise ValueError("facility_ids_json must be a JSON array of facility ID strings")
if forecast_interval_hours < 1 or max_lead_hours < 0 or observation_lookback_hours < 1:
    raise ValueError("Weather horizons and intervals must be positive")

for table_name in TABLES.values():
    if not spark.catalog.tableExists(table_name):
        raise RuntimeError(f"Weather table not found; run Weather_001 first: {table_name}")

points = load_station_points(selected_facility_ids, require_active_equipment)
global_spot_api_key = notebookutils.credentials.getSecret(key_vault_uri, global_spot_api_key_secret_name)
land_observations_api_key = notebookutils.credentials.getSecret(
    key_vault_uri,
    land_observations_api_key_secret_name,
)
session = retry_session()
run_id = str(uuid4())
started_at = datetime.now(timezone.utc)
observation_cutoff = started_at - timedelta(hours=observation_lookback_hours)
location_rows = []
forecast_rows = []
observation_rows = []
reference_times = []

print(f"Run {run_id}: loading UKMet data for {len(points)} facilities")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

for point in points:
    location_rows.append({
        "location_id": point["location_id"],
        "location_name": point["location_name"],
        "latitude": float(point["latitude"]),
        "longitude": float(point["longitude"]),
        "elevation_m": None,
        "metadata_json": json.dumps({"selection_method": "facility_inventory_coordinate"}),
        "updated_at_utc": started_at,
    })

    forecast_payload = get_json(
        session,
        global_spot_endpoint,
        global_spot_api_key,
        {
            "latitude": float(point["latitude"]),
            "longitude": float(point["longitude"]),
            "excludeParameterMetadata": "true",
            "includeLocationName": "true",
        },
    )
    write_raw("global-spot", point["location_id"], run_id, forecast_payload)
    feature = (forecast_payload.get("features") or [{}])[0]
    properties = feature.get("properties") or {}
    reference_time = utc_datetime(properties["modelRunDate"])
    reference_times.append(reference_time)
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    forecast_longitude = finite_number(coordinates[0]) if len(coordinates) > 1 else float(point["longitude"])
    forecast_latitude = finite_number(coordinates[1]) if len(coordinates) > 1 else float(point["latitude"])

    for record in time_series(forecast_payload):
        valid_text = record.get("time")
        if not valid_text:
            continue
        valid_time = utc_datetime(valid_text)
        lead_hours = int(round((valid_time - reference_time).total_seconds() / 3600.0))
        if lead_hours < 0 or lead_hours > max_lead_hours or lead_hours % forecast_interval_hours:
            continue
        values = record.get("data") if isinstance(record.get("data"), dict) else record
        for variable_id, (aliases, unit) in FORECAST_FIELDS.items():
            value = field_value(values, aliases)
            if value is None:
                continue
            forecast_rows.append({
                "source_id": SOURCE_ID,
                "variable_id": variable_id,
                "location_id": point["location_id"],
                "latitude": forecast_latitude,
                "longitude": forecast_longitude,
                "reference_time_utc": reference_time,
                "valid_time_utc": valid_time,
                "valid_date": valid_time.date(),
                "lead_hours": lead_hours,
                "value": value,
                "unit": unit,
                "ensemble_member": "deterministic",
                "source_item_id": f"{point['location_id']}:{reference_time.isoformat()}",
                "run_id": run_id,
                "ingested_at_utc": started_at,
            })

    nearest_payload = get_json(
        session,
        f"{land_observations_endpoint}/nearest",
        land_observations_api_key,
        {"lat": round(float(point["latitude"]), 2), "lon": round(float(point["longitude"]), 2), "max": 1},
    )
    if not isinstance(nearest_payload, list) or not nearest_payload or not nearest_payload[0].get("geohash"):
        raise RuntimeError(f"No UKMet Land Observation station found for {point['location_id']}")
    geohash = str(nearest_payload[0]["geohash"])
    observations_payload = get_json(
        session,
        f"{land_observations_endpoint}/{geohash}",
        land_observations_api_key,
    )
    write_raw(
        "land-observations",
        point["location_id"],
        run_id,
        {"nearest": nearest_payload[0], "observations": observations_payload},
    )
    if not isinstance(observations_payload, list):
        raise RuntimeError(f"Unexpected Land Observations response for {point['location_id']}")

    for record in observations_payload:
        observed_text = record.get("datetime")
        if not observed_text:
            continue
        observed_at = utc_datetime(observed_text)
        if observed_at < observation_cutoff:
            continue
        for variable_id, (field_name, unit) in OBSERVATION_FIELDS.items():
            raw_value = record.get(field_name)
            value = COMPASS_DEGREES.get(str(raw_value).upper()) if variable_id == "wind_direction" else finite_number(raw_value)
            if value is None:
                continue
            observation_rows.append({
                "source_id": SOURCE_ID,
                "variable_id": variable_id,
                "location_id": point["location_id"],
                "latitude": float(point["latitude"]),
                "longitude": float(point["longitude"]),
                "observed_at_utc": observed_at,
                "observed_date": observed_at.date(),
                "value": value,
                "unit": unit,
                "quality": "reported",
                "source_item_id": geohash,
                "run_id": run_id,
                "ingested_at_utc": started_at,
            })

if not forecast_rows:
    raise RuntimeError("UKMet Global Spot returned no mapped forecast values")
if not observation_rows:
    raise RuntimeError("UKMet Land Observations returned no mapped recent values")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

locations_df = spark.createDataFrame(
    location_rows,
    "location_id string, location_name string, latitude double, longitude double, elevation_m double, metadata_json string, updated_at_utc timestamp",
)
forecasts_df = spark.createDataFrame(
    forecast_rows,
    "source_id string, variable_id string, location_id string, latitude double, longitude double, reference_time_utc timestamp, valid_time_utc timestamp, valid_date date, lead_hours int, value double, unit string, ensemble_member string, source_item_id string, run_id string, ingested_at_utc timestamp",
)
observations_df = spark.createDataFrame(
    observation_rows,
    "source_id string, variable_id string, location_id string, latitude double, longitude double, observed_at_utc timestamp, observed_date date, value double, unit string, quality string, source_item_id string, run_id string, ingested_at_utc timestamp",
)
locations_df.createOrReplaceTempView("incoming_ukmet_locations")
forecasts_df.createOrReplaceTempView("incoming_ukmet_forecasts")
observations_df.createOrReplaceTempView("incoming_ukmet_observations")

spark.sql(f"""
MERGE INTO {TABLES['locations']} target USING incoming_ukmet_locations source
ON target.location_id = source.location_id
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")
spark.sql(f"""
MERGE INTO {TABLES['forecasts']} target USING incoming_ukmet_forecasts source
ON target.source_id = source.source_id
AND target.variable_id = source.variable_id
AND target.location_id = source.location_id
AND target.reference_time_utc = source.reference_time_utc
AND target.valid_time_utc = source.valid_time_utc
AND target.ensemble_member <=> source.ensemble_member
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")
spark.sql(f"""
MERGE INTO {TABLES['observations']} target USING incoming_ukmet_observations source
ON target.source_id = source.source_id
AND target.variable_id = source.variable_id
AND target.location_id = source.location_id
AND target.observed_at_utc = source.observed_at_utc
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")

completed_at = datetime.now(timezone.utc)
reference_time = max(reference_times) if reference_times else None
spark.createDataFrame(
    [{
        "run_id": run_id,
        "source_id": SOURCE_ID,
        "source_item_id": f"global-spot:{reference_time.isoformat() if reference_time else 'unknown'}",
        "data_kind": "forecast_and_observation",
        "reference_time_utc": reference_time,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "status": "succeeded",
        "row_count": len(forecast_rows) + len(observation_rows),
        "error_message": None,
    }],
    "run_id string, source_id string, source_item_id string, data_kind string, reference_time_utc timestamp, started_at_utc timestamp, completed_at_utc timestamp, status string, row_count long, error_message string",
).write.mode("append").saveAsTable(TABLES["ingestion_runs"])

display(forecasts_df.orderBy("location_id", "valid_time_utc").limit(20))
display(observations_df.orderBy(F.desc("observed_at_utc")).limit(20))
print(f"Run {run_id} succeeded: {len(forecast_rows)} forecast rows, {len(observation_rows)} observation rows")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }