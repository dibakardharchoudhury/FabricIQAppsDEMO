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

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — RTI Eventstream / Eventhouse ingestion
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
    "eventstream_name",
    "eventhouse_name",
    "kql_database_name",
    "eventhouse_table_name",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
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
# CORE WORKSPACE / FOLDER / LAKEHOUSE SETTINGS
# --------------------------------------------

workspace_id = settings["workspace_id"]
workspace_folder_path = settings["workspace_folder_path"]
target_folder_id = settings["target_folder_id"]

lakehouse_name = settings["lakehouse_name"]
lakehouse_id = settings["lakehouse_id"]

# --------------------------------------------
# EVENTSTREAM / EVENTHOUSE SETTINGS
# Keep existing variable names used by later 006 cells.
# --------------------------------------------

fabric_eventstream_name = settings["eventstream_name"]
fabric_eventhouse_name = settings["eventhouse_name"]
fabric_kql_db_name = settings["kql_database_name"]
fabric_eventhouse_table = settings["eventhouse_table_name"]

# Optional values if previous setup cells persist them.
eventstream_id = settings.get("eventstream_id", "")
eventhouse_id = settings.get("eventhouse_id", "")
kql_database_id = settings.get("kql_database_id", "")

# Some later cells may use uppercase names.
EVENTHOUSE_ID = eventhouse_id
KQL_DATABASE_ID = kql_database_id

cluster_query_uri = settings.get(
    "cluster_query_uri",
    f"https://{fabric_kql_db_name}.kusto.fabric.microsoft.com"
)

# --------------------------------------------
# KEY VAULT / AUTH SETTINGS
# --------------------------------------------

key_vault_uri = settings["key_vault_uri"]
key_vault_tenant_id_secret = settings["key_vault_tenant_id_secret"]
key_vault_client_id_secret = settings["key_vault_client_id_secret"]
key_vault_client_secret_secret = settings["key_vault_client_secret_secret"]

# --------------------------------------------
# STRUCTURED SIGNAL SOURCE
# 006 should read this table to know which OPC UA signals to generate.
# --------------------------------------------

silver_signal_master_table = settings["silver_signal_master_table"]
SILVER_SIGNAL_MASTER_TABLE = silver_signal_master_table

print("✅ Loaded 006 configuration from shared settings.")
print("✅ Workspace ID:", workspace_id)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Lakehouse:", lakehouse_name)
print("✅ Lakehouse ID:", lakehouse_id)
print("✅ Eventstream name:", fabric_eventstream_name)
print("✅ Eventstream ID:", eventstream_id if eventstream_id else "<not found in settings>")
print("✅ Eventhouse name:", fabric_eventhouse_name)
print("✅ Eventhouse ID:", eventhouse_id if eventhouse_id else "<not found in settings>")
print("✅ KQL database:", fabric_kql_db_name)
print("✅ KQL database ID:", kql_database_id if kql_database_id else "<not found in settings>")
print("✅ Eventhouse table:", fabric_eventhouse_table)
print("✅ Cluster query URI:", cluster_query_uri)
print("✅ Signal source table:", silver_signal_master_table)
print("✅ Key Vault URI:", key_vault_uri)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 1
# Setup, Auth, Eventhouse, KQL DB & slim KQL table
# =========================

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import notebookutils
from pyspark.sql.types import StructType, StructField, StringType
from pyspark.sql import functions as F


FABRIC_BASE_URL = "https://api.fabric.microsoft.com/v1"


# =========================
# AUTH: SPN → Fabric REST
# =========================

_fabric_token_cache = {
    "token": None,
    "expires_at": 0.0,
}


def get_spn_access_token_for_fabric() -> str:
    """
    Get an access token for Fabric REST APIs using the service principal.
    Secret names come from rti_demo_settings via the 006 config cell.
    """

    now = time.time()

    if _fabric_token_cache["token"] and now < _fabric_token_cache["expires_at"]:
        return _fabric_token_cache["token"]

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

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError("Unable to fetch SPN credentials from Key Vault.")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "https://api.fabric.microsoft.com/.default",
    }

    response = requests.post(
        token_url,
        data=data,
        timeout=30,
    )

    response.raise_for_status()

    token_data = response.json()

    _fabric_token_cache["token"] = token_data["access_token"]
    _fabric_token_cache["expires_at"] = now + token_data.get("expires_in", 3600) - 60

    return _fabric_token_cache["token"]


access_token = get_spn_access_token_for_fabric()
print("✅ Got Fabric access token using SPN.")


# =========================
# FABRIC ITEM HELPERS
# =========================

def fabric_get_items(workspace_id, access_token, item_type=None):
    """
    List Fabric items in the workspace.
    """

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=60,
    )

    response.raise_for_status()

    items = response.json().get("value", [])

    if item_type:
        return [
            item
            for item in items
            if item.get("type", "").lower() == item_type.lower()
        ]

    return items


