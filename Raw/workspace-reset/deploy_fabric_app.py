#!/usr/bin/env python3
"""Deploy HydroOperationsApp to a Fabric tenant and workspace.

This orchestrates the canonical HydroOperationsApp/DEPLOY.md flow while keeping
Rayfin and setup-live-auth as the implementation sources of truth. It is used by
the local "Initialize Your Fabric Demo" web app and can also be run directly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
APP_DISPLAY_NAME = "Hydro Operations Fabric Client"
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
HOSTING_URL_RE = re.compile(r"https://[a-z0-9-]+\.webapp\.fabricapps\.net")
REQUIRED_DELEGATED = {
    "2746ea77-4702-4b45-80ca-3c97e680e8b7": {"user_impersonation"},
    "00000009-0000-0000-c000-000000000000": {
        "GraphQLApi.Execute.All",
        "Workspace.Read.All",
        "Item.Read.All",
        "Item.Execute.All",
    },
}
RESOURCE_NAMES = {
    "2746ea77-4702-4b45-80ca-3c97e680e8b7": "Azure Data Explorer",
    "00000009-0000-0000-c000-000000000000": "Power BI Service / Microsoft Fabric",
}
STALE_TOKEN_CHALLENGE_RE = re.compile(
    r"TokenCreatedWithOutdatedPolicies|Continuous access evaluation|InteractionRequired|"
    r"AADSTS50076|AADSTS50079|AADSTS50173",
    re.IGNORECASE,
)
AZURE_CLI_TOKEN_CACHE_FILES = ("msal_token_cache.bin", "msal_http_cache.bin")

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
APP_DIR = REPO_ROOT / "HydroOperationsApp"
RAYFIN_DIR = APP_DIR / "rayfin"


class DeployError(RuntimeError):
    """Expected deployment failure with an operator-readable message."""


def command_argv(executable: str, *args: str) -> list[str]:
    """Build an argv that can invoke .cmd shims on Windows without shell=True."""
    resolved = shutil.which(executable)
    if not resolved:
        raise DeployError(f"Required command '{executable}' was not found on PATH.")
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", executable, *args]
    return [resolved, *args]


def run_capture(argv: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise DeployError(detail or f"Command failed with exit code {proc.returncode}.")
    return proc.stdout.strip()


def run_stream(argv: list[str], *, cwd: Path | None = None) -> str:
    """Run a command while forwarding output and retaining it for URL parsing."""
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        lines.append(line)
        print(line, flush=True)
    returncode = proc.wait()
    output = "\n".join(lines)
    if returncode != 0:
        raise DeployError(f"Command failed with exit code {returncode}.")
    return output


def az(*args: str) -> list[str]:
    return command_argv("az", *args)


_NODE24_EXECUTABLE: Path | None = None


def node24_executable() -> Path:
    """Resolve and verify the Node 24 executable supplied by npx."""
    global _NODE24_EXECUTABLE
    if _NODE24_EXECUTABLE and _NODE24_EXECUTABLE.is_file():
        return _NODE24_EXECUTABLE
    executable = Path(
        run_capture(command_argv("npx", "-y", "-p", "node@24", "-c", "node -p process.execPath"))
    )
    if not executable.is_file():
        raise DeployError(f"Node 24 executable was not found at {executable}.")
    version = run_capture([str(executable), "-p", "process.versions.node"])
    if version.split(".", 1)[0] != "24":
        raise DeployError(f"Expected Node 24, but npx resolved Node {version} at {executable}.")
    _NODE24_EXECUTABLE = executable
    return executable


def npm_cli_path() -> Path:
    """Locate npm's JavaScript entry point so Node 24 can host it explicitly."""
    executable = shutil.which("npm")
    if not executable:
        raise DeployError("Required command 'npm' was not found on PATH.")
    npm_path = Path(executable)
    resolved = npm_path.resolve()
    candidates = [
        resolved if resolved.name == "npm-cli.js" else None,
        npm_path.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
        npm_path.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        npm_path.parent.parent / "share" / "nodejs" / "npm" / "bin" / "npm-cli.js",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise DeployError(f"Could not locate npm-cli.js for {npm_path}.")


def npm24(*arguments: str) -> list[str]:
    """Run npm's CLI with Node 24, bypassing a global npm shim's Node version."""
    return [str(node24_executable()), str(npm_cli_path()), *arguments]


def rayfin24(*arguments: str) -> list[str]:
    """Run the installed Rayfin CLI directly with Node 24."""
    package_dir = APP_DIR / "node_modules" / "@microsoft" / "rayfin-cli"
    package = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    entrypoint = package_dir / str((package.get("bin") or {}).get("rayfin") or "")
    if not entrypoint.is_file():
        raise DeployError("Installed Rayfin CLI entry point is unavailable.")
    return [str(node24_executable()), str(entrypoint), *arguments]


def node24_script(script: Path, *arguments: str) -> list[str]:
    """Run a repository JavaScript file directly with Node 24."""
    return [str(node24_executable()), str(script), *arguments]


def installed_rayfin_version() -> str:
    """Verify the local Rayfin package without loading project configuration."""
    package_dir = APP_DIR / "node_modules" / "@microsoft" / "rayfin-cli"
    package_path = package_dir / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeployError(f"Installed Rayfin package metadata is unavailable: {package_path}") from exc

    version = str(package.get("version") or "").strip()
    bin_path = package_dir / str((package.get("bin") or {}).get("rayfin") or "")
    if not version or not bin_path.is_file():
        raise DeployError("Installed Rayfin CLI package is incomplete.")
    return version


def stop_hydro_node_tooling() -> None:
    """Stop this app's Vite/esbuild processes before npm replaces node_modules."""
    if os.name != "nt":
        return

    app_path = str(APP_DIR).replace("'", "''")
    script = (
        "$app = '" + app_path + "'; "
        "$matches = Get-CimInstance Win32_Process | Where-Object { "
        "$_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.IndexOf($app, "
        "[System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and ("
        "$_.Name -eq 'esbuild.exe' -or $_.CommandLine -match "
        "'(?i)(vite(?:\\.js)?|npm(?:-cli\\.js)?\\s+run\\s+(dev|preview))') }; "
        "$ids = @($matches.ProcessId | Sort-Object -Unique); "
        "if ($ids.Count -gt 0) { Stop-Process -Id $ids -Force -ErrorAction SilentlyContinue; "
        "$ids -join ',' }"
    )
    stopped = run_capture(
        command_argv(
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        )
    )
    if stopped:
        print(
            f"Stopped Hydro Operations development tooling that was locking node_modules "
            f"(process IDs: {stopped}).",
            flush=True,
        )


def ensure_deploy_dependencies() -> None:
    """Restore the locked Node toolchain and verify the local Rayfin CLI."""
    manifests = (APP_DIR / "package.json", APP_DIR / "package-lock.json")
    missing = [path.name for path in manifests if not path.is_file()]
    if missing:
        raise DeployError(
            "Hydro Operations dependency manifest is incomplete; missing "
            f"{', '.join(missing)}. Restore the files from Git and retry."
        )
    if not shutil.which("npx"):
        raise DeployError(
            "Node.js/npm/npx are not installed or not on PATH. Install Node.js from "
            "https://nodejs.org/ (npm and npx are included), reopen the launcher, and retry. "
            "The deployer downloads Node 24 and installs Rayfin automatically afterward."
        )

    stop_hydro_node_tooling()
    print("Restoring locked Hydro Operations npm dependencies (including Rayfin)...", flush=True)
    try:
        run_stream(npm24("ci", "--no-audit", "--no-fund"), cwd=APP_DIR)
        version = installed_rayfin_version()
    except DeployError as exc:
        raise DeployError(
            "Could not restore or verify the locked Hydro Operations npm dependencies. "
            "Check internet/proxy access to npm, write access to HydroOperationsApp/node_modules, "
            f"and package-lock.json, then retry. Underlying error: {exc}"
        ) from exc
    print(f"Rayfin dependency ready: {version}", flush=True)


def ensure_azure_tenant(tenant: str) -> None:
    account = json.loads(run_capture(az("account", "show", "-o", "json")))
    active = str(account.get("tenantId") or "")
    if active.casefold() != tenant.casefold():
        raise DeployError(
            f"Azure CLI is signed into tenant {active or '(unknown)'}, not {tenant}. "
            "Use the local app's Switch button with the target tenant, then retry."
        )
    user = (account.get("user") or {}).get("name") or "current Azure CLI user"
    print(f"Azure identity: {user} (tenant {active})", flush=True)


def fabric_headers() -> dict[str, str]:
    token = run_capture(
        az(
            "account",
            "get-access-token",
            "--resource",
            "https://api.fabric.microsoft.com",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        )
    )
    return {"Authorization": f"Bearer {token}"}


def fabric_get(path: str, headers: dict[str, str]) -> dict[str, Any]:
    response = requests.get(f"{FABRIC_BASE}/{path.lstrip('/')}", headers=headers, timeout=60)
    if not response.ok:
        raise DeployError(f"Fabric API GET {path} failed: HTTP {response.status_code} {response.text}")
    return response.json()


def resolve_workspace(workspace: str) -> tuple[str, str]:
    headers = fabric_headers()
    if GUID_RE.fullmatch(workspace):
        item = fabric_get(f"workspaces/{workspace}", headers)
        return workspace, str(item.get("displayName") or workspace)

    matches: list[dict[str, Any]] = []
    url: str | None = f"{FABRIC_BASE}/workspaces"
    while url:
        response = requests.get(url, headers=headers, timeout=60)
        if not response.ok:
            raise DeployError(f"Could not list Fabric workspaces: HTTP {response.status_code} {response.text}")
        page = response.json()
        matches.extend(
            item
            for item in page.get("value", [])
            if str(item.get("displayName") or "").casefold() == workspace.casefold()
        )
        url = page.get("continuationUri")
    if not matches:
        raise DeployError(f"No accessible Fabric workspace named '{workspace}' was found.")
    if len(matches) > 1:
        ids = ", ".join(str(item.get("id")) for item in matches)
        raise DeployError(f"Multiple workspaces are named '{workspace}': {ids}. Use the workspace GUID.")
    return str(matches[0]["id"]), str(matches[0]["displayName"])


def warn_live_auth(message: str) -> None:
    print(
        f"WARNING: {message}\n"
        "Fabric app deployment will continue, but browser sign-in and live Fabric data features "
        "may remain unavailable until an Entra administrator completes the SPA setup.",
        flush=True,
    )


def existing_spa_candidate(tenant: str) -> str | None:
    values, _ = current_rayfin_target()
    candidate = values.get("RAYFIN_PUBLIC_AAD_CLIENT_ID", "")
    configured_tenant = values.get("RAYFIN_PUBLIC_TENANT_ID", "")
    if configured_tenant.casefold() == tenant.casefold() and GUID_RE.fullmatch(candidate):
        return candidate
    return None


def ensure_spa_service_principal(client_id: str) -> None:
    try:
        run_capture(az("ad", "sp", "show", "--id", client_id, "--output", "none"))
        return
    except DeployError:
        pass

    try:
        run_capture(az("ad", "sp", "create", "--id", client_id, "--output", "none"))
        print(f"Created tenant service principal for SPA {client_id}.", flush=True)
    except DeployError as exc:
        warn_live_auth(
            f"The enterprise application/service principal for SPA {client_id} is missing and "
            f"could not be created ({exc}). Consent can be granted after an Entra administrator "
            "creates the enterprise application."
        )


def resolve_spa(client_id: str | None, tenant: str) -> str | None:
    if client_id:
        if not GUID_RE.fullmatch(client_id):
            raise DeployError("SPA client id must be a GUID.")
        try:
            run_capture(az("ad", "app", "show", "--id", client_id, "--output", "none"))
            print(f"Using requested SPA app registration: {client_id}", flush=True)
        except DeployError as exc:
            warn_live_auth(
                f"The requested SPA {client_id} could not be verified ({exc}). "
                "It will still be included in the deployed app configuration."
            )
        ensure_spa_service_principal(client_id)
        return client_id

    fallback = existing_spa_candidate(tenant)
    try:
        apps = json.loads(
            run_capture(
                az(
                    "ad",
                    "app",
                    "list",
                    "--display-name",
                    APP_DISPLAY_NAME,
                    "--query",
                    "[].appId",
                    "-o",
                    "json",
                )
            )
        )
    except (DeployError, json.JSONDecodeError) as exc:
        if fallback:
            warn_live_auth(
                f"Tenant SPA discovery failed ({exc}); reusing the unverified client ID "
                f"from the existing Rayfin environment: {fallback}."
            )
            return fallback
        warn_live_auth(f"Tenant SPA discovery failed and no existing client ID is available ({exc}).")
        return None
    if len(apps) == 1:
        print(f"Reusing tenant SPA app registration: {apps[0]}", flush=True)
        app_id = str(apps[0])
        ensure_spa_service_principal(app_id)
        return app_id
    if len(apps) > 1:
        if fallback and fallback in apps:
            warn_live_auth(
                f"Multiple app registrations are named '{APP_DISPLAY_NAME}'; reusing the "
                f"existing Rayfin client ID {fallback}."
            )
            return fallback
        warn_live_auth(
            f"Multiple app registrations are named '{APP_DISPLAY_NAME}' and none can be "
            "selected safely. Enter the intended SPA client ID on a later deployment."
        )
        return None

    print(f"Creating tenant SPA app registration '{APP_DISPLAY_NAME}'...", flush=True)
    try:
        app_id = run_capture(
            az(
                "ad",
                "app",
                "create",
                "--display-name",
                APP_DISPLAY_NAME,
                "--sign-in-audience",
                "AzureADMyOrg",
                "--query",
                "appId",
                "-o",
                "tsv",
            )
        )
    except DeployError as exc:
        warn_live_auth(
            f"Could not create the single-tenant SPA '{APP_DISPLAY_NAME}'. "
            "Ask an Application Administrator / Cloud Application Administrator to create it "
            "and configure its SPA redirect URIs and delegated permissions. Ask a Privileged "
            "Role Administrator / Global Administrator to grant tenant-wide admin consent. "
            "The deployed hosting URL can be added afterward. "
            f"Underlying Azure CLI error: {exc}"
        )
        return None
    if not GUID_RE.fullmatch(app_id):
        warn_live_auth(f"Azure CLI returned an invalid SPA client ID: {app_id!r}.")
        return None
    print(f"Created SPA app registration: {app_id}", flush=True)
    ensure_spa_service_principal(app_id)
    return app_id


def current_rayfin_target() -> tuple[dict[str, str], dict[str, Any] | None]:
    values: dict[str, str] = {}
    env_path = RAYFIN_DIR / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in raw or raw.lstrip().startswith("#"):
                continue
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()

    state_path = RAYFIN_DIR / ".deployments.json"
    if not state_path.exists():
        return values, None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        active = state.get("active")
        deployment = (state.get("deployments") or {}).get(active)
        return values, deployment if isinstance(deployment, dict) else None
    except (json.JSONDecodeError, OSError):
        return values, None


def fabric_item_exists(workspace_id: str, item_id: str) -> bool:
    """Return false for deleted saved items; fail for other Fabric API errors."""
    if not GUID_RE.fullmatch(item_id):
        return False
    response = requests.get(
        f"{FABRIC_BASE}/workspaces/{workspace_id}/items/{item_id}",
        headers=fabric_headers(),
        timeout=60,
    )
    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    raise DeployError(
        f"Could not validate saved Fabric item {item_id}: "
        f"HTTP {response.status_code} {response.text}"
    )


def prepare_rayfin_env(
    tenant: str, workspace_id: str, workspace_name: str, client_id: str | None
) -> None:
    values, deployment = current_rayfin_target()
    target_matches = deployment and all(
        (
            values.get("FABRIC_WORKSPACE_NAME") == workspace_name,
            values.get("RAYFIN_PUBLIC_WORKSPACE_ID", "").casefold() == workspace_id.casefold(),
            values.get("RAYFIN_PUBLIC_TENANT_ID", "").casefold() == tenant.casefold(),
            values.get("RAYFIN_PUBLIC_AAD_CLIENT_ID", "").casefold()
            == (client_id or "").casefold(),
            str(deployment.get("fabricWorkspaceId") or "").casefold() == workspace_id.casefold(),
            str(deployment.get("fabricTenantId") or "").casefold() == tenant.casefold(),
        )
    )
    if target_matches:
        item_id = str(deployment.get("fabricItemId") or "")
        if fabric_item_exists(workspace_id, item_id):
            print("Existing Rayfin state already targets this tenant/workspace; reusing it.", flush=True)
            return
        print(f"Saved Fabric AppBackend {item_id or '(missing)'} no longer exists; resetting state.", flush=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(tempfile.gettempdir()) / "fabric-demo-rayfin-backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in (".env", ".env.local", ".deployments.json"):
        source = RAYFIN_DIR / name
        if source.exists():
            shutil.copy2(source, backup_dir / name)
            source.unlink()
    print(f"Previous Rayfin state backed up to {backup_dir}", flush=True)

    template = (RAYFIN_DIR / ".env.example").read_text(encoding="utf-8")
    replacements = {
        "<your Fabric workspace display name>": workspace_name,
        "<your Fabric workspace GUID>": workspace_id,
        "<your Entra SPA app (client) id>": client_id or "",
        "<your Entra tenant id>": tenant,
    }
    for placeholder, value in replacements.items():
        if placeholder not in template:
            raise DeployError(f"Expected placeholder {placeholder!r} is missing from rayfin/.env.example.")
        template = template.replace(placeholder, value)
    (RAYFIN_DIR / ".env").write_text(template, encoding="utf-8", newline="\n")
    print("Generated fresh rayfin/.env for the target workspace.", flush=True)


def validate_fabric_app(workspace_id: str) -> str:
    """Fail unless the expected Fabric AppBackend exists in the target workspace."""
    _, deployment = current_rayfin_target()
    item_id = str((deployment or {}).get("fabricItemId") or "")
    if not GUID_RE.fullmatch(item_id):
        raise DeployError("Rayfin deployment state does not contain a valid Fabric AppBackend item id.")
    item = fabric_get(f"workspaces/{workspace_id}/items/{item_id}", fabric_headers())
    if item.get("type") != "AppBackend" or str(item.get("workspaceId")) != workspace_id:
        raise DeployError(
            f"Fabric item validation failed for {item_id}: expected AppBackend in workspace {workspace_id}."
        )
    print(f"Validated Fabric AppBackend {item_id}.", flush=True)
    return item_id


def validate_entra_live_auth(client_id: str, hosting_url: str) -> None:
    """Validate Entra runtime contracts; callers decide whether failures are fatal."""
    app = json.loads(run_capture(az("ad", "app", "show", "--id", client_id, "-o", "json")))
    redirect_uris = set((app.get("spa") or {}).get("redirectUris") or [])
    if hosting_url not in redirect_uris:
        raise DeployError(f"SPA redirect validation failed: {hosting_url} is not registered on {client_id}.")
    print(f"Entra redirect check passed: {hosting_url}", flush=True)

    required_access = {
        str(block.get("resourceAppId")): {
            str(access.get("id"))
            for access in block.get("resourceAccess") or []
            if access.get("type") == "Scope"
        }
        for block in app.get("requiredResourceAccess") or []
    }
    resource_sp_ids: dict[str, str] = {}
    permission_issues: list[str] = []
    for resource_app_id, scope_values in REQUIRED_DELEGATED.items():
        resource = json.loads(
            run_capture(az("ad", "sp", "show", "--id", resource_app_id, "-o", "json"))
        )
        resource_sp_ids[resource_app_id] = str(resource.get("id") or "")
        scope_ids = {
            str(scope.get("value")): str(scope.get("id"))
            for scope in resource.get("oauth2PermissionScopes") or []
            if scope.get("value") in scope_values
        }
        configured_ids = required_access.get(resource_app_id, set())
        missing_configured = {
            scope for scope, scope_id in scope_ids.items() if scope_id not in configured_ids
        } | (scope_values - set(scope_ids))
        if missing_configured:
            permission_issues.append(
                f"- {RESOURCE_NAMES[resource_app_id]}: app registration is missing requested "
                f"scope(s): {', '.join(sorted(missing_configured))}."
            )
    if permission_issues:
        raise DeployError(
            "Delegated API permission configuration is incomplete:\n"
            + "\n".join(permission_issues)
            + "\nFix: Entra admin center > App registrations > Hydro Operations Fabric Client > "
            "API permissions > Add a permission."
        )
    print("Entra API permission check passed: all required delegated scopes are configured.", flush=True)

    client_sp_id = run_capture(az("ad", "sp", "show", "--id", client_id, "--query", "id", "-o", "tsv"))
    grants = json.loads(
        run_capture(
            az(
                "rest",
                "--method",
                "GET",
                "--uri",
                f"https://graph.microsoft.com/v1.0/servicePrincipals/{client_sp_id}/oauth2PermissionGrants",
                "--query",
                "value",
                "-o",
                "json",
            )
        )
    )
    current_user = json.loads(run_capture(az("ad", "signed-in-user", "show", "-o", "json")))
    current_user_id = str(current_user.get("id") or "")
    current_user_name = str(current_user.get("userPrincipalName") or current_user_id)
    principal_fallbacks: list[str] = []
    consent_issues: list[str] = []
    for resource_app_id, scope_values in REQUIRED_DELEGATED.items():
        tenant_grants = [
            grant
            for grant in grants
            if grant.get("resourceId") == resource_sp_ids[resource_app_id]
            and grant.get("consentType") == "AllPrincipals"
        ]
        tenant_scopes = {
            scope
            for grant in tenant_grants
            for scope in str(grant.get("scope") or "").split()
        }
        missing = scope_values - tenant_scopes
        if not missing:
            continue

        principal_scopes = {
            scope
            for grant in grants
            if grant.get("resourceId") == resource_sp_ids[resource_app_id]
            and grant.get("consentType") == "Principal"
            and grant.get("principalId") == current_user_id
            for scope in str(grant.get("scope") or "").split()
        }
        principal_missing = scope_values - principal_scopes
        if principal_missing:
            other_user_grants = [
                grant
                for grant in grants
                if grant.get("resourceId") == resource_sp_ids[resource_app_id]
                and grant.get("consentType") == "Principal"
                and grant.get("principalId") != current_user_id
            ]
            found = "no consent grant"
            if tenant_scopes or principal_scopes:
                found = "partial consent"
            elif other_user_grants:
                found = f"per-user consent for {len(other_user_grants)} other user(s), not {current_user_name}"
            consent_issues.append(
                f"- {RESOURCE_NAMES[resource_app_id]}: missing consent for "
                f"{', '.join(sorted(principal_missing))}; found {found}."
            )
            continue
        principal_fallbacks.append(RESOURCE_NAMES[resource_app_id])
    if consent_issues:
        raise DeployError(
            "Delegated API scopes are configured, but OAuth consent is not granted:\n"
            + "\n".join(consent_issues)
            + "\nThe API permissions list declares requested scopes; it is not proof of consent. "
            "In its Status column, each API must show 'Granted for <tenant>'.\n"
            "Fix: Entra admin center > App registrations > Hydro Operations Fabric Client > "
            "API permissions > Grant admin consent for <tenant>. A disabled button means the "
            "signed-in administrator lacks a consent-granting directory role. Alternatively, if "
            "tenant policy allows user consent, sign in to the deployed app as the intended user "
            "and accept the prompt, then rerun verification."
        )
    if principal_fallbacks:
        print(
            "WARNING: live-auth consent is current-user only for resources "
            f"{', '.join(principal_fallbacks)}. The app works for this operator, but an enterprise "
            "rollout requires Privileged Role Administrator / Global Administrator to grant "
            "tenant-wide admin consent (AllPrincipals).",
            flush=True,
        )
    print(f"Validated all Entra live-auth contracts for SPA {client_id}.", flush=True)


def ensure_rayfin_login(tenant: str) -> None:
    try:
        status = run_stream(rayfin24("login", "status"), cwd=APP_DIR)
    except DeployError:
        status = ""
    if tenant.casefold() in status.casefold():
        print("Rayfin is already signed into the target tenant.", flush=True)
        return
    if status:
        run_stream(rayfin24("logout"), cwd=APP_DIR)
    print("Rayfin sign-in is opening in your browser...", flush=True)
    run_stream(rayfin24("login", "--tenant", tenant, "--select"), cwd=APP_DIR)
    verified = run_stream(rayfin24("login", "status"), cwd=APP_DIR)
    if tenant.casefold() not in verified.casefold():
        raise DeployError("Rayfin sign-in completed, but its tenant does not match the requested tenant.")


def validate_git_push_ready() -> None:
    """Ensure automatic config persistence cannot absorb unrelated local work."""
    branch = run_capture(command_argv("git", "branch", "--show-current"), cwd=REPO_ROOT)
    if branch != "main":
        raise DeployError("Automatic config persistence requires the repository to be on main.")
    if run_capture(command_argv("git", "status", "--porcelain"), cwd=REPO_ROOT):
        raise DeployError(
            "Automatic config persistence requires a clean Git working tree. "
            "Commit/stash local changes or clear 'Commit generated hosting origin'."
        )
    run_stream(command_argv("git", "fetch", "origin"), cwd=REPO_ROOT)
    counts = run_capture(
        command_argv("git", "rev-list", "--left-right", "--count", "HEAD...origin/main"),
        cwd=REPO_ROOT,
    ).split()
    if len(counts) != 2:
        raise DeployError("Could not determine main/origin-main divergence.")
    if counts[0] != "0":
        raise DeployError("Local main has unpushed commits. Push or reconcile them before deploying.")
    if counts[1] != "0":
        run_stream(command_argv("git", "merge", "--ff-only", "origin/main"), cwd=REPO_ROOT)


def persist_generated_origin(workspace_name: str) -> None:
    config = APP_DIR / "rayfin" / "rayfin.yml"
    relative = config.relative_to(REPO_ROOT).as_posix()
    if not run_capture(command_argv("git", "diff", "--", relative), cwd=REPO_ROOT):
        print("Rayfin redirect configuration was already current; no Git commit needed.", flush=True)
        return
    run_stream(command_argv("git", "fetch", "origin"), cwd=REPO_ROOT)
    counts = run_capture(
        command_argv("git", "rev-list", "--left-right", "--count", "HEAD...origin/main"),
        cwd=REPO_ROOT,
    ).split()
    if len(counts) != 2 or counts[1] != "0":
        raise DeployError("origin/main changed during deployment. Merge the Fabric commit-back, then rerun deploy.")
    run_stream(command_argv("git", "add", relative), cwd=REPO_ROOT)
    run_stream(
        command_argv("git", "commit", "-m", f"deploy: register {workspace_name} app origin"),
        cwd=REPO_ROOT,
    )
    run_stream(command_argv("git", "push", "origin", "main"), cwd=REPO_ROOT)


def _unique_redirect_uris(*groups: list[str]) -> list[str]:
    """Return redirect URIs in stable order with blanks and duplicates removed."""
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            uri = str(raw or "").strip()
            if not uri or uri in seen:
                continue
            seen.add(uri)
            result.append(uri)
    return result


def read_entra_spa_redirects(client_id: str) -> list[str]:
    """Read the SPA redirect URIs currently registered in Entra."""
    app = json.loads(run_capture(az("ad", "app", "show", "--id", client_id, "-o", "json")))
    return [
        str(uri).strip()
        for uri in ((app.get("spa") or {}).get("redirectUris") or [])
        if str(uri).strip()
    ]


def read_entra_spa_redirects_with_reauth(client_id: str, tenant: str) -> list[str]:
    """Retry an Entra redirect snapshot after a stale-token CAE challenge."""
    try:
        return read_entra_spa_redirects(client_id)
    except DeployError as exc:
        if not STALE_TOKEN_CHALLENGE_RE.search(str(exc)):
            raise

    print(
        "Azure CLI token was rejected by Conditional Access because it predates a tenant "
        "policy change. Re-authenticating before reading the existing SPA redirects...",
        flush=True,
    )
    azure_dir = Path.home() / ".azure"
    for filename in AZURE_CLI_TOKEN_CACHE_FILES:
        (azure_dir / filename).unlink(missing_ok=True)
    run_stream(az("login", "--tenant", tenant, "--only-show-errors"))
    ensure_azure_tenant(tenant)
    return read_entra_spa_redirects(client_id)


def _rayfin_redirect_block() -> tuple[Path, list[str], int, int, int, str]:
    """Locate services.auth.allowedRedirectUris in rayfin.yml."""
    config = RAYFIN_DIR / "rayfin.yml"
    lines = config.read_text(encoding="utf-8").splitlines(keepends=True)
    key_index = next(
        (index for index, line in enumerate(lines) if line.strip() == "allowedRedirectUris:"),
        None,
    )
    if key_index is None:
        raise DeployError("rayfin.yml does not contain services.auth.allowedRedirectUris.")

    key_indent = len(lines[key_index]) - len(lines[key_index].lstrip())
    end_index = key_index + 1
    while end_index < len(lines):
        line = lines[end_index]
        if line.strip():
            indent = len(line) - len(line.lstrip())
            if indent <= key_indent:
                break
        end_index += 1

    newline = "\r\n" if lines[key_index].endswith("\r\n") else "\n"
    return config, lines, key_index, end_index, key_indent, newline


def write_rayfin_redirects(redirects: list[str]) -> list[str]:
    """Write the complete deduplicated redirect list to rayfin.yml."""
    config, lines, key_index, end_index, key_indent, newline = _rayfin_redirect_block()
    merged = _unique_redirect_uris(redirects, ["http://localhost:5173"])
    replacement = [lines[key_index]]
    replacement.extend(
        f"{' ' * (key_indent + 2)}- {uri}{newline}"
        for uri in merged
    )
    config.write_text(
        "".join(lines[:key_index] + replacement + lines[end_index:]),
        encoding="utf-8",
    )
    return merged


def validate_spa_redirect_preservation(client_id: str, expected: list[str]) -> None:
    """Fail if any redirect URI captured/configured before deployment disappeared."""
    current = set(read_entra_spa_redirects(client_id))
    missing = [uri for uri in expected if uri not in current]
    if missing:
        raise DeployError(
            "SPA redirect preservation check failed. The deployment removed or failed to register "
            f"these URI(s) on {client_id}: {', '.join(missing)}"
        )
    print(
        f"Entra redirect preservation check passed: {len(expected)} required URI(s) are present.",
        flush=True,
    )


def deploy(args: argparse.Namespace) -> None:
    print("[1/8] Checking Azure tenant and Fabric workspace", flush=True)
    if args.push_config:
        validate_git_push_ready()
    ensure_azure_tenant(args.tenant)
    workspace_id, workspace_name = resolve_workspace(args.workspace)
    print(f"Target workspace: {workspace_name} ({workspace_id})", flush=True)

    print("[2/8] Resolving the tenant SPA app registration", flush=True)
    client_id = resolve_spa(args.client_id, args.tenant)

    # Capture both configuration sources BEFORE any Rayfin command can modify Entra.
    # A shared SPA may already serve several Fabric webapps, so losing even one existing
    # redirect URI is a deployment failure.
    original_entra_redirects: list[str] = []
    if client_id:
        try:
            original_entra_redirects = read_entra_spa_redirects_with_reauth(client_id, args.tenant)
        except (DeployError, json.JSONDecodeError) as exc:
            raise DeployError(
                f"Could not snapshot existing SPA redirect URIs for {client_id}. "
                "Refusing to deploy because redirect preservation cannot be guaranteed. "
                f"Underlying error: {exc}"
            ) from exc
        print(
            f"Captured {len(original_entra_redirects)} existing Entra SPA redirect URI(s) "
            "for preservation.",
            flush=True,
        )

    # Entra is authoritative for existing redirects. Do not resurrect historical hosts
    # that remain only in rayfin.yml; seed Rayfin with the live snapshot plus localhost.
    preserved_redirects = write_rayfin_redirects(
        _unique_redirect_uris(original_entra_redirects, ["http://localhost:5173"])
    )

    print("[3/8] Resetting local Rayfin deployment state", flush=True)
    prepare_rayfin_env(args.tenant, workspace_id, workspace_name, client_id)
    ensure_deploy_dependencies()

    print("[4/8] Authenticating Rayfin to the target tenant", flush=True)
    ensure_rayfin_login(args.tenant)

    print("[5/8] Provisioning backend, database schema, and static app", flush=True)
    output = run_stream(rayfin24("up", "--workspace-id", workspace_id, "--yes"), cwd=APP_DIR)
    urls = HOSTING_URL_RE.findall(output)
    if not urls:
        raise DeployError("Rayfin completed without reporting a Fabric hosting URL.")
    hosting_url = urls[-1]

    print("[6/8] Adding the current app URL to the preserved redirect configuration", flush=True)
    preserved_redirects = write_rayfin_redirects(
        _unique_redirect_uris(
            original_entra_redirects,
            [hosting_url],
            ["http://localhost:5173"],
        )
    )
    print(
        f"Rayfin redirect configuration now contains {len(preserved_redirects)} URI(s).",
        flush=True,
    )
    run_stream(
        rayfin24(
            "up", "--workspace-id", workspace_id,
            "--exclude-services", "staticHosting", "--yes",
        ),
        cwd=APP_DIR,
    )

    print("[7/8] Setting up browser sign-in (redirect, permissions, and consent)", flush=True)
    if client_id:
        try:
            run_stream(
                node24_script(APP_DIR / "scripts" / "setup-live-auth.mjs"),
                cwd=APP_DIR,
            )
        except DeployError as exc:
            warn_live_auth(f"Automated SPA configuration did not complete ({exc}).")
    else:
        warn_live_auth(
            "SPA configuration was skipped because no usable Application (client) ID is available."
        )

    print("[8/8] Checking the hosted page, Fabric backend, and sign-in readiness", flush=True)
    response = requests.get(hosting_url, timeout=60)
    if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", ""):
        raise DeployError(
            f"App verification failed: {hosting_url} returned HTTP {response.status_code} "
            f"with Content-Type {response.headers.get('Content-Type', '(missing)')}."
        )
    validate_fabric_app(workspace_id)
    if client_id:
        # Redirect preservation is a hard safety contract: never report success if a URI
        # that existed before deployment (or was configured in rayfin.yml) disappeared.
        validate_spa_redirect_preservation(client_id, preserved_redirects)
        try:
            validate_entra_live_auth(client_id, hosting_url)
        except (DeployError, json.JSONDecodeError) as exc:
            warn_live_auth(
                f"Browser sign-in readiness check did not pass for SPA {client_id}:\n{exc}"
            )
    else:
        warn_live_auth(
            f"Entra validation was skipped. After an administrator creates the SPA, register "
            f"{hosting_url} as its redirect URI and run npm run setup-live-auth."
        )
    print(f"DEPLOYED_APP_URL={hosting_url}", flush=True)

    if args.push_config:
        print("Persisting Rayfin redirect configuration to origin/main...", flush=True)
        persist_generated_origin(workspace_name)
    print(f"SUCCESS: Hydro Operations is live at {hosting_url}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="Target Entra tenant GUID or domain.")
    parser.add_argument("--workspace", required=True, help="Target Fabric workspace GUID or name.")
    parser.add_argument("--client-id", help="Existing SPA app client id; auto-resolved/created when omitted.")
    parser.add_argument(
        "--push-config",
        action="store_true",
        help="Commit and push Rayfin's generated hosting origin to origin/main.",
    )
    args = parser.parse_args()
    args.tenant = args.tenant.strip()
    args.workspace = args.workspace.strip()
    args.client_id = args.client_id.strip() if args.client_id else None
    if not TENANT_RE.fullmatch(args.tenant):
        parser.error("tenant must be a GUID or domain name")
    if not args.workspace or len(args.workspace) > 256:
        parser.error("workspace must be a GUID or display name")
    return args


if __name__ == "__main__":
    try:
        deploy(parse_args())
    except (DeployError, requests.RequestException, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc