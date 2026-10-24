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

# # 01 – Create Lakehouse and ADLS G2/ADLS G2 Shortcut (SPN + Key Vault + REST)
# 
# This notebook automates the **Fabric configuration** step for the  Fabric IQ scenario.
# 
# It will:
# 
# 1. Use a **Service Principal (SPN)** with secrets stored in **Azure Key Vault**.\n2. Use **Fabric REST APIs** to:
#    - Create (or reuse) a **Lakehouse** in the current Fabric workspace.
#    - Create a **ADLS G2 shortcut** under `Files/bronze` pointing to your  mock dataset.
# 3. Use the new **NotebookUtils** package (former MSSparkUtils) for environment variables and Key Vault access.
# 4. Avoid any use of `semPy` or user-context tokens.
# 5. Create/reuse the independent **HydroOperationalStore** SQL Database and initialize
#    its five operational tables without demo data; persist its verified item ID.
# 
# After this notebook, `RTI_003_ingest_transform_medallion` can read from:
# 
# ```text
# Files/bronze/...
# ```
# 
# inside the newly created Lakehouse.
#
# In `Pipe_Setup`, this is Stage 1: persist shared settings and rebind child
# notebooks before Stage 2 (`RTI_Orchestrator_Setup`) attaches the root default
# Lakehouse. Its children use `useRootDefaultLakehouse`; do not rename the notebooks.
# Ontology creation/binding is v2-only (live integer `properties.generation == 2`).
# Existing v1 is rejected, not migrated/deleted. `_V9`, REST `/v1`, and ADLS Gen2
# are not ontology generation indicators. Agent provisioning is always attempted;
# there are no agent mode or skip flags. Data Agent publication verifies source identity.
# Operations Agent provisions the playbook, Teams destination and email pipeline.
# Real failures abort setup; NB09 alone reports the exact documented temporary
# unsupported-Ontology-v2 product response without claiming runtime readiness.


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

# Seed dataset location + the connection feeding the shortcut. (INJECTED by Pipe_Setup.)
adls_account_url = ""
adls_subpath = ""
connection_name = ""
# Static shortcut placement (NOT injected).
shortcut_name = "bronze"
shortcut_parent_path = "Files"

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
    "adls_account_url": adls_account_url,
    "adls_subpath": adls_subpath,
    "connection_name": connection_name,
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

        "adls_account_url": adls_account_url,
        "adls_subpath": adls_subpath,

        "shortcut_name": shortcut_name,
        "shortcut_parent_path": shortcut_parent_path,
        "connection_name": connection_name,

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
# To allow the SPN to call the Fabric REST APIs (for example, to list items, create a Lakehouse, a connection, and a shortcut), the SPN must be granted access to the workspace.
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
# ✔️ Step 2 — Ensure the workspace has a Workspace Identity (one-time, admin task)
# 
# This notebook uses the Workspace Identity as the credential for the ADLS Gen2
# connection, so it can reach the firewall-protected storage via trusted access.
# A Workspace Identity CANNOT be created by a Service Principal — the
# provisionIdentity API returns 403 InsufficientPrivileges for app-only tokens.
# A workspace Admin (a user) must create it once:
#   Workspace settings (gear) → Workspace identity → + Workspace identity.
# Then grant that identity 'Storage Blob Data Contributor' on the ADLS account.
# The notebook detects the identity automatically once it exists.


# CELL ********************

# --------------------------------------------
# Fabric REST helpers
# --------------------------------------------

FABRIC_BASE_URL = "https://api.fabric.microsoft.com/v1"


