#!/usr/bin/env python3
"""Local API for the workspace-reset web UI.

Thin Flask backend that drives the workflow CLI scripts (kept as the single
source of truth) as background jobs:
    - sync_workspace_from_git.py  -> POST /api/sync
    - sync + pipeline + deploy    -> POST /api/full-workflow
    - delete_workspace_items.py   -> POST /api/delete
    - deploy_fabric_app.py        -> POST /api/deploy-app

Each POST starts a job and returns a jobId immediately. The child process is run
UNBUFFERED so its progress lines stream out live; the worker thread appends them
to the job and advances a coarse "phase" as known markers appear. The frontend
polls GET /api/jobs/<id>?since=<n> to render progress and live output until done.

Non-secret values are passed to the child as command-line flags; the GitHub PAT
is passed only through the child's environment (FABRIC_GIT_PAT) so it never lands
on a command line, in shell history, or on disk. The PAT is never logged or
returned to the client.

Runs on 127.0.0.1 only (loopback). Auth relies on your `az login` session.

    python -m pip install -r ../requirements.txt
    az login
    python server.py            # then open http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# Repo layout: this file lives in .../workspace-reset/webapp/, the CLI scripts
# live one level up in .../workspace-reset/.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = SCRIPT_DIR / "sync_workspace_from_git.py"
DELETE_SCRIPT = SCRIPT_DIR / "delete_workspace_items.py"
PIPELINE_SCRIPT = SCRIPT_DIR / "run_pipeline.py"
DEPLOY_SCRIPT = SCRIPT_DIR / "deploy_fabric_app.py"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PIPELINE_DEFAULTS_FILE = Path(__file__).with_name("pipeline-defaults.local.json")
REPO_ROOT = SCRIPT_DIR.parent.parent

SYNC_TIMEOUT_S = 1800
DELETE_TIMEOUT_S = 600
PIPELINE_TIMEOUT_S = 3 * 3600
DEPLOY_TIMEOUT_S = 30 * 60
FOUNDRY_TIMEOUT_S = 90 * 60
ENERGY_TIMEOUT_S = 3 * 3600

# Ordered phase labels + the substrings that mark reaching each phase. Index 0 is
# the initial state; success jumps to the final phase.
SYNC_PHASES = [
    "Queued", "Creating connection", "Connecting", "Initializing",
    "Importing items", "Finishing", "Done",
]
SYNC_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("Creating GitHub connection", "created connection")),
    (2, ("Connecting '", "  connected.")),
    (3, ("Initializing connection", "requiredAction=")),
    (4, ("Updating workspace from Git", "update complete", "nothing to update")),
    (5, ("Disconnecting from Git", "Publishing Weather Environment", "Weather Environment publish completed", "disconnected", "Retained connection", "Cleaning up failed connection")),
]

DELETE_PHASES = ["Queued", "Working", "Done"]
DELETE_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("About to DELETE", "  - [", "deleted ", "gone ", "Nothing to delete")),
]

TEST_TIMEOUT_S = 300
TEST_PHASES = ["Queued", "Testing", "Done"]
TEST_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("Testing GitHub connectivity", "Trying connection", "Testing the supplied PAT")),
    (2, ("CONNECTION TEST:",)),
]

PIPELINE_PHASES = ["Queued", "Resolving workspace", "Checking Key Vault", "Resolving pipeline", "Running", "Done"]
PIPELINE_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("Key Vault preflight:", "Pipeline '")),
    (2, ("public network access", "checking Fabric managed private endpoints", "Fabric endpoint state")),
    (3, ("Pipeline '",)),
    (4, ("Starting pipeline", "run queued", "job status:")),
]

DEPLOY_PHASES = [
    "Queued", "Checking prerequisites", "Resolving SPA", "Preparing state",
    "Signing in", "Provisioning app", "Syncing backend settings", "Setting up sign-in",
    "Checking endpoint and CORS readiness", "Done",
]
DEPLOY_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("[1/8]",)),
    (2, ("[2/8]",)),
    (3, ("[3/8]",)),
    (4, ("[4/8]",)),
    (5, ("[5/8]",)),
    (6, ("[6/8]",)),
    (7, ("[7/8]",)),
    (8, ("[8/8]",)),
]
FOUNDRY_PHASES = [
    "Queued", "Checking prerequisites", "Resolving SPA", "Preparing source/gateway",
    "Foundry project", "Foundry agents", "Hosted Agent", "Done",
]
FOUNDRY_MARKERS = [
    (1, ("[1/8]",)), (2, ("[2/8]",)), (3, ("[3/8]",)),
    (4, ("Foundry: project",)), (5, ("Foundry: agents",)), (6, ("Foundry: Hosted Agent",)),
]
ENERGY_PHASES = ["Queued", "Checking prerequisites", "Preparing energy sources", "Verifying read models", "Done"]
ENERGY_MARKERS = [
    (1, ("[1/8]",)), (2, ("Energy: setup",)), (3, ("Energy map read models are ready",)),
]

CORE_SETUP_PHASES = [
    "Queued",
    *[f"Pipeline · {phase}" for phase in PIPELINE_PHASES[1:-1]],
    *[f"Energy · {phase}" for phase in ENERGY_PHASES[1:-1]],
    "Done",
]
WORKFLOW_PHASES = [
    "Queued",
    *[f"Sync · {phase}" for phase in SYNC_PHASES[1:-1]],
    *CORE_SETUP_PHASES[1:-1],
    *[f"Foundry · {phase}" for phase in FOUNDRY_PHASES[1:-1]],
    *[f"Deploy · {phase}" for phase in DEPLOY_PHASES[1:-1]],
    "Done",
]

# Interactive `az login` run from the UI so sign-in is the app's first step
# (no tenant/az-login prerequisite before launch). Gives the user 5 minutes to
# finish in the browser.
LOGIN_TIMEOUT_S = 300
LOGIN_PHASES = ["Starting", "Waiting for browser sign-in", "Done"]
LOGIN_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("web browser has been opened", "opened at", "To sign in, use", "enter the code")),
]
# Tenant is used to scope `az login --tenant`; allow only GUIDs / domain names so
# the value is safe to place on a command line.
TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


def clean_repo(repository: str) -> str:
    """Normalize 'owner/repo' or a pasted GitHub URL to 'owner/repo'.

    Drops the protocol/host, a '.git' suffix, and any '/tree/<branch>' or
    '/blob/<branch>/<path>' tail that GitHub appends when you copy a URL from a
    branch or file view; returns '' when there aren't at least owner + repo.
    """
    value = re.sub(r"^(https?://)?(www\.)?github\.com[/:]", "", repository.strip(), flags=re.IGNORECASE)
    value = re.sub(r"^git@github\.com:", "", value, flags=re.IGNORECASE)
    value = value.removesuffix(".git").strip("/")
    parts = [p for p in value.split("/") if p]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


app = Flask(__name__, static_folder=None)
ALLOWED_WEB_ORIGINS = {"http://127.0.0.1:5000", "http://localhost:5000"}


@app.before_request
def enforce_local_request():
    """Reject remote, DNS-rebound, and cross-origin browser requests."""
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify(error="this service accepts loopback requests only."), 403
    host = request.host.lower()
    if host not in {"127.0.0.1", "127.0.0.1:5000", "localhost", "localhost:5000"}:
        return jsonify(error="invalid local Host header."), 403
    origin = request.headers.get("Origin")
    if origin and origin.lower() not in ALLOWED_WEB_ORIGINS:
        return jsonify(error="cross-origin requests are not allowed."), 403
    return None


class Job:
    """In-memory record of one running/finished script invocation."""

    def __init__(self, phases: list[str], exclusive: bool = False) -> None:
        self.id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.lines: list[str] = []
        self.phases = phases
        self.phase_index = 0
        self.status = "running"  # running | succeeded | failed | cancelled
        self.returncode: int | None = None
        self.exclusive = exclusive
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.component: str | None = None
        self.failed_component: str | None = None


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()

# The server can run without a console (detached launch / after Restart); on
# Windows a child process would then pop its own window, so suppress it. Output
# is still captured through the pipe.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _terminate_owned_process(proc: subprocess.Popen[str]) -> None:
    """Terminate one job's process tree without affecting unrelated processes."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
            check=False,
        )
        if result.returncode != 0 and proc.poll() is None:
            proc.kill()
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)


