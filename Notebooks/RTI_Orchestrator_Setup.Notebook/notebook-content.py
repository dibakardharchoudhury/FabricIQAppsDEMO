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
# - Runs **NB02–NB06, NB08–NB10** in one Spark session (VNet cold start paid once); independent
#   branches run in parallel per the DAG.
# - **NB01 already ran in Stage 1** and is not in this DAG.
# - **Streaming (NB07) is excluded** — run it on demand from the `Pipe_Stream` pipeline.

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
# lakehouseName has no Python default here because %%configure resolves it before Python runs.
per_notebook_timeout_secs = 3600

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import notebookutils

# NB01 already ran in Stage 1 (created the lakehouse, wrote rti_demo_settings, rebound children).
# The %%configure cell attached that lakehouse to this session, so NB02–NB10 inherit it and their
# relative table reads/writes resolve. useRootDefaultLakehouse rides in each activity's args so a
# child always adopts THIS (root) session's default lakehouse regardless of its own saved pin.
_lh = {"useRootDefaultLakehouse": True}
setup_dag = {
    "activities": [
        {"name": "NB02_eventhouse", "path": "RTI_002_Setup_Eventhouse_Only",                 "dependencies": [],                                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB03_medallion",  "path": "RTI_003_ingest_transform_medallion",            "dependencies": [],                                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB04_ontology",   "path": "RTI_004_build_ontology_mapping_rti_structured", "dependencies": ["NB03_medallion"],                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB05_entitybind", "path": "RTI_005_entity_DataBinding_rti_structured",     "dependencies": ["NB04_ontology"],                    "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB06_tsbind",     "path": "RTI_006_TimeSeriesBinding_RTI_signal",          "dependencies": ["NB04_ontology", "NB02_eventhouse"], "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB08_dashboard",  "path": "RTI_008_build_realtime_dashboard",              "dependencies": ["NB02_eventhouse"],                  "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB09_dataagent",  "path": "RTI_009_build_data_agent",                      "dependencies": ["NB04_ontology"],                    "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
        {"name": "NB10_opsagent",   "path": "RTI_010_build_operations_agent",                "dependencies": ["NB09_dataagent"],                   "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
    ],
    "timeoutInSeconds": 7200,
    "concurrency": 4,
}

results = notebookutils.notebook.runMultiple(setup_dag, {"displayDAGViaGraphviz": True})
print("✅ Setup orchestration complete (NB02–06, 08–10).")
results

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
