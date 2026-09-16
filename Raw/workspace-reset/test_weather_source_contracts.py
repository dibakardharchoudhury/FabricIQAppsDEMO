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
        fallback = 'if not url and body.get("continuationToken")'
        self.assertEqual(setup.count(fallback), 1)
        self.assertEqual(raw_source.count(fallback), 1)
        ukmet = (ROOT / "Notebooks/Weather_003_fetch_ukmet.Notebook/notebook-content.py").read_text(encoding="utf-8")
        self.assertNotIn('raise RuntimeError("UKMet Land Observations returned no mapped recent values")', ukmet)
        self.assertIn("keeping forecast ingestion", ukmet)

    def test_pipeline_and_redirect_contracts_remain_intact(self):
        pipeline = json.loads((ROOT / "Orchestrator_Pipelines/03_Pipe_Weather.DataPipeline/pipeline-content.json").read_text(encoding="utf-8"))
        dependencies = [dependency["dependencyConditions"] for activity in pipeline["properties"]["activities"] for dependency in activity.get("dependsOn", [])]
        self.assertTrue(dependencies)
        self.assertTrue(all(conditions == ["Completed"] for conditions in dependencies))
        rayfin = (ROOT / "HydroOperationsApp/rayfin/rayfin.yml").read_text(encoding="utf-8")
        self.assertNotIn("right-forge-2c0b55c94f", rayfin)


if __name__ == "__main__":
    unittest.main()
