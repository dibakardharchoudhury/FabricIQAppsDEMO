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
# **Deployment model — self-contained known-good definition, in user context:**
# The Operations Agent REST APIs support **User context only** (not Service Principal).
# Running this notebook interactively authenticates with your **delegated** identity, so
# the agent's *Run as* binds to you and Re-authenticate works — exactly like an agent
# built in the UI. Under an SPN/app-only token the *Run as* stays an unprovisioned
# "User" that cannot be saved, re-authenticated, or used to Generate Playbook.
# The full agent definition is **embedded in this notebook** (CELL 1: `EMBEDDED_CONFIGURATION`
# + `EMBEDDED_PLAYBOOK`) — it does **not** depend on any pre-existing UI agent. The notebook
# creates/reuses `ops_agent_name` and deploys the embedded definition via `updateDefinition`
# (format `OperationsAgentV1`, single `Configurations.json` part).
# The embedded config contains: `$schema`, the **Ontology** data source (datasource-key byte
# order of the ontology item id + zero workspaceId), a **Teams channel** message destination,
# and a single **FabricJobAction** ("Send Email Alert!") whose `connection` points at the
# `Pipe_SendEmailAlert` Data Pipeline. Environment-specific ids (ontology, pipeline, Teams)
# are CELL-0 settings with RTI-demo defaults, so another environment overrides them without
# editing code. FabricJobAction carries its pipeline `connection` in the definition, so —
# unlike a PowerAutomateAction — the action is **fully wired via REST** with no UI step: the
# agent posts a Teams alert and invokes the pipeline to email operations. `instructions` come
# from the INSTRUCTIONS constant and `shouldRun` from settings; `identity` is intentionally
# omitted (delegated token provisions Run-as).
# **Run-as + Teams destination as inputs:** `ops_agent_run_as_user` (UPN) is a guardrail —
# the agent Run-as always binds to whoever runs this notebook, so the notebook prints the
# effective (signed-in) identity and warns if it differs from the input. `ops_agent_teams_team_id`
# and `ops_agent_teams_channel_id` are the destination the agent posts to. The definition stores
# *ids*, not the display names the portal shows: the Team id is a GUID and the Channel id looks
# like `19:...@thread.tacv2`. Defaults are the RTI-demo destination (Team
# "FacilitiesRealTimeMonitoring" / Channel "Alerts"); override the two ids for another channel.
# **Playbook:** the `playbook` key (OntologyDefinitions + RuleDefinitions) is a serialized
# part of the definition — `getDefinition` returns it and `updateDefinition` sends it back,
# so an *already-generated* playbook CAN be pushed. What the API canNOT do is *trigger*
# generation (the "Generate Playbook" computation is UI-only). By default
# (`ops_agent_copy_playbook=true`) the reference's generated playbook is copied verbatim so
# the target is playbook-ready; set it to `false` to deploy config-only and click Generate
# Playbook in the portal. If a pushed playbook doesn't show as live, opening the agent once
# and selecting Generate Playbook re-binds/refreshes it.
# This notebook: reads settings → creates/reuses the target **OperationsAgent** item →
# pushes the embedded definition (instructions + playbook) via REST → optionally starts it
# (`shouldRun`) → persists identifiers to `rti_demo_settings`.


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

# Target agent to (re)deploy. The full definition is embedded in CELL 1 (no external agent).
ops_agent_name = first_setting("ops_agent_name", default="RTI_Demo_OpsAgent_V3")
# Environment resources the embedded definition binds to (defaults = RTI demo; override per env).
# The Ontology data source key is Fabric's datasource-key byte order of the ontology item id.
ops_agent_ontology_datasource_id = first_setting(
    "ops_agent_ontology_datasource_id", default="4cdeb9b3-801f-a9db-46c6-d2db30f512c4")
ops_agent_email_pipeline_id = first_setting(
    "ops_agent_email_pipeline_id", "email_pipeline_id", default="ca6f0002-f791-4d1a-9c48-ff3c1d131150")
