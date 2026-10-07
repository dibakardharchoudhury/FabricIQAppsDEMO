#!/usr/bin/env python3
"""Deploy HydroOperationsApp to a Fabric tenant and workspace.

This orchestrates the canonical HydroOperationsApp/DEPLOY.md flow while keeping
Rayfin and setup-live-auth as the implementation sources of truth. It is used by
the local "Initialize Your Fabric Demo" web app and can also be run directly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, quote, urlparse

import requests


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
DEFAULT_APP_DISPLAY_NAME = "Hydro Operations Fabric Client"
APP_DISPLAY_NAME = os.environ.get("HYDRO_SPA_DISPLAY_NAME", "").strip() or DEFAULT_APP_DISPLAY_NAME
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
HOSTING_URL_RE = re.compile(r"https://[a-z0-9-]+\.webapp\.fabricapps\.net")
APPBACKEND_CORS_PATHS = ("/graphql", "/api/auth/v1/token")
APPBACKEND_READINESS_DELAYS = (0, 2, 5, 10, 20)
REQUIRED_DELEGATED = {
    "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe": {"user_impersonation"},
    "2746ea77-4702-4b45-80ca-3c97e680e8b7": {"user_impersonation"},
    "00000009-0000-0000-c000-000000000000": {
        "GraphQLApi.Execute.All",
        "Workspace.Read.All",
        "Item.Read.All",
        "Item.Execute.All",
        "DataAgent.Execute.All",
        "Fabric.Embed",
    },
}
RESOURCE_NAMES = {
    "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe": "Microsoft Foundry Agent Service",
    "2746ea77-4702-4b45-80ca-3c97e680e8b7": "Azure Data Explorer",
    "00000009-0000-0000-c000-000000000000": "Power BI Service / Microsoft Fabric",
}
STALE_TOKEN_CHALLENGE_RE = re.compile(
    r"TokenCreatedWithOutdatedPolicies|Continuous access evaluation|InteractionRequired|"
    r"AADSTS50076|AADSTS50079|AADSTS50173|does not exist in MSAL token cache|"
    r"Please run ['\"]?az login|Run ['\"]?az login",
    re.IGNORECASE,
)
RTI_ARTIFACT_SUFFIX_RE = r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
APP_DIR = REPO_ROOT / "HydroOperationsApp"
RAYFIN_DIR = APP_DIR / "rayfin"
DEPENDENCY_STAMP = APP_DIR / "node_modules" / ".fabric-demo-package-lock.sha256"
AZURE_CLI_SESSION_ROOT = Path(tempfile.gettempdir()) / "fabric-demo-azure-cli"


class DeployError(RuntimeError):
    """Expected deployment failure with an operator-readable message."""


class AzureCliReauthenticationError(DeployError):
    """Azure CLI operation failed after a clean tenant-scoped login."""


T = TypeVar("T")


class _HostingPage(HTMLParser):
    """Read identity-bearing HTML elements without executing the hosting sign-in flow."""

    def __init__(self) -> None:
        super().__init__()
        self.titles: list[str] = []
        self.headings: list[str] = []
        self.bootstraps: list[str] = []
        self.module_sources: list[str] = []
        self.signin_button = False
        self.signin_body = False
        self.app_root = False
        self._capture: tuple[str, list[str]] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "title":
            self._capture = (tag, self.titles)
            self._text = []
        elif tag == "h1":
            self._capture = (tag, self.headings)
            self._text = []
        elif tag == "script":
            if attributes.get("id") == "sh-bootstrap" and attributes.get("type") == "application/json":
                self._capture = (tag, self.bootstraps)
                self._text = []
            if attributes.get("type") == "module" and attributes.get("src"):
                self.module_sources.append(str(attributes["src"]))
        elif tag == "body" and attributes.get("data-state") == "signin":
            self.signin_body = True
        elif tag == "button" and attributes.get("id") == "sh-signin":
            self.signin_button = True
        elif tag == "div" and attributes.get("id") == "root":
            self.app_root = True

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture and self._capture[0] == tag:
            self._capture[1].append("".join(self._text).strip())
            self._capture = None
            self._text = []


def validate_hosted_page(
    hosting_url: str, workspace_id: str, item_id: str, tenant_id: str
) -> str:
    """Verify app-shell or identity-matched protected-gate availability, not browser acceptance."""
    if not HOSTING_URL_RE.fullmatch(hosting_url.removesuffix("/")):
        raise DeployError("Hosting verification requires the generated HTTPS Fabric hosting origin.")
    try:
        # Protected hosting negotiates a browser gate for HTML; */* instead returns JSON 401.
        response = requests.get(
            hosting_url, headers={"Accept": "text/html"}, timeout=60, allow_redirects=False
        )
    except requests.RequestException as exc:
        raise DeployError(f"Hosting availability verification failed: {exc}") from exc
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or content_type.split(";", 1)[0].strip().lower() != "text/html":
        raise DeployError(
            f"Hosting verification failed: {hosting_url} returned HTTP {response.status_code} "
            f"with Content-Type {content_type or '(missing)'}. Expected identifiable HTML; "
            "an unauthorized response or redirect is not deployment verification."
        )
    page = _HostingPage()
    page.feed(response.text)
    page.close()
    if page.bootstraps:
        if (
            len(page.bootstraps) != 1
            or page.titles != ["Sign in required"]
            or "Sign in to continue" not in page.headings
            or not page.signin_body
            or not page.signin_button
        ):
            raise DeployError("Hosting verification failed: unrecognized Fabric protected sign-in gate.")
        try:
            bootstrap = json.loads(page.bootstraps[0])
        except (ValueError, TypeError) as exc:
            raise DeployError("Hosting verification failed: invalid protected-gate bootstrap JSON.") from exc
        if not isinstance(bootstrap, dict) or not isinstance(bootstrap.get("authorizeBrokerUrl"), str):
            raise DeployError("Hosting verification failed: missing protected-gate broker identity.")
        broker = urlparse(bootstrap["authorizeBrokerUrl"])
        parameters = parse_qs(broker.query, keep_blank_values=True)
        expected = {
            "workspaceId": workspace_id, "itemType": "AppBackend", "itemId": item_id,
            "extensionPath": "/brokeredauth", "ctid": tenant_id,
        }
        if (
            bootstrap.get("brokerOrigin") != "https://app.fabric.microsoft.com"
            or str(bootstrap.get("projectId", "")).casefold() != item_id.casefold()
            or broker.scheme != "https"
            or broker.netloc != "app.fabric.microsoft.com"
            or broker.path != "/secureItemEmbed"
            or broker.fragment
            or any(
                len(parameters.get(key, [])) != 1
                or parameters[key][0].casefold() != value.casefold()
                for key, value in expected.items()
            )
        ):
            raise DeployError(
                "Hosting verification failed: protected-gate Fabric broker, tenant, workspace, "
                "or AppBackend identity does not match this deployment."
            )
        state = "protected-sign-in-gate"
        print(
            "Hosting availability verified: identity-matched Fabric protected sign-in gate. "
            "The application bundle/UI was not loaded by this check.",
            flush=True,
        )
    elif (
        page.titles == ["Hydro Operations"]
        and page.app_root
        and any(re.fullmatch(r"/assets/index-[A-Za-z0-9_-]+\.js", source) for source in page.module_sources)
    ):
        state = "app-shell"
        print(
            "Hosting availability verified: Hydro Operations HTML shell references a built app bundle. "
            "JavaScript execution and authenticated UI behavior were not tested.",
            flush=True,
        )
    else:
        raise DeployError(
            "Hosting verification failed: HTML is neither the Hydro Operations app shell "
            "nor an identity-matched Fabric protected sign-in gate."
        )
    print(f"HOSTING_VERIFICATION={state}", flush=True)
    print("INTERACTIVE_APP_ACCEPTANCE=not-performed", flush=True)
    return state


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


def run_stream(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Run a command while forwarding output and retaining it for URL parsing."""
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
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


