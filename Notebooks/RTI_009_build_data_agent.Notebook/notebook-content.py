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

# # 09 — Build & Deploy the Data Agent (NL Q&A over the RTI Ontology)
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
# This notebook:
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

data_agent_name = first_setting("data_agent_name", default="RTI_Demo_DataAgent_V3")

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

import requests
import notebookutils  # Fabric notebook utility

FABRIC_BASE_URL = "https://api.fabric.microsoft.com/v1"


# -------------------------------------------------------------------------
# SPN auth + generic Fabric item helpers
# -------------------------------------------------------------------------
def get_spn_access_token_for_fabric():
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
    resp = requests.post(token_url, data=data)
    resp.raise_for_status()
    return resp.json()["access_token"]


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


def find_existing_item(access_token, display_name, item_type):
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    for item in resp.json().get("value", []):
        if item.get("displayName") == display_name and item.get("type", "").lower() == item_type.lower():
            return item
    return None


def resolve_ontology_id(access_token):
    """Return the id of the ontology item named `ontology_name` in the target folder."""
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    matches = [
        it for it in resp.json().get("value", [])
        if it.get("displayName") == ontology_name and it.get("type", "").lower() == "ontology"
    ]
    if not matches:
        raise RuntimeError(f"Ontology '{ontology_name}' not found. Run 004–006 first.")
    in_folder = [it for it in matches if it.get("folderId") == target_folder_id]
    chosen = (in_folder or matches)[0]
    return chosen["id"]


def wait_for_lro(resp, access_token, max_tries=30, delay_sec=5):
    """Follow a Fabric long-running-operation (202) until it completes."""
    location = resp.headers.get("Location")
    if not location:
        return resp
    headers = {"Authorization": f"Bearer {access_token}"}
    for attempt in range(1, max_tries + 1):
        poll = requests.get(location, headers=headers)
        status = poll.json().get("status") if poll.content else None
        if status in ("Succeeded", "Completed"):
            print(f"✅ Operation succeeded (attempt {attempt}).")
            return poll
        if status == "Failed":
            raise Exception(f"Operation failed: {poll.text}")
        print(f"⏳ In progress (attempt {attempt}/{max_tries}, status={status})...")
        time.sleep(delay_sec)
    raise Exception("Operation did not complete in the allotted time.")


def deploy_item_with_parts(access_token, display_name, item_type, parts):
    """Create or updateDefinition a Fabric item from (path, text) definition parts."""
    definition = {
        "parts": [
            {"path": path, "payload": b64(text), "payloadType": "InlineBase64"}
            for path, text in parts
        ]
    }
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    existing = find_existing_item(access_token, display_name, item_type)
    if existing:
        item_id = existing["id"]
        url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items/{item_id}/updateDefinition"
        resp = requests.post(url, headers=headers, json={"definition": definition})
        if resp.status_code not in (200, 202):
            raise Exception(f"updateDefinition failed: {resp.status_code} | {resp.text}")
        wait_for_lro(resp, access_token)
        print(f"♻️ Updated existing {item_type} '{display_name}' (ID: {item_id}).")
        return item_id

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    payload = {
        "displayName": display_name,
        "type": item_type,
        "folderId": target_folder_id,
        "definition": definition,
    }
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201, 202):
        raise Exception(f"create {item_type} failed: {resp.status_code} | {resp.text}")
    if resp.status_code == 202:
        poll = wait_for_lro(resp, access_token)
        body = poll.json() if poll.content else {}
        item_id = body.get("id") or (body.get("resourceLocation") or "").rsplit("/", 1)[-1]
    else:
        item_id = resp.json().get("id")
    print(f"✅ Created {item_type} '{display_name}' (ID: {item_id}).")
    return item_id


# -------------------------------------------------------------------------
# Data Agent definition parts
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


def build_data_agent_parts(ontology_id: str):
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "DataAgent", "displayName": data_agent_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }

    data_agent = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/dataAgent/2.1.0/schema.json"
    }

    publish_info = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/publishInfo/1.0.0/schema.json",
        "description": "",
    }

    stage_config = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/stageConfiguration/1.0.0/schema.json",
        "aiInstructions": AI_INSTRUCTIONS,
    }

    datasource = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/dataSource/1.0.0/schema.json",
        "artifactId": ontology_id,
        "workspaceId": "00000000-0000-0000-0000-000000000000",
        "dataSourceInstructions": None,
        "displayName": ontology_name,
        "type": "ontology",
        "userDescription": None,
        "metadata": {},
        "elements": [
            {
                "id": name,
                "is_selected": True,
                "display_name": name,
                "type": "ontology.entity",
                "description": cols,
                "children": [],
            }
            for name, cols in ONTOLOGY_ELEMENTS
        ],
    }

    ontology_dir = f"ontology-{ontology_name}"
    return [
        (".platform", json.dumps(platform, indent=2)),
        ("Files/Config/data_agent.json", json.dumps(data_agent, indent=2)),
        ("Files/Config/publish_info.json", json.dumps(publish_info, indent=2)),
        ("Files/Config/draft/stage_config.json", json.dumps(stage_config, indent=2)),
        (f"Files/Config/draft/{ontology_dir}/datasource.json", json.dumps(datasource, indent=2)),
    ]


# -------------------------------------------------------------------------
# Deploy (best-effort) + persist identifiers
# -------------------------------------------------------------------------
data_agent_item_id = None
try:
    access_token = get_spn_access_token_for_fabric()
    print("✅ Got Fabric access token (SPN).")

    ontology_id = resolve_ontology_id(access_token)
    print("✅ Resolved ontology ID:", ontology_id)

    parts = build_data_agent_parts(ontology_id)
    # Validate every JSON part before deploying.
    for path, text in parts:
        json.loads(text)
    print(f"✅ Data Agent definition built and validated ({len(parts)} parts).")

    data_agent_item_id = deploy_item_with_parts(access_token, data_agent_name, "DataAgent", parts)
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
