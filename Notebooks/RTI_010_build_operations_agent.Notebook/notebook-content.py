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

# # 10 — Build & Deploy the Operations Agent (health monitoring → Work Order)
# Creates a Fabric **Operations Agent** that watches turbine telemetry and raises a
# work order (via a Power Automate action) when signal `quality` degrades.
# **Design — the agent consumes the Ontology, it does not embed a table mapping.**
# Per the Operations Agent schema, `dataSource.type` may be `KustoDatabase` **or
# `Ontology`**. We use the **`Ontology`** data source (`RTI_Demo_Ontology_V3`). Its
# `signal_master` entity already joins:
# - **Real-time (KQL)** `OPCUAEvents` → `event_time`, `value`, `quality`
# - **Static (Lakehouse)** `silver_signal_master` → `equipment_id`, `facility_id`,
#   `system_id`, `unit`, `tag`, keyed on `opcua_node_id`.
# So the agent gets `equipment_id` / `facility_id` / `unit` for every live event
# **from the ontology join** — no OPC UA schema change and no hard-coded
# `OntologyDefinitions` block in the agent definition. The rules simply reference the
# `signal_master` entity.
# Rules (business intent — configured in the agent UI):
# - `quality = "BAD"` → severity HIGH, type SingleFailure, trend Failing → raise WO.
# - `quality = "UNCERTAIN"` → severity MEDIUM, type SignalDegradation, trend Degrading → raise WO.
# **Deployment model (verified against the Fabric REST + the OilGas RTI reference):**
# The Operations Agent is created EMPTY via its type-specific endpoint
# (`/workspaces/{ws}/OperationsAgents`), then its instructions are pushed via
# `updateDefinition` (format `OperationsAgentV1`, single `Configurations.json` part).
# The **data source (Ontology), actions (Power Automate) and the generated playbook**
# are NOT publicly settable via REST yet — you bind the data source, add the Power
# Automate action (connect it to the flow in `Raw/PowerAutomate/`), select **Generate
# Playbook**, then **Start** the agent in the Fabric UI.
# This notebook: reads settings → resolves the live `ontology_id` (for the UI step) →
# creates the **OperationsAgent** item → pushes instructions via REST (best-effort,
# manual fallback) → persists identifiers to `rti_demo_settings`.


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

# Key Vault names/URIs for SPN auth (written by RTI_001).
key_vault_uri = first_setting("key_vault_uri", required=True)
key_vault_tenant_id_secret = first_setting("key_vault_tenant_id_secret", required=True)
key_vault_client_id_secret = first_setting("key_vault_client_id_secret", required=True)
key_vault_client_secret_secret = first_setting("key_vault_client_secret_secret", required=True)

ops_agent_name = first_setting("ops_agent_name", default="RTI_Demo_OpsAgent_V3")
# UPN that receives the alert notification. Update to a user in your tenant.
ops_agent_recipient = first_setting("ops_agent_recipient", default="admin@MngEnvMCAP677316.onmicrosoft.com")

print("✅ Settings loaded")
print("   Workspace ID      :", workspace_id)
print("   Target folder ID  :", target_folder_id)
print("   Ontology name     :", ontology_name)
print("   Ops Agent name    :", ops_agent_name)
print("   Alert recipient   :", ops_agent_recipient)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 1
# Build the Operations Agent definition, deploy the item, persist settings
# =========================

import json
import time
import base64
from typing import Optional

import requests
import notebookutils  # Fabric notebook utility

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
# Operations Agent uses a type-specific route + a named definition format.
OPS_AGENT_DEFINITION_FORMAT = "OperationsAgentV1"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

