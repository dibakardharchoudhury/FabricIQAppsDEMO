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

# # 10 — Build & Deploy the Operations Agent (health monitoring → Teams alert)
# Creates a Fabric **Operations Agent** that watches turbine telemetry and posts an
# **alert to a Teams channel** when signal `quality` degrades.
# **Design — the agent consumes the Ontology, it does not embed a table mapping.**
# Per the Operations Agent schema, `dataSource.type` may be `KustoDatabase` **or
# `Ontology`**. We use the **`Ontology`** data source (`RTI_Demo_Ontology_V3`). Its
# `signal_master` entity already joins:
# - **Real-time (KQL)** `OPCUAEvents` → `event_time`, `value`, `quality`
# - **Static (Lakehouse)** `silver_signal_master` → `equipment_id`, `facility_id`,
#   `system_id`, `unit`, `tag`, keyed on `opcua_node_id`.
# So the agent gets `equipment_id` / `facility_id` / `unit` for every live event
# **from the ontology join** — no OPC UA schema change and no hard-coded mapping.
# Rules (business intent):
# - `quality = "BAD"` → severity HIGH, type SingleFailure, trend Failing → alert.
# - `quality = "UNCERTAIN"` → severity MEDIUM, type SignalDegradation, trend Degrading → alert.
# **Deployment model — user context, matching an agent created in the Fabric UI:**
# The Operations Agent REST APIs support **User context only** (not Service Principal).
# Running this notebook interactively authenticates with your **delegated** identity, so
# the agent's *Run as* binds to you and Re-authenticate works — exactly like an agent
# built in the UI. Under an SPN/app-only token the *Run as* stays an unprovisioned
# "User" that cannot be saved, re-authenticated, or used to Generate Playbook.
# The agent is created via `/workspaces/{ws}/OperationsAgents`, then the full definition
# is pushed via `updateDefinition` (format `OperationsAgentV1`, single `Configurations.json`
# part): instructions + the Ontology **data source** + a **Teams channel** message
# destination + a **PowerAutomate action** ("New WO to Investigate / Repair"), and
# `shouldRun` to START it. The Teams destination is the passive alert; the action drives
# the approval workflow — Fabric invokes the connected Power Automate flow, which posts an
# Approve/Reject card to Teams and, on **Approve**, sends an email (see
# `Raw/PowerAutomate/NewWOtoInvestigateRepair_*.zip`). Connecting the action to that flow
# is a one-time UI step (PowerAutomateAction has no `connection` field in REST). The Teams
# destination is copied from an existing working agent (`ops_agent_reference_name`) or set
# via `ops_agent_team_id` / `ops_agent_channel_id`. `playbook` is reserved (Generate
# Playbook is UI-only); the agent runs from `instructions`.
# This notebook: reads settings → resolves the live `ontology_id` → resolves the Teams
# destination → creates the **OperationsAgent** item → pushes the full definition via REST
# → optionally starts it (`shouldRun`) → persists identifiers to `rti_demo_settings`.


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

ops_agent_name = first_setting("ops_agent_name", default="RTI_Demo_OpsAgent_V3")

# Message delivery = a Teams channel (matches the working GUI agent). Either set the
# Team (group) id + channel id explicitly, or point ops_agent_reference_name at an
# existing, working Operations Agent whose Teams destination this notebook copies.
ops_agent_team_id = first_setting("ops_agent_team_id", default=None)
ops_agent_channel_id = first_setting("ops_agent_channel_id", default=None)
ops_agent_reference_name = first_setting("ops_agent_reference_name", default="New_RTI_Demo_OpsAgent_V3")
# Optional email fallback, only used if no Teams channel is available.
ops_agent_recipient = first_setting("ops_agent_recipient", default=None)
# Start the agent programmatically (definition `shouldRun`); 'false' deploys it stopped.
ops_agent_should_run = str(first_setting("ops_agent_should_run", default="true")).lower() in ("true", "1", "yes")


print("✅ Settings loaded")
print("   Workspace ID      :", workspace_id)
print("   Target folder ID  :", target_folder_id)
print("   Ontology name     :", ontology_name)
print("   Ops Agent name    :", ops_agent_name)
print("   Teams team id     :", ops_agent_team_id or "(copy from reference agent)")
print("   Teams channel id  :", ops_agent_channel_id or "(copy from reference agent)")
print("   Reference agent   :", ops_agent_reference_name)
print("   Start agent       :", ops_agent_should_run)

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
import uuid
from typing import Optional

import requests
import notebookutils  # Fabric notebook utility

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
# Operations Agent uses a type-specific route + a named definition format.
OPS_AGENT_DEFINITION_FORMAT = "OperationsAgentV1"

