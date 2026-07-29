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
# Rules:
# - `quality = "BAD"` → severity HIGH, type SingleFailure, trend Failing → raise WO.
# - `quality = "UNCERTAIN"` → severity MEDIUM, type SignalDegradation, trend Degrading → raise WO.
# The Power Automate action ("New WO to Investigate/Repair") is declared here; connect
# it to the imported flow (`Raw/PowerAutomate/`) in the Operations Agent UI after deploy.
# This notebook: reads settings → resolves the live `ontology_id` → builds
# `Configurations.json` → deploys the **OperationsAgent** item via REST (best-effort,
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
import uuid
import base64

import requests
import notebookutils  # Fabric notebook utility

FABRIC_BASE_URL = "https://api.fabric.microsoft.com/v1"

# Stable id for the Power Automate action (connect to the imported flow after deploy).
WO_ACTION_ID = "72c769c8-3260-4c10-9413-3548ca3a3e2e"
WO_ACTION_ALIAS = "Action: New WO and notify"


# -------------------------------------------------------------------------
# SPN auth + generic Fabric item helpers
# -------------------------------------------------------------------------
def get_spn_access_token_for_fabric():
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
    resp = requests.post(token_url, data=data)
    resp.raise_for_status()
    return resp.json()["access_token"]


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


def find_existing_item(access_token, display_name, item_type):
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    for item in resp.json().get("value", []):
        if item.get("displayName") == display_name and item.get("type", "").lower() == item_type.lower():
            return item
    return None


def resolve_ontology_id(access_token):
    """Return the id of the ontology item named `ontology_name` in the target folder."""
    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    matches = [
        it for it in resp.json().get("value", [])
        if it.get("displayName") == ontology_name and it.get("type", "").lower() == "ontology"
    ]
    if not matches:
        raise RuntimeError(f"Ontology '{ontology_name}' not found. Run 004–006 first.")
    in_folder = [it for it in matches if it.get("folderId") == target_folder_id]
    return (in_folder or matches)[0]["id"]


def wait_for_lro(resp, access_token, max_tries=30, delay_sec=5):
    """Follow a Fabric long-running-operation (202) until it completes."""
    location = resp.headers.get("Location")
    if not location:
        return resp
    headers = {"Authorization": f"Bearer {access_token}"}
    for attempt in range(1, max_tries + 1):
        poll = requests.get(location, headers=headers)
        status = poll.json().get("status") if poll.content else None
        if status in ("Succeeded", "Completed"):
            print(f"✅ Operation succeeded (attempt {attempt}).")
            return poll
        if status == "Failed":
            raise Exception(f"Operation failed: {poll.text}")
        print(f"⏳ In progress (attempt {attempt}/{max_tries}, status={status})...")
        time.sleep(delay_sec)
    raise Exception("Operation did not complete in the allotted time.")


def deploy_item_with_parts(access_token, display_name, item_type, parts):
    """Create or updateDefinition a Fabric item from (path, text) definition parts."""
    definition = {
        "parts": [
            {"path": path, "payload": b64(text), "payloadType": "InlineBase64"}
            for path, text in parts
        ]
    }
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    existing = find_existing_item(access_token, display_name, item_type)
    if existing:
        item_id = existing["id"]
        url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items/{item_id}/updateDefinition"
        resp = requests.post(url, headers=headers, json={"definition": definition})
        if resp.status_code not in (200, 202):
            raise Exception(f"updateDefinition failed: {resp.status_code} | {resp.text}")
        wait_for_lro(resp, access_token)
        print(f"♻️ Updated existing {item_type} '{display_name}' (ID: {item_id}).")
        return item_id

    url = f"{FABRIC_BASE_URL}/workspaces/{workspace_id}/items"
    payload = {
        "displayName": display_name,
        "type": item_type,
        "folderId": target_folder_id,
        "definition": definition,
    }
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201, 202):
        raise Exception(f"create {item_type} failed: {resp.status_code} | {resp.text}")
    if resp.status_code == 202:
        poll = wait_for_lro(resp, access_token)
        body = poll.json() if poll.content else {}
        item_id = body.get("id") or (body.get("resourceLocation") or "").rsplit("/", 1)[-1]
    else:
        item_id = resp.json().get("id")
    print(f"✅ Created {item_type} '{display_name}' (ID: {item_id}).")
    return item_id


# -------------------------------------------------------------------------
# Operations Agent definition (schema: operationsAgents/definition/1.0.0)
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

# The 6 parameters passed to the Power Automate flow.
ACTION_PARAMETERS = [
    ("equipment_id", "ID of the equipment"),
    ("facility_id", "ID of the facility"),
    ("value", "Measured value"),
    ("unit", "Unit of measurement"),
    ("quality", "Signal quality"),
    ("event_time", "Time of the event"),
]


