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

# # Fetch point and enclosed-area weather
#
# Aurora implementation of the source-adapter contract. It reads the latest signed
# STAC Zarr asset, extracts 72-hour point forecasts, creates operating-area geometry,
# and merges canonical records into the tables created by Weather_001.
#
# Configure a Fabric Environment with `xarray`, `zarr<3`, `adlfs`, `shapely`, and
# `pyproj`. Attach the Weather Lakehouse before running. Store the API key in Key
# Vault; never put it in notebook parameters.

# PARAMETERS CELL ********************

import json

table_prefix = "weather"
endpoint = "https://mai-weather-api.azure-api.net"
api_version = "2025-04-30-preview"
collection = "aurora-1p5-zarr-staging"
asset_key = "data"
max_lead_hours = 72
interval_hours = 6

key_vault_uri = ""  # Empty reads key_vault_uri from rti_demo_settings.
api_key_secret_name = "mai-weather-api-key"

facilities_table = "silver_facilities"
equipment_table = "silver_equipment"
require_active_equipment = True
facility_ids_json = "[]"  # Empty selects all eligible facilities.
aggregation_radius_km = 20.0
aggregation_circle_vertices = 72

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from datetime import timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

import numpy as np
import pandas as pd
import requests
import xarray as xr
from adlfs import AzureBlobFileSystem
import notebookutils
from pyproj import Geod
from requests.adapters import HTTPAdapter
from shapely.geometry import Polygon, mapping
from urllib3.util.retry import Retry

TABLES = {
    name: f"{table_prefix}_{name}"
    for name in ["locations", "areas", "ingestion_runs", "forecasts"]
}
SOURCE_ID = "aurora"
PRECIPITATION_EPSILON_M = 1e-3
GEOD = Geod(ellps="WGS84")
VARIABLES = {
    "pressure": {"unit": "hPa", "aggregation": "mean"},
    "temperature": {"unit": "degC", "aggregation": "mean"},
    "relative_humidity": {"unit": "%", "aggregation": "mean"},
    "dew_point": {"unit": "degC", "aggregation": "mean"},
    "solar_radiation": {"unit": "W/m2", "aggregation": "mean"},
    "wind_speed": {"unit": "m/s", "aggregation": "mean"},
    "wind_gust": {"unit": "m/s", "aggregation": "max"},
    "wind_direction": {"unit": "degree", "aggregation": "circular_mean"},
    "precipitation": {"unit": "mm", "aggregation": "sum"},
}
SOURCE_VARIABLES = {
    "surface_pressure_0",
    "2m_temperature_0",
    "2m_dewpoint_temperature_0",
    "surface_solar_radiation_downwards_1h_0",
    "10m_u_component_of_wind_0",
    "10m_v_component_of_wind_0",
    "instantaneous_10m_wind_gust_0",
    "scaled_total_precipitation_1h_0",
}


def service_base(service_endpoint: str) -> str:
    parsed = urlsplit(service_endpoint)
    prefix = parsed.path.split("/stac", 1)[0].rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{prefix}"


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


def latest_stac_item(service_endpoint: str, api_key: str) -> dict:
    response = retry_session().get(
        f"{service_base(service_endpoint)}/stac/search",
        params={
            "api-version": api_version,
            "key": api_key,
            "collections": collection,
            "limit": 1,
            "sortby": "-datetime",
            "sign": "true",
            "duration": 60,
        },
        timeout=60,
    )
    response.raise_for_status()
    features = response.json().get("features", [])
    if not features:
        raise RuntimeError(f"No STAC items found in {collection}")
    return features[0]


def open_signed_zarr(asset_href: str) -> xr.Dataset:
    parsed = urlsplit(asset_href)
    path_parts = parsed.path.strip("/").split("/", 1)
    if len(path_parts) != 2 or not parsed.query:
        raise ValueError("STAC data asset is not a signed Azure Zarr URL")
    account_name = parsed.netloc.split(".", 1)[0]
    container, zarr_path = path_parts
    filesystem = AzureBlobFileSystem(account_name=account_name, sas_token=parsed.query)
    return xr.open_zarr(
        filesystem.get_mapper(f"{container}/{zarr_path}"),
        consolidated=True,
        decode_timedelta=False,
    )


