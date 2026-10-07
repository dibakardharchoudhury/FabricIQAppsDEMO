"""Provision persistent Hydro agents from the app's versioned source definitions.

Called by deploy_fabric_app.py, never a separate app deployment path.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from urllib.parse import urlparse

import requests


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
        response = requests.request(method, url, headers=headers, timeout=180, **kwargs)
        if not response.ok:
            raise deploy.DeployError(f"Foundry provisioning failed: {method} {url}: HTTP {response.status_code}: {response.text[:800]}")
        return response

    model = os.environ.get("HYDRO_FOUNDRY_MODEL", "").strip() or "gpt-5-mini"
    account_id = project_id.rsplit("/projects/", 1)[0]
    model_data = request("GET", f"https://management.azure.com{account_id}/deployments/{model}?api-version=2025-06-01", arm_headers).json()
    if model_data.get("properties", {}).get("provisioningState") != "Succeeded":
        raise deploy.DeployError("The selected Foundry model deployment is not ready.")

    binding = json.loads(deploy._public_config_value(values, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"))
    ontology = binding["ontologyId"]
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
        response = requests.get(url + "?api-version=v1", headers=agent_headers, timeout=60)
        if response.status_code not in (200, 404):
            raise deploy.DeployError(f"Cannot read Foundry agent {name}: HTTP {response.status_code}")
        previous = response.json().get("versions", {}).get("latest", {}) if response.ok else {}
        if previous.get("definition") != definition:
            request("POST", url + "/versions?api-version=v1", agent_headers, json={"definition": definition})
        live = request("GET", url + "?api-version=v1", agent_headers).json()["versions"]["latest"]
        if any(live["definition"].get(key) != value for key, value in definition.items()):
            raise deploy.DeployError(f"Foundry definition readback failed for {name}.")
        print(f"Verified persistent Foundry agent {name}:{live['version']}", flush=True)

    env_path = deploy.RAYFIN_DIR / ".env"
    env_path.write_text(deploy._rebind_public_env(env_path.read_text(encoding="utf-8"), {
        "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": model,
    }), encoding="utf-8")
    print("Foundry configuration readback verified; agent runtime acceptance is a separate check.", flush=True)
