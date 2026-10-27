"""Provision persistent Hydro agents from the app's versioned source definitions.

Called by deploy_fabric_app.py, never a separate app deployment path. The Hosted Agent is converged by
deploy_hosted_agent.py immediately before its final verification below.
"""
from __future__ import annotations

import base64
import hashlib
from typing import NamedTuple
import importlib.util
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests


def request_with_read_retry(method: str, url: str, **kwargs) -> requests.Response:
    method = method.upper()
    for attempt in range(4):
        try:
            return requests.request(method, url, **kwargs)
        except requests.exceptions.SSLError:
            raise
        except (requests.ConnectionError, requests.Timeout) as error:
            if method != "GET" or attempt == 3:
                raise
            delay = 2 ** (attempt + 1)
            print(
                f"Provisioning GET to {urlparse(url).hostname} failed "
                f"({type(error).__name__}); retry {attempt + 1}/3 in {delay}s.",
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("Unreachable provisioning retry state.")


def definition_operation_url(location: str) -> str:
    parsed = urlparse(location)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment
            or not (host == "api.fabric.microsoft.com" or (host.startswith("wabi-") and host.endswith(".analysis.windows.net")))
            or not re.fullmatch(r"/v1/operations/[0-9a-fA-F-]{36}", parsed.path)):
        raise RuntimeError("Unexpected Fabric definition operation location.")
    return "https://api.fabric.microsoft.com" + parsed.path


def verify_published_identity(definition: dict, workspace: str, ontology: str) -> None:
    matched = False
    for part in definition.get("definition", {}).get("parts", []):
        path = part.get("path", "")
        context_source = path.startswith("Files/Config/published/ontologies/") and path.endswith("/ontology.json")
        if not path.startswith("Files/Config/published/") or not (
            path.endswith("/datasource.json") or context_source
        ):
            continue
        source = json.loads(base64.b64decode(part["payload"], validate=True).decode("utf-8"))
        if not context_source and str(source.get("type", "")).lower() != "ontology":
            continue
        if source.get("workspaceId", "").lower() != workspace.lower() or source.get("artifactId", "").lower() != ontology.lower():
            raise RuntimeError("The published Data Agent has a mismatched ontology source.")
        matched = True
    if not matched:
        raise RuntimeError("No published Data Agent source matches the selected Ontology v2.")


def read_data_agent_definition(deploy, workspace: str, agent_id: str, headers: dict, *, collection="dataAgents") -> dict:
    if collection not in ("dataAgents", "ontologies", "items"):
        raise ValueError("Unsupported Fabric definition collection.")
    response = request_with_read_retry(
        "POST", f"{deploy.FABRIC_BASE}/workspaces/{workspace}/{collection}/{agent_id}/getDefinition",
        headers=headers, timeout=60,
    )
    response.raise_for_status()
    if response.status_code == 202:
        location = definition_operation_url(response.headers["Location"])
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            operation_response = request_with_read_retry("GET", location, headers=headers, timeout=60)
            operation_response.raise_for_status()
            operation = operation_response.json()
            if operation["status"] == "Succeeded":
                response = request_with_read_retry("GET", location + "/result", headers=headers, timeout=60)
                response.raise_for_status()
                break
            if operation["status"] in ("Failed", "Cancelled"):
                raise deploy.DeployError(f"{collection} definition read failed: {operation}")
            time.sleep(2)
        else:
            raise deploy.DeployError(f"{collection} definition read timed out.")
    return response.json()


def published_sql_source_ready(definition: dict, workspace: str, sql_id: str) -> bool:
    required = {"WorkOrders", "MaintenanceNotifications", "Inspections", "SpareParts", "Asset3DModels"}
    for part in definition.get("definition", {}).get("parts", []):
        path = part.get("path", "")
        if not path.startswith("Files/Config/published/") or not path.endswith("/datasource.json"):
            continue
        source = json.loads(base64.b64decode(part["payload"], validate=True).decode("utf-8"))
        if (source.get("type") != "sql_database" or source.get("workspaceId") != workspace
                or source.get("artifactId") != sql_id):
            continue
        selected = set()
        pending = list(source.get("elements", []))
        while pending:
            element = pending.pop()
            if element.get("type") == "sql_database.table" and element.get("is_selected") is True:
                selected.add(element.get("display_name"))
            pending.extend(element.get("children", []))
        if required.issubset(selected):
            return True
    return False


def linked_application_insights(connections: list[dict]) -> str:
    insights = {
        connection["properties"]["target"]
        for connection in connections
        if connection.get("properties", {}).get("category") == "AppInsights"
        and connection.get("properties", {}).get("target")
    }
    if len(insights) != 1:
        raise RuntimeError("The selected Foundry project must have one linked Application Insights resource for trace drill-down.")
    resource_id = insights.pop()
    if not isinstance(resource_id, str) or not re.fullmatch(
        r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/microsoft\.insights/components/[^/]+",
        resource_id, re.IGNORECASE,
    ):
        raise RuntimeError("The linked Application Insights target is not an authoritative resource ID.")
    return resource_id


def hosted_invocations_url(agent: dict, endpoint: str) -> str:
    latest = agent.get("versions", {}).get("latest", {})
    definition = latest.get("definition", {})
    if (agent.get("name") != "hydro-orchestrator" or definition.get("kind") != "hosted"
            or latest.get("status") != "active"):
        raise RuntimeError("The existing Hydro Agent Framework runtime must be an active hosted agent.")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".services.ai.azure.com"):
        raise RuntimeError("The Foundry project endpoint is invalid.")
    return endpoint.rstrip("/") + "/agents/hydro-orchestrator/endpoint/protocols/invocations?api-version=v1"


