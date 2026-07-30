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

# CELL ********************

# =========================
# CELL 0
# Config settings
# =========================

# --- End-to-End Fabric Stream Ingestion: Setup and Automation ---

from pyspark.sql import functions as F

settings_table_name = "rti_demo_settings"

# Load shared settings written by RTI_001 (single source of truth, incl. Key Vault names/URIs).
settings_df = spark.read.table(settings_table_name)

settings = {
    row["setting_name"]: row["setting_value"]
    for row in settings_df.collect()
}

# Apply settings using the variable names already used by this notebook.
workspace_id = settings["workspace_id"]
workspace_folder_path = settings["workspace_folder_path"]
target_folder_id = settings["target_folder_id"]

fabric_eventstream_name = settings["eventstream_name"]
fabric_eventhouse_name = settings["eventhouse_name"]
fabric_kql_db_name = settings["kql_database_name"]
fabric_eventhouse_table = settings["eventhouse_table_name"]

lakehouse_name = settings["lakehouse_name"]
lakehouse_id = settings["lakehouse_id"]

key_vault_uri = settings["key_vault_uri"]
key_vault_tenant_id_secret = settings["key_vault_tenant_id_secret"]
key_vault_client_id_secret = settings["key_vault_client_id_secret"]
key_vault_client_secret_secret = settings["key_vault_client_secret_secret"]

print("Loaded configuration for workspace and artifact setup.")
print("✅ Settings table:", settings_table_name)
print("✅ Workspace ID:", workspace_id)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Lakehouse name:", lakehouse_name)
print("✅ Lakehouse ID:", lakehouse_id)
print("✅ Eventstream name:", fabric_eventstream_name)
print("✅ Eventhouse name:", fabric_eventhouse_name)
print("✅ KQL DB name:", fabric_kql_db_name)
print("✅ Eventhouse table:", fabric_eventhouse_table)
print("✅ Key Vault URI:", key_vault_uri)
print("✅ Target folder ID:", target_folder_id)

display(spark.read.table(settings_table_name).orderBy("setting_name"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 1
# Setup, Auth, Eventhouse, KQL DB & KQL Table
# =========================

import json
import time
from datetime import datetime
import random

import requests
import notebookutils  # Fabric notebook utility


FABRIC_BASE_URL = "https://api.fabric.microsoft.com/v1"


if "target_folder_id" not in globals():
    raise RuntimeError(
        "target_folder_id is not defined. "
        "Load it in CELL 0 with: target_folder_id = settings['target_folder_id']"
    )


# =========================
# AUTH: SPN → Fabric REST
# =========================

def get_spn_access_token_for_fabric():
    """
    Get an access token for the Fabric REST APIs using a service principal.
    Scope: https://api.fabric.microsoft.com/.default
    """
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

access_token = get_spn_access_token_for_fabric()
print("✅ Got Fabric access token (SPN).")


# =========================
# FABRIC ITEM HELPERS
# =========================

def fabric_get_items(workspace_id, access_token, item_type=None):
    """
    List items in workspace; optionally filter by type.
    """
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    if item_type:
        return [i for i in items if i.get("type", "").lower() == item_type.lower()]
    return items


def fabric_ensure_item(workspace_id, display_name, item_type, description, access_token, target_folder_id):
    """
    Ensure an item exists in the target folder.

    New items are created in target_folder_id.
    Existing items are reused only if they are already in target_folder_id.
    Existing same-name items outside target_folder_id stop the notebook,
    because this is meant to be a clean from-scratch demo setup.
    """
    for item in fabric_get_items(workspace_id, access_token, item_type=item_type):
        if item.get("displayName") == display_name:
            existing_folder_id = item.get("folderId")

            if existing_folder_id != target_folder_id:
                raise RuntimeError(
                    f"{item_type} '{display_name}' already exists, but not in the target folder.\n\n"
                    f"Existing item ID: {item['id']}\n"
                    f"Existing folder ID: {existing_folder_id}\n"
                    f"Target folder ID: {target_folder_id}\n\n"
                    "For a clean from-scratch test, delete the existing item or change the demo item name."
                )

            print(f"♻️ {item_type} '{display_name}' exists in target folder, ID: {item['id']}")
            return item["id"]

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "displayName": display_name,
        "description": description,
        "type": item_type,
        "folderId": target_folder_id,
    }

    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201, 202):
        raise Exception(f"Failed to create {item_type}: {resp.status_code} | {resp.text}")

    print(f"✅ Created {item_type} '{display_name}' in folder ID: {target_folder_id}")
    return resp.json()["id"]