# --- User inputs: Run-as identity + Teams destination -------------------
# Run-as: the agent runs autonomously under the delegated identity that DEPLOYS it (whoever
# runs this notebook). Set this to that account's UPN; the notebook warns if the signed-in
# user differs. Run-as cannot be pointed at an arbitrary other user via REST.
ops_agent_run_as_user = first_setting("ops_agent_run_as_user", "run_as_user", default="")
# Teams Team + Channel the agent posts alerts to. The Operations Agent definition stores the
# *ids*, not the display names the portal shows: the Team id is a GUID and the Channel id looks
# like "19:...@thread.tacv2". No Microsoft Graph / SPN permission is used — paste the ids here.
# HOW TO GET THE IDs (from the Teams app, no admin needed): hover the channel -> "..." ->
# "Get link to channel". The URL is
#   .../channel/19%3A...%40thread.tacv2/Alerts?groupId=<TEAM-GUID>&tenantId=...
#   Team id    = the groupId=<GUID> value.
#   Channel id = the "19%3A...%40thread.tacv2" segment, URL-decoded (%3A->':', %40->'@').
# Defaults are the RTI demo destination:
#   Team "FacilitiesRealTimeMonitoring"  ->  c480320e-9204-474b-9b2c-54a53e94f220
#   Channel "Alerts"                     ->  19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2
ops_agent_teams_team_id = first_setting(
    "ops_agent_teams_team_id", "teams_team_id", default="c480320e-9204-474b-9b2c-54a53e94f220")
ops_agent_teams_channel_id = first_setting(
    "ops_agent_teams_channel_id", "teams_channel_id",
    default="19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2")
# Start the agent programmatically (definition `shouldRun`); 'false' deploys it stopped.
ops_agent_should_run = str(first_setting("ops_agent_should_run", default="true")).lower() in ("true", "1", "yes")
# Include the embedded known-good `playbook` (OntologyDefinitions + RuleDefinitions) so the
# target deploys playbook-ready. The playbook is part of the pushable definition — what the
# API CANNOT do is *trigger* generation. Set 'false' to deploy config-only and click Generate
# Playbook in the portal instead.
ops_agent_copy_playbook = str(first_setting("ops_agent_copy_playbook", default="true")).lower() in ("true", "1", "yes")


