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

# # 08 — Build & Deploy the Real-Time Dashboard (OPC UA Telemetry Stats)
# Replicates the reference RTI dashboard, adapted to our **slim** `OPCUAEvents`
# schema (`event_time`, `opcua_node_id`, `value`, `quality`).
# Key design points:
# - The reference dashboard grouped by `facility_id` / `equipment_id`, which do
#   **not** exist in our Eventhouse table. Instead we parse the hierarchy that is
#   already encoded in `opcua_node_id` (`ns=2;s=T001.inlet_pressure`):
#   - `Turbine` = `extract(@';s=([^.]+)\.', 1, opcua_node_id)`  → `T001`..`T005`
#   - `Signal`  = `extract(@'\.([^.]+)$', 1, opcua_node_id)`   → `inlet_pressure`, ...
#   So **no Kusto lookup table / shortcut is required** — the facility/equipment
#   breakdown is achieved purely from the node id.
# - Our data is a single facility (`FACILITY_RTI_001`) with 5 turbines, per-turbine timecharts (T001, T002) are added.
# This notebook:
# 1. Reads `rti_demo_settings` for the live `cluster_query_uri`, `fabric_kql_db_id`
#    and KQL DB name (written by RTI_002).
# 2. Injects them into the dashboard definition (schema_version 77).
# 3. Writes an importable copy to the Lakehouse `Files/dashboards/` folder.
# 4. Deploys it as a Fabric **KQLDashboard** item via REST (best-effort). If the
#    deploy call fails, the notebook prints manual-import steps for the file it wrote.


# CELL ********************

# =========================
# CELL 0
# Load shared settings written by RTI_001 / RTI_002
# =========================

from pyspark.sql import functions as F

settings_table_name = "rti_demo_settings"

spark.catalog.clearCache()
spark.sql(f"REFRESH TABLE {settings_table_name}")

settings_df = spark.read.table(settings_table_name)

settings = {
    row["setting_name"]: row["setting_value"]
    for row in settings_df.collect()
}


def first_setting(*names, required: bool = False, default: str = None):
    """Return the first non-empty value among the given setting names."""
    for name in names:
        value = settings.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    if required:
        raise RuntimeError(
            f"Missing required setting. Tried these setting names: {list(names)}"
        )
    return default


workspace_id = first_setting("workspace_id", required=True)
target_folder_id = first_setting("target_folder_id", required=True)

# Live Kusto endpoint + KQL database, persisted by RTI_002 (do NOT invent URLs).
cluster_query_uri = first_setting("cluster_query_uri", required=True)
kql_db_id = first_setting("fabric_kql_db_id", "kql_database_id", required=True)
kql_db_name = first_setting("fabric_kql_db_name", "kql_database_name", required=True)
eventhouse_table_name = first_setting("eventhouse_table_name", "fabric_eventhouse_table", default="OPCUAEvents")

# Key Vault names/URIs for SPN auth (written by RTI_001).
key_vault_uri = first_setting("key_vault_uri", required=True)
key_vault_tenant_id_secret = first_setting("key_vault_tenant_id_secret", required=True)
key_vault_client_id_secret = first_setting("key_vault_client_id_secret", required=True)
key_vault_client_secret_secret = first_setting("key_vault_client_secret_secret", required=True)

dashboard_name = settings.get("dashboard_name", "RTI_Demo_OPCUA_TelemetryStats_V3")

print("✅ Settings loaded")
print("   Workspace ID       :", workspace_id)
print("   Target folder ID   :", target_folder_id)
print("   Cluster query URI  :", cluster_query_uri)
print("   KQL DB name        :", kql_db_name)
print("   KQL DB id          :", kql_db_id)
print("   Eventhouse table   :", eventhouse_table_name)
print("   Dashboard name     :", dashboard_name)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 1
# Build definition, write importable copy, deploy KQLDashboard via REST
# =========================

import json
import time
import uuid
import base64
import os

import requests
import notebookutils  # Fabric notebook utility

FABRIC_BASE_URL = "https://api.fabric.microsoft.com/v1"