def cached_node24_executable() -> Path | None:
    """Find a previously downloaded npx Node 24 runtime without invoking npx."""
    cache_roots = {
        Path(value).expanduser()
        for value in (
            os.environ.get("npm_config_cache"),
            str(Path(os.environ["LOCALAPPDATA"]) / "npm-cache")
            if os.environ.get("LOCALAPPDATA") else None,
            str(Path.home() / ".npm"),
        )
        if value
    }
    executable_name = "node.exe" if os.name == "nt" else "node"
    candidates = (
        executable
        for cache_root in cache_roots
        for executable in cache_root.glob(
            f"_npx/*/node_modules/node/bin/{executable_name}"
        )
    )
    for executable in sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            version = run_capture([str(executable), "-p", "process.versions.node"])
        except (DeployError, OSError):
            continue
        if version.split(".", 1)[0] == "24":
            return executable
    return None


def node24_executable() -> Path:
    """Resolve and verify the Node 24 executable supplied by npx."""
    global _NODE24_EXECUTABLE
    if _NODE24_EXECUTABLE and _NODE24_EXECUTABLE.is_file():
        return _NODE24_EXECUTABLE
    cached = cached_node24_executable()
    if cached:
        _NODE24_EXECUTABLE = cached
        return cached
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


def deploy_dependencies_ready() -> bool:
    """Return true when the locked top-level dependency tree is already usable."""
    package_lock = APP_DIR / "package-lock.json"
    required = (
        APP_DIR / "node_modules" / ".bin" / ("vite.cmd" if os.name == "nt" else "vite"),
        APP_DIR / "node_modules" / ".bin" / ("tsc.cmd" if os.name == "nt" else "tsc"),
    )
    if not all(path.is_file() for path in required):
        return False
    expected_fingerprint = hashlib.sha256(package_lock.read_bytes()).hexdigest()
    try:
        installed_fingerprint = DEPENDENCY_STAMP.read_text(encoding="ascii").strip()
    except OSError:
        return False
    if installed_fingerprint != expected_fingerprint:
        return False
    try:
        installed_rayfin_version()
        run_capture(npm24("ls", "--depth=0", "--json"), cwd=APP_DIR)
        return True
    except DeployError:
        return False


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

    if deploy_dependencies_ready():
        print(
            f"Locked Hydro Operations dependencies already ready "
            f"(Rayfin {installed_rayfin_version()}); skipping npm ci.",
            flush=True,
        )
        return

    stop_hydro_node_tooling()
    print("Restoring locked Hydro Operations npm dependencies (including Rayfin)...", flush=True)
    try:
        command = npm24("ci", "--no-audit", "--no-fund")
        try:
            run_stream(command, cwd=APP_DIR)
        except DeployError:
            print(
                "The first npm restore failed; stopping app tooling again and retrying once...",
                flush=True,
            )
            stop_hydro_node_tooling()
            run_stream(command, cwd=APP_DIR)
        version = installed_rayfin_version()
        DEPENDENCY_STAMP.write_text(
            hashlib.sha256((APP_DIR / "package-lock.json").read_bytes()).hexdigest(),
            encoding="ascii",
        )
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


def isolated_azure_cli_config(tenant: str) -> Path:
    """Return the tenant-scoped cache used only for CAE reauthentication."""
    safe_tenant = re.sub(r"[^A-Za-z0-9._-]", "_", tenant).casefold()
    return AZURE_CLI_SESSION_ROOT / safe_tenant


def secure_private_directory(directory: Path, *, recursive: bool = False) -> None:
    """Create an owner-only directory, including an explicit Windows ACL."""
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        if os.name == "nt":
            identity = run_capture(command_argv("whoami", "/user", "/fo", "csv", "/nh"))
            sid_match = re.search(r"S-\d+(?:-\d+)+", identity)
            if not sid_match:
                raise DeployError("Could not determine the current Windows user SID.")
            acl_command = command_argv(
                "icacls",
                str(directory),
                "/inheritance:r",
                "/grant:r",
                f"*{sid_match.group(0)}:(OI)(CI)F",
                "/grant:r",
                "*S-1-5-18:(OI)(CI)F",
            )
            run_capture(acl_command)
            if recursive and any(directory.iterdir()):
                run_capture(
                    command_argv(
                        "icacls",
                        str(directory / "*"),
                        "/inheritance:r",
                        "/grant:r",
                        f"*{sid_match.group(0)}:F",
                        "/grant:r",
                        "*S-1-5-18:F",
                        "/T",
                        "/C",
                    )
                )
    except (OSError, DeployError) as exc:
        raise DeployError(
            f"Could not secure the tenant-scoped Azure CLI cache at {directory}."
        ) from exc


def reauthenticate_azure_cli(tenant: str, operation: str) -> None:
    print(
        f"Azure CLI authentication needs to be refreshed before {operation}. "
        f"Opening Microsoft sign-in for tenant {tenant}...",
        flush=True,
    )
    config_dir = isolated_azure_cli_config(tenant)
    secure_private_directory(config_dir.parent)
    if config_dir.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_dir = config_dir.with_name(f"{config_dir.name}.stale-{timestamp}")
        try:
            config_dir.rename(backup_dir)
        except OSError as exc:
            raise DeployError(
                f"Could not preserve the stale tenant-scoped Azure CLI cache at {config_dir}. "
                "No Azure CLI login state was changed. Close processes using that directory and retry."
            ) from exc
        secure_private_directory(backup_dir, recursive=True)
        print(f"Preserved stale Azure CLI session at {backup_dir}.", flush=True)
    secure_private_directory(config_dir)
    os.environ["AZURE_CONFIG_DIR"] = str(config_dir)
    run_stream(
        az(
            "login",
            "--tenant",
            tenant,
            "--allow-no-subscriptions",
            "--only-show-errors",
            "--output",
            "none",
        )
    )
    ensure_azure_tenant(tenant)


