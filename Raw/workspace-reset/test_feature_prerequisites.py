"""Offline tests: every HTTP session, token provider and sleep is mocked."""

from __future__ import annotations

import copy
import io
import json
import os
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

import feature_prerequisites as fp

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
SUBSCRIPTION = "33333333-3333-4333-8333-333333333333"
APP = "44444444-4444-4444-8444-444444444444"
CLIENT = "55555555-5555-4555-8555-555555555555"
PRINCIPAL = "66666666-6666-4666-8666-666666666666"
GROUP = "77777777-7777-4777-8777-777777777777"
CALLER = "88888888-8888-4888-8888-888888888888"
PASSWORD = "99999999-9999-4999-8999-999999999999"
ADMIN_GROUP = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EXCLUDED_GROUP = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
REQUEST_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
SECRET = "offline-unit-test-password-not-a-real-credential"
TOKEN = "offline-unit-test-token"


def target_args() -> dict[str, str]:
    return {
        "tenant_id": TENANT, "workspace_id": WORKSPACE, "subscription_id": SUBSCRIPTION,
        "resource_group": "rg-hydro-feature-test", "vault_name": "kv-hydro-feature-test",
        "location": "norwayeast",
    }


def policy() -> fp.Json:
    return {
        "settingName": fp.PUBLIC_API_SETTING, "title": "Public APIs", "tenantSettingGroup": "Developer",
        "enabled": True, "canSpecifySecurityGroups": True,
        "enabledSecurityGroups": [{"graphId": ADMIN_GROUP, "name": "FabricAPIAdmin"}],
        "excludedSecurityGroups": [{"graphId": EXCLUDED_GROUP, "name": "Existing exclusions"}],
        "properties": [{"name": "ExistingProperty", "value": "true", "type": "Boolean"}],
        "delegateToCapacity": False, "delegateToDomain": True, "delegateToWorkspace": False,
    }


