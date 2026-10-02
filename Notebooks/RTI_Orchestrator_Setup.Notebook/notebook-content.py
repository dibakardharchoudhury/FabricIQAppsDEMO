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

# # RTI Demo – Setup Orchestrator (Stage 2 of 2)
# 
# **Stage 1** (`RTI_001_create_lakehouse_shortcut`) runs first as its own pipeline activity: it
# creates the lakehouse, writes `rti_demo_settings`, and rebinds every child notebook's default
# lakehouse. **Stage 2 is this notebook**, launched by `Pipe_Setup` only after Stage 1 succeeds.
# 
# The `%%configure` cell below attaches the (now-existing) lakehouse to THIS session, so the single
# `runMultiple` session has a real default lakehouse. Every child inherits it, so their relative
# `spark.read.table(...)` / `saveAsTable(...)` calls resolve — no per-notebook attach, no ABFS rewrite.
# 
# - Runs **NB02–NB06, NB08–NB10** and **Weather_001** in one Spark session (VNet cold start paid once); independent
#   branches run in parallel per the DAG.
# - **NB01 already ran in Stage 1** and is not in this DAG.
# - **Streaming (NB07) is excluded** — run it on demand from the `Pipe_Stream` pipeline.
#
# **Ontology v2 only:** NB04 creates explicit TMDL database/model/default namespace;
# NB04–NB06 require live integer `properties.generation == 2`. Existing v1 is
# rejected, not migrated or deleted. Binding reruns preserve live TMDL/custom parts
# and verify service readback. REST `/v1` and suffixes such as `_V9` are not generations.
#
# Agent notebook completion is not runtime readiness. `enabled` (default) and `auto`
# require actual provisioning; only explicit `disabled` skips. NB09 verifies its v2
# draft/published identity and attempts a delegated MCP ontology-only query. Publication,
# semantic errors returned as text, and inconclusive functional smoke results cannot
# pass the runtime gate. NB10 restores the full playbook, Teams/action and email
# pipeline with definition readback. Both reject v1 and propagate actual API failures.
# NB11 runs separately after app/SQL provisioning; SQL seed and GraphQL setup are
# independent of Data Agent availability. The Knowledge Graph uses native
# ontology-managed graph instances, enriched by KQL and operational SQL.
# Materialize through Manage graph and configure the verified graph binding.
# NB09 and NB10 run sequentially because both MERGE capability status into the
# same Delta settings table; unrelated setup branches remain parallel.

# CELL ********************

# MAGIC %%configure
# MAGIC {
# MAGIC     "defaultLakehouse": {
# MAGIC         "name": {
# MAGIC             "parameterName": "lakehouseName",
# MAGIC             "defaultValue": "Energy_IQ_LakehouseRTI_V5"
# MAGIC         }
# MAGIC     }
# MAGIC }

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# PARAMETERS CELL ********************

# Supplied by the Pipe_Setup pipeline (Stage 2 activity) at runtime:
#   lakehouseName             -> consumed by the %%configure cell above (session default lakehouse).
#   per_notebook_timeout_secs -> max seconds any single child notebook may run before timeout.
#   workspace/key-vault values -> used only to enable the Weather schedule after setup succeeds.
# lakehouseName has no Python default here because %%configure resolves it before Python runs.
per_notebook_timeout_secs = 3600
workspace_id = ""
key_vault_uri = ""
key_vault_tenant_id_secret_name = "tenantid"
key_vault_client_id_secret_name = "clientid"
key_vault_client_secret_name = "clientsecret"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json
import requests
import notebookutils
from urllib.parse import quote

