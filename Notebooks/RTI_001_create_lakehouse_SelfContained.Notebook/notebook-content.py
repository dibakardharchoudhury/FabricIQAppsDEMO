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
# 
# After this notebook, `03_ingest_transform_medallion` reads the STID source from:
# 
# ```text
# Files/bronze/stid/*.csv
# ```
# 
# inside the newly created Lakehouse.
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

# Operations Agent Teams targets. (INJECTED by Pipe_Setup.)
ops_agent_run_as_user = ""      # UPN the agent runs as; blank => the deploying user (optional)
ops_agent_teams_team_id = ""
ops_agent_teams_channel_id = ""
# Whether to copy the playbook (STATIC, NOT injected).
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
# ops_agent_run_as_user is optional (blank => the deploying user), so it is not required here.
_required_injected = {
    "env_suffix": env_suffix,
    "workspace_id": workspace_id,
    "key_vault_uri": key_vault_uri,
    "key_vault_tenant_id_secret_name": key_vault_tenant_id_secret_name,
    "key_vault_client_id_secret_name": key_vault_client_id_secret_name,
    "key_vault_client_secret_name": key_vault_client_secret_name,
    "ops_agent_teams_team_id": ops_agent_teams_team_id,
    "ops_agent_teams_channel_id": ops_agent_teams_channel_id,
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

        # Operations Agent deployment inputs.
        "ops_agent_run_as_user": ops_agent_run_as_user,
        "ops_agent_teams_team_id": ops_agent_teams_team_id,
        "ops_agent_teams_channel_id": ops_agent_teams_channel_id,
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

def get_spn_access_token() -> str:
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
        "scope": "https://api.fabric.microsoft.com/.default",
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
            url = f"{url}&continuationToken={continuation_token}"

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

# --------------------------------------------
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
]


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
        ok = notebookutils.notebook.updateDefinition(
            name=nb_name,
            content=json.dumps(nb_json),
        )
        return (nb_name, bool(ok), "bound to current lakehouse successfully!")
    except Exception as exc:
        return (nb_name, False, exc)


# Run concurrently — each notebook's get/update pair is independent I/O, so a
# small thread pool cuts total wall time to roughly one notebook's round-trip
# instead of the sum of all of them.
with ThreadPoolExecutor(max_workers=len(chain_notebooks)) as pool:
    futures = [pool.submit(_rebind_lakehouse, nb) for nb in chain_notebooks]
    for future in as_completed(futures):
        nb_name, ok, detail = future.result()
        if ok:
            print(f"✅ '{nb_name}': {detail}")
        else:
            print(f"⚠️  Could not rebind '{nb_name}': {detail}")

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
