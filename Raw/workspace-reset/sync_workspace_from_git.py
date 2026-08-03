#!/usr/bin/env python3
"""Populate a Microsoft Fabric workspace from a GitHub branch, then disconnect.

Runs the full, clean Git-integration cycle against a workspace:
    1. connect the workspace to the GitHub repo/branch (via a configured connection),
    2. initialize the connection (PreferRemote),
    3. updateFromGit  -> imports every supported item (and its folder structure)
       from the branch into the workspace,
    4. disconnect     -> leaves the workspace populated but NOT Git-linked.

Because Git integration mirrors folders, items land in same-named workspace
folders (e.g. Notebooks/, Orchestrator_Pipelines/) -- reusing any empty folders
that already exist. This is why it also resolves the "empty folder that won't
delete" situation: the folder is reused and filled instead of removed.

Authentication uses your CURRENT sign-in context -- the Azure CLI session. Run
`az login` first. Connect/sync/disconnect require the workspace Admin role.

The script is interactive: run it with no arguments and it prompts for every
required value (tenant, workspace, owner, repository) and offers defaults for
branch (main) and directory (/). Any value passed on the command line is used
as-is and not prompted for.

Every value can also come from an environment variable, so a run can be fully
driven from the environment (order: CLI flag > env var > prompt):
    FABRIC_TENANT         tenant id or domain
    FABRIC_WORKSPACE      workspace GUID or display name
    FABRIC_REPOSITORY     GitHub repo as 'owner/repo' (or bare repo name)
    FABRIC_OWNER          GitHub owner/org (optional if FABRIC_REPOSITORY has it)
    FABRIC_BRANCH         branch (default main)
    FABRIC_DIRECTORY      repo directory mapped to the workspace root (default /)
    FABRIC_CONNECTION_ID  reuse an existing connection instead of creating one
    FABRIC_GIT_PAT        GitHub PAT (also accepts GITHUB_PAT)

Git credentials: by default the script creates a fresh Fabric GitHub connection
named FabricOntologyDemo_<UTC timestamp> from the PAT (FABRIC_GIT_PAT/GITHUB_PAT
env var, else a hidden prompt -- never a CLI flag, so it stays out of shell
history), and deletes that connection again on exit. Pass --connection-id (or
FABRIC_CONNECTION_ID) to reuse an existing connection; then no PAT is requested.

Examples:
    az login
    # fully env-driven (set these first, PAT hidden and out of history):
    #   $env:FABRIC_TENANT = "<tenant-guid>"
    #   $env:FABRIC_WORKSPACE = "<workspace-guid-or-name>"
    #   $env:FABRIC_REPOSITORY = "dibakardharchoudhury/FabricOntologyHydro"
    #   $env:FABRIC_GIT_PAT = (Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText)
    python sync_workspace_from_git.py --yes

    # interactive -- asks for everything, creates + cleans up a connection:
    python sync_workspace_from_git.py

    # reuse an existing connection (no PAT prompt):
    python sync_workspace_from_git.py \
        --tenant <tenant-guid> \
        --workspace <workspace-guid-or-name> \
        --connection-id <existing-connection-guid> \
        --owner dibakardharchoudhury \
        --repository FabricOntologyHydro \
        --branch main --directory /
    # add --yes to skip the confirmation prompt
    # add --keep-connected to skip disconnect (keeps the created connection too)
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

try:
    import requests
    from azure.core.exceptions import ClientAuthenticationError
    from azure.identity import AzureCliCredential
except ImportError as exc:  # pragma: no cover - dependency guard
    sys.exit(
        f"Missing dependency: {exc.name}. Install requirements first:\n"
        "    python -m pip install -r requirements.txt"
    )

FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class Fabric:
    """Thin Fabric REST client that authenticates with the current az login context."""

    def __init__(self, tenant_id: str) -> None:
        # process_timeout is generous because az.cmd cold-starts (and AV scanning
        # the spawned python) can easily exceed the 10s azure-identity default.
        self._credential = AzureCliCredential(tenant_id=tenant_id, process_timeout=60)
        self._session = requests.Session()
        self._cached_token: str | None = None
        self._token_expiry: float = 0.0

    def _token(self) -> str:
        # Cache the token and refresh only near expiry, so long LRO polling doesn't
        # invoke the (slow) Azure CLI on every single request.
        now = time.time()
        if not self._cached_token or now >= self._token_expiry - 300:
            access = self._credential.get_token(FABRIC_SCOPE)
            self._cached_token = access.token
            self._token_expiry = float(access.expires_on)
        return self._cached_token

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token()}"
        for _ in range(6):
            resp = self._session.request(method, url, headers=headers, timeout=120, **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "10"))
                print(f"  throttled (429); waiting {wait}s...")
                time.sleep(wait)
                continue
            return resp
        return resp

    def create_github_connection(self, display_name: str, repo_url: str, pat: str) -> str:
        """Create a ShareableCloud GitHubSourceControl connection from a PAT; return its id."""
        body = {
            "connectivityType": "ShareableCloud",
            "displayName": display_name,
            "connectionDetails": {
                "type": "GitHubSourceControl",
                "creationMethod": "GitHubSourceControl.Contents",
                "parameters": [
                    {"dataType": "Text", "name": "url", "value": repo_url},
                ],
            },
            "privacyLevel": "Private",
            "credentialDetails": {
                "singleSignOnType": "None",
                "connectionEncryption": "NotEncrypted",
                "skipTestConnection": False,
                "credentials": {"credentialType": "Key", "key": pat},
            },
        }
        resp = self.request("POST", f"{FABRIC_BASE}/connections", json=body)
        if resp.status_code not in (200, 201):
            raise SystemExit(f"Failed to create GitHub connection: HTTP {resp.status_code} {resp.text}")
        return resp.json()["id"]

    def delete_connection(self, connection_id: str) -> None:
        self.request("DELETE", f"{FABRIC_BASE}/connections/{connection_id}")

    def list_connections(self) -> list[dict[str, Any]]:
        """Return every connection visible to the caller, following pagination."""
        out: list[dict[str, Any]] = []
        url: str | None = f"{FABRIC_BASE}/connections"
        while url:
            resp = self.request("GET", url)
            if resp.status_code != 200:
                break
            data = resp.json()
            out.extend(data.get("value", []))
            token = data.get("continuationToken")
            url = f"{FABRIC_BASE}/connections?continuationToken={token}" if token else None
        return out

    def find_github_connections(self, repo_url: str) -> list[str]:
        """Ids of existing GitHubSourceControl connections pointing at repo_url.

        Newest-looking first (the FabricOntologyDemo_<timestamp> names sort so the
        most recent one is tried before older / manually-named connections).
        """
        want = repo_url.rstrip("/").removesuffix(".git").lower()
        matches: list[dict[str, Any]] = []
        for conn in self.list_connections():
            details = conn.get("connectionDetails") or {}
            if (details.get("type") or "").lower() != "githubsourcecontrol":
                continue
            path = (details.get("path") or "").rstrip("/").removesuffix(".git").lower()
            if path == want:
                matches.append(conn)
        matches.sort(key=lambda c: c.get("displayName", ""), reverse=True)
        return [c["id"] for c in matches if c.get("id")]

    def resolve_workspace_id(self, workspace: str) -> tuple[str, str]:
        if GUID_RE.match(workspace):
            resp = self.request("GET", f"{FABRIC_BASE}/workspaces/{workspace}")
            if resp.status_code == 200:
                return workspace, resp.json().get("displayName", workspace)
            raise SystemExit(f"Workspace '{workspace}' not found or not accessible.")
        resp = self.request("GET", f"{FABRIC_BASE}/workspaces")
        resp.raise_for_status()
        matches = [
            w for w in resp.json().get("value", [])
            if w.get("displayName", "").lower() == workspace.lower()
        ]
        if not matches:
            raise SystemExit(f"No workspace named '{workspace}' found.")
        if len(matches) > 1:
            ids = ", ".join(w["id"] for w in matches)
            raise SystemExit(f"Multiple workspaces named '{workspace}': {ids}. Use the GUID.")
        return matches[0]["id"], matches[0]["displayName"]

    def poll_lro(self, resp: requests.Response) -> requests.Response:
        """Follow a 202 long-running-operation to completion; return the final response."""
        if resp.status_code != 202:
            return resp
        location = resp.headers.get("Location") or resp.headers.get("Operation-Location")
        retry = int(resp.headers.get("Retry-After", "5"))
        if not location:
            return resp
        while True:
            time.sleep(retry)
            status = self.request("GET", location)
            if status.status_code not in (200, 202):
                return status
            body = status.json() if status.content else {}
            state = body.get("status", "")
            print(f"  operation status: {state or status.status_code}")
            if state == "Succeeded":
                return status
            if state in ("Failed", "Cancelled"):
                raise SystemExit(f"Git operation {state}: {status.text}")
            retry = int(status.headers.get("Retry-After", str(retry)))


def confirm(name: str, ws_id: str) -> bool:
    print(
        f"\nAbout to OVERWRITE workspace '{name}' ({ws_id}) with the Git branch "
        "contents (updateFromGit / PreferRemote)."
    )
    answer = input(f"Type the workspace name or id to proceed: ").strip()
    return answer.lower() in (name.lower(), ws_id.lower())


def prompt_required(label: str, current: str | None) -> str:
    """Return a non-empty value, asking the user until one is given."""
    value = (current or "").strip()
    while not value:
        value = input(f"  {label}: ").strip()
    return value


def prompt_default(label: str, current: str | None, default: str) -> str:
    """Return the provided value, or ask (offering a default on blank input)."""
    if current and current.strip():
        return current.strip()
    entered = input(f"  {label} [{default}]: ").strip()
    return entered or default


def split_owner_repo(repository: str, owner: str | None) -> tuple[str | None, str]:
    """Accept 'owner/repo', a bare 'repo', or a pasted GitHub URL; return (owner, repo)."""
    value = re.sub(r"^(https?://)?(www\.)?github\.com[/:]", "", repository.strip(), flags=re.IGNORECASE)
    value = re.sub(r"^git@github\.com:", "", value, flags=re.IGNORECASE)
    value = value.removesuffix(".git").strip("/")
    if not owner and "/" in value:
        o, r = value.split("/", 1)
        return o.strip(), r.strip()
    return owner, value


def connect_and_initialize(
    fab: "Fabric", base: str, ws_name: str, args: argparse.Namespace, connection_id: str
) -> dict[str, Any] | None:
    """Disconnect any stale link, connect via connection_id, then initialize.

    Returns the initializeConnection JSON on success, or None if the connection
    could not be used (e.g. its stored PAT is expired/invalid), leaving the
    workspace disconnected so the next candidate can be tried cleanly.
    """
    state = fab.request("GET", f"{base}/connection")
    if state.status_code == 200 and state.json().get("gitConnectionState") != "NotConnected":
        fab.request("POST", f"{base}/disconnect")

    print(f"Connecting '{ws_name}' -> {args.owner}/{args.repository}@{args.branch} ({args.directory}) via {connection_id}...")
    connect_body = {
        "gitProviderDetails": {
            "gitProviderType": "GitHub",
            "ownerName": args.owner,
            "repositoryName": args.repository,
            "branchName": args.branch,
            "directoryName": args.directory,
        },
        "myGitCredentials": {
            "source": "ConfiguredConnection",
            "connectionId": connection_id,
        },
    }
    r = fab.request("POST", f"{base}/connect", json=connect_body)
    if r.status_code not in (200, 201):
        print(f"  connect failed for {connection_id}: HTTP {r.status_code} {r.text}", file=sys.stderr)
        return None
    print("  connected.")

    print("Initializing connection (PreferRemote)...")
    r = fab.request("POST", f"{base}/initializeConnection", json={"initializationStrategy": "PreferRemote"})
    try:
        r = fab.poll_lro(r)
    except SystemExit as exc:
        print(f"  initialize failed: {exc}", file=sys.stderr)
        fab.request("POST", f"{base}/disconnect")
        return None
    if r.status_code not in (200, 201):
        print(f"  initializeConnection failed: HTTP {r.status_code} {r.text}", file=sys.stderr)
        fab.request("POST", f"{base}/disconnect")
        return None
    return r.json() if r.content else {}


def test_connection_flow(
    fab: "Fabric", base: str, ws_name: str, args: argparse.Namespace,
    candidate_ids: list[str], pat: str,
) -> int:
    """Verify GitHub connectivity without importing anything.

    Tries each existing connection (and, if a PAT is supplied, a throwaway one) via
    connect+initialize, then always disconnects and removes any temp connection.
    Returns 0 on success, 3 when nothing works (a PAT is needed), 1 on other errors.
    """
    print(f"Testing GitHub connectivity for workspace '{ws_name}'...")
    repo_url = f"https://github.com/{args.owner}/{args.repository}"
    created_id: str | None = None
    try:
        for cid in candidate_ids:
            print(f"Trying connection {cid}...")
            if connect_and_initialize(fab, base, ws_name, args, cid) is not None:
                print(f"CONNECTION TEST: SUCCESS -- connection {cid} can reach GitHub.")
                return 0
            print(f"  connection {cid} did not work.")
        if pat:
            conn_name = f"FabricOntologyDemoTest_{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
            print(f"Testing the supplied PAT via a temporary connection '{conn_name}'...")
            created_id = fab.create_github_connection(conn_name, repo_url, pat)
            if connect_and_initialize(fab, base, ws_name, args, created_id) is not None:
                print("CONNECTION TEST: SUCCESS -- the supplied PAT can reach GitHub.")
                return 0
            print("CONNECTION TEST: FAILED -- the supplied PAT could not connect to GitHub.")
            return 3
        print("CONNECTION TEST: FAILED -- no existing connection works; a GitHub PAT is required.")
        return 3
    finally:
        state = fab.request("GET", f"{base}/connection")
        if state.status_code == 200 and state.json().get("gitConnectionState") != "NotConnected":
            fab.request("POST", f"{base}/disconnect")
        if created_id:
            fab.delete_connection(created_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", help="Tenant id or domain to sign in against.")
    parser.add_argument("--workspace", help="Workspace GUID or display name.")
    parser.add_argument("--connection-id", help="Connection to use: a Fabric connection GUID to reuse exactly, or 'yes'/'auto' to auto-discover and reuse an existing GitHub connection for this repo (creating a new one from a PAT only if none works). Omit to always create a new one.")
    parser.add_argument("--owner", help="GitHub owner/org name (optional if --repository is 'owner/repo').")
    parser.add_argument("--repository", help="GitHub repository, as 'owner/repo' or a bare repo name.")
    parser.add_argument("--branch", help="Branch to sync from (default: main).")
    parser.add_argument("--directory", help="Repo directory that maps to the workspace root (default: /).")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument("--keep-connected", action="store_true", help="Do not disconnect after syncing.")
    parser.add_argument("--test-connection", action="store_true", help="Only verify GitHub connectivity (connect+initialize+disconnect), import nothing, then report SUCCESS/FAILED.")
    args = parser.parse_args()

    # Resolution order for every value: CLI flag > environment variable > prompt.
    # This lets the whole run be driven from env vars (handy for automation) while
    # still prompting interactively for anything left unset.
    args.tenant = args.tenant or os.environ.get("FABRIC_TENANT")
    args.workspace = args.workspace or os.environ.get("FABRIC_WORKSPACE")
    args.connection_id = args.connection_id or os.environ.get("FABRIC_CONNECTION_ID")
    args.owner = args.owner or os.environ.get("FABRIC_OWNER")
    args.repository = args.repository or os.environ.get("FABRIC_REPOSITORY")
    args.branch = args.branch or os.environ.get("FABRIC_BRANCH")
    args.directory = args.directory or os.environ.get("FABRIC_DIRECTORY")

    # Interactively collect anything still not supplied. Only show the "press Enter"
    # banner when we're actually attached to a terminal; when driven by CLI flags/env
    # vars (e.g. from the web app) every value is already set and nothing prompts.
    if sys.stdin.isatty():
        print("Fabric workspace <- GitHub sync. Provide the following (press Enter to accept a [default]):")
    args.tenant = prompt_required("Tenant id or domain", args.tenant)
    args.workspace = prompt_required("Workspace GUID or name", args.workspace)
    # Accept either "owner/repo" in one field or a bare repo name; only ask for
    # the owner separately when it can't be derived from the repository value.
    args.repository = prompt_required("GitHub repository (owner/repo, repo, or URL)", args.repository)
    args.owner, args.repository = split_owner_repo(args.repository, args.owner)
    args.owner = prompt_required("GitHub owner/org", args.owner)
    args.branch = prompt_default("Branch", args.branch, "main")
    args.directory = prompt_default("Repo directory (workspace root)", args.directory, "/")

    # A GitHub PAT is only needed when we may have to CREATE a connection. Three
    # connection modes, decided by the --connection-id value:
    #   * a GUID           -> reuse exactly that connection (no PAT).
    #   * "yes"/"auto"/etc -> auto-discover an existing connection for this repo and
    #                         reuse it if it works; a PAT is only needed as a fallback
    #                         to create a fresh connection when none works.
    #   * empty            -> always create a new connection from a PAT.
    # The PAT comes from FABRIC_GIT_PAT/GITHUB_PAT (never a CLI flag) or a hidden prompt.
    conn_raw = (args.connection_id or "").strip()
    explicit_conn = bool(conn_raw) and bool(GUID_RE.match(conn_raw))
    reuse_auto = bool(conn_raw) and not explicit_conn

    pat = ""
    if not explicit_conn:
        pat = (os.environ.get("FABRIC_GIT_PAT") or os.environ.get("GITHUB_PAT") or "").strip()
        if pat:
            print("  Using GitHub PAT from environment variable.")
        elif args.test_connection:
            pass  # test mode never prompts; the UI supplies a PAT on retry
        elif reuse_auto:
            # Reuse mode: the PAT is optional -- only used if no existing connection works.
            if sys.stdin.isatty():
                print("  Reuse mode: paste a PAT to allow creating a new connection if no")
                print("  existing one works, or press Enter to try reuse only.")
                pat = getpass.getpass("  GitHub PAT (optional, input hidden): ").strip()
        else:
            print("  No --connection-id and no FABRIC_GIT_PAT/GITHUB_PAT env var;")
            print("  a new GitHub connection will be created from a PAT.")
            while not pat:
                pat = getpass.getpass("  GitHub Personal Access Token (input hidden): ").strip()

    try:
        fab = Fabric(args.tenant)
        ws_id, ws_name = fab.resolve_workspace_id(args.workspace)
    except ClientAuthenticationError:
        print("Authentication failed. Run `az login` (optionally --tenant) first.", file=sys.stderr)
        return 1

    if not args.test_connection and not args.yes and not confirm(ws_name, ws_id):
        print("Aborted.")
        return 0

    base = f"{FABRIC_BASE}/workspaces/{ws_id}/git"
    repo_url = f"https://github.com/{args.owner}/{args.repository}"
    created_connection_id: str | None = None

    # Ordered list of existing connection ids to try before creating a new one.
    candidate_ids: list[str] = []
    if explicit_conn:
        candidate_ids = [conn_raw]
        print(f"Reusing existing connection {conn_raw}.")
    elif reuse_auto:
        candidate_ids = fab.find_github_connections(repo_url)
        if candidate_ids:
            print(f"Found {len(candidate_ids)} existing connection(s) for {repo_url}; will try to reuse.")
        else:
            print(f"No existing connection found for {repo_url}; will create one.")

    # Test-only mode: verify connectivity and report, importing nothing.
    if args.test_connection:
        return test_connection_flow(fab, base, ws_name, args, candidate_ids, pat)

    try:
        # 0-3. Reuse a working connection if we can; otherwise create a fresh one.
        init: dict[str, Any] | None = None
        connection_id: str | None = None
        for cid in candidate_ids:
            init = connect_and_initialize(fab, base, ws_name, args, cid)
            if init is not None:
                connection_id = cid
                print(f"  reusing connection {cid}.")
                break
            print(f"  connection {cid} did not work; trying the next option...")

        if init is None:
            if not pat:
                print(
                    "No reusable connection worked and no PAT is available to create one.\n"
                    "Provide a GitHub PAT (FABRIC_GIT_PAT / GITHUB_PAT) and retry.",
                    file=sys.stderr,
                )
                return 1
            conn_name = f"FabricOntologyDemo_{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
            print(f"Creating GitHub connection '{conn_name}' for {repo_url}...")
            connection_id = fab.create_github_connection(conn_name, repo_url, pat)
            created_connection_id = connection_id
            print(f"  created connection {connection_id}.")
            init = connect_and_initialize(fab, base, ws_name, args, connection_id)
            if init is None:
                print("connect/initialize failed even with a freshly created connection.", file=sys.stderr)
                return 1

        remote_hash = init.get("remoteCommitHash")
        required = init.get("requiredAction", "")
        print(f"  requiredAction={required} remoteCommitHash={remote_hash}")

        # 4. Import everything from Git into the workspace.
        if required == "UpdateFromGit" or remote_hash:
            print("Updating workspace from Git...")
            update_body = {
                "remoteCommitHash": remote_hash,
                "conflictResolution": {
                    "conflictResolutionType": "Workspace",
                    "conflictResolutionPolicy": "PreferRemote",
                },
                "options": {"allowOverrideItems": True},
            }
            r = fab.request("POST", f"{base}/updateFromGit", json=update_body)
            r = fab.poll_lro(r)
            if r.status_code not in (200, 201):
                print(f"updateFromGit failed: HTTP {r.status_code} {r.text}", file=sys.stderr)
                return 1
            print("  update complete.")
        else:
            print("  nothing to update (workspace already matches Git).")
    finally:
        # Always leave the workspace un-linked (unless --keep-connected), even if the
        # sync failed partway -- otherwise a crash would leave it Git-connected. This
        # runs before deleting our connection, since a live link still needs it.
        if not args.keep_connected:
            state = fab.request("GET", f"{base}/connection")
            if state.status_code == 200 and state.json().get("gitConnectionState") != "NotConnected":
                print("Disconnecting from Git...")
                d = fab.request("POST", f"{base}/disconnect")
                print("  disconnected." if d.status_code in (200, 204)
                      else f"  disconnect returned HTTP {d.status_code} {d.text}")
        # Remove the connection we created, unless the workspace stays Git-connected
        # (a live Git connection still needs its credential connection).
        if created_connection_id and not args.keep_connected:
            print(f"Cleaning up connection {created_connection_id}...")
            fab.delete_connection(created_connection_id)

    # 6. Report resulting inventory.
    items = fab.request("GET", f"{FABRIC_BASE}/workspaces/{ws_id}/items")
    folders = fab.request("GET", f"{FABRIC_BASE}/workspaces/{ws_id}/folders?recursive=true")
    n_items = len(items.json().get("value", [])) if items.status_code == 200 else "?"
    n_folders = len(folders.json().get("value", [])) if folders.status_code == 200 else "?"
    print(f"\nDone. Workspace '{ws_name}' now has {n_items} items across {n_folders} folders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