print("✅ Settings loaded")
print("   Workspace ID      :", workspace_id)
print("   Target folder ID  :", target_folder_id)
print("   Ops Agent name    :", ops_agent_name)
print("   Ontology dsrc     :", ops_agent_ontology_datasource_id)
print("   Email pipeline    :", ops_agent_email_pipeline_id)
print("   Run as (expected) :", ops_agent_run_as_user or "(the user running this notebook)")
print("   Teams team id     :", ops_agent_teams_team_id)
print("   Teams channel id  :", ops_agent_teams_channel_id)
print("   Start agent       :", ops_agent_should_run)
print("   Copy playbook     :", ops_agent_copy_playbook)

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
# User-input helper: Run-as guardrail (no Graph — decodes the pbi token only)
# -------------------------------------------------------------------------
def _decode_jwt_claims(token: str) -> dict:
    """Best-effort decode of a JWT payload (no signature check) to read the user claim."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


def check_run_as(expected_upn: str) -> None:
    """Print the effective Run-as (the deploying user) and warn if it differs from the input."""
    claims = _decode_jwt_claims(get_access_token_for_fabric())
    signed_in = (claims.get("upn") or claims.get("unique_name")
                 or claims.get("preferred_username") or claims.get("email") or "")
    print(f"ℹ️  Agent will Run as the deploying user: {signed_in or '(unknown)'}")
    if expected_upn and signed_in and expected_upn.strip().lower() != signed_in.strip().lower():
        print(f"⚠️  Run-as input '{expected_upn}' != signed-in '{signed_in}'. Run-as binds to the")
        print("    account running THIS notebook — re-run as that user to change the agent Run-as.")


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


# -------------------------------------------------------------------------
# Operations Agent instructions — verbatim from the working New_RTI_Demo_OpsAgent_V3
# agent that successfully generates a playbook (Goals / Operational / Semantic).
# -------------------------------------------------------------------------
INSTRUCTIONS = '''*** Goals ***
- Monitor OPC UA signal quality for industrial turbine equipment by using the "signal_master" ontology entity.
- Notify operations when an OPC UA signal has failed or degraded.
- Recommend an email alert containing the available equipment and signal context.

*** Operational Instructions ***
1. Monitor the "signal_master" entity, uniquely identified by the "opcua_node_id" property.

2. Create an alert when the current value of "quality" equals "BAD".

3. Create an alert when the current value of "quality" equals "UNCERTAIN".

4. For every alert, identify the affected equipment by using the "equipment_id" property.

5. Include the following available ontology properties in the alert context:
   - "equipment_id"
   - "facility_id"
   - "quality"
   - "value"
   - "unit"
   - "event_time"

6. For every generated alert, recommend the "Send Email Alert!" action.

*** Semantic Instructions ***
1. The "signal_master" entity represents an OPC UA signal.

2. The "opcua_node_id" property uniquely identifies each "signal_master" entity.

3. The "equipment_id" property identifies the equipment associated with the signal.

4. A "quality" value of "BAD" means that the signal has failed and requires immediate investigation.

5. A "quality" value of "UNCERTAIN" means that the signal is degraded and requires investigation.

6. The "value" property contains the current measured value.

7. The "unit" property describes the measurement unit.

8. The "event_time" property contains the timestamp of the signal event.

9. Use only properties available from the ontology when creating the alert context or recommending an action.'''

# -------------------------------------------------------------------------
# Embedded known-good agent definition — no dependency on any external agent.
# Environment-specific ids come from CELL-0 settings (RTI demo defaults); the
# generated playbook is a byte-exact base64 of the OntologyDefinitions + RuleDefinitions.
# -------------------------------------------------------------------------
EMBEDDED_CONFIGURATION = {
    "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/operationsAgents/definition/1.0.0/schema.json",
    "configuration": {
        "instructions": INSTRUCTIONS,
        "dataSources": {
            ops_agent_ontology_datasource_id: {
                "id": ops_agent_ontology_datasource_id,
                "type": "Ontology",
                "workspaceId": "00000000-0000-0000-0000-000000000000",
            }
        },
        "actions": {
            "94ef718d-6bdb-46f3-9a15-661af4fabb39": {
                "connection": {
                    "jobArtifactId": ops_agent_email_pipeline_id,
                    "jobWorkspaceId": workspace_id,
                    "itemType": "Pipeline",
                    "jobType": "Pipeline",
                    "subItemId": "",
                },
                "id": "94ef718d-6bdb-46f3-9a15-661af4fabb39",
                "displayName": "Send Email Alert!",
                "description": "Send Email Alert so that appropriate Action can be taken! Replace this with any Pipeline or Power Automate Flow based Action!",
                "kind": "FabricJobAction",
                "parameters": [],
            }
        },
        "messageDestination": {
            "kind": "TeamsChannel",
            "teamId": "c480320e-9204-474b-9b2c-54a53e94f220",
            "channelId": "19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2",
        },
    },
    "shouldRun": True,
}

# Base64 of the generated playbook (2 RuleDefinitions: BAD / UNCERTAIN signal quality →
# "Send Email Alert!"). Only used when ops_agent_copy_playbook is true. Regenerate from the
# exported Configurations.json if the rules change.
EMBEDDED_PLAYBOOK_B64 = "eyJPbnRvbG9neURlZmluaXRpb25zIjp7InNpZ25hbF9tYXN0ZXIiOnsiJHR5cGUiOiJjbGFzcyIsIklSSSI6InNpZ25hbF9tYXN0ZXIiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6InNpZ25hbF9tYXN0ZXIiLCJEZXNjcmlwdGlvbiI6IkFuIE9QQyBVQSBzaWduYWwgcmVjb3JkIHByb3ZpZGluZyBlcXVpcG1lbnQsIGZhY2lsaXR5LCBhbmQgbWVhc3VyZW1lbnQgY29udGV4dC4ifSwidW5pdCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJ1bml0IiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJ1bml0IiwiRGVzY3JpcHRpb24iOiJNZWFzdXJlbWVudCB1bml0IGZvciB2YWx1ZSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwiZmFjaWxpdHlfaWQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoiZmFjaWxpdHlfaWQiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6ImZhY2lsaXR5X2lkIiwiRGVzY3JpcHRpb24iOiJJZGVudGlmaWVyIG9mIGZhY2lsaXR5IHdoZXJlIGVxdWlwbWVudCByZXNpZGVzIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjF9LCJ2YWx1ZSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJ2YWx1ZSIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoidmFsdWUiLCJEZXNjcmlwdGlvbiI6IkN1cnJlbnQgbWVhc3VyZWQgc2lnbmFsIHZhbHVlIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6ImRlY2ltYWwiLCJLaW5kIjoxfSwiZXF1aXBtZW50X2lkIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6ImVxdWlwbWVudF9pZCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoiZXF1aXBtZW50X2lkIiwiRGVzY3JpcHRpb24iOiJJZGVudGlmaWVyIG9mIGFmZmVjdGVkIGVxdWlwbWVudCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwicXVhbGl0eSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJxdWFsaXR5IiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJxdWFsaXR5IiwiRGVzY3JpcHRpb24iOiJDdXJyZW50IE9QQyBVQSBzaWduYWwgcXVhbGl0eSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwiZXZlbnRfdGltZSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJldmVudF90aW1lIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJldmVudF90aW1lIiwiRGVzY3JpcHRpb24iOiJUaW1lc3RhbXAgb2YgdGhlIHNpZ25hbCBldmVudCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwib3BjdWFfbm9kZV9pZCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJvcGN1YV9ub2RlX2lkIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJvcGN1YV9ub2RlX2lkIiwiRGVzY3JpcHRpb24iOiJPUEMgVUEgbm9kZSBpZGVudGlmaWVyIGZvciB0aGUgc2lnbmFsIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjB9fSwiUnVsZURlZmluaXRpb25zIjp7IjMyNjBhZjA3LWEwNGQtNGVmNS05YTA5LTI5ODBmOWFmMzQ1OCI6eyJJZCI6IjMyNjBhZjA3LWEwNGQtNGVmNS05YTA5LTI5ODBmOWFmMzQ1OCIsIk5hbWUiOiJTZW5kIEVtYWlsIEFsZXJ0IGZvciBCQUQgT1BDIFVBIFNpZ25hbCBRdWFsaXR5IiwiRGVzY3JpcHRpb24iOiJTZW5kIGFuIGVtYWlsIGFsZXJ0IHdoZW5ldmVyIGFuIE9QQyBVQSBzaWduYWwgaW4gc2lnbmFsX21hc3RlciBoYXMgcXVhbGl0eSBlcXVhbCB0byBCQUQsIGluY2x1ZGluZyBlcXVpcG1lbnQgYW5kIHNpZ25hbCBjb250ZXh0LiIsIkNsYXNzRXhwcmVzc2lvbiI6eyIkdHlwZSI6Im9udG9sb2d5cXVlcnlleHByZXNzaW9uIiwiRXhwcmVzc2lvbiI6IntcIkVudGl0eVNlbGVjdG9yXCI6e1wicXVlcnlUeXBlXCI6XCJHUUxcIixcIlF1ZXJ5XCI6XCJNQVRDSCAobm9kZV9zaWduYWxfbWFzdGVyOlxcdTAwNjBzaWduYWxfbWFzdGVyXFx1MDA2MCkgUkVUVVJOIG5vZGVfc2lnbmFsX21hc3Rlci5cXHUwMDYwb3BjdWFfbm9kZV9pZFxcdTAwNjAgQVMgXFx1MDA2MG9wY3VhX25vZGVfaWRcXHUwMDYwLCBub2RlX3NpZ25hbF9tYXN0ZXIuXFx1MDA2MGVxdWlwbWVudF9pZFxcdTAwNjAgQVMgXFx1MDA2MGVxdWlwbWVudF9pZFxcdTAwNjAsIG5vZGVfc2lnbmFsX21hc3Rlci5cXHUwMDYwZmFjaWxpdHlfaWRcXHUwMDYwIEFTIFxcdTAwNjBmYWNpbGl0eV9pZFxcdTAwNjAsIG5vZGVfc2lnbmFsX21hc3Rlci5cXHUwMDYwdW5pdFxcdTAwNjAgQVMgXFx1MDA2MHVuaXRcXHUwMDYwXCJ9LFwiVGltZVNlcmllc1NlbGVjdG9yXCI6e1wiRW50aXR5VHlwZVwiOntcIk5hbWVcIjpcInNpZ25hbF9tYXN0ZXJcIn0sXCJLZXlDb2x1bW5zXCI6e1wib3BjdWFfbm9kZV9pZFwiOlwib3BjdWFfbm9kZV9pZFwifSxcIk1ldHJpY3NcIjpbe1wiRmllbGRcIjpcImV2ZW50X3RpbWVcIixcIkFnZ3JlZ2F0aW9uXCI6XCJMYXN0S25vd25WYWx1ZVwiLFwiQWxpYXNcIjpcImV2ZW50X3RpbWVcIn0se1wiRmllbGRcIjpcInF1YWxpdHlcIixcIkFnZ3JlZ2F0aW9uXCI6XCJMYXN0S25vd25WYWx1ZVwiLFwiQWxpYXNcIjpcInF1YWxpdHlcIn0se1wiRmllbGRcIjpcInZhbHVlXCIsXCJBZ2dyZWdhdGlvblwiOlwiTGFzdEtub3duVmFsdWVcIixcIkFsaWFzXCI6XCJ2YWx1ZVwifV0sXCJUaW1lUmFuZ2VcIjp7XCJTdGFydFwiOlwiMjAyNC0wNy0yOVQwMDowMDowMFpcIixcIkVuZFwiOlwiMjAyNi0wNy0yOVQyMDozNjowNVpcIn0sXCJHcm91cEJ5XCI6W1wib3BjdWFfbm9kZV9pZFwiXX19IiwiRGVzY3JpcHRpb24iOiJTZW5kIGFuIGVtYWlsIGFsZXJ0IHdoZW5ldmVyIGFuIE9QQyBVQSBzaWduYWwgaW4gc2lnbmFsX21hc3RlciBoYXMgcXVhbGl0eSBlcXVhbCB0byBCQUQsIGluY2x1ZGluZyBlcXVpcG1lbnQgYW5kIHNpZ25hbCBjb250ZXh0LiJ9LCJSdWxlQ29uZGl0aW9uIjp7IiR0eXBlIjoidGV4dHdoZW5pc2VxdWFsIiwiRGF0YVByb3BlcnR5TmFtZSI6InF1YWxpdHkiLCJWYWx1ZSI6IkJBRCJ9LCJBY3Rpb25CaW5kaW5nIjp7IiR0eXBlIjoibXVsdGlhY3Rpb25iaW5kaW5nIiwiRGVzY3JpcHRpb24iOiJBY3Rpb24gYmluZGluZ3MgZm9yIHRoaXMgcnVsZSIsIkFjdGlvbkJpbmRpbmdzIjpbeyJOYW1lIjoiU2VuZCBFbWFpbCBBbGVydCEiLCJEZXNjcmlwdGlvbiI6IlNlbmQgRW1haWwgQWxlcnQgc28gdGhhdCBhcHByb3ByaWF0ZSBBY3Rpb24gY2FuIGJlIHRha2VuISBSZXBsYWNlIHRoaXMgd2l0aCBhbnkgUGlwZWxpbmUgb3IgUG93ZXIgQXV0b21hdGUgRmxvdyBiYXNlZCBBY3Rpb24hIiwiQWN0aW9uSWQiOiI5NGVmNzE4ZC02YmRiLTQ2ZjMtOWExNS02NjFhZjRmYWJiMzkiLCJQYXJhbWV0ZXJCaW5kaW5ncyI6W3siJHR5cGUiOiJwYXJhbWV0ZXJiaW5kaW5nY29udGV4dGtleSIsIk5hbWUiOiJpZCIsIktleSI6ImFnZW50Om9wZXJhdGlvbmFsU2V0OnNpZ25hbF9tYXN0ZXI6b3BjdWFfbm9kZV9pZCIsIkRlc2NyaXB0aW9uIjoiVGhlIHVuaXF1ZSBpZGVudGlmaWVyIG9mIHRoZSBzaWduYWxfbWFzdGVyIGVudGl0eSJ9XX1dfSwiTG9jYWxPbnRvbG9neSI6eyJzaWduYWxfbWFzdGVyIjp7IiR0eXBlIjoiY2xhc3MiLCJJUkkiOiJzaWduYWxfbWFzdGVyIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJzaWduYWxfbWFzdGVyIiwiRGVzY3JpcHRpb24iOiJBbiBPUEMgVUEgc2lnbmFsIHJlY29yZCBwcm92aWRpbmcgZXF1aXBtZW50LCBmYWNpbGl0eSwgYW5kIG1lYXN1cmVtZW50IGNvbnRleHQuIn0sInVuaXQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoidW5pdCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoidW5pdCIsIkRlc2NyaXB0aW9uIjoiTWVhc3VyZW1lbnQgdW5pdCBmb3IgdmFsdWUiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sImZhY2lsaXR5X2lkIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6ImZhY2lsaXR5X2lkIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJmYWNpbGl0eV9pZCIsIkRlc2NyaXB0aW9uIjoiSWRlbnRpZmllciBvZiBmYWNpbGl0eSB3aGVyZSBlcXVpcG1lbnQgcmVzaWRlcyIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwidmFsdWUiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoidmFsdWUiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6InZhbHVlIiwiRGVzY3JpcHRpb24iOiJDdXJyZW50IG1lYXN1cmVkIHNpZ25hbCB2YWx1ZSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJkZWNpbWFsIiwiS2luZCI6MX0sImVxdWlwbWVudF9pZCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJlcXVpcG1lbnRfaWQiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6ImVxdWlwbWVudF9pZCIsIkRlc2NyaXB0aW9uIjoiSWRlbnRpZmllciBvZiBhZmZlY3RlZCBlcXVpcG1lbnQiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sInF1YWxpdHkiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoicXVhbGl0eSIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoicXVhbGl0eSIsIkRlc2NyaXB0aW9uIjoiQ3VycmVudCBPUEMgVUEgc2lnbmFsIHF1YWxpdHkiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sImV2ZW50X3RpbWUiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoiZXZlbnRfdGltZSIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoiZXZlbnRfdGltZSIsIkRlc2NyaXB0aW9uIjoiVGltZXN0YW1wIG9mIHRoZSBzaWduYWwgZXZlbnQiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sIm9wY3VhX25vZGVfaWQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoib3BjdWFfbm9kZV9pZCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoib3BjdWFfbm9kZV9pZCIsIkRlc2NyaXB0aW9uIjoiT1BDIFVBIG5vZGUgaWRlbnRpZmllciBmb3IgdGhlIHNpZ25hbCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjowfX19LCJhOGJlOWU5Mi1iYzYzLTQyOTYtODlmZC0yZDEwZDU5YjkzZjQiOnsiSWQiOiJhOGJlOWU5Mi1iYzYzLTQyOTYtODlmZC0yZDEwZDU5YjkzZjQiLCJOYW1lIjoiT1BDIFVBIFNpZ25hbCBRdWFsaXR5IFVOQ0VSVEFJTiBBbGVydCIsIkRlc2NyaXB0aW9uIjoiU2VuZCBhbiBlbWFpbCBhbGVydCB3aGVuZXZlciBhbiBPUEMgVUEgc2lnbmFsIGluIHNpZ25hbF9tYXN0ZXIgaGFzIHF1YWxpdHkgZXF1YWwgdG8gVU5DRVJUQUlOLCBpbmNsdWRpbmcgZXF1aXBtZW50IGFuZCBzaWduYWwgY29udGV4dC4iLCJDbGFzc0V4cHJlc3Npb24iOnsiJHR5cGUiOiJvbnRvbG9neXF1ZXJ5ZXhwcmVzc2lvbiIsIkV4cHJlc3Npb24iOiJ7XCJFbnRpdHlTZWxlY3RvclwiOntcInF1ZXJ5VHlwZVwiOlwiR1FMXCIsXCJRdWVyeVwiOlwiTUFUQ0ggKG5fc2lnbmFsX21hc3RlcjpcXHUwMDYwc2lnbmFsX21hc3RlclxcdTAwNjApIFJFVFVSTiBuX3NpZ25hbF9tYXN0ZXIuXFx1MDA2MG9wY3VhX25vZGVfaWRcXHUwMDYwIEFTIFxcdTAwNjBvcGN1YV9ub2RlX2lkXFx1MDA2MCwgbl9zaWduYWxfbWFzdGVyLlxcdTAwNjBlcXVpcG1lbnRfaWRcXHUwMDYwIEFTIFxcdTAwNjBlcXVpcG1lbnRfaWRcXHUwMDYwLCBuX3NpZ25hbF9tYXN0ZXIuXFx1MDA2MGZhY2lsaXR5X2lkXFx1MDA2MCBBUyBcXHUwMDYwZmFjaWxpdHlfaWRcXHUwMDYwLCBuX3NpZ25hbF9tYXN0ZXIuXFx1MDA2MHVuaXRcXHUwMDYwIEFTIFxcdTAwNjB1bml0XFx1MDA2MFwifSxcIlRpbWVTZXJpZXNTZWxlY3RvclwiOntcIkVudGl0eVR5cGVcIjp7XCJOYW1lXCI6XCJzaWduYWxfbWFzdGVyXCJ9LFwiS2V5Q29sdW1uc1wiOntcIm9wY3VhX25vZGVfaWRcIjpcIm9wY3VhX25vZGVfaWRcIn0sXCJNZXRyaWNzXCI6W3tcIkZpZWxkXCI6XCJxdWFsaXR5XCIsXCJBZ2dyZWdhdGlvblwiOlwiTGFzdEtub3duVmFsdWVcIixcIkFsaWFzXCI6XCJxdWFsaXR5XCJ9LHtcIkZpZWxkXCI6XCJ2YWx1ZVwiLFwiQWdncmVnYXRpb25cIjpcIkxhc3RLbm93blZhbHVlXCIsXCJBbGlhc1wiOlwidmFsdWVcIn0se1wiRmllbGRcIjpcImV2ZW50X3RpbWVcIixcIkFnZ3JlZ2F0aW9uXCI6XCJMYXN0S25vd25WYWx1ZVwiLFwiQWxpYXNcIjpcImV2ZW50X3RpbWVcIn1dLFwiVGltZVJhbmdlXCI6e1wiU3RhcnRcIjpcIjIwMjQtMDctMjlUMDA6MDA6MDBaXCIsXCJFbmRcIjpcIjIwMjYtMDctMjlUMjA6MzY6MjIuMzY3ODg5NFpcIn0sXCJHcm91cEJ5XCI6W1wib3BjdWFfbm9kZV9pZFwiXX19IiwiRGVzY3JpcHRpb24iOiJTZW5kIGFuIGVtYWlsIGFsZXJ0IHdoZW5ldmVyIGFuIE9QQyBVQSBzaWduYWwgaW4gc2lnbmFsX21hc3RlciBoYXMgcXVhbGl0eSBlcXVhbCB0byBVTkNFUlRBSU4sIGluY2x1ZGluZyBlcXVpcG1lbnQgYW5kIHNpZ25hbCBjb250ZXh0LiJ9LCJSdWxlQ29uZGl0aW9uIjp7IiR0eXBlIjoidGV4dHdoZW5pc2VxdWFsIiwiRGF0YVByb3BlcnR5TmFtZSI6InF1YWxpdHkiLCJWYWx1ZSI6IlVOQ0VSVEFJTiJ9LCJBY3Rpb25CaW5kaW5nIjp7IiR0eXBlIjoibXVsdGlhY3Rpb25iaW5kaW5nIiwiRGVzY3JpcHRpb24iOiJBY3Rpb24gYmluZGluZ3MgZm9yIHRoaXMgcnVsZSIsIkFjdGlvbkJpbmRpbmdzIjpbeyJOYW1lIjoiU2VuZCBFbWFpbCBBbGVydCEiLCJEZXNjcmlwdGlvbiI6IlNlbmQgRW1haWwgQWxlcnQgc28gdGhhdCBhcHByb3ByaWF0ZSBBY3Rpb24gY2FuIGJlIHRha2VuISBSZXBsYWNlIHRoaXMgd2l0aCBhbnkgUGlwZWxpbmUgb3IgUG93ZXIgQXV0b21hdGUgRmxvdyBiYXNlZCBBY3Rpb24hIiwiQWN0aW9uSWQiOiI5NGVmNzE4ZC02YmRiLTQ2ZjMtOWExNS02NjFhZjRmYWJiMzkiLCJQYXJhbWV0ZXJCaW5kaW5ncyI6W3siJHR5cGUiOiJwYXJhbWV0ZXJiaW5kaW5nY29udGV4dGtleSIsIk5hbWUiOiJvcGN1YV9ub2RlX2lkIiwiS2V5IjoiYWdlbnQ6b3BlcmF0aW9uYWxTZXQ6c2lnbmFsX21hc3RlcjpvcGN1YV9ub2RlX2lkIiwiRGVzY3JpcHRpb24iOiJUaGUgdW5pcXVlIGlkZW50aWZpZXIgb2YgdGhlIE9QQyBVQSBzaWduYWwgaW4gc2lnbmFsX21hc3RlciJ9XX1dfSwiTG9jYWxPbnRvbG9neSI6eyJzaWduYWxfbWFzdGVyIjp7IiR0eXBlIjoiY2xhc3MiLCJJUkkiOiJzaWduYWxfbWFzdGVyIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJzaWduYWxfbWFzdGVyIiwiRGVzY3JpcHRpb24iOiJBbiBPUEMgVUEgc2lnbmFsIHJlY29yZCBwcm92aWRpbmcgZXF1aXBtZW50LCBmYWNpbGl0eSwgYW5kIG1lYXN1cmVtZW50IGNvbnRleHQuIn0sInVuaXQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoidW5pdCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoidW5pdCIsIkRlc2NyaXB0aW9uIjoiTWVhc3VyZW1lbnQgdW5pdCBmb3IgdmFsdWUiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sImZhY2lsaXR5X2lkIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6ImZhY2lsaXR5X2lkIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJmYWNpbGl0eV9pZCIsIkRlc2NyaXB0aW9uIjoiSWRlbnRpZmllciBvZiBmYWNpbGl0eSB3aGVyZSBlcXVpcG1lbnQgcmVzaWRlcyIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwidmFsdWUiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoidmFsdWUiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6InZhbHVlIiwiRGVzY3JpcHRpb24iOiJDdXJyZW50IG1lYXN1cmVkIHNpZ25hbCB2YWx1ZSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJkZWNpbWFsIiwiS2luZCI6MX0sImVxdWlwbWVudF9pZCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJlcXVpcG1lbnRfaWQiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6ImVxdWlwbWVudF9pZCIsIkRlc2NyaXB0aW9uIjoiSWRlbnRpZmllciBvZiBhZmZlY3RlZCBlcXVpcG1lbnQiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sInF1YWxpdHkiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoicXVhbGl0eSIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoicXVhbGl0eSIsIkRlc2NyaXB0aW9uIjoiQ3VycmVudCBPUEMgVUEgc2lnbmFsIHF1YWxpdHkiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sImV2ZW50X3RpbWUiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoiZXZlbnRfdGltZSIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoiZXZlbnRfdGltZSIsIkRlc2NyaXB0aW9uIjoiVGltZXN0YW1wIG9mIHRoZSBzaWduYWwgZXZlbnQiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sIm9wY3VhX25vZGVfaWQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoib3BjdWFfbm9kZV9pZCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoib3BjdWFfbm9kZV9pZCIsIkRlc2NyaXB0aW9uIjoiT1BDIFVBIG5vZGUgaWRlbnRpZmllciBmb3IgdGhlIHNpZ25hbCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjowfX19fX0="


def build_configurations(should_run: Optional[bool] = None,
                         copy_playbook: Optional[bool] = None,
                         team_id: Optional[str] = None,
                         channel_id: Optional[str] = None) -> dict:
    """Configurations.json body, built from the embedded known-good definition.

    No external agent is read. `$schema`, the Ontology `dataSources`, the FabricJobAction
    wired to the Send Email Alert pipeline, `instructions` and `shouldRun` come from
    `EMBEDDED_CONFIGURATION` (env ids from CELL-0 settings). The Teams `messageDestination`
    is set from the resolved `team_id`/`channel_id` user inputs. When `copy_playbook` is true
    the byte-exact embedded playbook is attached so the agent is immediately playbook-ready;
    when false it is omitted and generated in the portal. `identity` is never set — the
    running user's delegated token provisions Run-as.
    """
    run_state = ops_agent_should_run if should_run is None else should_run
    keep_playbook = ops_agent_copy_playbook if copy_playbook is None else copy_playbook
    config = deepcopy(EMBEDDED_CONFIGURATION)
    config["shouldRun"] = run_state
    message_destination = config["configuration"]["messageDestination"]
    if team_id:
        message_destination["teamId"] = team_id
    if channel_id:
        message_destination["channelId"] = channel_id
    if keep_playbook:
        config["playbook"] = json.loads(base64.b64decode(EMBEDDED_PLAYBOOK_B64))
    return config


# -------------------------------------------------------------------------
# Deploy: create (empty) -> push instructions. Best-effort + manual fallback.
# -------------------------------------------------------------------------
ops_agent_item_id = None
try:
    get_access_token_for_fabric()
    print("✅ Got Fabric access token (delegated user context).")

    # Run-as guardrail: confirm the agent will Run as the intended (signed-in) user.
    check_run_as(ops_agent_run_as_user)

    print("✅ Using embedded known-good definition (no external reference agent):")
    print("   data source (Ontology)  :", ops_agent_ontology_datasource_id)
    print("   action (FabricJobAction): Send Email Alert! ->", ops_agent_email_pipeline_id)
    print("   message dest.           : TeamsChannel", ops_agent_teams_team_id, "/", ops_agent_teams_channel_id)

    # 1) Create (or reuse) the target Operations Agent — empty, no definition.
    ops_agent = create_operations_agent(ops_agent_name, OPS_AGENT_DESCRIPTION)
    ops_agent_item_id = ops_agent.get("id")

    # 2) Push the copied definition (verbatim instructions + playbook) via updateDefinition.
    #    Runs in User context so the agent's Run-as provisions correctly (Re-authenticate works).
    started = ops_agent_should_run
    try:
        configurations = build_configurations(
            should_run=started, team_id=ops_agent_teams_team_id, channel_id=ops_agent_teams_channel_id)
        json.dumps(configurations)  # validate serializable
        update_operations_agent_definition(ops_agent_item_id, configurations)
    except RuntimeError as update_exc:
        # If starting was refused, still deploy the definition stopped so nothing is half-done.
        if not started:
            raise
        print("ℹ️  Start (shouldRun=true) was refused — deploying stopped so the definition lands:")
        print("   ", update_exc)
        started = False
        configurations = build_configurations(
            should_run=False, team_id=ops_agent_teams_team_id, channel_id=ops_agent_teams_channel_id)
        update_operations_agent_definition(ops_agent_item_id, configurations)
    _run_state = "started (shouldRun=true)" if started else "deployed, stopped (shouldRun=false)"
    _pb_state = "with embedded playbook" if ops_agent_copy_playbook else "config-only (generate playbook in UI)"
    print(f"✅ Operations Agent '{ops_agent_name}' {_run_state}, {_pb_state} — Ontology data source,")
    print(f"   Teams destination and Send Email Alert pipeline action from the embedded definition (id={ops_agent_item_id}).")
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
print("✅ Set programmatically via REST (User context) from the embedded known-good definition:")
print("   instructions (verbatim), Ontology data source, Teams message destination, the")
print("   'Send Email Alert!' Fabric job action (wired to Pipe_SendEmailAlert), and run state.")
print("ℹ️  FabricJobAction carries its pipeline connection in the definition, so no UI wiring is")
print("   needed — the agent posts the Teams alert and runs the pipeline to email operations.")
if ops_agent_copy_playbook:
    print("ℹ️  The embedded playbook (OntologyDefinitions + RuleDefinitions) is attached byte-exact.")
    print("   The playbook is part of the pushable definition — the API can send it, but it CANNOT")
    print("   trigger generation. If the target doesn't show it as live, open the agent once and")
    print("   select 'Generate Playbook' to (re)bind/refresh it against current data.")
else:
    print("ℹ️  Deployed config-only (ops_agent_copy_playbook=false). Open the agent and select")
    print("   'Generate Playbook' in the portal; the instructions compile cleanly so it succeeds.")


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
