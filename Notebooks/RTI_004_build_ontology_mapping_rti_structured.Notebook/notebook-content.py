# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "789fb22a-cc44-4776-9e02-5344aaa89724",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI_V3",
# META       "default_lakehouse_workspace_id": "19f3d588-1585-4f3b-bb59-5abaf90c193a",
# META       "known_lakehouses": [
# META         {
# META           "id": "789fb22a-cc44-4776-9e02-5344aaa89724"
# META         }
# META       ]
# META     },
# META     "environment": {}
# META   }
# META }

# MARKDOWN ********************

# # 04 – Build Ontology for Structured Data + Direct Eventhouse RTI Binding
# 
# This notebook builds `OntologyTestRTI` for the clean Fabric ontology pattern:
# 
# - static/structured entities are sourced from Lakehouse tables
# - RTI telemetry stays in Eventhouse
# - `signal_master` is the semantic bridge between structured metadata and RTI
# - `event_time`, `value`, and `quality` are defined as time-series properties on `signal_master`
# 
# This notebook does not create a Lakehouse copy of RTI telemetry.


# CELL ********************

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG – Ontology for structured data + direct Eventhouse RTI binding
# Reads shared settings from rti_demo_settings
# ══════════════════════════════════════════════════════════════════════════════

from pyspark.sql import functions as F

USE_MANUAL_TABLE_LIST = True

# --------------------------------------------
# LOAD SHARED RTI DEMO SETTINGS
# --------------------------------------------

settings_table_name = "rti_demo_settings"

spark.catalog.clearCache()
spark.sql(f"REFRESH TABLE {settings_table_name}")

settings_df = spark.read.table(settings_table_name)

settings = {
    row["setting_name"]: row["setting_value"]
    for row in settings_df.collect()
}

required_settings = [
    "workspace_id",
    "workspace_folder_path",
    "target_folder_id",
    "lakehouse_id",
    "lakehouse_name",
    "ontology_name",
    "eventhouse_name",
    "kql_database_name",
    "eventhouse_table_name",
    "silver_facilities_table",
    "silver_systems_table",
    "silver_equipment_table",
    "silver_instruments_table",
    "silver_signal_master_table",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
]

missing_settings = [name for name in required_settings if name not in settings]
if missing_settings:
    raise RuntimeError(
        f"Missing required settings in '{settings_table_name}': {missing_settings}"
    )

# --------------------------------------------
# CORE WORKSPACE / FOLDER / LAKEHOUSE SETTINGS
# --------------------------------------------

workspace_id = settings["workspace_id"]
workspace_folder_path = settings["workspace_folder_path"]
target_folder_id = settings["target_folder_id"]

lakehouse_name = settings["lakehouse_name"]
lakehouse_id = settings["lakehouse_id"]

# --------------------------------------------
# AUTHENTICATION SETTINGS
# --------------------------------------------

key_vault_uri = settings["key_vault_uri"]
key_vault_tenant_id_secret = settings["key_vault_tenant_id_secret"]
key_vault_client_id_secret = settings["key_vault_client_id_secret"]
key_vault_client_secret_secret = settings["key_vault_client_secret_secret"]

# --------------------------------------------
# EVENTHOUSE / RTI SOURCE SETTINGS
# Used for direct Eventhouse RTI time-series binding
# --------------------------------------------

fabric_eventhouse_name = settings["eventhouse_name"]
fabric_kql_db_name = settings["kql_database_name"]
fabric_eventhouse_table = settings["eventhouse_table_name"]

# --------------------------------------------
# STRUCTURED SOURCE TABLES
# Produced by setup / medallion notebooks
# --------------------------------------------

SILVER_FACILITIES_TABLE = settings["silver_facilities_table"]
SILVER_SYSTEMS_TABLE = settings["silver_systems_table"]
SILVER_EQUIPMENT_TABLE = settings["silver_equipment_table"]
SILVER_INSTRUMENTS_TABLE = settings["silver_instruments_table"]
SILVER_SIGNAL_MASTER_TABLE = settings["silver_signal_master_table"]

# --------------------------------------------
# ONTOLOGY DEPLOYMENT NAME
# --------------------------------------------

ONTOLOGY_NAME = settings["ontology_name"]

# --------------------------------------------
# STATIC ENTITY TABLES FOR ONTOLOGY
# Keep RTI stream rows out of Lakehouse ontology entities.
# Eventhouse binding happens directly through signal_master/opcua_node_id.
# --------------------------------------------

MANUAL_TABLE_LIST = [
    SILVER_FACILITIES_TABLE,
    SILVER_SYSTEMS_TABLE,
    SILVER_EQUIPMENT_TABLE,
    SILVER_INSTRUMENTS_TABLE,
    SILVER_SIGNAL_MASTER_TABLE,
]

print("✅ Loaded 004 configuration from shared settings.")
print("✅ Workspace ID:", workspace_id)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Lakehouse:", lakehouse_name)
print("✅ Lakehouse ID:", lakehouse_id)
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Eventhouse:", fabric_eventhouse_name)
print("✅ KQL database:", fabric_kql_db_name)
print("✅ Eventhouse table:", fabric_eventhouse_table)
print("✅ Manual ontology source tables:", MANUAL_TABLE_LIST)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ══════════════════════════════════════════════════════════════════════════════
# PREP – Validate static ontology source tables from structured metadata
# ══════════════════════════════════════════════════════════════════════════════

from pyspark.sql import functions as F

def table_exists(table_name: str) -> bool:
    try:
        return spark.catalog.tableExists(table_name)
    except Exception:
        return False

required_tables = [
    SILVER_FACILITIES_TABLE,
    SILVER_SYSTEMS_TABLE,
    SILVER_EQUIPMENT_TABLE,
    SILVER_INSTRUMENTS_TABLE,
    SILVER_SIGNAL_MASTER_TABLE,
]

missing_tables = [
    table_name
    for table_name in required_tables
    if not table_exists(table_name)
]

if missing_tables:
    raise RuntimeError(
        "Required ontology source tables are missing. "
        f"Run Notebook 003 first. Missing tables: {missing_tables}"
    )

required_columns_by_table = {
    SILVER_FACILITIES_TABLE: {
        "facility_id",
    },
    SILVER_SYSTEMS_TABLE: {
        "system_id",
        "facility_id",
    },
    SILVER_EQUIPMENT_TABLE: {
        "equipment_id",
        "system_id",
        "facility_id",
    },
    SILVER_INSTRUMENTS_TABLE: {
        "opcua_node_id",
        "tag",
        "instrument_id",
        "equipment_id",
        "system_id",
        "facility_id",
        "unit",
        "is_active",
    },
    SILVER_SIGNAL_MASTER_TABLE: {
        "opcua_node_id",
        "tag",
        "instrument_id",
        "equipment_id",
        "system_id",
        "facility_id",
        "unit",
        "is_active",
        "signal_type",
    },
}

validation_rows = []

for table_name in required_tables:
    df = spark.read.table(table_name)
    actual_columns = set(df.columns)
    missing_columns = sorted(required_columns_by_table[table_name] - actual_columns)

    validation_rows.append({
        "table_name": table_name,
        "row_count": df.count(),
        "missing_columns": ", ".join(missing_columns),
        "status": "OK" if not missing_columns else "MISSING_COLUMNS",
    })

validation_df = spark.createDataFrame(validation_rows)

display(validation_df.orderBy("table_name"))

