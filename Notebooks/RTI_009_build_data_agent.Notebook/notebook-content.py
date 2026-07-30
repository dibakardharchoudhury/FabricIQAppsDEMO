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

# # 09 — Build & Deploy the Data Agent (NL Q&A over the RTI Ontology)
# 
# Creates a Fabric **Data Agent** that answers natural-language questions over the
# `RTI_Demo_Ontology_V3` ontology. Because the ontology's `signal_master` entity is
# bound to **both** sources, the agent can join across them with **no schema change**:
# 
# - **Real-time (KQL)** — `OPCUAEvents` in `RTI_Demo_Eventhouse_V3` provides the
#   time-series `event_time` / `value` / `quality`.
# - **Static (Lakehouse)** — `silver_signal_master` (and the `equipment`,
#   `facilities`, `systems`, `instruments` entities) provide `equipment_id`,
#   `facility_id`, `system_id`, `unit`, `tag`, ... keyed on `opcua_node_id`.
# 
# The agent's data source is the **ontology item** (`type = ontology`), so the
# real-time↔lakehouse join is handled by the ontology bindings themselves.
# 
# ## This notebook
# 
# 1. Reads shared settings from `rti_demo_settings` (written by 001/002).
# 2. Resolves the live `ontology_id` by name in the target folder.
# 3. Builds the Data Agent item definition (`.platform` + `Files/Config/**`).
# 4. Deploys it as a Fabric **DataAgent** item via REST (best-effort, with a
#    manual-import fallback).
# 5. Persists `data_agent_name` / `data_agent_id` back to `rti_demo_settings`.


# CELL ********************

# =========================
# CELL 0
# Load shared settings written by RTI_001 / RTI_002
# =========================

from pyspark.sql import functions as F

settings_table_name = "rti_demo_settings"

spark.catalog.clearCache()
spark.sql(f"REFRESH TABLE {settings_table_name}")

settings = {
    row["setting_name"]: row["setting_value"]
    for row in spark.read.table(settings_table_name).collect()
}


def first_setting(*names, required: bool = False, default: str = None):
    """Return the first non-empty value among the given setting names."""
    for name in names:
        value = settings.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    if required:
        raise RuntimeError(f"Missing required setting. Tried: {list(names)}")
    return default


workspace_id = first_setting("workspace_id", required=True)
target_folder_id = first_setting("target_folder_id", required=True)

ontology_name = first_setting("ontology_name", "fabric_ontology_name", required=True)
lakehouse_id = first_setting("lakehouse_id", required=True)
lakehouse_name = first_setting("lakehouse_name", default="Energy_IQ_LakehouseRTI_V3")
kql_db_name = first_setting("fabric_kql_db_name", "kql_database_name", required=True)

# Key Vault names/URIs for SPN auth (written by RTI_001).
key_vault_uri = first_setting("key_vault_uri", required=True)
key_vault_tenant_id_secret = first_setting("key_vault_tenant_id_secret", required=True)
key_vault_client_id_secret = first_setting("key_vault_client_id_secret", required=True)
key_vault_client_secret_secret = first_setting("key_vault_client_secret_secret", required=True)

data_agent_name = first_setting("data_agent_name", default="RTI_Demo_Agent_V3")

print("✅ Settings loaded")
print("   Workspace ID     :", workspace_id)
print("   Target folder ID :", target_folder_id)
print("   Ontology name    :", ontology_name)
print("   Lakehouse name   :", lakehouse_name)
print("   KQL DB name      :", kql_db_name)
print("   Data Agent name  :", data_agent_name)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 1
# Build the Data Agent definition, deploy the DataAgent item, persist settings
# =========================

import json
import time
import uuid
import base64
from typing import Optional

import requests
import notebookutils  # Fabric notebook utility

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
DATA_AGENT_ITEM_TYPE = "DataAgent"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

# The ontology is attached as an "ontology" data source. The draft part folder
# is `ontology-<ontology_name>` — matching the layout Fabric writes for a
# published ontology-backed agent.
DATASOURCE_TYPE = "ontology"
DRAFT_STAGE_CONFIG_PATH = "Files/Config/draft/stage_config.json"
DATASOURCE_PATH = f"Files/Config/draft/{DATASOURCE_TYPE}-{ontology_name}/datasource.json"

STAGE_CONFIG_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/"
    "definition/stageConfiguration/1.0.0/schema.json"
)
DATASOURCE_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/"
    "definition/dataSource/1.0.0/schema.json"
)