def fabric_ensure_item(
    workspace_id,
    display_name,
    item_type,
    description,
    access_token,
    target_folder_id,
):
    """
    Ensure a Fabric item exists in the target folder.

    For clean from-scratch testing, do not silently reuse an item with the same
    name outside target_folder_id.
    """

    existing_items = fabric_get_items(
        workspace_id,
        access_token,
        item_type=item_type,
    )

    for item in existing_items:
        if item.get("displayName") != display_name:
            continue

        existing_folder_id = item.get("folderId")

        if existing_folder_id != target_folder_id:
            raise RuntimeError(
                f"{item_type} '{display_name}' already exists, but not in the target folder.\n"
                f"Existing item ID: {item.get('id')}\n"
                f"Existing folder ID: {existing_folder_id}\n"
                f"Target folder ID: {target_folder_id}\n"
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

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=120,
    )

    if response.status_code not in (200, 201, 202):
        raise RuntimeError(
            f"Failed to create {item_type}: {response.status_code} | {response.text}"
        )

    if response.text and response.text.strip():
        try:
            result = response.json()
            if "id" in result:
                print(f"✅ Created {item_type} '{display_name}', ID: {result['id']}")
                return result["id"]
        except Exception:
            pass

    # Some Fabric create calls may complete asynchronously. Poll by name.
    for attempt in range(1, 31):
        time.sleep(5)

        for item in fabric_get_items(workspace_id, access_token, item_type=item_type):
            if (
                item.get("displayName") == display_name
                and item.get("folderId") == target_folder_id
            ):
                print(f"✅ Created {item_type} '{display_name}', ID: {item['id']}")
                return item["id"]

        print(f"⏳ Waiting for {item_type} '{display_name}' to appear ({attempt}/30)...")

    raise RuntimeError(
        f"{item_type} '{display_name}' create request succeeded, but item was not found afterwards."
    )


def get_eventhouse(workspace_id, eventhouse_id, access_token):
    """
    Get Eventhouse details, including Kusto query/ingest URIs.
    """

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/eventhouses/{eventhouse_id}"

    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


def ensure_eventhouse_and_kql_db(
    workspace_id,
    eventhouse_name,
    kql_db_name,
    access_token,
    target_folder_id,
):
    """
    Ensure Eventhouse and attached KQL database exist in the target folder.
    """

    eventhouse_id = fabric_ensure_item(
        workspace_id=workspace_id,
        display_name=eventhouse_name,
        item_type="Eventhouse",
        description="Eventhouse for slim OPC UA RTI telemetry",
        access_token=access_token,
        target_folder_id=target_folder_id,
    )

    existing_kql = fabric_get_items(
        workspace_id,
        access_token,
        item_type="KqlDatabase",
    )

    kql_db_id = None

    for item in existing_kql:
        if item.get("displayName") != kql_db_name:
            continue

        existing_folder_id = item.get("folderId")

        if existing_folder_id != target_folder_id:
            raise RuntimeError(
                f"KQL DB '{kql_db_name}' already exists, but not in the target folder.\n"
                f"Existing item ID: {item.get('id')}\n"
                f"Existing folder ID: {existing_folder_id}\n"
                f"Target folder ID: {target_folder_id}\n"
                "For a clean from-scratch test, delete the existing item or change the demo item name."
            )

        print(f"♻️ KQL DB '{kql_db_name}' exists in target folder, ID: {item['id']}")
        kql_db_id = item["id"]
        break

    if not kql_db_id:
        url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/kqlDatabases"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "displayName": kql_db_name,
            "folderId": target_folder_id,
            "creationPayload": {
                "databaseType": "ReadWrite",
                "parentEventhouseItemId": eventhouse_id,
            },
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=120,
        )

        if response.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"Failed to create KQL DB: {response.status_code} | {response.text}"
            )

        if response.text and response.text.strip():
            try:
                result = response.json()
                if "id" in result:
                    kql_db_id = result["id"]
                    print(f"✅ Created KQL DB '{kql_db_name}', ID: {kql_db_id}")
            except Exception:
                pass

        if not kql_db_id:
            for attempt in range(1, 31):
                time.sleep(5)

                for item in fabric_get_items(workspace_id, access_token, item_type="KqlDatabase"):
                    if (
                        item.get("displayName") == kql_db_name
                        and item.get("folderId") == target_folder_id
                    ):
                        kql_db_id = item["id"]
                        print(f"✅ Created KQL DB '{kql_db_name}', ID: {kql_db_id}")
                        break

                if kql_db_id:
                    break

                print(f"⏳ Waiting for KQL DB '{kql_db_name}' to appear ({attempt}/30)...")

        if not kql_db_id:
            raise RuntimeError(
                f"KQL DB '{kql_db_name}' create request succeeded, but item was not found afterwards."
            )

    cluster_query_uri = None
    cluster_ingest_uri = None
    last_details = None

    max_tries = 30
    delay_sec = 10

    for attempt in range(1, max_tries + 1):
        details = get_eventhouse(
            workspace_id,
            eventhouse_id,
            access_token,
        )

        last_details = details

        props = details.get("properties") or {}

        cluster_query_uri = props.get("queryServiceUri")
        cluster_ingest_uri = props.get("ingestionServiceUri")

        if cluster_query_uri:
            print(f"✅ Kusto URIs available on attempt {attempt}:")
            print("   Query URI :", cluster_query_uri)
            print("   Ingest URI:", cluster_ingest_uri)
            break

        print(f"⏳ Kusto URIs not ready yet ({attempt}/{max_tries}) – waiting {delay_sec}s...")
        time.sleep(delay_sec)

    if not cluster_query_uri:
        print("❗ Kusto URIs still not available. Full Eventhouse details:")
        print(json.dumps(last_details, indent=2))
        raise RuntimeError("Kusto query URI is empty; Eventhouse may not be fully initialized.")

    return eventhouse_id, kql_db_id, cluster_query_uri, cluster_ingest_uri


