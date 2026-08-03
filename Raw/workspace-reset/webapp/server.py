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

import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

# Repo layout: this file lives in .../workspace-reset/webapp/, the CLI scripts
# live one level up in .../workspace-reset/.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = SCRIPT_DIR / "sync_workspace_from_git.py"
DELETE_SCRIPT = SCRIPT_DIR / "delete_workspace_items.py"
STATIC_DIR = Path(__file__).resolve().parent / "static"

SYNC_TIMEOUT_S = 900
DELETE_TIMEOUT_S = 600

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
        proc = subprocess.Popen(
            [sys.executable, "-u", *argv],
            cwd=str(SCRIPT_DIR),
            env=env,
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
    else:
        env_extra["FABRIC_GIT_PAT"] = pat
    if keep_connected:
        argv += ["--keep-connected"]

    job_id = _start(argv, env_extra, SYNC_TIMEOUT_S, SYNC_PHASES, SYNC_MARKERS)
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
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