# NB01 already ran in Stage 1 (created the lakehouse, wrote rti_demo_settings, rebound children).
# The %%configure cell attached that lakehouse to this session, so NB02–NB10 inherit it and their
# relative table reads/writes resolve. useRootDefaultLakehouse rides in each activity's args so a
# child always adopts THIS (root) session's default lakehouse regardless of its own saved pin.
_lh = {"useRootDefaultLakehouse": True}
setup_dag = {
    "activities": [
        {"name": "NB02_eventhouse", "path": "RTI_002_Setup_Eventhouse_Only",                 "dependencies": [],                                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB03_medallion",  "path": "RTI_003_ingest_transform_medallion_SelfContained", "dependencies": [],                                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB04_ontology",   "path": "RTI_004_build_ontology_mapping_rti_structured", "dependencies": ["NB03_medallion"],                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB05_entitybind", "path": "RTI_005_entity_DataBinding_rti_structured",     "dependencies": ["NB04_ontology"],                    "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB06_tsbind",     "path": "RTI_006_TimeSeriesBinding_RTI_signal",          "dependencies": ["NB04_ontology", "NB05_entitybind", "NB02_eventhouse"], "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        # NB08 shortcuts the Lakehouse silver tables into the Eventhouse, so it needs NB03 as well as NB02.
        {"name": "NB08_dashboard",  "path": "RTI_008_build_realtime_dashboard",              "dependencies": ["NB02_eventhouse", "NB03_medallion"], "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        # Both agents consume the completed v2 ontology binding directly; neither consumes the
        # other agent. Keep them as independent branches so one product failure does not block
        # the other's deployment evidence. Required failures still fail the overall DAG below.
        {"name": "NB09_dataagent",  "path": "RTI_009_build_data_agent",                      "dependencies": ["NB06_tsbind"],                      "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB10_opsagent",   "path": "RTI_010_build_operations_agent",                "dependencies": ["NB06_tsbind"],                      "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NBW01_weather",   "path": "Weather_001_create_lakehouse",                    "dependencies": [],                                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
    ],
    "timeoutInSeconds": 7200,
    "concurrency": 4,
}

results = notebookutils.notebook.runMultiple(setup_dag, {"displayDAGViaGraphviz": True})


def _require_successful_dag(results_by_activity: dict) -> None:
    expected = {activity["name"] for activity in setup_dag["activities"]}
    if not isinstance(results_by_activity, dict) or set(results_by_activity) != expected:
        returned = set(results_by_activity) if isinstance(results_by_activity, dict) else set()
        raise RuntimeError(f"Stage 2 returned incomplete DAG results: {sorted(expected - returned)}")
    failed = []
    for name, outcome in results_by_activity.items():
        status = str(outcome.get("status", "")).lower() if isinstance(outcome, dict) else "invalid"
        if not isinstance(outcome, dict) or outcome.get("exception") or status in {"failed", "failure", "cancelled", "canceled"}:
            failed.append(name)
    if failed:
        raise RuntimeError(f"Stage 2 DAG failed; Weather schedule remains disabled: {sorted(failed)}")


def _activate_weather_schedule() -> None:
    required = {
        "workspace_id": workspace_id,
        "key_vault_uri": key_vault_uri,
        "key_vault_tenant_id_secret_name": key_vault_tenant_id_secret_name,
        "key_vault_client_id_secret_name": key_vault_client_id_secret_name,
        "key_vault_client_secret_name": key_vault_client_secret_name,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValueError("Missing Stage 2 parameter(s): " + ", ".join(missing))
    tenant_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_tenant_id_secret_name)
    client_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_id_secret_name)
    client_secret = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_secret_name)
    token_response = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://api.fabric.microsoft.com/.default",
        },
    )
    token_response.raise_for_status()
    headers = {
        "Authorization": f"Bearer {token_response.json()['access_token']}",
        "Content-Type": "application/json",
    }
    items = []
    items_url = f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items"
    while items_url:
        items_response = requests.get(items_url, headers=headers)
        items_response.raise_for_status()
        body = items_response.json()
        items.extend(body.get("value", []))
        items_url = body.get("continuationUri")
        if not items_url and body.get("continuationToken"):
            items_url = (
                f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items"
                f"?continuationToken={quote(body['continuationToken'], safe='')}"
            )
    pipelines = [
        item for item in items
        if item.get("displayName") == "03_Pipe_Weather"
        and item.get("type") in {"DataPipeline", "Pipeline"}
    ]
    if len(pipelines) != 1:
        raise RuntimeError(f"Expected one 03_Pipe_Weather pipeline, found {len(pipelines)}")
    schedules_url = (
        f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items/"
        f"{pipelines[0]['id']}/jobs/Pipeline/schedules"
    )
    schedules_response = requests.get(schedules_url, headers=headers)
    schedules_response.raise_for_status()
    schedules = schedules_response.json().get("value", [])
    if len(schedules) != 1 or not schedules[0].get("id"):
        raise RuntimeError(f"Expected one provisioned Weather schedule, found {len(schedules)}")
    schedule = schedules[0]
    if schedule.get("enabled") is True:
        return
    update_response = requests.patch(
        f"{schedules_url}/{schedule['id']}",
        headers=headers,
        json={"enabled": True, "configuration": schedule.get("configuration") or {}},
    )
    update_response.raise_for_status()


def _report_agent_capabilities(results_by_activity: dict) -> None:
    for name in ("NB09_dataagent", "NB10_opsagent"):
        exit_value = results_by_activity[name].get("exitVal")
        if not exit_value:
            raise RuntimeError(f"{name} returned no capability evidence.")
        capability = json.loads(exit_value) if isinstance(exit_value, str) else exit_value
        if not isinstance(capability, dict) or not capability.get("status"):
            raise RuntimeError(f"{name} returned an invalid capability result: {capability!r}")
        expected = "ready" if name == "NB09_dataagent" else "configured"
        mode = capability.get("mode")
        if mode == "disabled":
            valid = capability["status"] == "skipped"
        else:
            valid = mode in ("auto", "enabled") and capability["status"] == expected
            if name == "NB09_dataagent":
                valid = (valid and capability.get("publication_status") == "published"
                         and capability.get("runtime_status") == "verified")
        if not valid or capability.get("generation") != 2:
            raise RuntimeError(f"{name} did not fulfill its required deployment contract: {capability!r}")
        print(f"{name}: {capability['status']} - {capability.get('reason', '')}")


_require_successful_dag(results)
_report_agent_capabilities(results)
_activate_weather_schedule()
print("✅ Setup contracts complete; Weather schedule enabled. Agent runtime actions remain untested.")
results

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
