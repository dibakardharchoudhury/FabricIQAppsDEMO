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
# **Deployment model — exact replica of the working GUI agent, in user context:**
# The Operations Agent REST APIs support **User context only** (not Service Principal).
# Running this notebook interactively authenticates with your **delegated** identity, so
# the agent's *Run as* binds to you and Re-authenticate works — exactly like an agent
# built in the UI. Under an SPN/app-only token the *Run as* stays an unprovisioned
# "User" that cannot be saved, re-authenticated, or used to Generate Playbook.
# Rather than hand-build the definition, this notebook **copies the full configuration of
# a known-good agent** (`ops_agent_reference_name`, default `New_RTI_Demo_OpsAgent_V3`,
# created in the UI) via `getDefinition`, and re-deploys it to `ops_agent_name` via
# `updateDefinition` (format `OperationsAgentV1`, single `Configurations.json` part).
# The copied config keeps, exactly: `$schema`, the **Ontology** data source (encoded
# datasource id + zero workspaceId), a **Teams channel** message destination, and a single
# **FabricJobAction** ("Send Email Alert!") whose `connection` points at the
# `Pipe_SendEmailAlert` Data Pipeline. FabricJobAction carries its pipeline `connection`
# in the definition, so — unlike a PowerAutomateAction — the action is **fully wired via
# REST** with no UI step: the agent posts a Teams alert and invokes the pipeline to email
# operations. This notebook overrides **only** `instructions` (with a time-series-aware
# version that Generate Playbook can compile) and `shouldRun`; `playbook` and `identity`
# are intentionally omitted (matching the working agent).
# This notebook: reads settings → loads the reference agent's config → creates/reuses the
# target **OperationsAgent** item → pushes the copied definition (with fixed instructions)
# via REST → optionally starts it (`shouldRun`) → persists identifiers to `rti_demo_settings`.


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

# Target agent to (re)deploy, and the known-good agent whose full config is copied.
ops_agent_name = first_setting("ops_agent_name", default="RTI_Demo_OpsAgent_V3")
ops_agent_reference_name = first_setting("ops_agent_reference_name", default="New_RTI_Demo_OpsAgent_V3")
# Start the agent programmatically (definition `shouldRun`); 'false' deploys it stopped.
ops_agent_should_run = str(first_setting("ops_agent_should_run", default="true")).lower() in ("true", "1", "yes")


print("✅ Settings loaded")
print("   Workspace ID      :", workspace_id)
print("   Target folder ID  :", target_folder_id)
print("   Ops Agent name    :", ops_agent_name)
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
from copy import deepcopy
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

