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
# 
# After this notebook, the PySpark notebook `03_ingest_transform_medallion` can read from:
# 
# ```text
# Files/bronze/...
# ```
# 
# inside the newly created Lakehouse.


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

# CELL ********************

# --------------------------------------------
# USER PARAMETERS / BOOTSTRAP SETTINGS
# --------------------------------------------

from datetime import datetime, timezone

# Shared settings table name.
# Later notebooks should read this table instead of redefining common settings.
settings_table_name = "rti_demo_settings"

# Lakehouse name & description
lakehouse_name = "Energy_IQ_LakehouseRTI_V3"

lakehouse_description = "Lakehouse for Fabric IQ mock dataset (STID, SAP, OPC UA, SOLV, P&ID, Documents)."

# Workspace ID that will host the Lakehouse
workspace_id = "19f3d588-1585-4f3b-bb59-5abaf90c193a"  # from behind /groups/ in Fabric URL

# Workspace folder where the demo items should be created.
# This is a Fabric workspace folder path, not a Lakehouse path.
workspace_folder_path = "RTI_DEMO_V3"

# ADLS G2 URL containing the mock dataset.
# This should be the root folder containing `bronze/stid`, `bronze/sap`, etc.
adls_account_url = "https://didharchadlsg2.dfs.core.windows.net"
adls_subpath = "/dataiq/bronze"

# Shortcut configuration (we want Lakehouse/Files/bronze -> ADLS G2 dataset)
shortcut_name = "bronze"
shortcut_parent_path = "Files"
connection_name = "ontologydidharch-connection"

# Standard item names used by later notebooks
ontology_name = "RTI_Demo_Ontology_V3"
eventhouse_name = "RTI_Demo_Eventhouse_V3"
kql_database_name = "RTI_Demo_Eventhouse_V3"
eventstream_name = "RTI_Demo_Eventstream_V3"
eventhouse_table_name = "OPCUAEvents"
data_agent_name = "RTI_Demo_Agent_V3"

# Structured table names used by the ontology
silver_facilities_table = "silver_facilities"
silver_systems_table = "silver_systems"
silver_equipment_table = "silver_equipment"
silver_instruments_table = "silver_instruments"
silver_signal_master_table = "silver_signal_master"


def build_rti_demo_settings_rows(extra_settings: dict | None = None) -> list:
    """
    Build settings rows for the shared rti_demo_settings table.

    Do not store secrets here.
    IDs created later can be added through extra_settings.
    """

    updated_utc = datetime.now(timezone.utc).isoformat()

    settings = {
        "settings_table_name": settings_table_name,

        "workspace_id": workspace_id,
        "workspace_folder_path": workspace_folder_path,

        "lakehouse_name": lakehouse_name,
        "lakehouse_description": lakehouse_description,

        "adls_account_url": adls_account_url,
        "adls_subpath": adls_subpath,

        "shortcut_name": shortcut_name,
        "shortcut_parent_path": shortcut_parent_path,
        "connection_name": connection_name,

        "ontology_name": ontology_name,
        "eventhouse_name": eventhouse_name,
        "kql_database_name": kql_database_name,
        "eventstream_name": eventstream_name,
        "eventhouse_table_name": eventhouse_table_name,
        "data_agent_name": data_agent_name,

        "silver_facilities_table": silver_facilities_table,
        "silver_systems_table": silver_systems_table,
        "silver_equipment_table": silver_equipment_table,
        "silver_instruments_table": silver_instruments_table,
        "silver_signal_master_table": silver_signal_master_table,
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


print("✅ Settings table name:", settings_table_name)
print("✅ Lakehouse name:", lakehouse_name)
print("✅ Lakehouse description:", lakehouse_description)
print("✅ Workspace ID:", workspace_id)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ ADLS G2 dataset URL:", adls_account_url + adls_subpath)
print("✅ Shortcut parent/path:", f"{shortcut_parent_path}/{shortcut_name}")
print("✅ Ontology name:", ontology_name)
print("✅ Eventhouse name:", eventhouse_name)
print("✅ Eventstream name:", eventstream_name)
print("✅ Eventhouse table name:", eventhouse_table_name)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# --------------------------------------------
# Azure Key Vault configuration for SPN secrets (NotebookUtils)
# --------------------------------------------

# The URI of your Azure Key Vault (DNS name)
key_vault_uri = "https://akvfabcapnew.vault.azure.net/"

# Secret names inside Key Vault (not the values themselves).
# Each of these secrets should store one SPN value as plain text.
key_vault_tenant_id_secret_name  = "tenantid"
key_vault_client_id_secret_name  = "clientid"
key_vault_client_secret_name     = "clientsecret"

print("Key Vault URI:", key_vault_uri)
print("Tenant ID secret name:", key_vault_tenant_id_secret_name)
print("Client ID secret name:", key_vault_client_id_secret_name)
print("Client Secret secret name:", key_vault_client_secret_name)

# --------------------------------------------
# SPN AUTH: Get Fabric API token via Client Credentials (from Key Vault)
# --------------------------------------------

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
        "lakehouse_id": lakehouse_id,
        "target_folder_id": target_folder_id,
        # Key Vault names/URIs (not secret values) so notebooks 002–007 read them from here.
        "key_vault_uri": key_vault_uri,
        "key_vault_tenant_id_secret": key_vault_tenant_id_secret_name,
        "key_vault_client_id_secret": key_vault_client_id_secret_name,
        "key_vault_client_secret_secret": key_vault_client_secret_name,
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
    "RTI_006_generate_and_ingest_OPCUA_Stream",
    "RTI_007_TimeSeriesBinding_RTI_signal",
    "RTI_008_build_realtime_dashboard",
    "RTI_009_build_data_agent",
    "RTI_010_build_operations_agent",
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

