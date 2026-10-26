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

# # 01 – Create Lakehouse and Seed STID (SPN + Key Vault + REST) — Self-Contained
# 
# Self-contained variant of `RTI_001_create_lakehouse_shortcut`. It removes the ADLS
# Gen2 dependency (no cloud connection, no shortcut, no workspace identity) and instead
# **seeds the STID CSVs directly into the new Lakehouse** at `Files/bronze/stid/`.
# 
# It will:
# 
# 1. Use a **Service Principal (SPN)** with secrets stored in **Azure Key Vault**.
# 2. Use **Fabric REST APIs** to create (or reuse) a **Lakehouse** in the current workspace.
# 3. Write the four **STID** seed files (facilities/systems/equipment/instruments) into
#    `Files/bronze/stid/` via the explicit OneLake path — no external storage required.
# 4. Use **NotebookUtils** for Key Vault access and OneLake file writes.
# 5. Create/reuse the independent **HydroOperationalStore** SQL Database and initialize
#    its five operational tables without demo data; persist its verified item ID.
# 
# After this notebook, `RTI_003_ingest_transform_medallion_SelfContained` reads from:
# 
# ```text
# Files/bronze/stid/*.csv
# ```
# 
# inside the newly created Lakehouse.
#
# As Stage 1, persist shared settings and rebind child notebooks before Stage 2
# (`RTI_Orchestrator_Setup`) attaches the root default Lakehouse. Its children use
# `useRootDefaultLakehouse`; canonical notebook names remain unchanged.
# Ontology creation/binding is v2-only (live integer `properties.generation == 2`).
# Existing v1 is rejected, not migrated/deleted. `_V9`, REST `/v1`, and ADLS Gen2
# are not ontology generation indicators. Agent provisioning is always attempted;
# there are no agent mode or skip flags. Data Agent publication verifies source identity.
# Operations Agent provisions the playbook, Teams destination and email pipeline.
# Real failures abort setup; NB09 alone reports the exact documented temporary
# unsupported-Ontology-v2 product response without claiming runtime readiness.
# 
# ---
# 
# ## ✅ Prerequisites — before running `Pipe_Setup`
# 
# These are **one-time** setup steps that give the executing **Service Principal (SPN)**
# the access it needs. (No ADLS Gen2, shortcut, cloud connection, or Workspace Identity
# is required by this self-contained variant.)
# 
# 1. **Private endpoint to Azure Key Vault** — required **only if the Key Vault is not
#    publicly accessible**. Create a **Managed private endpoint** to the vault under
#    **Workspace settings → Outbound networking → Managed private endpoints**, then approve
#    it on the Key Vault's *Networking → Private endpoint connections*. Skip if the vault
#    allows public network access.
# 2. **SPN access to the Key Vault** — the SPN must be able to **Get** the secrets
#    (`tenantid`, `clientid`, `clientsecret`), e.g. via the **Key Vault Secrets User** role
#    or an access policy granting *Secret Get*.
# 3. **SPN Contributor on the Fabric workspace** — grant the SPN at least **Contributor**
#    (Workspace → Manage access) so it can create the Lakehouse and downstream items.


# CELL ********************

import json
import requests
import notebookutils
from urllib.parse import quote

print("Notebook environment is ready (using notebookutils).")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# PARAMETERS CELL ********************

# --------------------------------------------
# PARAMETERS  (tagged cell — RAW knobs only; derived names are in the NEXT cell)
# --------------------------------------------
# Two groups below:
#   • INJECTED by Pipe_Setup (via the orchestrator's nb01_args) — blank defaults so the
#     pipeline is the single source of truth; the next cell fails fast if any is missing.
#   • STATIC config — NOT injected; keeps real defaults (edit here to change it).

from datetime import datetime, timezone

# Shared settings table name (later notebooks read this table instead of redefining common settings).
settings_table_name = "rti_demo_settings"

# THE one environment lever (INJECTED by Pipe_Setup). The workspace folder and every artifact
# name derive from it in the next cell, AFTER the injected override is applied.
env_suffix = ""

# Workspace that will host the demo items. (INJECTED by Pipe_Setup.)
workspace_id = ""

lakehouse_description = "Lakehouse for Fabric IQ mock dataset (STID, SAP, OPC UA, SOLV, P&ID, Documents)."

# Bronze layer root inside the Lakehouse where STID seed files are written (NOT injected).
bronze_root = "Files/bronze"

# Azure Key Vault (URI + secret NAMES only, never values). (INJECTED by Pipe_Setup.)
key_vault_uri = ""
key_vault_tenant_id_secret_name = ""
key_vault_client_id_secret_name = ""
key_vault_client_secret_name = ""

# Eventhouse landing table (a table name, not a versioned artifact). (STATIC.)
eventhouse_table_name = "OPCUAEvents"

# Operations Agent Teams inputs. (INJECTED by Pipe_Setup.)
ops_agent_run_as_user = ""      # UPN the agent runs as; blank => the deploying user (optional)
ops_agent_teams_team_id = ""
ops_agent_teams_channel_id = ""
alert_email_to = ""             # Required recipient for Pipe_SendEmailAlert
# Whether to copy the playbook; false leaves generation to the portal. (STATIC, NOT injected.)
ops_agent_copy_playbook = "true"

# Structured table names used by the ontology.
silver_facilities_table = "silver_facilities"
silver_systems_table = "silver_systems"
silver_equipment_table = "silver_equipment"
silver_instruments_table = "silver_instruments"
silver_signal_master_table = "silver_signal_master"

# Silver table prefix (NB05) and the ontology entity the time-series binding attaches to (NB04/NB06).
silver_table_prefix = "silver_"
signal_master_entity_name = "signal_master"

# OPCUAEvents column mapping consumed by the Eventhouse time-series binding (NB06).
timeseries_timestamp_column = "event_time"
timeseries_key_column = "opcua_node_id"
timeseries_value_column = "value"
timeseries_quality_column = "quality"

# Eventstream component names (NB02/NB07).
eventstream_source_name = "OPCUA_CustomEndpoint"
eventstream_stream_name = "OPCUA_DefaultStream"
eventstream_destination_name = "Eventhouse"

# Alert Data Pipeline description (its name is derived in the next cell).
alert_pipeline_description = "This will be triggered from Ops Agent!"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --------------------------------------------
# DERIVED NAMES  (computed AFTER parameter injection so overriding env_suffix flows through)
# --------------------------------------------
# Every versioned artifact name derives from env_suffix here — nothing is edited by hand.
# The KQL database reuses the Eventhouse name (Fabric names an Eventhouse's default KQL DB the same way).

# Fail fast if the injected parameters (from Pipe_Setup via the orchestrator's nb01_args) are
# missing. Only these are injected; the STATIC config above keeps its own defaults.
# ops_agent_run_as_user is an optional delegated-identity guard.
_required_injected = {
    "env_suffix": env_suffix,
    "workspace_id": workspace_id,
    "key_vault_uri": key_vault_uri,
    "key_vault_tenant_id_secret_name": key_vault_tenant_id_secret_name,
    "key_vault_client_id_secret_name": key_vault_client_id_secret_name,
    "key_vault_client_secret_name": key_vault_client_secret_name,
    "ops_agent_teams_team_id": ops_agent_teams_team_id,
    "ops_agent_teams_channel_id": ops_agent_teams_channel_id,
    "alert_email_to": alert_email_to,
}
_missing = [name for name, value in _required_injected.items() if not str(value).strip()]
if _missing:
    raise ValueError(
        "Missing required parameter(s): " + ", ".join(_missing) +
        ". These are injected by the Pipe_Setup pipeline (orchestrator nb01_args). "
        "Run via Pipe_Setup, or fill them in the parameters cell for a standalone run."
    )

lakehouse_name = f"Energy_IQ_LakehouseRTI_{env_suffix}"
workspace_folder_path = f"RTI_DEMO_{env_suffix}"  # Fabric workspace folder, not a Lakehouse path
ontology_name = f"RTI_Demo_Ontology_{env_suffix}"
eventhouse_name = f"RTI_Demo_Eventhouse_{env_suffix}"
kql_database_name = f"RTI_Demo_Eventhouse_{env_suffix}"
eventstream_name = f"RTI_Demo_Eventstream_{env_suffix}"
data_agent_name = f"RTI_Demo_Agent_{env_suffix}"
dashboard_name = f"RTI_Demo_OPCUA_TelemetryStats_{env_suffix}"
ops_agent_name = f"RTI_Demo_OpsAgent_{env_suffix}"

# Data Pipeline names — NOT versioned (one pipeline per workspace, no env_suffix).
alert_pipeline_name = "Pipe_SendEmailAlert"   # NB10 alert pipeline
setup_pipeline_name = "Pipe_Setup"            # orchestrated setup (NB01–06, 08–10)
stream_pipeline_name = "Pipe_Stream"          # on-demand OPC UA stream (NB07)


def build_rti_demo_settings_rows(extra_settings: dict | None = None) -> list:
    """
    Build settings rows for the shared rti_demo_settings table.

    Do not store secrets here.
    IDs created later can be added through extra_settings.
    """

    updated_utc = datetime.now(timezone.utc).isoformat()

    settings = {
        "settings_table_name": settings_table_name,
        "env_suffix": env_suffix,

        "workspace_id": workspace_id,
        "workspace_folder_path": workspace_folder_path,

        "lakehouse_name": lakehouse_name,
        "lakehouse_description": lakehouse_description,

        "bronze_root": bronze_root,

        # Key Vault URI + secret NAMES (never secret values) so NB002–010 authenticate the SPN.
        "key_vault_uri": key_vault_uri,
        "key_vault_tenant_id_secret": key_vault_tenant_id_secret_name,
        "key_vault_client_id_secret": key_vault_client_id_secret_name,
        "key_vault_client_secret_secret": key_vault_client_secret_name,

        "ontology_name": ontology_name,
        "eventhouse_name": eventhouse_name,
        "kql_database_name": kql_database_name,
        "eventstream_name": eventstream_name,
        "eventhouse_table_name": eventhouse_table_name,
        "data_agent_name": data_agent_name,
        "dashboard_name": dashboard_name,
        "ops_agent_name": ops_agent_name,

        # Canonical fabric_* aliases so downstream first_setting() primary keys
        # resolve straight from NB01 (no fallback dependency, no name drift).
        "fabric_ontology_name": ontology_name,
        "fabric_eventhouse_name": eventhouse_name,
        "fabric_kql_db_name": kql_database_name,
        "fabric_eventhouse_table": eventhouse_table_name,

        # Operations Agent inputs; not evidence of runtime readiness.
        "ops_agent_run_as_user": ops_agent_run_as_user,
        "ops_agent_teams_team_id": ops_agent_teams_team_id,
        "ops_agent_teams_channel_id": ops_agent_teams_channel_id,
        "alert_email_to": alert_email_to,
        "ops_agent_copy_playbook": ops_agent_copy_playbook,

        "silver_facilities_table": silver_facilities_table,
        "silver_systems_table": silver_systems_table,
        "silver_equipment_table": silver_equipment_table,
        "silver_instruments_table": silver_instruments_table,
        "silver_signal_master_table": silver_signal_master_table,

        # Data-model / structural parameters (previously hardcoded in NB04/NB05/NB06).
        "silver_table_prefix": silver_table_prefix,
        "signal_master_entity_name": signal_master_entity_name,
        "timeseries_timestamp_column": timeseries_timestamp_column,
        "timeseries_key_column": timeseries_key_column,
        "timeseries_value_column": timeseries_value_column,
        "timeseries_quality_column": timeseries_quality_column,

        # Eventstream component names (NB02/NB07) and alert Data Pipeline (NB10).
        "eventstream_source_name": eventstream_source_name,
        "eventstream_stream_name": eventstream_stream_name,
        "eventstream_destination_name": eventstream_destination_name,
        "alert_pipeline_name": alert_pipeline_name,
        "alert_pipeline_description": alert_pipeline_description,
        "setup_pipeline_name": setup_pipeline_name,
        "stream_pipeline_name": stream_pipeline_name,
    }

    if extra_settings:
        settings.update(extra_settings)

    rows = []

    for setting_name, setting_value in settings.items():
        rows.append({
            "setting_name": setting_name,
            "setting_value": "" if setting_value is None else str(setting_value),
            "updated_utc": updated_utc,
        })

    return rows


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --------------------------------------------
# SPN AUTH: Get Fabric API token via Client Credentials (from Key Vault)
# --------------------------------------------
# Key Vault URI and secret names come from the parameter cell above.

def get_spn_access_token(scope: str = "https://api.fabric.microsoft.com/.default") -> str:
    """
    Get an access token for Fabric REST APIs using a Service Principal (SPN),
    with secrets stored in Azure Key Vault.

    Requirements:
      - Azure Key Vault with three secrets:
          key_vault_tenant_id_secret_name   (tenant ID)
          key_vault_client_id_secret_name   (client ID / application ID)
          key_vault_client_secret_name      (client secret)
      - The identity executing this notebook (user / workspace identity / SPN)
        must have permissions to **get** secrets in the Key Vault.
    """

    # 1) Retrieve SPN values from Azure Key Vault via NotebookUtils
    tenant_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_tenant_id_secret_name)
    client_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_id_secret_name)
    client_secret = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_secret_name)

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError(
            "Failed to retrieve one or more SPN values from Key Vault. "
            "Check that the secrets exist and that this identity has 'get secret' permissions."
        )

    # 2) Call Entra ID token endpoint with client credentials
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": scope,
    }

    resp = requests.post(token_url, data=data)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to obtain SPN access token (HTTP {resp.status_code}): {resp.text}"
        )

    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("No access_token field in SPN token response.")

    print("✅ Successfully obtained SPN access token via Key Vault (NotebookUtils).")
    return token




# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# 🔐 Required: Grant the Service Principal (SPN) access to the Fabric Workspace
# This notebook authenticates to Fabric using a Service Principal (SPN) whose secrets are stored in Azure Key Vault.
# To allow the SPN to call the Fabric REST APIs (for example, to list items and create a Lakehouse), the SPN must be granted access to the workspace.
# 
# ✔️ Step 1 — Add the SPN to the Workspace
# 
# Open the Fabric workspace you are deploying into.
# Click Manage access (top‑right).
# Click Add → Add user or group.
# Enter the name of your App Registration (the SPN).
# Assign the following role:
# 
# Contributor (recommended) or Member
# 
# Click Add.
# 
# This self-contained variant does NOT require a Workspace Identity or an ADLS Gen2
# connection — the STID seed files are written directly into the new Lakehouse.


# CELL ********************

# --------------------------------------------
# Fabric REST helpers
# --------------------------------------------

FABRIC_BASE_URL = "https://api.fabric.microsoft.com/v1"


def list_workspace_folders(workspace_id: str, access_token: str) -> list:
    """
    List Fabric workspace folders.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    folders = []
    continuation_token = None

    while True:
        url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/folders?recursive=true"

        if continuation_token:
            url = f"{url}&continuationToken={quote(continuation_token, safe='')}"

        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to list folders in workspace {workspace_id} "
                f"(HTTP {resp.status_code}): {resp.text}"
            )

        body = resp.json()
        folders.extend(body.get("value", []))

        continuation_token = body.get("continuationToken")
        if not continuation_token:
            break

    return folders


def create_workspace_folder(
    workspace_id: str,
    folder_name: str,
    access_token: str,
    parent_folder_id: str | None = None,
) -> dict:
    """
    Create a Fabric workspace folder.
    """
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/folders"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "displayName": folder_name,
    }

    if parent_folder_id:
        payload["parentFolderId"] = parent_folder_id

    resp = requests.post(url, headers=headers, json=payload)

    if resp.status_code != 201:
        raise RuntimeError(
            f"Failed to create folder '{folder_name}' "
            f"(HTTP {resp.status_code}): {resp.text}"
        )

    return resp.json()


def get_or_create_workspace_folder_path(
    workspace_id: str,
    workspace_folder_path: str,
    access_token: str,
) -> str:
    """
    Resolve/create a Fabric workspace folder path like:
        joa/RTI_Demo

    Returns the folder ID for the final folder in the path.
    """
    folder_parts = [
        part.strip()
        for part in workspace_folder_path.replace("\\", "/").split("/")
        if part.strip()
    ]

    if not folder_parts:
        raise ValueError("workspace_folder_path cannot be empty.")

    parent_folder_id = None

    for folder_name in folder_parts:
        folders = list_workspace_folders(
            workspace_id=workspace_id,
            access_token=access_token,
        )

        matching_folder = next(
            (
                folder
                for folder in folders
                if folder.get("displayName") == folder_name
                and folder.get("parentFolderId") == parent_folder_id
            ),
            None,
        )

        if matching_folder:
            parent_folder_id = matching_folder["id"]
            print(f"♻️ Reusing folder '{folder_name}' with ID: {parent_folder_id}")
        else:
            created_folder = create_workspace_folder(
                workspace_id=workspace_id,
                folder_name=folder_name,
                access_token=access_token,
                parent_folder_id=parent_folder_id,
            )
            parent_folder_id = created_folder["id"]
            print(f"✅ Created folder '{folder_name}' with ID: {parent_folder_id}")

    return parent_folder_id


def get_lakehouse_id_by_name(workspace_id: str, lakehouse_name: str, access_token: str):
    """
    Look up a Lakehouse by display name in the given workspace.
    Returns its ID if found, otherwise None.
    """
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to list items in workspace {workspace_id} "
            f"(HTTP {resp.status_code}): {resp.text}"
        )

    body = resp.json()
    items = body.get("value") if isinstance(body, dict) and "value" in body else body

    if not isinstance(items, list):
        raise RuntimeError(
            "Unexpected response format when listing items: " + json.dumps(body, indent=2)
        )

    for item in items:
        display_name = item.get("displayName") or item.get("name")
        item_type = item.get("type")
        if display_name == lakehouse_name and item_type == "Lakehouse":
            lakehouse_id = item.get("id") or item.get("itemId")
            print(f"ℹ️ Found existing Lakehouse '{lakehouse_name}' with ID: {lakehouse_id}")
            return lakehouse_id

    print(f"ℹ️ Lakehouse '{lakehouse_name}' not found in workspace {workspace_id}.")
    return None


def create_lakehouse(
    workspace_id: str,
    lakehouse_name: str,
    lakehouse_description: str,
    access_token: str,
    target_folder_id: str,
) -> str:
    """
    Create a Lakehouse in the specified workspace folder using Fabric REST.
    Returns the Lakehouse ID.
    """
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "displayName": lakehouse_name,
        "description": lakehouse_description,
        "type": "Lakehouse",
        "folderId": target_folder_id,
    }

    print(
        f"🚧 Creating Lakehouse '{lakehouse_name}' "
        f"in workspace '{workspace_id}', folder '{target_folder_id}' via REST..."
    )

    resp = requests.post(url, headers=headers, json=payload)

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Lakehouse creation failed (HTTP {resp.status_code}): {resp.text}"
        )

    body = resp.json()
    lakehouse_id = body.get("id") or body.get("itemId")

    if not lakehouse_id:
        raise RuntimeError(
            "Lakehouse creation response did not contain an ID:\n"
            + json.dumps(body, indent=2)
        )

    print(f"✅ Lakehouse '{lakehouse_name}' created with ID: {lakehouse_id}")
    return lakehouse_id


def ensure_lakehouse(
    workspace_id: str,
    lakehouse_name: str,
    lakehouse_description: str,
    access_token: str,
    target_folder_id: str,
) -> str:
    """
    Idempotent helper:
      - If the Lakehouse already exists by name, return its ID.
      - Otherwise, create it in the target Fabric workspace folder.
    """
    existing_id = get_lakehouse_id_by_name(
        workspace_id=workspace_id,
        lakehouse_name=lakehouse_name,
        access_token=access_token,
    )

    if existing_id:
        print(f"♻️ Reusing existing Lakehouse '{lakehouse_name}'.")
        return existing_id

    return create_lakehouse(
        workspace_id=workspace_id,
        lakehouse_name=lakehouse_name,
        lakehouse_description=lakehouse_description,
        access_token=access_token,
        target_folder_id=target_folder_id,
    )

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""Shared operational-store support embedded in NB01 and NB11; no local imports."""
import hashlib
import base64
import json
import re
import struct
import time

