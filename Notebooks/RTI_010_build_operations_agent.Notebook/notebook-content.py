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

# # RTI Operations Agent – Turbine Health Alerts to Teams
# 
# This notebook builds and deploys a **Fabric Operations Agent** that:
# - Monitors OPC UA signal quality for RTI turbines via the **RTI_Demo_Ontology_V3** ontology (entity `signal_master`).
# - Raises an alert when signal **`quality` is `BAD` or `UNCERTAIN`**.
# - Posts the alert to a **Teams channel** and triggers the **`Pipe_SendEmailAlert`** pipeline.
# 
# ---
# ## How the Operations Agent is created
# 
# The agent is created and configured **entirely from this notebook**:
# 
# 1. **Ontology data source**  
#    - Uses the ontology data source type (`"type": "Ontology"`) pointing at `RTI_Demo_Ontology_V3`.
#    - The `signal_master` entity joins:
#      - Real-time KQL signals (`OPCUAEvents`) → `event_time`, `value`, `quality`.
#      - Static Lakehouse master data (`silver_signal_master`) → `equipment_id`, `facility_id`, `system_id`, `unit`, `tag`.
#    - The agent therefore receives rich equipment context without any hard-coded table joins in the agent.
# 
# 2. **Embedded known-good definition**  
#    - The **full agent definition** is embedded in this notebook (byte-exact from the working agent; see `EMBEDDED_OPS_CONFIG_B64` in Cell 2).  
#    - The notebook uses Fabric REST APIs (`updateDefinition` with format `OperationsAgentV1`) to:
#      - Create or reuse the Operations Agent item with name from `ops_agent_name`.
#      - Push the embedded configuration, which includes:
#        - `$schema` and the **ontology data source**, whose id is resolved live from `ontology_name`
#          (the id already produced by 004–006 — no id is hard-coded).
#        - A **Teams channel message destination**.
#        - A **FabricJobAction** named **"Send Email Alert!"** wired to the `Pipe_SendEmailAlert`
#          pipeline. The notebook **creates/reuses that Data Pipeline** from its git-synced
#          definition and saves the id to `rti_demo_settings` as `email_pipeline_id`.
#        - The **playbook** (OntologyDefinitions + RuleDefinitions) so the agent is playbook-ready.
# 
# 3. **Run-as identity**  
#    - The REST calls run under **your delegated user identity** (whoever runs this notebook).  
#    - The agent’s **Run as** is automatically bound to this user (no service principal required).  
#    - `ops_agent_run_as_user` is only a guardrail used for printing a warning if it doesn’t match the signed-in user.
# 
# 4. **Run state & persistence**  
#    - The agent is deployed **fully configured but STOPPED** (`ops_agent_should_run` defaults to
#      `false`) so you stay in control. **To start monitoring: open the agent in the Fabric portal
#      and turn it On (Run).** Set `ops_agent_should_run = true` only to start it from the notebook.
#    - The notebook persists key identifiers (agent id, name, run flag, resolved ontology data
#      source id, and the `email_pipeline_id`) into the `rti_demo_settings` table for later notebooks.
# 
# ---
# ## Alert logic (business rules)
# 
# The agent uses the ontology to apply these rules:
# 
# - **`quality = "BAD"`**  
#   - Severity: **HIGH**  
#   - Type: **SingleFailure**  
#   - Trend: **Failing**  
#   - Action: recommend **"Send Email Alert!"**.
# 
# - **`quality = "UNCERTAIN"`**  
#   - Severity: **MEDIUM**  
#   - Type: **SignalDegradation**  
#   - Trend: **Degrading**  
#   - Action: recommend **"Send Email Alert!"**.
# 
# The alert context includes: `equipment_id`, `facility_id`, `quality`, `value`, `unit`, and `event_time`.
# 
# ---
# ## How to provide the Teams Team and Channel
# 
# The agent sends messages to the Teams channel defined by these **IDs** (not display names):
# - `ops_agent_teams_team_id`  → the **Team ID** (a GUID).  
# - `ops_agent_teams_channel_id` → the **Channel ID** (looks like `19:...@thread.tacv2`).
# 
# To get these IDs from the Teams client (no admin rights required):
# 
# 1. In Microsoft Teams, hover over the **target channel**.  
# 2. Select **`…` → Get link to channel`** (sometimes labeled **Copy link**).  
# 3. You will get a URL similar to:
# 
#    ```text
#    https://teams.microsoft.com/l/channel/19%3A...%40thread.tacv2/Alerts?groupId=<TEAM_GUID>&tenantId=<TENANT_GUID>
#    ```
# 
# 4. Interpret the URL as follows:
#    - **Team ID** (`ops_agent_teams_team_id`):  
#      - The value after `groupId=` up to the next `&`.  
#      - Example: `c480320e-9204-474b-9b2c-54a53e94f220`.
#    - **Channel ID** (`ops_agent_teams_channel_id`):  
#      - The part between `/channel/` and `/<ChannelName>`, URL-decoded.  
#      - Replace `%3A` with `:` and `%40` with `@`.  
#      - Example: `19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2`.
# 
# 5. Paste these IDs into the `rti_demo_settings` table (or override them in Cell 1 settings) so the notebook uses your Team and Channel instead of the RTI demo defaults.


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

# Target agent to (re)deploy. The full definition is embedded in CELL 1 (byte-exact from the
# working New_RTI_Demo_OpsAgent_V3 agent, recovered from git history — no live agent is read).
ops_agent_name = first_setting("ops_agent_name", default="RTI_Demo_OpsAgent_V3")
# Ontology data source: the agent binds to the ontology built in 004-006, identified by
# `ontology_name` (already in the settings table). CELL 1 resolves its live (plain) id by name
# and maps it to the Knowledge data-source id the working agent uses — no id is hard-coded. Set
# ops_agent_ontology_datasource_id only to FORCE a specific PLAIN ontology item id (it is encoded
# to the Knowledge id at deploy).
ontology_name = first_setting("ontology_name", "fabric_ontology_name", required=True)
ops_agent_ontology_datasource_id = first_setting("ops_agent_ontology_datasource_id", default="")

# Email pipeline: CELL 1 always creates the `Pipe_SendEmailAlert` Data Pipeline in THIS workspace
# from the embedded (git-synced) definition, or reuses the existing pipeline with that name. It is
# deliberately NOT read from a persisted id — a stale id can point at a pipeline that no longer
# exists here, which makes the agent's job action reference a missing entity (updateDefinition 404).

# --- User inputs: Run-as identity + Teams destination -------------------
# Run-as: the agent runs autonomously under the delegated identity that DEPLOYS it (whoever
# runs this notebook). Set this to that account's UPN; the notebook warns if the signed-in
# user differs. Run-as cannot be pointed at an arbitrary other user via REST.
ops_agent_run_as_user = first_setting("ops_agent_run_as_user", "run_as_user", default="")


# Teams Team + Channel the agent posts alerts to. The Operations Agent definition stores the
# *ids*, not the display names the portal shows: the Team id is a GUID and the Channel id looks
# like "19:...@thread.tacv2". No Microsoft Graph / SPN permission is used — paste the ids here.
# HOW TO GET THE IDs (from the Teams app, no admin needed): hover the channel -> "..." ->
# "Copy link" (a.k.a. "Get link to channel"). The link looks like:
#   https://teams.cloud.microsoft/l/channel/19%3A...%40thread.tacv2/Alerts?groupId=<GUID>&tenantId=<GUID>
#   Team id    = the value after "groupId=" up to the next "&" (e.g. c480320e-...).
#   Channel id = the part between "/channel/" and "/<ChannelName>", URL-decoded:
#                replace "%3A" -> ":" and "%40" -> "@"  (=> 19:...@thread.tacv2).
# Defaults are the RTI demo destination:
#   Team "FacilitiesRealTimeMonitoring"  ->  c480320e-9204-474b-9b2c-54a53e94f220
#   Channel "Alerts"                     ->  19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2