def response(status: int = 200, data: object = None, headers: dict[str, str] | None = None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.headers.update({"request-id": REQUEST_ID, **(headers or {})})
    result._content = json.dumps({} if data is None else data).encode()
    return result


class FakeCloud:
    """An allowlisted in-memory REST fixture, never a network transport."""

    def __init__(self, target: fp.FeatureTarget) -> None:
        self.target = target
        self.calls: list[tuple[str, str, fp.Json]] = []
        self.rg: fp.Json | None = None
        self.vault: fp.Json | None = None
        self.app: fp.Json | None = None
        self.principal: fp.Json | None = None
        self.group: fp.Json | None = None
        self.members: list[str] = []
        self.owners: list[str] = []
        self.auto_owner = True
        self.parent_groups: list[fp.Json] = []
        self.extra_memberships: list[fp.Json] = []
        self.app_grants: list[fp.Json] = []
        self.oauth_grants: list[fp.Json] = []
        self.roles: dict[str, fp.Json] = {}
        self.workspace_roles: list[fp.Json] = []
        self.secrets: dict[str, fp.Json] = {}
        self.deployments: dict[str, fp.Json] = {}
        self.policy = policy()
        self.admin_policy = {
            "settingName": "ServicePrincipalAccessGlobalAPIs", "enabled": True,
            "canSpecifySecurityGroups": True,
            "enabledSecurityGroups": [{"graphId": ADMIN_GROUP, "name": "FabricAPIAdmin"}],
        }
        self.fail_password_write = False
        self.damage_policy_after_update = False
        self.hidden_member_reads = 0
        self.subscription_tenant = TENANT
        self.workspace_id = WORKSPACE

    @property
    def writes(self) -> list[tuple[str, str, fp.Json]]:
        return [call for call in self.calls if call[0] != "GET"]

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        self.calls.append((method, url, copy.deepcopy(kwargs)))
        parsed = urlsplit(url)
        path = parsed.path
        query = parse_qs(parsed.query)
        body = kwargs.get("json") or {}
        assert isinstance(body, dict)
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == (10, 60)
        headers = kwargs["headers"]
        assert isinstance(headers, dict) and headers["Authorization"] == f"Bearer {TOKEN}"
        if parsed.hostname == "graph.microsoft.com":
            if method == "GET" and path == "/v1.0/me":
                return response(data={"id": CALLER})
            collections = {
                "/v1.0/applications": self.app, "/v1.0/servicePrincipals": self.principal,
                "/v1.0/groups": self.group,
            }
            if method == "GET" and path in collections:
                item = collections[path]
                return response(data={"value": [item] if item is not None else []})
            if method == "POST" and path == "/v1.0/applications":
                self.app = {**copy.deepcopy(body), "id": APP, "appId": CLIENT, "passwordCredentials": []}
                return response(201, self.app)
            if method == "GET" and path == f"/v1.0/applications/{APP}":
                return response(data=self.app)
            if method == "POST" and path == f"/v1.0/applications/{APP}/addPassword":
                assert self.app is not None
                metadata = {**body["passwordCredential"], "keyId": PASSWORD}
                self.app["passwordCredentials"].append(metadata)
                return response(data={**metadata, "secretText": SECRET})
            if method == "POST" and path == "/v1.0/servicePrincipals":
                self.principal = {
                    **copy.deepcopy(body), "id": PRINCIPAL,
                    "appOwnerOrganizationId": TENANT, "servicePrincipalType": "Application",
                }
                return response(201, self.principal)
            if method == "GET" and path == f"/v1.0/servicePrincipals/{PRINCIPAL}":
                return response(data=self.principal)
            if method == "GET" and path == f"/v1.0/servicePrincipals/{PRINCIPAL}/transitiveMemberOf":
                memberships = [{"id": GROUP, "@odata.type": "#microsoft.graph.group"}] if PRINCIPAL in self.members else []
                return response(data={"value": memberships + self.extra_memberships})
            if method == "GET" and path == f"/v1.0/servicePrincipals/{PRINCIPAL}/appRoleAssignments":
                return response(data={"value": self.app_grants})
            if method == "GET" and path == f"/v1.0/servicePrincipals/{PRINCIPAL}/oauth2PermissionGrants":
                return response(data={"value": self.oauth_grants})
            if method == "POST" and path == "/v1.0/groups":
                self.group = {**copy.deepcopy(body), "id": GROUP}
                self.owners = [CALLER] if self.auto_owner else []
                self.members = [urlsplit(member).path.rsplit("/", 1)[-1] for member in body["members@odata.bind"]]
                return response(201, self.group)
            if method == "GET" and path == f"/v1.0/groups/{GROUP}":
                if "$expand" in query:
                    if self.hidden_member_reads:
                        self.hidden_member_reads -= 1
                        return response(data={"id": GROUP, "members": []})
                    return response(data={"id": GROUP, "members": [{"id": member} for member in self.members]})
                return response(data=self.group)
            if method == "GET" and path == f"/v1.0/groups/{GROUP}/owners":
                return response(data={"value": [{"id": owner} for owner in self.owners]})
            if method == "POST" and path == f"/v1.0/groups/{GROUP}/owners/$ref":
                owner = body["@odata.id"].rsplit("/", 1)[-1]
                assert owner not in self.owners
                self.owners.append(owner)
                return response(204)
            if method == "GET" and path == f"/v1.0/groups/{GROUP}/transitiveMemberOf":
                return response(data={"value": self.parent_groups})
            if method == "POST" and path == f"/v1.0/groups/{GROUP}/members/$ref":
                member = body["@odata.id"].rsplit("/", 1)[-1]
                assert member not in self.members, "duplicate member add"
                self.members.append(member)
                return response(204)
        elif parsed.hostname == "management.azure.com":
            if method == "GET" and path == f"/subscriptions/{SUBSCRIPTION}":
                return response(data={"subscriptionId": SUBSCRIPTION, "tenantId": self.subscription_tenant, "state": "Enabled"})
            if path == self.target.resource_group_id:
                if method == "PUT":
                    self.rg = {**copy.deepcopy(body), "id": path, "properties": {"provisioningState": "Succeeded"}}
                    return response(201, self.rg)
                if method == "GET":
                    return response(200, self.rg) if self.rg is not None else response(404)
            if path == self.target.vault_id:
                if method == "PUT":
                    self.vault = {**copy.deepcopy(body), "id": path}
                    self.vault["properties"].update(vaultUri=self.target.vault_uri, provisioningState="Succeeded")
                    return response(201, self.vault)
                if method == "GET":
                    return response(200, self.vault) if self.vault is not None else response(404)
            if path.startswith(f"{self.target.vault_id}/secrets/") and method == "GET":
                name = path.rsplit("/", 1)[-1]
                secret = self.secrets.get(name)
                if secret is None:
                    return response(404)
                return response(data={
                    "id": path, "name": name, "tags": secret["tags"],
                    "properties": {
                        "attributes": secret["attributes"], "contentType": "text/plain",
                        "secretUri": f"{self.target.vault_uri}secrets/{name}",
                        "secretUriWithVersion": f"{self.target.vault_uri}secrets/{name}/fake-version",
                    },
                })
            if path.startswith(f"{self.target.resource_group_id}/providers/Microsoft.Resources/deployments/"):
                if method == "PUT":
                    if self.fail_password_write:
                        return response(503, {"error": {"code": "Unavailable", "message": f"Never echo {SECRET} or {TOKEN}"}})
                    props = body["properties"]
                    for resource in props["template"]["resources"]:
                        name = resource["name"].rsplit("/", 1)[-1]
                        parameter = resource["properties"]["value"].split("'")[1]
                        self.secrets[name] = {
                            "value": props["parameters"][parameter]["value"],
                            "tags": copy.deepcopy(resource["tags"]),
                            "attributes": copy.deepcopy(resource["properties"]["attributes"]),
                        }
                    self.deployments[path] = {"properties": {"provisioningState": "Succeeded"}}
                    return response(201, self.deployments[path])
                if method == "GET":
                    return response(data=self.deployments[path])
            role_prefix = f"{self.target.vault_id}/providers/Microsoft.Authorization/roleAssignments/"
            if path.startswith(role_prefix):
                if method == "PUT":
                    self.roles[path] = {"id": path, "properties": {**copy.deepcopy(body["properties"]), "scope": self.target.vault_id}}
                    return response(201, self.roles[path])
                if method == "GET":
                    return response(200, self.roles[path]) if path in self.roles else response(404)
        elif parsed.hostname == "api.fabric.microsoft.com":
            assert headers["x-ms-fabric-skill"] == fp.FABRIC_SKILL
            if method == "GET" and path == f"/v1/workspaces/{WORKSPACE}":
                return response(data={"id": self.workspace_id})
            if path == f"/v1/workspaces/{WORKSPACE}/roleAssignments":
                if method == "GET":
                    return response(data={"value": self.workspace_roles})
                if method == "POST":
                    assignment = {"id": PRINCIPAL, **copy.deepcopy(body)}
                    self.workspace_roles.append(assignment)
                    return response(201, assignment)
            if method == "GET" and path == "/v1/admin/tenantsettings":
                return response(data={"tenantSettings": [self.policy, self.admin_policy]})
            if method == "POST" and path == f"/v1/admin/tenantsettings/{fp.PUBLIC_API_SETTING}/update":
                self.policy.update(copy.deepcopy(body))
                if self.damage_policy_after_update:
                    self.policy["excludedSecurityGroups"] = []
                return response(data={"tenantSettings": [self.policy]})
        elif parsed.hostname == urlsplit(self.target.vault_uri).hostname and path.startswith("/secrets/"):
            name = path.rsplit("/", 1)[-1]
            assert name in {"tenantid", "clientid", "clientsecret"}
            if method == "GET":
                return response(200, self.secrets[name]) if name in self.secrets else response(404)
            if method == "PUT":
                if name == "clientsecret" and self.fail_password_write:
                    return response(503, {"error": {"code": "Unavailable", "message": f"Never echo {SECRET} or {TOKEN}"}})
                self.secrets[name] = {**copy.deepcopy(body), "id": f"{self.target.vault_uri}secrets/{name}/fake-version"}
                return response(data=self.secrets[name])
        raise AssertionError(f"Unexpected offline HTTP route: {method} {parsed.hostname}{path}")


class OfflineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.target = fp.FeatureTarget.validate(**target_args())
        self.cloud = FakeCloud(self.target)
        self.session = Mock()
        self.session.request.side_effect = self.cloud.request
        self.credential = Mock()
        self.credential.get_token.return_value = SimpleNamespace(token=TOKEN, expires_on=time.time() + 3600)
        self.session_factory = self.enterContext(patch.object(fp.requests, "Session", return_value=self.session))
        self.credential_factory = self.enterContext(patch.object(fp, "AzureCliCredential", return_value=self.credential))
        self.sleep = self.enterContext(patch.object(fp.time, "sleep"))
        self.enterContext(patch.dict(os.environ, {"AZURE_CONFIG_DIR": "/tmp/offline-hydro-azure-profile"}))
        self.enterContext(patch.object(fp.os.path, "isdir", return_value=True))

    def ensure(self, **kwargs: object) -> dict[str, str]:
        return fp.ensure_feature_prerequisites(**{**target_args(), "allow_public_api_group": True, **kwargs})

    def client(self) -> fp._Client:
        return fp._Client(self.target)


class TargetAndOwnershipTests(OfflineTestCase):
    def test_names_include_full_workspace_ownership_and_short_unique_name(self):
        names = fp.feature_names(WORKSPACE.upper())
        self.assertEqual(names.application, "Hydro Feature Notebook 22222222")
        self.assertEqual(names.group, "Hydro Feature Fabric API 22222222")
        self.assertIn(WORKSPACE, names.marker)
        self.assertEqual(fp.FeatureTarget.validate(**{**target_args(), "tenant_id": TENANT.upper()}).tenant_id, TENANT)

    def test_invalid_targets_fail_before_authentication(self):
        cases = [
            ("tenant_id", "cfbb"), ("workspace_id", "../../bad"), ("subscription_id", "0" * 32),
            ("tenant_id", "00000000-0000-0000-0000-000000000000"),
            ("workspace_id", " " + WORKSPACE), ("resource_group", "rg/name"),
            ("resource_group", "rg?api-version=bad"), ("resource_group", "trailing."),
            ("vault_name", "kv--unsafe"), ("vault_name", "1bad"), ("vault_name", "ab"),
            ("vault_name", "UPPERCASE"), ("vault_name", "long" * 7),
            ("location", "Norway East"), ("location", "norwayeast&bad=yes"),
            ("allow_public_api_group", "yes"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(fp.FeaturePrerequisiteError):
                self.ensure(**{key: value})
        self.credential_factory.assert_not_called()
        self.session_factory.assert_not_called()

    def test_isolated_cli_profile_is_required_without_changing_defaults(self):
        for value in ("", "relative/profile", os.path.expanduser("~/.azure")):
            with self.subTest(value=value), patch.dict(os.environ, {"AZURE_CONFIG_DIR": value}):
                with self.assertRaises(fp.FeaturePrerequisiteError):
                    self.ensure()
        self.credential_factory.assert_not_called()

    def test_pure_bodies_have_only_explicitly_approved_grants(self):
        app = fp.application_body(self.target)
        self.assertEqual(app["requiredResourceAccess"], [])
        self.assertEqual(app["signInAudience"], "AzureADMyOrg")
        self.assertFalse({"spa", "web", "publicClient", "passwordCredentials"} & set(app))
        group = fp.group_body(self.target, PRINCIPAL, CALLER)
        self.assertEqual(group["members@odata.bind"], [f"{fp.GRAPH_BASE}/directoryObjects/{PRINCIPAL}"])
        self.assertNotIn("owners@odata.bind", group)
        self.assertFalse(group["isAssignableToRole"])
        self.assertFalse(group["mailEnabled"])
        vault = fp.vault_body(self.target)
        self.assertEqual(vault["tags"][fp.WORKSPACE_TAG], WORKSPACE)
        props = vault["properties"]
        self.assertEqual(props["sku"], {"family": "A", "name": "standard"})
        self.assertTrue(props["enableRbacAuthorization"])
        self.assertEqual(props["accessPolicies"], [])
        self.assertEqual(props["publicNetworkAccess"], "Disabled")
        self.assertEqual(props["networkAcls"]["defaultAction"], "Deny")
        self.assertEqual(props["createMode"], "default")
        self.assertTrue(props["enablePurgeProtection"])

    def test_application_collision_marker_is_not_just_short_prefix(self):
        app = {**fp.application_body(self.target), "id": APP, "appId": CLIENT}
        for description in (
            "", self.target.names.marker + " extra",
            "Hydro feature workspace: 22222222-ffff-4fff-8fff-ffffffffffff",
        ):
            with self.subTest(description=description), self.assertRaises(fp.FeaturePrerequisiteError):
                fp._validate_application({**app, "description": description}, self.target)
        fp._validate_application(app, self.target)

    def test_reusing_spa_or_api_permission_app_is_forbidden(self):
        app = {**fp.application_body(self.target), "id": APP, "appId": CLIENT}
        for changes in (
            {"spa": {"redirectUris": ["https://example.invalid/callback"]}},
            {"web": {"redirectUris": ["https://example.invalid/callback"]}},
            {"requiredResourceAccess": [{"resourceAppId": APP}]},
            {"isFallbackPublicClient": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(fp.FeaturePrerequisiteError):
                fp._validate_application({**app, **changes}, self.target)

    def test_group_must_be_static_nonprivileged_and_owned(self):
        group = {**fp.group_body(self.target, PRINCIPAL, CALLER), "id": GROUP}
        for changes in (
            {"description": "unrelated"}, {"mailEnabled": True}, {"securityEnabled": False},
            {"isAssignableToRole": True}, {"groupTypes": ["DynamicMembership"]},
            {"onPremisesSyncEnabled": True}, {"membershipRule": "user.enabled -eq true"},
        ):
            with self.subTest(changes=changes), self.assertRaises(fp.FeaturePrerequisiteError):
                fp._validate_group({**group, **changes}, self.target)

    def test_unowned_resource_group_stops_before_any_cloud_write(self):
        self.cloud.rg = {"id": self.target.resource_group_id, "location": self.target.location, "tags": {}}
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "collision"):
            self.ensure()
        self.assertEqual(self.cloud.writes, [])

    def test_vault_does_not_take_over_access_policies_or_open_private_network(self):
        base = {**fp.vault_body(self.target), "id": self.target.vault_id}
        base["properties"]["vaultUri"] = self.target.vault_uri
        for changes in (
            {"enableRbacAuthorization": False}, {"tenantId": CALLER},
            {"publicNetworkAccess": "Unrecognized"},
            {"sku": {"family": "A", "name": "premium"}},
        ):
            vault = copy.deepcopy(base)
            vault["properties"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(fp.FeaturePrerequisiteError):
                fp._validate_arm_resource(vault, self.target, vault=True)
        fp._validate_arm_resource(base, self.target, vault=True)

    def test_uuid5_role_assignment_is_stable_and_vault_scoped(self):
        one = fp.role_assignment_spec(self.target, PRINCIPAL, fp.SECRETS_USER)
        self.assertEqual(one, fp.role_assignment_spec(self.target, PRINCIPAL, fp.SECRETS_USER))
        self.assertNotEqual(one[0], fp.role_assignment_spec(self.target, CALLER, fp.SECRETS_OFFICER)[0])
        props = one[1]["properties"]
        self.assertEqual(props["principalId"], PRINCIPAL)
        self.assertEqual(props["principalType"], "ServicePrincipal")
        self.assertTrue(props["roleDefinitionId"].endswith(fp.SECRETS_USER))
        with self.assertRaises(fp.FeaturePrerequisiteError):
            fp.role_assignment_spec(self.target, PRINCIPAL, ADMIN_GROUP)
        actual = {"properties": {**props, "scope": self.target.vault_id}}
        fp._validate_role(actual, self.target, one[1])
        for changes in (
            {"principalId": CALLER}, {"principalType": "User"},
            {"roleDefinitionId": props["roleDefinitionId"].replace(fp.SECRETS_USER, fp.SECRETS_OFFICER)},
            {"scope": f"/subscriptions/{SUBSCRIPTION}"}, {"condition": "unexpected", "conditionVersion": "2.0"},
        ):
            with self.subTest(changes=changes), self.assertRaises(fp.FeaturePrerequisiteError):
                fp._validate_role({"properties": {**actual["properties"], **changes}}, self.target, one[1])

    def test_existing_workspace_admin_is_not_silently_reused(self):
        for role in ("Admin", "Member", "Viewer"):
            with self.subTest(role=role), self.assertRaises(fp.FeaturePrerequisiteError):
                fp._workspace_assignment([{"role": role, "principal": {"id": PRINCIPAL, "type": "ServicePrincipal"}}], PRINCIPAL)

    def test_duplicate_names_on_later_pages_are_never_adopted(self):
        base = f"{fp.GRAPH_BASE}/applications"
        self.session.request.side_effect = [
            response(data={"value": [{"id": APP}], "@odata.nextLink": base + "?page=2"}),
            response(data={"value": [{"id": CALLER}]}),
        ]
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "Ambiguous"):
            fp._find(self.client(), "applications", "displayName", self.target.names.application, fp.APP_SELECT)
        self.assertEqual(self.session.request.call_count, 2)

    def test_resource_provisioning_poll_is_bounded_and_terminal_failure_stops(self):
        self.cloud.rg = {
            "id": self.target.resource_group_id, "tags": self.target.tags, "location": self.target.location,
            "properties": {"provisioningState": "Updating"},
        }
        with patch.object(fp, "POLL_ATTEMPTS", 2), self.assertRaisesRegex(fp.FeaturePrerequisiteError, "bounded wait"):
            fp._ensure_arm_resource(self.client(), self.target, self.cloud.rg, vault=False)
        self.assertEqual(self.session.request.call_count, 2)
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(self.cloud.writes, [])
        self.cloud.rg["properties"]["provisioningState"] = "Failed"
        self.session.request.reset_mock()
        self.sleep.reset_mock()
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "provisioning failed"):
            fp._ensure_arm_resource(self.client(), self.target, self.cloud.rg, vault=False)
        self.assertEqual(self.session.request.call_count, 1)
        self.sleep.assert_not_called()


class PolicyTests(OfflineTestCase):
    def merge(self, setting: fp.Json, **kwargs: object) -> fp.Json | None:
        return fp.merge_public_api_policy(setting, **{
            "group_id": GROUP, "group_name": self.target.names.group,
            "allow_public_api_group": True, **kwargs,
        })

    def test_append_preserves_groups_exclusions_properties_and_all_delegations(self):
        before = policy()
        unchanged = copy.deepcopy(before)
        body = self.merge(before)
        self.assertIsNotNone(body)
        self.assertEqual(before, unchanged)
        self.assertEqual(body["enabledSecurityGroups"], before["enabledSecurityGroups"] + [
            {"graphId": GROUP, "name": self.target.names.group},
        ])
        self.assertEqual(set(body), set(fp.POLICY_FIELDS))
        for key in fp.POLICY_FIELDS:
            if key != "enabledSecurityGroups":
                self.assertEqual(body[key], before[key])
        self.assertNotIn("canSpecifySecurityGroups", body)

    def test_append_does_not_invent_absent_optional_policy_properties(self):
        before = policy()
        for key in ("properties", "excludedSecurityGroups"):
            del before[key]
        body = self.merge(before)
        self.assertNotIn("properties", body)
        self.assertNotIn("excludedSecurityGroups", body)

    def test_opt_out_refuses_only_an_actual_policy_change(self):
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "allow_public_api_group"):
            self.merge(policy(), allow_public_api_group=False)
        already = policy()
        already["enabledSecurityGroups"].append({"graphId": GROUP, "name": self.target.names.group})
        self.assertIsNone(self.merge(already, allow_public_api_group=False))
        for can_specify in (True, False):
            broad = {**policy(), "enabledSecurityGroups": [], "canSpecifySecurityGroups": can_specify}
            self.assertIsNone(self.merge(broad, allow_public_api_group=False))

    def test_disabled_excluded_other_switch_and_unknown_fields_fail_closed(self):
        cases = [
            {**policy(), "enabled": False},
            {**policy(), "excludedSecurityGroups": [{"graphId": GROUP, "name": "excluded"}]},
            {**policy(), "settingName": "ServicePrincipalAccessGlobalAPIs"},
            {**policy(), "delegateToFutureLevel": True},
            {**policy(), "canSpecifySecurityGroups": False},
        ]
        for setting in cases:
            with self.subTest(setting=setting), self.assertRaises(fp.FeaturePrerequisiteError):
                self.merge(setting)
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "exclusions"):
            self.merge(policy(), principal_group_ids=frozenset({EXCLUDED_GROUP}))

    def test_preflight_for_nonexistent_group_has_no_placeholder_identity(self):
        with self.assertRaises(fp.FeaturePrerequisiteError):
            self.merge(policy(), group_id=None, allow_public_api_group=False)
        body = self.merge(policy(), group_id=None)
        self.assertEqual(body["enabledSecurityGroups"], policy()["enabledSecurityGroups"])

    def test_reread_prevents_overwriting_concurrent_policy_change(self):
        baseline = policy()
        self.cloud.policy["delegateToCapacity"] = True
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "concurrently"):
            fp._ensure_policy(self.client(), baseline, GROUP, self.target, True, frozenset())
        self.assertEqual(self.cloud.writes, [])

    def test_policy_preservation_is_verified_after_post_without_rollback(self):
        self.cloud.damage_policy_after_update = True
        admin_before = copy.deepcopy(self.cloud.admin_policy)
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "preservation"):
            fp._ensure_policy(self.client(), policy(), GROUP, self.target, True, frozenset())
        self.assertEqual(len(self.cloud.writes), 1)
        self.assertEqual(self.cloud.writes[0][1], f"{fp.FABRIC_BASE}/admin/tenantsettings/{fp.PUBLIC_API_SETTING}/update")
        self.assertEqual(self.cloud.admin_policy, admin_before)

    def test_dedicated_group_cannot_also_be_allowed_for_admin_apis(self):
        self.cloud.admin_policy["enabledSecurityGroups"].append({"graphId": GROUP, "name": self.target.names.group})
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "another tenant setting"):
            fp._public_setting([self.cloud.policy, self.cloud.admin_policy], GROUP)

    def test_throttled_policy_post_is_not_retried_without_a_fresh_guard(self):
        self.session.request.side_effect = [
            response(data={"tenantSettings": [policy()]}),
            response(429, headers={"Retry-After": "1"}),
        ]
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "HTTP 429"):
            fp._ensure_policy(self.client(), policy(), GROUP, self.target, True, frozenset())
        self.assertEqual([call.args[0] for call in self.session.request.call_args_list], ["GET", "POST"])
        self.sleep.assert_not_called()