# User-chosen alias for the single Ontology data source in the definition.
OPS_AGENT_DATASOURCE_ALIAS = "signalOntology"
# Work-order action (approval -> email flow). Its parameters become the Power Automate
# trigger's inputFields; connect it to Raw/PowerAutomate/NewWOtoInvestigateRepair in the UI.
OPS_AGENT_ACTION_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ops_agent_name}:createWorkOrder"))
OPS_AGENT_ACTION_ALIAS = "createWorkOrder"
OPS_AGENT_ACTION_DISPLAY_NAME = "New WO to Investigate / Repair"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

OPS_AGENT_DESCRIPTION = (
    "AI Operations Agent monitoring RTI turbine OPC UA telemetry via the "
    f"'{ontology_name}' ontology. Posts a Teams alert and, on approval, raises a Work "
    "Order via email when signal quality degrades (UNCERTAIN) or fails (BAD)."
)[:256]  # Fabric item description max length is 256 chars


# -------------------------------------------------------------------------
# Delegated (user-context) auth + retry / LRO helpers
# -------------------------------------------------------------------------
def get_access_token_for_fabric() -> str:
    """Return the running user's delegated Fabric token.

    Operations Agent REST supports User context only. A delegated token binds the
    agent's *Run as* to the interactive user (Re-authenticate works) — the same
    result as creating the agent in the Fabric UI. An SPN/app-only token leaves the
    agent's identity unprovisioned ("User" with no principal, cannot re-authenticate).
    """
    return notebookutils.credentials.getToken("pbi")


def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_access_token_for_fabric()}",
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
    if target_folder_id:
        body["folderId"] = target_folder_id  # land in the V3 folder like the other items
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


