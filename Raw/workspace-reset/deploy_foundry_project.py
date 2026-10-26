"""Create the Foundry project (account, project, model deployment, ACR, Application Insights) on request.

Called by provision_foundry_agents.provision() only when no Foundry endpoint is selected AND the operator explicitly
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
ACCESS_TIMEOUT_S = 600
ACCESS_POLL_S = 15


def enabled() -> bool:
    return os.environ.get("HYDRO_FOUNDRY_CREATE", "").strip().lower() == "true"


def normalize_location(value: str) -> str:
    """'Sweden Central' (Fabric) -> 'swedencentral' (Azure)."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def environment_name(tenant: str, workspace: str, subscription: str = "") -> str:
    """Stable per target, so reruns reuse one azd environment (and its resource-name salt)."""
    override = os.environ.get("HYDRO_FOUNDRY_PROJECT_NAME", "").strip()
    if override:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,30}", override):
            raise ValueError("HYDRO_FOUNDRY_PROJECT_NAME must be 1-31 letters, digits or hyphens.")
        return override
    return "hydro-foundry-" + hashlib.sha256(
        f"{tenant.lower()}|{subscription.lower()}|{workspace.lower()}".encode()
    ).hexdigest()[:12]


def model_defaults(deploy, *, subscription: str, location: str, model: str) -> dict[str, str]:
    """Use live regional catalog defaults, never guessed versions or quota."""
    if not model:
        raise deploy.DeployError("Enter the model name for the new project. Resource names and model version are automatic.")
    catalog_name = os.environ.get("HYDRO_FOUNDRY_MODEL_NAME", "").strip() or model
    version = os.environ.get("HYDRO_FOUNDRY_MODEL_VERSION", "").strip()
    sku = os.environ.get("HYDRO_FOUNDRY_MODEL_SKU", "").strip()
    capacity = os.environ.get("HYDRO_FOUNDRY_MODEL_CAPACITY", "").strip()
    catalog = json.loads(deploy.run_capture(deploy.az(
        "cognitiveservices", "model", "list", "--subscription", subscription, "--location", location, "-o", "json",
    )))
    models = [entry["model"] for entry in catalog if isinstance(entry.get("model"), dict)
              and entry["model"].get("name") == catalog_name
              and entry["model"].get("format") == "OpenAI"
              and entry["model"].get("capabilities", {}).get("chatCompletion") in (True, "true")]
    models = [entry for entry in models if entry.get("version") == version] if version else [
        entry for entry in models if entry.get("isDefaultVersion") is True
    ]
    if len(models) != 1:
        raise deploy.DeployError(
            f"Model '{catalog_name}' has no unique supported chat version in {location}. "
            "Choose an available model, or set its version under 'Model deployment settings'."
        )
    selected = models[0]
    skus = [entry for entry in selected.get("skus", [])
            if "finetune" not in str(entry.get("usageName", "")).lower()]
    if sku:
        skus = [entry for entry in skus if entry.get("name") == sku]
    else:
        # Never silently move inference across regions or reserve provisioned capacity.
        skus = [entry for entry in skus if entry.get("name") == "Standard"]
    if len(skus) != 1:
        choices = ", ".join(str(entry.get("name")) for entry in selected.get("skus", []))
        raise deploy.DeployError(
            f"Model '{catalog_name}' requires a deployment type choice in {location}. "
            f"Under 'Model deployment settings', choose an approved SKU. Available: {choices}. "
            "Global, data-zone and provisioned deployments are never selected automatically."
        )
    limits = skus[0].get("capacity") or {}
    minimum = limits.get("minimum")
    if not capacity:
        allowed = limits.get("allowedValues")
        if isinstance(allowed, list) and allowed:
            capacity = str(min(allowed))
        elif isinstance(minimum, int) and not isinstance(minimum, bool) and minimum > 0:
            capacity = str(minimum)
        elif type(limits.get("default")) is int and limits["default"] > 0:
            capacity = str(limits["default"])
        else:
            raise deploy.DeployError(
                "The model catalog did not report a capacity default. Enter an approved capacity "
                "under 'Model deployment settings'; quota is verified by Azure during provisioning."
            )
    if not re.fullmatch(r"[1-9][0-9]*", capacity):
        raise deploy.DeployError("Model capacity must be a positive whole number.")
    units = int(capacity)
    allowed = limits.get("allowedValues")
    maximum = limits.get("maximum")
    step = limits.get("step")
    if (
        (isinstance(allowed, list) and allowed and units not in allowed)
        or (type(minimum) is int and units < minimum)
        or (type(maximum) is int and units > maximum)
        or (type(step) is int and step > 0 and (units - (minimum or 0)) % step != 0)
    ):
        raise deploy.DeployError("Model capacity is outside the selected deployment type's catalog limits.")
    print(
        f"Foundry: new model settings from the {location} catalog: {catalog_name}, version "
        f"{selected['version']}, SKU {skus[0]['name']}, capacity {capacity}. Azure validates quota during provisioning.",
        flush=True,
    )
    return {
        "HYDRO_FOUNDRY_MODEL_NAME": catalog_name,
        "HYDRO_FOUNDRY_MODEL_VERSION": str(selected["version"]),
        "HYDRO_FOUNDRY_MODEL_SKU": str(skus[0]["name"]),
        "HYDRO_FOUNDRY_MODEL_CAPACITY": capacity,
    }