class HttpSafetyTests(OfflineTestCase):
    def test_unknown_http_error_redacts_body_and_keeps_diagnostics(self):
        self.session.request.side_effect = [response(403, {"error": {"message": SECRET, "token": TOKEN}})]
        with self.assertRaises(fp.FeaturePrerequisiteError) as caught:
            self.client().request(fp.GRAPH_SCOPE, "GET", f"{fp.GRAPH_BASE}/me", action="Read caller")
        text = str(caught.exception)
        for expected in ("HTTP 403", "resource=graph.microsoft.com", "Read caller", REQUEST_ID):
            self.assertIn(expected, text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(self.session.request.call_count, 1)
        self.credential.get_token.assert_called_once_with(fp.GRAPH_SCOPE)

    def test_malformed_success_body_is_not_echoed(self):
        malformed = response()
        malformed._content = SECRET.encode()
        self.session.request.side_effect = [malformed]
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "invalid JSON") as caught:
            self.client().object(fp.VAULT_SCOPE, "GET", f"{self.target.vault_uri}secrets/clientsecret", action="Read secret")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_authentication_and_transport_errors_are_redacted_and_never_retried(self):
        self.credential.get_token.side_effect = RuntimeError(f"CLI output {TOKEN} {SECRET}")
        with self.assertRaises(fp.FeaturePrerequisiteError) as caught:
            self.client().request(fp.GRAPH_SCOPE, "GET", f"{fp.GRAPH_BASE}/me", action="Read caller")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.session.request.assert_not_called()
        self.credential.get_token.side_effect = None
        self.session.request.side_effect = requests.ConnectionError(f"payload {SECRET}")
        with self.assertRaises(fp.FeaturePrerequisiteError) as caught:
            self.client().request(fp.GRAPH_SCOPE, "POST", f"{fp.GRAPH_BASE}/applications/{APP}/addPassword", action="Create password")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(self.session.request.call_count, 1)
        self.sleep.assert_not_called()

    def test_expected_rbac_403_retries_but_firewall_and_unknown_auth_errors_stop(self):
        forbidden = response(403, {"error": {"code": "Forbidden", "innererror": {"code": "ForbiddenByRbac"}}})
        self.session.request.side_effect = [forbidden, response(data={"value": "dummy"})]
        self.client().request(fp.VAULT_SCOPE, "GET", f"{self.target.vault_uri}secrets/tenantid",
                              action="Read secret", retry_codes=((403, "ForbiddenByRbac"),))
        self.assertEqual(self.session.request.call_count, 2)
        self.assertEqual(self.sleep.call_count, 1)
        for status, code in ((403, "ForbiddenByFirewall"), (401, "ForbiddenByRbac"), (500, "Unknown")):
            self.session.request.reset_mock()
            self.session.request.side_effect = [response(status, {"error": {"code": code, "message": SECRET}})]
            with self.subTest(status=status), self.assertRaises(fp.FeaturePrerequisiteError):
                self.client().request(fp.VAULT_SCOPE, "GET", f"{self.target.vault_uri}secrets/tenantid",
                                      action="Read secret", retry_codes=((403, "ForbiddenByRbac"),))
            self.assertEqual(self.session.request.call_count, 1)

    def test_propagation_and_throttling_retries_are_bounded_without_final_sleep(self):
        for status in (404, 429):
            self.session.request.reset_mock()
            self.sleep.reset_mock()
            self.session.request.side_effect = [response(status, headers={"Retry-After": "bad"})] * fp.RETRY_ATTEMPTS
            with self.subTest(status=status), self.assertRaisesRegex(fp.FeaturePrerequisiteError, f"HTTP {status}"):
                self.client().request(fp.GRAPH_SCOPE, "GET", f"{fp.GRAPH_BASE}/applications/{APP}",
                                      action="Read new app", retry_statuses=(404,))
            self.assertEqual(self.session.request.call_count, fp.RETRY_ATTEMPTS)
            self.assertEqual(self.sleep.call_count, fp.RETRY_ATTEMPTS - 1)
        self.session.request.side_effect = [response(429, headers={"Retry-After": "999"})]
        self.sleep.reset_mock()
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "bound"):
            self.client().request(fp.GRAPH_SCOPE, "GET", f"{fp.GRAPH_BASE}/me", action="Read caller")
        self.sleep.assert_not_called()

    def test_missing_resource_404_is_not_retried_or_treated_as_an_auth_error(self):
        self.session.request.side_effect = [response(404)]
        result = self.client().object(fp.ARM_SCOPE, "GET", f"{fp.ARM_BASE}{self.target.vault_id}",
                                      action="Discover vault", missing=True)
        self.assertIsNone(result)
        self.assertEqual(self.session.request.call_count, 1)
        self.sleep.assert_not_called()

    def test_redirect_is_not_followed(self):
        self.session.request.side_effect = [response(302, headers={"Location": "https://example.invalid/steal"})]
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "HTTP 302"):
            self.client().request(fp.GRAPH_SCOPE, "GET", f"{fp.GRAPH_BASE}/me", action="Read caller")
        self.assertFalse(self.session.request.call_args.kwargs["allow_redirects"])
        self.assertEqual(self.session.request.call_count, 1)

    def test_unsafe_target_is_refused_before_token_acquisition(self):
        for url in (
            "http://graph.microsoft.com/v1.0/me", "https://graph.microsoft.com.evil.invalid/v1.0/me",
            "https://user@graph.microsoft.com/v1.0/me", "https://graph.microsoft.com:444/v1.0/me",
            "https://graph.microsoft.com:invalid/v1.0/me", "https://graph.microsoft.com/v1.0/me#fragment",
            "https://graph.microsoft.com\\@example.invalid/v1.0/me",
        ):
            with self.subTest(url=url), self.assertRaises(fp.FeaturePrerequisiteError):
                self.client().request(fp.GRAPH_SCOPE, "GET", url, action="Read caller")
        self.credential.get_token.assert_not_called()
        self.session.request.assert_not_called()

    def test_all_list_protocols_paginate_empty_pages_and_preserve_fabric_header(self):
        for scope, base, link_key in (
            (fp.GRAPH_SCOPE, f"{fp.GRAPH_BASE}/applications", "@odata.nextLink"),
            (fp.ARM_SCOPE, f"{fp.ARM_BASE}/subscriptions", "nextLink"),
            (fp.FABRIC_SCOPE, f"{fp.FABRIC_BASE}/admin/tenantsettings", "continuationUri"),
        ):
            self.session.request.reset_mock()
            self.session.request.side_effect = [
                response(data={"value": [], link_key: base + "?page=2"}),
                response(data={"value": [{"id": APP}]}),
            ]
            with self.subTest(scope=scope):
                self.assertEqual(self.client().pages(scope, base, action="List"), [{"id": APP}])
                self.assertEqual(self.session.request.call_count, 2)
                if scope == fp.FABRIC_SCOPE:
                    for call in self.session.request.call_args_list:
                        self.assertEqual(call.kwargs["headers"]["x-ms-fabric-skill"], fp.FABRIC_SKILL)

    def test_fabric_token_only_continuation_is_encoded_not_interpolated(self):
        self.session.request.side_effect = [
            response(data={"value": [], "continuationToken": "a&b=secret-looking"}),
            response(data={"value": []}),
        ]
        self.client().pages(fp.FABRIC_SCOPE, f"{fp.FABRIC_BASE}/admin/tenantsettings", action="Settings")
        url = self.session.request.call_args.args[1]
        self.assertEqual(parse_qs(urlsplit(url).query), {"continuationToken": ["a&b=secret-looking"]})

    def test_continuation_cannot_change_host_resource_or_loop_forever(self):
        base = f"{fp.GRAPH_BASE}/applications"
        for next_url in ("https://example.invalid/apps", f"{fp.GRAPH_BASE}/groups", base):
            self.session.request.reset_mock()
            self.session.request.side_effect = [response(data={"value": [], "@odata.nextLink": next_url})]
            with self.subTest(next_url=next_url), self.assertRaises(fp.FeaturePrerequisiteError):
                self.client().pages(fp.GRAPH_SCOPE, base, action="List apps")
            self.assertEqual(self.session.request.call_count, 1)
        self.session.request.reset_mock()
        self.session.request.side_effect = [
            response(data={"value": [], "@odata.nextLink": base + "?page=2"}),
            response(data={"value": [], "@odata.nextLink": base + "?page=3"}),
        ]
        with patch.object(fp, "MAX_PAGES", 2), self.assertRaisesRegex(fp.FeaturePrerequisiteError, "exceeded"):
            self.client().pages(fp.GRAPH_SCOPE, base, action="List apps")
        self.assertEqual(self.session.request.call_count, 2)

    def test_expanded_members_workaround_refuses_truncation_instead_of_assuming_empty(self):
        self.session.request.side_effect = [response(data={
            "id": GROUP, "members": [{"id": PRINCIPAL}],
            "members@odata.nextLink": f"{fp.GRAPH_BASE}/groups/{GROUP}/members?$skiptoken=next",
        })]
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "truncated"):
            fp._member_ids(self.client(), GROUP)
        self.assertIn("$expand=members", self.session.request.call_args.args[1])


