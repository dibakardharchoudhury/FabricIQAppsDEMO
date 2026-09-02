#!/usr/bin/env python3
"""Run a Microsoft Fabric Data Pipeline on demand, passing its parameters, and wait.

Finds a pipeline by display name (default: 01_Pipe_Setup) in a workspace, starts an
on-demand run with the parameters you supply, then polls the job instance to
completion and reports Succeeded / Failed.

Authentication uses your CURRENT sign-in context -- the Azure CLI session. Run
`az login` first (optionally `az login --tenant <tenant>`); whatever identity you
are signed in as is the identity that runs the pipeline. Running a pipeline needs
the Item.Execute.All scope, i.e. a Contributor+ role on the workspace.

Both tenant and workspace are REQUIRED -- supply them via --tenant/--workspace or
the FABRIC_TENANT/FABRIC_WORKSPACE environment variables (CLI flag > env var).

Pipeline parameters are passed as one JSON object, either with --parameters-json
or via the FABRIC_PIPELINE_PARAMS environment variable (handy for the web UI so
values with @ and : -- like a Teams channel id -- never need shell escaping). The
keys must match the pipeline's declared parameter names; values keep their JSON
type (a JSON number stays an int/float, so per_notebook_timeout_secs arrives as an
int). Parameters are sent as executionData.parameters, the documented shape for
Fabric Data Pipeline runs.

Examples:
    az login
    # env-driven params (recommended -- no escaping headaches):
    #   $env:FABRIC_TENANT = "<tenant-guid>"; $env:FABRIC_WORKSPACE = "<workspace>"
    #   $env:FABRIC_PIPELINE_PARAMS = '{"env_suffix":"V6","per_notebook_timeout_secs":3600}'
    python run_pipeline.py --yes

    # everything on the command line:
    python run_pipeline.py --tenant <tenant> --workspace <workspace> \
        --pipeline 01_Pipe_Setup \
        --parameters-json '{"env_suffix":"V6"}' --yes

    # start it but don't wait for it to finish:
    python run_pipeline.py --tenant <tenant> --workspace <workspace> --no-wait --yes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any

from key_vault_preflight import PreflightError, ensure_key_vault_access

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
DEFAULT_PIPELINE = "01_Pipe_Setup"
# The pipeline is a DataPipeline item; keep a couple of synonyms in case the API
# labels it differently across regions.
PIPELINE_TYPES = {"DataPipeline", "Pipeline"}
TERMINAL_STATES = {"Completed", "Failed", "Cancelled", "Deduped"}


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
        # Cache the token and refresh only near expiry, so long pipeline polls don't
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

    def resolve_workspace_id(self, workspace: str) -> tuple[str, str]:
        if GUID_RE.match(workspace):
            resp = self.request("GET", f"{FABRIC_BASE}/workspaces/{workspace}")
            if resp.status_code == 200:
                return workspace, resp.json().get("displayName", workspace)
            raise SystemExit(
                f"Workspace '{workspace}' not found or not accessible "
                f"(HTTP {resp.status_code}): {resp.text}"
            )
        resp = self.request("GET", f"{FABRIC_BASE}/workspaces")
        resp.raise_for_status()
        matches = [
            w for w in resp.json().get("value", [])
            if w.get("displayName", "").casefold() == workspace.casefold()
        ]
        if not matches:
            raise SystemExit(f"No workspace named '{workspace}' is visible to the signed-in user.")
        if len(matches) > 1:
            ids = ", ".join(w["id"] for w in matches)
            raise SystemExit(f"Multiple workspaces named '{workspace}': {ids}. Use the GUID.")
        return matches[0]["id"], matches[0]["displayName"]

    def list_items(self, ws_id: str) -> list[dict[str, Any]]:
        """Return every item in the workspace, following pagination."""
        out: list[dict[str, Any]] = []
        url: str | None = f"{FABRIC_BASE}/workspaces/{ws_id}/items"
        while url:
            resp = self.request("GET", url)
            if resp.status_code != 200:
                raise SystemExit(f"Failed to list items: HTTP {resp.status_code} {resp.text}")
            data = resp.json()
            out.extend(data.get("value", []))
            token = data.get("continuationToken")
            url = f"{FABRIC_BASE}/workspaces/{ws_id}/items?continuationToken={token}" if token else None
        return out

    def find_pipeline(self, ws_id: str, name: str) -> tuple[str, str]:
        """Return (itemId, displayName) of the pipeline matching name (GUID or display name)."""
        items = self.list_items(ws_id)
        pipelines = [it for it in items if (it.get("type") in PIPELINE_TYPES)]
        if GUID_RE.match(name):
            for it in pipelines:
                if it.get("id", "").casefold() == name.casefold():
                    return it["id"], it.get("displayName", name)
            raise SystemExit(f"No pipeline with id '{name}' found in this workspace.")
        matches = [it for it in pipelines if it.get("displayName", "").casefold() == name.casefold()]
        if not matches:
            available = ", ".join(sorted(p.get("displayName", "?") for p in pipelines)) or "(none)"
            raise SystemExit(
                f"No pipeline named '{name}' found in this workspace. Pipelines present: {available}."
            )
        if len(matches) > 1:
            ids = ", ".join(m["id"] for m in matches)
            raise SystemExit(f"Multiple pipelines named '{name}': {ids}. Use the pipeline GUID.")
        return matches[0]["id"], matches[0]["displayName"]

    def start_pipeline(self, ws_id: str, item_id: str, parameters: dict[str, Any]) -> str:
        """Start an on-demand pipeline run; return the job instance status URL (Location)."""
        url = f"{FABRIC_BASE}/workspaces/{ws_id}/items/{item_id}/jobs/instances?jobType=Pipeline"
        # Data Pipelines take per-run parameters as executionData.parameters (an
        # object keyed by parameter name). Omit executionData entirely when there
        # are no parameters so pipeline defaults apply.
        body: dict[str, Any] = {"executionData": {"parameters": parameters}} if parameters else {}
        resp = self.request("POST", url, json=body)
        if resp.status_code not in (200, 201, 202):
            raise SystemExit(f"Failed to start pipeline: HTTP {resp.status_code} {resp.text}")
        location = resp.headers.get("Location") or resp.headers.get("Operation-Location")
        if not location:
            raise SystemExit("Pipeline accepted but no job-instance Location header was returned.")
        return location

    def poll_job(self, status_url: str, first_wait: int) -> dict[str, Any]:
        """Poll a job instance until it reaches a terminal state; return the final body."""
        wait = max(2, first_wait)
        last = ""
        while True:
            time.sleep(wait)
            resp = self.request("GET", status_url)
            if resp.status_code not in (200, 202):
                raise SystemExit(f"Failed to read job status: HTTP {resp.status_code} {resp.text}")
            body = resp.json() if resp.content else {}
            state = body.get("status", "") or str(resp.status_code)
            if state != last:
                print(f"  job status: {state}")
                last = state
            if state in TERMINAL_STATES:
                return body
            wait = min(30, int(resp.headers.get("Retry-After", str(wait))) or wait)


# Whether we may prompt the user. Set in main(): false under --yes or without a TTY.
_INTERACTIVE = True


def load_parameters(args: argparse.Namespace) -> dict[str, Any]:
    """Build the parameter dict from --parameters-json or FABRIC_PIPELINE_PARAMS."""
    raw = args.parameters_json or os.environ.get("FABRIC_PIPELINE_PARAMS") or ""
    raw = raw.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--parameters-json / FABRIC_PIPELINE_PARAMS is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise SystemExit("Pipeline parameters must be a JSON object of name -> value.")
    return parsed


def prompt_required(label: str, current: str | None) -> str:
    value = (current or "").strip()
    if not value and not _INTERACTIVE:
        raise SystemExit(f"ERROR: {label} is required but was not supplied (non-interactive run).")
    while not value:
        value = input(f"  {label}: ").strip()
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", help="Tenant id or domain to sign in against.")
    parser.add_argument("--workspace", help="Workspace GUID or display name.")
    parser.add_argument("--pipeline", help=f"Pipeline display name or GUID (default: {DEFAULT_PIPELINE}).")
    parser.add_argument("--parameters-json", help="Pipeline parameters as a JSON object (name -> value). Alternatively set FABRIC_PIPELINE_PARAMS.")
    parser.add_argument("--no-wait", action="store_true", help="Start the run and exit without waiting for it to finish.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args()

    args.tenant = args.tenant or os.environ.get("FABRIC_TENANT")
    args.workspace = args.workspace or os.environ.get("FABRIC_WORKSPACE")
    args.pipeline = args.pipeline or os.environ.get("FABRIC_PIPELINE") or DEFAULT_PIPELINE

    global _INTERACTIVE
    _INTERACTIVE = not args.yes and sys.stdin.isatty()

    args.tenant = prompt_required("Tenant id or domain", args.tenant)
    args.workspace = prompt_required("Workspace GUID or name", args.workspace)
    parameters = load_parameters(args)

    try:
        fab = Fabric(args.tenant)
        ws_id, ws_name = fab.resolve_workspace_id(args.workspace)
    except ClientAuthenticationError:
        print("Authentication failed. Run `az login` (optionally --tenant) first.", file=sys.stderr)
        return 1

    # The action target is the source of truth. Notebooks require the canonical
    # GUID even when the user selected the workspace by display name.
    parameters["workspace_id"] = ws_id
    key_vault_uri = str(parameters.get("key_vault_uri") or "").strip()
    if key_vault_uri:
        try:
            ensure_key_vault_access(args.tenant, ws_id, key_vault_uri)
        except PreflightError as exc:
            print(f"Key Vault preflight failed: {exc}", file=sys.stderr)
            return 1

    item_id, pipe_name = fab.find_pipeline(ws_id, args.pipeline)
    print(f"Pipeline '{pipe_name}' ({item_id}) in workspace '{ws_name}' ({ws_id}).")
    if parameters:
        print("Parameters:")
        for key in sorted(parameters):
            print(f"  - {key} = {parameters[key]}")
    else:
        print("Parameters: (none supplied; the pipeline's own defaults apply)")

    if _INTERACTIVE and not args.yes:
        answer = input(f"\nRun pipeline '{pipe_name}' now? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0

    print(f"\nStarting pipeline '{pipe_name}'...")
    status_url = fab.start_pipeline(ws_id, item_id, parameters)
    print("  run queued.")

    if args.no_wait:
        print("Started; not waiting for completion (--no-wait).")
        print(f"Track it at: {status_url}")
        return 0

    print("Waiting for the pipeline to finish (this can take a while)...")
    result = fab.poll_job(status_url, first_wait=10)
    state = result.get("status", "")
    if state == "Completed":
        print(f"\nDone. Pipeline '{pipe_name}' run COMPLETED.")
        return 0
    reason = result.get("failureReason") or {}
    message = reason.get("message") if isinstance(reason, dict) else reason
    print(f"\nPipeline '{pipe_name}' run {state.upper() or 'ENDED'}.", file=sys.stderr)
    if message:
        print(f"  reason: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
