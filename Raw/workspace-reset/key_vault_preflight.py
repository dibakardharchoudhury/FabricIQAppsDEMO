#!/usr/bin/env python3
"""Ensure a Fabric workspace can reach an Azure Key Vault.

Uses the current Azure CLI identity for both Fabric and Azure Resource Manager.
No vault secret values are read. If public networking is not open, this module
creates/reuses a Fabric managed private endpoint and approves the corresponding
Key Vault connection when the signed-in identity has permission.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote, urlparse

import requests
from azure.identity import AzureCliCredential

FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
ARM_BASE = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
ARM_RESOURCES_API = "2021-04-01"
KEY_VAULT_API = "2024-11-01"
VAULT_HOST_RE = re.compile(r"^(?P<name>[a-z0-9-]{3,24})\.vault\.azure\.net$", re.IGNORECASE)


class PreflightError(RuntimeError):
    """A safe, actionable preflight failure suitable for console output."""


def vault_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri.strip())
    match = VAULT_HOST_RE.fullmatch(parsed.hostname or "")
    if parsed.scheme.lower() != "https" or not match or parsed.username or parsed.port:
        raise PreflightError("Key Vault URI must look like https://<vault-name>.vault.azure.net/.")
    return match.group("name")


def public_network_is_open(vault: dict[str, Any]) -> bool:
    properties = vault.get("properties") or {}
    if str(properties.get("publicNetworkAccess", "Enabled")).casefold() == "disabled":
        return False
    network_acls = properties.get("networkAcls") or {}
    return str(network_acls.get("defaultAction", "Allow")).casefold() == "allow"


def endpoint_state(endpoint: dict[str, Any]) -> tuple[str, str]:
    properties = endpoint.get("properties") or {}
    provisioning = (
        properties.get("provisioningState")
        or endpoint.get("provisioningState")
        or endpoint.get("provisionState")
        or "Unknown"
    )
    connection = properties.get("connectionState") or endpoint.get("connectionState") or "Unknown"
    if isinstance(connection, dict):
        connection = connection.get("status") or connection.get("state") or "Unknown"
    return str(provisioning), str(connection)


def matching_endpoint(endpoints: list[dict[str, Any]], vault_resource_id: str) -> dict[str, Any] | None:
    target = vault_resource_id.rstrip("/").casefold()
    for endpoint in endpoints:
        properties = endpoint.get("properties") or {}
        resource_id = properties.get("targetPrivateLinkResourceId") or endpoint.get("targetPrivateLinkResourceId")
        subresource = properties.get("targetSubresourceType") or endpoint.get("targetSubresourceType")
        if str(resource_id or "").rstrip("/").casefold() == target and str(subresource or "").casefold() == "vault":
            return endpoint
    return None


def select_connection(connections: list[dict[str, Any]], known_ids: set[str]) -> dict[str, Any] | None:
    pending: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []
    for connection in connections:
        state = ((connection.get("properties") or {}).get("privateLinkServiceConnectionState") or {})
        status = str(state.get("status", "")).casefold()
        if connection.get("id") in known_ids:
            continue
        if status == "pending":
            pending.append(connection)
        elif status == "approved":
            approved.append(connection)
    if len(pending) == 1:
        return pending[0]
    if not pending and len(approved) == 1:
        return approved[0]
    return None


def connection_state(connection: dict[str, Any]) -> tuple[str, str]:
    properties = connection.get("properties") or {}
    approval = properties.get("privateLinkServiceConnectionState") or {}
    return (
        str(properties.get("provisioningState") or "Unknown"),
        str(approval.get("status") or "Unknown"),
    )


def connection_is_transitioning(connection: dict[str, Any]) -> bool:
    provisioning, _ = connection_state(connection)
    return provisioning.casefold() in {
        "accepted",
        "creating",
        "pending",
        "provisioning",
        "updating",
        "updatingdns",
    }


class CloudClient:
    def __init__(self, tenant_id: str) -> None:
        self.credential = AzureCliCredential(tenant_id=tenant_id, process_timeout=60)
        self.session = requests.Session()
        self.tokens: dict[str, tuple[str, float]] = {}

    def request(self, scope: str, method: str, url: str, **kwargs: Any) -> requests.Response:
        token, expiry = self.tokens.get(scope, ("", 0.0))
        if not token or time.time() >= expiry - 300:
            access = self.credential.get_token(scope)
            token, expiry = access.token, float(access.expires_on)
            self.tokens[scope] = (token, expiry)
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        for attempt in range(6):
            response = self.session.request(method, url, headers=headers, timeout=120, **kwargs)
            if response.status_code != 429:
                return response
            time.sleep(min(30, int(response.headers.get("Retry-After", "5")) * (attempt + 1)))
        return response


def _json_or_error(response: requests.Response, action: str, expected: set[int] = {200}) -> dict[str, Any]:
    if response.status_code not in expected:
        detail = response.text[:1000]
        if response.status_code in {401, 403}:
            detail = f"signed-in identity is not authorized ({response.status_code})"
        raise PreflightError(f"{action} failed: {detail}")
    return response.json() if response.content else {}


def _find_vault(client: CloudClient, vault_name: str) -> dict[str, Any]:
    subscriptions = _json_or_error(
        client.request(ARM_SCOPE, "GET", f"{ARM_BASE}/subscriptions?api-version=2022-12-01"),
        "Listing Azure subscriptions",
    ).get("value", [])
    matches: list[dict[str, Any]] = []
    escaped_name = vault_name.replace("'", "''")
    resource_filter = quote(
        f"resourceType eq 'Microsoft.KeyVault/vaults' and name eq '{escaped_name}'",
        safe=" ='",
    )
    for subscription in subscriptions:
        subscription_id = subscription.get("subscriptionId")
        if not subscription_id:
            continue
        url = f"{ARM_BASE}/subscriptions/{subscription_id}/resources?api-version={ARM_RESOURCES_API}&$filter={resource_filter}"
        response = client.request(ARM_SCOPE, "GET", url)
        if response.status_code == 200:
            matches.extend(response.json().get("value", []))
    if not matches:
        raise PreflightError(f"Key Vault '{vault_name}' was not found in any accessible Azure subscription.")
    if len(matches) > 1:
        raise PreflightError(f"Multiple accessible Key Vault resources are named '{vault_name}'; use an unambiguous vault.")
    resource_id = matches[0]["id"]
    return _json_or_error(
        client.request(ARM_SCOPE, "GET", f"{ARM_BASE}{resource_id}?api-version={KEY_VAULT_API}"),
        f"Reading Key Vault '{vault_name}'",
    )


def _list_fabric_endpoints(client: CloudClient, workspace_id: str) -> list[dict[str, Any]]:
    data = _json_or_error(
        client.request(FABRIC_SCOPE, "GET", f"{FABRIC_BASE}/workspaces/{workspace_id}/managedPrivateEndpoints"),
        "Listing Fabric managed private endpoints",
    )
    return data.get("value", [])


def _list_vault_connections(client: CloudClient, vault_id: str) -> list[dict[str, Any]]:
    data = _json_or_error(
        client.request(ARM_SCOPE, "GET", f"{ARM_BASE}{vault_id}/privateEndpointConnections?api-version={KEY_VAULT_API}"),
        "Listing Key Vault private endpoint connections",
    )
    return data.get("value", [])


def ensure_key_vault_access(tenant_id: str, workspace_id: str, key_vault_uri: str,
                            poll_seconds: int = 600) -> None:
    vault_name = vault_name_from_uri(key_vault_uri)
    client = CloudClient(tenant_id)
    print(f"Key Vault preflight: resolving '{vault_name}'...")
    vault = _find_vault(client, vault_name)
    vault_id = vault["id"]
    if public_network_is_open(vault):
        print("  public network access is open; no managed private endpoint is required.")
        return

    print("  public network access is restricted; checking Fabric managed private endpoints...")
    endpoints = _list_fabric_endpoints(client, workspace_id)
    endpoint = matching_endpoint(endpoints, vault_id)
    # For a new endpoint, distinguish its Azure approval request from preexisting
    # connections. For an existing pending endpoint, safely accept the sole
    # pending/approved connection; ambiguity still requires administrator review.
    known_connection_ids: set[str] = set()
    if endpoint is None:
        known_connection_ids = {str(item.get("id")) for item in _list_vault_connections(client, vault_id)}
        endpoint_name = f"mpe-kv-{vault_name}"[:64]
        payload = {
            "name": endpoint_name,
            "targetPrivateLinkResourceId": vault_id,
            "targetSubresourceType": "vault",
            "requestMessage": f"Fabric workspace {workspace_id} requires Key Vault access.",
        }
        print(f"  creating Fabric managed private endpoint '{endpoint_name}'...")
        endpoint = _json_or_error(
            client.request(
                FABRIC_SCOPE, "POST",
                f"{FABRIC_BASE}/workspaces/{workspace_id}/managedPrivateEndpoints",
                json=payload,
            ),
            "Creating Fabric managed private endpoint",
            {200, 201, 202},
        )

    provisioning, connection = endpoint_state(endpoint)
    print(f"  Fabric endpoint state: provisioning={provisioning}, connection={connection}.")
    if connection.casefold() == "approved" and provisioning.casefold() in {"succeeded", "success"}:
        return
    if connection.casefold() == "rejected" or provisioning.casefold() == "failed":
        raise PreflightError("The Fabric managed private endpoint is rejected or failed; delete it and retry.")

    # Fabric activation must succeed before the data-source owner approves the
    # request. Approving while activation is still Provisioning can terminally
    # fail the Fabric endpoint even though Key Vault records the approval.
    activation_deadline = time.time() + poll_seconds
    while provisioning.casefold() not in {"succeeded", "success"} and time.time() < activation_deadline:
        time.sleep(10)
        endpoint = matching_endpoint(_list_fabric_endpoints(client, workspace_id), vault_id)
        if endpoint is None:
            continue
        provisioning, connection = endpoint_state(endpoint)
        print(f"  Fabric endpoint state: provisioning={provisioning}, connection={connection}.")
        if connection.casefold() == "rejected" or provisioning.casefold() == "failed":
            raise PreflightError(
                "Fabric could not activate the managed private endpoint. Delete the failed "
                "endpoint in Workspace settings > Network security, then retry."
            )
    if provisioning.casefold() not in {"succeeded", "success"}:
        raise PreflightError(
            f"Fabric managed private endpoint activation did not succeed within {poll_seconds} seconds."
        )
    if connection.casefold() == "approved":
        print("  Key Vault private connectivity is approved and ready.")
        return

    deadline = time.time() + poll_seconds
    selected: dict[str, Any] | None = None
    while time.time() < deadline and selected is None:
        selected = select_connection(_list_vault_connections(client, vault_id), known_connection_ids)
        if selected is None:
            time.sleep(5)
    if selected is None:
        raise PreflightError(
            "Could not uniquely identify the new Key Vault approval request. Approve it in Key Vault > Networking > Private endpoint connections, then retry."
        )

    approval_state = ((selected.get("properties") or {}).get("privateLinkServiceConnectionState") or {})
    if str(approval_state.get("status", "")).casefold() != "approved":
        while connection_is_transitioning(selected) and time.time() < deadline:
            provisioning, _ = connection_state(selected)
            print(f"  Key Vault connection is still provisioning ({provisioning}); waiting before approval...")
            time.sleep(10)
            selected = _json_or_error(
                client.request(
                    ARM_SCOPE,
                    "GET",
                    f"{ARM_BASE}{selected['id']}?api-version={KEY_VAULT_API}",
                ),
                "Refreshing the Key Vault private endpoint connection",
            )
        if connection_is_transitioning(selected):
            provisioning, _ = connection_state(selected)
            raise PreflightError(
                "The Key Vault private endpoint connection did not finish provisioning "
                f"({provisioning}) before the approval timeout. Retry the pipeline action."
            )
        provisioning, approval_status = connection_state(selected)
        if provisioning.casefold() == "failed" or approval_status.casefold() == "rejected":
            raise PreflightError(
                "The Key Vault private endpoint connection was rejected or failed before approval."
            )
        print(f"  approving Key Vault connection '{selected.get('name', '')}'...")
        approval = {
            "etag": selected.get("etag", ""),
            "properties": {
                "privateEndpoint": (selected.get("properties") or {}).get("privateEndpoint"),
                "privateLinkServiceConnectionState": {
                    "status": "Approved",
                    "description": "Approved by the Fabric deployment app.",
                    "actionsRequired": "None",
                },
            },
        }
        _json_or_error(
            client.request(ARM_SCOPE, "PUT", f"{ARM_BASE}{selected['id']}?api-version={KEY_VAULT_API}", json=approval),
            "Approving the Key Vault private endpoint connection",
        )

    while time.time() < deadline:
        endpoint = matching_endpoint(_list_fabric_endpoints(client, workspace_id), vault_id)
        if endpoint:
            provisioning, connection = endpoint_state(endpoint)
            print(f"  Fabric endpoint state: provisioning={provisioning}, connection={connection}.")
            if connection.casefold() == "approved" and provisioning.casefold() in {"succeeded", "success"}:
                print("  Key Vault private connectivity is approved and ready.")
                return
            if connection.casefold() == "rejected" or provisioning.casefold() == "failed":
                break
        time.sleep(10)
    raise PreflightError("Key Vault private connectivity did not become ready within 10 minutes.")
