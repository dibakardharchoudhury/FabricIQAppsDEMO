import ast
import argparse
import base64
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