def _cancel_job(job: Job) -> bool:
    with job.lock:
        if job.status != "running":
            return False
        job.cancel_requested = True
        proc = job.process
        job.lines.append("[cancel requested from local app]")
    if proc is not None:
        _terminate_owned_process(proc)
    return True


def _worker(job: Job, argv: list[str], env_extra: dict[str, str] | None,
            timeout: int, markers: list[tuple[int, tuple[str, ...]]],
            python: bool = True) -> None:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if env_extra:
        env.update(env_extra)
    # Python scripts run unbuffered via the current interpreter; non-python argv
    # (e.g. `az login`) is executed as-is.
    cmd = [sys.executable, "-u", *argv] if python else list(argv)
    try:
        # -u forces the child's stdout to be unbuffered so lines arrive live.
        # DEVNULL stdin makes the child non-interactive (isatty()==False) so it never
        # blocks on a hidden prompt reading the server's own terminal.
        proc = subprocess.Popen(
            cmd,
            cwd=str(SCRIPT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=NO_WINDOW,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        with job.lock:
            job.lines.append(f"[failed to start: {exc}]")
            job.status = "failed"
            job.returncode = -1
        return

    with job.lock:
        job.process = proc
        cancel_requested = job.cancel_requested
    if cancel_requested:
        _terminate_owned_process(proc)

    timed_out = threading.Event()

    def terminate_for_timeout() -> None:
        if proc.poll() is None:
            timed_out.set()
            _terminate_owned_process(proc)

    timer = threading.Timer(timeout, terminate_for_timeout)
    timer.start()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            with job.lock:
                job.lines.append(line)
                for idx, subs in markers:
                    if idx > job.phase_index and any(s in line for s in subs):
                        job.phase_index = idx
        proc.wait()
    finally:
        timer.cancel()
        if proc.stdout is not None:
            proc.stdout.close()

    with job.lock:
        job.process = None
        job.returncode = proc.returncode
        if job.cancel_requested:
            job.status = "cancelled"
        else:
            job.status = "succeeded" if proc.returncode == 0 else "failed"
        if job.status == "succeeded":
            job.phase_index = len(job.phases) - 1


def _run_workflow_step(
    job: Job,
    name: str,
    argv: list[str],
    env_extra: dict[str, str] | None,
    timeout: int,
    markers: list[tuple[int, tuple[str, ...]]],
    phase_offset: int,
) -> int:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, "-u", *argv]
    with job.lock:
        job.phase_index = phase_offset
        job.component = name
        job.lines.extend(("", f"===== {name} ====="))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(SCRIPT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=NO_WINDOW,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        with job.lock:
            job.lines.append(f"[{name} failed to start: {exc}]")
        return -1

    with job.lock:
        job.process = proc
        cancel_requested = job.cancel_requested
    if cancel_requested:
        _terminate_owned_process(proc)

    timed_out = threading.Event()

    def terminate_for_timeout() -> None:
        if proc.poll() is None:
            timed_out.set()
            _terminate_owned_process(proc)

    timer = threading.Timer(timeout, terminate_for_timeout)
    timer.start()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            with job.lock:
                job.lines.append(f"[{name}] {line}")
                for idx, subs in markers:
                    target = phase_offset + idx - 1
                    if target > job.phase_index and any(marker in line for marker in subs):
                        job.phase_index = target
        proc.wait()
    finally:
        timer.cancel()
        if proc.stdout is not None:
            proc.stdout.close()
        with job.lock:
            job.process = None
            if timed_out.is_set():
                job.lines.append(f"[{name} timed out after {timeout} seconds]")
    return proc.returncode


def _workflow_worker(job: Job, steps: list[dict[str, object]]) -> None:
    for step in steps:
        if job.cancel_requested:
            break
        name = str(step["name"])
        try:
            returncode = _run_workflow_step(
                job,
                name,
                step["argv"],
                step.get("env"),
                int(step["timeout"]),
                step["markers"],
                int(step["phase_offset"]),
            )
        except Exception as exc:
            with job.lock:
                job.status = "failed"
                job.returncode = -1
                job.failed_component = name
                job.process = None
                job.lines.append(
                    f"[workflow stopped] {name} monitor failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            return
        if returncode != 0:
            with job.lock:
                job.returncode = returncode
                if job.cancel_requested:
                    job.status = "cancelled"
                else:
                    job.status = "failed"
                    job.failed_component = name
                    job.lines.append(
                        f"[workflow stopped] {name} failed with exit code {returncode}; "
                        "later steps were not started."
                    )
            return
        with job.lock:
            job.lines.append(f"[workflow] {name} completed successfully.")

    with job.lock:
        if job.cancel_requested:
            job.status = "cancelled"
            job.returncode = job.returncode if job.returncode is not None else -1
        else:
            job.status = "succeeded"
            job.returncode = 0
            job.component = None
            job.phase_index = len(job.phases) - 1
            job.lines.append("[workflow] Completed: " + ", ".join(str(step["name"]) for step in steps) + ".")


def _start_workflow(steps: list[dict[str, object]], *, phases: list[str] | None = None) -> str | None:
    job = Job(WORKFLOW_PHASES if phases is None else phases, exclusive=True)
    with JOBS_LOCK:
        running = [existing for existing in JOBS.values() if existing.status == "running"]
        if running:
            return None
        JOBS[job.id] = job
    threading.Thread(target=_workflow_worker, args=(job, steps), daemon=True).start()
    return job.id


def _start(argv: list[str], env_extra: dict[str, str] | None, timeout: int,
           phases: list[str], markers: list[tuple[int, tuple[str, ...]]],
           python: bool = True, exclusive: bool = True) -> str | None:
    job = Job(phases, exclusive=exclusive)
    with JOBS_LOCK:
        running = [existing for existing in JOBS.values() if existing.status == "running"]
        if any(existing.exclusive for existing in running) or (exclusive and running):
            return None
        JOBS[job.id] = job
    threading.Thread(
        target=_worker, args=(job, argv, env_extra, timeout, markers, python), daemon=True
    ).start()
    return job.id


def job_response(job_id: str | None):
    if job_id is None:
        return jsonify(error="another initializer job is running; wait for it to finish."), 409
    return jsonify(jobId=job_id)


def _sync_invocation(data: dict) -> tuple[list[str], dict[str, str]]:
    tenant = (data.get("tenant") or "").strip()
    workspace = (data.get("workspace") or "").strip()
    raw_repository = (data.get("repository") or "").strip()
    branch = (data.get("branch") or "main").strip() or "main"
    directory = (data.get("directory") or "/").strip() or "/"
    connection_id = (data.get("connectionId") or "").strip()
    pat = (data.get("pat") or "").strip()
    if not tenant or not workspace or not raw_repository:
        raise ValueError("tenant, workspace and repository are required.")
    repository = clean_repo(raw_repository)
    if not repository:
        raise ValueError("repository must be 'owner/repo' or a GitHub repo URL.")
    if not connection_id and not pat:
        raise ValueError("a PAT is required unless a connection id is reused.")
    owner, repo = (part.strip() for part in repository.split("/", 1))
    argv = [
        str(SYNC_SCRIPT), "--tenant", tenant, "--workspace", workspace,
        "--owner", owner, "--repository", repo, "--branch", branch,
        "--directory", directory, "--yes",
    ]
    if connection_id:
        argv += ["--connection-id", connection_id]
    if data.get("keepConnected"):
        argv += ["--keep-connected"]
    return argv, {"FABRIC_GIT_PAT": pat} if pat else {}


@app.post("/api/sync")
def api_sync():
    data = request.get_json(silent=True) or {}
    try:
        argv, env_extra = _sync_invocation(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    job_id = _start(
        argv, env_extra, SYNC_TIMEOUT_S, SYNC_PHASES, SYNC_MARKERS, exclusive=True
    )
    return job_response(job_id)


@app.post("/api/test-connection")
def api_test_connection():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    workspace = (data.get("workspace") or "").strip()
    repository = (data.get("repository") or "").strip()
    branch = (data.get("branch") or "main").strip() or "main"
    directory = (data.get("directory") or "/").strip() or "/"
    connection_id = (data.get("connectionId") or "").strip()
    pat = (data.get("pat") or "").strip()

    if not tenant or not workspace or not repository:
        return jsonify(error="tenant, workspace and repository are required."), 400
    repository = clean_repo(repository)
    if not repository:
        return jsonify(error="repository must be 'owner/repo' or a GitHub repo URL."), 400
    if not connection_id and not pat:
        return jsonify(error="enter a connection id (or 'yes') or a PAT to test."), 400

    owner, repo = (part.strip() for part in repository.split("/", 1))
    argv = [
        str(SYNC_SCRIPT),
        "--tenant", tenant,
        "--workspace", workspace,
        "--owner", owner,
        "--repository", repo,
        "--branch", branch,
        "--directory", directory,
        "--test-connection",
    ]
    env_extra: dict[str, str] = {}
    if connection_id:
        argv += ["--connection-id", connection_id]
    if pat:
        env_extra["FABRIC_GIT_PAT"] = pat

    job_id = _start(argv, env_extra, TEST_TIMEOUT_S, TEST_PHASES, TEST_MARKERS)
    return job_response(job_id)


@app.post("/api/delete")
def api_delete():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    workspace = (data.get("workspace") or "").strip()
    dry_run = bool(data.get("dryRun"))

    if not tenant or not workspace:
        return jsonify(error="tenant and workspace are required."), 400

    argv = [
        str(DELETE_SCRIPT),
        "--tenant", tenant,
        "--workspace", workspace,
        "--dry-run" if dry_run else "--yes",
    ]
    job_id = _start(
        argv, None, DELETE_TIMEOUT_S, DELETE_PHASES, DELETE_MARKERS, exclusive=True
    )
    return job_response(job_id)


# Declared parameters of the 01_Pipe_Setup pipeline, in display order. Each entry
# drives one field in the UI (label + help + prefilled default) and is validated
# here. Defaults are tenant-neutral; target destinations must be supplied explicitly.
# (they are ids and secret *names*, not secret values); "int" values are coerced
# so they reach the pipeline as JSON numbers, not strings.
PIPELINE_PARAM_SPEC: list[dict[str, str]] = [
    {"name": "env_suffix", "type": "string", "default": "V6",
     "label": "Environment suffix",
     "help": "Suffix appended to the artifacts this run creates (e.g. the lakehouse). Bump it to keep parallel demo runs from colliding."},
    {"name": "workspace_id", "type": "string", "default": "",
     "label": "Workspace name or ID",
     "help": "Mirrors the target workspace on the left. Names are resolved to the canonical workspace GUID before the pipeline starts."},
    {"name": "key_vault_uri", "type": "string", "default": "",
     "label": "Key Vault URI",
     "help": "Azure Key Vault that holds the service-principal secrets the notebooks read (e.g. https://myvault.vault.azure.net/)."},
    {"name": "key_vault_tenant_id_secret_name", "type": "string", "default": "tenantid",
     "label": "KV secret · tenant id",
     "help": "Name of the Key Vault secret that stores the tenant id."},
    {"name": "key_vault_client_id_secret_name", "type": "string", "default": "clientid",
     "label": "KV secret · client id",
     "help": "Name of the Key Vault secret that stores the app (client) id."},
    {"name": "key_vault_client_secret_name", "type": "string", "default": "clientsecret",
     "label": "KV secret · client secret",
     "help": "Name of the Key Vault secret that stores the client secret."},
    {"name": "ops_agent_teams_team_id", "type": "string", "default": "",
     "label": "Teams team id",
     "help": "In Teams, copy the target channel's link. Use the GUID after groupId= as the Team ID.",
     "helpUrl": "https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-teams#deep-link-to-navigate-to-channel"},
    {"name": "ops_agent_teams_channel_id", "type": "string", "default": "",
     "label": "Teams channel id",
     "help": "From the same Teams link, copy the segment after /channel/ and URL-decode it (%3A becomes : and %40 becomes @). Use the full 19:...@thread.tacv2 ID, not the channel name. Delivery still requires permissions.",
     "helpUrl": "https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-teams#deep-link-to-navigate-to-channel"},
    {"name": "ops_agent_run_as_user", "type": "string", "default": "",
     "label": "Agent run-as user",
     "help": "Optional UPN guard for the delegated user that provisions and runs the Operations Agents; blank uses the signed-in deploying user."},
    {"name": "alert_email_to", "type": "string", "default": "",
     "label": "Alert email recipient",
     "help": "Required by this launcher. This independent To address is used by Pipe_SendEmailAlert; direct notebook runs fall back to the run-as/deploying user when blank."},
    {"name": "per_notebook_timeout_secs", "type": "int", "default": "3600",
     "label": "Per-notebook timeout (secs)",
     "help": "Seconds the orchestrator waits for each notebook before giving up (e.g. 3600)."},
]
PIPELINE_PARAM_NAMES = {p["name"] for p in PIPELINE_PARAM_SPEC}


@app.get("/api/pipeline-params")
def api_pipeline_params():
    """Expose the pipeline's parameter spec so the UI can render labelled fields."""
    parameters = [dict(spec) for spec in PIPELINE_PARAM_SPEC]
    configured = os.environ.get("FABRIC_PIPELINE_PARAMS", "").strip()
    tenant = os.environ.get("FABRIC_TENANT", "").strip()
    workspace = os.environ.get("FABRIC_WORKSPACE", "").strip()
    source = "server configuration"
    if not configured and PIPELINE_DEFAULTS_FILE.exists():
        try:
            local = json.loads(PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return jsonify(error=f"Could not read local pipeline defaults: {exc}"), 400
        if (
            not isinstance(local, dict)
            or not isinstance(local.get("tenant"), str)
            or not isinstance(local.get("workspace"), str)
            or not isinstance(local.get("parameters"), dict)
        ):
            return jsonify(error="Local pipeline defaults require tenant, workspace and a parameters object."), 400
        tenant = local["tenant"].strip()
        workspace = local["workspace"].strip()
        configured = json.dumps(local["parameters"])
        source = "local configuration"
    if configured:
        if not tenant or not workspace:
            return jsonify(error="Pipeline defaults require a tenant and workspace (FABRIC_TENANT and FABRIC_WORKSPACE for environment defaults)."), 400
        try:
            values = json.loads(configured)
        except json.JSONDecodeError:
            return jsonify(error="FABRIC_PIPELINE_PARAMS must be a JSON object."), 400
        if not isinstance(values, dict):
            return jsonify(error="FABRIC_PIPELINE_PARAMS must be a JSON object."), 400
        matching_target = (
            request.args.get("tenant", "").strip().casefold() == tenant.casefold()
            and request.args.get("workspace", "").strip().casefold() == workspace.casefold()
        )
        for spec in parameters:
            if spec["name"] == "workspace_id" or spec["name"] not in values:
                continue
            value = values[spec["name"]]
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                return jsonify(error=f"Configured {spec['label']} must be a string or integer."), 400
            if spec["type"] == "int":
                try:
                    int(str(value).strip())
                except ValueError:
                    return jsonify(error=f"Configured {spec['label']} must be a whole number."), 400
            if matching_target:
                spec["default"] = str(value).strip()
                spec["source"] = source
    response = jsonify(pipeline="01_Pipe_Setup", parameters=parameters)
    response.headers["Cache-Control"] = "no-store"
    return response


def _pipeline_invocation(data: dict) -> tuple[list[str], dict[str, str]]:
    tenant = (data.get("tenant") or "").strip()
    workspace = (data.get("workspace") or "").strip()
    pipeline = (data.get("pipeline") or "01_Pipe_Setup").strip() or "01_Pipe_Setup"
    raw_params = data.get("parameters") or {}
    if not tenant or not workspace:
        raise ValueError("tenant and workspace are required.")
    if not isinstance(raw_params, dict):
        raise ValueError("parameters must be an object of name -> value.")
    if not str(raw_params.get("key_vault_uri") or "").strip():
        raise ValueError("Key Vault URI is required for connectivity preflight.")
    if not str(raw_params.get("alert_email_to") or "").strip():
        raise ValueError("Alert email recipient is required.")
    for name, label in (
        ("ops_agent_teams_team_id", "Teams team id"),
        ("ops_agent_teams_channel_id", "Teams channel id"),
    ):
        if not str(raw_params.get(name) or "").strip():
            raise ValueError(f"{label} is required for Operations Agent provisioning.")
    params: dict[str, object] = {}
    for spec in PIPELINE_PARAM_SPEC:
        name = spec["name"]
        if name not in raw_params:
            continue
        value = raw_params[name]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if spec["type"] == "int":
            try:
                params[name] = int(str(value).strip())
            except ValueError as exc:
                raise ValueError(f"{spec['label']} must be a whole number.") from exc
        else:
            params[name] = str(value).strip()
    params["workspace_id"] = workspace
    params["ops_agent_run_as_user"] = str(raw_params.get("ops_agent_run_as_user") or "").strip()
    argv = [
        str(PIPELINE_SCRIPT), "--tenant", tenant, "--workspace", workspace,
        "--pipeline", pipeline, "--yes",
    ]
    return argv, {"FABRIC_PIPELINE_PARAMS": json.dumps(params)}


@app.post("/api/run-pipeline")
def api_run_pipeline():
    data = request.get_json(silent=True) or {}
    try:
        steps = _core_setup_steps(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = _start_workflow(steps, phases=CORE_SETUP_PHASES)
    return job_response(job_id)


def _core_setup_steps(data: dict, *, phase_offset: int = 1) -> list[dict[str, object]]:
    argv, env_extra = _pipeline_invocation(data)
    energy_argv = _deploy_invocation({**data, "energyMap": "enabled"}, stage="energy")
    return [
        {
            "name": "Setup pipeline", "argv": argv, "env": env_extra,
            "timeout": PIPELINE_TIMEOUT_S, "markers": PIPELINE_MARKERS, "phase_offset": phase_offset,
        },
        {
            "name": "Energy setup", "argv": energy_argv, "env": None,
            "timeout": ENERGY_TIMEOUT_S, "markers": ENERGY_MARKERS,
            "phase_offset": phase_offset + len(PIPELINE_PHASES) - 2,
        },
    ]


@app.post("/api/deploy-app")
def api_deploy_app():
    data = request.get_json(silent=True) or {}
    try:
        argv = _deploy_invocation(data)
        env = _deploy_environment(data)
        if any(key != "HYDRO_ONTOLOGY_GRAPH_BINDING" for key in env):
            raise ValueError("Use the Foundry multi-agent setup module to change Foundry settings, then deploy the app.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = _start(
        argv, env or None, DEPLOY_TIMEOUT_S, DEPLOY_PHASES, DEPLOY_MARKERS, exclusive=True
    )
    return job_response(job_id)


@app.post("/api/setup-foundry")
def api_setup_foundry():
    return _setup_stage("foundry", FOUNDRY_TIMEOUT_S, FOUNDRY_PHASES, FOUNDRY_MARKERS)


@app.post("/api/setup-energy")
def api_setup_energy():
    return _setup_stage("energy", ENERGY_TIMEOUT_S, ENERGY_PHASES, ENERGY_MARKERS)


def _setup_stage(stage, timeout, phases, markers):
    data = request.get_json(silent=True) or {}
    try:
        argv = _deploy_invocation(data, stage=stage)
        env = _deploy_environment(data) if stage == "foundry" else {}
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return job_response(_start(argv, env or None, timeout, phases, markers, exclusive=True))


FOUNDRY_ENDPOINT_RE = re.compile(r"^https://[a-z0-9-]+\.services\.ai\.azure\.com/api/projects/[A-Za-z0-9_.-]+/?$")
FOUNDRY_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ACR_NAME_RE = re.compile(r"^[a-z0-9]{5,50}$")
AZURE_LOCATION_RE = re.compile(r"^[A-Za-z][A-Za-z ]{2,39}$")


def _deploy_environment(data: dict) -> dict[str, str]:
    """Foundry settings for the deployer, from the form. Only whitelisted, validated, non-secret values are passed,
    as environment variables the deployer already reads. Blank fields pass nothing, so the server's own environment
    (and the deployer's discovery) still applies."""
    def text(key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""

    env: dict[str, str] = {}
    graph_binding = text("ontologyGraphBinding")
    if graph_binding:
        try:
            binding = json.loads(graph_binding)
        except ValueError as error:
            raise ValueError("Ontology graph binding must be a JSON object.") from error
        if not isinstance(binding, dict) or any(
            not isinstance(binding.get(key), str)
            or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", binding[key])
            for key in ("workspaceId", "ontologyId", "graphModelId")
        ):
            raise ValueError("Ontology graph binding requires workspaceId, ontologyId and graphModelId GUIDs.")
        env["HYDRO_ONTOLOGY_GRAPH_BINDING"] = json.dumps(binding, separators=(",", ":"))
    subscription = text("azureSubscriptionId")
    if subscription:
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", subscription):
            raise ValueError("Azure subscription ID must be a GUID in the selected Fabric tenant.")
        env["HYDRO_AZURE_SUBSCRIPTION_ID"] = subscription
    endpoint = text("foundryEndpoint")
    if endpoint:
        if not FOUNDRY_ENDPOINT_RE.fullmatch(endpoint):
            raise ValueError("Foundry project endpoint must look like https://<resource>.services.ai.azure.com/api/projects/<project>.")
        env["HYDRO_FOUNDRY_PROJECT_ENDPOINT"] = endpoint
    project_id = text("foundryProjectResourceId")
    if project_id:
        if not re.fullmatch(
            r"/subscriptions/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
            r"/resourceGroups/[^/?#]+/providers/Microsoft\.CognitiveServices"
            r"/accounts/[A-Za-z0-9-]+/projects/[A-Za-z0-9_.-]+", project_id, re.IGNORECASE,
        ):
            raise ValueError("Foundry project resource ID must be a full project ARM resource ID.")
        env["HYDRO_FOUNDRY_PROJECT_RESOURCE_ID"] = project_id
    model = text("foundryModel")
    if model:
        if not FOUNDRY_MODEL_RE.fullmatch(model):
            raise ValueError("Foundry model deployment name may contain only letters, digits, '.', '_' and '-'.")
        env["HYDRO_FOUNDRY_MODEL"] = model
    for field, key in (
        ("foundryModelName", "HYDRO_FOUNDRY_MODEL_NAME"),
        ("foundryModelVersion", "HYDRO_FOUNDRY_MODEL_VERSION"),
        ("foundryModelSku", "HYDRO_FOUNDRY_MODEL_SKU"),
    ):
        value = text(field)
        if value:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
                raise ValueError("Foundry model name, version and SKU must be valid identifiers.")
            env[key] = value
    capacity = data.get("foundryModelCapacity")
    if capacity not in (None, ""):
        if type(capacity) not in (str, int) or not re.fullmatch(r"[1-9][0-9]*", str(capacity).strip()):
            raise ValueError("Foundry model capacity must be a positive whole number.")
        env["HYDRO_FOUNDRY_MODEL_CAPACITY"] = str(capacity).strip()
    registry = text("acrName")
    if registry:
        if not ACR_NAME_RE.fullmatch(registry):
            raise ValueError("Container registry name must be 5-50 lowercase letters or digits.")
        env["HYDRO_ACR_NAME"] = registry
    location = text("foundryLocation")
    if location:
        if not AZURE_LOCATION_RE.fullmatch(location):
            raise ValueError("Foundry location must be an Azure region such as swedencentral.")
        env["HYDRO_FOUNDRY_LOCATION"] = location
    if "createFoundry" in data:
        if type(data["createFoundry"]) is not bool:
            raise ValueError("Create Foundry must be an explicit boolean.")
        env["HYDRO_FOUNDRY_CREATE"] = str(data["createFoundry"]).lower()
    writes = text("productionWrites").lower()
    if writes:
        if writes not in ("true", "false"):
            raise ValueError("Work-order writes must be enabled or disabled, or left to keep the current setting.")
        env["HYDRO_PRODUCTION_WRITES_ENABLED"] = writes
    return env


def _deploy_invocation(data: dict, *, stage: str = "app") -> list[str]:
    tenant = (data.get("tenant") or "").strip()
    workspace = (data.get("workspace") or "").strip()
    client_id = (data.get("clientId") or "").strip()

    if not tenant or not workspace:
        raise ValueError("tenant and workspace are required.")
    if not TENANT_RE.fullmatch(tenant):
        raise ValueError("tenant must be a GUID or a domain like contoso.onmicrosoft.com.")
    if client_id and not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        client_id,
    ):
        raise ValueError("SPA client id must be a GUID.")
    energy = data.get("energyMap", "keep")
    if energy not in ("keep", "enabled", "disabled"):
        raise ValueError("Energy map must be keep, enabled or disabled.")

    argv = [
        str(DEPLOY_SCRIPT),
        "--tenant", tenant,
        "--workspace", workspace,
        "--stage", stage,
        "--energy-map", energy,
    ]
    if client_id:
        argv += ["--client-id", client_id]
    return argv


@app.post("/api/full-workflow")
def api_full_workflow():
    data = request.get_json(silent=True) or {}
    try:
        sync_argv, sync_env = _sync_invocation(data)
        core_steps = _core_setup_steps(data, phase_offset=len(SYNC_PHASES) - 1)
        deploy_argv = _deploy_invocation(data)
        deploy_env = _deploy_environment(data)
        foundry_argv = _deploy_invocation(data, stage="foundry")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    sync_offset = 1
    foundry_offset = len(SYNC_PHASES) - 1 + len(CORE_SETUP_PHASES) - 2
    deploy_offset = foundry_offset + len(FOUNDRY_PHASES) - 2
    steps = [
        {
            "name": "GitHub sync", "argv": sync_argv, "env": sync_env,
            "timeout": SYNC_TIMEOUT_S, "markers": SYNC_MARKERS, "phase_offset": sync_offset,
        },
        *core_steps,
        {
            "name": "Foundry multi-agent setup", "argv": foundry_argv, "env": deploy_env or None,
            "timeout": FOUNDRY_TIMEOUT_S, "markers": FOUNDRY_MARKERS,
            "phase_offset": foundry_offset,
        },
        {
            "name": "Fabric app deploy", "argv": deploy_argv,
            "env": {key: value for key, value in deploy_env.items()
                    if key == "HYDRO_ONTOLOGY_GRAPH_BINDING"} or None,
            "timeout": DEPLOY_TIMEOUT_S, "markers": DEPLOY_MARKERS,
            "phase_offset": deploy_offset,
        },
    ]
    job_id = _start_workflow(steps, phases=WORKFLOW_PHASES)
    return job_response(job_id)


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify(error="unknown jobId."), 404
    try:
        since = max(0, int(request.args.get("since", "0")))
    except ValueError:
        since = 0
    with job.lock:
        new_lines = job.lines[since:]
        total = len(job.lines)
        phase_index = job.phase_index
        phases = job.phases
        status = job.status
        returncode = job.returncode
        component = job.component
        failed_component = job.failed_component
    return jsonify(
        status=status,
        returncode=returncode,
        done=status != "running",
        phases=phases,
        phaseIndex=phase_index,
        phase=phases[phase_index],
        component=component,
        failedComponent=failed_component,
        lines=new_lines,
        nextSince=total,
    )


@app.post("/api/jobs/cancel-all")
def api_cancel_all_jobs():
    """Cancel every child process currently owned by this local server."""
    with JOBS_LOCK:
        running = [job for job in JOBS.values() if job.status == "running"]
    cancelled = sum(_cancel_job(job) for job in running)
    return jsonify(ok=True, cancelled=cancelled)


def _detached_popen_kwargs() -> dict[str, object]:
    """Popen kwargs that fully untie a child server from this console/session.

    Without these the server dies the moment the launching window is closed; with
    them it keeps running (so the Restart button and closing the terminal both
    behave). No console means its stdout/stderr go to the void -- job output is
    still captured in-memory and streamed to the page.
    """
    kwargs: dict[str, object] = {
        "cwd": os.getcwd(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no inherited console.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _respawn_detached() -> None:
    """Start a fresh, console-independent server, then exit this one.

    Preferred over os.execv, which keeps the replacement bound to the original
    console (so a later window-close still kills it). The new server retry-binds
    port 5000, so it comes up as soon as this process releases it.
    """
    env = os.environ.copy()
    env["FABRIC_UI_NO_BROWSER"] = "1"  # a tab is already open
    subprocess.Popen([sys.executable, *sys.argv], env=env, **_detached_popen_kwargs())
    os._exit(0)


@app.post("/api/restart")
def api_restart():
    """Relaunch the server (console-independent) to pick up edits; refuses mid-job."""
    with JOBS_LOCK:
        if any(j.status == "running" for j in JOBS.values()):
            return jsonify(error="a job is still running; wait for it to finish."), 409
    threading.Timer(0.5, _respawn_detached).start()
    return jsonify(ok=True)


@app.post("/api/shutdown")
def api_shutdown():
    """Stop the local server (needed when it runs detached from any terminal)."""
    with JOBS_LOCK:
        if any(j.status == "running" for j in JOBS.values()):
            return jsonify(error="a job is still running; wait for it to finish."), 409
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return jsonify(ok=True)


@app.post("/api/login")
def api_login():
    """Start an interactive `az login` (opens the browser Entra account picker).

    Runs as a streamed job so the UI can show 'waiting for browser sign-in'. An
    optional tenant scopes the login to a single tenant; otherwise az shows the
    normal account picker so the user can choose which identity to sign in as.
    """
    az = shutil.which("az")
    if not az:
        return jsonify(error="Azure CLI (az) is not installed. Install it from https://aka.ms/installazurecli."), 400
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    if tenant and not TENANT_RE.match(tenant):
        return jsonify(error="tenant must be a GUID or a domain like contoso.onmicrosoft.com."), 400
    # On Windows `az` resolves to az.cmd, which CreateProcess can't launch directly,
    # so route it through cmd.exe. The arg list (never a shell string) plus the
    # validated tenant keeps this free of shell-injection.
    cmd = ["cmd", "/c", az, "login"] if os.name == "nt" else [az, "login"]
    if tenant:
        cmd += ["--tenant", tenant]
    job_id = _start(cmd, None, LOGIN_TIMEOUT_S, LOGIN_PHASES, LOGIN_MARKERS, python=False)
    return job_response(job_id)


@app.get("/api/subscriptions")
def api_subscriptions():
    cmd = "az account list --all -o json" if os.name == "nt" else ["az", "account", "list", "--all", "-o", "json"]
    try:
        proc = subprocess.run(
            cmd, shell=(os.name == "nt"), capture_output=True, text=True, timeout=40, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return jsonify(error=f"Could not list signed-in subscriptions: {exc}"), 503
    if proc.returncode != 0:
        return jsonify(error=(proc.stderr or proc.stdout or "Azure CLI subscription discovery failed").strip()[:500]), 503
    try:
        accounts = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return jsonify(error="Azure CLI returned invalid subscription JSON."), 502
    if not isinstance(accounts, list) or any(not isinstance(account, dict) for account in accounts):
        return jsonify(error="Azure CLI returned an invalid subscription list."), 502
    return jsonify(subscriptions=[
        {"id": account["id"], "name": account.get("name", account["id"]), "tenantId": account["tenantId"]}
        for account in accounts
        if account.get("state") == "Enabled" and account.get("id") and account.get("tenantId")
    ])


@app.get("/api/whoami")
def api_whoami():
    """Report the identity of the current `az login` session (best-effort, cached)."""
    # `az account show` returns the signed-in user (UPN) and the active tenant. A
    # static command with no user input, so shell=True on Windows (to resolve
    # az.cmd on PATH) is safe here.
    cmd = "az account show -o json" if os.name == "nt" else ["az", "account", "show", "-o", "json"]
    try:
        proc = subprocess.run(cmd, shell=(os.name == "nt"), capture_output=True, text=True, timeout=40, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return jsonify(signedIn=False, error=str(exc))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return jsonify(signedIn=False, error="not signed in", detail=detail[:300])
    try:
        acct = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return jsonify(signedIn=False, error="could not parse az output")
    user = acct.get("user") or {}
    return jsonify(
        signedIn=True,
        user=user.get("name"),
        userType=user.get("type"),
        tenantId=acct.get("tenantId"),
        subscription=acct.get("name"),
        subscriptionId=acct.get("id"),
    )


@app.get("/api/version")
def app_version():
    """Return deterministic build identity from the checked-out Git commit."""
    git_executable = shutil.which("git")
    git_metadata = REPO_ROOT / ".git"
    if not git_executable or not git_metadata.exists():
        reason = (
            "Git is not available to the local server process. Install Git for Windows and restart the server."
            if not git_executable
            else f"{REPO_ROOT} is not a Git checkout. Clone the repository instead of downloading/copying only its files."
        )
        timestamp = datetime.fromtimestamp(Path(__file__).stat().st_mtime, UTC).isoformat()
        response = jsonify(
            version="Local copy",
            timestamp=timestamp,
            revision="unavailable",
            detail=reason,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def git_value(*args: str) -> str:
        return subprocess.run(
            [git_executable, "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=True,
            creationflags=NO_WINDOW,
        ).stdout.strip()

    try:
        commit_count = git_value("rev-list", "--count", "HEAD")
        revision = git_value("rev-parse", "--short=8", "HEAD")
        timestamp = git_value("show", "-s", "--format=%cI", "HEAD")
        branch = git_value("branch", "--show-current")
        repository = clean_repo(git_value("remote", "get-url", "origin"))
    except (OSError, subprocess.SubprocessError) as exc:
        timestamp = datetime.fromtimestamp(Path(__file__).stat().st_mtime, UTC).isoformat()
        response = jsonify(
            version="Local copy",
            timestamp=timestamp,
            revision="unavailable",
            detail=f"Git metadata could not be read: {exc}",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    response = jsonify(
        version=f"v1.0.{commit_count}", timestamp=timestamp, revision=revision,
        branch=branch, repository=repository,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/deploy-guide")
def deploy_guide():
    return send_from_directory(REPO_ROOT / "HydroOperationsApp", "DEPLOY.md", mimetype="text/plain")


@app.get("/")
@app.get("/<path:path>")
def serve_frontend(path: str = ""):
    """Serve the self-contained static UI (no build step required)."""
    target = path if path and (STATIC_DIR / path).is_file() else "index.html"
    response = send_from_directory(STATIC_DIR, target)
    response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    # Loopback only -- do not expose this beyond the local machine. threaded=True
    # so the poll endpoint stays responsive while a job runs.
    url = "http://127.0.0.1:5000/"
    if os.environ.get("FABRIC_UI_NO_BROWSER") != "1":
        print(f"\n  Opening {url} in your browser...\n  (leave this window open; close it to stop the app)\n")
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # Retry the bind briefly: on Restart the outgoing server may still hold the
    # port for a moment while this fresh one is coming up.
    for attempt in range(20):
        try:
            app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
            break
        except OSError as exc:
            if attempt < 19 and getattr(exc, "errno", None) in (48, 98, 10048, 10013):
                time.sleep(0.5)
                continue
            raise