def run_with_azure_cli_reauthentication(
    tenant: str,
    operation: str,
    action: Callable[[], T],
) -> T:
    """Retry one stale-token failure with a new tenant-isolated Azure CLI cache."""
    try:
        return action()
    except DeployError as exc:
        if not STALE_TOKEN_CHALLENGE_RE.search(str(exc)):
            raise

    try:
        reauthenticate_azure_cli(tenant, operation)
    except DeployError as exc:
        raise AzureCliReauthenticationError(str(exc)) from exc
    try:
        return action()
    except DeployError as exc:
        if not STALE_TOKEN_CHALLENGE_RE.search(str(exc)):
            raise AzureCliReauthenticationError(str(exc)) from exc
        raise AzureCliReauthenticationError(
            f"Azure CLI authentication was still rejected by Continuous Access Evaluation while "
            f"{operation}, even after a clean tenant-scoped login. Upgrade Azure CLI to the current "
            "version and retry; if the challenge continues, ask the tenant administrator to verify "
            "the applicable Conditional Access policy."
        ) from exc


def fabric_headers(tenant: str) -> dict[str, str]:
    command = az(
        "account",
        "get-access-token",
        "--tenant",
        tenant,
        "--resource",
        "https://api.fabric.microsoft.com",
        "--query",
        "accessToken",
        "-o",
        "tsv",
    )
    token = run_with_azure_cli_reauthentication(
        tenant,
        "accessing the Fabric workspace",
        lambda: run_capture(command),
    )
    return {"Authorization": f"Bearer {token}"}


def fabric_get(path: str, headers: dict[str, str]) -> dict[str, Any]:
    response = requests.get(f"{FABRIC_BASE}/{path.lstrip('/')}", headers=headers, timeout=60)
    if not response.ok:
        raise DeployError(f"Fabric API GET {path} failed: HTTP {response.status_code} {response.text}")
    return response.json()


def check_capacity_availability(capacity_id: str, headers: dict[str, str]) -> None:
    url = f"{FABRIC_BASE}/capacities"
    seen_tokens: set[str] = set()
    while True:
        response = requests.get(url, headers=headers, timeout=60)
        if response.status_code == 403:
            print(
                "WARNING: Capacity-list access is unavailable. Capacity state is unverified; "
                "the mandatory deployment endpoint checks must still pass.",
                flush=True,
            )
            return
        if not response.ok:
            raise DeployError(
                f"Fabric capacity availability check failed: HTTP {response.status_code} {response.text}"
            )
        page = response.json()
        if not isinstance(page, dict):
            raise DeployError("Fabric capacity availability response is not an object.")
        items = page.get("value")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise DeployError("Fabric capacity availability response omitted a valid capacity list.")
        for item in items:
            if str(item.get("id") or "").casefold() != capacity_id.casefold():
                continue
            state = item.get("state")
            if state != "Active":
                raise DeployError(
                    f"Assigned Fabric capacity '{item.get('displayName') or capacity_id}' "
                    f"({capacity_id}) is {state or 'in an unknown state'}, not Active. "
                    "Deployment stopped before changing SPA, agents or Rayfin state. "
                    "Ask the capacity owner to restore availability, then rerun this command. "
                    "No resume, resize or reassignment was attempted."
                )
            print(f"Assigned Fabric capacity is Active ({capacity_id}).", flush=True)
            return
        token = page.get("continuationToken")
        if not token:
            if page.get("continuationUri"):
                raise DeployError("Fabric capacity pagination omitted its continuation token.")
            break
        if not isinstance(token, str) or token in seen_tokens:
            raise DeployError("Fabric capacity pagination returned an invalid or repeated token.")
        seen_tokens.add(token)
        url = f"{FABRIC_BASE}/capacities?continuationToken={quote(token, safe='')}"
    print(
        f"WARNING: Assigned capacity {capacity_id} is not visible to this identity. "
        "Capacity state is unverified; the mandatory deployment endpoint checks must still pass.",
        flush=True,
    )


def resolve_workspace(workspace: str, tenant: str) -> tuple[str, str, str]:
    headers = fabric_headers(tenant)
    if GUID_RE.fullmatch(workspace):
        item = fabric_get(f"workspaces/{workspace}", headers)
        capacity_id = str(item.get("capacityId") or "")
        if not GUID_RE.fullmatch(capacity_id):
            raise DeployError(f"Fabric workspace '{workspace}' is not assigned to a usable capacity.")
        check_capacity_availability(capacity_id, headers)
        return workspace, str(item.get("displayName") or workspace), capacity_id

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
    workspace_id = str(matches[0]["id"])
    capacity_id = str(matches[0].get("capacityId") or "")
    if not GUID_RE.fullmatch(capacity_id):
        item = fabric_get(f"workspaces/{workspace_id}", headers)
        capacity_id = str(item.get("capacityId") or "")
    if not GUID_RE.fullmatch(capacity_id):
        raise DeployError(f"Fabric workspace '{workspace}' is not assigned to a usable capacity.")
    check_capacity_availability(capacity_id, headers)
    return workspace_id, str(matches[0]["displayName"]), capacity_id


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
            raise DeployError(
                f"The requested SPA {client_id} could not be verified. Deployment stopped "
                f"before changing Rayfin state. Underlying error: {exc}"
            ) from exc
        ensure_spa_service_principal(client_id)
        return client_id

    fallback = existing_spa_candidate(tenant)
    discovery_command = az(
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
    try:
        discovery_output = run_with_azure_cli_reauthentication(
            tenant,
            "discovering the tenant SPA app registration",
            lambda: run_capture(discovery_command),
        )
    except AzureCliReauthenticationError:
        raise
    except DeployError as exc:
        if fallback and not STALE_TOKEN_CHALLENGE_RE.search(str(exc)):
            warn_live_auth(
                f"Tenant SPA discovery failed ({exc}); reusing the unverified client ID "
                f"from the existing Rayfin environment: {fallback}."
            )
            return fallback
        if not STALE_TOKEN_CHALLENGE_RE.search(str(exc)):
            warn_live_auth(f"Tenant SPA discovery failed and no existing client ID is available ({exc}).")
            return None
        raise
    try:
        apps = json.loads(discovery_output)
    except json.JSONDecodeError as exc:
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


def fabric_item_exists(workspace_id: str, item_id: str, tenant: str) -> bool:
    """Return false for deleted saved items; fail for other Fabric API errors."""
    if not GUID_RE.fullmatch(item_id):
        return False
    response = requests.get(
        f"{FABRIC_BASE}/workspaces/{workspace_id}/items/{item_id}",
        headers=fabric_headers(tenant),
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


def rayfin_api_targets_capacity(
    values: dict[str, str], deployment: dict[str, Any], capacity_id: str
) -> bool:
    expected_path = f"/capacities/{capacity_id.casefold()}/"
    urls = (
        values.get("RAYFIN_PUBLIC_API_URL", ""),
        str(deployment.get("fabricApiUrl") or ""),
    )
    return all(expected_path in url.casefold() for url in urls)


def validate_rayfin_endpoint_contract(
    capacity_id: str, workspace_id: str, item_id: str
) -> str:
    values, deployment = current_rayfin_target()
    env_url = values.get("RAYFIN_PUBLIC_API_URL", "").rstrip("/")
    deployment_url = str((deployment or {}).get("fabricApiUrl") or "").rstrip("/")
    if not env_url or env_url != deployment_url:
        raise DeployError(
            "Rayfin endpoint validation failed: rayfin/.env and deployment state do not "
            "contain the same API URL."
        )

    parsed = urlparse(env_url)
    expected_host = f"{capacity_id.replace('-', '').casefold()}.pbidedicated.windows.net"
    expected_path = (
        f"/webapi/capacities/{capacity_id.casefold()}/workloads/baas/baasservice/"
        f"automatic/v1/workspaces/{workspace_id.casefold()}/appbackends/{item_id.casefold()}"
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.path.rstrip("/").casefold() != expected_path
    ):
        raise DeployError(
            "Rayfin endpoint validation failed: generated API URL does not target the current "
            f"capacity/workspace/AppBackend ({capacity_id}/{workspace_id}/{item_id}). "
            f"Found: {env_url or '(missing)'}"
        )
    print("Validated Rayfin endpoint against the current Fabric deployment.", flush=True)
    return env_url


def validate_rayfin_publishable_key() -> str:
    values, deployment = current_rayfin_target()
    env_key = values.get("RAYFIN_PUBLIC_PUBLISHABLE_KEY", "")
    deployment_key = str((deployment or {}).get("publishableKey") or "")
    if not env_key.startswith("pk-") or env_key != deployment_key:
        raise DeployError(
            "Rayfin publishable-key validation failed: rayfin/.env and deployment state "
            "do not contain the same valid publishable key."
        )
    return env_key


def validate_appbackend_cors(
    api_url: str, hosting_url: str, publishable_key: str
) -> None:
    origin = urlparse(hosting_url).scheme + "://" + str(urlparse(hosting_url).netloc)
    preflight_headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,x-publishable-key",
    }
    post_headers = {
        "Origin": origin,
        "Content-Type": "application/json",
        "x-publishable-key": publishable_key,
    }
    post_bodies = {
        "/graphql": {"query": "query { __typename }"},
        "/api/auth/v1/token": {},
    }
    failures: list[str] = []
    for path in APPBACKEND_CORS_PATHS:
        last_failure = "no response"
        for delay in APPBACKEND_READINESS_DELAYS:
            if delay:
                time.sleep(delay)
            try:
                response = requests.options(
                    f"{api_url}{path}",
                    headers=preflight_headers,
                    timeout=60,
                )
                allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
                allow_headers = {
                    value.strip().casefold()
                    for value in response.headers.get(
                        "Access-Control-Allow-Headers", ""
                    ).split(",")
                    if value.strip()
                }
                required_headers = {"authorization", "content-type", "x-publishable-key"}
                if not (
                    200 <= response.status_code < 300
                    and allow_origin in {"*", origin}
                    and required_headers.issubset(allow_headers)
                ):
                    last_failure = (
                        f"preflight HTTP {response.status_code}; Access-Control-Allow-Origin="
                        f"{allow_origin or '(missing)'}; Access-Control-Allow-Headers="
                        f"{response.headers.get('Access-Control-Allow-Headers', '(missing)')}"
                    )
                    continue

                post_response = requests.post(
                    f"{api_url}{path}",
                    headers=post_headers,
                    json=post_bodies[path],
                    timeout=60,
                )
                post_allow_origin = post_response.headers.get(
                    "Access-Control-Allow-Origin", ""
                )
                expected_status = (
                    post_response.status_code == 200
                    if path == "/graphql"
                    else 200 <= post_response.status_code < 500
                    and post_response.status_code not in {404, 405}
                )
                if expected_status and post_allow_origin in {"*", origin}:
                    print(
                        f"Validated AppBackend browser path: {path} "
                        f"(preflight HTTP {response.status_code}, POST HTTP "
                        f"{post_response.status_code}, origin {post_allow_origin}).",
                        flush=True,
                    )
                    break
                last_failure = (
                    f"POST HTTP {post_response.status_code}; Access-Control-Allow-Origin="
                    f"{post_allow_origin or '(missing)'}"
                )
            except requests.RequestException as exc:
                last_failure = str(exc)
        else:
            failures.append(f"- {path}: {last_failure}")

    if failures:
        raise DeployError(
            "AppBackend browser readiness failed after runtime settings were reapplied:\n"
            + "\n".join(failures)
            + "\nThe deployment is not healthy; do not treat the hosted HTML page as success."
        )


def _canonical_graph_binding(raw: str) -> str:
    value = raw.strip()
    if value in ("", "''", '""'):
        return ""
    cli_quoted = value.startswith('"') and value.endswith('"')
    if value.startswith("'") and value.endswith("'"):
        value = value[1:-1]
    if len(value) > 1024 * 1024:
        raise DeployError("The operator-provided Ontology graph binding exceeds the producer size limit.")
    # Undo only the bounded Rayfin 1.36 writer/reader mismatch, at the producer.
    # The browser still accepts strict JSON only; identities are verified below.
    for layer in range(16):
        try:
            decoded = json.loads(value)
        except ValueError:
            if not cli_quoted or not value.startswith("{") or not value.endswith("}"):
                break
            try:
                decoded = json.loads('"' + value + '"')
            except ValueError:
                break
            if len(decoded) >= len(value):
                break
            value = decoded
            continue
        if isinstance(decoded, dict):
            try:
                encoded = json.dumps(decoded, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            except ValueError:
                break
            encoded = re.sub(
                r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})',
                lambda match: "\\u" + format(ord(json.loads('"' + match.group(0) + '"')), "04x"),
                encoded,
            )
            return encoded.replace("'", r"\u0027").replace("$", r"\u0024")
        if layer == 0 and cli_quoted and isinstance(decoded, str):
            value = decoded
            continue
        break
    raise DeployError("The operator-provided Ontology graph binding is invalid JSON.")


def _public_config_value(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if key == "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING":
        return _canonical_graph_binding(value)
    if value.startswith('"') and value.endswith('"'):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else value
        except ValueError:
            return value[1:-1]
    return value[1:-1] if value.startswith("'") and value.endswith("'") else value


def _workspace_artifacts(workspace_id: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    path = f"workspaces/{workspace_id}/items"
    next_path: str | None = path
    seen_pages: set[str] = set()
    items: list[dict[str, Any]] = []
    while next_path:
        if next_path in seen_pages or len(seen_pages) >= 100:
            raise DeployError("Target-workspace artifact listing exceeded its pagination bound.")
        seen_pages.add(next_path)
        page = fabric_get(next_path, headers)
        entries = page.get("value")
        if not isinstance(entries, list) or any(
            not isinstance(item, dict) or not item.get("id") or not item.get("type")
            or not item.get("displayName")
            or (item.get("workspaceId") and str(item["workspaceId"]).casefold() != workspace_id.casefold())
            for item in entries
        ):
            raise DeployError("Target-workspace artifact listing is malformed or contains another workspace.")
        items.extend(entries)
        continuation = page.get("continuationUri")
        token = page.get("continuationToken")
        next_path = None
        if continuation:
            if not isinstance(continuation, str):
                raise DeployError("Invalid Fabric artifact continuation URI.")
            parsed = urlparse(continuation)
            if (
                parsed.scheme != "https" or parsed.netloc != "api.fabric.microsoft.com"
                or parsed.path != f"/v1/{path}" or parsed.fragment
            ):
                raise DeployError("Refusing artifact continuation outside the selected workspace.")
            next_path = path + (f"?{parsed.query}" if parsed.query else "")
        elif token:
            if not isinstance(token, str):
                raise DeployError("Invalid Fabric artifact continuation token.")
            next_path = f"{path}?continuationToken={quote(token, safe='')}"
    if len({item["id"] for item in items}) != len(items):
        raise DeployError("Target-workspace artifact listing contains duplicate identities.")
    return items


def _lineage_graph_binding(
    workspace_id: str,
    ontology: dict[str, Any],
    items: list[dict[str, Any]],
    headers: dict[str, str],
) -> dict[str, str] | None:
    ontology_id = str(ontology["id"])
    metadata = fabric_get(
        f"workspaces/{workspace_id}/ontologies/{quote(ontology_id, safe='')}", headers
    )
    if (
        metadata.get("id") != ontology_id
        or (metadata.get("workspaceId") and str(metadata["workspaceId"]).casefold() != workspace_id.casefold())
        or not isinstance(metadata.get("properties"), dict)
        or metadata["properties"].get("generation") != 2
    ):
        raise DeployError("The selected Ontology is not a verified generation-2 item in the target workspace.")

    lineage = fabric_get(
        f"workspaces/{workspace_id}/items/{quote(ontology_id, safe='')}/relations/downstream?beta=true",
        headers,
    )
    related_items = lineage.get("items")
    relations = lineage.get("relations")
    if not isinstance(related_items, list) or not isinstance(relations, list):
        raise DeployError("Ontology downstream lineage response is malformed.")
    related_by_id = {
        str(item["id"]): item for item in related_items
        if isinstance(item, dict) and item.get("id")
    }
    workspace_by_id = {str(item["id"]): item for item in items}
    graph_ids = {
        str(relation["itemId"])
        for relation in relations
        if isinstance(relation, dict)
        and str(relation.get("dependentOnItemId", "")).casefold() == ontology_id.casefold()
        and relation.get("relationType") == "CascadeDelete"
        and related_by_id.get(str(relation.get("itemId")), {}).get("type") == "GraphIndex"
        and str(related_by_id[str(relation["itemId"])].get("workspaceId", "")).casefold() == workspace_id.casefold()
        and workspace_by_id.get(str(relation.get("itemId")), {}).get("type") == "GraphModel"
    }
    if len(graph_ids) > 1:
        raise DeployError("Ontology lineage identifies multiple materialized GraphModels; graph ownership is ambiguous.")
    if not graph_ids:
        return None
    return {
        "workspaceId": workspace_id,
        "ontologyId": ontology_id,
        "graphModelId": next(iter(graph_ids)),
    }


def resolve_public_artifact_config(
    workspace_id: str, tenant: str, configured: dict[str, str]
) -> dict[str, str]:
    """Resolve public data pointers from target metadata only; never follow old env URLs."""
    headers = fabric_headers(tenant)
    items = _workspace_artifacts(workspace_id, headers)
    same_scope = (
        _public_config_value(configured, "RAYFIN_PUBLIC_WORKSPACE_ID").casefold() == workspace_id.casefold()
        and _public_config_value(configured, "RAYFIN_PUBLIC_TENANT_ID").casefold() == tenant.casefold()
    )
    hints = configured if same_scope else {}

    def select(kind: str, name_key: str, convention: str, *, required: bool = False) -> dict[str, Any] | None:
        candidates = [item for item in items if item["type"] == kind]
        name = _public_config_value(hints, name_key)
        exact = [item for item in candidates if item["displayName"] == name] if name else []
        matches = exact or [item for item in candidates if re.fullmatch(convention, str(item["displayName"]))]
        if len(matches) > 1:
            raise DeployError(
                f"Ambiguous target-workspace {kind} candidates; set {name_key} to a unique "
                "verified name in this workspace before deploying."
            )
        if not matches and required:
            raise DeployError(
                f"No verified {kind} matches {name_key} or the RTI naming convention in the target workspace."
            )
        return matches[0] if matches else None

    eventhouse = select(
        "Eventhouse",
        "RAYFIN_PUBLIC_EVENTHOUSE_NAME",
        rf"RTI_Demo_Eventhouse(?:_{RTI_ARTIFACT_SUFFIX_RE})?",
        required=True,
    )
    assert eventhouse is not None
    databases = []
    for item in items:
        if item["type"] != "KQLDatabase":
            continue
        detail = fabric_get(f"workspaces/{workspace_id}/kqlDatabases/{quote(str(item['id']), safe='')}", headers)
        if (
            detail.get("id") != item["id"]
            or (detail.get("workspaceId") and str(detail["workspaceId"]).casefold() != workspace_id.casefold())
            or not isinstance(detail.get("properties"), dict)
        ):
            raise DeployError("KQL database metadata does not match its target-workspace identity.")
        if str(detail["properties"].get("parentEventhouseItemId", "")).casefold() == str(eventhouse["id"]).casefold():
            databases.append(detail)
    configured_database = _public_config_value(hints, "RAYFIN_PUBLIC_KQL_DATABASE")
    named_databases = [item for item in databases if item.get("displayName") == configured_database]
    matches = named_databases or databases
    if len(matches) != 1:
        raise DeployError(
            "Expected one KQL database with a verified parentEventhouseItemId matching the selected "
            "Eventhouse; if multiple exist, set RAYFIN_PUBLIC_KQL_DATABASE to a unique associated database name."
        )
    database = matches[0]
    cluster_uri = str(database["properties"].get("queryServiceUri") or "")
    uri = urlparse(cluster_uri)
    if (
        not database.get("displayName") or uri.scheme != "https" or not uri.hostname
        or uri.username or uri.password or uri.query or uri.fragment or uri.path not in ("", "/")
    ):
        raise DeployError("The target Eventhouse's KQL database has no valid HTTPS queryServiceUri or display name.")

    graphql = select("GraphQLApi", "RAYFIN_PUBLIC_STID_GRAPHQL_NAME", r"Hydro_STID_API")
    pipeline = select("DataPipeline", "RAYFIN_PUBLIC_STREAM_PIPELINE_NAME", r"02_Pipe_Stream")
    notebook = select("Notebook", "RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_NAME", r"RTI_011_seed_sql_wire_graphql_agent")
    lakehouse = select(
        "Lakehouse",
        "RAYFIN_PUBLIC_LAKEHOUSE_NAME",
        rf"Energy_IQ_LakehouseRTI(?:_{RTI_ARTIFACT_SUFFIX_RE})?",
    )
    dashboard = select(
        "KQLDashboard",
        "RAYFIN_PUBLIC_KQL_DASHBOARD_NAME",
        rf"RTI_Demo_OPCUA_TelemetryStats(?:_{RTI_ARTIFACT_SUFFIX_RE})?",
    )
    values = {
        "RAYFIN_PUBLIC_EVENTHOUSE_NAME": str(eventhouse["displayName"]),
        "RAYFIN_PUBLIC_EVENTHOUSE_ID": str(eventhouse["id"]),
        "RAYFIN_PUBLIC_KQL_DATABASE": str(database["displayName"]),
        "RAYFIN_PUBLIC_KQL_DATABASE_ID": str(database["id"]),
        "RAYFIN_PUBLIC_KQL_CLUSTER_URI": cluster_uri.rstrip("/"),
        "RAYFIN_PUBLIC_STID_GRAPHQL_NAME": str(graphql["displayName"]) if graphql else "",
        "RAYFIN_PUBLIC_STID_GRAPHQL_ID": str(graphql["id"]) if graphql else "",
        "RAYFIN_PUBLIC_STID_GRAPHQL_URL": f"{FABRIC_BASE}/workspaces/{workspace_id}/graphqlapis/{graphql['id']}/graphql" if graphql else "",
        "RAYFIN_PUBLIC_LAKEHOUSE_SQL_ENDPOINT": "",
        "RAYFIN_PUBLIC_ONTOLOGY_NAME": "",
        "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING": "",
    }
    for prefix, item, default_name in [
        ("STREAM_PIPELINE", pipeline, "02_Pipe_Stream"),
        ("POSTSEED_NOTEBOOK", notebook, "RTI_011_seed_sql_wire_graphql_agent"),
        ("LAKEHOUSE", lakehouse, ""), ("KQL_DASHBOARD", dashboard, ""),
    ]:
        values[f"RAYFIN_PUBLIC_{prefix}_NAME"] = str(item["displayName"]) if item else default_name
        values[f"RAYFIN_PUBLIC_{prefix}_ID"] = str(item["id"]) if item else ""

    ontology_name = _public_config_value(hints, "RAYFIN_PUBLIC_ONTOLOGY_NAME")
    ontologies = [item for item in items if item["type"] == "Ontology"]
    named_ontologies = (
        [item for item in ontologies if item["displayName"] == ontology_name]
        if ontology_name else []
    )
    selected_ontologies = named_ontologies or ontologies
    if len(selected_ontologies) > 1:
        raise DeployError(
            "The target workspace contains multiple Ontologies; set RAYFIN_PUBLIC_ONTOLOGY_NAME "
            "to the intended generation-2 item."
        )
    selected_ontology = selected_ontologies[0] if selected_ontologies else None
    lineage_binding = (
        _lineage_graph_binding(workspace_id, selected_ontology, items, headers)
        if selected_ontology else None
    )
    if selected_ontology:
        values["RAYFIN_PUBLIC_ONTOLOGY_NAME"] = str(selected_ontology["displayName"])
    if lineage_binding:
        values["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"] = json.dumps(
            lineage_binding, separators=(",", ":")
        )

    binding_text = _public_config_value(configured, "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING")
    if binding_text and not lineage_binding:
        try:
            binding = json.loads(binding_text)
        except ValueError as exc:
            raise DeployError("The operator-provided Ontology graph binding is invalid JSON.") from exc
        by_id = {str(item["id"]): item for item in items}
        if (
            not isinstance(binding, dict) or binding.get("workspaceId") != workspace_id
            or by_id.get(str(binding.get("ontologyId")), {}).get("type") != "Ontology"
            or by_id.get(str(binding.get("graphModelId")), {}).get("type") != "GraphModel"
        ):
            raise DeployError(
                "The operator-provided Ontology graph binding does not match the target workspace's "
                "Ontology/GraphModel identities. Clear it or provide a verified mapping; graph ownership is never guessed."
            )
        values["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"] = binding_text
    return values


def _rebind_public_env(text: str, values: dict[str, str]) -> str:
    def line_for(key: str, value: str) -> str:
        if key == "RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING":
            binding = _canonical_graph_binding(value)
            return f"{key}='{binding}'" if binding else f"{key}="
        encoded = json.dumps(value, ensure_ascii=False) if re.search(r'''[\s#'"\\]''', value) else value
        return f"{key}={encoded}"

    remaining = dict(values)
    lines = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if "=" in line and key in values:
            line = line_for(key, values[key])
            remaining.pop(key, None)
        lines.append(line)
    for key, value in remaining.items():
        lines.append(line_for(key, value))
    return "\n".join(lines) + "\n"


def prepare_rayfin_env(
    tenant: str,
    workspace_id: str,
    workspace_name: str,
    capacity_id: str,
    client_id: str | None,
) -> bool:
    values, deployment = current_rayfin_target()
    public_artifacts = resolve_public_artifact_config(workspace_id, tenant, values)
    same_target = deployment and all(
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
    target_matches = bool(
        same_target and rayfin_api_targets_capacity(values, deployment, capacity_id)
    )
    if same_target and not target_matches:
        print(
            "Saved Rayfin API URL targets a previous Fabric capacity; rotating state "
            "before reprovisioning.",
            flush=True,
        )
    if target_matches:
        item_id = str(deployment.get("fabricItemId") or "")
        if fabric_item_exists(workspace_id, item_id, tenant):
            env_path = RAYFIN_DIR / ".env"
            env_path.write_text(
                _rebind_public_env(env_path.read_text(encoding="utf-8"), public_artifacts),
                encoding="utf-8", newline="\n",
            )
            print("Rebound public artifact configuration from verified target-workspace metadata.", flush=True)
            print("Existing Rayfin state already targets this tenant/workspace; reusing it.", flush=True)
            return True
        print(f"Saved Fabric AppBackend {item_id or '(missing)'} no longer exists; resetting state.", flush=True)

    backup_root = Path(tempfile.gettempdir()) / "fabric-demo-rayfin-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-")
    backup_dir = Path(tempfile.mkdtemp(prefix=timestamp, dir=backup_root))
    for name in (".env", ".env.local", ".deployments.json"):
        source = RAYFIN_DIR / name
        if source.exists():
            shutil.move(source, backup_dir / name)
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
    (RAYFIN_DIR / ".env").write_text(_rebind_public_env(template, public_artifacts), encoding="utf-8", newline="\n")
    print("Generated fresh rayfin/.env for the target workspace.", flush=True)
    return False


def validate_fabric_app(workspace_id: str, tenant: str) -> str:
    """Fail unless the expected Fabric AppBackend exists in the target workspace."""
    _, deployment = current_rayfin_target()
    item_id = str((deployment or {}).get("fabricItemId") or "")
    if not GUID_RE.fullmatch(item_id):
        raise DeployError("Rayfin deployment state does not contain a valid Fabric AppBackend item id.")
    item = fabric_get(f"workspaces/{workspace_id}/items/{item_id}", fabric_headers(tenant))
    if item.get("type") != "AppBackend" or str(item.get("workspaceId")) != workspace_id:
        raise DeployError(
            f"Fabric item validation failed for {item_id}: expected AppBackend in workspace {workspace_id}."
        )
    print(f"Validated Fabric AppBackend {item_id}.", flush=True)
    return item_id


def validate_entra_live_auth(client_id: str, hosting_url: str) -> None:
    """Validate every Entra runtime contract required for a usable deployment."""
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
            + f"\nFix: Entra admin center > App registrations > {APP_DISPLAY_NAME} > "
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
        principal_missing = missing - principal_scopes
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
            "Every required scope needs tenant-wide consent or consent for the current user.\n"
            f"Fix: Entra admin center > App registrations > {APP_DISPLAY_NAME} > "
            "API permissions > Grant admin consent for <tenant>. A disabled button means the "
            "signed-in administrator lacks a consent-granting directory role. Alternatively, if "
            "tenant policy allows user consent, sign in to the deployed app as the intended user "
            "and accept the prompt, then rerun verification."
        )
    if principal_fallbacks:
        print(
            "WARNING: live-auth consent is current-user only for resources "
            f"{', '.join(principal_fallbacks)}. Consent covers this operator, but an enterprise "
            "rollout requires Privileged Role Administrator / Global Administrator to grant "
            "tenant-wide admin consent (AllPrincipals).",
            flush=True,
        )
    print(f"Validated all Entra live-auth contracts for SPA {client_id}.", flush=True)


def validate_entra_live_auth_with_reauth(
    client_id: str,
    hosting_url: str,
    tenant: str,
) -> None:
    """Retry final Entra contract validation after a stale-token challenge."""
    run_with_azure_cli_reauthentication(
        tenant,
        "validating browser sign-in readiness",
        lambda: validate_entra_live_auth(client_id, hosting_url),
    )


def rayfin_environment(tenant: str) -> dict[str, str]:
    """Pass the validated Azure CLI identity only to each Rayfin child process."""
    token = fabric_headers(tenant)["Authorization"].removeprefix("Bearer ").strip()
    if not token:
        raise DeployError("Azure CLI returned an empty Fabric token; Rayfin deployment stopped.")
    return {**os.environ, "RAYFIN_TOKEN": token, "RAYFIN_TENANT_ID": tenant}


def ensure_rayfin_login(tenant: str) -> None:
    status = run_stream(
        rayfin24("login", "status"), cwd=APP_DIR, env=rayfin_environment(tenant),
    )
    if "signed in (ambient token via rayfin_token)" not in status.casefold():
        raise DeployError("Rayfin did not accept the tenant-scoped Azure CLI token.")
    print("Rayfin is using the tenant-scoped Azure CLI identity; its separate MSAL cache is not required.", flush=True)


def export_frontend_env() -> None:
    run_stream(
        node24_script(APP_DIR / "scripts" / "export-env.mjs"),
        cwd=APP_DIR,
    )


def run_rayfin_deployment(command: list[str], tenant: str) -> str:
    try:
        return run_stream(command, cwd=APP_DIR, env=rayfin_environment(tenant))
    finally:
        # Rayfin also rewrites .env after the prebuild hook and on failed up runs.
        # Regenerate through the repository producer; never patch generated files.
        export_frontend_env()


def current_git_push_target() -> tuple[str, str]:
    """Return a non-main branch and its matching origin upstream."""
    branch = run_capture(command_argv("git", "branch", "--show-current"), cwd=REPO_ROOT)
    if not branch or branch == "main":
        raise DeployError("Automatic config persistence refuses to commit or push the main branch.")
    upstream = run_capture(
        command_argv(
            "git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
        ),
        cwd=REPO_ROOT,
    )
    if upstream != f"origin/{branch}":
        raise DeployError(
            f"Current branch '{branch}' must track 'origin/{branch}' before deployment; "
            f"found '{upstream or '(none)'}'."
        )
    return branch, upstream


def validate_git_push_ready() -> None:
    """Ensure automatic config persistence cannot absorb unrelated tracked work."""
    branch, upstream = current_git_push_target()
    if run_capture(
        command_argv("git", "status", "--porcelain", "--untracked-files=no"), cwd=REPO_ROOT,
    ):
        raise DeployError(
            "Automatic config persistence requires a clean tracked working tree. "
            "Commit tracked changes or clear 'Commit generated hosting origin'."
        )
    run_stream(command_argv("git", "fetch", "origin"), cwd=REPO_ROOT)
    counts = run_capture(
        command_argv("git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"),
        cwd=REPO_ROOT,
    ).split()
    if len(counts) != 2:
        raise DeployError(f"Could not determine divergence from {upstream}.")
    if counts[0] != "0":
        raise DeployError(f"Local {branch} has unpushed commits. Push or reconcile them before deploying.")
    if counts[1] != "0":
        run_stream(command_argv("git", "merge", "--ff-only", upstream), cwd=REPO_ROOT)


def persist_generated_origin(workspace_name: str) -> None:
    branch, upstream = current_git_push_target()
    config = APP_DIR / "rayfin" / "rayfin.yml"
    relative = config.relative_to(REPO_ROOT).as_posix()
    if not run_capture(command_argv("git", "diff", "--", relative), cwd=REPO_ROOT):
        print("Rayfin redirect configuration was already current; no Git commit needed.", flush=True)
        return
    run_stream(command_argv("git", "fetch", "origin"), cwd=REPO_ROOT)
    counts = run_capture(
        command_argv("git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"),
        cwd=REPO_ROOT,
    ).split()
    if len(counts) != 2 or counts[1] != "0":
        raise DeployError(f"{upstream} changed during deployment. Merge it, then rerun deploy.")
    run_stream(command_argv("git", "add", relative), cwd=REPO_ROOT)
    run_stream(
        command_argv(
            "git", "commit", "-m", f"deploy: register {workspace_name} app origin",
            "-m", "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        ),
        cwd=REPO_ROOT,
    )
    run_stream(command_argv("git", "push", "origin", branch), cwd=REPO_ROOT)


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
    return run_with_azure_cli_reauthentication(
        tenant,
        "reading the existing SPA redirects",
        lambda: read_entra_spa_redirects(client_id),
    )


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
    """Rebuild rayfin.yml from the caller's current Entra snapshot plus localhost."""
    config, lines, key_index, end_index, key_indent, newline = _rayfin_redirect_block()
    # Local rayfin.yml origins may belong to stale deployments; Entra is authoritative.
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


def validate_spa_redirect_preservation(
    client_id: str,
    expected: list[str],
    tenant: str,
) -> None:
    """Fail if any redirect URI captured/configured before deployment disappeared."""
    current = set(read_entra_spa_redirects_with_reauth(client_id, tenant))
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


def provision_foundry_agents(tenant: str, workspace_id: str) -> None:
    run_stream(node24_script(APP_DIR / "scripts" / "validate-env.mjs"), cwd=APP_DIR)
    foundry_spec = importlib.util.spec_from_file_location(
        "provision_foundry_agents", SCRIPT_DIR / "provision_foundry_agents.py"
    )
    if not foundry_spec or not foundry_spec.loader:
        raise DeployError("Foundry provisioning module is unavailable.")
    foundry = importlib.util.module_from_spec(foundry_spec)
    foundry_spec.loader.exec_module(foundry)
    foundry.provision(sys.modules[__name__], tenant, workspace_id)


def deploy(args: argparse.Namespace) -> None:
    print("[1/8] Checking Azure tenant and Fabric workspace", flush=True)
    if args.push_config:
        validate_git_push_ready()
    ensure_azure_tenant(args.tenant)
    workspace_id, workspace_name, capacity_id = resolve_workspace(args.workspace, args.tenant)
    print(f"Target workspace: {workspace_name} ({workspace_id})", flush=True)

    print("[2/8] Resolving the tenant SPA app registration", flush=True)
    client_id = resolve_spa(args.client_id, args.tenant)
    if not client_id:
        raise DeployError(
            "A usable Entra SPA Application (client) ID is required. Deployment stopped before "
            "changing Rayfin state so the app cannot be published with broken browser sign-in. "
            f"Ask an Entra administrator to create or identify '{APP_DISPLAY_NAME}', "
            "then retry with --client-id <guid>."
        )

    # Capture both configuration sources BEFORE any Rayfin command can modify Entra.
    # A shared SPA may already serve several Fabric webapps, so losing even one existing
    # redirect URI is a deployment failure.
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

    write_rayfin_redirects(
        _unique_redirect_uris(original_entra_redirects, ["http://localhost:5173"])
    )

    print("[3/8] Resetting local Rayfin deployment state", flush=True)
    reuse_deployment = prepare_rayfin_env(
        args.tenant, workspace_id, workspace_name, capacity_id, client_id
    )
    ensure_deploy_dependencies()
    provision_foundry_agents(args.tenant, workspace_id)

    print("[4/8] Authenticating Rayfin to the target tenant", flush=True)
    ensure_rayfin_login(args.tenant)

    print("[5/8] Provisioning backend, database schema, and static app", flush=True)
    if reuse_deployment:
        print("Existing backend is healthy; deploying static content without reprovisioning it.", flush=True)
        command = rayfin24("up", "staticapp", "deploy")
    else:
        command = rayfin24("up", "--workspace-id", workspace_id, "--yes")
    output = run_rayfin_deployment(command, args.tenant)
    urls = HOSTING_URL_RE.findall(output)
    if not urls:
        raise DeployError("Rayfin completed without reporting a Fabric hosting URL.")
    hosting_url = urls[-1]

    print("[6/8] Adding the current app URL to the preserved redirect configuration", flush=True)
    rayfin_redirects = write_rayfin_redirects(
        _unique_redirect_uris(
            original_entra_redirects,
            [hosting_url],
            ["http://localhost:5173"],
        )
    )
    # Only this tenant's own URIs can be asserted against Entra; teammate origins are not registered here.
    required_entra_redirects = _unique_redirect_uris(original_entra_redirects, [hosting_url])
    print(
        f"Rayfin redirect configuration now contains {len(rayfin_redirects)} URI(s).",
        flush=True,
    )
    print(
        "Reapplying backend runtime settings and database configuration so managed-service "
        "restarts cannot retain stale CORS state.",
        flush=True,
    )
    run_rayfin_deployment(
        rayfin24(
            "up", "--workspace-id", workspace_id,
            "--exclude-services", "staticHosting", "--yes",
        ),
        args.tenant,
    )

    print("[7/8] Setting up browser sign-in (redirect, permissions, and consent)", flush=True)
    if client_id:
        try:
            run_with_azure_cli_reauthentication(
                args.tenant,
                "configuring browser sign-in",
                lambda: run_stream(
                    node24_script(APP_DIR / "scripts" / "setup-live-auth.mjs"),
                    cwd=APP_DIR,
                    env={**os.environ, "FABRIC_DEMO_AUTH_OWNER": "orchestrator"},
                ),
            )
        except DeployError as exc:
            warn_live_auth(f"Automated SPA configuration did not complete ({exc}).")
    else:
        warn_live_auth(
            "SPA configuration was skipped because no usable Application (client) ID is available."
        )

    print("[8/8] Checking the hosted page, Fabric backend, and sign-in readiness", flush=True)
    item_id = validate_fabric_app(workspace_id, args.tenant)
    hosting_state = validate_hosted_page(hosting_url, workspace_id, item_id, args.tenant)
    api_url = validate_rayfin_endpoint_contract(capacity_id, workspace_id, item_id)
    publishable_key = validate_rayfin_publishable_key()
    validate_appbackend_cors(api_url, hosting_url, publishable_key)
    if client_id:
        # Redirect preservation is a hard safety contract: never report success if a URI
        # that existed in Entra before deployment disappeared.
        validate_spa_redirect_preservation(client_id, required_entra_redirects, args.tenant)
        validate_entra_live_auth_with_reauth(client_id, hosting_url, args.tenant)
    else:
        warn_live_auth(
            f"Entra validation was skipped. After an administrator creates the SPA, register "
            f"{hosting_url} as its redirect URI and run npm run setup-live-auth."
        )
    print(f"DEPLOYED_APP_URL={hosting_url}", flush=True)

    if args.push_config:
        branch, _ = current_git_push_target()
        print(f"Persisting Rayfin redirect configuration to origin/{branch}...", flush=True)
        persist_generated_origin(workspace_name)
    print(
        f"SUCCESS: Hydro Operations deployment checks passed at {hosting_url} "
        f"(hosting: {hosting_state}; interactive authenticated app acceptance not performed).",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="Target Entra tenant GUID or domain.")
    parser.add_argument("--workspace", required=True, help="Target Fabric workspace GUID or name.")
    parser.add_argument("--client-id", help="Existing SPA app client id; auto-resolved/created when omitted.")
    parser.add_argument(
        "--push-config",
        action="store_true",
        help="Commit and push Rayfin's generated hosting origin to the current tracked feature branch.",
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