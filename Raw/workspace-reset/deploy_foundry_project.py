"""Create the Foundry project (account, project, model deployment, ACR, Application Insights) on request.

Called by provision_foundry_agents.provision() only when no Foundry project can be found AND the operator explicitly
opted in (HYDRO_FOUNDRY_CREATE=true, or the local app's checkbox), because it creates billable Azure resources. It
is not a separate deployment path: it provisions the project the rest of the deployer then discovers and uses.

It runs the same Azure Developer CLI template Microsoft documents for new Foundry projects
(Azure-Samples/azd-ai-starter-basic) from a persistent per-target folder, so a rerun reuses the same azd
environment and resource names instead of creating duplicates.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

TEMPLATE = "https://github.com/Azure-Samples/azd-ai-starter-basic"
# The values of the verified working deployment; each can be overridden for another tenant, region or quota.
DEFAULT_MODEL_VERSION = "2026-07-09"
DEFAULT_MODEL_SKU = "GlobalStandard"
DEFAULT_MODEL_CAPACITY = 500
ACCESS_TIMEOUT_S = 600
ACCESS_POLL_S = 15


def enabled() -> bool:
    return os.environ.get("HYDRO_FOUNDRY_CREATE", "").strip().lower() == "true"


def normalize_location(value: str) -> str:
    """'Sweden Central' (Fabric) -> 'swedencentral' (Azure)."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def environment_name(tenant: str, workspace: str) -> str:
    """Stable per target, so reruns reuse one azd environment (and its resource-name salt)."""
    override = os.environ.get("HYDRO_FOUNDRY_PROJECT_NAME", "").strip()
    if override:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,30}", override):
            raise ValueError("HYDRO_FOUNDRY_PROJECT_NAME must be 1-31 letters, digits or hyphens.")
        return override
    return "hydro-foundry-" + hashlib.sha256(f"{tenant.lower()}|{workspace.lower()}".encode()).hexdigest()[:6]


def deployments_parameter(model: str) -> str:
    """AI_PROJECT_DEPLOYMENTS for the template.

    The template wraps this value in quotes inside its parameters file, so the JSON must arrive with its own quotes
    escaped; unescaped JSON fails with "invalid character 'n' after object key:value pair"."""
    try:
        capacity = int(os.environ.get("HYDRO_FOUNDRY_MODEL_CAPACITY", "") or DEFAULT_MODEL_CAPACITY)
    except ValueError as error:
        raise ValueError("HYDRO_FOUNDRY_MODEL_CAPACITY must be a whole number.") from error
    deployments = [{
        "name": model,
        "model": {"name": model, "format": "OpenAI",
                  "version": os.environ.get("HYDRO_FOUNDRY_MODEL_VERSION", "").strip() or DEFAULT_MODEL_VERSION},
        "sku": {"name": os.environ.get("HYDRO_FOUNDRY_MODEL_SKU", "").strip() or DEFAULT_MODEL_SKU,
                "capacity": capacity},
    }]
    return json.dumps(json.dumps(deployments, separators=(",", ":")))[1:-1]


def ensure_foundry_project(
    deploy, hosted, *, tenant: str, workspace: str, location: str, model: str, check_access, sleep=time.sleep,
) -> str:
    """Provision a Foundry project and return its endpoint once the signed-in user can create agents in it."""
    hosted.ensure_azd_ready(deploy, tenant)
    subscription = deploy.run_capture(deploy.az("account", "show", "--query", "id", "-o", "tsv"))
    name = environment_name(tenant, workspace)
    directory = hosted.state_root() / "foundry-project" / name
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "azure.yaml").exists():
        print(f"Foundry: initializing the project template in {directory}.", flush=True)
        deploy.run_stream(deploy.command_argv(
            "azd", "init", "-t", TEMPLATE, "-e", name, "--no-prompt",
        ), cwd=directory)
    settings = {
        "AZURE_SUBSCRIPTION_ID": subscription, "AZURE_LOCATION": location, "AZURE_AI_DEPLOYMENTS_LOCATION": location,
        "ENABLE_HOSTED_AGENTS": "true", "ENABLE_CAPABILITY_HOST": "false", "ENABLE_MONITORING": "true",
        "AI_PROJECT_DEPLOYMENTS": deployments_parameter(model),
    }
    for key, value in settings.items():
        deploy.run_capture(
            deploy.command_argv("azd", "env", "set", key, value, "--environment", name), cwd=directory,
        )
    print(
        f"Foundry: provisioning environment {name} in {location} (Foundry account, project, model {model}, "
        "container registry, Application Insights). This takes about 5-10 minutes.", flush=True,
    )
    deploy.run_stream(deploy.command_argv(
        "azd", "provision", "--environment", name, "--no-prompt",
    ), cwd=directory)
    values = hosted.parse_azd_values(deploy.run_capture(
        deploy.command_argv("azd", "env", "get-values", "--environment", name), cwd=directory,
    ))
    endpoint = values.get("AZURE_AI_PROJECT_ENDPOINT", "").rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".services.ai.azure.com"):
        raise deploy.DeployError("Provisioning finished but reported no valid Foundry project endpoint.")

    # Role assignments from the template can take a few minutes to take effect for the signed-in user.
    deadline = time.monotonic() + ACCESS_TIMEOUT_S
    while not check_access(endpoint):
        if time.monotonic() > deadline:
            raise deploy.DeployError(
                f"The new Foundry project {endpoint} is not yet usable by the signed-in user after "
                f"{ACCESS_TIMEOUT_S // 60} minutes (role assignments may still be propagating). Rerun the deployment."
            )
        sleep(ACCESS_POLL_S)
    print(f"Foundry: project ready at {endpoint}.", flush=True)
    return endpoint
