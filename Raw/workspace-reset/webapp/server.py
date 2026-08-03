#!/usr/bin/env python3
"""Local API for the workspace-reset web UI.

Thin Flask backend that drives the two existing CLI scripts (kept as the single
source of truth) as background jobs:
    - sync_workspace_from_git.py  -> POST /api/sync
    - delete_workspace_items.py   -> POST /api/delete

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
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# Repo layout: this file lives in .../workspace-reset/webapp/, the CLI scripts
# live one level up in .../workspace-reset/.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = SCRIPT_DIR / "sync_workspace_from_git.py"
DELETE_SCRIPT = SCRIPT_DIR / "delete_workspace_items.py"
PIPELINE_SCRIPT = SCRIPT_DIR / "run_pipeline.py"
STATIC_DIR = Path(__file__).resolve().parent / "static"

SYNC_TIMEOUT_S = 900
DELETE_TIMEOUT_S = 600
PIPELINE_TIMEOUT_S = 3 * 3600

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
    (5, ("Disconnecting from Git", "disconnected", "Cleaning up connection")),
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

PIPELINE_PHASES = ["Queued", "Resolving pipeline", "Running", "Done"]
PIPELINE_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("Pipeline '",)),
    (2, ("Starting pipeline", "run queued", "job status:")),
]

app = Flask(__name__, static_folder=None)


class Job:
    """In-memory record of one running/finished script invocation."""

    def __init__(self, phases: list[str]) -> None:
        self.id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.lines: list[str] = []
        self.phases = phases
        self.phase_index = 0
        self.status = "running"  # running | succeeded | failed
        self.returncode: int | None = None


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _worker(job: Job, argv: list[str], env_extra: dict[str, str] | None,
            timeout: int, markers: list[tuple[int, tuple[str, ...]]]) -> None:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if env_extra:
        env.update(env_extra)
    try:
        # -u forces the child's stdout to be unbuffered so lines arrive live.
        # DEVNULL stdin makes the child non-interactive (isatty()==False) so it never
        # blocks on a hidden prompt reading the server's own terminal.
        proc = subprocess.Popen(
            [sys.executable, "-u", *argv],
            cwd=str(SCRIPT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        with job.lock:
            job.lines.append(f"[failed to start: {exc}]")
            job.status = "failed"
            job.returncode = -1
        return

    timer = threading.Timer(timeout, proc.kill)
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

    with job.lock:
        job.returncode = proc.returncode
        job.status = "succeeded" if proc.returncode == 0 else "failed"
        if proc.returncode == 0:
            job.phase_index = len(job.phases) - 1


def _start(argv: list[str], env_extra: dict[str, str] | None, timeout: int,
           phases: list[str], markers: list[tuple[int, tuple[str, ...]]]) -> str:
    job = Job(phases)
    with JOBS_LOCK:
        JOBS[job.id] = job
    threading.Thread(
        target=_worker, args=(job, argv, env_extra, timeout, markers), daemon=True
    ).start()
    return job.id


@app.post("/api/sync")
def api_sync():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    workspace = (data.get("workspace") or "").strip()
    repository = (data.get("repository") or "").strip()
    branch = (data.get("branch") or "main").strip() or "main"
    directory = (data.get("directory") or "/").strip() or "/"
    connection_id = (data.get("connectionId") or "").strip()
    keep_connected = bool(data.get("keepConnected"))
    pat = (data.get("pat") or "").strip()

    if not tenant or not workspace or not repository:
        return jsonify(error="tenant, workspace and repository are required."), 400
    # Accept a pasted GitHub URL as well as 'owner/repo'.
    repository = re.sub(r"^(https?://)?(www\.)?github\.com[/:]", "", repository, flags=re.IGNORECASE)
    repository = re.sub(r"^git@github\.com:", "", repository, flags=re.IGNORECASE)
    repository = repository.removesuffix(".git").strip("/")
    if "/" not in repository:
        return jsonify(error="repository must be 'owner/repo' or a GitHub repo URL."), 400
    if not connection_id and not pat:
        return jsonify(error="a PAT is required unless a connection id is reused."), 400

    owner, repo = (part.strip() for part in repository.split("/", 1))
    argv = [
        str(SYNC_SCRIPT),
        "--tenant", tenant,
        "--workspace", workspace,
        "--owner", owner,
        "--repository", repo,
        "--branch", branch,
        "--directory", directory,
        "--yes",
    ]
    env_extra: dict[str, str] = {}
    if connection_id:
        argv += ["--connection-id", connection_id]
    # Pass the PAT whenever supplied: it's the create-new secret, and also the
    # fallback that lets auto-reuse ("yes") make a new connection if none works.
    if pat:
        env_extra["FABRIC_GIT_PAT"] = pat
    if keep_connected:
        argv += ["--keep-connected"]

    job_id = _start(argv, env_extra, SYNC_TIMEOUT_S, SYNC_PHASES, SYNC_MARKERS)
    return jsonify(jobId=job_id)


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
    repository = re.sub(r"^(https?://)?(www\.)?github\.com[/:]", "", repository, flags=re.IGNORECASE)
    repository = re.sub(r"^git@github\.com:", "", repository, flags=re.IGNORECASE)
    repository = repository.removesuffix(".git").strip("/")
    if "/" not in repository:
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
    return jsonify(jobId=job_id)


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
    job_id = _start(argv, None, DELETE_TIMEOUT_S, DELETE_PHASES, DELETE_MARKERS)
    return jsonify(jobId=job_id)


# Declared parameters of the 01_Pipe_Setup pipeline, in display order. Each entry
# drives one field in the UI (label + help + prefilled default) and is validated
# here. Defaults mirror the pipeline's own defaultValue in pipeline-content.json
# (they are ids and secret *names*, not secret values); "int" values are coerced
# so they reach the pipeline as JSON numbers, not strings.
PIPELINE_PARAM_SPEC: list[dict[str, str]] = [
    {"name": "env_suffix", "type": "string", "default": "V6",
     "label": "Environment suffix",
     "help": "Suffix appended to the artifacts this run creates (e.g. the lakehouse). Bump it to keep parallel demo runs from colliding."},
    {"name": "workspace_id", "type": "string", "default": "a79a4b7e-e508-4fa4-8b6f-15deadca0f34",
     "label": "Workspace id",
     "help": "GUID of the Fabric workspace the notebooks build into — normally the same workspace you're running in."},
    {"name": "key_vault_uri", "type": "string", "default": "https://akvfabcapnew.vault.azure.net/",
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
    {"name": "ops_agent_teams_team_id", "type": "string", "default": "c480320e-9204-474b-9b2c-54a53e94f220",
     "label": "Teams team id",
     "help": "GUID of the Microsoft Teams team the operations agent posts to."},
    {"name": "ops_agent_teams_channel_id", "type": "string", "default": "19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2",
     "label": "Teams channel id",
     "help": "Channel (thread) id the operations agent posts to, e.g. 19:...@thread.tacv2."},
    {"name": "ops_agent_run_as_user", "type": "string", "default": "admin@mngenvmcap218279.onmicrosoft.com",
     "label": "Agent run-as user",
     "help": "UPN the operations agent runs as when sending Teams messages."},
    {"name": "per_notebook_timeout_secs", "type": "int", "default": "3600",
     "label": "Per-notebook timeout (secs)",
     "help": "Seconds the orchestrator waits for each notebook before giving up (e.g. 3600)."},
]
PIPELINE_PARAM_NAMES = {p["name"] for p in PIPELINE_PARAM_SPEC}


@app.get("/api/pipeline-params")
def api_pipeline_params():
    """Expose the pipeline's parameter spec so the UI can render labelled fields."""
    return jsonify(pipeline="01_Pipe_Setup", parameters=PIPELINE_PARAM_SPEC)