eventhouse_id, kql_db_id, cluster_query_uri, cluster_ingest_uri = ensure_eventhouse_and_kql_db(
    workspace_id=workspace_id,
    eventhouse_name=fabric_eventhouse_name,
    kql_db_name=fabric_kql_db_name,
    access_token=access_token,
    target_folder_id=target_folder_id,
)

EVENTHOUSE_ID = eventhouse_id
KQL_DATABASE_ID = kql_db_id


# =========================
# KUSTO TOKEN + QUERY/MGMT HELPERS
# =========================

_kusto_token_cache = {
    "token": None,
    "scope": None,
    "expires_at": 0.0,
}


def get_kusto_token(cluster_url: str) -> str:
    """
    Get an access token for the Kusto cluster using the same SPN.
    """

    if not cluster_url:
        raise RuntimeError("Kusto cluster URL is empty.")

    parsed = urlparse(cluster_url)
    resource = f"{parsed.scheme}://{parsed.netloc}"
    scope = f"{resource}/.default"

    now = time.time()

    if (
        _kusto_token_cache["token"]
        and _kusto_token_cache["scope"] == scope
        and now < _kusto_token_cache["expires_at"]
    ):
        return _kusto_token_cache["token"]

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

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError("Unable to fetch SPN credentials from Key Vault for Kusto token.")

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

    _kusto_token_cache["token"] = token_data["access_token"]
    _kusto_token_cache["scope"] = scope
    _kusto_token_cache["expires_at"] = now + token_data.get("expires_in", 3600) - 60

    return _kusto_token_cache["token"]


def execute_kql_query(kql_query: str):
    """
    Execute a KQL query against the configured KQL database.
    """

    token = get_kusto_token(cluster_query_uri)

    query_url = f"{cluster_query_uri}/v1/rest/query"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body = {
        "db": fabric_kql_db_name,
        "csl": kql_query,
    }

    response = requests.post(
        query_url,
        headers=headers,
        json=body,
        timeout=120,
    )

    if response.status_code not in (200, 201):
        print(f"❗ Kusto query error: {response.status_code}")
        print(response.text[:3000])
        response.raise_for_status()

    return response.json()


def execute_kql_mgmt(kql_command: str):
    """
    Execute a KQL management command against the configured KQL database.
    """

    token = get_kusto_token(cluster_query_uri)

    mgmt_url = f"{cluster_query_uri}/v1/rest/mgmt"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body = {
        "db": fabric_kql_db_name,
        "csl": kql_command,
    }

    response = requests.post(
        mgmt_url,
        headers=headers,
        json=body,
        timeout=120,
    )

    if response.status_code not in (200, 201):
        print(f"❗ Kusto management error: {response.status_code}")
        print(response.text[:3000])
        response.raise_for_status()

    return response.json() if response.text and response.text.strip() else {}


# =========================
# KUSTO RESULT HELPER
# =========================

def _kusto_result_to_records(result):
    """
    Convert common Kusto/Fabric result shape into a list of dict rows.
    """

    if result is None:
        return []

    if hasattr(result, "to_dict"):
        try:
            return result.to_dict("records")
        except Exception:
            pass

    if isinstance(result, list):
        return result

    if not isinstance(result, dict):
        return []

    tables = result.get("Tables", [])

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


# =========================
# KUSTO: CREATE / VALIDATE SLIM RTI TABLE
# =========================

EXPECTED_RTI_COLUMNS = {
    "event_time",
    "opcua_node_id",
    "value",
    "quality",
}


def create_or_validate_slim_kql_table(table_name: str):
    """
    Create the slim OPC UA Eventhouse table.

    Slim table schema:
      event_time
      opcua_node_id
      value
      quality

    Static metadata such as tag, instrument_id, equipment_id, system_id,
    facility_id, and unit stays in silver_signal_master.
    """

    csl = f"""
.create-merge table {table_name} (
    event_time: datetime,
    opcua_node_id: string,
    value: real,
    quality: string
)
""".strip()

    execute_kql_mgmt(csl)

    schema_query = f"""
{table_name}
| getschema
| project ColumnName
"""

    schema_result = execute_kql_query(schema_query)
    schema_records = _kusto_result_to_records(schema_result)

    actual_columns = {
        str(row["ColumnName"])
        for row in schema_records
        if row.get("ColumnName")
    }

    missing_columns = sorted(EXPECTED_RTI_COLUMNS - actual_columns)
    extra_columns = sorted(actual_columns - EXPECTED_RTI_COLUMNS)

    if missing_columns:
        raise RuntimeError(
            f"Eventhouse table '{table_name}' is missing required slim RTI columns: "
            f"{missing_columns}"
        )

    if extra_columns:
        raise RuntimeError(
            f"Eventhouse table '{table_name}' has extra columns that do not belong "
            f"in the slim RTI telemetry model: {extra_columns}\n\n"
            "Expected only: event_time, opcua_node_id, value, quality.\n"
            "This usually means the table was created earlier with the old wide schema. "
            "For a clean test, use a new table name or delete/recreate the Eventhouse table."
        )

    print(f"✅ Slim Eventhouse table '{table_name}' is ready.")
    print("✅ Schema: event_time, opcua_node_id, value, quality")


create_or_validate_slim_kql_table(fabric_eventhouse_table)


