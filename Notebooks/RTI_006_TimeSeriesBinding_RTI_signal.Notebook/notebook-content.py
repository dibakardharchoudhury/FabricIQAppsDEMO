# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "de191d24-5e98-458f-a018-f4b838ecbc17",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI_V5",
# META       "default_lakehouse_workspace_id": "19f3d588-1585-4f3b-bb59-5abaf90c193a",
# META       "known_lakehouses": [
# META         {
# META           "id": "de191d24-5e98-458f-a018-f4b838ecbc17"
# META         }
# META       ]
# META     },
# META     "environment": {}
# META   }
# META }

# MARKDOWN ********************

# # 07 — Bind Eventhouse RTI Stream to `signal_master`
# 
# This notebook adds the direct Eventhouse TimeSeries DataBinding to the Fabric Ontology.
# 
# Clean model:
# 
# - Structured/static data stays in Lakehouse and is already bound by notebook 04.
# - RTI data stays in Eventhouse.
# - `signal_master` is the semantic bridge.
# - `opcua_node_id` links Eventhouse telemetry to the structured signal metadata.
# - No copied RTI Lakehouse table.
# - No `rti_measurements` ontology entity.


# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 1 — Config
#  Eventhouse time-series binding for signal_master
#  Reads shared values from rti_demo_settings
# ╚══════════════════════════════════════════════════════════════════════════╝

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


def first_setting(*names, required: bool = False) -> str:
    """
    Return the first non-empty setting value from the supplied setting names.
    This keeps the notebook compatible with older and newer setting keys.
    """
    for name in names:
        value = settings.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()

    if required:
        raise RuntimeError(
            f"Missing required setting. Tried these setting names: {list(names)}"
        )

    return ""

# --------------------------------------------
# WORKSPACE / FOLDER / ONTOLOGY
# Keep uppercase names because later 007 cells expect them.
# --------------------------------------------

WORKSPACE_ID = first_setting("workspace_id", required=True)
workspace_id = WORKSPACE_ID

workspace_folder_path = first_setting("workspace_folder_path", required=True)
target_folder_id = first_setting("target_folder_id", required=True)

ONTOLOGY_NAME = first_setting("ontology_name", "fabric_ontology_name", required=True)

# --------------------------------------------
# KEY VAULT / AUTH
# --------------------------------------------

key_vault_uri = first_setting("key_vault_uri", required=True)
key_vault_tenant_id_secret = first_setting("key_vault_tenant_id_secret", required=True)
key_vault_client_id_secret = first_setting("key_vault_client_id_secret", required=True)
key_vault_client_secret_secret = first_setting("key_vault_client_secret_secret", required=True)

# --------------------------------------------
# TARGET ONTOLOGY ENTITY
# The Eventhouse time-series binding attaches to signal_master.
# --------------------------------------------

STATIC_ENTITY_NAME = settings.get("signal_master_entity_name", "signal_master")

# --------------------------------------------
# EVENTHOUSE / KQL SOURCE
# --------------------------------------------
# Do not construct the Kusto URI from an Eventhouse or KQL DB display name.
# Eventhouse/Kusto endpoint values are either:
#   1. persisted by the Eventhouse setup step, or
#   2. resolved from Fabric Eventhouse properties in Cell 3.

EVENTHOUSE_NAME = first_setting("fabric_eventhouse_name", "eventhouse_name", required=True)
KQL_DB_NAME = first_setting("fabric_kql_db_name", "kql_database_name", "kql_db_name", required=True)
KQL_TABLE_NAME = first_setting("fabric_eventhouse_table", "eventhouse_table_name", "kql_table_name", required=True)

EVENTHOUSE_ID = first_setting("fabric_eventhouse_id", "eventhouse_id")
KQL_DATABASE_ID = first_setting("fabric_kql_db_id", "kql_database_id", "kql_db_id")
KQL_DB_ID = KQL_DATABASE_ID

cluster_query_uri = first_setting("cluster_query_uri")
cluster_ingest_uri = first_setting("cluster_ingest_uri")

CLUSTER_QUERY_URI = cluster_query_uri
CLUSTER_INGEST_URI = cluster_ingest_uri

# --------------------------------------------
# EVENTHOUSE COLUMNS
# Must match slim OPCUAEvents table:
#   event_time, opcua_node_id, value, quality
# --------------------------------------------

TIMESTAMP_COLUMN_NAME = settings.get("timeseries_timestamp_column", "event_time")
KEY_COLUMN_NAME = settings.get("timeseries_key_column", "opcua_node_id")
VALUE_COLUMN_NAME = settings.get("timeseries_value_column", "value")
QUALITY_COLUMN_NAME = settings.get("timeseries_quality_column", "quality")

# --------------------------------------------
# REPLACE OLD/BAD RTI BINDINGS
# Keep this True while cleaning up stale opcua_stream / wide-schema attempts.
# --------------------------------------------

REPLACE_EXISTING_TIMESERIES_BINDING = True

print("✅ Loaded 007 configuration from shared settings.")
print("✅ Workspace ID:", WORKSPACE_ID)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Static ontology entity:", STATIC_ENTITY_NAME)
print("✅ Eventhouse name:", EVENTHOUSE_NAME)
print("✅ Eventhouse ID:", EVENTHOUSE_ID if EVENTHOUSE_ID else "<will resolve in Cell 3>")
print("✅ KQL database:", KQL_DB_NAME)
print("✅ KQL database ID:", KQL_DATABASE_ID if KQL_DATABASE_ID else "<will resolve in Cell 3>")
print("✅ KQL table:", KQL_TABLE_NAME)
print("✅ Cluster query URI:", CLUSTER_QUERY_URI if CLUSTER_QUERY_URI else "<will resolve from Eventhouse properties in Cell 3>")
print("✅ Cluster ingest URI:", CLUSTER_INGEST_URI if CLUSTER_INGEST_URI else "<will resolve from Eventhouse properties in Cell 3>")
print("✅ Timestamp column:", TIMESTAMP_COLUMN_NAME)
print("✅ Key column:", KEY_COLUMN_NAME)
print("✅ Value column:", VALUE_COLUMN_NAME)
print("✅ Quality column:", QUALITY_COLUMN_NAME)
print("✅ Replace existing time-series binding:", REPLACE_EXISTING_TIMESERIES_BINDING)


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 2 — Imports and Fabric API helpers
#  Uses shared 007 config from rti_demo_settings
# ╚══════════════════════════════════════════════════════════════════════════╝

import requests
import time
import json
import base64
import uuid
from typing import Optional
from urllib.parse import urlparse
from IPython.display import display, Markdown
from notebookutils import credentials


def md(text):
    display(Markdown(text))