def _parameter_bindings():
    """Bind each action parameter to a signal_master property (via the ontology)."""
    descriptions = dict(ACTION_PARAMETERS)
    return [
        {
            "$type": "parameterbindingcontextkey",
            "Name": name,
            "Key": f"agent:operationalSet:signal_master:{name}",
            "Description": descriptions[name],
        }
        for name, _ in ACTION_PARAMETERS
    ]


def _rule(rule_id, name, quality_value, description):
    return {
        "Id": rule_id,
        "Name": name,
        "Description": description,
        "ClassExpression": {
            "$type": "manchesterclassexp",
            "Expression": (
                "ClassExpression: AllSignals ``` signal_master ``` "
                "Annotations: metadata:description \"All telemetry signals monitored for quality.\""
            ),
            "Description": "All telemetry signals monitored for quality.",
        },
        "RuleCondition": {
            "$type": "textwhenisequal",
            "DataPropertyName": "quality",
            "Value": quality_value,
        },
        "ActionBinding": {
            "$type": "actionbinding",
            "Name": WO_ACTION_ALIAS,
            "Description": "New WO and notify",
            "ActionId": WO_ACTION_ID,
            "ParameterBindings": _parameter_bindings(),
        },
    }


def build_ops_agent_parts(ontology_id: str):
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "OperationsAgent", "displayName": ops_agent_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }

    configurations = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/operationsAgents/definition/1.0.0/schema.json",
        "configuration": {
            "instructions": INSTRUCTIONS,
            "dataSources": {
                ontology_name: {
                    "id": ontology_id,
                    "type": "Ontology",
                    "workspaceId": workspace_id,
                }
            },
            "actions": {
                WO_ACTION_ALIAS: {
                    "id": WO_ACTION_ID,
                    "displayName": WO_ACTION_ALIAS,
                    "description": "New WO and notify",
                    "kind": "PowerAutomateAction",
                    "parameters": [
                        {"name": name, "description": desc}
                        for name, desc in ACTION_PARAMETERS
                    ],
                }
            },
            "messageDestination": {
                "kind": "Recipient",
                "recipient": ops_agent_recipient,
            },
        },
        "playbook": {
            "RuleDefinitions": {
                "f3e7c7d2-8b6e-4e2a-9c1c-7e8b2f4c1e9a": _rule(
                    "f3e7c7d2-8b6e-4e2a-9c1c-7e8b2f4c1e9a",
                    "Uncertain Signal Quality",
                    "UNCERTAIN",
                    "Monitor signal_master entities where quality = 'UNCERTAIN' and raise a work order.",
                ),
                "b7c8e2e1-3e6c-4a8e-9c2d-4e2b9e8a1f2b": _rule(
                    "b7c8e2e1-3e6c-4a8e-9c2d-4e2b9e8a1f2b",
                    "Bad Signal Quality",
                    "BAD",
                    "Monitor signal_master entities where quality = 'BAD' and raise a work order.",
                ),
            }
        },
        "shouldRun": False,
    }

    return [
        (".platform", json.dumps(platform, indent=2)),
        ("Configurations.json", json.dumps(configurations, indent=2)),
    ]


# -------------------------------------------------------------------------
# Deploy (best-effort) + persist identifiers
# -------------------------------------------------------------------------
ops_agent_item_id = None
try:
    access_token = get_spn_access_token_for_fabric()
    print("✅ Got Fabric access token (SPN).")

    ontology_id = resolve_ontology_id(access_token)
    print("✅ Resolved ontology ID:", ontology_id)

    parts = build_ops_agent_parts(ontology_id)
    for path, text in parts:
        json.loads(text)  # validate every JSON part before deploying
    print(f"✅ Operations Agent definition built and validated ({len(parts)} parts).")

    ops_agent_item_id = deploy_item_with_parts(access_token, ops_agent_name, "OperationsAgent", parts)
except Exception as exc:  # noqa: BLE001 - best-effort deploy with manual fallback
    print("⚠️ Automated Operations Agent deployment did not complete:")
    print("   ", exc)
    print()
    print("Manual fallback:")
    print("   1. In your Fabric workspace: New → Operations agent.")
    print(f"   2. Name it '{ops_agent_name}'.")
    print(f"   3. Add data source → Ontology → '{ontology_name}'.")
    print("   4. Paste the instructions from INSTRUCTIONS above.")
    print("   5. Add rules on signal_master.quality = BAD and = UNCERTAIN.")
    print("   6. Add a Power Automate action and connect it to the imported flow")
    print("      (Raw/PowerAutomate/NewWOtoInvestigateRepair_*.zip).")


print()
print("ℹ️ Next step (manual): open the Operations Agent → the 'New WO to Investigate/Repair'")
print("   action → connect it to the Power Automate flow imported from Raw/PowerAutomate/.")
print("   Then set the agent to run (shouldRun is deployed as false).")


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