# =========================
# Persist Eventhouse/KQL details back to shared settings
# =========================

def persist_006_settings():
    """
    Persist IDs and Kusto URIs to rti_demo_settings so later notebooks can reuse them.
    """

    if "settings_table_name" not in globals():
        print("⚠️ settings_table_name not found; skipping settings persistence.")
        return

    current_settings = dict(settings) if "settings" in globals() else {}

    current_settings.update({
        "eventhouse_id": eventhouse_id,
        "kql_database_id": kql_db_id,
        "cluster_query_uri": cluster_query_uri,
        "cluster_ingest_uri": cluster_ingest_uri or "",
    })

    updated_utc = datetime.now(timezone.utc).isoformat()

    settings_schema = StructType([
        StructField("setting_name", StringType(), False),
        StructField("setting_value", StringType(), True),
        StructField("updated_utc", StringType(), False),
    ])

    settings_rows = [
        {
            "setting_name": setting_name,
            "setting_value": "" if setting_value is None else str(setting_value),
            "updated_utc": updated_utc,
        }
        for setting_name, setting_value in current_settings.items()
    ]

    settings_out_df = spark.createDataFrame(
        settings_rows,
        schema=settings_schema,
    )

    (
        settings_out_df
        .withColumn("updated_utc", F.to_timestamp("updated_utc"))
        .write
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(settings_table_name)
    )

    print(f"✅ Persisted Eventhouse/KQL details to '{settings_table_name}'.")


persist_006_settings()


# ============================================================
# Eventhouse ingestion validation helper
# ============================================================

def validate_eventhouse_ingestion_since(
    eventhouse_table_name: str,
    simulation_start_utc,
    min_rows: int = 1,
    poll_timeout_seconds: int = 180,
    poll_interval_seconds: int = 15,
) -> dict:
    """
    Validate that rows generated by this notebook run arrived in Eventhouse.

    Uses Kusto ingestion_time(), not a custom run_id column.
    """

    if isinstance(simulation_start_utc, datetime):
        if simulation_start_utc.tzinfo is None:
            simulation_start_utc = simulation_start_utc.replace(tzinfo=timezone.utc)

        simulation_start_literal = (
            simulation_start_utc
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )

    else:
        simulation_start_literal = str(simulation_start_utc)

    validation_query = f"""
{eventhouse_table_name}
| where ingestion_time() >= datetime({simulation_start_literal})
| summarize
    rows = count(),
    first_event = min(event_time),
    last_event = max(event_time),
    latest_ingestion = max(ingestion_time())
"""

    deadline = time.time() + poll_timeout_seconds
    last_record = None

    while time.time() <= deadline:
        result = execute_kql_query(validation_query)
        records = _kusto_result_to_records(result)

        if records:
            last_record = records[0]
            rows = int(last_record.get("rows") or 0)

            if rows >= min_rows:
                print("✅ Eventhouse ingestion verified.")
                print(f"   New rows since simulation start: {rows}")
                print(f"   First event_time: {last_record.get('first_event')}")
                print(f"   Last event_time: {last_record.get('last_event')}")
                print(f"   Latest ingestion_time: {last_record.get('latest_ingestion')}")
                return last_record

        print(f"⏳ Waiting for Eventhouse ingestion ({poll_interval_seconds}s)...")
        time.sleep(poll_interval_seconds)

    diagnostic_query = f"""
{eventhouse_table_name}
| summarize
    total_rows = count(),
    latest_event = max(event_time),
    latest_ingestion = max(ingestion_time())
"""

    diagnostic_result = execute_kql_query(diagnostic_query)
    diagnostic_records = _kusto_result_to_records(diagnostic_result)
    diagnostic_record = diagnostic_records[0] if diagnostic_records else {}

    raise RuntimeError(
        "Events were generated and sent, but no new rows arrived in the "
        f"Eventhouse table '{eventhouse_table_name}' after the simulation started.\n\n"
        "Most likely cause:\n"
        "- The Eventstream Eventhouse destination is inactive, unpublished, "
        "or not writing to the expected table.\n\n"
        "Check in Fabric Eventstream:\n"
        "- Custom Endpoint is Active\n"
        "- Eventhouse destination is Active\n"
        f"- Destination table is '{eventhouse_table_name}'\n\n"
        f"Simulation start UTC: {simulation_start_literal}\n"
        f"Last validation record: {last_record}\n"
        f"Table diagnostic: {diagnostic_record}"
    )