DATA_AGENT_DESCRIPTION = (
    "Conversational agent over the RTI turbine telemetry ontology "
    f"'{ontology_name}': real-time OPC UA readings joined to equipment, "
    "facility, system and instrument context."
)[:256]  # Fabric item description max length is 256 chars


# -------------------------------------------------------------------------
# SPN auth (Key Vault) + retry / LRO helpers
# -------------------------------------------------------------------------
_token_cache = {"token": None, "expires_at": 0.0}


def get_spn_access_token_for_fabric() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    tenant_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_tenant_id_secret)
    client_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_id_secret)
    client_secret = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_secret_secret)
    if not tenant_id or not client_id or not client_secret:
        raise Exception("Unable to fetch SPN credentials from Key Vault")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "https://api.fabric.microsoft.com/.default",
    }
    resp = requests.post(token_url, data=data, timeout=60)
    resp.raise_for_status()
    token_json = resp.json()
    _token_cache["token"] = token_json["access_token"]
    _token_cache["expires_at"] = now + int(token_json.get("expires_in", 3600)) - 60
    return _token_cache["token"]


def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_spn_access_token_for_fabric()}",
        "Content-Type": "application/json",
    }


def api_request(method: str, url: str, data=None, params=None, timeout=60):
    """Retry wrapper for Fabric REST calls (429 + 5xx)."""
    last_response = None
    for _ in range(MAX_RETRIES):
        response = requests.request(
            method=method, url=url, headers=get_headers(),
            json=data, params=params, timeout=timeout,
        )
        last_response = response
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
            print(f"Rate limited. Retrying in {wait}s.")
            time.sleep(wait)
            continue
        if response.status_code >= 500:
            print(f"Server error {response.status_code}. Retrying.")
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        return response
    return last_response


def wait_for_lro(operation_url: str) -> dict:
    """Poll a Fabric long-running-operation URL until terminal."""
    start = time.time()
    while time.time() - start < LRO_MAX_WAIT_SECONDS:
        response = api_request("GET", operation_url, timeout=60)
        if response.status_code not in (200, 202):
            raise RuntimeError(f"LRO polling failed: {response.status_code} {response.text}")
        try:
            result = response.json()
        except ValueError:
            result = {"status": "Unknown"}
        status = result.get("status", "Unknown")
        if status in ("Succeeded", "Completed"):
            return result
        if status in ("Failed", "Cancelled"):
            raise RuntimeError(json.dumps(result, indent=2))
        print(f"⏳ LRO status: {status}")
        time.sleep(LRO_POLL_INTERVAL_SECONDS)
    raise TimeoutError("LRO polling timed out.")


