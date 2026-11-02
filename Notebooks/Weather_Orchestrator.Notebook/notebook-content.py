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

# # Refresh weather in one Spark session
#
# Area preparation, UKMet ingestion and serving calculations share this notebook's
# Spark session instead of paying three separate pipeline session startups.
# Aurora remains opt-in and runs only after the UKMet serving tables are ready.
# Every child inherits the root Lakehouse and Weather Environment.

# PARAMETERS CELL ********************

enable_aurora = False
per_notebook_timeout_secs = 3600

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json
import time
import notebookutils
from notebookutils.mssparkutils.handlers.notebookHandler import RunMultipleFailedException


def weather_dag(aurora, timeout):
    if not isinstance(aurora, bool):
        raise ValueError("enable_aurora must be a boolean")
    if type(timeout) is not int or timeout < 1:
        raise ValueError("per_notebook_timeout_secs must be a positive integer")
    stages = [
        ("Prepare_areas", "Weather_002_fetch_area_weather", {"prepare_only": True}),
        ("Ingest_UKMet", "Weather_003_fetch_ukmet", {}),
        ("Publish_UKMet", "Weather_020_area_calculations", {}),
    ]
    if aurora:
        stages += [
            ("Ingest_Aurora", "Weather_002_fetch_area_weather", {"prepare_only": False}),
            ("Publish_with_Aurora", "Weather_020_area_calculations", {}),
        ]
    return {
        "activities": [
            {
                "name": name, "path": path,
                "dependencies": [stages[index - 1][0]] if index else [],
                "args": {"useRootDefaultLakehouse": True, **args},
                "timeoutPerCellInSeconds": timeout,
                "retry": 0 if name == "Ingest_Aurora" else 2,
                "retryIntervalInSeconds": 60,
            }
            for index, (name, path, args) in enumerate(stages)
        ],
        "concurrency": 1,
        "timeoutInSeconds": timeout * len(stages),
    }


def require_weather_success(results, dag):
    expected = {stage["name"] for stage in dag["activities"]}
    if not isinstance(results, dict) or set(results) != expected:
        raise RuntimeError("Weather refresh returned incomplete stage results.")
    failed = [
        name for name, result in results.items()
        if not isinstance(result, dict) or "exception" not in result or "exitVal" not in result
        or result.get("exception")
        or str(result.get("status", "")).lower() in {"failed", "failure", "cancelled", "canceled", "skipped"}
    ]
    if failed:
        raise RuntimeError(f"Weather refresh failed: {sorted(failed)}")


dag = weather_dag(enable_aurora, per_notebook_timeout_secs)
started = time.perf_counter()
try:
    results = notebookutils.notebook.runMultiple(dag, {"displayDAGViaGraphviz": False})
except RunMultipleFailedException as error:
    failures = [
        f"{name}: {str(result['exception']).splitlines()[0][:500]}"
        for name, result in error.result.items() if result.get("exception")
    ]
    raise RuntimeError("Weather refresh failed: " + "; ".join(failures)) from error
require_weather_success(results, dag)
print("WEATHER_REFRESH_VERIFIED=" + json.dumps({
    "elapsed_seconds": round(time.perf_counter() - started, 3),
    "stages": list(results), "enable_aurora": enable_aurora,
}))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