print("\n✅ CELL 1 complete – Eventhouse, KQL DB, slim RTI table, and validation helpers are ready.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 2
# Eventstream Custom Endpoint, slim OPC UA simulation, ingestion validation
# =========================

# Run CELL 1 first so these exist:
# workspace_id, FABRIC_BASE_URL, access_token, fabric_get_items,
# eventhouse_id, kql_db_id, cluster_query_uri, fabric_kql_db_name,
# fabric_eventhouse_table, get_kusto_token, execute_kql_query,
# validate_eventhouse_ingestion_since

import base64
import json
import time
import random
import hmac
import hashlib
import requests

from datetime import datetime, timezone
from urllib.parse import quote_plus
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType


SOURCE_NAME = "OPCUA_CustomEndpoint"
STREAM_NAME = "OPCUA_DefaultStream"
DESTINATION_NAME = "Eventhouse"


# ============================================================
# 0) Validate required globals from previous cells
# ============================================================

required_globals = [
    "workspace_id",
    "target_folder_id",
    "FABRIC_BASE_URL",
    "access_token",
    "fabric_get_items",
    "fabric_eventstream_name",
    "fabric_kql_db_name",
    "fabric_eventhouse_table",
    "kql_db_id",
    "silver_signal_master_table",
    "validate_eventhouse_ingestion_since",
]

missing_globals = [
    name
    for name in required_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_globals:
    raise RuntimeError(
        "Missing required values. Run the 006 config cell and CELL 1 first. "
        f"Missing: {missing_globals}"
    )


# ============================================================
# 1) Eventstream control-plane helpers
# ============================================================

def decode_base64_json(payload: str) -> dict:
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.b64decode(padded).decode("utf-8"))


def encode_base64_json(obj: dict) -> str:
    return base64.b64encode(
        json.dumps(obj, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def wait_for_fabric_lro(operation_url: str, max_wait_seconds: int = 300, poll_seconds: int = 5) -> dict:
    """
    Poll Fabric long-running operation URL until completion.
    """

    deadline = time.time() + max_wait_seconds

    while time.time() <= deadline:
        response = requests.get(
            operation_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=60,
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f"LRO poll failed: {response.status_code} | {response.text[:3000]}"
            )

        result = response.json()
        status = result.get("status", "Unknown")

        if status in ("Succeeded", "Completed"):
            print(f"✅ LRO completed: {status}")
            return result

        if status in ("Failed", "Cancelled"):
            raise RuntimeError(f"LRO {status}: {json.dumps(result, indent=2)}")

        print(f"⏳ LRO status: {status}. Polling again in {poll_seconds}s...")
        time.sleep(poll_seconds)

    raise TimeoutError(f"LRO timed out after {max_wait_seconds}s: {operation_url}")


def get_eventstream_by_name(workspace_id: str, name: str, token: str, target_folder_id: str):
    """
    Return Eventstream ID if it exists in target_folder_id.

    If the same name exists outside the target folder, return that ID only if it
    was created by this notebook flow and no in-folder Eventstream exists yet.
    This avoids blocking when Fabric creates Eventstreams in root even though
    folderId was requested.
    """

    items = fabric_get_items(workspace_id, token, "Eventstream")

    matches = [
        item
        for item in items
        if item.get("displayName") == name
    ]

    if not matches:
        print(f"ℹ️ No Eventstream named '{name}' found.")
        return None

    matches_in_folder = [
        item
        for item in matches
        if item.get("folderId") == target_folder_id
    ]

    if matches_in_folder:
        item = matches_in_folder[0]
        print(f"♻️ Eventstream '{name}' found in target folder – ID: {item['id']}")
        return item["id"]

    # Eventstream create may place the item at workspace root.
    # Do not fail here; reuse the same-name Eventstream so the run can continue.
    item = matches[0]
    print(
        f"⚠️ Eventstream '{name}' exists but folderId is {item.get('folderId')}, "
        f"not target_folder_id {target_folder_id}."
    )
    print(
        "⚠️ Reusing it because Fabric appears to have created the Eventstream "
        "outside the folder."
    )
    print(f"♻️ Eventstream ID: {item['id']}")

    return item["id"]


def create_eventstream(workspace_id: str, name: str, token: str, target_folder_id: str) -> str:
    """
    Create a new Eventstream.

    First tries the generic Fabric item create API with folderId.
    If Fabric does not return an ID, polls items by displayName.
    """

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "displayName": name,
        "description": "Eventstream for slim OPC UA RTI telemetry",
        "type": "Eventstream",
        "folderId": target_folder_id,
    }

    response = requests.post(
        url,
        headers=headers,
        json=body,
        timeout=120,
    )

    if response.status_code == 400 and "ItemDisplayNameNotAvailableYet" in response.text:
        print("⏳ Display name not available yet. Waiting 30 seconds and retrying create...")
        time.sleep(30)

        response = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=120,
        )

    if response.status_code == 202:
        operation_url = (
            response.headers.get("Location")
            or response.headers.get("Operation-Location")
            or response.headers.get("operation-location")
        )

        if operation_url:
            wait_for_fabric_lro(operation_url)

    elif response.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to create Eventstream via generic item API: "
            f"{response.status_code} | {response.text}"
        )

    if response.text and response.text.strip():
        try:
            result = response.json()
            if "id" in result:
                print(f"✅ Created Eventstream '{name}' – ID: {result['id']}")
                return result["id"]
        except Exception:
            pass

    print("ℹ️ Create Eventstream returned no ID body; polling items to resolve ID...")

    for attempt in range(1, 31):
        eventstream_id = get_eventstream_by_name(
            workspace_id,
            name,
            token,
            target_folder_id,
        )

        if eventstream_id:
            print(f"✅ Resolved Eventstream ID via polling: {eventstream_id}")
            return eventstream_id

        print(f"⏳ Eventstream '{name}' not visible yet ({attempt}/30)...")
        time.sleep(5)

    raise RuntimeError(f"Could not resolve Eventstream ID for '{name}' after creation.")

# ============================================================
# 2) Get and update Eventstream definition
# ============================================================