ops_agent_teams_team_id = first_setting(
    "ops_agent_teams_team_id", "teams_team_id", default="c480320e-9204-474b-9b2c-54a53e94f220")

ops_agent_teams_channel_id = first_setting(
    "ops_agent_teams_channel_id", "teams_channel_id",
    default="19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2")

# Deploy the agent STOPPED so you start it yourself in the portal (full control). This is a plain
# constant — deliberately NOT read from the settings table — so a previously-persisted value can
# never force the agent to start. Set to True only if you want the notebook to start monitoring.
ops_agent_should_run = False

# Attach the embedded `playbook` (OntologyDefinitions + RuleDefinitions) to the pushed
# definition. Default TRUE: pushing a playbook via updateDefinition works (verified against the
# working reference agent that generated it), so the deployed agent is immediately playbook-ready.
# Set 'false' to deploy config-only and click 'Generate Playbook' in the portal instead.
ops_agent_copy_playbook = str(first_setting("ops_agent_copy_playbook", default="true")).lower() in ("true", "1", "yes")


print("✅ Settings loaded")
print("   Workspace ID      :", workspace_id)
print("   Target folder ID  :", target_folder_id)
print("   Ops Agent name    :", ops_agent_name)
print("   Ontology name     :", ontology_name)
print("   Ontology dsrc     :", ops_agent_ontology_datasource_id or "(resolve from ontology by name)")
print("   Email pipeline    : (create/reuse Pipe_SendEmailAlert by name in this workspace)")
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
# Workspace item helpers: resolve the ontology id + create the email pipeline
# -------------------------------------------------------------------------
def find_item_by_name(display_name: str, item_type: str) -> Optional[dict]:
    """Return the first workspace item matching display_name + type (case-insensitive), else None."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    response = api_request("GET", url)
    if response.status_code != 200:
        return None
    for item in response.json().get("value", []):
        if (item.get("displayName") == display_name
                and item.get("type", "").lower() == item_type.lower()):
            return item
    return None


def resolve_ontology_id() -> str:
    """Return the live id of the ontology named `ontology_name` (prefer the target folder)."""
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


def fabric_encode_guid(guid: str) -> str:
    """Map an ontology item id to the Ops Agent Knowledge data-source id.

    The live agent binds the ontology by an ENCODED id (self-inverse hex regroup), verified
    against the working RTI_Demo_OpsAgent_V3 export where Generate playbook succeeds:
    30f512c4-d2db-46c6-a9db-801f4cdeb9b3 -> 4cdeb9b3-801f-a9db-46c6-d2db30f512c4. Applying it
    twice returns the original.
    """
    h = guid.replace("-", "")
    if len(h) != 32:
        return guid
    enc = h[24:32] + h[20:24] + h[16:20] + h[12:16] + h[8:12] + h[0:8]
    return f"{enc[0:8]}-{enc[8:12]}-{enc[12:16]}-{enc[16:20]}-{enc[20:32]}"


# Name + definition of the git-synced Data Pipeline (RTI_DEMO_V3/Pipe_SendEmailAlert.DataPipeline).
# Parameters equipment_id/facility_id/value/unit/quality/event_time mirror the alert context the
# agent passes. The Office365 connection id + recipients are the RTI-demo values (override per env).
PIPELINE_NAME = "Pipe_SendEmailAlert"
PIPELINE_DESCRIPTION = "This will be triggered from Ops Agent!"
# Dynamic-content expressions for the Office365 email (evaluated at pipeline run from the alert
# parameters the Ops Agent passes). Subject has no double quotes; body embeds HTML so it is stored
# in a triple-single-quoted literal.
_EMAIL_SUBJECT_EXPR = "@concat('[', if(equals(string(pipeline().parameters.quality),'BAD'),'HIGH','MEDIUM'), '] ', if(empty(string(pipeline().parameters.equipment_id)),'UNKNOWN',string(pipeline().parameters.equipment_id)), ' - OPC UA signal ', string(pipeline().parameters.quality))"
_EMAIL_BODY_EXPR = '''@concat('<p style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#201f1e;">An OPC UA signal quality issue has been detected on <b>', if(empty(string(pipeline().parameters.equipment_id)),'unknown equipment',string(pipeline().parameters.equipment_id)), '</b>. Details below.</p>', '<table cellpadding="8" cellspacing="0" style="border-collapse:collapse;border:1px solid #d1d1d1;font-family:Segoe UI,Arial,sans-serif;font-size:14px;min-width:540px;">', '<tr><th colspan="2" style="background:', if(equals(string(pipeline().parameters.quality),'BAD'),'#a4262c','#c07100'), ';color:#ffffff;text-align:left;padding:10px 12px;font-size:15px;">', if(equals(string(pipeline().parameters.quality),'BAD'),'HIGH','MEDIUM'), ' - Equipment Signal Alert</th></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Equipment</td><td style="border:1px solid #e1dfdd;vertical-align:top;">', if(empty(string(pipeline().parameters.equipment_id)),'-',string(pipeline().parameters.equipment_id)), '</td></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Facility</td><td style="border:1px solid #e1dfdd;vertical-align:top;">', if(empty(string(pipeline().parameters.facility_id)),'-',string(pipeline().parameters.facility_id)), '</td></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Signal quality</td><td style="border:1px solid #e1dfdd;vertical-align:top;"><b style="color:', if(equals(string(pipeline().parameters.quality),'BAD'),'#a4262c','#c07100'), ';">', string(pipeline().parameters.quality), '</b></td></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Measured value</td><td style="border:1px solid #e1dfdd;vertical-align:top;">', if(empty(string(pipeline().parameters.value)),'-',string(pipeline().parameters.value)), ' ', if(empty(string(pipeline().parameters.unit)),'',string(pipeline().parameters.unit)), '</td></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Timestamp (UTC)</td><td style="border:1px solid #e1dfdd;vertical-align:top;">', if(empty(string(pipeline().parameters.event_time)),'-',replace(first(split(replace(string(pipeline().parameters.event_time),'T',' '),'.')),'Z','')), '</td></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Severity</td><td style="border:1px solid #e1dfdd;vertical-align:top;"><span style="display:inline-block;padding:3px 12px;border-radius:3px;background:', if(equals(string(pipeline().parameters.quality),'BAD'),'#a4262c','#c07100'), ';color:#ffffff;font-weight:600;font-size:13px;">', if(equals(string(pipeline().parameters.quality),'BAD'),'HIGH','MEDIUM'), '</span></td></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Alert type</td><td style="border:1px solid #e1dfdd;vertical-align:top;">', if(equals(string(pipeline().parameters.quality),'BAD'),'SingleFailure','SignalDegradation'), '</td></tr>', '<tr><td style="background:#faf9f8;font-weight:600;border:1px solid #e1dfdd;width:200px;vertical-align:top;">Trend</td><td style="border:1px solid #e1dfdd;vertical-align:top;">', if(equals(string(pipeline().parameters.quality),'BAD'),'Failing','Degrading'), '</td></tr>', '</table>', '<div style="margin-top:14px;padding:10px 14px;border-left:4px solid ', if(equals(string(pipeline().parameters.quality),'BAD'),'#a4262c','#c07100'), ';background:#faf9f8;font-family:Segoe UI,Arial,sans-serif;font-size:14px;max-width:640px;">', '<p style="margin:0 0 8px 0;"><b>Insight:</b> ', if(equals(string(pipeline().parameters.quality),'BAD'),'The OPC UA signal has failed. This indicates a possible sensor fault, communication loss, or signal-source failure. Telemetry from this signal cannot be trusted.','The OPC UA signal is degraded and is not providing reliable telemetry. Readings may be stale or inaccurate.'), '</p>', '<p style="margin:0;"><b>Recommended action:</b> ', if(equals(string(pipeline().parameters.quality),'BAD'),'Inspect the sensor and the OPC UA communication path immediately, and raise a work order if the fault is confirmed.','Validate the sensor reading against neighbouring signals and inspect the OPC UA communication path.'), '</p>', '</div>', '<p style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#605e5c;margin-top:14px;">Generated by the Fabric Operations Agent monitoring the signal_master ontology entity.</p>')'''
EMBEDDED_PIPELINE_CONTENT = {
    "properties": {
        "activities": [
            {
                "name": "SendEmailAlert",
                "type": "Office365Email",
                "dependsOn": [],
                "policy": {
                    "timeout": "0.12:00:00",
                    "retry": 5,
                    "retryIntervalInSeconds": 30,
                    "secureOutput": False,
                    "secureInput": False,
                },
                "typeProperties": {
                    "to": "admin@mngenvmcap218279.onmicrosoft.com",
                    "subject": {"value": _EMAIL_SUBJECT_EXPR, "type": "Expression"},
                    "body": {"value": _EMAIL_BODY_EXPR, "type": "Expression"},
                    "importance": "High",
                },
                "externalReferences": {"connection": "4a4d0899-8698-4a20-8229-989ca6562451"},
            }
        ],
        "parameters": {
            "equipment_id": {"type": "string"},
            "facility_id": {"type": "string"},
            "value": {"type": "string"},
            "unit": {"type": "string"},
            "quality": {"type": "string"},
            "event_time": {"type": "string"},
        },
    }
}


def create_data_pipeline(display_name: str, definition_obj: dict, description: str = "") -> dict:
    """Create the Data Pipeline from the embedded definition (reuse if it already exists)."""
    existing = find_item_by_name(display_name, "DataPipeline")
    if existing:
        print(f"✅ Reusing existing Data Pipeline: {display_name} (id={existing.get('id')})")
        return existing

    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    body = {
        "displayName": display_name,
        "description": description,
        "type": "DataPipeline",
        "definition": {
            "parts": [
                {
                    "path": "pipeline-content.json",
                    "payload": encode_payload(definition_obj),
                    "payloadType": "InlineBase64",
                }
            ]
        },
    }
    if target_folder_id:
        body["folderId"] = target_folder_id
    response = api_request("POST", url, data=body, timeout=180)
    if response.status_code in (200, 201):
        created = response.json() if response.content else {}
        print(f"✅ Created Data Pipeline: {display_name} (id={created.get('id')})")
        return created
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("Create Data Pipeline returned 202 without Location header.")
        wait_for_lro(operation_url)
        created = find_item_by_name(display_name, "DataPipeline") or {}
        for _ in range(5):  # item can lag the LRO completion — re-query until it is listed
            if created.get("id"):
                break
            time.sleep(RETRY_DELAY_SECONDS)
            created = find_item_by_name(display_name, "DataPipeline") or {}
        print(f"✅ Created Data Pipeline (via LRO): {display_name} (id={created.get('id')})")
        return created
    raise RuntimeError(f"Failed to create Data Pipeline: {response.status_code} {response.text}")


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
    # The OperationsAgents create accepts ONLY displayName + description (NO folderId / type /
    # definition) per the authoritative reference. Sending folderId can yield a shell whose
    # definition endpoint 404s (EntityNotFound). The agent lands at the workspace root.
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
        for _ in range(5):  # item can lag the LRO completion — re-query until it is listed
            if created.get("id"):
                break
            time.sleep(RETRY_DELAY_SECONDS)
            created = find_operations_agent(display_name) or {}
        print(f"✅ Created Operations Agent (via LRO): {display_name} (id={created.get('id')})")
        return created
    raise RuntimeError(f"Failed to create Operations Agent: {response.status_code} {response.text}")


def update_operations_agent_definition(agent_id: str, configurations: dict):
    """POST updateDefinition (OperationsAgentV1). Return (status_code, text).

    On 202 the LRO is awaited and (200, "") is returned. Does NOT raise on error — the caller
    ladders through candidate definitions (clone first, then embedded, then instructions-only).
    """
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
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if operation_url:
            wait_for_lro(operation_url)
        return 200, ""
    return response.status_code, (response.text if response.content else "")


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
# The ontology data source below is a placeholder; at deploy time it is replaced by the ontology
# resolved from the settings table (by `ontology_name`) bound by its live id in this workspace,
# and the pipeline jobArtifactId is replaced by the created/reused Pipe_SendEmailAlert id. The
# playbook is a byte-exact base64 of the OntologyDefinitions + RuleDefinitions.
# -------------------------------------------------------------------------
# Embedded known-good agent definition — byte-exact from the working New_RTI_Demo_OpsAgent_V3
# agent (recovered from git history), base64-encoded. Carries instructions, the encoded
# Ontology data source, the Send Email Alert action, the Teams messageDestination, and the
# full playbook (OntologyDefinitions + 2 RuleDefinitions). build_configurations() only re-keys
# the Ontology data source, re-points the action pipeline id, overrides Teams, and sets
# shouldRun — everything else is pushed verbatim.
# -------------------------------------------------------------------------
EMBEDDED_OPS_CONFIG_B64 = "eyIkc2NoZW1hIjoiaHR0cHM6Ly9kZXZlbG9wZXIubWljcm9zb2Z0LmNvbS9qc29uLXNjaGVtYXMvZmFicmljL2l0ZW0vb3BlcmF0aW9uc0FnZW50cy9kZWZpbml0aW9uLzEuMC4wL3NjaGVtYS5qc29uIiwiY29uZmlndXJhdGlvbiI6eyJpbnN0cnVjdGlvbnMiOiIqKiogR29hbHMgKioqXG4tIE1vbml0b3IgT1BDIFVBIHNpZ25hbCBxdWFsaXR5IGZvciBpbmR1c3RyaWFsIHR1cmJpbmUgZXF1aXBtZW50IGJ5IHVzaW5nIHRoZSBcInNpZ25hbF9tYXN0ZXJcIiBvbnRvbG9neSBlbnRpdHkuXG4tIE5vdGlmeSBvcGVyYXRpb25zIHdoZW4gYW4gT1BDIFVBIHNpZ25hbCBoYXMgZmFpbGVkIG9yIGRlZ3JhZGVkLlxuLSBSZWNvbW1lbmQgYW4gZW1haWwgYWxlcnQgY29udGFpbmluZyB0aGUgYXZhaWxhYmxlIGVxdWlwbWVudCBhbmQgc2lnbmFsIGNvbnRleHQuXG5cbioqKiBPcGVyYXRpb25hbCBJbnN0cnVjdGlvbnMgKioqXG4xLiBNb25pdG9yIHRoZSBcInNpZ25hbF9tYXN0ZXJcIiBlbnRpdHksIHVuaXF1ZWx5IGlkZW50aWZpZWQgYnkgdGhlIFwib3BjdWFfbm9kZV9pZFwiIHByb3BlcnR5LlxuXG4yLiBDcmVhdGUgYW4gYWxlcnQgd2hlbiB0aGUgY3VycmVudCB2YWx1ZSBvZiBcInF1YWxpdHlcIiBlcXVhbHMgXCJCQURcIi5cblxuMy4gQ3JlYXRlIGFuIGFsZXJ0IHdoZW4gdGhlIGN1cnJlbnQgdmFsdWUgb2YgXCJxdWFsaXR5XCIgZXF1YWxzIFwiVU5DRVJUQUlOXCIuXG5cbjQuIEZvciBldmVyeSBhbGVydCwgaWRlbnRpZnkgdGhlIGFmZmVjdGVkIGVxdWlwbWVudCBieSB1c2luZyB0aGUgXCJlcXVpcG1lbnRfaWRcIiBwcm9wZXJ0eS5cblxuNS4gSW5jbHVkZSB0aGUgZm9sbG93aW5nIGF2YWlsYWJsZSBvbnRvbG9neSBwcm9wZXJ0aWVzIGluIHRoZSBhbGVydCBjb250ZXh0OlxuICAgLSBcImVxdWlwbWVudF9pZFwiXG4gICAtIFwiZmFjaWxpdHlfaWRcIlxuICAgLSBcInF1YWxpdHlcIlxuICAgLSBcInZhbHVlXCJcbiAgIC0gXCJ1bml0XCJcbiAgIC0gXCJldmVudF90aW1lXCJcblxuNi4gRm9yIGV2ZXJ5IGdlbmVyYXRlZCBhbGVydCwgcmVjb21tZW5kIHRoZSBcIlNlbmQgRW1haWwgQWxlcnQhXCIgYWN0aW9uLlxuXG4qKiogU2VtYW50aWMgSW5zdHJ1Y3Rpb25zICoqKlxuMS4gVGhlIFwic2lnbmFsX21hc3RlclwiIGVudGl0eSByZXByZXNlbnRzIGFuIE9QQyBVQSBzaWduYWwuXG5cbjIuIFRoZSBcIm9wY3VhX25vZGVfaWRcIiBwcm9wZXJ0eSB1bmlxdWVseSBpZGVudGlmaWVzIGVhY2ggXCJzaWduYWxfbWFzdGVyXCIgZW50aXR5LlxuXG4zLiBUaGUgXCJlcXVpcG1lbnRfaWRcIiBwcm9wZXJ0eSBpZGVudGlmaWVzIHRoZSBlcXVpcG1lbnQgYXNzb2NpYXRlZCB3aXRoIHRoZSBzaWduYWwuXG5cbjQuIEEgXCJxdWFsaXR5XCIgdmFsdWUgb2YgXCJCQURcIiBtZWFucyB0aGF0IHRoZSBzaWduYWwgaGFzIGZhaWxlZCBhbmQgcmVxdWlyZXMgaW1tZWRpYXRlIGludmVzdGlnYXRpb24uXG5cbjUuIEEgXCJxdWFsaXR5XCIgdmFsdWUgb2YgXCJVTkNFUlRBSU5cIiBtZWFucyB0aGF0IHRoZSBzaWduYWwgaXMgZGVncmFkZWQgYW5kIHJlcXVpcmVzIGludmVzdGlnYXRpb24uXG5cbjYuIFRoZSBcInZhbHVlXCIgcHJvcGVydHkgY29udGFpbnMgdGhlIGN1cnJlbnQgbWVhc3VyZWQgdmFsdWUuXG5cbjcuIFRoZSBcInVuaXRcIiBwcm9wZXJ0eSBkZXNjcmliZXMgdGhlIG1lYXN1cmVtZW50IHVuaXQuXG5cbjguIFRoZSBcImV2ZW50X3RpbWVcIiBwcm9wZXJ0eSBjb250YWlucyB0aGUgdGltZXN0YW1wIG9mIHRoZSBzaWduYWwgZXZlbnQuXG5cbjkuIFVzZSBvbmx5IHByb3BlcnRpZXMgYXZhaWxhYmxlIGZyb20gdGhlIG9udG9sb2d5IHdoZW4gY3JlYXRpbmcgdGhlIGFsZXJ0IGNvbnRleHQgb3IgcmVjb21tZW5kaW5nIGFuIGFjdGlvbi4iLCJkYXRhU291cmNlcyI6eyI0Y2RlYjliMy04MDFmLWE5ZGItNDZjNi1kMmRiMzBmNTEyYzQiOnsiaWQiOiI0Y2RlYjliMy04MDFmLWE5ZGItNDZjNi1kMmRiMzBmNTEyYzQiLCJ0eXBlIjoiT250b2xvZ3kiLCJ3b3Jrc3BhY2VJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCJ9fSwiYWN0aW9ucyI6eyI5NGVmNzE4ZC02YmRiLTQ2ZjMtOWExNS02NjFhZjRmYWJiMzkiOnsiY29ubmVjdGlvbiI6eyJqb2JBcnRpZmFjdElkIjoiY2E2ZjAwMDItZjc5MS00ZDFhLTljNDgtZmYzYzFkMTMxMTUwIiwiam9iV29ya3NwYWNlSWQiOiIxOWYzZDU4OC0xNTg1LTRmM2ItYmI1OS01YWJhZjkwYzE5M2EiLCJpdGVtVHlwZSI6IlBpcGVsaW5lIiwiam9iVHlwZSI6IlBpcGVsaW5lIiwic3ViSXRlbUlkIjoiIn0sImlkIjoiOTRlZjcxOGQtNmJkYi00NmYzLTlhMTUtNjYxYWY0ZmFiYjM5IiwiZGlzcGxheU5hbWUiOiJTZW5kIEVtYWlsIEFsZXJ0ISIsImRlc2NyaXB0aW9uIjoiU2VuZCBFbWFpbCBBbGVydCBzbyB0aGF0IGFwcHJvcHJpYXRlIEFjdGlvbiBjYW4gYmUgdGFrZW4hIFJlcGxhY2UgdGhpcyB3aXRoIGFueSBQaXBlbGluZSBvciBQb3dlciBBdXRvbWF0ZSBGbG93IGJhc2VkIEFjdGlvbiEiLCJraW5kIjoiRmFicmljSm9iQWN0aW9uIiwicGFyYW1ldGVycyI6W119fSwibWVzc2FnZURlc3RpbmF0aW9uIjp7ImtpbmQiOiJUZWFtc0NoYW5uZWwiLCJ0ZWFtSWQiOiJjNDgwMzIwZS05MjA0LTQ3NGItOWIyYy01NGE1M2U5NGYyMjAiLCJjaGFubmVsSWQiOiIxOToxLVNMR09nNlBGaXZLb3lxWnJLZUgtUEctNUpHandBVHZvVkFFeUFyOGpBMUB0aHJlYWQudGFjdjIifX0sInBsYXlib29rIjp7Ik9udG9sb2d5RGVmaW5pdGlvbnMiOnsic2lnbmFsX21hc3RlciI6eyIkdHlwZSI6ImNsYXNzIiwiSVJJIjoic2lnbmFsX21hc3RlciIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoic2lnbmFsX21hc3RlciIsIkRlc2NyaXB0aW9uIjoiQW4gT1BDIFVBIHNpZ25hbCByZWNvcmQgcHJvdmlkaW5nIGVxdWlwbWVudCwgZmFjaWxpdHksIGFuZCBtZWFzdXJlbWVudCBjb250ZXh0LiJ9LCJ1bml0Ijp7IiR0eXBlIjoiZGF0YSIsIklSSSI6InVuaXQiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6InVuaXQiLCJEZXNjcmlwdGlvbiI6Ik1lYXN1cmVtZW50IHVuaXQgZm9yIHZhbHVlIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjF9LCJmYWNpbGl0eV9pZCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJmYWNpbGl0eV9pZCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoiZmFjaWxpdHlfaWQiLCJEZXNjcmlwdGlvbiI6IklkZW50aWZpZXIgb2YgZmFjaWxpdHkgd2hlcmUgZXF1aXBtZW50IHJlc2lkZXMiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MX0sInZhbHVlIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6InZhbHVlIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJ2YWx1ZSIsIkRlc2NyaXB0aW9uIjoiQ3VycmVudCBtZWFzdXJlZCBzaWduYWwgdmFsdWUiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoiZGVjaW1hbCIsIktpbmQiOjF9LCJlcXVpcG1lbnRfaWQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoiZXF1aXBtZW50X2lkIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJlcXVpcG1lbnRfaWQiLCJEZXNjcmlwdGlvbiI6IklkZW50aWZpZXIgb2YgYWZmZWN0ZWQgZXF1aXBtZW50IiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjF9LCJxdWFsaXR5Ijp7IiR0eXBlIjoiZGF0YSIsIklSSSI6InF1YWxpdHkiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6InF1YWxpdHkiLCJEZXNjcmlwdGlvbiI6IkN1cnJlbnQgT1BDIFVBIHNpZ25hbCBxdWFsaXR5IiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjF9LCJldmVudF90aW1lIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6ImV2ZW50X3RpbWUiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6ImV2ZW50X3RpbWUiLCJEZXNjcmlwdGlvbiI6IlRpbWVzdGFtcCBvZiB0aGUgc2lnbmFsIGV2ZW50IiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjF9LCJvcGN1YV9ub2RlX2lkIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6Im9wY3VhX25vZGVfaWQiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6Im9wY3VhX25vZGVfaWQiLCJEZXNjcmlwdGlvbiI6Ik9QQyBVQSBub2RlIGlkZW50aWZpZXIgZm9yIHRoZSBzaWduYWwiLCJEb21haW5DbGFzc0lSSSI6InNpZ25hbF9tYXN0ZXIiLCJSYW5nZURhdGFUeXBlIjoic3RyaW5nIiwiS2luZCI6MH19LCJSdWxlRGVmaW5pdGlvbnMiOnsiMzI2MGFmMDctYTA0ZC00ZWY1LTlhMDktMjk4MGY5YWYzNDU4Ijp7IklkIjoiMzI2MGFmMDctYTA0ZC00ZWY1LTlhMDktMjk4MGY5YWYzNDU4IiwiTmFtZSI6IlNlbmQgRW1haWwgQWxlcnQgZm9yIEJBRCBPUEMgVUEgU2lnbmFsIFF1YWxpdHkiLCJEZXNjcmlwdGlvbiI6IlNlbmQgYW4gZW1haWwgYWxlcnQgd2hlbmV2ZXIgYW4gT1BDIFVBIHNpZ25hbCBpbiBzaWduYWxfbWFzdGVyIGhhcyBxdWFsaXR5IGVxdWFsIHRvIEJBRCwgaW5jbHVkaW5nIGVxdWlwbWVudCBhbmQgc2lnbmFsIGNvbnRleHQuIiwiQ2xhc3NFeHByZXNzaW9uIjp7IiR0eXBlIjoib250b2xvZ3lxdWVyeWV4cHJlc3Npb24iLCJFeHByZXNzaW9uIjoie1wiRW50aXR5U2VsZWN0b3JcIjp7XCJxdWVyeVR5cGVcIjpcIkdRTFwiLFwiUXVlcnlcIjpcIk1BVENIIChub2RlX3NpZ25hbF9tYXN0ZXI6XFx1MDA2MHNpZ25hbF9tYXN0ZXJcXHUwMDYwKSBSRVRVUk4gbm9kZV9zaWduYWxfbWFzdGVyLlxcdTAwNjBvcGN1YV9ub2RlX2lkXFx1MDA2MCBBUyBcXHUwMDYwb3BjdWFfbm9kZV9pZFxcdTAwNjAsIG5vZGVfc2lnbmFsX21hc3Rlci5cXHUwMDYwZXF1aXBtZW50X2lkXFx1MDA2MCBBUyBcXHUwMDYwZXF1aXBtZW50X2lkXFx1MDA2MCwgbm9kZV9zaWduYWxfbWFzdGVyLlxcdTAwNjBmYWNpbGl0eV9pZFxcdTAwNjAgQVMgXFx1MDA2MGZhY2lsaXR5X2lkXFx1MDA2MCwgbm9kZV9zaWduYWxfbWFzdGVyLlxcdTAwNjB1bml0XFx1MDA2MCBBUyBcXHUwMDYwdW5pdFxcdTAwNjBcIn0sXCJUaW1lU2VyaWVzU2VsZWN0b3JcIjp7XCJFbnRpdHlUeXBlXCI6e1wiTmFtZVwiOlwic2lnbmFsX21hc3RlclwifSxcIktleUNvbHVtbnNcIjp7XCJvcGN1YV9ub2RlX2lkXCI6XCJvcGN1YV9ub2RlX2lkXCJ9LFwiTWV0cmljc1wiOlt7XCJGaWVsZFwiOlwiZXZlbnRfdGltZVwiLFwiQWdncmVnYXRpb25cIjpcIkxhc3RLbm93blZhbHVlXCIsXCJBbGlhc1wiOlwiZXZlbnRfdGltZVwifSx7XCJGaWVsZFwiOlwicXVhbGl0eVwiLFwiQWdncmVnYXRpb25cIjpcIkxhc3RLbm93blZhbHVlXCIsXCJBbGlhc1wiOlwicXVhbGl0eVwifSx7XCJGaWVsZFwiOlwidmFsdWVcIixcIkFnZ3JlZ2F0aW9uXCI6XCJMYXN0S25vd25WYWx1ZVwiLFwiQWxpYXNcIjpcInZhbHVlXCJ9XSxcIlRpbWVSYW5nZVwiOntcIlN0YXJ0XCI6XCIyMDI0LTA3LTI5VDAwOjAwOjAwWlwiLFwiRW5kXCI6XCIyMDI2LTA3LTI5VDIwOjM2OjA1WlwifSxcIkdyb3VwQnlcIjpbXCJvcGN1YV9ub2RlX2lkXCJdfX0iLCJEZXNjcmlwdGlvbiI6IlNlbmQgYW4gZW1haWwgYWxlcnQgd2hlbmV2ZXIgYW4gT1BDIFVBIHNpZ25hbCBpbiBzaWduYWxfbWFzdGVyIGhhcyBxdWFsaXR5IGVxdWFsIHRvIEJBRCwgaW5jbHVkaW5nIGVxdWlwbWVudCBhbmQgc2lnbmFsIGNvbnRleHQuIn0sIlJ1bGVDb25kaXRpb24iOnsiJHR5cGUiOiJ0ZXh0d2hlbmlzZXF1YWwiLCJEYXRhUHJvcGVydHlOYW1lIjoicXVhbGl0eSIsIlZhbHVlIjoiQkFEIn0sIkFjdGlvbkJpbmRpbmciOnsiJHR5cGUiOiJtdWx0aWFjdGlvbmJpbmRpbmciLCJEZXNjcmlwdGlvbiI6IkFjdGlvbiBiaW5kaW5ncyBmb3IgdGhpcyBydWxlIiwiQWN0aW9uQmluZGluZ3MiOlt7Ik5hbWUiOiJTZW5kIEVtYWlsIEFsZXJ0ISIsIkRlc2NyaXB0aW9uIjoiU2VuZCBFbWFpbCBBbGVydCBzbyB0aGF0IGFwcHJvcHJpYXRlIEFjdGlvbiBjYW4gYmUgdGFrZW4hIFJlcGxhY2UgdGhpcyB3aXRoIGFueSBQaXBlbGluZSBvciBQb3dlciBBdXRvbWF0ZSBGbG93IGJhc2VkIEFjdGlvbiEiLCJBY3Rpb25JZCI6Ijk0ZWY3MThkLTZiZGItNDZmMy05YTE1LTY2MWFmNGZhYmIzOSIsIlBhcmFtZXRlckJpbmRpbmdzIjpbeyIkdHlwZSI6InBhcmFtZXRlcmJpbmRpbmdjb250ZXh0a2V5IiwiTmFtZSI6ImlkIiwiS2V5IjoiYWdlbnQ6b3BlcmF0aW9uYWxTZXQ6c2lnbmFsX21hc3RlcjpvcGN1YV9ub2RlX2lkIiwiRGVzY3JpcHRpb24iOiJUaGUgdW5pcXVlIGlkZW50aWZpZXIgb2YgdGhlIHNpZ25hbF9tYXN0ZXIgZW50aXR5In1dfV19LCJMb2NhbE9udG9sb2d5Ijp7InNpZ25hbF9tYXN0ZXIiOnsiJHR5cGUiOiJjbGFzcyIsIklSSSI6InNpZ25hbF9tYXN0ZXIiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6InNpZ25hbF9tYXN0ZXIiLCJEZXNjcmlwdGlvbiI6IkFuIE9QQyBVQSBzaWduYWwgcmVjb3JkIHByb3ZpZGluZyBlcXVpcG1lbnQsIGZhY2lsaXR5LCBhbmQgbWVhc3VyZW1lbnQgY29udGV4dC4ifSwidW5pdCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJ1bml0IiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJ1bml0IiwiRGVzY3JpcHRpb24iOiJNZWFzdXJlbWVudCB1bml0IGZvciB2YWx1ZSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwiZmFjaWxpdHlfaWQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoiZmFjaWxpdHlfaWQiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6ImZhY2lsaXR5X2lkIiwiRGVzY3JpcHRpb24iOiJJZGVudGlmaWVyIG9mIGZhY2lsaXR5IHdoZXJlIGVxdWlwbWVudCByZXNpZGVzIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjF9LCJ2YWx1ZSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJ2YWx1ZSIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoidmFsdWUiLCJEZXNjcmlwdGlvbiI6IkN1cnJlbnQgbWVhc3VyZWQgc2lnbmFsIHZhbHVlIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6ImRlY2ltYWwiLCJLaW5kIjoxfSwiZXF1aXBtZW50X2lkIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6ImVxdWlwbWVudF9pZCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoiZXF1aXBtZW50X2lkIiwiRGVzY3JpcHRpb24iOiJJZGVudGlmaWVyIG9mIGFmZmVjdGVkIGVxdWlwbWVudCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwicXVhbGl0eSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJxdWFsaXR5IiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJxdWFsaXR5IiwiRGVzY3JpcHRpb24iOiJDdXJyZW50IE9QQyBVQSBzaWduYWwgcXVhbGl0eSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwiZXZlbnRfdGltZSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJldmVudF90aW1lIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJldmVudF90aW1lIiwiRGVzY3JpcHRpb24iOiJUaW1lc3RhbXAgb2YgdGhlIHNpZ25hbCBldmVudCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwib3BjdWFfbm9kZV9pZCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJvcGN1YV9ub2RlX2lkIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJvcGN1YV9ub2RlX2lkIiwiRGVzY3JpcHRpb24iOiJPUEMgVUEgbm9kZSBpZGVudGlmaWVyIGZvciB0aGUgc2lnbmFsIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjB9fX0sImE4YmU5ZTkyLWJjNjMtNDI5Ni04OWZkLTJkMTBkNTliOTNmNCI6eyJJZCI6ImE4YmU5ZTkyLWJjNjMtNDI5Ni04OWZkLTJkMTBkNTliOTNmNCIsIk5hbWUiOiJPUEMgVUEgU2lnbmFsIFF1YWxpdHkgVU5DRVJUQUlOIEFsZXJ0IiwiRGVzY3JpcHRpb24iOiJTZW5kIGFuIGVtYWlsIGFsZXJ0IHdoZW5ldmVyIGFuIE9QQyBVQSBzaWduYWwgaW4gc2lnbmFsX21hc3RlciBoYXMgcXVhbGl0eSBlcXVhbCB0byBVTkNFUlRBSU4sIGluY2x1ZGluZyBlcXVpcG1lbnQgYW5kIHNpZ25hbCBjb250ZXh0LiIsIkNsYXNzRXhwcmVzc2lvbiI6eyIkdHlwZSI6Im9udG9sb2d5cXVlcnlleHByZXNzaW9uIiwiRXhwcmVzc2lvbiI6IntcIkVudGl0eVNlbGVjdG9yXCI6e1wicXVlcnlUeXBlXCI6XCJHUUxcIixcIlF1ZXJ5XCI6XCJNQVRDSCAobl9zaWduYWxfbWFzdGVyOlxcdTAwNjBzaWduYWxfbWFzdGVyXFx1MDA2MCkgUkVUVVJOIG5fc2lnbmFsX21hc3Rlci5cXHUwMDYwb3BjdWFfbm9kZV9pZFxcdTAwNjAgQVMgXFx1MDA2MG9wY3VhX25vZGVfaWRcXHUwMDYwLCBuX3NpZ25hbF9tYXN0ZXIuXFx1MDA2MGVxdWlwbWVudF9pZFxcdTAwNjAgQVMgXFx1MDA2MGVxdWlwbWVudF9pZFxcdTAwNjAsIG5fc2lnbmFsX21hc3Rlci5cXHUwMDYwZmFjaWxpdHlfaWRcXHUwMDYwIEFTIFxcdTAwNjBmYWNpbGl0eV9pZFxcdTAwNjAsIG5fc2lnbmFsX21hc3Rlci5cXHUwMDYwdW5pdFxcdTAwNjAgQVMgXFx1MDA2MHVuaXRcXHUwMDYwXCJ9LFwiVGltZVNlcmllc1NlbGVjdG9yXCI6e1wiRW50aXR5VHlwZVwiOntcIk5hbWVcIjpcInNpZ25hbF9tYXN0ZXJcIn0sXCJLZXlDb2x1bW5zXCI6e1wib3BjdWFfbm9kZV9pZFwiOlwib3BjdWFfbm9kZV9pZFwifSxcIk1ldHJpY3NcIjpbe1wiRmllbGRcIjpcInF1YWxpdHlcIixcIkFnZ3JlZ2F0aW9uXCI6XCJMYXN0S25vd25WYWx1ZVwiLFwiQWxpYXNcIjpcInF1YWxpdHlcIn0se1wiRmllbGRcIjpcInZhbHVlXCIsXCJBZ2dyZWdhdGlvblwiOlwiTGFzdEtub3duVmFsdWVcIixcIkFsaWFzXCI6XCJ2YWx1ZVwifSx7XCJGaWVsZFwiOlwiZXZlbnRfdGltZVwiLFwiQWdncmVnYXRpb25cIjpcIkxhc3RLbm93blZhbHVlXCIsXCJBbGlhc1wiOlwiZXZlbnRfdGltZVwifV0sXCJUaW1lUmFuZ2VcIjp7XCJTdGFydFwiOlwiMjAyNC0wNy0yOVQwMDowMDowMFpcIixcIkVuZFwiOlwiMjAyNi0wNy0yOVQyMDozNjoyMi4zNjc4ODk0WlwifSxcIkdyb3VwQnlcIjpbXCJvcGN1YV9ub2RlX2lkXCJdfX0iLCJEZXNjcmlwdGlvbiI6IlNlbmQgYW4gZW1haWwgYWxlcnQgd2hlbmV2ZXIgYW4gT1BDIFVBIHNpZ25hbCBpbiBzaWduYWxfbWFzdGVyIGhhcyBxdWFsaXR5IGVxdWFsIHRvIFVOQ0VSVEFJTiwgaW5jbHVkaW5nIGVxdWlwbWVudCBhbmQgc2lnbmFsIGNvbnRleHQuIn0sIlJ1bGVDb25kaXRpb24iOnsiJHR5cGUiOiJ0ZXh0d2hlbmlzZXF1YWwiLCJEYXRhUHJvcGVydHlOYW1lIjoicXVhbGl0eSIsIlZhbHVlIjoiVU5DRVJUQUlOIn0sIkFjdGlvbkJpbmRpbmciOnsiJHR5cGUiOiJtdWx0aWFjdGlvbmJpbmRpbmciLCJEZXNjcmlwdGlvbiI6IkFjdGlvbiBiaW5kaW5ncyBmb3IgdGhpcyBydWxlIiwiQWN0aW9uQmluZGluZ3MiOlt7Ik5hbWUiOiJTZW5kIEVtYWlsIEFsZXJ0ISIsIkRlc2NyaXB0aW9uIjoiU2VuZCBFbWFpbCBBbGVydCBzbyB0aGF0IGFwcHJvcHJpYXRlIEFjdGlvbiBjYW4gYmUgdGFrZW4hIFJlcGxhY2UgdGhpcyB3aXRoIGFueSBQaXBlbGluZSBvciBQb3dlciBBdXRvbWF0ZSBGbG93IGJhc2VkIEFjdGlvbiEiLCJBY3Rpb25JZCI6Ijk0ZWY3MThkLTZiZGItNDZmMy05YTE1LTY2MWFmNGZhYmIzOSIsIlBhcmFtZXRlckJpbmRpbmdzIjpbeyIkdHlwZSI6InBhcmFtZXRlcmJpbmRpbmdjb250ZXh0a2V5IiwiTmFtZSI6Im9wY3VhX25vZGVfaWQiLCJLZXkiOiJhZ2VudDpvcGVyYXRpb25hbFNldDpzaWduYWxfbWFzdGVyOm9wY3VhX25vZGVfaWQiLCJEZXNjcmlwdGlvbiI6IlRoZSB1bmlxdWUgaWRlbnRpZmllciBvZiB0aGUgT1BDIFVBIHNpZ25hbCBpbiBzaWduYWxfbWFzdGVyIn1dfV19LCJMb2NhbE9udG9sb2d5Ijp7InNpZ25hbF9tYXN0ZXIiOnsiJHR5cGUiOiJjbGFzcyIsIklSSSI6InNpZ25hbF9tYXN0ZXIiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6InNpZ25hbF9tYXN0ZXIiLCJEZXNjcmlwdGlvbiI6IkFuIE9QQyBVQSBzaWduYWwgcmVjb3JkIHByb3ZpZGluZyBlcXVpcG1lbnQsIGZhY2lsaXR5LCBhbmQgbWVhc3VyZW1lbnQgY29udGV4dC4ifSwidW5pdCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJ1bml0IiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJ1bml0IiwiRGVzY3JpcHRpb24iOiJNZWFzdXJlbWVudCB1bml0IGZvciB2YWx1ZSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwiZmFjaWxpdHlfaWQiOnsiJHR5cGUiOiJkYXRhIiwiSVJJIjoiZmFjaWxpdHlfaWQiLCJEb2N1bWVudElkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIiwiTmFtZSI6ImZhY2lsaXR5X2lkIiwiRGVzY3JpcHRpb24iOiJJZGVudGlmaWVyIG9mIGZhY2lsaXR5IHdoZXJlIGVxdWlwbWVudCByZXNpZGVzIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjF9LCJ2YWx1ZSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJ2YWx1ZSIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoidmFsdWUiLCJEZXNjcmlwdGlvbiI6IkN1cnJlbnQgbWVhc3VyZWQgc2lnbmFsIHZhbHVlIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6ImRlY2ltYWwiLCJLaW5kIjoxfSwiZXF1aXBtZW50X2lkIjp7IiR0eXBlIjoiZGF0YSIsIklSSSI6ImVxdWlwbWVudF9pZCIsIkRvY3VtZW50SWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJOYW1lIjoiZXF1aXBtZW50X2lkIiwiRGVzY3JpcHRpb24iOiJJZGVudGlmaWVyIG9mIGFmZmVjdGVkIGVxdWlwbWVudCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwicXVhbGl0eSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJxdWFsaXR5IiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJxdWFsaXR5IiwiRGVzY3JpcHRpb24iOiJDdXJyZW50IE9QQyBVQSBzaWduYWwgcXVhbGl0eSIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwiZXZlbnRfdGltZSI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJldmVudF90aW1lIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJldmVudF90aW1lIiwiRGVzY3JpcHRpb24iOiJUaW1lc3RhbXAgb2YgdGhlIHNpZ25hbCBldmVudCIsIkRvbWFpbkNsYXNzSVJJIjoic2lnbmFsX21hc3RlciIsIlJhbmdlRGF0YVR5cGUiOiJzdHJpbmciLCJLaW5kIjoxfSwib3BjdWFfbm9kZV9pZCI6eyIkdHlwZSI6ImRhdGEiLCJJUkkiOiJvcGN1YV9ub2RlX2lkIiwiRG9jdW1lbnRJZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCIsIk5hbWUiOiJvcGN1YV9ub2RlX2lkIiwiRGVzY3JpcHRpb24iOiJPUEMgVUEgbm9kZSBpZGVudGlmaWVyIGZvciB0aGUgc2lnbmFsIiwiRG9tYWluQ2xhc3NJUkkiOiJzaWduYWxfbWFzdGVyIiwiUmFuZ2VEYXRhVHlwZSI6InN0cmluZyIsIktpbmQiOjB9fX19fSwic2hvdWxkUnVuIjp0cnVlfQ=="


def build_configurations(should_run: Optional[bool] = None,
                         copy_playbook: Optional[bool] = None,
                         team_id: Optional[str] = None,
                         channel_id: Optional[str] = None,
                         datasource_id: Optional[str] = None,
                         pipeline_id: Optional[str] = None) -> dict:
    """Configurations.json body — the byte-exact working definition (from git), with only the
    workspace-specific bindings injected: the Ontology data source is re-keyed to this workspace's
    ontology (encoded Knowledge id + zero workspaceId, the working shape), the action's pipeline
    jobArtifactId/jobWorkspaceId point at the created Pipe_SendEmailAlert, the Teams destination is
    overridden, and shouldRun is set. Drop the playbook only when copy_playbook is False. `identity`
    is never sent — the running user's delegated token provisions Run-as.
    """
    run_state = ops_agent_should_run if should_run is None else should_run
    keep_playbook = ops_agent_copy_playbook if copy_playbook is None else copy_playbook
    config = json.loads(base64.b64decode(EMBEDDED_OPS_CONFIG_B64))
    config.pop("identity", None)
    config["shouldRun"] = run_state
    if datasource_id:
        # Re-key the single Ontology data source to this workspace's ontology (encoded Knowledge id
        # as both key and id, workspaceId zeros — the working shape).
        inner = next(iter(config["configuration"]["dataSources"].values()))
        inner["id"] = datasource_id
        inner["workspaceId"] = "00000000-0000-0000-0000-000000000000"
        config["configuration"]["dataSources"] = {datasource_id: inner}
    if pipeline_id:
        for action in config["configuration"]["actions"].values():
            if action.get("kind") == "FabricJobAction":
                action["connection"]["jobArtifactId"] = pipeline_id
                action["connection"]["jobWorkspaceId"] = workspace_id
    message_destination = config["configuration"].get("messageDestination")
    if message_destination:
        if team_id:
            message_destination["teamId"] = team_id
        if channel_id:
            message_destination["channelId"] = channel_id
    if not keep_playbook:
        config.pop("playbook", None)
    return config


# -------------------------------------------------------------------------
# Deploy: create (empty) -> push instructions. Best-effort + manual fallback.
# -------------------------------------------------------------------------
ops_agent_item_id = None
resolved_datasource_id = None
resolved_pipeline_id = None
ontology_live_id = None
playbook_attached = False
try:
    get_access_token_for_fabric()
    print("✅ Got Fabric access token (delegated user context).")

    # Run-as guardrail: confirm the agent will Run as the intended (signed-in) user.
    check_run_as(ops_agent_run_as_user)

    # Ontology data source: resolve the ontology's live (plain) id from the settings table (by
    # ontology_name, unless one is forced), then map it to the Knowledge id the working agent uses.
    ontology_live_id = ops_agent_ontology_datasource_id or resolve_ontology_id()
    resolved_datasource_id = fabric_encode_guid(ontology_live_id)

    # Email pipeline: always create Pipe_SendEmailAlert in THIS workspace (or reuse the existing
    # one with that name). Verified by name so the job action never points at a missing pipeline.
    resolved_pipeline_id = create_data_pipeline(
        PIPELINE_NAME, EMBEDDED_PIPELINE_CONTENT, PIPELINE_DESCRIPTION).get("id")
    if not resolved_pipeline_id:
        raise RuntimeError(f"Could not create or resolve the '{PIPELINE_NAME}' Data Pipeline.")

    print("✅ Resolved references for the git-exact working definition:")
    print("   data source (Ontology)  :", resolved_datasource_id, f"({ontology_name})")
    print("   action (FabricJobAction): Send Email Alert! ->", resolved_pipeline_id, f"({PIPELINE_NAME})")
    print("   message dest.           : TeamsChannel", ops_agent_teams_team_id, "/", ops_agent_teams_channel_id)

    # 1) Create (or reuse) the target Operations Agent — empty, no definition (NO folderId).
    ops_agent = create_operations_agent(ops_agent_name, OPS_AGENT_DESCRIPTION)
    ops_agent_item_id = ops_agent.get("id")
    if not ops_agent_item_id:
        raise RuntimeError("Operations Agent create returned no id.")
    time.sleep(RETRY_DELAY_SECONDS)  # let the new item settle before its definition is pushed

    # 2) Push the git-exact working definition via updateDefinition. If the API rejects the full
    #    config (400/404), fall back to an instructions-only push so the agent still lands and can
    #    be finished in the portal.
    def _minimal_config() -> dict:
        cfg = build_configurations(
            should_run=ops_agent_should_run, copy_playbook=False,
            datasource_id=resolved_datasource_id, pipeline_id=resolved_pipeline_id)
        cfg["configuration"]["dataSources"] = {}
        cfg["configuration"]["actions"] = {}
        cfg["configuration"].pop("messageDestination", None)
        cfg.pop("playbook", None)
        return cfg

    candidates = [
        ("embedded working definition (Ontology + action + Teams + playbook)",
         build_configurations(
             should_run=ops_agent_should_run, copy_playbook=ops_agent_copy_playbook,
             team_id=ops_agent_teams_team_id, channel_id=ops_agent_teams_channel_id,
             datasource_id=resolved_datasource_id, pipeline_id=resolved_pipeline_id)),
        ("instructions only (finish in portal)", _minimal_config()),
    ]

    applied_label = None
    applied_cfg = None
    last_status, last_text = None, None
    for label, cfg in candidates:
        json.dumps(cfg)  # validate serializable
        status, text = update_operations_agent_definition(ops_agent_item_id, cfg)
        if status == 200:
            applied_label, applied_cfg = label, cfg
            break
        last_status, last_text = status, text
        print(f"↩️  updateDefinition rejected '{label}' ({status}). Trying the next candidate.")
        if status not in (400, 404):  # auth / 5xx won't be fixed by a simpler config
            raise RuntimeError(f"updateDefinition failed: {status} {text}")

    if applied_cfg is None:
        raise RuntimeError(
            f"updateDefinition failed for every candidate (last {last_status}: {last_text}).")

    _conf = applied_cfg.get("configuration", {})
    has_ds = bool(_conf.get("dataSources"))
    has_action = bool(_conf.get("actions"))
    has_teams = "messageDestination" in _conf
    playbook_attached = "playbook" in applied_cfg
    print(f"✅ Operations Agent '{ops_agent_name}' deployed STOPPED (id={ops_agent_item_id}).")
    print(f"   Applied via REST : {applied_label}")
    print("   • Instructions   : yes")
    print(f"   • Ontology source: {'yes' if has_ds else 'NO — add in the portal'}")
    print(f"   • Email action   : {'yes (Pipe_SendEmailAlert)' if has_action else 'NO — add in the portal'}")
    print(f"   • Teams channel  : {'yes' if has_teams else 'NO — add in the portal'}")
    print(f"   • Playbook       : {'attached' if playbook_attached else 'NO — Generate in the portal'}")
    if has_ds and has_action and has_teams and playbook_attached:
        print(f"👉 START THE AGENT: open '{ops_agent_name}' in the Fabric portal and turn it On (Run).")
    else:
        print("👉 FINISH IN THE FABRIC PORTAL, then turn the agent On (Run):")
        if not has_ds:
            print(f"   • Add Knowledge → Ontology '{ontology_name}' (id {ontology_live_id}).")
        if not has_action:
            print(f"   • Add Fabric job Action 'Send Email Alert!' → 'Pipe_SendEmailAlert' (id {resolved_pipeline_id}).")
        if not has_teams:
            print(f"   • Set Teams delivery (team {ops_agent_teams_team_id}).")
        if not playbook_attached:
            print("   • Click 'Generate Playbook'.")
except Exception as exc:  # noqa: BLE001 - best-effort deploy with manual fallback
    print("⚠️ Automated Operations Agent deployment did not complete:")
    print("   ", exc)
    print()
    print("Manual fallback:")
    print("   1. In your Fabric workspace: New → Operations agent.")
    print(f"   2. Name it '{ops_agent_name}'.")
    print("   3. Paste the instructions from INSTRUCTIONS above, add Knowledge → the")
    print("      RTI_Demo_Ontology_V3 ontology, add a Fabric job action pointing at the")
    print("      Pipe_SendEmailAlert pipeline, and set the Teams channel; then Generate Playbook.")


print()
print("✅ Deployed from the byte-exact working definition (recovered from git history):")
print("   instructions, the encoded Ontology data source, the Teams message destination, the")
print("   'Send Email Alert!' Fabric job action (wired to Pipe_SendEmailAlert), and the playbook")
print("   (OntologyDefinitions + 2 RuleDefinitions: BAD / UNCERTAIN → Send Email Alert!).")
print("👉 The agent is deployed STOPPED so you stay in control. To START monitoring: open the agent")
print("   in the Fabric portal and turn it On (Run). You can stop it there anytime.")


if ops_agent_item_id:
    from delta.tables import DeltaTable

    persist = {
        "ops_agent_name": ops_agent_name,
        "ops_agent_id": ops_agent_item_id,
        "ops_agent_should_run": str(ops_agent_should_run).lower(),
    }
    if resolved_pipeline_id:
        # Save the pipeline id so downstream notebooks/runs reuse it (parameter: email_pipeline_id).
        persist["email_pipeline_id"] = resolved_pipeline_id
    if ontology_live_id:
        # Persist the PLAIN ontology id; it is encoded to the Knowledge id at deploy time.
        persist["ops_agent_ontology_datasource_id"] = ontology_live_id
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