bad_rows = validation_df.where(F.col("status") != "OK").count()
if bad_rows > 0:
    raise RuntimeError(
        "One or more ontology source tables are missing required columns. "
        "See validation output above."
    )

print("✅ Ontology source tables validated.")
print("✅ No static ontology source tables were overwritten.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""
ontology_generate_all_parts.py – structured ontology + Eventhouse RTI edition
──────────────────────────────────────────────────────────────
Generates ontology definition parts for the structured entities and direct Eventhouse RTI binding model.

This cell expects the config cell to define:
- USE_MANUAL_TABLE_LIST
- MANUAL_TABLE_LIST
- ONTOLOGY_NAME
"""

import re
import json
import base64
import hashlib
from collections import defaultdict

import pandas as pd
from IPython.display import display, Markdown


def md(text):
    display(Markdown(text))


OWN_PK_OVERRIDES = {
    "facilities": "facility_id",
    "systems": "system_id",
    "equipment": "equipment_id",
    "instruments": "instrument_id",
    "signal_master": "opcua_node_id",
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def encode_payload(data):
    return base64.b64encode(
        json.dumps(data, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8")


def clean_entity_name(table_name):
    base = re.sub(r"^silver_", "", table_name)
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)

    if not re.match(r"^[A-Za-z]", base):
        base = "E_" + base

    return base[:26]


def make_relationship_name(src_entity: str, fk_col: str, tgt_entity: str) -> str:
    def _sanitize(s):
        s = re.sub(r"[^A-Za-z0-9_]", "_", s)
        s = re.sub(r"[^A-Za-z0-9]+$", "", s)
        s = re.sub(r"^[^A-Za-z0-9]+", "", s)
        return s

    def _fits(s):
        return 1 <= len(s) <= 26

    def _abbrev(name: str) -> str:
        if len(name) <= 10:
            return name

        parts = name.split("_")
        if len(parts) >= 2:
            return parts[0] + "_" + parts[-1][:4]

        return name[:6]

    def _singular(name: str) -> str:
        if name.endswith("ies"):
            return name[:-3] + "y"
        if name.endswith("ses") or name.endswith("xes"):
            return name[:-2]
        if name.endswith("s") and not name.endswith("ss"):
            return name[:-1]
        return name

    def _truncate(s):
        s = s[:26]
        return re.sub(r"[^A-Za-z0-9]+$", "", s) or "R"

    fk_stem = re.sub(r"_id$", "", fk_col)
    tgt_lower = tgt_entity.lower()
    tgt_single = _singular(tgt_lower)

    is_plain = (
        fk_stem == tgt_lower
        or fk_stem == tgt_single
        or fk_stem.endswith(f"_{tgt_lower}")
        or fk_stem.endswith(f"_{tgt_single}")
    )

    qualifier = None

    if not is_plain:
        if fk_stem.endswith(f"_{tgt_lower}"):
            qualifier = fk_stem[:-(len(tgt_lower) + 1)]
        elif fk_stem.endswith(f"_{tgt_single}"):
            qualifier = fk_stem[:-(len(tgt_single) + 1)]
        elif fk_stem.startswith(f"{tgt_lower}_"):
            qualifier = fk_stem[len(tgt_lower) + 1:]
        elif fk_stem.startswith(f"{tgt_single}_"):
            qualifier = fk_stem[len(tgt_single) + 1:]

    for src_form in [src_entity, _abbrev(src_entity)]:
        for tgt_form in [tgt_entity, _abbrev(tgt_entity)]:
            if not qualifier:
                candidate = _sanitize(f"{src_form}_has_{tgt_form}")
                if _fits(candidate):
                    return candidate
            else:
                candidate = _sanitize(f"{src_form}_{qualifier}_{tgt_form}")
                if _fits(candidate):
                    return candidate

                candidate = _sanitize(f"{src_form}_{qualifier}")
                if _fits(candidate):
                    return candidate

    return _truncate(_sanitize(f"{src_entity}_to_{tgt_entity}"))


def generate_id(name, offset=0):
    h = hashlib.sha256((name + str(offset)).encode("utf-8")).digest()
    id64 = int.from_bytes(h[:8], "big", signed=False)

    if id64 > 2**63 - 1:
        id64 = id64 % (2**63)

    return id64


sparktype_to_api = {
    "StringType()": "String",
    "IntegerType()": "BigInt",
    "LongType()": "BigInt",
    "DoubleType()": "Double",
    "FloatType()": "Double",
    "BooleanType()": "Boolean",
    "TimestampType()": "DateTime",
    "DateType()": "DateTime",
}


def spark_to_api_type(dt):
    return sparktype_to_api.get(str(dt), "String")


VALID_VALUE_TYPES = {
    "String",
    "BigInt",
    "Boolean",
    "DateTime",
    "Object",
    "Double",
}


def discover_silver_tables():
    all_tbls = notebookutils.lakehouse.listTables()
    return [t for t in all_tbls if t["name"].startswith("silver_")]


def validate_manual_table_list(tables, manual_list):
    names_set = set(t["name"] for t in tables)
    valid_list = [tbl for tbl in manual_list if tbl in names_set]
    invalid_set = set(manual_list) - names_set
    return valid_list, sorted(list(invalid_set))


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — DISCOVER / SELECT SOURCE TABLES
# ══════════════════════════════════════════════════════════════════════════════

md("## 📚 Step 1 — Discovering Source Tables")

all_silver_tables = discover_silver_tables()

if USE_MANUAL_TABLE_LIST and MANUAL_TABLE_LIST:
    valid_manual, missing = validate_manual_table_list(
        all_silver_tables,
        MANUAL_TABLE_LIST,
    )

    if missing:
        md(f"**⚠️ WARNING:** These tables were not found and will be skipped: {missing}")

    table_by_name = {
        t["name"]: t
        for t in all_silver_tables
    }

    target_tables = [
        table_by_name[tbl]
        for tbl in valid_manual
    ]

    md(f"**Manual entity table list used:** {valid_manual}  ")

else:
    target_tables = all_silver_tables
    md(f"**Auto-discovered tables used:** {len(target_tables)} tables")


table_schemas = []

for tbl in target_tables:
    name = tbl["name"]

    try:
        df = spark.read.table(name)

        cols = [
            {
                "name": f.name,
                "dataType": spark_to_api_type(f.dataType),
            }
            for f in df.schema.fields
        ]

        table_schemas.append({
            "table": name,
            "entity": clean_entity_name(name),
            "columns": cols,
        })

        md(f"&nbsp;&nbsp;✅ `{name}` — {len(cols)} columns")

    except Exception as e:
        md(f"&nbsp;&nbsp;❌ `{name}` — {e}")

md(f"**{len(table_schemas)} tables will be processed for ontology build.**")

total_tables = len(table_schemas)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — RESOLVE PROPERTY TYPES
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔄 Step 2 — Resolving Cross-Table Property Type Conflicts")
md("""
When the same column name appears in multiple tables with different types,
we apply a deterministic resolution:

- If any table sees it as `DateTime`, use `DateTime`
- If all tables agree, use that type
- Otherwise, use the alphabetically first type
""")

property_types: dict[str, set] = {}

for table in table_schemas:
    for col in table["columns"]:
        property_types.setdefault(col["name"], set()).add(col["dataType"])

final_property_type: dict[str, str] = {}

for pname, typeset in property_types.items():
    if "DateTime" in typeset:
        final_property_type[pname] = "DateTime"
    elif len(typeset) == 1:
        final_property_type[pname] = list(typeset)[0]
    else:
        final_property_type[pname] = sorted(typeset)[0]

conflicts = {
    p: t
    for p, t in property_types.items()
    if len(t) > 1
}

if conflicts:
    md(f"⚠️ **{len(conflicts)} type conflicts resolved:**")

    display(pd.DataFrame([
        {
            "Property": p,
            "Observed Types": str(t),
            "Resolved": final_property_type[p],
        }
        for p, t in conflicts.items()
    ]))

else:
    md("✅ No type conflicts found.")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — RESOLVE OWN PK PER ENTITY
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔑 Step 3 — Resolving Own PK per Entity")
md("""
**Rule:** For table `silver_<entity>`, the own PK is resolved by trying:

1. `<entity>_id`
2. `<singular>_id`
3. Any `_id` column whose stem matches the entity name
4. Any `_id` column that appears in only one table
5. `OWN_PK_OVERRIDES` for known model-specific cases

The result is used as the entity's own identity key.
""")


def singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses") or name.endswith("xes"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


col_table_count: dict[str, int] = defaultdict(int)

for t in table_schemas:
    for c in t["columns"]:
        if c["name"].endswith("_id"):
            col_table_count[c["name"]] += 1


own_pk_map: dict[str, str] = {}

for t in table_schemas:
    entity = t["entity"]
    col_names = {
        c["name"]
        for c in t["columns"]
    }

    candidates_to_try = [
        f"{entity}_id",
        f"{singular(entity)}_id",
    ]

    stem_matches = [
        c["name"]
        for c in t["columns"]
        if c["name"].endswith("_id")
        and (
            re.sub(r"_id$", "", c["name"]) in entity
            or entity in re.sub(r"_id$", "", c["name"])
        )
    ]

    unique_cols = [
        c["name"]
        for c in t["columns"]
        if c["name"].endswith("_id")
        and col_table_count[c["name"]] == 1
    ]

    resolved = None

    for candidate in candidates_to_try:
        if candidate in col_names:
            resolved = candidate
            break

    if not resolved:
        for candidate in stem_matches:
            if candidate in col_names:
                resolved = candidate
                break

    if not resolved and unique_cols:
        resolved = unique_cols[0]

    if resolved:
        own_pk_map[entity] = resolved

        if resolved != f"{entity}_id":
            md(f"&nbsp;&nbsp;🔑 `{entity}` — own PK resolved as `{resolved}`")

    elif entity in OWN_PK_OVERRIDES:
        own_pk_map[entity] = OWN_PK_OVERRIDES[entity]
        md(f"&nbsp;&nbsp;🔑 `{entity}` — own PK from override: `{OWN_PK_OVERRIDES[entity]}`")

    else:
        own_pk_map[entity] = f"{entity}_id"
        md(
            f"&nbsp;&nbsp;⚠️ `{entity}` — could not resolve own PK, "
            f"falling back to `{entity}_id`"
        )


display(pd.DataFrame([
    {
        "Entity": e,
        "Own PK": pk,
        "In Table": pk in {
            c["name"]
            for c in next(
                t["columns"]
                for t in table_schemas
                if t["entity"] == e
            )
        },
    }
    for e, pk in sorted(own_pk_map.items())
]))


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — BUILD entityIdParts PER ENTITY
# ══════════════════════════════════════════════════════════════════════════════

md("## 🧩 Step 4 — Building entityIdParts per Entity")
md("""
**Rule:** `entityIdParts = own PK only`.

Scope and hierarchy columns such as `facility_id`, `system_id`, and
`equipment_id` stay as normal properties. They are used for relationship
contextualization where they exist in both the source and target tables.

This avoids making every relationship require all scope columns.
""")

all_own_pks = set(own_pk_map.values())

entity_col_map = {
    t["entity"]: {
        c["name"]
        for c in t["columns"]
    }
    for t in table_schemas
}

pk_to_entity: dict[str, str] = {
    pk: ename
    for ename, pk in own_pk_map.items()
}

entity_id_parts_map: dict[str, list] = {}

id_parts_report = []

for t in table_schemas:
    entity = t["entity"]
    own_pk = own_pk_map.get(entity)

    id_parts = [
        own_pk
    ] if own_pk and own_pk in entity_col_map[entity] else []

    fk_cols = [
        c["name"]
        for c in t["columns"]
        if c["name"].endswith("_id")
        and c["name"] != own_pk
        and c["name"] in pk_to_entity
    ]

    other_id_cols = [
        c["name"]
        for c in t["columns"]
        if c["name"].endswith("_id")
        and c["name"] != own_pk
        and c["name"] not in pk_to_entity
    ]

    entity_id_parts_map[entity] = id_parts

    id_parts_report.append({
        "Entity": entity,
        "Own PK": own_pk or "⚠️ not found",
        "entityIdParts": str(id_parts),
        "FK cols": str(fk_cols),
        "Other _id cols": str(other_id_cols),
    })

display(pd.DataFrame(id_parts_report))


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — GENERATE ENTITY TYPE PARTS
# ══════════════════════════════════════════════════════════════════════════════

md("## ⚙️ Step 5 — Generating EntityType Parts")
md("""
Clean RTI binding model:

- Static Lakehouse tables become ontology entity types.
- `signal_master` keeps structured metadata from `silver_signal_master`.
- `signal_master` also receives Eventhouse RTI `timeseriesProperties`:
  `event_time`, `value`, and `quality`.
- RTI telemetry remains in Eventhouse and is bound directly later.
- No copied RTI measurement entity is created in Lakehouse.
""")

parts = []
entity_defs = []
property_id_globalset: set[int] = set()
id_to_entity: dict[str, int] = {}
entity_pid_map: dict[str, dict] = {}

SIGNAL_MASTER_ENTITY = "signal_master"

RTI_TIMESERIES_PROPERTIES = [
    {
        "name": "event_time",
        "dataType": "DateTime",
    },
    {
        "name": "value",
        "dataType": "Double",
    },
    {
        "name": "quality",
        "dataType": "String",
    },
]

for table in table_schemas:
    entity_name = table["entity"]
    entity_id = generate_id(entity_name)

    id_to_entity[entity_name] = entity_id

    columns = [
        dict(c)
        for c in table["columns"]
    ]

    props = []
    timeseries_props = []
    pid_by_col: dict[str, int] = {}

    for col in columns:
        pid = generate_id(f"{entity_id}:{col['name']}")

        while pid in property_id_globalset:
            pid = generate_id(f"{entity_id}:{col['name']}", offset=pid)

        property_id_globalset.add(pid)
        pid_by_col[col["name"]] = pid

        resolved_type = final_property_type.get(
            col["name"],
            col["dataType"],
        )

        props.append({
            "id": str(pid),
            "name": col["name"],
            "valueType": resolved_type if resolved_type in VALID_VALUE_TYPES else "String",
        })

    if entity_name == SIGNAL_MASTER_ENTITY:
        existing_static_names = {
            c["name"]
            for c in columns
        }

        for ts_col in RTI_TIMESERIES_PROPERTIES:
            if ts_col["name"] in existing_static_names:
                raise RuntimeError(
                    f"RTI time-series property '{ts_col['name']}' already exists as a static "
                    f"property on '{SIGNAL_MASTER_ENTITY}'. Remove it from the static source schema."
                )

            pid = generate_id(f"{entity_id}:timeseries:{ts_col['name']}")

            while pid in property_id_globalset:
                pid = generate_id(
                    f"{entity_id}:timeseries:{ts_col['name']}",
                    offset=pid,
                )

            property_id_globalset.add(pid)
            pid_by_col[ts_col["name"]] = pid

            timeseries_props.append({
                "id": str(pid),
                "name": ts_col["name"],
                "valueType": ts_col["dataType"] if ts_col["dataType"] in VALID_VALUE_TYPES else "String",
            })

        md(
            "&nbsp;&nbsp;📡 `signal_master` — added Eventhouse RTI "
            "`timeseriesProperties`: `event_time`, `value`, `quality`"
        )

    id_part_cols = entity_id_parts_map.get(entity_name, [])

    entityIdParts = [
        str(pid_by_col[col_name])
        for col_name in id_part_cols
        if col_name in pid_by_col
    ]

    if not entityIdParts:
        md(
            f"&nbsp;&nbsp;⚠️ `{entity_name}` — own PK `{own_pk_map.get(entity_name)}` "
            "not found in static properties. Add to `OWN_PK_OVERRIDES` in CONFIG. "
            "Skipping entityIdParts."
        )

        displayNamePropertyId = props[0]["id"] if props else None

    else:
        displayNamePropertyId = entityIdParts[0]

    entity_def = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/ontology/entityType/1.0.0/schema.json",
        "id": str(entity_id),
        "namespace": "usertypes",
        "name": entity_name,
        "namespaceType": "Custom",
        "visibility": "Visible",
        "properties": props,
        "entityIdParts": entityIdParts if entityIdParts else None,
        "displayNamePropertyId": displayNamePropertyId,
        "timeseriesProperties": timeseries_props,
        "baseEntityTypeId": None,
    }

    entity_def = {
        k: v
        for k, v in entity_def.items()
        if v is not None
    }

    entity_defs.append((str(entity_id), entity_def))

    entity_pid_map[str(entity_id)] = {
        name: str(pid)
        for name, pid in pid_by_col.items()
    }

    parts.append({
        "path": f"EntityTypes/{entity_id}/definition.json",
        "payload": encode_payload(entity_def),
        "payloadType": "InlineBase64",
    })

md(f"✅ **{len(entity_defs)} entity type parts** generated.")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — GENERATE RELATIONSHIP TYPE PARTS
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔗 Step 6 — Generating RelationshipType Parts")
md("""
Only direct semantic hierarchy relationships are generated.

Target path:

`signal_master → instruments → equipment → systems → facilities`
""")

ENTITY_PARENT_MAP = {
    "systems": "facilities",
    "equipment": "systems",
    "instruments": "equipment",
    "signal_master": "instruments",
}

REL_NAME_OVERRIDES = {
    ("systems", "facilities"): "systems_in_facilities",
    ("equipment", "systems"): "equipment_in_systems",
    ("instruments", "equipment"): "instruments_on_equipment",
    ("signal_master", "instruments"): "signals_from_instruments",
}

rel_id_globalset: set[int] = set()
rel_defs = []
valid_rels = []
ctx_gap_rels = []
generated_rel_keys: set[str] = set()


def make_rel_id(src_eid: str, tgt_eid: str, rel_name: str) -> str:
    rid = generate_id(f"R:{src_eid}:{tgt_eid}:{rel_name}")

    while rid in rel_id_globalset:
        rid = generate_id(
            f"R:{src_eid}:{tgt_eid}:{rel_name}",
            offset=rid,
        )

    rel_id_globalset.add(rid)

    return str(rid)


def make_safe_rel_name(src_entity: str, tgt_entity: str) -> str:
    override = REL_NAME_OVERRIDES.get((src_entity, tgt_entity))

    if override:
        return override

    candidate = f"{src_entity}_to_{tgt_entity}"
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", candidate)

    if len(candidate) <= 26:
        return candidate

    return candidate[:26].rstrip("_") or "relationship"


entity_names = {
    t["entity"]
    for t in table_schemas
}

configured_missing = [
    (src, tgt)
    for src, tgt in ENTITY_PARENT_MAP.items()
    if src not in entity_names or tgt not in entity_names
]

if configured_missing:
    md("⚠️ Some configured relationships refer to entities not present in this build:")

    display(pd.DataFrame([
        {
            "Source entity": src,
            "Target entity": tgt,
            "Source exists": src in entity_names,
            "Target exists": tgt in entity_names,
        }
        for src, tgt in configured_missing
    ]))


for src_entity, tgt_entity in ENTITY_PARENT_MAP.items():
    if src_entity not in entity_names or tgt_entity not in entity_names:
        continue

    src_eid = str(id_to_entity[src_entity])
    tgt_eid = str(id_to_entity[tgt_entity])

    src_cols = entity_col_map[src_entity]
    tgt_id_parts = entity_id_parts_map.get(tgt_entity, [])
    tgt_own_pk = own_pk_map.get(tgt_entity)

    if not tgt_own_pk:
        ctx_gap_rels.append({
            "Relationship": f"{src_entity} → {tgt_entity}",
            "tgt Own PK": "",
            "Full tgt IdParts": str(tgt_id_parts),
            "Effective join keys": "[]",
            "Missing in src": str(tgt_id_parts),
            "Note": "⚠️ Target own PK not resolved",
        })
        continue

    effective_keys = [
        key
        for key in tgt_id_parts
        if key in src_cols
    ]

    missing_in_src = [
        key
        for key in tgt_id_parts
        if key not in src_cols
    ]

    rel_name = make_safe_rel_name(src_entity, tgt_entity)
    rel_key = f"{src_entity}→{tgt_entity}"

    if rel_key in generated_rel_keys:
        continue

    generated_rel_keys.add(rel_key)

    rid = make_rel_id(src_eid, tgt_eid, rel_name)

    rel_def = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/ontology/relationshipType/1.0.0/schema.json",
        "namespace": "usertypes",
        "id": rid,
        "name": rel_name,
        "namespaceType": "Custom",
        "source": {
            "entityTypeId": src_eid,
        },
        "target": {
            "entityTypeId": tgt_eid,
        },
    }

    rel_defs.append(rel_def)

    parts.append({
        "path": f"RelationshipTypes/{rid}/definition.json",
        "payload": encode_payload(rel_def),
        "payloadType": "InlineBase64",
    })

    rel_label = f"{src_entity} → {tgt_entity}"

    if missing_in_src:
        ctx_gap_rels.append({
            "Relationship": rel_label,
            "tgt Own PK": tgt_own_pk,
            "Full tgt IdParts": str(tgt_id_parts),
            "Effective join keys": str(effective_keys),
            "Missing in src": str(missing_in_src),
            "Note": "⚠️ Source table is missing the target identity key",
        })

    valid_rels.append({
        "Relationship": rel_label,
        "tgt Own PK": tgt_own_pk,
        "Effective join keys": str(effective_keys),
        "Rel Name": rel_name,
        "Status": "✅ Full" if not missing_in_src else "⚠️ Partial",
    })


md(f"### ✅ Relationships Generated ({len(valid_rels)})")

if valid_rels:
    display(pd.DataFrame(valid_rels))
else:
    md("⚠️ No relationships were generated. Check `ENTITY_PARENT_MAP` and table schemas.")

if ctx_gap_rels:
    md(f"### ⚠️ {len(ctx_gap_rels)} relationships have join-key gaps")
    display(pd.DataFrame(ctx_gap_rels))
else:
    md("✅ All generated relationships have full join key coverage.")

md("""
### Resulting AI-friendly relationship path

`signal_master → instruments → equipment → systems → facilities`

This gives AI one clear semantic path from RTI signals back to physical and
organizational asset context.
""")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — ASSEMBLE FINAL PARTS LIST
# ══════════════════════════════════════════════════════════════════════════════

md("## 📦 Step 7 — Assembling Final Parts")

parts.insert(0, {
    "path": ".platform",
    "payload": encode_payload({
        "metadata": {
            "type": "Ontology",
            "displayName": ONTOLOGY_NAME,
        }
    }),
    "payloadType": "InlineBase64",
})

parts.insert(1, {
    "path": "definition.json",
    "payload": encode_payload({}),
    "payloadType": "InlineBase64",
})

md(
    f"**Total parts:** {len(parts)}  "
    f"({len(entity_defs)} entities + {len(rel_defs)} relationships + 2 root)"
)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — SAVE OUTPUTS
# ══════════════════════════════════════════════════════════════════════════════

md("## 💾 Step 8 — Saving Outputs")

generated_at = pd.Timestamp.now().isoformat()


def safe_decode(payload: str) -> dict:
    try:
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        return {}


parts_rows = []

for p in parts:
    path = p["path"]
    segs = path.split("/")
    part_type = segs[0]
    decoded = safe_decode(p.get("payload", ""))

    parts_rows.append({
        "path": path,
        "part_type": part_type,
        "sub_type": segs[2] if len(segs) >= 3 else "",
        "item_id": str(decoded.get("id", "")),
        "item_name": decoded.get("name", decoded.get("displayName", "")),
        "namespace": decoded.get("namespace", ""),
        "entity_id_parts": json.dumps(decoded.get("entityIdParts", [])),
        "source_entity_id": decoded.get("source", {}).get("entityTypeId", ""),
        "target_entity_id": decoded.get("target", {}).get("entityTypeId", ""),
        "payload": p.get("payload", ""),
        "payload_type": p.get("payloadType", ""),
        "generated_at": generated_at,
    })

(
    spark.createDataFrame(pd.DataFrame(parts_rows))
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("ontology_parts_latest")
)

md("✅ `ontology_parts_latest` — API-ready parts for deploy, DataBindings, and Contextualizations")


rel_rows = []

for r in valid_rels:
    gap = next(
        (
            g
            for g in ctx_gap_rels
            if g["Relationship"] == r["Relationship"]
        ),
        None,
    )

    rel_rows.append({
        "relationship": r["Relationship"],
        "tgt_own_pk": r["tgt Own PK"],
        "rel_name": r["Rel Name"],
        "effective_join_keys": r["Effective join keys"],
        "missing_in_src": gap["Missing in src"] if gap else "",
        "status": r["Status"],
        "generated_at": generated_at,
    })

(
    spark.createDataFrame(pd.DataFrame(rel_rows))
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("ontology_relationship_audit")
)

md("✅ `ontology_relationship_audit` — effective join keys used by contextualization script")


entity_audit_rows = [
    {
        "entity": entity,
        "own_pk": own_pk_map.get(entity) or "",
        "entity_id_parts": json.dumps(entity_id_parts_map.get(entity, [])),
        "fk_cols": json.dumps([
            c
            for c in entity_col_map.get(entity, set())
            if c.endswith("_id")
            and c != own_pk_map.get(entity)
            and c in pk_to_entity
        ]),
        "generated_at": generated_at,
    }
    for entity in sorted(entity_id_parts_map)
]

(
    spark.createDataFrame(pd.DataFrame(entity_audit_rows))
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("ontology_entity_audit")
)

md("✅ `ontology_entity_audit` — entity PK and identity decisions for human review")

md(f"""
| Table | Rows | Used by |
|---|---:|---|
| `ontology_parts_latest` | {len(parts_rows)} | ontology deploy, DataBindings, Contextualizations |
| `ontology_relationship_audit` | {len(rel_rows)} | Contextualizations and human review |
| `ontology_entity_audit` | {len(entity_audit_rows)} | Human review |
""")


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

md(f"""
---
## 📋 Summary

| Item | Count |
|---|---:|
| Tables loaded | **{total_tables}** |
| Entity types generated | **{len(entity_defs)}** |
| Relationships generated | **{len(valid_rels)}** |
| Relationships with ctx gaps | **{len(ctx_gap_rels)}** |
| Total ontology parts | **{len(parts)}** |

## Logic Applied

| Decision | Rule |
|---|---|
| **Source tables** | Uses `MANUAL_TABLE_LIST` when `USE_MANUAL_TABLE_LIST = True` |
| **Own PK** | Resolved from table/entity name, with `OWN_PK_OVERRIDES` for known model-specific cases |
| **entityIdParts** | Own PK only |
| **Static properties** | All selected Lakehouse table columns become normal ontology properties |
| **RTI time-series properties** | `event_time`, `value`, and `quality` are added as `timeseriesProperties` on `signal_master` |
| **RTI telemetry storage** | Remains in Eventhouse; no copied RTI measurement table/entity is created in Lakehouse |
| **Relationship policy** | Only direct semantic hierarchy relationships from `ENTITY_PARENT_MAP` |
| **Relationship path** | `signal_master → instruments → equipment → systems → facilities` |
| **Effective join keys** | Target `entityIdParts` that are present in the source table |
| **Type conflict** | Same column, different types across tables → DateTime wins, else deterministic fallback |
""")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""
ontology_api_helpers.py – Fabric deployment helpers for RTI structured ontology
════════════════════════════════════════════════════════════════════════════════
Fabric Ontology API helpers for deploying the structured ontology with direct
Eventhouse RTI binding.

Expected config variables from the 004 config cell:
- workspace_id
- target_folder_id
- key_vault_uri
- key_vault_tenant_id_secret
- key_vault_client_id_secret
- key_vault_client_secret_secret
- ONTOLOGY_NAME
"""

import requests
import time
import json
from typing import Optional
from IPython.display import display, Markdown

# ── API / retry config ───────────────────────────────────────────────────────

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
FABRIC_API_VERSION = "v1"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

# ── Validate required config from shared settings ─────────────────────────────

required_helper_config = [
    "workspace_id",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
]

missing_helper_config = [
    name
    for name in required_helper_config
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_helper_config:
    raise RuntimeError(
        "Missing required ontology API helper config values: "
        f"{missing_helper_config}. Run the 004 config cell first."
    )

if "target_folder_id" not in globals() or not target_folder_id:
    print("⚠️ target_folder_id is not defined. Ontology folder guard will not be applied.")


# ── Token cache ───────────────────────────────────────────────────────────────

_token_cache = {
    "token": None,
    "expires_at": 0.0,
}


def get_spn_access_token() -> str:
    """
    Fetch SPN token from Key Vault and cache it until 60 seconds before expiry.

    Uses Key Vault secret names from rti_demo_settings/config:
    - key_vault_tenant_id_secret
    - key_vault_client_id_secret
    - key_vault_client_secret_secret
    """

    now = time.time()

    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    tenant_id = notebookutils.credentials.getSecret(
        key_vault_uri,
        key_vault_tenant_id_secret,
    )

    client_id = notebookutils.credentials.getSecret(
        key_vault_uri,
        key_vault_client_id_secret,
    )

    client_secret = notebookutils.credentials.getSecret(
        key_vault_uri,
        key_vault_client_secret_secret,
    )

    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://api.fabric.microsoft.com/.default",
        },
        timeout=30,
    )

    resp.raise_for_status()

    token_data = resp.json()

    _token_cache["token"] = token_data["access_token"]
    _token_cache["expires_at"] = now + token_data.get("expires_in", 3600) - 60

    return _token_cache["token"]


def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_spn_access_token()}",
        "Content-Type": "application/json",
    }


# ── Core request helper ───────────────────────────────────────────────────────

def api_request(
    method: str,
    url: str,
    data=None,
    params=None,
    timeout: int = 60,
) -> requests.Response:
    """
    Retryable API request with:
    - 429 backoff
    - 5xx retry
    - SPN auth header refresh per attempt
    """

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=get_headers(),
                json=data,
                params=params,
                timeout=timeout,
            )

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                print(
                    f"Rate limited — retrying in {wait}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                print(
                    f"Server error {resp.status_code} — retrying in "
                    f"{RETRY_DELAY_SECONDS}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            return resp

        except requests.exceptions.RequestException as ex:
            print(
                f"Request exception: {ex} "
                f"(attempt {attempt + 1}/{MAX_RETRIES})"
            )

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise

    raise RuntimeError(f"API request failed after {MAX_RETRIES} attempts: {method} {url}")


# ── LRO polling ───────────────────────────────────────────────────────────────

def wait_for_lro(operation_url: str) -> dict:
    """
    Poll a Fabric long-running operation until Succeeded/Completed/Failed/Cancelled.
    """

    start = time.time()

    while time.time() - start < LRO_MAX_WAIT_SECONDS:
        resp = api_request("GET", operation_url, timeout=60)

        if resp.status_code >= 400:
            print(f"❌ LRO poll failed: {resp.status_code}")
            print(resp.text[:3000])
            raise RuntimeError(f"LRO poll failed: {resp.status_code}")

        try:
            result = resp.json()
        except Exception:
            print("❌ LRO poll response was not valid JSON.")
            print(resp.text[:3000])
            raise

        status = result.get("status", "Unknown")

        if status in ("Succeeded", "Completed"):
            print(f"✅ LRO completed: {status}")
            return result

        if status in ("Failed", "Cancelled"):
            error = result.get("error", {})
            raise RuntimeError(f"LRO {status}: {error}")

        print(
            f"  ⏳ LRO status: {status} — polling again in "
            f"{LRO_POLL_INTERVAL_SECONDS}s"
        )

        time.sleep(LRO_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"LRO timed out after {LRO_MAX_WAIT_SECONDS}s — last URL: {operation_url}"
    )


# ── Ontology API functions ────────────────────────────────────────────────────

def list_ontologies() -> list:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{workspace_id}/ontologies"
    )

    resp = api_request("GET", url)

    if resp.status_code == 200:
        return resp.json().get("value", [])

    print(f"Failed to list ontologies: {resp.status_code}")
    print(resp.text[:3000])

    return []


def get_ontology(ontology_id: str) -> Optional[dict]:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{workspace_id}/ontologies/{ontology_id}"
    )

    resp = api_request("GET", url)

    if resp.status_code == 200:
        return resp.json()

    print(f"Could not retrieve ontology {ontology_id}: {resp.status_code}")
    print(resp.text[:3000])

    return None


def find_ontology_by_name(
    display_name: str,
    folder_id: Optional[str] = None,
    enforce_folder_guard: bool = True,
) -> Optional[dict]:
    """
    Find ontology by display name.

    If folder_id is supplied, return only the ontology in that folder.

    If an ontology with the same display name exists outside the target folder
    and no matching ontology exists inside the target folder, raise a clear
    error instead of silently reusing the wrong item.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = globals().get("target_folder_id")

    matches = [
        ont
        for ont in list_ontologies()
        if ont.get("displayName") == display_name
    ]

    if not matches:
        return None

    if not resolved_folder_id:
        return matches[0]

    matches_in_folder = [
        ont
        for ont in matches
        if ont.get("folderId") == resolved_folder_id
    ]

    if matches_in_folder:
        return matches_in_folder[0]

    if enforce_folder_guard:
        first = matches[0]
        raise RuntimeError(
            f"Ontology '{display_name}' already exists, but not in the target folder.\n"
            f"Existing ontology ID: {first.get('id')}\n"
            f"Existing folder ID: {first.get('folderId')}\n"
            f"Target folder ID: {resolved_folder_id}\n"
            "For a clean from-scratch test, delete the existing ontology or change the ontology name."
        )

    return None


def create_ontology(
    display_name: str,
    description: str = "",
    folder_id: Optional[str] = None,
) -> dict:
    """
    Create a Fabric ontology.

    Uses target_folder_id by default when folder_id is not supplied.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = globals().get("target_folder_id")

    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{workspace_id}/ontologies"
    )

    data = {
        "displayName": display_name,
        "description": description,
    }

    if resolved_folder_id:
        data["folderId"] = resolved_folder_id
        print(f"Creating ontology in folder: {resolved_folder_id}")

    resp = api_request("POST", url, data=data)

    if resp.status_code == 201:
        result = resp.json()
        display(Markdown(f"**✅ Created Ontology ID:** `{result.get('id')}`"))
        return result

    if resp.status_code == 202:
        operation_url = (
            resp.headers.get("Location")
            or resp.headers.get("Operation-Location")
            or resp.headers.get("operation-location")
        )

        if not operation_url:
            raise RuntimeError("Location header missing for async ontology create.")

        wait_for_lro(operation_url)

        created = find_ontology_by_name(
            display_name=display_name,
            folder_id=resolved_folder_id,
            enforce_folder_guard=False,
        )

        if created:
            display(Markdown(f"**✅ Created Ontology ID via LRO:** `{created.get('id')}`"))
            return created

        raise RuntimeError("LRO completed but ontology was not found by name.")

    print(f"Failed to create ontology: {resp.status_code}")
    print(resp.text[:3000])

    raise RuntimeError(f"Create ontology failed: {resp.status_code}")


def ensure_ontology(
    display_name: str,
    description: str = "",
    folder_id: Optional[str] = None,
) -> dict:
    """
    Return existing ontology in the target folder, or create it.

    Does not silently reuse an ontology with the same name outside the target folder.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = globals().get("target_folder_id")

    existing = find_ontology_by_name(
        display_name=display_name,
        folder_id=resolved_folder_id,
        enforce_folder_guard=True,
    )

    if existing:
        display(Markdown(f"**✅ Reusing Ontology ID:** `{existing.get('id')}`"))
        return existing

    return create_ontology(
        display_name=display_name,
        description=description,
        folder_id=resolved_folder_id,
    )


def get_ontology_definition(ontology_id: str) -> dict:
    """
    Fabric getDefinition pattern:
    1. POST getDefinition
    2. If 202, poll LRO Location
    3. Fetch result from {operation_url}/result
    """

    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{workspace_id}"
        f"/ontologies/{ontology_id}/getDefinition"
    )

    resp = api_request("POST", url)

    if resp.status_code == 200:
        if not resp.text or not resp.text.strip():
            return {}

        return resp.json()

    if resp.status_code == 202:
        operation_url = (
            resp.headers.get("Location")
            or resp.headers.get("Operation-Location")
            or resp.headers.get("operation-location")
        )

        if not operation_url:
            raise RuntimeError("Location header missing for LRO getDefinition.")

        wait_for_lro(operation_url)

        result_url = operation_url.rstrip("/") + "/result"
        result_resp = api_request("GET", result_url)

        if result_resp.status_code == 200:
            if not result_resp.text or not result_resp.text.strip():
                return {}

            return result_resp.json()

        print(f"Failed to fetch definition result: {result_resp.status_code}")
        print(result_resp.text[:3000])

        return {}

    print(f"Failed to get ontology definition: {resp.status_code}")
    print(resp.text[:3000])

    return {}


def update_ontology_definition(
    ontology_id: str,
    definition_data: dict,
) -> dict:
    """
    Push ontology definition to Fabric.

    Handles:
    - 202 Accepted with LRO polling
    - 200 OK with JSON body
    - 200 OK with empty body
    """

    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{workspace_id}"
        f"/ontologies/{ontology_id}/updateDefinition"
    )

    response = api_request(
        "POST",
        url,
        data=definition_data,
        timeout=300,
    )

    if response.status_code == 202:
        operation_url = (
            response.headers.get("Location")
            or response.headers.get("Operation-Location")
            or response.headers.get("operation-location")
        )

        if not operation_url:
            raise RuntimeError(
                "Fabric returned 202 Accepted but no LRO Location header was found."
            )

        lro_result = wait_for_lro(operation_url)
        print("✅ Definition update async LRO complete")

        return lro_result or {}

    if response.status_code == 200:
        print("✅ Definition updated successfully")

        if not response.text or not response.text.strip():
            return {}

        try:
            return response.json()
        except Exception:
            print("⚠️ Fabric returned HTTP 200, but the response body was not JSON.")
            print(response.text[:3000])
            return {}

    print(f"❌ Failed to update definition: {response.status_code}")
    print(response.text[:3000])

    raise RuntimeError(f"Update definition failed: {response.status_code}")


print("✅ Ontology API helpers loaded.")
print("✅ Workspace ID:", workspace_id)
print("✅ Target folder ID:", globals().get("target_folder_id"))
print("✅ Key Vault URI:", key_vault_uri)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""
ontology_deploy.py – deploy structured RTI ontology
════════════════════════════════════════════════════════════════════════════════
Idempotent deployment step for the structured RTI ontology.

Creates/reuses the ontology inside target_folder_id, then updates the ontology
definition using parts generated by the ontology parts generation cell.

Expected previous cells:
- 004 config cell
- ontology_generate_all_parts cell
- ontology_api_helpers cell
"""