# =========================
# EVENTHOUSE & KQL DB SETUP
# =========================

def get_eventhouse(workspace_id, eventhouse_id, access_token):
    """
    Get Eventhouse details, including its Kusto URIs.
    """
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/eventhouses/{eventhouse_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def ensure_eventhouse_and_kql_db(workspace_id, eventhouse_name, kql_db_name, access_token):
    # 1. Ensure Eventhouse item
    eventhouse_id = fabric_ensure_item(
        workspace_id,
        eventhouse_name,
        "Eventhouse",
        "Eventhouse for OPC UA streaming data",
        access_token,
        target_folder_id,
    )

    # 2. Ensure KQL Database attached to Eventhouse
    existing_kql = fabric_get_items(workspace_id, access_token, "KqlDatabase")
    for item in existing_kql:
        if item.get("displayName") == kql_db_name:
            existing_folder_id = item.get("folderId")

            if existing_folder_id != target_folder_id:
                raise RuntimeError(
                    f"KQL DB '{kql_db_name}' already exists, but not in the target folder.\n\n"
                    f"Existing item ID: {item['id']}\n"
                    f"Existing folder ID: {existing_folder_id}\n"
                    f"Target folder ID: {target_folder_id}\n\n"
                    "For a clean from-scratch test, delete the existing KQL DB/Eventhouse or change the demo item name."
                )

            print(f"♻️ KQL DB '{kql_db_name}' exists in target folder, ID: {item['id']}")
            kql_db_id = item["id"]
            break
    else:
        url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/kqlDatabases"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "displayName": kql_db_name,
            "folderId": target_folder_id,
            "creationPayload": {
                "databaseType": "ReadWrite",
                "parentEventhouseItemId": eventhouse_id
            }
        }
        resp = requests.post(url, headers=headers, json=payload)
        if resp.status_code not in (200, 201, 202):
            raise Exception(f"Failed to create KQL DB: {resp.status_code} | {resp.text}")
        kql_db_id = resp.json()["id"]
        print(f"✅ Created KQL DB '{kql_db_name}' in folder ID: {target_folder_id}, ID: {kql_db_id}")

    # 3. Poll Eventhouse until Kusto URIs are available
    cluster_query_uri = None
    cluster_ingest_uri = None
    last_details = None

    max_tries = 30
    delay_sec = 10

    for attempt in range(1, max_tries + 1):
        details = get_eventhouse(workspace_id, eventhouse_id, access_token)
        last_details = details

        props = details.get("properties") or {}

        # YOUR tenant uses these two fields:
        cluster_query_uri = props.get("queryServiceUri")
        cluster_ingest_uri = props.get("ingestionServiceUri")

        if cluster_query_uri:
            print(f"✅ Kusto URIs available (attempt {attempt}):")
            print("   Query URI :", cluster_query_uri)
            print("   Ingest URI:", cluster_ingest_uri)
            break

        print(f"⏳ Kusto URIs not ready yet (attempt {attempt}/{max_tries}) – waiting {delay_sec}s...")
        time.sleep(delay_sec)

    if not cluster_query_uri:
        print("❗ Kusto URIs still not available. Full Eventhouse details:")
        print(json.dumps(last_details, indent=2))
        raise Exception("Kusto cluster URL is empty; Eventhouse may not be fully initialized yet.")

    return eventhouse_id, kql_db_id, cluster_query_uri, cluster_ingest_uri


eventhouse_id, kql_db_id, cluster_query_uri, cluster_ingest_uri = ensure_eventhouse_and_kql_db(
    workspace_id,
    fabric_eventhouse_name,
    fabric_kql_db_name,
    access_token
)


from urllib.parse import urlparse

# =========================
# KUSTO: CREATE KQL TABLE
# =========================

def get_kusto_token(cluster_url: str) -> str:
    """
    Get an access token for the Kusto cluster using the same SPN as for Fabric.

    Kusto expects the scope to be the cluster base URL + '/.default', e.g.:
      https://<cluster>.kusto.fabric.microsoft.com/.default
    """
    # 1) Resolve base resource from cluster URL
    if not cluster_url:
        raise Exception("Kusto cluster URL is empty; Eventhouse may not be fully initialized yet.")

    parsed = urlparse(cluster_url)
    resource = f"{parsed.scheme}://{parsed.netloc}"  # e.g. https://<cluster>.kusto.fabric.microsoft.com
    scope = f"{resource}/.default"

    # 2) Get SPN credentials from Key Vault
    tenant_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_tenant_id_secret)
    client_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_id_secret)
    client_secret = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_secret_secret)
    if not tenant_id or not client_id or not client_secret:
        raise Exception("Unable to fetch SPN credentials from Key Vault for Kusto token.")

    # 3) Request token from Entra ID
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": scope,
    }
    resp = requests.post(token_url, data=data)
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_kql_table(cluster_query_uri, database_name, table_name):
    """
    Create the OPC UA events table via Kusto management REST API.
    Uses SPN-based token for the Kusto cluster.
    """
    token = get_kusto_token(cluster_query_uri)
    mgmt_url = f"{cluster_query_uri}/v1/rest/mgmt"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # UPDATED SCHEMA BASED ON NEW SIMULATED DATA
    csl = f"""
    .create table {table_name} (
        event_time: datetime,
        opcua_node_id: string,
        value: real,
        quality: string
    )
    """.strip()

    body = {
        "db": database_name,
        "csl": csl
    }

    resp = requests.post(mgmt_url, headers=headers, json=body)
    if resp.status_code in (200, 201):
        print(f"✅ Created table '{table_name}' (or it already exists).")
        print(resp.text[:500])
    else:
        print(f"❗ Kusto mgmt error: {resp.status_code} | {resp.text}")
        resp.raise_for_status()


# Create the table
create_kql_table(cluster_query_uri, fabric_kql_db_name, fabric_eventhouse_table)


# =========================
# PERSIST EVENTHOUSE / KQL RUNTIME OUTPUTS
# =========================
# The Eventhouse display name is not a Kusto URI.
# The real Kusto endpoints are runtime outputs returned by Fabric:
#   - queryServiceUri
#   - ingestionServiceUri
# Persist them so later notebooks do not invent or derive URLs from names.

from delta.tables import DeltaTable
from pyspark.sql import functions as F

runtime_settings = {
    # Current canonical names used by this pipeline
    "eventhouse_name": fabric_eventhouse_name,
    "eventhouse_id": eventhouse_id,
    "kql_database_name": fabric_kql_db_name,
    "kql_database_id": kql_db_id,
    "eventhouse_table_name": fabric_eventhouse_table,

    # Explicit aliases used by later notebooks / newer cells
    "fabric_eventhouse_name": fabric_eventhouse_name,
    "fabric_eventhouse_id": eventhouse_id,
    "fabric_kql_db_name": fabric_kql_db_name,
    "fabric_kql_db_id": kql_db_id,
    "fabric_eventhouse_table": fabric_eventhouse_table,

    # Real Fabric/Kusto endpoint values returned by Eventhouse properties
    "cluster_query_uri": cluster_query_uri,
    "cluster_ingest_uri": cluster_ingest_uri,
}

missing_runtime_settings = [
    key
    for key, value in runtime_settings.items()
    if value is None or str(value).strip() == ""
]

if missing_runtime_settings:
    raise RuntimeError(
        "Cannot persist Eventhouse/KQL runtime settings because these values are missing: "
        f"{missing_runtime_settings}"
    )

runtime_settings_df = (
    spark.createDataFrame(
        [
            {
                "setting_name": key,
                "setting_value": str(value),
            }
            for key, value in runtime_settings.items()
        ]
    )
    .withColumn("updated_utc", F.current_timestamp())
)

settings_delta_table = DeltaTable.forName(spark, settings_table_name)