OPERATIONAL_STORE_NAME = "HydroOperationalStore"
OPERATIONAL_STORE_DESCRIPTION = "Hydro shared operational store; lifecycle independent of AppBackend."
OPERATIONAL_ARCHIVE_PREFIX = "Hydro archived operational store; verified replacement "
OPERATIONAL_DEMO_COUNTS = {
    "WorkOrders": 12, "MaintenanceNotifications": 6, "Inspections": 30,
    "SpareParts": 12, "Asset3DModels": 15,
}


def operational_store_name(env_suffix):
    if not isinstance(env_suffix, str) or not re.fullmatch(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", env_suffix):
        raise RuntimeError("Operational SQL requires the explicit setup environment suffix.")
    return f"RTI_Demo_SQLDatabase_{env_suffix}"


def is_operational_store_name(name):
    return name == OPERATIONAL_STORE_NAME or (
        isinstance(name, str) and re.fullmatch(r"RTI_Demo_SQLDatabase_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", name) is not None
    )


def active_operational_items(items):
    candidates = [item for item in items if item.get("type") == "SQLDatabase"
                  and is_operational_store_name(item.get("displayName"))]
    active, archived = [], []
    for item in candidates:
        match = re.fullmatch(re.escape(OPERATIONAL_ARCHIVE_PREFIX) + r"([0-9a-fA-F-]{36})\.",
                             item.get("description") or "")
        if match:
            archived.append(match[1].lower())
        else:
            active.append(item)
    if archived and (len(active) != 1 or any(target != active[0]["id"].lower() for target in archived)):
        raise RuntimeError("Archived operational SQL does not identify one available active replacement.")
    return active


def operational_source_path(parts, workspace_id, item_id, preferred_path):
    sources = {
        part["path"]: json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
        for part in parts
        if part["path"].startswith("Files/Config/draft/") and part["path"].endswith("/datasource.json")
    }
    matching = [path for path, source in sources.items()
                if source.get("type") == "sql_database" and source.get("artifactId") == item_id]
    if len(matching) > 1:
        raise RuntimeError("Multiple Data Agent SQL sources reference the same operational item.")
    if matching:
        path = matching[0]
        if sources[path].get("workspaceId") != workspace_id:
            raise RuntimeError("Existing operational SQL source has a conflicting workspace identity.")
        return path
    if preferred_path in sources:
        raise RuntimeError("The preferred operational SQL source path belongs to another source.")
    return preferred_path

OPERATIONAL_COLUMNS = {
    "WorkOrders": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "workOrderNumber": "nvarchar(max) NOT NULL", "equipmentId": "nvarchar(max) NOT NULL",
        "instrumentId": "nvarchar(max) NULL", "opcuaNodeId": "nvarchar(max) NULL",
        "title": "nvarchar(max) NOT NULL", "description": "nvarchar(max) NULL",
        "priority": "nvarchar(max) NOT NULL", "status": "nvarchar(max) NOT NULL",
        "assignedToOid": "nvarchar(max) NULL", "createdByOid": "nvarchar(max) NOT NULL",
        "createdAt": "datetime2 NOT NULL", "dueAt": "datetime2 NULL", "completedAt": "datetime2 NULL",
    },
    "MaintenanceNotifications": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "equipmentId": "nvarchar(max) NOT NULL", "opcuaNodeId": "nvarchar(max) NULL",
        "summary": "nvarchar(max) NOT NULL", "severity": "nvarchar(max) NOT NULL",
        "status": "nvarchar(max) NOT NULL", "reportedByOid": "nvarchar(max) NOT NULL",
        "reportedAt": "datetime2 NOT NULL",
    },
    "Asset3DModels": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "equipmentId": "nvarchar(max) NOT NULL", "modelName": "nvarchar(max) NOT NULL",
        "format": "nvarchar(max) NOT NULL", "modelUrl": "nvarchar(max) NOT NULL",
        "thumbnailUrl": "nvarchar(max) NULL", "fileSizeMb": "decimal(38,10) NULL",
        "version": "nvarchar(max) NULL", "updatedByOid": "nvarchar(max) NOT NULL",
        "updatedAt": "datetime2 NOT NULL",
    },
    "Inspections": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "equipmentId": "nvarchar(max) NOT NULL", "opcuaNodeId": "nvarchar(max) NULL",
        "inspectionType": "nvarchar(max) NOT NULL", "result": "nvarchar(max) NOT NULL",
        "findings": "nvarchar(max) NULL", "inspectorOid": "nvarchar(max) NOT NULL",
        "inspectedAt": "datetime2 NOT NULL", "nextDueAt": "datetime2 NULL",
    },
    "SpareParts": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "partNumber": "nvarchar(255) NOT NULL UNIQUE", "name": "nvarchar(max) NOT NULL",
        "category": "nvarchar(max) NOT NULL", "equipmentType": "nvarchar(max) NOT NULL",
        "quantityOnHand": "int NOT NULL", "reorderLevel": "int NOT NULL",
        "storageLocation": "nvarchar(max) NOT NULL", "unitCostUsd": "decimal(38,10) NULL",
        "lastRestockedAt": "datetime2 NULL",
    },
}

WEATHER_PROJECT_ENTITIES = {
    "WeatherSource": ("weather_sources", ("source_id",)),
    "WeatherVariable": ("weather_variables", ("variable_id",)),
    "WeatherLocation": ("weather_locations", ("location_id",)),
    "WeatherArea": ("weather_areas", ("area_id",)),
    "WeatherLatestObservation": ("weather_latest_observations", ("source_id", "location_id", "observed_at_utc")),
    "WeatherLatestForecast": ("weather_latest_forecasts",
                              ("source_id", "target_kind", "target_id", "reference_time_utc", "valid_time_utc")),
    "WeatherIngestionRun": ("weather_ingestion_runs", ("run_id",)),
    "InstrumentClassification": ("gold_instrument_classification", ("system_id", "signal_type")),
}

SQL_PROJECT_ENTITIES = {
    "WorkOrder": "WorkOrders", "Inspection": "Inspections", "SparePart": "SpareParts",
    "Asset3DModel": "Asset3DModels", "MaintenanceNotification": "MaintenanceNotifications",
}

def project_identity_expression(keys):
    """Encode every natural-key member, including UTC timestamps, before hashing."""
    if not keys or any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key) for key in keys):
        raise RuntimeError("Project identity requires explicit safe source columns.")
    members = ", ".join(f"'{key}', `{key}`" for key in keys)
    return ("sha2(to_json(named_struct(" + members + "), map("
            "'timeZone', 'UTC', 'timestampFormat', 'yyyy-MM-dd HH:mm:ss.SSSSSS', "
            "'ignoreNullFields', 'false')), 256)")

def project_source_expressions(workspace, lakehouse, database):
    return [
        f'expression {name} =\n\t\tlet\n\t\t    Source = AzureStorage.DataLake('
        f'"https://onelake.dfs.fabric.microsoft.com/{workspace}/{item}", [HierarchicalNavigation=true])'
        "\n\t\tin\n\t\t    Source\n"
        for name, item in (("HydroProjectOperations", database), ("HydroProjectLakehouse", lakehouse))
    ]


def project_ontology_source_metadata(parts, workspace, lakehouse, database):
    """Pin authoritative source metadata on shared expressions, as Fabric retains it."""
    workspace_id = workspace.get("id")
    sources = {item.get("id"): item for item in (lakehouse, database)}
    if (not workspace_id or not workspace.get("displayName")
            or lakehouse.get("type") != "Lakehouse" or database.get("type") != "SQLDatabase"
            or len(sources) != 2 or None in sources):
        raise RuntimeError("Ontology source metadata requires authoritative workspace, Lakehouse and SQL identities.")
    for item in sources.values():
        if item.get("workspaceId", workspace_id) != workspace_id:
            raise RuntimeError("Ontology source metadata belongs to another workspace.")
    decode = lambda part: base64.b64decode(part["payload"]).decode("utf-8").replace("\r\n", "\n")
    indexed = {part["path"]: part for part in parts}
    if len(indexed) != len(parts) or "expressions.tmdl" not in indexed:
        raise RuntimeError("Ontology source metadata requires unambiguous shared expressions.")
    expression_items = {}
    domains = {name: lakehouse["id"] for name in
               ("facilities", "systems", "equipment", "instruments", "signal_master", *WEATHER_PROJECT_ENTITIES)}
    domains.update({name: database["id"] for name in SQL_PROJECT_ENTITIES})
    for entity, item_id in domains.items():
        text = decode(indexed[f"entities/{entity}.tmdl"])
        backing = re.findall(r"(?m)^\s*backingTable:\s*(.+?)\s*$", text)
        if len(backing) != 1:
            raise RuntimeError(f"Cannot pin an ambiguous ontology backing table for {entity}.")
        table = backing[0].strip("'").replace("''", "'")
        partition = decode(indexed[f"tables/{table}.tmdl"])
        expressions = re.findall(r"(?m)^\s*expressionSource:\s*(.+?)\s*$", partition)
        if len(expressions) != 1:
            raise RuntimeError(f"Cannot pin an ambiguous ontology source for {entity}.")
        name = expressions[0].strip("'").replace("''", "'")
        if name in expression_items and expression_items[name] != item_id:
            raise RuntimeError("An ontology expression cannot identify two different source items.")
        expression_items[name] = item_id
    original = decode(indexed["expressions.tmdl"])
    seen = set()
    def pin(match):
        block = match[0]
        name = match[1].strip("'").replace("''", "'")
        if name not in expression_items:
            return block
        if name in seen:
            raise RuntimeError(f"Duplicate ontology source expression {name}.")
        seen.add(name)
        item = sources[expression_items[name]]
        expected = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{item['id']}"
        urls = re.findall(r'AzureStorage\.DataLake\(\s*"([^"]+)"', block)
        if len(urls) != 1 or urls[0].rstrip("/").casefold() != expected.casefold():
            raise RuntimeError(f"Ontology expression {name} does not match its authoritative source identity.")
        properties = item.get("properties", {})
        sql_endpoint = (properties.get("sqlEndpointProperties", {}).get("connectionString")
                        if item["type"] == "Lakehouse" else properties.get("serverFqdn"))
        sql_database = item.get("displayName") if item["type"] == "Lakehouse" else properties.get("databaseName")
        values = {
            "ONT_WorkspaceId": workspace_id, "ONT_ItemId": item["id"], "ONT_ItemKind": item["type"],
            "ONT_ItemName": item.get("displayName"), "ONT_WorkspaceName": workspace["displayName"],
            "ONT_SqlEndpoint": sql_endpoint, "ONT_SqlDatabase": sql_database,
        }
        for key, value in values.items():
            if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
                raise RuntimeError(f"Missing or invalid authoritative ontology source metadata: {key}.")
            pattern = r"(?m)^\tannotation " + key + r" = (.+?)[ \t]*$"
            existing = re.findall(pattern, block)
            if len(existing) > 1:
                raise RuntimeError(f"Duplicate ontology source metadata {name}.{key}.")
            if existing:
                if key in ("ONT_WorkspaceId", "ONT_ItemId", "ONT_ItemKind") and existing[0].casefold() != value.casefold():
                    raise RuntimeError(f"Conflicting ontology source metadata {name}.{key}.")
                block = re.sub(pattern, lambda _match: f"\tannotation {key} = {value}", block)
            else:
                block = block.rstrip() + f"\n\n\tannotation {key} = {value}\n"
        pinned = re.findall(r"(?m)^\tannotation ONT_PinnedAtUtc = (.+?)[ \t]*$", block)
        if len(pinned) > 1:
            raise RuntimeError(f"Duplicate ontology source pin timestamp for {name}.")
        if not pinned:
            block = block.rstrip() + "\n\n\tannotation ONT_PinnedAtUtc = " + time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n"
        return block.rstrip() + "\n\n"
    updated = re.sub(r"(?ms)^expression (.+?) =\n.*?(?=^expression |\Z)", pin, original)
    if seen != set(expression_items):
        raise RuntimeError("An ontology source expression is missing or cannot be verified.")
    if updated == original:
        return parts
    result = []
    for part in parts:
        if part["path"] == "expressions.tmdl":
            part = {**part, "payload": base64.b64encode(updated.encode()).decode()}
        result.append(part)
    return result


def project_ontology_extension(parts, workspace, lakehouse, database, weather_columns, sql_columns, verify_existing=None):
    """Add verified source-backed domains without changing existing core definitions."""
    by_path = {part["path"]: dict(part) for part in parts}
    if len(by_path) != len(parts) or "database.tmdl" not in by_path or "model.tmdl" not in by_path:
        raise RuntimeError("Project extension requires one unambiguous generation-2 TMDL contract.")
    decode = lambda part: base64.b64decode(part["payload"]).decode("utf-8").replace("\r\n", "\n")
    texts = {path: decode(part) for path, part in by_path.items()}
    core_tables = {}
    for entity in ("facilities", "systems", "equipment", "instruments", "signal_master"):
        text = texts.get(f"entities/{entity}.tmdl", "")
        matches = re.findall(r"(?m)^\s*backingTable:\s*([A-Za-z_][A-Za-z_0-9]*)\s*$", text)
        if len(matches) != 1:
            raise RuntimeError(f"Core {entity} backing identity is not compatible with an additive project extension.")
        core_tables[entity] = matches[0]
    additions = {}
    entities = []
    tables = []
    def add_entity(name, physical, columns, keys, expression):
        if not set(keys).issubset(columns):
            raise RuntimeError(f"Project ontology {name} is missing source key columns {set(keys) - set(columns)}.")
        if len(keys) > 1:
            if columns.get("ontology_key") != "string":
                raise RuntimeError(f"Project ontology {name} requires its verified physical ontology_key.")
            keys = ("ontology_key",)
        alias = "HydroProject_" + name
        entity = [f"entity {name}", f"\tbackingTable: {alias}"]
        entity.extend(f"\tkeyProperty: {key}" for key in keys)
        table = [f"table {alias}"]
        for column, kind in columns.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", column):
                raise RuntimeError(f"Unsupported project source column identifier: {column!r}")
            entity.extend((f"\tproperty {column}", f"\t\tdataType: {kind}",
                           "\t\tbackingConfiguration", f"\t\t\tvalueColumn: {alias}.{column}"))
            table.extend((f"\tcolumn {column}", f"\t\tdataType: {kind}", f"\t\tsourceColumn: {column}"))
        table.extend((f"\tpartition {alias} = entity", "\t\tmode: directLake", "\t\tsource",
                      f"\t\t\tentityName: {physical}", "\t\t\tschemaName: dbo",
                      f"\t\t\texpressionSource: {expression}"))
        entity.append("\tannotation HydroProjectExtension = 1")
        table.append("\tannotation HydroProjectExtension = 1")
        additions[f"entities/{name}.tmdl"] = "\n".join(entity) + "\n"
        additions[f"tables/{alias}.tmdl"] = "\n".join(table) + "\n"
        entities.append(name)
        tables.append(alias)
        return alias
    aliases = {}
    for name, physical in SQL_PROJECT_ENTITIES.items():
        aliases[name] = add_entity(name, physical, sql_columns[physical], ("id",), "HydroProjectOperations")
    for name, (physical, keys) in WEATHER_PROJECT_ENTITIES.items():
        aliases[name] = add_entity(name, physical, weather_columns[physical], keys, "HydroProjectLakehouse")
    relationships = []
    semantic = []
    def edge(name, source, target, source_key, target_key):
        source_table = aliases[source]
        target_table = aliases.get(target, core_tables.get(target))
        physical_name = "HydroProject_" + name
        relationships.append(
            f"relationship {physical_name}\n\tfromColumn: {source_table}.{source_key}\n"
            f"\ttoColumn: {target_table}.{target_key}\n")
        semantic.append(
            f"entityRelationship {name}\n\tfromEntity: {source}\n\ttoEntity: {target}\n"
            f"\tbackingConfiguration\n\t\trelationship: {physical_name}\n")
    for name in ("WorkOrder", "Inspection", "Asset3DModel", "MaintenanceNotification"):
        edge(f"{name}_for_equipment", name, "equipment", "equipmentId", "equipment_id")
    edge("WorkOrder_for_instrument", "WorkOrder", "instruments", "instrumentId", "instrument_id")
    for name in ("WorkOrder", "Inspection", "MaintenanceNotification"):
        edge(f"{name}_for_signal", name, "signal_master", "opcuaNodeId", "opcua_node_id")
    edge("WeatherLatestObservation_at_location", "WeatherLatestObservation", "WeatherLocation", "location_id", "location_id")
    edge("WeatherLatestObservation_from_source", "WeatherLatestObservation", "WeatherSource", "source_id", "source_id")
    edge("WeatherLatestForecast_from_source", "WeatherLatestForecast", "WeatherSource", "source_id", "source_id")
    edge("WeatherIngestionRun_from_source", "WeatherIngestionRun", "WeatherSource", "source_id", "source_id")
    edge("InstrumentClassification_for_system", "InstrumentClassification", "systems", "system_id", "system_id")
    for path, wanted in additions.items():
        if path in texts and texts[path] != wanted:
            if verify_existing:
                verify_existing([{"path": path, "payload": base64.b64encode(wanted.encode()).decode()}], [by_path[path]])
                continue
            # Readback adds lineage metadata. Never overwrite a pre-existing custom entity.
            normalized = "\n".join(line for line in texts[path].splitlines() if not line.strip().startswith("lineageTag:")) + "\n"
            if normalized != wanted:
                raise RuntimeError(f"Project ontology part {path} already exists with incompatible custom content.")
        elif path not in texts:
            texts[path] = wanted
    for path, blocks in (
        ("relationships.tmdl", relationships),
        ("entityRelationships.tmdl", semantic),
        ("expressions.tmdl", project_source_expressions(workspace, lakehouse, database)),
    ):
        current = texts.get(path, "")
        for block in blocks:
            header = block.splitlines()[0]
            if re.search(r"(?m)^" + re.escape(header) + r"\s*$", current):
                if verify_existing:
                    verify_existing(
                        [{"path": path, "payload": base64.b64encode(block.encode()).decode()}],
                        [{"path": path, "payload": base64.b64encode(current.encode()).decode()}])
                    continue
                existing = re.search(r"(?ms)^" + re.escape(header) + r"\s*\n(.*?)(?=^[^\s]|\Z)", current)
                clean = "\n".join(line for line in (header + "\n" + existing[1]).splitlines()
                                  if line.strip() and not line.strip().startswith("lineageTag:")) if existing else ""
                expected = "\n".join(line for line in block.splitlines() if line.strip())
                if clean != expected:
                    raise RuntimeError(f"Existing project binding {header} differs from the verified source.")
            else:
                current = current.rstrip() + "\n\n" + block
        texts[path] = current
    model = texts["model.tmdl"]
    for kind, names in (("table", tables), ("entity", entities)):
        for name in names:
            ref = f"ref {kind} {name}"
            if not re.search(r"(?m)^" + re.escape(ref) + r"\s*$", model):
                model = model.rstrip() + "\n" + ref + "\n"
    texts["model.tmdl"] = model
    for path, text in texts.items():
        if path not in by_path or text != decode(by_path[path]):
            by_path[path] = {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}
    return list(by_path.values())