def lead_hours(values: np.ndarray) -> np.ndarray:
    if np.issubdtype(values.dtype, np.timedelta64):
        return values.astype("timedelta64[h]").astype(int)
    return values.astype(int)


def precipitation_mm(scaled_values):
    values = np.asarray(scaled_values, dtype=float)
    metres = np.exp(values + np.log(PRECIPITATION_EPSILON_M)) - PRECIPITATION_EPSILON_M
    return np.maximum(metres, 0.0) * 1000.0


def relative_humidity_percent(temperature_c, dew_point_c):
    exponent = (
        17.625 * dew_point_c / (243.04 + dew_point_c)
        - 17.625 * temperature_c / (243.04 + temperature_c)
    )
    return np.clip(100.0 * np.exp(exponent), 0.0, 100.0)


def weather_grids(dataset: xr.Dataset, step_index: int) -> dict[str, np.ndarray]:
    def grid(name: str) -> np.ndarray:
        return np.asarray(
            dataset[name].isel(time=0, step=step_index).values,
            dtype=float,
        )

    temperature_c = grid("2m_temperature_0") - 273.15
    dew_point_c = grid("2m_dewpoint_temperature_0") - 273.15
    wind_u = grid("10m_u_component_of_wind_0")
    wind_v = grid("10m_v_component_of_wind_0")
    wind_speed = np.hypot(wind_u, wind_v)
    wind_direction = np.degrees(np.arctan2(-wind_u, -wind_v)) % 360.0
    wind_direction = np.where(wind_speed < 0.1, np.nan, wind_direction)

    return {
        "pressure": grid("surface_pressure_0") / 100.0,
        "temperature": temperature_c,
        "relative_humidity": relative_humidity_percent(temperature_c, dew_point_c),
        "dew_point": dew_point_c,
        "solar_radiation": np.maximum(
            grid("surface_solar_radiation_downwards_1h_0") / 3600.0,
            0.0,
        ),
        "wind_speed": wind_speed,
        "wind_gust": np.maximum(grid("instantaneous_10m_wind_gust_0"), 0.0),
        "wind_direction": wind_direction,
        "precipitation": precipitation_mm(grid("scaled_total_precipitation_1h_0")),
    }


def longitude_180(value):
    return ((np.asarray(value, dtype=float) + 180.0) % 360.0) - 180.0


def nearest_grid_indices(latitudes, longitudes, latitude, longitude):
    lat_index = int(np.argmin(np.abs(latitudes - latitude)))
    distances = np.abs(((longitudes - (longitude % 360.0) + 180.0) % 360.0) - 180.0)
    return lat_index, int(np.argmin(distances))


def geodesic_area_m2(geometry) -> float:
    return abs(float(GEOD.geometry_area_perimeter(geometry)[0]))


def station_aggregation_area(point: dict, radius_km: float, vertices: int) -> dict:
    """Create a geodesic circle centered on one inventory station."""
    if radius_km <= 0 or vertices < 12:
        raise ValueError("Aggregation radius must be positive and circle vertices must be at least 12")
    bearings = np.linspace(0.0, 360.0, vertices, endpoint=False)
    longitudes, latitudes, _ = GEOD.fwd(
        np.full(vertices, float(point["longitude"])),
        np.full(vertices, float(point["latitude"])),
        bearings,
        np.full(vertices, radius_km * 1000.0),
    )
    polygon = Polygon(zip(longitudes, latitudes))
    if not polygon.is_valid:
        raise ValueError(f"Could not create aggregation area for {point['location_id']}")
    return {
        "area_id": f"weather_{point['location_id']}_{radius_km:g}km",
        "area_name": f"{point['location_name']} · {radius_km:g} km radius",
        "location_id": point["location_id"],
        "radius_km": radius_km,
        "geometry": polygon,
        "geometry_json": json.dumps(mapping(polygon), separators=(",", ":")),
    }

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from pyspark.sql import functions as F