# -------------------------------------------------------------------------
# Real-Time Dashboard definition (schema_version 77).
# Placeholders are replaced from rti_demo_settings so the data source points
# at THIS workspace's Eventhouse. Turbine/Signal are parsed from opcua_node_id,
# so no facility_id/equipment_id columns or Kusto lookup are needed.
# -------------------------------------------------------------------------
DASHBOARD_TEMPLATE_JSON = r'''{
  "schema_version": 77,
  "flavor": "RTDashboard_Regular",
  "baseQueries": [],
  "embeddedApps": [],
  "dataSources": [
    {
      "id": "d5000000-0000-4000-8000-000000000001",
      "kind": "kusto-trident",
      "clusterUri": "__CLUSTER_QUERY_URI__",
      "databaseArtifactId": "__KQL_DB_ID__",
      "database": "__KQL_DB_NAME__",
      "workspace": "__WORKSPACE_ID__",
      "name": "__KQL_DB_NAME__"
    }
  ],
  "pages": [
    {
      "id": "d5000000-0000-4000-8000-000000000002",
      "name": "OPC UA Telemetry"
    }
  ],
  "parameters": [
    {
      "kind": "duration",
      "id": "d5000000-0000-4000-8000-000000000003",
      "displayName": "Time range",
      "description": "",
      "beginVariableName": "_startTime",
      "endVariableName": "_endTime",
      "defaultValue": {
        "kind": "dynamic",
        "count": 1,
        "unit": "hours"
      },
      "showOnPages": {
        "kind": "all"
      }
    }
  ],
  "queries": [
    {
      "id": "d5000000-0000-4000-8000-000000000011",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| summarize ['Total Events'] = count()",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    },
    {
      "id": "d5000000-0000-4000-8000-000000000012",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| summarize ['Event Count'] = count() by ['Time Window'] = bin(event_time, 30m)\n| sort by ['Time Window'] desc",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    },
    {
      "id": "d5000000-0000-4000-8000-000000000013",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| sort by event_time desc\n| take 1000",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    },
    {
      "id": "d5000000-0000-4000-8000-000000000014",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| extend Turbine = extract(@';s=([^.]+)\\.', 1, opcua_node_id)\n| summarize ['Total'] = count(), ['Good'] = countif(quality == 'GOOD'), ['Bad'] = countif(quality == 'BAD'), ['Uncertain'] = countif(quality == 'UNCERTAIN') by Turbine\n| sort by Turbine asc",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    },
    {
      "id": "d5000000-0000-4000-8000-000000000015",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| extend Turbine = extract(@';s=([^.]+)\\.', 1, opcua_node_id)\n| summarize ['Good'] = countif(quality == 'GOOD'), ['Bad'] = countif(quality == 'BAD'), ['Uncertain'] = countif(quality == 'UNCERTAIN') by Turbine\n| project Turbine, ['Good'], ['Bad'], ['Uncertain']",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    },
    {
      "id": "d5000000-0000-4000-8000-000000000016",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| summarize ['Event Count'] = count() by bin(event_time, 30m)\n| render timechart",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    },
    {
      "id": "d5000000-0000-4000-8000-000000000017",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| extend Turbine = extract(@';s=([^.]+)\\.', 1, opcua_node_id)\n| where Turbine == 'T001'\n| extend Signal = extract(@'\\.([^.]+)$', 1, opcua_node_id)\n| summarize AvgValue = avg(value) by Signal, bin(event_time, 1m)\n| render timechart",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    },
    {
      "id": "d5000000-0000-4000-8000-000000000018",
      "text": "OPCUAEvents\n| where event_time between (_startTime .. _endTime)\n| extend Turbine = extract(@';s=([^.]+)\\.', 1, opcua_node_id)\n| where Turbine == 'T002'\n| extend Signal = extract(@'\\.([^.]+)$', 1, opcua_node_id)\n| summarize AvgValue = avg(value) by Signal, bin(event_time, 1m)\n| render timechart",
      "dataSource": { "kind": "inline", "dataSourceId": "d5000000-0000-4000-8000-000000000001" },
      "usedVariables": ["_startTime", "_endTime"]
    }
  ],
  "tiles": [
    {
      "id": "d5000000-0000-4000-8000-000000000021",
      "title": "Total Count of Events",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 0, "y": 0, "width": 6, "height": 4 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000011" },
      "visualType": "card",
      "visualOptions": {}
    },
    {
      "id": "d5000000-0000-4000-8000-000000000024",
      "title": "Equipment Health by Turbine",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 0, "y": 4, "width": 6, "height": 6 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000014" },
      "visualType": "table",
      "visualOptions": {}
    },
    {
      "id": "d5000000-0000-4000-8000-000000000022",
      "title": "Events per 30 minutes",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 6, "y": 0, "width": 8, "height": 10 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000012" },
      "visualType": "table",
      "visualOptions": {}
    },
    {
      "id": "d5000000-0000-4000-8000-000000000023",
      "title": "Sample 1000 Rows",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 14, "y": 0, "width": 10, "height": 10 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000013" },
      "visualType": "table",
      "visualOptions": {}
    },
    {
      "id": "d5000000-0000-4000-8000-000000000026",
      "title": "Events per 30 minutes (trend)",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 0, "y": 10, "width": 12, "height": 7 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000016" },
      "visualType": "timechart",
      "visualOptions": {}
    },
    {
      "id": "d5000000-0000-4000-8000-000000000025",
      "title": "Equipment Health by Turbine (quality mix)",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 12, "y": 10, "width": 12, "height": 7 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000015" },
      "visualType": "column",
      "visualOptions": {}
    },
    {
      "id": "d5000000-0000-4000-8000-000000000027",
      "title": "Turbine T001 - Signal Values",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 0, "y": 17, "width": 12, "height": 7 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000017" },
      "visualType": "timechart",
      "visualOptions": {}
    },
    {
      "id": "d5000000-0000-4000-8000-000000000028",
      "title": "Turbine T002 - Signal Values",
      "pageId": "d5000000-0000-4000-8000-000000000002",
      "layout": { "x": 12, "y": 17, "width": 12, "height": 7 },
      "queryRef": { "kind": "query", "queryId": "d5000000-0000-4000-8000-000000000018" },
      "visualType": "timechart",
      "visualOptions": {}
    }
  ]
}'''