def encode_payload(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decode_payload(payload: str) -> dict:
    try:
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        return {}


# -------------------------------------------------------------------------
# Item discovery + Data Agent REST operations
# -------------------------------------------------------------------------
def find_item_by_name(display_name: str, item_type: Optional[str] = None) -> Optional[dict]:
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    params = {"type": item_type} if item_type else None
    response = api_request("GET", url, params=params)
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    for item in (data or {}).get("value", []) or []:
        if item.get("displayName") == display_name:
            return item
    return None


def resolve_ontology_id() -> str:
    """Return the id of the ontology item named `ontology_name` in the target folder."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    response = api_request("GET", url)
    response.raise_for_status()
    matches = [
        it for it in response.json().get("value", [])
        if it.get("displayName") == ontology_name and it.get("type", "").lower() == "ontology"
    ]
    if not matches:
        raise RuntimeError(f"Ontology '{ontology_name}' not found. Run 004–006 first.")
    in_folder = [it for it in matches if it.get("folderId") == target_folder_id]
    return (in_folder or matches)[0]["id"]


def create_data_agent(display_name: str, description: str = "") -> dict:
    """Create an EMPTY Data Agent item (reuse if it already exists)."""
    existing = find_item_by_name(display_name, item_type=DATA_AGENT_ITEM_TYPE)
    if existing:
        print(f"✅ Reusing existing Data Agent: {display_name} (id={existing.get('id')})")
        return existing

    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    body = {"displayName": display_name, "description": description, "type": DATA_AGENT_ITEM_TYPE}
    if target_folder_id:
        body["folderId"] = target_folder_id
    response = api_request("POST", url, data=body, timeout=120)

    if response.status_code in (200, 201):
        created = response.json() if response.content else {}
        print(f"✅ Created Data Agent: {display_name} (id={created.get('id')})")
        return created
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("Create Data Agent returned 202 without Location header.")
        wait_for_lro(operation_url)
        created = find_item_by_name(display_name, item_type=DATA_AGENT_ITEM_TYPE) or {}
        print(f"✅ Created Data Agent (via LRO): {display_name} (id={created.get('id')})")
        return created
    raise RuntimeError(f"Failed to create Data Agent: {response.status_code} {response.text}")


def get_item_definition(item_id: str) -> dict:
    """Read an item's definition (InlineBase64 parts) via getDefinition."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items/{item_id}/getDefinition"
    response = api_request("POST", url, timeout=120)
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("getDefinition returned 202 without Location header.")
        wait_for_lro(operation_url)
        result_response = api_request("GET", f"{operation_url}/result", timeout=120)
        if result_response.status_code == 200:
            return result_response.json() if result_response.content else {}
        raise RuntimeError(f"getDefinition result failed: {result_response.status_code} {result_response.text}")
    raise RuntimeError(f"Failed to get item definition: {response.status_code} {response.text}")


def update_item_definition(item_id: str, definition: dict) -> dict:
    """Write an item's full definition via updateDefinition."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items/{item_id}/updateDefinition"
    response = api_request("POST", url, data={"definition": definition}, timeout=300)
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("updateDefinition returned 202 without Location header.")
        return wait_for_lro(operation_url)
    raise RuntimeError(f"Failed to update item definition: {response.status_code} {response.text}")


def upsert_part(parts: list, path: str, obj: dict) -> list:
    """Replace (or append) an InlineBase64 part at `path` with `obj`."""
    encoded = {"path": path, "payload": encode_payload(obj), "payloadType": "InlineBase64"}
    for i, part in enumerate(parts):
        if part.get("path") == path:
            parts[i] = encoded
            return parts
    parts.append(encoded)
    return parts


# -------------------------------------------------------------------------
# Agent instructions + ontology data source
# -------------------------------------------------------------------------
AI_INSTRUCTIONS = (
    "You are an expert on industrial turbine telemetry modelled as a Fabric Ontology.\n"
    "- Real-time telemetry lives in the KQL Eventhouse and is exposed through the "
    "`signal_master` entity time-series properties (`event_time`, `value`, `quality`).\n"
    "- Static/reference data lives in the Lakehouse and is exposed through the "
    "`signal_master`, `equipment`, `facilities`, `systems` and `instruments` entities.\n"
    "- Every signal is keyed on `opcua_node_id`; join real-time readings to equipment "
    "metadata using `opcua_node_id`, and roll up to sites via "
    "`equipment_id` -> `facility_id` -> `system_id`.\n"
    "- The `unit` property on `signal_master`/`instruments` tells you whether a reading "
    "is pressure, temperature, flow, vibration or position.\n"
    "- `quality` values are GOOD (normal), UNCERTAIN (degraded) and BAD (failure). "
    "Use them to reason about equipment health.\n"
    "- Prefer answers that combine live readings with equipment/facility context. "
    "Keep answers short, clear and business-readable."
)

# Ontology entities to expose to the agent (name -> column summary used as description).
ONTOLOGY_ELEMENTS = [
    ("signal_master",
     "opcua_node_id,tag,instrument_id,equipment_id,system_id,facility_id,unit,"
     "is_active,signal_type,event_time,value,quality"),
    ("equipment",
     "equipment_id,facility_id,system_id,equipment_type_code,equipment_type_name,tag,"
     "manufacturer,model,criticality,install_date,status,is_active"),
    ("facilities",
     "facility_id,facility_name,type,country,lat,lon,commissioned_date"),
    ("systems",
     "system_id,facility_id,system_name,oag_rds_system_code"),
    ("instruments",
     "opcua_node_id,tag,instrument_id,equipment_id,system_id,facility_id,unit,"
     "instrument_type,is_active"),
]


def build_stage_obj(existing: dict) -> dict:
    """Draft stage_config carrying the agent-level aiInstructions."""
    stage = dict(existing)
    stage["$schema"] = STAGE_CONFIG_SCHEMA_URL
    stage["aiInstructions"] = AI_INSTRUCTIONS
    return stage


def build_datasource_obj(existing: dict, ontology_id: str) -> dict:
    """Ontology data source in the Fabric Data Agent shape (entity `elements`)."""
    ds = dict(existing)
    ds["$schema"] = DATASOURCE_SCHEMA_URL
    ds["artifactId"] = ontology_id
    # Must be the ontology's real workspace GUID (empty/zero GUID is rejected).
    ds["workspaceId"] = workspace_id
    ds["displayName"] = ontology_name
    ds["type"] = DATASOURCE_TYPE
    ds.setdefault("dataSourceInstructions", None)
    ds.setdefault("userDescription", None)
    ds.setdefault("metadata", {})
    ds["elements"] = [
        {
            "id": name,
            "is_selected": True,
            "display_name": name,
            "type": "ontology.entity",
            "description": cols,
            "children": [],
        }
        for name, cols in ONTOLOGY_ELEMENTS
    ]
    return ds


# -------------------------------------------------------------------------
# Deploy: create (empty) -> discover -> patch draft parts. Best-effort.
# -------------------------------------------------------------------------
data_agent_item_id = None
try:
    get_spn_access_token_for_fabric()
    print("✅ Got Fabric access token (SPN).")

    ontology_id = resolve_ontology_id()
    print("✅ Resolved ontology ID:", ontology_id)

    # 1) Create (or reuse) the Data Agent item — empty, no definition.
    data_agent_item = create_data_agent(data_agent_name, DATA_AGENT_DESCRIPTION)
    data_agent_item_id = data_agent_item.get("id")

    # 2) DISCOVERY — read the live definition Fabric generated.
    definition = get_item_definition(data_agent_item_id)
    parts = definition.get("definition", {}).get("parts", []) or []
    print(f"🔎 Live definition has {len(parts)} part(s):")
    for part in parts:
        print("   •", part.get("path", ""))

    # 3) PATCH — upsert the draft aiInstructions + ontology data source.
    existing_stage = next(
        (decode_payload(p.get("payload", "")) for p in parts if p.get("path") == DRAFT_STAGE_CONFIG_PATH),
        {},
    )
    parts = upsert_part(parts, DRAFT_STAGE_CONFIG_PATH, build_stage_obj(existing_stage))

    existing_ds = next(
        (decode_payload(p.get("payload", "")) for p in parts if p.get("path") == DATASOURCE_PATH),
        {},
    )
    parts = upsert_part(parts, DATASOURCE_PATH, build_datasource_obj(existing_ds, ontology_id))

    print(f"Applying definition: {len(parts)} part(s)")
    print("   • aiInstructions        ->", DRAFT_STAGE_CONFIG_PATH)
    print("   • ontology data source  ->", DATASOURCE_PATH)
    print(f"       {len(ONTOLOGY_ELEMENTS)} entity element(s) selected.")

    update_item_definition(data_agent_item_id, {"parts": parts})
    print(f"✅ Data Agent '{data_agent_name}' configured (id={data_agent_item_id}).")
    print("ℹ️ Draft only — open the Data Agent in Fabric and PUBLISH to go live.")
except Exception as exc:  # noqa: BLE001 - best-effort deploy with manual fallback
    print("⚠️ Automated Data Agent deployment did not complete:")
    print("   ", exc)
    print()
    print("Manual fallback:")
    print("   1. In your Fabric workspace: New → Data agent.")
    print(f"   2. Name it '{data_agent_name}'.")
    print(f"   3. Add data source → Ontology → '{ontology_name}'.")
    print("   4. Select entities: signal_master, equipment, facilities, systems, instruments.")
    print("   5. Paste the AI instructions from AI_INSTRUCTIONS above, then publish.")


if data_agent_item_id:
    from delta.tables import DeltaTable

    persist = {"data_agent_name": data_agent_name, "data_agent_id": data_agent_item_id}
    persist_df = (
        spark.createDataFrame([{"setting_name": k, "setting_value": str(v)} for k, v in persist.items()])
        .withColumn("updated_utc", F.current_timestamp())
    )
    settings_delta_table = DeltaTable.forName(spark, settings_table_name)
    (
        settings_delta_table.alias("target")
        .merge(persist_df.alias("source"), "target.setting_name = source.setting_name")
        .whenMatchedUpdate(set={"setting_value": "source.setting_value", "updated_utc": "source.updated_utc"})
        .whenNotMatchedInsert(
            values={
                "setting_name": "source.setting_name",
                "setting_value": "source.setting_value",
                "updated_utc": "source.updated_utc",
            }
        )
        .execute()
    )
    print("✅ Persisted Data Agent settings:", persist)
    display(spark.read.table(settings_table_name).orderBy("setting_name"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