def get_eventstream_definition(workspace_id: str, eventstream_id: str, token: str):
    """
    Get Eventstream definition and return:
    - eventstream_json dict
    - existing .platform part if present
    """

    url = (
        f"{FABRIC_BASE_URL}/workspaces/{workspace_id}"
        f"/eventstreams/{eventstream_id}/getDefinition"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json={},
        timeout=120,
    )

    if response.status_code == 202:
        operation_url = (
            response.headers.get("Location")
            or response.headers.get("Operation-Location")
            or response.headers.get("operation-location")
        )

        if not operation_url:
            raise RuntimeError("getDefinition returned 202 but no LRO Location header.")

        wait_for_fabric_lro(operation_url)

        result_url = operation_url.rstrip("/") + "/result"
        response = requests.get(
            result_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )

    response.raise_for_status()

    definition_parts = response.json()["definition"]["parts"]

    eventstream_json = None
    platform_part = None

    for part in definition_parts:
        if part["path"] == "eventstream.json":
            eventstream_json = decode_base64_json(part["payload"])
        elif part["path"] == ".platform":
            platform_part = part

    if eventstream_json is None:
        raise RuntimeError("eventstream.json not found in Eventstream definition.")

    return eventstream_json, platform_part


def mutate_definition_for_slim_opcua(evt_def: dict) -> dict:
    """
    Ensure:
    - CustomEndpoint source exists
    - DefaultStream exists and reads from source
    - Eventhouse destination writes slim JSON to fabric_eventhouse_table
    """

    sources = evt_def.get("sources", [])
    streams = evt_def.get("streams", [])
    destinations = evt_def.get("destinations", [])

    # Custom Endpoint source
    source = next((s for s in sources if s.get("name") == SOURCE_NAME), None)

    if not source:
        sources.append({
            "name": SOURCE_NAME,
            "type": "CustomEndpoint",
            "properties": {
                "authenticationMode": "Sas",
                "protocol": "EventHub",
            },
        })
        print("✅ Added CustomEndpoint source.")
    else:
        source["type"] = "CustomEndpoint"
        source["properties"] = {
            "authenticationMode": "Sas",
            "protocol": "EventHub",
        }
        print("♻️ CustomEndpoint source exists; properties enforced.")

    # Default stream
    stream = next((s for s in streams if s.get("name") == STREAM_NAME), None)

    if not stream:
        streams.append({
            "name": STREAM_NAME,
            "type": "DefaultStream",
            "properties": {},
            "inputNodes": [
                {"name": SOURCE_NAME},
            ],
        })
        print("✅ Added DefaultStream wired from CustomEndpoint.")
    else:
        stream["type"] = "DefaultStream"
        stream.setdefault("properties", {})
        stream.setdefault("inputNodes", [])

        if not any(node.get("name") == SOURCE_NAME for node in stream["inputNodes"]):
            stream["inputNodes"].append({"name": SOURCE_NAME})
            print("🔧 Wired DefaultStream to CustomEndpoint.")

        print("♻️ DefaultStream exists; wiring enforced.")

    # Eventhouse destination
    destination = next((d for d in destinations if d.get("name") == DESTINATION_NAME), None)

    destination_obj = {
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
                    "encoding": "UTF8",
                },
            },
        },
        "inputNodes": [
            {"name": STREAM_NAME},
        ],
        "inputSchemas": [],
    }

    if not destination:
        destinations.append(destination_obj)
        print("✅ Added Eventhouse destination for slim RTI table.")
    else:
        destination.clear()
        destination.update(destination_obj)
        print("♻️ Eventhouse destination exists; destination settings enforced.")

    evt_def["sources"] = sources
    evt_def["streams"] = streams
    evt_def["destinations"] = destinations

    return evt_def


def update_eventstream_definition(workspace_id: str, eventstream_id: str, display_name: str, token: str):
    """
    Update Eventstream definition with CustomEndpoint + DefaultStream + Eventhouse destination.
    """

    evt_def, platform_part = get_eventstream_definition(
        workspace_id,
        eventstream_id,
        token,
    )

    print(f"ℹ️ Existing sources: {len(evt_def.get('sources', []))}")
    print(f"ℹ️ Existing streams: {len(evt_def.get('streams', []))}")
    print(f"ℹ️ Existing destinations: {len(evt_def.get('destinations', []))}")

    evt_def_mutated = mutate_definition_for_slim_opcua(evt_def)

    eventstream_part = {
        "path": "eventstream.json",
        "payload": encode_base64_json(evt_def_mutated),
        "payloadType": "InlineBase64",
    }

    parts = [eventstream_part]

    if platform_part:
        parts.append(platform_part)

    body = {
        "definition": {
            "parts": parts,
        },
    }

    url = (
        f"{FABRIC_BASE_URL}/workspaces/{workspace_id}"
        f"/eventstreams/{eventstream_id}/updateDefinition?updateMetadata=True"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=body,
        timeout=120,
    )

    if response.status_code == 202:
        operation_url = (
            response.headers.get("Location")
            or response.headers.get("Operation-Location")
            or response.headers.get("operation-location")
        )

        if operation_url:
            wait_for_fabric_lro(operation_url)

        print("✅ Eventstream definition update accepted and completed.")
        return

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to update Eventstream definition: "
            f"{response.status_code} | {response.text}"
        )

    print("✅ Eventstream definition updated.")


# Ensure Eventstream exists in target folder, then update definition
eventstream_id = get_eventstream_by_name(
    workspace_id,
    fabric_eventstream_name,
    access_token,
    target_folder_id,
)