def get_operations_agent_definition(agent_id: str) -> Optional[dict]:
    """Return the Configurations.json of an existing agent (to copy its Teams destination)."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/OperationsAgents/{agent_id}/getDefinition"
    response = api_request("POST", url, params={"format": OPS_AGENT_DEFINITION_FORMAT}, timeout=120)
    body = None
    if response.status_code == 200:
        body = response.json() if response.content else None
    elif response.status_code == 202:
        operation_url = response.headers.get("Location")
        if operation_url:
            wait_for_lro(operation_url)
            result = api_request("GET", operation_url + "/result", timeout=120)
            if result.status_code == 200 and result.content:
                body = result.json()
    if not body:
        return None
    for part in body.get("definition", {}).get("parts", []):
        if part.get("path") == "Configurations.json":
            raw = base64.b64decode(part["payload"]).decode("utf-8")
            return json.loads(raw)
    return None


def resolve_message_destination() -> dict:
    """Teams channel destination — matches the working GUI agent.

    Priority: explicit team/channel ids -> copy from an existing working agent
    (ops_agent_reference_name) -> email recipient fallback.
    """
    if ops_agent_team_id and ops_agent_channel_id:
        return {"kind": "TeamsChannel", "teamId": ops_agent_team_id, "channelId": ops_agent_channel_id}
    if ops_agent_reference_name and ops_agent_reference_name != ops_agent_name:
        reference = find_operations_agent(ops_agent_reference_name)
        if reference and reference.get("id"):
            ref_cfg = get_operations_agent_definition(reference["id"])
            destination = (ref_cfg or {}).get("configuration", {}).get("messageDestination")
            if destination:
                print(f"✅ Copied Teams destination from working agent '{ops_agent_reference_name}'.")
                return destination
    if ops_agent_recipient:
        print("ℹ️  No Teams channel found — falling back to email recipient.")
        return {"kind": "Recipient", "recipient": ops_agent_recipient}
    raise RuntimeError(
        "No message destination. Set ops_agent_team_id + ops_agent_channel_id, or "
        "ops_agent_reference_name (an existing working agent), or ops_agent_recipient."
    )


# -------------------------------------------------------------------------
# Operations Agent instructions (schema: operationsAgents/definition/1.0.0)
# -------------------------------------------------------------------------
INSTRUCTIONS = (
    "*** Goals ***\n"
    "- Monitor industrial turbine OPC UA telemetry in real time via the RTI ontology.\n"
    "- Detect equipment health issues from signal quality and post a Teams alert automatically.\n"
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
    "   quality, event_time. If any field is missing, pass an empty string. A supervisor then approves in\n"
    "   Teams to trigger the work-order email.\n\n"
    "*** Semantic Notes ***\n"
    "- quality: GOOD = normal, UNCERTAIN = degraded signal, BAD = failure condition.\n"
    "- unit tells whether the reading is pressure, temperature, flow, vibration or position.\n"
    "- Keep alerts short, clear and human-readable; use only ontology-provided fields."
)

def build_configurations(ontology_id: str, message_destination: dict, should_run: Optional[bool] = None) -> dict:
    """Configurations.json body — OperationsAgentV1 format.

    Root requires `configuration`, `playbook` and `shouldRun`; `configuration`
    requires `instructions`, `dataSources` and `actions`. The Ontology data source
    must be present (the API rejects empty `dataSources`). `messageDestination` posts
    the passive Teams alert; the PowerAutomateAction lets the agent invoke the
    approval->email work-order flow (its parameters become the flow trigger's
    inputFields). Connecting the action to the actual flow is a UI-only step
    (PowerAutomateAction has no `connection` field). `identity` is omitted: the running
    user's delegated token provisions the agent's Run-as identity automatically.
    `should_run` sets the run state (True = start the agent now).
    """
    run_state = ops_agent_should_run if should_run is None else should_run
    return {
        "configuration": {
            "instructions": INSTRUCTIONS,
            "dataSources": {
                OPS_AGENT_DATASOURCE_ALIAS: {
                    "id": ontology_id,
                    "type": "Ontology",
                    "workspaceId": workspace_id,
                }
            },
            "actions": {
                OPS_AGENT_ACTION_ALIAS: {
                    "id": OPS_AGENT_ACTION_ID,
                    "kind": "PowerAutomateAction",
                    "displayName": OPS_AGENT_ACTION_DISPLAY_NAME,
                    "description": "Raise a work-order approval: posts an Approve/Reject card to Teams; on approval sends an email.",
                    "parameters": [
                        {"name": "equipment_id", "description": "Equipment to investigate"},
                        {"name": "facility_id", "description": "Facility of the equipment"},
                        {"name": "value", "description": "Measured value"},
                        {"name": "unit", "description": "Unit of the measured value"},
                        {"name": "quality", "description": "Signal quality (GOOD/UNCERTAIN/BAD)"},
                        {"name": "event_time", "description": "Event timestamp"},
                    ],
                }
            },
            "messageDestination": message_destination,
        },
        "playbook": {},
        "shouldRun": run_state,
    }


# -------------------------------------------------------------------------
# Deploy: create (empty) -> push instructions. Best-effort + manual fallback.
# -------------------------------------------------------------------------
ops_agent_item_id = None
ontology_id = None
try:
    get_access_token_for_fabric()
    print("✅ Got Fabric access token (delegated user context).")

    ontology_id = resolve_ontology_id()
    print("✅ Resolved ontology ID:", ontology_id)

    message_destination = resolve_message_destination()
    print("✅ Message destination:", message_destination.get("kind"))

    # 1) Create (or reuse) the Operations Agent — empty, no definition.
    ops_agent = create_operations_agent(ops_agent_name, OPS_AGENT_DESCRIPTION)
    ops_agent_item_id = ops_agent.get("id")

    # 2) Push the full definition via updateDefinition (OperationsAgentV1 / Configurations.json).
    #    Runs in User context so the agent's Run-as provisions correctly (Re-authenticate works).
    started = ops_agent_should_run
    try:
        configurations = build_configurations(ontology_id, message_destination, should_run=started)
        json.dumps(configurations)  # validate serializable
        update_operations_agent_definition(ops_agent_item_id, configurations)
    except RuntimeError as update_exc:
        # If starting was refused, still deploy the definition stopped so nothing is half-done.
        if not started:
            raise
        print("ℹ️  Start (shouldRun=true) was refused — deploying stopped so the definition lands:")
        print("   ", update_exc)
        started = False
        configurations = build_configurations(ontology_id, message_destination, should_run=False)
        update_operations_agent_definition(ops_agent_item_id, configurations)
    _run_state = "started (shouldRun=true)" if started else "deployed, stopped (shouldRun=false)"
    print(f"✅ Operations Agent '{ops_agent_name}' {_run_state} — instructions, Ontology data source")
    print(f"   and Teams message destination all set via REST (id={ops_agent_item_id}).")
except Exception as exc:  # noqa: BLE001 - best-effort deploy with manual fallback
    print("⚠️ Automated Operations Agent deployment did not complete:")
    print("   ", exc)
    print()
    print("Manual fallback:")
    print("   1. In your Fabric workspace: New → Operations agent.")
    print(f"   2. Name it '{ops_agent_name}'.")
    print("   3. Paste the instructions from INSTRUCTIONS above and set the Teams channel.")


print()
print("✅ Set programmatically via REST (User context): instructions, Ontology data source,")
print("   Teams message destination, work-order action, and run state (shouldRun).")
print("ℹ️  One-time UI step: open the agent, Add action → Power Automate → connect")
print(f"   '{OPS_AGENT_ACTION_DISPLAY_NAME}' to the flow imported from")
print("   Raw/PowerAutomate/NewWOtoInvestigateRepair_*.zip (posts the Teams Approve/Reject")
print("   card and, on approval, sends the email). PowerAutomateAction has no REST connection field.")
print("ℹ️  Optionally select 'Generate Playbook' to pre-generate the plan (the `playbook`")
print("   object is reserved in the API; the agent still runs from its instructions).")


if ops_agent_item_id:
    from delta.tables import DeltaTable

    persist = {
        "ops_agent_name": ops_agent_name,
        "ops_agent_id": ops_agent_item_id,
        "ops_agent_should_run": str(ops_agent_should_run).lower(),
    }
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
