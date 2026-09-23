"""Scoped prerequisites for the orchestrator's Git-free feature workspace mode.

This module has no CLI, import-time authentication, logging, or file output. It
uses the existing AzureCliCredential + requests pattern, inheriting the parent's
isolated AZURE_CONFIG_DIR without logging in or changing Azure CLI defaults.

The caller needs delegated Graph Application.ReadWrite.All, Group.ReadWrite.All
and Directory.Read.All (the last is for auditing existing OAuth grants), plus
the corresponding Entra privileges. Fabric needs Tenant.ReadWrite.All and
Workspace.ReadWrite.All with tenant administrator/workspace role-management
privileges. ARM needs resource/vault creation and vault-scoped RBAC assignment
permissions. None of those administrative permissions are granted to the notebook.

New vaults use Standard, RBAC, soft delete, purge protection and private-only
networking. Private vault credentials are initialized by an incremental ARM
deployment with secure parameters; no local data-plane access is attempted.
The workspace preflight provisions the private endpoint for notebook reads.
Only the caller gets Secrets Officer; the notebook gets Secrets User on this
vault and Contributor on this workspace. No Git or SPA APIs are used.

Operations are bounded but not transactional. Serialize calls for one workspace:
Fabric's update API has no documented conditional-write/ETag contract. We reread
before writing and verify afterwards, but cannot eliminate that final race.
Nothing is deleted or rolled back. A password expires after 90 days; reruns reuse
an enabled, unexpired vault credential linked to Graph password metadata, not a
new password. Expired, mismatched, or lost credentials require explicit operator
recovery. In particular an interruption between addPassword and the vault write
must not generate another password on the next run. Credential validation here
checks metadata, not a notebook sign-in smoke test.

REST contracts:
https://learn.microsoft.com/rest/api/fabric/admin/tenants/update-tenant-setting
https://learn.microsoft.com/graph/api/application-addpassword
https://learn.microsoft.com/graph/api/group-list-members
"""

from __future__ import annotations

import copy
import hmac
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import requests
from azure.identity import AzureCliCredential

Json = dict[str, Any]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ARM_BASE = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
VAULT_SCOPE = "https://vault.azure.net/.default"
FABRIC_SKILL = "git-integration-operations-cli"
PUBLIC_API_SETTING = "ServicePrincipalAccessPermissionAPIs"
RESOURCE_API = "2021-04-01"
VAULT_API = "2024-11-01"
SECRET_API = "7.4"
ROLE_API = "2022-04-01"
DEPLOYMENT_API = "2022-09-01"
SECRETS_OFFICER = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
SECRETS_USER = "4633458b-17de-408a-b874-0445c86b69e6"
MANAGED_BY = "hydro-feature-bootstrap"
WORKSPACE_TAG = "featureWorkspaceId"
RETRY_ATTEMPTS = 6
POLL_ATTEMPTS = 24
POLL_SECONDS = 5
MAX_PAGES = 100
PASSWORD_DAYS = 90
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
POLICY_FIELDS = (
    "enabled", "enabledSecurityGroups", "excludedSecurityGroups", "properties",
    "delegateToCapacity", "delegateToDomain", "delegateToWorkspace",
)
POLICY_READ_ONLY = {"settingName", "title", "tenantSettingGroup", "canSpecifySecurityGroups"}


class FeaturePrerequisiteError(RuntimeError):
    """An actionable error that never includes tokens, secrets or HTTP bodies."""


class TenantSecurityGroup(TypedDict):
    graphId: str
    name: str


@dataclass(frozen=True)
class FeatureNames:
    application: str
    group: str
    mail_nickname: str
    marker: str
    password: str


def _uuid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not UUID_RE.fullmatch(value) or UUID(value).int == 0:
        raise FeaturePrerequisiteError(f"{field} must be a nonzero, hyphenated UUID.")
    return str(UUID(value))


def feature_names(workspace_id: str) -> FeatureNames:
    workspace_id = _uuid(workspace_id, "workspace_id")
    suffix = workspace_id[:8]
    marker = f"Hydro feature workspace: {workspace_id}"
    return FeatureNames(
        f"Hydro Feature Notebook {suffix}",
        f"Hydro Feature Fabric API {suffix}",
        f"hydro-feature-api-{suffix}",
        marker,
        f"{marker}; notebook password",
    )