def hosted_runtime_identity(agent: dict) -> tuple[str, str]:
    latest = agent.get("versions", {}).get("latest", {})
    version = str(latest.get("version") or "")
    image = latest.get("definition", {}).get("container_configuration", {}).get("image")
    match = re.fullmatch(r"[^@\s]+@(?P<digest>sha256:[0-9a-f]{64})", image or "")
    if not version.isdecimal() or not match:
        raise RuntimeError("The active hosted runtime must expose a numeric version and immutable image digest.")
    return version, match.group("digest")


def definition_matches(actual: dict, expected: dict) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def source_configuration_digest(tenant: str, workspace: str, ontology: str, values: dict[str, str]) -> str:
    config = {
        "tenant_id": tenant,
        "workspace_id": workspace,
        "ontology_id": ontology,
        "eventhouse_id": values.get("RAYFIN_PUBLIC_EVENTHOUSE_ID", ""),
        "database_id": values.get("RAYFIN_PUBLIC_KQL_DATABASE_ID", ""),
        "lakehouse_id": values.get("RAYFIN_PUBLIC_LAKEHOUSE_ID", ""),
        "appbackend_id": values.get("RAYFIN_PUBLIC_ITEM_ID", ""),
        "operational_sql_database_id": values.get("RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID", ""),
        "api_url": values.get("RAYFIN_PUBLIC_API_URL", ""),
        "publishable_key": values.get("RAYFIN_PUBLIC_PUBLISHABLE_KEY", ""),
    }
    if any(not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        config[key],
    ) for key in tuple(config)[:8]):
        raise RuntimeError("Hosted source identity is incomplete; all selected item IDs must be verified GUIDs.")
    api = urlparse(config["api_url"])
    if (api.scheme != "https" or api.username or api.password or api.port or api.query or api.fragment
            or not re.fullmatch(r"[0-9a-f]{32}\.pbidedicated\.windows\.net", api.hostname or "")):
        raise RuntimeError("Hosted source AppBackend endpoint is not the selected capacity endpoint.")
    if not re.fullmatch(r"pk-\S+", config["publishable_key"]):
        raise RuntimeError("Hosted source publishable key is missing.")
    return hashlib.sha256(json.dumps(config, separators=(",", ":")).encode()).hexdigest()


def verify_hosted_source(agent: dict, tenant: str, workspace: str, ontology: str, digest: str) -> None:
    try:
        raw = agent["versions"]["latest"]["definition"]["environment_variables"]["HYDRO_ORCHESTRATOR_CONFIG"]
        configuration = json.loads(raw)
        source = configuration["source"]
        native_binding = configuration["native_binding"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("The active hosted runtime has no readable source/native-binding configuration.") from error
    expected = {
        "tenant_id": tenant,
        "workspace_id": workspace,
        "ontology_id": ontology,
        "generation": 2,
        "configuration_digest": digest,
    }
    if source != expected:
        raise RuntimeError("The active hosted runtime source identity does not match the selected Fabric deployment.")
    if native_binding != {"source": expected, "connections": {"data-agent": "hydro-fabric-iq"},
                          "toolbox": "hydro-fabric-iq-tools"}:
        raise RuntimeError("The active hosted runtime must retain only the verified Data Agent native binding.")


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROJECT_RESOURCE_RE = re.compile(
    r"/subscriptions/([0-9a-f-]{36})/resourceGroups/[^/?#]+/providers/"
    r"Microsoft\.CognitiveServices/accounts/[A-Za-z0-9-]+/projects/[A-Za-z0-9_.-]+",
    re.IGNORECASE,
)


def resolve_subscription(deploy, tenant: str, prepared_subscription: str = "") -> str:
    selected = os.environ.get("HYDRO_AZURE_SUBSCRIPTION_ID", "").strip() or prepared_subscription
    if selected and not deploy.GUID_RE.fullmatch(selected):
        raise deploy.DeployError("HYDRO_AZURE_SUBSCRIPTION_ID must be a subscription GUID.")
    arguments = ("--subscription", selected) if selected else ()
    account = json.loads(deploy.run_capture(deploy.az("account", "show", *arguments, "-o", "json")))
    subscription = str(account.get("id") or "")
    if (not deploy.GUID_RE.fullmatch(subscription)
            or str(account.get("tenantId") or "").casefold() != tenant.casefold()
            or account.get("state") != "Enabled"):
        raise deploy.DeployError("The selected Azure subscription must be enabled and belong to the target Fabric tenant.")
    print(f"Foundry: using subscription {subscription} in tenant {tenant}.", flush=True)
    return subscription


def select_model_deployment(deployments: list[dict], wanted: str) -> str:
    ready = [
        item for item in deployments
        if item.get("properties", {}).get("provisioningState") == "Succeeded"
    ]
    if wanted:
        matches = [item for item in ready if item.get("name") == wanted]
    else:
        matches = [
            item for item in ready
            if item.get("properties", {}).get("model", {}).get("format") == "OpenAI"
            and item.get("properties", {}).get("capabilities", {}).get("chatCompletion") in (True, "true")
        ]
    if len(matches) != 1:
        names = ", ".join(str(item.get("name")) for item in ready
                          if item.get("properties", {}).get("capabilities", {}).get("chatCompletion") in (True, "true"))
        raise RuntimeError(
            "Under 'Project and model choices', enter one ready chat model deployment "
            "(HYDRO_FOUNDRY_MODEL). Automatic selection requires exactly one chat deployment. "
            f"Ready chat deployments: {names or '(none)' }."
        )
    return matches[0]["name"]


def fabric_iq_target_matches(target: str, workspace: str, item: str, *, ontology: bool = False) -> bool:
    parsed = urlparse(target)
    path = (f"/v1/mcp/dataPlane/workspaces/{workspace}/items/{item}/ontologyEndpoint" if ontology
            else f"/v1/mcp/workspaces/{workspace}/dataagents/{item}/agent")
    workspace_host = re.fullmatch(
        re.escape(workspace.replace("-", "").lower()) + r"\.z[0-9a-f]{2}\.w\.api\.fabric\.microsoft\.com",
        parsed.hostname or "",
    )
    return bool(
        parsed.scheme == "https" and not parsed.username and not parsed.password
        and parsed.port in (None, 443) and not parsed.query and not parsed.fragment
        and (parsed.hostname == "api.fabric.microsoft.com" or workspace_host)
        and parsed.path.lower() == path.lower()
    )


def fabric_iq_target(workspace: str, item: str, connections: list[dict], *, ontology: bool = False) -> str:
    candidates = {
        properties["target"]
        for connection in connections
        if isinstance((properties := connection.get("properties")), dict)
        and isinstance(properties.get("metadata"), dict)
        and properties["metadata"].get("type") == "fabric_iq_preview"
        and isinstance(properties.get("target"), str)
        and fabric_iq_target_matches(properties["target"], workspace, item, ontology=ontology)
    }
    scoped = {target for target in candidates if urlparse(target).hostname != "api.fabric.microsoft.com"}
    if len(scoped) > 1:
        raise RuntimeError("Fabric IQ has conflicting workspace-specific endpoints for the selected item.")
    if scoped:
        return next(iter(scoped))
    return (f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace}/items/{item}/ontologyEndpoint"
            if ontology else f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace}/dataagents/{item}/agent")


