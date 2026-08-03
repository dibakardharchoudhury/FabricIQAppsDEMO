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


def node24(command: str) -> list[str]:
    """Run a repo command under the Node 24 wrapper required by the app."""
    if os.name == "nt":
        inner = f'cd /d "{APP_DIR}" && {command}'
    else:
        import shlex

        inner = f"cd {shlex.quote(str(APP_DIR))} && {command}"
    return command_argv("npx", "-y", "-p", "node@24", "-c", inner)


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


def resolve_spa(client_id: str | None) -> str:
    if client_id:
        if not GUID_RE.fullmatch(client_id):
            raise DeployError("SPA client id must be a GUID.")
        run_capture(az("ad", "app", "show", "--id", client_id, "--output", "none"))
        print(f"Using requested SPA app registration: {client_id}", flush=True)
        return client_id

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
    if len(apps) == 1:
        print(f"Reusing tenant SPA app registration: {apps[0]}", flush=True)
        return str(apps[0])
    if len(apps) > 1:
        raise DeployError(
            f"Multiple app registrations are named '{APP_DISPLAY_NAME}'. "
            "Enter the intended SPA client id in the deploy form."
        )

    print(f"Creating tenant SPA app registration '{APP_DISPLAY_NAME}'...", flush=True)
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
    if not GUID_RE.fullmatch(app_id):
        raise DeployError(f"Azure CLI returned an invalid SPA client id: {app_id!r}")
    print(f"Created SPA app registration: {app_id}", flush=True)
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


def prepare_rayfin_env(tenant: str, workspace_id: str, workspace_name: str, client_id: str) -> None:
    values, deployment = current_rayfin_target()
    if deployment and all(
        (
            values.get("FABRIC_WORKSPACE_NAME") == workspace_name,
            values.get("RAYFIN_PUBLIC_WORKSPACE_ID", "").casefold() == workspace_id.casefold(),
            values.get("RAYFIN_PUBLIC_TENANT_ID", "").casefold() == tenant.casefold(),
            values.get("RAYFIN_PUBLIC_AAD_CLIENT_ID", "").casefold() == client_id.casefold(),
            str(deployment.get("fabricWorkspaceId") or "").casefold() == workspace_id.casefold(),
            str(deployment.get("fabricTenantId") or "").casefold() == tenant.casefold(),
        )
    ):
        print("Existing Rayfin state already targets this tenant/workspace; reusing it.", flush=True)
        return

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
        "<your Entra SPA app (client) id>": client_id,
        "<your Entra tenant id>": tenant,
    }
    for placeholder, value in replacements.items():
        if placeholder not in template:
            raise DeployError(f"Expected placeholder {placeholder!r} is missing from rayfin/.env.example.")
        template = template.replace(placeholder, value)
    (RAYFIN_DIR / ".env").write_text(template, encoding="utf-8", newline="\n")
    print("Generated fresh rayfin/.env for the target workspace.", flush=True)


