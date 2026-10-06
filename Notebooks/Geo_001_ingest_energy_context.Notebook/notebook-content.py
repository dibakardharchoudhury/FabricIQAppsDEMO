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

# # Public energy context — complete, validated layer snapshots
#
# Bind this notebook to **Hydro_GeoContext_{env_suffix}**, never the synthetic STID
# lakehouse. Deployment supplies the binding; no service principal, secret, or Fabric
# control-plane call is needed here. `workspace_id` defaults to runtime context.
# Use separate workspace/bindings and an explicit suffix for dev/test/prod promotion.
# Create a **schema-disabled** GeoContext Lakehouse by omitting `creationPayload`
# from the REST create request (`enableSchemas` only accepts `true`). The aliases
# `HydroGeoFeatures` / `HydroGeoStatus` expect `Tables/geo_map_features` /
# `Tables/geo_source_status`. Actual Delta locations are reported at startup and
# in the successful run summary. A schema-enabled/different layout fails before
# source imports, reporting both locations instead of silently breaking the aliases.
#
# `all` refreshes every source, but skips healthy network/plant snapshots less than
# 24 hours old unless `force_refresh=True`. `operational` skips network/plants even
# when forced, and refreshes reservoirs, Statnett and UMM once. There is no polling
# loop or real-time SLA. Serialize notebook runs (pipeline concurrency **1**).
#
# **Publication:** each ArcGIS layer is read by OBJECTID inventory and bounded query
# POSTs (read-only), with initial/final count + ID reconciliation. Driver memory holds
# IDs and one page, not 800,000 feature dictionaries. Normalized gzip JSONL shards
# are read by Spark once per layer; Delta `replaceWhere` atomically replaces only a
# complete layer. Source/validation failures retain the previous partition.
# Bronze responses, rejected geometry and attempt logs are retained under
# `Files/bronze/geo_context/<source>/<run_id>/<layer>/`; nothing is vacuumed here.
#
# `geo_source_status` has one row per layer. Its allowed states are only `ready` and
# `error`, so a started/interrupted import is explicitly `error` / "in progress",
# preserving last-good counts and last_success_at until publication succeeds.
# `rejected_count` counts rejected feature geometries (retained as unmapped rows),
# NOT silently discarded source records. Identity/schema/completeness errors fail
# the layer. A fatal status-storage/programming error is not disguised as success.
# Features and status are separate Delta transactions: an interrupted final status
# update can leave a committed snapshot with an error status; a rerun repairs it.
#
# **Interpretation / attribution:**
# - NVE Nettanlegg4: provider geometry with varying stated accuracy, not a live
#   outage/safety layer. All six national layers are ingested, including all masts.
# - NVE Powerplant registry (NLOD) joined to Vannkraft1/0 by plant number; missing
#   or ambiguous GIS joins remain unmapped. MidProd_91_20 is **mean annual GWh for
#   1991–2020**, not current production. GIS service notices are archived separately;
#   their copyrightText was "None", not a verified grant of NLOD for GIS.
# - Magasinstatistikk: ALL area statistics, not individual reservoir positions.
#   EL/NO points are explicitly derived area representatives; VASS stays unmapped.
# - Statnett: Norway-wide balance, Norwegian-linked schematic area-to-area flows,
#   and the latest sample in a bounded three-minute frequency window. Missing is
#   null, not zero; metric timestamps can differ. EN/other unknown endpoints are
#   not guessed. Schematic lines are NOT cable routes.
# - Nord Pool UMM: documented `publicationStartDate`, `publicationStopDate`,
#   `IncludeOutdated`, `skip`, `limit`, verified with a small public response.
#   Coverage is a fixed **30-day publication window**, at most 10,000 revisions;
#   it does not claim to cover older, still-active notices. All pages must reconcile.
#   No area-filter guessing or access workaround. The documented infrastructure
#   area lookup returned HTTP 403 during authoring, so it is NOT a dependency:
#   resolve explicit area names and EIC/name pairs present in the returned messages.
#   Unknown/zero-area notices remain in the list; all affected areas are preserved.
#   Latest non-outdated revisions only; cancellations and unknown-date notices are
#   retained with `map_eligible=False`. Consumers MUST honor `map_eligible`.
#
# **Importance `rules-v1` (not Nord Pool's ranking, not a grid safety score):**
# Direct Norwegian relevance +10; explicitly unplanned +25; maximum reported
# single-unit interval unavailable MW: >0 +5, >=100 +20, >=1000 +35; longest
# reported continuous interval: >=4h +5, >=24h +15. Do NOT sum overlapping units
# or intervals or extract MW from prose. High >=65, medium >=35, otherwise low.
# Missing quantitative/type evidence, unknown relevance, cancelled/ended or
# undatable notices are **unranked** (null score), not low.
#
# **Auxiliary outputs (no changes to the twelve source layers or their facts):**
# - `geo_reservoir_areas`: the FEATURE_FIELDS schema, nine reservoir-layer features:
#   NO1-NO5 from NVE Nettomraader/0 (NLOD, overview boundaries, not street-level)
#   and four complete Natural Earth 1:10m v5.1.2 country backgrounds (public domain).
#   Preserve every supplied polygon/ring, including islands. Country membership
#   uses the provider's sovereign grouping: FI includes Aland; DK includes Greenland
#   and the Faroe Islands; Norway includes its published offshore territories.
#   These generalized boundaries are not a survey-grade coastline or land mask.
#   NO country figures are explicitly NATIONAL aggregates, not island measurements.
#   SE/FI/DK have no NVE reservoir figures: false availability, null filling.
#   Original NVE EL/NO/VASS facts remain unchanged in `geo_map_features`.
#   Source family stays `nve-reservoir`; geometry provenance/terms are separate.
#   Healthy price boundaries cache for 24h; pinned, validated country geometry can
#   be reused indefinitely. Force/all refreshes both. Statistics are rejoined each run.
# - `geo_market_asset_links`: exact normalized unit name AND verified source owner
#   versus publisher/market-participant name, only when the asset is unambiguous.
#   Unicode/case/whitespace normalization is allowed; corporate periods normalize
#   A.S. to AS. No fuzzy names, area/proximity matching, capacity inference or EIC
#   crosswalk guesses. No validated EIC-to-NVE crosswalk is currently available.
#   Only hydro plants and transformers are indexed, never the mast inventory.
#   Cancellations may link, but a link never asserts an active outage.
#   `manual` links override automation for their exact message ID AND version.
#   Historical manual corrections are retained byte-for-byte, not promoted to a
#   later revision. Serving MUST join message_feature_id AND message_version.
#   Unsupported/ambiguous notices remain unlinked with coverage counts in run logs.
# Both new tables have validated flat `Tables/<table>` locations and atomic writes.
# Auxiliary failures keep last-good tables, are archived/reported, and fail the job.
# Auxiliary attempt logs do not add rows or change counts in `geo_source_status`.
#
# References:
# https://kart.nve.no/enterprise/rest/services/Nettanlegg4/MapServer
# https://api.nve.no/web/Powerplant/GetHydroPowerPlants
# https://kart.nve.no/enterprise/rest/services/Vannkraft1/MapServer
# https://biapi.nve.no/magasinstatistikk/api/Magasinstatistikk/HentOffentligDataSisteUke
# https://driftsdata.statnett.no/
# https://developers.nordpoolgroup.com/reference/umm-api-messages-search
# https://developers.nordpoolgroup.com/reference/messages-copy
# https://kart.nve.no/enterprise/rest/services/Nettomraader/MapServer/0
# https://www.nve.no/karttjenester/
# https://www.naturalearthdata.com/about/terms-of-use/
# https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/

# PARAMETERS CELL ********************

workspace_id = ""
env_suffix = "V6"
refresh_mode = "all"
force_refresh = False

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import gzip
import hashlib
import json
import math
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import requests

FEATURE_TABLE = "geo_map_features"
STATUS_TABLE = "geo_source_status"
RESERVOIR_AREA_TABLE = "geo_reservoir_areas"
ASSET_LINK_TABLE = "geo_market_asset_links"
FEATURE_FIELDS = (
    ("feature_id", "string"), ("source_id", "string"), ("layer_id", "string"),
    ("label", "string"), ("geometry_json", "string"), ("properties_json", "string"),
    ("min_lon", "double"), ("min_lat", "double"), ("max_lon", "double"),
    ("max_lat", "double"), ("observed_at", "string"), ("ingested_at", "string"),
    ("source_url", "string"), ("run_id", "string"),
)
STATUS_FIELDS = (
    ("layer_id", "string"), ("source_id", "string"), ("state", "string"),
    ("last_attempt_at", "string"), ("last_success_at", "string"),
    ("row_count", "long"), ("unmapped_count", "long"), ("rejected_count", "long"),
    ("message", "string"), ("source_url", "string"),
)
ASSET_LINK_FIELDS = (
    ("message_feature_id", "string"), ("message_version", "long"),
    ("asset_feature_id", "string"), ("asset_layer_id", "string"),
    ("match_method", "string"), ("match_evidence_json", "string"),
    ("linked_at", "string"), ("run_id", "string"),
)
LINK_MESSAGE_KEY = ("message_feature_id", "message_version")
LINK_ROW_KEY = ("message_feature_id", "message_version", "asset_feature_id", "asset_layer_id")
TABLE_CONTRACTS = (
    (FEATURE_TABLE, FEATURE_FIELDS, ["layer_id"]),
    (STATUS_TABLE, STATUS_FIELDS, []),
    (RESERVOIR_AREA_TABLE, FEATURE_FIELDS, ["layer_id"]),
    (ASSET_LINK_TABLE, ASSET_LINK_FIELDS, []),
)
GRID_SERVICE = "https://kart.nve.no/enterprise/rest/services/Nettanlegg4/MapServer"
HYDRO_SERVICE = "https://kart.nve.no/enterprise/rest/services/Vannkraft1/MapServer"
HYDRO_URL = "https://api.nve.no/web/Powerplant/GetHydroPowerPlants"
RESERVOIR_URL = "https://biapi.nve.no/magasinstatistikk/api/Magasinstatistikk/HentOffentligDataSisteUke"
BALANCE_URL = "https://driftsdata.statnett.no/restapi/ProductionConsumption/GetLatestDetailedOverview"
FLOW_URL = "https://driftsdata.statnett.no/restapi/PhysicalFlowMap/GetFlow"
FREQUENCY_URL = "https://driftsdata.statnett.no/restapi/Frequency/BySecondWithXy"
UMM_URL = "https://ummapi.nordpoolgroup.com/messages"
PRICE_AREA_SERVICE = "https://kart.nve.no/enterprise/rest/services/Nettomraader/MapServer"
PRICE_AREA_URL = f"{PRICE_AREA_SERVICE}/0"
NVE_MAP_TERMS_URL = "https://www.nve.no/karttjenester/"
NATURAL_EARTH_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson"
NATURAL_EARTH_TERMS_URL = "https://www.naturalearthdata.com/about/terms-of-use/"
NATURAL_EARTH_BLOB_SHA = "5ebc66e25fc1af01edaebe9375c546655e04cf1e"
PRICE_AREA_CODES = ("NO1", "NO2", "NO3", "NO4", "NO5")
COUNTRY_COMPONENTS = {
    "NO": ("NOR",), "SE": ("SWE",), "FI": ("FIN", "ALD"),
    "DK": ("DNK", "GRL", "FRO"),
}
COUNTRY_NAMES = {"NO": "Norway", "SE": "Sweden", "FI": "Finland", "DK": "Denmark"}
BOUNDARY_MAX_BYTES = 20 * 1024 * 1024
BOUNDARY_MAX_POSITIONS = 300000
LINKABLE_ASSET_LAYERS = ("hydro-plants", "transformers")
MAX_LINKABLE_ASSETS = 25000  # bounded small dimension, not national network features
AUXILIARY_CONFIGS = (
    {"artifact_id": RESERVOIR_AREA_TABLE, "layer_id": "reservoirs",
     "source_id": "nve-reservoir", "source_url": PRICE_AREA_URL},
    {"artifact_id": ASSET_LINK_TABLE, "layer_id": "umm",
     "source_id": "nordpool", "source_url": UMM_URL},
)
GRID_LAYERS = (
    (0, "transmission"), (1, "regional"), (2, "distribution"),
    (3, "sea-cables"), (4, "masts"), (5, "transformers"),
)
LAYER_CONFIGS = tuple(
    {"layer_id": name, "source_id": "nve-grid", "source_url": f"{GRID_SERVICE}/{number}",
     "static": True, "arcgis_layer": number}
    for number, name in GRID_LAYERS
) + (
    {"layer_id": "hydro-plants", "source_id": "nve-hydro", "source_url": HYDRO_URL, "static": True},
    {"layer_id": "reservoirs", "source_id": "nve-reservoir", "source_url": RESERVOIR_URL, "static": False},
    {"layer_id": "power-balance", "source_id": "statnett", "source_url": BALANCE_URL, "static": False},
    {"layer_id": "power-flows", "source_id": "statnett", "source_url": FLOW_URL, "static": False},
    {"layer_id": "grid-frequency", "source_id": "statnett", "source_url": FREQUENCY_URL, "static": False},
    {"layer_id": "umm", "source_id": "nordpool", "source_url": UMM_URL, "static": False},
)
# All coordinates below are deliberately illustrative representatives, NOT centroids
# from authoritative area polygons, station positions, or interconnector routes.
AREA_POINTS = {
    "NO1": (10.75, 59.91), "NO2": (8.0, 58.15), "NO3": (10.40, 63.43),
    "NO4": (18.95, 69.65), "NO5": (5.32, 60.39), "NO": (10.0, 64.5),
    "SE1": (20.22, 67.85), "SE2": (17.31, 62.39), "SE3": (18.07, 59.33),
    "SE4": (13.0, 55.61), "DK1": (10.20, 56.16), "DK2": (12.57, 55.68),
    "FI": (25.75, 64.5), "NL": (5.30, 52.13), "DE": (10.45, 51.16),
    "GB": (-2.5, 54.5), "EE": (25.0, 58.7), "LV": (24.6, 56.9),
    "LT": (24.0, 55.2), "PL": (19.1, 52.1),
}
NVE_EXTENT = (-15.0, 48.0, 45.0, 82.0)  # also rejects swapped Norwegian lon/lat
ARCGIS_CHUNK_SIZE = 1000  # <= advertised maxRecordCount=2000
NORMALIZED_SHARD_ROWS = 10000  # gzip shards allow parallel Spark input
UMM_HORIZON_DAYS = 30
UMM_PAGE_SIZE = 500
UMM_MAX_REVISIONS = 10000
UMM_UNIT_FIELDS = (
    "productionUnits", "generationUnits", "consumptionUnits", "transmissionUnits", "otherUnits",
)
BALANCE_METRICS = {
    "ProductionData": "production_mw", "HydroData": "hydro_mw",
    "WindData": "wind_mw", "ThermalData": "thermal_mw", "NuclearData": "nuclear_mw",
    "ConsumptionData": "consumption_mw", "NetExchangeData": "net_exchange_mw",
}