OPS_AGENT_DESCRIPTION = (
    "AI Operations Agent monitoring RTI turbine OPC UA telemetry via the "
    "RTI ontology. Posts a Teams alert and invokes the Send Email Alert pipeline "
    "when signal quality degrades (UNCERTAIN) or fails (BAD)."
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
    """Return the Configurations.json of an existing agent (to copy the full working config)."""
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


def load_reference_configuration() -> dict:
    """Return the full Configurations.json of the known-good reference agent.

    We replicate the working GUI agent exactly rather than rebuild the definition by
    hand: its Ontology data source (encoded datasource id + zero workspaceId), its Teams
    channel destination, and its single FabricJobAction ("Send Email Alert!") wired to the
    Pipe_SendEmailAlert Data Pipeline are all environment-correct and fully REST-settable.
    """
    reference = find_operations_agent(ops_agent_reference_name)
    if not reference or not reference.get("id"):
        raise RuntimeError(
            f"Reference agent '{ops_agent_reference_name}' not found in the workspace. "
            "Create it once in the Fabric UI (New → Operations agent), configure its Teams "
            "channel + Send Email Alert pipeline action, then re-run."
        )
    ref_cfg = get_operations_agent_definition(reference["id"])
    if not ref_cfg or "configuration" not in ref_cfg:
        raise RuntimeError(
            f"Could not read a usable definition from reference agent '{ops_agent_reference_name}'."
        )
    return ref_cfg


# -------------------------------------------------------------------------
# Operations Agent instructions (schema: operationsAgents/definition/1.0.0)
# -------------------------------------------------------------------------
INSTRUCTIONS = (
    "*** Role ***\n"
    "Monitor industrial turbine OPC UA telemetry in real time through the RTI ontology and\n"
    "raise an operational alert when a signal's quality degrades or fails.\n\n"
    "*** Data source ***\n"
    "Use the 'signal_master' entity of the ontology. Its 'quality', 'value' and 'event_time'\n"
    "are TIME-SERIES properties (bound to the real-time OPCUAEvents stream); 'equipment_id',\n"
    "'facility_id', 'system_id', 'unit' and 'tag' are static properties resolved by the\n"
    "ontology. For each 'equipment_id', evaluate its LATEST 'signal_master' reading by\n"
    "'event_time'.\n\n"
    "*** Trigger rules (one explicit condition each) ***\n"
    "Rule 1 - Failure:     trigger when the latest 'quality' reading equals \"BAD\".\n"
    "Rule 2 - Degradation: trigger when the latest 'quality' reading equals \"UNCERTAIN\".\n"
    "Do not alert when the latest 'quality' equals \"GOOD\".\n\n"
    "*** Enrichment (apply only after a rule triggers; these are derived, not queried) ***\n"
    "- severity:      BAD -> HIGH ;         UNCERTAIN -> MEDIUM.\n"
    "- alert_type:    BAD -> SingleFailure ; UNCERTAIN -> SignalDegradation.\n"
    "- derived_trend: BAD -> Failing ;       UNCERTAIN -> Degrading.\n\n"
    "*** Alert message (format exactly) ***\n"
    "   Equipment Alert: {equipment_id}\n"
    "   Facility: {facility_id}\n"
    "   Signal Quality: {quality}\n"
    "   Measured Value: {value} {unit}\n"
    "   Timestamp: {event_time}\n"
    "   Severity: {severity}\n"
    "   Type: {alert_type}\n"
    "   Trend: {derived_trend}\n"
    "   Insight: Explain the issue in simple business terms.\n"
    "   Recommended Action: Provide the next step.\n\n"
    "*** Action ***\n"
    "For each triggered alert, invoke the action \"Send Email Alert!\" so operations can act,\n"
    "passing equipment_id, facility_id, value, unit, quality and event_time (empty string if missing).\n\n"
    "*** Notes ***\n"
    "- quality: GOOD = normal, UNCERTAIN = degraded signal, BAD = failure condition.\n"
    "- unit indicates whether the reading is pressure, temperature, flow, vibration or position.\n"
    "- Suppress duplicate alerts for the same 'equipment_id' within 10 minutes.\n"
    "- Use only ontology-provided fields; keep messages short and human-readable."
)

def build_configurations(reference_configuration: dict, should_run: Optional[bool] = None) -> dict:
    """Configurations.json body — an exact copy of the reference agent's config.

    Deep-copy the working agent's full configuration (its `$schema`, Ontology
    `dataSources`, the FabricJobAction wired to Pipe_SendEmailAlert, and the Teams
    `messageDestination`) and override only two things: `instructions` (the
    time-series-aware text above, which Generate Playbook can compile) and `shouldRun`.
    `playbook` and `identity` are left absent, exactly like the working agent — the
    running user's delegated token provisions Run-as automatically.
    """
    run_state = ops_agent_should_run if should_run is None else should_run
    config = deepcopy(reference_configuration)
    config.pop("playbook", None)                       # match the working agent (no playbook key)
    config.get("configuration", {}).pop("identity", None)
    config["configuration"]["instructions"] = INSTRUCTIONS
    config["shouldRun"] = run_state
    return config


# -------------------------------------------------------------------------
# Deploy: create (empty) -> push instructions. Best-effort + manual fallback.
# -------------------------------------------------------------------------
ops_agent_item_id = None
reference_configuration = None
try:
    get_access_token_for_fabric()
    print("✅ Got Fabric access token (delegated user context).")

    reference_configuration = load_reference_configuration()
    _ref_cfg = reference_configuration.get("configuration", {})
    print(f"✅ Loaded reference config from '{ops_agent_reference_name}':")
    print("   data sources     :", list(_ref_cfg.get("dataSources", {}).keys()))
    print("   actions          :", [a.get("kind") for a in _ref_cfg.get("actions", {}).values()])
    print("   message dest.    :", _ref_cfg.get("messageDestination", {}).get("kind"))

    # 1) Create (or reuse) the target Operations Agent — empty, no definition.
    ops_agent = create_operations_agent(ops_agent_name, OPS_AGENT_DESCRIPTION)
    ops_agent_item_id = ops_agent.get("id")

    # 2) Push the copied definition (fixed instructions) via updateDefinition.
    #    Runs in User context so the agent's Run-as provisions correctly (Re-authenticate works).
    started = ops_agent_should_run
    try:
        configurations = build_configurations(reference_configuration, should_run=started)
        json.dumps(configurations)  # validate serializable
        update_operations_agent_definition(ops_agent_item_id, configurations)
    except RuntimeError as update_exc:
        # If starting was refused, still deploy the definition stopped so nothing is half-done.
        if not started:
            raise
        print("ℹ️  Start (shouldRun=true) was refused — deploying stopped so the definition lands:")
        print("   ", update_exc)
        started = False
        configurations = build_configurations(reference_configuration, should_run=False)
        update_operations_agent_definition(ops_agent_item_id, configurations)
    _run_state = "started (shouldRun=true)" if started else "deployed, stopped (shouldRun=false)"
    print(f"✅ Operations Agent '{ops_agent_name}' {_run_state} — copied Ontology data source,")
    print(f"   Teams destination and Send Email Alert pipeline action from the reference (id={ops_agent_item_id}).")
except Exception as exc:  # noqa: BLE001 - best-effort deploy with manual fallback
    print("⚠️ Automated Operations Agent deployment did not complete:")
    print("   ", exc)
    print()
    print("Manual fallback:")
    print("   1. In your Fabric workspace: New → Operations agent.")
    print(f"   2. Name it '{ops_agent_name}'.")
    print("   3. Paste the instructions from INSTRUCTIONS above, set the Teams channel and")
    print("      add a Fabric job action pointing at the Pipe_SendEmailAlert pipeline.")


print()
print("✅ Set programmatically via REST (User context) — an exact copy of the reference agent:")
print("   instructions (time-series-aware), Ontology data source, Teams message destination,")
print("   the 'Send Email Alert!' Fabric job action (wired to Pipe_SendEmailAlert), and run state.")
print("ℹ️  FabricJobAction carries its pipeline connection in the definition, so no UI wiring is")
print("   needed — the agent posts the Teams alert and runs the pipeline to email operations.")
print("ℹ️  To Generate Playbook in the UI: make sure the OPC UA stream (RTI_007) is running so")
print("   recent BAD/UNCERTAIN readings exist, then open the agent and select 'Generate Playbook'.")


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
