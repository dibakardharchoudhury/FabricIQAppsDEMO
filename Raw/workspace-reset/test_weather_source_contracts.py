import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class WeatherSourceContractTests(unittest.TestCase):
    def test_scoped_area_run_does_not_execute_global_cleanup(self):
        source = (ROOT / "Notebooks/Weather_002_fetch_area_weather.Notebook/notebook-content.py").read_text(encoding="utf-8")
        compile(source, "Weather_002", "exec")
        self.assertIn("if not selected_facility_ids:\n    active_area_ids", source)

    def test_canonical_metrics_keep_types_until_serving(self):
        source = (ROOT / "Notebooks/Weather_020_area_calculations.Notebook/notebook-content.py").read_text(encoding="utf-8")
        compile(source, "Weather_020", "exec")
        canonical, serving = source.split("# ## Serving projections", 1)
        self.assertIn('area_forecasts = typed_forecasts.join', canonical)
        self.assertNotIn("selected_forecast_types", canonical)
        self.assertIn("selected_forecast_types", serving)
        self.assertIn("serving_area_metrics", serving)

    def test_raw_setup_binds_weather_020_environment_twice(self):
        notebook = json.loads((ROOT / "Raw/RTI_Notebooks/RTI_001_create_lakehouse_SelfContained.ipynb").read_text(encoding="utf-8"))
        source = "\n".join("\n".join(cell.get("source", [])) for cell in notebook["cells"])
        expected = '{"Weather_002_fetch_area_weather", "Weather_003_fetch_ukmet", "Weather_020_area_calculations"}'
        self.assertEqual(source.count(expected), 2)

    def test_weather_query_contract_uses_supported_bound_and_refreshes_discovery(self):
        source = (ROOT / "HydroOperationsApp/src/services/fabric.ts").read_text(encoding="utf-8")
        weather_query = source.split("query HydroWeather", 1)[1].split("const response", 1)[0]
        self.assertEqual(weather_query.count("first: 100000)"), 5)
        self.assertNotIn("first: 1000)", weather_query)
        self.assertIn("ensureConfig(forceRefresh, forceRefresh)", source)

    def test_setup_pagination_and_observation_fallbacks(self):
        setup = (ROOT / "Notebooks/RTI_001_create_lakehouse_SelfContained.Notebook/notebook-content.py").read_text(encoding="utf-8")
        raw = json.loads((ROOT / "Raw/RTI_Notebooks/RTI_001_create_lakehouse_SelfContained.ipynb").read_text(encoding="utf-8"))
        raw_source = "\n".join("".join(cell.get("source", [])) for cell in raw["cells"])
        compile(setup, "RTI_001_create_lakehouse_SelfContained", "exec")
        for cell_number, cell in enumerate(raw["cells"]):
            if cell.get("cell_type") == "code":
                compile("\n".join(cell.get("source", [])), f"Raw/RTI_001 cell {cell_number}", "exec")
        self.assertEqual(setup.count('{"useRootDefaultLakehouse": True}'), 1)
        self.assertEqual(raw_source.count('{"useRootDefaultLakehouse": True}'), 1)
        fallback = 'if not url and body.get("continuationToken")'
        self.assertEqual(setup.count(fallback), 1)
        self.assertEqual(raw_source.count(fallback), 1)
        ukmet = (ROOT / "Notebooks/Weather_003_fetch_ukmet.Notebook/notebook-content.py").read_text(encoding="utf-8")
        self.assertNotIn('raise RuntimeError("UKMet Land Observations returned no mapped recent values")', ukmet)
        self.assertIn("keeping forecast ingestion", ukmet)
        self.assertIn("actual_lead = members[-1][0]", ukmet)
        self.assertIn('"lead_hours": actual_lead', ukmet)
        self.assertNotIn('"lead_hours": bucket_lead', ukmet)
        self.assertIn("has_recent_mapped_observation(payload, observation_cutoff)", ukmet)
        self.assertNotIn("if isinstance(payload, list) and payload:", ukmet)

    def test_pipeline_and_redirect_contracts_remain_intact(self):
        pipeline = json.loads((ROOT / "Orchestrator_Pipelines/03_Pipe_Weather.DataPipeline/pipeline-content.json").read_text(encoding="utf-8"))
        dependencies = [dependency["dependencyConditions"] for activity in pipeline["properties"]["activities"] for dependency in activity.get("dependsOn", [])]
        self.assertTrue(dependencies)
        self.assertTrue(all(conditions == ["Succeeded"] for conditions in dependencies))
        rayfin = (ROOT / "HydroOperationsApp/rayfin/rayfin.yml").read_text(encoding="utf-8")
        self.assertNotIn("right-forge-2c0b55c94f", rayfin)


    def test_remaining_review_remediation_contracts(self):
        fabric = (ROOT / "HydroOperationsApp/src/services/fabric.ts").read_text(encoding="utf-8")
        self.assertIn("continuationToken?: string", fabric)
        self.assertIn("encodeURIComponent(page.continuationToken)", fabric)
        weather_sequence = fabric.split("async function runWeatherSequence", 1)[1].split("export const runWeatherNotebooks", 1)[0]
        self.assertIn("resolveWeatherPipelineId()", weather_sequence)
        self.assertIn("runJob(pipelineId, 'Pipeline'", weather_sequence)
        self.assertNotIn("weatherNotebookNames.entries()", weather_sequence)
        self.assertGreater(weather_sequence.index("resolvePostseedNotebookId()"), weather_sequence.index("runJob(pipelineId, 'Pipeline'"))
        page = (ROOT / "HydroOperationsApp/src/ui-shared/pages/WeatherPage.tsx").read_text(encoding="utf-8")
        self.assertIn("stillAvailable", page)
        self.assertIn("useState(true)", page)
        self.assertIn("VARIABLE_ORDER.filter(variableId => !PRIMARY_VARIABLES.has(variableId))", page)
        aurora = (ROOT / "Notebooks/Weather_002_fetch_area_weather.Notebook/notebook-content.py").read_text(encoding="utf-8")
        self.assertIn("np.all(np.isnan(selected)", aurora)
        self.assertIn("DELETE FROM {TABLES['forecasts']}", aurora)
        ukmet = (ROOT / "Notebooks/Weather_003_fetch_ukmet.Notebook/notebook-content.py").read_text(encoding="utf-8")
        self.assertIn("for source_id, issue_time, location_id", ukmet)
        canonical = (ROOT / "Notebooks/RTI_001_create_lakehouse_SelfContained.Notebook/notebook-content.py").read_text(encoding="utf-8")
        raw = json.loads((ROOT / "Raw/RTI_Notebooks/RTI_001_create_lakehouse_SelfContained.ipynb").read_text(encoding="utf-8"))
        raw_source = "".join("".join(cell.get("source", [])) for cell in raw["cells"])
        marker = "Required weather notebook binding failed"
        self.assertEqual(canonical.count(marker), 1)
        self.assertEqual(raw_source.count(marker), 1)
        self.assertEqual(canonical.count("quote(token, safe='')"), 1)
        self.assertEqual(raw_source.count("quote(token, safe='')"), 1)
        self.assertEqual(canonical.count("quote(continuation_token, safe='')"), 1)
        self.assertEqual(raw_source.count("quote(continuation_token, safe='')"), 1)
        self.assertNotIn("continuationToken={continuation_token}", canonical)
        self.assertNotIn("continuationToken={continuation_token}", raw_source)
        self.assertEqual(canonical.count("_activate_weather_schedule()"), 2)
        self.assertEqual(raw_source.count("_activate_weather_schedule()"), 2)
        graphql = (ROOT / "Notebooks/RTI_011_seed_sql_wire_graphql_agent.Notebook/notebook-content.py").read_text(encoding="utf-8")
        raw_graphql = json.loads((ROOT / "Raw/RTI_Notebooks/RTI_011_seed_sql_wire_graphql_agent.ipynb").read_text(encoding="utf-8"))
        raw_graphql_source = "".join("".join(cell.get("source", [])) for cell in raw_graphql["cells"])
        for object_name in ("weather_sources", "weather_ingestion_runs"):
            self.assertEqual(graphql.count(f'("{object_name}"'), 1)
            self.assertEqual(raw_graphql_source.count(f'("{object_name}"'), 1)
        metrics = (ROOT / "Notebooks/Weather_020_area_calculations.Notebook/notebook-content.py").read_text(encoding="utf-8")
        self.assertIn("latest_issue_utc", metrics)
        self.assertIn('F.col("latest_issue_utc") <', metrics)

if __name__ == "__main__":
    unittest.main()