def rebind_project_operational_source(parts, workspace, lakehouse, source, target):
    """Rebind only the verified operational expression after an explicit SQL copy."""
    if source.get("id") == target.get("id"):
        raise RuntimeError("Operational source replacement requires different SQL identities.")
    indexed = {part["path"]: part for part in parts}
    if len(indexed) != len(parts) or "expressions.tmdl" not in indexed:
        raise RuntimeError("Operational source replacement requires unambiguous ontology parts.")
    text = base64.b64decode(indexed["expressions.tmdl"]["payload"]).decode("utf-8").replace("\r\n", "\n")
    pattern = r"(?ms)^expression HydroProjectOperations =\n.*?(?=^expression |\Z)"
    blocks = list(re.finditer(pattern, text))
    if len(blocks) != 1:
        raise RuntimeError("Operational source replacement requires exactly one project SQL expression.")
    block = blocks[0][0]
    old_url = f"https://onelake.dfs.fabric.microsoft.com/{workspace['id']}/{source['id']}"
    new_url = f"https://onelake.dfs.fabric.microsoft.com/{workspace['id']}/{target['id']}"
    if new_url in block:
        return project_ontology_source_metadata(parts, workspace, lakehouse, target)
    project_ontology_source_metadata(parts, workspace, lakehouse, source)
    if block.count(old_url) != 1:
        raise RuntimeError("Existing operational ontology source identity must be verified before replacement.")
    replacement = block.replace(old_url, new_url)
    replacement = re.sub(r"(?m)^(\tannotation ONT_ItemId = ).+$",
                         lambda match: match[1] + target["id"], replacement)
    replacement = re.sub(r"(?m)^(\tannotation ONT_PinnedAtUtc = ).+$",
                         lambda match: match[1] + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), replacement)
    updated = text[:blocks[0].start()] + replacement + text[blocks[0].end():]
    result = [{**part, "payload": base64.b64encode(updated.encode()).decode()}
              if part["path"] == "expressions.tmdl" else part for part in parts]
    return project_ontology_source_metadata(result, workspace, lakehouse, target)


def operational_item(request, workspace, item_id):
    response = request("GET", f"workspaces/{workspace}/sqlDatabases/{item_id}")
    response.raise_for_status()
    item = response.json()
    if (item.get("id") != item_id or item.get("type") != "SQLDatabase"
            or item.get("workspaceId", workspace) != workspace):
        raise RuntimeError("Operational SQL item identity does not match the selected workspace.")
    properties = item.get("properties", {})
    if not properties.get("serverFqdn") or not properties.get("databaseName"):
        raise RuntimeError("Operational SQL item has no authoritative TDS connection identity.")
    return item


def _operational_inventory(request, workspace, folder_id=None):
    from urllib.parse import quote

    base = f"workspaces/{workspace}/items"
    if folder_id is not None:
        base += f"?rootFolderId={quote(folder_id, safe='')}&recursive=false"
    url = base
    items = []
    visited = set()
    while url:
        if url in visited or len(visited) >= 20:
            raise RuntimeError("Operational SQL inventory pagination did not terminate.")
        visited.add(url)
        response = request("GET", url)
        response.raise_for_status()
        page = response.json()
        items.extend(page["value"])
        if page.get("continuationToken"):
            separator = "&" if "?" in base else "?"
            url = base + separator + "continuationToken=" + quote(page["continuationToken"], safe="")
        else:
            url = page.get("continuationUri")
            if url:
                prefix = "https://api.fabric.microsoft.com/v1/"
                if not url.startswith(prefix + f"workspaces/{workspace}/items?"):
                    raise RuntimeError("Unexpected SQL inventory continuation workspace or host.")
                url = url[len(prefix):]
    return items


def ensure_operational_item(request, workspace, env_suffix, folder_id):
    wanted_name = operational_store_name(env_suffix)
    if not isinstance(folder_id, str) or not folder_id.strip():
        raise RuntimeError("Operational SQL requires the explicit setup folder identity.")
    candidates = active_operational_items([
        item for item in _operational_inventory(request, workspace)
        if item.get("displayName") in (wanted_name, OPERATIONAL_STORE_NAME)
    ])
    if len(candidates) > 1:
        raise RuntimeError("Multiple shared operational stores match the explicit setup name.")
    expected_name = candidates[0]["displayName"] if candidates else wanted_name
    if candidates:
        item = operational_item(request, workspace, candidates[0]["id"])
        if item.get("description") != OPERATIONAL_STORE_DESCRIPTION:
            raise RuntimeError("The shared SQL name is already used by an unrelated item; refusing to adopt it.")
    else:
        response = request("POST", f"workspaces/{workspace}/sqlDatabases", {
            "displayName": wanted_name, "description": OPERATIONAL_STORE_DESCRIPTION,
        })
        response.raise_for_status()
        if response.status_code == 202:
            operation_url = _fabric_operation_url(response, "https://api.fabric.microsoft.com/v1")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                operation = request("GET", operation_url)
                operation.raise_for_status()
                status = operation.json()["status"]
                if status == "Succeeded":
                    response = request("GET", _fabric_result_url(operation_url))
                    response.raise_for_status()
                    break
                if status in ("Failed", "Cancelled"):
                    raise RuntimeError(f"Shared operational SQL creation failed: {operation.text}")
                time.sleep(3)
            else:
                raise RuntimeError("Shared operational SQL creation exceeded five minutes; inspect the existing operation.")
        item_id = response.json().get("id")
        if not item_id:
            raise RuntimeError("Shared operational SQL creation did not return an item identity.")
        item = operational_item(request, workspace, item_id)
    item_path = f"workspaces/{workspace}/items/{item['id']}"
    if item["displayName"] != wanted_name:
        print(
            f"Fabric SQLDatabase names are immutable. Retaining {item['displayName']!r} "
            f"({item['id']}); {wanted_name!r} requires an explicitly approved replacement migration.")
    if not candidates or candidates[0].get("folderId") != folder_id:
        response = request("POST", item_path + "/move", {"targetFolderId": folder_id})
        response.raise_for_status()
    folder_items = _operational_inventory(request, workspace, folder_id)
    if not any(value["id"] == item["id"] and value.get("folderId") == folder_id for value in folder_items):
        raise RuntimeError("Shared operational SQL folder readback did not match setup.")
    verified = operational_item(request, workspace, item["id"])
    if verified.get("displayName") != expected_name or verified.get("description") != OPERATIONAL_STORE_DESCRIPTION:
        raise RuntimeError("Shared operational SQL naming/ownership readback did not match setup.")
    return verified


def operational_connection(item, sql_token):
    import pyodbc

    installed = pyodbc.drivers()
    driver = next((name for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")
                   if name in installed), None)
    if driver is None:
        raise RuntimeError("SQL setup requires Microsoft ODBC Driver 18 or 17. Fabric Runtime 2.0 includes Driver 18.")
    properties = item["properties"]
    token = sql_token().encode("utf-16-le")
    connection = pyodbc.connect(
        f"Driver={{{driver}}};Server={properties['serverFqdn']};Database={properties['databaseName']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60",
        attrs_before={1256: struct.pack("<I", len(token)) + token},
    )
    connection.timeout = 60
    return connection


def initialize_operational_schema(connection):
    cursor = connection.cursor()
    cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000")
    for table, columns in OPERATIONAL_COLUMNS.items():
        definitions = ", ".join(f"[{name}] {kind}" for name, kind in columns.items())
        cursor.execute(f"IF OBJECT_ID(N'dbo.{table}', N'U') IS NULL CREATE TABLE dbo.[{table}] ({definitions})")
        cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", "dbo", table)
        actual = {row[0] for row in cursor.fetchall()}
        if not set(columns).issubset(actual):
            raise RuntimeError(f"Shared operational table {table} is missing required columns; schema migration is required.")
    cursor.execute(
        "IF OBJECT_ID(N'dbo.HydroOperationalStoreState', N'U') IS NULL "
        "CREATE TABLE dbo.HydroOperationalStoreState ("
        "id int NOT NULL PRIMARY KEY CHECK (id=1), sourceItemId uniqueidentifier NOT NULL,"
        "targetItemId uniqueidentifier NOT NULL, receipt nvarchar(max) NOT NULL)"
    )
    connection.commit()


def _operational_projection(columns):
    return ",".join(
        f"CONVERT(nvarchar(33),[{column}],126) AS [{column}]" if kind.lower().startswith("datetime2")
        else f"[{column}]" for column, kind in columns.items())