if not eventstream_id:
    eventstream_id = create_eventstream(
        workspace_id,
        fabric_eventstream_name,
        access_token,
        target_folder_id,
    )

print("\n────────────────────────────────────────")
update_eventstream_definition(
    workspace_id,
    eventstream_id,
    fabric_eventstream_name,
    access_token,
)
print("⏳ Waiting 10 seconds before reading topology and sending events...")
time.sleep(10)
print(f"➡️ Using Eventstream ID: {eventstream_id}")
print("────────────────────────────────────────\n")


# ============================================================
# 3) Persist Eventstream ID back to settings
# ============================================================

def persist_eventstream_settings():
    if "settings_table_name" not in globals():
        print("⚠️ settings_table_name not found; skipping Eventstream settings persistence.")
        return

    current_settings = dict(settings) if "settings" in globals() else {}

    current_settings.update({
        "eventstream_id": eventstream_id,
    })

    updated_utc = datetime.now(timezone.utc).isoformat()

    settings_schema = StructType([
        StructField("setting_name", StringType(), False),
        StructField("setting_value", StringType(), True),
        StructField("updated_utc", StringType(), False),
    ])

    settings_rows = [
        {
            "setting_name": setting_name,
            "setting_value": "" if setting_value is None else str(setting_value),
            "updated_utc": updated_utc,
        }
        for setting_name, setting_value in current_settings.items()
    ]

    settings_out_df = spark.createDataFrame(
        settings_rows,
        schema=settings_schema,
    )

    (
        settings_out_df
        .withColumn("updated_utc", F.to_timestamp("updated_utc"))
        .write
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(settings_table_name)
    )

    print(f"✅ Persisted Eventstream ID to '{settings_table_name}'.")


persist_eventstream_settings()


# ============================================================
# 4) Topology + Custom Endpoint connection
# ============================================================

def get_eventstream_topology(workspace_id: str, eventstream_id: str, token: str) -> dict:
    url = (
        f"{FABRIC_BASE_URL}/workspaces/{workspace_id}"
        f"/eventstreams/{eventstream_id}/topology"
    )

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )

    response.raise_for_status()

    topology = response.json()

    sources = topology.get("sources", [])
    streams = topology.get("streams", [])
    destinations = topology.get("destinations", [])

    print(f"📈 Topology: {len(sources)} sources, {len(streams)} streams, {len(destinations)} destinations.")

    return topology


def get_custom_endpoint_connection(
    workspace_id: str,
    eventstream_id: str,
    token: str,
    source_name: str,
) -> dict:
    topology = get_eventstream_topology(
        workspace_id,
        eventstream_id,
        token,
    )

    sources = topology.get("sources", [])

    source = next(
        (s for s in sources if s.get("name") == source_name),
        None,
    )

    if not source:
        raise RuntimeError(f"CustomEndpoint source '{source_name}' not found in topology.")

    source_id = source["id"]

    url = (
        f"{FABRIC_BASE_URL}/workspaces/{workspace_id}"
        f"/eventstreams/{eventstream_id}/sources/{source_id}/connection"
    )

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )

    response.raise_for_status()

    info = response.json()

    print(
        f"🔌 Custom Endpoint connection resolved – "
        f"namespace: {info['fullyQualifiedNamespace']}, "
        f"eventHub: {info['eventHubName']}"
    )

    return {
        "endpoint": f"sb://{info['fullyQualifiedNamespace']}/",
        "entityPath": info["eventHubName"],
        "connectionString": info["accessKeys"]["primaryConnectionString"],
    }


custom_ep_info = get_custom_endpoint_connection(
    workspace_id,
    eventstream_id,
    access_token,
    SOURCE_NAME,
)

print("\n✅ Custom Endpoint connection ready.\n")


# ============================================================
# 5) HTTP / SAS helpers
# ============================================================

def parse_connection_string(connection_string: str) -> dict:
    parsed = {}

    for item in connection_string.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            parsed[key] = value

    return parsed