OPS_AGENT_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/"
    "operationsAgents/definition/1.0.0/schema.json"
)
OPS_AGENT_DESCRIPTION = (
    "AI Operations Agent monitoring RTI turbine OPC UA telemetry via the "
    f"'{ontology_name}' ontology. Raises a work order when signal quality "
    "degrades (UNCERTAIN) or fails (BAD)."
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


# -------------------------------------------------------------------------
# Operations Agent REST operations (type-specific /OperationsAgents route)
# -------------------------------------------------------------------------
def find_operations_agent(display_name: str) -> Optional[dict]:
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/OperationsAgents"
    response = api_request("GET", url)
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
    """Return the id of the ontology named `ontology_name` (for the UI binding step)."""
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


def create_operations_agent(display_name: str, description: str = "") -> dict:
    """Create an EMPTY Operations Agent item (reuse if it already exists)."""
    existing = find_operations_agent(display_name)
    if existing:
        print(f"✅ Reusing existing Operations Agent: {display_name} (id={existing.get('id')})")
        return existing

    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/OperationsAgents"
    body = {"displayName": display_name, "description": description}
    response = api_request("POST", url, data=body, timeout=120)

    if response.status_code in (200, 201):
        created = response.json() if response.content else {}
        print(f"✅ Created Operations Agent: {display_name} (id={created.get('id')})")
        return created
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("Create Operations Agent returned 202 without Location header.")
        wait_for_lro(operation_url)
        created = find_operations_agent(display_name) or {}
        print(f"✅ Created Operations Agent (via LRO): {display_name} (id={created.get('id')})")
        return created
    raise RuntimeError(f"Failed to create Operations Agent: {response.status_code} {response.text}")


def update_operations_agent_definition(agent_id: str, configurations: dict) -> dict:
    """Push Configurations.json via updateDefinition (OperationsAgentV1 format)."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/OperationsAgents/{agent_id}/updateDefinition"
    definition = {
        "format": OPS_AGENT_DEFINITION_FORMAT,
        "parts": [
            {
                "path": "Configurations.json",
                "payload": encode_payload(configurations),
                "payloadType": "InlineBase64",
            }
        ],
    }
    response = api_request("POST", url, data={"definition": definition}, timeout=300)
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("updateDefinition returned 202 without Location header.")
        return wait_for_lro(operation_url)
    raise RuntimeError(f"Failed to update agent definition: {response.status_code} {response.text}")


# -------------------------------------------------------------------------
# Operations Agent instructions (schema: operationsAgents/definition/1.0.0)
# -------------------------------------------------------------------------
INSTRUCTIONS = (
    "*** Goals ***\n"
    "- Monitor industrial turbine OPC UA telemetry in real time via the RTI ontology.\n"
    "- Detect equipment health issues from signal quality and raise a work order automatically.\n"
    "- Escalate degraded (UNCERTAIN) or failed (BAD) signals to operations without manual triage.\n\n"
    "*** Operational Instructions ***\n"
    "1. Operate on the 'signal_master' entity of the ontology. Each event is complete and independent.\n"
    "   - Do NOT invent rowIndex, event_id or synthetic keys, and do NOT reconstruct rows with joins/arg_max.\n"
    "   - 'signal_master' already resolves equipment_id, facility_id, system_id and unit from the ontology\n"
    "     (real-time OPCUAEvents joined to the lakehouse signal registry on opcua_node_id).\n"
    "2. Use 'equipment_id' as the business entity to evaluate alerts; evaluate each incoming event independently.\n"
    "3. Trigger an alert when quality = \"BAD\" or quality = \"UNCERTAIN\".\n"
    "4. Suppress duplicate alerts for the same 'equipment_id' for 10 minutes.\n"
    "5. Assign severity:  BAD -> HIGH ;  UNCERTAIN -> MEDIUM.\n"
    "6. Classify type:    BAD -> SingleFailure ;  UNCERTAIN -> SignalDegradation.\n"
    "7. Derive trend from the current signal (no history):  BAD -> Failing ;  UNCERTAIN -> Degrading ;  GOOD -> Stable.\n"
    "8. Generate the alert message EXACTLY:\n\n"
    "   Equipment Alert: {equipment_id}\n"
    "   Facility: {facility_id}\n"
    "   Signal Quality: {quality}\n"
    "   Measured Value: {value} {unit}\n"
    "   Timestamp: {event_time}\n"
    "   Severity: {severity}\n"
    "   Type: {alert_type}\n"
    "   Trend: {derived_trend}\n"
    "   Insight: Explain the issue in simple business terms\n"
    "   Recommended Action: Provide the next step\n\n"
    "9. Invoke the action \"New WO to Investigate / Repair\" and pass: equipment_id, facility_id, value, unit,\n"
    "   quality, event_time. If any field is missing, pass an empty string.\n\n"
    "*** Semantic Notes ***\n"
    "- quality: GOOD = normal, UNCERTAIN = degraded signal, BAD = failure condition.\n"
    "- unit tells whether the reading is pressure, temperature, flow, vibration or position.\n"
    "- Keep alerts short, clear and human-readable; use only ontology-provided fields."
)

def build_configurations() -> dict:
    """Configurations.json body — instructions only (goals folded in).

    Per the Fabric REST surface, `dataSources`, `actions` and the generated
    `playbook` are not settable via updateDefinition yet, so they are left empty
    and bound in the agent UI. `shouldRun` stays false until you Start it there.
    """
    return {
        "$schema": OPS_AGENT_SCHEMA_URL,
        "configuration": {
            "instructions": INSTRUCTIONS,
            "dataSources": {},
            "actions": {},
        },
        "shouldRun": False,
    }


# -------------------------------------------------------------------------
# Deploy: create (empty) -> push instructions. Best-effort + manual fallback.
# -------------------------------------------------------------------------
ops_agent_item_id = None
ontology_id = None
try:
    get_spn_access_token_for_fabric()
    print("✅ Got Fabric access token (SPN).")

    ontology_id = resolve_ontology_id()
    print("✅ Resolved ontology ID (bind this in the UI):", ontology_id)

    # 1) Create (or reuse) the Operations Agent — empty, no definition.
    ops_agent = create_operations_agent(ops_agent_name, OPS_AGENT_DESCRIPTION)
    ops_agent_item_id = ops_agent.get("id")

    # 2) Push instructions via updateDefinition (OperationsAgentV1 / Configurations.json).
    configurations = build_configurations()
    json.dumps(configurations)  # validate serializable
    update_operations_agent_definition(ops_agent_item_id, configurations)
    print(f"✅ Operations Agent '{ops_agent_name}' created + instructions set (id={ops_agent_item_id}).")
except Exception as exc:  # noqa: BLE001 - best-effort deploy with manual fallback
    print("⚠️ Automated Operations Agent deployment did not complete:")
    print("   ", exc)
    print()
    print("Manual fallback:")
    print("   1. In your Fabric workspace: New → Operations agent.")
    print(f"   2. Name it '{ops_agent_name}'.")
    print("   3. Paste the instructions from INSTRUCTIONS above.")


print()
print("ℹ️ Finish in the Fabric UI (data source, actions, playbook and Start are UI steps):")
print(f"   1. Open the Operations Agent '{ops_agent_name}'.")
print(f"   2. Knowledge source → Ontology → '{ontology_name}'" + (f" (id {ontology_id})." if ontology_id else "."))
print("   3. Add a Power Automate action ('New WO to Investigate/Repair') and connect it")
print("      to the flow imported from Raw/PowerAutomate/NewWOtoInvestigateRepair_*.zip.")
print("      Pass: equipment_id, facility_id, value, unit, quality, event_time.")
print("   4. Add rules on signal_master.quality = 'BAD' (HIGH) and = 'UNCERTAIN' (MEDIUM).")
print("   5. Select 'Generate Playbook', review, then 'Start' the agent.")


if ops_agent_item_id:
    from delta.tables import DeltaTable

    persist = {"ops_agent_name": ops_agent_name, "ops_agent_id": ops_agent_item_id}
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
    print("✅ Persisted Operations Agent settings:", persist)
    display(spark.read.table(settings_table_name).orderBy("setting_name"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