def ensure_workspace_identity(workspace_id: str, access_token: str) -> dict:
    """
    Ensure the Fabric workspace has a Workspace Identity, and return it.

    The Workspace Identity is the trusted identity that firewall-protected
    storage accounts recognize (via resource instance rules for
    Microsoft.Fabric/workspaces). The ADLS connection below uses
    credentialType "WorkspaceIdentity", so this identity must exist.

    NOTE ON CREATION:
    A Workspace Identity can only be *created* by a user/admin (creating it mints
    an Entra app registration + service principal). Service principal app-only
    tokens are rejected by the provisionIdentity API with 403
    InsufficientPrivileges, even when the SPN is a workspace Admin. Therefore
    this function DETECTS the identity via GET /workspaces/{id} (which an SPN
    workspace member can read) and, if it is missing, fails with clear
    instructions to create it once in the Fabric UI.
    """
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    print(f"🔎 GET {url} (check for existing workspace identity)")
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to read workspace {workspace_id} "
            f"(HTTP {resp.status_code}): {resp.text}"
        )

    identity = resp.json().get("workspaceIdentity")
    if identity:
        print(
            f"✅ Workspace identity present: "
            f"appId={identity.get('applicationId')}, "
            f"servicePrincipalId={identity.get('servicePrincipalId')}"
        )
        return identity

    raise RuntimeError(
        "This workspace does NOT have a Workspace Identity, and it cannot be "
        "created with a Service Principal token (the provisionIdentity API "
        "returns 403 InsufficientPrivileges for app-only tokens).\n\n"
        "One-time fix (requires a workspace Admin who is a user):\n"
        "  1. Open the workspace in the Fabric portal.\n"
        "  2. Workspace settings (gear) -> Workspace identity tab.\n"
        "  3. Click '+ Workspace identity' to create it.\n"
        "  4. Grant the new workspace identity 'Storage Blob Data Contributor' "
        "on the target ADLS Gen2 account.\n"
        "  5. Re-run this notebook.\n\n"
        "Once the identity exists, this notebook detects it automatically and "
        "uses it as the ADLS connection credential."
    )


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

def get_existing_connection_id(connection_name: str, access_token: str) -> str:
    """Look up an existing connection by display name and return its ID."""
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(f"{FABRIC_BASE_URL}/connections", headers=headers)
    resp.raise_for_status()

    connections = resp.json().get("value", [])
    for conn in connections:
        if conn.get("displayName") == connection_name:
            connection_id = conn["id"]
            print(f"♻️  Reusing existing connection '{connection_name}': {connection_id}")
            return connection_id

    raise RuntimeError(
        f"Connection '{connection_name}' reported as duplicate but not found in list."
    )


def create_adls_gen2_cloud_connection(
    connection_name: str,
    adls_account_url: str,  # "https://adlsgen22didharch.dfs.core.windows.net"
    tenant_id: str,
    client_id: str,
    client_secret: str,
    access_token: str,
) -> str:
    api_url = f"{FABRIC_BASE_URL}/connections"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "connectivityType": "ShareableCloud",
        "displayName": connection_name,
        "connectionDetails": {
            "type": "AzureDataLakeStorage",
            "creationMethod": "AzureDataLakeStorage",
            "parameters": [
                {
                    "dataType": "Text",
                    "name": "server",
                    "value": adls_account_url,
                }
            ],
        },
        "privacyLevel": "Organizational",
        "credentialDetails": {
            "singleSignOnType": "None",
            "connectionEncryption": "NotEncrypted",
            "skipTestConnection": False,
            "credentials": {
                "credentialType": "WorkspaceIdentity",
            },
        },
    }

    print(f"🚀 POST {api_url} (create ADLS Gen2 cloud connection)")

    resp = requests.post(api_url, headers=headers, json=payload)
    print(f"   Status: {resp.status_code}")

    # ✅ Already exists — look it up and reuse
    if resp.status_code == 409:
        print(f"ℹ️  Connection '{connection_name}' already exists, fetching existing ID...")
        return get_existing_connection_id(connection_name, access_token)

    if resp.status_code not in (200, 201):
        # Only print full response on error
        print("   Error response:", resp.text)
        raise RuntimeError(
            f"Connection creation failed (HTTP {resp.status_code}). Body: {resp.text}"
        )

    resp_json = resp.json()
    connection_id = resp_json.get("id")
    if not connection_id:
        raise RuntimeError("Connection created but no 'id' returned in response.")

    print(f"✅ Connection created with id: {connection_id}")
    return connection_id


def get_existing_shortcut(
    workspace_id: str,
    lakehouse_id: str,
    parent_path: str,
    shortcut_name: str,
    access_token: str,
) -> dict | None:
    """Return the shortcut dict if it already exists, else None."""
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(
        f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items/{lakehouse_id}/shortcuts",
        headers=headers,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    for sc in resp.json().get("value", []):
        if sc.get("name") == shortcut_name and sc.get("path") == parent_path:
            return sc
    return None


def create_adls_gen2_shortcut_with_connection(
    workspace_id: str,
    lakehouse_id: str,
    shortcut_name: str,    # e.g. "bronze"
    parent_path: str,      # e.g. "Files"
    adls_account_url: str, # "https://adlsgen22didharch.dfs.core.windows.net"
    adls_subpath: str,     # "/dataiq/bronze"
    connection_name: str,  # e.g. "adlsgen22didharch-connection"
    access_token: str,
):
    """
    Idempotent function:
    1) Gets or creates the ADLS Gen2 SPN connection
    2) Gets or creates the shortcut under Lakehouse/Files
    Safe to rerun multiple times.
    """

    # Resolve SPN secrets from Key Vault
    tenant_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_tenant_id_secret_name)
    client_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_id_secret_name)
    client_secret = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_secret_name)

    # 1) Get or create the connection
    connection_id = create_adls_gen2_cloud_connection(
        connection_name=connection_name,
        adls_account_url=adls_account_url,
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
    )

    # 2) Check if shortcut already exists (best-effort — 409 handler below is the safety net)
    existing = get_existing_shortcut(
        workspace_id, lakehouse_id, parent_path, shortcut_name, access_token
    )
    if existing:
        print(f"♻️  Shortcut '{parent_path}/{shortcut_name}' already exists, skipping creation.")
        return existing

    # 3) Create the shortcut
    api_url = (
        f"{FABRIC_BASE_URL}/workspaces/{workspace_id}"
        f"/items/{lakehouse_id}/shortcuts"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "path": parent_path,
        "name": shortcut_name,
        "target": {
            "adlsGen2": {
                "location": adls_account_url,
                "subpath": adls_subpath,
                "connectionId": connection_id,
            }
        },
    }

    print(f"🚀 POST {api_url} (create shortcut)")

    resp = requests.post(api_url, headers=headers, json=payload)
    print(f"   Status: {resp.status_code}")

    # ✅ Already exists — treat as success
    if resp.status_code == 409:
        print(f"♻️  Shortcut '{parent_path}/{shortcut_name}' already exists, skipping.")
        return {}

    if resp.status_code not in (200, 201):
        # Only print full response on error
        print("   Error response:", resp.text)
        raise RuntimeError(
            f"Shortcut creation failed (HTTP {resp.status_code}). Body: {resp.text}"
        )

    print(f"✅ Shortcut created at '{parent_path}/{shortcut_name}'")
    return resp.json()

    

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


