import copy
from datetime import datetime
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
        self.deployment_history = []
        self.calls = []
        self.agent_page = {"data": [], "has_more": False}
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
        if argv[:3] == ["deployment", "group", "list"]:
            self.assertEqual(argv[argv.index("--subscription") + 1], self.subscription)
            self.assertEqual(argv[argv.index("--resource-group") + 1], "rg")
            return json.dumps(self.deployment_history)
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
            return Mock(status_code=200, ok=True, json=lambda: copy.deepcopy(self.agent_page))
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
                                       "systemData": {"createdAt": "2026-11-02T18:25:42.8764902Z"},
                                       "properties": {"internalId": uuid.uuid4().hex,
                                                      "provisioningState": "Succeeded", "endpoints": {
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

    def deployment_record(self, resources, *, state="Succeeded", name="bootstrap"):
        return {
            "id": self.account.split("/providers/")[0] + "/providers/Microsoft.Resources/deployments/" + name,
            "state": state, "resources": [{"id": identity} for identity in resources],
        }

    def test_deleted_project_recovers_infrastructure_from_account_deployment_without_mutation(self):
        self.infrastructure = []
        record = self.deployment_record([self.account, self.registry, self.monitor])
        self.deployment_history = [record, copy.deepcopy(record)]
        before = copy.deepcopy(self.allocation)
        with self.environment(), patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            selection = foundry.resolve_project(
                self.deploy, "tenant", "20000000-0000-0000-0000-000000000001",
            )
        self.assertFalse(selection.project_id)
        settings = selection.creation_settings
        assert settings is not None
        self.assertEqual(settings["registry_id"], self.registry)
        self.assertEqual(settings["insights_id"], self.monitor)
        self.assertEqual(self.allocation, before)
        self.assertFalse(self.projects)
        self.assertFalse(self.connections)
        self.assertTrue(all(method == "GET" for method, _ in self.calls))
        self.deploy.run_stream.assert_not_called()

    def test_recovery_only_creates_approved_project_and_owned_connections(self):
        self.infrastructure = []
        self.deployment_history = [self.deployment_record([self.account, self.registry, self.monitor])]
        workspace = "20000000-0000-0000-0000-000000000001"
        project = self.account + "/projects/" + workspace_project_name(workspace)
        before = copy.deepcopy(self.allocation)
        with self.environment(), patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            with patch.dict(os.environ, {"HYDRO_FOUNDRY_CREATE": "false"}):
                with self.assertRaisesRegex(RuntimeError, "was not found"):
                    foundry.resolve_project(self.deploy, "tenant", workspace, allow_create=True)
            preflight = foundry.resolve_project(self.deploy, "tenant", workspace)
            selected = foundry.resolve_project(
                self.deploy, "tenant", workspace, allow_create=True, creation_settings=preflight.creation_settings,
            )
        self.assertEqual(selected.project_id, project)
        self.assertEqual(self.allocation, before)
        writes = [path for method, path in self.calls if method != "GET"]
        self.assertEqual(len(writes), 3)
        self.assertTrue(all(path == project or path.startswith(project + "/connections/") for path in writes))

    def test_deployment_recovery_only_fills_missing_infrastructure(self):
        self.infrastructure = self.infrastructure[:1]
        self.deployment_history = [self.deployment_record([self.account, self.registry + "other", self.monitor])]
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            settings = shared.prepare_shared_project(
                self.deploy, tenant="tenant", subscription=self.subscription,
                account_name="shared", project_name="new", model="shared-model",
            )
        assert settings is not None
        self.assertEqual(settings["registry_id"], self.registry)
        self.assertEqual(settings["insights_id"], self.monitor)

    def test_deployment_inventory_denial_and_malformed_payload_are_not_ignored(self):
        self.infrastructure = []
        for inventory in (None, [{"state": "Succeeded", "id": self.deployment_record([])["id"],
                                  "resources": "invalid"}], RuntimeError("deployment inventory access denied")):
            def capture(argv, **kwargs):
                if argv[:3] == ["deployment", "group", "list"]:
                    if isinstance(inventory, RuntimeError):
                        raise inventory
                    return json.dumps(inventory)
                return self.capture(argv, **kwargs)
            with self.subTest(inventory=inventory), self.environment(), \
                    patch.object(self.deploy, "run_capture", side_effect=capture), \
                    patch.object(foundry, "request_with_read_retry", side_effect=self.response):
                with self.assertRaisesRegex(RuntimeError, "inventory is invalid|access denied"):
                    shared.prepare_shared_project(
                        self.deploy, tenant="tenant", subscription=self.subscription,
                        account_name="shared", project_name="new", model="shared-model",
                    )
        self.assertFalse(self.projects)
        self.assertFalse(self.connections)

    def test_deployment_recovery_requires_success_exact_account_and_scoped_deployment_identity(self):
        self.infrastructure = []
        unrelated = self.account + "-other"
        records = [
            self.deployment_record([self.account, self.registry, self.monitor], state="Failed"),
            self.deployment_record([unrelated, self.registry, self.monitor]),
            self.deployment_record([self.account, self.registry, self.monitor]),
        ]
        records[-1]["id"] = records[-1]["id"].replace("/resourceGroups/rg/", "/resourceGroups/other/")
        for record in records:
            with self.subTest(record=record), self.environment(), \
                    patch.object(foundry, "request_with_read_retry", side_effect=self.response):
                self.deployment_history = [record]
                with self.assertRaisesRegex(RuntimeError, "incomplete or ambiguous"):
                    shared.prepare_shared_project(
                        self.deploy, tenant="tenant", subscription=self.subscription,
                        account_name="shared", project_name="new", model="shared-model",
                    )
        self.assertFalse(self.projects)
        self.assertFalse(self.connections)
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_deployment_recovery_rejects_ambiguous_or_unavailable_infrastructure(self):
        self.infrastructure = []
        records = [
            self.deployment_record([self.account, self.registry, self.monitor, self.registry + "other"]),
            self.deployment_record([self.account, self.registry, self.monitor, self.monitor + "other"]),
            self.deployment_record([self.account, self.registry + "other", self.monitor]),
        ]
        for record in records:
            with self.subTest(record=record), self.environment(), \
                    patch.object(foundry, "request_with_read_retry", side_effect=self.response):
                self.deployment_history = [record]
                with self.assertRaisesRegex(RuntimeError, "incomplete or ambiguous|identity/readback"):
                    shared.prepare_shared_project(
                        self.deploy, tenant="tenant", subscription=self.subscription,
                        account_name="shared", project_name="new", model="shared-model",
                    )
        self.assertFalse(self.projects)
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_connected_infrastructure_does_not_read_deployment_history(self):
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            shared.prepare_shared_project(
                self.deploy, tenant="tenant", subscription=self.subscription,
                account_name="shared", project_name="new", model="shared-model",
            )
        self.assertFalse(any(call.args[0][:3] == ["deployment", "group", "list"]
                             for call in self.deploy.run_capture.call_args_list))

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
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    foundry.resolve_project(self.deploy, "tenant", workspace, allow_create=True)
                with patch.dict(os.environ, {"HYDRO_FOUNDRY_CREATE": "false"}):
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
            plan = shared.prepare_shared_project(
                self.deploy, tenant="tenant", subscription=self.subscription,
                account_name="shared", project_name="test", model="shared-model",
            )
            shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
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
            with patch.dict(os.environ, {
                "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "feature", "HYDRO_FOUNDRY_CREATE": "false",
            }):
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

    def test_create_checked_rejects_existing_projects_in_both_preflight_and_provisioning(self):
        project_id = self.account + "/projects/existing"
        self.response("PUT", "https://management.azure.com" + project_id, json={
            "identity": {"type": "SystemAssigned"}, "properties": {},
        })
        before = copy.deepcopy(self.projects)
        with self.environment(), patch.dict(os.environ, {"HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "existing"}), \
                patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            for allow_create in (False, True):
                start = len(self.calls)
                with self.subTest(allow_create=allow_create), self.assertRaisesRegex(RuntimeError, "already exists"):
                    foundry.resolve_project(
                        self.deploy, "tenant", "20000000-0000-0000-0000-000000000001", allow_create=allow_create,
                    )
                self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))
        self.assertEqual(self.projects, before)
        self.assertFalse(self.connections)

    def test_create_checked_checks_live_arm_absence_even_if_discovery_omits_project(self):
        project_id = self.account + "/projects/existing"
        self.response("PUT", "https://management.azure.com" + project_id, json={
            "identity": {"type": "SystemAssigned"}, "properties": {},
        })
        start = len(self.calls)
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response), \
                self.assertRaisesRegex(RuntimeError, "already exists"):
            shared.prepare_shared_project(
                self.deploy, tenant="tenant", subscription=self.subscription, account_name="shared",
                project_name="existing", model="shared-model", require_new=True,
            )
        self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))

    def test_create_checked_rechecks_absence_after_preflight_without_adopting_partial_project(self):
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(
                self.deploy, tenant="tenant", subscription=self.subscription, account_name="shared",
                project_name="new", model="shared-model", require_new=True,
            )
            self.response("PUT", "https://management.azure.com" + self.account + "/projects/new", json={
                "identity": {"type": "SystemAssigned"}, "properties": {},
            })
            start = len(self.calls)
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan, require_new=True)
        self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))
        self.assertFalse(self.connections)

    def test_checked_blank_project_ignores_saved_project_and_uses_workspace_guid_name(self):
        workspace = "20000000-0000-0000-0000-000000000001"
        old = self.account + "/projects/old"
        self.response("PUT", "https://management.azure.com" + old, json={
            "identity": {"type": "SystemAssigned"}, "properties": {},
        })
        self.deploy.current_rayfin_target.return_value = ({
            "RAYFIN_PUBLIC_TENANT_ID": "tenant", "RAYFIN_PUBLIC_WORKSPACE_ID": workspace,
            "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID": self.subscription,
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID": old,
            "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": "https://shared.services.ai.azure.com/api/projects/old",
        }, None)
        start = len(self.calls)
        with self.environment(), patch.object(foundry, "resolve_subscription", return_value=self.subscription), \
                patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            selection = foundry.resolve_project(self.deploy, "tenant", workspace)
        settings = selection.creation_settings
        assert settings is not None
        self.assertEqual(settings["project_name"], workspace_project_name(workspace))
        self.assertFalse(selection.project_id)
        self.assertEqual(list(self.projects), [old])
        self.assertTrue(all(method == "GET" for method, _ in self.calls[start:]))

    def test_absence_check_denial_is_not_treated_as_missing(self):
        for status in (403, 500):
            with self.subTest(status=status), patch.object(foundry, "request_with_read_retry", return_value=Mock(
                ok=False, status_code=status,
            )) as request, self.assertRaisesRegex(RuntimeError, "Cannot verify"):
                shared._require_missing_project(self.deploy, self.account + "/projects/new", {})
            request.assert_called_once()
            self.assertEqual(request.call_args.args[0], "GET")

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

    def test_recreated_project_rejects_retained_agents_on_creation_and_rerun(self):
        self.agent_page = {"data": [{
            "name": "hydro-qa-agent-old-workspace", "versions": {"latest": {"created_at": 1793634885}},
        }], "has_more": False}
        with self.environment(), patch.object(foundry, "request_with_read_retry", side_effect=self.response):
            plan = shared.prepare_shared_project(
                self.deploy, tenant="tenant", subscription=self.subscription,
                account_name="shared", project_name="new", model="shared-model",
            )
            for attempt in range(2):
                with self.subTest(attempt=attempt), self.assertRaisesRegex(RuntimeError, "project lifecycle"):
                    shared.ensure_shared_project(self.deploy, tenant="tenant", settings=plan)
            self.assertEqual(len(self.projects), 1)
            self.assertFalse(self.connections)
            self.assertTrue(all(method != "DELETE" for method, _ in self.calls))

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