def fabric_iq_connection_properties(target: str) -> dict:
    return {
        "category": "RemoteTool", "authType": "UserEntraToken", "target": target,
        "audience": "https://api.fabric.microsoft.com", "isSharedToAll": False,
        "metadata": {"type": "fabric_iq_preview"},
    }

def ensure_native_toolbox(endpoint: str, headers: dict, *, verify_only: bool = False) -> str:
    url = endpoint.rstrip("/") + "/toolboxes/hydro-fabric-iq-tools"
    tools = [{
        "type": "fabric_iq_preview", "name": "hydro-data", "server_label": "fabriciq-data-agent",
        "project_connection_id": "hydro-fabric-iq", "require_approval": "never",
    }]
    response = request_with_read_retry("GET", url + "?api-version=v1", headers=headers, timeout=60)
    if response.status_code not in (200, 404):
        raise RuntimeError(f"Native Foundry toolbox read failed: HTTP {response.status_code}.")
    current = {}
    if response.ok:
        version = response.json().get("default_version", "")
        if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]*", version):
            raise RuntimeError("The native Foundry toolbox has no valid default version.")
        response = request_with_read_retry(
            "GET", f"{url}/versions/{version}?api-version=v1", headers=headers, timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"Native Foundry toolbox version read failed: HTTP {response.status_code}.")
        current = response.json()
        if current.get("tools") == tools:
            print(f"Foundry: reusing native toolbox hydro-fabric-iq-tools:{version}.", flush=True)
            return version
    if verify_only:
        raise RuntimeError("The native Foundry toolbox is missing or its Data Agent binding has drifted.")
    body = {key: current[key] for key in ("policies", "skills", "metadata", "description") if key in current}
    body["tools"] = tools
    response = request_with_read_retry(
        "POST", url + "/versions?api-version=v1", headers=headers, json=body, timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Native Foundry toolbox creation failed: HTTP {response.status_code}.")
    version = response.json().get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]*", version):
        raise RuntimeError("Native Foundry toolbox creation returned no valid version.")
    response = request_with_read_retry(
        "PATCH", url + "?api-version=v1", headers=headers, json={"default_version": version}, timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Native Foundry toolbox publication failed: HTTP {response.status_code}.")
    if ensure_native_toolbox(endpoint, headers, verify_only=True) != version:
        raise RuntimeError("Native Foundry toolbox publication readback differs from the requested version.")
    return version


def verify_prepared_connections(deploy, tenant: str, workspace: str, ontology: str, values: dict[str, str]) -> None:
    value = lambda key: deploy._public_config_value(values, key)
    project_id = value("RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID")
    agent_id = value("RAYFIN_PUBLIC_FOUNDRY_DATA_AGENT_ID")
    if (not re.fullmatch(
            r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/?#]+/providers/"
            r"Microsoft\.CognitiveServices/accounts/[^/?#]+/projects/[^/?#]+", project_id)
            or not deploy.GUID_RE.fullmatch(agent_id)):
        raise RuntimeError("Prepared Foundry project/Data Agent identity is missing.")
    token = deploy.run_capture(deploy.az(
        "account", "get-access-token", "--tenant", tenant, "--resource", "https://management.azure.com/",
        "--query", "accessToken", "-o", "tsv",
    ))
    if not token:
        raise RuntimeError("No delegated Azure management token was returned.")

    def read(path: str) -> dict:
        response = request_with_read_retry(
            "GET", f"https://management.azure.com{path}", headers={"Authorization": f"Bearer {token}"}, timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"Prepared Foundry metadata read failed: HTTP {response.status_code}.")
        return response.json().get("properties", {})

    project = read(project_id + "?api-version=2025-06-01")
    if project.get("endpoints", {}).get("AI Foundry API", "").rstrip("/") != value("RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT").rstrip("/"):
        raise RuntimeError("Prepared Foundry project resource and endpoint do not match.")
    for name, item, is_ontology in (
        ("hydro-fabric-iq", agent_id, False), ("hydro-fabric-ontology", ontology, True),
    ):
        connection = read(f"{project_id}/connections/{name}?api-version=2025-10-01-preview")
        target = connection.get("target", "")
        if (not fabric_iq_target_matches(target, workspace, item, ontology=is_ontology)
                or not definition_matches(connection, fabric_iq_connection_properties(target))):
            raise RuntimeError(f"Prepared Foundry connection {name} no longer matches the selected source/authentication.")
    definition = read_data_agent_definition(deploy, workspace, agent_id, deploy.fabric_headers(tenant))
    verify_published_identity(definition, workspace, ontology)
    if not published_sql_source_ready(definition, workspace, value("RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID")):
        raise RuntimeError("The connected Data Agent no longer exposes the verified operational SQL source.")
    token = deploy.run_capture(deploy.az(
        "account", "get-access-token", "--tenant", tenant, "--resource", "https://ai.azure.com",
        "--query", "accessToken", "-o", "tsv",
    ))
    ensure_native_toolbox(value("RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT"),
                          {"Authorization": f"Bearer {token}"}, verify_only=True)


def verify_existing(deploy, tenant: str, workspace: str) -> None:
    """Read back prepared agents without project discovery, builds, or publication."""
    values, _ = deploy.current_rayfin_target()
    value = lambda key: deploy._public_config_value(values, key)
    guidance = "Run the local Foundry multi-agent setup module, then publish the Fabric app again."
    if value("RAYFIN_PUBLIC_TENANT_ID") != tenant or value("RAYFIN_PUBLIC_WORKSPACE_ID") != workspace:
        raise deploy.DeployError(f"Foundry configuration belongs to a different target. {guidance}")
    endpoint = value("RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT").rstrip("/")
    model = value("RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT")
    if not re.fullmatch(r"https://[a-z0-9-]+\.services\.ai\.azure\.com/api/projects/[A-Za-z0-9_.-]+", endpoint) or not model:
        raise deploy.DeployError(f"Verified Foundry project/model configuration is missing. {guidance}")
    hosted_module = _load_sibling("deploy_hosted_agent")
    if value("RAYFIN_PUBLIC_ORCHESTRATOR_CODE_DIGEST") != hosted_module.source_fingerprint(deploy.REPO_ROOT):
        raise deploy.DeployError(f"Foundry setup is missing or its packaged code is stale. {guidance}")
    try:
        binding = json.loads(value("RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"))
        ontology = binding["ontologyId"]
        public_values = {key: deploy._public_config_value(values, key) for key in (
            "RAYFIN_PUBLIC_EVENTHOUSE_ID", "RAYFIN_PUBLIC_KQL_DATABASE_ID", "RAYFIN_PUBLIC_LAKEHOUSE_ID",
            "RAYFIN_PUBLIC_ITEM_ID", "RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID",
            "RAYFIN_PUBLIC_API_URL", "RAYFIN_PUBLIC_PUBLISHABLE_KEY",
        )}
        digest = source_configuration_digest(tenant, workspace, ontology, public_values)
    except (ValueError, KeyError, TypeError, RuntimeError) as error:
        raise deploy.DeployError(f"Foundry source configuration is invalid. {guidance} {error}") from error
    if digest != value("RAYFIN_PUBLIC_ORCHESTRATOR_SOURCE_DIGEST"):
        raise deploy.DeployError(f"Foundry source identities changed. {guidance}")
    metadata = deploy.fabric_get(f"workspaces/{workspace}/ontologies/{ontology}", deploy.fabric_headers(tenant))
    generation = metadata.get("properties", {}).get("generation")
    if type(generation) is not int or generation != 2:
        raise deploy.DeployError("Live numeric Ontology generation 2 is required.")
    if not deploy._published_operational_source_ready(workspace, values, "", deploy.fabric_headers(tenant)):
        raise deploy.DeployError(f"Published Data Agent/SQL source configuration is incomplete. {guidance}")
    try:
        verify_prepared_connections(deploy, tenant, workspace, ontology, values)
    except RuntimeError as error:
        raise deploy.DeployError(f"{error} {guidance}") from error
    token = deploy.run_capture(deploy.az(
        "account", "get-access-token", "--tenant", tenant, "--resource", "https://ai.azure.com",
        "--query", "accessToken", "-o", "tsv",
    ))
    if not token:
        raise deploy.DeployError("No delegated Foundry token was returned.")
    headers = {"Authorization": f"Bearer {token}"}
    definitions = json.loads(deploy.run_capture(deploy.node24_script(
        deploy.APP_DIR / "scripts" / "export-agent-definitions.ts", model, "hydro-fabric-iq",
    ), cwd=deploy.APP_DIR))
    print("Foundry: read-only verification of prepared specialists and Hosted coordinator.", flush=True)
    for name in (*definitions, "hydro-orchestrator"):
        response = request_with_read_retry(
            "GET", f"{endpoint}/agents/{name}?api-version=v1", headers=headers, timeout=60,
        )
        if not response.ok:
            raise deploy.DeployError(f"Cannot verify Foundry agent {name}: HTTP {response.status_code}. {guidance}")
        agent = response.json()
        latest = agent.get("versions", {}).get("latest", {})
        if name != "hydro-orchestrator":
            if not definition_matches(latest.get("definition", {}), definitions[name]):
                raise deploy.DeployError(f"Foundry specialist {name} is stale. {guidance}")
            continue
        try:
            verify_hosted_source(agent, tenant, workspace, ontology, digest)
            version, image = hosted_runtime_identity(agent)
            url = hosted_invocations_url(agent, endpoint)
        except RuntimeError as error:
            raise deploy.DeployError(f"{error} {guidance}") from error
        if (version != value("RAYFIN_PUBLIC_ORCHESTRATOR_VERSION")
                or image != value("RAYFIN_PUBLIC_ORCHESTRATOR_IMAGE_DIGEST")
                or url != value("RAYFIN_PUBLIC_FOUNDRY_INVOCATIONS_URL")):
            raise deploy.DeployError(f"Foundry Hosted runtime identity changed. {guidance}")
    print("Prepared Foundry configuration verified; no agents were provisioned or republished.", flush=True)


class FoundrySelection(NamedTuple):
    subscription: str
    project_id: str
    endpoint: str
    model: str
    creation_settings: dict[str, str] | None = None


def creation_location(deploy, tenant: str, workspace: str, project_module) -> str:
    override = os.environ.get("HYDRO_FOUNDRY_LOCATION", "").strip()
    if override:
        return project_module.normalize_location(override)
    headers = deploy.fabric_headers(tenant)
    capacity_id = str(deploy.fabric_get(f"workspaces/{workspace}", headers).get("capacityId") or "").lower()
    response = request_with_read_retry("GET", f"{deploy.FABRIC_BASE}/capacities", headers=headers, timeout=60)
    if not response.ok:
        raise deploy.DeployError(f"Cannot read the Fabric capacity region: HTTP {response.status_code}.")
    region = next((
        str(item.get("region") or "") for item in response.json().get("value", [])
        if str(item.get("id") or "").lower() == capacity_id
    ), "")
    if not region:
        raise deploy.DeployError("Fabric capacity region is unavailable. Set the region under new-project defaults.")
    return project_module.normalize_location(region)


def resolve_project(
    deploy, tenant: str, workspace: str, *, allow_create: bool = False,
    creation_settings: dict[str, str] | None = None,
) -> FoundrySelection:
    """Resolve the target before backend mutation; create only in the provisioning pass."""
    values, _ = deploy.current_rayfin_target()
    print("Foundry: project - resolving the Foundry project.", flush=True)
    same_target = (
        deploy._public_config_value(values, "RAYFIN_PUBLIC_TENANT_ID").casefold() == tenant.casefold()
        and deploy._public_config_value(values, "RAYFIN_PUBLIC_WORKSPACE_ID").casefold() == workspace.casefold()
    )
    explicit_project_id = os.environ.get("HYDRO_FOUNDRY_PROJECT_RESOURCE_ID", "").strip()
    project_match = PROJECT_RESOURCE_RE.fullmatch(explicit_project_id) if explicit_project_id else None
    if explicit_project_id and not project_match:
        raise deploy.DeployError("HYDRO_FOUNDRY_PROJECT_RESOURCE_ID must be the full Foundry project ARM resource ID.")
    prepared_subscription = (
        project_match[1] if project_match else
        deploy._public_config_value(values, "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID") if same_target else ""
    )
    subscription = resolve_subscription(deploy, tenant, prepared_subscription)
    same_target = (
        same_target
        and deploy._public_config_value(values, "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID").casefold() in ("", subscription.casefold())
    )
    saved_project_id = deploy._public_config_value(values, "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID") if same_target else ""
    saved_endpoint = (
        deploy._public_config_value(values, "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT")
        if same_target and (not explicit_project_id or explicit_project_id.casefold() == saved_project_id.casefold())
        else ""
    )
    explicit_endpoint = os.environ.get("HYDRO_FOUNDRY_PROJECT_ENDPOINT", "").strip()
    project_name = os.environ.get("HYDRO_FOUNDRY_EXISTING_PROJECT_NAME", "").strip()
    if project_name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", project_name):
        raise deploy.DeployError("Existing Foundry project name must contain only letters, digits, '.', '_' or '-'.")
    if project_name and not explicit_project_id and not explicit_endpoint \
            and saved_project_id.rsplit("/", 1)[-1].casefold() != project_name.casefold():
        saved_project_id = ""
        saved_endpoint = ""
    endpoint = explicit_endpoint or saved_endpoint
    model = os.environ.get("HYDRO_FOUNDRY_MODEL", "").strip()
    if not model and endpoint and endpoint.rstrip("/") == saved_endpoint.rstrip("/"):
        model = deploy._public_config_value(values, "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT")

    def discover(wanted: str) -> list[tuple[str, str]]:
        projects = json.loads(deploy.run_capture(deploy.az(
            "resource", "list", "--subscription", subscription,
            "--resource-type", "Microsoft.CognitiveServices/accounts/projects", "-o", "json",
        )))
        found = []
        wanted_name = project_name or (urlparse(wanted).path.rstrip("/").rsplit("/", 1)[-1] if wanted else "")
        if wanted_name:
            projects = [
                project for project in projects
                if project["id"].rsplit("/", 1)[-1].casefold() == wanted_name.casefold()
            ]
            print(f"Foundry: checking {len(projects)} matching project resource(s) for '{wanted_name}'; "
                  "unrelated project metadata is skipped.", flush=True)
        for project in projects:
            detail = json.loads(deploy.run_capture(deploy.az(
                "resource", "show", "--ids", project["id"], "--api-version", "2025-06-01", "-o", "json",
            )))
            url = detail.get("properties", {}).get("endpoints", {}).get("AI Foundry API", "").rstrip("/")
            if (url and (not wanted or url == wanted.rstrip("/"))
                    and (not project_name or detail["id"].rsplit("/", 1)[-1].casefold() == project_name.casefold())):
                found.append((detail["id"], url))
        return found

    project_id = explicit_project_id or (
        saved_project_id if not explicit_endpoint or explicit_endpoint.rstrip("/") == saved_endpoint.rstrip("/") else ""
    )
    if project_id:
        identity = PROJECT_RESOURCE_RE.fullmatch(project_id)
        if not identity or identity[1].casefold() != subscription.casefold():
            raise deploy.DeployError("The selected Foundry project resource ID must belong to the selected subscription.")
        if project_name and project_id.rsplit("/", 1)[-1].casefold() != project_name.casefold():
            raise deploy.DeployError("The selected Foundry project resource ID does not match the existing project name.")
        detail = json.loads(deploy.run_capture(deploy.az(
            "resource", "show", "--ids", project_id, "--api-version", "2025-06-01", "-o", "json",
        )))
        live_endpoint = str(detail.get("properties", {}).get("endpoints", {}).get("AI Foundry API") or "").rstrip("/")
        if (str(detail.get("id") or "").casefold() != project_id.casefold()
                or not live_endpoint or (endpoint and endpoint.rstrip("/") != live_endpoint)):
            raise deploy.DeployError("Foundry project identity/endpoint readback does not match; refusing project discovery or replacement.")
        candidates = [(project_id, live_endpoint)]
        endpoint = live_endpoint
        print("Foundry: verified the selected project directly; no subscription-wide resource discovery.", flush=True)
    else:
        candidates = discover(endpoint)
    if endpoint and not candidates and not explicit_endpoint:
        print("Foundry: saved project is absent from the selected subscription; discarding its endpoint/model hints.", flush=True)
        endpoint = ""
        model = os.environ.get("HYDRO_FOUNDRY_MODEL", "").strip()
        candidates = discover("")
    if endpoint and not candidates:
        raise deploy.DeployError(
            "The selected Foundry project endpoint was not found in the current subscription. "
            "Verify the tenant, subscription and HYDRO_FOUNDRY_PROJECT_ENDPOINT; refusing to create "
            "a different project in place of the selected one."
        )
    project_module = _load_sibling("deploy_foundry_project")
    if not endpoint and not project_name:
        from deploy_foundry_project import environment_name

        expected_name = "ai-project-" + environment_name(tenant, workspace, subscription)
        conventional = [candidate for candidate in candidates
                        if candidate[0].rsplit("/", 1)[-1].casefold() == expected_name.casefold()]
        if len(conventional) == 1:
            candidates = conventional
            print(f"Foundry: reusing this target's convention-named project {expected_name}.", flush=True)
        elif project_module.enabled():
            candidates = []
    if project_name and not candidates:
        raise deploy.DeployError(
            f"Existing Foundry project '{project_name}' was not found in the selected subscription. "
            "Check the project name under 'Project and model choices'; no replacement was created."
        )
    if not candidates and project_module.enabled() and not allow_create:
        location = creation_location(deploy, tenant, workspace, project_module)
        settings = project_module.model_defaults(
            deploy, subscription=subscription, location=location, model=model,
        )
        try:
            project_module.deployments_parameter(model, settings)
        except ValueError as exc:
            raise deploy.DeployError(str(exc)) from exc
        return FoundrySelection(subscription, "", "", model, {**settings, "location": location})
    if not candidates and project_module.enabled():
        hosted_module = _load_sibling("deploy_hosted_agent")

        def agent_access(project_endpoint: str) -> bool:
            access = deploy.run_capture(deploy.az(
                "account", "get-access-token", "--tenant", tenant, "--resource", "https://ai.azure.com",
                "--query", "accessToken", "-o", "tsv",
            ))
            return request_with_read_retry(
                "GET", f"{project_endpoint}/agents?api-version=v1", headers={"Authorization": f"Bearer {access}"},
                timeout=60,
            ).status_code == 200

        print("Foundry: project - no project selected; provisioning the target-specific project as requested.", flush=True)
        location = (creation_settings or {}).get("location") or creation_location(deploy, tenant, workspace, project_module)
        settings = creation_settings or project_module.model_defaults(
            deploy, subscription=subscription, location=location, model=model,
        )
        created = project_module.ensure_foundry_project(
            deploy, hosted_module, tenant=tenant, workspace=workspace, subscription=subscription,
            location=location, model=model, check_access=agent_access, model_settings=settings,
        )
        candidates = discover(created)
    if len(candidates) != 1:
        choices = "\n".join(f"  - {identity.rsplit('/', 1)[-1]} ({identity})" for identity, _ in candidates)
        raise deploy.DeployError(
            "Select exactly one existing Foundry project. In the local app, open 'Project and model choices' "
            "and enter an existing project name (HYDRO_FOUNDRY_EXISTING_PROJECT_NAME). "
            "Use a full project resource ID only when the name is duplicated."
            + (f"\nAvailable projects:\n{choices}" if candidates else
               " No matching project was found. To create one, explicitly approve new project creation.")
        )
    project_id, endpoint = candidates[0]
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".services.ai.azure.com"):
        raise deploy.DeployError("Foundry project metadata returned an invalid endpoint.")
    model = read_model_selection(deploy, tenant, project_id, model)
    print(f"Foundry: project and model verified before backend provisioning ({project_id.rsplit('/', 1)[-1]}, {model}).", flush=True)
    return FoundrySelection(subscription, project_id, endpoint, model)


def read_model_selection(deploy, tenant: str, project_id: str, wanted: str) -> str:
    access = deploy.run_capture(deploy.az(
        "account", "get-access-token", "--tenant", tenant, "--resource", "https://management.azure.com/",
        "--query", "accessToken", "-o", "tsv",
    ))
    if not access:
        raise deploy.DeployError("No delegated Azure management token was returned.")
    account_id = project_id.rsplit("/projects/", 1)[0]
    path = f"{account_id}/deployments" + (f"/{quote(wanted, safe='')}" if wanted else "")
    if wanted:
        print(f"Foundry: verifying selected model deployment '{wanted}' directly; no model discovery.", flush=True)
    url = f"https://management.azure.com{path}?api-version=2025-06-01"
    deployments = []
    while url:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "management.azure.com" or parsed.path != path:
            raise deploy.DeployError("Unexpected Foundry model pagination URL.")
        response = request_with_read_retry(
            "GET", url, headers={"Authorization": f"Bearer {access}"}, timeout=60,
        )
        if not response.ok:
            raise deploy.DeployError(f"Cannot read Foundry model deployments: HTTP {response.status_code}.")
        page = response.json()
        deployments.extend([page] if wanted else page.get("value", []))
        url = None if wanted else page.get("nextLink")
    try:
        return select_model_deployment(deployments, wanted)
    except RuntimeError as exc:
        raise deploy.DeployError(str(exc)) from exc