def encode(obj: dict) -> str:
    return base64.b64encode(
        json.dumps(obj, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8")


def decode(payload: str) -> dict:
    try:
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# API config
# ══════════════════════════════════════════════════════════════════════════════

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
FABRIC_API_VERSION = "v1"
FABRIC_BASE_URL = f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300


# ══════════════════════════════════════════════════════════════════════════════
# Validate config from Cell 1
# ══════════════════════════════════════════════════════════════════════════════

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
        "Missing required 007 helper config values. Run the 007 config cell first. "
        f"Missing: {missing_helper_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Token cache
# ══════════════════════════════════════════════════════════════════════════════

_token_cache = {
    # scope -> {"token": str, "expires_at": float}
}


def get_spn_access_token(scope: str = "https://api.fabric.microsoft.com/.default") -> str:
    """
    Get SPN access token for Fabric/Kusto scopes.

    Uses Key Vault secret names from rti_demo_settings via the 007 config cell.
    """

    now = time.time()

    cached = _token_cache.get(scope)
    if cached and cached["token"] and now < cached["expires_at"]:
        return cached["token"]

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

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError("Unable to fetch SPN credentials from Key Vault.")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": scope,
    }

    response = requests.post(
        token_url,
        data=data,
        timeout=30,
    )

    response.raise_for_status()

    token_data = response.json()

    _token_cache[scope] = {
        "token": token_data["access_token"],
        "expires_at": now + token_data.get("expires_in", 3600) - 60,
    }

    return _token_cache[scope]["token"]


def get_headers(scope: str = "https://api.fabric.microsoft.com/.default") -> dict:
    return {
        "Authorization": f"Bearer {get_spn_access_token(scope)}",
        "Content-Type": "application/json",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Generic Fabric API helpers
# ══════════════════════════════════════════════════════════════════════════════

def api_request(
    method: str,
    url: str,
    data=None,
    params=None,
    timeout: int = 60,
    scope: str = "https://api.fabric.microsoft.com/.default",
) -> requests.Response:
    """
    Retryable Fabric API request.
    """

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=get_headers(scope),
                json=data,
                params=params,
                timeout=timeout,
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                print(
                    f"Rate limited. Retrying in {retry_after}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(retry_after)
                continue

            if response.status_code >= 500:
                print(
                    f"Server error {response.status_code}. Retrying in "
                    f"{RETRY_DELAY_SECONDS}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            return response

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


def wait_for_lro(operation_url: str) -> dict:
    """
    Poll Fabric long-running operation URL until completion.
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
            raise RuntimeError(f"LRO {status}: {result.get('error', {})}")

        print(f"Operation status: {status}")
        time.sleep(LRO_POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"Operation timed out after {LRO_MAX_WAIT_SECONDS} seconds")


# ══════════════════════════════════════════════════════════════════════════════
# Workspace / ontology helpers
# ══════════════════════════════════════════════════════════════════════════════

def list_workspace_items(item_type: str | None = None) -> list:
    url = f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}/items"

    response = api_request("GET", url)
    response.raise_for_status()

    items = response.json().get("value", [])

    if item_type:
        return [
            item
            for item in items
            if item.get("type", "").lower() == item_type.lower()
        ]

    return items


def list_ontologies() -> list:
    url = f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}/ontologies"

    response = api_request("GET", url)
    response.raise_for_status()

    return response.json().get("value", [])


def find_ontology_by_name(
    display_name: str,
    folder_id: Optional[str] = None,
    enforce_folder_guard: bool = True,
) -> Optional[dict]:
    """
    Find ontology by display name.

    With folder_id provided, only returns the ontology inside that folder.
    Raises if the same name exists elsewhere and no target-folder match exists.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = target_folder_id

    matches = [
        ontology
        for ontology in list_ontologies()
        if ontology.get("displayName") == display_name
    ]

    if not matches:
        return None

    matches_in_folder = [
        ontology
        for ontology in matches
        if ontology.get("folderId") == resolved_folder_id
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
            "Run 004 against the intended folder, delete the wrong ontology, or change the ontology name."
        )

    return None


def get_ontology_definition(ontology_id: str) -> dict:
    """
    Get Fabric ontology definition.

    Handles:
    - 200 direct response
    - 202 LRO + /result response
    """

    url = (
        f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}"
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
            raise RuntimeError("Missing Location header for getDefinition LRO.")

        wait_for_lro(operation_url)

        result_url = operation_url.rstrip("/") + "/result"

        result_response = api_request(
            "GET",
            result_url,
            timeout=120,
        )

        result_response.raise_for_status()

        if not result_response.text or not result_response.text.strip():
            return {}

        return result_response.json()

    print(response.text[:3000])
    raise RuntimeError(f"Failed to get ontology definition: {response.status_code}")


def update_ontology_definition(
    ontology_id: str,
    definition_data: dict,
) -> dict:
    """
    Update Fabric ontology definition.

    Handles:
    - 200 with JSON body
    - 200 with empty body
    - 202 LRO
    """

    url = (
        f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}"
        f"/ontologies/{ontology_id}/updateDefinition"
    )

    response = api_request(
        "POST",
        url,
        data=definition_data,
        timeout=300,
    )

    if response.status_code == 200:
        print("Definition updated successfully.")

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
            raise RuntimeError("Missing Location header for updateDefinition LRO.")

        result = wait_for_lro(operation_url)
        print("Definition update async LRO complete.")
        return result or {}

    print(response.text[:3000])
    raise RuntimeError(f"Failed to update ontology definition: {response.status_code}")


# ══════════════════════════════════════════════════════════════════════════════
# Kusto token helper for Eventhouse validation if needed later
# ══════════════════════════════════════════════════════════════════════════════

def get_kusto_token(cluster_url: str) -> str:
    """
    Get token for a Kusto/Eventhouse cluster.
    """

    if not cluster_url:
        raise RuntimeError("cluster_url is empty.")

    parsed = urlparse(cluster_url)
    resource = f"{parsed.scheme}://{parsed.netloc}"
    scope = f"{resource}/.default"

    return get_spn_access_token(scope=scope)


# ══════════════════════════════════════════════════════════════════════════════
# Resolve ontology_id for downstream 007 cells
# ══════════════════════════════════════════════════════════════════════════════

ontology = find_ontology_by_name(
    ONTOLOGY_NAME,
    folder_id=target_folder_id,
    enforce_folder_guard=True,
)

if ontology is None:
    raise RuntimeError(
        f"Ontology '{ONTOLOGY_NAME}' was not found in target folder '{target_folder_id}'. "
        "Run 004 first."
    )

ontology_id = ontology["id"]

print("✅ 007 API helpers loaded.")
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

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 3 — Resolve ontology, Eventhouse, KQL database, and cluster URI
# ╚══════════════════════════════════════════════════════════════════════════╝

md("## 🔎 Resolving ontology and Eventhouse/KQL source")


# ══════════════════════════════════════════════════════════════════════════════
# Helper: find Fabric item by ID or by name, guarded by target folder
# ══════════════════════════════════════════════════════════════════════════════

def resolve_workspace_item(
    item_type: str,
    display_name: str,
    configured_id: str = "",
    target_folder_id: str | None = None,
) -> dict:
    """
    Resolve a Fabric workspace item by configured ID first, then by display name.

    If resolving by name, do not silently use same-name items outside target_folder_id.
    """

    items = list_workspace_items(item_type)

    if configured_id:
        match_by_id = next(
            (
                item
                for item in items
                if item.get("id") == configured_id
            ),
            None,
        )

        if not match_by_id:
            raise RuntimeError(
                f"{item_type} configured ID was not found in workspace.\n"
                f"Configured ID: {configured_id}\n"
                f"Expected display name: {display_name}"
            )

        item_folder_id = match_by_id.get("folderId")

        if target_folder_id and item_folder_id != target_folder_id:
            raise RuntimeError(
                f"{item_type} '{display_name}' was found by configured ID, "
                "but it is not in the target folder.\n"
                f"Item ID: {configured_id}\n"
                f"Existing folder ID: {item_folder_id}\n"
                f"Target folder ID: {target_folder_id}"
            )

        return match_by_id

    matches = [
        item
        for item in items
        if item.get("displayName") == display_name
    ]

    if not matches:
        raise RuntimeError(
            f"{item_type} '{display_name}' not found in workspace."
        )

    if target_folder_id:
        matches_in_folder = [
            item
            for item in matches
            if item.get("folderId") == target_folder_id
        ]

        if matches_in_folder:
            return matches_in_folder[0]

        first = matches[0]

        raise RuntimeError(
            f"{item_type} '{display_name}' exists, but not in the target folder.\n"
            f"Existing item ID: {first.get('id')}\n"
            f"Existing folder ID: {first.get('folderId')}\n"
            f"Target folder ID: {target_folder_id}\n"
            "Resolve the duplicate/wrong-root item before continuing."
        )

    return matches[0]


# ══════════════════════════════════════════════════════════════════════════════
# Resolve ontology
# ══════════════════════════════════════════════════════════════════════════════

ontology = find_ontology_by_name(
    ONTOLOGY_NAME,
    folder_id=target_folder_id,
    enforce_folder_guard=True,
)

if ontology is None:
    raise RuntimeError(
        f"Ontology '{ONTOLOGY_NAME}' not found in target folder '{target_folder_id}'. "
        "Run 004 first."
    )

ontology_id = ontology["id"]

md(f"✅ Ontology `{ONTOLOGY_NAME}` found in target folder: `{ontology_id}`")


# ══════════════════════════════════════════════════════════════════════════════
# Resolve KQL database
# ══════════════════════════════════════════════════════════════════════════════

configured_kql_database_id = ""

if "KQL_DATABASE_ID" in globals() and KQL_DATABASE_ID:
    configured_kql_database_id = KQL_DATABASE_ID
elif "KQL_DB_ID" in globals() and KQL_DB_ID:
    configured_kql_database_id = KQL_DB_ID

kql_db_item = resolve_workspace_item(
    item_type="KqlDatabase",
    display_name=KQL_DB_NAME,
    configured_id=configured_kql_database_id,
    target_folder_id=target_folder_id,
)

KQL_DB_ID = kql_db_item["id"]
KQL_DATABASE_ID = KQL_DB_ID

md(f"✅ KQL DB `{KQL_DB_NAME}` found in target folder: `{KQL_DB_ID}`")


# ══════════════════════════════════════════════════════════════════════════════
# Resolve Eventhouse
# ══════════════════════════════════════════════════════════════════════════════

parent_eventhouse_id = (
    kql_db_item.get("properties", {}) or {}
).get("parentEventhouseItemId")

configured_eventhouse_id = ""

if "EVENTHOUSE_ID" in globals() and EVENTHOUSE_ID:
    configured_eventhouse_id = EVENTHOUSE_ID

if parent_eventhouse_id:
    EVENTHOUSE_ID = parent_eventhouse_id

    eventhouses = list_workspace_items("Eventhouse")

    eventhouse_item = next(
        (
            item
            for item in eventhouses
            if item.get("id") == EVENTHOUSE_ID
        ),
        None,
    )

    if eventhouse_item is None:
        raise RuntimeError(
            f"KQL DB points to Eventhouse ID '{EVENTHOUSE_ID}', "
            "but that Eventhouse item was not found in the workspace."
        )

    if eventhouse_item.get("folderId") != target_folder_id:
        raise RuntimeError(
            f"Parent Eventhouse for KQL DB '{KQL_DB_NAME}' is not in the target folder.\n"
            f"Eventhouse ID: {EVENTHOUSE_ID}\n"
            f"Existing folder ID: {eventhouse_item.get('folderId')}\n"
            f"Target folder ID: {target_folder_id}"
        )

else:
    eventhouse_item = resolve_workspace_item(
        item_type="Eventhouse",
        display_name=EVENTHOUSE_NAME,
        configured_id=configured_eventhouse_id,
        target_folder_id=target_folder_id,
    )

    EVENTHOUSE_ID = eventhouse_item["id"]

md(f"✅ Eventhouse `{EVENTHOUSE_NAME}` found in target folder: `{EVENTHOUSE_ID}`")


# ══════════════════════════════════════════════════════════════════════════════
# Resolve Eventhouse Kusto URIs
# ══════════════════════════════════════════════════════════════════════════════
# The Eventhouse display name is not a Kusto hostname.
# Always prefer the real runtime URI returned by Fabric Eventhouse properties.
# Persisted settings are allowed as a fallback, but the notebook must never
# manufacture a URI from the Eventhouse or KQL DB display name.

eventhouse_url = (
    f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}"
    f"/eventhouses/{EVENTHOUSE_ID}"
)

eventhouse_resp = api_request("GET", eventhouse_url)
eventhouse_resp.raise_for_status()

eventhouse_details = eventhouse_resp.json()
eventhouse_props = eventhouse_details.get("properties", {}) or {}

resolved_cluster_query_uri = eventhouse_props.get("queryServiceUri")
resolved_cluster_ingest_uri = eventhouse_props.get("ingestionServiceUri")

configured_cluster_query_uri = ""
configured_cluster_ingest_uri = ""

if "cluster_query_uri" in globals() and cluster_query_uri:
    configured_cluster_query_uri = str(cluster_query_uri).strip()
elif "CLUSTER_QUERY_URI" in globals() and CLUSTER_QUERY_URI:
    configured_cluster_query_uri = str(CLUSTER_QUERY_URI).strip()

if "cluster_ingest_uri" in globals() and cluster_ingest_uri:
    configured_cluster_ingest_uri = str(cluster_ingest_uri).strip()
elif "CLUSTER_INGEST_URI" in globals() and CLUSTER_INGEST_URI:
    configured_cluster_ingest_uri = str(CLUSTER_INGEST_URI).strip()

CLUSTER_QUERY_URI = resolved_cluster_query_uri or configured_cluster_query_uri
CLUSTER_INGEST_URI = resolved_cluster_ingest_uri or configured_cluster_ingest_uri

if not CLUSTER_QUERY_URI:
    raise RuntimeError(
        "Could not resolve Eventhouse queryServiceUri. "
        "The Eventhouse item was found, but Fabric did not return a queryServiceUri "
        "and no cluster_query_uri setting was available. Wait for Eventhouse provisioning "
        "to finish and rerun this cell."
    )

if not CLUSTER_INGEST_URI:
    print(
        "⚠️ Eventhouse ingestionServiceUri was not returned and no cluster_ingest_uri "
        "setting was available. Continuing because this notebook only needs the query URI "
        "for schema verification and binding metadata."
    )

# Keep lowercase aliases for compatibility with any later cells.
cluster_query_uri = CLUSTER_QUERY_URI
cluster_ingest_uri = CLUSTER_INGEST_URI

# ══════════════════════════════════════════════════════════════════════════════
# Final confirmation
# ══════════════════════════════════════════════════════════════════════════════

md(f"✅ Eventhouse query URI: `{CLUSTER_QUERY_URI}`")
md(f"✅ RTI table target: `{KQL_TABLE_NAME}`")
md(f"✅ Timestamp column: `{TIMESTAMP_COLUMN_NAME}`")
md(f"✅ Key column: `{KEY_COLUMN_NAME}`")
md(f"✅ Value column: `{VALUE_COLUMN_NAME}`")
md(f"✅ Quality column: `{QUALITY_COLUMN_NAME}`")

print("✅ 007 source resolution complete.")
print("✅ Ontology ID:", ontology_id)
print("✅ Eventhouse ID:", EVENTHOUSE_ID)
print("✅ KQL DB ID:", KQL_DB_ID)
print("✅ Cluster query URI:", CLUSTER_QUERY_URI)
print("✅ KQL table:", KQL_TABLE_NAME)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 4 — Verify Eventhouse slim RTI table schema
# ╚══════════════════════════════════════════════════════════════════════════╝

md("## 📡 Verifying Eventhouse slim RTI table schema")


# ══════════════════════════════════════════════════════════════════════════════
# Validate required globals
# ══════════════════════════════════════════════════════════════════════════════

required_schema_globals = [
    "CLUSTER_QUERY_URI",
    "KQL_DB_NAME",
    "KQL_TABLE_NAME",
    "TIMESTAMP_COLUMN_NAME",
    "KEY_COLUMN_NAME",
    "VALUE_COLUMN_NAME",
    "QUALITY_COLUMN_NAME",
    "get_spn_access_token",
]

missing_schema_globals = [
    name
    for name in required_schema_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_schema_globals:
    raise RuntimeError(
        "Missing required values. Run 007 Cells 1-3 first. "
        f"Missing: {missing_schema_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Kusto helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_kusto_token_for_cluster(cluster_url: str) -> str:
    if not cluster_url:
        raise RuntimeError("CLUSTER_QUERY_URI is empty.")

    parsed = urlparse(cluster_url)

    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(
            "CLUSTER_QUERY_URI must be a full Eventhouse/Kusto URL returned by Fabric. "
            f"Current value: {cluster_url}"
        )

    resource = f"{parsed.scheme}://{parsed.netloc}"
    return get_spn_access_token(scope=f"{resource}/.default")


def run_kusto_mgmt(
    cluster_query_uri: str,
    database_name: str,
    csl: str,
) -> dict:
    token = get_kusto_token_for_cluster(cluster_query_uri)

    mgmt_url = f"{cluster_query_uri.rstrip('/')}/v1/rest/mgmt"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body = {
        "db": database_name,
        "csl": csl,
    }

    response = requests.post(
        mgmt_url,
        headers=headers,
        json=body,
        timeout=120,
    )

    if response.status_code not in (200, 201):
        print("❌ Kusto management command failed.")
        print("Command:")
        print(csl)
        print("Response:")
        print(response.text[:3000])
        response.raise_for_status()

    return response.json() if response.text and response.text.strip() else {}


def kusto_result_to_records(result: dict) -> list[dict]:
    tables = result.get("Tables", []) if isinstance(result, dict) else []

    if not tables:
        return []

    primary = next(
        (
            table
            for table in tables
            if table.get("TableKind") == "PrimaryResult"
        ),
        tables[0],
    )

    columns = [
        column.get("ColumnName") or column.get("name")
        for column in primary.get("Columns", [])
    ]

    rows = primary.get("Rows", [])

    return [
        dict(zip(columns, row))
        for row in rows
    ]


def list_kql_tables(
    cluster_query_uri: str,
    database_name: str,
) -> list[str]:
    result = run_kusto_mgmt(
        cluster_query_uri,
        database_name,
        ".show tables",
    )

    records = kusto_result_to_records(result)

    return sorted([
        record.get("TableName")
        for record in records
        if record.get("TableName")
    ])


def get_kql_table_columns(
    cluster_query_uri: str,
    database_name: str,
    table_name: str,
) -> list[tuple[str, str]]:
    result = run_kusto_mgmt(
        cluster_query_uri,
        database_name,
        f".show table {table_name} schema as json",
    )

    records = kusto_result_to_records(result)

    if not records:
        available_tables = list_kql_tables(
            cluster_query_uri,
            database_name,
        )

        raise RuntimeError(
            f"KQL table '{table_name}' not found in database '{database_name}'. "
            f"Available tables: {available_tables}"
        )

    schema_json = None

    for value in records[0].values():
        if isinstance(value, str) and value.strip().startswith("{"):
            schema_json = value
            break

    if not schema_json:
        raise RuntimeError(
            f"Could not locate schema JSON for KQL table '{table_name}'. "
            f"Returned record: {records[0]}"
        )

    schema_obj = json.loads(schema_json)

    ordered_columns = schema_obj.get("OrderedColumns", [])

    return [
        (
            column.get("Name"),
            column.get("CslType") or column.get("Type"),
        )
        for column in ordered_columns
        if column.get("Name")
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Read and validate schema
# ══════════════════════════════════════════════════════════════════════════════

kql_cols = get_kql_table_columns(
    CLUSTER_QUERY_URI,
    KQL_DB_NAME,
    KQL_TABLE_NAME,
)

kql_col_names = {
    name
    for name, _ in kql_cols
}

expected_kql_cols = {
    TIMESTAMP_COLUMN_NAME,
    KEY_COLUMN_NAME,
    VALUE_COLUMN_NAME,
    QUALITY_COLUMN_NAME,
}

missing_kql_cols = sorted(expected_kql_cols - kql_col_names)
extra_kql_cols = sorted(kql_col_names - expected_kql_cols)

if missing_kql_cols:
    raise RuntimeError(
        f"KQL table '{KQL_TABLE_NAME}' is missing required slim RTI columns: "
        f"{missing_kql_cols}. Available columns: {sorted(kql_col_names)}"
    )

if extra_kql_cols:
    raise RuntimeError(
        f"KQL table '{KQL_TABLE_NAME}' has extra columns that do not belong in "
        f"the slim RTI telemetry model: {extra_kql_cols}\n\n"
        "Expected only:\n"
        f"- {TIMESTAMP_COLUMN_NAME}\n"
        f"- {KEY_COLUMN_NAME}\n"
        f"- {VALUE_COLUMN_NAME}\n"
        f"- {QUALITY_COLUMN_NAME}\n\n"
        "This usually means the table was created earlier with the old wide schema. "
        "For a clean test, delete/recreate the Eventhouse table or use a new table name."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Validate expected column types
# ══════════════════════════════════════════════════════════════════════════════

actual_type_by_col = {
    name: str(kql_type).lower()
    for name, kql_type in kql_cols
}

expected_type_by_col = {
    TIMESTAMP_COLUMN_NAME: "datetime",
    KEY_COLUMN_NAME: "string",
    VALUE_COLUMN_NAME: "real",
    QUALITY_COLUMN_NAME: "string",
}

type_mismatches = []

for column_name, expected_type in expected_type_by_col.items():
    actual_type = actual_type_by_col.get(column_name)

    if actual_type != expected_type:
        type_mismatches.append({
            "column_name": column_name,
            "expected_type": expected_type,
            "actual_type": actual_type,
        })

if type_mismatches:
    raise RuntimeError(
        f"KQL table '{KQL_TABLE_NAME}' has column type mismatches: {type_mismatches}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Display result
# ══════════════════════════════════════════════════════════════════════════════

md(f"✅ KQL table `{KQL_TABLE_NAME}` has the expected slim RTI schema.")

display(
    spark.createDataFrame(
        [
            {
                "column_name": name,
                "kql_type": kql_type,
            }
            for name, kql_type in kql_cols
        ]
    )
)

print("✅ Eventhouse RTI schema verified.")
print("✅ Expected slim columns:", sorted(expected_kql_cols))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 5 — Fetch ontology definition and validate signal_master
# ╚══════════════════════════════════════════════════════════════════════════╝

md("## 🧠 Fetching ontology definition and validating signal_master")


# ══════════════════════════════════════════════════════════════════════════════
# Validate required globals
# ══════════════════════════════════════════════════════════════════════════════

required_entity_globals = [
    "ontology_id",
    "STATIC_ENTITY_NAME",
    "KEY_COLUMN_NAME",
    "TIMESTAMP_COLUMN_NAME",
    "VALUE_COLUMN_NAME",
    "QUALITY_COLUMN_NAME",
    "get_ontology_definition",
    "decode",
]

missing_entity_globals = [
    name
    for name in required_entity_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_entity_globals:
    raise RuntimeError(
        "Missing required values. Run 007 Cells 1-4 first. "
        f"Missing: {missing_entity_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Fetch live ontology definition
# ══════════════════════════════════════════════════════════════════════════════

live_def = get_ontology_definition(ontology_id)
live_parts = live_def.get("definition", {}).get("parts", [])

if not live_parts:
    raise RuntimeError("Live ontology definition contains no parts.")


# ══════════════════════════════════════════════════════════════════════════════
# Extract EntityType definitions
# ══════════════════════════════════════════════════════════════════════════════

entity_defs = {}

for part in live_parts:
    path = part.get("path", "")
    segments = path.split("/")

    if (
        len(segments) >= 3
        and segments[0] == "EntityTypes"
        and segments[-1] == "definition.json"
    ):
        obj = decode(part.get("payload", ""))
        entity_id = obj.get("id", segments[1])

        if not entity_id:
            raise RuntimeError(
                f"Entity definition part has no entity ID. Path: {path}"
            )

        entity_defs[entity_id] = obj

if not entity_defs:
    raise RuntimeError("No EntityTypes found in live ontology definition.")


# ══════════════════════════════════════════════════════════════════════════════
# Resolve signal_master entity
# ══════════════════════════════════════════════════════════════════════════════

entity_name_counts = {}

for entity_id, obj in entity_defs.items():
    entity_name = obj.get("name")

    if entity_name:
        entity_name_counts[entity_name] = entity_name_counts.get(entity_name, 0) + 1

duplicate_entity_names = sorted([
    entity_name
    for entity_name, count in entity_name_counts.items()
    if count > 1
])

if duplicate_entity_names:
    raise RuntimeError(
        f"Duplicate entity names found in ontology definition: {duplicate_entity_names}"
    )

entity_name_to_id = {
    obj.get("name"): entity_id
    for entity_id, obj in entity_defs.items()
    if obj.get("name")
}

signal_entity_id = entity_name_to_id.get(STATIC_ENTITY_NAME)

if not signal_entity_id:
    raise RuntimeError(
        f"Entity '{STATIC_ENTITY_NAME}' not found in ontology. "
        f"Available entities: {sorted(entity_name_to_id.keys())}"
    )

signal_entity_def = entity_defs[signal_entity_id]


# ══════════════════════════════════════════════════════════════════════════════
# Extract static and time-series properties
# ══════════════════════════════════════════════════════════════════════════════

static_props = {
    prop["name"]: prop["id"]
    for prop in signal_entity_def.get("properties", [])
    if prop.get("name") and prop.get("id")
}

timeseries_props = {
    prop["name"]: prop["id"]
    for prop in signal_entity_def.get("timeseriesProperties", [])
    if prop.get("name") and prop.get("id")
}

if not static_props:
    raise RuntimeError(
        f"Entity '{STATIC_ENTITY_NAME}' has no static properties."
    )

if not timeseries_props:
    raise RuntimeError(
        f"Entity '{STATIC_ENTITY_NAME}' has no timeseriesProperties. "
        "Rerun 004 with the corrected ontology generation cell that writes "
        "Eventhouse RTI fields to timeseriesProperties on signal_master."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Validate identity key
# ══════════════════════════════════════════════════════════════════════════════

if KEY_COLUMN_NAME not in static_props:
    raise RuntimeError(
        f"Entity '{STATIC_ENTITY_NAME}' is missing static key property "
        f"'{KEY_COLUMN_NAME}'. Available static properties: {sorted(static_props.keys())}"
    )

entity_id_part_property_ids = set(signal_entity_def.get("entityIdParts", []))
key_property_id = static_props[KEY_COLUMN_NAME]

if key_property_id not in entity_id_part_property_ids:
    raise RuntimeError(
        f"'{KEY_COLUMN_NAME}' exists on '{STATIC_ENTITY_NAME}', but is not part "
        "of entityIdParts. The ontology generation step should identify "
        "signal_master by opcua_node_id."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Validate Eventhouse RTI time-series properties
# ══════════════════════════════════════════════════════════════════════════════

required_ts_props = {
    TIMESTAMP_COLUMN_NAME,
    VALUE_COLUMN_NAME,
    QUALITY_COLUMN_NAME,
}

missing_ts_props = sorted(required_ts_props - set(timeseries_props))

if missing_ts_props:
    raise RuntimeError(
        f"Entity '{STATIC_ENTITY_NAME}' is missing required timeseriesProperties: "
        f"{missing_ts_props}. Available timeseriesProperties: {sorted(timeseries_props.keys())}\n\n"
        "Rerun 004 with the corrected ontology generation cell that writes "
        "event_time, value, and quality to timeseriesProperties on signal_master."
    )

unexpected_static_ts_overlap = sorted(
    set(static_props).intersection(set(timeseries_props))
)

if unexpected_static_ts_overlap:
    raise RuntimeError(
        f"These properties exist both as static properties and timeseriesProperties "
        f"on '{STATIC_ENTITY_NAME}': {unexpected_static_ts_overlap}. "
        "Each property should be in only one place."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Build target property lookup for downstream binding cell
# ══════════════════════════════════════════════════════════════════════════════

all_target_props = {}
all_target_props.update(static_props)
all_target_props.update(timeseries_props)


# ══════════════════════════════════════════════════════════════════════════════
# Display validation summary
# ══════════════════════════════════════════════════════════════════════════════

md(f"✅ Entity `{STATIC_ENTITY_NAME}` found: `{signal_entity_id}`")
md(f"✅ Static key property `{KEY_COLUMN_NAME}` is present and is the entity identity key.")
md("✅ Eventhouse RTI fields are present as `timeseriesProperties` on `signal_master`.")

display(
    spark.createDataFrame(
        [
            {
                "property_group": "static",
                "property_name": property_name,
                "property_id": property_id,
                "is_entity_id_part": property_id in entity_id_part_property_ids,
            }
            for property_name, property_id in static_props.items()
        ]
        +
        [
            {
                "property_group": "timeseries",
                "property_name": property_name,
                "property_id": property_id,
                "is_entity_id_part": False,
            }
            for property_name, property_id in timeseries_props.items()
        ]
    ).orderBy("property_group", "property_name")
)

print("✅ signal_master ontology validation complete.")
print("✅ signal_master entity ID:", signal_entity_id)
print("✅ Static properties:", sorted(static_props.keys()))
print("✅ Time-series properties:", sorted(timeseries_props.keys()))
print("✅ Entity ID part property IDs:", sorted(entity_id_part_property_ids))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 6 — Build and push Eventhouse TimeSeries DataBinding
# ╚══════════════════════════════════════════════════════════════════════════╝

md("## 🚀 Building and pushing Eventhouse TimeSeries DataBinding")


# ══════════════════════════════════════════════════════════════════════════════
# Validate required globals
# ══════════════════════════════════════════════════════════════════════════════

required_binding_globals = [
    "ontology_id",
    "live_parts",
    "signal_entity_id",
    "STATIC_ENTITY_NAME",
    "WORKSPACE_ID",
    "EVENTHOUSE_ID",
    "CLUSTER_QUERY_URI",
    "KQL_DB_NAME",
    "KQL_TABLE_NAME",
    "KEY_COLUMN_NAME",
    "TIMESTAMP_COLUMN_NAME",
    "VALUE_COLUMN_NAME",
    "QUALITY_COLUMN_NAME",
    "static_props",
    "timeseries_props",
    "REPLACE_EXISTING_TIMESERIES_BINDING",
    "encode",
    "decode",
    "update_ontology_definition",
]

missing_binding_globals = [
    name
    for name in required_binding_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_binding_globals:
    raise RuntimeError(
        "Missing required values. Run 007 Cells 1-5 first. "
        f"Missing: {missing_binding_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Validate source and target properties
# ══════════════════════════════════════════════════════════════════════════════

DATABINDING_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/ontology/"
    "dataBinding/1.0.0/schema.json"
)

required_static_props = {
    KEY_COLUMN_NAME,
}

required_timeseries_props = {
    TIMESTAMP_COLUMN_NAME,
    VALUE_COLUMN_NAME,
    QUALITY_COLUMN_NAME,
}

missing_static_props = sorted(required_static_props - set(static_props))
missing_timeseries_props = sorted(required_timeseries_props - set(timeseries_props))

if missing_static_props:
    raise RuntimeError(
        f"`{STATIC_ENTITY_NAME}` is missing required static properties: {missing_static_props}"
    )

if missing_timeseries_props:
    raise RuntimeError(
        f"`{STATIC_ENTITY_NAME}` is missing required time-series properties: "
        f"{missing_timeseries_props}"
    )

# Cell 4 should already have kql_col_names, but keep this optional check safe.
if "kql_col_names" in globals():
    required_source_cols = {
        KEY_COLUMN_NAME,
        TIMESTAMP_COLUMN_NAME,
        VALUE_COLUMN_NAME,
        QUALITY_COLUMN_NAME,
    }

    missing_source_cols = sorted(required_source_cols - set(kql_col_names))

    if missing_source_cols:
        raise RuntimeError(
            f"KQL table `{KQL_TABLE_NAME}` is missing columns required for "
            f"the time-series binding: {missing_source_cols}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Identify existing Eventhouse/Kusto TimeSeries bindings on signal_master
# ══════════════════════════════════════════════════════════════════════════════

data_binding_prefix = f"EntityTypes/{signal_entity_id}/DataBindings/"


def is_eventhouse_timeseries_binding(part: dict) -> bool:
    path = part.get("path", "")

    if not path.startswith(data_binding_prefix):
        return False

    obj = decode(part.get("payload", ""))
    cfg = obj.get("dataBindingConfiguration", {}) or {}
    src = cfg.get("sourceTableProperties", {}) or {}

    return (
        cfg.get("dataBindingType") == "TimeSeries"
        and src.get("sourceType") == "KustoTable"
    )


existing_eventhouse_timeseries_bindings = [
    part
    for part in live_parts
    if is_eventhouse_timeseries_binding(part)
]

if existing_eventhouse_timeseries_bindings:
    existing_binding_summary = []

    for part in existing_eventhouse_timeseries_bindings:
        obj = decode(part.get("payload", ""))
        cfg = obj.get("dataBindingConfiguration", {}) or {}
        src = cfg.get("sourceTableProperties", {}) or {}

        existing_binding_summary.append({
            "path": part.get("path"),
            "databaseName": src.get("databaseName"),
            "sourceTableName": src.get("sourceTableName"),
            "itemId": src.get("itemId"),
            "clusterUri": src.get("clusterUri"),
        })

    md("### Existing Eventhouse/Kusto TimeSeries binding(s) found")

    display(spark.createDataFrame(existing_binding_summary))


# ══════════════════════════════════════════════════════════════════════════════
# Build corrected Eventhouse TimeSeries DataBinding
# ══════════════════════════════════════════════════════════════════════════════

if existing_eventhouse_timeseries_bindings and not REPLACE_EXISTING_TIMESERIES_BINDING:
    md(
        f"⏭️ `{STATIC_ENTITY_NAME}` already has Eventhouse TimeSeries DataBinding(s). "
        "`REPLACE_EXISTING_TIMESERIES_BINDING` is False, so no update was pushed."
    )

    updated_parts = live_parts

else:
    if existing_eventhouse_timeseries_bindings:
        md(
            f"♻️ Removing {len(existing_eventhouse_timeseries_bindings)} existing "
            f"Eventhouse/Kusto TimeSeries DataBinding(s) for `{STATIC_ENTITY_NAME}`."
        )

    parts_to_keep = [
        part
        for part in live_parts
        if not is_eventhouse_timeseries_binding(part)
    ]

    binding_id = str(uuid.uuid4())

    property_bindings = [
        {
            "sourceColumnName": KEY_COLUMN_NAME,
            "targetPropertyId": str(static_props[KEY_COLUMN_NAME]),
        },
        {
            "sourceColumnName": TIMESTAMP_COLUMN_NAME,
            "targetPropertyId": str(timeseries_props[TIMESTAMP_COLUMN_NAME]),
        },
        {
            "sourceColumnName": VALUE_COLUMN_NAME,
            "targetPropertyId": str(timeseries_props[VALUE_COLUMN_NAME]),
        },
        {
            "sourceColumnName": QUALITY_COLUMN_NAME,
            "targetPropertyId": str(timeseries_props[QUALITY_COLUMN_NAME]),
        },
    ]

    timeseries_binding = {
        "$schema": DATABINDING_SCHEMA,
        "id": binding_id,
        "dataBindingConfiguration": {
            "dataBindingType": "TimeSeries",
            "timestampColumnName": TIMESTAMP_COLUMN_NAME,
            "propertyBindings": property_bindings,
            "sourceTableProperties": {
                "sourceType": "KustoTable",
                "workspaceId": WORKSPACE_ID,
                "itemId": EVENTHOUSE_ID,
                "clusterUri": CLUSTER_QUERY_URI,
                "databaseName": KQL_DB_NAME,
                "sourceTableName": KQL_TABLE_NAME,
            },
        },
    }

    new_part = {
        "path": f"{data_binding_prefix}{binding_id}.json",
        "payload": encode(timeseries_binding),
        "payloadType": "InlineBase64",
    }

    updated_parts = parts_to_keep + [new_part]

    # Guard against duplicate part paths before updateDefinition.
    updated_paths = [
        part.get("path", "")
        for part in updated_parts
    ]

    duplicate_paths = sorted({
        path
        for path in updated_paths
        if updated_paths.count(path) > 1
    })

    if duplicate_paths:
        raise RuntimeError(
            f"Duplicate ontology definition paths detected before push: {duplicate_paths}"
        )

    md(
        f"⚡ Adding Eventhouse TimeSeries DataBinding for `{STATIC_ENTITY_NAME}` "
        f"from `{KQL_DB_NAME}.{KQL_TABLE_NAME}`."
    )

    display(
        spark.createDataFrame(
            [
                {
                    "source_column": binding["sourceColumnName"],
                    "target_property_id": binding["targetPropertyId"],
                    "target_group": (
                        "static"
                        if binding["sourceColumnName"] == KEY_COLUMN_NAME
                        else "timeseries"
                    ),
                }
                for binding in property_bindings
            ]
        )
    )

    update_ontology_definition(
        ontology_id,
        {
            "definition": {
                "parts": updated_parts,
            }
        },
    )

    # Keep downstream cells in sync.
    live_parts = updated_parts

    md("✅ Corrected Eventhouse TimeSeries DataBinding pushed.")


print("✅ Cell 6 complete.")
print("✅ Entity:", STATIC_ENTITY_NAME)
print("✅ Entity ID:", signal_entity_id)
print("✅ Eventhouse ID:", EVENTHOUSE_ID)
print("✅ KQL database:", KQL_DB_NAME)
print("✅ KQL table:", KQL_TABLE_NAME)
print("✅ Timestamp column:", TIMESTAMP_COLUMN_NAME)
print("✅ Key column:", KEY_COLUMN_NAME)
print("✅ Value column:", VALUE_COLUMN_NAME)
print("✅ Quality column:", QUALITY_COLUMN_NAME)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 7 — Verify Eventhouse TimeSeries DataBinding
# ╚══════════════════════════════════════════════════════════════════════════╝

md("## 🔍 Verifying Eventhouse TimeSeries DataBinding")


# ══════════════════════════════════════════════════════════════════════════════
# Validate required globals
# ══════════════════════════════════════════════════════════════════════════════

required_verify_globals = [
    "ontology_id",
    "signal_entity_id",
    "STATIC_ENTITY_NAME",
    "WORKSPACE_ID",
    "EVENTHOUSE_ID",
    "CLUSTER_QUERY_URI",
    "KQL_DB_NAME",
    "KQL_TABLE_NAME",
    "KEY_COLUMN_NAME",
    "TIMESTAMP_COLUMN_NAME",
    "VALUE_COLUMN_NAME",
    "QUALITY_COLUMN_NAME",
    "static_props",
    "timeseries_props",
    "get_ontology_definition",
    "decode",
]

missing_verify_globals = [
    name
    for name in required_verify_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_verify_globals:
    raise RuntimeError(
        "Missing required values. Run 007 Cells 1-6 first. "
        f"Missing: {missing_verify_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Fetch latest ontology definition
# ══════════════════════════════════════════════════════════════════════════════

verify_def = get_ontology_definition(ontology_id)
verify_parts = verify_def.get("definition", {}).get("parts", [])

if not verify_parts:
    raise RuntimeError("Live ontology definition contains no parts during verification.")

data_binding_prefix = f"EntityTypes/{signal_entity_id}/DataBindings/"


# ══════════════════════════════════════════════════════════════════════════════
# Expected binding contract
# ══════════════════════════════════════════════════════════════════════════════

expected_source_to_target = {
    KEY_COLUMN_NAME: str(static_props[KEY_COLUMN_NAME]),
    TIMESTAMP_COLUMN_NAME: str(timeseries_props[TIMESTAMP_COLUMN_NAME]),
    VALUE_COLUMN_NAME: str(timeseries_props[VALUE_COLUMN_NAME]),
    QUALITY_COLUMN_NAME: str(timeseries_props[QUALITY_COLUMN_NAME]),
}

expected_source_columns = set(expected_source_to_target)

expected_cluster_uri = CLUSTER_QUERY_URI.rstrip("/")


# ══════════════════════════════════════════════════════════════════════════════
# Inspect all TimeSeries DataBindings on signal_master
# ══════════════════════════════════════════════════════════════════════════════

matching_bindings = []

for part in verify_parts:
    path = part.get("path", "")

    if not path.startswith(data_binding_prefix):
        continue

    obj = decode(part.get("payload", ""))
    cfg = obj.get("dataBindingConfiguration", {}) or {}
    src = cfg.get("sourceTableProperties", {}) or {}

    if cfg.get("dataBindingType") != "TimeSeries":
        continue

    property_bindings = cfg.get("propertyBindings", []) or []

    source_to_target = {
        binding.get("sourceColumnName"): str(binding.get("targetPropertyId"))
        for binding in property_bindings
        if binding.get("sourceColumnName")
    }

    source_columns = set(source_to_target)

    missing_source_columns = sorted(expected_source_columns - source_columns)
    extra_source_columns = sorted(source_columns - expected_source_columns)

    incorrect_mappings = []

    for source_column, expected_target_property_id in expected_source_to_target.items():
        actual_target_property_id = source_to_target.get(source_column)

        if actual_target_property_id != expected_target_property_id:
            incorrect_mappings.append({
                "source_column": source_column,
                "expected_target_property_id": expected_target_property_id,
                "actual_target_property_id": actual_target_property_id,
            })

    source_cluster_uri = (src.get("clusterUri") or "").rstrip("/")

    is_valid = (
        src.get("sourceType") == "KustoTable"
        and src.get("workspaceId") == WORKSPACE_ID
        and src.get("itemId") == EVENTHOUSE_ID
        and source_cluster_uri == expected_cluster_uri
        and src.get("databaseName") == KQL_DB_NAME
        and src.get("sourceTableName") == KQL_TABLE_NAME
        and cfg.get("timestampColumnName") == TIMESTAMP_COLUMN_NAME
        and len(property_bindings) == 4
        and not missing_source_columns
        and not extra_source_columns
        and not incorrect_mappings
    )

    matching_bindings.append({
        "path": path,
        "binding_id": obj.get("id"),
        "source_type": src.get("sourceType"),
        "workspace_id": src.get("workspaceId"),
        "eventhouse_item_id": src.get("itemId"),
        "cluster_uri": src.get("clusterUri"),
        "database_name": src.get("databaseName"),
        "table_name": src.get("sourceTableName"),
        "timestamp_column": cfg.get("timestampColumnName"),
        "property_binding_count": len(property_bindings),
        "missing_source_columns": ", ".join(missing_source_columns),
        "extra_source_columns": ", ".join(extra_source_columns),
        "incorrect_mapping_count": len(incorrect_mappings),
        "is_valid": is_valid,
    })


if not matching_bindings:
    raise RuntimeError(
        f"No TimeSeries DataBinding found on `{STATIC_ENTITY_NAME}`."
    )


result_df = spark.createDataFrame(matching_bindings)
display(result_df)


valid_bindings = [
    binding
    for binding in matching_bindings
    if binding["is_valid"]
]

if not valid_bindings:
    decoded_details = []

    for part in verify_parts:
        path = part.get("path", "")

        if not path.startswith(data_binding_prefix):
            continue

        obj = decode(part.get("payload", ""))
        cfg = obj.get("dataBindingConfiguration", {}) or {}

        if cfg.get("dataBindingType") != "TimeSeries":
            continue

        decoded_details.append({
            "path": path,
            "decoded_binding": json.dumps(obj, indent=2),
        })

    if decoded_details:
        display(spark.createDataFrame(decoded_details))

    raise RuntimeError(
        f"No fully valid Eventhouse TimeSeries DataBinding found for "
        f"`{KQL_DB_NAME}.{KQL_TABLE_NAME}`.\n\n"
        "Expected source table properties:\n"
        f"- sourceType: KustoTable\n"
        f"- workspaceId: {WORKSPACE_ID}\n"
        f"- itemId: {EVENTHOUSE_ID}\n"
        f"- clusterUri: {CLUSTER_QUERY_URI}\n"
        f"- databaseName: {KQL_DB_NAME}\n"
        f"- sourceTableName: {KQL_TABLE_NAME}\n\n"
        "Expected exact property bindings:\n"
        f"- {KEY_COLUMN_NAME} -> {expected_source_to_target[KEY_COLUMN_NAME]}\n"
        f"- {TIMESTAMP_COLUMN_NAME} -> {expected_source_to_target[TIMESTAMP_COLUMN_NAME]}\n"
        f"- {VALUE_COLUMN_NAME} -> {expected_source_to_target[VALUE_COLUMN_NAME]}\n"
        f"- {QUALITY_COLUMN_NAME} -> {expected_source_to_target[QUALITY_COLUMN_NAME]}"
    )


if len(valid_bindings) > 1:
    raise RuntimeError(
        f"Found {len(valid_bindings)} valid Eventhouse TimeSeries DataBindings "
        f"on `{STATIC_ENTITY_NAME}`. Expected exactly one. "
        "Rerun Cell 6 with REPLACE_EXISTING_TIMESERIES_BINDING = True."
    )


# Keep downstream state in sync with the verified definition.
live_parts = verify_parts

valid_binding = valid_bindings[0]

md(
    f"✅ Verified Eventhouse TimeSeries DataBinding: "
    f"`{KQL_DB_NAME}.{KQL_TABLE_NAME}` → `{STATIC_ENTITY_NAME}` via `{KEY_COLUMN_NAME}`."
)

print("✅ Cell 7 complete.")
print("✅ Valid binding path:", valid_binding["path"])
print("✅ Binding ID:", valid_binding["binding_id"])
print("✅ Eventhouse ID:", EVENTHOUSE_ID)
print("✅ KQL database:", KQL_DB_NAME)
print("✅ KQL table:", KQL_TABLE_NAME)
print("✅ Timestamp column:", TIMESTAMP_COLUMN_NAME)
print("✅ Key column:", KEY_COLUMN_NAME)
print("✅ Value column:", VALUE_COLUMN_NAME)
print("✅ Quality column:", QUALITY_COLUMN_NAME)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