def load_station_points(
    facilities_table_name: str,
    equipment_table_name: str,
    selected_facility_ids: list[str] | None = None,
    active_equipment_only: bool = True,
) -> list[dict]:
    """Load validated weather points from the existing station inventory."""
    if not spark.catalog.tableExists(facilities_table_name):
        raise RuntimeError(f"Station inventory table not found: {facilities_table_name}")

    facilities = spark.table(facilities_table_name).select(
        F.col("facility_id").cast("string").alias("location_id"),
        F.coalesce(F.col("facility_name"), F.col("facility_id")).cast("string").alias("location_name"),
        F.col("lat").cast("double").alias("latitude"),
        F.col("lon").cast("double").alias("longitude"),
    )

    if active_equipment_only:
        if not spark.catalog.tableExists(equipment_table_name):
            raise RuntimeError(f"Equipment inventory table not found: {equipment_table_name}")
        equipment = spark.table(equipment_table_name)
        if "is_active" in equipment.columns:
            equipment = equipment.filter(F.col("is_active") == F.lit(True))
        elif "status" in equipment.columns:
            equipment = equipment.filter(F.upper(F.col("status")) == F.lit("ACTIVE"))
        else:
            raise RuntimeError(f"{equipment_table_name} has neither is_active nor status")
        active_facilities = equipment.select(
            F.col("facility_id").cast("string").alias("location_id")
        ).distinct()
        facilities = facilities.join(active_facilities, "location_id", "inner")

    if selected_facility_ids:
        facilities = facilities.filter(F.col("location_id").isin(selected_facility_ids))

    duplicate_ids = facilities.groupBy("location_id").count().filter(F.col("count") > 1)
    if duplicate_ids.limit(1).count():
        sample = [row["location_id"] for row in duplicate_ids.limit(10).collect()]
        raise ValueError(f"Duplicate facility IDs in station inventory: {sample}")

    invalid = facilities.filter(
        F.col("location_id").isNull()
        | F.col("latitude").isNull()
        | F.col("longitude").isNull()
        | ~F.col("latitude").between(-90.0, 90.0)
        | ~F.col("longitude").between(-180.0, 180.0)
    )
    if invalid.limit(1).count():
        sample = [row.asDict() for row in invalid.limit(10).collect()]
        raise ValueError(f"Invalid station inventory coordinates: {sample}")

    points = [row.asDict() for row in facilities.orderBy("location_id").collect()]
    if not points:
        raise RuntimeError("Station inventory contains no eligible weather locations")

    print(f"Loaded {len(points)} weather locations from {facilities_table_name}")
    return points

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

if not key_vault_uri.strip():
    settings_rows = (
        spark.table("rti_demo_settings")
        .filter(F.col("setting_name") == F.lit("key_vault_uri"))
        .select("setting_value")
        .limit(2)
        .collect()
    )
    if len(settings_rows) != 1 or not str(settings_rows[0]["setting_value"]).strip():
        raise ValueError("rti_demo_settings must contain one non-empty key_vault_uri setting")
    key_vault_uri = str(settings_rows[0]["setting_value"]).strip()

selected_facility_ids = json.loads(facility_ids_json)
if not isinstance(selected_facility_ids, list) or not all(
    isinstance(facility_id, str) for facility_id in selected_facility_ids
):
    raise ValueError("facility_ids_json must be a JSON array of facility ID strings")

points = load_station_points(
    facilities_table,
    equipment_table,
    selected_facility_ids,
    require_active_equipment,
)

for point in points:
    if not (-90 <= point["latitude"] <= 90 and -180 <= point["longitude"] <= 180):
        raise ValueError(f"Invalid point coordinates: {point}")

areas = [
    station_aggregation_area(point, aggregation_radius_km, aggregation_circle_vertices)
    for point in points
]
print(f"Created {len(areas)} station aggregation areas at {aggregation_radius_km:g} km radius")

api_key = notebookutils.credentials.getSecret(key_vault_uri, api_key_secret_name)
run_id = str(uuid4())
started_at = pd.Timestamp.now(tz="UTC").to_pydatetime()
item = latest_stac_item(endpoint, api_key)
source_item_id = str(item.get("id"))
properties = item.get("properties", {})
reference_text = properties.get("forecast:reference_datetime") or properties.get("datetime")
if not reference_text:
    raise RuntimeError("Latest STAC item has no forecast reference time")
reference_time = pd.Timestamp(reference_text).to_pydatetime()
asset_href = item.get("assets", {}).get(asset_key, {}).get("href")
if not asset_href:
    raise RuntimeError(f"STAC item has no {asset_key!r} asset")

raw_path = PurePosixPath("Files/weather/bronze/stac") / collection / f"{source_item_id}.json"
notebookutils.fs.put(str(raw_path), json.dumps(item, indent=2), True)

dataset = open_signed_zarr(asset_href)
missing_source_variables = sorted(SOURCE_VARIABLES - set(dataset.data_vars))
if missing_source_variables:
    raise KeyError(
        f"Missing Aurora variables: {missing_source_variables}. "
        f"Available: {sorted(dataset.data_vars)}"
    )

latitudes = np.asarray(dataset["latitude"].values, dtype=float)
longitudes = np.asarray(dataset["longitude"].values, dtype=float)
available_hours = lead_hours(dataset["step"].values)
requested_hours = set(range(interval_hours, max_lead_hours + 1, interval_hours))
step_indices = [index for index, value in enumerate(available_hours) if int(value) in requested_hours]
if not step_indices:
    raise RuntimeError("No requested forecast lead hours are present in the dataset")

