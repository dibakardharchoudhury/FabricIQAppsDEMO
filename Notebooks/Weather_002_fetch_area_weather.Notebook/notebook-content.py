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
# Aurora implementation of the source-adapter contract. It finds the newest complete
# operational issue in the `mai-aurora` STAC collection, extracts 72-hour point
# forecasts, creates operating-area geometry, and merges canonical records into the
# tables created by Weather_001.
#
# `mai-aurora` publishes one global NetCDF item per valid hour, named
# `<stream>_<issue>_<valid>`, with a kerchunk index giving each variable's byte range.
# The adapter range-reads only the variables it needs from each hourly item.
# Precipitation and solar radiation are one-hour totals, so precipitation is summed,
# solar radiation averaged, and gusts maximised across every hour inside the reporting
# interval; `interval_hours` records how much of that interval the value really covers.
# Pressure is mean sea-level pressure, matching the UKMet adapter.
#
# An issue older than `max_issue_age_hours` fails the run instead of silently
# reloading forecasts that retention would then discard.
#
# Use the project Weather Environment on Fabric Runtime 2.0 (Python 3.13), with
# its pinned NumPy 2, pandas, xarray, Zarr 2, adlfs, shapely and pyproj libraries.
# Attach the Weather Lakehouse before running. Store the API key in Key
# Vault; never put it in notebook parameters.

# PARAMETERS CELL ********************

import json

table_prefix = "weather"
prepare_only = False  # The UKMet-first pipeline prepares shared areas without calling Aurora.
endpoint = "https://mai-weather-api.azure-api.net"
api_version = "2025-04-30-preview"
collection = "mai-aurora"
stream = "oper"  # Operational deterministic run; "enfo" items are ensemble members.
data_asset_key = "data"
index_asset_key = "data-kerchunk"
max_lead_hours = 72
interval_hours = 6
max_issue_age_hours = 36
download_workers = 8

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

import base64
import zlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import numpy as np
import pandas as pd
import requests
import notebookutils
from numcodecs import Shuffle
from pyproj import Geod
from requests.adapters import HTTPAdapter
from shapely.geometry import Polygon, mapping
from urllib3.util.retry import Retry

TABLES = {
    name: f"{table_prefix}_{name}"
    for name in ["locations", "areas", "ingestion_runs", "forecasts"]
}
SOURCE_ID = "aurora"
RUN_SCHEMA = (
    "run_id string, source_id string, source_item_id string, data_kind string, "
    "reference_time_utc timestamp, started_at_utc timestamp, completed_at_utc timestamp, "
    "status string, row_count long, error_message string"
)
# Each hourly item's `tp` and `ssrd` are totals over the single hour ending at it.
ACCUMULATION_SOURCE_HOURS = 1
# Variables whose reported value covers the whole interval instead of one instant.
BUCKET_AGGREGATIONS = {"precipitation": "sum", "solar_radiation": "mean", "wind_gust": "max"}
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
# ECMWF short names: mean sea-level pressure (Pa), 2 m temperature and dew point (K),
# hourly downward solar radiation (J/m2), 10 m wind components and gust (m/s), and
# hourly total precipitation (m).
SOURCE_VARIABLES = ("msl", "2t", "2d", "ssrd", "10u", "10v", "i10fg", "tp")


def service_base(service_endpoint: str) -> str:
    parsed = urlsplit(service_endpoint)
    prefix = parsed.path.split("/stac", 1)[0].rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{prefix}"


def retry_session(pool_size: int = 10) -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size),
    )
    return session


