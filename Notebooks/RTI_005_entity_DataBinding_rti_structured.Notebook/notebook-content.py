# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "c42060fb-abbc-4fa1-ac87-ed0a5c460aaf",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI_V6",
# META       "default_lakehouse_workspace_id": "a79a4b7e-e508-4fa4-8b6f-15deadca0f34",
# META       "known_lakehouses": [
# META         {
# META           "id": "c42060fb-abbc-4fa1-ac87-ed0a5c460aaf"
# META         }
# META       ]
# META     },
# META     "environment": {}
# META   }
# META }

# CELL ********************

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — RTI + structured ontology DataBindings / Contextualizations
# Reads shared values from rti_demo_settings
# ══════════════════════════════════════════════════════════════════════════════

from pyspark.sql import functions as F

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
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
    "silver_facilities_table",
    "silver_systems_table",
    "silver_equipment_table",
    "silver_instruments_table",
    "silver_signal_master_table",
]

missing_settings = [
    name
    for name in required_settings
    if name not in settings or settings[name] in (None, "")
]

if missing_settings:
    raise RuntimeError(
        f"Missing required settings in '{settings_table_name}': {missing_settings}"
    )

# --------------------------------------------
# CORE ITEM SETTINGS
# Keep both upper/lower variable names because later 005 cells may use either.
# --------------------------------------------

workspace_id = settings["workspace_id"]
WORKSPACE_ID = workspace_id

workspace_folder_path = settings["workspace_folder_path"]
target_folder_id = settings["target_folder_id"]

lakehouse_name = settings["lakehouse_name"]
lakehouse_id = settings["lakehouse_id"]
LAKEHOUSE_ID = lakehouse_id

ONTOLOGY_NAME = settings["ontology_name"]

# --------------------------------------------
# KEY VAULT / AUTH SETTINGS
# --------------------------------------------

key_vault_uri = settings["key_vault_uri"]
key_vault_tenant_id_secret = settings["key_vault_tenant_id_secret"]
key_vault_client_id_secret = settings["key_vault_client_id_secret"]
key_vault_client_secret_secret = settings["key_vault_client_secret_secret"]

# --------------------------------------------
# LAKEHOUSE DATA BINDING SETTINGS
# --------------------------------------------

SOURCE_SCHEMA = None
TABLE_PREFIX = settings.get("silver_table_prefix", "silver_")

SILVER_FACILITIES_TABLE = settings["silver_facilities_table"]
SILVER_SYSTEMS_TABLE = settings["silver_systems_table"]
SILVER_EQUIPMENT_TABLE = settings["silver_equipment_table"]
SILVER_INSTRUMENTS_TABLE = settings["silver_instruments_table"]
SILVER_SIGNAL_MASTER_TABLE = settings["silver_signal_master_table"]

MANUAL_TABLE_LIST = [
    SILVER_FACILITIES_TABLE,
    SILVER_SYSTEMS_TABLE,
    SILVER_EQUIPMENT_TABLE,
    SILVER_INSTRUMENTS_TABLE,
    SILVER_SIGNAL_MASTER_TABLE,
]

# --------------------------------------------
# FABRIC ONTOLOGY SCHEMAS
# --------------------------------------------

DATABINDING_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/ontology/"
    "dataBinding/1.0.0/schema.json"
)

CONTEXTUALIZATION_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/ontology/"
    "contextualization/1.0.0/schema.json"
)

# --------------------------------------------
# RTI / EVENTHOUSE SETTINGS
# --------------------------------------------

fabric_eventhouse_name = settings["eventhouse_name"]
fabric_kql_db_name = settings["kql_database_name"]
fabric_eventhouse_table = settings["eventhouse_table_name"]

# Optional: only present if 002 persisted it after creating/reusing the Eventhouse.
EVENTHOUSE_ID = settings.get("eventhouse_id", "")

# Keep this variable because later cells may expect it.
cluster_query_uri = settings.get(
    "cluster_query_uri",
    f"https://{fabric_kql_db_name}.kusto.fabric.microsoft.com"
)

print("✅ Loaded 005 configuration from shared settings.")
print("✅ Workspace ID:", WORKSPACE_ID)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Lakehouse:", lakehouse_name)
print("✅ Lakehouse ID:", LAKEHOUSE_ID)
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Eventhouse name:", fabric_eventhouse_name)
print("✅ Eventhouse ID:", EVENTHOUSE_ID if EVENTHOUSE_ID else "<not found in settings>")
print("✅ KQL database:", fabric_kql_db_name)
print("✅ Eventhouse table:", fabric_eventhouse_table)
print("✅ Cluster query URI:", cluster_query_uri)
print("✅ Lakehouse binding tables:", MANUAL_TABLE_LIST)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ══════════════════════════════════════════════════════════════════════════════
# Fabric Ontology API helpers for DataBindings / Contextualizations
# Uses shared 005 config from rti_demo_settings
# ══════════════════════════════════════════════════════════════════════════════

import requests
import time
import json
from typing import Optional
from IPython.display import display, Markdown
from notebookutils import credentials

# --------------------------------------------
# API config
# --------------------------------------------

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
FABRIC_API_VERSION = "v1"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

# --------------------------------------------
# Validate required config
# --------------------------------------------

required_helper_globals = [
    "WORKSPACE_ID",
    "ONTOLOGY_NAME",
    "target_folder_id",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
]

missing_helper_globals = [
    name
    for name in required_helper_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_helper_globals:
    raise RuntimeError(
        "Missing required config values. Run the 005 config cell first. "
        f"Missing: {missing_helper_globals}"
    )

# --------------------------------------------
# Token cache
# --------------------------------------------

_token_cache = {
    "token": None,
    "expires_at": 0.0,
}


def get_spn_access_token() -> str:
    """
    Fetch SPN token from Key Vault and cache it until 60 seconds before expiry.
    Secret names come from rti_demo_settings via the 005 config cell.
    """

    now = time.time()

    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    tenant_id = credentials.getSecret(
        key_vault_uri,
        key_vault_tenant_id_secret,
    )

    client_id = credentials.getSecret(
        key_vault_uri,
        key_vault_client_id_secret,
    )

    client_secret = credentials.getSecret(
        key_vault_uri,
        key_vault_client_secret_secret,
    )

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "https://api.fabric.microsoft.com/.default",
    }

    resp = requests.post(
        token_url,
        data=data,
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


# --------------------------------------------
# Core request helper
# --------------------------------------------

def api_request(
    method: str,
    url: str,
    data=None,
    params=None,
    timeout: int = 60,
) -> requests.Response:
    """
    Retryable Fabric API request.
    Handles 429, 5xx retry, and cached SPN auth.
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
                retry_after = int(resp.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                print(
                    f"Rate limited. Retrying in {retry_after}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(retry_after)
                continue

            if resp.status_code >= 500:
                print(
                    f"Server error {resp.status_code}. Retrying in "
                    f"{RETRY_DELAY_SECONDS}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            return resp

        except requests.exceptions.RequestException as ex:
            print(
                f"API request failed: {ex} "
                f"(attempt {attempt + 1}/{MAX_RETRIES})"
            )

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise

    raise RuntimeError(f"API request failed after {MAX_RETRIES} attempts: {method} {url}")


# --------------------------------------------
# LRO helper
# --------------------------------------------

def wait_for_lro(operation_url: str) -> dict:
    """
    Poll a Fabric long-running operation until Succeeded/Completed/Failed/Cancelled.
    Uses api_request so retry/auth behavior is consistent.
    """

    start_time = time.time()

    while time.time() - start_time < LRO_MAX_WAIT_SECONDS:
        response = api_request(
            "GET",
            operation_url,
            timeout=60,
        )

        if response.status_code >= 400:
            print(f"❌ LRO poll failed: {response.status_code}")
            print(response.text[:3000])
            raise RuntimeError(f"LRO poll failed: {response.status_code}")

        try:
            result = response.json()
        except Exception:
            print("❌ LRO response was not valid JSON.")
            print(response.text[:3000])
            raise

        status = result.get("status", "Unknown")

        if status in ["Succeeded", "Completed"]:
            print("✅ Operation completed successfully.")
            return result

        if status in ["Failed", "Cancelled"]:
            print("Full LRO payload:")
            print(json.dumps(result, indent=2))
            error = result.get("error", {})
            raise RuntimeError(f"Operation {status}: {error}")

        print(f"Operation status: {status}")
        time.sleep(LRO_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Operation timed out after {LRO_MAX_WAIT_SECONDS} seconds: {operation_url}"
    )


# --------------------------------------------
# Ontology helpers
# --------------------------------------------

def list_ontologies() -> list:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{WORKSPACE_ID}/ontologies"
    )

    response = api_request("GET", url)

    if response.status_code == 200:
        return response.json().get("value", [])

    print(f"Failed to list ontologies: {response.status_code}")
    print(response.text[:3000])

    return []


def get_ontology(ontology_id: str) -> Optional[dict]:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{WORKSPACE_ID}/ontologies/{ontology_id}"
    )

    response = api_request("GET", url)

    if response.status_code == 200:
        return response.json()

    print(f"Could not retrieve ontology {ontology_id}: {response.status_code}")
    print(response.text[:3000])

    return None


def find_ontology_by_name(
    display_name: str,
    folder_id: Optional[str] = None,
    enforce_folder_guard: bool = True,
) -> Optional[dict]:
    """
    Find ontology by display name.

    If folder_id is provided, only return a match inside that folder.
    If a same-name ontology exists outside the target folder, raise a clear error.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = target_folder_id

    matches = [
        ont
        for ont in list_ontologies()
        if ont.get("displayName") == display_name
    ]

    if not matches:
        return None

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
            f"Ontology '{display_name}' exists, but not in the target folder.\n"
            f"Existing ontology ID: {first.get('id')}\n"
            f"Existing folder ID: {first.get('folderId')}\n"
            f"Target folder ID: {resolved_folder_id}\n"
            "For a clean test, delete the existing ontology or change the ontology name."
        )

    return None


def create_ontology(
    display_name: str,
    description: str = "",
    folder_id: Optional[str] = None,
) -> dict:
    """
    Create ontology in the target folder.
    Usually 005 should not need this because 004 already deployed the ontology,
    but keeping the helper makes the notebook robust.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = target_folder_id

    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{WORKSPACE_ID}/ontologies"
    )

    data = {
        "displayName": display_name,
        "description": description,
    }

    if resolved_folder_id:
        data["folderId"] = resolved_folder_id
        print(f"Creating ontology in folder: {resolved_folder_id}")

    response = api_request("POST", url, data=data)

    if response.status_code == 201:
        result = response.json()
        display(Markdown(f"**Created Ontology ID:** `{result.get('id')}`"))
        return result

    if response.status_code == 202:
        operation_url = (
            response.headers.get("Location")
            or response.headers.get("Operation-Location")
            or response.headers.get("operation-location")
        )

        if not operation_url:
            raise RuntimeError("Location header missing for async ontology create.")

        wait_for_lro(operation_url)

        created = find_ontology_by_name(
            display_name,
            folder_id=resolved_folder_id,
            enforce_folder_guard=False,
        )

        if created:
            display(Markdown(f"**Created Ontology ID via LRO:** `{created.get('id')}`"))
            return created

        raise RuntimeError("LRO completed but could not find ontology by name.")

    print(f"Failed to create ontology: {response.status_code}")
    print(response.text[:3000])

    raise RuntimeError(f"Create ontology failed: {response.status_code}")


def ensure_ontology(
    display_name: str,
    description: str = "",
    folder_id: Optional[str] = None,
) -> dict:
    """
    Reuse ontology from target folder, or create it there.
    Does not silently reuse same-name ontology from another folder.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = target_folder_id

    existing = find_ontology_by_name(
        display_name,
        folder_id=resolved_folder_id,
        enforce_folder_guard=True,
    )

    if existing:
        display(Markdown(f"**Using Ontology ID:** `{existing.get('id')}`"))
        return existing

    return create_ontology(
        display_name,
        description=description,
        folder_id=resolved_folder_id,
    )


def get_ontology_definition(ontology_id: str) -> dict:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{WORKSPACE_ID}"
        f"/ontologies/{ontology_id}/getDefinition"
    )

    response = api_request("POST", url)

    if response.status_code == 200:
        if not response.text or not response.text.strip():
            return {}

        return response.json()

    if response.status_code == 202:
        operation_url = (
            response.headers.get("Location")
            or response.headers.get("Operation-Location")
            or response.headers.get("operation-location")
        )

        if not operation_url:
            raise RuntimeError("Location header missing for LRO getDefinition.")

        wait_for_lro(operation_url)

        result_url = operation_url.rstrip("/") + "/result"

        result_response = api_request(
            "GET",
            result_url,
            timeout=120,
        )

        if result_response.status_code == 200:
            if not result_response.text or not result_response.text.strip():
                return {}

            return result_response.json()

        print(f"Failed to fetch definition result: {result_response.status_code}")
        print(result_response.text[:3000])

        return {}

    print(f"Failed to get ontology definition: {response.status_code}")
    print(response.text[:3000])

    return {}


def update_ontology_definition(
    ontology_id: str,
    definition_data: dict,
) -> dict:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{WORKSPACE_ID}"
        f"/ontologies/{ontology_id}/updateDefinition"
    )

    response = api_request(
        "POST",
        url,
        data=definition_data,
        timeout=300,
    )

    if response.status_code == 200:
        print("Definition updated successfully")

        if not response.text or not response.text.strip():
            return {}

        try:
            return response.json()
        except Exception:
            print("Fabric returned HTTP 200, but the response body was not valid JSON.")
            print(response.text[:1000])
            return {}

    if response.status_code == 202:
        operation_url = (
            response.headers.get("Location")
            or response.headers.get("Operation-Location")
            or response.headers.get("operation-location")
        )

        if not operation_url:
            raise RuntimeError("Location header missing for updateDefinition LRO.")

        lro_result = wait_for_lro(operation_url)
        print("Definition update async LRO complete")
        return lro_result or {}

    print(f"Failed to update definition: {response.status_code}")
    print(response.text[:3000])

    raise RuntimeError(f"Update definition failed: {response.status_code}")


# --------------------------------------------
# Resolve ontology_id for the rest of 005
# --------------------------------------------

ontology = find_ontology_by_name(
    ONTOLOGY_NAME,
    folder_id=target_folder_id,
    enforce_folder_guard=True,
)

if ontology is None:
    raise RuntimeError(
        f"Ontology with display name '{ONTOLOGY_NAME}' was not found in target folder "
        f"'{target_folder_id}'. Run 004 first."
    )

ontology_id = ontology["id"]

print("✅ Ontology API helpers loaded.")
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Ontology ID:", ontology_id)
print("✅ Workspace ID:", WORKSPACE_ID)
print("✅ Target folder ID:", target_folder_id)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""
ontology_update_bindings_and_contextualizations.py
RTI + structured ontology edition

Adds static Lakehouse DataBindings and relationship Contextualizations to the
live structured RTI ontology.

This cell does NOT create copied RTI measurement entities.
This cell does NOT bind Eventhouse time-series telemetry.
Eventhouse RTI time-series binding belongs in the later time-series binding step.