print(
    f"Run {run_id}: item {source_item_id}, {len(step_indices)} lead times, "
    f"{len(VARIABLES)} variables"
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

location_rows = []
point_cells = []
for point in points:
    lat_index, lon_index = nearest_grid_indices(
        latitudes, longitudes, point["latitude"], point["longitude"]
    )
    point_cells.append((point, lat_index, lon_index))
    location_rows.append({
        "location_id": point["location_id"],
        "location_name": point["location_name"],
        "latitude": float(point["latitude"]),
        "longitude": float(point["longitude"]),
        "elevation_m": None,
        "metadata_json": json.dumps({
            "grid_latitude": float(latitudes[lat_index]),
            "grid_longitude": float(longitude_180(longitudes[lon_index])),
            "selection_method": "nearest_grid_cell",
        }),
        "updated_at_utc": started_at,
    })

forecast_rows = []
for step_index in step_indices:
    lead = int(available_hours[step_index])
    valid_time = reference_time + timedelta(hours=lead)
    grids = weather_grids(dataset, step_index)
    for point, lat_index, lon_index in point_cells:
        for variable_id, variable_grid in grids.items():
            raw_value = float(variable_grid[lat_index, lon_index])
            value = None if variable_id == "wind_direction" and not np.isfinite(raw_value) else raw_value
            forecast_rows.append({
                "source_id": SOURCE_ID,
                "variable_id": variable_id,
                "location_id": point["location_id"],
                "latitude": float(latitudes[lat_index]),
                "longitude": float(longitude_180(longitudes[lon_index])),
                "reference_time_utc": reference_time,
                "valid_time_utc": valid_time,
                "valid_date": valid_time.date(),
                "lead_hours": lead,
                "value": value,
                "unit": VARIABLES[variable_id]["unit"],
                "ensemble_member": "deterministic",
                "source_item_id": source_item_id,
                "run_id": run_id,
                "ingested_at_utc": started_at,
            })

invalid_forecasts = [
    row
    for row in forecast_rows
    if row["value"] is not None and not np.isfinite(row["value"])
]
if invalid_forecasts:
    sample = [
        {
            "location_id": row["location_id"],
            "lead_hours": row["lead_hours"],
            "value": row["value"],
        }
        for row in invalid_forecasts[:10]
    ]
    raise ValueError(f"Invalid positive-lead weather forecasts: {sample}")

locations_df = spark.createDataFrame(
    location_rows,
    "location_id string, location_name string, latitude double, longitude double, elevation_m double, metadata_json string, updated_at_utc timestamp",
)
forecasts_df = spark.createDataFrame(
    forecast_rows,
    "source_id string, variable_id string, location_id string, latitude double, longitude double, reference_time_utc timestamp, valid_time_utc timestamp, valid_date date, lead_hours int, value double, unit string, ensemble_member string, source_item_id string, run_id string, ingested_at_utc timestamp",
)
locations_df.createOrReplaceTempView("incoming_weather_locations")
forecasts_df.createOrReplaceTempView("incoming_weather_forecasts")
spark.sql(f"""
MERGE INTO {TABLES['locations']} target USING incoming_weather_locations source
ON target.location_id = source.location_id
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")
spark.sql(f"""
MERGE INTO {TABLES['forecasts']} target USING incoming_weather_forecasts source
ON target.source_id = source.source_id
AND target.variable_id = source.variable_id
AND target.location_id = source.location_id
AND target.reference_time_utc = source.reference_time_utc
AND target.valid_time_utc = source.valid_time_utc
AND target.ensemble_member <=> source.ensemble_member
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")

display(forecasts_df.orderBy("location_id", "valid_time_utc").limit(20))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Operating areas
#
# Persist the reusable area geometry and its representative location. Vendor-neutral
# metrics are calculated from canonical forecasts by Weather_020.

# CELL ********************

area_rows = []

for area in areas:
    area_size_m2 = geodesic_area_m2(area["geometry"])
    area_rows.append({
        "area_id": area["area_id"],
        "area_name": area["area_name"],
        "geometry_geojson": area["geometry_json"],
        "crs": "EPSG:4326",
        "metadata_json": json.dumps({
            "geodesic_area_m2": area_size_m2,
            "generation_method": "station_geodesic_buffer",
            "location_id": area["location_id"],
            "radius_km": area["radius_km"],
        }),
        "updated_at_utc": started_at,
    })
areas_df = spark.createDataFrame(area_rows)
areas_df.createOrReplaceTempView("incoming_weather_areas")
active_area_ids = [area["area_id"] for area in areas]
active_area_ids_sql = ", ".join(f"'{area_id.replace(chr(39), chr(39) * 2)}'" for area_id in active_area_ids)
spark.sql(f"DELETE FROM {TABLES['areas']} WHERE area_id NOT IN ({active_area_ids_sql})")
spark.sql(f"""
MERGE INTO {TABLES['areas']} target USING incoming_weather_areas source
ON target.area_id = source.area_id
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")

display(areas_df.orderBy("area_id"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

completed_at = pd.Timestamp.now(tz="UTC").to_pydatetime()
row_count = len(forecast_rows)
run_rows = [{
    "run_id": run_id,
    "source_id": SOURCE_ID,
    "source_item_id": source_item_id,
    "data_kind": "forecast",
    "reference_time_utc": reference_time,
    "started_at_utc": started_at,
    "completed_at_utc": completed_at,
    "status": "succeeded",
    "row_count": row_count,
    "error_message": None,
}]
spark.createDataFrame(
    run_rows,
    "run_id string, source_id string, source_item_id string, data_kind string, reference_time_utc timestamp, started_at_utc timestamp, completed_at_utc timestamp, status string, row_count long, error_message string",
).write.mode("append").saveAsTable(TABLES["ingestion_runs"])

print(f"Run {run_id} succeeded: {len(forecast_rows)} point forecast rows")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }