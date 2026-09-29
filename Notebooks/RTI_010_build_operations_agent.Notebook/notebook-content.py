# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "c42060fb-abbc-4fa1-ac87-ed0a5c460aaf",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI_V6",
# META       "default_lakehouse_workspace_id": "a79a4b7e-e508-4fa4-8b6f-15deadca0f34",
# META       "known_lakehouses": [
# META         {
# META           "id": "c42060fb-abbc-4fa1-ac87-ed0a5c460aaf"
# META         }
# META       ]
# META     },
# META     "environment": {}
# META   }
# META }

# MARKDOWN ********************

# # 10 - Ontology v2 Operations Agent capability check
#
# This notebook is **v2-only**. It reads the live Ontology resource and requires
# `properties.generation == 2`. Existing generation 1 ontologies or agent sources
# must be replaced through an explicit migration; this notebook does not mutate them.
#
# `ontology_operations_agent_mode` accepts `auto` (default), `enabled`, or `disabled`.
# No verified generation 2 Operations Agent automation/playbook contract is implemented:
# auto records `blocked`; disabled records `skipped`; enabled records `blocked` and fails
# with an actionable error. Fabric known issue 1970 tracks new-experience playbook generation
# timeout, not permanent lack of product support.
#
# No agent, connection, email pipeline, definition, or playbook is created or configured.
# No legacy playbook is embedded. Existing artifacts are left untouched. The shared
# `ops_agent_deployment_status` and `ops_agent_deployment_reason` replace any stale success
# indication. A structured notebook exit reports capability, status, reason, mode, and
# observed generation; successful notebook completion never means an agent is ready.

# CELL ********************

from pyspark.sql import functions as F

settings_table_name = "rti_demo_settings"
spark.catalog.clearCache()
spark.sql(f"REFRESH TABLE {settings_table_name}")
settings = {
    row["setting_name"]: row["setting_value"]
    for row in spark.read.table(settings_table_name).collect()
}


def first_setting(*names, required: bool = False, default: str = None):
    for name in names:
        value = settings.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    if required:
        raise RuntimeError(f"Missing required setting. Tried: {list(names)}")
    return default


workspace_id = first_setting("workspace_id", required=True)
target_folder_id = first_setting("target_folder_id", default=None)
ontology_name = first_setting("ontology_name", "fabric_ontology_name", required=True)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json
import time

import requests
import notebookutils

FABRIC_API_BASE = "https://api.fabric.microsoft.com"


def api_request(method: str, url: str, params=None):
    if method != "GET":
        raise ValueError("Operations Agent capability checks are read-only.")
    for attempt in range(3):
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {notebookutils.credentials.getToken('pbi')}"},
            params=params,
            timeout=60,
        )
        if response.status_code != 429 and response.status_code < 500:
            return response
        if attempt < 2:
            time.sleep(int(response.headers.get("Retry-After", "5")))
    response.raise_for_status()
    return response


def resolve_ontology_id() -> str:
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    matches = []
    while url:
        response = api_request("GET", url)
        response.raise_for_status()
        body = response.json()
        matches.extend(
            item for item in body.get("value", [])
            if item.get("type", "").lower() == "ontology" and item.get("displayName") == ontology_name
        )
        url = body.get("continuationUri")
    in_folder = [item for item in matches if item.get("folderId") == target_folder_id]
    candidates = in_folder if target_folder_id else matches
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one live Ontology named {ontology_name!r} in configured target folder "
            f"{target_folder_id!r}; found {len(candidates)}. "
            "Complete the v2-only ontology setup and disambiguate its folder before retrying."
        )
    return candidates[0]["id"]


def validate_agent_mode(value: str) -> str:
    mode = str(value).strip().lower()
    if mode not in ("auto", "enabled", "disabled"):
        raise ValueError(f"Invalid ontology_operations_agent_mode {value!r}; use auto, enabled, or disabled.")
    return mode


def get_ontology_generation(ontology_id: str) -> int:
    response = api_request(
        "GET", f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/ontologies/{ontology_id}"
    )
    response.raise_for_status()
    generation = (response.json().get("properties") or {}).get("generation")
    if type(generation) is int and generation in (1, 2):
        return generation
    raise RuntimeError(
        f"Unknown live Ontology properties.generation {generation!r}. "
        "Verify the Ontology API response; only generation 2 is supported by this deployment."
    )


def agent_capability_policy(generation: int, mode: str) -> dict:
    mode = validate_agent_mode(mode)
    if type(generation) is not int or generation not in (1, 2):
        raise ValueError(f"Unsupported live ontology generation: {generation!r}")
    if generation != 2:
        return {
            "status": "blocked",
            "reason": "This deployment is v2-only. Replace the generation 1 ontology and agent sources "
                      "through an explicit migration before retrying; no legacy agent writes are allowed.",
        }
    if mode == "disabled":
        return {"status": "skipped", "reason": "Ontology v2 Operations Agent deployment explicitly disabled."}
    return {
        "status": "blocked",
        "reason": "No verified Ontology v2 Operations Agent automation/playbook contract is implemented. "
                  "Fabric known issue 1970 tracks new-experience playbook generation timeout. "
                  "Verify current product support and a v2 playbook manually; this notebook does not "
                  "create, configure, or start an agent.",
    }


def persist_agent_status(status: str, reason: str) -> None:
    from delta.tables import DeltaTable

    values = {"ops_agent_deployment_status": status, "ops_agent_deployment_reason": reason}
    source = spark.createDataFrame(
        [{"setting_name": k, "setting_value": v} for k, v in values.items()]
    ).withColumn("updated_utc", F.current_timestamp())
    (DeltaTable.forName(spark, settings_table_name).alias("target")
     .merge(source.alias("source"), "target.setting_name = source.setting_name")
     .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())


def check_agent_capability() -> dict:
    persist_agent_status("checking", "Checking v2-only Operations Agent capability; no readiness established.")
    try:
        mode = validate_agent_mode(first_setting("ontology_operations_agent_mode", default="auto"))
        ontology_id = resolve_ontology_id()
        generation = get_ontology_generation(ontology_id)
        policy = agent_capability_policy(generation, mode)
    except Exception as exc:
        persist_agent_status("failed", str(exc))
        raise
    persist_agent_status(policy["status"], policy["reason"])
    if generation != 2 or mode == "enabled":
        raise RuntimeError(policy["reason"])
    return {
        "capability": "operations_agent", "generation": generation, "mode": mode, **policy,
        "ops_agent_deployment_status": policy["status"],
        "ops_agent_deployment_reason": policy["reason"],
    }


result = check_agent_capability()
print(json.dumps(result))
notebookutils.notebook.exit(json.dumps(result))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