def deployments_parameter(model: str, settings: dict[str, str] | None = None) -> str:
    """AI_PROJECT_DEPLOYMENTS for the template.

    The template wraps this value in quotes inside its parameters file, so the JSON must arrive with its own quotes
    escaped; unescaped JSON fails with "invalid character 'n' after object key:value pair"."""
    values = os.environ if settings is None else settings
    required = ("HYDRO_FOUNDRY_MODEL_VERSION", "HYDRO_FOUNDRY_MODEL_SKU", "HYDRO_FOUNDRY_MODEL_CAPACITY")
    missing = [key for key in required if not values.get(key, "").strip()]
    if not model or missing:
        raise ValueError(
            "New Foundry projects require HYDRO_FOUNDRY_MODEL (deployment name), "
            "HYDRO_FOUNDRY_MODEL_VERSION, HYDRO_FOUNDRY_MODEL_SKU and HYDRO_FOUNDRY_MODEL_CAPACITY. "
            "Choose a chat model/version/SKU/capacity available in the selected subscription and region."
        )
    model_name = values.get("HYDRO_FOUNDRY_MODEL_NAME", "").strip() or model
    for value in (model, model_name, values["HYDRO_FOUNDRY_MODEL_VERSION"], values["HYDRO_FOUNDRY_MODEL_SKU"]):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
            raise ValueError("Foundry model names, version and SKU must be valid identifiers.")
    try:
        capacity = int(values["HYDRO_FOUNDRY_MODEL_CAPACITY"])
    except ValueError as error:
        raise ValueError("HYDRO_FOUNDRY_MODEL_CAPACITY must be a whole number.") from error
    if capacity <= 0:
        raise ValueError("HYDRO_FOUNDRY_MODEL_CAPACITY must be a positive whole number.")
    deployments = [{
        "name": model,
        "model": {"name": model_name, "format": "OpenAI",
                  "version": values["HYDRO_FOUNDRY_MODEL_VERSION"].strip()},
        "sku": {"name": values["HYDRO_FOUNDRY_MODEL_SKU"].strip(),
                "capacity": capacity},
    }]
    return json.dumps(json.dumps(deployments, separators=(",", ":")))[1:-1]


def ensure_foundry_project(
    deploy, hosted, *, tenant: str, workspace: str, subscription: str, location: str, model: str, check_access,
    sleep=time.sleep,
    model_settings: dict[str, str] | None = None,
) -> str:
    """Provision a Foundry project and return its endpoint once the signed-in user can create agents in it."""
    try:
        deployments = deployments_parameter(model, model_settings)
    except ValueError as error:
        raise deploy.DeployError(str(error)) from error
    hosted.ensure_azd_ready(deploy, tenant)
    name = environment_name(tenant, workspace, subscription)
    directory = hosted.state_root() / "foundry-project" / name
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "azure.yaml").exists():
        print(f"Foundry: initializing the project template in {directory}.", flush=True)
        deploy.run_stream(deploy.command_argv(
            "azd", "init", "-t", TEMPLATE, "-e", name, "--no-prompt",
        ), cwd=directory)
    settings = {
        "AZURE_SUBSCRIPTION_ID": subscription, "AZURE_TENANT_ID": tenant,
        "AZURE_LOCATION": location, "AZURE_AI_DEPLOYMENTS_LOCATION": location,
        "ENABLE_HOSTED_AGENTS": "true", "ENABLE_CAPABILITY_HOST": "false", "ENABLE_MONITORING": "true",
        "AI_PROJECT_DEPLOYMENTS": deployments,
    }
    hosted.set_azd_environment(deploy, directory, name, settings)
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
