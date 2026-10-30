import base64
import copy
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path
import unittest
import uuid
from unittest.mock import Mock, call, patch

import requests

spec = importlib.util.spec_from_file_location("provision_foundry_agents", Path(__file__).with_name("provision_foundry_agents.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def definition(stage="published", workspace="workspace", ontology="ontology"):
    return {"definition": {"parts": [{
        "path": f"Files/Config/{stage}/ontology-test/datasource.json",
        "payload": base64.b64encode(json.dumps({
            "type": "ontology", "workspaceId": workspace, "artifactId": ontology,
        }).encode()).decode(),
    }]}}


class FabricIQConnectionTests(unittest.TestCase):
    def test_project_connection_inventory_preserves_pagination_and_rejects_foreign_pages(self):
        project = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a/projects/p"
        next_page = f"https://management.azure.com{project}/connections?api-version=2025-10-01-preview&skip=next"
        deploy = Mock(DeployError=RuntimeError)
        with patch.object(module, "request_with_read_retry", side_effect=[
            Mock(ok=True, json=lambda: {"value": [{"name": "first"}], "nextLink": next_page}),
            Mock(ok=True, json=lambda: {"value": [{"name": "second"}]}),
        ]) as request:
            self.assertEqual(module.read_project_connections(deploy, project, {}), [
                {"name": "first"}, {"name": "second"},
            ])
            self.assertEqual(request.call_count, 2)
        for invalid in (
            {"value": [], "nextLink": next_page.replace("management.azure.com", "example.com")},
            {"value": [], "nextLink": next_page.replace("/projects/p/", "/projects/other/")},
            {"value": None},
        ):
            with self.subTest(page=invalid), patch.object(module, "request_with_read_retry", return_value=Mock(
                ok=True, json=lambda: invalid,
            )) as request, self.assertRaises(RuntimeError):
                module.read_project_connections(deploy, project, {})
            self.assertEqual(request.call_count, 1)

    def test_definition_reads_are_coalesced_only_inside_one_verification(self):
        deploy = Mock(FABRIC_BASE="https://api.fabric.microsoft.com/v1", DeployError=RuntimeError)
        cached: dict = {}
        payload = definition()
        response = Mock(status_code=200, json=Mock(return_value=payload))
        with patch.object(module, "request_with_read_retry", return_value=response) as request:
            for _ in range(2):
                self.assertEqual(module.read_data_agent_definition(
                    deploy, "workspace", "agent", {}, definition_cache=cached,
                ), payload)
            self.assertEqual(request.call_count, 1)
            for workspace, item, collection in (
                ("other", "agent", "dataAgents"), ("workspace", "other", "dataAgents"),
                ("workspace", "agent", "ontologies"),
            ):
                module.read_data_agent_definition(
                    deploy, workspace, item, {}, collection=collection, definition_cache=cached,
                )
            module.read_data_agent_definition(deploy, "workspace", "agent", {}, definition_cache={})
            self.assertEqual(request.call_count, 5)

    def test_failed_definition_is_not_cached(self):
        deploy = Mock(FABRIC_BASE="https://api.fabric.microsoft.com/v1", DeployError=RuntimeError)
        cached: dict = {}
        response = Mock(status_code=200, json=Mock(return_value=definition()))
        with patch.object(module, "request_with_read_retry", side_effect=[
            requests.HTTPError("denied"), response,
        ]) as request:
            with self.assertRaises(requests.HTTPError):
                module.read_data_agent_definition(deploy, "workspace", "agent", {}, definition_cache=cached)
            self.assertEqual(cached, {})
            module.read_data_agent_definition(deploy, "workspace", "agent", {}, definition_cache=cached)
            self.assertEqual(request.call_count, 2)

    def test_connections_match_portal_fabric_audience_and_type_metadata(self):
        properties = module.fabric_iq_connection_properties("https://api.fabric.microsoft.com/example")
        self.assertEqual(properties["audience"], "https://api.fabric.microsoft.com")
        self.assertEqual(properties["metadata"], {"type": "fabric_iq_preview"})
        self.assertEqual(properties["authType"], "UserEntraToken")

    def test_reuses_verified_workspace_endpoint_without_copying_another_workspace(self):
        workspace, item = str(uuid.uuid4()), str(uuid.uuid4())
        host = workspace.replace("-", "") + ".zc7.w.api.fabric.microsoft.com"
        target = f"https://{host}/v1/mcp/workspaces/{workspace}/dataagents/{item}/agent"
        candidates = [
            {"properties": module.fabric_iq_connection_properties(target)},
            {"properties": module.fabric_iq_connection_properties(target)},
            {"properties": {"metadata": None}},
        ]
        self.assertEqual(module.fabric_iq_target(workspace, item, candidates), target)
        self.assertTrue(module.fabric_iq_target_matches(target, workspace, item))
        for invalid in (
            target.replace(host, "other.zc7.w.api.fabric.microsoft.com"),
            target.replace(host, "example.com"), target + "?redirect=1", target + "#fragment",
            target.replace("https://", "https://user@"), target.replace(item, str(uuid.uuid4())),
        ):
            with self.subTest(target=invalid):
                self.assertFalse(module.fabric_iq_target_matches(invalid, workspace, item))
        fallback = f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace}/dataagents/{item}/agent"
        self.assertEqual(module.fabric_iq_target(workspace, item, []), fallback)
        self.assertEqual(module.fabric_iq_target(str(uuid.uuid4()), item, candidates).split("/")[2],
                         "api.fabric.microsoft.com")

    def test_conflicting_verified_workspace_hosts_require_resolution(self):
        workspace, item = str(uuid.uuid4()), str(uuid.uuid4())
        connections = [
            {"properties": module.fabric_iq_connection_properties(
                f"https://{workspace.replace('-', '')}.z{zone}.w.api.fabric.microsoft.com"
                f"/v1/mcp/workspaces/{workspace}/dataagents/{item}/agent")}
            for zone in ("c7", "a1")
        ]
        with self.assertRaisesRegex(RuntimeError, "conflicting"):
            module.fabric_iq_target(workspace, item, connections)


class NativeToolboxProvisionTests(unittest.TestCase):
    tools = [{
        "type": "fabric_iq_preview", "name": "hydro-data", "server_label": "fabriciq-data-agent",
        "project_connection_id": "hydro-fabric-iq", "require_approval": "never",
    }]

    def run_convergence(self, *, current=None, verify_only=False, broken_readback=False):
        saved = copy.deepcopy(current)
        calls = []

        def request(method, url, **kwargs):
            nonlocal saved
            calls.append((method, url, kwargs))
            if method == "POST":
                saved = {**kwargs["json"], "version": "2"}
                return Mock(ok=True, status_code=200, json=lambda: saved)
            if method == "PATCH":
                return Mock(ok=True, status_code=200, json=lambda: {"default_version": "2"})
            if saved is None:
                return Mock(ok=False, status_code=404)
            body = saved if "/versions/" in url else {
                "default_version": "1" if broken_readback else saved["version"],
            }
            return Mock(ok=True, status_code=200, json=lambda: body)

        with patch.object(module, "request_with_read_retry", side_effect=request):
            version = module.ensure_native_toolbox("https://foundry.example/api/projects/hydro", {},
                                                   verify_only=verify_only)
        return version, calls, saved

    def test_existing_verified_toolbox_is_read_only_reused(self):
        version, calls, _ = self.run_convergence(current={"version": "1", "tools": self.tools})
        self.assertEqual(version, "1")
        self.assertEqual([item[0] for item in calls], ["GET", "GET"])

    def test_missing_toolbox_is_created_promoted_and_read_back(self):
        version, calls, saved = self.run_convergence()
        self.assertEqual(version, "2")
        self.assertEqual([item[0] for item in calls], ["GET", "POST", "PATCH", "GET", "GET"])
        self.assertEqual(saved["tools"], self.tools)

    def test_connection_convergence_preserves_existing_governance(self):
        old = copy.deepcopy(self.tools)
        old[0]["project_connection_id"] = "portal-prototype"
        governance = {"policies": {"sample": {"type": "test-policy"}},
                      "skills": [{"name": "existing-skill"}], "metadata": {"owner": "operator"},
                      "description": "Existing read-only native tools."}
        version, calls, saved = self.run_convergence(current={"version": "1", "tools": old, **governance})
        self.assertEqual(version, "2")
        for key, value in governance.items():
            self.assertEqual(saved[key], value)
        self.assertEqual([item[0] for item in calls], ["GET", "GET", "POST", "PATCH", "GET", "GET"])

    def test_prepared_verification_does_not_create_or_repair_missing_toolboxes(self):
        with self.assertRaisesRegex(RuntimeError, "missing or.*drifted"):
            self.run_convergence(verify_only=True)

    def test_default_version_must_match_publication_readback(self):
        with self.assertRaisesRegex(RuntimeError, "publication readback"):
            self.run_convergence(broken_readback=True)


class IncrementalProvisionTests(unittest.TestCase):
    def run_provision(self, *, drift=None, missing=None, broken_readback=False, denied=False):
        deploy, values, hosted, tenant, workspace = PreparedFoundryTests.prepared(self)
        endpoint = values["RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT"]
        ontology = values["RAYFIN_PUBLIC_ONTOLOGY_ID"]
        project_id = f"/subscriptions/{tenant}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/hydro/projects/hydro"
        selection = module.FoundrySelection(tenant, project_id, endpoint, "model")
        expected = {
            f"hydro-{role}-agent": {"kind": "prompt", "model": "model", "instructions": role, "tools": []}
            for role in ("qa", "rca", "work-order", "fabric-iq", "weather")
        }
        live = {
            name: {"version": "7", "definition": {**copy.deepcopy(spec), "server_default": None}}
            for name, spec in expected.items()
        }
        if missing:
            del live[missing]
        if drift in ("model", "instructions", "tools"):
            live["hydro-qa-agent"]["definition"][drift] = ["old-tool"] if drift == "tools" else "old"
        connections = {}
        for name, target in (
            ("hydro-fabric-iq", f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace}/dataagents/data-agent/agent"),
            ("hydro-fabric-ontology", f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace}/items/{ontology}/ontologyEndpoint"),
        ):
            connections[name] = {
                "category": "RemoteTool", "authType": "UserEntraToken", "target": target,
                "audience": "https://api.fabric.microsoft.com", "isSharedToAll": False,
                "metadata": {"type": "fabric_iq_preview"},
            }
        if drift == "connection":
            connections["hydro-fabric-iq"]["target"] = "https://old.example/agent"
        insights = {"name": "monitor", "properties": {
            "category": "AppInsights",
            "target": f"/subscriptions/{tenant}/resourceGroups/rg/providers/microsoft.insights/components/monitor",
        }}
        deploy.FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
        deploy._workspace_artifacts.return_value = [
            {"id": "data-agent", "type": "DataAgent", "displayName": "RTI_Demo_Agent_test"},
        ]
        deploy.node24_script.return_value = ["node", "export"]
        deploy.run_capture.side_effect = lambda argv, **kwargs: json.dumps(expected) if argv == ["node", "export"] else "token"
        deploy._rebind_public_env.side_effect = lambda content, updates: content
        hosted_module = Mock(source_fingerprint=Mock(return_value="code"))
        hosted_module.ensure_hosted_agent.return_value = hosted

        def response(method, url, **kwargs):
            if "/toolboxes/" in url:
                body = {"default_version": "1"}
                if "/versions/" in url:
                    body = {"tools": [{
                        "type": "fabric_iq_preview", "name": "hydro-data", "server_label": "fabriciq-data-agent",
                        "project_connection_id": "hydro-fabric-iq", "require_approval": "never",
                    }]}
            elif url == f"https://management.azure.com{project_id}/connections?api-version=2025-10-01-preview":
                body = {"value": [
                    {"name": name, "properties": copy.deepcopy(properties)} for name, properties in connections.items()
                ] + [insights]}
            elif "/connections/" in url:
                name = url.split("/connections/", 1)[1].split("?")[0]
                if method == "PUT":
                    connections[name] = kwargs["json"]["properties"]
                body = {"properties": connections[name]}
            elif "/agents/" in url:
                name = url.split("/agents/", 1)[1].split("/")[0].split("?")[0]
                if denied:
                    return Mock(status_code=403, ok=False, text="access denied")
                if method == "POST":
                    if not broken_readback:
                        live[name] = {"version": "8", "definition": kwargs["json"]["definition"]}
                    body = {}
                elif name not in live:
                    return Mock(status_code=404, ok=False)
                else:
                    body = {"versions": {"latest": copy.deepcopy(live[name])}}
            elif f"/ontologies/{ontology}" in url:
                body = {"properties": {"generation": 2}}
            elif "/kqlDatabases/" in url:
                body = {"properties": {"queryServiceUri": "https://kusto.example.fabric.microsoft.com"}}
            else:
                raise AssertionError((method, url))
            return Mock(status_code=200, ok=True, json=lambda: body)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(module, "resolve_project", return_value=selection) as resolve, \
                patch.object(module, "_load_sibling", return_value=hosted_module), \
                patch.object(module, "read_data_agent_definition", return_value=definition(workspace=workspace, ontology=ontology)), \
                patch.object(module, "request_with_read_retry", side_effect=response) as request:
            deploy.RAYFIN_DIR = Path(directory)
            (deploy.RAYFIN_DIR / ".env").write_text("", encoding="utf-8")
            module.provision(deploy, tenant, workspace)
            resolve.assert_called_once_with(deploy, tenant, workspace, allow_create=True)
            hosted_module.ensure_hosted_agent.assert_called_once()
        return request.call_args_list

    def test_current_agents_with_server_defaults_and_connections_are_read_only(self):
        calls = self.run_provision()
        self.assertTrue(all(entry.args[0] == "GET" for entry in calls))
        agent_reads = [entry for entry in calls if "/agents/" in entry.args[1]]
        self.assertEqual(len(agent_reads), 5)
        self.assertFalse(any("/deployments" in entry.args[1] for entry in calls))

    def test_only_a_changed_specialist_gets_a_new_version(self):
        for drift in ("model", "instructions", "tools"):
            with self.subTest(drift=drift):
                calls = self.run_provision(drift=drift)
                writes = [entry for entry in calls if entry.args[0] != "GET"]
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0].args[0], "POST")
                self.assertIn("/agents/hydro-qa-agent/versions?", writes[0].args[1])

    def test_only_a_missing_agent_is_created(self):
        calls = self.run_provision(missing="hydro-weather-agent")
        writes = [entry for entry in calls if entry.args[0] != "GET"]
        self.assertEqual(len(writes), 1)
        self.assertIn("/agents/hydro-weather-agent/versions?", writes[0].args[1])

    def test_only_a_drifted_connection_is_updated(self):
        calls = self.run_provision(drift="connection")
        writes = [entry for entry in calls if entry.args[0] != "GET"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].args[0], "PUT")
        self.assertIn("/connections/hydro-fabric-iq?", writes[0].args[1])

    def test_denied_reads_and_failed_readback_do_not_report_success(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot read Foundry agent"):
            self.run_provision(denied=True)
        with self.assertRaisesRegex(RuntimeError, "definition readback failed"):
            self.run_provision(drift="instructions", broken_readback=True)


class PreparedFoundryTests(unittest.TestCase):
    def test_account_scopes_discovery_discards_other_account_hints_and_rejects_conflicting_id(self):
        subscription = str(uuid.uuid4())
        root = f"/subscriptions/{subscription}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/"
        selected = root + "selected-account/projects/shared-project"
        other = root + "other-account/projects/shared-project"
        endpoint = "https://selected-account.services.ai.azure.com/api/projects/shared-project"
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.az.side_effect = lambda *args: list(args)
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({
            "RAYFIN_PUBLIC_TENANT_ID": "tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "workspace",
            "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID": subscription,
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID": other,
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint.replace("selected-account", "other-account"),
            "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": "old-model",
        }, None)

        def capture(argv):
            if argv[:2] == ["resource", "list"]:
                return json.dumps([{"id": other}, {"id": selected}])
            if argv[:2] == ["resource", "show"]:
                self.assertEqual(argv[argv.index("--ids") + 1], selected)
                return json.dumps({"id": selected, "properties": {"endpoints": {"AI Foundry API": endpoint}}})
            raise AssertionError(argv)

        deploy.run_capture.side_effect = capture
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_ACCOUNT_NAME": "selected-account",
            "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "shared-project",
            "HYDRO_FOUNDRY_NEW_PROJECT_NAME": "", "HYDRO_FOUNDRY_CREATE": "false",
            "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": "", "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "",
            "HYDRO_FOUNDRY_MODEL": "",
        }), patch.object(module, "resolve_subscription", return_value=subscription), \
                patch.object(module, "read_model_selection", return_value="ready-chat") as model:
            result = module.resolve_project(deploy, "tenant", "workspace")
            self.assertEqual((result.project_id, result.endpoint), (selected, endpoint))
            model.assert_called_once_with(deploy, "tenant", selected, "")
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": other}):
                with self.assertRaisesRegex(RuntimeError, "does not match the Foundry account name"):
                    module.resolve_project(deploy, "tenant", "workspace")
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_ACCOUNT_NAME": "missing-account"}):
                with self.assertRaisesRegex(RuntimeError, "was not found"):
                    module.resolve_project(deploy, "tenant", "workspace")
        deploy.run_stream.assert_not_called()

    def test_named_creation_ignores_saved_target_then_reuses_only_the_named_project(self):
        subscription = str(uuid.uuid4())
        base = f"/subscriptions/{subscription}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/account/projects/"
        endpoint = "https://account.services.ai.azure.com/api/projects/new-project"
        projects = [base + "old-project"]
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.az.side_effect = lambda *args: list(args)
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({
            "RAYFIN_PUBLIC_TENANT_ID": "tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "workspace",
            "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID": subscription,
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID": base + "old-project",
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint.replace("new-project", "old-project"),
            "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": "old-model",
        }, None)
        def capture(argv):
            if argv[:2] == ["resource", "list"]:
                return json.dumps([{"id": identity} for identity in projects])
            if argv[:2] == ["resource", "show"]:
                self.assertEqual(argv[argv.index("--ids") + 1], base + "new-project")
                return json.dumps({"id": base + "new-project", "properties": {
                    "endpoints": {"AI Foundry API": endpoint},
                }})
            raise AssertionError(argv)
        deploy.run_capture.side_effect = capture
        creation = Mock()
        creation.enabled.return_value = True
        creation.model_defaults.return_value = {
            "HYDRO_FOUNDRY_MODEL_NAME": "catalog-chat", "HYDRO_FOUNDRY_MODEL_VERSION": "v1",
            "HYDRO_FOUNDRY_MODEL_SKU": "Standard", "HYDRO_FOUNDRY_MODEL_CAPACITY": "1",
        }
        def create(*args, **kwargs):
            self.assertEqual(kwargs["model"], "chosen-deployment")
            projects.append(base + "new-project")
            return endpoint
        creation.ensure_foundry_project.side_effect = create
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_NEW_PROJECT_NAME": "new-project", "HYDRO_FOUNDRY_CREATE": "true",
            "HYDRO_FOUNDRY_MODEL": "chosen-deployment",
            "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "old-project",
            "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": base + "old-project",
            "HYDRO_FOUNDRY_PROJECT_ENDPOINT": endpoint.replace("new-project", "old-project"),
        }), patch.object(module, "resolve_subscription", return_value=subscription), \
                patch.object(module, "_load_sibling", return_value=creation), \
                patch.object(module, "creation_location", return_value="region"), \
                patch.object(module, "read_model_selection", return_value="chosen-deployment"):
            preflight = module.resolve_project(deploy, "tenant", "workspace")
            self.assertEqual((preflight.project_id, preflight.model), ("", "chosen-deployment"))
            creation.ensure_foundry_project.assert_not_called()
            selected = module.resolve_project(
                deploy, "tenant", "workspace", allow_create=True, creation_settings=preflight.creation_settings,
            )
            self.assertEqual((selected.project_id, selected.endpoint), (base + "new-project", endpoint))
            rerun = module.resolve_project(deploy, "tenant", "workspace", allow_create=True)
            self.assertEqual(rerun.project_id, selected.project_id)
        creation.ensure_foundry_project.assert_called_once()
        deploy.run_stream.assert_not_called()

    def test_convention_project_is_selected_among_unrelated_projects_without_creation(self):
        from deploy_foundry_project import environment_name

        for _ in range(2):
            tenant, workspace, subscription = [str(uuid.uuid4()) for _ in range(3)]
            expected = "ai-project-" + environment_name(tenant, workspace, subscription)
            base = f"/subscriptions/{subscription}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/account/projects/"
            projects = [base + "unrelated", base + expected]
            deploy = Mock()
            deploy.DeployError = RuntimeError
            deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
            deploy.current_rayfin_target.return_value = ({}, None)
            deploy.az.side_effect = lambda *args: list(args)
            def capture(argv):
                if argv[:2] == ["resource", "list"]:
                    return json.dumps([{"id": identity} for identity in projects])
                if argv[:2] == ["resource", "show"]:
                    identity = argv[argv.index("--ids") + 1]
                    return json.dumps({"id": identity, "properties": {
                        "endpoints": {"AI Foundry API": f"https://account.services.ai.azure.com/api/projects/{identity.rsplit('/', 1)[-1]}"},
                    }})
                raise AssertionError(argv)
            deploy.run_capture.side_effect = capture
            with patch.dict(os.environ, {
                "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": "", "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "",
                "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "", "HYDRO_FOUNDRY_CREATE": "",
                "HYDRO_FOUNDRY_PROJECT_NAME": "", "HYDRO_FOUNDRY_MODEL": "",
            }), patch.object(module, "resolve_subscription", return_value=subscription), \
                    patch.object(module, "read_model_selection", return_value="ready-chat") as model:
                selection = module.resolve_project(deploy, tenant, workspace)
            self.assertEqual(selection.project_id, base + expected)
            self.assertEqual(selection.model, "ready-chat")
            model.assert_called_once_with(deploy, tenant, base + expected, "")
            deploy.run_stream.assert_not_called()

    def test_ambiguous_project_lists_simple_names_and_accepts_an_explicit_name(self):
        subscription = str(uuid.uuid4())
        base = f"/subscriptions/{subscription}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/account/projects/"
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({}, None)
        deploy.az.side_effect = lambda *args: list(args)
        def capture(argv):
            if argv[:2] == ["resource", "list"]:
                return json.dumps([{"id": base + name} for name in ("first-project", "second-project")])
            identity = argv[argv.index("--ids") + 1]
            return json.dumps({"id": identity, "properties": {"endpoints": {
                "AI Foundry API": "https://account.services.ai.azure.com/api/projects/" + identity.rsplit("/", 1)[-1],
            }}})
        deploy.run_capture.side_effect = capture
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": "", "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "",
            "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "", "HYDRO_FOUNDRY_CREATE": "",
        }), patch.object(module, "resolve_subscription", return_value=subscription), \
                patch.object(module, "read_model_selection", return_value="chat"):
            with self.assertRaisesRegex(RuntimeError, "Available projects:[\\s\\S]*first-project[\\s\\S]*second-project"):
                module.resolve_project(deploy, "tenant", "workspace")
            deploy.run_capture.reset_mock()
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "second-project"}):
                selected = module.resolve_project(deploy, "tenant", "workspace")
                self.assertEqual(selected.project_id, base + "second-project")
                reads = [entry.args[0] for entry in deploy.run_capture.call_args_list
                         if entry.args[0][:2] == ["resource", "show"]]
                self.assertEqual(len(reads), 1)
                self.assertIn(base + "second-project", reads[0])
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "missing"}):
                with self.assertRaisesRegex(RuntimeError, "no replacement was created"):
                    module.resolve_project(deploy, "tenant", "workspace")
        deploy.run_stream.assert_not_called()

    def test_named_missing_project_can_be_prepared_only_with_creation_approval(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({}, None)
        deploy.az.side_effect = lambda *args: list(args)
        deploy.run_capture.return_value = "[]"
        creation = Mock()
        creation.enabled.return_value = True
        creation.model_defaults.return_value = {"HYDRO_FOUNDRY_MODEL_VERSION": "current"}
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": "", "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "",
            "HYDRO_FOUNDRY_CREATE": "true", "HYDRO_FOUNDRY_NEW_PROJECT_NAME": "",
            "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "requested-project",
            "HYDRO_FOUNDRY_MODEL": "requested-deployment",
        }), patch.object(module, "resolve_subscription", return_value="selected-sub"), \
                patch.object(module, "_load_sibling", return_value=creation), \
                patch.object(module, "creation_location", return_value="targetregion"):
            selected = module.resolve_project(deploy, "tenant", "workspace")
        self.assertEqual(selected.model, "requested-deployment")
        self.assertEqual(selected.creation_settings["location"], "targetregion")
        creation.model_defaults.assert_called_once_with(
            deploy, subscription="selected-sub", location="targetregion", model="requested-deployment",
        )
        creation.ensure_foundry_project.assert_not_called()
        deploy.run_stream.assert_not_called()

    def test_model_preflight_is_read_only_and_rejects_untrusted_pagination(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.run_capture.return_value = "access"
        project_id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/account/projects/project"
        ready = {"name": "chosen-chat", "properties": {
            "provisioningState": "Succeeded", "model": {"format": "OpenAI"},
            "capabilities": {"chatCompletion": "true"},
            "rateLimits": [{"key": "token", "count": 75000, "renewalPeriod": 60}],
        }}
        with patch.object(module, "request_with_read_retry", return_value=Mock(
            ok=True, json=lambda: {"value": [ready]},
        )) as request:
            self.assertEqual(module.read_model_selection(deploy, "tenant", project_id, ""), "chosen-chat")
            self.assertEqual(request.call_args.args[0], "GET")
        with patch.object(module, "request_with_read_retry", return_value=Mock(
            ok=True, json=lambda: {"value": [], "nextLink": "https://untrusted.test/token"},
        )) as request:
            with self.assertRaisesRegex(RuntimeError, "pagination"):
                module.read_model_selection(deploy, "tenant", project_id, "")
            request.assert_called_once()
        deploy.run_stream.assert_not_called()

    def test_explicit_model_is_verified_directly_without_listing_other_deployments(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.run_capture.return_value = "access"
        project_id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/account/projects/project"
        ready = {"name": "chosen-chat", "properties": {
            "provisioningState": "Succeeded", "model": {"format": "OpenAI"},
            "capabilities": {"chatCompletion": "true"},
            "rateLimits": [{"key": "token", "count": 75000, "renewalPeriod": 60}],
        }}
        with patch.object(module, "request_with_read_retry", return_value=Mock(
            ok=True, json=lambda: ready,
        )) as request:
            self.assertEqual(module.read_model_selection(deploy, "tenant", project_id, "chosen-chat"), "chosen-chat")
            request.assert_called_once()
            self.assertEqual(request.call_args.args[:2], (
                "GET", "https://management.azure.com" + project_id.rsplit("/projects/", 1)[0]
                + "/deployments/chosen-chat?api-version=2025-06-01",
            ))
            ready["name"] = "unrelated-chat"
            with self.assertRaisesRegex(RuntimeError, "enter one ready chat model"):
                module.read_model_selection(deploy, "tenant", project_id, "chosen-chat")
            request.reset_mock()
            request.return_value.ok = False
            request.return_value.status_code = 404
            with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                module.read_model_selection(deploy, "tenant", project_id, "chosen-chat")
            request.assert_called_once()

    def test_model_preflight_accepts_lower_capacity_but_rejects_unverifiable_limits_without_mutation(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.run_capture.return_value = "access"
        project_id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/account/projects/project"
        ready = {"name": "chosen-chat", "properties": {
            "provisioningState": "Succeeded", "model": {"format": "OpenAI"},
            "capabilities": {"chatCompletion": "true"},
        }}
        with patch.object(module, "request_with_read_retry", return_value=Mock(
            ok=True, json=lambda: ready,
        )) as request:
            for limits in ([], [{"key": "request", "count": 100, "renewalPeriod": 60}],
                           [{"key": "token", "count": True, "renewalPeriod": 60}],
                           [{"key": "token", "count": float("nan"), "renewalPeriod": 60}],
                           [{"key": "token", "count": float("inf"), "renewalPeriod": 60}],
                           [{"key": "token", "count": 40000, "renewalPeriod": 0}]):
                ready["properties"]["rateLimits"] = limits
                with self.subTest(limits=limits), self.assertRaisesRegex(RuntimeError, "no verifiable token"):
                    module.read_model_selection(deploy, "tenant", project_id, "chosen-chat")
            for count, period in ((10000, 60), (40000, 60), (74999, 60), (149999, 120)):
                ready["properties"]["rateLimits"] = [{"key": "token", "count": count, "renewalPeriod": period}]
                with self.subTest(count=count), patch("builtins.print") as output:
                    self.assertEqual(module.read_model_selection(deploy, "tenant", project_id, "chosen-chat"), "chosen-chat")
                    self.assertIn("Continuing with the available allocation", str(output.call_args_list))
            for count, period in ((75000, 60), (150000, 120), (150000, 60)):
                ready["properties"]["rateLimits"] = [{"key": "token", "count": count, "renewalPeriod": period}]
                self.assertEqual(module.read_model_selection(deploy, "tenant", project_id, "chosen-chat"), "chosen-chat")
            self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
        deploy.run_stream.assert_not_called()

    def prepared(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.APP_DIR = Path(__file__).resolve().parents[2] / "HydroOperationsApp"
        deploy.REPO_ROOT = deploy.APP_DIR.parent
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        tenant, workspace, ontology = [f"{i:08x}-1111-4111-8111-{i:012x}" for i in range(1, 4)]
        values = {
            "RAYFIN_PUBLIC_TENANT_ID": tenant, "RAYFIN_PUBLIC_WORKSPACE_ID": workspace,
            "RAYFIN_PUBLIC_ONTOLOGY_ID": ontology,
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": "https://hydro.services.ai.azure.com/api/projects/hydro",
            "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": "model",
            "RAYFIN_PUBLIC_ORCHESTRATOR_CODE_DIGEST": "code",
            "RAYFIN_PUBLIC_ORCHESTRATOR_VERSION": "7",
            "RAYFIN_PUBLIC_ORCHESTRATOR_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "RAYFIN_PUBLIC_API_URL": "https://" + "a" * 32 + ".pbidedicated.windows.net",
            "RAYFIN_PUBLIC_PUBLISHABLE_KEY": "pk-test",
        }
        for index, key in enumerate((
            "RAYFIN_PUBLIC_EVENTHOUSE_ID", "RAYFIN_PUBLIC_KQL_DATABASE_ID", "RAYFIN_PUBLIC_LAKEHOUSE_ID",
            "RAYFIN_PUBLIC_ITEM_ID", "RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID",
        ), 4):
            values[key] = f"{index:08x}-1111-4111-8111-{index:012x}"
        digest = module.source_configuration_digest(tenant, workspace, ontology, values)
        values["RAYFIN_PUBLIC_ORCHESTRATOR_SOURCE_DIGEST"] = digest
        source = {"tenant_id": tenant, "workspace_id": workspace, "ontology_id": ontology,
                  "generation": 2, "configuration_digest": digest}
        agent = {"name": "hydro-orchestrator", "versions": {"latest": {
            "version": "7", "status": "active", "definition": {
                "kind": "hosted", "container_configuration": {"image": "registry/image@sha256:" + "a" * 64},
                "environment_variables": {"HYDRO_ORCHESTRATOR_CONFIG": json.dumps({
                    "source": source,
                    "native_binding": {"source": source, "connections": {"data-agent": "hydro-fabric-iq"},
                                       "toolbox": "hydro-fabric-iq-tools"},
                })},
            },
        }}}
        values["RAYFIN_PUBLIC_FOUNDRY_INVOCATIONS_URL"] = module.hosted_invocations_url(
            agent, values["RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT"],
        )
        deploy.current_rayfin_target.return_value = values, None
        deploy.fabric_get.return_value = {"properties": {"generation": 2}}
        deploy._published_operational_source_ready.return_value = True
        deploy.run_capture.side_effect = ["token", json.dumps({"hydro-weather-agent": {"model": "model"}})]
        return deploy, values, agent, tenant, workspace

    def test_app_verifies_prepared_agents_using_get_only(self):
        deploy, _, agent, tenant, workspace = self.prepared()
        hosted = Mock(source_fingerprint=Mock(return_value="code"))
        responses = [
            Mock(ok=True, json=lambda: {"versions": {"latest": {"definition": {"model": "model"}}}}),
            Mock(ok=True, json=lambda: agent),
        ]
        with patch.object(module, "_load_sibling", return_value=hosted), \
                patch.object(module, "verify_prepared_connections") as connections, \
                patch.object(module, "request_with_read_retry", side_effect=responses) as request, \
                patch("builtins.print") as output:
            module.verify_existing(deploy, tenant, workspace)
        connections.assert_called_once()
        deploy._published_operational_source_ready.assert_called_once()
        self.assertIs(
            connections.call_args.kwargs["definition_cache"],
            deploy._published_operational_source_ready.call_args.kwargs["definition_cache"],
        )
        self.assertEqual(connections.call_args.kwargs["foundry_headers"], {"Authorization": "Bearer token"})
        self.assertEqual([call.args[0] for call in request.call_args_list], ["GET", "GET"])
        self.assertEqual([call.args[:2] for call in deploy.az.call_args_list], [("account", "get-access-token")])
        self.assertNotIn("WARNING", str(output.call_args_list))

    def test_app_reuses_compatible_runtime_with_older_or_unknown_packaged_code(self):
        for code_digest in ("old", ""):
            with self.subTest(code_digest=code_digest):
                deploy, values, agent, tenant, workspace = self.prepared()
                values["RAYFIN_PUBLIC_ORCHESTRATOR_CODE_DIGEST"] = code_digest
                hosted = Mock(source_fingerprint=Mock(return_value="code"))
                responses = [
                    Mock(ok=True, json=lambda: {"versions": {"latest": {"definition": {"model": "model"}}}}),
                    Mock(ok=True, json=lambda: agent),
                ]
                with patch.object(module, "_load_sibling", return_value=hosted), \
                        patch.object(module, "verify_prepared_connections") as connections, \
                        patch.object(module, "request_with_read_retry", side_effect=responses) as request, \
                        patch("builtins.print") as output:
                    module.verify_existing(deploy, tenant, workspace)
                connections.assert_called_once()
                deploy._published_operational_source_ready.assert_called_once()
                self.assertEqual([item.args[0] for item in request.call_args_list], ["GET", "GET"])
                self.assertIn("new agent features are not certified", str(output.call_args_list))
                self.assertIn("setup module separately", str(output.call_args_list))
                deploy.run_stream.assert_not_called()
                deploy.write_env_values.assert_not_called()

    def test_stale_sources_and_specialists_fail_even_with_older_packaged_code(self):
        for stale in ("source", "specialist", "hosted"):
            with self.subTest(stale=stale):
                deploy, values, agent, tenant, workspace = self.prepared()
                values["RAYFIN_PUBLIC_ORCHESTRATOR_CODE_DIGEST"] = "old"
                if stale == "source":
                    values["RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID"] = workspace
                if stale == "hosted":
                    agent["versions"]["latest"]["version"] = "8"
                response = Mock(ok=True, json=lambda: {
                    "versions": {"latest": {"definition": {"model": "old" if stale == "specialist" else "model"}}},
                })
                with patch.object(module, "_load_sibling", return_value=Mock(
                    source_fingerprint=Mock(return_value="code"),
                )), patch.object(module, "verify_prepared_connections"), \
                        patch.object(module, "request_with_read_retry", side_effect=[
                    response, Mock(ok=True, json=lambda: agent),
                ]) as request:
                    expected_error = {
                        "source": "source identities changed",
                        "specialist": "specialist .* is stale",
                        "hosted": "Hosted runtime identity changed",
                    }[stale]
                    with self.assertRaisesRegex(RuntimeError, expected_error):
                        module.verify_existing(deploy, tenant, workspace)
                    self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_older_code_does_not_mask_missing_runtime_or_broken_connections(self):
        for failure in ("missing-agent", "connection"):
            with self.subTest(failure=failure):
                deploy, values, _, tenant, workspace = self.prepared()
                values["RAYFIN_PUBLIC_ORCHESTRATOR_CODE_DIGEST"] = "old"
                with patch.object(module, "_load_sibling") as load, \
                        patch.object(module, "verify_prepared_connections",
                                     side_effect=RuntimeError("source/authentication mismatch")
                                     if failure == "connection" else None), \
                        patch.object(module, "request_with_read_retry",
                                     return_value=Mock(ok=False, status_code=404)) as request, \
                        patch("builtins.print") as output:
                    expected = "source/authentication mismatch" if failure == "connection" else "Cannot verify Foundry agent"
                    with self.assertRaisesRegex(RuntimeError, expected):
                        module.verify_existing(deploy, tenant, workspace)
                load.assert_not_called()
                self.assertNotIn("new agent features", str(output.call_args_list))
                self.assertTrue(all(item.args[0] == "GET" for item in request.call_args_list))
                deploy.run_stream.assert_not_called()

    def test_connection_readback_rejects_cross_workspace_targets_before_reading_their_agent(self):
        for wrong_workspace in (False, True):
            with self.subTest(wrong_workspace=wrong_workspace):
                deploy, values, _, tenant, workspace = self.prepared()
                deploy.GUID_RE = re.compile(r"[0-9a-f-]{36}")
                agent_id = "00000009-1111-4111-8111-000000000009"
                project_id = f"/subscriptions/{tenant}/resourceGroups/hydro/providers/Microsoft.CognitiveServices/accounts/ai/projects/hydro"
                values["RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID"] = project_id
                values["RAYFIN_PUBLIC_FOUNDRY_DATA_AGENT_ID"] = agent_id
                deploy.run_capture.side_effect = None
                deploy.run_capture.return_value = "token"
                def respond(method, url, **kwargs):
                    if "/toolboxes/" in url:
                        body = {"default_version": "1"}
                        if "/versions/" in url:
                            body = {"tools": [{
                                "type": "fabric_iq_preview", "name": "hydro-data",
                                "server_label": "fabriciq-data-agent",
                                "project_connection_id": "hydro-fabric-iq", "require_approval": "never",
                            }]}
                        return Mock(ok=True, status_code=200, json=lambda: body)
                    props = {"endpoints": {"AI Foundry API": values["RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT"]}}
                    if "/connections/" in url:
                        target_workspace = tenant if wrong_workspace else workspace
                        target = (f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{target_workspace}/dataagents/{agent_id}/agent"
                                  if "/hydro-fabric-iq?" in url else
                                  f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace}/items/"
                                  f"{values['RAYFIN_PUBLIC_ONTOLOGY_ID']}/ontologyEndpoint")
                        props = {"category": "RemoteTool", "authType": "UserEntraToken", "target": target,
                                 "audience": "https://api.fabric.microsoft.com", "isSharedToAll": False,
                                 "metadata": {"type": "fabric_iq_preview"}}
                    return Mock(ok=True, json=lambda: {"properties": props})
                ontology = values["RAYFIN_PUBLIC_ONTOLOGY_ID"]
                with patch.object(module, "request_with_read_retry", side_effect=respond) as request, \
                        patch.object(module, "read_data_agent_definition", return_value=definition(
                            workspace=workspace, ontology=ontology,
                        )) as read_agent, patch.object(module, "published_sql_source_ready",
                            side_effect=AssertionError("Ontology-associated SQL needs no separate profile")):
                    if wrong_workspace:
                        with self.assertRaisesRegex(RuntimeError, "no longer matches"):
                            module.verify_prepared_connections(deploy, tenant, workspace, ontology, values)
                        read_agent.assert_not_called()
                    else:
                        module.verify_prepared_connections(deploy, tenant, workspace, ontology, values)
                        read_agent.assert_called_once()
                        deploy._published_operational_source_ready.assert_not_called()
                    self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))


class PublishedIdentityTests(unittest.TestCase):
    def test_prepared_project_uses_direct_read_without_subscription_discovery(self):
        subscription = "11111111-2222-3333-4444-555555555555"
        project_id = f"/subscriptions/{subscription}/resourceGroups/hydro/providers/Microsoft.CognitiveServices/accounts/hydro/projects/demo"
        endpoint = "https://hydro.services.ai.azure.com/api/projects/demo"
        for explicit in (False, True, "different-prepared-project", "matching-name"):
            with self.subTest(explicit=explicit):
                deploy = Mock()
                deploy.DeployError = RuntimeError
                deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
                deploy.az.side_effect = lambda *args: list(args)
                deploy.current_rayfin_target.return_value = ({
                    "RAYFIN_PUBLIC_TENANT_ID": "tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "workspace",
                    "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID": subscription,
                    "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID": project_id,
                    "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint,
                } if not explicit or explicit == "matching-name" else {
                    "RAYFIN_PUBLIC_TENANT_ID": "tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "workspace",
                    "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID": project_id + "-previous",
                    "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint + "-previous",
                } if explicit == "different-prepared-project" else {}, None)
                def capture(arguments):
                    if arguments[:2] == ["resource", "show"]:
                        return json.dumps({"id": project_id, "properties": {"endpoints": {"AI Foundry API": endpoint}}})
                    raise RuntimeError("direct project verified; stop before provisioning")
                deploy.run_capture.side_effect = capture
                with patch.dict(os.environ, {
                    "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": project_id if explicit and explicit != "matching-name" else "",
                    "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "", "HYDRO_AZURE_SUBSCRIPTION_ID": "",
                    "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "demo" if explicit == "matching-name" else "",
                }), patch.object(module, "resolve_subscription", return_value=subscription) as resolve, \
                        patch.object(module, "_load_sibling"):
                    with self.assertRaisesRegex(RuntimeError, "direct project verified"):
                        module.provision(deploy, "tenant", "workspace")
                resolve.assert_called_once_with(deploy, "tenant", subscription)
                self.assertEqual(deploy.run_capture.call_args_list[0].args[0][:2], ["resource", "show"])
                self.assertFalse(any(call.args[0][:2] == ["resource", "list"]
                                     for call in deploy.run_capture.call_args_list))

    def test_direct_project_mismatch_fails_without_discovery_or_creation(self):
        subscription = "11111111-2222-3333-4444-555555555555"
        project_id = f"/subscriptions/{subscription}/resourceGroups/hydro/providers/Microsoft.CognitiveServices/accounts/hydro/projects/demo"
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({}, None)
        deploy.run_capture.return_value = json.dumps({"id": project_id, "properties": {"endpoints": {
            "AI Foundry API": "https://other.services.ai.azure.com/api/projects/other",
        }}})
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": project_id,
            "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "https://selected.services.ai.azure.com/api/projects/demo",
        }), patch.object(module, "resolve_subscription", return_value=subscription), \
                patch.object(module, "_load_sibling") as load:
            with self.assertRaisesRegex(RuntimeError, "refusing project discovery"):
                module.provision(deploy, "tenant", "workspace")
        deploy.run_capture.assert_called_once()
        load.assert_not_called()

    def test_prepared_subscription_is_verified_without_using_current_cli_default(self):
        subscription = "11111111-2222-3333-4444-555555555555"
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.GUID_RE = re.compile(r"[0-9a-f-]{36}")
        deploy.az.side_effect = lambda *args: list(args)
        deploy.run_capture.return_value = json.dumps({"id": subscription, "tenantId": "tenant", "state": "Enabled"})
        with patch.dict(os.environ, {"HYDRO_AZURE_SUBSCRIPTION_ID": ""}):
            self.assertEqual(module.resolve_subscription(deploy, "tenant", subscription), subscription)
        deploy.run_capture.assert_called_once_with(["account", "show", "--subscription", subscription, "-o", "json"])

    def test_published_sql_requires_target_identity_and_all_selected_operational_tables(self):
        tables = ["WorkOrders", "MaintenanceNotifications", "Inspections", "SpareParts", "Asset3DModels"]
        source = {"type": "sql_database", "workspaceId": "workspace", "artifactId": "sql",
                  "elements": [{"type": "sql_database.schema", "children": [
                      {"type": "sql_database.table", "display_name": name, "is_selected": True, "children": []}
                      for name in tables]}]}

        def encoded(stage="published"):
            return {"definition": {"parts": [{
                "path": f"Files/Config/{stage}/sql-database-app/datasource.json",
                "payload": base64.b64encode(json.dumps(source).encode()).decode(),
            }]}}

        self.assertTrue(module.published_sql_source_ready(encoded(), "workspace", "sql"))
        self.assertFalse(module.published_sql_source_ready(encoded("draft"), "workspace", "sql"))
        self.assertFalse(module.published_sql_source_ready(encoded(), "another", "sql"))
        self.assertFalse(module.published_sql_source_ready(encoded(), "workspace", "another"))
        source["elements"][0]["children"][0]["is_selected"] = False
        self.assertFalse(module.published_sql_source_ready(encoded(), "workspace", "sql"))
        with self.assertRaises(ValueError):
            module.published_sql_source_ready({"definition": {"parts": [{
                "path": "Files/Config/published/sql-database-app/datasource.json", "payload": "not-base64",
            }]}}, "workspace", "sql")

    def test_missing_explicit_project_never_creates_a_different_project(self):
        endpoint = "https://selected.services.ai.azure.com/api/projects/hydro"
        for saved in (False, True):
            with self.subTest(saved=saved):
                deploy = Mock()
                deploy.DeployError = RuntimeError
                deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
                deploy.current_rayfin_target.return_value = (
                    {"RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint,
                     "RAYFIN_PUBLIC_TENANT_ID": "tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "workspace",
                     "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID": "subscription"} if saved else {}, None,
                )
                deploy.run_capture.return_value = "[]"
                with patch.dict(os.environ, {
                    "HYDRO_FOUNDRY_PROJECT_ENDPOINT": endpoint,
                    "HYDRO_FOUNDRY_CREATE": "true",
                }), patch.object(module, "resolve_subscription", return_value="subscription"), \
                     patch.object(module, "_load_sibling") as load:
                    with self.assertRaisesRegex(RuntimeError, "refusing to create"):
                        module.provision(deploy, "tenant", "workspace")
                load.assert_not_called()

    def test_saved_endpoint_from_another_workspace_is_not_a_selection_hint(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({
            "RAYFIN_PUBLIC_TENANT_ID": "old-tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "old-workspace",
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": "https://old.services.ai.azure.com/api/projects/old",
            "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": "old-model",
        }, None)
        deploy.run_capture.return_value = "[]"
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_PROJECT_ENDPOINT": "", "HYDRO_FOUNDRY_CREATE": ""}), \
             patch.object(module, "resolve_subscription", return_value="selected-subscription"):
            with self.assertRaisesRegex(RuntimeError, "Select exactly one existing Foundry project"):
                module.provision(deploy, "new-tenant", "new-workspace")

    def test_missing_legacy_saved_endpoint_is_discarded_before_fresh_target_discovery(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({
            "RAYFIN_PUBLIC_TENANT_ID": "target-tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": "target-workspace",
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": "https://old.services.ai.azure.com/api/projects/old",
            "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": "old-model",
        }, None)
        deploy.run_capture.return_value = "[]"
        with patch.dict(os.environ, {"HYDRO_FOUNDRY_PROJECT_ENDPOINT": "", "HYDRO_FOUNDRY_CREATE": ""}), \
             patch.object(module, "resolve_subscription", return_value="new-subscription"):
            with self.assertRaisesRegex(RuntimeError, "Select exactly one existing Foundry project"):
                module.provision(deploy, "target-tenant", "target-workspace")
        self.assertEqual(deploy.run_capture.call_count, 2)

    def test_subscription_is_verified_in_target_tenant_without_changing_cli_default(self):
        subscription = "11111111-2222-3333-4444-555555555555"
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy.GUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
        deploy.az.side_effect = lambda *args: list(args)
        deploy.run_capture.return_value = json.dumps({"id": subscription, "tenantId": "target", "state": "Enabled"})
        with patch.dict(os.environ, {"HYDRO_AZURE_SUBSCRIPTION_ID": subscription}):
            self.assertEqual(module.resolve_subscription(deploy, "target"), subscription)
        deploy.run_capture.assert_called_once_with(["account", "show", "--subscription", subscription, "-o", "json"])
        for overrides in ({"tenantId": "other"}, {"state": "Disabled"}, {"id": ""}):
            deploy.run_capture.return_value = json.dumps({
                "id": subscription, "tenantId": "target", "state": "Enabled", **overrides,
            })
            with self.subTest(overrides=overrides), patch.dict(os.environ, {"HYDRO_AZURE_SUBSCRIPTION_ID": ""}):
                with self.assertRaisesRegex(RuntimeError, "target Fabric tenant"):
                    module.resolve_subscription(deploy, "target")

    def test_model_selection_uses_live_capability_or_explicit_deployment_never_a_fixed_name(self):
        first = {"name": "tenant-specific-chat", "properties": {
            "provisioningState": "Succeeded", "model": {"format": "OpenAI"},
            "capabilities": {"chatCompletion": "true"},
        }}
        embedding = {"name": "embedding", "properties": {
            "provisioningState": "Succeeded", "model": {"format": "OpenAI"},
            "capabilities": {"embeddings": "true"},
        }}
        self.assertEqual(module.select_model_deployment([first, embedding], ""), first["name"])
        second = {**first, "name": "other-chat"}
        self.assertEqual(module.select_model_deployment([first, second], second["name"]), second["name"])
        for deployments, wanted in (([], ""), ([embedding], ""), ([first, second], ""), ([first], "missing"),
                                    ([{**first, "properties": {"provisioningState": "Failed"}}], first["name"])):
            with self.subTest(wanted=wanted, deployments=deployments):
                with self.assertRaisesRegex(RuntimeError, "HYDRO_FOUNDRY_MODEL"):
                    module.select_model_deployment(deployments, wanted)

    def test_opted_in_target_creation_does_not_reuse_an_unrelated_existing_project(self):
        deploy = Mock()
        deploy.DeployError = RuntimeError
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({}, None)
        project_id = "/subscriptions/selected/resourceGroups/old/providers/Microsoft.CognitiveServices/accounts/old/projects/old"
        deploy.run_capture.side_effect = [
            json.dumps([{"id": project_id}]),
            json.dumps({"id": project_id, "properties": {"endpoints": {
                "AI Foundry API": "https://old.services.ai.azure.com/api/projects/old",
            }}}),
        ]
        creation, hosted = Mock(), Mock()
        creation.enabled.return_value = True
        creation.normalize_location.side_effect = lambda location: location
        creation.ensure_foundry_project.side_effect = RuntimeError("target creation reached")
        with patch.dict(os.environ, {
            "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "", "HYDRO_FOUNDRY_LOCATION": "targetregion",
            "HYDRO_FOUNDRY_MODEL": "target-model",
        }), patch.object(module, "resolve_subscription", return_value="selected"), \
             patch.object(module, "_load_sibling", side_effect=[creation, hosted]):
            with self.assertRaisesRegex(RuntimeError, "target creation reached"):
                module.provision(deploy, "target-tenant", "target-workspace")
        arguments = creation.ensure_foundry_project.call_args.kwargs
        self.assertEqual((arguments["tenant"], arguments["workspace"], arguments["subscription"], arguments["model"]),
                         ("target-tenant", "target-workspace", "selected", "target-model"))

    def test_transient_get_failures_retry_with_bounded_backoff(self):
        response = Mock(status_code=200)
        with patch.object(module.requests, "request", side_effect=[
            requests.ConnectionError("DNS unavailable"), requests.Timeout("read timed out"), response,
        ]) as request, patch.object(module.time, "sleep") as sleep:
            self.assertIs(module.request_with_read_retry("GET", "https://example.test/agents", timeout=60), response)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(sleep.call_args_list, [call(2), call(4)])

    def test_exhausted_read_failure_propagates(self):
        error = requests.ConnectionError("DNS unavailable")
        with patch.object(module.requests, "request", side_effect=error) as request, patch.object(module.time, "sleep") as sleep:
            with self.assertRaises(requests.ConnectionError) as caught:
                module.request_with_read_retry("GET", "https://example.test/agents", timeout=60)
            self.assertIs(caught.exception, error)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(sleep.call_args_list, [call(2), call(4), call(8)])

    def test_mutations_and_certificate_failures_are_never_retried(self):
        for method, error in [
            ("POST", requests.ConnectionError("unknown write outcome")),
            ("PUT", requests.Timeout("unknown write outcome")),
            ("GET", requests.exceptions.SSLError("invalid certificate")),
        ]:
            with self.subTest(method=method, error=type(error).__name__):
                with patch.object(module.requests, "request", side_effect=error) as request, patch.object(module.time, "sleep") as sleep:
                    with self.assertRaises(type(error)):
                        module.request_with_read_retry(method, "https://example.test/agents", timeout=60)
                    self.assertEqual(request.call_count, 1)
                    sleep.assert_not_called()

    def test_http_errors_are_not_retried_or_hidden(self):
        for status in (401, 403, 404, 429, 500):
            response = Mock(status_code=status)
            with patch.object(module.requests, "request", return_value=response) as request, patch.object(module.time, "sleep") as sleep:
                self.assertIs(module.request_with_read_retry("GET", "https://example.test/agents", timeout=60), response)
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_linked_insights_identity_is_required_and_never_inferred_by_name(self):
        resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/demo/providers/Microsoft.Insights/components/demo"
        connection = {"properties": {"category": "AppInsights", "target": resource_id}}
        self.assertEqual(module.linked_application_insights([connection]), resource_id)
        for invalid in [
            [],
            [{"properties": {"category": "RemoteTool", "target": resource_id}}],
            [{"properties": {"category": "AppInsights", "target": "https://untrusted.example"}}],
            [connection, {"properties": {"category": "AppInsights", "target": resource_id + "-other"}}],
        ]:
            with self.assertRaises(RuntimeError):
                module.linked_application_insights(invalid)

    def test_hosted_invocations_url_requires_active_named_runtime(self):
        endpoint = "https://demo.services.ai.azure.com/api/projects/hydro"
        agent = {
            "name": "hydro-orchestrator",
            "versions": {"latest": {"status": "active", "definition": {"kind": "hosted"}}},
        }
        self.assertEqual(
            module.hosted_invocations_url(agent, endpoint),
            endpoint + "/agents/hydro-orchestrator/endpoint/protocols/invocations?api-version=v1",
        )
        for invalid in [
            {**agent, "name": "other"},
            {**agent, "versions": {"latest": {"status": "inactive", "definition": {"kind": "hosted"}}}},
            {**agent, "versions": {"latest": {"status": "active", "definition": {"kind": "prompt"}}}},
        ]:
            with self.assertRaises(RuntimeError):
                module.hosted_invocations_url(invalid, endpoint)
        with self.assertRaises(RuntimeError):
            module.hosted_invocations_url(agent, "http://untrusted.example")

    def test_hosted_runtime_identity_requires_immutable_digest(self):
        agent = {"versions": {"latest": {
            "version": "7",
            "definition": {"container_configuration": {
                "image": f"demo.azurecr.io/hydro-orchestrator@sha256:{'a' * 64}",
            }},
        }}}
        self.assertEqual(module.hosted_runtime_identity(agent), ("7", f"sha256:{'a' * 64}"))
        for invalid in [
            {},
            {"versions": {"latest": {**agent["versions"]["latest"], "version": "candidate"}}},
            {"versions": {"latest": {**agent["versions"]["latest"], "definition": {
                "container_configuration": {"image": "demo.azurecr.io/hydro-orchestrator:latest"},
            }}}},
        ]:
            with self.assertRaises(RuntimeError):
                module.hosted_runtime_identity(invalid)

    def test_source_digest_matches_runtime_configuration_contract(self):
        ids = [f"{index:08x}-1111-4111-8111-{index:012x}" for index in range(1, 9)]
        values = {
            "RAYFIN_PUBLIC_EVENTHOUSE_ID": ids[3],
            "RAYFIN_PUBLIC_KQL_DATABASE_ID": ids[4],
            "RAYFIN_PUBLIC_LAKEHOUSE_ID": ids[5],
            "RAYFIN_PUBLIC_ITEM_ID": ids[6],
            "RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID": ids[7],
            "RAYFIN_PUBLIC_API_URL": f"https://{'a' * 32}.pbidedicated.windows.net/workload",
            "RAYFIN_PUBLIC_PUBLISHABLE_KEY": "pk-runtime-contract",
        }
        config = {
            "tenant_id": ids[0], "workspace_id": ids[1], "ontology_id": ids[2],
            "eventhouse_id": ids[3], "database_id": ids[4], "lakehouse_id": ids[5],
            "appbackend_id": ids[6], "operational_sql_database_id": ids[7], "api_url": values["RAYFIN_PUBLIC_API_URL"],
            "publishable_key": values["RAYFIN_PUBLIC_PUBLISHABLE_KEY"],
        }
        expected = hashlib.sha256(json.dumps(config, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(module.source_configuration_digest(ids[0], ids[1], ids[2], values), expected)
        for key in values:
            with self.subTest(missing=key):
                invalid = {**values, key: ""}
                with self.assertRaises(RuntimeError):
                    module.source_configuration_digest(ids[0], ids[1], ids[2], invalid)

    def test_active_hosted_source_must_match_selected_deployment(self):
        tenant = "00000001-1111-4111-8111-000000000001"
        workspace = "00000002-1111-4111-8111-000000000002"
        ontology = "00000003-1111-4111-8111-000000000003"
        digest = "a" * 64
        source = {
            "tenant_id": tenant, "workspace_id": workspace, "ontology_id": ontology,
            "generation": 2, "configuration_digest": digest,
        }
        native_binding = {"source": source, "connections": {"data-agent": "hydro-fabric-iq"},
                          "toolbox": "hydro-fabric-iq-tools"}
        agent = {"versions": {"latest": {"definition": {"environment_variables": {
            "HYDRO_ORCHESTRATOR_CONFIG": json.dumps({"source": source, "native_binding": native_binding}),
        }}}}}
        module.verify_hosted_source(agent, tenant, workspace, ontology, digest)
        for invalid in [
            {},
            {"versions": {"latest": {"definition": {"environment_variables": {
                "HYDRO_ORCHESTRATOR_CONFIG": "not-json",
            }}}}},
            {"versions": {"latest": {"definition": {"environment_variables": {
                "HYDRO_ORCHESTRATOR_CONFIG": json.dumps({
                    "source": {**source, "workspace_id": tenant}, "native_binding": native_binding,
                }),
            }}}}},
            {"versions": {"latest": {"definition": {"environment_variables": {
                "HYDRO_ORCHESTRATOR_CONFIG": json.dumps({
                    "source": source,
                    "native_binding": {
                        "source": source,
                        "connections": {
                            "data-agent": "hydro-fabric-iq", "ontology": "hydro-fabric-ontology",
                        },
                    },
                }),
            }}}}},
        ]:
            with self.assertRaises(RuntimeError):
                module.verify_hosted_source(invalid, tenant, workspace, ontology, digest)

    def test_regional_definition_operations_are_polled_on_canonical_origin(self):
        path = "/v1/operations/46823fb9-2ddd-4c7b-8089-068435f979f0"
        for host in ["api.fabric.microsoft.com", "wabi-us-central-b-primary-redirect.analysis.windows.net"]:
            self.assertEqual(module.definition_operation_url("https://" + host + path), "https://api.fabric.microsoft.com" + path)
        for invalid in ["https://example.org" + path, "http://api.fabric.microsoft.com" + path,
                        "https://api.fabric.microsoft.com/not-an-operation"]:
            with self.assertRaises(RuntimeError):
                module.definition_operation_url(invalid)

    def test_exact_published_v2_identity_required(self):
        module.verify_published_identity(definition(), "workspace", "ontology")
        for invalid in [definition(stage="draft"), definition(workspace="other"), definition(ontology="other")]:
            with self.assertRaises(RuntimeError):
                module.verify_published_identity(invalid, "workspace", "ontology")

    def test_published_ontology_context_identity_without_separate_source_profiles(self):
        def context(stage="published", workspace="workspace", ontology="ontology"):
            return {"definition": {"parts": [{
                "path": f"Files/Config/{stage}/ontologies/model/ontology.json",
                "payload": base64.b64encode(json.dumps({
                    "workspaceId": workspace, "artifactId": ontology,
                    "elements": [{"id": "facilities", "is_selected": True}],
                }).encode()).decode(),
            }]}}

        module.verify_published_identity(context(), "workspace", "ontology")
        for invalid in (context(stage="draft"), context(workspace="other"), context(ontology="other")):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                module.verify_published_identity(invalid, "workspace", "ontology")
        mixed = context()
        mixed["definition"]["parts"] += definition(ontology="other")["definition"]["parts"]
        with self.assertRaises(RuntimeError):
            module.verify_published_identity(mixed, "workspace", "ontology")

    def test_malformed_source_is_not_ignored(self):
        malformed = definition()
        malformed["definition"]["parts"][0]["payload"] = "not-base64"
        with self.assertRaises(ValueError):
            module.verify_published_identity(malformed, "workspace", "ontology")


if __name__ == "__main__":
    unittest.main()
