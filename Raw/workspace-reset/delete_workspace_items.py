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

    # Reconcile the workspace to empty. Fabric only deletes an EMPTY folder (no
    # items AND no nested subfolders — there is no recursive/force delete), and
    # deleting a Lakehouse/Warehouse auto-removes its child items (SQLEndpoint,
    # default semantic model) after a short lag. So we loop: each round we RE-LIST
    # items+folders (to see lagging child removals), delete every deletable item,
    # then delete only LEAF folders (those with no child folder). Re-listing makes
    # a folder that held a lagging child become empty and deletable on a later pass.
    def err_code(resp: requests.Response) -> str:
        try:
            return resp.json().get("errorCode", "") or ""
        except ValueError:
            return ""

    max_rounds = 20
    wait_s = 15
    deleted_items = 0
    deleted_folders = 0
    prev_total: int | None = None
    no_progress = 0
    item_failures: dict[str, tuple[dict[str, Any], str]] = {}
    folder_blocked: dict[str, tuple[dict[str, Any], str]] = {}

    for round_no in range(1, max_rounds + 1):
        items_now = fabric.list_items(workspace_id)
        folders_now = fabric.list_folders(workspace_id)
        deletable_now = [it for it in items_now if it.get("type") not in CHILD_ITEM_TYPES]
        total = len(deletable_now) + len(folders_now)
        if total == 0:
            break

        no_progress = no_progress + 1 if prev_total is not None and total >= prev_total else 0
        prev_total = total

        item_failures = {}
        for it in deletable_now:
            resp = fabric.delete_item(workspace_id, it["id"])
            label = f"[{it.get('type')}] {it.get('displayName')}"
            if resp.status_code in (200, 204):
                print(f"  deleted {label}")
                deleted_items += 1
            elif resp.status_code == 404:
                print(f"  gone     {label} (already removed)")
            else:
                item_failures[it["id"]] = (it, f"HTTP {resp.status_code} {err_code(resp)}: {resp.text[:180]}")

        # Only leaf folders (no child folder present) can possibly be empty.
        parent_ids = {f.get("parentFolderId") for f in folders_now if f.get("parentFolderId")}
        leaves = [f for f in folders_now if f["id"] not in parent_ids]
        folder_blocked = {}
        for fo in leaves:
            resp = fabric.delete_folder(workspace_id, fo["id"])
            label = f"[Folder] {fo.get('displayName')}"
            if resp.status_code in (200, 204):
                print(f"  deleted {label}")
                deleted_folders += 1
            elif resp.status_code == 404:
                print(f"  gone     {label} (already removed)")
            else:
                folder_blocked[fo["id"]] = (fo, f"HTTP {resp.status_code} {err_code(resp)}: {resp.text[:180]}")

        # No shrink this round means we're waiting on child-item removal lag
        # (FolderNotEmpty) — pause, then re-list and try again.
        if no_progress >= 1 and (folder_blocked or item_failures) and round_no < max_rounds:
            print(
                f"  {len(folder_blocked)} folder(s)/{len(item_failures)} item(s) not deletable yet "
                f"(child-removal lag); waiting {wait_s}s and retrying "
                f"(round {round_no}/{max_rounds})..."
            )
            time.sleep(wait_s)

    # Authoritative final check.
    items_left = [it for it in fabric.list_items(workspace_id) if it.get("type") not in CHILD_ITEM_TYPES]
    folders_left = fabric.list_folders(workspace_id)
    if items_left or folders_left:
        print(
            f"\nCould not fully empty the workspace: {len(items_left)} item(s) and "
            f"{len(folders_left)} folder(s) remain.",
            file=sys.stderr,
        )
        for it in items_left:
            reason = item_failures.get(it["id"], (None, ""))[1]
            print(f"  - [{it.get('type')}] {it.get('displayName')} {it['id']} {reason}".rstrip(),
                  file=sys.stderr)
        for fo in folders_left:
            reason = folder_blocked.get(fo["id"], (None, "FolderNotEmpty (still has items/subfolders)"))[1]
            print(f"  - [Folder] {fo.get('displayName')} {fo['id']} {reason}".rstrip(),
                  file=sys.stderr)
        return 1

    print(
        f"\nDone. Deleted {deleted_items} item(s) and {deleted_folders} folder(s). "
        f"Workspace '{name}' is empty."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
