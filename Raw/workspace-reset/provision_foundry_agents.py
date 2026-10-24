"""Provision persistent Hydro agents from the app's versioned source definitions.

Called by deploy_fabric_app.py, never a separate app deployment path. The Hosted Agent is converged by
deploy_hosted_agent.py immediately before its final verification below.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import time
from pathlib import Path
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
    if collection not in ("dataAgents", "ontologies"):
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
    if native_binding != {"source": expected, "connections": {"data-agent": "hydro-fabric-iq"}}:
        raise RuntimeError("The active hosted runtime must retain only the verified Data Agent native binding.")


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_subscription(deploy, tenant: str) -> str:
    selected = os.environ.get("HYDRO_AZURE_SUBSCRIPTION_ID", "").strip()
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
        raise RuntimeError(
            "Select one ready Foundry chat model deployment with HYDRO_FOUNDRY_MODEL. "
            "Automatic selection requires exactly one deployment advertising chatCompletion capability."
        )
    return matches[0]["name"]


def provision(deploy, tenant: str, workspace: str) -> None:
    values, _ = deploy.current_rayfin_target()
    print("Foundry: project - resolving the Foundry project.", flush=True)
    subscription = resolve_subscription(deploy, tenant)
    same_target = (
        deploy._public_config_value(values, "RAYFIN_PUBLIC_TENANT_ID").casefold() == tenant.casefold()
        and deploy._public_config_value(values, "RAYFIN_PUBLIC_WORKSPACE_ID").casefold() == workspace.casefold()
        and deploy._public_config_value(values, "RAYFIN_PUBLIC_FOUNDRY_SUBSCRIPTION_ID").casefold() in ("", subscription.casefold())
    )
    saved_endpoint = deploy._public_config_value(values, "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT") if same_target else ""
    explicit_endpoint = os.environ.get("HYDRO_FOUNDRY_PROJECT_ENDPOINT", "").strip()
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
        for project in projects:
            detail = json.loads(deploy.run_capture(deploy.az(
                "resource", "show", "--ids", project["id"], "--api-version", "2025-06-01", "-o", "json",
            )))
            url = detail.get("properties", {}).get("endpoints", {}).get("AI Foundry API", "").rstrip("/")
            if url and (not wanted or url == wanted.rstrip("/")):
                found.append((detail["id"], url))
        return found

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
    if project_module.enabled() and not endpoint:
        candidates = []
    if not candidates and project_module.enabled():
        hosted_module = _load_sibling("deploy_hosted_agent")

        def target_location() -> str:
            override = os.environ.get("HYDRO_FOUNDRY_LOCATION", "").strip()
            if override:
                return project_module.normalize_location(override)
            headers = deploy.fabric_headers(tenant)
            capacity_id = str(deploy.fabric_get(f"workspaces/{workspace}", headers).get("capacityId") or "").lower()
            response = request_with_read_retry("GET", f"{deploy.FABRIC_BASE}/capacities", headers=headers, timeout=60)
            region = next((
                str(item.get("region") or "") for item in (response.json().get("value", []) if response.ok else [])
                if str(item.get("id") or "").lower() == capacity_id
            ), "")
            if not region:
                raise deploy.DeployError(
                    "Set HYDRO_FOUNDRY_LOCATION to the Azure region for the new Foundry project; the Fabric "
                    "capacity region could not be read."
                )
            return project_module.normalize_location(region)

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
        created = project_module.ensure_foundry_project(
            deploy, hosted_module, tenant=tenant, workspace=workspace, subscription=subscription,
            location=target_location(), model=model, check_access=agent_access,
        )
        candidates = discover(created)
    if len(candidates) != 1:
        raise deploy.DeployError(
            "Select exactly one existing Foundry project with HYDRO_FOUNDRY_PROJECT_ENDPOINT; automatic discovery is ambiguous or unavailable."
            + ("" if candidates else " To create one instead, enable 'Create/reuse a target-specific Foundry project' "
               "in the local app (HYDRO_FOUNDRY_CREATE=true).")
        )
    project_id, endpoint = candidates[0]
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".services.ai.azure.com"):
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

    account_id = project_id.rsplit("/projects/", 1)[0]
    models_url = f"https://management.azure.com{account_id}/deployments"
    model_page = request("GET", models_url + "?api-version=2025-06-01", arm_headers).json()
    deployments = model_page.get("value", [])
    while model_page.get("nextLink"):
        link = urlparse(model_page["nextLink"])
        if link.scheme != "https" or link.netloc != "management.azure.com" or link.path != f"{account_id}/deployments":
            raise deploy.DeployError("Unexpected Foundry model pagination URL.")
        model_page = request("GET", model_page["nextLink"], arm_headers).json()
        deployments.extend(model_page.get("value", []))
    try:
        model = select_model_deployment(deployments, model)
    except RuntimeError as error:
        raise deploy.DeployError(str(error)) from error
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
        deploy.APP_DIR / "scripts" / "export-agent-definitions.ts", model, iq_name,
    ), cwd=deploy.APP_DIR))
    print("Foundry: agents - provisioning the specialist prompt agents.", flush=True)
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
        "RAYFIN_PUBLIC_FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT": model,
        "RAYFIN_PUBLIC_FOUNDRY_APP_INSIGHTS_RESOURCE_ID": insights_id,
        "RAYFIN_PUBLIC_FOUNDRY_INVOCATIONS_URL": invocations_url,
        "RAYFIN_PUBLIC_ORCHESTRATOR_VERSION": runtime_version,
        "RAYFIN_PUBLIC_ORCHESTRATOR_IMAGE_DIGEST": runtime_image_digest,
        "RAYFIN_PUBLIC_ORCHESTRATOR_SOURCE_DIGEST": source_digest,
    }), encoding="utf-8")
    print("Foundry configuration readback verified; agent runtime acceptance is a separate check.", flush=True)
