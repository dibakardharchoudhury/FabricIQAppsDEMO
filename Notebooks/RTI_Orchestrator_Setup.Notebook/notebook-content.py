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

# # RTI Demo – Setup Orchestrator
# 
# Runs the full **setup** sequence (NB01–NB06, NB08–NB10) inside **one Spark session** via
# `notebookutils.notebook.runMultiple`, so the ~5 min VNet cold start is paid **once** instead
# of once per notebook. Independent notebooks run in parallel per the dependency DAG.
# 
# - **Streaming (NB07) is excluded** — run it on demand from `Pipe_Stream`.
# - Only **NB01** receives the parameters below; NB02–NB10 read everything from the
#   `rti_demo_settings` table that NB01 writes.
# - Intended to be launched by the **`Pipe_Setup`** Data Pipeline (single activity).

# PARAMETERS CELL ********************

# The ONLY inputs. Pipe_Setup_<suffix> injects these; this notebook forwards them to NB01.
# Everything else derives from env_suffix inside NB01.

env_suffix = "V5"
workspace_id = "19f3d588-1585-4f3b-bb59-5abaf90c193a"

# Azure Key Vault (URI + secret NAMES only — never secret values).
key_vault_uri = "https://akvfabcapnew.vault.azure.net/"
key_vault_tenant_id_secret_name = "tenantid"
key_vault_client_id_secret_name = "clientid"
key_vault_client_secret_name = "clientsecret"

# Seed dataset location + the connection feeding the bronze shortcut.
adls_account_url = "https://didharchadlsg2.dfs.core.windows.net"
adls_subpath = "/dataiq/bronze"
connection_name = "ontologydidharch-connection"

# Operations Agent (Teams) targets.
ops_agent_teams_team_id = "c480320e-9204-474b-9b2c-54a53e94f220"
ops_agent_teams_channel_id = "19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2"
ops_agent_run_as_user = "admin@mngenvmcap218279.onmicrosoft.com"

# Max seconds any single child notebook may run before it is timed out.
per_notebook_timeout_secs = 3600

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import notebookutils

# Forward ONLY to NB01; every downstream notebook reads the rti_demo_settings table NB01 writes.
nb01_args = {
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
    "ops_agent_run_as_user": ops_agent_run_as_user,
}

# Setup DAG — one Spark session for all activities (VNet cold start paid once).
# Edges are DATA dependencies resolved through rti_demo_settings; independent branches run in parallel.
setup_dag = {
    "activities": [
        {"name": "NB01_lakehouse",  "path": "RTI_001_create_lakehouse_shortcut",             "dependencies": [],                                   "args": nb01_args, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB02_eventhouse", "path": "RTI_002_Setup_Eventhouse_Only",                 "dependencies": ["NB01_lakehouse"],                   "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB03_medallion",  "path": "RTI_003_ingest_transform_medallion",            "dependencies": ["NB01_lakehouse"],                   "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB04_ontology",   "path": "RTI_004_build_ontology_mapping_rti_structured", "dependencies": ["NB03_medallion"],                   "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB05_entitybind", "path": "RTI_005_entity_DataBinding_rti_structured",     "dependencies": ["NB04_ontology"],                    "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB06_tsbind",     "path": "RTI_006_TimeSeriesBinding_RTI_signal",          "dependencies": ["NB04_ontology", "NB02_eventhouse"], "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB08_dashboard",  "path": "RTI_008_build_realtime_dashboard",              "dependencies": ["NB02_eventhouse"],                  "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB09_dataagent",  "path": "RTI_009_build_data_agent",                      "dependencies": ["NB04_ontology"],                    "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB10_opsagent",   "path": "RTI_010_build_operations_agent",                "dependencies": ["NB09_dataagent"],                   "timeoutPerCellInSeconds": per_notebook_timeout_secs},
    ],
    "timeoutInSeconds": 7200,
    "concurrency": 4,
}

results = notebookutils.notebook.runMultiple(setup_dag, {"displayDAGViaGraphviz": True})
print("✅ Setup orchestration complete (NB01–06, 08–10).")
results

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