def to_utc(value) -> datetime:
    timestamp = pd.Timestamp(value)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def stac_search(session: requests.Session, service_endpoint: str, api_key: str, params: dict) -> dict:
    """Run one STAC search; errors never echo the URL because it carries the API key."""
    response = session.get(
        f"{service_base(service_endpoint)}/stac/search",
        params={"api-version": api_version, "key": api_key, **params},
        timeout=60,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        raise RuntimeError(f"Aurora STAC search failed with HTTP {response.status_code}") from None
    return response.json()


def next_page_params(page: dict) -> dict | None:
    """Decode the `next` link's query once; re-encoding it would corrupt the page token."""
    link = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
    if not link or not link.get("href"):
        return None
    return {
        name: value
        for name, value in parse_qsl(urlsplit(link["href"]).query)
        if name not in ("key", "api-version")
    }


def newest_stream_issue(
    session: requests.Session, service_endpoint: str, api_key: str,
    max_issues: int = 7, issue_spacing_hours: int = 6,
) -> datetime:
    """Probe known operational issue IDs; old long horizons cannot hide a new issue."""
    now = datetime.now(timezone.utc)
    newest = now.replace(hour=now.hour - now.hour % issue_spacing_hours, minute=0, second=0, microsecond=0)
    for offset in range(max_issues):
        issue = newest - timedelta(hours=offset * issue_spacing_hours)
        if issue_items(session, service_endpoint, api_key, issue, [1]):
            return issue
    raise RuntimeError(f"No recent {stream!r} issues found in {collection}")


def issue_items(
    session: requests.Session,
    service_endpoint: str,
    api_key: str,
    issue: datetime,
    leads: list[int],
) -> dict[int, dict]:
    """Fetch the signed hourly items of one issue by their deterministic IDs, keyed by lead."""
    prefix = f"{stream}_{issue:%Y%m%d%H%M}_"
    wanted = {f"{prefix}{issue + timedelta(hours=lead):%Y%m%d%H%M}": lead for lead in leads}
    names = list(wanted)
    found = {}
    for start in range(0, len(names), 50):
        page = stac_search(
            session,
            service_endpoint,
            api_key,
            {
                "collections": collection,
                "ids": ",".join(names[start:start + 50]),
                "limit": 100,
                "sign": "true",
                "duration": 60,
            },
        )
        for item in page.get("features", []):
            if item.get("id") in wanted:
                found[wanted[item["id"]]] = item
    return found


def latest_complete_issue(
    session: requests.Session,
    service_endpoint: str,
    api_key: str,
    leads: list[int],
    fallback_issues: int = 4,
    issue_spacing_hours: int = 6,
) -> tuple[datetime, dict[int, dict]]:
    """Pick the newest issue whose every requested lead is published, newest first."""
    newest = newest_stream_issue(session, service_endpoint, api_key, issue_spacing_hours=issue_spacing_hours)
    for offset in range(fallback_issues + 1):
        issue = newest - timedelta(hours=offset * issue_spacing_hours)
        items = issue_items(session, service_endpoint, api_key, issue, leads)
        if len(items) == len(leads):
            return issue, items
        print(f"Aurora {stream} issue {issue:%Y-%m-%dT%H:%MZ} has {len(items)} of {len(leads)} leads; trying an earlier issue")
    raise RuntimeError(f"No complete {stream!r} issue with {len(leads)} leads found in {collection}")


def sanitize_stac_item(item: dict) -> dict:
    """Return a deep copy with URL query credentials removed from every asset href."""
    sanitized = deepcopy(item)
    for asset in (sanitized.get("assets") or {}).values():
        href = asset.get("href") if isinstance(asset, dict) else None
        if isinstance(href, str):
            asset["href"] = urlsplit(href)._replace(query="").geturl()
    return sanitized


def asset_href(item: dict, key: str) -> str:
    href = ((item.get("assets") or {}).get(key) or {}).get("href")
    if not href:
        raise RuntimeError(f"STAC item {item.get('id')} has no {key!r} asset")
    return href


def kerchunk_refs(session: requests.Session, item: dict) -> dict:
    response = session.get(asset_href(item, index_asset_key), timeout=60)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        raise RuntimeError(f"Aurora index read for {item.get('id')} failed with HTTP {response.status_code}") from None
    document = response.json()
    return document.get("refs", document)


def reference_metadata(refs: dict, name: str, part: str) -> dict:
    value = refs.get(f"{name}/{part}")
    if value is None:
        raise KeyError(f"Aurora item is missing {name!r}; available: {sorted({key.split('/')[0] for key in refs if '/' in key})}")
    return json.loads(value) if isinstance(value, str) else value


def read_reference_array(session: requests.Session, data_href: str, refs: dict, name: str) -> np.ndarray:
    """Range-read and decode one variable stored as a single kerchunk-indexed chunk."""
    zarray = reference_metadata(refs, name, ".zarray")
    shape = list(zarray["shape"])
    if list(zarray.get("chunks", shape)) != shape:
        raise ValueError(f"Aurora variable {name!r} spans several chunks; expected one per variable")
    if zarray.get("compressor"):
        raise ValueError(f"Unsupported compressor for {name!r}: {zarray['compressor']}")
    reference = refs.get(f"{name}/" + (".".join("0" * len(shape)) if shape else "0"))
    if isinstance(reference, str):
        raw = base64.b64decode(reference[len("base64:"):]) if reference.startswith("base64:") else reference.encode("latin-1")
    elif isinstance(reference, list) and len(reference) == 3:
        _, offset, length = reference
        response = session.get(
            data_href,
            headers={"Range": f"bytes={int(offset)}-{int(offset) + int(length) - 1}"},
            timeout=120,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError:
            raise RuntimeError(f"Aurora data read for {name!r} failed with HTTP {response.status_code}") from None
        raw = response.content
        if len(raw) != int(length):
            raise RuntimeError(f"Aurora data read for {name!r} returned {len(raw)} of {length} bytes")
    else:
        raise ValueError(f"Unsupported kerchunk reference for {name!r}")
    for codec in reversed(zarray.get("filters") or []):
        if codec.get("id") == "zlib":
            raw = zlib.decompress(raw)
        elif codec.get("id") == "shuffle":
            raw = Shuffle(int(codec["elementsize"])).decode(raw)
        else:
            raise ValueError(f"Unsupported codec for {name!r}: {codec}")
    array = np.frombuffer(bytes(raw), dtype=zarray["dtype"]).reshape(shape).astype(float)
    attributes = json.loads(refs[f"{name}/.zattrs"]) if isinstance(refs.get(f"{name}/.zattrs"), str) else refs.get(f"{name}/.zattrs") or {}
    return array * float(attributes.get("scale_factor", 1.0)) + float(attributes.get("add_offset", 0.0))


def read_item_points(
    session: requests.Session,
    item: dict,
    lead: int,
    grid_shape: tuple[int, int],
    lat_indices: np.ndarray,
    lon_indices: np.ndarray,
) -> dict[str, np.ndarray]:
    """Return each source variable at the selected grid cells for one hourly item."""
    refs = kerchunk_refs(session, item)
    data_href = asset_href(item, data_asset_key)
    step = float(read_reference_array(session, data_href, refs, "step"))
    if round(step) != lead:
        raise ValueError(f"Aurora item {item.get('id')} reports step {step}h, expected {lead}h")
    values = {}
    for name in SOURCE_VARIABLES:
        grid = read_reference_array(session, data_href, refs, name)
        if grid.shape != grid_shape:
            raise ValueError(f"Aurora variable {name!r} in {item.get('id')} has shape {grid.shape}, expected {grid_shape}")
        values[name] = grid[lat_indices, lon_indices]
    return values


def relative_humidity_percent(temperature_c, dew_point_c):
    exponent = (
        17.625 * dew_point_c / (243.04 + dew_point_c)
        - 17.625 * temperature_c / (243.04 + temperature_c)
    )
    return np.clip(100.0 * np.exp(exponent), 0.0, 100.0)


def weather_series(source: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Convert (step, point) source arrays to canonical variables and units."""
    temperature_c = source["2t"] - 273.15
    dew_point_c = source["2d"] - 273.15
    wind_u = source["10u"]
    wind_v = source["10v"]
    wind_speed = np.hypot(wind_u, wind_v)
    wind_direction = np.degrees(np.arctan2(-wind_u, -wind_v)) % 360.0
    wind_direction = np.where(wind_speed < 0.1, np.nan, wind_direction)

    return {
        "pressure": source["msl"] / 100.0,
        "temperature": temperature_c,
        "relative_humidity": relative_humidity_percent(temperature_c, dew_point_c),
        "dew_point": dew_point_c,
        # Hourly J/m2 totals become mean W/m2; the model emits small negatives at night.
        "solar_radiation": np.maximum(source["ssrd"] / 3600.0, 0.0),
        "wind_speed": wind_speed,
        "wind_gust": np.maximum(source["i10fg"], 0.0),
        "wind_direction": wind_direction,
        "precipitation": np.maximum(source["tp"], 0.0) * 1000.0,
    }


def bucket_value(values: np.ndarray, method: str | None, end_position: int, positions: list[int]):
    """Collapse the native steps inside one reporting interval to a single value per point."""
    if method == "sum":
        selected = values[positions, :]
        return np.where(np.all(np.isnan(selected), axis=0), np.nan, np.nansum(selected, axis=0))
    if method == "mean":
        return np.nanmean(values[positions, :], axis=0)
    if method == "max":
        return np.nanmax(values[positions, :], axis=0)
    return values[end_position, :]


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


def persist_station_areas(areas, updated_at, selected_facility_ids):
    area_rows = []
    for area in areas:
        area_rows.append({
            "area_id": area["area_id"],
            "area_name": area["area_name"],
            "location_id": area["location_id"],
            "geometry_geojson": area["geometry_json"],
            "crs": "EPSG:4326",
            "metadata_json": json.dumps({
                "geodesic_area_m2": geodesic_area_m2(area["geometry"]),
                "generation_method": "station_geodesic_buffer",
                "location_id": area["location_id"],
                "radius_km": area["radius_km"],
            }),
            "updated_at_utc": updated_at,
        })
    areas_df = spark.createDataFrame(area_rows)
    if "location_id" not in spark.table(TABLES["areas"]).columns:
        spark.sql(f"ALTER TABLE {TABLES['areas']} ADD COLUMNS (location_id STRING)")
    areas_df.createOrReplaceTempView("incoming_weather_areas")
    if not selected_facility_ids:
        active_area_ids = [area["area_id"] for area in areas]
        active_area_ids_sql = ", ".join(f"'{area_id.replace(chr(39), chr(39) * 2)}'" for area_id in active_area_ids)
        spark.sql(f"DELETE FROM {TABLES['areas']} WHERE area_id NOT IN ({active_area_ids_sql})")
    spark.sql(f"""
MERGE INTO {TABLES['areas']} target USING incoming_weather_areas source
ON target.area_id = source.area_id
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")
    return areas_df

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

if not isinstance(prepare_only, bool):
    raise ValueError("prepare_only must be a boolean")
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

started_at = pd.Timestamp.now(tz="UTC").to_pydatetime()
areas_df = persist_station_areas(areas, started_at, selected_facility_ids)
display(areas_df.orderBy("area_id"))
if prepare_only:
    notebookutils.notebook.exit(json.dumps({
        "status": "prepared", "areas": len(areas), "aurora": "disabled",
    }))

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

run_id = str(uuid4())

# Fabric aborts the remaining cells on error, so the audit row is written before any
# external credential or network call. A run left in 'started' is a failed run.
spark.createDataFrame(
    [{
        "run_id": run_id,
        "source_id": SOURCE_ID,
        "source_item_id": None,
        "data_kind": "forecast",
        "reference_time_utc": None,
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "status": "started",
        "row_count": 0,
        "error_message": None,
    }],
    RUN_SCHEMA,
).write.mode("append").saveAsTable(TABLES["ingestion_runs"])

api_key = notebookutils.credentials.getSecret(key_vault_uri, api_key_secret_name)
if max_lead_hours < interval_hours or interval_hours < 1 or download_workers < 1:
    raise ValueError("max_lead_hours, interval_hours, and download_workers must be positive and consistent")
native_hours = list(range(1, max_lead_hours + 1))
session = retry_session(max(int(download_workers), 1) * 2)
reference_time, items_by_lead = latest_complete_issue(session, endpoint, api_key, native_hours)
source_item_id = f"{stream}_{reference_time:%Y%m%d%H%M}"
issue_age_hours = (started_at - reference_time).total_seconds() / 3600.0
if issue_age_hours > max_issue_age_hours:
    raise RuntimeError(
        f"Newest complete Aurora {stream!r} issue {reference_time:%Y-%m-%dT%H:%MZ} is "
        f"{issue_age_hours:.0f}h old, beyond max_issue_age_hours={max_issue_age_hours}; "
        f"the {collection} feed may have stalled"
    )

raw_path = PurePosixPath("Files/weather/bronze/stac") / collection / f"{source_item_id}.json"
notebookutils.fs.put(
    str(raw_path),
    json.dumps(
        {
            "collection": collection,
            "issue_id": source_item_id,
            "items": [sanitize_stac_item(items_by_lead[lead]) for lead in native_hours],
        },
        indent=2,
    ),
    True,
)

requested_hours = set(range(interval_hours, max_lead_hours + 1, interval_hours))
bucket_leads = sorted(hour for hour in set(native_hours) if hour in requested_hours)
if not bucket_leads:
    raise RuntimeError("No requested forecast lead hours are present in the issue")

# Each reported lead covers the interval that ends at it, so accumulating variables are
# summed over every native step inside that window instead of sampling one step in six.
bucket_members = {
    lead: [index for index, hour in enumerate(native_hours) if lead - interval_hours < hour <= lead]
    for lead in bucket_leads
}
member_indices = sorted({index for members in bucket_members.values() for index in members})
member_position = {index: position for position, index in enumerate(member_indices)}
bucket_coverage = {
    lead: min(len(members) * ACCUMULATION_SOURCE_HOURS, interval_hours)
    for lead, members in bucket_members.items()
}
partial_leads = [lead for lead, covered in bucket_coverage.items() if covered < interval_hours]
if partial_leads:
    print(
        f"Aurora publishes {ACCUMULATION_SOURCE_HOURS}h totals every "
        f"{min(native_hours[1:] or [interval_hours])}h, so {len(partial_leads)} of "
        f"{len(bucket_leads)} leads cover {bucket_coverage[partial_leads[0]]}h of the "
        f"{interval_hours}h interval; interval_hours records the shortfall"
    )

# All hourly items share one global grid, so the nearest cells are resolved once.
first_item = items_by_lead[native_hours[0]]
first_refs = kerchunk_refs(session, first_item)
first_data_href = asset_href(first_item, data_asset_key)
latitudes = read_reference_array(session, first_data_href, first_refs, "latitude")
longitudes = read_reference_array(session, first_data_href, first_refs, "longitude")
grid_shape = (len(latitudes), len(longitudes))
point_cells = []
for point in points:
    lat_index, lon_index = nearest_grid_indices(
        latitudes, longitudes, point["latitude"], point["longitude"]
    )
    point_cells.append((point, lat_index, lon_index))
lat_indices = np.asarray([lat_index for _, lat_index, _ in point_cells], dtype=int)
lon_indices = np.asarray([lon_index for _, _, lon_index in point_cells], dtype=int)

member_leads = [native_hours[index] for index in member_indices]
with ThreadPoolExecutor(max_workers=int(download_workers)) as pool:
    member_values = list(pool.map(
        lambda lead: read_item_points(session, items_by_lead[lead], lead, grid_shape, lat_indices, lon_indices),
        member_leads,
    ))
source_series = {
    name: np.vstack([values[name] for values in member_values])
    for name in SOURCE_VARIABLES
}

print(
    f"Run {run_id}: issue {source_item_id} ({issue_age_hours:.0f}h old), "
    f"{len(bucket_leads)} lead times from {len(member_indices)} hourly items, "
    f"{len(VARIABLES)} variables"
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

location_rows = []
for point, lat_index, lon_index in point_cells:
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

series = weather_series(source_series)

forecast_rows = []
for lead in bucket_leads:
    valid_time = reference_time + timedelta(hours=lead)
    positions = [member_position[index] for index in bucket_members[lead]]
    end_position = member_position[native_hours.index(lead)]
    for variable_id, variable_series in series.items():
        method = BUCKET_AGGREGATIONS.get(variable_id)
        values = bucket_value(variable_series, method, end_position, positions)
        for point_index, (point, lat_index, lon_index) in enumerate(point_cells):
            raw_value = float(values[point_index])
            value = raw_value if np.isfinite(raw_value) else None
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
                "interval_hours": bucket_coverage[lead] if method else 0,
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
    "source_id string, variable_id string, location_id string, latitude double, longitude double, reference_time_utc timestamp, valid_time_utc timestamp, valid_date date, lead_hours int, interval_hours int, value double, unit string, ensemble_member string, source_item_id string, run_id string, ingested_at_utc timestamp",
)
locations_df.createOrReplaceTempView("incoming_weather_locations")
forecasts_df.createOrReplaceTempView("incoming_weather_forecasts")
for point in points:
    location_id = str(point["location_id"]).replace(chr(39), chr(39) * 2)
    spark.sql(
        f"DELETE FROM {TABLES['forecasts']} WHERE source_id = '{SOURCE_ID}' "
        f"AND source_item_id = '{source_item_id.replace(chr(39), chr(39) * 2)}' "
        f"AND location_id = '{location_id}'"
    )
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
spark.createDataFrame(run_rows, RUN_SCHEMA).createOrReplaceTempView("incoming_weather_run")
spark.sql(f"""
MERGE INTO {TABLES['ingestion_runs']} target USING incoming_weather_run source
ON target.run_id = source.run_id
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")

print(f"Run {run_id} succeeded: {len(forecast_rows)} point forecast rows")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }