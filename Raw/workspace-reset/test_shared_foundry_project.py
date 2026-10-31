import copy
import json
import os
import unittest
from unittest.mock import Mock, patch

import provision_foundry_agents as foundry
import shared_foundry_project as shared
from foundry_resource_names import workspace_project_name


class SharedProjectTests(unittest.TestCase):
    def setUp(self):
        self.subscription = "10000000-0000-0000-0000-000000000001"
        self.account = f"/subscriptions/{self.subscription}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/shared"
        self.registry = f"/subscriptions/{self.subscription}/resourceGroups/rg/providers/Microsoft.ContainerRegistry/registries/sharedreg"
        self.monitor = f"/subscriptions/{self.subscription}/resourceGroups/rg/providers/microsoft.insights/components/sharedmonitor"
        self.principal = "30000000-0000-0000-0000-000000000001"
        self.deploy = Mock(DeployError=RuntimeError)
        self.deploy.az.side_effect = lambda *args: list(args)
        self.deploy.current_rayfin_target.return_value = ({}, None)
        self.deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        self.projects = {}
        self.connections = {}
        self.calls = []
        self.allocation = {"name": "shared-model", "sku": {"name": "Standard", "capacity": 75}, "properties": {
            "provisioningState": "Succeeded", "model": {"format": "OpenAI", "name": "chat", "version": "1"},
            "capabilities": {"chatCompletion": "true"},
            "rateLimits": [{"key": "token", "count": 75000, "renewalPeriod": 60}],
        }}
        self.infrastructure = [
            {"name": "original-acr", "properties": {"category": "ContainerRegistry", "target": "sharedreg.azurecr.io"}},
            {"name": "original-monitor", "properties": {"category": "AppInsights", "target": self.monitor}},
            {"name": "foreign-source", "properties": {"category": "RemoteTool", "target": "https://foreign.example"}},
        ]
        self.deploy.run_capture.side_effect = self.capture

    def capture(self, argv, **kwargs):
        if argv[:2] == ["resource", "list"]:
            if argv[argv.index("--resource-type") + 1].endswith("/projects"):
                return json.dumps([{"id": identity} for identity in self.projects])
            return json.dumps([{"id": self.account}])
        if argv[:2] == ["resource", "show"]:
            identity = argv[argv.index("--ids") + 1]
            return json.dumps(self.projects[identity])
        if argv[:2] == ["acr", "show"]:
            return json.dumps({"id": self.registry, "loginServer": "sharedreg.azurecr.io"})
        if argv[:3] == ["role", "assignment", "list"]:
            return "[]"
        if argv[:3] == ["role", "assignment", "create"]:
            return "{}"
        if argv[:3] == ["ad", "signed-in-user", "show"]:
            return self.principal
        if argv[:2] == ["account", "get-access-token"]:
            return "token"
        raise AssertionError(argv)

    def response(self, method, url, **kwargs):
        if ".services.ai.azure.com/" in url:
            self.calls.append((method, url))
            return Mock(status_code=200, ok=True, json=lambda: {"value": []})
        path = url.split("management.azure.com", 1)[1].split("?", 1)[0]
        self.calls.append((method, path))
        if "/deployments/" in path:
            self.assertEqual(method, "GET")
            body = copy.deepcopy(self.allocation)
        elif path == self.account:
            self.assertEqual(method, "GET")
            body = {"id": self.account, "kind": "AIServices", "location": "swedencentral", "properties": {
                "provisioningState": "Succeeded", "customSubDomainName": "shared",
            }}
        elif path == self.monitor:
            body = {"properties": {"InstrumentationKey": "fixture-key"}}
        elif path.endswith("/connections"):
            if path == self.account + "/connections":
                body = {"value": copy.deepcopy(self.infrastructure)}
            else:
                project = path.rsplit("/connections", 1)[0]
                body = {"value": [{"name": name, "properties": copy.deepcopy(properties)}
                                  for (identity, name), properties in self.connections.items() if identity == project]}
        elif "/connections/" in path:
            project, name = path.split("/connections/", 1)
            if method == "PUT":
                self.connections[(project, name)] = copy.deepcopy(kwargs["json"]["properties"])
            if (project, name) not in self.connections:
                return Mock(status_code=404, ok=False)
            body = {"properties": copy.deepcopy(self.connections[(project, name)])}
            body["properties"].pop("credentials", None)
        elif "/projects/" in path:
            if method == "PUT":
                self.assertEqual(kwargs["json"]["identity"], {"type": "SystemAssigned"})
                self.assertEqual(kwargs["json"]["properties"], {})
                name = path.rsplit("/", 1)[1]
                self.projects[path] = {"id": path, "identity": {"principalId": self.principal},
                                       "properties": {"provisioningState": "Succeeded", "endpoints": {
                                           "AI Foundry API": f"https://shared.services.ai.azure.com/api/projects/{name}",
                                       }}}
            if path not in self.projects:
                return Mock(status_code=404, ok=False)
            body = copy.deepcopy(self.projects[path])
        else:
            raise AssertionError((method, path))
        return Mock(status_code=200, ok=True, json=lambda: body)

    def environment(self):
        return patch.dict(os.environ, {
            "HYDRO_FOUNDRY_ACCOUNT_NAME": "shared", "HYDRO_FOUNDRY_MODEL": "shared-model",
            "HYDRO_FOUNDRY_CREATE": "true", "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "",
            "HYDRO_FOUNDRY_NEW_PROJECT_NAME": "", "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": "",
            "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "", "HYDRO_FOUNDRY_LOCATION": "", "HYDRO_ACR_NAME": "",
        })

    def test_ten_workspaces_create_ten_projects_without_model_or_account_mutation(self):
        before = copy.deepcopy(self.allocation)
        with self.environment(), patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response), \
                patch("deploy_foundry_project.model_defaults") as quota:
            for index in range(1, 11):
                workspace = f"20000000-0000-0000-0000-{index:012x}"
                preflight = foundry.resolve_project(self.deploy, "tenant", workspace)
                self.assertFalse(preflight.project_id)
                self.assertEqual(len(self.projects), index - 1)
                selected = foundry.resolve_project(
                    self.deploy, "tenant", workspace, allow_create=True, creation_settings=preflight.creation_settings,
                )
                self.assertEqual(selected.project_id, self.account + "/projects/" + workspace_project_name(workspace))
                self.assertEqual(selected.model, "shared-model")
                writes_before = len([method for method, _ in self.calls if method != "GET"])
                rerun = foundry.resolve_project(self.deploy, "tenant", workspace, allow_create=True)
                self.assertEqual(rerun.project_id, selected.project_id)
                self.assertEqual(len([method for method, _ in self.calls if method != "GET"]), writes_before)
            quota.assert_not_called()
        self.assertEqual(len(self.projects), 10)
        self.assertEqual(self.allocation, before)
        self.assertEqual(len([1 for method, path in self.calls if method == "PUT" and "/connections/" not in path]), 10)
        self.assertTrue(all("/projects/" in path for method, path in self.calls if method != "GET"))
        self.assertTrue(all(properties["category"] != "RemoteTool" for properties in self.connections.values()))
        self.deploy.run_stream.assert_not_called()

    def test_missing_or_unready_model_stops_before_project_creation_and_quota(self):
        self.allocation["properties"]["provisioningState"] = "Failed"
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response), \
                patch("deploy_foundry_project.model_defaults") as quota:
            with self.assertRaisesRegex(RuntimeError, "ready|chat"):
                shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                              account_name="shared", project_name="new", model="shared-model")
            quota.assert_not_called()
        self.assertFalse(self.projects)
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_model_change_after_preflight_prevents_project_creation(self):
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="new", model="shared-model")
            self.allocation["sku"]["capacity"] = 100
            with self.assertRaisesRegex(RuntimeError, "changed after preflight"):
                shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        self.assertFalse(self.projects)

    def test_partial_project_reuses_identity_and_connections_on_retry(self):
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="new", model="shared-model")
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
            del self.connections[(self.account + "/projects/new", "hydro-shared-monitoring")]
            start = len(self.calls)
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        writes = [path for method, path in self.calls[start:] if method != "GET"]
        self.assertEqual(writes, [self.account + "/projects/new/connections/hydro-shared-monitoring"])

    def test_creation_off_does_not_reuse_an_unrelated_sole_project(self):
        with self.environment(), patch.dict(os.environ, {"HYDRO_FOUNDRY_CREATE": "false"}), \
                patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            with self.assertRaisesRegex(RuntimeError, "was not found"):
                foundry.resolve_project(self.deploy, "tenant", "20000000-0000-0000-0000-000000000001")
        self.assertFalse(self.projects)

    def test_explicit_region_change_is_rejected_before_mutation(self):
        with self.environment(), patch.dict(os.environ, {"HYDRO_FOUNDRY_LOCATION": "eastus"}), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            with self.assertRaisesRegex(RuntimeError, "existing account's region"):
                shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                              account_name="shared", project_name="new", model="shared-model")
        self.assertFalse(self.projects)
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_missing_shared_monitoring_does_not_create_a_model_or_project(self):
        self.infrastructure = self.infrastructure[:1]
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            with self.assertRaisesRegex(RuntimeError, "incomplete or ambiguous"):
                shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                              account_name="shared", project_name="new", model="shared-model")
        self.assertFalse(self.projects)

    def test_access_propagation_is_bounded_and_verified(self):
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="new", model="shared-model")
        attempts = []
        def delayed(method, url, **kwargs):
            if ".services.ai.azure.com/" in url:
                attempts.append(url)
                if len(attempts) == 1:
                    return Mock(status_code=403, ok=False)
            return self.response(method, url, **kwargs)
        with patch.object(foundry, "request_with_read_retry", side_effect=delayed):
            sleep = Mock()
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan, sleep=sleep)
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once_with(15)

    def test_role_assignment_failure_preserves_partial_project_without_connections(self):
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="new", model="shared-model")
            with patch.object(shared, "_assign_role", side_effect=RuntimeError("role assignment denied")):
                with self.assertRaisesRegex(RuntimeError, "role assignment denied"):
                    shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        self.assertEqual(len(self.projects), 1)
        self.assertFalse(self.connections)
        self.assertTrue(all("/projects/" in path for method, path in self.calls if method != "GET"))


if __name__ == "__main__":
    unittest.main()
