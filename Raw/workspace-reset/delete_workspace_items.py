#!/usr/bin/env python3
"""Delete EVERY item in a Microsoft Fabric workspace so you can redeploy from scratch.

Authentication uses your CURRENT sign-in context — the Azure CLI session. Run
`az login` first (optionally `az login --tenant <tenant>`); this script asks the
CLI for a Fabric token, so whatever identity you are signed in as is the identity
that performs the deletions.

Both tenant and workspace are REQUIRED — supply them via --tenant/--workspace or
the FABRIC_TENANT/FABRIC_WORKSPACE environment variables (CLI flag > env var). The
script refuses to run if either is missing. This is destructive and effectively
irreversible, so by default it prints what it will delete and asks you to confirm;
pass --yes to skip the prompt or --dry-run to list without deleting anything.

Examples:
    az login
    # env-driven:
    #   $env:FABRIC_TENANT = "<tenant-guid>"; $env:FABRIC_WORKSPACE = "<workspace>"
    python delete_workspace_items.py --dry-run

    python delete_workspace_items.py --tenant contoso.onmicrosoft.com --workspace "Hydro Ops Dev"
    python delete_workspace_items.py --tenant <tenant-guid> --workspace <workspace-guid> --dry-run
    python delete_workspace_items.py --tenant <tenant-guid> --workspace <workspace-guid> --yes
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
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
# Item types that cannot be deleted directly — they are removed automatically when
# their parent (e.g. a Lakehouse or Warehouse) is deleted.
CHILD_ITEM_TYPES = {"SQLEndpoint", "DefaultSemanticModel"}


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
        # Cache the token and refresh only near expiry, so long deletes don't
        # invoke the (slow) Azure CLI on every single request.
        now = time.time()
        if not self._cached_token or now >= self._token_expiry - 300:
            access = self._credential.get_token(FABRIC_SCOPE)
            self._cached_token = access.token
            self._token_expiry = float(access.expires_on)
        return self._cached_token

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        # Fetch a fresh token per request so long deletes don't fail on expiry.
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token()}"
        for attempt in range(6):
            resp = self._session.request(method, url, headers=headers, timeout=60, **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "10"))
                print(f"  throttled (429); waiting {wait}s...")
                time.sleep(wait)
                continue
            return resp
        return resp

    def resolve_workspace_id(self, workspace: str) -> tuple[str, str]:
        """Return (workspace_id, display_name) for a GUID or a display name."""
        if GUID_RE.match(workspace):
            resp = self._request("GET", f"{FABRIC_BASE}/workspaces/{workspace}")
            if resp.status_code == 200:
                return workspace, resp.json().get("displayName", workspace)
            raise SystemExit(
                f"Workspace '{workspace}' not found or not accessible "
                f"(HTTP {resp.status_code}): {resp.text}"
            )

        matches = [
            ws for ws in self._list("workspaces")
            if ws.get("displayName", "").casefold() == workspace.casefold()
        ]
        if not matches:
            raise SystemExit(
                f"No workspace named '{workspace}' is visible to the signed-in user."
            )
        if len(matches) > 1:
            ids = ", ".join(ws["id"] for ws in matches)
            raise SystemExit(
                f"Multiple workspaces named '{workspace}' found ({ids}); "
                "pass the workspace GUID instead."
            )
        return matches[0]["id"], matches[0]["displayName"]

    def _list(self, path: str) -> list[dict[str, Any]]:
        """GET a paginated Fabric collection and return all rows."""
        items: list[dict[str, Any]] = []
        url = f"{FABRIC_BASE}/{path}"
        while url:
            resp = self._request("GET", url)
            if resp.status_code != 200:
                raise SystemExit(
                    f"GET {url} failed (HTTP {resp.status_code}): {resp.text}"
                )
            body = resp.json()
            items.extend(body.get("value", []))
            url = body.get("continuationUri") or ""
        return items

    def list_items(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._list(f"workspaces/{workspace_id}/items")

    def delete_item(self, workspace_id: str, item_id: str) -> requests.Response:
        return self._request(
            "DELETE", f"{FABRIC_BASE}/workspaces/{workspace_id}/items/{item_id}"
        )

    def list_folders(self, workspace_id: str) -> list[dict[str, Any]]:
        # recursive=true returns nested subfolders too, so we can delete deepest-first.
        return self._list(f"workspaces/{workspace_id}/folders?recursive=true")

    def delete_folder(self, workspace_id: str, folder_id: str) -> requests.Response:
        return self._request(
            "DELETE", f"{FABRIC_BASE}/workspaces/{workspace_id}/folders/{folder_id}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete every item in a Fabric workspace using your current az login context.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tenant",
        help="Entra tenant id (GUID) or domain, e.g. contoso.onmicrosoft.com. Falls back to the FABRIC_TENANT env var.",
    )
    parser.add_argument(
        "--workspace",
        help="Fabric workspace id (GUID) or display name. Falls back to the FABRIC_WORKSPACE env var.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt (non-interactive).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List the items that would be deleted, but delete nothing.",
    )
    return parser.parse_args()


def confirm(workspace_id: str, name: str, count: int) -> bool:
    print(
        f"\nAbout to DELETE {count} item(s) from workspace "
        f"'{name}' ({workspace_id}). This cannot be undone."
    )
    answer = input("Type the workspace name or id to confirm: ").strip()
    return answer.casefold() in {name.casefold(), workspace_id.casefold()}


def main() -> int:
    args = parse_args()

    # Resolution order for tenant/workspace: CLI flag > environment variable.
    tenant = (args.tenant or os.environ.get("FABRIC_TENANT") or "").strip()
    workspace = (args.workspace or os.environ.get("FABRIC_WORKSPACE") or "").strip()

    # Both are mandatory (from a flag or an env var); refuse to run without them.
    if not tenant or not workspace:
        print(
            "ERROR: tenant and workspace are required. Pass --tenant/--workspace "
            "or set FABRIC_TENANT/FABRIC_WORKSPACE.",
            file=sys.stderr,
        )
        return 2

    fabric = Fabric(tenant)
    try:
        workspace_id, name = fabric.resolve_workspace_id(workspace)
    except ClientAuthenticationError as exc:
        print(
            "ERROR: could not authenticate with the Azure CLI. Run `az login` "
            f"(optionally `az login --tenant {tenant}`) and retry.\n{exc}",
            file=sys.stderr,
        )
        return 1

    items = fabric.list_items(workspace_id)
    folders = fabric.list_folders(workspace_id)
    deletable = [it for it in items if it.get("type") not in CHILD_ITEM_TYPES]
    skipped = [it for it in items if it.get("type") in CHILD_ITEM_TYPES]

    print(
        f"Workspace '{name}' ({workspace_id}) has {len(items)} item(s) "
        f"and {len(folders)} folder(s)."
    )
    for it in items:
        marker = "  (auto-removed with parent)" if it in skipped else ""
        print(f"  - [{it.get('type')}] {it.get('displayName')} {it.get('id')}{marker}")
    for fo in folders:
        print(f"  - [Folder] {fo.get('displayName')} {fo.get('id')}")

    if not deletable and not folders:
        print("Nothing to delete.")
        return 0

    if args.dry_run:
        print(
            f"\n--dry-run: would delete {len(deletable)} item(s) and "
            f"{len(folders)} folder(s); nothing was changed."
        )
        return 0

    if not args.yes and not confirm(workspace_id, name, len(deletable) + len(folders)):
        print("Confirmation did not match. Aborted; nothing was deleted.")
        return 1

    # Delete in repeated passes: some items (e.g. a Lakehouse) remove children, and
    # deleting a parent first can 400 a child listed earlier. Loop until stable.
    remaining = {it["id"]: it for it in deletable}
    failures: dict[str, str] = {}
    for _pass in range(1, 6):
        if not remaining:
            break
        progressed = False
        failures = {}
        for item_id, it in list(remaining.items()):
            resp = fabric.delete_item(workspace_id, item_id)
            label = f"[{it.get('type')}] {it.get('displayName')}"
            if resp.status_code in (200, 204):
                print(f"  deleted {label}")
                remaining.pop(item_id, None)
                progressed = True
            elif resp.status_code == 404:
                print(f"  gone     {label} (already removed)")
                remaining.pop(item_id, None)
                progressed = True
            else:
                failures[item_id] = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if not progressed:
            break

    if failures:
        print(f"\n{len(failures)} item(s) could not be deleted:", file=sys.stderr)
        for item_id, reason in failures.items():
            it = remaining.get(item_id, {})
            print(f"  - [{it.get('type')}] {it.get('displayName')} {item_id}: {reason}",
                  file=sys.stderr)
        return 1

    # Folders are a separate API and can only be deleted once empty. Fabric's
    # emptiness check lags for a minute or two after the items are deleted
    # (FolderNotEmpty even though the folder is empty), so wait and retry.
    folder_remaining = {fo["id"]: fo for fo in folders}
    folder_failures: dict[str, str] = {}
    max_folder_attempts = 12
    for attempt in range(1, max_folder_attempts + 1):
        if not folder_remaining:
            break
        folder_failures = {}
        for folder_id, fo in list(folder_remaining.items()):
            resp = fabric.delete_folder(workspace_id, folder_id)
            label = f"[Folder] {fo.get('displayName')}"
            if resp.status_code in (200, 204):
                print(f"  deleted {label}")
                folder_remaining.pop(folder_id, None)
            elif resp.status_code == 404:
                print(f"  gone     {label} (already removed)")
                folder_remaining.pop(folder_id, None)
            else:
                folder_failures[folder_id] = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if folder_remaining and attempt < max_folder_attempts:
            wait = 15
            print(
                f"  {len(folder_remaining)} folder(s) not deletable yet "
                f"(FolderNotEmpty lag); waiting {wait}s and retrying "
                f"(attempt {attempt}/{max_folder_attempts})..."
            )
            time.sleep(wait)

    if folder_failures:
        print(f"\n{len(folder_failures)} folder(s) could not be deleted:", file=sys.stderr)
        for folder_id, reason in folder_failures.items():
            fo = folder_remaining.get(folder_id, {})
            print(f"  - [Folder] {fo.get('displayName')} {folder_id}: {reason}",
                  file=sys.stderr)
        return 1

    print(
        f"\nDone. Deleted {len(deletable)} item(s) and {len(folders)} folder(s). "
        f"Workspace '{name}' is empty."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