# -------------------------------------------------------------------------
# 1. Resolve the definition against live settings + validate JSON
# -------------------------------------------------------------------------
resolved_json = (
    DASHBOARD_TEMPLATE_JSON
    .replace("__CLUSTER_QUERY_URI__", cluster_query_uri)
    .replace("__KQL_DB_NAME__", kql_db_name)
    .replace("__KQL_DB_ID__", kql_db_id)
    .replace("__WORKSPACE_ID__", workspace_id)
)

dashboard_def = json.loads(resolved_json)  # raises if invalid
resolved_json = json.dumps(dashboard_def, indent=2)
print(f"✅ Dashboard definition built and validated ({len(dashboard_def['tiles'])} tiles).")


# -------------------------------------------------------------------------
# 2. Write an importable copy to the Lakehouse (manual-import fallback)
# -------------------------------------------------------------------------
files_dir = "/lakehouse/default/Files/dashboards"
os.makedirs(files_dir, exist_ok=True)
local_dashboard_path = f"{files_dir}/{dashboard_name}.json"
with open(local_dashboard_path, "w", encoding="utf-8") as fh:
    fh.write(resolved_json)
print(f"✅ Importable dashboard written to: {local_dashboard_path}")


# -------------------------------------------------------------------------
# 3. Deploy as a Fabric KQLDashboard item (best-effort)
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


def find_existing_item(workspace_id, access_token, display_name, item_type):
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    for item in resp.json().get("value", []):
        if item.get("displayName") == display_name and item.get("type", "").lower() == item_type.lower():
            return item
    return None


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
            print(f"✅ Deployment operation succeeded (attempt {attempt}).")
            return poll
        if status == "Failed":
            raise Exception(f"Deployment operation failed: {poll.text}")
        print(f"⏳ Deployment in progress (attempt {attempt}/{max_tries}, status={status})...")
        time.sleep(delay_sec)
    raise Exception("Deployment operation did not complete in the allotted time.")


def deploy_kql_dashboard(access_token):
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "KQLDashboard", "displayName": dashboard_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }

    definition = {
        "parts": [
            {"path": "RealTimeDashboard.json", "payload": b64(resolved_json), "payloadType": "InlineBase64"},
            {"path": ".platform", "payload": b64(json.dumps(platform)), "payloadType": "InlineBase64"},
        ]
    }

    existing = find_existing_item(workspace_id, access_token, dashboard_name, "KQLDashboard")
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    if existing:
        item_id = existing["id"]
        url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items/{item_id}/updateDefinition"
        resp = requests.post(url, headers=headers, json={"definition": definition})
        if resp.status_code not in (200, 202):
            raise Exception(f"updateDefinition failed: {resp.status_code} | {resp.text}")
        wait_for_lro(resp, access_token)
        print(f"♻️ Updated existing KQLDashboard '{dashboard_name}' (ID: {item_id}).")
        return item_id

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    payload = {
        "displayName": dashboard_name,
        "type": "KQLDashboard",
        "folderId": target_folder_id,
        "definition": definition,
    }
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201, 202):
        raise Exception(f"create KQLDashboard failed: {resp.status_code} | {resp.text}")

    if resp.status_code == 202:
        poll = wait_for_lro(resp, access_token)
        body = poll.json() if poll.content else {}
        item_id = body.get("id") or (body.get("resourceLocation") or "").rsplit("/", 1)[-1]
    else:
        item_id = resp.json().get("id")
    print(f"✅ Created KQLDashboard '{dashboard_name}' in folder {target_folder_id} (ID: {item_id}).")
    return item_id


dashboard_item_id = None
try:
    access_token = get_spn_access_token_for_fabric()
    print("✅ Got Fabric access token (SPN).")
    dashboard_item_id = deploy_kql_dashboard(access_token)
except Exception as exc:  # noqa: BLE001 - best-effort deploy with manual fallback
    print("⚠️ Automated dashboard deployment did not complete:")
    print("   ", exc)
    print()
    print("You can still import the dashboard manually:")
    print("   1. In your Fabric workspace: New → Real-Time Dashboard.")
    print("   2. Open the new dashboard → Manage → Replace with file.")
    print(f"   3. Choose the file written above: {local_dashboard_path}")
    print("      (download it from the Lakehouse Files/dashboards folder first).")
    print("   4. If prompted, point the data source at Eventhouse:", kql_db_name)


# -------------------------------------------------------------------------
# 4. Persist dashboard identifiers back to rti_demo_settings
# -------------------------------------------------------------------------
from delta.tables import DeltaTable

persist = {"dashboard_name": dashboard_name}
if dashboard_item_id:
    persist["dashboard_id"] = dashboard_item_id

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

print("✅ Persisted dashboard settings:", persist)
display(spark.read.table(settings_table_name).orderBy("setting_name"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
