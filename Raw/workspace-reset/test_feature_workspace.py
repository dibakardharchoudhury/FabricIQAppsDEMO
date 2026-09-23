import ast
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import feature_workspace as feature


ROOT = Path(__file__).resolve().parents[2]
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
SUBSCRIPTION = "33333333-3333-4333-8333-333333333333"


def config_dict():
    return {
        "tenant_id": TENANT, "workspace_id": WORKSPACE, "subscription_id": SUBSCRIPTION,
        "resource_group": "rg-hydro-feature", "vault_name": "kv-hydro-feature",
        "location": "norwayeast", "allow_public_api_group": True,
    }


class FeatureWorkspaceTests(unittest.TestCase):
    def test_bootstrap_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(feature.load_feature_config(TENANT, WORKSPACE))

    def test_configuration_pins_the_target_and_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feature.json"
            raw = config_dict()
            path.write_text(json.dumps(raw))
            config = feature.FeatureConfig.load(path, TENANT, WORKSPACE)
            self.assertEqual(config.env_suffix, "V6")
            self.assertEqual(config.state_path, path.with_suffix(".state.json"))
            with self.assertRaisesRegex(feature.FeatureWorkspaceError, "match"):
                feature.FeatureConfig.load(path, TENANT, SUBSCRIPTION)
            for key, value in (
                ("client_secret", "must-not-be-accepted"),
                ("allow_public_api_group", "true"),
                ("enable_energy_map", "true"),
                ("workspace_id", "not-a-guid"),
            ):
                path.write_text(json.dumps({**raw, key: value}))
                with self.subTest(key=key), self.assertRaises(feature.FeatureWorkspaceError):
                    feature.FeatureConfig.load(path, TENANT, WORKSPACE)

    def test_notebook_target_bindings_are_cleared_without_changing_code(self):
        source = (
            '# Fabric notebook source\n\n# METADATA ********************\n\n'
            '# META {\n# META   "kernel_info": {"name": "synapse_pyspark"},\n'
            '# META   "dependencies": {"lakehouse": {"default_lakehouse": "old-id"}}\n'
            '# META }\n\n# CELL ********************\nprint("old-id in code stays")\n'
        )
        cleaned = feature.clean_notebook_dependencies(source)
        self.assertIn('"dependencies": {}', cleaned)
        self.assertIn('"synapse_pyspark"', cleaned)
        self.assertIn('print("old-id in code stays")', cleaned)
        self.assertNotIn("default_lakehouse", cleaned)
        with self.assertRaises(feature.FeatureWorkspaceError):
            feature.clean_notebook_dependencies("print('missing metadata')")

    def test_pipeline_rebinding_handles_nested_notebook_activities(self):
        source = {"properties": {
            "activities": [{"type": "ForEach", "typeProperties": {"activities": [{
                "type": "TridentNotebook", "name": "nested",
                "typeProperties": {"notebookId": "logical-id", "workspaceId": "old-workspace"},
            }]}}],
            "parameters": {"workspace_id": {"type": "string", "defaultValue": "old-workspace"}},
        }}
        result = feature.rebind_pipeline(source, {"logical-id": "target-id"}, WORKSPACE, {"workspace_id": WORKSPACE})
        activity = result["properties"]["activities"][0]["typeProperties"]["activities"][0]
        self.assertEqual(activity["typeProperties"], {"notebookId": "target-id", "workspaceId": WORKSPACE})
        self.assertEqual(result["properties"]["parameters"]["workspace_id"]["defaultValue"], WORKSPACE)
        self.assertEqual(source["properties"]["parameters"]["workspace_id"]["defaultValue"], "old-workspace")
        with self.assertRaisesRegex(feature.FeatureWorkspaceError, "Unresolved"):
            feature.rebind_pipeline(source, {}, WORKSPACE, {})

    def test_local_inventory_has_resolvable_pipeline_references(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json")
            workspace = feature.FeatureWorkspace(config, ROOT)
            self.assertGreater(len(workspace.specs), 20)
            self.assertTrue(all(s["type"] in {"Notebook", "DataPipeline", "Environment"} for s in workspace.specs))

    def test_energy_opt_in_preserves_the_baseline_digest_and_completed_jobs(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            path = Path(directory) / "state.json"
            baseline = feature.FeatureWorkspace(feature.FeatureConfig(**config_dict(), state_path=path), ROOT)
            enabled = feature.FeatureWorkspace(
                feature.FeatureConfig(**config_dict(), state_path=path, enable_energy_map=True), ROOT,
            )
            self.assertEqual(baseline.source_digest, enabled.source_digest)
            self.assertEqual(len(enabled.specs), len(baseline.specs) + 2)
            self.assertFalse(feature.ENERGY_ITEMS & {spec["name"] for spec in baseline.specs})
            self.assertTrue(feature.ENERGY_ITEMS <= {spec["name"] for spec in enabled.specs})
            body = {"executionData": {"parameters": {"workspace_id": WORKSPACE}}}
            request_digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            enabled.state["jobs"][f"setup-id:{baseline.source_digest}:{request_digest}"] = "completed-job-url"
            enabled.fabric.request.return_value = Mock(status_code=200, json=lambda: {"status": "Completed"})
            with patch.object(enabled, "_item", return_value={"id": "setup-id"}):
                enabled._run("01_Pipe_Setup", "DataPipeline", body)
            enabled.fabric.request.assert_called_once_with("GET", "completed-job-url")

    def test_energy_pipeline_is_serial_and_forwards_the_notebook_parameters(self):
        pipeline = json.loads((ROOT / "Orchestrator_Pipelines/04_Pipe_EnergyMap.DataPipeline/pipeline-content.json").read_text())
        notebook = json.loads((ROOT / "Notebooks/Geo_001_ingest_energy_context.Notebook/.platform").read_text())
        properties = pipeline["properties"]
        self.assertEqual(properties["concurrency"], 1)
        self.assertEqual(len(properties["activities"]), 1)
        activity = properties["activities"][0]
        self.assertEqual(activity["typeProperties"]["notebookId"], notebook["config"]["logicalId"])
        self.assertEqual(set(properties["parameters"]), {"workspace_id", "env_suffix", "refresh_mode", "force_refresh"})
        for name in properties["parameters"]:
            self.assertEqual(activity["typeProperties"]["parameters"][name]["value"]["value"], f"@pipeline().parameters.{name}")
        self.assertEqual(properties["parameters"]["force_refresh"]["defaultValue"], False)
        self.assertEqual(properties["parameters"]["refresh_mode"]["defaultValue"], "all")
        rebound = feature.rebind_pipeline(
            pipeline, {notebook["config"]["logicalId"]: "target-notebook"}, WORKSPACE,
            {"workspace_id": WORKSPACE, "env_suffix": "V8"},
        )
        self.assertEqual(rebound["properties"]["activities"][0]["typeProperties"]["workspaceId"], WORKSPACE)
        self.assertEqual(rebound["properties"]["parameters"]["env_suffix"]["defaultValue"], "V8")

    def test_energy_lakehouse_is_separate_owned_and_created_without_schemas(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json", enable_energy_map=True)
            workspace = feature.FeatureWorkspace(config, ROOT)
            lakehouse = {
                "id": SUBSCRIPTION, "type": "Lakehouse", "displayName": "Hydro_GeoContext_V6",
                "description": f"{workspace.marker}: energy context",
            }
            workspace.fabric.list_workspace_items.side_effect = [[], [lakehouse]]
            workspace.fabric.request.return_value = Mock(status_code=201)
            with (
                patch.object(workspace, "_item", return_value={"id": "map-notebook"}),
                patch.object(feature, "notebook_definition", return_value={}),
                patch.object(feature, "bind_notebook_definition", return_value=False) as bind,
                patch.object(workspace, "_run") as run,
                patch.object(workspace, "_publish_energy_read_models") as publish,
            ):
                workspace._prepare_energy_map()
            payload = workspace.fabric.request.call_args.kwargs["json"]
            self.assertNotIn("creationPayload", payload)
            self.assertEqual(payload["displayName"], "Hydro_GeoContext_V6")
            self.assertEqual(bind.call_args.args[1]["default_lakehouse"], SUBSCRIPTION)
            self.assertEqual(run.call_args.args[0], feature.ENERGY_PIPELINE)
            self.assertEqual(run.call_args.args[2]["executionData"]["parameters"]["workspace_id"], WORKSPACE)
            publish.assert_called_once_with(SUBSCRIPTION)

    def test_fabric_errors_report_the_service_code_and_message(self):
        response = Mock(status_code=400, headers={}, json=lambda: {
            "errorCode": "InvalidLakehouseCreationPayload", "message": "Only true is allowed.",
        })
        with self.assertRaisesRegex(feature.FeatureWorkspaceError, "InvalidLakehouseCreationPayload: Only true is allowed"):
            feature.check_response(response, "Create energy context Lakehouse", {201, 202})

    def test_energy_lakehouse_does_not_take_over_an_unowned_item(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json", enable_energy_map=True)
            workspace = feature.FeatureWorkspace(config, ROOT)
            workspace.fabric.list_workspace_items.return_value = [{
                "id": SUBSCRIPTION, "type": "Lakehouse", "displayName": "Hydro_GeoContext_V6",
                "description": "Not owned by the feature deployment",
            }]
            with self.assertRaisesRegex(feature.FeatureWorkspaceError, "unowned"):
                workspace._prepare_energy_map()
            workspace.fabric.request.assert_not_called()

    def test_energy_read_models_use_discovered_table_locations_and_require_all_layers(self):
        names = [
            "transmission", "regional", "distribution", "sea-cables", "masts", "transformers",
            "hydro-plants", "reservoirs", "power-balance", "power-flows", "grid-frequency", "umm",
        ]
        for case in ("ready", "missing", "failed", "duplicate", "empty", "partial-query", "foreign-location"):
            with (
                self.subTest(case=case), tempfile.TemporaryDirectory() as directory,
                patch.object(feature, "FeatureFabric"), patch.object(feature, "kusto_session") as session_factory,
            ):
                post = session_factory.return_value.__enter__.return_value.post
                config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json", enable_energy_map=True)
                workspace = feature.FeatureWorkspace(config, ROOT)
                location = f"abfss://{WORKSPACE}@onelake.dfs.fabric.microsoft.com/{SUBSCRIPTION}/Tables"
                if case == "foreign-location":
                    location = location.replace(WORKSPACE, TENANT)
                tables = [
                    {"name": table, "format": "delta", "location": f"{location}/{table}"}
                    for table in ("geo_map_features", "geo_source_status")
                ]
                workspace.fabric.request.side_effect = [
                    Mock(status_code=200, json=lambda: {"properties": {"queryServiceUri": "https://feature.kusto.fabric.microsoft.com"}}),
                    Mock(status_code=200, json=lambda: {"data": tables[:1], "continuationToken": "next/page"}),
                    Mock(status_code=200, json=lambda: {"data": tables[1:]}),
                ]
                rows = [[name, "ready", 0 if name == "umm" else 2] for name in names]
                if case == "missing":
                    rows.pop()
                elif case == "failed":
                    rows[0][1] = "error"
                elif case == "duplicate":
                    rows[-1] = rows[0]
                elif case == "empty":
                    rows[0][2] = 0
                result = {"Tables": [{"Rows": rows}]}
                if case == "partial-query":
                    result["Exceptions"] = ["partial result failure"]
                post.side_effect = [
                    Mock(status_code=200, json=lambda: {}),
                    Mock(status_code=200, json=lambda: {}),
                    Mock(status_code=200, json=lambda: result),
                ]
                with patch.object(workspace, "_item", return_value={"id": "eventhouse"}):
                    if case == "ready":
                        workspace._publish_energy_read_models(SUBSCRIPTION)
                        self.assertEqual(workspace.state["energy_map_verification"]["umm"], 0)
                        self.assertEqual(len(workspace.state["energy_map_verification"]), 12)
                        for index, table in enumerate(tables):
                            self.assertIn(table["location"] + ";impersonate", post.call_args_list[index].kwargs["json"]["csl"])
                        self.assertIn("continuationToken=next%2Fpage", workspace.fabric.request.call_args.args[1])
                    else:
                        with self.assertRaises(feature.FeatureWorkspaceError):
                            workspace._publish_energy_read_models(SUBSCRIPTION)
                        self.assertNotIn("energy_map_verification", workspace.state)

    def test_kusto_transport_retries_connections_without_weakening_tls_or_retrying_auth(self):
        with feature.kusto_session() as session:
            self.assertTrue(session.verify)
            retry = session.get_adapter("https://feature.kusto.fabric.microsoft.com").max_retries
            self.assertEqual(retry.total, 3)
            self.assertEqual(retry.connect, 3)
            self.assertEqual(retry.read, 0)
            self.assertEqual(retry.allowed_methods, {"POST"})
            self.assertFalse(retry.is_retry("POST", 401))
            self.assertFalse(retry.is_retry("POST", 403))

    def test_import_refuses_unowned_existing_items(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json")
            workspace = feature.FeatureWorkspace(config, ROOT)
            spec = next(s for s in workspace.specs if s["type"] == "Notebook")
            workspace.fabric.list_workspace_items.return_value = [{
                "id": "someone-elses-item", "type": "Notebook", "displayName": spec["name"],
                "description": "Unrelated work",
            }]
            with self.assertRaisesRegex(feature.FeatureWorkspaceError, "unowned"):
                workspace._upsert(spec, "folder", {}, {})
            workspace.fabric.request.assert_not_called()

    def test_import_reuses_unchanged_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json")
            workspace = feature.FeatureWorkspace(config, ROOT)
            spec = next(s for s in workspace.specs if s["type"] == "Notebook")
            item = {"id": "target-id", "type": "Notebook", "displayName": spec["name"],
                    "description": workspace.marker}
            workspace.fabric.list_workspace_items.side_effect = [[], [item], [item]]
            workspace.fabric.request.return_value = Mock(status_code=201)
            self.assertEqual(workspace._upsert(spec, "folder", {}, {}), "target-id")
            self.assertEqual(workspace._upsert(spec, "folder", {}, {}), "target-id")
            self.assertEqual(workspace.fabric.request.call_count, 1)
            payload = workspace.fabric.request.call_args.kwargs["json"]
            self.assertEqual(payload["definition"]["format"], "fabricGitSource")
            source = next(p for p in payload["definition"]["parts"] if p["path"] == "notebook-content.py")
            self.assertIn('"dependencies": {}', base64.b64decode(source["payload"]).decode())

    def test_cross_host_continuations_are_rejected(self):
        with patch.object(feature.Fabric, "__init__", return_value=None):
            client = feature.FeatureFabric(TENANT)
        with self.assertRaisesRegex(feature.FeatureWorkspaceError, "non-Fabric"):
            client.request("GET", "https://example.com/token-leak")

    def test_lro_requires_status_url_and_fails_on_terminal_error(self):
        with patch.object(feature.Fabric, "__init__", return_value=None):
            client = feature.FeatureFabric(TENANT)
        with self.assertRaisesRegex(feature.FeatureWorkspaceError, "status URL"):
            client.poll_lro(Mock(status_code=202, headers={}))
        accepted = Mock(status_code=202, headers={"Location": f"{feature.FABRIC_BASE}/operations/job"})
        failed = Mock(status_code=200, headers={}, json=lambda: {"status": "Failed", "error": {"errorCode": "BadDefinition"}})
        with patch.object(client, "request", return_value=failed), patch.object(feature.time, "sleep"):
            with self.assertRaisesRegex(feature.FeatureWorkspaceError, "BadDefinition"):
                client.poll_lro(accepted)

    def test_lro_uses_canonical_operation_id_instead_of_backend_location(self):
        with patch.object(feature.Fabric, "__init__", return_value=None):
            client = feature.FeatureFabric(TENANT)
        accepted = Mock(status_code=202, headers={
            "Location": "https://backend.example.invalid/operation",
            "x-ms-operation-id": WORKSPACE,
        })
        done = Mock(status_code=200, headers={}, json=lambda: {"status": "Succeeded"})
        with patch.object(client, "request", return_value=done) as request, patch.object(feature.time, "sleep"):
            self.assertIs(client.poll_lro(accepted), done)
        request.assert_called_once_with("GET", f"{feature.FABRIC_BASE}/operations/{WORKSPACE}")

    def test_feature_configuration_does_not_edit_generated_env_local(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json", env_suffix="V8")
            workspace = feature.FeatureWorkspace(config, ROOT)
            env = Path(directory) / ".env"
            env.write_text("RAYFIN_PUBLIC_EVENTHOUSE_NAME=old\n")
            generated = Path(directory) / ".env.local"
            generated.write_text("unchanged")
            workspace.configure_app(env)
            self.assertIn("RTI_Demo_Eventhouse_V8", env.read_text())
            self.assertIn("RAYFIN_PUBLIC_KQL_DASHBOARD_NAME=RTI_Demo_OPCUA_TelemetryStats_V8", env.read_text())
            self.assertEqual(generated.read_text(), "unchanged")

    def test_feature_schedule_flag_is_wired_and_disables_before_api_access(self):
        source = (ROOT / "Notebooks/RTI_Orchestrator_Setup.Notebook/notebook-content.py").read_text()
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_activate_weather_schedule")
        namespace = {"enable_weather_schedule": False}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "schedule", "exec"), namespace)
        namespace["_activate_weather_schedule"]()
        pipeline = json.loads((ROOT / "Orchestrator_Pipelines/01_Pipe_Setup.DataPipeline/pipeline-content.json").read_text())
        stage_two = pipeline["properties"]["activities"][1]
        self.assertEqual(pipeline["properties"]["parameters"]["enable_weather_schedule"]["defaultValue"], True)
        self.assertEqual(stage_two["typeProperties"]["parameters"]["enable_weather_schedule"]["type"], "bool")

    def test_setup_does_not_require_teams_and_mirrors_remain_valid(self):
        for name in ("RTI_001_create_lakehouse_SelfContained", "RTI_010_build_operations_agent",
                     "RTI_011_seed_sql_wire_graphql_agent", "RTI_Orchestrator_Setup"):
            notebook = json.loads((ROOT / f"Raw/RTI_Notebooks/{name}.ipynb").read_text())
            for cell in notebook["cells"]:
                if cell["cell_type"] == "code":
                    code = "".join(cell.get("source", []))
                    if not code.lstrip().startswith("%%"):
                        compile(code, name, "exec")
        source = (ROOT / "Notebooks/RTI_001_create_lakehouse_SelfContained.Notebook/notebook-content.py").read_text()
        required = source.split("_required_injected = {", 1)[1].split("}", 1)[0]
        self.assertNotIn("ops_agent_teams", required)

    def test_strict_seed_fails_instead_of_reporting_partial_success(self):
        source = (ROOT / "Notebooks/RTI_011_seed_sql_wire_graphql_agent.Notebook/notebook-content.py").read_text()
        raw = json.loads((ROOT / "Raw/RTI_Notebooks/RTI_011_seed_sql_wire_graphql_agent.ipynb").read_text())
        mirror = "\n".join("".join(cell.get("source", [])) for cell in raw["cells"] if cell["cell_type"] == "code")
        for text in (source, mirror):
            self.assertIn("strict_setup = False", text)
            self.assertEqual(text.count("    if strict_setup:\n        raise"), 3)
            self.assertIn("Operational seed validation failed", text)

    def test_feature_steps_are_part_of_the_canonical_deployment(self):
        spec = importlib.util.spec_from_file_location("feature_test_deploy", Path(__file__).with_name("deploy_fabric_app.py"))
        deploy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deploy)
        config = Mock()
        workspace = Mock()
        events = []
        workspace.prepare.side_effect = lambda: events.append("prepare")
        workspace.finish.side_effect = lambda: events.append("finish")
        url = "https://feature.webapp.fabricapps.net"
        args = argparse.Namespace(tenant=TENANT, workspace=WORKSPACE, client_id=SUBSCRIPTION, push_config=False)
        with (
            patch.dict(os.environ, {"FABRIC_FEATURE_CONFIG": "/not-read-by-mock.json"}),
            patch.object(feature, "load_feature_config", return_value=config),
            patch.object(feature, "FeatureWorkspace", return_value=workspace),
            patch.object(deploy, "ensure_azure_tenant"),
            patch.object(deploy, "resolve_workspace", return_value=(WORKSPACE, "Feature")),
            patch.object(deploy, "resolve_spa", return_value=SUBSCRIPTION),
            patch.object(deploy, "read_entra_spa_redirects_with_reauth", return_value=[url]),
            patch.object(deploy, "write_rayfin_redirects", side_effect=lambda uris: uris),
            patch.object(deploy, "prepare_rayfin_env", return_value=True),
            patch.object(deploy, "ensure_deploy_dependencies"),
            patch.object(deploy, "ensure_rayfin_login", side_effect=lambda tenant: events.append("login")),
            patch.object(deploy, "npm24", return_value=["validate"]),
            patch.object(deploy, "rayfin24", return_value=["app"]),
            patch.object(deploy, "node24_script", return_value=["auth"]),
            patch.object(deploy, "run_stream", side_effect=lambda argv, **_: events.append(argv[0]) or url),
            patch.object(deploy.requests, "get", return_value=Mock(status_code=200, headers={"Content-Type": "text/html"})),
            patch.object(deploy, "validate_fabric_app"),
            patch.object(deploy, "validate_spa_redirect_preservation"),
            patch.object(deploy, "validate_entra_live_auth_with_reauth"),
        ):
            deploy.deploy(args)
        self.assertEqual(events, ["validate", "login", "prepare", "app", "auth", "finish"])

    def test_private_endpoint_is_ready_before_import_or_setup(self):
        events = []
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json")
            workspace = feature.FeatureWorkspace(config, ROOT)
            workspace.specs = []
            workspace.fabric.request.side_effect = [
                Mock(status_code=200, json=lambda: {"gitConnectionState": "NotConnected"}),
                Mock(status_code=200, json=lambda: {"capacityId": "capacity"}),
                Mock(status_code=200, json=lambda: {"value": [{"id": "capacity", "state": "Active"}]}),
            ]
            prerequisites = {"key_vault_uri": "https://kv-hydro-feature.vault.azure.net/"}
            with (
                patch("feature_prerequisites.ensure_feature_prerequisites", return_value=prerequisites),
                patch.object(feature, "ensure_key_vault_access", side_effect=lambda *args: events.append("private")),
                patch.object(feature, "configure_weather_assets", side_effect=lambda *args, **kwargs: events.append("environment")),
                patch.object(workspace, "_run", side_effect=lambda *args: events.append("setup")),
                patch.object(workspace, "_verify_schedules_disabled"),
                patch.object(workspace, "_item"),
            ):
                workspace.prepare()
            self.assertEqual(events, ["private", "environment", "setup", "environment"])
            self.assertTrue(workspace.state["private_connectivity_ready"])

    def test_private_endpoint_failure_blocks_setup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(feature, "FeatureFabric"):
            config = feature.FeatureConfig(**config_dict(), state_path=Path(directory) / "state.json")
            workspace = feature.FeatureWorkspace(config, ROOT)
            workspace.fabric.request.side_effect = [
                Mock(status_code=200, json=lambda: {"gitConnectionState": "NotConnected"}),
                Mock(status_code=200, json=lambda: {"capacityId": "capacity"}),
                Mock(status_code=200, json=lambda: {"value": [{"id": "capacity", "state": "Active"}]}),
            ]
            with (
                patch("feature_prerequisites.ensure_feature_prerequisites", return_value={"key_vault_uri": "https://kv-hydro-feature.vault.azure.net/"}),
                patch.object(feature, "ensure_key_vault_access", side_effect=feature.PreflightError("approval needed")),
                patch.object(workspace, "_upsert") as upsert,
                patch.object(workspace, "_run") as run,
            ):
                with self.assertRaisesRegex(feature.FeatureWorkspaceError, "approval needed"):
                    workspace.prepare()
            upsert.assert_not_called()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
