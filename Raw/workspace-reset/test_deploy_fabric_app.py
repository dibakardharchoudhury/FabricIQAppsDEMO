import argparse
import base64
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode


MODULE_PATH = Path(__file__).with_name("deploy_fabric_app.py")
SPEC = importlib.util.spec_from_file_location("deploy_fabric_app", MODULE_PATH)
assert SPEC and SPEC.loader
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class CommandEncodingTests(unittest.TestCase):
    def test_capture_child_can_print_unicode_even_with_legacy_parent_encoding(self):
        with patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}):
            output = DEPLOY.run_capture([sys.executable, "-c", "print('\\u2501')"])
        self.assertEqual(output, "\u2501")

    def test_stream_child_preserves_explicit_environment_and_prints_unicode(self):
        env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0", "ENCODING_TEST": "retained"}
        output = DEPLOY.run_stream(
            [sys.executable, "-c", "import os; assert os.environ['ENCODING_TEST'] == 'retained'; print('\\u2501')"],
            env=env,
        )
        self.assertEqual(output, "\u2501")
        self.assertEqual(env["PYTHONIOENCODING"], "cp1252")


class DeploymentTimingTests(unittest.TestCase):
    def test_ordered_durations_and_total_are_machine_readable(self):
        with patch.object(DEPLOY.time, "monotonic", side_effect=[10, 10, 15, 23]), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            timings = DEPLOY.DeploymentTimings()
            timings.stage(1, "Identity")
            timings.stage(2, "SPA")
            timings.finish(True)
        summary = json.loads(output.getvalue().split("DEPLOY_TIMINGS=", 1)[1])
        self.assertEqual(summary["total_seconds"], 13)
        self.assertEqual([step["seconds"] for step in summary["steps"]], [5, 8])
        self.assertEqual([step["status"] for step in summary["steps"]], ["completed", "completed"])
        self.assertEqual(summary["status"], "succeeded")

    def test_failure_is_reported_without_swallowing_the_error_or_retaining_tokens(self):
        def fail(args, timings):
            timings.stage(1, "Identity")
            DEPLOY._FABRIC_TOKEN_CACHE[("tenant", "")] = (9999999999, "private")
            raise DEPLOY.DeployError("permission denied")

        with patch.object(DEPLOY, "_deploy", side_effect=fail), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            with self.assertRaisesRegex(DEPLOY.DeployError, "permission denied"):
                DEPLOY.deploy(argparse.Namespace())
        summary = json.loads(output.getvalue().split("DEPLOY_TIMINGS=", 1)[1])
        self.assertEqual(summary["steps"][-1]["status"], "failed")
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(DEPLOY._FABRIC_TOKEN_CACHE, {})
        self.assertNotIn("private", output.getvalue())


class LakehouseConnectorReceiptTests(unittest.TestCase):
    def test_required_connector_generation_and_apply_must_both_succeed(self):
        receipt = {
            "status": "success",
            "generate": [{"name": "hydrolakehouse", "status": "generated"}],
            "results": [{"name": "hydrolakehouse", "status": "success"}],
        }
        DEPLOY.validate_lakehouse_connector_apply("diagnostic line\n" + json.dumps(receipt))
        for key in ("generate", "results"):
            for invalid in ([], None, ["invalid"], [{"name": "other", "status": "success"}],
                            [{"name": "hydrolakehouse", "status": "skipped"}],
                            [{"name": "hydrolakehouse", "status": "error"}]):
                with self.subTest(key=key, invalid=invalid):
                    with self.assertRaises(DEPLOY.DeployError):
                        DEPLOY.validate_lakehouse_connector_apply(json.dumps({**receipt, key: invalid}))
        for output in ("", "not JSON", "{}", json.dumps({**receipt, "status": "partial"})):
            with self.subTest(output=output), self.assertRaises(DEPLOY.DeployError):
                DEPLOY.validate_lakehouse_connector_apply(output)


def protected_hosting_gate(workspace="workspace-id", item="appbackend-id", tenant="tenant.example"):
    bootstrap = {
        "authorizeBrokerUrl": "https://app.fabric.microsoft.com/secureItemEmbed?" + urlencode({
            "workspaceId": workspace, "itemType": "AppBackend", "itemId": item,
            "extensionPath": "/brokeredauth", "ctid": tenant,
        }),
        "brokerOrigin": "https://app.fabric.microsoft.com",
        "projectId": item,
        "handoffCodeParam": "_hc",
        "codeVerifierHeader": "x-rayfin-sh-code-verifier",
        "monikerHeader": "x-ms-workload-resource-moniker",
        "sessionKey": "authSession",
    }
    return (
        '<!doctype html><html><head><title>Sign in required</title></head>'
        '<body data-state="signin"><span>Microsoft Fabric</span>'
        '<h1>Sign in to continue</h1>'
        '<p>This app is private. Sign in with your Microsoft Fabric account to open it.</p>'
        '<button id="sh-signin" type="button">Sign in</button>'
        '<script type="application/json" id="sh-bootstrap">'
        + json.dumps(bootstrap) + '</script></body></html>'
    )


class FoundryBackendGatewayTests(unittest.TestCase):
    def test_existing_backend_skips_rayfin_but_identifies_the_progress_phase(self):
        with (
            patch.object(DEPLOY, "appbackend_identity_present", return_value=True),
            patch.object(DEPLOY, "ensure_rayfin_login") as login,
            patch.object(DEPLOY, "run_rayfin_deployment") as run,
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            DEPLOY.ensure_appbackend_identity("tenant", "workspace")
        login.assert_not_called()
        run.assert_not_called()
        self.assertIn("Foundry: backend gateway", output.getvalue())
        self.assertIn("Reusing the configured AppBackend identity", output.getvalue())

    def test_new_backend_excludes_static_hosting_and_requires_identity_readback(self):
        for readback in (True, False):
            with (
                self.subTest(readback=readback),
                patch.object(DEPLOY, "appbackend_identity_present", side_effect=[False, readback]),
                patch.object(DEPLOY, "current_rayfin_target", return_value=(
                    {"RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID": "11111111-1111-1111-1111-111111111111"},
                    None,
                )),
                patch.object(DEPLOY, "ensure_rayfin_login") as login,
                patch.object(DEPLOY, "rayfin24", return_value=["backend-command"]) as command,
                patch.object(DEPLOY, "run_rayfin_deployment") as run,
                patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                if readback:
                    DEPLOY.ensure_appbackend_identity("tenant", "workspace")
                else:
                    with self.assertRaisesRegex(DEPLOY.DeployError, "did not record its identity"):
                        DEPLOY.ensure_appbackend_identity("tenant", "workspace")
                login.assert_called_once_with("tenant", "workspace")
                command.assert_called_once_with(
                    "up", "--workspace-id", "workspace", "--exclude-services", "staticHosting", "--yes",
                )
                run.assert_called_once_with(["backend-command"], "tenant")
                self.assertIn("Fabric App item and its SQL/GraphQL dependencies", output.getvalue())
                self.assertIn("without static hosting", output.getvalue())

    def test_foundry_only_returns_before_any_website_publication_for_new_or_existing_target(self):
        for reuse in (True, False):
            with (
                self.subTest(reuse=reuse),
                patch.dict(os.environ, {"FABRIC_FEATURE_CONFIG": ""}),
                patch.object(DEPLOY, "prefer_tenant_scoped_azure_cli_config"),
                patch.object(DEPLOY, "ensure_azure_tenant", return_value="tenant"),
                patch.object(DEPLOY, "resolve_workspace", return_value=("workspace", "Demo", "capacity")),
                patch.object(DEPLOY, "resolve_spa", return_value="11111111-1111-1111-1111-111111111111"),
                patch.object(DEPLOY, "read_entra_spa_redirects_with_reauth", return_value=[]),
                patch.object(DEPLOY, "write_rayfin_redirects"),
                patch.object(DEPLOY, "prepare_rayfin_env", return_value=reuse),
                patch.object(DEPLOY, "current_rayfin_target", return_value=({}, None)),
                patch.object(DEPLOY, "configure_geo_connector"),
                patch.object(DEPLOY, "ensure_deploy_dependencies"),
                patch.object(DEPLOY, "node24_script", return_value=["validate-command"]),
                patch.object(DEPLOY, "run_stream"),
                patch.object(DEPLOY, "provision_foundry_agents") as provision,
                patch.object(DEPLOY, "verify_foundry_agents") as verify,
                patch.object(DEPLOY, "ensure_rayfin_login") as login,
                patch.object(DEPLOY, "run_rayfin_deployment") as publish,
                patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                timings = Mock()
                DEPLOY._deploy(argparse.Namespace(
                    push_config=False, tenant="tenant", workspace="workspace", stage="foundry",
                    client_id=None, energy_map="keep",
                ), timings)
                provision.assert_called_once_with("tenant", "workspace")
                verify.assert_not_called()
                login.assert_not_called()
                publish.assert_not_called()
                self.assertEqual([call.args[0] for call in timings.stage.call_args_list], [1, 2, 3])
                self.assertIn("FOUNDRY_SETUP_SUCCESS", output.getvalue())
                self.assertIn("no website published; agent execution is separate", output.getvalue())


class FoundryOntologyPrerequisiteTests(unittest.TestCase):
    def test_core_energy_finalize_does_not_start_a_second_ingestion(self):
        for publish_only in (True, False):
            with self.subTest(publish_only=publish_only), \
                    patch.object(DEPLOY, "prefer_tenant_scoped_azure_cli_config"), \
                    patch.object(DEPLOY, "ensure_azure_tenant", return_value="tenant"), \
                    patch.object(DEPLOY, "resolve_workspace", return_value=("workspace", "Demo", "capacity")), \
                    patch.object(DEPLOY, "energy_workspace") as energy, \
                    patch.object(DEPLOY, "current_rayfin_target", return_value=({}, None)):
                args = argparse.Namespace(
                    push_config=False, tenant="tenant", workspace="workspace", stage="energy",
                    energy_map="enabled", energy_read_models_only=publish_only,
                )
                DEPLOY._deploy(args, Mock())
                if publish_only:
                    energy.return_value.publish_energy.assert_called_once_with()
                    energy.return_value.prepare_energy.assert_not_called()
                else:
                    energy.return_value.prepare_energy.assert_called_once_with()
                    energy.return_value.publish_energy.assert_not_called()

    def test_missing_ontology_id_stops_before_loading_foundry_provisioning(self):
        for binding in ("", "''", '""'):
            with (
                self.subTest(binding=binding),
                patch.object(DEPLOY, "current_rayfin_target", return_value=(
                    {"RAYFIN_PUBLIC_ONTOLOGY_ID": binding}, None,
                )),
                patch.object(DEPLOY, "ensure_appbackend_identity") as backend,
                patch.object(DEPLOY, "ensure_hosted_source_prerequisites") as sources,
                patch.object(
                    DEPLOY.importlib.util, "spec_from_file_location",
                    side_effect=AssertionError("Foundry provisioning must not load without an ontology ID."),
                ),
            ):
                with self.assertRaisesRegex(DEPLOY.DeployError, "verified selected Ontology item ID is missing"):
                    DEPLOY.provision_foundry_agents("tenant", "workspace")
                backend.assert_not_called()
                sources.assert_not_called()

    def test_ontology_without_graph_continues_to_the_existing_foundry_provisioner(self):
        foundry = Mock()
        with (
            patch.object(DEPLOY, "current_rayfin_target", return_value=(
                {"RAYFIN_PUBLIC_ONTOLOGY_ID": "99999999-9999-9999-9999-999999999999"}, None,
            )),
            patch.object(DEPLOY, "ensure_appbackend_identity") as backend,
            patch.object(DEPLOY, "ensure_hosted_source_prerequisites") as sources,
            patch.object(DEPLOY.importlib.util, "spec_from_file_location", return_value=Mock()),
            patch.object(DEPLOY.importlib.util, "module_from_spec", return_value=foundry),
            patch.dict(sys.modules, {DEPLOY.__name__: DEPLOY}),
        ):
            DEPLOY.provision_foundry_agents("tenant", "workspace")
        backend.assert_called_once_with("tenant", "workspace")
        sources.assert_called_once_with("tenant", "workspace")
        foundry.resolve_project.assert_called_once_with(DEPLOY, "tenant", "workspace")
        foundry.provision.assert_called_once_with(
            DEPLOY, "tenant", "workspace", selection=foundry.resolve_project.return_value,
        )

    def test_ambiguous_project_stops_before_backend_creation(self):
        foundry = Mock()
        foundry.resolve_project.side_effect = DEPLOY.DeployError("Choose one project")
        with (
            patch.object(DEPLOY, "current_rayfin_target", return_value=(
                {"RAYFIN_PUBLIC_ONTOLOGY_ID": "99999999-9999-9999-9999-999999999999"}, None,
            )),
            patch.object(DEPLOY, "ensure_appbackend_identity") as backend,
            patch.object(DEPLOY, "ensure_hosted_source_prerequisites") as sources,
            patch.object(DEPLOY.importlib.util, "spec_from_file_location", return_value=Mock()),
            patch.object(DEPLOY.importlib.util, "module_from_spec", return_value=foundry),
            patch.dict(sys.modules, {DEPLOY.__name__: DEPLOY}),
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Choose one project"):
                DEPLOY.provision_foundry_agents("tenant", "workspace")
        backend.assert_not_called()
        sources.assert_not_called()
        foundry.provision.assert_not_called()


class HostedSourceBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.rayfin = Path(self.directory.name)
        self.env = self.rayfin / ".env"
        self.env.write_text("RAYFIN_PUBLIC_STID_GRAPHQL_ID=\n", encoding="utf-8")
        self.workspace = "11111111-1111-1111-1111-111111111111"
        self.notebook = "22222222-2222-2222-2222-222222222222"
        self.backend = "33333333-3333-3333-3333-333333333333"
        self.graphql = "44444444-4444-4444-4444-444444444444"
        self.legacy = "77777777-7777-7777-7777-777777777777"
        self.operational = "88888888-8888-8888-8888-888888888888"
        self.ontology = "99999999-9999-9999-9999-999999999999"
        self.values = {
            "RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_ID": self.notebook,
            "RAYFIN_PUBLIC_ITEM_ID": self.backend,
            "RAYFIN_PUBLIC_LAKEHOUSE_ID": "66666666-6666-6666-6666-666666666666",
            "RAYFIN_PUBLIC_ONTOLOGY_ID": self.ontology,
        }
        self.active = []
        self.states = ["NotStarted", "InProgress", "Completed"]
        self.location = (f"{DEPLOY.FABRIC_BASE}/workspaces/{self.workspace}/items/"
                         f"{self.notebook}/jobs/instances/55555555-5555-5555-5555-555555555555")
        self.response = Mock(status_code=202, headers={"Location": self.location})
        expected = (DEPLOY.SCRIPT_DIR.parent.parent / "Notebooks" /
                    "RTI_011_seed_sql_wire_graphql_agent.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        notebook = {"cells": [{"cell_type": "code", "source": expected}], "metadata": {
            "dependencies": {"lakehouse": {
                "default_lakehouse": self.values["RAYFIN_PUBLIC_LAKEHOUSE_ID"],
                "default_lakehouse_workspace_id": self.workspace,
            }},
        }}
        self.definition = {"parts": [{"path": "notebook-content.ipynb", "payload":
                           base64.b64encode(json.dumps(notebook).encode()).decode()}]}

        def read(path, headers):
            if path.endswith(f"/items/{self.backend}"):
                return {"id": self.backend, "type": "AppBackend", "displayName": "target-sql"}
            if path.endswith("/items"):
                return {"value": [{"id": self.legacy, "type": "SQLDatabase", "displayName": "target-sql"}]}
            if path.endswith("/jobs/instances"):
                return {"value": self.active}
            return {"status": self.states.pop(0), "failureReason": {"message": "real failure"}}

        self.enterContext(patch.object(DEPLOY, "RAYFIN_DIR", self.rayfin))
        self.enterContext(patch.object(DEPLOY, "current_rayfin_target", return_value=(self.values, None)))
        self.binding = self.enterContext(patch.object(DEPLOY, "bind_operational_store_env"))
        self.headers = self.enterContext(patch.object(DEPLOY, "fabric_headers", return_value={}))
        self.get = self.enterContext(patch.object(DEPLOY, "fabric_get", side_effect=read))
        self.post = self.enterContext(patch.object(DEPLOY.requests, "post", return_value=self.response))
        self.enterContext(patch.object(DEPLOY.time, "sleep"))
        self.discover = self.enterContext(patch.object(
            DEPLOY, "resolve_public_artifact_config",
            return_value={"RAYFIN_PUBLIC_STID_GRAPHQL_ID": self.graphql}))
        self.enterContext(patch("sync_workspace_from_git.Fabric"))
        self.definition_read = self.enterContext(patch(
            "sync_workspace_from_git.notebook_definition", return_value=self.definition))
        self.runtime = self.enterContext(patch("sync_workspace_from_git.ensure_spark_runtime"))
        self.environments = self.enterContext(patch("sync_workspace_from_git.verify_environment_runtimes"))
        self.source_ready = False
        self.publication = self.enterContext(patch.object(
            DEPLOY, "_published_operational_source_ready",
            side_effect=lambda *args, **kwargs: self.source_ready or not self.states))

    def test_missing_published_sql_bootstraps_without_demo_dml_then_verifies_readback(self):
        DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.assertEqual(self.post.call_args.kwargs["json"], {"parameters": [
            {"name": "seed_demo_data", "value": False, "type": "Boolean"},
            {"name": "provision_shared_store", "value": True, "type": "Boolean"},
            {"name": "legacy_sql_db_item_id", "value": self.legacy, "type": "Text"},
            {"name": "project_ontology_item_id", "value": self.ontology, "type": "Text"},
        ]})
        self.runtime.assert_called_once()
        self.environments.assert_called_once()
        self.assertEqual(self.publication.call_count, 2)
        self.discover.assert_not_called()

    def test_existing_published_sql_needs_no_notebook_or_demo_seed(self):
        self.source_ready = True
        DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.post.assert_not_called()
        self.headers.assert_called_once()
        self.definition_read.assert_not_called()

    def test_existing_published_store_has_no_legacy_sql_dependency(self):
        self.source_ready = True
        with patch.object(DEPLOY, "_workspace_artifacts",
                          side_effect=AssertionError("Legacy SQL must not be required after cutover.")):
            DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.binding.assert_called_once_with(self.workspace, {})
        self.post.assert_not_called()
        self.definition_read.assert_not_called()

    def test_native_fabric_source_retains_and_verifies_dependency_metadata(self):
        source = (DEPLOY.SCRIPT_DIR.parent.parent / "Notebooks" /
                  "RTI_011_seed_sql_wire_graphql_agent.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        notebook = json.loads(base64.b64decode(self.definition["parts"][0]["payload"]))
        metadata = re.search(r"(?m)^# METADATA \*+\n\n((?:# META[^\n]*\n)+)", source)
        block = "".join(f"# META {line}\n" for line in json.dumps(notebook["metadata"], indent=2).splitlines())
        source = source[:metadata.start(1)] + block + source[metadata.end(1):]
        self.definition["parts"] = [{"path": "notebook-content.py",
                                    "payload": base64.b64encode(source.encode()).decode()}]
        DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.post.assert_called_once()

    def test_old_notebook_cannot_silently_ignore_the_nonseeding_parameter(self):
        self.definition["parts"][0]["payload"] = base64.b64encode(
            json.dumps({"cells": [{"cell_type": "code", "source": "seed_demo_data = True"}]}).encode()).decode()
        with self.assertRaisesRegex(DEPLOY.DeployError, "Sync the current Git branch"):
            DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.post.assert_not_called()
        self.runtime.assert_not_called()

    def test_active_notebook_is_not_restarted_with_different_seed_semantics(self):
        self.active = [{"status": "InProgress"}]
        with self.assertRaisesRegex(DEPLOY.DeployError, "already active"):
            DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.post.assert_not_called()

    def test_other_workspace_lakehouse_binding_stops_before_execution(self):
        part = self.definition["parts"][0]
        notebook = json.loads(base64.b64decode(part["payload"]))
        notebook["metadata"]["dependencies"]["lakehouse"]["default_lakehouse_workspace_id"] = "other"
        part["payload"] = base64.b64encode(json.dumps(notebook).encode()).decode()
        with self.assertRaisesRegex(DEPLOY.DeployError, "not bound to the selected workspace"):
            DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.post.assert_not_called()
        self.runtime.assert_not_called()

    def test_failed_notebook_propagates_without_rebinding_config(self):
        for state in ("Failed", "Cancelled", "Unexpected"):
            self.states = [state]
            with self.subTest(state=state), self.assertRaisesRegex(DEPLOY.DeployError, "bootstrap failed"):
                DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.discover.assert_not_called()
        self.assertNotIn(self.graphql, self.env.read_text(encoding="utf-8"))

    def test_untrusted_job_location_is_not_followed(self):
        self.response.headers = {"Location": "https://example.invalid/job"}
        with self.assertRaisesRegex(DEPLOY.DeployError, "verified target-workspace job"):
            DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.discover.assert_not_called()

    def test_notebook_wait_is_bounded_and_reports_existing_job(self):
        with patch.object(DEPLOY.time, "monotonic", side_effect=[0, 901]):
            with self.assertRaisesRegex(DEPLOY.DeployError, "exceeded 15 minutes"):
                DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.discover.assert_not_called()

    def test_completion_without_verified_sql_publication_is_not_success(self):
        self.publication.side_effect = None
        self.publication.return_value = False
        with self.assertRaisesRegex(DEPLOY.DeployError, "SQL source publication could not be verified"):
            DEPLOY.ensure_hosted_source_prerequisites("tenant", self.workspace)
        self.assertNotIn(self.graphql, self.env.read_text(encoding="utf-8"))


class DeployOrderTests(unittest.TestCase):
    def setUp(self):
        DEPLOY._FABRIC_TOKEN_CACHE.clear()
        self.addCleanup(DEPLOY._FABRIC_TOKEN_CACHE.clear)
        energy = patch.object(DEPLOY, "energy_workspace")
        energy.start()
        self.addCleanup(energy.stop)

    def test_inactive_capacity_stops_before_configuration_changes(self):
        workspace = "11111111-1111-1111-1111-111111111111"
        capacity = "22222222-2222-2222-2222-222222222222"
        args = argparse.Namespace(tenant="tenant.example", workspace=workspace, client_id=None, push_config=False)
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {"value": [{"id": capacity, "displayName": "Target", "state": "Inactive"}]}
        with (
            patch.object(DEPLOY, "ensure_azure_tenant"),
            patch.object(DEPLOY, "fabric_headers", return_value={}),
            patch.object(DEPLOY, "fabric_get", return_value={"id": workspace, "capacityId": capacity}),
            patch.object(DEPLOY.requests, "get", return_value=response),
            patch.object(DEPLOY, "resolve_spa") as spa,
            patch.object(DEPLOY, "write_rayfin_redirects") as redirects,
            patch.object(DEPLOY, "prepare_rayfin_env") as state,
            patch.object(DEPLOY, "provision_foundry_agents") as agents,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Inactive, not Active"):
                DEPLOY.deploy(args)
        for mutation in (spa, redirects, state, agents):
            mutation.assert_not_called()

    def test_capacity_availability_uses_exact_assignment_and_all_pages(self):
        first = Mock(status_code=200, ok=True)
        first.json.return_value = {"value": [{"id": "other", "state": "Inactive"}], "continuationToken": "next+/="}
        second = Mock(status_code=200, ok=True)
        second.json.return_value = {"value": [{"id": "TARGET", "state": "Active"}]}
        with patch.object(DEPLOY.requests, "get", side_effect=[first, second]) as get:
            DEPLOY.check_capacity_availability("target", {})
        self.assertEqual(get.call_args_list[1].args[0], f"{DEPLOY.FABRIC_BASE}/capacities?continuationToken=next%2B%2F%3D")

    def test_capacity_visibility_does_not_introduce_a_new_permission_requirement(self):
        forbidden = Mock(status_code=403, ok=False)
        hidden = Mock(status_code=200, ok=True)
        hidden.json.return_value = {"value": []}
        for response in (forbidden, hidden):
            with (
                self.subTest(status=response.status_code),
                patch.object(DEPLOY.requests, "get", return_value=response),
                patch("builtins.print") as output,
            ):
                DEPLOY.check_capacity_availability("target", {})
                self.assertIn("Capacity state is unverified", output.call_args.args[0])

    def test_capacity_state_and_real_api_failures_cannot_report_success(self):
        for payload in (
            {"value": [{"id": "target", "state": "Provisioning"}]},
            {"value": [{"id": "target"}]},
            {"value": "invalid"},
            {"value": [], "continuationUri": "https://untrusted.example/"},
            {"value": [], "continuationToken": "repeated"},
            [],
        ):
            response = Mock(status_code=200, ok=True)
            response.json.return_value = payload
            with self.subTest(payload=payload), patch.object(DEPLOY.requests, "get", return_value=response):
                with self.assertRaises(DEPLOY.DeployError):
                    DEPLOY.check_capacity_availability("target", {})
        for status in (401, 429, 500):
            with self.subTest(status=status), patch.object(
                DEPLOY.requests, "get", return_value=Mock(status_code=status, ok=False, text="service failure")
            ):
                with self.assertRaisesRegex(DEPLOY.DeployError, f"HTTP {status}"):
                    DEPLOY.check_capacity_availability("target", {})

    def test_workspace_name_checks_the_resolved_capacity(self):
        capacity = "22222222-2222-2222-2222-222222222222"
        response = Mock(ok=True)
        response.json.return_value = {"value": [{"id": "workspace-id", "displayName": "Target", "capacityId": capacity}]}
        with (
            patch.object(DEPLOY, "fabric_headers", return_value={"header": "value"}),
            patch.object(DEPLOY.requests, "get", return_value=response),
            patch.object(DEPLOY, "check_capacity_availability") as check,
        ):
            self.assertEqual(DEPLOY.resolve_workspace("target", "tenant"), ("workspace-id", "Target", capacity))
        check.assert_called_once_with(capacity, {"header": "value"})

    def test_consent_combines_tenant_grants_with_only_the_current_users_grants(self):
        origin = "https://app.webapp.fabricapps.net"
        app = {
            "spa": {"redirectUris": [origin]},
            "requiredResourceAccess": [{
                "resourceAppId": "resource-app",
                "resourceAccess": [{"id": name, "type": "Scope"} for name in ("Read", "Execute")],
            }],
        }
        resource = {
            "id": "resource-sp",
            "oauth2PermissionScopes": [{"id": name, "value": name} for name in ("Read", "Execute")],
        }
        for principal_id, scope, valid in [
            ("current-user", "Execute", True),
            ("other-user", "Execute", False),
            ("current-user", "Read", False),
        ]:
            with self.subTest(principal_id=principal_id, scope=scope):
                grants = [
                    {"resourceId": "resource-sp", "consentType": "AllPrincipals", "scope": "Read"},
                    {"resourceId": "resource-sp", "consentType": "Principal", "principalId": principal_id, "scope": scope},
                ]
                with (
                    patch.object(DEPLOY, "REQUIRED_DELEGATED", {"resource-app": {"Read", "Execute"}}),
                    patch.object(DEPLOY, "RESOURCE_NAMES", {"resource-app": "Test API"}),
                    patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
                    patch.object(DEPLOY, "run_capture", side_effect=[
                        json.dumps(app), json.dumps(resource), "client-sp", json.dumps(grants),
                        json.dumps({"id": "current-user", "userPrincipalName": "operator@example.test"}),
                    ]),
                ):
                    if valid:
                        DEPLOY.validate_entra_live_auth("client-app", origin)
                    else:
                        with self.assertRaisesRegex(DEPLOY.DeployError, "missing consent for Execute"):
                            DEPLOY.validate_entra_live_auth("client-app", origin)

    def test_frontend_export_runs_the_repository_producer_on_node24(self):
        with (
            patch.object(DEPLOY, "node24_script", return_value=["node24", "export-env.mjs"]) as node_script,
            patch.object(DEPLOY, "run_stream") as run,
        ):
            DEPLOY.export_frontend_env()
        node_script.assert_called_once_with(DEPLOY.APP_DIR / "scripts" / "export-env.mjs")
        run.assert_called_once_with(["node24", "export-env.mjs"], cwd=DEPLOY.APP_DIR)

    def test_frontend_export_failure_prevents_deployment_success(self):
        with (
            patch.object(DEPLOY, "rayfin_environment", return_value={}),
            patch.object(DEPLOY, "run_stream", return_value="hosting result"),
            patch.object(DEPLOY, "export_frontend_env", side_effect=DEPLOY.DeployError("invalid frontend config")),
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "invalid frontend config"):
                DEPLOY.run_rayfin_deployment(["rayfin", "up"], "tenant")

    def test_rayfin_commands_export_validated_configuration_on_success_and_failure(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                events = []

                def command_run(*_args, **_kwargs):
                    events.append("rayfin")
                    if fails:
                        raise DEPLOY.DeployError("deployment failed")
                    return "hosting result"

                with (
                    patch.object(DEPLOY, "rayfin_environment", return_value={}),
                    patch.object(DEPLOY, "run_stream", side_effect=command_run),
                    patch.object(DEPLOY, "export_frontend_env", side_effect=lambda: events.append("export")),
                ):
                    if fails:
                        with self.assertRaisesRegex(DEPLOY.DeployError, "deployment failed"):
                            DEPLOY.run_rayfin_deployment(["rayfin", "up"], "tenant")
                    else:
                        self.assertEqual(DEPLOY.run_rayfin_deployment(["rayfin", "up"], "tenant"), "hosting result")
                self.assertEqual(events, ["rayfin", "export"])

    def test_graph_binding_producer_rejects_malformed_and_over_limit_encodings(self):
        for value in ('{"workspaceId":', '"not JSON"', "[]", "null", '{"bad":NaN}', "x" * (1024 * 1024 + 1)):
            with self.subTest(value=value[:40]), self.assertRaises(DEPLOY.DeployError):
                DEPLOY._public_config_value({"RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": value}, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING")

    def test_repeated_rayfin_escaping_is_repaired_by_the_producer(self):
        binding = {
            "workspaceId": "9c73201e-b2e5-48eb-81b9-3526d320faca",
            "ontologyId": "d0d041aa-13ea-4277-aa6e-1d3ab565a2d4",
            "graphModelId": "87bb9ac2-4599-44b9-8014-b45c696332bd",
            "nodeTypes": {"source#'instrument": "hydro#instrument"},
        }
        value = json.dumps(binding)
        for _ in range(5):
            value = json.dumps(value)[1:-1]
        raw = '"' + value + '"'
        normalized = DEPLOY._public_config_value({"RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": raw}, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING")
        self.assertEqual(json.loads(normalized), binding)
        rendered = DEPLOY._rebind_public_env("", {"RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": normalized})
        self.assertTrue(rendered.startswith("RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING='"))
        self.assertEqual(json.loads(rendered.split("=", 1)[1].strip()[1:-1]), binding)

    def test_rayfin_login_uses_tenant_scoped_azure_cli_token_not_stale_msal_cache(self):
        with (
            patch.dict(os.environ, {"RAYFIN_TOKEN": "old-token", "RAYFIN_TENANT_ID": "old-tenant"}),
            patch.object(DEPLOY, "fabric_headers", return_value={"Authorization": "Bearer scoped-token"}) as headers,
            patch.object(DEPLOY, "rayfin24", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_stream", return_value="Signed in (ambient token via RAYFIN_TOKEN)") as run,
            patch.object(DEPLOY, "fabric_get", return_value={"id": "workspace-id"}) as probe,
        ):
            DEPLOY.ensure_rayfin_login("target-tenant", "workspace-id")
            environment = DEPLOY.rayfin_environment("target-tenant")
            self.assertEqual(os.environ["RAYFIN_TOKEN"], "old-token")
            self.assertEqual(os.environ["RAYFIN_TENANT_ID"], "old-tenant")

        self.assertEqual(headers.call_count, 2)
        headers.assert_called_with("target-tenant")
        run.assert_not_called()
        self.assertEqual(probe.call_args.args[0], "workspaces/workspace-id")
        self.assertEqual(probe.call_args.args[1]["Authorization"].removeprefix("Bearer "), "scoped-token")
        self.assertEqual(environment["RAYFIN_TOKEN"], "scoped-token")
        self.assertEqual(environment["RAYFIN_TENANT_ID"], "target-tenant")

    def test_rayfin_login_rejects_a_mismatched_workspace_or_authentication_failure(self):
        with (
            patch.object(DEPLOY, "fabric_headers", return_value={"Authorization": "Bearer scoped-token"}),
            patch.object(DEPLOY, "rayfin24", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_stream", return_value="Tenant: target-tenant\nToken: expired or unavailable"),
            patch.object(DEPLOY, "fabric_get", return_value={"id": "wrong-workspace"}) as probe,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "did not verify the selected workspace"):
                DEPLOY.ensure_rayfin_login("target-tenant", "workspace-id")
            probe.side_effect = DEPLOY.DeployError("HTTP 401")
            with self.assertRaisesRegex(DEPLOY.DeployError, "HTTP 401"):
                DEPLOY.ensure_rayfin_login("target-tenant", "workspace-id")

    def test_rayfin_environment_rejects_empty_azure_cli_token(self):
        with patch.object(DEPLOY, "fabric_headers", return_value={"Authorization": "Bearer "}):
            with self.assertRaisesRegex(DEPLOY.DeployError, "empty Fabric token"):
                DEPLOY.rayfin_environment("target-tenant")

    def test_spa_name_has_a_stable_configurable_default(self):
        self.assertEqual(DEPLOY.DEFAULT_APP_DISPLAY_NAME, "Hydro Operations Fabric Client")
        self.assertTrue(DEPLOY.APP_DISPLAY_NAME)

    def test_node24_uses_cached_runtime_without_invoking_npx(self):
        cached = Path("C:/npm-cache/_npx/node24/node_modules/node/bin/node.exe")

        with (
            patch.object(DEPLOY, "_NODE24_EXECUTABLE", None),
            patch.object(DEPLOY, "cached_node24_executable", return_value=cached),
            patch.object(DEPLOY, "command_argv") as command,
        ):
            self.assertEqual(DEPLOY.node24_executable(), cached)

        command.assert_not_called()

    def test_reuses_healthy_dependencies_without_npm_ci(self):
        with (
            patch.object(DEPLOY.shutil, "which", return_value="npx"),
            patch.object(DEPLOY, "deploy_dependencies_ready", return_value=True),
            patch.object(DEPLOY, "installed_rayfin_version", return_value="1.33.2"),
            patch.object(DEPLOY, "stop_hydro_node_tooling") as stop_tooling,
            patch.object(DEPLOY, "run_stream") as run_stream,
        ):
            DEPLOY.ensure_deploy_dependencies()

        stop_tooling.assert_not_called()
        run_stream.assert_not_called()

    def test_dependency_tree_requires_current_lockfile_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            (app_dir / "node_modules" / ".bin").mkdir(parents=True)
            for executable in ("vite.cmd", "tsc.cmd"):
                (app_dir / "node_modules" / ".bin" / executable).write_text("", encoding="utf-8")
            (app_dir / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")

            with (
                patch.object(DEPLOY, "APP_DIR", app_dir),
                patch.object(DEPLOY, "DEPENDENCY_STAMP", app_dir / "node_modules" / ".stamp"),
                patch.object(DEPLOY, "installed_rayfin_version", return_value="1.33.2"),
                patch.object(DEPLOY, "run_capture") as run_capture,
            ):
                self.assertFalse(DEPLOY.deploy_dependencies_ready())

            run_capture.assert_not_called()

    def test_npm24_hosts_npm_cli_with_node24(self):
        npm_cli = Path("C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js")
        node = Path("C:/node24/node.exe")

        with (
            patch.object(DEPLOY, "npm_cli_path", return_value=npm_cli),
            patch.object(DEPLOY, "node24_executable", return_value=node),
        ):
            command = DEPLOY.npm24("ci", "--no-audit")

        self.assertEqual(command, [str(node), str(npm_cli), "ci", "--no-audit"])

    def test_stops_only_hydro_node_tooling_before_dependency_restore(self):
        with (
            patch.object(DEPLOY.os, "name", "nt"),
            patch.object(DEPLOY, "command_argv", return_value=["powershell", "script"]) as command,
            patch.object(DEPLOY, "run_capture", return_value="101,202") as run_capture,
        ):
            DEPLOY.stop_hydro_node_tooling()

        command.assert_called_once()
        powershell_script = command.call_args.args[-1]
        self.assertIn(str(DEPLOY.APP_DIR), powershell_script)
        self.assertIn("$_.ProcessId -ne $PID", powershell_script)
        self.assertIn("IndexOf", powershell_script)
        self.assertIn("vite", powershell_script)
        self.assertIn("esbuild.exe", powershell_script)
        self.assertNotIn("Get-Process node", powershell_script)
        run_capture.assert_called_once_with(["powershell", "script"])

    def test_dependency_restore_stops_hydro_tooling_before_npm_ci(self):
        events = []

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(DEPLOY.shutil, "which", return_value="npx"),
                patch.object(DEPLOY, "deploy_dependencies_ready", return_value=False),
                patch.object(DEPLOY, "DEPENDENCY_STAMP", Path(temp_dir) / ".stamp"),
                patch.object(DEPLOY, "stop_hydro_node_tooling", side_effect=lambda: events.append("stop")),
                patch.object(DEPLOY, "npm24", return_value=["npm-ci"]),
                patch.object(DEPLOY, "run_stream", side_effect=lambda _argv, **_kwargs: events.append("npm")),
                patch.object(DEPLOY, "installed_rayfin_version", return_value="1.33.2"),
            ):
                DEPLOY.ensure_deploy_dependencies()

        self.assertEqual(events, ["stop", "npm"])

    def test_dependency_restore_retries_once_after_transient_npm_failure(self):
        events = []

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(DEPLOY.shutil, "which", return_value="npx"),
                patch.object(DEPLOY, "deploy_dependencies_ready", return_value=False),
                patch.object(DEPLOY, "DEPENDENCY_STAMP", Path(temp_dir) / ".stamp"),
                patch.object(DEPLOY, "stop_hydro_node_tooling", side_effect=lambda: events.append("stop")),
                patch.object(DEPLOY, "npm24", return_value=["npm-ci"]),
                patch.object(
                    DEPLOY,
                    "run_stream",
                    side_effect=[DEPLOY.DeployError("ENOTEMPTY"), ""],
                ),
                patch.object(DEPLOY, "installed_rayfin_version", return_value="1.33.2"),
            ):
                DEPLOY.ensure_deploy_dependencies()

        self.assertEqual(events, ["stop", "stop"])

    def test_does_not_write_redirects_when_entra_snapshot_fails(self):
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id="11111111-1111-1111-1111-111111111111",
            push_config=False,
        )

        with (
            patch.object(
                DEPLOY,
                "ensure_azure_tenant",
                return_value="11111111-1111-4111-8111-111111111111",
            ),
            patch.object(
                DEPLOY,
                "resolve_workspace",
                return_value=(
                    "workspace-id",
                    "Demo Workspace",
                    "22222222-2222-2222-2222-222222222222",
                ),
            ),
            patch.object(DEPLOY, "resolve_spa", return_value=args.client_id),
            patch.object(
                DEPLOY,
                "read_entra_spa_redirects_with_reauth",
                side_effect=DEPLOY.DeployError("snapshot still unavailable"),
            ),
            patch.object(DEPLOY, "write_rayfin_redirects") as write_redirects,
        ):
            with self.assertRaisesRegex(
                DEPLOY.DeployError,
                "redirect preservation cannot be guaranteed",
            ):
                DEPLOY.deploy(args)

        write_redirects.assert_not_called()

    def test_reauthenticates_after_stale_token_before_reading_redirects(self):
        stale = DEPLOY.DeployError(
            "Continuous access evaluation resulted in challenge with result: "
            "InteractionRequired and code: TokenCreatedWithOutdatedPolicies"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)),
                patch.object(
                    DEPLOY,
                    "read_entra_spa_redirects",
                    side_effect=[stale, ["https://existing.webapp.fabricapps.net"]],
                ) as read_redirects,
                patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
                patch.object(DEPLOY, "run_stream") as run_stream,
                patch.object(DEPLOY, "ensure_azure_tenant") as ensure_tenant,
            ):
                redirects = DEPLOY.read_entra_spa_redirects_with_reauth("client-id", "tenant-id")

        self.assertEqual(redirects, ["https://existing.webapp.fabricapps.net"])
        self.assertEqual(read_redirects.call_count, 2)
        run_stream.assert_called_once_with(
            [
                "login", "--tenant", "tenant-id", "--allow-no-subscriptions",
                "--only-show-errors", "--output", "none",
            ]
        )
        ensure_tenant.assert_called_once_with("tenant-id")

    def test_reauthenticates_after_stale_token_before_discovering_spa(self):
        stale = DEPLOY.DeployError(
            "Continuous access evaluation resulted in challenge with result: "
            "InteractionRequired and code: TokenCreatedWithOutdatedPolicies"
        )
        client_id = "11111111-1111-1111-1111-111111111111"

        with (
            patch.object(DEPLOY, "existing_spa_candidate", return_value=None),
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=[stale, json.dumps([client_id])]) as run_capture,
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
            patch.object(DEPLOY, "ensure_spa_service_principal") as ensure_service_principal,
        ):
            resolved = DEPLOY.resolve_spa(None, "tenant-id")

        self.assertEqual(resolved, client_id)
        self.assertEqual(run_capture.call_count, 2)
        reauthenticate.assert_called_once_with(
            "tenant-id",
            "discovering the tenant SPA app registration",
        )
        ensure_service_principal.assert_called_once_with(client_id)

    def test_spa_discovery_non_cae_failure_uses_existing_fallback_without_login(self):
        client_id = "11111111-1111-1111-1111-111111111111"
        with (
            patch.object(DEPLOY, "existing_spa_candidate", return_value=client_id),
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(
                DEPLOY,
                "run_capture",
                side_effect=DEPLOY.DeployError("Authorization_RequestDenied"),
            ),
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
            patch.object(DEPLOY, "warn_live_auth") as warn_live_auth,
        ):
            resolved = DEPLOY.resolve_spa(None, "tenant-id")

        self.assertEqual(resolved, client_id)
        reauthenticate.assert_not_called()
        warn_live_auth.assert_called_once()

    def test_spa_discovery_post_reauthentication_failure_does_not_use_fallback(self):
        stale = DEPLOY.DeployError("TokenCreatedWithOutdatedPolicies")
        denied = DEPLOY.DeployError("Authorization_RequestDenied")
        with (
            patch.object(
                DEPLOY,
                "existing_spa_candidate",
                return_value="11111111-1111-1111-1111-111111111111",
            ),
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=[stale, denied]),
            patch.object(DEPLOY, "reauthenticate_azure_cli"),
        ):
            with self.assertRaisesRegex(
                DEPLOY.AzureCliReauthenticationError,
                "Authorization_RequestDenied",
            ):
                DEPLOY.resolve_spa(None, "tenant-id")

    def test_explicit_spa_must_exist_before_deployment(self):
        client_id = "11111111-1111-1111-1111-111111111111"
        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(
                DEPLOY,
                "run_capture",
                side_effect=DEPLOY.DeployError("Application was not found"),
            ),
            patch.object(DEPLOY, "ensure_spa_service_principal") as ensure_sp,
        ):
            with self.assertRaisesRegex(
                DEPLOY.DeployError, "Deployment stopped before changing Rayfin state"
            ):
                DEPLOY.resolve_spa(client_id, "tenant-id")

        ensure_sp.assert_not_called()

    def test_spa_discovery_failed_login_does_not_use_fallback(self):
        stale = DEPLOY.DeployError("TokenCreatedWithOutdatedPolicies")
        with (
            patch.object(
                DEPLOY,
                "existing_spa_candidate",
                return_value="11111111-1111-1111-1111-111111111111",
            ),
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=stale),
            patch.object(
                DEPLOY,
                "reauthenticate_azure_cli",
                side_effect=DEPLOY.DeployError("Clean tenant login failed"),
            ),
        ):
            with self.assertRaisesRegex(
                DEPLOY.AzureCliReauthenticationError,
                "Clean tenant login failed",
            ):
                DEPLOY.resolve_spa(None, "tenant-id")

    def test_git_push_target_uses_matching_feature_upstream(self):
        with (
            patch.object(DEPLOY, "command_argv", side_effect=lambda executable, *args: [executable, *args]),
            patch.object(
                DEPLOY,
                "run_capture",
                side_effect=["feat/dibakar", "origin/feat/dibakar"],
            ),
        ):
            target = DEPLOY.current_git_push_target()

        self.assertEqual(target, ("feat/dibakar", "origin/feat/dibakar"))

    def test_git_push_target_refuses_main(self):
        with (
            patch.object(DEPLOY, "command_argv", side_effect=lambda executable, *args: [executable, *args]),
            patch.object(DEPLOY, "run_capture", return_value="main"),
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "refuses to commit or push the main branch"):
                DEPLOY.current_git_push_target()

    def test_persist_generated_origin_pushes_current_feature_branch(self):
        with (
            patch.object(
                DEPLOY,
                "current_git_push_target",
                return_value=("feat/dibakar", "origin/feat/dibakar"),
            ),
            patch.object(DEPLOY, "command_argv", side_effect=lambda executable, *args: [executable, *args]),
            patch.object(DEPLOY, "run_capture", side_effect=["changed", "0 0"]),
            patch.object(DEPLOY, "run_stream") as run_stream,
        ):
            DEPLOY.persist_generated_origin("Demo Workspace")

        self.assertEqual(run_stream.call_args_list[-1].args[0], ["git", "push", "origin", "feat/dibakar"])
        self.assertEqual(run_stream.call_args_list[-2].args[0], [
            "git", "commit", "-m", "deploy: register Demo Workspace app origin",
            "-m", "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        ])

    def test_fabric_token_missing_from_msal_cache_reauthenticates_once(self):
        missing = DEPLOY.DeployError(
            "ERROR: User 'admin@example.test' does not exist in MSAL token cache. Run 'az login'."
        )

        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=[
                missing, json.dumps({"accessToken": "fabric-token", "expires_on": 4102444800}),
            ]) as run_capture,
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
        ):
            headers = DEPLOY.fabric_headers("tenant-id")

        self.assertEqual(headers, {"Authorization": "Bearer fabric-token"})
        self.assertEqual(run_capture.call_count, 2)
        reauthenticate.assert_called_once_with(
            "tenant-id",
            "accessing the Fabric workspace",
        )

    def test_fabric_token_uses_the_requested_tenant_without_reauthentication(self):
        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", return_value=json.dumps({
                "accessToken": "fabric-token", "expires_on": 4102444800,
            })) as run_capture,
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
        ):
            headers = DEPLOY.fabric_headers("target-tenant")

        self.assertEqual(headers, {"Authorization": "Bearer fabric-token"})
        self.assertEqual(
            run_capture.call_args.args[0],
            [
                "account", "get-access-token", "--tenant", "target-tenant",
                "--resource", "https://api.fabric.microsoft.com",
                "-o", "json",
            ],
        )
        reauthenticate.assert_not_called()

    def test_fabric_token_cache_is_expiry_tenant_and_cli_config_scoped(self):
        with patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)), \
             patch.object(DEPLOY.time, "time", return_value=1000) as clock, \
             patch.object(DEPLOY, "run_capture", return_value=json.dumps({
                 "accessToken": "fabric-token", "expires_on": 2000,
             })) as capture, patch.dict(os.environ, {"AZURE_CONFIG_DIR": "config-a"}):
            DEPLOY.fabric_headers("tenant-a")
            DEPLOY.fabric_headers("TENANT-A")
            self.assertEqual(capture.call_count, 1)
            DEPLOY.fabric_headers("tenant-b")
            with patch.dict(os.environ, {"AZURE_CONFIG_DIR": "config-b"}):
                DEPLOY.fabric_headers("tenant-a")
            self.assertEqual(capture.call_count, 3)
            clock.return_value = 1900
            DEPLOY.fabric_headers("tenant-a")
            self.assertEqual(capture.call_count, 4)

    def test_fabric_token_metadata_fails_closed(self):
        tenant = "11111111-1111-1111-1111-111111111111"
        for metadata in ([], {}, {"accessToken": "token", "expires_on": 0},
                         {"accessToken": "token", "expires_on": 4102444800, "tenant": "wrong"}):
            with self.subTest(metadata=metadata), \
                 patch.object(DEPLOY, "run_capture", return_value=json.dumps(metadata)):
                with self.assertRaises(DEPLOY.DeployError):
                    DEPLOY.fabric_headers(tenant)
        self.assertEqual(DEPLOY._FABRIC_TOKEN_CACHE, {})

    def test_existing_tenant_scoped_cache_is_reused_by_new_process(self):
        shared_config = os.environ.get("AZURE_CONFIG_DIR")
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "tenant-id"
            config_dir.mkdir()
            (config_dir / "azureProfile.json").write_text(
                json.dumps({"subscriptions": [{"tenantId": "tenant-id"}]}), encoding="utf-8-sig",
            )
            with patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)):
                reused = DEPLOY.prefer_tenant_scoped_azure_cli_config("TENANT-ID")

            self.assertTrue(reused)
            self.assertEqual(os.environ.get("AZURE_CONFIG_DIR"), str(config_dir))

        if shared_config is None:
            os.environ.pop("AZURE_CONFIG_DIR", None)
        else:
            os.environ["AZURE_CONFIG_DIR"] = shared_config

    def test_missing_tenant_scoped_cache_preserves_current_configuration(self):
        shared_config = os.environ.get("AZURE_CONFIG_DIR")
        os.environ["AZURE_CONFIG_DIR"] = "existing-config"
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)):
                reused = DEPLOY.prefer_tenant_scoped_azure_cli_config("tenant-id")

        self.assertFalse(reused)
        self.assertEqual(os.environ.get("AZURE_CONFIG_DIR"), "existing-config")
        if shared_config is None:
            os.environ.pop("AZURE_CONFIG_DIR", None)
        else:
            os.environ["AZURE_CONFIG_DIR"] = shared_config

    def test_empty_or_other_tenant_cache_does_not_override_an_existing_identity(self):
        for profile in (None, {}, {"installationId": "bootstrap-only"}, {"subscriptions": []},
                        {"subscriptions": [{"tenantId": "other-tenant"}]}):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temp_dir:
                config_dir = Path(temp_dir) / "tenant-id"
                config_dir.mkdir()
                if profile is not None:
                    (config_dir / "azureProfile.json").write_text(json.dumps(profile), encoding="utf-8")
                with (
                    patch.dict(os.environ, {"AZURE_CONFIG_DIR": "existing-config"}),
                    patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)),
                ):
                    self.assertFalse(DEPLOY.prefer_tenant_scoped_azure_cli_config("tenant-id"))
                    self.assertEqual(os.environ["AZURE_CONFIG_DIR"], "existing-config")

    def test_corrupt_tenant_profile_reports_an_explicit_failure(self):
        for content in ("not-json", "[]", '{"subscriptions": null}', '{"subscriptions": ["invalid"]}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp_dir:
                config_dir = Path(temp_dir) / "tenant-id"
                config_dir.mkdir()
                (config_dir / "azureProfile.json").write_text(content, encoding="utf-8")
                with patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)):
                    with self.assertRaises(DEPLOY.DeployError):
                        DEPLOY.prefer_tenant_scoped_azure_cli_config("tenant-id")

    def test_fresh_reauthentication_preserves_and_replaces_stale_cache(self):
        shared_config = os.environ.get("AZURE_CONFIG_DIR")
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "tenant-id"
            config_dir.mkdir()
            (config_dir / "azureProfile.json").write_text("stale-profile", encoding="utf-8")
            (config_dir / "msal_token_cache.bin").write_text("stale-token", encoding="utf-8")

            def assert_clean_cache(_command):
                self.assertTrue(config_dir.is_dir())
                self.assertFalse((config_dir / "msal_token_cache.bin").exists())

            with (
                patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)),
                patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
                patch.object(DEPLOY, "run_stream", side_effect=assert_clean_cache),
                patch.object(DEPLOY, "ensure_azure_tenant"),
            ):
                DEPLOY.reauthenticate_azure_cli("tenant-id", "testing")

            backups = list(Path(temp_dir).glob("tenant-id.stale-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(
                (backups[0] / "msal_token_cache.bin").read_text(encoding="utf-8"),
                "stale-token",
            )

        if shared_config is None:
            os.environ.pop("AZURE_CONFIG_DIR", None)
        else:
            os.environ["AZURE_CONFIG_DIR"] = shared_config

    def test_cache_security_failure_is_reported_as_reauthentication_failure(self):
        stale = DEPLOY.DeployError("TokenCreatedWithOutdatedPolicies")

        with (
            patch.object(DEPLOY, "secure_private_directory", side_effect=DEPLOY.DeployError("ACL failed")),
            patch.object(DEPLOY, "run_capture", side_effect=stale),
        ):
            with self.assertRaisesRegex(DEPLOY.AzureCliReauthenticationError, "ACL failed"):
                DEPLOY.run_with_azure_cli_reauthentication(
                    "tenant-id",
                    "testing authentication",
                    lambda: DEPLOY.run_capture(["az"]),
                )

    def test_reauthentication_is_tenant_scoped_and_non_deleting(self):
        shared_config = os.environ.get("AZURE_CONFIG_DIR")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)),
                patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
                patch.object(DEPLOY, "run_stream") as run_stream,
                patch.object(DEPLOY, "ensure_azure_tenant") as ensure_tenant,
                patch.object(DEPLOY.Path, "unlink", side_effect=AssertionError("must not delete cache")),
            ):
                DEPLOY.reauthenticate_azure_cli("tenant-id", "testing")

            isolated_config = os.environ.get("AZURE_CONFIG_DIR")
            self.assertEqual(isolated_config, str(Path(temp_dir) / "tenant-id"))
            self.assertNotEqual(isolated_config, shared_config)

        if shared_config is None:
            os.environ.pop("AZURE_CONFIG_DIR", None)
        else:
            os.environ["AZURE_CONFIG_DIR"] = shared_config

        run_stream.assert_called_once_with([
            "login", "--tenant", "tenant-id", "--allow-no-subscriptions",
            "--only-show-errors", "--output", "none",
        ])
        ensure_tenant.assert_called_once_with("tenant-id")

    def test_reauthentication_does_not_login_when_cache_rotation_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "tenant-id"
            config_dir.mkdir()
            with (
                patch.object(DEPLOY, "AZURE_CLI_SESSION_ROOT", Path(temp_dir)),
                patch.object(DEPLOY.Path, "rename", side_effect=OSError("locked")),
                patch.object(DEPLOY, "run_stream") as run_stream,
            ):
                with self.assertRaisesRegex(DEPLOY.DeployError, "No Azure CLI login state was changed"):
                    DEPLOY.reauthenticate_azure_cli("tenant-id", "testing")

        run_stream.assert_not_called()

    def test_reauthentication_stops_after_second_stale_token_challenge(self):
        stale = DEPLOY.DeployError("TokenCreatedWithOutdatedPolicies")
        action = Mock(side_effect=[stale, stale])

        with patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate:
            with self.assertRaisesRegex(
                DEPLOY.DeployError,
                "even after a clean tenant-scoped login",
            ):
                DEPLOY.run_with_azure_cli_reauthentication(
                    "tenant-id",
                    "testing authentication",
                    action,
                )

        self.assertEqual(action.call_count, 2)
        reauthenticate.assert_called_once_with("tenant-id", "testing authentication")

    def test_non_stale_authentication_failure_is_not_retried(self):
        action = Mock(side_effect=DEPLOY.DeployError("Forbidden"))

        with patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate:
            with self.assertRaisesRegex(DEPLOY.DeployError, "Forbidden"):
                DEPLOY.run_with_azure_cli_reauthentication(
                    "tenant-id",
                    "testing authentication",
                    action,
                )

        action.assert_called_once_with()
        reauthenticate.assert_not_called()

    def test_non_stale_failure_after_reauthentication_is_preserved(self):
        action = Mock(
            side_effect=[
                DEPLOY.DeployError("TokenCreatedWithOutdatedPolicies"),
                DEPLOY.DeployError("Authorization_RequestDenied"),
            ]
        )

        with patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate:
            with self.assertRaisesRegex(
                DEPLOY.AzureCliReauthenticationError,
                "Authorization_RequestDenied",
            ):
                DEPLOY.run_with_azure_cli_reauthentication(
                    "tenant-id",
                    "testing authentication",
                    action,
                )

        self.assertEqual(action.call_count, 2)
        reauthenticate.assert_called_once_with("tenant-id", "testing authentication")

    def test_final_redirect_validation_uses_stale_token_recovery(self):
        with patch.object(
            DEPLOY,
            "read_entra_spa_redirects_with_reauth",
            return_value=["https://app.example"],
        ) as read_redirects:
            DEPLOY.validate_spa_redirect_preservation(
                "client-id",
                ["https://app.example"],
                "tenant-id",
            )

        read_redirects.assert_called_once_with("client-id", "tenant-id")

    def test_wrong_active_tenant_is_rejected_without_login(self):
        target = "11111111-1111-4111-8111-111111111111"
        account = {
            "tenantId": "22222222-2222-4222-8222-222222222222",
            "user": {"name": "user@example.test"},
        }
        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", return_value=json.dumps(account)),
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, f"not {target}"):
                DEPLOY.ensure_azure_tenant(target)

        reauthenticate.assert_not_called()

    def test_tenant_domains_resolve_to_authoritative_guids_before_target_configuration(self):
        tenant = "11111111-2222-3333-4444-555555555555"
        with patch.object(DEPLOY.requests, "get") as get:
            self.assertEqual(DEPLOY.resolve_tenant_id(tenant), tenant)
            get.assert_not_called()
            get.return_value.json.return_value = {"issuer": f"https://login.microsoftonline.com/{tenant}/v2.0"}
            self.assertEqual(DEPLOY.resolve_tenant_id("example.onmicrosoft.com"), tenant)
            get.assert_called_once_with(
                "https://login.microsoftonline.com/example.onmicrosoft.com/v2.0/.well-known/openid-configuration",
                timeout=30, allow_redirects=False,
            )
            for issuer in ("https://untrusted.example/" + tenant, "https://login.microsoftonline.com/common/v2.0", ""):
                get.return_value.json.return_value = {"issuer": issuer}
                with self.assertRaisesRegex(DEPLOY.DeployError, "authoritative Entra"):
                    DEPLOY.resolve_tenant_id("example.onmicrosoft.com")

    def test_deploy_starts_with_the_users_current_azure_cli_session(self):
        args = argparse.Namespace(
            tenant="tenant-id",
            workspace="workspace-id",
            client_id=None,
            push_config=False,
        )

        with (
            patch.object(DEPLOY, "ensure_azure_tenant", side_effect=DEPLOY.DeployError("stop")),
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "stop"):
                DEPLOY.deploy(args)

        reauthenticate.assert_not_called()

    def test_fabric_token_authorization_error_does_not_trigger_login(self):
        denied = DEPLOY.DeployError("Authorization_RequestDenied")

        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=denied),
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Authorization_RequestDenied"):
                DEPLOY.fabric_headers("tenant-id")

        reauthenticate.assert_not_called()

    def test_does_not_reauthenticate_for_non_cae_redirect_failure(self):
        failure = DEPLOY.DeployError("Authorization_RequestDenied")

        with (
            patch.object(DEPLOY, "read_entra_spa_redirects", side_effect=failure),
            patch.object(DEPLOY, "run_stream") as run_stream,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Authorization_RequestDenied"):
                DEPLOY.read_entra_spa_redirects_with_reauth("client-id", "tenant-id")

        run_stream.assert_not_called()

    def test_missing_spa_stops_before_rayfin_state_is_touched(self):
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id=None,
            push_config=False,
        )
        prepare = Mock()

        with (
            patch.object(
                DEPLOY,
                "ensure_azure_tenant",
                return_value="11111111-1111-4111-8111-111111111111",
            ),
            patch.object(
                DEPLOY,
                "resolve_workspace",
                return_value=(
                    "workspace-id",
                    "Demo Workspace",
                    "22222222-2222-2222-2222-222222222222",
                ),
            ),
            patch.object(DEPLOY, "resolve_spa", return_value=None),
            patch.object(DEPLOY, "write_rayfin_redirects") as write_redirects,
            patch.object(DEPLOY, "prepare_rayfin_env", prepare),
            patch.object(DEPLOY, "ensure_deploy_dependencies") as ensure_dependencies,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "usable Entra SPA"):
                DEPLOY.deploy(args)

        write_redirects.assert_not_called()
        prepare.assert_not_called()
        ensure_dependencies.assert_not_called()

    @patch.object(DEPLOY, "current_rayfin_target", return_value=({}, {}))
    @patch.object(DEPLOY, "configure_geo_connector")
    def test_reuses_existing_backend_for_static_deploy(self, configure_geo, current_target):
        client_id = "11111111-1111-1111-1111-111111111111"
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id=client_id,
            push_config=False,
            stage="app",
        )

        with (
            patch.object(
                DEPLOY,
                "ensure_azure_tenant",
                return_value="11111111-1111-4111-8111-111111111111",
            ),
            patch.object(
                DEPLOY,
                "resolve_workspace",
                return_value=(
                    "workspace-id",
                    "Demo Workspace",
                    "22222222-2222-2222-2222-222222222222",
                ),
            ),
            patch.object(DEPLOY, "resolve_spa", return_value=client_id),
            patch.object(DEPLOY, "read_entra_spa_redirects_with_reauth", return_value=[]),
            patch.object(DEPLOY, "write_rayfin_redirects", return_value=["http://localhost:5173"]),
            patch.object(DEPLOY, "prepare_rayfin_env", return_value=True),
            patch.object(DEPLOY, "ensure_deploy_dependencies"),
            patch.object(DEPLOY, "ensure_rayfin_login"),
            patch.object(DEPLOY, "export_frontend_env"),
            patch.object(DEPLOY, "rayfin_environment", side_effect=[{"RAYFIN_TOKEN": "first"}, {"RAYFIN_TOKEN": "refreshed"}, {"RAYFIN_TOKEN": "connector"}, {"RAYFIN_TOKEN": "operational"}]),
            patch.object(DEPLOY, "provision_foundry_agents") as provision_agents,
            patch.object(DEPLOY, "verify_foundry_agents") as verify_agents,
            patch.object(
                DEPLOY,
                "rayfin24",
                side_effect=lambda *arguments: list(arguments),
            ),
            patch.object(
                DEPLOY,
                "run_stream",
                side_effect=[
                    "environment validated",
                    "Hosting URL: https://fast.webapp.fabricapps.net",
                    "backend updated",
                    json.dumps({"status": "success", "generate": [{"name": "hydrolakehouse", "status": "generated"}],
                                "results": [{"name": "hydrolakehouse", "status": "success"}]}),
                    json.dumps({"status": "success", "generate": [{"name": "hydrooperations", "status": "generated"}],
                                "results": [{"name": "hydrooperations", "status": "success"}]}),
                    "live auth configured",
                ],
            ) as run_stream,
            patch.object(DEPLOY, "validate_hosted_page", return_value="app-shell"),
            patch.object(DEPLOY, "validate_fabric_app"),
            patch.object(DEPLOY, "validate_rayfin_endpoint_contract", return_value="https://api.test"),
            patch.object(DEPLOY, "validate_rayfin_publishable_key", return_value="pk-test"),
            patch.object(DEPLOY, "validate_appbackend_cors"),
            patch.object(DEPLOY, "validate_spa_redirect_preservation"),
            patch.object(DEPLOY, "validate_entra_live_auth"),
        ):
            DEPLOY.deploy(args)

        self.assertIn("validate-env.mjs", " ".join(run_stream.call_args_list[0].args[0]))
        self.assertEqual(run_stream.call_args_list[1].args[0], ["up", "staticapp", "deploy"])
        self.assertEqual(run_stream.call_args_list[1].kwargs["env"]["RAYFIN_TOKEN"], "first")
        self.assertEqual(run_stream.call_args_list[2].kwargs["env"]["RAYFIN_TOKEN"], "refreshed")
        self.assertNotIn("RAYFIN_TOKEN", run_stream.call_args_list[5].kwargs["env"])
        provision_agents.assert_not_called()
        verify_agents.assert_called_once_with("11111111-1111-4111-8111-111111111111", "workspace-id")

    @patch.object(DEPLOY, "current_rayfin_target", return_value=({}, {}))
    @patch.object(DEPLOY, "configure_geo_connector")
    @patch.object(DEPLOY, "provision_foundry_agents")
    def test_existing_registered_origin_reapplies_backend_configuration(self, provision_agents, configure_geo, current_target):
        hosting_url = "https://fast.webapp.fabricapps.net"
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id="11111111-1111-1111-1111-111111111111",
            push_config=False,
        )

        with (
            patch.object(
                DEPLOY,
                "ensure_azure_tenant",
                return_value="11111111-1111-4111-8111-111111111111",
            ),
            patch.object(
                DEPLOY,
                "resolve_workspace",
                return_value=(
                    "workspace-id",
                    "Demo Workspace",
                    "22222222-2222-2222-2222-222222222222",
                ),
            ),
            patch.object(DEPLOY, "resolve_spa", return_value=args.client_id),
            patch.object(DEPLOY, "read_entra_spa_redirects_with_reauth", return_value=[hosting_url]),
            patch.object(DEPLOY, "write_rayfin_redirects", side_effect=lambda redirects: redirects),
            patch.object(DEPLOY, "prepare_rayfin_env", return_value=True),
            patch.object(DEPLOY, "ensure_deploy_dependencies"),
            patch.object(DEPLOY, "ensure_rayfin_login"),
            patch.object(DEPLOY, "export_frontend_env"),
            patch.object(DEPLOY, "rayfin_environment", return_value={}),
            patch.object(DEPLOY, "rayfin24", side_effect=lambda *arguments: list(arguments)),
            patch.object(DEPLOY, "node24_script", side_effect=lambda script, *args: [script.stem]),
            patch.object(DEPLOY, "run_stream", side_effect=lambda command, **kwargs:
                         json.dumps({"status": "success", "generate": [{"name": command[4], "status": "generated"}],
                                     "results": [{"name": command[4], "status": "success"}]})
                         if command[:3] == ["up", "connector", "apply"] else f"Hosting URL: {hosting_url}") as run_stream,
            patch.object(
                DEPLOY.requests,
                "get",
                side_effect=lambda url, **kwargs: Mock(
                    status_code=200, headers={"Content-Type": "text/html; charset=utf-8"},
                    text=protected_hosting_gate(),
                ) if kwargs.get("headers", {}).get("Accept") == "text/html" else Mock(
                    status_code=401, headers={"Content-Type": "application/json"}, text='{"error":"Unauthorized"}',
                ),
            ) as hosting_get,
            patch.object(DEPLOY, "validate_fabric_app", return_value="appbackend-id"),
            patch.object(DEPLOY, "validate_rayfin_endpoint_contract", return_value="https://api.test"),
            patch.object(DEPLOY, "validate_rayfin_publishable_key", return_value="pk-test"),
            patch.object(DEPLOY, "validate_appbackend_cors"),
            patch.object(DEPLOY, "validate_spa_redirect_preservation"),
            patch.object(DEPLOY, "validate_entra_live_auth"),
            patch("builtins.print") as printed,
        ):
            DEPLOY.deploy(args)

        self.assertEqual(run_stream.call_count, 6)
        provision_agents.assert_called_once_with("11111111-1111-4111-8111-111111111111", "workspace-id")
        self.assertEqual(run_stream.call_args_list[0].args[0], ["validate-env"])
        self.assertEqual(run_stream.call_args_list[1].args[0], ["up", "staticapp", "deploy"])
        self.assertEqual(
            run_stream.call_args_list[2].args[0],
            ["up", "--workspace-id", "workspace-id", "--exclude-services", "staticHosting", "--yes"],
        )
        self.assertEqual(run_stream.call_args_list[3].args[0], ["up", "connector", "apply", "--name", "hydrolakehouse", "--json"])
        self.assertEqual(run_stream.call_args_list[4].args[0], ["up", "connector", "apply", "--name", "hydrooperations", "--json"])
        self.assertEqual(run_stream.call_args_list[5].args[0], ["setup-live-auth"])
        hosting_get.assert_called_once_with(hosting_url, headers={"Accept": "text/html"}, timeout=60, allow_redirects=False)
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("HOSTING_VERIFICATION=protected-sign-in-gate", output)
        self.assertIn("INTERACTIVE_APP_ACCEPTANCE=not-performed", output)
        self.assertIn("SUCCESS: Hydro Operations deployment checks passed", output)
        self.assertNotIn("Hydro Operations is live", output)

    def test_verifies_installed_rayfin_without_running_the_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            package_dir = app_dir / "node_modules" / "@microsoft" / "rayfin-cli"
            executable = package_dir / "scripts" / "main"
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            (package_dir / "package.json").write_text(
                json.dumps({"version": "1.33.2", "bin": {"rayfin": "scripts/main"}}),
                encoding="utf-8",
            )

            with patch.object(DEPLOY, "APP_DIR", app_dir):
                self.assertEqual(DEPLOY.installed_rayfin_version(), "1.33.2")

    def test_live_auth_contract_covers_foundry_and_fabric_embed(self):
        self.assertEqual(
            DEPLOY.REQUIRED_DELEGATED["18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"],
            {"user_impersonation"},
        )
        self.assertNotIn("7d312290-28c8-473c-a0ed-8e53749b6d6d", DEPLOY.REQUIRED_DELEGATED)
        self.assertIn(
            "Fabric.Embed",
            DEPLOY.REQUIRED_DELEGATED["00000009-0000-0000-c000-000000000000"],
        )

    def test_rayfin_api_capacity_match_requires_current_capacity_in_both_urls(self):
        capacity_id = "22222222-2222-2222-2222-222222222222"
        api_url = (
            "https://host.pbidedicated.windows.net/webapi/capacities/"
            f"{capacity_id}/workloads/BaaS/"
        )
        self.assertTrue(
            DEPLOY.rayfin_api_targets_capacity(
                {"RAYFIN_PUBLIC_API_URL": api_url},
                {"fabricApiUrl": api_url},
                capacity_id,
            )
        )
        self.assertFalse(
            DEPLOY.rayfin_api_targets_capacity(
                {"RAYFIN_PUBLIC_API_URL": api_url},
                {
                    "fabricApiUrl": (
                        "https://old.pbidedicated.windows.net/webapi/capacities/"
                        "33333333-3333-3333-3333-333333333333/workloads/BaaS/"
                    )
                },
                capacity_id,
            )
        )

    def test_endpoint_contract_requires_current_capacity_workspace_and_item(self):
        capacity_id = "22222222-2222-2222-2222-222222222222"
        workspace_id = "33333333-3333-3333-3333-333333333333"
        item_id = "44444444-4444-4444-4444-444444444444"
        api_url = (
            "https://22222222222222222222222222222222.pbidedicated.windows.net/"
            f"webapi/capacities/{capacity_id}/workloads/BaaS/BaaSService/automatic/v1/"
            f"workspaces/{workspace_id}/appbackends/{item_id}"
        )

        with patch.object(
            DEPLOY,
            "current_rayfin_target",
            return_value=(
                {"RAYFIN_PUBLIC_API_URL": api_url},
                {"fabricApiUrl": api_url},
            ),
        ):
            self.assertEqual(
                DEPLOY.validate_rayfin_endpoint_contract(
                    capacity_id, workspace_id, item_id
                ),
                api_url,
            )

    def test_cors_validation_retries_until_all_four_endpoints_are_ready(self):
        origin = "https://app.webapp.fabricapps.net"
        failed = Mock(status_code=500, headers={})
        ready = Mock(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Headers": (
                    "authorization, content-type, x-publishable-key"
                ),
            },
        )

        with (
            patch.object(
                DEPLOY.requests,
                "options",
                side_effect=[failed, ready, ready, ready, ready],
            ) as options,
            patch.object(
                DEPLOY.requests,
                "post",
                side_effect=[
                    Mock(
                        status_code=200,
                        headers={"Access-Control-Allow-Origin": origin},
                    ),
                    Mock(
                        status_code=400,
                        headers={"Access-Control-Allow-Origin": origin},
                    ),
                    Mock(
                        status_code=401,
                        headers={"Access-Control-Allow-Origin": origin},
                    ),
                    Mock(
                        status_code=401,
                        headers={"Access-Control-Allow-Origin": origin},
                    ),
                ],
            ) as post,
            patch.object(DEPLOY.time, "sleep") as sleep,
        ):
            DEPLOY.validate_appbackend_cors("https://api.test", origin, "pk-test")

        self.assertEqual(options.call_count, 5)
        self.assertEqual(post.call_count, 4)
        self.assertTrue(post.call_args.args[0].endswith("/connectors/hydrooperations/graphql"))
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])
        sleep.assert_called_once_with(2)

    def test_browser_readiness_retries_token_endpoint_http_500(self):
        origin = "https://app.webapp.fabricapps.net"
        ready = Mock(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": (
                    "authorization, content-type, x-publishable-key"
                ),
            },
        )
        with (
            patch.object(DEPLOY.requests, "options", return_value=ready),
            patch.object(
                DEPLOY.requests,
                "post",
                side_effect=[
                    Mock(status_code=200, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=500, headers={}),
                    Mock(status_code=400, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=401, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=401, headers={"Access-Control-Allow-Origin": "*"}),
                ],
            ) as post,
            patch.object(DEPLOY.time, "sleep") as sleep,
        ):
            DEPLOY.validate_appbackend_cors(
                "https://api.test", origin, "pk-test"
            )

        self.assertEqual(post.call_count, 5)
        sleep.assert_called_once_with(2)

    def test_connector_transport_requires_an_authentication_challenge_not_a_missing_route(self):
        ready = Mock(status_code=200, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type,x-publishable-key",
        })
        for status in (200, 400, 403, 404, 405, 500):
            with (
                self.subTest(status=status),
                patch.object(DEPLOY, "APPBACKEND_READINESS_DELAYS", (0,)),
                patch.object(DEPLOY.requests, "options", return_value=ready),
                patch.object(DEPLOY.requests, "post", side_effect=[
                    Mock(status_code=200, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=400, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=status, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=401, headers={"Access-Control-Allow-Origin": "*"}),
                ]),
            ):
                with self.assertRaisesRegex(DEPLOY.DeployError, "/connectors/hydrolakehouse/graphql"):
                    DEPLOY.validate_appbackend_cors("https://api.test", "https://app.test", "pk-test")

    def test_energy_connector_readiness_is_required_only_when_enabled(self):
        ready = Mock(status_code=200, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type,x-publishable-key",
        })
        for status in (401, 404, 500):
            with (
                self.subTest(status=status),
                patch.object(DEPLOY, "APPBACKEND_READINESS_DELAYS", (0,)),
                patch.object(DEPLOY.requests, "options", return_value=ready) as options,
                patch.object(DEPLOY.requests, "post", side_effect=[
                    Mock(status_code=200, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=400, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=401, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=401, headers={"Access-Control-Allow-Origin": "*"}),
                    Mock(status_code=status, headers={"Access-Control-Allow-Origin": "*"}),
                ]),
            ):
                if status == 401:
                    DEPLOY.validate_appbackend_cors("https://api.test", "https://app.test", "pk-test", energy_enabled=True)
                else:
                    with self.assertRaisesRegex(DEPLOY.DeployError, "/connectors/hydrogeo/graphql"):
                        DEPLOY.validate_appbackend_cors("https://api.test", "https://app.test", "pk-test", energy_enabled=True)
                self.assertTrue(options.call_args.args[0].endswith("/connectors/hydrogeo/graphql"))

    def test_cors_validation_rejects_missing_allow_origin(self):
        response = Mock(
            status_code=200,
            headers={
                "Access-Control-Allow-Headers": (
                    "authorization, content-type, x-publishable-key"
                ),
            },
        )

        with (
            patch.object(DEPLOY, "APPBACKEND_READINESS_DELAYS", (0,)),
            patch.object(DEPLOY.requests, "options", return_value=response),
        ):
            with self.assertRaisesRegex(
                DEPLOY.DeployError, "AppBackend browser readiness failed"
            ):
                DEPLOY.validate_appbackend_cors(
                    "https://api.test",
                    "https://app.webapp.fabricapps.net",
                    "pk-test",
                )

    def test_publishable_key_must_match_deployment_state(self):
        with patch.object(
            DEPLOY,
            "current_rayfin_target",
            return_value=(
                {"RAYFIN_PUBLIC_PUBLISHABLE_KEY": "pk-current"},
                {"publishableKey": "pk-stale"},
            ),
        ):
            with self.assertRaisesRegex(
                DEPLOY.DeployError, "publishable-key validation failed"
            ):
                DEPLOY.validate_rayfin_publishable_key()

    def test_state_rotation_moves_only_known_files_to_temp_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rayfin_dir = root / "rayfin"
            backup_root = root / "temp"
            rayfin_dir.mkdir()
            backup_root.mkdir()
            template = "\n".join(
                (
                    "FABRIC_WORKSPACE_NAME=<your Fabric workspace display name>",
                    "RAYFIN_PUBLIC_WORKSPACE_ID=<your Fabric workspace GUID>",
                    "RAYFIN_PUBLIC_AAD_CLIENT_ID=<your Entra SPA app (client) id>",
                    "RAYFIN_PUBLIC_TENANT_ID=<your Entra tenant id>",
                )
            )
            (rayfin_dir / ".env.example").write_text(template, encoding="utf-8")
            prior = {
                ".env": "old env",
                ".env.local": "old generated env",
                ".deployments.json": "{}",
            }
            for name, content in prior.items():
                (rayfin_dir / name).write_text(content, encoding="utf-8")

            with (
                patch.object(DEPLOY, "RAYFIN_DIR", rayfin_dir),
                patch.object(DEPLOY.tempfile, "gettempdir", return_value=str(backup_root)),
                patch.object(DEPLOY.Path, "unlink", side_effect=AssertionError("must not delete state")),
                patch.object(DEPLOY, "resolve_public_artifact_config", return_value={}),
            ):
                DEPLOY.prepare_rayfin_env(
                    "ad340c84-1886-4202-a483-2da2cb9168eb",
                    "a79a4b7e-e508-4fa4-8b6f-15deadca0f34",
                    "Demo Workspace",
                    "33333333-3333-3333-3333-333333333333",
                    "22dedc54-8b7e-442c-929d-497c4df086e6",
                )

            backup_dirs = list((backup_root / "fabric-demo-rayfin-backups").iterdir())
            self.assertEqual(len(backup_dirs), 1)
            for name, content in prior.items():
                self.assertEqual((backup_dirs[0] / name).read_text(encoding="utf-8"), content)
            self.assertIn(
                "RAYFIN_PUBLIC_AAD_CLIENT_ID=22dedc54-8b7e-442c-929d-497c4df086e6",
                (rayfin_dir / ".env").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "RAYFIN_PUBLIC_ENABLE_ENERGY_MAP=false",
                (rayfin_dir / ".env").read_text(encoding="utf-8"),
            )

    def test_feature_flag_update_replaces_stale_map_setting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = Path(temp_dir) / ".env"
            env.write_text("RAYFIN_PUBLIC_ENABLE_ENERGY_MAP=true\n", encoding="utf-8")

            DEPLOY.configure_app_feature_flags(env, enable_energy_map=False)

            self.assertEqual(env.read_text(encoding="utf-8"), "RAYFIN_PUBLIC_ENABLE_ENERGY_MAP=false\n")

    def test_backend_rotation_preserves_only_same_workspace_foundry_selection(self):
        for same_tenant, same_workspace in ((True, True), (False, True), (True, False)):
            with self.subTest(tenant=same_tenant, workspace=same_workspace), tempfile.TemporaryDirectory() as directory:
                rayfin = Path(directory) / "rayfin"
                rayfin.mkdir()
                old = {
                    "RAYFIN_PUBLIC_TENANT_ID": "tenant" if same_tenant else "another",
                    "RAYFIN_PUBLIC_WORKSPACE_ID": "workspace" if same_workspace else "another",
                    "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID": "subscription",
                    "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID": "/subscriptions/subscription/projects/project",
                    "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": "https://account.services.ai.azure.com/api/projects/project",
                    "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": "chat-model",
                    "RAYFIN_PUBLIC_ORCHESTRATOR_VERSION": "old-version",
                    "RAYFIN_PUBLIC_API_URL": "old-capacity-endpoint",
                }
                (rayfin / ".env").write_text("\n".join(f"{key}={value}" for key, value in old.items()), encoding="utf-8")
                (rayfin / ".env.example").write_text(
                    "FABRIC_WORKSPACE_NAME=<your Fabric workspace display name>\n"
                    "RAYFIN_PUBLIC_WORKSPACE_ID=<your Fabric workspace GUID>\n"
                    "RAYFIN_PUBLIC_TENANT_ID=<your Entra tenant id>\n"
                    "RAYFIN_PUBLIC_AAD_CLIENT_ID=<your Entra SPA app (client) id>\n", encoding="utf-8",
                )
                with patch.object(DEPLOY, "RAYFIN_DIR", rayfin), \
                        patch.object(DEPLOY.tempfile, "gettempdir", return_value=directory), \
                        patch.object(DEPLOY, "resolve_public_artifact_config", return_value={}):
                    self.assertFalse(DEPLOY.prepare_rayfin_env("tenant", "workspace", "Demo", "capacity", "client"))
                restored = (rayfin / ".env").read_text(encoding="utf-8")
                for key in (
                    "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID", "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID",
                    "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT", "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT",
                ):
                    if same_tenant and same_workspace:
                        self.assertIn(f"{key}={old[key]}", restored)
                    else:
                        self.assertNotIn(key, restored)
                self.assertNotIn("old-version", restored)
                self.assertNotIn("old-capacity-endpoint", restored)

    def test_routine_env_preparation_preserves_target_energy_flag_unless_explicitly_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            rayfin = Path(directory)
            values = {
                "FABRIC_WORKSPACE_NAME": "Demo",
                "RAYFIN_PUBLIC_TENANT_ID": "tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "workspace",
                "RAYFIN_PUBLIC_AAD_CLIENT_ID": "client", "RAYFIN_PUBLIC_ENABLE_ENERGY_MAP": "true",
            }
            deployment = {"fabricWorkspaceId": "workspace", "fabricTenantId": "tenant", "fabricItemId": "item"}
            for selection, expected in ((None, "true"), (False, "false")):
                with self.subTest(selection=selection):
                    (rayfin / ".env").write_text("RAYFIN_PUBLIC_ENABLE_ENERGY_MAP=true\n", encoding="utf-8")
                    with patch.object(DEPLOY, "RAYFIN_DIR", rayfin), \
                            patch.object(DEPLOY, "current_rayfin_target", return_value=(values, deployment)), \
                            patch.object(DEPLOY, "resolve_public_artifact_config", return_value={}), \
                            patch.object(DEPLOY, "rayfin_api_targets_capacity", return_value=True), \
                            patch.object(DEPLOY, "fabric_item_exists", return_value=True):
                        self.assertTrue(DEPLOY.prepare_rayfin_env(
                            "tenant", "workspace", "Demo", "capacity", "client", enable_energy_map=selection,
                        ))
                    self.assertIn(f"RAYFIN_PUBLIC_ENABLE_ENERGY_MAP={expected}", (rayfin / ".env").read_text())

    def test_write_rayfin_redirects_retains_current_entra_origin(self):
        current = "https://current-app-swedencentral.webapp.fabricapps.net"
        with tempfile.TemporaryDirectory() as temp_dir:
            rayfin_dir = Path(temp_dir)
            (rayfin_dir / "rayfin.yml").write_text(
                "services:\n  auth:\n    allowedRedirectUris:\n  staticHosting:\n    enabled: true\n",
                encoding="utf-8",
            )

            with patch.object(DEPLOY, "RAYFIN_DIR", rayfin_dir):
                merged = DEPLOY.write_rayfin_redirects([current])
                written = (rayfin_dir / "rayfin.yml").read_text(encoding="utf-8")

        self.assertEqual(merged, [current, "http://localhost:5173"])
        self.assertIn(f"      - {current}", written)
        self.assertIn("  staticHosting:", written)

    def test_write_rayfin_redirects_removes_stale_local_origin(self):
        stale = "https://stale-app-swedencentral.webapp.fabricapps.net"
        current = "https://current-app-swedencentral.webapp.fabricapps.net"
        with tempfile.TemporaryDirectory() as temp_dir:
            rayfin_dir = Path(temp_dir)
            (rayfin_dir / "rayfin.yml").write_text(
                "\n".join(
                    (
                        "services:",
                        "  auth:",
                        "    allowedRedirectUris:",
                        f"      - {stale}",
                        "      - http://localhost:5173",
                        "  staticHosting:",
                        "    enabled: true",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            with patch.object(DEPLOY, "RAYFIN_DIR", rayfin_dir):
                merged = DEPLOY.write_rayfin_redirects([current])
                written = (rayfin_dir / "rayfin.yml").read_text(encoding="utf-8")

        self.assertEqual(merged, [current, "http://localhost:5173"])
        self.assertNotIn(stale, written)
        self.assertIn(f"      - {current}", written)


class WorkspaceArtifactConfigTests(unittest.TestCase):
    workspace = "9c73201e-b2e5-48eb-81b9-3526d320faca"
    tenant = "ad340c84-1886-4202-a483-2da2cb9168eb"
    eventhouse = "f2917154-6a4c-4930-bae3-e55e4daa9440"
    database = "019bbbad-042c-4930-b818-799879aa98bc"
    graphql = "a110f3b7-c446-4f2d-a998-6d792781b725"
    cluster = "https://trd-pxczt6cwfz7z032cn8.z9.kusto.fabric.microsoft.com"

    def setUp(self):
        self.config = {
            "FABRIC_WORKSPACE_NAME": "Target V3",
            "RAYFIN_PUBLIC_WORKSPACE_ID": self.workspace,
            "RAYFIN_PUBLIC_TENANT_ID": self.tenant,
            "RAYFIN_PUBLIC_AAD_CLIENT_ID": "spa-id",
            "RAYFIN_PUBLIC_EVENTHOUSE_NAME": "RTI_Demo_Eventhouse_V6",
            "RAYFIN_PUBLIC_KQL_DATABASE": "RTI_Demo_Eventhouse_V6",
            "RAYFIN_PUBLIC_KQL_CLUSTER_URI": "https://old.kusto.fabric.microsoft.com",
            "RAYFIN_PUBLIC_STID_GRAPHQL_URL": "https://api.fabric.microsoft.com/v1/workspaces/old/graphqlapis/old/graphql",
            "RAYFIN_PUBLIC_STID_GRAPHQL_ID": "old-id",
            "RAYFIN_PUBLIC_STREAM_PIPELINE_ID": "old-pipeline",
            "RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_ID": "old-notebook",
            "RAYFIN_PUBLIC_LAKEHOUSE_NAME": "Energy_IQ_LakehouseRTI_V6",
        }
        self.items = [
            {"id": self.eventhouse, "type": "Eventhouse", "displayName": "RTI_Demo_Eventhouse_V3"},
            {"id": "managed-eventhouse", "type": "Eventhouse", "displayName": "Ontology managed Eventhouse"},
            {"id": self.database, "type": "KQLDatabase", "displayName": "RTI_Demo_Eventhouse_V3"},
            {"id": self.graphql, "type": "GraphQLApi", "displayName": "Hydro_STID_API"},
            {"id": "lakehouse-v3", "type": "Lakehouse", "displayName": "Energy_IQ_LakehouseRTI_V3"},
        ]
        self.details = {
            "lakehouse-v3": {
                "id": "lakehouse-v3", "workspaceId": self.workspace,
                "properties": {"sqlEndpointProperties": {"id": "33333333-3333-3333-3333-333333333333"}},
            },
            self.database: {
                "id": self.database, "displayName": "RTI_Demo_Eventhouse_V3",
                "properties": {"parentEventhouseItemId": self.eventhouse, "queryServiceUri": self.cluster},
            }
        }

    def read_metadata(self, path, headers):
        self.assertTrue(path.startswith(f"workspaces/{self.workspace}/"), path)
        if path == f"workspaces/{self.workspace}/items":
            return {"value": self.items}
        return self.details[path.rsplit("/", 1)[-1]]

    def resolve(self, configured=None):
        with (
            patch.object(DEPLOY, "fabric_headers", return_value={}),
            patch.object(DEPLOY, "fabric_get", side_effect=self.read_metadata),
        ):
            return DEPLOY.resolve_public_artifact_config(
                self.workspace, self.tenant, self.config if configured is None else configured
            )

    def test_unmaterialized_ontology_identity_is_verified_without_graph_lineage_requests(self):
        ontology_id = "99999999-9999-9999-9999-999999999999"
        self.items.append({"id": ontology_id, "type": "Ontology", "displayName": "Hydro ontology"})
        self.details[ontology_id] = {
            "id": ontology_id, "workspaceId": self.workspace, "properties": {"generation": 2},
        }
        result = self.resolve()
        self.assertEqual(result["RAYFIN_PUBLIC_ONTOLOGY_ID"], ontology_id)
        self.assertEqual(result["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"], "")
        for generation in (1, "2", 2.0, None):
            self.details[ontology_id]["properties"]["generation"] = generation
            with self.subTest(generation=generation), self.assertRaisesRegex(DEPLOY.DeployError, "generation-2"):
                self.resolve()

    def test_named_shared_store_is_discovered_without_the_legacy_display_name(self):
        from operational_store_support import OPERATIONAL_STORE_DESCRIPTION
        item_id = "7972fa8c-e2cd-4c9a-bdd6-053b37293f19"
        self.items.append({
            "id": item_id, "type": "SQLDatabase", "displayName": "RTI_Demo_SQLDatabase_V3",
        })
        self.details[item_id] = {"id": item_id, "description": OPERATIONAL_STORE_DESCRIPTION}
        self.assertEqual(self.resolve()["RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID"], item_id)
        self.details[item_id]["description"] = "Unrelated item"
        with self.assertRaisesRegex(DEPLOY.DeployError, "ownership"):
            self.resolve()

    def test_connector_uses_live_sql_endpoint_not_lakehouse_or_stale_override(self):
        self.config["RAYFIN_PUBLIC_LAKEHOUSE_SQL_ENDPOINT_ID"] = "old-endpoint"
        values = self.resolve()
        self.assertEqual(values["RAYFIN_PUBLIC_LAKEHOUSE_ID"], "lakehouse-v3")
        self.assertEqual(values["RAYFIN_PUBLIC_LAKEHOUSE_SQL_ENDPOINT_ID"], "33333333-3333-3333-3333-333333333333")
        for endpoint in (None, {}, {"id": "not-a-guid"}):
            self.details["lakehouse-v3"]["properties"]["sqlEndpointProperties"] = endpoint or {}
            with self.assertRaisesRegex(DEPLOY.DeployError, "SQL analytics endpoint"):
                self.resolve()

    def test_geo_connector_uses_its_own_live_endpoint_and_clears_stale_configuration(self):
        geo_id = "90909090-9090-4090-8090-909090909090"
        endpoint = "80808080-8080-4080-8080-808080808080"
        self.items.append({"id": geo_id, "type": "Lakehouse", "displayName": "Hydro_GeoContext_V3"})
        self.details[geo_id] = {
            "id": geo_id, "workspaceId": self.workspace,
            "properties": {"sqlEndpointProperties": {"id": endpoint}},
        }
        self.config["RAYFIN_PUBLIC_GEO_SQL_ENDPOINT_ID"] = "stale"
        values = self.resolve()
        self.assertEqual(values["RAYFIN_PUBLIC_GEO_SQL_ENDPOINT_ID"], endpoint)
        self.assertEqual(values["RAYFIN_PUBLIC_GEO_LAKEHOUSE_ID"], geo_id)
        self.details[geo_id]["properties"]["sqlEndpointProperties"]["id"] = values["RAYFIN_PUBLIC_LAKEHOUSE_SQL_ENDPOINT_ID"]
        with self.assertRaisesRegex(DEPLOY.DeployError, "distinct verified SQL"):
            self.resolve()
        self.details[geo_id]["properties"]["sqlEndpointProperties"]["id"] = endpoint
        self.details[geo_id]["workspaceId"] = "different-workspace"
        with self.assertRaisesRegex(DEPLOY.DeployError, "distinct verified SQL"):
            self.resolve()
        self.items.pop()
        self.assertEqual(self.resolve()["RAYFIN_PUBLIC_GEO_SQL_ENDPOINT_ID"], "")

    def test_verified_archive_does_not_ambigously_select_the_previous_operational_store(self):
        from operational_store_support import OPERATIONAL_ARCHIVE_PREFIX, OPERATIONAL_STORE_DESCRIPTION
        target_id = "9b3dfea4-0934-4122-aaa4-1d7f17a183bd"
        self.items.extend([
            {"id": "original", "type": "SQLDatabase", "displayName": "HydroOperationalStore",
             "description": OPERATIONAL_ARCHIVE_PREFIX + target_id + "."},
            {"id": target_id, "type": "SQLDatabase", "displayName": "RTI_Demo_SQLDatabase_V3",
             "description": OPERATIONAL_STORE_DESCRIPTION},
        ])
        self.details[target_id] = {"id": target_id, "description": OPERATIONAL_STORE_DESCRIPTION}
        self.assertEqual(self.resolve()["RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID"], target_id)

    def test_reused_state_rebinds_stale_v6_public_config_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            rayfin_dir = Path(directory)
            (rayfin_dir / ".env").write_text("\n".join(f"{key}={value}" for key, value in self.config.items()), encoding="utf-8")
            (rayfin_dir / ".env.local").write_text("CLI-owned generated output", encoding="utf-8")
            deployment = {"fabricWorkspaceId": self.workspace, "fabricTenantId": self.tenant, "fabricItemId": "app-id"}
            with (
                patch.object(DEPLOY, "RAYFIN_DIR", rayfin_dir),
                patch.object(DEPLOY, "current_rayfin_target", return_value=(self.config, deployment)),
                patch.object(DEPLOY, "rayfin_api_targets_capacity", return_value=True),
                patch.object(DEPLOY, "fabric_item_exists", return_value=True),
                patch.object(DEPLOY, "fabric_headers", return_value={}),
                patch.object(DEPLOY, "fabric_get", side_effect=self.read_metadata),
            ):
                self.assertTrue(DEPLOY.prepare_rayfin_env(self.tenant, self.workspace, "Target V3", "capacity-id", "spa-id"))
            rebound = (rayfin_dir / ".env").read_text(encoding="utf-8")
            self.assertNotIn("V6", rebound)
            self.assertNotIn("workspaces/old", rebound)
            self.assertIn("RTI_Demo_Eventhouse_V3", rebound)
            self.assertIn(self.cluster, rebound)
            self.assertNotIn("STID_GRAPHQL", rebound)
            self.assertEqual((rayfin_dir / ".env.local").read_text(encoding="utf-8"), "CLI-owned generated output")

    def test_fresh_template_cannot_reintroduce_stale_defaults(self):
        resolved = self.resolve({})
        stale_template = "\n".join(f"{key}={value}" for key, value in self.config.items())
        rebound = DEPLOY._rebind_public_env(stale_template, resolved)
        self.assertNotIn("V6", rebound)
        self.assertNotIn("workspaces/old", rebound)
        self.assertNotIn("STID_GRAPHQL", rebound)
        self.assertEqual(resolved["RAYFIN_PUBLIC_KQL_DATABASE_ID"], self.database)
        self.assertEqual(resolved["RAYFIN_PUBLIC_EVENTHOUSE_ID"], self.eventhouse)

    def test_full_workflow_discovers_alphanumeric_underscore_suffix(self):
        self.items[0]["displayName"] = "RTI_Demo_Eventhouse_VJOA_2"
        self.items[2]["displayName"] = "RTI_Demo_Eventhouse_VJOA_2"
        self.details[self.database]["displayName"] = "RTI_Demo_Eventhouse_VJOA_2"
        self.items[4]["displayName"] = "Energy_IQ_LakehouseRTI_VJOA_2"
        self.items.append({
            "id": "dashboard-vjoa-2",
            "type": "KQLDashboard",
            "displayName": "RTI_Demo_OPCUA_TelemetryStats_VJOA_2",
        })

        resolved = self.resolve({})

        self.assertEqual(resolved["RAYFIN_PUBLIC_EVENTHOUSE_NAME"], "RTI_Demo_Eventhouse_VJOA_2")
        self.assertEqual(resolved["RAYFIN_PUBLIC_KQL_DATABASE"], "RTI_Demo_Eventhouse_VJOA_2")
        self.assertEqual(resolved["RAYFIN_PUBLIC_LAKEHOUSE_NAME"], "Energy_IQ_LakehouseRTI_VJOA_2")
        self.assertEqual(
            resolved["RAYFIN_PUBLIC_KQL_DASHBOARD_NAME"],
            "RTI_Demo_OPCUA_TelemetryStats_VJOA_2",
        )

    def test_before_seed_clears_missing_graphql_and_other_stale_ids(self):
        self.items = [item for item in self.items if item["type"] != "GraphQLApi"]
        resolved = self.resolve()
        for key in [
            "RAYFIN_PUBLIC_STREAM_PIPELINE_ID", "RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_ID",
            "RAYFIN_PUBLIC_LAKEHOUSE_SQL_ENDPOINT",
        ]:
            self.assertEqual(resolved[key], "", key)
        self.assertNotIn("RAYFIN_PUBLIC_STID_GRAPHQL_ID", resolved)
        self.assertEqual(resolved["RAYFIN_PUBLIC_KQL_DATABASE"], "RTI_Demo_Eventhouse_V3")

    def test_missing_optional_jobs_keep_discoverable_names_but_clear_stale_ids(self):
        resolved = self.resolve()
        for prefix, name in [
            ("STREAM_PIPELINE", "02_Pipe_Stream"),
            ("POSTSEED_NOTEBOOK", "RTI_011_seed_sql_wire_graphql_agent"),
        ]:
            self.assertEqual(resolved[f"RAYFIN_PUBLIC_{prefix}_NAME"], name)
            self.assertEqual(resolved[f"RAYFIN_PUBLIC_{prefix}_ID"], "")
        for kind, prefix, name in [
            ("DataPipeline", "STREAM_PIPELINE", "02_Pipe_Stream"),
            ("Notebook", "POSTSEED_NOTEBOOK", "RTI_011_seed_sql_wire_graphql_agent"),
        ]:
            self.items.append({"id": prefix, "type": kind, "displayName": name})
        resolved = self.resolve()
        self.assertEqual(resolved["RAYFIN_PUBLIC_STREAM_PIPELINE_ID"], "STREAM_PIPELINE")
        self.assertEqual(resolved["RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_ID"], "POSTSEED_NOTEBOOK")

    def test_does_not_select_ontology_managed_eventhouse_or_database_by_name(self):
        self.items.append({"id": "unrelated-database", "type": "KQLDatabase", "displayName": "RTI_Demo_Eventhouse_V3"})
        self.details["unrelated-database"] = {
            "id": "unrelated-database", "displayName": "RTI_Demo_Eventhouse_V3",
            "properties": {"parentEventhouseItemId": "managed-eventhouse", "queryServiceUri": "https://other.kusto.fabric.microsoft.com"},
        }
        resolved = self.resolve()
        self.assertEqual(resolved["RAYFIN_PUBLIC_EVENTHOUSE_ID"], self.eventhouse)
        self.assertEqual(resolved["RAYFIN_PUBLIC_KQL_DATABASE_ID"], self.database)
        self.assertEqual(resolved["RAYFIN_PUBLIC_KQL_CLUSTER_URI"], self.cluster)

    def test_multiple_rti_eventhouses_fail_without_a_target_verified_name(self):
        self.items.append({"id": "other-rti", "type": "Eventhouse", "displayName": "RTI_Demo_Eventhouse_V4"})
        with self.assertRaisesRegex(DEPLOY.DeployError, "Ambiguous target-workspace Eventhouse"):
            self.resolve()
        configured = {**self.config, "RAYFIN_PUBLIC_EVENTHOUSE_NAME": "RTI_Demo_Eventhouse_V3"}
        self.assertEqual(self.resolve(configured)["RAYFIN_PUBLIC_EVENTHOUSE_ID"], self.eventhouse)
        configured["RAYFIN_PUBLIC_WORKSPACE_ID"] = "old-workspace"
        with self.assertRaisesRegex(DEPLOY.DeployError, "Ambiguous"):
            self.resolve(configured)

    def test_custom_eventhouse_name_requires_verification_in_current_scope(self):
        self.items[0]["displayName"] = "Telemetry custom"
        configured = {**self.config, "RAYFIN_PUBLIC_EVENTHOUSE_NAME": '"Telemetry custom"'}
        self.assertEqual(self.resolve(configured)["RAYFIN_PUBLIC_EVENTHOUSE_NAME"], "Telemetry custom")
        configured["RAYFIN_PUBLIC_TENANT_ID"] = "old-tenant"
        with self.assertRaisesRegex(DEPLOY.DeployError, "No verified Eventhouse"):
            self.resolve(configured)

    def test_missing_or_multiple_associated_databases_fail_closed(self):
        self.details[self.database]["properties"]["parentEventhouseItemId"] = "managed-eventhouse"
        with self.assertRaisesRegex(DEPLOY.DeployError, "parentEventhouseItemId"):
            self.resolve()
        self.details[self.database]["properties"]["parentEventhouseItemId"] = self.eventhouse
        self.items.append({"id": "second-database", "type": "KQLDatabase", "displayName": "Second database"})
        self.details["second-database"] = {
            "id": "second-database", "displayName": "Second database",
            "properties": {"parentEventhouseItemId": self.eventhouse, "queryServiceUri": self.cluster},
        }
        with self.assertRaisesRegex(DEPLOY.DeployError, "parentEventhouseItemId"):
            self.resolve()
        resolved = self.resolve({**self.config, "RAYFIN_PUBLIC_KQL_DATABASE": "Second database"})
        self.assertEqual(resolved["RAYFIN_PUBLIC_KQL_DATABASE_ID"], "second-database")

    def test_retired_stid_and_unrelated_graphql_items_are_not_deployment_dependencies(self):
        self.items.append({"id": "other-graphql", "type": "GraphQLApi", "displayName": "Hydro_STID_API"})
        self.assertNotIn("RAYFIN_PUBLIC_STID_GRAPHQL_ID", self.resolve())
        self.items = [item for item in self.items if item["type"] != "GraphQLApi"]
        self.items.append({"id": "other-graphql", "type": "GraphQLApi", "displayName": "Unrelated API"})
        self.assertNotIn("RAYFIN_PUBLIC_STID_GRAPHQL_URL", self.resolve())

    def test_graph_binding_is_preserved_only_when_operator_mapping_is_target_scoped(self):
        self.items.extend([
            {"id": "ontology-id", "type": "Ontology", "displayName": "Hydro ontology"},
            {"id": "graph-id", "type": "GraphModel", "displayName": "Operator verified graph"},
        ])
        self.details["ontology-id"] = {
            "id": "ontology-id", "workspaceId": self.workspace, "properties": {"generation": 2},
        }
        self.details["downstream?beta=true"] = {"items": [], "relations": [], "workspaces": []}
        binding = {
            "workspaceId": self.workspace, "ontologyId": "ontology-id", "graphModelId": "graph-id",
            "nodeTypes": {"opaque": "hydro#equipment"},
        }
        configured = {**self.config, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": "'" + json.dumps(binding) + "'"}
        resolved = self.resolve(configured)
        self.assertEqual(json.loads(resolved["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"]), binding)
        rendered = DEPLOY._rebind_public_env("", resolved)
        roundtrip = dict(line.split("=", 1) for line in rendered.splitlines())
        self.assertEqual(json.loads(DEPLOY._public_config_value(roundtrip, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING")), binding)
        for changed in [{"workspaceId": "old-workspace"}, {"ontologyId": "absent"}, {"graphModelId": "absent"}]:
            with self.subTest(changed=changed), self.assertRaisesRegex(DEPLOY.DeployError, "operator-provided Ontology graph binding"):
                self.resolve({**configured, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": json.dumps({**binding, **changed})})

    def test_graph_binding_is_discovered_from_authoritative_ontology_lineage(self):
        ontology_id = "ontology-id"
        graph_id = "graph-id"
        self.items.extend([
            {"id": ontology_id, "type": "Ontology", "displayName": "RTI_Demo_Ontology_V100"},
            {"id": graph_id, "type": "GraphModel", "displayName": "opaque-generated-name"},
        ])
        self.details[ontology_id] = {
            "id": ontology_id, "workspaceId": self.workspace, "properties": {"generation": 2},
        }
        self.details["downstream?beta=true"] = {
            "items": [
                {"id": ontology_id, "type": "Ontology", "displayName": "RTI_Demo_Ontology_V100",
                 "workspaceId": self.workspace},
                {"id": graph_id, "type": "GraphIndex", "displayName": "opaque-generated-name",
                 "workspaceId": self.workspace},
            ],
            "relations": [
                {"itemId": graph_id, "dependentOnItemId": ontology_id, "relationType": "CascadeDelete"},
            ],
            "workspaces": [{"id": self.workspace, "displayName": "Target V100"}],
        }

        resolved = self.resolve()

        self.assertEqual(resolved["RAYFIN_PUBLIC_ONTOLOGY_NAME"], "RTI_Demo_Ontology_V100")
        self.assertEqual(json.loads(resolved["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"]), {
            "workspaceId": self.workspace,
            "ontologyId": ontology_id,
            "graphModelId": graph_id,
        })
        binding = {"workspaceId": self.workspace, "ontologyId": ontology_id, "graphModelId": "conflicting-graph"}
        with patch.dict(DEPLOY.os.environ, {"HYDRO_ONTOLOGY_GRAPH_BINDING": json.dumps(binding)}):
            with self.assertRaisesRegex(DEPLOY.DeployError, "conflicts with authoritative"):
                self.resolve()

    def test_explicit_graph_binding_selects_the_ontology_on_a_fresh_target_without_editing_state(self):
        self.items.extend([
            {"id": "ontology-id", "type": "Ontology", "displayName": "Chosen ontology"},
            {"id": "other-ontology", "type": "Ontology", "displayName": "Unrelated ontology"},
            {"id": "graph-id", "type": "GraphModel", "displayName": "Operator verified graph"},
        ])
        self.details["ontology-id"] = {
            "id": "ontology-id", "workspaceId": self.workspace, "properties": {"generation": 2},
        }
        self.details["downstream?beta=true"] = {"items": [], "relations": [], "workspaces": []}
        binding = {"workspaceId": self.workspace, "ontologyId": "ontology-id", "graphModelId": "graph-id"}
        old = {
            **self.config, "RAYFIN_PUBLIC_WORKSPACE_ID": "old-workspace",
            "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": json.dumps({**binding, "workspaceId": "old-workspace"}),
        }
        with patch.dict(DEPLOY.os.environ, {"HYDRO_ONTOLOGY_GRAPH_BINDING": json.dumps(binding)}):
            result = self.resolve(old)
        self.assertEqual(json.loads(result["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"]), binding)
        self.assertEqual(result["RAYFIN_PUBLIC_ONTOLOGY_NAME"], "Chosen ontology")

    def test_old_workspace_graph_binding_is_not_reused_for_a_new_target(self):
        old = {
            **self.config, "RAYFIN_PUBLIC_WORKSPACE_ID": "old-workspace",
            "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": json.dumps({
                "workspaceId": "old-workspace", "ontologyId": "old-ontology", "graphModelId": "old-graph",
            }),
        }
        with patch.dict(DEPLOY.os.environ, {"HYDRO_ONTOLOGY_GRAPH_BINDING": ""}):
            self.assertEqual(self.resolve(old)["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"], "")

    def test_stale_same_workspace_ontology_hint_rotates_to_authoritative_lineage(self):
        ontology_id = "ontology-v10"
        graph_id = "graph-v10"
        self.items.extend([
            {"id": ontology_id, "type": "Ontology", "displayName": "RTI_Demo_Ontology_V10"},
            {"id": graph_id, "type": "GraphModel", "displayName": "generated-v10-graph"},
        ])
        self.details[ontology_id] = {
            "id": ontology_id, "workspaceId": self.workspace, "properties": {"generation": 2},
        }
        self.details["downstream?beta=true"] = {
            "items": [
                {"id": graph_id, "type": "GraphIndex", "displayName": "generated-v10-graph",
                 "workspaceId": self.workspace},
            ],
            "relations": [
                {"itemId": graph_id, "dependentOnItemId": ontology_id,
                 "relationType": "CascadeDelete"},
            ],
            "workspaces": [{"id": self.workspace, "displayName": "Target"}],
        }
        stale_binding = {
            "workspaceId": self.workspace,
            "ontologyId": "ontology-v20",
            "graphModelId": "graph-v20",
        }
        resolved = self.resolve({
            **self.config,
            "RAYFIN_PUBLIC_ONTOLOGY_NAME": "RTI_Demo_Ontology_V20",
            "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": json.dumps(stale_binding),
        })

        self.assertEqual(resolved["RAYFIN_PUBLIC_ONTOLOGY_NAME"], "RTI_Demo_Ontology_V10")
        self.assertEqual(json.loads(resolved["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"]), {
            "workspaceId": self.workspace,
            "ontologyId": ontology_id,
            "graphModelId": graph_id,
        })

    def test_graph_binding_ignores_non_authoritative_and_cross_workspace_relations(self):
        ontology = {"id": "ontology-id", "type": "Ontology", "displayName": "Hydro ontology"}
        graph = {"id": "graph-id", "type": "GraphModel", "displayName": "Only graph"}
        metadata = {
            "id": ontology["id"], "workspaceId": self.workspace, "properties": {"generation": 2},
        }
        false_relations = [
            {
                "items": [{"id": graph["id"], "type": "GraphIndex", "workspaceId": self.workspace}],
                "relations": [{"itemId": graph["id"], "dependentOnItemId": ontology["id"],
                               "relationType": "Reference"}],
            },
            {
                "items": [{"id": graph["id"], "type": "GraphIndex", "workspaceId": self.workspace}],
                "relations": [{"itemId": ontology["id"], "dependentOnItemId": graph["id"],
                               "relationType": "CascadeDelete"}],
            },
            {
                "items": [{"id": graph["id"], "type": "GraphIndex", "workspaceId": "other-workspace"}],
                "relations": [{"itemId": graph["id"], "dependentOnItemId": ontology["id"],
                               "relationType": "CascadeDelete"}],
            },
        ]
        for lineage in false_relations:
            with self.subTest(lineage=lineage), patch.object(
                DEPLOY, "fabric_get", side_effect=[metadata, lineage]
            ):
                self.assertIsNone(
                    DEPLOY._lineage_graph_binding(self.workspace, ontology, [ontology, graph], {})
                )

    def test_graph_binding_fails_closed_when_lineage_has_multiple_graphs(self):
        ontology = {"id": "ontology-id", "type": "Ontology", "displayName": "Hydro ontology"}
        graphs = [
            {"id": "graph-one", "type": "GraphModel", "displayName": "Graph one"},
            {"id": "graph-two", "type": "GraphModel", "displayName": "Graph two"},
        ]
        metadata = {
            "id": ontology["id"], "workspaceId": self.workspace, "properties": {"generation": 2},
        }
        lineage = {
            "items": [
                {"id": graph["id"], "type": "GraphIndex", "workspaceId": self.workspace}
                for graph in graphs
            ],
            "relations": [
                {"itemId": graph["id"], "dependentOnItemId": ontology["id"],
                 "relationType": "CascadeDelete"}
                for graph in graphs
            ],
        }
        with (
            patch.object(DEPLOY, "fabric_get", side_effect=[metadata, lineage]),
            self.assertRaisesRegex(DEPLOY.DeployError, "multiple materialized GraphModels"),
        ):
            DEPLOY._lineage_graph_binding(self.workspace, ontology, [ontology, *graphs], {})

    def test_artifact_pagination_never_leaves_target_workspace(self):
        initial = f"workspaces/{self.workspace}/items"
        with (
            patch.object(DEPLOY, "fabric_headers", return_value={}),
            patch.object(DEPLOY, "fabric_get", return_value={
                "value": [],
                "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces/old/items?continuationToken=next",
            }) as get,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "outside the selected workspace"):
                DEPLOY.resolve_public_artifact_config(self.workspace, self.tenant, self.config)
            get.assert_called_once_with(initial, {})

    def test_token_pagination_is_encoded_and_target_scoped(self):
        initial = f"workspaces/{self.workspace}/items"
        with patch.object(DEPLOY, "fabric_get", side_effect=[
            {"value": self.items[:2], "continuationToken": "opaque +/&"},
            {"value": self.items[2:]},
        ]) as get:
            self.assertEqual(DEPLOY._workspace_artifacts(self.workspace, {}), self.items)
        self.assertEqual(get.call_args_list[1].args[0], initial + "?continuationToken=opaque%20%2B%2F%26")

    def test_resolution_failure_precedes_env_mutation_or_state_rotation(self):
        with (
            patch.object(DEPLOY, "current_rayfin_target", return_value=(self.config, None)),
            patch.object(DEPLOY, "resolve_public_artifact_config", side_effect=DEPLOY.DeployError("Ambiguous target artifacts")),
            patch.object(DEPLOY.shutil, "move") as move,
            patch.object(DEPLOY.Path, "write_text") as write,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Ambiguous"):
                DEPLOY.prepare_rayfin_env(self.tenant, self.workspace, "Target V3", "capacity-id", "spa-id")
            move.assert_not_called()
            write.assert_not_called()


class GeoConnectorConfigurationTests(unittest.TestCase):
    def test_new_geo_sources_exist_before_all_stage_endpoint_discovery(self):
        args = argparse.Namespace(
            tenant="tenant-id", workspace="workspace-id", client_id=None,
            push_config=False, stage="all", energy_map="enabled",
        )
        energy = Mock()

        def discover_endpoints(*args, **kwargs):
            energy.prepare_energy.assert_called_once_with()
            raise DEPLOY.DeployError("configuration boundary")

        with (
            patch.dict(os.environ, {"FABRIC_FEATURE_CONFIG": ""}),
            patch.object(DEPLOY, "prefer_tenant_scoped_azure_cli_config"),
            patch.object(DEPLOY, "ensure_azure_tenant", return_value="tenant-id"),
            patch.object(DEPLOY, "resolve_workspace", return_value=("workspace-id", "Demo", "capacity-id")),
            patch.object(DEPLOY, "resolve_spa", return_value="client-id"),
            patch.object(DEPLOY, "read_entra_spa_redirects_with_reauth", return_value=[]),
            patch.object(DEPLOY, "write_rayfin_redirects"),
            patch.object(DEPLOY, "energy_workspace", return_value=energy),
            patch.object(DEPLOY, "prepare_rayfin_env", side_effect=discover_endpoints),
            patch.object(DEPLOY, "ensure_deploy_dependencies") as dependencies,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "configuration boundary"):
                DEPLOY.deploy(args)
        dependencies.assert_not_called()

    def test_optional_connector_roundtrip_preserves_the_other_connectors(self):
        original = "connectors:\n  - name: hydrolakehouse\n    type: fabric-sqlanalytics\n"
        with tempfile.TemporaryDirectory() as directory, patch.object(DEPLOY, "RAYFIN_DIR", Path(directory)):
            config = Path(directory) / "rayfin.yml"
            config.write_text(original)
            DEPLOY.configure_geo_connector(enabled=True)
            enabled = config.read_text()
            self.assertIn("itemId: ${RAYFIN_PUBLIC_GEO_SQL_ENDPOINT_ID}", enabled)
            self.assertEqual(enabled.count("name: hydrogeo"), 1)
            DEPLOY.configure_geo_connector(enabled=True)
            self.assertEqual(config.read_text(), enabled)
            DEPLOY.configure_geo_connector(enabled=False)
            self.assertEqual(config.read_text(), original)

    def test_custom_or_duplicate_connector_is_not_silently_overwritten(self):
        for block in ("  - name: hydrogeo\n    type: custom\n", "  - name: hydrogeo\n  - name: hydrogeo\n"):
            with tempfile.TemporaryDirectory() as directory, patch.object(DEPLOY, "RAYFIN_DIR", Path(directory)):
                config = Path(directory) / "rayfin.yml"
                original = "connectors:\n" + block
                config.write_text(original)
                with self.assertRaises(DEPLOY.DeployError):
                    DEPLOY.configure_geo_connector(enabled=True)
                self.assertEqual(config.read_text(), original)


class ProtectedHostingTests(unittest.TestCase):
    hosting_url = "https://fast.webapp.fabricapps.net"

    def verify(self, body, *, status=200, content_type="text/html; charset=utf-8"):
        response = Mock(status_code=status, headers={"Content-Type": content_type}, text=body)
        with patch.object(DEPLOY.requests, "get", return_value=response) as get:
            result = DEPLOY.validate_hosted_page(
                self.hosting_url, "workspace-id", "appbackend-id", "tenant.example"
            )
        get.assert_called_once_with(
            self.hosting_url, headers={"Accept": "text/html"}, timeout=60, allow_redirects=False
        )
        return result

    def test_identity_matched_fabric_gate_is_availability_not_app_acceptance(self):
        with patch("builtins.print") as printed:
            self.assertEqual(self.verify(protected_hosting_gate()), "protected-sign-in-gate")
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("application bundle/UI was not loaded", output)
        self.assertIn("INTERACTIVE_APP_ACCEPTANCE=not-performed", output)

    def test_gate_bootstrap_accepts_real_escaped_query_separators(self):
        gate = protected_hosting_gate().replace("&", "\\u0026")
        self.assertEqual(self.verify(gate), "protected-sign-in-gate")

    def test_rejects_401_redirects_errors_and_non_html_even_with_gate_body(self):
        for status, content_type in [
            (401, "application/json"), (401, "text/html"), (403, "text/html"),
            (302, "text/html"), (500, "text/html"), (200, "application/json"),
            (200, "text/html-not-really"), (200, ""),
        ]:
            with self.subTest(status=status, content_type=content_type):
                with self.assertRaisesRegex(DEPLOY.DeployError, "HTTP"):
                    self.verify(protected_hosting_gate(), status=status, content_type=content_type)

    def test_rejects_gate_for_another_tenant_workspace_or_appbackend(self):
        for parameters in [
            {"workspace": "other-workspace"}, {"item": "other-appbackend"}, {"tenant": "other-tenant"},
        ]:
            with self.subTest(parameters=parameters):
                with self.assertRaisesRegex(DEPLOY.DeployError, "identity does not match"):
                    self.verify(protected_hosting_gate(**parameters))

    def test_rejects_untrusted_or_wrong_broker_identity_and_duplicate_query_keys(self):
        gate = protected_hosting_gate()
        for body in [
            gate.replace("app.fabric.microsoft.com", "example.test"),
            gate.replace("https://app.fabric.microsoft.com", "http://app.fabric.microsoft.com"),
            gate.replace("/secureItemEmbed?", "/unrelated?"),
            gate.replace("itemType=AppBackend", "itemType=Notebook"),
            gate.replace("extensionPath=%2Fbrokeredauth", "extensionPath=%2Funrelated"),
            gate.replace("workspaceId=workspace-id", "workspaceId=workspace-id&workspaceId=another"),
            gate.replace('"projectId": "appbackend-id"', '"projectId": "another"'),
        ]:
            with self.subTest(body=body):
                with self.assertRaisesRegex(DEPLOY.DeployError, "identity does not match"):
                    self.verify(body)

    def test_rejects_arbitrary_html_and_gate_lookalikes_or_malformed_bootstraps(self):
        gate = protected_hosting_gate()
        for body in [
            "<html><title>It works</title><h1>Welcome</h1></html>",
            "<html><title>Sign in required</title><h1>Sign in to continue</h1></html>",
            gate.replace('<button id="sh-signin"', '<button id="not-the-gate"'),
            gate.replace('data-state="signin"', 'data-state="error"'),
            gate.replace("Sign in required", "Something else"),
            gate.replace('{"authorizeBrokerUrl"', '{INVALID "authorizeBrokerUrl"'),
            gate.replace('"authorizeBrokerUrl":', '"unknownField":'),
            gate.replace("</body>", '<script type="application/json" id="sh-bootstrap">{}</script></body>'),
            '<!-- ' + gate + ' -->',
        ]:
            with self.subTest(body=body):
                with self.assertRaises(DEPLOY.DeployError):
                    self.verify(body)

    def test_accepts_hydro_app_shell_without_claiming_javascript_executed(self):
        body = (
            '<!doctype html><html><head><title>Hydro Operations</title></head>'
            '<body><div id="root"></div><script type="module" crossorigin '
            'src="/assets/index-ABC_123.js"></script></body></html>'
        )
        with patch("builtins.print") as printed:
            self.assertEqual(self.verify(body, content_type="Text/HTML; charset=utf-8"), "app-shell")
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("JavaScript execution and authenticated UI behavior were not tested", output)
        self.assertIn("INTERACTIVE_APP_ACCEPTANCE=not-performed", output)
        for unexpected in [
            body.replace('id="root"', 'id="other"'),
            body.replace('/assets/index-ABC_123.js', 'https://example.test/index.js'),
            body.replace('/assets/index-ABC_123.js', '/src/main.tsx'),
            body.replace("Hydro Operations", "Another app"),
        ]:
            with self.subTest(body=unexpected):
                with self.assertRaisesRegex(DEPLOY.DeployError, "neither"):
                    self.verify(unexpected)

    def test_rejects_untrusted_hosting_origin_without_requesting_it(self):
        with patch.object(DEPLOY.requests, "get") as get:
            for url in [
                "http://fast.webapp.fabricapps.net", "https://example.test",
                "https://fast.webapp.fabricapps.net.example.test",
                "https://fast.webapp.fabricapps.net/other",
            ]:
                with self.subTest(url=url):
                    with self.assertRaisesRegex(DEPLOY.DeployError, "generated HTTPS Fabric"):
                        DEPLOY.validate_hosted_page(url, "workspace-id", "appbackend-id", "tenant.example")
            get.assert_not_called()

    def test_network_failure_remains_a_deployment_error(self):
        with patch.object(DEPLOY.requests, "get", side_effect=DEPLOY.requests.Timeout("Timed out")):
            with self.assertRaisesRegex(DEPLOY.DeployError, "availability verification failed"):
                DEPLOY.validate_hosted_page(self.hosting_url, "workspace-id", "appbackend-id", "tenant.example")


if __name__ == "__main__":
    unittest.main()
