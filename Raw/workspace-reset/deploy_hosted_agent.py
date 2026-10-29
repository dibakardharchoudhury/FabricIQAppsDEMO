"""Publish the Hydro Supervisor as the Foundry Hosted Agent ``hydro-orchestrator``.

Called by provision_foundry_agents.provision() immediately before it verifies the hosted agent; it is not a
separate deployment path. It converges the agent on the current source and target:

1. fingerprint every file the Dockerfile packages and build ``hydro-orchestrator:src-<fingerprint>`` in the
   project's ACR only when that tag does not exist yet;
2. compose both runtime configurations from the verified target (never copied from another one);
3. publish a new immutable version with azd only when the live agent differs; and
4. wait until the active version carries exactly the expected image digest and configuration.

An agent that is already current costs one registry lookup and changes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

AGENT_NAME = "hydro-orchestrator"
IMAGE_REPOSITORY = "hydro-orchestrator"
AZD_ENVIRONMENT = "hydro-orchestrator"  # the `name` in azure.yaml
DOCKERFILE = "HydroOperationsService/Dockerfile"
DEFAULT_STATE_DIR = "/home/session/hydro"
REQUIRED_EXTENSIONS = ("azure.ai.agents", "microsoft.foundry")  # pinned in azure.yaml requiredVersions
ACTIVATION_TIMEOUT_S = 900
ACTIVATION_POLL_S = 10
IMAGE_DIGEST_RE = re.compile(r"^[^@\s]+@(?P<digest>sha256:[0-9a-f]{64})$")
MISSING_RE = re.compile(r"does not exist|not found|unknown|404", re.IGNORECASE)
JSON_VARIABLES = ("HYDRO_ORCHESTRATOR_CONFIG", "HYDRO_FABRIC_SOURCE_CONFIG")
PINNED_ENDPOINT_RE = re.compile(r"^(?P<head>\s*endpoint:\s*)(?P<value>\S+)\s*$", re.MULTILINE)


def state_root() -> Path:
    """Per-user, untracked home for generated azd projects (override with HYDRO_STATE_DIR)."""
    return Path(os.environ.get("HYDRO_STATE_DIR", "").strip() or Path.home() / ".fabric-demo")


def azd_project_dir(deploy, endpoint: str) -> Path:
    """Generate isolated azd state for the verified project, never reuse repository-local target state."""
    root = deploy.REPO_ROOT
    text = (root / "azure.yaml").read_text(encoding="utf-8")
    pinned = PINNED_ENDPOINT_RE.search(text)
    if not pinned or pinned["value"] != "${AZURE_AI_PROJECT_ENDPOINT}":
        raise deploy.DeployError("azure.yaml must use the target-independent ${AZURE_AI_PROJECT_ENDPOINT} placeholder.")
    wanted = endpoint.rstrip("/")
    parsed = urlparse(wanted)
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".services.ai.azure.com")
            or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment
            or not re.fullmatch(r"/api/projects/[A-Za-z0-9_.-]+", parsed.path)):
        raise deploy.DeployError("The selected Foundry project endpoint is invalid.")
    label = re.sub(r"[^a-z0-9-]", "-", f"{(parsed.hostname or 'project').split('.')[0]}-{parsed.path.rsplit('/', 1)[-1]}".lower())
    directory = state_root() / "hosted-agent" / f"{label}-{hashlib.sha256(wanted.encode()).hexdigest()[:12]}"
    directory.mkdir(parents=True, exist_ok=True)
    generated = PINNED_ENDPOINT_RE.sub(lambda match: match["head"] + wanted, text, count=1)
    target = directory / "azure.yaml"
    if not target.exists() or target.read_text(encoding="utf-8") != generated:
        target.write_text(generated, encoding="utf-8", newline="\n")
    return directory


def packaged_sources(repo_root: Path) -> list[Path]:
    """Every file the Dockerfile copies into the image (plus the Dockerfile and its ignore file)."""
    dockerfile = repo_root / DOCKERFILE
    files: set[Path] = {dockerfile}
    ignore = dockerfile.with_name(dockerfile.name + ".dockerignore")
    if ignore.exists():
        files.add(ignore)
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if not parts or parts[0].upper() != "COPY" or any(part.startswith("--from") for part in parts[1:]):
            continue
        for pattern in [part for part in parts[1:] if not part.startswith("--")][:-1]:
            matches = sorted(repo_root.glob(pattern)) if any(char in pattern for char in "*?[") else [repo_root / pattern]
            if not matches or any(not match.exists() for match in matches):
                raise RuntimeError(f"The Dockerfile copies {pattern}, which does not exist.")
            for match in matches:
                if match.is_dir():
                    files.update(item for item in match.rglob("*") if item.is_file())
                else:
                    files.add(match)
    return sorted(files, key=lambda path: path.relative_to(repo_root).as_posix())


def source_fingerprint(repo_root: Path) -> str:
    digest = hashlib.sha256()
    for path in packaged_sources(repo_root):
        digest.update(path.relative_to(repo_root).as_posix().encode() + b"\0")
        # Line endings must not change the identity of the same source across operating systems.
        digest.update(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).digest())
    return digest.hexdigest()


def image_tag(repo_root: Path) -> str:
    return f"src-{source_fingerprint(repo_root)[:16]}"


def compose_runtime_configuration(
    *, tenant: str, workspace: str, ontology: str, spa_client_id: str, kusto_origin: str,
    project_endpoint: str, values: dict[str, str], source_digest: str, production_writes: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    """The two JSON documents the runtime requires, built only from the verified deployment target."""
    kusto = kusto_origin.rstrip("/")
    source = {
        "tenant_id": tenant, "workspace_id": workspace, "ontology_id": ontology, "generation": 2,
        "configuration_digest": source_digest,
    }
    orchestrator: dict[str, object] = {
        "source": source,
        "authorization": {
            "tenant_id": tenant, "spa_client_id": spa_client_id,
            "audiences": {
                "fabric": ["https://api.fabric.microsoft.com"], "foundry": ["https://ai.azure.com"],
                "graphql": ["https://analysis.windows.net/powerbi/api"], "kusto": [kusto],
            },
            "scopes": {
                "fabric": "https://api.fabric.microsoft.com/.default", "foundry": "https://ai.azure.com/.default",
                "graphql": "https://analysis.windows.net/powerbi/api/.default", "kusto": f"{kusto}/.default",
            },
        },
        "project_endpoint": project_endpoint.rstrip("/"),
        "native_binding": {"source": source, "connections": {"data-agent": "hydro-fabric-iq"},
                           "toolbox": "hydro-fabric-iq-tools"},
        "production_writes_enabled": production_writes,
    }
    fabric_source: dict[str, object] = {
        "tenant_id": tenant, "workspace_id": workspace, "ontology_id": ontology,
        "eventhouse_id": values["RAYFIN_PUBLIC_EVENTHOUSE_ID"], "database_id": values["RAYFIN_PUBLIC_KQL_DATABASE_ID"],
        "lakehouse_id": values["RAYFIN_PUBLIC_LAKEHOUSE_ID"], "appbackend_id": values["RAYFIN_PUBLIC_ITEM_ID"],
        "operational_sql_database_id": values["RAYFIN_PUBLIC_OPERATIONAL_SQL_DATABASE_ID"],
        "api_url": values["RAYFIN_PUBLIC_API_URL"], "publishable_key": values["RAYFIN_PUBLIC_PUBLISHABLE_KEY"],
    }
    return orchestrator, fabric_source


def hosted_state(agent: dict | None) -> tuple[str, str, str, dict[str, str]]:
    latest = (agent or {}).get("versions", {}).get("latest", {})
    definition = latest.get("definition", {})
    image = definition.get("container_configuration", {}).get("image") or ""
    match = IMAGE_DIGEST_RE.match(image)
    return (
        f"{definition.get('kind', '')}:{latest.get('status', '')}", str(latest.get("version") or ""),
        match.group("digest") if match else "", definition.get("environment_variables") or {},
    )


def needs_publish(agent: dict | None, *, image_digest: str, environment: dict[str, str]) -> str | None:
    """Why the live agent differs from the expected one, or None when it is current."""
    if agent is None:
        return "no hosted agent exists yet"
    kind_status, _version, live_digest, live_environment = hosted_state(agent)
    if kind_status != "hosted:active":
        return f"the latest version is {kind_status or 'unreadable'}, not an active hosted agent"
    if live_digest != image_digest:
        return "its image is not the one built from the current source"
    for key, expected in environment.items():
        live = live_environment.get(key)
        try:
            same = json.loads(live) == json.loads(expected) if key in JSON_VARIABLES else live == expected
        except (TypeError, ValueError):
            same = False
        if not same:
            return f"{key} differs from the verified deployment target"
    return None


def registry_name(connections: list[dict], override: str) -> str:
    names = {
        urlparse(target if "//" in target else f"https://{target}").hostname or ""
        for connection in connections
        if (properties := connection.get("properties", {})).get("category") == "ContainerRegistry"
        and (target := str(properties.get("target") or ""))
    }
    names = {host.split(".")[0] for host in names if host.endswith(".azurecr.io")}
    if override.strip():
        if override.strip() not in names:
            raise RuntimeError("HYDRO_ACR_NAME must match a live ContainerRegistry connection in the selected project.")
        return override.strip()
    if len(names) == 1:
        return names.pop()
    raise RuntimeError(
        "Could not determine the container registry: the Foundry project has "
        f"{len(names)} registry connection(s). Set HYDRO_ACR_NAME to the registry to build in."
    )


def parse_azd_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)
            except ValueError:
                value = value[1:-1]
        values[key.strip()] = value
    return values


def azd_bootstrap_values(
    *, project_id: str, endpoint: str, tenant: str, location: str, registry: str, registry_id: str,
    connection: str,
) -> dict[str, str]:
    """The azd environment variables a project-bound hosted-agent deploy needs, derived from the live project."""
    match = re.fullmatch(
        r"/subscriptions/(?P<sub>[^/]+)/resourceGroups/(?P<group>[^/]+)/providers/Microsoft\.CognitiveServices/"
        r"accounts/(?P<account>[^/]+)/projects/(?P<project>[^/]+)", project_id, re.IGNORECASE,
    )
    if not match:
        raise RuntimeError("The Foundry project resource ID is not in the expected form.")
    login_server = f"{registry}.azurecr.io"
    return {
        "AZURE_SUBSCRIPTION_ID": match["sub"], "AZURE_TENANT_ID": tenant, "AZURE_LOCATION": location,
        "AZURE_AI_DEPLOYMENTS_LOCATION": location, "AZURE_RESOURCE_GROUP": match["group"],
        "AZURE_FOUNDRY_RESOURCE_GROUP": match["group"], "AZURE_AI_ACCOUNT_NAME": match["account"],
        "AZURE_AI_PROJECT_NAME": match["project"], "AZURE_AI_PROJECT_ID": project_id,
        "AZURE_AI_PROJECT_ENDPOINT": endpoint, "FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "AZURE_AI_PROJECT_CONNECTIONS_PROJECT_ENDPOINT": endpoint,
        "AZURE_OPENAI_ENDPOINT": f"https://{match['account']}.openai.azure.com/",
        "AZURE_CONTAINER_REGISTRY_NAME": registry, "AZURE_CONTAINER_REGISTRY_ENDPOINT": login_server,
        "AZURE_CONTAINER_REGISTRY_RESOURCE_ID": registry_id, "AZURE_AI_PROJECT_ACR_CONNECTION_NAME": connection,
        "ENABLE_HOSTED_AGENTS": "true", "USE_EXISTING_AI_PROJECT": "true", "ENABLE_CAPABILITY_HOST": "false",
    }


def ensure_hosted_image(deploy, *, registry: str, subscription: str) -> tuple[str, str]:
    root = deploy.REPO_ROOT
    tag = image_tag(root)
    digest = _registry_digest(deploy, registry, subscription, tag)
    if digest is not None:
        return tag, digest
    print(f"Foundry image preparation: building {IMAGE_REPOSITORY}:{tag} in {registry}.", flush=True)
    sources = packaged_sources(root)
    with tempfile.TemporaryDirectory(prefix="hydro-agent-build-") as directory:
        context = Path(directory)
        for source in sources:
            target = context / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if image_tag(context) != tag:
            raise deploy.DeployError("Hosted source changed while staging its build context; retry deployment.")
        print(f"Staged hosted build context: {len(sources)} files, "
              f"{sum(source.stat().st_size for source in sources)} bytes.", flush=True)
        deploy.run_stream(
            deploy.az("acr", "build", "--registry", registry, "--subscription", subscription,
                      "--image", f"{IMAGE_REPOSITORY}:{tag}", "--platform", "linux/amd64",
                      "--file", str(context / DOCKERFILE), "--no-logs", "--timeout", "1200",
                      "--query", "{runId:runId,status:status}", "--output", "json", str(context)),
            cwd=root, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    digest = _registry_digest(deploy, registry, subscription, tag)
    if digest is None:
        raise deploy.DeployError(f"The registry has no {IMAGE_REPOSITORY}:{tag} after the build completed.")
    return tag, digest


def ensure_hosted_agent(
    deploy, *, get_agent, endpoint: str, project_id: str, model: str, tenant: str, workspace: str, ontology: str,
    spa_client_id: str, values: dict[str, str], source_digest: str, kusto_origin: str, connections: list[dict],
    sleep=time.sleep,
) -> dict:
    """Return the active hosted agent after converging it on the current source and deployment target.

    ``values`` are the already-verified public deployment values (RAYFIN_PUBLIC_*), unquoted."""
    existing = get_agent()
    _kind, _version, _digest, live_environment = hosted_state(existing)
    override = os.environ.get("HYDRO_ACR_NAME", "")
    try:
        registry = registry_name(connections, override)
    except RuntimeError as error:
        raise deploy.DeployError(str(error)) from error
    subscription = project_id.split("/")[2]

    writes = os.environ.get("HYDRO_PRODUCTION_WRITES_ENABLED", "").strip().lower() or "true"
    if writes not in ("true", "false"):
        raise deploy.DeployError("HYDRO_PRODUCTION_WRITES_ENABLED must be true or false.")
    production_writes = writes == "true"
    orchestrator, fabric_source = compose_runtime_configuration(
        tenant=tenant, workspace=workspace, ontology=ontology, spa_client_id=spa_client_id, kusto_origin=kusto_origin,
        project_endpoint=endpoint, values=values, source_digest=source_digest, production_writes=production_writes,
    )
    environment = {
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": model,
        "HYDRO_RUNTIME_STATE_DIR": live_environment.get("HYDRO_RUNTIME_STATE_DIR") or DEFAULT_STATE_DIR,
        "HYDRO_ORCHESTRATOR_CONFIG": json.dumps(orchestrator, separators=(",", ":")),
        "HYDRO_FABRIC_SOURCE_CONFIG": json.dumps(fabric_source, separators=(",", ":")),
    }

    tag, digest = ensure_hosted_image(deploy, registry=registry, subscription=subscription)
    reason = needs_publish(existing, image_digest=digest, environment=environment)
    if reason is None:
        print(f"Hosted agent {AGENT_NAME} is current (image {tag}); nothing to publish.", flush=True)
        return existing  # type: ignore[return-value]
    print(f"Publishing Hosted Agent {AGENT_NAME}: {reason}.", flush=True)

    project_dir = azd_project_dir(deploy, endpoint)
    ensure_azd_ready(deploy, tenant)
    bootstrap = _configure_azd_environment(
        deploy, project_dir=project_dir, project_id=project_id, endpoint=endpoint, tenant=tenant,
        registry=registry, subscription=subscription, connections=connections,
        create=AZD_ENVIRONMENT not in _azd_environment_names(deploy, project_dir),
    )
    set_azd_environment(deploy, project_dir, AZD_ENVIRONMENT, {
        **bootstrap, **environment,
        "HYDRO_ORCHESTRATOR_IMAGE": f"{registry}.azurecr.io/{IMAGE_REPOSITORY}@{digest}",
    })
    deploy.run_stream(
        deploy.command_argv("azd", "deploy", AGENT_NAME, "--environment", AZD_ENVIRONMENT, "--no-prompt"),
        cwd=project_dir,
    )

    deadline = time.monotonic() + ACTIVATION_TIMEOUT_S
    while True:
        agent = get_agent()
        reason = needs_publish(agent, image_digest=digest, environment=environment)
        if reason is None:
            print(f"Hosted Agent {AGENT_NAME} version {hosted_state(agent)[1]} is active on {tag}.", flush=True)
            return agent  # type: ignore[return-value]
        if time.monotonic() > deadline:
            raise deploy.DeployError(
                f"Hosted Agent {AGENT_NAME} did not become active with the expected image and configuration "
                f"within {ACTIVATION_TIMEOUT_S // 60} minutes: {reason}."
            )
        sleep(ACTIVATION_POLL_S)


def _azd_environment_names(deploy, project_dir: Path) -> set[str]:
    listing = json.loads(deploy.run_capture(
        deploy.command_argv("azd", "env", "list", "--output", "json"), cwd=project_dir,
    ) or "[]")
    return {str(item.get("Name") or item.get("name")) for item in listing if isinstance(item, dict)}


def _registry_digest(deploy, registry: str, subscription: str, tag: str) -> str | None:
    try:
        digest = deploy.run_capture(deploy.az(
            "acr", "repository", "show", "--name", registry, "--subscription", subscription,
            "--image", f"{IMAGE_REPOSITORY}:{tag}", "--query", "digest", "-o", "tsv",
        ))
    except deploy.DeployError as error:
        if MISSING_RE.search(str(error)):
            return None
        raise
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise deploy.DeployError(f"The registry returned an unexpected digest for {IMAGE_REPOSITORY}:{tag}.")
    return digest


def ensure_azd_ready(deploy, tenant: str) -> None:
    """azd must be signed in and carry the Foundry extensions azure.yaml requires; install what is missing."""
    try:
        deploy.run_capture(deploy.command_argv("azd", "auth", "login", "--check-status"))
    except deploy.DeployError:
        print("azd is not signed in; opening sign-in for the target tenant.", flush=True)
        deploy.run_stream(deploy.command_argv("azd", "auth", "login", "--tenant-id", tenant))
    listing = json.loads(deploy.run_capture(
        deploy.command_argv("azd", "extension", "list", "--installed", "--output", "json"),
    ) or "[]")
    installed = {str(item.get("id")) for item in listing if isinstance(item, dict)}
    for extension in REQUIRED_EXTENSIONS:
        if extension not in installed:
            print(f"Installing the azd extension {extension}.", flush=True)
            deploy.run_stream(deploy.command_argv("azd", "extension", "install", extension))


def set_azd_environment(deploy, directory: Path, name: str, values: dict[str, str]) -> None:
    deploy.run_capture(deploy.command_argv(
        "azd", "env", "set", *(f"{key}={value}" for key, value in values.items()),
        "--environment", name,
    ), cwd=directory)


def _configure_azd_environment(
    deploy, *, project_dir: Path, project_id: str, endpoint: str, tenant: str, registry: str, subscription: str,
    connections: list[dict], create: bool,
) -> dict[str, str]:
    """Rebind every publish to live project metadata, including environments created on an earlier run."""
    root = project_dir
    location = deploy.run_capture(deploy.az(
        "resource", "show", "--ids", project_id, "--api-version", "2025-06-01", "--query", "location", "-o", "tsv",
    ))
    registry_id = deploy.run_capture(deploy.az(
        "acr", "show", "--name", registry, "--subscription", subscription, "--query", "id", "-o", "tsv",
    ))
    names = [
        connection["name"] for connection in connections
        if connection.get("properties", {}).get("category") == "ContainerRegistry"
        and str(connection["properties"].get("target", "")).startswith(f"{registry}.azurecr.io")
    ]
    if len(names) != 1:
        raise deploy.DeployError(f"Expected exactly one Foundry connection to registry {registry}, found {len(names)}.")
    if create:
        print(f"Creating the azd environment {AZD_ENVIRONMENT} from the live Foundry project.", flush=True)
        deploy.run_capture(deploy.command_argv(
            "azd", "env", "new", AZD_ENVIRONMENT, "--subscription", subscription, "--location", location, "--no-prompt",
        ), cwd=root)
    else:
        print(f"Refreshing the azd environment {AZD_ENVIRONMENT} from the live Foundry project.", flush=True)
    return azd_bootstrap_values(
        project_id=project_id, endpoint=endpoint, tenant=tenant, location=location, registry=registry,
        registry_id=registry_id, connection=names[0],
    )