def validate_deployed_app(workspace_id: str, client_id: str, hosting_url: str) -> None:
    """Fail unless Fabric and Entra reflect every required deployment contract."""
    _, deployment = current_rayfin_target()
    item_id = str((deployment or {}).get("fabricItemId") or "")
    if not GUID_RE.fullmatch(item_id):
        raise DeployError("Rayfin deployment state does not contain a valid Fabric AppBackend item id.")
    item = fabric_get(f"workspaces/{workspace_id}/items/{item_id}", fabric_headers())
    if item.get("type") != "AppBackend" or str(item.get("workspaceId")) != workspace_id:
        raise DeployError(
            f"Fabric item validation failed for {item_id}: expected AppBackend in workspace {workspace_id}."
        )

    app = json.loads(run_capture(az("ad", "app", "show", "--id", client_id, "-o", "json")))
    redirect_uris = set((app.get("spa") or {}).get("redirectUris") or [])
    if hosting_url not in redirect_uris:
        raise DeployError(f"SPA redirect validation failed: {hosting_url} is not registered on {client_id}.")

    required_access = {
        str(block.get("resourceAppId")): {
            str(access.get("id"))
            for access in block.get("resourceAccess") or []
            if access.get("type") == "Scope"
        }
        for block in app.get("requiredResourceAccess") or []
    }
    resource_sp_ids: dict[str, str] = {}
    for resource_app_id, scope_values in REQUIRED_DELEGATED.items():
        resource = json.loads(
            run_capture(az("ad", "sp", "show", "--id", resource_app_id, "-o", "json"))
        )
        resource_sp_ids[resource_app_id] = str(resource.get("id") or "")
        scope_ids = {
            str(scope.get("id"))
            for scope in resource.get("oauth2PermissionScopes") or []
            if scope.get("value") in scope_values
        }
        if len(scope_ids) != len(scope_values) or not scope_ids.issubset(required_access.get(resource_app_id, set())):
            raise DeployError(
                f"Delegated permission validation failed for resource {resource_app_id}: "
                f"required scopes are {', '.join(sorted(scope_values))}."
            )

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
    current_user_id = run_capture(az("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"))
    principal_fallbacks: list[str] = []
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
            raise DeployError(
                f"Delegated consent validation failed for resource {resource_app_id}; "
                f"missing for the current user: {', '.join(sorted(principal_missing))}."
            )
        principal_fallbacks.append(resource_app_id)
    if principal_fallbacks:
        print(
            "WARNING: live-auth consent is current-user only for resources "
            f"{', '.join(principal_fallbacks)}. The app works for this operator, but an enterprise "
            "rollout requires Privileged Role Administrator / Global Administrator to grant "
            "tenant-wide admin consent (AllPrincipals).",
            flush=True,
        )
    print(f"Validated Fabric AppBackend {item_id} and all Entra live-auth contracts.", flush=True)


def ensure_rayfin_login(tenant: str) -> None:
    try:
        status = run_stream(node24("rayfin login status"))
    except DeployError:
        status = ""
    if tenant.casefold() in status.casefold():
        print("Rayfin is already signed into the target tenant.", flush=True)
        return
    if status:
        run_stream(node24("rayfin logout"))
    print("Rayfin sign-in is opening in your browser...", flush=True)
    run_stream(node24(f"rayfin login --tenant {tenant} --select"))
    verified = run_stream(node24("rayfin login status"))
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


def deploy(args: argparse.Namespace) -> None:
    print("[1/8] Validating Azure tenant and Fabric workspace", flush=True)
    if args.push_config:
        validate_git_push_ready()
    ensure_azure_tenant(args.tenant)
    workspace_id, workspace_name = resolve_workspace(args.workspace)
    print(f"Target workspace: {workspace_name} ({workspace_id})", flush=True)

    print("[2/8] Resolving the tenant SPA app registration", flush=True)
    client_id = resolve_spa(args.client_id)

    print("[3/8] Resetting local Rayfin deployment state", flush=True)
    prepare_rayfin_env(args.tenant, workspace_id, workspace_name, client_id)

    print("[4/8] Authenticating Rayfin to the target tenant", flush=True)
    ensure_rayfin_login(args.tenant)

    print("[5/8] Provisioning backend, database schema, and static app", flush=True)
    output = run_stream(node24(f"rayfin up --workspace-id {workspace_id} --yes"))
    urls = HOSTING_URL_RE.findall(output)
    if not urls:
        raise DeployError("Rayfin completed without reporting a Fabric hosting URL.")
    hosting_url = urls[-1]

    print("[6/8] Applying the generated hosting origin to backend auth", flush=True)
    run_stream(
        node24(
            f"rayfin up --workspace-id {workspace_id} "
            "--exclude-services staticHosting --yes"
        )
    )

    print("[7/8] Configuring SPA redirects, delegated permissions, and consent", flush=True)
    run_stream(node24("npm run setup-live-auth"))

    print("[8/8] Verifying the deployed app", flush=True)
    response = requests.get(hosting_url, timeout=60)
    if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", ""):
        raise DeployError(
            f"App verification failed: {hosting_url} returned HTTP {response.status_code} "
            f"with Content-Type {response.headers.get('Content-Type', '(missing)')}."
        )
    validate_deployed_app(workspace_id, client_id, hosting_url)
    print(f"DEPLOYED_APP_URL={hosting_url}", flush=True)

    if args.push_config:
        print("Persisting Rayfin's generated hosting origin to origin/main...", flush=True)
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