def ensure_operational_item(request, workspace):
    url = f"workspaces/{workspace}/items"
    candidates = []
    visited = set()
    while url:
        if url in visited or len(visited) >= 20:
            raise RuntimeError("Operational SQL inventory pagination did not terminate.")
        visited.add(url)
        response = request("GET", url)
        response.raise_for_status()
        page = response.json()
        candidates.extend(item for item in page["value"]
                          if item["type"] == "SQLDatabase" and item["displayName"] == OPERATIONAL_STORE_NAME)
        url = page.get("continuationUri")
        if url:
            prefix = "https://api.fabric.microsoft.com/v1/"
            if not url.startswith(prefix):
                raise RuntimeError("Unexpected SQL inventory continuation host.")
            url = url[len(prefix):]
        elif page.get("continuationToken"):
            raise RuntimeError("SQL inventory returned an unsupported continuation without its URI.")
    if len(candidates) > 1:
        raise RuntimeError("Multiple shared operational stores match the explicit setup name.")
    if candidates:
        item = operational_item(request, workspace, candidates[0]["id"])
        if item.get("description") != OPERATIONAL_STORE_DESCRIPTION:
            raise RuntimeError("The shared SQL name is already used by an unrelated item; refusing to adopt it.")
        return item
    response = request("POST", f"workspaces/{workspace}/sqlDatabases", {
        "displayName": OPERATIONAL_STORE_NAME, "description": OPERATIONAL_STORE_DESCRIPTION,
    })
    response.raise_for_status()
    if response.status_code == 202:
        location = response.headers.get("Location", "")
        match = re.fullmatch(r"https://api\.fabric\.microsoft\.com/v1/(operations/[0-9a-fA-F-]{36})", location)
        if not match:
            raise RuntimeError("SQL creation returned an unexpected operation location.")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            operation = request("GET", match[1])
            operation.raise_for_status()
            status = operation.json()["status"]
            if status == "Succeeded":
                response = request("GET", match[1] + "/result")
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
    return operational_item(request, workspace, item_id)


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
            projection = ",".join(
                f"CONVERT(nvarchar(33),[{column}],126) AS [{column}]" if kind.lower().startswith("datetime2")
                else f"[{column}]" for column, kind in columns.items())
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

def _normal_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")

def _decode_part(part):
    return _normal_text(base64.b64decode(part["payload"]).decode("utf-8"))

def _parts_by_path(parts):
    result = {part["path"]: part for part in parts}
    if len(result) != len(parts):
        raise RuntimeError("Duplicate ontology definition part paths")
    return result

def _identifier_parts(value):
    tokens = re.findall(r"'(?:[^']|'')*'|[^.\s]+", value.strip())
    if ".".join(tokens) != value.strip():
        raise RuntimeError(f"Unsupported or ambiguous TMDL reference: {value!r}")
    return [token[1:-1].replace("''", "'") if token.startswith("'") else token
            for token in tokens]

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
        match = re.match(r"(ref (?:entity|table|namespace)|entity|table|column|property|entityRelationship|relationship) (.+)$", statement)
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


print("\n=== STEP 1.5: Verify Workspace Identity exists ===")
ensure_workspace_identity(
    workspace_id=workspace_id,
    access_token=access_token,
)


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
        method, f"https://api.fabric.microsoft.com/v1/{path}",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=payload, timeout=60,
    )


operational_store = ensure_operational_item(shared_sql_request, workspace_id)
operational_sql_connection = operational_connection(
    operational_store, lambda: get_spn_access_token("https://database.windows.net/.default"))
try:
    initialize_operational_schema(operational_sql_connection)
finally:
    operational_sql_connection.close()
print("Shared operational SQL Database:", operational_store["id"])


print("\n=== STEP 4: Create ADLS Gen2 shortcut under Lakehouse/Files ===")
response = create_adls_gen2_shortcut_with_connection(
    workspace_id=workspace_id,
    lakehouse_id=lakehouse_id,
    shortcut_name=shortcut_name,
    parent_path=shortcut_parent_path,
    adls_account_url=adls_account_url,
    adls_subpath=adls_subpath,
    connection_name=connection_name,
    access_token=access_token,
)

print(
    "\n🎉 Done! If the status is 200/201 and no error was raised,\n"
    f"you should now see a shortcut at '/Files/{shortcut_name}' in your Lakehouse.\n"
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
        "operational_sql_db_id": operational_store["id"],
        "operational_sql_db_name": OPERATIONAL_STORE_NAME,
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
    "RTI_003_ingest_transform_medallion",
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
# 1. In the notebook **Explorer** pane, expand **Lakehouses → your Lakehouse → Files** and verify you see `bronze`.
# 2. Open `bronze` and confirm you can browse the subfolders for the  mock dataset (STID, SAP, OPC UA, SOLV, P&ID, documents, etc.).
# 3. Move to the next Notebook.
# 
# 