import json
import time
import base64 as _base64
from collections import Counter
from IPython.display import display, Markdown


def md(text):
    display(Markdown(text))


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def count_parts_by_type(parts: list) -> dict:
    counts = Counter()

    for p in parts:
        seg = p.get("path", "").split("/")[0]
        counts[seg] += 1

    return dict(counts)


def parts_summary_md(parts: list, label: str) -> str:
    breakdown = count_parts_by_type(parts)
    rows = "\n".join(
        f"| `{k}` | {v} |"
        for k, v in sorted(breakdown.items())
    )

    return (
        f"**{label}** — {len(parts)} total parts\n\n"
        f"| Path segment | Count |\n|---|---:|\n{rows}"
    )


def _decode(payload: str) -> dict:
    try:
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(_base64.b64decode(padded).decode("utf-8"))
    except Exception:
        return {}


def _encode(obj: dict) -> str:
    return _base64.b64encode(
        json.dumps(obj, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def safe_rel_id(src: str, tgt: str, rel_name: str, used_ids: set) -> str:
    """
    Generate a positive 64-bit integer relationship ID that does not collide
    with live ontology IDs, entity IDs, property IDs, timeseries property IDs,
    or relationship IDs already assigned in this batch.
    """

    import hashlib

    seed = f"R:{src}:{tgt}:{rel_name}"
    attempt = 0

    while True:
        h = hashlib.sha256((seed + str(attempt)).encode()).digest()
        id64 = int.from_bytes(h[:8], "big", signed=False) % (2**63)
        sid = str(id64)

        if sid not in used_ids:
            used_ids.add(sid)
            return sid

        attempt += 1


# ══════════════════════════════════════════════════════════════════════════════
# Step 0 — Validate required config/helpers
# ══════════════════════════════════════════════════════════════════════════════

required_globals = [
    "ONTOLOGY_NAME",
    "workspace_id",
    "target_folder_id",
    "ensure_ontology",
    "get_ontology_definition",
    "update_ontology_definition",
]

missing_globals = [
    name
    for name in required_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_globals:
    raise RuntimeError(
        "Missing required deploy variables/helpers. "
        f"Run the 004 config and ontology API helper cells first. Missing: {missing_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Load parts from Lakehouse tables
# ══════════════════════════════════════════════════════════════════════════════

md("## 📥 Step 1 — Loading Ontology Parts from Lakehouse Tables")

try:
    parts_df = (
        spark.read.table("ontology_parts_latest")
        .select(
            "path",
            "payload",
            "payload_type",
            "item_name",
            "part_type",
            "generated_at",
        )
        .orderBy("path")
    )

    parts_rows = parts_df.collect()

    if not parts_rows:
        raise ValueError("ontology_parts_latest is empty — run the ontology generation cell first.")

    parts = [
        {
            "path": r["path"],
            "payload": r["payload"],
            "payloadType": r["payload_type"],
        }
        for r in parts_rows
    ]

    generated_at = parts_rows[0]["generated_at"]

except Exception as e:
    raise RuntimeError(
        f"Could not load from ontology_parts_latest: {e}\n"
        "Run the ontology generation cell first."
    )

md(parts_summary_md(parts, "Parts loaded from `ontology_parts_latest`"))

try:
    entity_count = spark.read.table("ontology_entity_audit").count()
    rel_count = spark.read.table("ontology_relationship_audit").count()

    md(f"""
**Audit tables cross-check:**

| Table | Rows |
|---|---:|
| `ontology_parts_latest` | {len(parts)} parts |
| `ontology_entity_audit` | {entity_count} entities |
| `ontology_relationship_audit` | {rel_count} relationships |
| Generated at | `{generated_at}` |
""")

except Exception as e:
    md(f"⚠️ Could not read audit tables for cross-check: `{e}`")


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Ensure ontology exists in target folder
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔍 Step 2 — Ensure Ontology Exists in Target Folder")

ontology = ensure_ontology(
    display_name=ONTOLOGY_NAME,
    description="Auto-generated structured RTI ontology.",
    folder_id=target_folder_id,
)

ontology_id = ontology.get("id")

if not ontology_id:
    raise RuntimeError(f"Ontology '{ONTOLOGY_NAME}' was returned without an id: {ontology}")

md(f"""
✅ Ontology ready.

| Property | Value |
|---|---|
| Name | `{ONTOLOGY_NAME}` |
| ID | `{ontology_id}` |
| Target folder ID | `{target_folder_id}` |
""")


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Fetch live definition and merge
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔀 Step 3 — Fetching Live Definition & Merging")

md("""
Merge strategy:

- EntityTypes and root parts are replaced with the freshly generated version.
- Existing RelationshipTypes are reused when the live ontology already has the
  same source and target entity pair.
- New RelationshipTypes receive collision-safe IDs.
- RTI telemetry remains represented as `timeseriesProperties` on `signal_master`;
  no copied RTI measurement entity is created.
""")

live_def = get_ontology_definition(ontology_id)
live_parts = live_def.get("definition", {}).get("parts", [])

md(parts_summary_md(live_parts, "Live definition before merge"))

used_id_set: set[str] = set()

# Reserve IDs from live ontology.
for p in live_parts:
    segs = p.get("path", "").split("/")

    if len(segs) >= 2:
        used_id_set.add(segs[1])

    obj = _decode(p.get("payload", ""))

    if "id" in obj:
        used_id_set.add(str(obj["id"]))

    for prop in obj.get("properties", []):
        if "id" in prop:
            used_id_set.add(str(prop["id"]))

    for prop in obj.get("timeseriesProperties", []):
        if "id" in prop:
            used_id_set.add(str(prop["id"]))

# Reserve IDs from current generated batch.
for p in parts:
    segs = p.get("path", "").split("/")

    if len(segs) >= 2:
        used_id_set.add(segs[1])

    obj = _decode(p.get("payload", ""))

    if "id" in obj:
        used_id_set.add(str(obj["id"]))

    for prop in obj.get("properties", []):
        if "id" in prop:
            used_id_set.add(str(prop["id"]))

    for prop in obj.get("timeseriesProperties", []):
        if "id" in prop:
            used_id_set.add(str(prop["id"]))

md(f"&nbsp;&nbsp;🔒 **{len(used_id_set)} IDs** reserved for collision avoidance")

live_by_path = {
    p["path"]: p
    for p in live_parts
}

live_rel_by_pair: dict[tuple, dict] = {}

for p in live_parts:
    segs = p.get("path", "").split("/")

    if segs[0] == "RelationshipTypes" and len(segs) == 3:
        obj = _decode(p.get("payload", ""))
        src = obj.get("source", {}).get("entityTypeId", "")
        tgt = obj.get("target", {}).get("entityTypeId", "")

        if src and tgt:
            live_rel_by_pair[(src, tgt)] = p


merged_parts = []
added = 0
reused = 0
updated = 0

for p in parts:
    segs = p["path"].split("/")

    if segs[0] == "RelationshipTypes" and len(segs) == 3:
        obj = _decode(p["payload"])
        src = obj.get("source", {}).get("entityTypeId", "")
        tgt = obj.get("target", {}).get("entityTypeId", "")
        pair = (src, tgt)

        if pair in live_rel_by_pair:
            live_part = live_rel_by_pair[pair]
            live_obj = _decode(live_part.get("payload", ""))

            if live_obj.get("name") != obj.get("name"):
                live_obj["name"] = obj["name"]
                live_part = dict(
                    live_part,
                    payload=_encode(live_obj),
                )
                updated += 1
                md(f"&nbsp;&nbsp;🔄 `{obj.get('name')}` — name updated on existing relationship")
            else:
                reused += 1

            merged_parts.append(live_part)

        else:
            rel_name = obj.get("name", "relationship")
            new_id = safe_rel_id(src, tgt, rel_name, used_id_set)

            obj["id"] = new_id

            if "$schema" not in obj:
                obj["$schema"] = (
                    "https://developer.microsoft.com/json-schemas/fabric/item/"
                    "ontology/relationshipType/1.0.0/schema.json"
                )

            new_path = f"RelationshipTypes/{new_id}/definition.json"

            merged_parts.append({
                "path": new_path,
                "payload": _encode(obj),
                "payloadType": "InlineBase64",
            })

            added += 1
            md(
                f"&nbsp;&nbsp;➕ `{obj.get('name')}` — id=`{new_id}` "
                f"(`{src[:8]}…` → `{tgt[:8]}…`)"
            )

    else:
        merged_parts.append(p)

        if p["path"] in live_by_path:
            updated += 1
        else:
            added += 1

md(f"""
**Merge result:** {len(merged_parts)} total parts

| Result | Count |
|---|---:|
| Reused from live relationship pair | {reused} |
| Updated or replaced | {updated} |
| Net new | {added} |
""")


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Push definition in two stages
# ══════════════════════════════════════════════════════════════════════════════

md("## 🚀 Step 4 — Pushing Definition in Two Stages")

md("""
EntityTypes are pushed first so Fabric registers all entity IDs before
RelationshipTypes reference them.
""")

stage1_parts = [
    p
    for p in merged_parts
    if not p["path"].startswith("RelationshipTypes/")
]

md(f"**Stage 1** — pushing {len(stage1_parts)} parts: root + EntityTypes")

update_ontology_definition(
    ontology_id,
    {
        "definition": {
            "parts": stage1_parts,
        }
    },
)

md("✅ Stage 1 complete — entities registered.")

time.sleep(3)

live_after_stage1 = (
    get_ontology_definition(ontology_id)
    .get("definition", {})
    .get("parts", [])
)

for p in live_after_stage1:
    segs = p.get("path", "").split("/")

    if len(segs) >= 2:
        used_id_set.add(segs[1])

    obj = _decode(p.get("payload", ""))

    if "id" in obj:
        used_id_set.add(str(obj["id"]))

    for prop in obj.get("properties", []):
        if "id" in prop:
            used_id_set.add(str(prop["id"]))

    for prop in obj.get("timeseriesProperties", []):
        if "id" in prop:
            used_id_set.add(str(prop["id"]))

md(f"&nbsp;&nbsp;🔒 **{len(used_id_set)} IDs** reserved after Stage 1")

rel_parts = [
    p
    for p in merged_parts
    if p["path"].startswith("RelationshipTypes/")
]

stage2_parts = stage1_parts + rel_parts

md(f"**Stage 2** — pushing {len(stage2_parts)} parts: root + EntityTypes + RelationshipTypes")

update_ontology_definition(
    ontology_id,
    {
        "definition": {
            "parts": stage2_parts,
        }
    },
)

md("✅ Stage 2 complete.")


# ══════════════════════════════════════════════════════════════════════════════
# Step 5 — Verify
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔍 Step 5 — Verifying Pushed Definition")

time.sleep(3)

after_def = get_ontology_definition(ontology_id)
after_parts = after_def.get("definition", {}).get("parts", [])

md(parts_summary_md(after_parts, "Definition after update"))

before_counts = count_parts_by_type(live_parts)
after_counts = count_parts_by_type(after_parts)

all_keys = sorted(
    set(before_counts)
    | set(after_counts)
)

rows = "\n".join(
    f"| `{k}` | {before_counts.get(k, 0)} | {after_counts.get(k, 0)} | "
    f"{'✅ same' if before_counts.get(k, 0) == after_counts.get(k, 0) else '🔄 changed'} |"
    for k in all_keys
)

md(f"""
### Before vs After

| Segment | Before | After | Status |
|---|---:|---:|---|
{rows}
""")

expected_count = len(stage2_parts)

if len(after_parts) == expected_count:
    md(f"✅ Part count matches: **{expected_count}** parts pushed and confirmed.")
else:
    md(
        f"⚠️ Part count mismatch — pushed **{expected_count}** but API returned "
        f"**{len(after_parts)}**. Check for validation errors in the Fabric ontology UI."
    )

md(f"""
---
## ✅ Ontology deployment complete

| Property | Value |
|---|---|
| Ontology name | `{ONTOLOGY_NAME}` |
| Ontology ID | `{ontology_id}` |
| Workspace ID | `{workspace_id}` |
| Target folder ID | `{target_folder_id}` |
| Parts pushed | `{expected_count}` |
""")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