@dataclass(frozen=True)
class FeatureTarget:
    tenant_id: str
    workspace_id: str
    subscription_id: str
    resource_group: str
    vault_name: str
    location: str

    @classmethod
    def validate(cls, *, tenant_id: str, workspace_id: str, subscription_id: str,
                 resource_group: str, vault_name: str, location: str) -> FeatureTarget:
        ids = [_uuid(value, key) for key, value in (
            ("tenant_id", tenant_id), ("workspace_id", workspace_id),
            ("subscription_id", subscription_id),
        )]
        # Deliberately narrower than Azure's full Unicode RG-name grammar.
        if (not isinstance(resource_group, str)
                or not re.fullmatch(r"[A-Za-z0-9_().-]{1,90}", resource_group)
                or resource_group.endswith(".")):
            raise FeaturePrerequisiteError("resource_group must be a safe Azure resource-group name.")
        if (not isinstance(vault_name, str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{1,22}[a-z0-9]", vault_name)
                or "--" in vault_name):
            raise FeaturePrerequisiteError("vault_name must be a lowercase Azure Key Vault name.")
        if not isinstance(location, str) or not re.fullmatch(r"[a-z][a-z0-9]{1,63}", location):
            raise FeaturePrerequisiteError("location must be an Azure region code, not a display name.")
        return cls(*ids, resource_group, vault_name, location)

    @property
    def names(self) -> FeatureNames:
        return feature_names(self.workspace_id)

    @property
    def tags(self) -> dict[str, str]:
        return {"managedBy": MANAGED_BY, WORKSPACE_TAG: self.workspace_id}

    @property
    def resource_group_id(self) -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"

    @property
    def vault_id(self) -> str:
        return f"{self.resource_group_id}/providers/Microsoft.KeyVault/vaults/{self.vault_name}"

    @property
    def vault_uri(self) -> str:
        return f"https://{self.vault_name}.vault.azure.net/"


def application_body(target: FeatureTarget) -> Json:
    return {
        "displayName": target.names.application,
        "description": target.names.marker,
        "signInAudience": "AzureADMyOrg",
        "requiredResourceAccess": [],
    }


def group_body(target: FeatureTarget, principal_id: str, caller_id: str) -> Json:
    return {
        "displayName": target.names.group,
        "description": target.names.marker,
        "mailEnabled": False,
        "mailNickname": target.names.mail_nickname,
        "securityEnabled": True,
        "groupTypes": [],
        "isAssignableToRole": False,
        "owners@odata.bind": [f"{GRAPH_BASE}/users/{_uuid(caller_id, 'caller_id')}"],
        "members@odata.bind": [f"{GRAPH_BASE}/directoryObjects/{_uuid(principal_id, 'principal_id')}"],
    }


def vault_body(target: FeatureTarget) -> Json:
    return {
        "location": target.location,
        "tags": target.tags,
        "properties": {
            "tenantId": target.tenant_id,
            "sku": {"family": "A", "name": "standard"},
            "enableRbacAuthorization": True,
            "accessPolicies": [],
            "enabledForDeployment": False,
            "enabledForDiskEncryption": False,
            "enabledForTemplateDeployment": False,
            "enableSoftDelete": True,
            "softDeleteRetentionInDays": 90,
            "enablePurgeProtection": True,
            "createMode": "default",  # Never recover a soft-deleted name collision.
            "publicNetworkAccess": "Disabled",
            "networkAcls": {"bypass": "None", "defaultAction": "Deny"},
        },
    }


def _objects(value: Any, field: str) -> list[Json]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise FeaturePrerequisiteError(f"Invalid collection for {field}; refusing an incomplete safety check.")
    return value


def _policy_body(setting: Json) -> Json:
    setting = copy.deepcopy(setting)
    for key in ("enabledSecurityGroups", "excludedSecurityGroups", "properties"):
        if setting.get(key) is None:
            setting[key] = []
    if setting.get("settingName") != PUBLIC_API_SETTING:
        raise FeaturePrerequisiteError("Only ServicePrincipalAccessPermissionAPIs may be changed.")
    if set(setting) - set(POLICY_FIELDS) - POLICY_READ_ONLY:
        raise FeaturePrerequisiteError("Unrecognized tenant-setting fields; cannot guarantee preservation.")
    if setting.get("enabled") is not True:
        raise FeaturePrerequisiteError("ServicePrincipalAccessPermissionAPIs is disabled; not enabling it.")
    for key in ("enabledSecurityGroups", "excludedSecurityGroups"):
        seen: set[str] = set()
        for group in _objects(setting.get(key, []), key):
            group_id = _uuid(group.get("graphId"), "tenant setting graphId")
            if (set(group) != {"graphId", "name"} or not isinstance(group["name"], str)
                    or not group["name"] or group_id in seen):
                raise FeaturePrerequisiteError("Invalid or duplicate tenant-setting security group.")
            seen.add(group_id)
    _objects(setting.get("properties", []), "tenant setting properties")
    for key in ("canSpecifySecurityGroups", *POLICY_FIELDS[4:]):
        if key in setting and type(setting[key]) is not bool:
            raise FeaturePrerequisiteError("Invalid tenant-setting delegation/scope flag.")
    if setting.get("canSpecifySecurityGroups") is False and setting.get("enabledSecurityGroups"):
        raise FeaturePrerequisiteError("Inconsistent tenant-wide and group-scoped policy; refusing to guess.")
    return copy.deepcopy({key: setting[key] for key in POLICY_FIELDS if key in setting})


def merge_public_api_policy(setting: Json, *, group_id: str | None, group_name: str,
                            allow_public_api_group: bool,
                            principal_group_ids: frozenset[str] = frozenset()) -> Json | None:
    """Return a preserving append-only update, or None if no policy write is needed.

    An already organization-wide enabled setting is left alone, never created.
    The caller must supply verified transitive membership for a reused principal.
    """
    if type(allow_public_api_group) is not bool:
        raise FeaturePrerequisiteError("allow_public_api_group must be a boolean.")
    body = _policy_body(setting)
    memberships = {_uuid(item, "principal group id") for item in principal_group_ids}
    if group_id is not None:
        group_id = _uuid(group_id, "group_id")
        memberships.add(group_id)
    excluded = {_uuid(item["graphId"], "excluded group id")
                for item in body.get("excludedSecurityGroups", [])}
    if memberships & excluded:
        raise FeaturePrerequisiteError("Tenant-setting exclusions prevent safely scoped notebook API access.")
    allowed = body.get("enabledSecurityGroups", [])
    if not allowed:  # Existing organization-wide access; do not rewrite/restrict it.
        return None
    if group_id and any(_uuid(item["graphId"], "enabled group id") == group_id for item in allowed):
        return None
    if not allow_public_api_group:
        raise FeaturePrerequisiteError("A tenant-policy append is needed; allow_public_api_group is False.")
    if group_id is None:
        # Preflight only. No request may use a placeholder identity.
        return body
    if not isinstance(group_name, str) or not group_name:
        raise FeaturePrerequisiteError("The dedicated group's display name is required.")
    entry: TenantSecurityGroup = {"graphId": group_id, "name": group_name}
    body["enabledSecurityGroups"].append(entry)
    return body


def _policy_state(setting: Json) -> Json:
    """Compare policy semantics, allowing server omission of empty/false defaults."""
    body = _policy_body(setting)
    for key in ("enabledSecurityGroups", "excludedSecurityGroups"):
        body[key] = sorted(body.get(key, []), key=lambda item: item["graphId"].lower())
    body.setdefault("properties", [])
    for key in POLICY_FIELDS[4:]:
        body.setdefault(key, False)
    body["canSpecifySecurityGroups"] = setting.get("canSpecifySecurityGroups")
    return body


def _validate_url(url: str, base: str) -> None:
    try:
        parsed, expected = urlsplit(url), urlsplit(base)
        valid = (
            parsed.scheme == "https" and parsed.hostname == expected.hostname
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and not parsed.fragment and not any(ord(char) < 33 for char in url)
            and "\\" not in url
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise FeaturePrerequisiteError("Unsafe HTTP/continuation target; credentials were not forwarded.")


def _request_id(response: requests.Response) -> str:
    for name in ("x-ms-request-id", "request-id", "x-ms-correlation-request-id", "client-request-id"):
        value = response.headers.get(name, "")
        if UUID_RE.fullmatch(value):
            return value
    return "unavailable"


def _http_failure(response: requests.Response, action: str, resource: str) -> FeaturePrerequisiteError:
    detail = ""
    if action in {"Creating dedicated Fabric API group", "Adding notebook only to dedicated group"}:
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                detail = f" {error['message'][:1000]}"
        except (ValueError, AttributeError):
            pass
    return FeaturePrerequisiteError(
        f"{action}: HTTP {response.status_code}; resource={resource}; requestId={_request_id(response)}.{detail}"
    )


def _json(response: requests.Response, action: str, resource: str) -> Json:
    try:
        data = response.json()
    except ValueError:
        raise _http_failure(response, f"{action} (invalid JSON)", resource) from None
    if not isinstance(data, dict):
        raise _http_failure(response, f"{action} (invalid object)", resource)
    return data


def _error_codes(response: requests.Response) -> set[str]:
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return set()
    if not isinstance(error, dict):
        return set()
    codes = {error.get("code")} if isinstance(error.get("code"), str) else set()
    inner = error.get("innererror", error.get("innerError", {}))
    if isinstance(inner, dict) and isinstance(inner.get("code"), str):
        codes.add(inner["code"])
    return codes


class _Client:
    """No redirects, raw-body exceptions, unbounded retries or ambient credentials."""

    def __init__(self, target: FeatureTarget) -> None:
        self.credential = AzureCliCredential(tenant_id=target.tenant_id, process_timeout=60)
        self.session = requests.Session()
        self.tokens: dict[str, tuple[str, float]] = {}
        self.bases = {
            GRAPH_SCOPE: GRAPH_BASE, ARM_SCOPE: ARM_BASE,
            FABRIC_SCOPE: FABRIC_BASE, VAULT_SCOPE: target.vault_uri,
        }

    def close(self) -> None:
        self.tokens.clear()
        self.session.close()
        self.credential.close()

    def request(self, scope: str, method: str, url: str, *, action: str,
                ok: tuple[int, ...] = (200,), body: Json | None = None,
                retry_statuses: tuple[int, ...] = (),
                retry_codes: tuple[tuple[int, str], ...] = (),
                retry_throttling: bool = True) -> requests.Response:
        if scope not in self.bases:
            raise FeaturePrerequisiteError("Unknown token audience.")
        _validate_url(url, self.bases[scope])
        parsed = urlsplit(url)
        resource = f"{parsed.hostname}{parsed.path}"  # Never include continuation/query tokens.
        for attempt in range(RETRY_ATTEMPTS):
            token, expiry = self.tokens.get(scope, ("", 0.0))
            if not token or time.time() >= expiry - 300:
                try:
                    access = self.credential.get_token(scope)
                    token, expiry = access.token, float(access.expires_on)
                    if not isinstance(token, str) or not token or expiry <= time.time():
                        raise ValueError("invalid token metadata")
                except Exception:
                    # Auth libraries can embed CLI output/tokens in their exceptions.
                    # Redact at this boundary, fail immediately, and never retry it.
                    raise FeaturePrerequisiteError(
                        f"{action}: authentication failed; HTTP unavailable; "
                        f"resource={resource}; requestId=unavailable."
                    ) from None
                self.tokens[scope] = (token, expiry)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "x-ms-client-request-id": str(uuid4()),
            }
            if scope == FABRIC_SCOPE:
                headers["x-ms-fabric-skill"] = FABRIC_SKILL
            try:
                response = self.session.request(
                    method, url, headers=headers, json=body,
                    timeout=(10, 60), allow_redirects=False,
                )
            except requests.RequestException:
                raise FeaturePrerequisiteError(
                    f"{action}: transport failure (outcome may be unknown); HTTP unavailable; "
                    f"resource={resource}; requestId={headers['x-ms-client-request-id']}."
                ) from None
            if response.status_code in ok:
                return response
            retry = (response.status_code == 429 and retry_throttling) or response.status_code in retry_statuses
            if not retry and retry_codes:
                codes = _error_codes(response)
                retry = any(response.status_code == status and code in codes for status, code in retry_codes)
            if not retry or attempt == RETRY_ATTEMPTS - 1:
                raise _http_failure(response, action, resource)
            delay = min(30, 5 * 2 ** attempt)
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdecimal():
                if int(retry_after) > 60:
                    raise _http_failure(response, f"{action} (retry delay exceeds bound)", resource)
                delay = max(delay, int(retry_after))
            time.sleep(delay)
        raise AssertionError("unreachable retry loop")

    def object(self, scope: str, method: str, url: str, *, action: str,
               missing: bool = False, **kwargs: Any) -> Json | None:
        if missing:
            kwargs["ok"] = (200, 404)
        response = self.request(scope, method, url, action=action, **kwargs)
        if missing and response.status_code == 404:
            return None
        return _json(response, action, urlsplit(self.bases[scope]).hostname or "unknown")

    def pages(self, scope: str, url: str, *, action: str,
              retry_statuses: tuple[int, ...] = (), collection_key: str = "value") -> list[Json]:
        result: list[Json] = []
        seen: set[str] = set()
        first = url
        for _ in range(MAX_PAGES):
            if url in seen:
                raise FeaturePrerequisiteError(f"{action}: repeated continuation; pagination stopped.")
            _validate_url(url, self.bases[scope])
            # A continuation cannot silently switch collections/workspaces.
            if urlsplit(url).path != urlsplit(first).path:
                raise FeaturePrerequisiteError(f"{action}: continuation changed resource path.")
            seen.add(url)
            page = self.object(scope, "GET", url, action=action, retry_statuses=retry_statuses)
            assert page is not None
            result.extend(_objects(page.get(collection_key), action))
            next_url = page.get("@odata.nextLink") or page.get("nextLink") or page.get("continuationUri")
            if not next_url and page.get("continuationToken"):
                query = [(key, value) for key, value in parse_qsl(urlsplit(first).query)
                         if key != "continuationToken"]
                query.append(("continuationToken", page["continuationToken"]))
                next_url = urlunsplit(urlsplit(first)._replace(query=urlencode(query)))
            if not next_url:
                return result
            if not isinstance(next_url, str):
                raise FeaturePrerequisiteError(f"{action}: invalid continuation.")
            url = next_url
        raise FeaturePrerequisiteError(f"{action}: pagination exceeded {MAX_PAGES} pages.")


def _owned_description(resource: Json, target: FeatureTarget, kind: str, name: str) -> None:
    description = resource.get("description")
    if (resource.get("displayName") != name or not isinstance(description, str)
            or target.names.marker not in description.splitlines()):
        raise FeaturePrerequisiteError(f"{kind} name collision: full workspace ownership marker is required.")
    _uuid(resource.get("id"), f"{kind} object id")


def _validate_application(app: Json, target: FeatureTarget) -> None:
    _owned_description(app, target, "Notebook application", target.names.application)
    _uuid(app.get("appId"), "notebook client id")
    if app.get("signInAudience") != "AzureADMyOrg" or app.get("requiredResourceAccess", []):
        raise FeaturePrerequisiteError("Notebook app is not a permission-free, single-tenant application.")
    for platform in ("spa", "web", "publicClient"):
        platform_data = app.get(platform) or {}
        if not isinstance(platform_data, dict) or platform_data.get("redirectUris"):
            raise FeaturePrerequisiteError("Notebook app has interactive/SPA redirects; refusing to reuse or modify it.")
    if app.get("isFallbackPublicClient"):
        raise FeaturePrerequisiteError("Notebook app permits public-client authentication; refusing reuse.")
    _objects(app.get("passwordCredentials", []), "application password metadata")


def _validate_principal(principal: Json, app: Json, target: FeatureTarget) -> None:
    _owned_description(principal, target, "Notebook service principal", target.names.application)
    if (_uuid(principal.get("appId"), "service principal client id") != _uuid(app["appId"], "client id")
            or _uuid(principal.get("appOwnerOrganizationId"), "principal home tenant") != target.tenant_id
            or principal.get("servicePrincipalType") != "Application"
            or principal.get("accountEnabled") is not True):
        raise FeaturePrerequisiteError("Notebook service principal has an unexpected app, tenant, type or status.")


def _validate_group(group: Json, target: FeatureTarget) -> None:
    _owned_description(group, target, "Fabric API group", target.names.group)
    if (group.get("securityEnabled") is not True or group.get("mailEnabled") is not False
            or group.get("groupTypes") != [] or group.get("isAssignableToRole") not in (None, False)
            or group.get("onPremisesSyncEnabled") is True or group.get("membershipRule")):
        raise FeaturePrerequisiteError("Dedicated API group must be a static, cloud-only, non-role-assignable security group.")


def _validate_arm_resource(resource: Json, target: FeatureTarget, *, vault: bool, ready: bool = True) -> None:
    expected_id = target.vault_id if vault else target.resource_group_id
    tags = resource.get("tags") or {}
    if (not isinstance(tags, dict) or any(tags.get(key) != value for key, value in target.tags.items())
            or str(resource.get("id", "")).lower() != expected_id.lower()):
        raise FeaturePrerequisiteError("Azure resource name collision: matching workspace ownership tags are required.")
    if str(resource.get("location", "")).lower() != target.location:
        raise FeaturePrerequisiteError("Owned Azure resource is in a different location; refusing to modify it.")
    if vault and ready:
        properties = resource.get("properties") or {}
        if not isinstance(properties, dict):
            raise FeaturePrerequisiteError("Owned vault returned invalid properties.")
        sku = properties.get("sku") or {}
        network = properties.get("networkAcls") or {}
        if (not isinstance(sku, dict) or not isinstance(network, dict)
                or str(properties.get("tenantId", "")).lower() != target.tenant_id
                or str(sku.get("name", "")).lower() != "standard" or sku.get("family") != "A"
                or properties.get("enableRbacAuthorization") is not True
                or properties.get("accessPolicies")
                or properties.get("publicNetworkAccess", "Enabled") not in {"Enabled", "Disabled"}):
            raise FeaturePrerequisiteError("Owned vault does not match the tenant/Standard/RBAC contract.")
        _validate_url(str(properties.get("vaultUri", "")), target.vault_uri)
        if urlsplit(properties["vaultUri"]).path not in ("", "/"):
            raise FeaturePrerequisiteError("Owned vault returned an unexpected data-plane URI.")


APP_SELECT = (
    "id,appId,displayName,description,signInAudience,requiredResourceAccess,"
    "spa,web,publicClient,isFallbackPublicClient,passwordCredentials"
)
SP_SELECT = "id,appId,displayName,description,appOwnerOrganizationId,servicePrincipalType,accountEnabled"
GROUP_SELECT = (
    "id,displayName,description,securityEnabled,mailEnabled,groupTypes,"
    "isAssignableToRole,onPremisesSyncEnabled,membershipRule"
)


def _find(client: _Client, collection: str, field: str, value: str, select: str) -> Json | None:
    query = urlencode({"$filter": f"{field} eq '{value.replace(chr(39), chr(39) * 2)}'", "$select": select})
    items = client.pages(GRAPH_SCOPE, f"{GRAPH_BASE}/{collection}?{query}", action=f"Discovering {collection}")
    if len(items) > 1:
        raise FeaturePrerequisiteError(f"Ambiguous {collection} collision; no resource will be adopted.")
    return items[0] if items else None


def _graph_read(client: _Client, collection: str, object_id: str, select: str) -> Json:
    result = client.object(
        GRAPH_SCOPE, "GET", f"{GRAPH_BASE}/{collection}/{_uuid(object_id, 'Graph object id')}?$select={select}",
        action=f"Verifying {collection}", retry_statuses=(404,),
    )
    assert result is not None
    return result


def _member_ids(client: _Client, group_id: str) -> set[str]:
    # v1.0 /groups/{id}/members omits SPs (documented Graph issue).
    # The documented $expand workaround includes them. A dedicated group has at
    # most one member; a truncated expansion is itself grounds to refuse reuse.
    url = f"{GRAPH_BASE}/groups/{_uuid(group_id, 'group_id')}?$select=id&$expand=members($select=id)"
    group = client.object(GRAPH_SCOPE, "GET", url, action="Inspecting dedicated group members", retry_statuses=(404,))
    assert group is not None
    members = _objects(group.get("members"), "expanded group members")
    if group.get("members@odata.nextLink"):
        _validate_url(group["members@odata.nextLink"], GRAPH_BASE)
        raise FeaturePrerequisiteError("Dedicated group membership is truncated; cannot prove single-principal scope.")
    ids = [_uuid(member.get("id"), "group member id") for member in members]
    if len(ids) != len(set(ids)) or len(ids) > 1:
        raise FeaturePrerequisiteError("Dedicated API group contains additional members; refusing to change membership.")
    return set(ids)


def _check_isolation(client: _Client, group_id: str | None, principal_id: str | None, *,
                     propagation: bool = False) -> frozenset[str]:
    retries = (404,) if propagation else ()
    if group_id:
        parents = client.pages(
            GRAPH_SCOPE, f"{GRAPH_BASE}/groups/{group_id}/transitiveMemberOf",
            action="Checking API group nesting", retry_statuses=retries,
        )
        if parents:
            raise FeaturePrerequisiteError("Dedicated API group is nested in another group/role; refusing broader grants.")
        members = _member_ids(client, group_id)
        if members - ({principal_id} if principal_id else set()):
            raise FeaturePrerequisiteError("Dedicated API group contains a different principal; no members will be removed.")
    if not principal_id:
        return frozenset()
    memberships = client.pages(
        GRAPH_SCOPE, f"{GRAPH_BASE}/servicePrincipals/{principal_id}/transitiveMemberOf",
        action="Checking notebook principal memberships", retry_statuses=retries,
    )
    ids = frozenset(_uuid(item.get("id"), "principal membership id") for item in memberships)
    if ids - ({group_id} if group_id else set()):
        raise FeaturePrerequisiteError("Notebook principal belongs to another group/role; refusing inherited privileges.")
    for relation in ("appRoleAssignments", "oauth2PermissionGrants"):
        if client.pages(GRAPH_SCOPE, f"{GRAPH_BASE}/servicePrincipals/{principal_id}/{relation}",
                        action="Checking existing notebook API grants", retry_statuses=retries):
            raise FeaturePrerequisiteError("Notebook principal already has API grants; refusing reuse without review.")
    return ids


def _settings(client: _Client) -> list[Json]:
    return client.pages(
        FABRIC_SCOPE, f"{FABRIC_BASE}/admin/tenantsettings",
        action="Reading Fabric tenant settings", collection_key="tenantSettings",
    )


def _public_setting(settings: list[Json], group_id: str | None) -> Json:
    matches = [item for item in settings if item.get("settingName") == PUBLIC_API_SETTING]
    if len(matches) != 1:
        raise FeaturePrerequisiteError("Cannot uniquely resolve ServicePrincipalAccessPermissionAPIs.")
    if group_id:
        for item in settings:
            if item.get("settingName") != PUBLIC_API_SETTING:
                for group in _objects(item.get("enabledSecurityGroups") or [], "other setting groups"):
                    if str(group.get("graphId", "")).lower() == group_id:
                        raise FeaturePrerequisiteError("Dedicated group is allowed in another tenant setting; refusing broader access.")
    _policy_body(matches[0])
    return matches[0]


def _ensure_policy(client: _Client, baseline: Json, group_id: str, target: FeatureTarget,
                   allow_public_api_group: bool, memberships: frozenset[str]) -> bool:
    body = merge_public_api_policy(
        baseline, group_id=group_id, group_name=target.names.group,
        allow_public_api_group=allow_public_api_group, principal_group_ids=memberships,
    )
    # No intervening cloud operation between this read and the POST.
    fresh = _public_setting(_settings(client), group_id)
    if _policy_state(fresh) != _policy_state(baseline):
        raise FeaturePrerequisiteError("Tenant setting changed concurrently; no policy write was attempted. Rerun after review.")
    if body is None:
        return False
    client.request(
        FABRIC_SCOPE, "POST", f"{FABRIC_BASE}/admin/tenantsettings/{PUBLIC_API_SETTING}/update",
        action="Appending dedicated Fabric public-API group", body=body,
        # A delayed transparent retry would no longer have an immediate
        # read-before-write guard. Surface throttling and require a fresh run.
        retry_throttling=False,
    )
    after = _public_setting(_settings(client), group_id)
    expected = {**baseline, **body}
    if _policy_state(after) != _policy_state(expected):
        raise FeaturePrerequisiteError("Tenant-setting preservation verification failed; no automatic rollback was attempted.")
    return True


def _ensure_arm_resource(client: _Client, target: FeatureTarget, existing: Json | None, *, vault: bool) -> Json:
    resource_id = target.vault_id if vault else target.resource_group_id
    url = f"{ARM_BASE}{resource_id}?api-version={VAULT_API if vault else RESOURCE_API}"
    if existing is None:
        # Check again at the write boundary, not only at initial discovery.
        existing = client.object(ARM_SCOPE, "GET", url, action="Rechecking Azure resource ownership", missing=True)
        if existing is None:
            client.request(
                ARM_SCOPE, "PUT", url, action="Creating dedicated vault" if vault else "Creating dedicated resource group",
                body=vault_body(target) if vault else {"location": target.location, "tags": target.tags},
                ok=(200, 201),
            )
    for attempt in range(POLL_ATTEMPTS):
        resource = client.object(ARM_SCOPE, "GET", url, action="Waiting for dedicated Azure resource",
                                 retry_statuses=(404,))
        assert resource is not None
        properties = resource.get("properties") or {}
        if not isinstance(properties, dict):
            raise FeaturePrerequisiteError("Azure resource returned invalid provisioning metadata.")
        state = properties.get("provisioningState", "Succeeded")
        _validate_arm_resource(resource, target, vault=vault, ready=state == "Succeeded")
        if state == "Succeeded":
            return resource
        if state not in {"Accepted", "Creating", "Updating", "RegisteringDns"}:
            raise FeaturePrerequisiteError("Dedicated Azure resource provisioning failed or returned an unknown state.")
        if attempt < POLL_ATTEMPTS - 1:
            time.sleep(POLL_SECONDS)
    raise FeaturePrerequisiteError("Dedicated Azure resource provisioning exceeded the bounded wait; rerun later.")


def role_assignment_spec(target: FeatureTarget, principal_id: str, role_id: str) -> tuple[str, Json]:
    principal_id = _uuid(principal_id, "RBAC principal id")
    if role_id not in {SECRETS_OFFICER, SECRETS_USER}:
        raise FeaturePrerequisiteError("Only the two vault-scoped secrets roles are supported.")
    assignment_id = str(uuid5(NAMESPACE_URL, f"{target.vault_id.lower()}|{principal_id}|{role_id}"))
    return assignment_id, {"properties": {
        "principalId": principal_id,
        "principalType": "User" if role_id == SECRETS_OFFICER else "ServicePrincipal",
        "roleDefinitionId": f"/subscriptions/{target.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{role_id}",
    }}


def _validate_role(assignment: Json, target: FeatureTarget, body: Json) -> None:
    properties = assignment.get("properties") or {}
    desired = body["properties"]
    if (any(str(properties.get(key, "")).lower() != value.lower() for key, value in desired.items())
            or str(properties.get("scope", "")).lower() != target.vault_id.lower()
            or properties.get("condition") or properties.get("conditionVersion")):
        raise FeaturePrerequisiteError("RBAC assignment collision: exact vault scope, principal, role and type are required.")


def _ensure_role(client: _Client, target: FeatureTarget, principal_id: str, role_id: str) -> str:
    assignment_id, body = role_assignment_spec(target, principal_id, role_id)
    resource_id = f"{target.vault_id}/providers/Microsoft.Authorization/roleAssignments/{assignment_id}"
    url = f"{ARM_BASE}{resource_id}?api-version={ROLE_API}"
    existing = client.object(ARM_SCOPE, "GET", url, action="Reading vault RBAC assignment", missing=True)
    if existing is None:
        client.request(
            ARM_SCOPE, "PUT", url, action="Creating vault-only RBAC assignment", body=body, ok=(200, 201),
            retry_codes=((400, "PrincipalNotFound"),),
        )
        existing = client.object(ARM_SCOPE, "GET", url, action="Verifying vault-only RBAC assignment",
                                 retry_statuses=(404,))
    assert existing is not None
    _validate_role(existing, target, body)
    return resource_id


def _workspace_assignment(assignments: list[Json], principal_id: str) -> Json | None:
    matches = [item for item in assignments
               if str((item.get("principal") or {}).get("id", "")).lower() == principal_id]
    if len(matches) > 1:
        raise FeaturePrerequisiteError("Duplicate notebook workspace assignments; refusing to guess.")
    if not matches:
        return None
    if matches[0].get("role") != "Contributor" or matches[0]["principal"].get("type") != "ServicePrincipal":
        raise FeaturePrerequisiteError("Notebook workspace role differs from Contributor; not changing an existing grant.")
    return matches[0]


def _ensure_workspace_role(client: _Client, target: FeatureTarget, principal_id: str) -> None:
    url = f"{FABRIC_BASE}/workspaces/{target.workspace_id}/roleAssignments"
    assignments = client.pages(FABRIC_SCOPE, url, action="Reading target workspace roles")
    if _workspace_assignment(assignments, principal_id) is not None:
        return
    client.request(
        FABRIC_SCOPE, "POST", url, action="Granting target workspace Contributor", ok=(201,),
        body={"principal": {"id": principal_id, "type": "ServicePrincipal"}, "role": "Contributor"},
    )
    for attempt in range(RETRY_ATTEMPTS):
        if _workspace_assignment(client.pages(FABRIC_SCOPE, url, action="Verifying target workspace role"), principal_id):
            return
        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(POLL_SECONDS)
    raise FeaturePrerequisiteError("Target workspace Contributor assignment did not become visible within the retry bound.")


def _date(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        pass
    raise FeaturePrerequisiteError(f"Invalid {field} metadata.")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_secret(secret: Json, target: FeatureTarget, app: Json) -> str:
    tags = secret.get("tags") or {}
    if (not isinstance(tags, dict) or any(tags.get(key) != value for key, value in target.tags.items())
            or tags.get("applicationObjectId") != app["id"]):
        raise FeaturePrerequisiteError("Vault secret ownership/app metadata differs; no existing secret will be overwritten.")
    value = secret.get("value")
    attrs = secret.get("attributes") or {}
    if not isinstance(attrs, dict):
        raise FeaturePrerequisiteError("Vault credential has invalid attributes; no existing secret will be overwritten.")
    now = time.time()
    not_before = attrs.get("nbf") if attrs.get("nbf") is not None else 0
    expires = attrs.get("exp") if attrs.get("exp") is not None else now + 3600
    if (not isinstance(value, str) or not value or attrs.get("enabled", True) is not True
            or not isinstance(not_before, (int, float)) or not isinstance(expires, (int, float))
            or not_before > now or expires <= now + 60):
        raise FeaturePrerequisiteError("Vault credential is empty, disabled, not yet valid or expired; explicit recovery is required.")
    return value


def _ensure_credentials(client: _Client, target: FeatureTarget, app: Json) -> dict[str, str]:
    secrets: dict[str, Json | None] = {}
    for name in ("tenantid", "clientid", "clientsecret"):
        secrets[name] = client.object(
            VAULT_SCOPE, "GET", f"{target.vault_uri}secrets/{name}?api-version={SECRET_API}",
            action=f"Reading dedicated vault {name}", missing=True,
            retry_codes=((403, "ForbiddenByRbac"),),
        )
    for name, expected in (("tenantid", target.tenant_id), ("clientid", app["appId"])):
        if secrets[name] is not None and _validate_secret(secrets[name], target, app) != expected:
            raise FeaturePrerequisiteError(f"Existing {name} does not match the target notebook; no credentials were changed.")
    metadata = _objects(app.get("passwordCredentials", []), "password metadata")
    existing = secrets["clientsecret"]
    if existing is not None:
        _validate_secret(existing, target, app)
        tags = existing["tags"]
        key_id = _uuid(tags.get("passwordKeyId"), "stored password key id")
        matches = [item for item in metadata if str(item.get("keyId", "")).lower() == key_id]
        if len(matches) != 1 or matches[0].get("displayName") != target.names.password:
            raise FeaturePrerequisiteError("Stored clientsecret does not match a managed Graph password; explicit recovery is required.")
        expiry = _date(matches[0].get("endDateTime"), "Graph password expiry")
        if (_date(matches[0].get("startDateTime"), "Graph password start") > datetime.now(timezone.utc)
                or expiry <= datetime.now(timezone.utc) + timedelta(minutes=1)
                or _date(tags.get("passwordEndDateTime"), "stored password expiry") != expiry
                or existing.get("attributes", {}).get("exp") != int(expiry.timestamp())):
            raise FeaturePrerequisiteError("Stored Graph password is expired or has inconsistent validity metadata; not rotating it.")
    else:
        if any(item.get("displayName") == target.names.password for item in metadata):
            raise FeaturePrerequisiteError(
                "A managed Graph password exists but clientsecret is missing. It cannot be retrieved; "
                "explicit recovery is required, and another password will not be created."
            )
        key_id, expiry = "", datetime.now(timezone.utc) + timedelta(days=PASSWORD_DAYS)

    def put(name: str, value: str, *, password: bool = False) -> None:
        tags = {**target.tags, "applicationObjectId": app["id"]}
        attributes: Json = {"enabled": True}
        if password:
            tags.update(passwordKeyId=key_id, passwordEndDateTime=_iso(expiry))
            attributes["exp"] = int(expiry.timestamp())
        response = client.object(
            VAULT_SCOPE, "PUT", f"{target.vault_uri}secrets/{name}?api-version={SECRET_API}",
            action=f"Persisting dedicated vault {name}",
            body={"value": value, "tags": tags, "attributes": attributes, "contentType": "text/plain"},
            retry_codes=((403, "ForbiddenByRbac"),),
        )
        assert response is not None
        if not hmac.compare_digest(_validate_secret(response, target, app).encode(), value.encode()):
            raise FeaturePrerequisiteError("Vault secret persistence verification failed; no secret value is included.")
        if (response.get("tags") != tags
                or any((response.get("attributes") or {}).get(key) != val for key, val in attributes.items())):
            raise FeaturePrerequisiteError("Vault secret persistence metadata verification failed.")

    for name, value in (("tenantid", target.tenant_id), ("clientid", app["appId"])):
        if secrets[name] is None:
            put(name, value)
    if existing is None:
        password = client.object(
            GRAPH_SCOPE, "POST", f"{GRAPH_BASE}/applications/{app['id']}/addPassword",
            action="Creating bounded-lived notebook password",
            retry_throttling=False,  # Non-idempotent: never hide an extra password attempt.
            body={"passwordCredential": {
                "displayName": target.names.password,
                "startDateTime": _iso(datetime.now(timezone.utc)),
                "endDateTime": _iso(expiry),
            }},
        )
        assert password is not None
        key_id = _uuid(password.get("keyId"), "created password key id")
        returned_expiry = _date(password.get("endDateTime"), "created password expiry")
        if returned_expiry > expiry or returned_expiry <= datetime.now(timezone.utc) + timedelta(minutes=1):
            raise FeaturePrerequisiteError("Graph returned an unexpected password lifetime; explicit recovery is required.")
        expiry = returned_expiry
        value = password.get("secretText")
        if not isinstance(value, str) or not value:
            raise FeaturePrerequisiteError("Graph did not return a password value; explicit recovery is required.")
        # The only destination of secretText is this TLS vault write. No printing,
        # CLI args, files, returned metadata, credential deletion or other rotation.
        try:
            put("clientsecret", value, password=True)
        except FeaturePrerequisiteError as exc:
            raise FeaturePrerequisiteError(
                f"Graph password created but vault persistence failed; explicit recovery may be required. {exc}"
            ) from None
    return {"notebook_password_key_id": key_id, "notebook_password_expires_on": _iso(expiry)}


def credential_deployment_body(target: FeatureTarget, records: dict[str, Json]) -> Json:
    """Use write-only ARM secret provisioning; secret values are secure parameters."""
    if not records or set(records) - {"tenantid", "clientid", "clientsecret"}:
        raise FeaturePrerequisiteError("Unsupported credential deployment records.")
    template: Json = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0", "parameters": {}, "resources": [],
    }
    parameters: Json = {}
    for name, record in records.items():
        if not isinstance(record.get("value"), str) or not record["value"]:
            raise FeaturePrerequisiteError("A credential deployment value is missing.")
        parameter = f"{name}Value"
        parameters[parameter] = {"value": record["value"]}
        template["parameters"][parameter] = {"type": "secureString"}
        template["resources"].append({
            "type": "Microsoft.KeyVault/vaults/secrets", "apiVersion": VAULT_API,
            "name": f"{target.vault_name}/{name}",
            "tags": record["tags"],
            "properties": {
                "value": f"[parameters('{parameter}')]",
                "contentType": "text/plain", "attributes": record["attributes"],
            },
        })
    return {"properties": {"mode": "Incremental", "template": template, "parameters": parameters}}


def _read_arm_secret(client: _Client, target: FeatureTarget, name: str) -> Json | None:
    return client.object(
        ARM_SCOPE, "GET", f"{ARM_BASE}{target.vault_id}/secrets/{name}?api-version={VAULT_API}",
        action=f"Reading ARM credential metadata for {name}", missing=True,
    )


def _validate_arm_secret(secret: Json, target: FeatureTarget, app: Json, name: str) -> None:
    # ARM intentionally never returns values. Verify ownership and validity here;
    # the setup notebook verifies actual values and client-credential sign-in.
    if str(secret.get("id", "")).lower() != f"{target.vault_id}/secrets/{name}".lower():
        raise FeaturePrerequisiteError("ARM credential metadata belongs to a different resource.")
    tags = secret.get("tags") or {}
    expected = {**target.tags, "applicationObjectId": app["id"],
                "tenantId": target.tenant_id, "clientId": app["appId"]}
    if not isinstance(tags, dict) or any(tags.get(key) != value for key, value in expected.items()):
        raise FeaturePrerequisiteError(f"Existing {name} metadata does not match this deployment.")
    props = secret.get("properties") or {}
    if not isinstance(props, dict):
        raise FeaturePrerequisiteError(f"Existing {name} has invalid ARM metadata.")
    attributes = props.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise FeaturePrerequisiteError(f"Existing {name} has invalid credential attributes.")
    not_before = attributes.get("nbf") or 0
    expires = attributes.get("exp") if attributes.get("exp") is not None else time.time() + 3600
    if (attributes.get("enabled") is not True
            or not isinstance(not_before, (int, float)) or not isinstance(expires, (int, float))
            or not_before > time.time() or expires <= time.time() + 60):
        raise FeaturePrerequisiteError(f"Existing {name} is disabled, not yet valid or expired.")
    uri = props.get("secretUriWithVersion") or ""
    _validate_url(uri, target.vault_uri)
    if not re.fullmatch(rf"/secrets/{name}/[A-Za-z0-9-]+", urlsplit(uri).path):
        raise FeaturePrerequisiteError(f"Existing {name} has invalid secret-version metadata.")


def _deploy_arm_credentials(client: _Client, target: FeatureTarget, records: dict[str, Json]) -> None:
    deployment_name = f"hydro-credentials-{target.workspace_id[:8]}-{uuid4().hex[:8]}"
    url = (
        f"{ARM_BASE}{target.resource_group_id}/providers/Microsoft.Resources/"
        f"deployments/{deployment_name}?api-version={DEPLOYMENT_API}"
    )
    client.request(
        ARM_SCOPE, "PUT", url, action="Deploying secure notebook credentials",
        body=credential_deployment_body(target, records), ok=(200, 201, 202),
        retry_throttling=False,
    )
    for attempt in range(120):
        deployment = client.object(ARM_SCOPE, "GET", url, action="Checking secure credential deployment")
        assert deployment is not None
        state = (deployment.get("properties") or {}).get("provisioningState")
        if state == "Succeeded":
            return
        if state in {"Failed", "Canceled", "Cancelled"}:
            raise FeaturePrerequisiteError(
                f"Secure credential deployment {deployment_name} {state}; inspect its ARM status."
            )
        if state not in {"Accepted", "Running", "Creating", "Updating"}:
            raise FeaturePrerequisiteError(f"Unexpected credential deployment state: {state}.")
        if attempt < 119:
            time.sleep(5)
    raise FeaturePrerequisiteError(
        f"Secure credential deployment {deployment_name} did not finish within ten minutes; do not rotate credentials."
    )


def _ensure_private_credentials(client: _Client, target: FeatureTarget, app: Json) -> dict[str, str]:
    secrets = {name: _read_arm_secret(client, target, name) for name in ("tenantid", "clientid", "clientsecret")}
    for name, secret in secrets.items():
        if secret is not None:
            _validate_arm_secret(secret, target, app, name)
    passwords = _objects(app.get("passwordCredentials", []), "password metadata")
    existing = secrets["clientsecret"]
    records: dict[str, Json] = {}
    tags = {**target.tags, "applicationObjectId": app["id"],
            "tenantId": target.tenant_id, "clientId": app["appId"]}
    for name, value in (("tenantid", target.tenant_id), ("clientid", app["appId"])):
        if secrets[name] is None:
            records[name] = {"value": value, "tags": tags, "attributes": {"enabled": True}}
    if existing is not None:
        key_id = _uuid(existing["tags"].get("passwordKeyId"), "stored password key id")
        matches = [item for item in passwords if str(item.get("keyId", "")).lower() == key_id]
        if len(matches) != 1 or matches[0].get("displayName") != target.names.password:
            raise FeaturePrerequisiteError("Private credential is not linked to the managed Graph password.")
        expiry = _date(matches[0].get("endDateTime"), "Graph password expiry")
        if (_date(matches[0].get("startDateTime"), "Graph password start") > datetime.now(timezone.utc)
                or expiry <= datetime.now(timezone.utc) + timedelta(minutes=1)
                or _date(existing["tags"].get("passwordEndDateTime"), "stored password expiry") != expiry
                or existing["properties"]["attributes"].get("exp") != int(expiry.timestamp())):
            raise FeaturePrerequisiteError("Private credential expiry metadata is inconsistent; explicit recovery is required.")
    else:
        if any(item.get("displayName") == target.names.password for item in passwords):
            raise FeaturePrerequisiteError(
                "A managed Graph password exists without a stored private credential; "
                "explicit recovery is required, and another password will not be created."
            )
        requested_expiry = datetime.now(timezone.utc) + timedelta(days=PASSWORD_DAYS)
        password = client.object(
            GRAPH_SCOPE, "POST", f"{GRAPH_BASE}/applications/{app['id']}/addPassword",
            action="Creating private-vault notebook password", retry_throttling=False,
            body={"passwordCredential": {
                "displayName": target.names.password, "startDateTime": _iso(datetime.now(timezone.utc)),
                "endDateTime": _iso(requested_expiry),
            }},
        )
        assert password is not None
        key_id = _uuid(password.get("keyId"), "created password key id")
        expiry = _date(password.get("endDateTime"), "created password expiry")
        value = password.get("secretText")
        if (not isinstance(value, str) or not value or expiry > requested_expiry
                or expiry <= datetime.now(timezone.utc) + timedelta(minutes=1)):
            raise FeaturePrerequisiteError("Graph returned invalid password metadata; explicit recovery is required.")
        records["clientsecret"] = {
            "value": value, "tags": {**tags, "passwordKeyId": key_id, "passwordEndDateTime": _iso(expiry)},
            "attributes": {"enabled": True, "exp": int(expiry.timestamp())},
        }
    if records:
        try:
            _deploy_arm_credentials(client, target, records)
        except FeaturePrerequisiteError as exc:
            raise FeaturePrerequisiteError(f"Private credential persistence failed; explicit recovery may be required. {exc}") from None
    for name in secrets:
        secret = _read_arm_secret(client, target, name)
        if secret is None:
            raise FeaturePrerequisiteError(f"ARM deployment did not persist {name}.")
        _validate_arm_secret(secret, target, app, name)
        if name in records and (
            secret["tags"] != records[name]["tags"]
            or any(secret["properties"]["attributes"].get(k) != v for k, v in records[name]["attributes"].items())
        ):
            raise FeaturePrerequisiteError(f"ARM persistence metadata verification failed for {name}.")
    return {"notebook_password_key_id": key_id, "notebook_password_expires_on": _iso(expiry)}


def _isolated_cli_config() -> None:
    config = os.environ.get("AZURE_CONFIG_DIR", "")
    if (not config or not os.path.isabs(config) or not os.path.isdir(config)
            or os.path.realpath(config) == os.path.realpath(os.path.expanduser("~/.azure"))):
        raise FeaturePrerequisiteError("Parent must supply an existing, isolated, absolute AZURE_CONFIG_DIR.")


def ensure_feature_prerequisites(*, tenant_id: str, workspace_id: str, subscription_id: str,
                                 resource_group: str, vault_name: str, location: str,
                                 allow_public_api_group: bool) -> dict[str, str]:
    """Create/reuse marked prerequisites; return only nonsecret IDs and metadata."""
    target = FeatureTarget.validate(
        tenant_id=tenant_id, workspace_id=workspace_id, subscription_id=subscription_id,
        resource_group=resource_group, vault_name=vault_name, location=location,
    )
    if type(allow_public_api_group) is not bool:
        raise FeaturePrerequisiteError("allow_public_api_group must be a boolean.")
    _isolated_cli_config()
    client = _Client(target)
    try:
        return _ensure(client, target, allow_public_api_group)
    finally:
        client.close()


def _ensure(client: _Client, target: FeatureTarget, allow_public_api_group: bool) -> dict[str, str]:
    me = client.object(GRAPH_SCOPE, "GET", f"{GRAPH_BASE}/me?$select=id", action="Identifying delegated Graph caller")
    assert me is not None
    caller_id = _uuid(me.get("id"), "delegated caller id")  # /me rejects app-only tokens.
    subscription = client.object(
        ARM_SCOPE, "GET", f"{ARM_BASE}/subscriptions/{target.subscription_id}?api-version=2022-12-01",
        action="Verifying subscription tenant",
    )
    assert subscription is not None
    if (_uuid(subscription.get("tenantId"), "subscription tenant") != target.tenant_id
            or _uuid(subscription.get("subscriptionId"), "subscription id") != target.subscription_id
            or subscription.get("state") != "Enabled"):
        raise FeaturePrerequisiteError("Subscription is not enabled in the requested tenant; no writes were attempted.")
    workspace = client.object(
        FABRIC_SCOPE, "GET", f"{FABRIC_BASE}/workspaces/{target.workspace_id}", action="Verifying target workspace",
    )
    assert workspace is not None
    if _uuid(workspace.get("id"), "workspace response id") != target.workspace_id:
        raise FeaturePrerequisiteError("Fabric returned a different workspace; no writes were attempted.")

    # Discover and validate *all* name collisions and policy consent before writes.
    settings = _settings(client)
    baseline = _public_setting(settings, None)
    rg = client.object(ARM_SCOPE, "GET", f"{ARM_BASE}{target.resource_group_id}?api-version={RESOURCE_API}",
                       action="Discovering dedicated resource group", missing=True)
    vault = client.object(ARM_SCOPE, "GET", f"{ARM_BASE}{target.vault_id}?api-version={VAULT_API}",
                          action="Discovering dedicated vault", missing=True)
    if rg is not None:
        _validate_arm_resource(rg, target, vault=False)
    if vault is not None:
        _validate_arm_resource(vault, target, vault=True)
    app = _find(client, "applications", "displayName", target.names.application, APP_SELECT)
    principal = _find(client, "servicePrincipals", "displayName", target.names.application, SP_SELECT)
    group = _find(client, "groups", "displayName", target.names.group, GROUP_SELECT)
    if app is not None:
        _validate_application(app, target)
        by_app = _find(client, "servicePrincipals", "appId", app["appId"], SP_SELECT)
        if principal is not None and (by_app is None or by_app["id"] != principal["id"]):
            raise FeaturePrerequisiteError("Service-principal name/app collision; refusing to adopt it.")
        principal = by_app
    elif principal is not None:
        raise FeaturePrerequisiteError("Notebook principal name exists without the owned application.")
    if principal is not None:
        assert app is not None
        _validate_principal(principal, app, target)
    if group is not None:
        _validate_group(group, target)
    group_id = _uuid(group["id"], "group id") if group else None
    principal_id = _uuid(principal["id"], "principal id") if principal else None
    memberships = _check_isolation(client, group_id, principal_id)
    baseline = _public_setting(settings, group_id)
    merge_public_api_policy(
        baseline, group_id=group_id, group_name=target.names.group,
        allow_public_api_group=allow_public_api_group, principal_group_ids=memberships,
    )

    _ensure_arm_resource(client, target, rg, vault=False)
    vault = _ensure_arm_resource(client, target, vault, vault=True)
    if app is None:
        # Recheck Graph names at the write boundary. Graph display names are not
        # unique keys, so a concurrent creator still requires serialized callers.
        app = _find(client, "applications", "displayName", target.names.application, APP_SELECT)
        if app is None:
            created = client.object(GRAPH_SCOPE, "POST", f"{GRAPH_BASE}/applications",
                                    action="Creating notebook-only application", body=application_body(target), ok=(201,))
            assert created is not None
            app = _graph_read(client, "applications", created.get("id"), APP_SELECT)
    _validate_application(app, target)
    if principal is None:
        principal = _find(client, "servicePrincipals", "appId", app["appId"], SP_SELECT)
        if principal is None:
            created = client.object(
                GRAPH_SCOPE, "POST", f"{GRAPH_BASE}/servicePrincipals", action="Creating notebook service principal",
                ok=(201,), body={"appId": app["appId"], "displayName": target.names.application,
                                 "description": target.names.marker, "accountEnabled": True},
            )
            assert created is not None
            principal = _graph_read(client, "servicePrincipals", created.get("id"), SP_SELECT)
    _validate_principal(principal, app, target)
    principal_id = _uuid(principal["id"], "notebook principal id")
    group_created = False
    if group is None:
        group = _find(client, "groups", "displayName", target.names.group, GROUP_SELECT)
        if group is None:
            created = client.object(
                GRAPH_SCOPE, "POST", f"{GRAPH_BASE}/groups", action="Creating dedicated Fabric API group",
                ok=(201,), body=group_body(target, principal_id, caller_id),
            )
            assert created is not None
            group_created = True
            group = _graph_read(client, "groups", created.get("id"), GROUP_SELECT)
    _validate_group(group, target)
    group_id = _uuid(group["id"], "dedicated group id")
    _check_isolation(client, group_id, principal_id, propagation=True)
    # The create body already bound the SP. Poll delayed visibility rather than
    # accidentally attempting the same member add twice during propagation.
    if not group_created and not _member_ids(client, group_id):
        client.request(
            GRAPH_SCOPE, "POST", f"{GRAPH_BASE}/groups/{group_id}/members/$ref",
            action="Adding notebook only to dedicated group", ok=(204,),
            body={"@odata.id": f"{GRAPH_BASE}/directoryObjects/{principal_id}"},
        )
    for attempt in range(RETRY_ATTEMPTS):
        members = _member_ids(client, group_id)
        if members == {principal_id}:
            break
        if members:
            raise FeaturePrerequisiteError("Dedicated group membership changed concurrently; refusing tenant-policy update.")
        if attempt == RETRY_ATTEMPTS - 1:
            raise FeaturePrerequisiteError("Dedicated group membership propagation exceeded the retry bound.")
        time.sleep(POLL_SECONDS)
    memberships = _check_isolation(client, group_id, principal_id, propagation=True)
    policy_changed = _ensure_policy(client, baseline, group_id, target, allow_public_api_group, memberships)
    caller_role = _ensure_role(client, target, caller_id, SECRETS_OFFICER)
    notebook_role = _ensure_role(client, target, principal_id, SECRETS_USER)
    _ensure_workspace_role(client, target, principal_id)
    # Read fresh password metadata immediately before deciding whether to create.
    app = _graph_read(client, "applications", app["id"], APP_SELECT)
    _validate_application(app, target)
    private_only = (vault.get("properties") or {}).get("publicNetworkAccess") == "Disabled"
    password_metadata = (
        _ensure_private_credentials(client, target, app) if private_only
        else _ensure_credentials(client, target, app)
    )
    return {
        "tenant_id": target.tenant_id,
        "workspace_id": target.workspace_id,
        "subscription_id": target.subscription_id,
        "resource_group": target.resource_group,
        "key_vault_uri": target.vault_uri,
        "key_vault_resource_id": target.vault_id,
        "notebook_client_id": _uuid(app["appId"], "notebook client id"),
        "notebook_principal_id": principal_id,
        "notebook_application_object_id": _uuid(app["id"], "notebook app object id"),
        "group_id": group_id,
        "caller_principal_id": caller_id,
        "caller_vault_role_assignment_id": caller_role,
        "notebook_vault_role_assignment_id": notebook_role,
        "public_api_policy_changed": str(policy_changed).lower(),
        "credential_provisioning": "secure-arm-deployment" if private_only else "vault-data-plane",
        **password_metadata,
    }
