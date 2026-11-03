"""Project-only provisioning: account, model allocation and infrastructure remain shared."""
from __future__ import annotations

import hashlib
from datetime import datetime
import json
import math
import os
import re
import time
from urllib.parse import quote

ACCOUNT_RE = re.compile(
    r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/([^/?#]+)/providers/"
    r"Microsoft\.CognitiveServices/accounts/([A-Za-z0-9-]+)", re.IGNORECASE,
)
ACR_PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
OPENAI_USER = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"


def infrastructure_connection_name(project_id: str, kind: str) -> str:
    account, separator, project = project_id.casefold().rpartition("/projects/")
    if (not separator or not ACCOUNT_RE.fullmatch(account)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", project)
            or kind not in ("registry", "monitoring")):
        raise ValueError("Infrastructure connection names require a verified project ARM identity and kind.")
    suffix = hashlib.sha256(project_id.casefold().encode()).hexdigest()[:32]
    return f"hydro-shared-{kind}-{suffix}"


def owned_infrastructure_connections(deploy, project_id: str, connections: list[dict]) -> list[dict]:
    infrastructure = [item for item in connections
                      if item.get("properties", {}).get("category") in ("ContainerRegistry", "AppInsights")]
    if any(not isinstance(item.get("id"), str) or not item["id"] for item in infrastructure):
        raise deploy.DeployError("Project infrastructure connection ownership identity is missing.")
    return [item for item in infrastructure if re.fullmatch(
        re.escape(project_id) + r"/connections/[^/?#]+", item["id"], re.IGNORECASE,
    )]


def _headers(deploy, tenant, resource="https://management.azure.com/"):
    token = deploy.run_capture(deploy.az(
        "account", "get-access-token", "--tenant", tenant, "--resource", resource,
        "--query", "accessToken", "-o", "tsv",
    ))
    if not token:
        raise deploy.DeployError("No delegated Azure management token was returned.")
    return {"Authorization": f"Bearer {token}"}


def _request(deploy, method, path, headers, *, api="2025-06-01", **kwargs):
    from provision_foundry_agents import request_with_read_retry

    response = request_with_read_retry(
        method, f"https://management.azure.com{path}?api-version={api}",
        headers=headers, timeout=180, **kwargs,
    )
    if not response.ok:
        raise deploy.DeployError(
            f"Shared Foundry {method} {path} (API {api}) failed: "
            f"HTTP {response.status_code}: {response.text[:800]}"
        )
    return response.json() if response.content else {}


def _model_fingerprint(deploy, settings, headers):
    model = _request(
        deploy, "GET", settings["account_id"] + "/deployments/" + quote(settings["model"], safe=""), headers,
    )
    return hashlib.sha256(json.dumps({
        "model": model.get("properties", {}).get("model"),
        "rateLimits": model.get("properties", {}).get("rateLimits"),
        "sku": model.get("sku"),
    }, sort_keys=True).encode()).hexdigest()


def verify_project_lifecycle(
    deploy, *, tenant, project_id, endpoint, project=None, first_page=None,
    require_empty=False, agent_headers=None,
):
    """Reject retained agents; this does not certify service-side ownership or execution."""
    from provision_foundry_agents import request_with_read_retry

    account, separator, name = project_id.rpartition("/projects/")
    if not separator or not ACCOUNT_RE.fullmatch(account) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise deploy.DeployError("Foundry project lifecycle requires a verified project ARM identity.")
    headers = _headers(deploy, tenant)
    if project is None:
        project = _request(deploy, "GET", project_id, headers)
    if (not isinstance(project, dict) or str(project.get("id", "")).casefold() != project_id.casefold()
            or project.get("properties", {}).get("provisioningState") != "Succeeded"
            or project.get("properties", {}).get("endpoints", {}).get("AI Foundry API", "").rstrip("/")
            != endpoint.rstrip("/")):
        raise deploy.DeployError("Foundry project lifecycle ARM identity/state/endpoint does not match.")
    incarnation = project.get("properties", {}).get("internalId")
    created_at = project.get("systemData", {}).get("createdAt")
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError) as error:
        raise deploy.DeployError("Foundry project lifecycle creation timestamp is missing or invalid.") from error
    if not isinstance(incarnation, str) or not incarnation or created.utcoffset() is None:
        raise deploy.DeployError("Foundry project lifecycle incarnation/timestamp is unverifiable.")
    if agent_headers is None:
        agent_headers = _headers(deploy, tenant, "https://ai.azure.com")
    page = first_page
    cursor = ""
    seen_cursors = set()
    seen_names = set()
    while True:
        if page is None:
            response = request_with_read_retry(
                "GET", endpoint.rstrip("/") + "/agents?api-version=v1&limit=100"
                + ("&after=" + quote(cursor, safe="") if cursor else ""),
                headers=agent_headers, timeout=60,
            )
            if not response.ok:
                raise deploy.DeployError(f"Foundry project lifecycle inventory failed: HTTP {response.status_code}.")
            page = response.json()
        if (not isinstance(page, dict) or not isinstance(page.get("data"), list)
                or type(page.get("has_more")) is not bool):
            raise deploy.DeployError("Foundry project lifecycle inventory is malformed or incomplete.")
        for agent in page["data"]:
            name = agent.get("name") if isinstance(agent, dict) else None
            versions = agent.get("versions") if isinstance(agent, dict) else None
            latest = versions.get("latest") if isinstance(versions, dict) else None
            timestamp = latest.get("created_at") if isinstance(latest, dict) else None
            if (not isinstance(name, str) or not name or name in seen_names
                    or not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
                    or not math.isfinite(timestamp) or timestamp <= 0):
                raise deploy.DeployError("Foundry project lifecycle agent identity/timestamp is unverifiable.")
            seen_names.add(name)
            # Agent timestamps have second precision; ARM creation timestamps have fractional seconds.
            if require_empty or timestamp < math.floor(created.timestamp()):
                raise deploy.DeployError(
                    f"Foundry project lifecycle mismatch: agent '{name}' is retained from before project "
                    f"creation or appeared in a newly created project (createdAt={created_at}, "
                    f"internalId={incarnation}). Refusing to reuse or update retained agents. "
                    "Stop concurrent deployments and explicitly review/clean up retained agents before "
                    "project recreation; if the identity conflict persists, contact Azure support. "
                    "No agents were deleted by this check."
                )
        if not page["has_more"]:
            break
        cursor = page.get("last_id")
        if (not page["data"] or not isinstance(cursor, str) or not cursor
                or cursor in seen_cursors or len(seen_cursors) >= 1000):
            raise deploy.DeployError("Foundry project lifecycle inventory pagination is incomplete or cyclic.")
        seen_cursors.add(cursor)
        page = None
    readback = _request(deploy, "GET", project_id, headers)
    if (readback.get("properties", {}).get("internalId") != incarnation
            or readback.get("systemData", {}).get("createdAt") != created_at
            or str(readback.get("id", "")).casefold() != project_id.casefold()
            or readback.get("properties", {}).get("provisioningState") != "Succeeded"
            or readback.get("properties", {}).get("endpoints", {}).get("AI Foundry API", "").rstrip("/")
            != endpoint.rstrip("/")):
        raise deploy.DeployError("Foundry project lifecycle changed during inventory verification; stop deployment.")


def _monitoring_connection_string(deploy, resource_id, headers):
    monitor = _request(deploy, "GET", resource_id, headers, api="2020-02-02")
    if str(monitor.get("id", "")).casefold() != resource_id.casefold():
        raise deploy.DeployError("Shared Application Insights resource identity/readback does not match.")
    connection_string = monitor.get("properties", {}).get("ConnectionString")
    if (not isinstance(connection_string, str)
            or not re.search(r"(?:^|;)InstrumentationKey=[0-9a-f-]{36}(?:;|$)", connection_string, re.IGNORECASE)
            or not re.search(r"(?:^|;)IngestionEndpoint=https://[^;\s]+(?:;|$)", connection_string, re.IGNORECASE)):
        raise deploy.DeployError(
            "Shared Application Insights requires a full ConnectionString from ARM readback; "
            "an instrumentation key alone is not supported. Verify monitoring before retrying."
        )
    return connection_string


def _deployed_infrastructure(deploy, account_id: str, missing: set[str]) -> list[dict]:
    identity = ACCOUNT_RE.fullmatch(account_id)
    if not identity:
        raise deploy.DeployError("Infrastructure recovery requires a verified Foundry account ARM identity.")
    scope = account_id.rsplit("/providers/", 1)[0]
    deployments = json.loads(deploy.run_capture(deploy.az(
        "deployment", "group", "list", "--subscription", identity[1], "--resource-group", identity[2],
        "--query", "[?properties.provisioningState=='Succeeded']."
        "{id:id,state:properties.provisioningState,resources:properties.outputResources}", "-o", "json",
    )))
    if not isinstance(deployments, list) or any(not isinstance(item, dict) for item in deployments):
        raise deploy.DeployError("Shared infrastructure deployment inventory is invalid.")
    recovered = {}
    types = {"ContainerRegistry": "Microsoft.ContainerRegistry/registries",
             "AppInsights": "Microsoft.Insights/components"}
    for deployment in deployments:
        if (deployment.get("state") != "Succeeded" or not re.fullmatch(
                re.escape(scope) + r"/providers/Microsoft\.Resources/deployments/[^/?#]+",
                str(deployment.get("id", "")), re.IGNORECASE,
        )):
            continue
        resources = deployment.get("resources")
        if resources is None:
            continue
        if not isinstance(resources, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in resources
        ):
            raise deploy.DeployError("Shared infrastructure deployment resource inventory is invalid.")
        if not any(item["id"].casefold() == account_id.casefold() for item in resources):
            continue
        for item in resources:
            resource_id = item["id"]
            for kind in missing:
                if re.fullmatch(
                    rf"/subscriptions/{re.escape(identity[1])}/resourceGroups/[^/?#]+/providers/"
                    + re.escape(types[kind]) + r"/[^/?#]+", resource_id, re.IGNORECASE,
                ):
                    target = (resource_id.rsplit("/", 1)[-1] + ".azurecr.io"
                              if kind == "ContainerRegistry" else resource_id)
                    recovered.setdefault((kind, resource_id.casefold()), {
                        "properties": {"category": kind, "target": target, "metadata": {"ResourceId": resource_id}},
                    })
    if recovered:
        print("Foundry: recovered shared infrastructure candidates from successful ARM deployment resource IDs; "
              "verifying live registry and monitoring readback.", flush=True)
    return list(recovered.values())


def prepare_shared_project(deploy, *, tenant, subscription, account_name, project_name, model, require_new=False):
    from deploy_hosted_agent import registry_name
    from provision_foundry_agents import read_model_selection, read_project_connections, linked_application_insights

    accounts = json.loads(deploy.run_capture(deploy.az(
        "resource", "list", "--subscription", subscription, "--resource-type",
        "Microsoft.CognitiveServices/accounts", "--name", account_name, "-o", "json",
    )))
    matches = [item for item in accounts if (identity := ACCOUNT_RE.fullmatch(item.get("id", "")))
               and identity[1].casefold() == subscription.casefold() and identity[3].casefold() == account_name.casefold()]
    if not matches:
        print(f"Foundry: account '{account_name}' does not exist; approved account/model bootstrap is required.", flush=True)
        return None
    if len(matches) != 1:
        raise deploy.DeployError("The selected Foundry account name is ambiguous in the selected subscription.")
    account_id = matches[0]["id"]
    headers = _headers(deploy, tenant)
    account = _request(deploy, "GET", account_id, headers)
    if (str(account.get("id", "")).casefold() != account_id.casefold()
            or account.get("kind") != "AIServices"
            or account.get("properties", {}).get("provisioningState") != "Succeeded"
            or not account.get("location")):
        raise deploy.DeployError("The selected account must be a verified ready Foundry AIServices resource.")
    domain = account.get("properties", {}).get("customSubDomainName", "")
    if not re.fullmatch(r"[A-Za-z0-9-]+", domain):
        raise deploy.DeployError("The selected account has no verified custom subdomain.")
    from deploy_foundry_project import normalize_location
    requested_location = os.environ.get("HYDRO_FOUNDRY_LOCATION", "").strip()
    if requested_location and normalize_location(requested_location) != normalize_location(account["location"]):
        raise deploy.DeployError("Project-only creation must use the existing account's region; no region was substituted.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", project_name):
        raise deploy.DeployError("Project-only creation requires a valid project name.")
    project_id = account_id + "/projects/" + project_name
    if require_new:
        _require_missing_project(deploy, project_id, headers)
    model = read_model_selection(deploy, tenant, project_id, model)
    connections = read_project_connections(deploy, account_id, headers)
    projects = json.loads(deploy.run_capture(deploy.az(
        "resource", "list", "--subscription", subscription, "--resource-type",
        "Microsoft.CognitiveServices/accounts/projects", "-o", "json",
    )))
    for item in projects:
        identity = str(item.get("id", ""))
        if identity.rsplit("/projects/", 1)[0].casefold() == account_id.casefold():
            connections.extend(read_project_connections(deploy, identity, headers))
    infrastructure = [item for item in connections
                      if item.get("properties", {}).get("category") in ("ContainerRegistry", "AppInsights")]
    missing = {"ContainerRegistry", "AppInsights"} - {
        item["properties"]["category"] for item in infrastructure
    }
    if missing:
        infrastructure.extend(_deployed_infrastructure(deploy, account_id, missing))
    try:
        registry = registry_name(infrastructure, os.environ.get("HYDRO_ACR_NAME", ""))
        insights_id = linked_application_insights(infrastructure)
    except RuntimeError as error:
        raise deploy.DeployError(
            f"Shared account infrastructure is incomplete or ambiguous: {str(error).rstrip('.')}. "
            "Verify its connected registry/monitoring or a successful ARM deployment recording this exact account "
            "and its supporting resource IDs before project-only creation; no model was allocated."
        ) from error
    registry_detail = json.loads(deploy.run_capture(deploy.az(
        "acr", "show", "--name", registry, "--subscription", subscription, "-o", "json",
    )))
    registry_id = str(registry_detail.get("id", ""))
    registry_host = str(registry_detail.get("loginServer", ""))
    if (not re.fullmatch(
            rf"/subscriptions/{re.escape(subscription)}/resourceGroups/[^/?#]+/providers/"
            rf"Microsoft\.ContainerRegistry/registries/{re.escape(registry)}", registry_id, re.IGNORECASE,
        )
            or registry_host != registry + ".azurecr.io"):
        raise deploy.DeployError("Shared registry resource identity/readback does not match its connection.")
    if not insights_id.lower().startswith(f"/subscriptions/{subscription.lower()}/"):
        raise deploy.DeployError("Shared monitoring must belong to the selected subscription.")
    _monitoring_connection_string(deploy, insights_id, headers)
    settings = {
        "account_id": account_id, "project_name": project_name, "model": model,
        "location": account["location"], "registry_id": registry_id, "registry_host": registry_host,
        "insights_id": insights_id,
        "account_host": domain.lower() + ".services.ai.azure.com",
    }
    settings["model_fingerprint"] = _model_fingerprint(deploy, settings, headers)
    print(f"Foundry: project-only plan for '{project_name}'; shared model '{model}', no quota allocation.", flush=True)
    return settings


def _assign_role(deploy, scope, principal, role, principal_type):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", principal):
        raise deploy.DeployError("A verified principal GUID is required for project access.")
    existing = json.loads(deploy.run_capture(deploy.az(
        "role", "assignment", "list", "--scope", scope, "--query",
        f"[?principalId=='{principal}' && ends_with(roleDefinitionId, '{role}')].id", "-o", "json",
    )))
    if not existing:
        deploy.run_capture(deploy.az(
            "role", "assignment", "create", "--assignee-object-id", principal,
            "--assignee-principal-type", principal_type, "--role", role, "--scope", scope, "-o", "json",
        ))


def _require_missing_project(deploy, project_id, headers):
    from provision_foundry_agents import request_with_read_retry

    response = request_with_read_retry(
        "GET", f"https://management.azure.com{project_id}?api-version=2025-06-01", headers=headers, timeout=60,
    )
    if response.ok:
        raise deploy.DeployError(
            "Create a fresh Foundry project was selected, but the requested project already exists. "
            "Uncheck 'Allow project creation' to reuse/update its agents, or enter an unused project name. "
            "No existing project or agents were deleted or reused."
        )
    if response.status_code != 404:
        raise deploy.DeployError(f"Cannot verify the fresh Foundry project is absent: HTTP {response.status_code}.")
    return response


def ensure_shared_project(deploy, *, tenant, settings, sleep=time.sleep, require_new=False):
    from provision_foundry_agents import request_with_read_retry, definition_matches, read_project_connections

    headers = _headers(deploy, tenant)
    if _model_fingerprint(deploy, settings, headers) != settings["model_fingerprint"]:
        raise deploy.DeployError("The shared model deployment changed after preflight; no project was created.")
    connection_string = _monitoring_connection_string(deploy, settings["insights_id"], headers)
    project_id = settings["account_id"] + "/projects/" + settings["project_name"]
    url = f"https://management.azure.com{project_id}?api-version=2025-06-01"
    response = (_require_missing_project(deploy, project_id, headers) if require_new
                else request_with_read_retry("GET", url, headers=headers, timeout=60))
    newly_created = response.status_code == 404
    if newly_created:
        print(f"Foundry: creating only project '{settings['project_name']}' in the existing account.", flush=True)
        _request(deploy, "PUT", project_id, headers, json={
            "location": settings["location"], "identity": {"type": "SystemAssigned"}, "properties": {},
        })
    elif not response.ok:
        raise deploy.DeployError(f"Cannot read the selected project: HTTP {response.status_code}.")
    deadline = time.monotonic() + 600
    while True:
        project = _request(deploy, "GET", project_id, headers)
        if str(project.get("id", "")).casefold() != project_id.casefold():
            raise deploy.DeployError("Project-only creation returned a different project identity.")
        state = project.get("properties", {}).get("provisioningState")
        if state == "Succeeded":
            break
        if state in ("Failed", "Canceled", "Cancelled") or time.monotonic() >= deadline:
            raise deploy.DeployError(f"Project-only provisioning did not succeed: {state}.")
        sleep(15)
    endpoint = project.get("properties", {}).get("endpoints", {}).get("AI Foundry API", "")
    if endpoint.rstrip("/").casefold() != f"https://{settings['account_host']}/api/projects/{settings['project_name']}".casefold():
        raise deploy.DeployError("Project endpoint does not match the verified account/project identity.")
    principal = str(project.get("identity", {}).get("principalId", ""))
    _assign_role(deploy, settings["registry_id"], principal, ACR_PULL, "ServicePrincipal")
    _assign_role(deploy, settings["account_id"], principal, OPENAI_USER, "ServicePrincipal")
    _assign_role(deploy, project_id, principal, FOUNDRY_USER, "ServicePrincipal")
    operator = deploy.run_capture(deploy.az("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"))
    _assign_role(deploy, project_id, operator, FOUNDRY_USER, "User")
    agent_headers = _headers(deploy, tenant, "https://ai.azure.com")
    deadline = time.monotonic() + 600
    while True:
        response = request_with_read_retry(
            "GET", endpoint.rstrip("/") + "/agents?api-version=v1", headers=agent_headers, timeout=60,
        )
        if response.ok:
            verify_project_lifecycle(
                deploy, tenant=tenant, project_id=project_id, endpoint=endpoint, project=project,
                first_page=response.json(), require_empty=newly_created, agent_headers=agent_headers,
            )
            break
        if response.status_code not in (401, 403, 404) or time.monotonic() >= deadline:
            raise deploy.DeployError(f"Project agent access did not become ready: HTTP {response.status_code}.")
        print(f"Foundry: waiting for project endpoint/scoped access propagation (HTTP {response.status_code}).", flush=True)
        sleep(15)
    connected = owned_infrastructure_connections(
        deploy, project_id, read_project_connections(deploy, project_id, headers),
    )
    for name, properties in (
        (infrastructure_connection_name(project_id, "registry"), {
            "category": "ContainerRegistry", "authType": "ManagedIdentity",
            # ContainerRegistry uses the project principal as clientId, matching the azd template.
            "credentials": {"clientId": principal, "resourceId": settings["registry_id"]},
            "target": settings["registry_host"], "isSharedToAll": False,
            "metadata": {"ResourceId": settings["registry_id"]},
        }),
        (infrastructure_connection_name(project_id, "monitoring"), {
            "category": "AppInsights", "authType": "ApiKey", "target": settings["insights_id"],
            "credentials": {"key": connection_string}, "isSharedToAll": False,
            "metadata": {"ApiType": "Azure", "ResourceId": settings["insights_id"]},
        }),
    ):
        matching = [item for item in connected if item.get("properties", {}).get("category") == properties["category"]
                    and item.get("properties", {}).get("target") == properties["target"]]
        if len(matching) > 1:
            raise deploy.DeployError("Project infrastructure has duplicate live connections.")
        if matching:
            compatible = {field: properties[field] for field in ("category", "authType", "target")}
            actual = matching[0].get("properties", {})
            if (not definition_matches(actual, compatible)
                    or not definition_matches(actual.get("metadata", {}), properties["metadata"])):
                raise deploy.DeployError(
                    f"Existing {properties['category']} connection authentication/resource identity "
                    "does not match the shared infrastructure; refusing replacement."
                )
            continue
        path = project_id + "/connections/" + name
        current = request_with_read_retry(
            "GET", f"https://management.azure.com{path}?api-version=2025-10-01-preview",
            headers=headers, timeout=60,
        )
        expected = {field: value for field, value in properties.items() if field != "credentials"}
        if current.status_code != 404 and not current.ok:
            raise deploy.DeployError(f"Cannot read shared infrastructure connection: HTTP {current.status_code}.")
        if not current.ok:
            _request(deploy, "PUT", path, headers, api="2025-10-01-preview", json={"properties": properties})
        readback = _request(deploy, "GET", path, headers, api="2025-10-01-preview")
        if (str(readback.get("id", "")).casefold() != path.casefold()
                or not definition_matches(readback.get("properties", {}), expected)):
            raise deploy.DeployError("Project infrastructure connection readback does not match; refusing replacement.")
    if _model_fingerprint(deploy, settings, headers) != settings["model_fingerprint"]:
        raise deploy.DeployError("The shared model allocation changed during project-only setup.")
    print("Foundry: project and infrastructure verified; shared model allocation unchanged.", flush=True)