def provision(deploy, tenant: str, workspace: str, *, selection: FoundrySelection | None = None) -> None:
    selection = selection or resolve_project(deploy, tenant, workspace, allow_create=True)
    if not selection.project_id:
        # Read-only preflight already resolved the model settings; creation still uses the same target convention.
        selection = resolve_project(
            deploy, tenant, workspace, allow_create=True, creation_settings=selection.creation_settings,
        )
    subscription, project_id, endpoint, model, _ = selection
    values, _ = deploy.current_rayfin_target()

    def token(resource):
        value = deploy.run_capture(deploy.az("account", "get-access-token", "--tenant", tenant, "--resource", resource, "--query", "accessToken", "-o", "tsv"))
        if not value:
            raise deploy.DeployError(f"No delegated token was returned for {resource}.")
        return {"Authorization": f"Bearer {value}"}

    arm_headers = token("https://management.azure.com/")
    agent_headers = token("https://ai.azure.com")
    fabric_headers = deploy.fabric_headers(tenant)

    def request(method, url, headers, **kwargs):
        response = request_with_read_retry(method, url, headers=headers, timeout=180, **kwargs)
        if not response.ok:
            raise deploy.DeployError(f"Foundry provisioning failed: {method} {url}: HTTP {response.status_code}: {response.text[:800]}")
        return response

    print(f"Foundry: using verified model deployment {model}.", flush=True)

    project_connections = request("GET", f"https://management.azure.com{project_id}/connections?api-version=2025-10-01-preview", arm_headers).json()
    connections = project_connections.get("value", [])
    while project_connections.get("nextLink"):
        next_link = urlparse(project_connections["nextLink"])
        if next_link.scheme != "https" or next_link.netloc != "management.azure.com" or next_link.path != f"{project_id}/connections":
            raise deploy.DeployError("Unexpected Foundry connection pagination URL.")
        project_connections = request("GET", project_connections["nextLink"], arm_headers).json()
        connections.extend(project_connections.get("value", []))
    try:
        insights_id = linked_application_insights(connections)
    except RuntimeError as error:
        raise deploy.DeployError(str(error)) from error

    binding = json.loads(deploy._public_config_value(values, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"))
    ontology = binding["ontologyId"]
    public_values = {
        key: deploy._public_config_value(values, key)
        for key in (
            "RAYFIN_PUBLIC_EVENTHOUSE_ID",
            "RAYFIN_PUBLIC_KQL_DATABASE_ID",
            "RAYFIN_PUBLIC_LAKEHOUSE_ID",
            "RAYFIN_PUBLIC_ITEM_ID",
            "RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID",
            "RAYFIN_PUBLIC_API_URL",
            "RAYFIN_PUBLIC_PUBLISHABLE_KEY",
        )
    }
    try:
        source_digest = source_configuration_digest(tenant, workspace, ontology, public_values)
    except RuntimeError as error:
        raise deploy.DeployError(str(error)) from error
    metadata = request("GET", f"{deploy.FABRIC_BASE}/workspaces/{workspace}/ontologies/{ontology}", fabric_headers).json()
    generation = metadata.get("properties", {}).get("generation")
    if type(generation) is not int or generation != 2:
        raise deploy.DeployError("Live numeric Ontology generation 2 is required for Fabric IQ.")
    items = deploy._workspace_artifacts(workspace, fabric_headers)
    agents = [item for item in items if item["type"] == "DataAgent" and item["displayName"].startswith("RTI_Demo_Agent_")]
    selected = []
    for agent in agents:
        definition = read_data_agent_definition(deploy, workspace, agent["id"], fabric_headers)
        try:
            verify_published_identity(definition, workspace, ontology)
        except RuntimeError:
            continue
        selected.append(agent)
    if len(selected) != 1:
        raise deploy.DeployError("Exactly one published Data Agent must match the verified v2 ontology.")

    iq_name = "hydro-fabric-iq"
    iq_url = fabric_iq_target(workspace, selected[0]["id"], connections)
    ontology_connection = "hydro-fabric-ontology"
    ontology_url = fabric_iq_target(workspace, ontology, connections, ontology=True)
    for connection_name, target in ((iq_name, iq_url), (ontology_connection, ontology_url)):
        connection_url = f"https://management.azure.com{project_id}/connections/{connection_name}?api-version=2025-10-01-preview"
        connection = fabric_iq_connection_properties(target)
        matching = [item for item in connections if item.get("name") == connection_name]
        if len(matching) > 1:
            raise deploy.DeployError(f"Foundry connection {connection_name} has duplicate live entries.")
        if matching and definition_matches(matching[0].get("properties", {}), connection):
            print(f"Foundry: reusing current connection {connection_name}; no update needed.", flush=True)
        else:
            print(f"Foundry: updating connection {connection_name}; source or authentication differs.", flush=True)
            request("PUT", connection_url, arm_headers, json={"properties": connection})
            readback = request("GET", connection_url, arm_headers).json()["properties"]
            if not definition_matches(readback, connection):
                raise deploy.DeployError(f"Fabric IQ connection {connection_name} readback did not match its requested identity/authentication.")

    definitions = json.loads(deploy.run_capture(deploy.node24_script(
        deploy.APP_DIR / "scripts" / "export-agent-definitions.ts", model, iq_name,
    ), cwd=deploy.APP_DIR))
    print("Foundry: agents - comparing live specialists with the current source definitions.", flush=True)
    reused = updated = created = 0
    for name, definition in definitions.items():
        url = f"{endpoint}/agents/{name}"
        response = request_with_read_retry("GET", url + "?api-version=v1", headers=agent_headers, timeout=60)
        if response.status_code not in (200, 404):
            raise deploy.DeployError(f"Cannot read Foundry agent {name}: HTTP {response.status_code}")
        previous = response.json().get("versions", {}).get("latest", {}) if response.ok else {}
        if definition_matches(previous.get("definition", {}), definition):
            reused += 1
            print(f"Reusing current Foundry agent {name}:{previous['version']}; definition unchanged.", flush=True)
            continue
        changes = ", ".join(key for key, value in definition.items() if previous.get("definition", {}).get(key) != value)
        print(f"Foundry: {'updating' if response.ok else 'creating'} {name}: {changes}.", flush=True)
        if response.ok:
            updated += 1
        else:
            created += 1
        request("POST", url + "/versions?api-version=v1", agent_headers, json={"definition": definition})
        live = request("GET", url + "?api-version=v1", agent_headers).json()["versions"]["latest"]
        if not definition_matches(live["definition"], definition):
            raise deploy.DeployError(f"Foundry definition readback failed for {name}.")
        print(f"Verified persistent Foundry agent {name}:{live['version']}", flush=True)
    print(f"Foundry: specialists current={reused}, updated={updated}, created={created}.", flush=True)
    ensure_native_toolbox(endpoint, agent_headers)

    kql = request(
        "GET", f"{deploy.FABRIC_BASE}/workspaces/{workspace}/kqlDatabases/{public_values['RAYFIN_PUBLIC_KQL_DATABASE_ID']}",
        fabric_headers,
    ).json()
    query_uri = urlparse(kql.get("properties", {}).get("queryServiceUri", ""))
    if query_uri.scheme != "https" or not query_uri.netloc:
        raise deploy.DeployError("The selected KQL database has no verifiable query service URI.")

    def get_agent():
        response = request_with_read_retry(
            "GET", f"{endpoint}/agents/hydro-orchestrator?api-version=v1", headers=agent_headers, timeout=60,
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise deploy.DeployError(f"Cannot read the hosted agent: HTTP {response.status_code}: {response.text[:400]}")
        return response.json()

    hosted_module = _load_sibling("deploy_hosted_agent")
    print("Foundry: Hosted Agent - converging hydro-orchestrator.", flush=True)
    hosted = hosted_module.ensure_hosted_agent(
        deploy, get_agent=get_agent, endpoint=endpoint, project_id=project_id, model=model, tenant=tenant,
        workspace=workspace, ontology=ontology,
        spa_client_id=deploy._public_config_value(values, "RAYFIN_PUBLIC_AAD_CLIENT_ID"),
        values=public_values, source_digest=source_digest, kusto_origin=f"https://{query_uri.netloc}",
        connections=connections,
    )
    try:
        invocations_url = hosted_invocations_url(hosted, endpoint)
        runtime_version, runtime_image_digest = hosted_runtime_identity(hosted)
        verify_hosted_source(hosted, tenant, workspace, ontology, source_digest)
    except RuntimeError as error:
        raise deploy.DeployError(str(error)) from error
    env_path = deploy.RAYFIN_DIR / ".env"
    env_path.write_text(deploy._rebind_public_env(env_path.read_text(encoding="utf-8"), {
        "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID": subscription,
        "RAYFIN_PUBLIC_FOUNDRY_PROJECT_RESOURCE_ID": project_id,
        "RAYFIN_PUBLIC_FOUNDRY_DATA_AGENT_ID": selected[0]["id"],
        "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": model,
        "RAYFIN_PUBLIC_FOUNDRY_APP_INSIGHTS_RESOURCE_ID": insights_id,
        "RAYFIN_PUBLIC_FOUNDRY_INVOCATIONS_URL": invocations_url,
        "RAYFIN_PUBLIC_ORCHESTRATOR_VERSION": runtime_version,
        "RAYFIN_PUBLIC_ORCHESTRATOR_IMAGE_DIGEST": runtime_image_digest,
        "RAYFIN_PUBLIC_ORCHESTRATOR_SOURCE_DIGEST": source_digest,
        "RAYFIN_PUBLIC_ORCHESTRATOR_CODE_DIGEST": hosted_module.source_fingerprint(deploy.REPO_ROOT),
    }), encoding="utf-8")
    print("Foundry configuration readback verified; agent runtime acceptance is a separate check.", flush=True)
