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

Git credentials: by default the script creates a fresh Fabric GitHub connection
named FabricOntologyDemo_<UTC timestamp> from a Personal Access Token that it
asks for at a hidden prompt (never a CLI flag, so it stays out of shell history),
and deletes that connection again on exit. Pass --connection-id to reuse an
existing connection instead; then no PAT is requested.

Examples:
    az login
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
        self._credential = AzureCliCredential(tenant_id=tenant_id)
        self._session = requests.Session()

    def _token(self) -> str:
        return self._credential.get_token(FABRIC_SCOPE).token

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", help="Tenant id or domain to sign in against.")
    parser.add_argument("--workspace", help="Workspace GUID or display name.")
    parser.add_argument("--connection-id", help="Reuse this existing Fabric GitHub connection id instead of creating one from a PAT.")
    parser.add_argument("--owner", help="GitHub owner/org name (optional if --repository is 'owner/repo').")
    parser.add_argument("--repository", help="GitHub repository, as 'owner/repo' or a bare repo name.")
    parser.add_argument("--branch", help="Branch to sync from (default: main).")
    parser.add_argument("--directory", help="Repo directory that maps to the workspace root (default: /).")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument("--keep-connected", action="store_true", help="Do not disconnect after syncing.")
    args = parser.parse_args()

    # Interactively collect anything not supplied on the command line.
    print("Fabric workspace <- GitHub sync. Provide the following (press Enter to accept a [default]):")
    args.tenant = prompt_required("Tenant id or domain", args.tenant)
    args.workspace = prompt_required("Workspace GUID or name", args.workspace)
    # Accept either "owner/repo" in one field or a bare repo name; only ask for
    # the owner separately when it can't be derived from the repository value.
    args.repository = prompt_required("GitHub repository (owner/repo or repo)", args.repository)
    if not args.owner and "/" in args.repository:
        args.owner, args.repository = (part.strip() for part in args.repository.split("/", 1))
    args.owner = prompt_required("GitHub owner/org", args.owner)
    args.branch = prompt_default("Branch", args.branch, "main")
    args.directory = prompt_default("Repo directory (workspace root)", args.directory, "/")

    # A GitHub PAT is only needed when we have to create a connection. If the user
    # passed an existing --connection-id we reuse it and never ask for a secret.
    pat = ""
    if not (args.connection_id and args.connection_id.strip()):
        print("  No --connection-id given; a new GitHub connection will be created from a PAT.")
        while not pat:
            pat = getpass.getpass("  GitHub Personal Access Token (input hidden): ").strip()

    try:
        fab = Fabric(args.tenant)
        ws_id, ws_name = fab.resolve_workspace_id(args.workspace)
    except ClientAuthenticationError:
        print("Authentication failed. Run `az login` (optionally --tenant) first.", file=sys.stderr)
        return 1

    if not args.yes and not confirm(ws_name, ws_id):
        print("Aborted.")
        return 0

    base = f"{FABRIC_BASE}/workspaces/{ws_id}/git"
    repo_url = f"https://github.com/{args.owner}/{args.repository}"
    created_connection_id: str | None = None

    try:
        # 0. Resolve the connection: reuse --connection-id, or create a fresh one.
        if args.connection_id and args.connection_id.strip():
            connection_id = args.connection_id.strip()
            print(f"Reusing existing connection {connection_id}.")
        else:
            conn_name = f"FabricOntologyDemo_{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
            print(f"Creating GitHub connection '{conn_name}' for {repo_url}...")
            connection_id = fab.create_github_connection(conn_name, repo_url, pat)
            created_connection_id = connection_id
            print(f"  created connection {connection_id}.")

        # 1. Ensure a clean connection: disconnect any stale link first (ignore if none).
        state = fab.request("GET", f"{base}/connection")
        if state.status_code == 200 and state.json().get("gitConnectionState") != "NotConnected":
            print("Workspace already Git-connected; disconnecting first for a clean run...")
            fab.request("POST", f"{base}/disconnect")

        # 2. Connect to the GitHub branch via the configured connection.
        print(f"Connecting '{ws_name}' -> {args.owner}/{args.repository}@{args.branch} ({args.directory})...")
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
            print(f"connect failed: HTTP {r.status_code} {r.text}", file=sys.stderr)
            return 1
        print("  connected.")

        # 3. Initialize the connection, preferring the remote (Git) side.
        print("Initializing connection (PreferRemote)...")
        r = fab.request("POST", f"{base}/initializeConnection", json={"initializationStrategy": "PreferRemote"})
        r = fab.poll_lro(r)
        if r.status_code not in (200, 201):
            print(f"initializeConnection failed: HTTP {r.status_code} {r.text}", file=sys.stderr)
            return 1
        init = r.json() if r.content else {}
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

        # 5. Disconnect unless asked to stay connected.
        if not args.keep_connected:
            print("Disconnecting from Git...")
            r = fab.request("POST", f"{base}/disconnect")
            if r.status_code not in (200, 204):
                print(f"disconnect returned HTTP {r.status_code} {r.text}", file=sys.stderr)
            else:
                print("  disconnected.")
    finally:
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