def seed_operational_records(connection, seed_sql):
    """Apply unwrapped seed DML, verifying existing fields before committing."""
    connection.autocommit = False
    cursor = connection.cursor()
    try:
        cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000")
        def snapshot():
            result = {}
            for table, columns in OPERATIONAL_COLUMNS.items():
                cursor.execute(
                    f"SELECT {_operational_projection(columns)} FROM dbo.[{table}] "
                    "WITH (TABLOCKX,HOLDLOCK) ORDER BY id")
                result[table] = [tuple(row) for row in cursor.fetchmany(100_001)]
                if len(result[table]) > 100_000:
                    raise RuntimeError("Seed preservation readback exceeds 100,000 rows; use an explicit data migration.")
            return result
        before = snapshot()
        cursor.execute(seed_sql)
        after = snapshot()
        for table, rows in before.items():
            indexed = {str(row[0]): row for row in after[table]}
            if any(indexed.get(str(row[0])) != row for row in rows):
                raise RuntimeError(f"Seed changed existing {table} fields or identities; transaction rolled back.")
            if len(after[table]) < OPERATIONAL_DEMO_COUNTS[table]:
                raise RuntimeError(f"Seed left {table} below its demo population; transaction rolled back.")
        receipt = {
            "existing_records_preserved": True,
            "existing_record_sha256": hashlib.sha256(
                json.dumps(before, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
            "tables": {table: {"before": len(before[table]), "after": len(after[table])} for table in before},
        }
        connection.commit()
        return receipt
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def copy_operational_records(source, target, source_id, target_id):
    """Copy under legacy table locks, verify every field, then fail closed on old writes."""
    if source_id == target_id:
        raise RuntimeError("Legacy and independent operational SQL identities must differ.")
    source_cursor, target_cursor = source.cursor(), target.cursor()
    target_cursor.execute("SELECT sourceItemId,targetItemId,receipt FROM dbo.HydroOperationalStoreState WHERE id=1")
    state = target_cursor.fetchone()
    target.commit()
    expected_triggers = {f"HydroArchive_{table}" for table in OPERATIONAL_COLUMNS}
    source_cursor.execute("SELECT name FROM sys.triggers WHERE name LIKE 'HydroArchive[_]%'")
    triggers = {row[0] for row in source_cursor.fetchall()}
    if state:
        if str(state[0]).lower() != source_id.lower() or str(state[1]).lower() != target_id.lower():
            raise RuntimeError("Operational store migration receipt belongs to different SQL items.")
        if triggers != expected_triggers:
            raise RuntimeError("Operational copy exists but the legacy write boundary is incomplete; repair before deployment.")
        source.rollback()
        return json.loads(state[2])
    if triggers:
        raise RuntimeError("Legacy SQL has archive triggers without a matching shared-store receipt.")
    receipt = {"source_item_id": source_id, "target_item_id": target_id, "tables": {}}
    try:
        source_cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        target_cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000")
        for table, columns in OPERATIONAL_COLUMNS.items():
            source_cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=?", "dbo", table)
            actual = {row[0] for row in source_cursor.fetchall()}
            if actual != set(columns):
                raise RuntimeError(f"Legacy {table} has a different column set; explicit migration is required to preserve custom data.")
            selection = ",".join(f"[{column}]" for column in columns)
            # ODBC/Python datetime loses datetime2's seventh fractional digit.
            projection = _operational_projection(columns)
            source_cursor.execute(f"SELECT {projection} FROM dbo.[{table}] WITH (TABLOCKX,HOLDLOCK) ORDER BY id")
            rows = [tuple(row) for row in source_cursor.fetchmany(100_001)]
            if len(rows) > 100_000:
                raise RuntimeError(f"Legacy {table} exceeds the bounded in-memory migration limit.")
            target_cursor.execute(f"SELECT {projection} FROM dbo.[{table}] WITH (TABLOCKX,HOLDLOCK) ORDER BY id")
            existing = {str(row[0]): tuple(row) for row in target_cursor.fetchmany(100_001)}
            for row in rows:
                previous = existing.get(str(row[0]))
                if previous is not None and previous != row:
                    raise RuntimeError(f"Conflicting existing {table} row {row[0]}; no record will be overwritten.")
                if previous is None:
                    target_cursor.execute(
                        f"INSERT INTO dbo.[{table}] ({selection}) VALUES ({','.join('?' for _ in columns)})", *row)
            target_cursor.execute(f"SELECT {projection} FROM dbo.[{table}] ORDER BY id")
            copied = [tuple(row) for row in target_cursor.fetchmany(100_001)]
            if copied != rows:
                raise RuntimeError(f"Exact operational row readback failed for {table}; source remains authoritative.")
            digest = hashlib.sha256(json.dumps(rows, default=str, separators=(",", ":")).encode()).hexdigest()
            receipt["tables"][table] = {"rows": len(rows), "sha256": digest}
        for table in OPERATIONAL_COLUMNS:
            source_cursor.execute(
                f"CREATE TRIGGER dbo.[HydroArchive_{table}] ON dbo.[{table}] AFTER INSERT,UPDATE,DELETE AS "
                "BEGIN SET NOCOUNT ON; THROW 51001, 'Operational store moved. Reload the current Hydro Operations app.', 1; END"
            )
        target_cursor.execute(
            "INSERT INTO dbo.HydroOperationalStoreState(id,sourceItemId,targetItemId,receipt) VALUES(1,?,?,?)",
            source_id, target_id, json.dumps(receipt, sort_keys=True))
        # The app is not switched until NB11 succeeds. Keep the copy if the source
        # commit fails, but never report that partially established boundary as ready.
        target.commit()
        source.commit()
    except Exception:
        target.rollback()
        source.rollback()
        raise
    return receipt

from collections import Counter
from urllib.parse import urljoin, urlsplit, urlunsplit

def _normal_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")

def _decode_part(part):
    return _normal_text(base64.b64decode(part["payload"]).decode("utf-8"))

def _encode_part(path, text):
    return {"path": path, "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "payloadType": "InlineBase64"}

def _parts_by_path(parts):
    result = {part["path"]: part for part in parts}
    if len(result) != len(parts):
        raise RuntimeError("Duplicate ontology definition part paths")
    return result

def _project_semantics():
    """Portable semantic catalog; contains no workspace identities or bindings."""
    return {'Asset3DModel': {'annotations': {'domain': 'asset',
                                      'modelRole': 'attachment',
                                      'preferredUsage': '3D model lookup and asset documentation',
                                      'queryHint': 'Use Asset3DModel when the question is about 3D model files, '
                                                   'versions, or previews tied to equipment.'},
                      'description': '3D model assets and renderings attached to hydro equipment. Use this '
                                     'entity for model assets, previews, and file metadata tied back to '
                                     'equipment.',
                      'properties': {'equipmentId': {'annotations': {},
                                                     'description': 'Canonical physical equipment identifier '
                                                                    'referencing equipment.equipment_id.',
                                                     'synonyms': ['equipment id', 'equipment identifier']},
                                     'fileSizeMb': {'annotations': {},
                                                    'description': 'File size in megabytes.',
                                                    'synonyms': ['file size', 'size mb']},
                                     'format': {'annotations': {},
                                                'description': 'File format of the model.',
                                                'synonyms': ['file format', 'model format']},
                                     'id': {'annotations': {},
                                            'description': 'Unique identifier for the 3D model record.',
                                            'synonyms': ['model id', 'asset model id']},
                                     'modelName': {'annotations': {},
                                                   'description': 'Display name of the 3D model.',
                                                   'synonyms': ['name', 'model title', 'asset model name']},
                                     'modelUrl': {'annotations': {},
                                                  'description': 'URL to the model file.',
                                                  'synonyms': ['model link', 'model location', 'file url']},
                                     'thumbnailUrl': {'annotations': {},
                                                      'description': 'URL to the model thumbnail.',
                                                      'synonyms': ['thumbnail link', 'preview image']},
                                     'updatedAt': {'annotations': {},
                                                   'description': 'Last update timestamp.',
                                                   'synonyms': ['updated at', 'last updated']},
                                     'updatedByOid': {'annotations': {},
                                                      'description': 'Object identifier of the user who last '
                                                                     'updated the model.',
                                                      'synonyms': ['updated by', 'updated by oid', 'editor oid']},
                                     'version': {'annotations': {},
                                                 'description': 'Model version label.',
                                                 'synonyms': ['revision', 'release', 'version label']}},
                      'synonyms': ['3D Model',
                                   'Equipment Model',
                                   'Digital Twin Model',
                                   'Model Asset',
                                   'Asset Rendering']},
     'Inspection': {'annotations': {'domain': 'maintenance',
                                    'modelRole': 'operational fact',
                                    'preferredUsage': 'inspection history and compliance review',
                                    'queryHint': 'Use Inspection for inspection activity, findings, and due '
                                                 'dates.'},
                    'description': 'equipment inspection records and outcomes. Use this entity for inspection '
                                   'history, findings, and schedule tracking.',
                    'properties': {'equipmentId': {'annotations': {},
                                                   'description': 'Canonical physical equipment identifier '
                                                                  'referencing equipment.equipment_id.',
                                                   'synonyms': ['equipment id', 'equipment identifier']},
                                   'findings': {'annotations': {},
                                                'description': 'Free-text inspection findings.',
                                                'synonyms': ['findings', 'notes']},
                                   'id': {'annotations': {},
                                          'description': 'Unique identifier for the inspection record.',
                                          'synonyms': ['inspection id', 'record id']},
                                   'inspectedAt': {'annotations': {},
                                                   'description': 'Timestamp when the inspection occurred.',
                                                   'synonyms': ['inspection time', 'inspected at']},
                                   'inspectionType': {'annotations': {},
                                                      'description': 'Type or category of inspection.',
                                                      'synonyms': ['type', 'inspection category']},
                                   'inspectorOid': {'annotations': {},
                                                    'description': 'Object identifier of the inspector.',
                                                    'synonyms': ['inspector', 'inspector oid']},
                                   'nextDueAt': {'annotations': {},
                                                 'description': 'Scheduled next inspection due time.',
                                                 'synonyms': ['next due', 'due at']},
                                   'opcuaNodeId': {'annotations': {},
                                                   'description': 'Optional canonical OPC UA signal identifier; '
                                                                  'join to signal_master.opcua_node_id when '
                                                                  'present.',
                                                   'synonyms': ['opc ua node id', 'node id']},
                                   'result': {'annotations': {},
                                              'description': 'Inspection outcome.',
                                              'synonyms': ['outcome', 'status']}},
                    'synonyms': ['Inspection Record',
                                 'Asset Inspection',
                                 'Maintenance Inspection',
                                 'Checkup',
                                 'Inspection History']},
     'InstrumentClassification': {'annotations': {'domain': 'asset',
                                                  'modelRole': 'fact',
                                                  'preferredUsage': 'signal inventory and system-level signal '
                                                                    'summary',
                                                  'queryHint': 'Use InstrumentClassification for counts of '
                                                               'signal types by system, not raw instrument '
                                                               'rows.'},
                                  'description': 'Aggregated count of signals by system and signal type. Use '
                                                 'this as the summarized signal inventory for a system.',
                                  'properties': {'ontology_key': {'annotations': {},
                                                                  'description': 'Verified SHA-256 identity of '
                                                                                 'the complete natural-key '
                                                                                 'tuple, with UTC timestamps '
                                                                                 'encoded before hashing. It is '
                                                                                 'not a measurement.',
                                                                  'synonyms': ['ontology key']},
                                                 'signal_count': {'annotations': {},
                                                                  'description': 'Count of signals of this type '
                                                                                 'for the system.',
                                                                  'synonyms': ['signal count',
                                                                               'count of signals']},
                                                 'signal_type': {'annotations': {},
                                                                 'description': 'Canonical signal category used '
                                                                                'for signal inventory and '
                                                                                'classification, not a telemetry '
                                                                                'sample value.',
                                                                 'synonyms': ['signal type', 'measurement type']},
                                                 'system_id': {'annotations': {},
                                                               'description': 'Canonical system identifier '
                                                                              'linking the record to its plant '
                                                                              'subsystem.',
                                                               'synonyms': ['system id']}},
                                  'synonyms': ['Signal Classification',
                                               'Instrument Signal Count',
                                               'System Signal Summary',
                                               'Classification Fact',
                                               'Signal Inventory Summary']},
     'MaintenanceNotification': {'annotations': {'domain': 'maintenance',
                                                 'modelRole': 'operational fact',
                                                 'preferredUsage': 'alert tracking and maintenance triage',
                                                 'queryHint': 'Use MaintenanceNotification for alerts, severity, '
                                                              'status, and reporting details.'},
                                 'description': 'Maintenance alerts and notifications raised for equipment or '
                                                'OPC UA signals. Use this entity for alert tracking, severity, '
                                                'status, and reporter context.',
                                 'properties': {'equipmentId': {'annotations': {},
                                                                'description': 'Canonical physical equipment '
                                                                               'identifier referencing '
                                                                               'equipment.equipment_id.',
                                                                'synonyms': ['equipment id',
                                                                             'equipment identifier']},
                                                'id': {'annotations': {},
                                                       'description': 'Unique identifier for the notification.',
                                                       'synonyms': ['notification id', 'alert id']},
                                                'opcuaNodeId': {'annotations': {},
                                                                'description': 'Optional canonical OPC UA signal '
                                                                               'identifier; join to '
                                                                               'signal_master.opcua_node_id when '
                                                                               'present.',
                                                                'synonyms': ['opc ua node id', 'node id']},
                                                'reportedAt': {'annotations': {},
                                                               'description': 'Timestamp when the notification '
                                                                              'was reported.',
                                                               'synonyms': ['reported time', 'report time']},
                                                'reportedByOid': {'annotations': {},
                                                                  'description': 'Object identifier of the '
                                                                                 'reporter.',
                                                                  'synonyms': ['reported by', 'reporter oid']},
                                                'severity': {'annotations': {},
                                                             'description': 'Severity level assigned to the '
                                                                            'notification.',
                                                             'synonyms': ['priority', 'severity level']},
                                                'status': {'annotations': {},
                                                           'description': 'Lifecycle or operating status '
                                                                          "recorded by this entity's source; not "
                                                                          'automatically inferred from telemetry '
                                                                          'quality.',
                                                           'synonyms': ['state', 'notification status']},
                                                'summary': {'annotations': {},
                                                            'description': 'Short notification summary.',
                                                            'synonyms': ['summary', 'message']}},
                                 'synonyms': ['Maintenance Alert',
                                              'Alert',
                                              'Notification',
                                              'Maintenance Notice',
                                              'Maintenance Alert Record']},
     'SparePart': {'annotations': {'domain': 'maintenance',
                                   'modelRole': 'inventory',
                                   'preferredUsage': 'stock availability, replenishment, and part compatibility',
                                   'queryHint': 'Use the bound operational SQL inventory. Do not infer an '
                                                'equipment foreign key from an equipment-type label.'},
                   'description': 'Inventory of spare parts and stock levels bound to the independent '
                                  'operational SQL database. equipmentType describes compatibility, not '
                                  'ownership by a particular equipment_id.',
                   'properties': {'category': {'annotations': {},
                                               'description': 'Spare part category.',
                                               'synonyms': ['type', 'part category']},
                                  'equipmentType': {'annotations': {},
                                                    'description': 'equipment type this part supports.',
                                                    'synonyms': ['equipment type', 'asset type']},
                                  'id': {'annotations': {},
                                         'description': 'Unique identifier for the spare part record.',
                                         'synonyms': ['id']},
                                  'lastRestockedAt': {'annotations': {},
                                                      'description': 'Timestamp when the part was last '
                                                                     'restocked.',
                                                      'synonyms': ['last restock time', 'restocked at']},
                                  'name': {'annotations': {},
                                           'description': 'Spare part name.',
                                           'synonyms': ['name']},
                                  'partNumber': {'annotations': {},
                                                 'description': 'Manufacturer or catalog part number.',
                                                 'synonyms': ['part no', 'catalog number']},
                                  'quantityOnHand': {'annotations': {},
                                                     'description': 'Available quantity in stock.',
                                                     'synonyms': ['on hand', 'quantity']},
                                  'reorderLevel': {'annotations': {},
                                                   'description': 'Threshold at which reorder is needed.',
                                                   'synonyms': ['reorder threshold', 'reorder point']},
                                  'storageLocation': {'annotations': {},
                                                      'description': 'Storage location of the spare part.',
                                                      'synonyms': ['warehouse location', 'bin location']},
                                  'unitCostUsd': {'annotations': {},
                                                  'description': 'Unit cost in U.S. dollars.',
                                                  'synonyms': ['unit cost', 'cost usd']}},
                   'synonyms': ['Spare Part', 'Part', 'Inventory Item', 'Replacement Part']},
     'WeatherArea': {'annotations': {'modelRole': 'dimension',
                                     'preferredUsage': 'spatial lookup and area grouping',
                                     'queryHint': 'Use WeatherArea to group or target area-based weather '
                                                  'summaries.'},
                     'description': 'Reusable spatial weather region, typically facility-centric or '
                                    'analysis-centric. Use as the lookup dimension for areal weather queries and '
                                    'forecast summarization.',
                     'properties': {'area_id': {'annotations': {},
                                                'description': 'Unique identifier for the weather area.',
                                                'synonyms': ['area id']},
                                    'area_name': {'annotations': {},
                                                  'description': 'Display name of the weather area.',
                                                  'synonyms': ['area name']},
                                    'crs': {'annotations': {},
                                            'description': 'Coordinate reference system used by the geometry.',
                                            'synonyms': ['spatial reference', 'coordinate system']},
                                    'geometry_geojson': {'annotations': {},
                                                         'description': 'GeoJSON geometry describing the area '
                                                                        'boundary.',
                                                         'synonyms': ['geometry geojson']},
                                    'metadata_json': {'annotations': {},
                                                      'description': 'Additional area metadata encoded as JSON.',
                                                      'synonyms': ['metadata json']},
                                    'updated_at_utc': {'annotations': {},
                                                       'description': 'Last update timestamp for the area '
                                                                      'definition.',
                                                       'synonyms': ['updated at utc']}},
                     'synonyms': ['Weather Zone',
                                  'Catchment Area',
                                  'Area of Interest',
                                  'Radius Area',
                                  'Weather Region']},
     'WeatherAreaMetric': {'annotations': {'grain': 'one row per area-variable-source-run-valid time',
                                           'modelRole': 'fact',
                                           'preferredUsage': 'area rollup and coverage analysis',
                                           'queryHint': 'Use WeatherAreaMetric for area-based rollups and '
                                                        'coverage-weighted values.'},
                           'description': 'Computed area-level weather summary fact for facility-centered '
                                          'weather areas. Use this entity for areal rollups, coverage metrics, '
                                          'and area-weighted time-series analysis.',
                           'properties': {'aggregation_method': {'annotations': {},
                                                                 'description': 'Method used to compute the '
                                                                                'metric.',
                                                                 'synonyms': ['aggregation method',
                                                                              'calculation method']},
                                          'area_coverage_fraction': {'annotations': {},
                                                                     'description': 'Fraction of the area '
                                                                                    'covered by contributing '
                                                                                    'cells.',
                                                                     'synonyms': ['area coverage fraction']},
                                          'area_id': {'annotations': {},
                                                      'description': 'Weather area identifier for the metric.',
                                                      'synonyms': ['area identifier']},
                                          'area_weighted_value': {'annotations': {},
                                                                  'description': 'Area-weighted value for the '
                                                                                 'metric.',
                                                                  'synonyms': ['area weighted value']},
                                          'calculated_at_utc': {'annotations': {},
                                                                'description': 'Timestamp when the metric was '
                                                                               'calculated.',
                                                                'synonyms': ['calculated at utc']},
                                          'contributing_cell_count': {'annotations': {},
                                                                      'description': 'Number of contributing '
                                                                                     'cells.',
                                                                      'synonyms': ['contributing cells',
                                                                                   'cell count']},
                                          'cumulative_rainfall_volume_m3': {'annotations': {},
                                                                            'description': 'Cumulative rainfall '
                                                                                           'volume in cubic '
                                                                                           'meters.',
                                                                            'synonyms': ['cumulative rainfall '
                                                                                         'volume m3']},
                                          'cumulative_value': {'annotations': {},
                                                               'description': 'Cumulative value for the metric.',
                                                               'synonyms': ['cumulative value']},
                                          'data_kind': {'annotations': {},
                                                        'description': 'Kind of area metric data, such as '
                                                                       'observation or forecast.',
                                                        'synonyms': ['data kind', 'record kind']},
                                          'forecast_type': {'annotations': {},
                                                            'description': 'Forecast classification or scenario '
                                                                           'type.',
                                                            'synonyms': ['forecast type', 'scenario type']},
                                          'interval_hours': {'annotations': {},
                                                             'description': 'Forecast interval length in hours.',
                                                             'synonyms': ['interval hours']},
                                          'lead_hours': {'annotations': {},
                                                         'description': 'Forecast lead time in hours.',
                                                         'synonyms': ['lead hours']},
                                          'rainfall_volume_m3': {'annotations': {},
                                                                 'description': 'Rainfall volume in cubic '
                                                                                'meters.',
                                                                 'synonyms': ['rainfall volume m3']},
                                          'reference_time_utc': {'annotations': {},
                                                                 'description': 'Reference time associated with '
                                                                                'the forecast or rollup.',
                                                                 'synonyms': ['reference time utc']},
                                          'run_id': {'annotations': {},
                                                     'description': 'Ingestion or calculation run identifier.',
                                                     'synonyms': ['run id', 'calculation run id']},
                                          'source_id': {'annotations': {},
                                                        'description': 'Weather source used for the metric.',
                                                        'synonyms': ['source identifier']},
                                          'source_item_id': {'annotations': {},
                                                             'description': 'Source record or batch identifier '
                                                                            'for the calculated area metric.',
                                                             'synonyms': ['source record id',
                                                                          'provider item id']},
                                          'unit': {'annotations': {},
                                                   'description': 'Measurement unit of the metric.',
                                                   'synonyms': ['measurement unit']},
                                          'valid_time_utc': {'annotations': {},
                                                             'description': 'Valid time for the area metric.',
                                                             'synonyms': ['valid time utc']},
                                          'variable_id': {'annotations': {},
                                                          'description': 'Meteorological variable associated '
                                                                         'with the metric.',
                                                          'synonyms': ['variable identifier']}},
                           'synonyms': ['Area Weather Metric',
                                        'Areal Weather Summary',
                                        'Area Forecast Metric',
                                        'Area Summary',
                                        'Area Weather Fact']},
     'WeatherForecast': {'annotations': {'grain': 'one row per source-variable-location-valid time and forecast '
                                                  'horizon',
                                         'modelRole': 'fact',
                                         'preferredUsage': 'forecast trend and comparison',
                                         'queryHint': 'Use WeatherForecast for forecast history; group by '
                                                      'location, variable, lead hours, and valid time.'},
                         'description': 'Canonical historical weather forecast fact at '
                                        'source-variable-location-time grain. Use this entity for forecast trend '
                                        'analysis, horizon comparisons, and scenario evaluation.',
                         'properties': {'ensemble_member': {'annotations': {},
                                                            'description': 'Forecast ensemble member identifier.',
                                                            'synonyms': ['ensemble member']},
                                        'ingested_at_utc': {'annotations': {},
                                                            'description': 'Ingested at utc recorded by the '
                                                                           'bound WeatherForecast source.',
                                                            'synonyms': ['ingested at utc']},
                                        'interval_hours': {'annotations': {},
                                                           'description': 'Forecast interval length in hours.',
                                                           'synonyms': ['interval hours']},
                                        'latitude': {'annotations': {},
                                                     'description': 'Latitude for the forecast location.',
                                                     'synonyms': ['latitude']},
                                        'lead_hours': {'annotations': {},
                                                       'description': 'Forecast lead time in hours.',
                                                       'synonyms': ['forecast horizon hours']},
                                        'location_id': {'annotations': {},
                                                        'description': 'Location or facility identifier '
                                                                       'associated with the forecast.',
                                                        'synonyms': ['location id']},
                                        'longitude': {'annotations': {},
                                                      'description': 'Longitude for the forecast location.',
                                                      'synonyms': ['longitude']},
                                        'reference_time_utc': {'annotations': {},
                                                               'description': 'Model or reference time for the '
                                                                              'forecast.',
                                                               'synonyms': ['reference time utc']},
                                        'run_id': {'annotations': {},
                                                   'description': 'Ingestion run identifier for the forecast.',
                                                   'synonyms': ['run id', 'load run id']},
                                        'source_id': {'annotations': {},
                                                      'description': 'Weather source that produced the forecast.',
                                                      'synonyms': ['source id']},
                                        'source_item_id': {'annotations': {},
                                                           'description': 'Source record identifier for the '
                                                                          'forecast.',
                                                           'synonyms': ['source record id', 'provider item id']},
                                        'unit': {'annotations': {},
                                                 'description': 'Unit of the forecasted value.',
                                                 'synonyms': ['measurement unit']},
                                        'valid_time_utc': {'annotations': {},
                                                           'description': 'Valid time of the forecasted value.',
                                                           'synonyms': ['valid time']},
                                        'value': {'annotations': {},
                                                  'description': 'Forecasted measurement value.',
                                                  'synonyms': ['forecast value', 'predicted value']},
                                        'variable_id': {'annotations': {},
                                                        'description': 'Meteorological variable forecasted by '
                                                                       'the record.',
                                                        'synonyms': ['variable id']}},
                         'synonyms': ['Forecast Weather',
                                      'Weather Prediction',
                                      'Forecast Fact',
                                      'Weather Outlook',
                                      'Forecast History']},
     'WeatherIngestionRun': {'annotations': {'modelRole': 'operational fact',
                                             'preferredUsage': 'pipeline monitoring',
                                             'queryHint': 'Use WeatherIngestionRun to inspect run status, row '
                                                          'counts, and errors.'},
                             'description': 'Operational load and ingestion run record for weather sources and '
                                            'derived facts. Use as the operational trace for load status, '
                                            'counts, and errors.',
                             'properties': {'completed_at_utc': {'annotations': {},
                                                                 'description': 'Run completion timestamp.',
                                                                 'synonyms': ['completed at utc']},
                                            'data_kind': {'annotations': {},
                                                          'description': 'Type of weather data processed in the '
                                                                         'run.',
                                                          'synonyms': ['data kind', 'record kind']},
                                            'error_message': {'annotations': {},
                                                              'description': 'Error text when the run fails.',
                                                              'synonyms': ['error message']},
                                            'reference_time_utc': {'annotations': {},
                                                                   'description': 'Reference time associated '
                                                                                  'with the ingested data.',
                                                                   'synonyms': ['reference time utc']},
                                            'row_count': {'annotations': {},
                                                          'description': 'Number of rows processed by the run.',
                                                          'synonyms': ['row count']},
                                            'run_id': {'annotations': {},
                                                       'description': 'Unique identifier for the ingestion run.',
                                                       'synonyms': ['run id']},
                                            'source_id': {'annotations': {},
                                                          'description': 'Weather source processed by the run.',
                                                          'synonyms': ['source id']},
                                            'source_item_id': {'annotations': {},
                                                               'description': 'Source record or batch identifier '
                                                                              'associated with the run.',
                                                               'synonyms': ['source item id',
                                                                            'provider item id']},
                                            'started_at_utc': {'annotations': {},
                                                               'description': 'Run start timestamp.',
                                                               'synonyms': ['started at utc']},
                                            'status': {'annotations': {},
                                                       'description': 'Lifecycle or operating status recorded by '
                                                                      "this entity's source; not automatically "
                                                                      'inferred from telemetry quality.',
                                                       'synonyms': ['state']}},
                             'synonyms': ['Ingestion Job',
                                          'Load Run',
                                          'Pipeline Run',
                                          'Import Run',
                                          'Weather Load Run']},
     'WeatherLatestForecast': {'annotations': {'modelRole': 'snapshot',
                                               'preferredUsage': 'current-state shortcut',
                                               'queryHint': 'Use only for the latest forecast state and '
                                                            'distinguish target_kind location from area. '
                                                            'Historical forecasts and area rollups require their '
                                                            'authorized history sources.',
                                               'snapshotOf': 'WeatherForecast'},
                               'description': 'Snapshot helper for the latest forecast state by target and '
                                              'source. Use this only for current-state reads; use '
                                              'WeatherForecast and WeatherAreaMetric for history and rollups.',
                               'properties': {'calculated_at_utc': {'annotations': {},
                                                                    'description': 'Timestamp when the latest '
                                                                                   'forecast snapshot was '
                                                                                   'calculated.',
                                                                    'synonyms': ['calculated at utc']},
                                              'cumulative_precipitation': {'annotations': {},
                                                                           'description': 'Latest forecast '
                                                                                          'cumulative '
                                                                                          'precipitation.',
                                                                           'synonyms': ['cumulative '
                                                                                        'precipitation']},
                                              'cumulative_rainfall_volume_m3': {'annotations': {},
                                                                                'description': 'Latest forecast '
                                                                                               'cumulative '
                                                                                               'rainfall volume '
                                                                                               'in cubic meters.',
                                                                                'synonyms': ['cumulative '
                                                                                             'rainfall volume '
                                                                                             'm3']},
                                              'dew_point': {'annotations': {},
                                                            'description': 'Latest forecast dew point.',
                                                            'synonyms': ['dew point']},
                                              'lead_hours': {'annotations': {},
                                                             'description': 'Forecast lead time in hours.',
                                                             'synonyms': ['lead hours']},
                                              'ontology_key': {'annotations': {},
                                                               'description': 'Verified SHA-256 identity of the '
                                                                              'complete natural-key tuple, with '
                                                                              'UTC timestamps encoded before '
                                                                              'hashing. It is not a measurement.',
                                                               'synonyms': ['ontology key']},
                                              'precipitation': {'annotations': {},
                                                                'description': 'Latest forecast precipitation.',
                                                                'synonyms': ['precipitation']},
                                              'precipitation_interval_hours': {'annotations': {},
                                                                               'description': 'Duration in hours '
                                                                                              'represented by '
                                                                                              'this '
                                                                                              'precipitation '
                                                                                              'forecast value.',
                                                                               'synonyms': ['precipitation '
                                                                                            'interval hours']},
                                              'pressure': {'annotations': {},
                                                           'description': 'Latest forecast pressure.',
                                                           'synonyms': ['pressure']},
                                              'rainfall_volume_m3': {'annotations': {},
                                                                     'description': 'Latest forecast rainfall '
                                                                                    'volume in cubic meters.',
                                                                     'synonyms': ['rainfall volume m3']},
                                              'reference_time_utc': {'annotations': {},
                                                                     'description': 'Reference time for the '
                                                                                    'latest forecast.',
                                                                     'synonyms': ['reference time utc']},
                                              'relative_humidity': {'annotations': {},
                                                                    'description': 'Latest forecast relative '
                                                                                   'humidity.',
                                                                    'synonyms': ['relative humidity']},
                                              'solar_radiation': {'annotations': {},
                                                                  'description': 'Latest forecast solar '
                                                                                 'radiation.',
                                                                  'synonyms': ['solar radiation']},
                                              'source_id': {'annotations': {},
                                                            'description': 'Weather source for the latest '
                                                                           'forecast.',
                                                            'synonyms': ['source id']},
                                              'source_item_id': {'annotations': {},
                                                                 'description': 'Source item id recorded by the '
                                                                                'bound WeatherLatestForecast '
                                                                                'source.',
                                                                 'synonyms': ['source record id',
                                                                              'provider item id']},
                                              'target_id': {'annotations': {},
                                                            'description': 'Identifier in the namespace selected '
                                                                           'by target_kind, not an unconditional '
                                                                           'facility or area foreign key.',
                                                            'synonyms': ['target identifier',
                                                                         'area id / location id']},
                                              'target_kind': {'annotations': {},
                                                              'description': 'Forecast target discriminator: '
                                                                             'point location or spatial area. '
                                                                             'Interpret target_id in this '
                                                                             'context.',
                                                              'synonyms': ['target type', 'destination type']},
                                              'temperature': {'annotations': {},
                                                              'description': 'Latest forecast temperature.',
                                                              'synonyms': ['temperature']},
                                              'valid_time_utc': {'annotations': {},
                                                                 'description': 'Valid time for the latest '
                                                                                'forecasted value.',
                                                                 'synonyms': ['valid time utc']},
                                              'wind_direction': {'annotations': {},
                                                                 'description': 'Latest forecast wind direction.',
                                                                 'synonyms': ['wind direction']},
                                              'wind_gust': {'annotations': {},
                                                            'description': 'Latest forecast wind gust.',
                                                            'synonyms': ['wind gust']},
                                              'wind_speed': {'annotations': {},
                                                             'description': 'Latest forecast wind speed.',
                                                             'synonyms': ['wind speed']}},
                               'synonyms': ['Latest Forecast',
                                            'Current Forecast',
                                            'Forecast Snapshot',
                                            'Weather Outlook Snapshot',
                                            'Latest Forecast Snapshot']},
     'WeatherLatestObservation': {'annotations': {'modelRole': 'snapshot',
                                                  'preferredUsage': 'current-state shortcut',
                                                  'queryHint': 'Use only for the latest observation state. '
                                                               'Historical trends require the authorized '
                                                               'weather_observations history source.',
                                                  'snapshotOf': 'WeatherObservation'},
                                  'description': 'Snapshot helper for the latest weather observation state by '
                                                 'source and location. Use this only for current-state reads; '
                                                 'use WeatherObservation for history and trends.',
                                  'properties': {'calculated_at_utc': {'annotations': {},
                                                                       'description': 'Timestamp when the latest '
                                                                                      'snapshot was calculated.',
                                                                       'synonyms': ['calculated at utc']},
                                                 'dew_point': {'annotations': {},
                                                               'description': 'Latest observed dew point.',
                                                               'synonyms': ['dew point']},
                                                 'location_id': {'annotations': {},
                                                                 'description': 'Canonical registered weather '
                                                                                'point identifier; do not infer '
                                                                                'a facility association without '
                                                                                'a verified mapping.',
                                                                 'synonyms': ['location id']},
                                                 'observed_at_utc': {'annotations': {},
                                                                     'description': 'Timestamp of the latest '
                                                                                    'observed reading.',
                                                                     'synonyms': ['observed at utc']},
                                                 'ontology_key': {'annotations': {},
                                                                  'description': 'Verified SHA-256 identity of '
                                                                                 'the complete natural-key '
                                                                                 'tuple, with UTC timestamps '
                                                                                 'encoded before hashing. It is '
                                                                                 'not a measurement.',
                                                                  'synonyms': ['ontology key']},
                                                 'precipitation': {'annotations': {},
                                                                   'description': 'Latest observed '
                                                                                  'precipitation.',
                                                                   'synonyms': ['precipitation']},
                                                 'pressure': {'annotations': {},
                                                              'description': 'Latest observed pressure.',
                                                              'synonyms': ['pressure']},
                                                 'relative_humidity': {'annotations': {},
                                                                       'description': 'Latest observed relative '
                                                                                      'humidity.',
                                                                       'synonyms': ['relative humidity']},
                                                 'solar_radiation': {'annotations': {},
                                                                     'description': 'Latest observed solar '
                                                                                    'radiation.',
                                                                     'synonyms': ['solar radiation']},
                                                 'source_id': {'annotations': {},
                                                               'description': 'Weather source for the latest '
                                                                              'observation.',
                                                               'synonyms': ['source id']},
                                                 'temperature': {'annotations': {},
                                                                 'description': 'Latest observed temperature.',
                                                                 'synonyms': ['temperature']},
                                                 'wind_direction': {'annotations': {},
                                                                    'description': 'Latest observed wind '
                                                                                   'direction.',
                                                                    'synonyms': ['wind direction']},
                                                 'wind_gust': {'annotations': {},
                                                               'description': 'Latest observed wind gust.',
                                                               'synonyms': ['wind gust']},
                                                 'wind_speed': {'annotations': {},
                                                                'description': 'Latest observed wind speed.',
                                                                'synonyms': ['wind speed']}},
                                  'synonyms': ['Current Weather',
                                               'Latest Weather Reading',
                                               'Weather Snapshot',
                                               'Current Observation',
                                               'Latest Observation Snapshot']},
     'WeatherLocation': {'annotations': {'domain': 'weather',
                                         'modelRole': 'dimension',
                                         'preferredUsage': 'point geography and weather location identity'},
                         'description': 'Registered point weather location with geographic coordinates. This is '
                                        'a location lookup, not an observation or a guarantee of an available '
                                        'provider feed.',
                         'properties': {'elevation_m': {'annotations': {},
                                                        'description': 'Elevation m recorded by the bound '
                                                                       'WeatherLocation source.',
                                                        'synonyms': ['elevation m']},
                                        'latitude': {'annotations': {},
                                                     'description': 'Latitude recorded by the bound '
                                                                    'WeatherLocation source.',
                                                     'synonyms': ['latitude']},
                                        'location_id': {'annotations': {},
                                                        'description': 'Canonical registered weather point '
                                                                       'identifier; do not infer a facility '
                                                                       'association without a verified mapping.',
                                                        'synonyms': ['location id']},
                                        'location_name': {'annotations': {},
                                                          'description': 'Location name recorded by the bound '
                                                                         'WeatherLocation source.',
                                                          'synonyms': ['location name']},
                                        'longitude': {'annotations': {},
                                                      'description': 'Longitude recorded by the bound '
                                                                     'WeatherLocation source.',
                                                      'synonyms': ['longitude']},
                                        'metadata_json': {'annotations': {},
                                                          'description': 'Metadata json recorded by the bound '
                                                                         'WeatherLocation source.',
                                                          'synonyms': ['metadata json']},
                                        'updated_at_utc': {'annotations': {},
                                                           'description': 'Updated at utc recorded by the bound '
                                                                          'WeatherLocation source.',
                                                           'synonyms': ['updated at utc']}},
                         'synonyms': ['Weather Location',
                                      'Weather Station Location',
                                      'Forecast Point',
                                      'Observation Location']},
     'WeatherObservation': {'annotations': {'grain': 'one row per source-variable-location-observation time',
                                            'modelRole': 'fact',
                                            'preferredUsage': 'historical trend and aggregation',
                                            'queryHint': 'Use WeatherObservation for measured weather history; '
                                                         'aggregate by location and variable before '
                                                         'summarizing.'},
                            'description': 'Canonical historical weather observation fact at '
                                           'source-variable-location-time grain. Use this entity for trend '
                                           'analysis, anomaly detection, and time-series comparisons.',
                            'properties': {'ingested_at_utc': {'annotations': {},
                                                               'description': 'Ingested at utc recorded by the '
                                                                              'bound WeatherObservation source.',
                                                               'synonyms': ['ingested at utc']},
                                           'latitude': {'annotations': {},
                                                        'description': 'Latitude for the observation location.',
                                                        'synonyms': ['latitude']},
                                           'location_id': {'annotations': {},
                                                           'description': 'Location or facility identifier '
                                                                          'associated with the observation.',
                                                           'synonyms': ['location id']},
                                           'longitude': {'annotations': {},
                                                         'description': 'Longitude for the observation location.',
                                                         'synonyms': ['longitude']},
                                           'observed_at_utc': {'annotations': {},
                                                               'description': 'Time when the observation was '
                                                                              'recorded.',
                                                               'synonyms': ['observation time', 'observed at']},
                                           'quality': {'annotations': {},
                                                       'description': 'Quality flag for the observation.',
                                                       'synonyms': ['data quality', 'quality flag']},
                                           'run_id': {'annotations': {},
                                                      'description': 'Ingestion run identifier for the '
                                                                     'observation.',
                                                      'synonyms': ['run id', 'load run id']},
                                           'source_id': {'annotations': {},
                                                         'description': 'Weather source that produced the '
                                                                        'observation.',
                                                         'synonyms': ['source id']},
                                           'source_item_id': {'annotations': {},
                                                              'description': 'Source record identifier for the '
                                                                             'observation.',
                                                              'synonyms': ['source record id',
                                                                           'provider item id']},
                                           'unit': {'annotations': {},
                                                    'description': 'Unit of the observed value.',
                                                    'synonyms': ['measurement unit']},
                                           'value': {'annotations': {},
                                                     'description': 'Observed measurement value.',
                                                     'synonyms': ['observation value', 'measured value']},
                                           'variable_id': {'annotations': {},
                                                           'description': 'Meteorological variable measured by '
                                                                          'the observation.',
                                                           'synonyms': ['variable id']}},
                            'synonyms': ['Observed Weather',
                                         'Weather Reading',
                                         'Observation Fact',
                                         'Weather Sample',
                                         'Observation History']},
     'WeatherSource': {'annotations': {'modelRole': 'dimension',
                                       'preferredUsage': 'lookup and provenance',
                                       'queryHint': 'Use WeatherSource for source metadata and filtering, not '
                                                    'for weather measurements.'},
                       'description': 'Canonical catalog of weather providers and feed metadata. Use as the '
                                      'lookup dimension for source identity, provenance, licensing, and provider '
                                      'status.',
                       'properties': {'active': {'annotations': {},
                                                 'description': 'Whether the source is currently active.',
                                                 'synonyms': ['enabled', 'active']},
                                      'endpoint': {'annotations': {},
                                                   'description': 'Source endpoint or feed location.',
                                                   'synonyms': ['endpoint']},
                                      'license': {'annotations': {},
                                                  'description': 'License or usage terms for the weather feed.',
                                                  'synonyms': ['license']},
                                      'provider': {'annotations': {},
                                                   'description': 'Provider organization or vendor name.',
                                                   'synonyms': ['vendor', 'provider name']},
                                      'source_id': {'annotations': {},
                                                    'description': 'Unique identifier for the weather source.',
                                                    'synonyms': ['source id', 'provider id']},
                                      'source_name': {'annotations': {},
                                                      'description': 'Display name of the weather source.',
                                                      'synonyms': ['source name', 'provider name']},
                                      'source_type': {'annotations': {},
                                                      'description': 'Type of weather source, such as forecast '
                                                                     'or observation feed.',
                                                      'synonyms': ['source type', 'feed type']},
                                      'updated_at_utc': {'annotations': {},
                                                         'description': 'Last update timestamp for the source '
                                                                        'metadata.',
                                                         'synonyms': ['updated at utc']}},
                       'synonyms': ['Weather Feed',
                                    'Weather Provider',
                                    'Forecast Source',
                                    'Observation Source',
                                    'Provider Catalog',
                                    'Feed Catalog']},
     'WeatherVariable': {'annotations': {'modelRole': 'dimension',
                                         'preferredUsage': 'lookup and semantic normalization',
                                         'queryHint': 'Use WeatherVariable to decode variable IDs, canonical '
                                                      'units, and aggregation intent.'},
                         'description': 'Canonical catalog of standardized meteorological variables, units, and '
                                        'aggregation semantics. Use as the lookup dimension for weather '
                                        'measurements and forecast variables.',
                         'properties': {'aggregation_kind': {'annotations': {},
                                                             'description': 'Preferred aggregation semantics for '
                                                                            'the variable.',
                                                             'synonyms': ['aggregation kind', 'rollup kind']},
                                        'canonical_name': {'annotations': {},
                                                           'description': 'Canonical variable name used in the '
                                                                          'IQ layer.',
                                                           'synonyms': ['canonical name', 'standard name']},
                                        'canonical_unit': {'annotations': {},
                                                           'description': 'Canonical measurement unit for the '
                                                                          'variable.',
                                                           'synonyms': ['unit', 'canonical unit']},
                                        'description': {'annotations': {},
                                                        'description': 'Human-readable variable definition.',
                                                        'synonyms': ['description']},
                                        'updated_at_utc': {'annotations': {},
                                                           'description': 'Last update timestamp for the '
                                                                          'variable metadata.',
                                                           'synonyms': ['updated at utc']},
                                        'variable_id': {'annotations': {},
                                                        'description': 'Unique identifier for the meteorological '
                                                                       'variable.',
                                                        'synonyms': ['variable id', 'metric id']}},
                         'synonyms': ['Weather Metric',
                                      'Weather Measure',
                                      'Meteorological Variable',
                                      'Weather Parameter',
                                      'Variable Catalog']},
     'WorkOrder': {'annotations': {'domain': 'maintenance',
                                   'modelRole': 'operational fact',
                                   'preferredUsage': 'work management and task lifecycle',
                                   'queryHint': 'Use WorkOrder for task status, scheduling, assignment, and '
                                                'equipment or instrument targeting.'},
                   'description': 'Operational work orders raised for hydro equipment and instrumentation. Use '
                                  'this entity for the lifecycle of maintenance tasks and dispatch work.',
                   'properties': {'assignedToOid': {'annotations': {},
                                                    'description': 'Object identifier of the assigned user, when '
                                                                   'present.',
                                                    'synonyms': ['assigned to', 'assignee oid']},
                                  'completedAt': {'annotations': {},
                                                  'description': 'Timestamp when the work order was completed.',
                                                  'synonyms': ['completion time', 'completed at']},
                                  'createdAt': {'annotations': {},
                                                'description': 'Timestamp when the work order was created.',
                                                'synonyms': ['created time', 'created at']},
                                  'createdByOid': {'annotations': {},
                                                   'description': 'Object identifier of the creator.',
                                                   'synonyms': ['created by', 'creator oid']},
                                  'description': {'annotations': {},
                                                  'description': 'Work order description.',
                                                  'synonyms': ['description']},
                                  'dueAt': {'annotations': {},
                                            'description': 'Due timestamp for the work order.',
                                            'synonyms': ['due time', 'deadline']},
                                  'equipmentId': {'annotations': {},
                                                  'description': 'Canonical physical equipment identifier '
                                                                 'referencing equipment.equipment_id.',
                                                  'synonyms': ['equipment id', 'equipment identifier']},
                                  'id': {'annotations': {},
                                         'description': 'Unique identifier for the work order.',
                                         'synonyms': ['id']},
                                  'instrumentId': {'annotations': {},
                                                   'description': 'Optional physical instrument identifier '
                                                                  'referencing instruments.instrument_id.',
                                                   'synonyms': ['instrument id', 'instrument identifier']},
                                  'opcuaNodeId': {'annotations': {},
                                                  'description': 'Optional canonical OPC UA signal identifier; '
                                                                 'join to signal_master.opcua_node_id when '
                                                                 'present.',
                                                  'synonyms': ['opc ua node id', 'node id']},
                                  'priority': {'annotations': {},
                                               'description': 'Work order priority.',
                                               'synonyms': ['urgency', 'priority level']},
                                  'status': {'annotations': {},
                                             'description': 'Lifecycle or operating status recorded by this '
                                                            "entity's source; not automatically inferred from "
                                                            'telemetry quality.',
                                             'synonyms': ['state', 'work order status']},
                                  'title': {'annotations': {},
                                            'description': 'Short title of the work order.',
                                            'synonyms': ['title']},
                                  'workOrderNumber': {'annotations': {},
                                                      'description': 'Business work order number.',
                                                      'synonyms': ['work order no', 'order number']}},
                   'synonyms': ['Work Order', 'Maintenance Work Order', 'Job', 'Task', 'Work Request']},
     'equipment': {'annotations': {'agentGuide': 'Canonical equipment entity. Prefer this over signal_master '
                                                 'when the question is about the physical machine or asset '
                                                 'record.',
                                   'modelRole': 'equipment registry',
                                   'preferredUsage': 'equipment identity, maintenance targeting, and inventory',
                                   'queryHint': 'Use equipment for physical asset identity and status. Use '
                                                'signal_master for signal identity and telemetry.'},
                   'description': 'Hydro equipment registry attached to facilities and systems. Use this entity '
                                  'for physical equipment identity, status, manufacturer, model, and maintenance '
                                  'targeting. Do not use it as the signal master.',
                   'properties': {'criticality': {'annotations': {},
                                                  'description': 'Recorded equipment criticality used to '
                                                                 'prioritize maintenance; not an inferred '
                                                                 'telemetry severity.',
                                                  'synonyms': ['criticality']},
                                  'equipment_id': {'annotations': {},
                                                   'description': 'Canonical physical equipment identifier. Use '
                                                                  'it to reconcile affected signals with '
                                                                  'operational records.',
                                                   'synonyms': ['equipment id', 'asset id']},
                                  'equipment_type_code': {'annotations': {},
                                                          'description': 'Controlled equipment-class code; '
                                                                         'distinguish this code from its '
                                                                         'human-readable type name.',
                                                          'synonyms': ['type code', 'asset class code']},
                                  'equipment_type_name': {'annotations': {},
                                                          'description': 'Human-readable equipment '
                                                                         'classification, not a unique physical '
                                                                         'equipment identifier.',
                                                          'synonyms': ['type name', 'asset class name']},
                                  'facility_id': {'annotations': {},
                                                  'description': 'Canonical facility identifier linking the '
                                                                 'record to its hydro site or plant.',
                                                  'synonyms': ['facility identifier', 'plant id']},
                                  'install_date': {'annotations': {},
                                                   'description': 'Recorded installation date of the physical '
                                                                  'equipment.',
                                                   'synonyms': ['installation date', 'commission date']},
                                  'is_active': {'annotations': {},
                                                'description': 'Whether the registry record is active. This is '
                                                               'not a measurement of current health or telemetry '
                                                               'freshness.',
                                                'synonyms': ['active', 'enabled']},
                                  'manufacturer': {'annotations': {},
                                                   'description': 'Manufacturer recorded for the physical '
                                                                  'equipment.',
                                                   'synonyms': ['manufacturer']},
                                  'model': {'annotations': {},
                                            'description': 'Manufacturer model designation of the physical '
                                                           'equipment, not a 3D attachment.',
                                            'synonyms': ['equipment model']},
                                  'status': {'annotations': {},
                                             'description': 'Lifecycle or operating status recorded by this '
                                                            "entity's source; not automatically inferred from "
                                                            'telemetry quality.',
                                             'synonyms': ['equipment status', 'asset status']},
                                  'system_id': {'annotations': {},
                                                'description': 'Canonical system identifier linking the record '
                                                               'to its plant subsystem.',
                                                'synonyms': ['system identifier', 'unit id']},
                                  'tag': {'annotations': {},
                                          'description': 'Engineering tag recorded by the source; retain '
                                                         'canonical identifiers for joins rather than relying on '
                                                         'tag text.',
                                          'synonyms': ['equipment tag', 'asset tag']}},
                   'synonyms': ['Equipment', 'Machine', 'Turbine Equipment', 'Unit Equipment']},
     'facilities': {'annotations': {'domain': 'asset',
                                    'modelRole': 'dimension',
                                    'preferredUsage': 'site lookup and geographic context',
                                    'queryHint': 'Use facilities when grouping or filtering by plant/site, and '
                                                 'when joining equipment, systems, or weather location context.'},
                    'description': 'Hydro generation facility master data. Site and plant identity, geography, '
                                   'and asset hierarchy. WeatherLocation is a separate source-bound entity; do '
                                   'not assume a weather location exists for every facility.',
                    'properties': {'commissioned_date': {'annotations': {},
                                                         'description': 'Commissioning date or date label for '
                                                                        'the facility.',
                                                         'synonyms': ['commissioned date', 'commission date']},
                                   'country': {'annotations': {},
                                               'description': 'Country where the facility is located.',
                                               'synonyms': ['country', 'nation']},
                                   'elevation_m': {'annotations': {},
                                                   'description': 'Elevation in meters for the weather location.',
                                                   'synonyms': ['elevation', 'elevation meters']},
                                   'facility_id': {'annotations': {},
                                                   'description': 'Canonical facility identifier linking the '
                                                                  'record to its hydro site or plant.',
                                                   'synonyms': ['facility id', 'site id', 'plant id']},
                                   'facility_name': {'annotations': {},
                                                     'description': 'Display name of the facility.',
                                                     'synonyms': ['facility name', 'site name', 'plant name']},
                                   'lat': {'annotations': {},
                                           'description': 'Facility latitude in decimal degrees.',
                                           'synonyms': ['latitude']},
                                   'latitude': {'annotations': {},
                                                'description': 'Latitude for the facility or weather location.',
                                                'synonyms': ['lat', 'latitude']},
                                   'location_id': {'annotations': {},
                                                   'description': 'Weather location identifier associated with '
                                                                  'the facility, when present.',
                                                   'synonyms': ['location id']},
                                   'location_name': {'annotations': {},
                                                     'description': 'Weather location display name associated '
                                                                    'with the facility, when present.',
                                                     'synonyms': ['location name']},
                                   'lon': {'annotations': {},
                                           'description': 'Facility longitude in decimal degrees.',
                                           'synonyms': ['longitude']},
                                   'longitude': {'annotations': {},
                                                 'description': 'Longitude for the facility or weather location.',
                                                 'synonyms': ['lon', 'longitude']},
                                   'metadata_json': {'annotations': {},
                                                     'description': 'Additional facility or location metadata '
                                                                    'encoded as JSON.',
                                                     'synonyms': ['metadata', 'json metadata']},
                                   'type': {'annotations': {},
                                            'description': 'facilities type or operating class.',
                                            'synonyms': ['facility type', 'plant type']},
                                   'updated_at_utc': {'annotations': {},
                                                      'description': 'Last update timestamp for the facility or '
                                                                     'weather location record.',
                                                      'synonyms': ['updated at', 'last updated']}},
                    'synonyms': ['Plant', 'Power Station', 'Hydro Plant', 'Hydropower Facility', 'Site']},
     'instruments': {'annotations': {'domain': 'asset',
                                     'modelRole': 'instrument registry',
                                     'preferredUsage': 'instrument inventory and physical equipment hierarchy',
                                     'queryHint': 'Use instruments for physical instrumentation and '
                                                  'instrument_id joins. Use signal_master for OPC UA telemetry.'},
                     'description': 'Physical instrument registry attached to equipment, systems, and '
                                    'facilities. Use signal_master for canonical signal identity and telemetry '
                                    'history.',
                     'properties': {'equipment_id': {'annotations': {},
                                                     'description': 'Canonical physical equipment identifier. '
                                                                    'Use it to reconcile affected signals with '
                                                                    'operational records.',
                                                     'synonyms': ['equipment id', 'asset id']},
                                    'event_time': {'annotations': {},
                                                   'description': 'Timestamp of the telemetry sample. Preserve '
                                                                  "this sample's alignment with value and "
                                                                  'quality and state the time zone.',
                                                   'synonyms': ['event timestamp', 'sample time']},
                                    'facility_id': {'annotations': {},
                                                    'description': 'Canonical facility identifier linking the '
                                                                   'record to its hydro site or plant.',
                                                    'synonyms': ['facility id', 'plant id']},
                                    'instrument_id': {'annotations': {},
                                                      'description': 'Physical instrument identifier used for '
                                                                     'the signal-to-instrument relationship.',
                                                      'synonyms': ['signal id', 'point id', 'tag id']},
                                    'instrument_type': {'annotations': {},
                                                        'description': 'Recorded physical instrumentation '
                                                                       'classification; use signal_type for the '
                                                                       'canonical signal category.',
                                                        'synonyms': ['signal type', 'instrument class']},
                                    'is_active': {'annotations': {},
                                                  'description': 'Whether the registry record is active. This is '
                                                                 'not a measurement of current health or '
                                                                 'telemetry freshness.',
                                                  'synonyms': ['active', 'enabled']},
                                    'opcua_node_id': {'annotations': {},
                                                      'description': 'Canonical OPC UA signal identifier shared '
                                                                     'by the static registry and telemetry event '
                                                                     'source.',
                                                      'synonyms': ['OPC UA node', 'node id', 'OPC node']},
                                    'quality': {'annotations': {},
                                                'description': 'Quality of the same telemetry sample as '
                                                               'event_time and value. A BAD sample is not proof '
                                                               'of a mechanical fault.',
                                                'synonyms': ['data quality', 'quality flag']},
                                    'signal_type': {'annotations': {},
                                                    'description': 'Canonical signal category used for signal '
                                                                   'inventory and classification, not a '
                                                                   'telemetry sample value.',
                                                    'synonyms': ['signal class', 'measurement type']},
                                    'system_id': {'annotations': {},
                                                  'description': 'Canonical system identifier linking the record '
                                                                 'to its plant subsystem.',
                                                  'synonyms': ['system id', 'unit id']},
                                    'tag': {'annotations': {},
                                            'description': 'Engineering tag recorded by the source; retain '
                                                           'canonical identifiers for joins rather than relying '
                                                           'on tag text.',
                                            'synonyms': ['asset tag', 'signal tag', 'point tag']},
                                    'unit': {'annotations': {},
                                             'description': 'Engineering unit of the signal or measurement. '
                                                            'Interpret values in this unit and do not silently '
                                                            'mix incompatible units.',
                                             'synonyms': ['measurement unit']},
                                    'value': {'annotations': {},
                                              'description': 'Historical telemetry value for this signal and '
                                                             'event_time, interpreted using its canonical unit.',
                                              'synonyms': ['signal value', 'sample value', 'telemetry value']}},
                     'synonyms': ['Instrument',
                                  'Physical Instrument',
                                  'Equipment Instrument',
                                  'Sensor Registry']},
     'signal_master': {'annotations': {'agentGuide': 'Canonical signal master entity. Prefer this over equipment '
                                                     'when the question is about OPC UA points, tags, units, '
                                                     'telemetry, or signal classification.',
                                       'modelRole': 'signal master',
                                       'preferredUsage': 'signal inventory, point lookup, and telemetry history',
                                       'queryHint': 'Use signal_master for signal identity and historical '
                                                    'telemetry. BAD today means any BAD sample in the stated '
                                                    'time window, not only the latest sample. Reconcile work '
                                                    'orders by equipment_id and opcua_node_id.'},
                       'description': 'Canonical OPC UA signal registry keyed by opcua_node_id. Static Lakehouse '
                                      'identity and equipment context are stitched to Eventhouse telemetry by '
                                      'that same identifier. event_time, value, and quality are aligned '
                                      'time-series properties, not independent latest scalar values.',
                       'properties': {'equipment_id': {'annotations': {},
                                                       'description': 'Canonical physical equipment identifier. '
                                                                      'Use it to reconcile affected signals with '
                                                                      'operational records.',
                                                       'synonyms': ['equipment id', 'asset id']},
                                      'event_time': {'annotations': {},
                                                     'description': 'Timestamp of the telemetry sample. Preserve '
                                                                    "this sample's alignment with value and "
                                                                    'quality and state the time zone.',
                                                     'synonyms': ['event timestamp', 'sample time']},
                                      'facility_id': {'annotations': {},
                                                      'description': 'Canonical facility identifier linking the '
                                                                     'record to its hydro site or plant.',
                                                      'synonyms': ['facility id', 'plant id']},
                                      'instrument_id': {'annotations': {},
                                                        'description': 'Physical instrument identifier used for '
                                                                       'the signal-to-instrument relationship.',
                                                        'synonyms': ['signal id', 'point id', 'tag id']},
                                      'instrument_type': {'annotations': {},
                                                          'description': 'Recorded physical instrumentation '
                                                                         'classification; use signal_type for '
                                                                         'the canonical signal category.',
                                                          'synonyms': ['signal type', 'instrument class']},
                                      'is_active': {'annotations': {},
                                                    'description': 'Whether the registry record is active. This '
                                                                   'is not a measurement of current health or '
                                                                   'telemetry freshness.',
                                                    'synonyms': ['active', 'enabled']},
                                      'opcua_node_id': {'annotations': {},
                                                        'description': 'Canonical OPC UA signal identifier '
                                                                       'shared by the static registry and '
                                                                       'telemetry event source.',
                                                        'synonyms': ['OPC UA node', 'node id', 'OPC node']},
                                      'quality': {'annotations': {},
                                                  'description': 'Quality of the same telemetry sample as '
                                                                 'event_time and value. A BAD sample is not '
                                                                 'proof of a mechanical fault.',
                                                  'synonyms': ['data quality', 'quality flag']},
                                      'signal_type': {'annotations': {},
                                                      'description': 'Canonical signal category used for signal '
                                                                     'inventory and classification, not a '
                                                                     'telemetry sample value.',
                                                      'synonyms': ['signal class', 'measurement type']},
                                      'system_id': {'annotations': {},
                                                    'description': 'Canonical system identifier linking the '
                                                                   'record to its plant subsystem.',
                                                    'synonyms': ['system id', 'unit id']},
                                      'tag': {'annotations': {},
                                              'description': 'Engineering tag recorded by the source; retain '
                                                             'canonical identifiers for joins rather than '
                                                             'relying on tag text.',
                                              'synonyms': ['asset tag', 'signal tag', 'point tag']},
                                      'unit': {'annotations': {},
                                               'description': 'Engineering unit of the signal or measurement. '
                                                              'Interpret values in this unit and do not silently '
                                                              'mix incompatible units.',
                                               'synonyms': ['measurement unit']},
                                      'value': {'annotations': {},
                                                'description': 'Historical telemetry value for this signal and '
                                                               'event_time, interpreted using its canonical '
                                                               'unit.',
                                                'synonyms': ['signal value', 'sample value', 'telemetry value']}},
                       'synonyms': ['Signal Master', 'Signal Registry', 'Tag', 'Signal', 'Sensor', 'Point']},
     'systems': {'annotations': {'domain': 'asset',
                                 'modelRole': 'dimension',
                                 'preferredUsage': 'system grouping and asset hierarchy',
                                 'queryHint': 'Use systems when aggregating equipment or instruments by '
                                              'subsystem or plant.'},
                 'description': 'Operational systems belonging to hydro facilities, such as turbine or plant '
                                'subsystems. Use this entity as the system-level grouping above equipment and '
                                'instruments.',
                 'properties': {'facility_id': {'annotations': {},
                                                'description': 'Canonical facility identifier linking the record '
                                                               'to its hydro site or plant.',
                                                'synonyms': ['facility id', 'plant id']},
                                'oag_rds_system_code': {'annotations': {},
                                                        'description': 'Operational or external code for the '
                                                                       'system.',
                                                        'synonyms': ['system code',
                                                                     'RDS code',
                                                                     'external system code']},
                                'system_id': {'annotations': {},
                                              'description': 'Canonical system identifier linking the record to '
                                                             'its plant subsystem.',
                                              'synonyms': ['system id', 'unit id']},
                                'system_name': {'annotations': {},
                                                'description': 'Display name of the system.',
                                                'synonyms': ['system name', 'unit name']}},
                 'synonyms': ['Turbine System', 'Asset System', 'Production System', 'Unit', 'Subsystem']}}

def _semantic_description(text, start, indent):
    lines = text[:start].splitlines()
    description = []
    while lines and lines[-1].startswith(indent + "///"):
        description.append(lines.pop()[len(indent) + 3:].strip())
    return " ".join(reversed(description)).strip()

def _enrich_semantic_block(block, metadata, indent):
    child = indent + "\t"
    additions = []
    synonyms = {
        value.strip("'").replace("''", "'").casefold()
        for value in re.findall(r"(?m)^" + re.escape(child) + r"synonym (.+?)[ \t]*$", block)
    }
    for synonym in metadata.get("synonyms", []):
        if synonym.casefold() not in synonyms:
            additions.append(child + "synonym " + _quote_name(synonym))
            synonyms.add(synonym.casefold())
    annotations = set(re.findall(
        r"(?m)^" + re.escape(child) + r"annotation ([A-Za-z_][A-Za-z_0-9]*)[ \t]*=", block))
    for key, value in metadata.get("annotations", {}).items():
        if key not in annotations:
            if "\n" in value or "\r" in value:
                raise RuntimeError(f"Multiline semantic annotation {key} needs explicit TMDL authoring.")
            additions.append(f"{child}annotation {key} = {value}")
    if not additions:
        return block
    lines = block.splitlines(keepends=True)
    trailing = []
    while lines and (not lines[-1].strip() or lines[-1].startswith(indent + "///")):
        trailing.append(lines.pop())
    return "".join(lines).rstrip() + "\n\n" + "\n".join(additions) + "\n" + "".join(reversed(trailing))

def _project_business_rules():
    return [
        ("Hydro_AnyBadSampleInWindow",
         "BAD telemetry analysis must include any BAD-quality sample in the requested time window, "
         "not only each signal's latest sample. State the evaluated time window and time zone.",
         {"signal_master": ("opcua_node_id", "event_time", "quality")}),
        ("Hydro_AlignedTelemetryHistory",
         "Join static signal identity and telemetry by the same canonical opcua_node_id. "
         "Keep event_time, value, and quality aligned to the same historical event; "
         "never combine independently selected latest values into a fictitious sample.",
         {"signal_master": ("opcua_node_id", "event_time", "value", "quality")}),
        ("Hydro_NoDuplicateMaintenance",
         "Reconcile affected signals with physical equipment and existing work orders by canonical "
         "equipment_id and opcua_node_id. Before proposing a new work order, identify existing "
         "open work covering the same issue and avoid duplicate maintenance.",
         {"signal_master": ("equipment_id", "opcua_node_id"),
          "WorkOrder": ("equipmentId", "opcuaNodeId", "status")}),
        ("Hydro_EvidenceBeforeRootCause",
         "BAD telemetry quality indicates an evidence-quality problem, not proof of mechanical failure. "
         "Separate observations, root-cause hypotheses, supporting evidence, and missing evidence. "
         "Do not present a hypothesis as a confirmed root cause.",
         {"signal_master": ("quality", "event_time", "value"), "equipment": ("equipment_id",)}),
        ("Hydro_OperatorApprovedWorkOrders",
         "Work-order recommendations are drafts. Do not create, modify, assign, or close operational "
         "records without the operator's explicit approval and successful authorized execution. "
         "Configuration or publication success is not evidence that a write or delivery occurred.",
         {"WorkOrder": ("title", "description", "status", "equipmentId")}),
        ("Hydro_WeatherEvidenceLimits",
         "Latest weather snapshots are for current-state questions, not historical trends. "
         "Report source, location or area, timestamp, units, and forecast horizon. "
         "Missing weather or failed ingestion is unavailable evidence, not zero weather or proof "
         "of causation for equipment faults.",
         {"WeatherLatestObservation": ("source_id", "location_id", "observed_at_utc"),
          "WeatherLatestForecast": ("source_id", "target_kind", "target_id", "valid_time_utc")}),
    ]

def _enrich_project_semantics(parts):
    result = _parts_by_path(parts)
    catalog = _project_semantics()
    present = {}
    for name, metadata in catalog.items():
        path = f"entities/{name}.tmdl"
        if path not in result:
            continue
        text = _decode_part(result[path])
        entities = _object_spans(text, "entity", 0)
        if len(entities) != 1 or entities[0][0] != name:
            raise RuntimeError(f"Semantic metadata cannot change ambiguous entity identity in {path}.")
        properties = _object_spans(text, "property", 4)
        present[name] = {item[0] for item in properties}
        for prop, start, end, block in reversed(properties):
            spec = metadata.get("properties", {}).get(prop)
            if not spec:
                continue
            replacement = _enrich_semantic_block(block, spec, "\t")
            if spec.get("description") and not _semantic_description(text, start, "\t"):
                replacement = "\t/// " + spec["description"] + "\n" + replacement
            text = text[:start] + replacement + text[end:]
        entity = _object_spans(text, "entity", 0)[0]
        replacement = _enrich_semantic_block(entity[3], metadata, "")
        if metadata.get("description") and not _semantic_description(text, entity[1], ""):
            replacement = "/// " + metadata["description"] + "\n" + replacement
        text = text[:entity[1]] + replacement + text[entity[2]:]
        if text != _decode_part(result[path]):
            result[path] = _encode_part(path, text)
    if "model.tmdl" not in result:
        raise RuntimeError("Project semantics require the existing TMDL model.")
    model = _decode_part(result["model.tmdl"])
    for name, statement, references in _project_business_rules():
        if not set(references).issubset(present):
            continue
        rule = [f"rule {name}", f"\tstatement: {statement}"]
        for entity, properties in references.items():
            if not set(properties).issubset(present[entity]):
                raise RuntimeError(f"Business rule {name} references missing properties of {entity}.")
            rule.extend(("", f"\truleReferencedEntity {entity}", "\t\tpropertyScope: specific", ""))
            rule.extend(f"\t\truleReferencedProperty {prop}" for prop in properties)
        path = f"rules/{name}.tmdl"
        wanted = "\n".join(rule) + "\n"
        if path in result:
            if _structural_lines(wanted) - _structural_lines(_decode_part(result[path])):
                raise RuntimeError(f"Existing project business rule {name} conflicts; preserve and review it explicitly.")
        else:
            result[path] = _encode_part(path, wanted)
        model = _append_model_ref(model, "rule", name)
    if model != _decode_part(result["model.tmdl"]):
        result["model.tmdl"] = _encode_part("model.tmdl", model)
    return list(result.values())

def _verify_semantic_descriptions(expected, actual):
    retained = _parts_by_path(actual)
    managed = {f"entities/{name}.tmdl" for name in _project_semantics()}
    for path, part in _parts_by_path(expected).items():
        if path not in managed:
            continue
        before, after = _decode_part(part), _decode_part(retained[path])
        for kind, depth, indent in (("entity", 0, ""), ("property", 4, "\t")):
            objects = {name: start for name, start, _, _ in _object_spans(after, kind, depth)}
            for name, start, _, _ in _object_spans(before, kind, depth):
                description = _semantic_description(before, start, indent)
                if description and (name not in objects
                                    or _semantic_description(after, objects[name], indent) != description):
                    raise RuntimeError(f"Fabric did not retain the description of {path}:{name}.")

def _identifier_parts(value):
    tokens = re.findall(r"'(?:[^']|'')*'|[^.\s]+", value.strip())
    if ".".join(tokens) != value.strip():
        raise RuntimeError(f"Unsupported or ambiguous TMDL reference: {value!r}")
    return [token[1:-1].replace("''", "'") if token.startswith("'") else token
            for token in tokens]

def _local_name(value):
    names = _identifier_parts(value)
    if len(names) == 2 and names[0] == "default":
        return names[1]
    if len(names) != 1:
        raise RuntimeError(f"Only the default namespace is managed: {value!r}")
    return names[0]

def _quote_name(name):
    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"

def _object_spans(text, kind, depth=None):
    """Locate named objects, stopping at *any* same/outer-indent sibling."""
    text = _normal_text(text)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = []
    for index, line in enumerate(lines):
        match = re.match(r"^([ \t]*)" + re.escape(kind) + r" (.+?)\s*$", line)
        if not match:
            continue
        indentation = len(match[1].expandtabs(4))
        if depth is not None and indentation != depth:
            continue
        # '=' introduces expression/partition content, outside the identifier.
        raw_name = re.split(r"\s+=\s*|\s*=$", match[2], maxsplit=1)[0]
        name = _local_name(raw_name)
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and (
                    not candidate.lstrip().startswith("//") or candidate.lstrip().startswith("///")):
                size = len(candidate) - len(candidate.lstrip(" \t"))
                if len(candidate[:size].expandtabs(4)) <= indentation:
                    break
            end += 1
        result.append((name, offsets[index], offsets[end], text[offsets[index]:offsets[end]]))
    names = [item[0] for item in result]
    if len(names) != len(set(names)):
        raise RuntimeError(f"Duplicate {kind} object names")
    return result

def _fabric_operation_url(response, api_root):
    headers = {name.lower(): value for name, value in response.headers.items()}
    operation_id = headers.get("x-ms-operation-id")
    if operation_id:
        if not re.fullmatch(r"[A-Za-z0-9-]+", operation_id):
            raise RuntimeError("Invalid Fabric operation ID")
        return api_root.rstrip("/") + "/operations/" + operation_id
    location = headers.get("operation-location") or headers.get("location")
    if not location:
        raise RuntimeError("Fabric LRO has neither an operation ID nor a Location header")
    url = urljoin(api_root.rstrip("/") + "/", location)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    trusted = any(host == domain or host.endswith("." + domain)
                  for domain in ("fabric.microsoft.com", "analysis.windows.net", "api.powerbi.com"))
    if parsed.scheme != "https" or not trusted or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise RuntimeError("Fabric LRO Location is not a trusted HTTPS Fabric endpoint")
    return url

def _fabric_result_url(operation_url):
    parsed = urlsplit(operation_url)
    return urlunsplit(parsed._replace(path=parsed.path.rstrip("/") + "/result"))

def _structural_lines(text):
    """An object-scoped readback contract: mappings cannot migrate to siblings."""
    stack = []
    result = Counter()
    for line in _normal_text(text).splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        prefix = line[:len(line) - len(line.lstrip(" \t"))]
        depth = len(prefix.expandtabs(4))
        statement = line.strip()
        match = re.match(r"(ref (?:entity|table|namespace|rule)|entity|table|column|property|entityRelationship|relationship|synonym|rule|ruleReferencedEntity|ruleReferencedProperty|ruleReferencedRelationship) (.+)$", statement)
        if match:
            statement = match[1] + " " + ".".join(_identifier_parts(match[2]))
        else:
            match = re.match(r"(keyProperty|backingTable|fromEntity|toEntity|valueColumn|orderingColumn|fromColumn|toColumn|table|relationship):\s*(.+)$", statement)
            if match:
                names = _identifier_parts(match[2])
                if len(names) > 1 and names[0] == "default":
                    names = names[1:]
                statement = match[1] + ": " + ".".join(names)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, statement))
        result[tuple(value for _, value in stack)] += 1
    return result

def _json_contains(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _json_contains(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_json_contains(value, candidate) for candidate in actual) for value in expected)
    return expected == actual

def _verify_definition(expected, actual):
    wanted = _parts_by_path(expected)
    retained = _parts_by_path(actual)
    for path, part in wanted.items():
        if path not in retained:
            raise RuntimeError(f"Fabric did not retain definition part {path}")
        before, after = _decode_part(part), _decode_part(retained[path])
        if path.endswith(".tmdl"):
            missing = _structural_lines(before) - _structural_lines(after)
            if missing:
                raise RuntimeError(f"Fabric did not retain submitted objects/settings in {path}: {list(missing)[:3]}")
        elif path.endswith(".json") or path == ".platform":
            if not _json_contains(json.loads(before), json.loads(after)):
                raise RuntimeError(f"Fabric did not retain submitted JSON in {path}")
        elif before != after:
            raise RuntimeError(f"Fabric changed unmanaged part {path}")
    _verify_semantic_descriptions(expected, actual)

def _append_model_ref(model, kind, name):
    for line in model.splitlines():
        match = re.fullmatch(r"ref " + re.escape(kind) + r" (.+)", line)
        if match and _local_name(match[1]) == name:
            return model
    return model.rstrip() + f"\nref {kind} {_quote_name(name)}\n"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# EMBEDDED STID SEED DATA + OneLake writer
# --------------------------------------------
# The four STID CSVs are embedded here so this notebook is fully self-contained:
# no ADLS Gen2 account, cloud connection, or shortcut is required. They are written
# directly into the new Lakehouse at Files/bronze/stid/ via the explicit OneLake
# ABFS path (no default lakehouse attach needed).

STID_FILES = {
    "facilities_stid.csv": (
        "facility_id,facility_name,type,country,lat,lon,commissioned_date\n"
        "FACILITY_RTI_001,Sloy Power Station,Hydropower,GB,56.2512,-4.7117,2015-06-01\n"
        "FACILITY_RTI_002,Foyers Power Station,Hydropower,GB,57.2600,-4.5200,2012-05-01\n"
        "FACILITY_RTI_003,Pitlochry Power Station,Hydropower,GB,56.7050,-3.7350,2018-09-01\n"
    ),
    "systems_stid.csv": (
        "system_id,facility_id,system_name,oag_rds_system_code\n"
        "SYSTEM_RTI_001,FACILITY_RTI_001,RTI Turbine System,TURBINE\n"
        "SYSTEM_RTI_002,FACILITY_RTI_002,RTI Turbine System,TURBINE\n"
        "SYSTEM_RTI_003,FACILITY_RTI_003,RTI Turbine System,TURBINE\n"
    ),
    "equipment_stid.csv": (
        "equipment_id,facility_id,system_id,equipment_type_code,equipment_type_name,tag,manufacturer,model,criticality,install_date,status\n"
        "EQUIP_RTI_T001,FACILITY_RTI_001,SYSTEM_RTI_001,TURB,Turbine,T001,Andritz,RTI-Turbine-A,1,2016-07-01,ACTIVE\n"
        "EQUIP_RTI_T002,FACILITY_RTI_001,SYSTEM_RTI_001,TURB,Turbine,T002,Voith,RTI-Turbine-B,1,2017-07-01,ACTIVE\n"
        "EQUIP_RTI_T003,FACILITY_RTI_001,SYSTEM_RTI_001,TURB,Turbine,T003,GE Vernova,RTI-Turbine-C,1,2018-07-01,ACTIVE\n"
        "EQUIP_RTI_T004,FACILITY_RTI_001,SYSTEM_RTI_001,TURB,Turbine,T004,Toshiba,RTI-Turbine-D,1,2019-07-01,ACTIVE\n"
        "EQUIP_RTI_T005,FACILITY_RTI_001,SYSTEM_RTI_001,TURB,Turbine,T005,Hitachi Energy,RTI-Turbine-E,1,2020-07-01,ACTIVE\n"
        "EQUIP_RTI_T006,FACILITY_RTI_002,SYSTEM_RTI_002,TURB,Turbine,T006,Andritz,RTI-Turbine-A,1,2013-07-01,ACTIVE\n"
        "EQUIP_RTI_T007,FACILITY_RTI_002,SYSTEM_RTI_002,TURB,Turbine,T007,Voith,RTI-Turbine-B,1,2014-07-01,ACTIVE\n"
        "EQUIP_RTI_T008,FACILITY_RTI_002,SYSTEM_RTI_002,TURB,Turbine,T008,GE Vernova,RTI-Turbine-C,1,2015-07-01,ACTIVE\n"
        "EQUIP_RTI_T009,FACILITY_RTI_002,SYSTEM_RTI_002,TURB,Turbine,T009,Toshiba,RTI-Turbine-D,1,2016-07-01,ACTIVE\n"
        "EQUIP_RTI_T010,FACILITY_RTI_002,SYSTEM_RTI_002,TURB,Turbine,T010,Hitachi Energy,RTI-Turbine-E,1,2017-07-01,ACTIVE\n"
        "EQUIP_RTI_T011,FACILITY_RTI_003,SYSTEM_RTI_003,TURB,Turbine,T011,Andritz,RTI-Turbine-A,1,2019-07-01,ACTIVE\n"
        "EQUIP_RTI_T012,FACILITY_RTI_003,SYSTEM_RTI_003,TURB,Turbine,T012,Voith,RTI-Turbine-B,1,2020-07-01,ACTIVE\n"
        "EQUIP_RTI_T013,FACILITY_RTI_003,SYSTEM_RTI_003,TURB,Turbine,T013,GE Vernova,RTI-Turbine-C,1,2021-07-01,ACTIVE\n"
        "EQUIP_RTI_T014,FACILITY_RTI_003,SYSTEM_RTI_003,TURB,Turbine,T014,Toshiba,RTI-Turbine-D,1,2022-07-01,ACTIVE\n"
        "EQUIP_RTI_T015,FACILITY_RTI_003,SYSTEM_RTI_003,TURB,Turbine,T015,Hitachi Energy,RTI-Turbine-E,1,2023-07-01,ACTIVE\n"
    ),
    "instruments_stid.csv": (
        "instrument_id,equipment_id,facility_id,system_id,instrument_type,tag,unit,opcua_node_id,range_low,range_high,sample_rate_hz\n"
        "INST_T001_INLET_PRESSURE,EQUIP_RTI_T001,FACILITY_RTI_001,SYSTEM_RTI_001,pressure,inlet_pressure,bar,ns=2;s=T001.inlet_pressure,5,25,1.0\n"
        "INST_T001_POWER_OUTPUT,EQUIP_RTI_T001,FACILITY_RTI_001,SYSTEM_RTI_001,power,power_output,MW,ns=2;s=T001.power_output,0,120,1.0\n"
        "INST_T001_TURBINE_SPEED,EQUIP_RTI_T001,FACILITY_RTI_001,SYSTEM_RTI_001,speed,turbine_speed,rpm,ns=2;s=T001.turbine_speed,290,310,1.0\n"
        "INST_T001_TURBINE_TEMP,EQUIP_RTI_T001,FACILITY_RTI_001,SYSTEM_RTI_001,temperature,turbine_temp,C,ns=2;s=T001.turbine_temp,30,110,0.2\n"
        "INST_T001_VIBRATION_A,EQUIP_RTI_T001,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_a,mm_s,ns=2;s=T001.vibration_a,0,45,1.0\n"
        "INST_T001_VIBRATION_D,EQUIP_RTI_T001,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_d,mm_s,ns=2;s=T001.vibration_d,0,45,1.0\n"
        "INST_T002_INLET_PRESSURE,EQUIP_RTI_T002,FACILITY_RTI_001,SYSTEM_RTI_001,pressure,inlet_pressure,bar,ns=2;s=T002.inlet_pressure,5,25,1.0\n"
        "INST_T002_POWER_OUTPUT,EQUIP_RTI_T002,FACILITY_RTI_001,SYSTEM_RTI_001,power,power_output,MW,ns=2;s=T002.power_output,0,120,1.0\n"
        "INST_T002_TURBINE_SPEED,EQUIP_RTI_T002,FACILITY_RTI_001,SYSTEM_RTI_001,speed,turbine_speed,rpm,ns=2;s=T002.turbine_speed,290,310,1.0\n"
        "INST_T002_TURBINE_TEMP,EQUIP_RTI_T002,FACILITY_RTI_001,SYSTEM_RTI_001,temperature,turbine_temp,C,ns=2;s=T002.turbine_temp,30,110,0.2\n"
        "INST_T002_VIBRATION_A,EQUIP_RTI_T002,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_a,mm_s,ns=2;s=T002.vibration_a,0,45,1.0\n"
        "INST_T002_VIBRATION_D,EQUIP_RTI_T002,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_d,mm_s,ns=2;s=T002.vibration_d,0,45,1.0\n"
        "INST_T003_INLET_PRESSURE,EQUIP_RTI_T003,FACILITY_RTI_001,SYSTEM_RTI_001,pressure,inlet_pressure,bar,ns=2;s=T003.inlet_pressure,5,25,1.0\n"
        "INST_T003_POWER_OUTPUT,EQUIP_RTI_T003,FACILITY_RTI_001,SYSTEM_RTI_001,power,power_output,MW,ns=2;s=T003.power_output,0,120,1.0\n"
        "INST_T003_TURBINE_SPEED,EQUIP_RTI_T003,FACILITY_RTI_001,SYSTEM_RTI_001,speed,turbine_speed,rpm,ns=2;s=T003.turbine_speed,290,310,1.0\n"
        "INST_T003_TURBINE_TEMP,EQUIP_RTI_T003,FACILITY_RTI_001,SYSTEM_RTI_001,temperature,turbine_temp,C,ns=2;s=T003.turbine_temp,30,110,0.2\n"
        "INST_T003_VIBRATION_A,EQUIP_RTI_T003,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_a,mm_s,ns=2;s=T003.vibration_a,0,45,1.0\n"
        "INST_T003_VIBRATION_D,EQUIP_RTI_T003,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_d,mm_s,ns=2;s=T003.vibration_d,0,45,1.0\n"
        "INST_T004_INLET_PRESSURE,EQUIP_RTI_T004,FACILITY_RTI_001,SYSTEM_RTI_001,pressure,inlet_pressure,bar,ns=2;s=T004.inlet_pressure,5,25,1.0\n"
        "INST_T004_POWER_OUTPUT,EQUIP_RTI_T004,FACILITY_RTI_001,SYSTEM_RTI_001,power,power_output,MW,ns=2;s=T004.power_output,0,120,1.0\n"
        "INST_T004_TURBINE_SPEED,EQUIP_RTI_T004,FACILITY_RTI_001,SYSTEM_RTI_001,speed,turbine_speed,rpm,ns=2;s=T004.turbine_speed,290,310,1.0\n"
        "INST_T004_TURBINE_TEMP,EQUIP_RTI_T004,FACILITY_RTI_001,SYSTEM_RTI_001,temperature,turbine_temp,C,ns=2;s=T004.turbine_temp,30,110,0.2\n"
        "INST_T004_VIBRATION_A,EQUIP_RTI_T004,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_a,mm_s,ns=2;s=T004.vibration_a,0,45,1.0\n"
        "INST_T004_VIBRATION_D,EQUIP_RTI_T004,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_d,mm_s,ns=2;s=T004.vibration_d,0,45,1.0\n"
        "INST_T005_INLET_PRESSURE,EQUIP_RTI_T005,FACILITY_RTI_001,SYSTEM_RTI_001,pressure,inlet_pressure,bar,ns=2;s=T005.inlet_pressure,5,25,1.0\n"
        "INST_T005_POWER_OUTPUT,EQUIP_RTI_T005,FACILITY_RTI_001,SYSTEM_RTI_001,power,power_output,MW,ns=2;s=T005.power_output,0,120,1.0\n"
        "INST_T005_TURBINE_SPEED,EQUIP_RTI_T005,FACILITY_RTI_001,SYSTEM_RTI_001,speed,turbine_speed,rpm,ns=2;s=T005.turbine_speed,290,310,1.0\n"
        "INST_T005_TURBINE_TEMP,EQUIP_RTI_T005,FACILITY_RTI_001,SYSTEM_RTI_001,temperature,turbine_temp,C,ns=2;s=T005.turbine_temp,30,110,0.2\n"
        "INST_T005_VIBRATION_A,EQUIP_RTI_T005,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_a,mm_s,ns=2;s=T005.vibration_a,0,45,1.0\n"
        "INST_T005_VIBRATION_D,EQUIP_RTI_T005,FACILITY_RTI_001,SYSTEM_RTI_001,vibration,vibration_d,mm_s,ns=2;s=T005.vibration_d,0,45,1.0\n"
        "INST_T006_INLET_PRESSURE,EQUIP_RTI_T006,FACILITY_RTI_002,SYSTEM_RTI_002,pressure,inlet_pressure,bar,ns=2;s=T006.inlet_pressure,5,25,1.0\n"
        "INST_T006_POWER_OUTPUT,EQUIP_RTI_T006,FACILITY_RTI_002,SYSTEM_RTI_002,power,power_output,MW,ns=2;s=T006.power_output,0,120,1.0\n"
        "INST_T006_TURBINE_SPEED,EQUIP_RTI_T006,FACILITY_RTI_002,SYSTEM_RTI_002,speed,turbine_speed,rpm,ns=2;s=T006.turbine_speed,290,310,1.0\n"
        "INST_T006_TURBINE_TEMP,EQUIP_RTI_T006,FACILITY_RTI_002,SYSTEM_RTI_002,temperature,turbine_temp,C,ns=2;s=T006.turbine_temp,30,110,0.2\n"
        "INST_T006_VIBRATION_A,EQUIP_RTI_T006,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_a,mm_s,ns=2;s=T006.vibration_a,0,45,1.0\n"
        "INST_T006_VIBRATION_D,EQUIP_RTI_T006,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_d,mm_s,ns=2;s=T006.vibration_d,0,45,1.0\n"
        "INST_T007_INLET_PRESSURE,EQUIP_RTI_T007,FACILITY_RTI_002,SYSTEM_RTI_002,pressure,inlet_pressure,bar,ns=2;s=T007.inlet_pressure,5,25,1.0\n"
        "INST_T007_POWER_OUTPUT,EQUIP_RTI_T007,FACILITY_RTI_002,SYSTEM_RTI_002,power,power_output,MW,ns=2;s=T007.power_output,0,120,1.0\n"
        "INST_T007_TURBINE_SPEED,EQUIP_RTI_T007,FACILITY_RTI_002,SYSTEM_RTI_002,speed,turbine_speed,rpm,ns=2;s=T007.turbine_speed,290,310,1.0\n"
        "INST_T007_TURBINE_TEMP,EQUIP_RTI_T007,FACILITY_RTI_002,SYSTEM_RTI_002,temperature,turbine_temp,C,ns=2;s=T007.turbine_temp,30,110,0.2\n"
        "INST_T007_VIBRATION_A,EQUIP_RTI_T007,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_a,mm_s,ns=2;s=T007.vibration_a,0,45,1.0\n"
        "INST_T007_VIBRATION_D,EQUIP_RTI_T007,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_d,mm_s,ns=2;s=T007.vibration_d,0,45,1.0\n"
        "INST_T008_INLET_PRESSURE,EQUIP_RTI_T008,FACILITY_RTI_002,SYSTEM_RTI_002,pressure,inlet_pressure,bar,ns=2;s=T008.inlet_pressure,5,25,1.0\n"
        "INST_T008_POWER_OUTPUT,EQUIP_RTI_T008,FACILITY_RTI_002,SYSTEM_RTI_002,power,power_output,MW,ns=2;s=T008.power_output,0,120,1.0\n"
        "INST_T008_TURBINE_SPEED,EQUIP_RTI_T008,FACILITY_RTI_002,SYSTEM_RTI_002,speed,turbine_speed,rpm,ns=2;s=T008.turbine_speed,290,310,1.0\n"
        "INST_T008_TURBINE_TEMP,EQUIP_RTI_T008,FACILITY_RTI_002,SYSTEM_RTI_002,temperature,turbine_temp,C,ns=2;s=T008.turbine_temp,30,110,0.2\n"
        "INST_T008_VIBRATION_A,EQUIP_RTI_T008,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_a,mm_s,ns=2;s=T008.vibration_a,0,45,1.0\n"
        "INST_T008_VIBRATION_D,EQUIP_RTI_T008,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_d,mm_s,ns=2;s=T008.vibration_d,0,45,1.0\n"
        "INST_T009_INLET_PRESSURE,EQUIP_RTI_T009,FACILITY_RTI_002,SYSTEM_RTI_002,pressure,inlet_pressure,bar,ns=2;s=T009.inlet_pressure,5,25,1.0\n"
        "INST_T009_POWER_OUTPUT,EQUIP_RTI_T009,FACILITY_RTI_002,SYSTEM_RTI_002,power,power_output,MW,ns=2;s=T009.power_output,0,120,1.0\n"
        "INST_T009_TURBINE_SPEED,EQUIP_RTI_T009,FACILITY_RTI_002,SYSTEM_RTI_002,speed,turbine_speed,rpm,ns=2;s=T009.turbine_speed,290,310,1.0\n"
        "INST_T009_TURBINE_TEMP,EQUIP_RTI_T009,FACILITY_RTI_002,SYSTEM_RTI_002,temperature,turbine_temp,C,ns=2;s=T009.turbine_temp,30,110,0.2\n"
        "INST_T009_VIBRATION_A,EQUIP_RTI_T009,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_a,mm_s,ns=2;s=T009.vibration_a,0,45,1.0\n"
        "INST_T009_VIBRATION_D,EQUIP_RTI_T009,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_d,mm_s,ns=2;s=T009.vibration_d,0,45,1.0\n"
        "INST_T010_INLET_PRESSURE,EQUIP_RTI_T010,FACILITY_RTI_002,SYSTEM_RTI_002,pressure,inlet_pressure,bar,ns=2;s=T010.inlet_pressure,5,25,1.0\n"
        "INST_T010_POWER_OUTPUT,EQUIP_RTI_T010,FACILITY_RTI_002,SYSTEM_RTI_002,power,power_output,MW,ns=2;s=T010.power_output,0,120,1.0\n"
        "INST_T010_TURBINE_SPEED,EQUIP_RTI_T010,FACILITY_RTI_002,SYSTEM_RTI_002,speed,turbine_speed,rpm,ns=2;s=T010.turbine_speed,290,310,1.0\n"
        "INST_T010_TURBINE_TEMP,EQUIP_RTI_T010,FACILITY_RTI_002,SYSTEM_RTI_002,temperature,turbine_temp,C,ns=2;s=T010.turbine_temp,30,110,0.2\n"
        "INST_T010_VIBRATION_A,EQUIP_RTI_T010,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_a,mm_s,ns=2;s=T010.vibration_a,0,45,1.0\n"
        "INST_T010_VIBRATION_D,EQUIP_RTI_T010,FACILITY_RTI_002,SYSTEM_RTI_002,vibration,vibration_d,mm_s,ns=2;s=T010.vibration_d,0,45,1.0\n"
        "INST_T011_INLET_PRESSURE,EQUIP_RTI_T011,FACILITY_RTI_003,SYSTEM_RTI_003,pressure,inlet_pressure,bar,ns=2;s=T011.inlet_pressure,5,25,1.0\n"
        "INST_T011_POWER_OUTPUT,EQUIP_RTI_T011,FACILITY_RTI_003,SYSTEM_RTI_003,power,power_output,MW,ns=2;s=T011.power_output,0,120,1.0\n"
        "INST_T011_TURBINE_SPEED,EQUIP_RTI_T011,FACILITY_RTI_003,SYSTEM_RTI_003,speed,turbine_speed,rpm,ns=2;s=T011.turbine_speed,290,310,1.0\n"
        "INST_T011_TURBINE_TEMP,EQUIP_RTI_T011,FACILITY_RTI_003,SYSTEM_RTI_003,temperature,turbine_temp,C,ns=2;s=T011.turbine_temp,30,110,0.2\n"
        "INST_T011_VIBRATION_A,EQUIP_RTI_T011,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_a,mm_s,ns=2;s=T011.vibration_a,0,45,1.0\n"
        "INST_T011_VIBRATION_D,EQUIP_RTI_T011,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_d,mm_s,ns=2;s=T011.vibration_d,0,45,1.0\n"
        "INST_T012_INLET_PRESSURE,EQUIP_RTI_T012,FACILITY_RTI_003,SYSTEM_RTI_003,pressure,inlet_pressure,bar,ns=2;s=T012.inlet_pressure,5,25,1.0\n"
        "INST_T012_POWER_OUTPUT,EQUIP_RTI_T012,FACILITY_RTI_003,SYSTEM_RTI_003,power,power_output,MW,ns=2;s=T012.power_output,0,120,1.0\n"
        "INST_T012_TURBINE_SPEED,EQUIP_RTI_T012,FACILITY_RTI_003,SYSTEM_RTI_003,speed,turbine_speed,rpm,ns=2;s=T012.turbine_speed,290,310,1.0\n"
        "INST_T012_TURBINE_TEMP,EQUIP_RTI_T012,FACILITY_RTI_003,SYSTEM_RTI_003,temperature,turbine_temp,C,ns=2;s=T012.turbine_temp,30,110,0.2\n"
        "INST_T012_VIBRATION_A,EQUIP_RTI_T012,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_a,mm_s,ns=2;s=T012.vibration_a,0,45,1.0\n"
        "INST_T012_VIBRATION_D,EQUIP_RTI_T012,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_d,mm_s,ns=2;s=T012.vibration_d,0,45,1.0\n"
        "INST_T013_INLET_PRESSURE,EQUIP_RTI_T013,FACILITY_RTI_003,SYSTEM_RTI_003,pressure,inlet_pressure,bar,ns=2;s=T013.inlet_pressure,5,25,1.0\n"
        "INST_T013_POWER_OUTPUT,EQUIP_RTI_T013,FACILITY_RTI_003,SYSTEM_RTI_003,power,power_output,MW,ns=2;s=T013.power_output,0,120,1.0\n"
        "INST_T013_TURBINE_SPEED,EQUIP_RTI_T013,FACILITY_RTI_003,SYSTEM_RTI_003,speed,turbine_speed,rpm,ns=2;s=T013.turbine_speed,290,310,1.0\n"
        "INST_T013_TURBINE_TEMP,EQUIP_RTI_T013,FACILITY_RTI_003,SYSTEM_RTI_003,temperature,turbine_temp,C,ns=2;s=T013.turbine_temp,30,110,0.2\n"
        "INST_T013_VIBRATION_A,EQUIP_RTI_T013,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_a,mm_s,ns=2;s=T013.vibration_a,0,45,1.0\n"
        "INST_T013_VIBRATION_D,EQUIP_RTI_T013,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_d,mm_s,ns=2;s=T013.vibration_d,0,45,1.0\n"
        "INST_T014_INLET_PRESSURE,EQUIP_RTI_T014,FACILITY_RTI_003,SYSTEM_RTI_003,pressure,inlet_pressure,bar,ns=2;s=T014.inlet_pressure,5,25,1.0\n"
        "INST_T014_POWER_OUTPUT,EQUIP_RTI_T014,FACILITY_RTI_003,SYSTEM_RTI_003,power,power_output,MW,ns=2;s=T014.power_output,0,120,1.0\n"
        "INST_T014_TURBINE_SPEED,EQUIP_RTI_T014,FACILITY_RTI_003,SYSTEM_RTI_003,speed,turbine_speed,rpm,ns=2;s=T014.turbine_speed,290,310,1.0\n"
        "INST_T014_TURBINE_TEMP,EQUIP_RTI_T014,FACILITY_RTI_003,SYSTEM_RTI_003,temperature,turbine_temp,C,ns=2;s=T014.turbine_temp,30,110,0.2\n"
        "INST_T014_VIBRATION_A,EQUIP_RTI_T014,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_a,mm_s,ns=2;s=T014.vibration_a,0,45,1.0\n"
        "INST_T014_VIBRATION_D,EQUIP_RTI_T014,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_d,mm_s,ns=2;s=T014.vibration_d,0,45,1.0\n"
        "INST_T015_INLET_PRESSURE,EQUIP_RTI_T015,FACILITY_RTI_003,SYSTEM_RTI_003,pressure,inlet_pressure,bar,ns=2;s=T015.inlet_pressure,5,25,1.0\n"
        "INST_T015_POWER_OUTPUT,EQUIP_RTI_T015,FACILITY_RTI_003,SYSTEM_RTI_003,power,power_output,MW,ns=2;s=T015.power_output,0,120,1.0\n"
        "INST_T015_TURBINE_SPEED,EQUIP_RTI_T015,FACILITY_RTI_003,SYSTEM_RTI_003,speed,turbine_speed,rpm,ns=2;s=T015.turbine_speed,290,310,1.0\n"
        "INST_T015_TURBINE_TEMP,EQUIP_RTI_T015,FACILITY_RTI_003,SYSTEM_RTI_003,temperature,turbine_temp,C,ns=2;s=T015.turbine_temp,30,110,0.2\n"
        "INST_T015_VIBRATION_A,EQUIP_RTI_T015,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_a,mm_s,ns=2;s=T015.vibration_a,0,45,1.0\n"
        "INST_T015_VIBRATION_D,EQUIP_RTI_T015,FACILITY_RTI_003,SYSTEM_RTI_003,vibration,vibration_d,mm_s,ns=2;s=T015.vibration_d,0,45,1.0\n"
    ),
}


def seed_stid_files(
    workspace_id: str,
    lakehouse_id: str,
    bronze_root: str,
    stid_files: dict,
) -> None:
    """
    Write each embedded STID CSV to Files/<bronze_root>/stid/ inside the new Lakehouse
    using the explicit OneLake ABFS path. notebookutils.fs.put writes one real named
    file (unlike Spark .write.csv, which produces a folder of part-* files).
    """
    stid_dir = (
        f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/"
        f"{lakehouse_id}/{bronze_root}/stid"
    )

    for file_name, content in stid_files.items():
        file_path = f"{stid_dir}/{file_name}"
        notebookutils.fs.put(file_path, content, True)  # overwrite=True
        print(f"✅ Seeded {file_path} ({len(content)} bytes)")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --------------------------------------------
# MAIN EXECUTION
# --------------------------------------------

print("=== STEP 1: Get SPN access token via Key Vault (NotebookUtils) ===")
access_token = get_spn_access_token()


print("\n=== STEP 2: Ensure Fabric workspace folder exists ===")
target_folder_id = get_or_create_workspace_folder_path(
    workspace_id=workspace_id,
    workspace_folder_path=workspace_folder_path,
    access_token=access_token,
)

print("Target folder path:", workspace_folder_path)
print("Target folder ID:", target_folder_id)


print("\n=== STEP 3: Ensure Lakehouse exists in target folder (create or reuse) ===")
lakehouse_id = ensure_lakehouse(
    workspace_id=workspace_id,
    lakehouse_name=lakehouse_name,
    lakehouse_description=lakehouse_description,
    access_token=access_token,
    target_folder_id=target_folder_id,
)

print("Lakehouse ID:", lakehouse_id)

def shared_sql_request(method, path, payload=None):
    return requests.request(
        method, path if path.startswith("https://") else f"https://api.fabric.microsoft.com/v1/{path}",
        headers={"Authorization": f"Bearer {get_spn_access_token()}", "Content-Type": "application/json"},
        json=payload, timeout=60,
    )


operational_store = ensure_operational_item(
    shared_sql_request, workspace_id, env_suffix=env_suffix, folder_id=target_folder_id)
operational_sql_connection = operational_connection(
    operational_store, lambda: get_spn_access_token("https://database.windows.net/.default"))
try:
    initialize_operational_schema(operational_sql_connection)
finally:
    operational_sql_connection.close()
print("Shared operational SQL Database:", operational_store["id"])


print(f"\n=== STEP 4: Seed STID files into {bronze_root}/stid (self-contained) ===")
seed_stid_files(
    workspace_id=workspace_id,
    lakehouse_id=lakehouse_id,
    bronze_root=bronze_root,
    stid_files=STID_FILES,
)

print(
    "\n🎉 Done! The STID seed files are now in "
    f"'{bronze_root}/stid' inside the Lakehouse.\n"
    "You can now proceed to the medallion ingestion notebook.\n"
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --------------------------------------------
# WRITE SHARED SETTINGS TABLE
# --------------------------------------------
# Write via the explicit OneLake ABFS path so this cell does NOT depend on a
# default lakehouse being attached to the notebook session. This avoids the
# "No default context found, please attach a lakehouse before running spark sql
# queries with partial namespaces" error when the freshly created lakehouse is
# not (yet) the notebook's default lakehouse.
from pyspark.sql.types import StructType, StructField, StringType
from pyspark.sql import functions as F

settings_schema = StructType([
    StructField("setting_name", StringType(), False),
    StructField("setting_value", StringType(), True),
    StructField("updated_utc", StringType(), False),
])

settings_rows = build_rti_demo_settings_rows(
    extra_settings={
        # Only genuinely runtime-derived IDs belong here; all config-time params live in the base dict.
        "lakehouse_id": lakehouse_id,
        "target_folder_id": target_folder_id,
        "operational_sql_db_id": operational_store["id"],
        "operational_sql_db_name": operational_store["displayName"],
    }
)

settings_df = spark.createDataFrame(settings_rows, schema=settings_schema)

# Fully-qualified OneLake path to the Delta table inside the new lakehouse.
settings_table_path = (
    f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/"
    f"{lakehouse_id}/Tables/{settings_table_name}"
)

(
    settings_df
    .withColumn("updated_utc", F.to_timestamp("updated_utc"))
    .write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .save(settings_table_path)
)

print(f"✅ Wrote shared settings table to: {settings_table_path}")

# Print every setting written so the full configuration is visible in the run log.
print(f"\n=== rti_demo_settings ({len(settings_rows)} settings) ===")
for _row in sorted(settings_rows, key=lambda r: r["setting_name"]):
    print(f"  {_row['setting_name']:<34} = {_row['setting_value']}")

display(spark.read.format("delta").load(settings_table_path).orderBy("setting_name"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --------------------------------------------
# BIND EACH DOWNSTREAM NOTEBOOK TO THE (NEW) LAKEHOUSE — AND ONLY THAT ONE
# --------------------------------------------
# Goal: every downstream notebook should reference exactly ONE lakehouse in its
# definition — the one created in THIS run — with no leftover (orphaned)
# lakehouses from a previous deploy or tenant.
#
#
# NOTE: A notebook's default lakehouse binds when its Spark session STARTS, so
# this can't change an already-running session; it updates each DOWNSTREAM
# notebook's SAVED definition, taking effect the next time that notebook starts.
#
# RTI_001 (this notebook) is intentionally excluded: Fabric forbids a notebook
# from updating its own definition ("Cannot update current artifact
# definition"), and it doesn't need a default lakehouse anyway because it writes
# via the explicit OneLake path above.
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

# Notebooks in this demo chain that should point at the new lakehouse.
# (Adjust this list if you add or rename notebooks.)
chain_notebooks = [
    "RTI_002_Setup_Eventhouse_Only",
    "RTI_003_ingest_transform_medallion_SelfContained",
    "RTI_004_build_ontology_mapping_rti_structured",
    "RTI_005_entity_DataBinding_rti_structured",
    "RTI_006_TimeSeriesBinding_RTI_signal",
    "RTI_007_generate_and_ingest_OPCUA_Stream",
    "RTI_008_build_realtime_dashboard",
    "RTI_009_build_data_agent",
    "RTI_010_build_operations_agent",
    "RTI_011_seed_sql_wire_graphql_agent",
    "Weather_001_create_lakehouse",
    "Weather_002_fetch_area_weather",
    "Weather_003_fetch_ukmet",
    "Weather_020_area_calculations",
]
weather_environment_name = "Weather"


def _workspace_item_id(display_name: str, item_type: str) -> str:
    """Resolve one workspace item by exact display name and type."""
    matches = []
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    while url:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to list workspace items (HTTP {response.status_code}): {response.text}"
            )
        body = response.json()
        matches.extend(
            item
            for item in body.get("value", [])
            if item.get("displayName") == display_name and item.get("type") == item_type
        )
        url = body.get("continuationUri")
        if not url and body.get("continuationToken"):
            token = body["continuationToken"]
            url = (
                f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
                f"?continuationToken={quote(token, safe='')}"
            )
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one {item_type} named '{display_name}', found {len(matches)}."
        )
    return matches[0]["id"]


weather_environment_id = _workspace_item_id(weather_environment_name, "Environment")


def _rebind_lakehouse(nb_name: str) -> tuple:
    """Point one notebook at ONLY the current lakehouse, dropping any stale
    (orphaned) known_lakehouses. Returns (name, ok, detail)."""
    try:
        nb_json = json.loads(notebookutils.notebook.getDefinition(nb_name))
        deps = nb_json.setdefault("metadata", {}).setdefault("dependencies", {})
        deps["lakehouse"] = {
            "default_lakehouse": lakehouse_id,
            "default_lakehouse_name": lakehouse_name,
            "default_lakehouse_workspace_id": workspace_id,
            "known_lakehouses": [{"id": lakehouse_id}],
        }
        if nb_name in {"Weather_002_fetch_area_weather", "Weather_003_fetch_ukmet", "Weather_020_area_calculations"}:
            deps["environment"] = {
                "environmentId": weather_environment_id,
                "workspaceId": workspace_id,
            }
        ok = notebookutils.notebook.updateDefinition(
            name=nb_name,
            content=json.dumps(nb_json),
        )
        detail = "bound to current lakehouse"
        if nb_name in {"Weather_002_fetch_area_weather", "Weather_003_fetch_ukmet", "Weather_020_area_calculations"}:
            detail += f" and Environment '{weather_environment_name}'"
        return (nb_name, bool(ok), detail + " successfully!")
    except Exception as exc:
        return (nb_name, False, exc)


# Run concurrently — each notebook's get/update pair is independent I/O, so a
# small thread pool cuts total wall time to roughly one notebook's round-trip
# instead of the sum of all of them.
binding_failures = []
with ThreadPoolExecutor(max_workers=len(chain_notebooks)) as pool:
    futures = [pool.submit(_rebind_lakehouse, nb) for nb in chain_notebooks]
    for future in as_completed(futures):
        nb_name, ok, detail = future.result()
        if ok:
            print(f"✅ '{nb_name}': {detail}")
        else:
            print(f"⚠️  Could not rebind '{nb_name}': {detail}")
            binding_failures.append((nb_name, detail))
if binding_failures:
    raise RuntimeError(
        "Required downstream notebook binding failed: "
        + "; ".join(f"{name}: {detail}" for name, detail in binding_failures)
    )

print(
    f"\nℹ️  All downstream notebooks now reference the lakehouse "
    f"'{lakehouse_name}' ({lakehouse_id}). They pick this up on their next "
    "session start."
)

# Publish the created lakehouse name as this notebook's exit value so the Stage 2 orchestrator
# attaches EXACTLY this lakehouse (via %%configure) instead of recomputing the name separately.
notebookutils.notebook.exit(lakehouse_name)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## ✅ Next Steps
# 
# 1. In the notebook **Explorer** pane, expand **Lakehouses → your Lakehouse → Files → bronze → stid** and verify the four STID CSVs are present.
# 2. Move to the next Notebook (`03_ingest_transform_medallion`).
# 
# 