(
    settings_delta_table.alias("target")
    .merge(
        runtime_settings_df.alias("source"),
        "target.setting_name = source.setting_name",
    )
    .whenMatchedUpdate(
        set={
            "setting_value": "source.setting_value",
            "updated_utc": "source.updated_utc",
        }
    )
    .whenNotMatchedInsert(
        values={
            "setting_name": "source.setting_name",
            "setting_value": "source.setting_value",
            "updated_utc": "source.updated_utc",
        }
    )
    .execute()
)

print("✅ Persisted Eventhouse/KQL runtime settings")
display(
    spark.read.table(settings_table_name)
    .where(F.col("setting_name").isin(list(runtime_settings.keys())))
    .orderBy("setting_name")
)


print("\n✅ CELL 1 complete – Eventhouse, KQL DB & table are ready.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 2
# Eventstream + Custom Endpoint setup
# =========================

import base64
import json
import requests
import time

SOURCE_NAME = settings.get("eventstream_source_name", "OPCUA_CustomEndpoint")
STREAM_NAME = settings.get("eventstream_stream_name", "OPCUA_DefaultStream")
DESTINATION_NAME = settings.get("eventstream_destination_name", "Eventhouse")

def get_eventstream_by_name(workspace_id: str, name: str, token: str):
    items = fabric_get_items(workspace_id, token, "Eventstream")
    for it in items:
        if it.get("displayName") == name:
            print(f"♻️ Eventstream '{name}' found – ID: {it['id']}")
            return it["id"]
    print(f"ℹ️ No existing Eventstream named '{name}' found.")
    return None


def create_eventstream(workspace_id: str, name: str, token: str) -> str:
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/eventstreams"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "displayName": name,
        "description": "Eventstream for slim OPC UA RTI telemetry",
        "folderId": target_folder_id,
    }

    resp = requests.post(url, headers=headers, json=body)

    if resp.status_code == 400 and "ItemDisplayNameNotAvailableYet" in resp.text:
        print("⏳ Display name not yet available. Waiting 30 seconds and retrying...")
        time.sleep(30)
        resp = requests.post(url, headers=headers, json=body)

    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        data = None

    if isinstance(data, dict) and "id" in data:
        eid = data["id"]
        print(f"✅ Created Eventstream '{name}' – ID: {eid}")
        return eid

    print("ℹ️ Create returned no body; polling items to resolve ID...")
    for attempt in range(1, 11):
        items = fabric_get_items(workspace_id, token, "Eventstream")
        match = next((it for it in items if it.get("displayName") == name), None)
        if match:
            eid = match["id"]
            print(f"✅ Found Eventstream '{name}' via polling – ID: {eid}")
            return eid
        time.sleep(3)

    raise RuntimeError(f"Could not resolve Eventstream ID for '{name}'.")


def get_eventstream_definition(workspace_id: str, eventstream_id: str, token: str):
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/eventstreams/{eventstream_id}/getDefinition"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json={})
    resp.raise_for_status()

    definition = resp.json()["definition"]["parts"]
    eventstream_json = None
    platform_part = None

    for part in definition:
        if part["path"] == "eventstream.json":
            eventstream_json = json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
        elif part["path"] == ".platform":
            platform_part = part

    if eventstream_json is None:
        raise RuntimeError("eventstream.json not found in definition.")

    return eventstream_json, platform_part


