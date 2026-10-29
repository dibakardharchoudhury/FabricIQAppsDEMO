import json
import ast
import unittest
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class WeatherSourceContractTests(unittest.TestCase):
    def test_ukmet_is_served_before_optional_aurora(self):
        pipeline = json.loads((ROOT / "Orchestrator_Pipelines/03_Pipe_Weather.DataPipeline/pipeline-content.json").read_text(encoding="utf-8"))
        properties = pipeline["properties"]
        self.assertIs(properties["parameters"]["enable_aurora"]["defaultValue"], False)
        self.assertEqual(len(properties["activities"]), 1)
        activity = properties["activities"][0]
        self.assertEqual(activity["name"], "Weather_Orchestrator")
        self.assertEqual(activity["typeProperties"]["parameters"]["enable_aurora"]["value"]["value"],
                         "@pipeline().parameters.enable_aurora")
        ns = self.orchestrator_functions()
        for aurora in (False, True):
            dag = ns["weather_dag"](aurora, 3600)
            self.assertEqual(dag["concurrency"], 1)
            stages = dag["activities"]
            self.assertEqual(len(stages), 5 if aurora else 3)
            self.assertEqual([stage["path"] for stage in stages[:3]], [
                "Weather_002_fetch_area_weather", "Weather_003_fetch_ukmet", "Weather_020_area_calculations",
            ])
            self.assertIs(stages[0]["args"]["prepare_only"], True)
            for index, stage in enumerate(stages):
                self.assertEqual(stage["dependencies"], [stages[index - 1]["name"]] if index else [])
                self.assertIs(stage["args"]["useRootDefaultLakehouse"], True)
                self.assertEqual(stage["retry"], 0 if stage["name"] == "Ingest_Aurora" else 2)
            if aurora:
                self.assertIs(stages[3]["args"]["prepare_only"], False)
                self.assertEqual(stages[4]["path"], "Weather_020_area_calculations")

    def orchestrator_functions(self):
        source = (ROOT / "Notebooks/Weather_Orchestrator.Notebook/notebook-content.py").read_text(encoding="utf-8")
        nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]
        ns = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "weather-orchestrator", "exec"), ns)
        return ns

    def test_shared_session_orchestrator_rejects_missing_failed_and_skipped_stages(self):
        ns = self.orchestrator_functions()
        dag = ns["weather_dag"](False, 3600)
        results = {stage["name"]: {"exitVal": "", "exception": None} for stage in dag["activities"]}
        ns["require_weather_success"](results, dag)
        for bad in (None, {}, {**results, "extra": {}},
                    {**results, "Ingest_UKMet": {"exception": RuntimeError("HTTP 403")}},
                    {**results, "Publish_UKMet": {"status": "Skipped"}},
                    {**results, "Publish_UKMet": None},
                    {**results, "Publish_UKMet": {}}):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                ns["require_weather_success"](bad, dag)
        for aurora, timeout in (("false", 3600), (False, 0), (False, True)):
            with self.assertRaises(ValueError):
                ns["weather_dag"](aurora, timeout)

    def test_forecast_refresh_uses_one_atomic_merge_with_exact_replacement_scopes(self):
        import pandas as pd

        source = (ROOT / "Notebooks/Weather_003_fetch_ukmet.Notebook/notebook-content.py").read_text(encoding="utf-8")
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "forecast_refresh_scope")
        ns = {"pd": pd}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "forecast-refresh", "exec"), ns)
        issue = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        rows = [{"source_id": "ukmet", "reference_time_utc": issue, "location_id": "F'1"},
                {"source_id": "ukmet", "reference_time_utc": issue - timedelta(hours=6), "location_id": "F2"}]
        predicate = ns["forecast_refresh_scope"](rows + rows)
        self.assertEqual(predicate.count("target.source_id"), 2)
        self.assertIn("target.location_id = 'F''1'", predicate)
        self.assertIn("target.reference_time_utc = TIMESTAMP '2026-10-28T12:00:00Z'", predicate)
        self.assertIn(") OR (", predicate)
        with self.assertRaises(ValueError):
            ns["forecast_refresh_scope"]([])
        self.assertNotIn("DELETE FROM {TABLES['forecasts']}", source)
        self.assertIn("WHEN NOT MATCHED BY SOURCE AND ({refresh_scope}) THEN DELETE", source)

    def test_weather_optimizations_keep_raw_mirrors_and_written_table_diagnostics(self):
        for name in ("Weather_003_fetch_ukmet", "Weather_020_area_calculations", "Weather_Orchestrator"):
            source = (ROOT / "Notebooks" / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
            raw = json.loads((ROOT / "Raw/RTI_Notebooks" / f"{name}.ipynb").read_text(encoding="utf-8"))
            mirrored = "\n".join("".join(cell["source"]) for cell in raw["cells"] if cell["cell_type"] == "code")
            self.assertEqual(ast.dump(ast.parse(source)), ast.dump(ast.parse(mirrored)), name)
        source = (ROOT / "Notebooks/Weather_020_area_calculations.Notebook/notebook-content.py").read_text(encoding="utf-8")
        for variable, table in (("area_metrics", "area_metrics"), ("serving", "latest_forecasts"),
                                ("observed_serving", "latest_observations")):
            written = source.index(f'saveAsTable(TABLES["{table}"])')
            rebound = source.index(f'{variable} = spark.table(TABLES["{table}"])', written)
            self.assertLess(written, rebound)
            self.assertGreater(source.index(f"{variable}.count()", rebound), rebound)

    def test_metadata_preparation_never_reads_aurora_credentials(self):
        source = (ROOT / "Notebooks/Weather_002_fetch_area_weather.Notebook/notebook-content.py").read_text(encoding="utf-8")
        self.assertLess(source.index("persist_station_areas(areas, started_at, selected_facility_ids)"),
                        source.index("if prepare_only:"))
        self.assertLess(source.index("if prepare_only:"), source.index("if not key_vault_uri.strip():"))
        self.assertLess(source.index("if prepare_only:"), source.index("run_id = str(uuid4())"))
        guard = next(node for node in ast.parse(source).body
                     if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "prepare_only")

        class NotebookExit(Exception):
            pass

        class Notebook:
            @staticmethod
            def exit(value):
                raise NotebookExit(value)

        class Utilities:
            notebook = Notebook

        with self.assertRaises(NotebookExit) as result:
            exec(compile(ast.Module(body=[guard], type_ignores=[]), "prepare_only", "exec"),
                 {"prepare_only": True, "notebookutils": Utilities, "json": json, "areas": [1, 2, 3]})
        self.assertEqual(json.loads(str(result.exception)), {
            "status": "prepared", "areas": 3, "aurora": "disabled",
        })

    def test_station_areas_are_prepared_without_a_vendor_response(self):
        source = (ROOT / "Notebooks/Weather_002_fetch_area_weather.Notebook/notebook-content.py").read_text(encoding="utf-8")
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "persist_station_areas")
        queries = []
        rows = []

        class Frame:
            columns = ["location_id"]

            def createOrReplaceTempView(self, name):
                self.name = name

        class Spark:
            @staticmethod
            def table(name):
                return Frame()

            @staticmethod
            def createDataFrame(values):
                rows.extend(values)
                return Frame()

            @staticmethod
            def sql(query):
                queries.append(query)

        context = {"spark": Spark, "json": json, "TABLES": {"areas": "weather_areas"},
                   "geodesic_area_m2": lambda geometry: 123.0}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "station_areas", "exec"), context)
        area = {"area_id": "weather_F001_20km", "area_name": "Station", "location_id": "F001",
                "radius_km": 20.0, "geometry": object(), "geometry_json": "{}"}
        context["persist_station_areas"]([area], datetime(2026, 10, 27), ["F001"])
        self.assertEqual(rows[0]["area_id"], area["area_id"])
        self.assertEqual(rows[0]["location_id"], area["location_id"])
        self.assertEqual(json.loads(rows[0]["metadata_json"])["geodesic_area_m2"], 123.0)
        self.assertEqual(len(queries), 1)
        self.assertIn("MERGE INTO weather_areas", queries[0])
        context["persist_station_areas"]([area], datetime(2026, 10, 27), [])
        self.assertIn("DELETE FROM weather_areas WHERE area_id NOT IN", queries[1])
        Frame.columns = []
        queries.clear()
        context["persist_station_areas"]([area], datetime(2026, 10, 27), ["F001"])
        self.assertEqual(len(queries), 2)
        self.assertIn("ALTER TABLE weather_areas ADD COLUMNS (location_id STRING)", queries[0])
        self.assertIn("MERGE INTO weather_areas", queries[1])

    def test_project_identity_producers_preserve_the_complete_natural_grain(self):
        expected = {
            "Weather_020_area_calculations": [
                ["source_id", "target_kind", "target_id", "reference_time_utc", "valid_time_utc"],
                ["source_id", "location_id", "observed_at_utc"],
            ],
            "RTI_003_ingest_transform_medallion": [["system_id", "signal_type"]],
            "RTI_003_ingest_transform_medallion_SelfContained": [["system_id", "signal_type"]],
        }
        for name, keys in expected.items():
            source = (ROOT / "Notebooks" / (name + ".Notebook") / "notebook-content.py").read_text(encoding="utf-8")
            raw = json.loads((ROOT / "Raw/RTI_Notebooks" / (name + ".ipynb")).read_text(encoding="utf-8"))
            mirror = "\n".join("".join(cell["source"]) for cell in raw["cells"] if cell["cell_type"] == "code")
            self.assertEqual(ast.dump(ast.parse(source)), ast.dump(ast.parse(mirror)), name)
            calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == "withColumn"
                     and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "ontology_key"]
            actual = []
            for call in calls:
                digest = call.args[1]
                self.assertEqual(digest.func.attr, "sha2")
                self.assertEqual(digest.args[1].value, 256)
                encoding = digest.args[0]
                self.assertEqual(ast.literal_eval(encoding.args[1]), {
                    "timeZone": "UTC", "timestampFormat": "yyyy-MM-dd HH:mm:ss.SSSSSS", "ignoreNullFields": "false",
                })
                actual.append([arg.value for arg in encoding.args[0].args])
            self.assertCountEqual(actual, keys, name)

    def test_aurora_discovers_recent_issues_without_valid_time_sorting(self):
        source = (ROOT / "Notebooks/Weather_002_fetch_area_weather.Notebook/notebook-content.py").read_text(encoding="utf-8")
        names = {"newest_stream_issue", "issue_items", "latest_complete_issue"}
        functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in names]
        frozen_now = datetime(2026, 10, 23, 14, tzinfo=timezone.utc)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now

        old = frozen_now.replace(hour=12) - timedelta(hours=48)
        recent = frozen_now.replace(hour=6)
        newest = frozen_now.replace(hour=12)
        items = {}
        for issue, horizon in ((old, 240), (recent, 72), (newest, 5)):
            for lead in range(1, horizon + 1):
                key = f"oper_{issue:%Y%m%d%H%M}_{issue + timedelta(hours=lead):%Y%m%d%H%M}"
                items[key] = {"id": key}

        def search(_session, _endpoint, _key, params):
            self.assertNotIn("sortby", params)
            self.assertLessEqual(len(params["ids"].split(",")), 50)
            return {"features": [items[key] for key in params["ids"].split(",") if key in items]}

        context = {"datetime": Clock, "timedelta": timedelta, "timezone": timezone,
                   "stream": "oper", "collection": "mai-aurora", "stac_search": search}
        code = "from __future__ import annotations\n" + ast.unparse(ast.Module(body=functions, type_ignores=[]))
        exec(compile(code, "aurora_issue_test", "exec"), context)
        issue, selected = context["latest_complete_issue"](None, "unused", "unused", list(range(1, 73)))
        self.assertEqual(issue, recent)
        self.assertEqual(set(selected), set(range(1, 73)))
        self.assertTrue(all(f"oper_{recent:%Y%m%d%H%M}_" in item["id"] for item in selected.values()))
        items.clear()
        for lead in range(1, 241):
            key = f"oper_{old:%Y%m%d%H%M}_{old + timedelta(hours=lead):%Y%m%d%H%M}"
            items[key] = {"id": key}
        with self.assertRaisesRegex(RuntimeError, "No recent"):
            context["latest_complete_issue"](None, "unused", "unused", list(range(1, 73)))

    def test_aurora_raw_mirror_retains_all_canonical_function_bodies(self):
        source = (ROOT / "Notebooks/Weather_002_fetch_area_weather.Notebook/notebook-content.py").read_text(encoding="utf-8")
        raw = json.loads((ROOT / "Raw/RTI_Notebooks/Weather_002_fetch_area_weather.ipynb").read_text(encoding="utf-8"))
        mirror = "\n".join("".join(cell["source"]) for cell in raw["cells"] if cell["cell_type"] == "code")
        functions = lambda text: {node.name: ast.dump(node) for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(functions(source), functions(mirror))

    def test_ukmet_forecast_pressure_is_stored_in_hpa(self):
        source = (ROOT / "Notebooks/Weather_003_fetch_ukmet.Notebook/notebook-content.py").read_text(encoding="utf-8")
        compile(source, "Weather_003", "exec")
        self.assertIn('FORECAST_SCALE = {"pressure": 0.01}', source)
        self.assertIn("value *= FORECAST_SCALE.get(variable_id, 1.0)", source)

    def test_packaged_workflows_require_runtime_two_without_weather_exceptions(self):
        for path in (ROOT / "Raw/RTI_Ontology_Workflow_clean.docx",
                     ROOT / "Raw/RTI_Notebooks/RTI_Ontology_Workflow_clean.docx"):
            with self.subTest(path=str(path)), zipfile.ZipFile(path) as document:
                root = ET.fromstring(document.read("word/document.xml"))
                text = "".join(root.itertext())
                self.assertIn("All Spark notebooks, including every Weather notebook, require Fabric Runtime 2.0", text)
                self.assertIn("no older-runtime exceptions or downgrade fallback", text)
                self.assertIn("Waits for asynchronous Eventstream creation/update operations", text)

    def test_weather_environment_requires_runtime_two_and_python_313_libraries(self):
        environment = ROOT / "Environments/Weather.Environment"
        compute = (environment / "Setting/Sparkcompute.yml").read_text(encoding="utf-8")
        libraries = (environment / "Libraries/PublicLibraries/environment.yml").read_text(encoding="utf-8")
        self.assertIn("runtime_version: '2.0'", compute)
        self.assertNotIn("instance_pool_id:", compute)
        for package in ("numpy=2.4.1", "pandas=2.3.3", "xarray=2025.10.1",
                        "shapely=2.1.2", "pyproj=3.7.2", "adlfs==2025.8.0",
                        "zarr==2.18.7", "numcodecs==0.15.1"):
            self.assertIn(package, libraries)
        self.assertNotIn("numpy=1.", libraries)

    def test_notebooks_inherit_runtime_without_bundled_environment_ids(self):
        notebooks = list((ROOT / "Notebooks").glob("*.Notebook/notebook-content.py"))
        raw_notebooks = list((ROOT / "Raw/RTI_Notebooks").glob("*.ipynb"))
        self.assertTrue(notebooks)
        self.assertTrue(raw_notebooks)
        for path in notebooks:
            header = path.read_text(encoding="utf-8").split("# MARKDOWN", 1)[0]
            lines = [line.removeprefix("# META ") for line in header.splitlines()
                     if line.startswith("# META ")]
            metadata, _ = json.JSONDecoder().raw_decode("\n".join(lines))
            self.assertFalse(metadata.get("dependencies", {}).get("environment"),
                             str(path))
        for path in raw_notebooks:
            metadata = json.loads(path.read_text(encoding="utf-8")).get("metadata", {})
            self.assertFalse(metadata.get("dependencies", {}).get("environment"),
                             str(path))

    def test_scoped_area_run_does_not_execute_global_cleanup(self):
        source = (ROOT / "Notebooks/Weather_002_fetch_area_weather.Notebook/notebook-content.py").read_text(encoding="utf-8")
        compile(source, "Weather_002", "exec")
        self.assertIn("if not selected_facility_ids:\n        active_area_ids", source)

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
        expected = '{"Weather_002_fetch_area_weather", "Weather_003_fetch_ukmet", "Weather_020_area_calculations", "Weather_Orchestrator"}'
        self.assertEqual(source.count(expected), 2)

    def test_weather_query_contract_pages_the_verified_connector_without_legacy_api(self):
        source = (ROOT / "HydroOperationsApp/src/services/fabric.ts").read_text(encoding="utf-8")
        weather_query = source.split("export async function queryWeatherData", 1)[1].split("export async function startStreamingPipeline", 1)[0]
        self.assertEqual(weather_query.count("collectLakehousePages(cursor =>"), 5)
        self.assertEqual(weather_query.count(".first(500).after(cursor).executePaginated()"), 5)
        self.assertIn("const key = lakehouseConnectionKey()", weather_query)
        self.assertIn("if (forceRefresh) weatherDataCache = undefined", weather_query)
        self.assertIn("weatherDataCache = undefined", weather_query.split(".catch(error =>", 1)[1])
        self.assertNotIn("graphqlUrl", weather_query)
        self.assertNotIn("query HydroWeather", source)

    def test_setup_pagination_and_observation_fallbacks(self):
        setup = (ROOT / "Notebooks/RTI_001_create_lakehouse_SelfContained.Notebook/notebook-content.py").read_text(encoding="utf-8")
        orchestrator = (ROOT / "Notebooks/RTI_Orchestrator_Setup.Notebook/notebook-content.py").read_text(encoding="utf-8")
        raw = json.loads((ROOT / "Raw/RTI_Notebooks/RTI_001_create_lakehouse_SelfContained.ipynb").read_text(encoding="utf-8"))
        raw_source = "\n".join("".join(cell.get("source", [])) for cell in raw["cells"])
        raw_orchestrator = json.loads((ROOT / "Raw/RTI_Notebooks/RTI_Orchestrator_Setup.ipynb").read_text(encoding="utf-8"))
        raw_orchestrator_source = "\n".join("".join(cell.get("source", [])) for cell in raw_orchestrator["cells"])
        compile(setup, "RTI_001_create_lakehouse_SelfContained", "exec")
        compile(orchestrator, "RTI_Orchestrator_Setup", "exec")
        for cell_number, cell in enumerate(raw["cells"]):
            if cell.get("cell_type") == "code":
                compile("\n".join(cell.get("source", [])), f"Raw/RTI_001 cell {cell_number}", "exec")
        for cell_number, cell in enumerate(raw_orchestrator["cells"]):
            if cell.get("cell_type") == "code":
                compile("\n".join(cell.get("source", [])), f"Raw/RTI_Orchestrator cell {cell_number}", "exec")
        self.assertNotIn("notebookutils.notebook.run(", setup)
        self.assertNotIn("notebookutils.notebook.run(", raw_source)
        self.assertNotIn("_activate_weather_schedule", setup)
        self.assertNotIn("_activate_weather_schedule", raw_source)
        for source in (orchestrator, raw_orchestrator_source):
            self.assertEqual(source.count('{"useRootDefaultLakehouse": True}'), 1)
            self.assertEqual(source.count('"path": "Weather_001_create_lakehouse"'), 1)
            self.assertIn("quote(body['continuationToken'], safe='')", source)
            self.assertIn("set(results_by_activity) != expected", source)
            self.assertIn('outcome.get("exception")', source)
            self.assertIn(
                '_require_successful_dag(results)\n_activate_weather_schedule()',
                source,
            )
            self.assertGreater(
                source.rindex("_activate_weather_schedule()"),
                source.index("notebookutils.notebook.runMultiple"),
            )
        pipeline = json.loads((ROOT / "Orchestrator_Pipelines/01_Pipe_Setup.DataPipeline/pipeline-content.json").read_text(encoding="utf-8"))
        stage_two = next(activity for activity in pipeline["properties"]["activities"] if activity["name"] == "RTI_Orchestrator_Setup")
        for parameter in (
            "workspace_id",
            "key_vault_uri",
            "key_vault_tenant_id_secret_name",
            "key_vault_client_id_secret_name",
            "key_vault_client_secret_name",
        ):
            self.assertEqual(
                stage_two["typeProperties"]["parameters"][parameter]["value"]["value"],
                f"@pipeline().parameters.{parameter}",
            )
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
        self.assertEqual(pipeline["properties"]["concurrency"], 1)
        self.assertEqual(len(pipeline["properties"]["activities"]), 1)
        self.assertEqual(pipeline["properties"]["activities"][0]["policy"]["retry"], 0)
        rayfin = (ROOT / "HydroOperationsApp/rayfin/rayfin.yml").read_text(encoding="utf-8")
        self.assertNotIn("right-forge-2c0b55c94f", rayfin)


    def test_remaining_review_remediation_contracts(self):
        fabric = (ROOT / "HydroOperationsApp/src/services/fabric.ts").read_text(encoding="utf-8")
        self.assertIn("continuationToken?: string", fabric)
        self.assertIn("encodeURIComponent(page.continuationToken)", fabric)
        weather_sequence = fabric.split("async function runWeatherSequence", 1)[1].split("export const runWeatherNotebooks", 1)[0]
        self.assertIn("resolveWeatherPipelineId()", weather_sequence)
        self.assertIn("runJob(pipelineId, 'Pipeline'", weather_sequence)
        self.assertNotIn("Weather_001_create_lakehouse", weather_sequence)
        self.assertNotIn("setupNotebookId", weather_sequence)
        self.assertNotIn("resolvePostseedNotebookId()", weather_sequence)
        self.assertNotIn("postseedStatus", weather_sequence)
        self.assertNotIn("weatherNotebookNames.entries()", weather_sequence)
        admin_page = (ROOT / "HydroOperationsApp/src/ui-shared/pages/AdministrationPage.tsx").read_text(encoding="utf-8")
        self.assertIn('className="v2-progress"', admin_page)
        self.assertIn('className="v2-progress-bar"', admin_page)
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
        marker = "Required downstream notebook binding failed"
        self.assertEqual(canonical.count(marker), 1)
        self.assertEqual(raw_source.count(marker), 1)
        self.assertEqual(canonical.count("quote(token, safe='')"), 1)
        self.assertEqual(raw_source.count("quote(token, safe='')"), 1)
        self.assertEqual(canonical.count("quote(continuation_token, safe='')"), 1)
        self.assertEqual(raw_source.count("quote(continuation_token, safe='')"), 1)
        self.assertNotIn("continuationToken={continuation_token}", canonical)
        self.assertNotIn("continuationToken={continuation_token}", raw_source)
        self.assertNotIn("_activate_weather_schedule", canonical)
        self.assertNotIn("_activate_weather_schedule", raw_source)
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