class SourceError(RuntimeError):
    """Expected provider, source-schema, or snapshot-reconciliation failure."""


class GeometryError(SourceError):
    """A source geometry is retained in bronze, but must not be plotted."""


def require_object(value, label, fields=()):
    if not isinstance(value, dict):
        raise SourceError(f"{label}: expected an object")
    missing = set(fields) - value.keys()
    if missing:
        raise SourceError(f"{label}: missing fields {sorted(missing)}")
    return value


def require_list(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise SourceError(f"{label}: expected {'nonempty ' if nonempty else ''}array")
    return value


def source_text(value, label, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise SourceError(f"{label}: expected {'nonempty ' if required else ''}text")
    return value.strip()


def number(value, label="number"):
    """Norwegian formatted numbers: spaces/NBSP group; comma or dot decimal."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SourceError(f"{label}: invalid numeric type")
    if isinstance(value, str):
        text = re.sub(r"\s+", "", value).replace("\u2212", "-")
        if text.lower() in {"", "-", "—", "–", "n/a", "null"}:
            return None
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
        else:
            text = text.replace(",", ".")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text):
            raise SourceError(f"{label}: invalid formatted number")
        value = text
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise SourceError(f"{label}: invalid number") from exc
    if not math.isfinite(result):
        raise SourceError(f"{label}: nonfinite number")
    return result


def integer(value, label, minimum=0):
    parsed = number(value, label)
    if parsed is None or not parsed.is_integer() or parsed < minimum:
        raise SourceError(f"{label}: expected integer >= {minimum}")
    return int(parsed)


def parse_time(value, label, epoch_ms=False, date_only=False, required=False):
    if value is None or value == "":
        if required:
            raise SourceError(f"{label}: missing timestamp")
        return None
    try:
        if epoch_ms and isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(number(value, label) / 1000, timezone.utc)
        elif isinstance(value, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and date_only:
                parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone absent")
        else:
            raise ValueError("not an ISO timestamp or epoch milliseconds")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise SourceError(f"{label}: invalid/ambiguous timestamp") from exc


def utc_text(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z") if value is not None else None


def observed(value, label, **options):
    return utc_text(parse_time(value, label, **options))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def reject_json_constant(value):
    raise ValueError(f"Non-JSON numeric constant: {value}")


def point_for(area):
    return {"type": "Point", "coordinates": list(AREA_POINTS[area])} if area in AREA_POINTS else None


def is_norwegian(area):
    return isinstance(area, str) and (area == "NO" or bool(re.fullmatch(r"NO\d+", area)))


def geometry_bounds(geometry, extent=None):
    """Validate GeoJSON x=longitude/y=latitude without swapping or geocoding."""
    if not isinstance(geometry, dict):
        raise GeometryError("geometry is not an object")
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    points = []

    def position(value):
        if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
            raise GeometryError("invalid coordinate position")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
            raise GeometryError("non-numeric/nonfinite coordinate")
        lon, lat = value[:2]
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise GeometryError("coordinate outside WGS84 lon/lat range")
        if extent and not (extent[0] <= lon <= extent[2] and extent[1] <= lat <= extent[3]):
            raise GeometryError("coordinate outside source extent; possible axis/CRS error")
        points.append((float(lon), float(lat)))

    def sequence(values, minimum):
        if not isinstance(values, (list, tuple)) or len(values) < minimum:
            raise GeometryError("empty/short coordinate sequence")
        for value in values:
            position(value)

    def polygon(rings):
        if not isinstance(rings, list) or not rings:
            raise GeometryError("polygon has no exterior ring")
        exterior_area = None
        for ring in rings:
            sequence(ring, 4)
            if ring[0] != ring[-1] or len({tuple(p[:2]) for p in ring[:-1]}) < 3:
                raise GeometryError("polygon ring must close and have three distinct vertices")
            # Offset first to avoid cancellation when a small island is far from (0, 0).
            x0, y0 = ring[0][:2]
            area = abs(math.fsum(
                (a[0] - x0) * (b[1] - y0) - (b[0] - x0) * (a[1] - y0)
                for a, b in zip(ring, ring[1:])
            )) / 2
            if area == 0:
                raise GeometryError("polygon ring is degenerate or self-crossing")
            if exterior_area is None:
                exterior_area = area
            elif area >= exterior_area or not point_in_ring(ring[0], rings[0]):
                raise GeometryError("polygon hole is outside/larger than its exterior ring")
        if len(points) > BOUNDARY_MAX_POSITIONS:
            raise GeometryError("polygon exceeds the bounded geometry position budget")

    if kind == "Point":
        position(coordinates)
    elif kind in ("MultiPoint", "LineString"):
        sequence(coordinates, 1 if kind == "MultiPoint" else 2)
    elif kind == "MultiLineString":
        if not isinstance(coordinates, list) or not coordinates:
            raise GeometryError("empty MultiLineString")
        for line in coordinates:
            sequence(line, 2)
    elif kind == "Polygon":
        polygon(coordinates)
    elif kind == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise GeometryError("empty MultiPolygon")
        for rings in coordinates:
            polygon(rings)
    else:
        raise GeometryError(f"unsupported geometry type: {kind}")
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def point_in_ring(point, ring):
    x, y = point[:2]
    inside = False
    for a, b in zip(ring, ring[1:]):
        if (a[1] > y) != (b[1] > y):
            crossing_x = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < crossing_x:
                inside = not inside
    return inside


def make_feature(feature_id, source_id, layer_id, label, geometry, properties,
                 timestamp, source_url, run_id, ingested_at, extent=None):
    properties = dict(properties)
    bounds, geometry_json = (None, None, None, None), ""
    if geometry is None:
        properties["geometry_status"] = "unmapped"
    else:
        try:
            bounds = geometry_bounds(geometry, extent)
            geometry_json = json_text(geometry)
            properties["geometry_status"] = "mapped"
        except GeometryError as exc:
            properties["geometry_status"] = "rejected"
            properties["geometry_issue"] = str(exc)
    return {
        "feature_id": feature_id, "source_id": source_id, "layer_id": layer_id,
        "label": label, "geometry_json": geometry_json,
        "properties_json": json_text(properties),
        "min_lon": bounds[0], "min_lat": bounds[1], "max_lon": bounds[2], "max_lat": bounds[3],
        "observed_at": timestamp, "ingested_at": ingested_at,
        "source_url": source_url, "run_id": run_id,
    }


def grid_identity(properties, layer_number):
    raw_guid = properties.get("globalid")
    if raw_guid:
        try:
            guid = UUID(source_text(raw_guid, "globalid").strip("{}"))
        except ValueError as exc:
            raise SourceError("invalid NVE globalid") from exc
        if guid.int:
            return f"nve-grid:Nettanlegg4:{layer_number}:globalid:{guid}", "globalid"
    if properties.get("nvenetbasid") is not None:
        netbas = integer(properties["nvenetbasid"], "nvenetbasid")
        if netbas:
            return f"nve-grid:Nettanlegg4:{layer_number}:nvenetbasid:{netbas}", "nvenetbasid"
    oid = integer(properties.get("objectid"), "objectid")
    return f"nve-grid:Nettanlegg4:{layer_number}:objectid:{oid}", "service/layer-qualified OBJECTID"


def normalize_grid(feature, config, run_id, ingested_at):
    require_object(feature, "grid Feature", ("type", "properties", "geometry"))
    props = require_object(feature["properties"], "grid properties", ("objectid",))
    identity, method = grid_identity(props, config["arcgis_layer"])
    name = source_text(props.get("navn"), "navn")
    return make_feature(
        identity, "nve-grid", config["layer_id"], name or f"{config['layer_id']} · {props['objectid']}",
        feature["geometry"], {
            "source_properties": props, "source_service": "Nettanlegg4",
            "source_layer": config["arcgis_layer"], "identity_method": method,
            "owner": props.get("eier"), "voltage_kv": number(props.get("spenning_kv"), "spenning_kv"),
            "network_level": props.get("nvenettnivaa"),
            "geographic_precision": "provider geometry; accuracy varies",
            "attribution": "NVE", "license_notice": "See archived GIS service/layer metadata",
        }, observed(props.get("kildeendretdato"), "kildeendretdato", epoch_ms=True),
        config["source_url"], run_id, ingested_at, extent=NVE_EXTENT,
    )

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Bounded HTTP, bronze archive and ArcGIS reconciliation
# Query POSTs below are read-only. Only public allowlisted endpoints are used.
# 401/403/other 4xx are never retried. No raw source/normalized snapshot is deleted.

# CELL ********************

class LayerSpool:
    def __init__(self, root, config, run_id, ingested_at):
        self.config, self.run_id, self.ingested_at = config, run_id, ingested_at
        artifact = config.get("artifact_id", config["layer_id"])
        self.local = Path(root) / config["source_id"] / artifact
        self.local.mkdir(parents=True, exist_ok=False)
        self.raw = gzip.open(self.local / "responses.jsonl.gz", "wt", encoding="utf-8")
        self.normalized = None
        self.row_count = self.unmapped_count = self.rejected_count = 0
        self.details = {}
        self.closed = False
        self.uri = f"Files/bronze/geo_context/{config['source_id']}/{run_id}/{artifact}"

    def record(self, value):
        json.dump(value, self.raw, ensure_ascii=False, allow_nan=False)
        self.raw.write("\n")

    def emit(self, row):
        if set(row) != {name for name, _ in FEATURE_FIELDS}:
            raise SourceError("normalized row does not match the exact feature schema")
        if row["source_id"] != self.config["source_id"] or row["layer_id"] != self.config["layer_id"]:
            raise SourceError("normalized row crossed its source/layer boundary")
        if not row["feature_id"] or not row["run_id"]:
            raise SourceError("normalized row has no stable identity/run ID")
        if self.row_count % NORMALIZED_SHARD_ROWS == 0:
            if self.normalized is not None:
                self.normalized.close()
            directory = self.local / "normalized"
            directory.mkdir(exist_ok=True)
            filename = directory / f"part-{self.row_count // NORMALIZED_SHARD_ROWS:06d}.jsonl.gz"
            self.normalized = gzip.open(filename, "wt", encoding="utf-8")
        self.normalized.write(json_text(row) + "\n")
        self.row_count += 1
        if not row["geometry_json"]:
            self.unmapped_count += 1
            self.rejected_count += int(json.loads(row["properties_json"]).get("geometry_status") == "rejected")

    def close(self):
        if not self.closed:
            self.raw.close()
            if self.normalized is not None:
                self.normalized.close()
            self.closed = True

    def manifest(self, state, message):
        return {
            "run_id": self.run_id, "source_id": self.config["source_id"],
            "artifact_id": self.config.get("artifact_id", self.config["layer_id"]),
            "layer_id": self.config["layer_id"], "source_url": self.config["source_url"],
            "state": state, "message": message, "ingested_at": self.ingested_at,
            "row_count": self.row_count, "unmapped_count": self.unmapped_count,
            "rejected_count": self.rejected_count, "details": self.details,
        }

    def archive(self, fs, state, message):
        self.close()
        fs.mkdirs(self.uri)
        for file in sorted(self.local.rglob("*.gz")):
            relative = file.relative_to(self.local).as_posix()
            target = f"{self.uri}/{relative}"
            fs.mkdirs(target.rsplit("/", 1)[0])
            if fs.cp(file.as_uri(), target) is False:
                raise SourceError(f"bronze upload failed: {target}")
        if fs.put(f"{self.uri}/attempt.json", json_text(self.manifest(state, message)), True) is False:
            raise SourceError("bronze attempt log upload failed")


def retry_seconds(header, attempt, now):
    delay = float(2 ** attempt)
    if header:
        try:
            delay = max(delay, float(header))
        except ValueError:
            try:
                delay = max(delay, (parsedate_to_datetime(header) - now).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass  # Invalid Retry-After is not a data validation failure.
    return min(60.0, max(0.0, delay))


class PublicHTTP:
    def __init__(self, session, spool, attempts=5):
        self.session, self.spool, self.attempts = session, spool, attempts

    def get_json(self, url, params=None, method="GET"):
        query = params or {}
        arcgis = any(
            url == base or re.fullmatch(re.escape(base) + r"/\d+(?:/query)?", url)
            for base in (GRID_SERVICE, HYDRO_SERVICE, PRICE_AREA_SERVICE)
        )
        if url not in {HYDRO_URL, RESERVOIR_URL, BALANCE_URL, FLOW_URL, FREQUENCY_URL, UMM_URL, NATURAL_EARTH_URL} and not arcgis:
            raise SourceError("HTTP endpoint is not an approved public source")
        if method not in ("GET", "POST") or (method == "POST" and not (arcgis and url.endswith("/query"))):
            raise SourceError("Only GET and read-only ArcGIS query POST are allowed")
        bounded_boundary = url == NATURAL_EARTH_URL or url.startswith(PRICE_AREA_SERVICE)
        for attempt in range(self.attempts):
            try:
                response = self.session.request(
                    method, url, params=query if method == "GET" else None,
                    data=query if method == "POST" else None, timeout=(15, 90),
                    allow_redirects=False,
                    **({"stream": True} if bounded_boundary else {}),
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                self.spool.record({"url": url, "method": method, "parameters": query,
                                   "attempt": attempt + 1, "transport_error": str(exc)[:1000]})
                if attempt + 1 == self.attempts:
                    raise SourceError(f"public source transport failure: {url}") from exc
                time.sleep(retry_seconds(None, attempt, datetime.now(timezone.utc)))
                continue
            try:
                status = response.status_code
                if bounded_boundary:
                    raw = bytearray()
                    for chunk in response.iter_content(65536):
                        raw.extend(chunk)
                        if len(raw) > BOUNDARY_MAX_BYTES:
                            self.spool.record({"url": url, "http_status": status,
                                               "error": "boundary response exceeded byte budget", "bytes_read": len(raw),
                                               "response_prefix": bytes(raw[:4096]).decode("utf-8", errors="replace")})
                            raise SourceError("boundary response exceeds bounded byte budget")
                    try:
                        text = raw.decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise SourceError("boundary response is not UTF-8 JSON") from exc
                else:
                    text = response.text
                self.spool.record({
                    "url": url, "method": method, "parameters": query,
                    "attempt": attempt + 1, "received_at": utc_text(datetime.now(timezone.utc)),
                    "http_status": status, "response_text": text,
                })
                if (status == 429 or 500 <= status < 600) and attempt + 1 < self.attempts:
                    time.sleep(retry_seconds(response.headers.get("Retry-After"), attempt, datetime.now(timezone.utc)))
                    continue
                if status != 200:
                    raise SourceError(f"public source HTTP {status}: {url}; response retained in bronze")
                if url == NATURAL_EARTH_URL:
                    digest = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
                    if digest != NATURAL_EARTH_BLOB_SHA:
                        raise SourceError("pinned Natural Earth content hash changed; review source/version before publishing")
                try:
                    result = json.loads(text, parse_constant=reject_json_constant)
                except ValueError as exc:
                    raise SourceError(f"non-JSON response: {url}; response retained in bronze") from exc
                if isinstance(result, dict) and result.get("error"):
                    raise SourceError(f"source returned an error envelope: {url}")
                return result
            finally:
                response.close()
        raise SourceError("HTTP retry budget exhausted")


def arcgis_inventory(http, url):
    count_body = require_object(http.get_json(
        f"{url}/query", {"f": "json", "where": "1=1", "returnCountOnly": "true"}, "POST",
    ), "ArcGIS count", ("count",))
    count = integer(count_body["count"], "ArcGIS count", minimum=1)
    body = require_object(http.get_json(
        f"{url}/query", {"f": "json", "where": "1=1", "returnIdsOnly": "true"}, "POST",
    ), "ArcGIS IDs", ("objectIds", "objectIdFieldName"))
    ids = [integer(value, "ArcGIS objectId") for value in require_list(body["objectIds"], "objectIds", True)]
    if len(ids) != count or len(set(ids)) != count or body.get("exceededTransferLimit"):
        raise SourceError("ArcGIS count/ID inventory incomplete or duplicated")
    field = source_text(body["objectIdFieldName"], "objectIdFieldName", True)
    ids.sort()
    digest = hashlib.sha256()
    for oid in ids:
        digest.update(f"{oid}\n".encode("ascii"))
    return ids, field, digest.hexdigest()


def arcgis_page_features(body, requested_ids, oid_field):
    body = require_object(body, "ArcGIS GeoJSON", ("type", "features"))
    if body["type"] != "FeatureCollection" or body.get("exceededTransferLimit"):
        raise SourceError("ArcGIS response is not a complete GeoJSON FeatureCollection")
    features = require_list(body["features"], "GeoJSON features")
    found = []
    for feature in features:
        require_object(feature, "ArcGIS feature", ("type", "properties", "geometry"))
        if feature["type"] != "Feature":
            raise SourceError("ArcGIS member is not a Feature")
        properties = require_object(feature["properties"], "ArcGIS properties", (oid_field,))
        found.append(integer(properties[oid_field], oid_field))
    if len(found) != len(requested_ids) or len(set(found)) != len(found) or set(found) != set(requested_ids):
        raise SourceError("ArcGIS page IDs missing, repeated or unexpected")
    return features


def iter_arcgis(http, url, details, expected_count=None):
    service_url = url.rsplit("/", 1)[0]
    service = require_object(http.get_json(service_url, {"f": "json"}), "ArcGIS service metadata")
    metadata = require_object(http.get_json(url, {"f": "json"}), "ArcGIS layer metadata", ("fields", "maxRecordCount"))
    fields = require_list(metadata["fields"], "ArcGIS fields", True)
    field_names = {require_object(field, "ArcGIS field", ("name",))["name"] for field in fields}
    limit = min(ARCGIS_CHUNK_SIZE, integer(metadata["maxRecordCount"], "maxRecordCount", minimum=1))
    ids, oid_field, digest = arcgis_inventory(http, url)
    if expected_count is not None and len(ids) != expected_count:
        raise SourceError(f"ArcGIS inventory expected {expected_count} features, found {len(ids)}")
    if oid_field not in field_names:
        raise SourceError("ArcGIS object ID field is absent from layer schema")
    details.update({
        "initial_count": len(ids), "object_id_field": oid_field, "id_sha256": digest,
        "service_notice": service.get("copyrightText"), "layer_notice": metadata.get("copyrightText"),
        "layer_description": metadata.get("description"),
        "page_size": limit, "reconciled": False,
    })
    fetched = 0
    for start in range(0, len(ids), limit):
        chunk = ids[start:start + limit]
        body = http.get_json(f"{url}/query", {
            "f": "geojson", "objectIds": ",".join(str(oid) for oid in chunk),
            "outFields": "*", "outSR": "4326", "returnGeometry": "true", "returnZ": "false",
        }, "POST")
        features = arcgis_page_features(body, chunk, oid_field)
        fetched += len(features)
        yield from features
        # Only the current response/page is retained; no national feature list.
        del features, body
    final_ids, final_field, final_digest = arcgis_inventory(http, url)
    if fetched != len(ids) or len(final_ids) != len(ids) or final_digest != digest or final_field != oid_field:
        raise SourceError("ArcGIS inventory changed during import; refusing partial/stale deletion")
    details.update({"final_count": len(final_ids), "fetched_count": fetched, "reconciled": True})

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Source-specific normalization — no inferred precise locations

# CELL ********************

def normalize_hydro(record, candidates, run_id, ingested_at):
    require_object(record, "Powerplant record", (
        "VannKraftverkID", "Navn", "MaksYtelse", "MidProd_91_20",
        "HovedEier", "ElspotomraadeNummer", "ErIDrift", "Kraftverkstatus",
    ))
    plant_id = integer(record["VannKraftverkID"], "VannKraftverkID", 1)
    geometry = candidates[0]["geometry"] if len(candidates) == 1 else None
    join_state = "matched" if len(candidates) == 1 else ("ambiguous GIS join" if candidates else "no GIS match")
    area_number = record.get("ElspotomraadeNummer")
    area = f"NO{integer(area_number, 'ElspotomraadeNummer')}" if area_number not in (None, "") else None
    return make_feature(
        f"nve-hydro:Powerplant:{plant_id}", "nve-hydro", "hydro-plants",
        source_text(record["Navn"], "Navn", True), geometry, {
            "source_properties": record, "gis_properties": [item["properties"] for item in candidates],
            "gis_source_url": f"{HYDRO_SERVICE}/0", "gis_join_state": join_state,
            "plant_id": plant_id, "owner": record["HovedEier"],
            "installed_capacity_mw": number(record["MaksYtelse"], "MaksYtelse"),
            "mean_annual_production_gwh_1991_2020": number(record["MidProd_91_20"], "MidProd_91_20"),
            "production_basis": "1991-2020 mean annual production; NOT current generation",
            "gross_head_m": number(record.get("BruttoFallhoyde_M"), "BruttoFallhoyde_M"),
            "max_discharge_m3_s": number(record.get("Slukeevne"), "Slukeevne"),
            "price_area": area, "in_operation": record["ErIDrift"], "plant_status": record["Kraftverkstatus"],
            "geographic_precision": "provider plant point" if geometry is not None else "unmapped",
            "attribution": "NVE", "registry_license": "NLOD",
            "gis_license_notice": "See archived Vannkraft1 service/layer metadata",
        }, None, HYDRO_URL, run_id, ingested_at, extent=NVE_EXTENT,
    )


def normalize_reservoir(record, run_id, ingested_at):
    require_object(record, "Magasinstatistikk record", (
        "omrType", "omrnr", "iso_aar", "iso_uke", "dato_Id",
        "fyllingsgrad", "kapasitet_TWh", "fylling_TWh", "endring_fyllingsgrad",
    ))
    kind = source_text(record["omrType"], "omrType", True).upper()
    area_id = integer(record["omrnr"], "omrnr")
    year, week = integer(record["iso_aar"], "iso_aar", 1900), integer(record["iso_uke"], "iso_uke", 1)
    try:
        datetime.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise SourceError("invalid reservoir ISO week/year") from exc
    fill = number(record["fyllingsgrad"], "fyllingsgrad")
    change = number(record["endring_fyllingsgrad"], "endring_fyllingsgrad")
    capacity = number(record["kapasitet_TWh"], "kapasitet_TWh")
    stored = number(record["fylling_TWh"], "fylling_TWh")
    if fill is None or not 0 <= fill <= 1 or (change is not None and not -1 <= change <= 1):
        raise SourceError("reservoir fill/change must be fractions, not percent values")
    if any(value is not None and value < 0 for value in (capacity, stored)):
        raise SourceError("reservoir TWh cannot be negative")
    area = f"NO{area_id}" if kind == "EL" else ("NO" if kind == "NO" else None)
    geometry = point_for(area)
    return make_feature(
        f"nve-reservoir:Magasinstatistikk:{kind}:{area_id}", "nve-reservoir", "reservoirs",
        f"{area or f'{kind} {area_id}'} · {year}-W{week:02d}", geometry, {
            "source_properties": record, "area_type": kind, "area_number": area_id,
            "price_area": area if kind == "EL" else None, "iso_year": year, "iso_week": week,
            "fill_pct": fill * 100, "stored_twh": stored, "capacity_twh": capacity,
            "change_percentage_points": change * 100 if change is not None else None,
            "geographic_precision": "area representative point" if geometry is not None else "unmapped area",
            "position_is_derived": geometry is not None, "represents": "area aggregate; not an individual reservoir",
            "observed_at_precision": "provider weekly date at 00:00Z; not an exact measurement time",
            "attribution": "NVE Magasinstatistikk",
        }, observed(record["dato_Id"], "dato_Id", date_only=True, required=True),
        RESERVOIR_URL, run_id, ingested_at,
    )


def normalize_balance(body, run_id, ingested_at):
    require_object(body, "Statnett overview", ("MeasuredAt", *BALANCE_METRICS.keys()))
    overview_at = observed(body["MeasuredAt"], "MeasuredAt", epoch_ms=True, required=True)
    metrics, metric_times, source_rows = {}, {}, {}
    for field, normalized in BALANCE_METRICS.items():
        entries = require_list(body[field], field)
        country_rows = []
        for entry in entries:
            require_object(entry, field + " entry", ("countryCode", "value"))
            if entry["countryCode"] == "NO":  # header countryCode is null; never row[0]
                country_rows.append(entry)
        if len(country_rows) > 1:
            raise SourceError(f"duplicate NO balance metric in {field}")
        row = country_rows[0] if country_rows else {}
        metrics[normalized] = number(row.get("value"), field)
        metric_times[normalized] = observed(row.get("measuredAt"), field + ".measuredAt", epoch_ms=True)
        source_rows[field] = row or None
    if all(metrics[key] is None for key in ("production_mw", "consumption_mw", "net_exchange_mw")):
        raise SourceError("Statnett overview contains no Norwegian balance values")
    properties = {
        "country_code": "NO", **metrics, "each_observed_at": metric_times,
        "overview_observed_at": overview_at, "source_properties": source_rows,
        "observed_at_scope": "overview timestamp; metric observation times differ or are unknown",
        "geographic_precision": "country representative point", "position_is_derived": True,
        "attribution": "Statnett", "net_exchange_convention": "source signed value, unmodified",
    }
    for key, timestamp in metric_times.items():
        properties[key.removesuffix("_mw") + "_observed_at"] = timestamp
    return make_feature(
        "statnett:ProductionConsumption:NO", "statnett", "power-balance",
        "Norway · production / consumption", point_for("NO"), properties,
        overview_at, BALANCE_URL, run_id, ingested_at,
    )


def normalize_flow(record, run_id, ingested_at):
    require_object(record, "Statnett flow", ("OutAreaElspotId", "InAreaElspotId", "Value", "MeasureDate"))
    out_area = source_text(record["OutAreaElspotId"], "OutAreaElspotId", True).upper()
    in_area = source_text(record["InAreaElspotId"], "InAreaElspotId", True).upper()
    if out_area == in_area:
        raise SourceError("flow endpoints are identical")
    if not (is_norwegian(out_area) or is_norwegian(in_area)):
        return None
    value = number(record["Value"], "flow.Value")
    start, end = (in_area, out_area) if value is not None and value < 0 else (out_area, in_area)
    geometry = None
    if start in AREA_POINTS and end in AREA_POINTS:
        geometry = {"type": "LineString", "coordinates": [list(AREA_POINTS[start]), list(AREA_POINTS[end])]}
    # Stable border identity does not change when the source orientation/value flips.
    identity = ":".join(sorted((out_area, in_area)))
    return make_feature(
        f"statnett:PhysicalFlowMap:{identity}", "statnett", "power-flows",
        f"{start} → {end}" if value not in (None, 0) else f"{out_area} ↔ {in_area}",
        geometry, {
            "source_properties": record, "source_out_area": out_area, "source_in_area": in_area,
            "from_area": start, "to_area": end, "signed_flow_mw": value,
            "flow_mw": abs(value) if value is not None else None,
            "direction_known": value not in (None, 0),
            "direction_convention": "source OutArea→InArea; negative Value reverses displayed endpoints",
            "geographic_precision": "schematic area-representative segment; NOT cable coordinates",
            "position_is_derived": True, "unlocated_endpoints": [area for area in (out_area, in_area) if area not in AREA_POINTS],
            "attribution": "Statnett",
        }, observed(record["MeasureDate"], "MeasureDate", epoch_ms=True, required=True),
        FLOW_URL, run_id, ingested_at,
    )


def normalize_frequency(body, window_start, window_end, run_id, ingested_at):
    require_object(body, "Statnett frequency", ("Measurements",))
    measurements = require_list(body["Measurements"], "Measurements", True)
    latest = None
    seen = set()
    for measurement in measurements:
        if not isinstance(measurement, list) or len(measurement) != 2:
            raise SourceError("frequency measurement must be [epoch_ms, Hz]")
        stamp = parse_time(measurement[0], "frequency timestamp", epoch_ms=True, required=True)
        hz = number(measurement[1], "frequency Hz")
        if hz is None or not 0 < hz < 100:
            raise SourceError("frequency measurement is missing or invalid")
        if stamp in seen:
            raise SourceError("duplicate frequency measurement timestamp")
        seen.add(stamp)
        if window_start <= stamp <= window_end and (latest is None or stamp > latest[0]):
            latest = (stamp, hz)
    if latest is None:
        raise SourceError("frequency has no measurements in the requested recent three-minute window")
    return make_feature(
        "statnett:Frequency:Nordic", "statnett", "grid-frequency", "Nordic grid frequency",
        point_for("NO"), {
            "frequency_hz": latest[1], "source_latest_measurement": [int(latest[0].timestamp() * 1000), latest[1]],
            "window_start": utc_text(window_start), "window_end": utc_text(window_end),
            "sample_count": len(measurements), "observation_scope": "Nordic synchronous grid, not an individual station",
            "geographic_precision": "country representative point", "position_is_derived": True,
            "attribution": "Statnett", "refresh_semantics": "snapshot/on-demand, no real-time SLA",
        }, utc_text(latest[0]), FREQUENCY_URL, run_id, ingested_at,
    )


def umm_identity(message):
    require_object(message, "UMM message", ("messageId", "version", "publicationDate", "messageType", "eventStatus", "isOutdated"))
    try:
        identity = str(UUID(source_text(message["messageId"], "messageId", True)))
    except ValueError as exc:
        raise SourceError("UMM messageId is not a GUID") from exc
    if not isinstance(message["isOutdated"], bool):
        raise SourceError("UMM isOutdated must be boolean")
    return identity, integer(message["version"], "UMM version", 1)


def umm_area_references(message):
    """Keep every area and both endpoints of every affected transmission unit."""
    refs = []
    for area in require_list(message.get("areas", []), "UMM areas"):
        require_object(area, "UMM area")
        refs.append({"name": source_text(area.get("name"), "area.name"),
                     "code": source_text(area.get("code"), "area.code")})
    for field in UMM_UNIT_FIELDS:
        for unit in require_list(message.get(field, []), "UMM " + field):
            require_object(unit, "UMM unit")
            pairs = (("inAreaName", "inAreaEic"), ("outAreaName", "outAreaEic")) if field == "transmissionUnits" else (("areaName", "areaEic"),)
            for name_field, code_field in pairs:
                name = source_text(unit.get(name_field), name_field)
                code = source_text(unit.get(code_field), code_field)
                if name or code:
                    refs.append({"name": name, "code": code})
    return refs


def update_area_crosswalk(crosswalk, message):
    """Only provider-returned EIC/name pairs are evidence; no EIC string guessing."""
    for ref in umm_area_references(message):
        name, code = ref["name"].upper(), ref["code"].upper()
        if code and name:
            if code in crosswalk and crosswalk[code] != name:
                raise SourceError("UMM EIC/name association conflicts within the publication window")
            crosswalk[code] = name


def resolve_umm_areas(message, crosswalk):
    refs, seen = [], set()
    for ref in umm_area_references(message):
        name, code = ref["name"].upper(), ref["code"].upper()
        if name and code in crosswalk and name != crosswalk[code]:
            raise SourceError("UMM area name conflicts with observed EIC crosswalk")
        resolved = crosswalk.get(code) or name or None
        key = (resolved, code)
        if key not in seen:
            seen.add(key)
            refs.append({
                **ref, "area": resolved,
                "resolution": "provider EIC/name pair in window" if code in crosswalk else ("provider area name" if name else "unresolved"),
                "mapped": resolved in AREA_POINTS,
            })
    return refs


def umm_intervals(message, now=None):
    intervals, capacities = [], []

    def add_interval(record, label):
        start = parse_time(record.get("eventStart"), label + ".eventStart")
        stop = parse_time(record.get("eventStop"), label + ".eventStop")
        if start is not None and stop is not None and stop < start:
            raise SourceError("UMM eventStop precedes eventStart")
        if start is not None or stop is not None:
            intervals.append((start, stop))
        return start, stop

    for field in UMM_UNIT_FIELDS:
        for unit in require_list(message.get(field, []), field):
            require_object(unit, "UMM unit")
            for period in require_list(unit.get("timePeriods", []), "timePeriods"):
                require_object(period, "UMM time period")
                _, stop = add_interval(period, "timePeriod")
                value = number(period.get("unavailableCapacity"), "unavailableCapacity")
                if value is not None:
                    if value < 0:
                        raise SourceError("UMM unavailableCapacity cannot be negative")
                    if now is None or stop is None or stop > now:
                        capacities.append(value)
    # A root start/stop can enclose gaps between unit periods. Do not count that
    # entire envelope as one continuous unavailability interval.
    if not intervals:
        add_interval(message, "UMM")
    durations = [(stop - start).total_seconds() / 3600 for start, stop in intervals
                 if start is not None and stop is not None and (now is None or stop > now)]
    return intervals, max(capacities) if capacities else None, max(durations) if durations else None


def umm_phase(intervals, now):
    if any(start is not None and start <= now and (stop is None or stop > now) for start, stop in intervals):
        return "ongoing"
    if any(start is not None and start > now for start, _ in intervals):
        return "future"
    if intervals and all(stop is not None and stop <= now for _, stop in intervals):
        return "ended"
    return "unknown"


def importance_rules(unavailability_type, unavailable_mw, duration_hours, relevance, eligible):
    kind = str(unavailability_type).lower() if unavailability_type is not None else ""
    known_type = kind in ("1", "2", "unplanned", "planned")
    if not eligible or relevance != "direct" or (not known_type and unavailable_mw is None):
        return {
            "importance_score": None, "importance_bucket": "unranked", "importance_method": "rules-v1",
            "importance_reason": "Unranked: inactive/undatable, unknown Norwegian relevance, or no explicit unavailability type/capacity evidence. Not an official rank or grid safety score.",
        }
    score, reasons = 10, ["Direct Norwegian area relevance +10"]
    if kind in ("1", "unplanned"):
        score += 25
        reasons.append("Explicitly unplanned +25")
    else:
        reasons.append("Planned/unspecified unavailability +0")
    capacity_points = 35 if unavailable_mw is not None and unavailable_mw >= 1000 else (20 if unavailable_mw is not None and unavailable_mw >= 100 else (5 if unavailable_mw is not None and unavailable_mw > 0 else 0))
    score += capacity_points
    reasons.append(f"Maximum single-unit interval unavailable MW={unavailable_mw}; +{capacity_points} (not summed system impact)")
    duration_points = 15 if duration_hours is not None and duration_hours >= 24 else (5 if duration_hours is not None and duration_hours >= 4 else 0)
    score += duration_points
    reasons.append(f"Longest declared continuous interval hours={duration_hours}; +{duration_points}")
    reasons.append("Not an official Nord Pool rank or grid safety score")
    return {
        "importance_score": score, "importance_bucket": "high" if score >= 65 else ("medium" if score >= 35 else "low"),
        "importance_method": "rules-v1", "importance_reason": "; ".join(reasons),
    }


def normalize_umm(message, crosswalk, now, coverage, run_id, ingested_at):
    identity, version = umm_identity(message)
    if message["isOutdated"]:
        return None  # never resurrect an older version; all revisions remain in bronze
    publication = observed(message["publicationDate"], "publicationDate", required=True)
    areas = resolve_umm_areas(message, crosswalk)
    norwegian = any(is_norwegian(ref["area"]) for ref in areas)
    unresolved = not areas or any(ref["area"] not in AREA_POINTS for ref in areas)
    if not norwegian and not unresolved:
        return None  # confidently foreign-only; raw still retained
    relevance = "direct" if norwegian else "unknown"
    intervals, unavailable_mw, duration = umm_intervals(message, now)
    phase = umm_phase(intervals, now)
    raw_status = str(message["eventStatus"]).lower()
    cancelled = raw_status in ("3", "dismissed")
    active = raw_status in ("1", "active") and not message.get("cancellationReason")
    if phase == "ended" and active:
        return None
    eligible = active and phase in ("ongoing", "future") and norwegian
    mapped_areas = sorted({ref["area"] for ref in areas if ref["mapped"]})
    geometry = None
    if len(mapped_areas) == 1:
        geometry = point_for(mapped_areas[0])
    elif mapped_areas:
        geometry = {"type": "MultiPoint", "coordinates": [list(AREA_POINTS[area]) for area in mapped_areas]}
    starts = [start for start, _ in intervals if start is not None]
    stops = [stop for _, stop in intervals if stop is not None]
    properties = {
        "source_message": message, "message_id": identity, "version": version,
        "publication_date": publication, "message_type": message["messageType"],
        "event_status": message["eventStatus"], "is_outdated": False, "is_cancelled": cancelled,
        "cancellation_reason": message.get("cancellationReason"), "event_phase": phase,
        "map_eligible": eligible, "affected_areas": areas, "norwegian_relevance": relevance,
        "unmapped_affected_area_count": sum(not ref["mapped"] for ref in areas),
        "event_start": utc_text(min(starts)) if starts else None,
        "event_stop": utc_text(max(stops)) if stops and all(stop is not None for _, stop in intervals) else None,
        "unavailable_mw": unavailable_mw,
        "unavailable_mw_basis": "maximum reported single-unit interval; not aggregate system impact",
        "duration_hours": duration, "duration_basis": "longest declared continuous interval, not sum of intervals",
        "geographic_precision": "area representative point(s); NOT affected asset coordinates",
        "position_is_derived": geometry is not None, "coverage": coverage,
        "attribution": "Nord Pool UMM / originating publisher",
        **importance_rules(message.get("unavailabilityType"), unavailable_mw, duration, relevance, eligible),
    }
    label = source_text(message.get("unavailabilityReason") or message.get("otherMarketUnits") or message.get("publisherName"), "UMM label")
    return make_feature(
        f"nordpool:UMM:{identity}", "nordpool", "umm", label or f"UMM {identity}", geometry,
        properties, publication, UMM_URL, run_id, ingested_at,
    )


def fetch_umm_threads(http, now, details):
    # Floor the query endpoints to seconds so client/server inclusive bounds agree.
    stop = now.astimezone(timezone.utc).replace(microsecond=0)
    start = stop - timedelta(days=UMM_HORIZON_DAYS)
    filters = {
        "publicationStartDate": utc_text(start), "publicationStopDate": utc_text(stop),
        "IncludeOutdated": "true",
    }
    latest, seen, crosswalk = {}, set(), {}
    total, skip, first_identity = None, 0, None
    previous_publication = None
    while total is None or skip < total:
        body = require_object(http.get_json(UMM_URL, {**filters, "skip": skip, "limit": UMM_PAGE_SIZE}),
                              "UMM page", ("items", "total"))
        page_total = integer(body["total"], "UMM total")
        if page_total > UMM_MAX_REVISIONS:
            raise SourceError(f"UMM 30-day coverage requires {page_total} revisions, exceeds bounded budget {UMM_MAX_REVISIONS}")
        if total is None:
            total = page_total
        if page_total != total:
            raise SourceError("UMM total changed during pagination")
        items = require_list(body["items"], "UMM items")
        if len(items) != min(UMM_PAGE_SIZE, total - skip):
            raise SourceError("UMM page is incomplete or exceeds the requested page size")
        for message in items:
            key = umm_identity(message)
            if key in seen:
                raise SourceError("UMM duplicate revision across pages; refusing incomplete snapshot")
            seen.add(key)
            publication = parse_time(message["publicationDate"], "publicationDate", required=True)
            if not start <= publication <= stop:
                raise SourceError("UMM publication filters were not honored")
            if previous_publication is not None and publication > previous_publication:
                raise SourceError("UMM default PublicationDate DESC ordering was not honored")
            previous_publication = publication
            if first_identity is None:
                first_identity = key
            update_area_crosswalk(crosswalk, message)
            prior = latest.get(key[0])
            if prior is None or key[1] > integer(prior["version"], "UMM version", 1):
                latest[key[0]] = message
        skip += len(items)
    # Recheck the fixed window, not the unbounded global history total.
    check = require_object(http.get_json(UMM_URL, {**filters, "skip": 0, "limit": 1}),
                           "UMM final count check", ("items", "total"))
    first = require_list(check["items"], "UMM final items")
    if integer(check["total"], "UMM final total") != total or len(first) != min(total, 1):
        raise SourceError("UMM final count changed during pagination")
    if first and umm_identity(first[0]) != first_identity:
        raise SourceError("UMM first revision changed during pagination")
    if len(seen) != total:
        raise SourceError("UMM revision count did not reconcile")
    coverage = {
        "publication_start": utc_text(start), "publication_stop": utc_text(stop),
        "publication_horizon_days": UMM_HORIZON_DAYS, "revision_count": total,
        "scope": "current/future Norwegian or unlocated notices and cancellations published within this window only",
        "limitation": "Older still-active/future notices are outside coverage; snapshot/on-demand, not real-time",
        "area_lookup": "provider names / EIC-name pairs in responses; infrastructure lookup unavailable (HTTP 403 at authoring)",
    }
    details.update({"coverage": coverage, "reconciled": True, "thread_count": len(latest)})
    return latest, crosswalk, coverage

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Reservoir coverage and evidence-only asset matching
# Auxiliary inputs come from existing Delta snapshots, not fresh national downloads.
# Polygons remain source geometries; aggregation concatenates complete country
# components without drawing, clipping, repairing or inferring new boundaries.

# CELL ********************

def properties_object(text, label):
    try:
        value = json.loads(text, parse_constant=reject_json_constant)
    except (ValueError, TypeError) as exc:
        raise SourceError(f"{label}: invalid properties JSON") from exc
    return require_object(value, label)


def polygon_parts(geometry):
    require_object(geometry, "boundary geometry", ("type", "coordinates"))
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise GeometryError("required area boundary is not Polygon/MultiPolygon")


def price_area_code(value):
    text = re.sub(r"\s+", "", source_text(value, "NVE budomr", True)).upper()
    if text not in PRICE_AREA_CODES:
        raise SourceError(f"unsupported/unverified NVE price area: {text}")
    return text


def boundary_record(kind, code, country, geometry, source_properties, acquired_at, bronze_uri):
    polygon_parts(geometry)
    geometry_bounds(geometry, NVE_EXTENT if kind == "price_area" else None)
    country_boundary = kind == "country"
    return {
        "area_kind": kind, "area_code": code, "country_code": country, "geometry": geometry,
        "source_url": NATURAL_EARTH_URL if country_boundary else PRICE_AREA_URL,
        "terms_url": NATURAL_EARTH_TERMS_URL if country_boundary else NVE_MAP_TERMS_URL,
        "license": "Public domain" if country_boundary else "NLOD (NVE map-data distribution)",
        "attribution": "Made with Natural Earth" if country_boundary else "NVE / Statnett bidding areas",
        "source_version": f"Natural Earth 5.1.2 / Git blob {NATURAL_EARTH_BLOB_SHA}" if country_boundary else "Nettomraader/0 live reconciled snapshot",
        "source_properties": source_properties, "acquired_at": acquired_at, "bronze_uri": bronze_uri,
        "geometry_sha256": hashlib.sha256(json_text(geometry).encode("utf-8")).hexdigest(),
    }


def validate_boundary(boundary):
    require_object(boundary, "boundary", (
        "area_kind", "area_code", "country_code", "geometry", "source_url", "terms_url",
        "license", "attribution", "source_version", "source_properties", "acquired_at",
        "bronze_uri", "geometry_sha256",
    ))
    kind, code, country = boundary["area_kind"], boundary["area_code"], boundary["country_code"]
    if kind == "price_area":
        if code not in PRICE_AREA_CODES or country != "NO":
            raise SourceError("price boundary has an invalid verified area/country code")
        expected_url, terms = PRICE_AREA_URL, NVE_MAP_TERMS_URL
        raw = require_list(boundary["source_properties"], "price boundary source properties", True)
        if len(raw) != 1 or price_area_code(require_object(raw[0], "price properties", ("budomr",))["budomr"]) != code:
            raise SourceError("price boundary code is not supported by its source properties")
    elif kind == "country":
        if code != country or country not in COUNTRY_COMPONENTS:
            raise SourceError("country boundary has an unexpected country code")
        expected_url, terms = NATURAL_EARTH_URL, NATURAL_EARTH_TERMS_URL
        raw = require_list(boundary["source_properties"], "country source components", True)
        components = [require_object(item, "country properties", ("ADM0_A3", "SOVEREIGNT"))["ADM0_A3"] for item in raw]
        if len(components) != len(set(components)) or set(components) != set(COUNTRY_COMPONENTS[country]):
            raise SourceError(f"{country}: incomplete/duplicate country land components")
        if any(item["SOVEREIGNT"] != COUNTRY_NAMES[country] for item in raw):
            raise SourceError("country component sovereignty/provenance does not match")
        if NATURAL_EARTH_BLOB_SHA not in boundary["source_version"]:
            raise SourceError("cached country geometry is not the pinned Natural Earth release")
    else:
        raise SourceError("unexpected reservoir boundary kind")
    if boundary["source_url"] != expected_url or boundary["terms_url"] != terms:
        raise SourceError("boundary provenance does not match approved sources/terms")
    if not boundary["license"] or not boundary["attribution"] or not str(boundary["bronze_uri"]).startswith("Files/bronze/geo_context/"):
        raise SourceError("boundary is missing license, attribution or raw-snapshot provenance")
    parse_time(boundary["acquired_at"], "boundary acquired_at", required=True)
    polygon_parts(boundary["geometry"])
    geometry_bounds(boundary["geometry"], NVE_EXTENT if kind == "price_area" else None)
    if hashlib.sha256(json_text(boundary["geometry"]).encode("utf-8")).hexdigest() != boundary["geometry_sha256"]:
        raise SourceError("boundary geometry digest does not reconcile")


def validate_boundary_coverage(boundaries):
    expected = {("price_area", code) for code in PRICE_AREA_CODES} | {("country", code) for code in COUNTRY_COMPONENTS}
    keys = []
    for boundary in boundaries:
        validate_boundary(boundary)
        keys.append((boundary["area_kind"], boundary["area_code"]))
    if len(keys) != len(expected) or set(keys) != expected:
        raise SourceError(f"reservoir boundary coverage incomplete/duplicated; expected {sorted(expected)}, found {sorted(keys)}")


def country_boundaries(body, acquired_at, bronze_uri):
    require_object(body, "Natural Earth GeoJSON", ("type", "features"))
    if body["type"] != "FeatureCollection":
        raise SourceError("Natural Earth response is not a FeatureCollection")
    features = require_list(body["features"], "Natural Earth features", True)
    if len(features) > 400:
        raise SourceError("Natural Earth country collection exceeds its bounded feature budget")
    component_country = {component: country for country, components in COUNTRY_COMPONENTS.items() for component in components}
    found = {}
    for feature in features:
        require_object(feature, "Natural Earth Feature", ("type", "properties", "geometry"))
        props = require_object(feature["properties"], "Natural Earth properties", ("ADM0_A3",))
        component = props["ADM0_A3"]
        if component not in component_country:
            continue
        country = component_country[component]
        if feature["type"] != "Feature" or props.get("SOVEREIGNT") != COUNTRY_NAMES[country] or component in found:
            raise SourceError("Natural Earth selected component has invalid/duplicate identity")
        polygon_parts(feature["geometry"])
        geometry_bounds(feature["geometry"])
        found[component] = feature
    if set(found) != set(component_country):
        raise SourceError(f"Natural Earth missing required land components: {sorted(set(component_country) - found.keys())}")
    result = []
    for country, components in COUNTRY_COMPONENTS.items():
        parts = [polygon for component in components for polygon in polygon_parts(found[component]["geometry"])]
        result.append(boundary_record(
            "country", country, country, {"type": "MultiPolygon", "coordinates": parts},
            [found[component]["properties"] for component in components], acquired_at, bronze_uri,
        ))
    return result


def cached_boundary_groups(rows, now, force, fs, details):
    groups = {"price_area": [], "country": []}
    if force:
        details["boundary_cache"] = {"reason": "manual all/force refresh"}
        return groups
    reasons = {}
    try:
        boundaries = []
        for row in rows:
            props = properties_object(row["properties_json"], "cached reservoir area")
            provenance = require_object(props.get("boundary_provenance"), "cached boundary provenance")
            boundary = {**provenance, "geometry": properties_object(row["geometry_json"], "cached area geometry")}
            validate_boundary(boundary)
            if row["source_url"] != boundary["source_url"]:
                raise SourceError("cached feature source URL disagrees with boundary provenance")
            boundaries.append(boundary)
        validate_boundary_coverage(boundaries)
    except SourceError as exc:
        details["boundary_cache"] = {"reason": "cache missing/invalid; reacquire", "validation": str(exc)}
        return groups
    for kind in groups:
        members = [boundary for boundary in boundaries if boundary["area_kind"] == kind]
        times = [parse_time(boundary["acquired_at"], "boundary acquired_at", required=True) for boundary in members]
        if any(stamp > now for stamp in times):
            reasons[kind] = "future cache timestamp; reacquire"
        elif kind == "price_area" and any(now - stamp >= timedelta(hours=24) for stamp in times):
            reasons[kind] = "price boundary cache older than 24h"
        elif not all(fs.exists(f"{boundary['bronze_uri']}/responses.jsonl.gz") for boundary in members):
            reasons[kind] = "raw boundary provenance missing; reacquire"
        else:
            groups[kind] = members
            reasons[kind] = "reused validated geometry; current statistics are rejoined"
    details["boundary_cache"] = reasons
    return groups


def reservoir_statistics_index(rows):
    index, without_overlay = {}, []
    for row in rows:
        if row["layer_id"] != "reservoirs" or row["source_id"] != "nve-reservoir":
            raise SourceError("reservoir statistics crossed source/layer boundaries")
        props = properties_object(row["properties_json"], "reservoir statistics")
        raw = require_object(props.get("source_properties"), "NVE reservoir source record", ("omrType", "omrnr"))
        kind = source_text(raw["omrType"], "omrType", True).upper()
        if kind == "EL":
            code = f"NO{integer(raw['omrnr'], 'omrnr')}"
            if code not in PRICE_AREA_CODES:
                without_overlay.append(row["feature_id"])
                continue
            if props.get("price_area") != code:
                raise SourceError("normalized price area disagrees with NVE EL code")
        elif kind == "NO":
            code = "NO"
        else:
            without_overlay.append(row["feature_id"])
            continue
        if code in index:
            raise SourceError(f"ambiguous/duplicate reservoir statistic for {code}")
        value = number(raw.get("fyllingsgrad"), "fyllingsgrad")
        if value is not None and not 0 <= value <= 1:
            raise SourceError("reservoir area filling must be a fraction in [0, 1]")
        index[code] = {"feature_id": row["feature_id"], "observed_at": row["observed_at"],
                       "source_url": row["source_url"], "source_properties": raw, "filling_fraction": value}
    return index, without_overlay


def reservoir_area_feature(boundary, statistics, run_id, ingested_at):
    validate_boundary(boundary)
    kind, code, country = boundary["area_kind"], boundary["area_code"], boundary["country_code"]
    stat = statistics.get(code) if country == "NO" else None
    fraction = stat["filling_fraction"] if stat is not None else None
    raw = stat["source_properties"] if stat is not None else None
    properties = {
        "area_code": code, "country_code": country, "area_kind": kind,
        "price_area": code if kind == "price_area" else None,
        "has_reservoir_data": fraction is not None, "filling_fraction": fraction,
        "fill_pct": fraction * 100 if fraction is not None else None,
        "capacity_twh": number(raw.get("kapasitet_TWh"), "kapasitet_TWh") if raw else None,
        "stored_twh": number(raw.get("fylling_TWh"), "fylling_TWh") if raw else None,
        "source_stats": raw, "statistics_feature_id": stat["feature_id"] if stat else None,
        "statistics_source_url": stat["source_url"] if stat else None,
        "iso_year": integer(raw["iso_aar"], "iso_aar") if raw and raw.get("iso_aar") is not None else None,
        "iso_week": integer(raw["iso_uke"], "iso_uke", 1) if raw and raw.get("iso_uke") is not None else None,
        "observed_at_precision": "provider weekly date; not an exact measurement time" if stat else None,
        "statistics_scope": (
            "Norway country aggregate of reporting reservoirs; NOT an island/site measurement"
            if country == "NO" and kind == "country"
            else ("NVE price-area reservoir aggregate" if kind == "price_area" else "no reservoir statistics from this source")
        ),
        "geographic_precision": (
            "NVE bidding-area overview boundary; not street-level"
            if kind == "price_area" else "Natural Earth 1:10m generalized country land coverage; not survey-grade"
        ),
        "country_components": list(COUNTRY_COMPONENTS[country]) if kind == "country" else [],
        "territorial_scope": "complete provider sovereign-country components, including islands" if kind == "country" else "provider bidding area",
        "boundary_provenance": {key: value for key, value in boundary.items() if key != "geometry"},
        "attribution": boundary["attribution"],
    }
    return make_feature(
        f"nve-reservoir:area:{kind}:{code}", "nve-reservoir", "reservoirs",
        f"{code} reservoir aggregate" if kind == "price_area" else f"{COUNTRY_NAMES[country]} country background",
        boundary["geometry"], properties, stat["observed_at"] if stat else None,
        boundary["source_url"], run_id, ingested_at,
    )


def produce_reservoir_areas(spool, http, cached_rows, statistics_rows, now, force, fs):
    groups = cached_boundary_groups(cached_rows, now, force, fs, spool.details)
    acquired_at = utc_text(now)
    if not groups["price_area"]:
        inventory = {}
        for feature in iter_arcgis(http, PRICE_AREA_URL, inventory, expected_count=5):
            props = require_object(feature["properties"], "Budomraade properties", ("budomr",))
            code = price_area_code(props["budomr"])
            groups["price_area"].append(boundary_record(
                "price_area", code, "NO", feature["geometry"], [props], acquired_at, spool.uri,
            ))
        spool.details["price_area_inventory"] = inventory
    if not groups["country"]:
        groups["country"] = country_boundaries(http.get_json(NATURAL_EARTH_URL), acquired_at, spool.uri)
    boundaries = groups["price_area"] + groups["country"]
    validate_boundary_coverage(boundaries)
    statistics, without_overlay = reservoir_statistics_index(statistics_rows)
    for boundary in boundaries:
        spool.emit(reservoir_area_feature(boundary, statistics, spool.run_id, spool.ingested_at))
    spool.details.update({
        "reconciled": True, "price_areas": list(PRICE_AREA_CODES), "country_components": COUNTRY_COMPONENTS,
        "statistics_without_overlay": without_overlay, "original_statistics_unchanged": True,
        "missing_price_area_statistics": sorted(set(PRICE_AREA_CODES) - statistics.keys()),
        "geometry_sources": [PRICE_AREA_URL, NATURAL_EARTH_URL],
        "terms": [NVE_MAP_TERMS_URL, NATURAL_EARTH_TERMS_URL],
    })


def exact_name(value, owner=False):
    text = unicodedata.normalize("NFKC", source_text(value, "identity name")).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    if owner:
        text = re.sub(r"(?<!\w)a\.\s*s\.(?!\w)", "as", text)
    return text


def asset_identity_aliases(row):
    layer = row["layer_id"]
    if layer not in LINKABLE_ASSET_LAYERS:
        return []
    expected_source = "nve-hydro" if layer == "hydro-plants" else "nve-grid"
    if row["source_id"] != expected_source:
        raise SourceError("asset-index source/layer identity mismatch")
    props = properties_object(row["properties_json"], "asset properties")
    raw = require_object(props.get("source_properties"), "asset source properties")
    owners = []
    if layer == "hydro-plants":
        require_object(raw, "Powerplant identity", ("Navn", "HovedEier", "VannKraftverkID"))
        name_field, owner_field = "Navn", "HovedEier"
        reported_owners = raw.get("Eiere")
        for index, owner in enumerate(require_list([] if reported_owners is None else reported_owners, "Powerplant owners")):
            require_object(owner, "Powerplant owner")
            if owner.get("Navn"):
                owners.append((source_text(owner["Navn"], "owner.Navn", True), f"Eiere[{index}].Navn"))
        identifiers = {"VannKraftverkID": raw.get("VannKraftverkID")}
    else:
        require_object(raw, "transformer identity", ("navn", "eier", "objectid"))
        name_field, owner_field = "navn", "eier"
        identifiers = {name: raw.get(name) for name in ("globalid", "nvenetbasid", "objectid")}
    name = source_text(raw.get(name_field), name_field)
    if raw.get(owner_field):
        owners.append((source_text(raw[owner_field], owner_field, True), owner_field))
    if not name:
        return []
    return [
        {
            "asset_feature_id": row["feature_id"], "asset_layer_id": layer,
            "name_key": exact_name(name), "owner_key": exact_name(owner, owner=True),
            "asset_name": name, "asset_owner": owner, "asset_name_field": name_field,
            "asset_owner_field": owner_path, "asset_source_url": row["source_url"],
            "source_asset_identifiers": identifiers,
        }
        for owner, owner_path in owners if exact_name(owner, owner=True)
    ]


def build_asset_identity_index(rows):
    """Stream only the small hydro/transformer dimension; retain identity evidence, not geometry."""
    index, seen, counts = {}, set(), {layer: 0 for layer in LINKABLE_ASSET_LAYERS}
    unnamed_or_ownerless = 0
    for row in rows:
        if row["layer_id"] not in LINKABLE_ASSET_LAYERS:
            raise SourceError("asset index must be partition-filtered before driver iteration")
        key = (row["feature_id"], row["layer_id"])
        if key in seen:
            raise SourceError("duplicate asset identity in eligible Delta features")
        seen.add(key)
        if len(seen) > MAX_LINKABLE_ASSETS:
            raise SourceError("eligible asset dimension exceeds its bounded size; refusing truncation")
        counts[row["layer_id"]] += 1
        aliases = asset_identity_aliases(row)
        unnamed_or_ownerless += int(not aliases)
        for alias in aliases:
            pair = (alias["name_key"], alias["owner_key"])
            candidates = index.setdefault(pair, {})
            previous = candidates.get(key)
            if previous is None or json_text(alias) < json_text(previous):
                candidates[key] = alias
    if any(count == 0 for count in counts.values()):
        raise SourceError("hydro-plants and transformers must both have a last-good snapshot before linking")
    return {
        key: tuple(candidates[identity] for identity in sorted(candidates))
        for key, candidates in index.items()
    }, {"eligible_assets": counts, "unnamed_or_ownerless_assets": unnamed_or_ownerless,
        "identity_pairs": len(index), "identifier_crosswalk": "none verified; EICs are evidence only"}


def asset_references(message):
    """Only documented asset/unit fields, never area IDs or a transmission border name."""
    references = []

    def add(name, path, identifiers):
        name = source_text(name, path)
        if not name:
            return
        # An area supplied in an asset field is not an asset crosswalk.
        identifiers = {key: source_text(value, key) for key, value in identifiers.items() if value}
        if any(re.fullmatch(r"\d{2}Y[A-Z0-9-]{13}", value.upper()) for value in identifiers.values()):
            return
        if re.fullmatch(r"NO\s*[1-5]", name, re.IGNORECASE):
            return
        references.append({"unit_name": name, "name_key": exact_name(name),
                           "source_field": path, "provider_unit_identifiers": identifiers})

    for field in ("productionUnits", "consumptionUnits", "otherUnits"):
        for index, unit in enumerate(require_list(message.get(field, []), field)):
            require_object(unit, field + " unit")
            add(unit.get("name"), f"{field}[{index}].name", {"eic": unit.get("eic")})
    for index, unit in enumerate(require_list(message.get("generationUnits", []), "generationUnits")):
        require_object(unit, "generation unit")
        add(unit.get("productionUnitName"), f"generationUnits[{index}].productionUnitName", {
            "productionUnitEic": unit.get("productionUnitEic"), "generationUnitEic": unit.get("eic"),
        })
    for index, asset in enumerate(require_list(message.get("assets", []), "UMM assets")):
        require_object(asset, "UMM asset")
        add(asset.get("name"), f"assets[{index}].name", {"asset_code": asset.get("code")})
    if message.get("otherMarketUnits"):
        add(message["otherMarketUnits"], "otherMarketUnits (entire field)", {})
    return references


def message_publishers(message):
    publishers = []
    if message.get("publisherName"):
        publishers.append({"name": source_text(message["publisherName"], "publisherName", True),
                           "source_field": "publisherName"})
    for index, participant in enumerate(require_list(message.get("marketParticipants", []), "marketParticipants")):
        require_object(participant, "market participant")
        if participant.get("name"):
            publishers.append({"name": source_text(participant["name"], "participant.name", True),
                               "source_field": f"marketParticipants[{index}].name"})
    return [{**publisher, "owner_key": exact_name(publisher["name"], owner=True)} for publisher in publishers]


def match_message_assets(message_feature_id, properties_json, asset_index, linked_at, run_id):
    props = properties_object(properties_json, "retained UMM feature")
    message = require_object(props.get("source_message"), "retained source UMM")
    message_id, version = umm_identity(message)
    if message_feature_id != f"nordpool:UMM:{message_id}" or integer(props.get("version"), "retained UMM version", 1) != version:
        raise SourceError("retained UMM feature identity/version disagrees with its source revision")
    result = {"message_version": version, "state": "unsupported", "reference_count": 0,
              "matched_reference_count": 0, "ambiguous_reference_count": 0, "links": []}
    if message["isOutdated"] or props.get("is_outdated"):
        result["state"] = "outdated"
        return result
    references, publishers = asset_references(message), message_publishers(message)
    result["reference_count"] = len(references)
    if not references or not publishers:
        return result
    linked_assets = {}
    for reference in references:
        candidates = {}
        for publisher in publishers:
            for asset in asset_index.get((reference["name_key"], publisher["owner_key"]), ()):
                identity = (asset["asset_feature_id"], asset["asset_layer_id"])
                candidate = candidates.setdefault(identity, {"asset": asset, "matched_pairs": []})
                candidate["matched_pairs"].append({
                    "publisher": publisher, "asset_owner": asset["asset_owner"],
                    "asset_owner_field": asset["asset_owner_field"],
                    "normalized_owner": publisher["owner_key"],
                })
        if len(candidates) > 1:
            result["ambiguous_reference_count"] += 1
        elif len(candidates) == 1:
            identity = next(iter(candidates))
            candidate = candidates[identity]
            linked = linked_assets.setdefault(identity, {"asset": candidate["asset"], "references": []})
            linked["references"].append({
                **reference, "matched_pairs": sorted(candidate["matched_pairs"], key=json_text),
            })
            result["matched_reference_count"] += 1
    if result["ambiguous_reference_count"]:
        result["state"] = "ambiguous"
        return result  # an ambiguous notice is not partially guessed onto another asset
    if not linked_assets:
        result["state"] = "unmatched"
        return result
    cancelled = str(message["eventStatus"]).lower() in ("3", "dismissed") or bool(message.get("cancellationReason"))
    for identity in sorted(linked_assets):
        linked = linked_assets[identity]
        evidence = {
            "policy": "exact-name-owner-v1", "identifier_crosswalk": "none verified",
            "message_id": message_id, "message_version": version,
            "event_status": message["eventStatus"], "is_cancelled": cancelled,
            "does_not_assert_active_outage": True,
            "asset": linked["asset"], "matched_references": sorted(linked["references"], key=json_text),
            "normalization": "Unicode NFKC/casefold/whitespace; corporate A.S. -> AS; no fuzzy/area/proximity match",
        }
        result["links"].append({
            "message_feature_id": message_feature_id, "message_version": version,
            "asset_feature_id": identity[0], "asset_layer_id": identity[1],
            "match_method": "exact_name_owner", "match_evidence_json": json_text(evidence),
            "linked_at": linked_at, "run_id": run_id,
        })
    result["state"] = "linked"
    return result

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Per-layer import orchestration and atomic Delta publication

# CELL ********************

def produce_layer(spool, http, clock):
    config = spool.config
    layer, run_id, ingested_at = config["layer_id"], spool.run_id, spool.ingested_at
    if config["source_id"] == "nve-grid":
        for feature in iter_arcgis(http, config["source_url"], spool.details):
            spool.emit(normalize_grid(feature, config, run_id, ingested_at))
        if spool.row_count != spool.details["initial_count"]:
            raise SourceError("normalized grid count does not reconcile to source inventory")
    elif layer == "hydro-plants":
        records = require_list(http.get_json(HYDRO_URL), "Powerplant registry", True)
        candidates, missing_gis_keys, gis_details = {}, 0, {}
        for feature in iter_arcgis(http, f"{HYDRO_SERVICE}/0", gis_details):
            props = require_object(feature["properties"], "Vannkraft1 properties", ("vannkraftverknr",))
            if props["vannkraftverknr"] is None:
                missing_gis_keys += 1
                continue  # auxiliary GIS row retained in bronze, not a lost registry row
            plant_id = integer(props["vannkraftverknr"], "vannkraftverknr")
            candidates.setdefault(plant_id, []).append(feature)
        ids = set()
        for record in records:
            require_object(record, "Powerplant record", ("VannKraftverkID",))
            plant_id = integer(record["VannKraftverkID"], "VannKraftverkID", 1)
            if plant_id in ids:
                raise SourceError("Powerplant registry contains duplicate VannKraftverkID")
            ids.add(plant_id)
            spool.emit(normalize_hydro(record, candidates.get(plant_id, []), run_id, ingested_at))
        spool.details.update({
            "registry_count": len(records), "gis_inventory": gis_details,
            "missing_registry_gis_matches": len(ids - candidates.keys()),
            "ambiguous_registry_gis_matches": sum(len(candidates.get(key, [])) > 1 for key in ids),
            "gis_rows_without_join_key": missing_gis_keys,
            "gis_ids_not_in_registry": len(candidates.keys() - ids),
            "reconciled": spool.row_count == len(records),
        })
    elif layer == "reservoirs":
        records = require_list(http.get_json(RESERVOIR_URL), "Magasinstatistikk", True)
        for record in records:
            spool.emit(normalize_reservoir(record, run_id, ingested_at))
        spool.details.update({"source_count": len(records), "scope": "all area types, including unmapped VASS"})
    elif layer == "power-balance":
        spool.emit(normalize_balance(http.get_json(BALANCE_URL), run_id, ingested_at))
        spool.details["scope"] = "Norway country aggregate; metric-specific timestamps retained"
    elif layer == "power-flows":
        records = require_list(http.get_json(FLOW_URL), "Statnett flows", True)
        for record in records:
            row = normalize_flow(record, run_id, ingested_at)
            if row is not None:
                spool.emit(row)
        if not spool.row_count:
            raise SourceError("Statnett returned no Norwegian-linked flows")
        spool.details.update({"source_count": len(records), "norwegian_linked_count": spool.row_count})
    elif layer == "grid-frequency":
        end = clock()
        start = end - timedelta(minutes=3)
        body = http.get_json(FREQUENCY_URL, {
            "FromInTicks": int(start.timestamp() * 1000), "ToInTicks": int(end.timestamp() * 1000),
        })
        spool.emit(normalize_frequency(body, start, end, run_id, ingested_at))
        spool.details.update({"window_start": utc_text(start), "window_end": utc_text(end)})
    elif layer == "umm":
        now = clock()
        latest, crosswalk, coverage = fetch_umm_threads(http, now, spool.details)
        for message in latest.values():
            row = normalize_umm(message, crosswalk, now, coverage, run_id, ingested_at)
            if row is not None:
                spool.emit(row)
        spool.details["area_eic_name_pairs_observed"] = len(crosswalk)
    else:
        raise SourceError(f"Unknown layer {layer}")


def schema_matches(actual, expected):
    """Pure contract check: column order may change when Delta moves partition keys."""
    if len(actual) != len(expected) or dict(actual) != dict(expected):
        raise SourceError(f"existing table schema differs from required contract: {actual}")


def spark_schema(fields):
    from pyspark.sql.types import StructType, StructField, StringType, DoubleType, LongType
    types = {"string": StringType, "double": DoubleType, "long": LongType}
    return StructType([StructField(name, types[kind](), True) for name, kind in fields])


def validate_snapshot_metrics(metrics, spool):
    if metrics["rows"] != spool.row_count or metrics["identities"] != spool.row_count:
        raise SourceError("staged feature count/identity uniqueness check failed")
    if metrics["invalid"]:
        raise SourceError("staged snapshot contains invalid source/layer/required fields")
    if metrics["unmapped"] != spool.unmapped_count:
        raise SourceError("staged unmapped count does not reconcile")
    if not spool.row_count and spool.config["layer_id"] != "umm":
        raise SourceError("refusing an empty snapshot for a required nonempty source")
    if not spool.row_count and not spool.details.get("reconciled"):
        raise SourceError("empty UMM snapshot is not backed by a reconciled source window")


def delta_location(table_name, location):
    """Report the actual Spark Delta URI and Lakehouse-relative serving path."""
    location = source_text(location, f"{table_name} location", True)
    parts = unquote(urlsplit(location).path).strip("/").split("/")
    if "Tables" not in parts:
        raise SourceError(f"{table_name}: Delta location is not under a Lakehouse Tables path: {location}")
    relative_path = "/".join(parts[parts.index("Tables"):])
    return {"location": location, "relative_path": relative_path,
            "expected_relative_path": f"Tables/{table_name}"}


def validate_table_locations(locations):
    mismatches = [
        name for name, _, _ in TABLE_CONTRACTS
        if name not in locations or locations[name]["relative_path"] != f"Tables/{name}"
    ]
    if mismatches:
        raise SourceError(
            "GeoContext requires a schema-disabled Lakehouse and flat Tables/<table> paths for "
            "the source and auxiliary serving tables. Refusing source imports into an incompatible layout. "
            "Actual Delta locations: " + json_text(locations)
        )


class SparkStore:
    def __init__(self, spark):
        from delta.tables import DeltaTable
        self.spark, self.delta = spark, DeltaTable
        self.feature_schema, self.status_schema = spark_schema(FEATURE_FIELDS), spark_schema(STATUS_FIELDS)
        self.link_schema = spark_schema(ASSET_LINK_FIELDS)
        self.table_locations = {}
        for name, fields, partitions in TABLE_CONTRACTS:
            schema = spark_schema(fields)
            if not spark.catalog.tableExists(name):
                writer = spark.createDataFrame([], schema).write.format("delta").mode("errorifexists")
                if partitions:
                    writer = writer.partitionBy(*partitions)
                writer.saveAsTable(name)
            schema_matches(
                [(field.name, field.dataType.simpleString().replace("bigint", "long")) for field in spark.table(name).schema],
                fields,
            )
            info = spark.sql(f"DESCRIBE DETAIL `{name}`").select("format", "partitionColumns", "location").first()
            if info["format"] != "delta" or info["partitionColumns"] != partitions:
                raise SourceError(f"{name}: incompatible format or partitioning")
            self.table_locations[name] = delta_location(name, info["location"])
        print(json_text({"geo_context_table_locations": self.table_locations}))
        validate_table_locations(self.table_locations)
        rows = spark.table(STATUS_TABLE).limit(len(LAYER_CONFIGS) + 1).collect()
        if len(rows) > len(LAYER_CONFIGS):
            raise SourceError("source status table is not the expected small per-layer table")
        self.statuses = {}
        allowed = {config["layer_id"]: config["source_id"] for config in LAYER_CONFIGS}
        for row in rows:
            value = row.asDict()
            layer = value["layer_id"]
            if layer in self.statuses or allowed.get(layer) != value["source_id"] or value["state"] not in ("ready", "error"):
                raise SourceError("source status table contains duplicate/invalid layer identities or states")
            self.statuses[layer] = value
        # A reset/deleted partition must not be hidden by a still-recent ready status.
        # This is one distributed count, not a driver collection of map features.
        partition_counts = {
            row["layer_id"]: row["count"]
            for row in spark.table(FEATURE_TABLE).groupBy("layer_id").count().collect()
        }
        for config in LAYER_CONFIGS:
            prior = self.statuses.get(config["layer_id"])
            actual = partition_counts.get(config["layer_id"], 0)
            if prior and prior["state"] == "ready" and actual != prior["row_count"]:
                self.set_status({
                    **prior, "state": "error",
                    "message": f"Stored partition count {actual} differs from last-good count {prior['row_count']}; refresh required",
                })

    def get_status(self, layer):
        return self.statuses.get(layer)

    def set_status(self, value):
        frame = self.spark.createDataFrame([value], self.status_schema)
        self.delta.forName(self.spark, STATUS_TABLE).alias("target").merge(
            frame.alias("incoming"), "target.layer_id = incoming.layer_id",
        ).whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
        self.statuses[value["layer_id"]] = value

    def publish(self, spool, table_name=FEATURE_TABLE):
        from pyspark import StorageLevel
        from pyspark.sql import functions as F
        if table_name not in (FEATURE_TABLE, RESERVOIR_AREA_TABLE):
            raise SourceError("unexpected feature publication target")
        if table_name == RESERVOIR_AREA_TABLE and (
            spool.row_count != 9 or spool.unmapped_count or spool.rejected_count or not spool.details.get("reconciled")
        ):
            raise SourceError("refusing incomplete reservoir-area coverage")
        frame = (
            self.spark.read.schema(self.feature_schema).option("mode", "FAILFAST")
            .json(f"{spool.uri}/normalized/*.jsonl.gz")
            if spool.row_count else self.spark.createDataFrame([], self.feature_schema)
        )
        frame = frame.persist(StorageLevel.DISK_ONLY)
        try:
            invalid = (
                F.col("layer_id").isNull() | (F.col("layer_id") != spool.config["layer_id"])
                | F.col("source_id").isNull() | (F.col("source_id") != spool.config["source_id"])
                | F.col("feature_id").isNull() | (F.col("feature_id") == "")
                | F.col("run_id").isNull() | (F.col("run_id") != spool.run_id)
                | F.col("geometry_json").isNull() | F.col("properties_json").isNull()
                | F.col("ingested_at").isNull() | F.col("source_url").isNull()
                | F.col("label").isNull()
            )
            metrics = frame.agg(
                F.count("*").alias("rows"), F.countDistinct("feature_id").alias("identities"),
                F.coalesce(F.sum(F.when(invalid, 1).otherwise(0)), F.lit(0)).alias("invalid"),
                F.coalesce(F.sum(F.when(F.col("geometry_json") == "", 1).otherwise(0)), F.lit(0)).alias("unmapped"),
            ).first().asDict()
            validate_snapshot_metrics(metrics, spool)
            # No MERGE/delete per page and no whole-table overwrite. Delta commits
            # the replacement only after every upstream/normalization gate passed.
            if table_name == RESERVOIR_AREA_TABLE:
                frame.coalesce(1).write.format("delta").mode("overwrite").option(
                    "mergeSchema", "false",
                ).partitionBy("layer_id").saveAsTable(RESERVOIR_AREA_TABLE)
            else:
                frame.repartition(max(1, min(32, math.ceil(spool.row_count / 50000)))).write.format("delta").mode(
                    "overwrite",
                ).option("replaceWhere", f"layer_id = '{spool.config['layer_id']}'").option(
                    "mergeSchema", "false",
                ).partitionBy("layer_id").saveAsTable(FEATURE_TABLE)
        finally:
            frame.unpersist()

    def require_source_snapshot(self, layer):
        status = self.get_status(layer)
        if not status or status["state"] != "ready" or not status["last_success_at"]:
            raise SourceError(f"required last-good {layer} source snapshot is unavailable")

    def reservoir_inputs(self):
        from pyspark.sql import functions as F
        self.require_source_snapshot("reservoirs")
        stats = self.spark.table(FEATURE_TABLE).where(F.col("layer_id") == "reservoirs").limit(65).collect()
        if len(stats) > 64:
            raise SourceError("reservoir statistics exceed their bounded area-record budget")
        cache = self.spark.table(RESERVOIR_AREA_TABLE).limit(10).collect()
        return [row.asDict() for row in cache], [row.asDict() for row in stats]

    def table_version(self, table_name):
        return int(self.delta.forName(self.spark, table_name).history(1).select("version").first()["version"])

    def snapshot_at(self, table_name, version):
        return self.spark.read.format("delta").option("versionAsOf", version).load(self.table_locations[table_name]["location"])

    def prepare_asset_links(self, spool):
        from pyspark import StorageLevel
        from pyspark.sql import functions as F
        from pyspark.sql.types import ArrayType, StructField
        for layer in (*LINKABLE_ASSET_LAYERS, "umm"):
            self.require_source_snapshot(layer)
        source_version = self.table_version(FEATURE_TABLE)
        link_version = self.table_version(ASSET_LINK_TABLE)
        source = self.snapshot_at(FEATURE_TABLE, source_version)
        # Partition pruning happens before streaming this small dimension. Only
        # identity evidence is retained, never masts or full asset geometries.
        assets = source.where(F.col("layer_id").isin(*LINKABLE_ASSET_LAYERS)).select(
            "feature_id", "source_id", "layer_id", "source_url", "properties_json",
        )
        index, asset_counts = build_asset_identity_index(row.asDict() for row in assets.toLocalIterator())
        index_broadcast = self.spark.sparkContext.broadcast(index)
        linked_at, link_run_id = spool.ingested_at, spool.run_id
        match_schema = spark_schema((
            ("message_version", "long"), ("state", "string"), ("reference_count", "long"),
            ("matched_reference_count", "long"), ("ambiguous_reference_count", "long"),
        )).add(StructField("links", ArrayType(self.link_schema), True))
        matcher = F.udf(
            lambda feature_id, props: match_message_assets(
                feature_id, props, index_broadcast.value, linked_at, link_run_id,
            ), match_schema,
        )
        processed = source.where(F.col("layer_id") == "umm").select(
            "feature_id", matcher("feature_id", "properties_json").alias("match"),
        ).persist(StorageLevel.DISK_ONLY)
        try:
            state_rows = processed.groupBy("match.state").agg(
                F.count("*").alias("messages"), F.sum("match.reference_count").alias("references"),
                F.sum("match.ambiguous_reference_count").alias("ambiguous_references"),
            ).collect()
            candidate_states = {row["state"]: row["messages"] for row in state_rows}
            total_messages = sum(candidate_states.values())
            if total_messages != self.get_status("umm")["row_count"]:
                raise SourceError("retained UMM count differs from its last-good source status")
            current_versions = processed.where(F.col("match.state") != "outdated").select(
                F.col("feature_id").alias("message_feature_id"), F.col("match.message_version").alias("message_version"),
            )
            manual = self.snapshot_at(ASSET_LINK_TABLE, link_version).where(F.col("match_method") == "manual")
            manual_count = validate_link_frame(manual, manual=True)
            automatic = processed.select(F.explode("match.links").alias("link")).select("link.*")
            automatic = exclude_manually_curated_messages(automatic, manual).select(*[name for name, _ in ASSET_LINK_FIELDS])
            automatic_count = validate_link_frame(automatic)
            candidate_uri = f"{spool.uri}/automatic-links.delta"
            automatic.coalesce(1).write.format("delta").mode("errorifexists").save(candidate_uri)
            staged = self.spark.read.format("delta").load(candidate_uri)
            automatic_messages = staged.select(*LINK_MESSAGE_KEY).distinct()
            manual_for_current_messages = manual.join(
                current_versions, list(LINK_MESSAGE_KEY), "inner",
            )
            real_assets = source.where(F.col("layer_id").isin(
                *LINKABLE_ASSET_LAYERS, *[layer for _, layer in GRID_LAYERS],
            )).select(F.col("feature_id").alias("asset_feature_id"), F.col("layer_id").alias("asset_layer_id"))
            # Validate curated references with a distributed join, not a driver
            # collection of national network features. Preserve orphaned history.
            current_manual_rows = real_assets.join(
                F.broadcast(manual_for_current_messages), ["asset_feature_id", "asset_layer_id"], "inner",
            ) if manual_count else manual_for_current_messages
            current_manual = current_manual_rows.select(*LINK_MESSAGE_KEY).distinct()
            linked_messages = automatic_messages.unionByName(current_manual).distinct().count()
            current_manual_count = current_manual.count()
            orphaned_manual_current = manual_for_current_messages.count() - current_manual_rows.count() if manual_count else 0
            spool.row_count = automatic_count + manual_count
            spool.details.update({
                **asset_counts, "source_feature_table_version": source_version,
                "link_table_version_before_publication": link_version,
                "retained_messages": total_messages, "automatic_candidate_states": candidate_states,
                "linked_current_messages": linked_messages,
                "unlinked_current_messages": total_messages - linked_messages,
                "current_manual_messages": current_manual_count,
                "manual_current_links_without_asset": orphaned_manual_current,
                "automatic_links": automatic_count, "preserved_manual_links": manual_count,
                "manual_history_is_not_promoted_to_new_versions": True,
                "candidate_delta_uri": candidate_uri,
                "serving_join": "message_feature_id AND message_version; a link does not assert active status",
                "reconciled": True,
            })
            spool.record({"input_lineage": spool.details})
            return staged, link_version
        finally:
            processed.unpersist()
            index_broadcast.destroy()

    def publish_asset_links(self, staged, expected_version):
        if self.table_version(ASSET_LINK_TABLE) != expected_version:
            raise SourceError("asset-link table changed while preparing the snapshot; rerun to preserve concurrent manual corrections")
        merge_automatic_links(self.delta.forName(self.spark, ASSET_LINK_TABLE), staged)


def exclude_manually_curated_messages(automatic, manual):
    return automatic.join(manual.select(*LINK_MESSAGE_KEY).distinct(), list(LINK_MESSAGE_KEY), "left_anti")


def validate_link_frame(frame, manual=False):
    from pyspark.sql import functions as F
    invalid = F.col("message_version").isNull() | (F.col("message_version") < 1)
    required = ("message_feature_id", "asset_feature_id", "asset_layer_id", "match_method")
    if not manual:
        required += ("match_evidence_json", "linked_at", "run_id")
        invalid = invalid | (F.col("match_method") != "exact_name_owner") | F.get_json_object("match_evidence_json", "$").isNull()
    for name in required:
        invalid = invalid | F.col(name).isNull() | (F.trim(F.col(name)) == "")
    metrics = frame.agg(
        F.count("*").alias("rows"), F.countDistinct(*LINK_ROW_KEY).alias("identities"),
        F.coalesce(F.sum(F.when(invalid, 1).otherwise(0)), F.lit(0)).alias("invalid"),
    ).first().asDict()
    if metrics["invalid"] or metrics["rows"] != metrics["identities"]:
        raise SourceError("manual/automatic asset links have invalid or duplicate revision-qualified identities")
    return metrics["rows"]


def merge_automatic_links(target, staged):
    condition = " AND ".join(f"target.{name} = incoming.{name}" for name in LINK_ROW_KEY)
    target.alias("target").merge(staged.alias("incoming"), condition).whenMatchedUpdateAll(
        condition="target.match_method <> 'manual'",
    ).whenNotMatchedInsertAll().whenNotMatchedBySourceDelete(
        condition="target.match_method IS NULL OR target.match_method <> 'manual'",
    ).execute()


def source_status(config, previous, attempted_at, state, message, finished_at=None, counts=None):
    previous = previous or {}
    values = counts if counts is not None else previous
    return {
        "layer_id": config["layer_id"], "source_id": config["source_id"], "state": state,
        "last_attempt_at": attempted_at, "last_success_at": finished_at if state == "ready" else previous.get("last_success_at"),
        "row_count": int(values.get("row_count") or 0),
        "unmapped_count": int(values.get("unmapped_count") or 0),
        "rejected_count": int(values.get("rejected_count") or 0),
        "message": message, "source_url": config["source_url"],
    }


def refresh_decision(config, previous, mode, force, now):
    if config["static"] and mode == "operational":
        return False, "operational mode skips network/plant snapshots"
    if config["static"] and not force and previous and previous["state"] == "ready" and previous["row_count"] > 0:
        last = parse_time(previous.get("last_success_at"), "last_success_at")
        if last is not None and timedelta(0) <= now - last < timedelta(hours=24):
            return False, "healthy static snapshot is less than 24 hours old"
    return True, "refresh requested"


def run_one_layer(config, store, fs, root, run_id, session, clock,
                  producer=produce_layer, failure_types=(SourceError, OSError)):
    previous = store.get_status(config["layer_id"])
    attempted = utc_text(clock())
    store.set_status(source_status(
        config, previous, attempted, "error",
        "Import in progress; previous snapshot/counts retained until complete validation and atomic publication.",
    ))
    spool, archived = None, False
    try:
        spool = LayerSpool(root, config, run_id, attempted)
        producer(spool, PublicHTTP(session, spool), clock)
        spool.archive(fs, "prepared", "Source acquisition complete; not yet committed to Delta")
        archived = True
        store.publish(spool)
    except failure_types as exc:
        message = f"{type(exc).__name__}: {exc}; last-good counts/success preserved"
        if spool is not None:
            try:
                if not archived:
                    spool.archive(fs, "error", message)
                else:
                    if fs.put(f"{spool.uri}/attempt.json", json_text(spool.manifest("error", message)), True) is False:
                        raise SourceError("failed to persist source-error run log")
            except failure_types as archive_error:
                message += f"; bronze archive error: {type(archive_error).__name__}: {archive_error}"
            finally:
                spool.close()
            message += f"; attempted_rows={spool.row_count}, rejected_geometries={spool.rejected_count}; bronze={spool.uri}"
        store.set_status(source_status(config, previous, attempted, "error", message))
        return {"source_id": config["source_id"], "layer_id": config["layer_id"], "state": "error", "message": message}
    finally:
        if spool is not None:
            spool.close()
    # Outside the source-error handler: publication already committed. A status/log
    # storage failure here is fatal, not a false claim that the old partition survived.
    message = (
        f"Complete snapshot: rows={spool.row_count}, unmapped={spool.unmapped_count}, "
        f"rejected_geometries={spool.rejected_count}; bronze={spool.uri}; {json_text(spool.details)}"
    )
    store.set_status(source_status(
        config, previous, attempted, "ready", message, utc_text(clock()),
        {"row_count": spool.row_count, "unmapped_count": spool.unmapped_count, "rejected_count": spool.rejected_count},
    ))
    if fs.put(f"{spool.uri}/attempt.json", json_text(spool.manifest("ready", message)), True) is False:
        raise SourceError(f"{config['layer_id']}: snapshot committed but final run-log persistence failed")
    return {"source_id": config["source_id"], "layer_id": config["layer_id"], "state": "ready",
            "row_count": spool.row_count, "unmapped_count": spool.unmapped_count, "rejected_count": spool.rejected_count}


def run_layers(configs, store, fs, root, run_id, session, mode, force, clock,
               producer=produce_layer, failure_types=(SourceError, OSError)):
    results = []
    # All twelve layer statuses are visible even while the first large import runs.
    for config in configs:
        if store.get_status(config["layer_id"]) is None:
            store.set_status(source_status(config, None, None, "error", "No successful snapshot yet; not imported"))
    for config in configs:
        previous = store.get_status(config["layer_id"])
        refresh, reason = refresh_decision(config, previous, mode, force, clock())
        if not refresh:
            path = f"Files/bronze/geo_context/{config['source_id']}/{run_id}/{config['layer_id']}"
            fs.mkdirs(path)
            if fs.put(f"{path}/attempt.json", json_text({
                "run_id": run_id, "layer_id": config["layer_id"], "outcome": "skipped",
                "reason": reason, "last_success_at": previous.get("last_success_at"),
            }), True) is False:
                raise SourceError("failed to persist skipped-layer run log")
            results.append({"layer_id": config["layer_id"], "outcome": "skipped", "reason": reason})
            continue
        result = run_one_layer(config, store, fs, root, run_id, session, clock, producer, failure_types)
        results.append(result)
        print(json_text(result))
    failures = [f"{result['source_id']}/{result['layer_id']}" for result in results if result.get("state") == "error"]
    if failures:
        raise SourceError("Energy ingestion failed for: " + ", ".join(failures) + ". All other selected layers were attempted; see geo_source_status and bronze logs.")
    return results


def run_auxiliary_output(config, store, fs, root, run_id, session, mode, force, clock,
                         failure_types=(SourceError, OSError)):
    spool = LayerSpool(root, config, run_id, utc_text(clock()))
    archived = False
    publication_attempted = False
    table_name = config["artifact_id"]
    fs.mkdirs(spool.uri)
    if fs.put(f"{spool.uri}/attempt.json", json_text(spool.manifest("started", "Auxiliary import in progress")), True) is False:
        spool.close()
        raise SourceError(f"{table_name}: failed to persist initial auxiliary attempt")
    try:
        if table_name == RESERVOIR_AREA_TABLE:
            cached, statistics = store.reservoir_inputs()
            produce_reservoir_areas(
                spool, PublicHTTP(session, spool), cached, statistics, clock(),
                force and mode == "all", fs,
            )
            spool.archive(fs, "prepared", "All nine real boundaries and source provenance validated")
            archived = True
            publication_attempted = True
            store.publish(spool, table_name=RESERVOIR_AREA_TABLE)
        elif table_name == ASSET_LINK_TABLE:
            staged, previous_version = store.prepare_asset_links(spool)
            spool.archive(fs, "prepared", "Revision-qualified automatic links staged; manual corrections preserved")
            archived = True
            publication_attempted = True
            store.publish_asset_links(staged, previous_version)
        else:
            raise SourceError(f"unsupported auxiliary table: {table_name}")
    except failure_types as exc:
        publication_note = (
            "publication failed/unconfirmed; inspect Delta history before assuming a new snapshot is ready"
            if publication_attempted else "previous published auxiliary snapshot retained"
        )
        message = f"{table_name}: {type(exc).__name__}: {exc}; {publication_note}"
        try:
            if not archived:
                spool.archive(fs, "error", message)
            elif fs.put(f"{spool.uri}/attempt.json", json_text(spool.manifest("error", message)), True) is False:
                raise SourceError("failed to persist auxiliary error log")
        except failure_types as archive_error:
            message += f"; bronze archive failure: {archive_error}"
        return {"table": table_name, "state": "error", "message": message, "bronze_uri": spool.uri}
    finally:
        spool.close()
    message = f"Published {table_name}; rows={spool.row_count}; coverage={json_text(spool.details)}"
    if fs.put(f"{spool.uri}/attempt.json", json_text(spool.manifest("ready", message)), True) is False:
        raise SourceError(f"{table_name}: committed but final auxiliary log persistence failed")
    return {"table": table_name, "state": "ready", "row_count": spool.row_count,
            "coverage": spool.details, "bronze_uri": spool.uri}


def run_auxiliary_outputs(store, fs, root, run_id, session, mode, force, clock,
                          failure_types=(SourceError, OSError)):
    results = []
    for config in AUXILIARY_CONFIGS:
        result = run_auxiliary_output(config, store, fs, root, run_id, session, mode, force, clock, failure_types)
        results.append(result)
        print(json_text(result))
    failures = [result["table"] for result in results if result["state"] == "error"]
    if failures:
        raise SourceError("Required auxiliary publication failed: " + ", ".join(failures) + "; see archived attempt logs")
    return results


def validate_parameters(workspace, suffix, mode, force, context):
    if mode not in ("all", "operational"):
        raise SourceError("refresh_mode must be 'all' or 'operational'")
    if isinstance(force, str) and force.lower() in ("true", "false"):
        force = force.lower() == "true"
    if not isinstance(force, bool):
        raise SourceError("force_refresh must be boolean (or true/false pipeline text)")
    if not isinstance(suffix, str) or not re.fullmatch(r"[A-Za-z0-9_]+", suffix):
        raise SourceError("env_suffix must be an explicit safe environment suffix")
    expected = f"Hydro_GeoContext_{suffix}"
    if context.get("defaultLakehouseName") != expected or not context.get("defaultLakehouseId"):
        raise SourceError(f"Bind the notebook to default lakehouse {expected}; refusing writes to another lakehouse")
    current = context.get("currentWorkspaceId")
    workspace = workspace or current
    try:
        if str(UUID(workspace)) != str(UUID(current)):
            raise SourceError("workspace_id does not match the runtime workspace")
        lakehouse_workspace = context.get("defaultLakehouseWorkspaceId")
        if not lakehouse_workspace or str(UUID(lakehouse_workspace)) != str(UUID(workspace)):
            raise SourceError("default lakehouse is not in the requested runtime workspace")
    except (ValueError, TypeError, AttributeError) as exc:
        raise SourceError("runtime/provided workspace ID is missing or invalid") from exc
    return workspace, force

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Execute once in the bound Fabric Spark session
# This cell deliberately uses notebook parameters directly. There is no standalone
# entry point, SparkSession builder, deployment API, credentials lookup, or schedule.

# CELL ********************

import notebookutils
from py4j.protocol import Py4JJavaError
from pyspark.errors import PySparkException

context = notebookutils.runtime.context
workspace_id, force_refresh = validate_parameters(
    workspace_id, env_suffix, refresh_mode, force_refresh, context,
)
is_for_pipeline = context["isForPipeline"]
run_id = str(uuid4())
store = SparkStore(spark)
with requests.Session() as public_session:
    # Do not inherit netrc credentials for these explicitly public source requests.
    public_session.trust_env = False
    public_session.headers.update({"Accept": "application/json", "User-Agent": "HydroOperations-GeoContext/1.0"})
    with tempfile.TemporaryDirectory(prefix="hydro_geo_context_") as owned_temp:
        results = run_layers(
            LAYER_CONFIGS, store, notebookutils.fs, owned_temp, run_id, public_session,
            refresh_mode, force_refresh, lambda: datetime.now(timezone.utc),
            failure_types=(SourceError, requests.RequestException, OSError, PySparkException, Py4JJavaError),
        )
        auxiliary_results = run_auxiliary_outputs(
            store, notebookutils.fs, owned_temp, run_id, public_session,
            refresh_mode, force_refresh, lambda: datetime.now(timezone.utc),
            failure_types=(SourceError, requests.RequestException, OSError, PySparkException, Py4JJavaError),
        )
print(json_text({
    "run_id": run_id, "refresh_mode": refresh_mode, "is_for_pipeline": is_for_pipeline,
    "table_locations": store.table_locations, "layers": results, "auxiliary_outputs": auxiliary_results,
}))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