Expected previous cells:
- 005 config cell
- ontology API helper cell
- 004 ontology generation/deploy completed successfully
"""

import ast
import base64
import json
import time
import uuid
import re
from collections import Counter
from IPython.display import display, Markdown


def md(text):
    display(Markdown(text))


def decode(payload: str) -> dict:
    try:
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        return {}


def encode(obj: dict) -> str:
    return base64.b64encode(
        json.dumps(obj, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


# ══════════════════════════════════════════════════════════════════════════════
# Step 0 — Validate required config/helpers
# ══════════════════════════════════════════════════════════════════════════════

required_globals = [
    "WORKSPACE_ID",
    "LAKEHOUSE_ID",
    "SOURCE_SCHEMA",
    "DATABINDING_SCHEMA",
    "CONTEXTUALIZATION_SCHEMA",
    "ONTOLOGY_NAME",
    "ontology_id",
    "get_ontology_definition",
    "update_ontology_definition",
]

missing_globals = [
    name
    for name in required_globals
    if name not in globals()
]

if missing_globals:
    raise RuntimeError(
        "Missing required variables/helpers. Run the 005 config and API helper cells first. "
        f"Missing: {missing_globals}"
    )


def entity_name_from_table(table_name: str) -> str:
    base = re.sub(r"^silver_", "", table_name)
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)

    if not re.match(r"^[A-Za-z]", base):
        base = "E_" + base

    return base[:26]


# Prefer explicit MANUAL_TABLE_LIST from config/settings.
# Fallback to TABLE_PREFIX only if MANUAL_TABLE_LIST is unavailable.
if "MANUAL_TABLE_LIST" in globals() and MANUAL_TABLE_LIST:
    ENTITY_TO_TABLE = {
        entity_name_from_table(table_name): table_name
        for table_name in MANUAL_TABLE_LIST
    }
else:
    TABLE_PREFIX = globals().get("TABLE_PREFIX", "silver_")
    ENTITY_TO_TABLE = {}

print("✅ Binding/contextualization config validated.")
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Ontology ID:", ontology_id)
print("✅ Workspace ID:", WORKSPACE_ID)
print("✅ Lakehouse ID:", LAKEHOUSE_ID)
print("✅ Explicit entity-to-table map:", ENTITY_TO_TABLE if ENTITY_TO_TABLE else "<using TABLE_PREFIX fallback>")


def table_for_entity(entity_name: str) -> str:
    if entity_name in ENTITY_TO_TABLE:
        return ENTITY_TO_TABLE[entity_name]

    return f"{TABLE_PREFIX}{entity_name}"


def source_table_props(table_name: str) -> dict:
    """
    Build sourceTableProperties for a Fabric Lakehouse table.

    sourceSchema is only included when SOURCE_SCHEMA is set.
    For default Fabric Lakehouse tables, SOURCE_SCHEMA should be None.
    """

    props = {
        "sourceType": "LakehouseTable",
        "workspaceId": WORKSPACE_ID,
        "itemId": LAKEHOUSE_ID,
        "sourceTableName": table_name,
    }

    if SOURCE_SCHEMA is not None:
        props["sourceSchema"] = SOURCE_SCHEMA

    return props


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Load ontology_entity_audit
# ══════════════════════════════════════════════════════════════════════════════

md("## 📋 Step 1 — Loading ontology_entity_audit")

generated_entities = {}

for row in spark.read.table("ontology_entity_audit").collect():
    generated_entities[row["entity"]] = {
        "own_pk": row["own_pk"],
        "entity_id_parts": (
            ast.literal_eval(row["entity_id_parts"])
            if row["entity_id_parts"]
            else []
        ),
    }

md(f"✅ **{len(generated_entities)} entities** loaded from `ontology_entity_audit`")


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Fetch live ontology
# ══════════════════════════════════════════════════════════════════════════════

md("## 📥 Step 2 — Fetching Live Ontology")

live_def = get_ontology_definition(ontology_id)
live_parts = live_def.get("definition", {}).get("parts", [])
existing_paths = {p["path"] for p in live_parts}

md(f"✅ **{len(live_parts)} parts** currently in live ontology")

live_entities = {}

for p in live_parts:
    segs = p["path"].split("/")

    if segs[0] == "EntityTypes" and segs[-1] == "definition.json":
        obj = decode(p["payload"])
        eid = obj.get("id", segs[1])

        # Static Lakehouse DataBindings use normal properties only.
        # timeseriesProperties are intentionally excluded here.
        live_entities[eid] = {
            "name": obj.get("name", ""),
            "entityIdParts": obj.get("entityIdParts", []),
            "properties": {
                pr["name"]: pr["id"]
                for pr in obj.get("properties", [])
            },
            "timeseriesProperties": {
                pr["name"]: pr["id"]
                for pr in obj.get("timeseriesProperties", [])
            },
        }

live_rels = {}

for p in live_parts:
    segs = p["path"].split("/")

    if segs[0] == "RelationshipTypes" and segs[-1] == "definition.json":
        obj = decode(p["payload"])
        rid = obj.get("id", segs[1])

        live_rels[rid] = {
            "name": obj.get("name", ""),
            "src_entity_id": obj.get("source", {}).get("entityTypeId", ""),
            "tgt_entity_id": obj.get("target", {}).get("entityTypeId", ""),
        }

entity_name_to_id = {
    entity_data["name"]: entity_id
    for entity_id, entity_data in live_entities.items()
}

md(f"✅ **{len(live_entities)} entities**, **{len(live_rels)} relationships** found in live ontology")


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Verify Lakehouse tables and columns
# ══════════════════════════════════════════════════════════════════════════════

md("## 🗄️ Step 3 — Verifying Lakehouse Tables & Columns")


def get_table_cols(table_name: str) -> set | None:
    """
    Return lowercase column set or None if table does not exist.
    """

    try:
        return {
            row["col_name"].lower()
            for row in spark.sql(f"DESCRIBE TABLE {table_name}").collect()
            if not row["col_name"].startswith("#")
        }
    except Exception:
        return None


table_columns: dict[str, set | None] = {}

all_entity_names = set(generated_entities) | {
    entity_data["name"]
    for entity_data in live_entities.values()
}

for entity_name in sorted(all_entity_names):
    table_name = table_for_entity(entity_name)
    table_columns[table_name] = get_table_cols(table_name)

    status = (
        f"{len(table_columns[table_name])} cols"
        if table_columns[table_name] is not None
        else "❌ NOT FOUND"
    )

    md(f"&nbsp;&nbsp;`{entity_name}` → `{table_name}` — {status}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Three-way reconciliation
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔀 Step 4 — Three-Way Reconciliation")

confirmed = []
skipped_api = []
skipped_tbl = []
partial_cols = []

for entity_name in sorted(generated_entities):
    eid = entity_name_to_id.get(entity_name)

    if not eid:
        skipped_api.append(entity_name)
        continue

    table_name = table_for_entity(entity_name)

    if table_columns.get(table_name) is None:
        skipped_tbl.append(entity_name)
        continue

    live_props = live_entities[eid]["properties"]
    ts_props = live_entities[eid]["timeseriesProperties"]
    tbl_cols = table_columns[table_name]

    valid_props = {
        col: pid
        for col, pid in live_props.items()
        if col.lower() in tbl_cols
    }

    missing_cols = [
        col
        for col in live_props
        if col.lower() not in tbl_cols
    ]

    # This is expected for signal_master: event_time/value/quality are time-series props,
    # not static Lakehouse columns.
    if ts_props:
        md(
            f"&nbsp;&nbsp;📡 `{entity_name}` has time-series properties "
            f"{list(ts_props.keys())}; these are not included in static Lakehouse DataBindings."
        )

    if missing_cols:
        partial_cols.append({
            "entity": entity_name,
            "missing": missing_cols,
        })

        md(
            f"&nbsp;&nbsp;⚠️ `{entity_name}` — {len(missing_cols)} static columns missing "
            f"from `{table_name}` and excluded from binding: `{missing_cols}`"
        )

    confirmed.append({
        "entity_name": entity_name,
        "entity_id": eid,
        "table_name": table_name,
        "valid_properties": valid_props,
        "entityIdParts": live_entities[eid]["entityIdParts"],
    })

md(f"""
| Result | Count |
|---|---:|
| ✅ Confirmed — audit + live API + Lakehouse | **{len(confirmed)}** |
| ⚠️ Partial static column coverage | **{len(partial_cols)}** |
| ❌ Not in live API | **{len(skipped_api)}** |
| ❌ Table missing in Lakehouse | **{len(skipped_tbl)}** |
""")

if skipped_api:
    md(f"> Not in live API: {', '.join(f'`{e}`' for e in skipped_api)}")

if skipped_tbl:
    md(f"> Tables missing: {', '.join(f'`{table_for_entity(e)}`' for e in skipped_tbl)}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 5 — Build static Lakehouse DataBinding parts
# ══════════════════════════════════════════════════════════════════════════════

md("## 🗄️ Step 5 — Building Static Lakehouse DataBinding Parts")

new_parts = []
db_added = 0
db_skipped = 0
db_failed = 0

for entity in confirmed:
    eid = entity["entity_id"]
    name = entity["entity_name"]
    table_name = entity["table_name"]
    props = entity["valid_properties"]
    db_prefix = f"EntityTypes/{eid}/DataBindings/"

    if any(p.startswith(db_prefix) for p in existing_paths):
        md(f"&nbsp;&nbsp;⏭️ `{name}` — already has DataBinding")
        db_skipped += 1
        continue

    if not props:
        md(f"&nbsp;&nbsp;⚠️ `{name}` — no valid static properties to bind, skipping")
        db_failed += 1
        continue

    binding_id = str(uuid.uuid4())

    binding_obj = {
        "$schema": DATABINDING_SCHEMA,
        "id": binding_id,
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": [
                {
                    "sourceColumnName": col,
                    "targetPropertyId": pid,
                }
                for col, pid in props.items()
            ],
            "sourceTableProperties": source_table_props(table_name),
        },
    }

    new_parts.append({
        "path": f"{db_prefix}{binding_id}.json",
        "payload": encode(binding_obj),
        "payloadType": "InlineBase64",
    })

    db_added += 1

    md(f"&nbsp;&nbsp;✅ `{name}` → `{table_name}` ({len(props)} static property bindings)")


md(f"\n**DataBindings:** {db_added} new, {db_skipped} already existed, {db_failed} skipped")


# ══════════════════════════════════════════════════════════════════════════════
# Step 6 — Build relationship Contextualization parts
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔀 Step 6 — Building Relationship Contextualization Parts")

rel_join_keys = {}

try:
    for row in spark.read.table("ontology_relationship_audit").collect():
        keys = (
            ast.literal_eval(row["effective_join_keys"])
            if row["effective_join_keys"]
            else []
        )

        rel_join_keys[row["relationship"]] = keys

    md(f"✅ **{len(rel_join_keys)} join key records** loaded from `ontology_relationship_audit`")

except Exception as e:
    md(f"⚠️ Could not load `ontology_relationship_audit`: `{e}`")


ctx_added = 0
ctx_skipped = 0
ctx_failed = 0

for rid, rel in live_rels.items():
    ctx_prefix = f"RelationshipTypes/{rid}/Contextualizations/"
    rel_name = rel["name"]

    if any(p.startswith(ctx_prefix) for p in existing_paths):
        md(f"&nbsp;&nbsp;⏭️ `{rel_name}` — already has Contextualization")
        ctx_skipped += 1
        continue

    src_eid = rel["src_entity_id"]
    tgt_eid = rel["tgt_entity_id"]

    src_data = live_entities.get(src_eid)
    tgt_data = live_entities.get(tgt_eid)

    if not src_data or not tgt_data:
        md(f"&nbsp;&nbsp;⚠️ `{rel_name}` — source or target entity not found in live ontology")
        ctx_failed += 1
        continue

    src_name = src_data["name"]
    tgt_name = tgt_data["name"]

    src_table = table_for_entity(src_name)
    tgt_table = table_for_entity(tgt_name)

    rel_label = f"{src_name} → {tgt_name}"

    for table_name in [src_table, tgt_table]:
        if table_name not in table_columns:
            table_columns[table_name] = get_table_cols(table_name)

    if table_columns.get(src_table) is None or table_columns.get(tgt_table) is None:
        md(f"&nbsp;&nbsp;❌ `{rel_name}` — table missing for source or target")
        ctx_failed += 1
        continue

    src_tbl_cols = table_columns[src_table]
    tgt_tbl_cols = table_columns[tgt_table]

    src_props = src_data["properties"]
    tgt_props = tgt_data["properties"]

    src_id_parts = src_data["entityIdParts"]
    tgt_id_parts = tgt_data["entityIdParts"]

    source_key_ref_bindings = [
        {
            "sourceColumnName": col,
            "targetPropertyId": pid,
        }
        for col, pid in src_props.items()
        if pid in src_id_parts
        and col.lower() in src_tbl_cols
    ]

    effective_keys = rel_join_keys.get(rel_label, [])

    if not effective_keys:
        effective_keys = [
            col
            for col, pid in tgt_props.items()
            if pid in tgt_id_parts
            and col.lower() in src_tbl_cols
            and col.lower() in tgt_tbl_cols
        ]

    target_key_ref_bindings = [
        {
            "sourceColumnName": col,
            "targetPropertyId": tgt_props[col],
        }
        for col in effective_keys
        if col in tgt_props
        and col.lower() in src_tbl_cols
        and col.lower() in tgt_tbl_cols
    ]

    tgt_id_part_cols = [
        col
        for col, pid in tgt_props.items()
        if pid in tgt_id_parts
    ]

    if not source_key_ref_bindings:
        md(
            f"&nbsp;&nbsp;⚠️ `{rel_name}` (`{rel_label}`) — no source entity key "
            f"columns found in `{src_table}`, skipping"
        )
        ctx_failed += 1
        continue

    if len(target_key_ref_bindings) != len(tgt_id_parts):
        missing_from_src = [
            col
            for col in tgt_id_part_cols
            if col.lower() not in src_tbl_cols
        ]

        md(
            f"&nbsp;&nbsp;⚠️ `{rel_name}` (`{rel_label}`) — skipping: "
            f"target needs {len(tgt_id_parts)} key columns but source has "
            f"{len(target_key_ref_bindings)}. Missing in source: `{missing_from_src}`"
        )

        ctx_failed += 1
        continue

    ctx_id = str(uuid.uuid4())

    ctx_obj = {
        "$schema": CONTEXTUALIZATION_SCHEMA,
        "id": ctx_id,
        "dataBindingTable": source_table_props(src_table),
        "sourceKeyRefBindings": source_key_ref_bindings,
        "targetKeyRefBindings": target_key_ref_bindings,
    }

    new_parts.append({
        "path": f"{ctx_prefix}{ctx_id}.json",
        "payload": encode(ctx_obj),
        "payloadType": "InlineBase64",
    })

    ctx_added += 1

    md(
        f"&nbsp;&nbsp;✅ `{rel_name}` (`{rel_label}`)  \n"
        f"&nbsp;&nbsp;&nbsp;&nbsp;source keys: "
        f"`{[b['sourceColumnName'] for b in source_key_ref_bindings]}`  \n"
        f"&nbsp;&nbsp;&nbsp;&nbsp;target keys: "
        f"`{[b['sourceColumnName'] for b in target_key_ref_bindings]}`"
    )


md(f"\n**Contextualizations:** {ctx_added} new, {ctx_skipped} already existed, {ctx_failed} skipped/failed")


# ══════════════════════════════════════════════════════════════════════════════
# Step 7 — Push updated definition
# ══════════════════════════════════════════════════════════════════════════════

md("## 🚀 Step 7 — Pushing Updated Definition")

if not new_parts:
    md("✅ Nothing to push — all static DataBindings and Contextualizations already exist.")

else:
    all_parts = live_parts + new_parts

    paths = [
        p.get("path", "")
        for p in all_parts
    ]

    duplicate_paths = sorted({
        path
        for path in paths
        if paths.count(path) > 1
    })

    if duplicate_paths:
        raise RuntimeError(
            f"Duplicate ontology part paths detected before updateDefinition: {duplicate_paths}"
        )

    md(
        f"Pushing **{len(all_parts)} total parts** "
        f"({len(live_parts)} existing + {len(new_parts)} new)"
    )

    update_ontology_definition(
        ontology_id,
        {
            "definition": {
                "parts": all_parts,
            }
        },
    )

    md(f"✅ Pushed — {db_added} DataBindings + {ctx_added} Contextualizations added")


# ══════════════════════════════════════════════════════════════════════════════
# Step 8 — Verify
# ══════════════════════════════════════════════════════════════════════════════

md("## 🔍 Step 8 — Verifying")

time.sleep(3)

after_parts = get_ontology_definition(ontology_id).get("definition", {}).get("parts", [])


def classify_part(path: str) -> str:
    if path == ".platform":
        return ".platform"

    if path == "definition.json":
        return "definition.json"

    if "/DataBindings/" in path:
        return "DataBindings"

    if "/Contextualizations/" in path:
        return "Contextualizations"

    if path.startswith("EntityTypes/") and path.endswith("/definition.json"):
        return "EntityType definitions"

    if path.startswith("RelationshipTypes/") and path.endswith("/definition.json"):
        return "RelationshipType definitions"

    return "Other"


counts = Counter(
    classify_part(p["path"])
    for p in after_parts
)

ordered_labels = [
    ".platform",
    "definition.json",
    "EntityType definitions",
    "RelationshipType definitions",
    "DataBindings",
    "Contextualizations",
    "Other",
]

rows = []

for label in ordered_labels:
    if counts.get(label, 0) > 0:
        rows.append(f"| `{label}` | {counts[label]} |")

md("| Part type | Count |\n|---|---:|\n" + "\n".join(rows))

entity_def_count = counts.get("EntityType definitions", 0)
rel_def_count = counts.get("RelationshipType definitions", 0)
db_count = counts.get("DataBindings", 0)
ctx_count = counts.get("Contextualizations", 0)

md(f"""
**Entity definitions:** {entity_def_count}  
**Relationship definitions:** {rel_def_count}  
**DataBindings:** {db_count}  
**Contextualizations:** {ctx_count}
""")

md("""
✅ Static Lakehouse DataBindings and relationship Contextualizations completed.

RTI telemetry remains in Eventhouse and is not copied into Lakehouse.
Time-series binding for `signal_master.event_time`, `signal_master.value`, and
`signal_master.quality` should be handled in the Eventhouse time-series binding step.
""")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