def mutate_definition_add_custom_endpoint(evt_def: dict) -> dict:
    sources = evt_def.get("sources", [])
    streams = evt_def.get("streams", [])
    destinations = evt_def.get("destinations", [])

    ce = next((s for s in sources if s.get("name") == SOURCE_NAME), None)
    if not ce:
        sources.append({
            "name": SOURCE_NAME,
            "type": "CustomEndpoint",
            "properties": {
                "authenticationMode": "Sas",
                "protocol": "EventHub"
            }
        })
        print("✅ Added CustomEndpoint source.")
    else:
        print("♻️ CustomEndpoint source already present.")

    ds = next((s for s in streams if s.get("name") == STREAM_NAME), None)
    if not ds:
        streams.append({
            "name": STREAM_NAME,
            "type": "DefaultStream",
            "properties": {},
            "inputNodes": [{"name": SOURCE_NAME}]
        })
        print("✅ Added DefaultStream wired from CustomEndpoint.")
    else:
        if not any(n["name"] == SOURCE_NAME for n in ds.get("inputNodes", [])):
            ds["inputNodes"].append({"name": SOURCE_NAME})
            print("🔧 Updated DefaultStream to read from CustomEndpoint.")
        print("♻️ DefaultStream already present.")

    dest = next((d for d in destinations if d.get("name") == DESTINATION_NAME), None)
    if not dest:
        destinations.append({
            "name": DESTINATION_NAME,
            "type": "Eventhouse",
            "properties": {
                "dataIngestionMode": "ProcessedIngestion",
                "workspaceId": workspace_id,
                "itemId": kql_db_id,
                "databaseName": fabric_kql_db_name,
                "tableName": fabric_eventhouse_table,
                "inputSerialization": {
                    "type": "Json",
                    "properties": {
                        "encoding": "UTF8"
                    }
                }
            },
            "inputNodes": [{"name": STREAM_NAME}],
            "inputSchemas": []
        })
        print("✅ Added Eventhouse destination.")
    else:
        if not any(n["name"] == STREAM_NAME for n in dest.get("inputNodes", [])):
            dest["inputNodes"].append({"name": STREAM_NAME})
        print("♻️ Eventhouse destination already present.")

    evt_def["sources"] = sources
    evt_def["streams"] = streams
    evt_def["destinations"] = destinations
    return evt_def


def update_eventstream_definition(workspace_id: str, eventstream_id: str, token: str):
    evt_def, platform_part = get_eventstream_definition(workspace_id, eventstream_id, token)
    evt_def_mut = mutate_definition_add_custom_endpoint(evt_def)

    evt_def_json = json.dumps(evt_def_mut, separators=(",", ":"))
    evt_def_b64 = base64.b64encode(evt_def_json.encode("utf-8")).decode("ascii")

    parts = [{
        "path": "eventstream.json",
        "payload": evt_def_b64,
        "payloadType": "InlineBase64"
    }]

    if platform_part:
        parts.append(platform_part)

    body = {"definition": {"parts": parts}}

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/eventstreams/{eventstream_id}/updateDefinition?updateMetadata=True"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resp = requests.post(url, headers=headers, json=body)
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Failed to update Eventstream definition: {resp.status_code} | {resp.text}")

    if resp.status_code == 202:
        print("⏳ Definition update accepted. Waiting 10 seconds...")
        time.sleep(10)

    print("✅ Eventstream definition updated.")


def get_eventstream_topology(workspace_id: str, eventstream_id: str, token: str) -> dict:
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/eventstreams/{eventstream_id}/topology"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    topo = resp.json()
    print(
        f"📈 Topology: {len(topo.get('sources', []))} sources, "
        f"{len(topo.get('streams', []))} streams, "
        f"{len(topo.get('destinations', []))} destinations."
    )
    return topo


def get_custom_endpoint_connection(workspace_id: str, eventstream_id: str, token: str, source_name: str) -> dict:
    topo = get_eventstream_topology(workspace_id, eventstream_id, token)
    ce = next((s for s in topo.get("sources", []) if s.get("name") == source_name), None)
    if not ce:
        raise RuntimeError(f"CustomEndpoint source '{source_name}' not found.")

    source_id = ce["id"]
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/eventstreams/{eventstream_id}/sources/{source_id}/connection"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    info = resp.json()

    print(
        f"🔌 Custom Endpoint ready – namespace: {info['fullyQualifiedNamespace']}, "
        f"eventHub: {info['eventHubName']}"
    )

    return {
        "endpoint": f"sb://{info['fullyQualifiedNamespace']}/",
        "entityPath": info["eventHubName"],
        "connectionString": info["accessKeys"]["primaryConnectionString"]
    }


eventstream_id = get_eventstream_by_name(workspace_id, fabric_eventstream_name, access_token)
if not eventstream_id:
    eventstream_id = create_eventstream(workspace_id, fabric_eventstream_name, access_token)

update_eventstream_definition(workspace_id, eventstream_id, access_token)
time.sleep(10)

custom_ep_info = get_custom_endpoint_connection(
    workspace_id,
    eventstream_id,
    access_token,
    SOURCE_NAME
)

print("✅ CELL 2 complete – Eventstream and Custom Endpoint are ready.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