@app.post("/api/run-pipeline")
def api_run_pipeline():
    data = request.get_json(silent=True) or {}
    tenant = (data.get("tenant") or "").strip()
    workspace = (data.get("workspace") or "").strip()
    pipeline = (data.get("pipeline") or "01_Pipe_Setup").strip() or "01_Pipe_Setup"
    raw_params = data.get("parameters") or {}

    if not tenant or not workspace:
        return jsonify(error="tenant and workspace are required."), 400
    if not isinstance(raw_params, dict):
        return jsonify(error="parameters must be an object of name -> value."), 400

    # Keep only known parameters, drop blanks (so pipeline defaults apply), and
    # coerce ints so they travel as JSON numbers.
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
            except ValueError:
                return jsonify(error=f"{spec['label']} must be a whole number."), 400
        else:
            params[name] = str(value).strip()

    argv = [
        str(PIPELINE_SCRIPT),
        "--tenant", tenant,
        "--workspace", workspace,
        "--pipeline", pipeline,
        "--yes",
    ]
    # Pass parameters as JSON through the environment so values with @ and : (a
    # Teams channel id) never need shell escaping and stay off the command line.
    env_extra = {"FABRIC_PIPELINE_PARAMS": json.dumps(params)}
    job_id = _start(argv, env_extra, PIPELINE_TIMEOUT_S, PIPELINE_PHASES, PIPELINE_MARKERS)
    return jsonify(jobId=job_id)


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
    return jsonify(
        status=status,
        returncode=returncode,
        done=status != "running",
        phases=phases,
        phaseIndex=phase_index,
        phase=phases[phase_index],
        lines=new_lines,
        nextSince=total,
    )


@app.post("/api/restart")
def api_restart():
    """Re-exec this process so server.py edits take effect; refuses while a job runs."""
    with JOBS_LOCK:
        if any(j.status == "running" for j in JOBS.values()):
            return jsonify(error="a job is still running; wait for it to finish."), 409
    # Don't reopen a browser tab on the restart; a tab is already open.
    os.environ["FABRIC_UI_NO_BROWSER"] = "1"
    threading.Timer(0.5, lambda: os.execv(sys.executable, [sys.executable, *sys.argv])).start()
    return jsonify(ok=True)


@app.get("/")
@app.get("/<path:path>")
def serve_frontend(path: str = ""):
    """Serve the self-contained static UI (no build step required)."""
    target = path if path and (STATIC_DIR / path).is_file() else "index.html"
    return send_from_directory(STATIC_DIR, target)


if __name__ == "__main__":
    # Loopback only -- do not expose this beyond the local machine. threaded=True
    # so the poll endpoint stays responsive while a job runs.
    url = "http://127.0.0.1:5000/"
    if os.environ.get("FABRIC_UI_NO_BROWSER") != "1":
        print(f"\n  Opening {url} in your browser...\n  (leave this window open; close it to stop the app)\n")
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
