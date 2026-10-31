"""Project-only provisioning: account, model allocation and infrastructure remain shared."""
from __future__ import annotations

import hashlib
import json
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
        raise deploy.DeployError(f"Shared Foundry project {method} failed: HTTP {response.status_code}: {response.text[:800]}")
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


def prepare_shared_project(deploy, *, tenant, subscription, account_name, project_name, model):
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
    try:
        registry = registry_name(infrastructure, os.environ.get("HYDRO_ACR_NAME", ""))
        insights_id = linked_application_insights(infrastructure)
    except RuntimeError as error:
        raise deploy.DeployError(
            f"Shared account infrastructure is incomplete or ambiguous: {error}. "
            "Prepare its connected registry and monitoring before project-only creation; no model was allocated."
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


def ensure_shared_project(deploy, *, tenant, settings, sleep=time.sleep):
    from provision_foundry_agents import request_with_read_retry, definition_matches, read_project_connections

    headers = _headers(deploy, tenant)
    if _model_fingerprint(deploy, settings, headers) != settings["model_fingerprint"]:
        raise deploy.DeployError("The shared model deployment changed after preflight; no project was created.")
    project_id = settings["account_id"] + "/projects/" + settings["project_name"]
    url = f"https://management.azure.com{project_id}?api-version=2025-06-01"
    response = request_with_read_retry("GET", url, headers=headers, timeout=60)
    if response.status_code == 404:
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
    monitor = _request(deploy, "GET", settings["insights_id"], headers, api="2020-02-02")
    key = monitor.get("properties", {}).get("InstrumentationKey")
    if not key:
        raise deploy.DeployError("Shared Application Insights readback has no instrumentation key.")
    connected = read_project_connections(deploy, project_id, headers)
    for name, properties in (
        ("hydro-shared-registry", {
            "category": "ContainerRegistry", "authType": "ManagedIdentity",
            "target": settings["registry_host"], "isSharedToAll": False,
            "metadata": {"ResourceId": settings["registry_id"]},
        }),
        ("hydro-shared-monitoring", {
            "category": "AppInsights", "authType": "ApiKey", "target": settings["insights_id"],
            "credentials": {"key": key}, "isSharedToAll": False,
            "metadata": {"ApiType": "AppInsights", "ResourceId": settings["insights_id"]},
        }),
    ):
        matching = [item for item in connected if item.get("properties", {}).get("category") == properties["category"]
                    and item.get("properties", {}).get("target") == properties["target"]]
        if len(matching) > 1:
            raise deploy.DeployError("Project infrastructure has duplicate live connections.")
        if matching:
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
        if not definition_matches(readback.get("properties", {}), expected):
            raise deploy.DeployError("Project infrastructure connection readback does not match; refusing replacement.")
    agent_headers = _headers(deploy, tenant, "https://ai.azure.com")
    deadline = time.monotonic() + 600
    while True:
        response = request_with_read_retry(
            "GET", endpoint.rstrip("/") + "/agents?api-version=v1", headers=agent_headers, timeout=60,
        )
        if response.ok:
            break
        if response.status_code not in (401, 403, 404) or time.monotonic() >= deadline:
            raise deploy.DeployError(f"Project agent access did not become ready: HTTP {response.status_code}.")
        print(f"Foundry: waiting for project endpoint/scoped access propagation (HTTP {response.status_code}).", flush=True)
        sleep(15)
    if _model_fingerprint(deploy, settings, headers) != settings["model_fingerprint"]:
        raise deploy.DeployError("The shared model allocation changed during project-only setup.")
    print("Foundry: project and infrastructure verified; shared model allocation unchanged.", flush=True)