class EndToEndTests(OfflineTestCase):
    def test_first_run_and_rerun_are_scoped_secret_safe_and_idempotent(self):
        admin_before = copy.deepcopy(self.cloud.admin_policy)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.ensure()
            first_writes = copy.deepcopy(self.cloud.writes)
            second = self.ensure(allow_public_api_group=False)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(self.cloud.writes, first_writes)
        self.assertEqual(self.cloud.admin_policy, admin_before)
        self.assertEqual(self.cloud.members, [PRINCIPAL])
        self.assertEqual(result["notebook_client_id"], CLIENT)
        self.assertEqual(result["notebook_principal_id"], PRINCIPAL)
        self.assertEqual(result["group_id"], GROUP)
        self.assertEqual(result["key_vault_uri"], self.target.vault_uri)
        self.assertEqual(result["public_api_policy_changed"], "true")
        self.assertEqual(second["public_api_policy_changed"], "false")
        self.assertEqual(result["notebook_password_key_id"], second["notebook_password_key_id"])
        self.assertTrue(all(isinstance(value, str) for value in result.values()))
        self.assertNotIn(SECRET, repr(result))
        self.assertNotIn(TOKEN, repr(result))
        self.assertEqual(self.cloud.secrets["tenantid"]["value"], TENANT)
        self.assertEqual(self.cloud.secrets["clientid"]["value"], CLIENT)
        self.assertEqual(self.cloud.secrets["clientsecret"]["value"], SECRET)
        self.assertEqual(len(self.cloud.app["passwordCredentials"]), 1)
        expiry = fp._date(result["notebook_password_expires_on"], "test expiry")
        self.assertGreater(expiry, datetime.now(timezone.utc) + timedelta(days=89))
        self.assertLessEqual(expiry, datetime.now(timezone.utc) + timedelta(days=90))
        role_props = [role["properties"] for role in self.cloud.roles.values()]
        self.assertEqual({props["scope"] for props in role_props}, {self.target.vault_id})
        self.assertEqual({(props["principalId"], props["roleDefinitionId"].rsplit("/", 1)[-1])
                          for props in role_props}, {(CALLER, fp.SECRETS_OFFICER), (PRINCIPAL, fp.SECRETS_USER)})
        self.assertEqual(self.cloud.workspace_roles[0]["role"], "Contributor")
        self.assertEqual(self.cloud.workspace_roles[0]["principal"], {"id": PRINCIPAL, "type": "ServicePrincipal"})
        for method, url, call in self.cloud.calls:
            self.assertNotIn("/git", url)
            self.assertNotIn("/spa", url)
            self.assertNotIn("/oauth2PermissionGrants", url if method != "GET" else "")
            self.assertNotEqual(method, "DELETE")
            self.assertNotEqual(method, "PATCH")
            if urlsplit(url).hostname == "api.fabric.microsoft.com":
                self.assertEqual(call["headers"]["x-ms-fabric-skill"], fp.FABRIC_SKILL)
                if method == "POST":
                    self.assertIn(urlsplit(url).path, {
                        f"/v1/admin/tenantsettings/{fp.PUBLIC_API_SETTING}/update",
                        f"/v1/workspaces/{WORKSPACE}/roleAssignments",
                    })
        self.credential_factory.assert_called_with(tenant_id=TENANT, process_timeout=60)
        self.assertEqual(os.environ["AZURE_CONFIG_DIR"], "/tmp/offline-hydro-azure-profile")

    def test_policy_opt_out_and_disabled_setting_fail_before_writes(self):
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "allow_public_api_group"):
            self.ensure(allow_public_api_group=False)
        self.assertEqual(self.cloud.writes, [])
        self.cloud.policy["enabled"] = False
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "disabled"):
            self.ensure()
        self.assertEqual(self.cloud.writes, [])

    def test_initial_group_bind_is_polled_not_added_twice_during_propagation(self):
        self.cloud.hidden_member_reads = 3
        self.ensure()
        self.assertEqual(self.cloud.members, [PRINCIPAL])
        self.assertFalse(any("/members/$ref" in url for _, url, _ in self.cloud.writes))
        self.assertEqual(self.sleep.call_count, 2)

    def test_group_membership_propagation_is_bounded_before_policy_write(self):
        self.cloud.hidden_member_reads = 100
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "propagation exceeded"):
            self.ensure()
        reads = [url for method, url, _ in self.cloud.calls if method == "GET" and "$expand=members" in url]
        self.assertEqual(len(reads), fp.RETRY_ATTEMPTS + 1)
        self.assertFalse(any("/members/$ref" in url or "/tenantsettings/" in url for _, url, _ in self.cloud.writes))
        self.assertEqual(self.cloud.secrets, {})

    def test_subscription_and_workspace_must_match_target_before_writes(self):
        self.cloud.subscription_tenant = CALLER
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "Subscription"):
            self.ensure()
        self.assertEqual(self.cloud.writes, [])
        self.cloud.subscription_tenant = TENANT
        self.cloud.workspace_id = APP
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "different workspace"):
            self.ensure()
        self.assertEqual(self.cloud.writes, [])

    def test_unowned_app_collision_stops_before_resource_creation(self):
        self.cloud.app = {**fp.application_body(self.target), "id": APP, "appId": CLIENT, "description": "arbitrary app"}
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "collision"):
            self.ensure()
        self.assertEqual(self.cloud.writes, [])

    def test_foreign_memberships_or_api_grants_are_not_ignored_or_removed(self):
        self.ensure()
        first_writes = copy.deepcopy(self.cloud.writes)
        for attribute, value in (
            ("members", [PRINCIPAL, CALLER]),
            ("parent_groups", [{"id": ADMIN_GROUP}]),
            ("extra_memberships", [{"id": ADMIN_GROUP}]),
            ("app_grants", [{"id": APP}]),
            ("oauth_grants", [{"id": APP}]),
        ):
            previous = copy.deepcopy(getattr(self.cloud, attribute))
            setattr(self.cloud, attribute, value)
            with self.subTest(attribute=attribute), self.assertRaises(fp.FeaturePrerequisiteError):
                self.ensure()
            self.assertEqual(self.cloud.writes, first_writes)
            setattr(self.cloud, attribute, previous)

    def test_existing_other_password_is_never_rotated_or_removed(self):
        other = {"keyId": CALLER, "displayName": "Manual credential"}
        self.cloud.app = {**fp.application_body(self.target), "id": APP, "appId": CLIENT, "passwordCredentials": [other]}
        self.ensure()
        self.ensure()
        self.assertEqual(self.cloud.app["passwordCredentials"][0], other)
        self.assertEqual(len(self.cloud.app["passwordCredentials"]), 2)

    def test_expired_or_mismatched_credentials_stop_instead_of_rotating(self):
        self.ensure()
        first_writes = copy.deepcopy(self.cloud.writes)
        self.cloud.secrets["tenantid"]["tags"]["tenantId"] = CALLER
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "tenantid"):
            self.ensure()
        self.cloud.secrets["tenantid"]["tags"]["tenantId"] = TENANT
        self.cloud.secrets["clientsecret"]["attributes"]["exp"] = int(time.time()) - 10
        with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "expired"):
            self.ensure()
        self.assertEqual(self.cloud.writes, first_writes)
        self.assertEqual(len(self.cloud.app["passwordCredentials"]), 1)

    def test_password_persistence_failure_is_redacted_and_rerun_does_not_add_another_password(self):
        self.cloud.fail_password_write = True
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "persistence failed") as caught:
                self.ensure()
            with self.assertRaisesRegex(fp.FeaturePrerequisiteError, "another password will not be created"):
                self.ensure()
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("HTTP 503", str(caught.exception))
        self.assertIn(REQUEST_ID, str(caught.exception))
        self.assertEqual(len(self.cloud.app["passwordCredentials"]), 1)
        self.assertNotIn("clientsecret", self.cloud.secrets)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        writes = [url for _, url, _ in self.cloud.writes if url.endswith("/addPassword")]
        self.assertEqual(len(writes), 1)

    def test_private_initialization_uses_secure_parameters_and_no_data_plane_calls(self):
        self.ensure()
        for method, url, call in self.cloud.calls:
            self.assertNotEqual(urlsplit(url).hostname, urlsplit(self.target.vault_uri).hostname)
            if method == "PUT" and "/deployments/" in url:
                props = call["json"]["properties"]
                self.assertEqual(props["mode"], "Incremental")
                self.assertNotIn(SECRET, repr(props["template"]))
                self.assertNotIn("outputs", props["template"])
                self.assertTrue(all(parameter["type"] == "secureString" for parameter in props["template"]["parameters"].values()))
                self.assertEqual({resource["name"] for resource in props["template"]["resources"]},
                                 {f"{self.target.vault_name}/{name}" for name in ("tenantid", "clientid", "clientsecret")})
        self.assertEqual(self.cloud.vault["properties"]["publicNetworkAccess"], "Disabled")

    def test_existing_public_vault_keeps_the_original_data_plane_path(self):
        self.cloud.vault = {**fp.vault_body(self.target), "id": self.target.vault_id}
        self.cloud.vault["properties"].update(
            publicNetworkAccess="Enabled", networkAcls={"defaultAction": "Allow"},
            vaultUri=self.target.vault_uri, provisioningState="Succeeded",
        )
        self.ensure()
        first_writes = copy.deepcopy(self.cloud.writes)
        self.ensure()
        self.assertEqual(first_writes, self.cloud.writes)
        self.assertFalse(self.cloud.deployments)
        self.assertTrue(any(urlsplit(url).hostname == urlsplit(self.target.vault_uri).hostname
                            for _, url, _ in self.cloud.calls))

    def test_existing_identifier_secrets_do_not_block_initial_password_creation(self):
        self.ensure()
        self.cloud.app["passwordCredentials"] = []
        del self.cloud.secrets["clientsecret"]
        self.ensure()
        self.assertIn("clientsecret", self.cloud.secrets)
        self.assertEqual(len(self.cloud.app["passwordCredentials"]), 1)

    def test_automatically_assigned_group_owner_is_not_added_twice(self):
        self.ensure()
        self.assertEqual(self.cloud.owners, [CALLER])
        self.assertFalse(any("/owners/$ref" in url for _, url, _ in self.cloud.writes))

    def test_group_owner_is_added_when_graph_does_not_assign_creator(self):
        self.cloud.auto_owner = False
        self.ensure()
        first_writes = copy.deepcopy(self.cloud.writes)
        self.ensure()
        self.assertEqual(self.cloud.owners, [CALLER])
        self.assertEqual(self.cloud.writes, first_writes)


if __name__ == "__main__":
    unittest.main()