class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.deploy = Mock(DeployError=RuntimeError)
        self.deploy.run_capture.return_value = "token"
        self.project_id = (
            "/subscriptions/10000000-0000-0000-0000-000000000001/resourceGroups/rg/"
            "providers/Microsoft.CognitiveServices/accounts/shared/projects/recreated"
        )
        self.endpoint = "https://shared.services.ai.azure.com/api/projects/recreated"
        self.project = {
            "id": self.project_id, "systemData": {"createdAt": "2026-11-02T18:25:42.8764902Z"},
            "properties": {"internalId": uuid.uuid4().hex, "provisioningState": "Succeeded",
                           "endpoints": {"AI Foundry API": self.endpoint}},
        }
        self.created_second = int(datetime.fromisoformat(self.project["systemData"]["createdAt"]).timestamp())
        self.pages = [{"data": [], "has_more": False}]
        self.agent_reads = []
        self.readback = None
        self.arm_reads = 0
        self.request = self.enterContext(patch.object(foundry, "request_with_read_retry", side_effect=self.response))

    def agent(self, *, name="hydro-qa-agent-workspace", timestamp=None):
        return {"name": name, "versions": {"latest": {
            "created_at": self.created_second if timestamp is None else timestamp,
        }}}

    def response(self, method, url, **kwargs):
        self.assertEqual(method, "GET")
        if ".services.ai.azure.com/" in url:
            self.agent_reads.append(url)
            body = self.pages.pop(0)
        else:
            self.arm_reads += 1
            body = self.readback if self.arm_reads > 1 and self.readback is not None else self.project
        return Mock(status_code=200, ok=True, content=b"json", json=lambda: copy.deepcopy(body))

    def verify(self, **kwargs):
        return shared.verify_project_lifecycle(
            self.deploy, tenant="tenant", project_id=self.project_id, endpoint=self.endpoint, **kwargs,
        )

    def test_existing_empty_and_current_shared_workspace_agents_are_valid(self):
        for data in ([], [self.agent(), self.agent(name="hydro-qa-agent-another-workspace")]):
            with self.subTest(data=data):
                self.pages = [{"data": data, "has_more": False}]
                self.verify()

    def test_new_project_requires_empty_inventory_even_with_current_timestamps(self):
        self.pages = [{"data": [self.agent()], "has_more": False}]
        with self.assertRaisesRegex(RuntimeError, "project lifecycle mismatch"):
            self.verify(require_empty=True)

    def test_retained_agent_on_later_page_is_rejected_and_cursor_is_encoded(self):
        self.pages = [
            {"data": [self.agent()], "has_more": True, "last_id": "cursor/&?"},
            {"data": [self.agent(name="old", timestamp=self.created_second - 1)], "has_more": False},
        ]
        with self.assertRaisesRegex(RuntimeError, "agent 'old'"):
            self.verify()
        self.assertEqual(self.agent_reads[-1], self.endpoint + "/agents?api-version=v1&limit=100&after=cursor%2F%26%3F")

    def test_valid_complete_pagination_is_read_only(self):
        self.pages = [
            {"data": [self.agent()], "has_more": True, "last_id": "first"},
            {"data": [self.agent(name="second")], "has_more": False},
        ]
        self.verify()
        self.assertEqual(len(self.agent_reads), 2)
        self.assertEqual(self.arm_reads, 2)

    def test_invalid_inventory_never_counts_as_ready(self):
        for page in (
            None, {"value": []}, {"data": [], "has_more": "false"},
            {"data": None, "has_more": False}, {"data": [], "has_more": True},
            {"data": [self.agent()], "has_more": True},
            {"data": [self.agent(), self.agent()], "has_more": False},
            {"data": [{}], "has_more": False},
            *({"data": [self.agent(timestamp=value)], "has_more": False}
              for value in (True, "1793643942", float("nan"), float("inf"), 0, -1)),
        ):
            with self.subTest(page=page):
                self.pages = [page]
                with self.assertRaisesRegex(RuntimeError, "project lifecycle"):
                    self.verify()

    def test_cyclic_pagination_is_rejected(self):
        self.pages = [
            {"data": [self.agent(name="first")], "has_more": True, "last_id": "cursor"},
            {"data": [self.agent(name="second")], "has_more": True, "last_id": "cursor"},
        ]
        with self.assertRaisesRegex(RuntimeError, "pagination"):
            self.verify()
        self.assertEqual(len(self.agent_reads), 2)

    def test_missing_or_untrusted_arm_incarnation_is_rejected_before_agent_read(self):
        for created, incarnation in (
            (None, "id"), ("invalid", "id"), ("2026-11-02T18:25:42", "id"),
            ("2026-11-02T18:25:42Z", ""),
        ):
            with self.subTest(created=created, incarnation=incarnation):
                self.project["systemData"]["createdAt"] = created
                self.project["properties"]["internalId"] = incarnation
                with self.assertRaisesRegex(RuntimeError, "project lifecycle"):
                    self.verify()
        self.assertFalse(self.agent_reads)

    def test_project_identity_endpoint_or_incarnation_change_fails_closed(self):
        for field in ("internalId", "createdAt", "endpoint", "id", "state"):
            with self.subTest(field=field):
                self.pages = [{"data": [], "has_more": False}]
                self.arm_reads = 0
                self.readback = copy.deepcopy(self.project)
                if field == "createdAt":
                    self.readback["systemData"][field] = "2026-11-03T18:25:42Z"
                elif field == "endpoint":
                    self.readback["properties"]["endpoints"]["AI Foundry API"] += "-other"
                elif field == "id":
                    self.readback["id"] += "-other"
                elif field == "state":
                    self.readback["properties"]["provisioningState"] = "Deleting"
                else:
                    self.readback["properties"][field] = uuid.uuid4().hex
                with self.assertRaisesRegex(RuntimeError, "changed during"):
                    self.verify()

    def test_access_denied_or_project_missing_is_not_success(self):
        for status in (403, 404, 500):
            with self.subTest(status=status):
                self.request.side_effect = None
                self.request.return_value = Mock(ok=False, status_code=status, text="denied")
                with self.assertRaises(RuntimeError):
                    self.verify()
        self.request.side_effect = self.response
        with patch.object(foundry, "request_with_read_retry", side_effect=[
            self.response("GET", "https://management.azure.com/project"),
            Mock(ok=False, status_code=403),
        ]), self.assertRaisesRegex(RuntimeError, "inventory failed"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