def build_sas_token(resource_uri: str, key_name: str, key_value: str, expiry_secs: int = 3600) -> str:
    expiry = int(time.time()) + expiry_secs
    encoded_resource_uri = quote_plus(resource_uri)

    string_to_sign = f"{encoded_resource_uri}\n{expiry}".encode("utf-8")

    signature = base64.b64encode(
        hmac.new(
            key_value.encode("utf-8"),
            string_to_sign,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    return (
        f"SharedAccessSignature sr={encoded_resource_uri}"
        f"&sig={quote_plus(signature)}"
        f"&se={expiry}"
        f"&skn={key_name}"
    )


def build_custom_endpoint_http_target(custom_endpoint_info: dict) -> dict:
    connection_string = parse_connection_string(custom_endpoint_info["connectionString"])

    endpoint_https = connection_string["Endpoint"].replace("sb://", "https://").rstrip("/")
    entity_path = custom_endpoint_info["entityPath"]

    resource_uri = f"{endpoint_https}/{entity_path}"
    post_url = f"{resource_uri}/messages?timeout=60&api-version=2014-01"

    return {
        "post_url": post_url,
        "resource_uri": resource_uri,
        "shared_access_key_name": connection_string["SharedAccessKeyName"],
        "shared_access_key": connection_string["SharedAccessKey"],
    }


def send_single_event_http(
    http_target: dict,
    event: dict,
    max_retries: int = 3,
    retry_delay_sec: float = 1.0,
):
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            sas_token = build_sas_token(
                resource_uri=http_target["resource_uri"],
                key_name=http_target["shared_access_key_name"],
                key_value=http_target["shared_access_key"],
            )

            headers = {
                "Authorization": sas_token,
                "Content-Type": "application/json",
            }

            response = requests.post(
                http_target["post_url"],
                headers=headers,
                data=json.dumps(event),
                timeout=30,
            )

            if response.status_code in (200, 201):
                return

            last_error = RuntimeError(
                f"Send failed: HTTP {response.status_code} | {response.text[:500]}"
            )

        except Exception as ex:
            last_error = ex

        if attempt < max_retries:
            time.sleep(retry_delay_sec)

    raise last_error if last_error else RuntimeError("Unknown send failure.")


# ============================================================
# 6) Slim OPC UA telemetry simulation from silver_signal_master
# ============================================================

signal_master_df = spark.read.table(silver_signal_master_table)

required_signal_cols = {
    "opcua_node_id",
}

missing_signal_cols = required_signal_cols - set(signal_master_df.columns)

if missing_signal_cols:
    raise RuntimeError(
        f"'{silver_signal_master_table}' is missing required columns: {sorted(missing_signal_cols)}"
    )

if "is_active" in signal_master_df.columns:
    signal_master_df = signal_master_df.filter(F.col("is_active") == True)

sim_select_cols = [
    F.col("opcua_node_id"),
]

if "signal_type" in signal_master_df.columns:
    sim_select_cols.append(F.col("signal_type"))
else:
    sim_select_cols.append(F.lit("generic").alias("signal_type"))

sim_keys_df = (
    signal_master_df
    .select(*sim_select_cols)
    .where(F.col("opcua_node_id").isNotNull())
    .dropDuplicates(["opcua_node_id"])
)

sim_keys = [
    {
        "opcua_node_id": row["opcua_node_id"],
        "signal_type": row["signal_type"],
    }
    for row in sim_keys_df.collect()
]

if not sim_keys:
    raise RuntimeError(
        f"No active OPC UA signals found in '{silver_signal_master_table}'."
    )

print(f"✅ Telemetry will be generated for {len(sim_keys)} signals from '{silver_signal_master_table}'.")


def generate_slim_opcua_event(opcua_node_id: str, signal_type: str) -> dict:
    quality = random.choices(
        ["GOOD", "UNCERTAIN", "BAD"],
        [0.85, 0.10, 0.05],
    )[0]

    event_time = (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    signal_type_l = (signal_type or "").lower()

    if "temp" in signal_type_l or "temperature" in signal_type_l:
        value = random.uniform(40, 95)
    elif "pressure" in signal_type_l:
        value = random.uniform(5, 80)
    elif "vibration" in signal_type_l:
        value = random.uniform(0, 15)
    elif "power" in signal_type_l:
        value = random.uniform(100, 2500)
    elif "speed" in signal_type_l:
        value = random.uniform(500, 3600)
    else:
        value = random.uniform(0, 100)

    return {
        "event_time": event_time,
        "opcua_node_id": opcua_node_id,
        "value": round(float(value), 3),
        "quality": quality,
    }


http_target = build_custom_endpoint_http_target(custom_ep_info)

SIM_DURATION_SECS = 600
MAX_ITERATIONS = 10
SLEEP_BETWEEN_ITERATIONS_SEC = 5

simulation_start_utc = datetime.now(timezone.utc)

print("🟢 Starting slim OPC UA telemetry simulation...\n")
print(f"Simulation start UTC: {simulation_start_utc.isoformat()}")

start_time = time.time()
iteration = 0
sent_count = 0
failed_count = 0

while (time.time() - start_time) < SIM_DURATION_SECS and iteration < MAX_ITERATIONS:
    iteration += 1
    elapsed = int(time.time() - start_time)

    print(
        f"📡 Simulation iteration {iteration} of {MAX_ITERATIONS} "
        f"(elapsed: {elapsed}s, active signals: {len(sim_keys)}, "
        f"sent: {sent_count}, failed: {failed_count})"
    )

    for signal in sim_keys:
        event = generate_slim_opcua_event(
            opcua_node_id=signal["opcua_node_id"],
            signal_type=signal["signal_type"],
        )

        try:
            send_single_event_http(http_target, event)
            sent_count += 1
        except Exception as ex:
            failed_count += 1
            print(f"❗ Event send failed for {event.get('opcua_node_id')}: {ex}")

    time.sleep(SLEEP_BETWEEN_ITERATIONS_SEC)

print(
    f"\n✅ Simulation completed – ran for {int(time.time() - start_time)} seconds. "
    f"Sent: {sent_count}. Failed: {failed_count}."
)

if sent_count == 0:
    raise RuntimeError("No events were sent. Check Custom Endpoint connection and signal source data.")

print("────────────────────────────────────────\n")


# ============================================================
# 7) Validate new Eventhouse ingestion from this run
# ============================================================

validation_record = validate_eventhouse_ingestion_since(
    eventhouse_table_name=fabric_eventhouse_table,
    simulation_start_utc=simulation_start_utc,
    min_rows=1,
    poll_timeout_seconds=180,
    poll_interval_seconds=15,
)

print("\n🎉 SUCCESS — New slim OPC UA events were ingested into Eventhouse.")
print("══════════════════════════════════════════════════════════")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
