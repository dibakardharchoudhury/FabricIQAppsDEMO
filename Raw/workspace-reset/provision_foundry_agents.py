"""Provision persistent Hydro agents from the app's versioned source definitions.

Called by deploy_fabric_app.py, never a separate app deployment path.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from urllib.parse import urlparse

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
        if not path.startswith("Files/Config/published/") or not path.endswith("/datasource.json"):
            continue
        source = json.loads(base64.b64decode(part["payload"], validate=True).decode("utf-8"))
        if str(source.get("type", "")).lower() != "ontology":
            continue
        if source.get("workspaceId", "").lower() != workspace.lower() or source.get("artifactId", "").lower() != ontology.lower():
            raise RuntimeError("The published Data Agent has a mismatched ontology source.")
        matched = True
    if not matched:
        raise RuntimeError("No published Data Agent source matches the selected Ontology v2.")


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


def source_configuration_digest(tenant: str, workspace: str, ontology: str, values: dict[str, str]) -> str:
    config = {
        "tenant_id": tenant,
        "workspace_id": workspace,
        "ontology_id": ontology,
        "eventhouse_id": values.get("RAYFIN_PUBLIC_EVENTHOUSE_ID", ""),
        "database_id": values.get("RAYFIN_PUBLIC_KQL_DATABASE_ID", ""),
        "graphql_id": values.get("RAYFIN_PUBLIC_STID_GRAPHQL_ID", ""),
        "appbackend_id": values.get("RAYFIN_PUBLIC_ITEM_ID", ""),
        "api_url": values.get("RAYFIN_PUBLIC_API_URL", ""),
        "publishable_key": values.get("RAYFIN_PUBLIC_PUBLISHABLE_KEY", ""),
    }
    if any(not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        config[key],
    ) for key in tuple(config)[:7]):
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
        source = json.loads(raw)["source"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("The active hosted runtime has no readable source identity configuration.") from error
    expected = {
        "tenant_id": tenant,
        "workspace_id": workspace,
        "ontology_id": ontology,
        "generation": 2,
        "configuration_digest": digest,
    }
    if source != expected:
        raise RuntimeError("The active hosted runtime source identity does not match the selected Fabric deployment.")


def provision(deploy, tenant: str, workspace: str) -> None:
    values, _ = deploy.current_rayfin_target()
    endpoint = os.environ.get("HYDRO_FOUNDRY_PROJECT_ENDPOINT", "").strip() or values.get("RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT", "").strip()
    projects = json.loads(deploy.run_capture(deploy.az(
        "resource", "list", "--resource-type", "Microsoft.CognitiveServices/accounts/projects", "-o", "json",
    )))
    candidates = []
    for project in projects:
        detail = json.loads(deploy.run_capture(deploy.az(
            "resource", "show", "--ids", project["id"], "--api-version", "2025-06-01", "-o", "json",
        )))
        url = detail.get("properties", {}).get("endpoints", {}).get("AI Foundry API", "").rstrip("/")
        if url and (not endpoint or url == endpoint.rstrip("/")):
            candidates.append((detail["id"], url))
    if len(candidates) != 1:
        raise deploy.DeployError("Select exactly one existing Foundry project with HYDRO_FOUNDRY_PROJECT_ENDPOINT; automatic discovery is ambiguous or unavailable.")
    project_id, endpoint = candidates[0]
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname.endswith(".services.ai.azure.com"):
        raise deploy.DeployError("Foundry project metadata returned an invalid endpoint.")

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

    model = os.environ.get("HYDRO_FOUNDRY_MODEL", "").strip() or "gpt-5-mini"
    account_id = project_id.rsplit("/projects/", 1)[0]
    model_data = request("GET", f"https://management.azure.com{account_id}/deployments/{model}?api-version=2025-06-01", arm_headers).json()
    if model_data.get("properties", {}).get("provisioningState") != "Succeeded":
        raise deploy.DeployError("The selected Foundry model deployment is not ready.")

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
            "RAYFIN_PUBLIC_STID_GRAPHQL_ID",
            "RAYFIN_PUBLIC_ITEM_ID",
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
        response = request("POST", f"{deploy.FABRIC_BASE}/workspaces/{workspace}/dataAgents/{agent['id']}/getDefinition", fabric_headers)
        if response.status_code == 202:
            location = definition_operation_url(response.headers["Location"])
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                operation = request("GET", location, fabric_headers).json()
                if operation["status"] == "Succeeded":
                    response = request("GET", location + "/result", fabric_headers)
                    break
                if operation["status"] in ("Failed", "Cancelled"):
                    raise deploy.DeployError(f"Data Agent definition read failed: {operation}")
                time.sleep(2)
            else:
                raise deploy.DeployError("Data Agent definition read timed out.")
        definition = response.json()
        try:
            verify_published_identity(definition, workspace, ontology)
        except RuntimeError:
            continue
        selected.append(agent)
    if len(selected) != 1:
        raise deploy.DeployError("Exactly one published Data Agent must match the verified v2 ontology.")

    iq_name = "hydro-fabric-iq"
    iq_url = f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace}/dataagents/{selected[0]['id']}/agent"
    ontology_connection = "hydro-fabric-ontology"
    ontology_url = f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspace}/items/{ontology}/ontologyEndpoint"
    for connection_name, target in ((iq_name, iq_url), (ontology_connection, ontology_url)):
        connection_url = f"https://management.azure.com{project_id}/connections/{connection_name}?api-version=2025-10-01-preview"
        connection = {
            "category": "RemoteTool", "authType": "UserEntraToken", "target": target,
            "audience": "https://analysis.windows.net/powerbi/api", "isSharedToAll": False,
        }
        request("PUT", connection_url, arm_headers, json={"properties": connection})
        readback = request("GET", connection_url, arm_headers).json()["properties"]
        if any(readback.get(key) != value for key, value in connection.items()):
            raise deploy.DeployError(f"Fabric IQ connection {connection_name} readback did not match its requested identity/authentication.")

    definitions = json.loads(deploy.run_capture(deploy.node24_script(
        deploy.APP_DIR / "scripts" / "export-agent-definitions.ts", model, iq_name, ontology_connection,
    ), cwd=deploy.APP_DIR))
    for name, definition in definitions.items():
        url = f"{endpoint}/agents/{name}"
        response = request_with_read_retry("GET", url + "?api-version=v1", headers=agent_headers, timeout=60)
        if response.status_code not in (200, 404):
            raise deploy.DeployError(f"Cannot read Foundry agent {name}: HTTP {response.status_code}")
        previous = response.json().get("versions", {}).get("latest", {}) if response.ok else {}
        if previous.get("definition") != definition:
            request("POST", url + "/versions?api-version=v1", agent_headers, json={"definition": definition})
        live = request("GET", url + "?api-version=v1", agent_headers).json()["versions"]["latest"]
        if any(live["definition"].get(key) != value for key, value in definition.items()):
            raise deploy.DeployError(f"Foundry definition readback failed for {name}.")
        print(f"Verified persistent Foundry agent {name}:{live['version']}", flush=True)

    hosted = request("GET", f"{endpoint}/agents/hydro-orchestrator?api-version=v1", agent_headers).json()
    try:
        invocations_url = hosted_invocations_url(hosted, endpoint)
        runtime_version, runtime_image_digest = hosted_runtime_identity(hosted)
        verify_hosted_source(hosted, tenant, workspace, ontology, source_digest)
    except RuntimeError as error:
        raise deploy.DeployError(str(error)) from error
    env_path = deploy.RAYFIN_DIR / ".env"
    env_path.write_text(deploy._rebind_public_env(env_path.read_text(encoding="utf-8"), {
        "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": model,
        "RAYFIN_PUBLIC_FOUNDRY_APP_INSIGHTS_RESOURCE_ID": insights_id,
        "RAYFIN_PUBLIC_FOUNDRY_INVOCATIONS_URL": invocations_url,
        "RAYFIN_PUBLIC_ORCHESTRATOR_VERSION": runtime_version,
        "RAYFIN_PUBLIC_ORCHESTRATOR_IMAGE_DIGEST": runtime_image_digest,
        "RAYFIN_PUBLIC_ORCHESTRATOR_SOURCE_DIGEST": source_digest,
    }), encoding="utf-8")
    print("Foundry configuration readback verified; agent runtime acceptance is a separate check.", flush=True)
