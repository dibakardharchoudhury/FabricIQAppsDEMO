import copy
import json
import os
import unittest
import uuid
from unittest.mock import Mock, patch

import provision_foundry_agents as foundry
import shared_foundry_project as shared
from foundry_resource_names import workspace_project_name


class SharedProjectTests(unittest.TestCase):
    def connection_name(self, project, kind):
        return shared.infrastructure_connection_name(self.account + "/projects/" + project, kind)

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
        self.instrumentation_key = "40000000-0000-0000-0000-000000000001"
        self.connection_string = (
            f"InstrumentationKey={self.instrumentation_key};"
            "IngestionEndpoint=https://swedencentral-0.in.applicationinsights.azure.com/;"
            "LiveEndpoint=https://swedencentral.livediagnostics.monitor.azure.com/;"
            "ApplicationId=50000000-0000-0000-0000-000000000001"
        )
        self.monitor_detail = {"id": self.monitor, "properties": {
            "InstrumentationKey": self.instrumentation_key, "ConnectionString": self.connection_string,
        }}
        self.allocation = {"name": "shared-model", "sku": {"name": "Standard", "capacity": 75}, "properties": {
            "provisioningState": "Succeeded", "model": {"format": "OpenAI", "name": "chat", "version": "1"},
            "capabilities": {"chatCompletion": "true"},
            "rateLimits": [{"key": "token", "count": 75000, "renewalPeriod": 60}],
        }}
        self.infrastructure = [
            {"name": "original-acr", "properties": {
                "category": "ContainerRegistry", "target": "sharedreg.azurecr.io",
                "authType": "ManagedIdentity", "metadata": {"ResourceId": self.registry},
            }},
            {"name": "original-monitor", "properties": {
                "category": "AppInsights", "target": self.monitor,
                "authType": "ApiKey", "metadata": {"ApiType": "Azure", "ResourceId": self.monitor},
            }},
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
            self.assertEqual(method, "GET")
            body = copy.deepcopy(self.monitor_detail)
        elif path.endswith("/connections"):
            if path == self.account + "/connections":
                body = {"value": copy.deepcopy(self.infrastructure)}
            else:
                project = path.rsplit("/connections", 1)[0]
                body = {"value": [{"id": identity + "/connections/" + name,
                                  "name": name, "properties": copy.deepcopy(properties)}
                                  for (identity, name), properties in self.connections.items() if identity == project]}
        elif "/connections/" in path:
            project, name = path.split("/connections/", 1)
            if method == "PUT":
                owner = next((identity for identity, existing_name in self.connections
                              if existing_name == name and identity != project), None)
                if owner is not None:
                    return Mock(status_code=400, ok=False, text=(
                        f"Connection {name} already exist, and can only be updated by the workspace "
                        f"that created it: {owner}"
                    ))
                properties = kwargs["json"]["properties"]
                if properties.get("category") == "ContainerRegistry" and (
                    properties.get("authType") != "ManagedIdentity"
                    or properties.get("credentials") != {
                        "clientId": self.projects.get(project, {}).get("identity", {}).get("principalId"),
                        "resourceId": self.registry,
                    }
                ):
                    return Mock(status_code=400, ok=False, text=(
                        "Credentials Property can't be empty for auth type RegistryIdentity"
                    ))
                if properties.get("category") == "AppInsights":
                    if properties.get("credentials") != {"key": self.connection_string}:
                        return Mock(status_code=400, ok=False, text=(
                            "An Application Insights instrumentation key is not supported. "
                            "Provide the full Application Insights connection string instead."
                        ))
                    self.assertEqual(properties.get("authType"), "ApiKey")
                    self.assertEqual(properties.get("target"), self.monitor)
                    self.assertEqual(properties.get("metadata"), {
                        "ApiType": "Azure", "ResourceId": self.monitor,
                    })
                self.connections[(project, name)] = copy.deepcopy(properties)
            if (project, name) not in self.connections:
                return Mock(status_code=404, ok=False)
            body = {"id": path, "name": name, "properties": copy.deepcopy(self.connections[(project, name)])}
            body["properties"].pop("credentials", None)
        elif "/projects/" in path:
            if method == "PUT":
                self.assertEqual(kwargs["json"]["identity"], {"type": "SystemAssigned"})
                self.assertEqual(kwargs["json"]["properties"], {})
                name = path.rsplit("/", 1)[1]
                self.projects[path] = {"id": path, "identity": {
                    "type": "SystemAssigned", "principalId": str(uuid.uuid5(uuid.NAMESPACE_URL, path)),
                },
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
        registry_principals = {
            properties["credentials"]["clientId"] for properties in self.connections.values()
            if properties["category"] == "ContainerRegistry"
        }
        self.assertEqual(len(registry_principals), 10)
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

    def test_test_project_does_not_claim_dev_private_connection_names(self):
        dev = self.account + "/projects/dev"
        test = self.account + "/projects/test"
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            self.response("PUT", "https://management.azure.com" + dev, json={
                "identity": {"type": "SystemAssigned"}, "properties": {},
            })
            for name, infrastructure in zip(
                ("hydro-shared-registry", "hydro-shared-monitoring"), self.infrastructure[:2],
            ):
                self.connections[(dev, name)] = copy.deepcopy(infrastructure["properties"])
            original = copy.deepcopy(self.connections)
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="test", model="shared-model")
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
            start = len(self.calls)
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        for key, properties in original.items():
            self.assertEqual(self.connections[key], properties)
        test_connections = {name: properties for (owner, name), properties in self.connections.items() if owner == test}
        self.assertEqual(len(test_connections), 2)
        self.assertTrue(all(name not in ("hydro-shared-registry", "hydro-shared-monitoring")
                            for name in test_connections))
        self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))
        registry = next(value for value in test_connections.values() if value["category"] == "ContainerRegistry")
        self.assertEqual(registry["credentials"]["clientId"], self.projects[test]["identity"]["principalId"])

    def test_connection_names_are_stable_project_scoped_and_bounded(self):
        dev = self.account + "/projects/dev"
        self.assertEqual(shared.infrastructure_connection_name(dev, "registry"),
                         shared.infrastructure_connection_name(dev.upper(), "registry"))
        names = {
            shared.infrastructure_connection_name(project, kind)
            for project in (dev, self.account + "/projects/test", self.account + "/projects/" + "p" * 64)
            for kind in ("registry", "monitoring")
        }
        self.assertEqual(len(names), 6)
        self.assertTrue(all(len(name) <= 64 for name in names))
        for name in names:
            self.assertRegex(name, r"^hydro-shared-(registry|monitoring)-[0-9a-f]{32}$")
        for identity, kind in (("project-name", "registry"), (dev, "source"), (dev + "/extra", "registry")):
            with self.assertRaises(ValueError):
                shared.infrastructure_connection_name(identity, kind)

    def test_compatible_owned_legacy_connections_are_retained_with_unknown_metadata(self):
        project = self.account + "/projects/dev"
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            self.response("PUT", "https://management.azure.com" + project, json={
                "identity": {"type": "SystemAssigned"}, "properties": {},
            })
            for name, infrastructure in zip(
                ("hydro-shared-registry", "hydro-shared-monitoring"), self.infrastructure[:2],
            ):
                properties = copy.deepcopy(infrastructure["properties"])
                properties["metadata"]["custom"] = "retain"
                self.connections[(project, name)] = properties
            original = copy.deepcopy(self.connections)
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="dev", model="shared-model")
            start = len(self.calls)
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        self.assertEqual(self.connections, original)
        self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))

    def test_inherited_foreign_connections_cannot_stand_in_for_owned_project_connections(self):
        dev = self.account + "/projects/dev"
        test = self.account + "/projects/test"
        def inherited(method, url, **kwargs):
            response = self.response(method, url, **kwargs)
            if method == "GET" and url.split("?", 1)[0].endswith(test + "/connections"):
                body = response.json()
                body["value"].extend({"id": dev + "/connections/" + item["name"], **copy.deepcopy(item)}
                                     for item in self.infrastructure[:2])
                return Mock(status_code=200, ok=True, json=lambda: body)
            return response
        with self.environment(), patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "test"}), \
                patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=inherited):
            self.response("PUT", "https://management.azure.com" + test, json={
                "identity": {"type": "SystemAssigned"}, "properties": {},
            })
            selection = foundry.resolve_project(
                self.deploy, "tenant", "20000000-0000-0000-0000-000000000001", allow_create=True,
            )
            self.assertEqual(selection.project_id, test)
        self.assertEqual(sum(owner == test for owner, _ in self.connections), 2)
        self.assertTrue(all(owner == test for owner, _ in self.connections))

    def test_missing_connection_ownership_stops_without_guessing_from_names(self):
        with self.assertRaisesRegex(RuntimeError, "ownership identity is missing"):
            shared.owned_infrastructure_connections(self.deploy, self.account + "/projects/test", self.infrastructure)

    def test_connection_readback_must_confirm_the_selected_project_identity(self):
        test = self.account + "/projects/test"
        def wrong_owner(method, url, **kwargs):
            response = self.response(method, url, **kwargs)
            if method == "GET" and "/connections/hydro-shared-registry-" in url and response.ok:
                body = response.json()
                body["id"] = body["id"].replace("/projects/test/", "/projects/dev/")
                return Mock(status_code=200, ok=True, json=lambda: body)
            return response
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=wrong_owner):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="test", model="shared-model")
            with self.assertRaisesRegex(RuntimeError, "readback does not match"):
                shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        self.assertEqual(sum(owner == test for owner, _ in self.connections), 1)

    def test_feature_existing_connections_skip_creation_but_dev_requires_valid_credentials(self):
        feature = self.account + "/projects/feature"
        with self.environment(), patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            self.response("PUT", "https://management.azure.com" + feature, json={
                "identity": {"type": "SystemAssigned"}, "properties": {},
            })
            for connection in self.infrastructure[:2]:
                self.connections[(feature, connection["name"])] = copy.deepcopy(connection["properties"])
            start = len(self.calls)
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "feature"}):
                selected = foundry.resolve_project(
                    self.deploy, "tenant", "20000000-0000-0000-0000-000000000001", allow_create=True,
                )
            self.assertEqual(selected.project_id, feature)
            self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "dev"}):
                plan = foundry.resolve_project(
                    self.deploy, "tenant", "20000000-0000-0000-0000-000000000002",
                )
                selected = foundry.resolve_project(
                    self.deploy, "tenant", "20000000-0000-0000-0000-000000000002",
                    allow_create=True, creation_settings=plan.creation_settings,
                )
            self.assertEqual(selected.project_id, self.account + "/projects/dev")
        self.assertEqual(self.connections[(selected.project_id, self.connection_name("dev", "registry"))]["credentials"], {
            "clientId": self.projects[selected.project_id]["identity"]["principalId"],
            "resourceId": self.registry,
        })
        self.assertNotEqual(self.projects[selected.project_id]["identity"]["principalId"],
                            self.projects[feature]["identity"]["principalId"])

    def test_original_missing_registry_credentials_are_rejected_without_connection_mutation(self):
        path = self.account + "/projects/dev/connections/hydro-shared-registry"
        with patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            with self.assertRaisesRegex(
                RuntimeError, r"PUT .*connections/hydro-shared-registry .*HTTP 400.*RegistryIdentity",
            ):
                shared._request(self.deploy, "PUT", path, {}, api="2025-10-01-preview", json={
                    "properties": {"category": "ContainerRegistry", "authType": "ManagedIdentity",
                                   "target": "sharedreg.azurecr.io"},
                })
        self.assertFalse(self.connections)

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
            del self.connections[(self.account + "/projects/new", self.connection_name("new", "monitoring"))]
            start = len(self.calls)
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        writes = [path for method, path in self.calls[start:] if method != "GET"]
        self.assertEqual(writes, [self.account + "/projects/new/connections/" + self.connection_name("new", "monitoring")])
        self.assertEqual(self.connections[(self.account + "/projects/new", self.connection_name("new", "monitoring"))]["credentials"],
                         {"key": self.connection_string})

    def test_instrumentation_key_only_payload_is_rejected_and_retry_preserves_registry_and_project(self):
        before = copy.deepcopy(self.allocation)
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="dev", model="shared-model")
        self.assertNotIn(self.connection_string, json.dumps(plan))
        def rejected(method, url, **kwargs):
            if method == "PUT" and "/connections/" + self.connection_name("dev", "monitoring") + "?" in url:
                kwargs["json"]["properties"]["credentials"]["key"] = self.instrumentation_key
            return self.response(method, url, **kwargs)
        with patch.object(foundry, "request_with_read_retry", side_effect=rejected):
            with self.assertRaisesRegex(RuntimeError, "hydro-shared-monitoring.*HTTP 400.*full Application Insights"):
                shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        project_id = self.account + "/projects/dev"
        project = copy.deepcopy(self.projects[project_id])
        registry = copy.deepcopy(self.connections[(project_id, self.connection_name("dev", "registry"))])
        self.assertNotIn((project_id, self.connection_name("dev", "monitoring")), self.connections)
        start = len(self.calls)
        with patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        self.assertEqual([path for method, path in self.calls[start:] if method == "PUT"],
                         [project_id + "/connections/" + self.connection_name("dev", "monitoring")])
        self.assertEqual(self.projects[project_id], project)
        self.assertEqual(self.connections[(project_id, self.connection_name("dev", "registry"))], registry)
        self.assertEqual(self.allocation, before)

    def test_missing_or_incomplete_connection_string_fails_preflight_before_mutation(self):
        for value in (None, "", self.instrumentation_key,
                      f"InstrumentationKey={self.instrumentation_key}",
                      f"InstrumentationKey={self.instrumentation_key};IngestionEndpoint=http://example.test"):
            with self.subTest(value=value), self.environment(), \
                    patch.object(foundry, "request_with_read_retry", side_effect=self.response):
                self.monitor_detail["properties"]["ConnectionString"] = value
                with self.assertRaisesRegex(RuntimeError, "full ConnectionString"):
                    shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                  account_name="shared", project_name="dev", model="shared-model")
                self.assertFalse(self.projects)
                self.assertFalse(self.connections)
                self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_monitoring_identity_mismatch_fails_preflight_before_mutation(self):
        self.monitor_detail["id"] = self.account
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            with self.assertRaisesRegex(RuntimeError, "Application Insights resource identity"):
                shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                              account_name="shared", project_name="dev", model="shared-model")
        self.assertFalse(self.projects)
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_monitoring_becoming_invalid_after_preflight_stops_before_project_or_roles(self):
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="dev", model="shared-model")
            self.monitor_detail["properties"].pop("ConnectionString")
            with patch.object(shared, "_assign_role") as roles:
                with self.assertRaisesRegex(RuntimeError, "full ConnectionString"):
                    shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
                roles.assert_not_called()
        self.assertFalse(self.projects)
        self.assertFalse(self.connections)
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_retry_after_registry_rejection_retains_project_and_model(self):
        before = copy.deepcopy(self.allocation)
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                 account_name="shared", project_name="dev", model="shared-model")
        def rejected(method, url, **kwargs):
            if method == "PUT" and "/connections/" + self.connection_name("dev", "registry") + "?" in url:
                kwargs["json"]["properties"].pop("credentials")
            return self.response(method, url, **kwargs)
        with patch.object(foundry, "request_with_read_retry", side_effect=rejected):
            with self.assertRaisesRegex(RuntimeError, "hydro-shared-registry.*HTTP 400"):
                shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        project = copy.deepcopy(self.projects[self.account + "/projects/dev"])
        self.assertFalse(self.connections)
        start = len(self.calls)
        with patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
        self.assertEqual(self.projects[self.account + "/projects/dev"], project)
        self.assertEqual(self.allocation, before)
        self.assertEqual([path for method, path in self.calls[start:] if method == "PUT"], [
            self.account + "/projects/dev/connections/" + self.connection_name("dev", "registry"),
            self.account + "/projects/dev/connections/" + self.connection_name("dev", "monitoring"),
        ])

    def test_existing_incompatible_connection_is_not_silently_reused_or_overwritten(self):
        for field, value in (("authType", "ApiKey"), ("metadata", {"ResourceId": self.monitor})):
            with self.subTest(field=field), self.environment(), \
                    patch.object(foundry, "request_with_read_retry", side_effect=self.response):
                self.projects.clear()
                self.connections.clear()
                plan = shared.prepare_shared_project(self.deploy, tenant="tenant", subscription=self.subscription,
                                                     account_name="shared", project_name="dev", model="shared-model")
                shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
                connection = self.connections[(self.account + "/projects/dev", self.connection_name("dev", "registry"))]
                connection[field] = value
                start = len(self.calls)
                with self.assertRaisesRegex(RuntimeError, "authentication/resource identity"):
                    shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
                self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))

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
