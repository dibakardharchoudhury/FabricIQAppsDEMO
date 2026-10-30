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

import hashlib
import importlib.util
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
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
PIPELINE_DEFAULTS_FILE = Path(
    os.environ.get("FABRIC_UI_PIPELINE_DEFAULTS_FILE") or Path(__file__).with_name("pipeline-defaults.local.json")
).expanduser().resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent
RAYFIN_TARGET_FILE = REPO_ROOT / "HydroOperationsApp" / "rayfin" / ".env"
_pipeline_defaults_lock = threading.RLock()

SYNC_TIMEOUT_S = 1800
DELETE_TIMEOUT_S = 600
PIPELINE_TIMEOUT_S = 3 * 3600
DEPLOY_TIMEOUT_S = 30 * 60
FOUNDRY_TIMEOUT_S = 90 * 60
ENERGY_TIMEOUT_S = 3 * 3600
COMBINED_TIMEOUT_S = max(ENERGY_TIMEOUT_S, FOUNDRY_TIMEOUT_S) + DEPLOY_TIMEOUT_S
COMBINED_STEP = "Energy + Foundry + app deployment"
WORKFLOW_STEPS = ["GitHub sync", "Setup pipeline", COMBINED_STEP]

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
    "Foundry project", "Fabric backend gateway", "Foundry agents", "Hosted Agent", "Done",
]
FOUNDRY_MARKERS = [
    (1, ("[1/8]",)), (2, ("[2/8]",)), (3, ("[3/8]",)),
    (4, ("Foundry: project",)), (5, ("Foundry: backend gateway",)),
    (6, ("Foundry: agents",)), (7, ("Foundry: Hosted Agent",)),
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
COMBINED_PHASES = [
    "Queued", "Checking prerequisites", "Resolving SPA",
    "Energy + Foundry concurrently",
    "App publication · Signing in", "App publication · Provisioning app",
    "App publication · Syncing backend settings", "App publication · Setting up sign-in",
    "App publication · Checking endpoint and CORS readiness", "Done",
]
COMBINED_MARKERS = DEPLOY_MARKERS
WORKFLOW_PHASES = [
    "Queued",
    *[f"Sync · {phase}" for phase in SYNC_PHASES[1:-1]],
    *[f"Pipeline · {phase}" for phase in PIPELINE_PHASES[1:-1]],
    *[f"Combined · {phase}" for phase in COMBINED_PHASES[1:-1]],
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
    """Live job record; Full Deploy checkpoints persist separately."""

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
        self.started = time.monotonic()
        self.ended: float | None = None
        self.phase_timings = [
            {"name": name, "status": "pending", "started": None, "ended": None}
            for name in phases
        ]
        self.phase_timings[0].update(status="running", started=self.started)
        self.component_timings: dict[str, dict] = {}
        self.workflow_key: str | None = None
        self.workflow_steps: list[str] = []
        self.completed_steps: list[str] = []
        self.resumed_by: str | None = None
        self.source_keys: dict[str, str] = {}
        self.pending_notebook_sync: list[str] = []

    def advance_phase(self, index: int) -> None:
        """Called under job.lock; omitted log markers have no invented duration."""
        if index <= self.phase_index:
            return
        now = time.monotonic()
        previous = self.phase_timings[self.phase_index]
        if previous["status"] == "running":
            previous.update(status="succeeded", ended=now)
        for timing in self.phase_timings[self.phase_index + 1:index]:
            if timing["status"] == "pending":
                timing["status"] = "unreported"
        self.phase_index = index
        self.phase_timings[index].update(status="running", started=now)

    def finish_component(self, name: str, status: str, first: int, last: int) -> None:
        now = time.monotonic()
        self.component_timings[name].update(status=status, ended=now)
        for timing in self.phase_timings[first:last]:
            if timing["status"] == "running":
                timing.update(status=status, ended=now)
            elif status == "succeeded" and timing["status"] == "pending":
                timing["status"] = "unreported"

    def finish_timing(self, status: str) -> None:
        if self.ended is None:
            self.ended = time.monotonic()
        for timing in self.phase_timings:
            if timing["status"] == "running":
                timing.update(status=status, ended=self.ended)

    def timing_snapshot(self) -> dict:
        now = self.ended if self.ended is not None else time.monotonic()

        def snapshot(timing):
            start = timing["started"]
            return {
                "name": timing["name"], "status": timing["status"],
                "elapsedSeconds": None if start is None else max(
                    0, (timing["ended"] if timing["ended"] is not None else now) - start,
                ),
            }

        return {
            "elapsedSeconds": max(0, now - self.started),
            "phaseTimings": [snapshot(timing) for timing in self.phase_timings],
            "componentTimings": [snapshot(timing) for timing in self.component_timings.values()],
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _write_local_settings(value: dict) -> None:
    PIPELINE_DEFAULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PIPELINE_DEFAULTS_FILE.with_name("." + PIPELINE_DEFAULTS_FILE.name + ".writing")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(PIPELINE_DEFAULTS_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def _save_workflow_checkpoints(*jobs: Job) -> None:
    records = {}
    for job in jobs:
        if job.workflow_key is None:
            continue
        with job.lock:
            records[job.id] = {
                "version": 2, "key": job.workflow_key, "steps": job.workflow_steps,
                "completed": job.completed_steps, "sources": job.source_keys,
                "status": job.status, "returncode": job.returncode,
                "failedComponent": job.failed_component, "resumedBy": job.resumed_by,
                "pendingNotebookSync": job.pending_notebook_sync,
                **job.timing_snapshot(),
            }
    if not records:
        return
    with _pipeline_defaults_lock:
        settings = json.loads(PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8")) if PIPELINE_DEFAULTS_FILE.exists() else {
            "version": 2, "targets": [],
        }
        settings.setdefault("workflowCheckpoints", {}).update(records)
        _write_local_settings(settings)


class LegacyWorkflowCheckpoint(ValueError):
    def __init__(self, record: dict) -> None:
        self.completed_steps = record.get("completed", [])
        self.previous_status = record.get("status", "unknown")
        stages = ["GitHub sync", "Setup pipeline", "Energy setup",
                  "Foundry multi-agent setup", "Fabric app deploy"]
        if (record.get("steps") != stages or not isinstance(self.completed_steps, list)
                or self.completed_steps != stages[:len(self.completed_steps)]
                or self.previous_status not in {"running", "succeeded", "failed", "cancelled", "interrupted"}):
            raise ValueError("Invalid local legacy Full Deploy checkpoint.")
        super().__init__(
            "Legacy five-step Full Deploy checkpoint cannot be resumed by the combined workflow. "
            f"Recorded completed stages: {self.completed_steps}. "
            "Checkpoint retained; verify Fabric before explicitly starting a new Full Deploy."
        )


def _restore_workflow(job_id: str) -> Job | None:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return None
    with JOBS_LOCK:
        if job_id in JOBS:
            return JOBS[job_id]
        with _pipeline_defaults_lock:
            if not PIPELINE_DEFAULTS_FILE.exists():
                return None
            settings = json.loads(PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))
            record = settings.get("workflowCheckpoints", {}).get(job_id)
        if not record:
            return None
        if record.get("version") == 1:
            raise LegacyWorkflowCheckpoint(record)
        names = WORKFLOW_STEPS
        completed = record.get("completed")
        if (record.get("version") != 2 or record.get("steps") != names or not isinstance(completed, list)
                or completed != names[:len(completed)]
                or not re.fullmatch(r"[a-f0-9]{64}", str(record.get("key", "")))
                or not set(completed).issubset({timing["name"] for timing in record.get("componentTimings", [])})):
            raise ValueError("Invalid local Full Deploy checkpoint.")
        job = Job(WORKFLOW_PHASES, exclusive=True)
        job.id = job_id
        job.workflow_key = record["key"]
        job.workflow_steps = names
        job.completed_steps = completed
        job.source_keys = record.get("sources", {})
        job.pending_notebook_sync = record.get("pendingNotebookSync", [])
        if (not isinstance(job.pending_notebook_sync, list)
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
                       for name in job.pending_notebook_sync)):
            raise ValueError("Invalid pending notebook synchronization checkpoint.")
        job.status = record["status"] if record["status"] in {"failed", "cancelled", "succeeded"} else "interrupted"
        job.returncode = record.get("returncode")
        job.failed_component = record.get("failedComponent")
        job.resumed_by = record.get("resumedBy")
        job.finish_timing(job.status)
        assert job.ended is not None
        restored_at = job.ended
        job.started = restored_at - float(record.get("elapsedSeconds", 0))
        # Monotonic timestamps cannot survive a restart; retain measured durations.
        def restore_timing(timing):
            elapsed = timing.get("elapsedSeconds")
            return {
                "name": timing["name"], "status": timing["status"],
                "started": None if elapsed is None else restored_at - float(elapsed),
                "ended": restored_at,
            }
        if len(record.get("phaseTimings", [])) == len(job.phases):
            job.phase_timings = [restore_timing(timing) for timing in record["phaseTimings"]]
        job.component_timings = {
            timing["name"]: restore_timing(timing) for timing in record.get("componentTimings", [])
        }
        job.phase_index = next((index for index, timing in enumerate(job.phase_timings)
                                if timing["status"] in {"failed", "cancelled", "running"}), 0)
        job.lines.append("[workflow] Restored local checkpoint; previous live output is not persisted.")
        JOBS[job_id] = job
        return job


def _workflow_source_keys() -> dict[str, str]:
    core, energy = [], []
    definitions = {}
    for folder in ("Notebooks", "Environments", "Orchestrator_Pipelines"):
        for path in (REPO_ROOT / folder).rglob("*"):
            if path.is_file():
                relative = path.relative_to(REPO_ROOT)
                (energy if any(part.startswith("Geo_") for part in relative.parts) else core).append(path)
                definitions["file:" + relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    core.append(PIPELINE_SCRIPT)
    energy.extend((DEPLOY_SCRIPT, SCRIPT_DIR / "feature_workspace.py"))
    foundry = [DEPLOY_SCRIPT, SCRIPT_DIR / "provision_foundry_agents.py",
               SCRIPT_DIR / "deploy_foundry_project.py", SCRIPT_DIR / "deploy_hosted_agent.py"]
    if (REPO_ROOT / "HydroOperationsService" / "Dockerfile").exists():
        spec = importlib.util.spec_from_file_location(
            "workflow_hosted_sources", Path(__file__).resolve().parent.parent / "deploy_hosted_agent.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load the Hosted Agent source manifest.")
        hosted = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hosted)
        foundry.extend(hosted.packaged_sources(REPO_ROOT))
    groups = {
        "Setup pipeline": core, "Energy setup": energy,
        "Foundry multi-agent setup": foundry,
    }
    app_root = REPO_ROOT / "HydroOperationsApp"
    app_sources = [
        path for folder in ("src", "scripts", "public") for path in (app_root / folder).rglob("*")
        if path.is_file()
    ]
    app_sources.extend(path for path in app_root.glob("*") if path.is_file()
                       and path.suffix in {".json", ".ts", ".js", ".html"})
    app_sources.extend(path for path in (app_root / "rayfin").glob("*.yml") if path.is_file())
    groups[COMBINED_STEP] = list(set(energy + foundry + app_sources + [SCRIPT_DIR / "webapp" / "server.py"]))
    return definitions | {
        name: hashlib.sha256(b"".join(
            str(path.relative_to(REPO_ROOT)).encode() + b"\0" + path.read_bytes()
            for path in sorted(paths) if path.is_file()
        )).hexdigest()
        for name, paths in groups.items()
    }

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
            job.finish_timing(job.status)
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
                        job.advance_phase(idx)
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
        job.finish_timing(job.status)
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
        job.advance_phase(phase_offset)
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
                        job.advance_phase(target)
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
    try:
        _workflow_worker_steps(job, steps)
    except Exception as exc:
        with job.lock:
            job.status = "failed"
            job.returncode = -1
            job.finish_timing(job.status)
            job.lines.append(f"[workflow stopped] {type(exc).__name__}: {exc}")
    finally:
        try:
            _save_workflow_checkpoints(job)
        except (OSError, ValueError, TypeError) as exc:
            with job.lock:
                job.lines.append(f"[checkpoint unavailable] {exc}; verify Fabric before retrying.")


def _workflow_worker_steps(job: Job, steps: list[dict[str, object]]) -> None:
    steps = steps[len(job.completed_steps):]
    for step_index, step in enumerate(steps):
        if job.cancel_requested:
            break
        name = str(step["name"])
        first = int(step["phase_offset"])
        last = int(steps[step_index + 1]["phase_offset"]) if step_index + 1 < len(steps) else len(job.phases) - 1
        with job.lock:
            job.component_timings[name] = {
                "name": name, "status": "running", "started": time.monotonic(), "ended": None,
            }
        try:
            if job.pending_notebook_sync:
                argv = [str(SYNC_SCRIPT), "--tenant", str(step["tenant"]), "--workspace", str(step["workspace"]), "--yes"]
                for notebook in job.pending_notebook_sync:
                    argv.extend(("--update-notebook", notebook))
                returncode = _run_workflow_step(
                    job, "Changed notebook sync", argv, None, SYNC_TIMEOUT_S, [], first,
                )
                if returncode != 0 or job.cancel_requested:
                    with job.lock:
                        job.failed_component = name
                        job.status = "cancelled" if job.cancel_requested else "failed"
                        job.returncode = returncode
                        job.finish_component(name, job.status, first, last)
                        job.finish_timing(job.status)
                        job.lines.append("[workflow stopped] Changed notebook sync did not complete; no setup step was retried.")
                    return
                with job.lock:
                    job.pending_notebook_sync = []
                _save_workflow_checkpoints(job)
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
                job.finish_component(name, "failed", first, last)
                job.finish_timing(job.status)
                job.lines.append(
                    f"[workflow stopped] {name} monitor failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            return
        with job.lock:
            job.finish_component(name, "cancelled" if job.cancel_requested else
                                 "succeeded" if returncode == 0 else "failed", first, last)
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
                job.finish_timing(job.status)
            return
        with job.lock:
            if job.cancel_requested:
                break
            job.completed_steps.append(name)
            job.lines.append(f"[workflow] {name} completed successfully.")
        _save_workflow_checkpoints(job)

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
        job.finish_timing(job.status)


def _start_workflow(
    steps: list[dict[str, object]], *, phases: list[str] | None = None,
    workflow_key: str | None = None, resume_from: Job | None = None,
    source_keys: dict[str, str] | None = None, restart_at: int | None = None,
    pending_notebook_sync: list[str] | None = None,
) -> str | None:
    job = Job(WORKFLOW_PHASES if phases is None else phases, exclusive=True)
    job.workflow_key = workflow_key
    job.workflow_steps = [str(step["name"]) for step in steps]
    job.source_keys = source_keys or {}
    job.pending_notebook_sync = pending_notebook_sync or []
    with JOBS_LOCK:
        running = [existing for existing in JOBS.values() if existing.status == "running"]
        if running:
            return None
        if resume_from is not None:
            with resume_from.lock:
                if resume_from.resumed_by is not None:
                    return None
                resume_from.resumed_by = job.id
                job.completed_steps = list(resume_from.completed_steps[:restart_at])
                first = int(steps[len(job.completed_steps)]["phase_offset"])
                for index in range(1, first):
                    job.phase_timings[index] = dict(resume_from.phase_timings[index])
                for name in job.completed_steps:
                    job.component_timings[name] = dict(resume_from.component_timings[name])
                job.lines.append(
                    f"[workflow] Resuming {resume_from.id} at {steps[len(job.completed_steps)]['name']}; "
                    "retaining completed steps: " + (", ".join(job.completed_steps) or "none") + "."
                )
        JOBS[job.id] = job
        try:
            _save_workflow_checkpoints(*([resume_from] if resume_from else []), job)
        except (OSError, ValueError, TypeError):
            JOBS.pop(job.id)
            if resume_from:
                resume_from.resumed_by = None
            raise
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


def _sync_invocation(data: dict, *, require_credentials: bool = True) -> tuple[list[str], dict[str, str]]:
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
    if require_credentials and not connection_id and not pat:
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
     "help": "Separates this run's artifacts."},
    {"name": "workspace_id", "type": "string", "default": "",
     "label": "Workspace name or ID",
     "help": "From the sidebar."},
    {"name": "key_vault_uri", "type": "string", "default": "",
     "label": "Key Vault URI",
     "help": "https://<vault>.vault.azure.net/"},
    {"name": "key_vault_tenant_id_secret_name", "type": "string", "default": "tenantid",
     "label": "KV secret · tenant id",
     "help": "Secret name for the SPN tenant ID."},
    {"name": "key_vault_client_id_secret_name", "type": "string", "default": "clientid",
     "label": "KV secret · client id",
     "help": "Secret name for the SPN client ID."},
    {"name": "key_vault_client_secret_name", "type": "string", "default": "clientsecret",
     "label": "KV secret · client secret",
     "help": "Secret name, not the credential value."},
    {"name": "ops_agent_teams_team_id", "type": "string", "default": "",
     "label": "Teams team id",
     "help": "Channel link: GUID after groupId=.",
     "helpUrl": "https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-teams#deep-link-to-navigate-to-channel"},
    {"name": "ops_agent_teams_channel_id", "type": "string", "default": "",
     "label": "Teams channel id",
     "help": "URL-decode the link's /channel/ ID: 19:...@thread.tacv2.",
     "helpUrl": "https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-teams#deep-link-to-navigate-to-channel"},
    {"name": "ops_agent_run_as_user", "type": "string", "default": "",
     "label": "Agent run-as user",
     "help": "Blank uses the signed-in user."},
    {"name": "alert_email_to", "type": "string", "default": "",
     "label": "Alert email recipient",
     "help": "Email alert destination, independent of Teams and run-as."},
    {"name": "per_notebook_timeout_secs", "type": "int", "default": "3600",
     "label": "Per-notebook timeout (secs)",
     "help": "Maximum seconds per notebook."},
]
PIPELINE_PARAM_NAMES = {p["name"] for p in PIPELINE_PARAM_SPEC}


def _pipeline_values(values: object) -> dict[str, str]:
    if not isinstance(values, dict):
        raise ValueError("Pipeline parameters must be a JSON object.")
    result = {}
    for spec in PIPELINE_PARAM_SPEC:
        name = spec["name"]
        if name == "workspace_id" or name not in values:
            continue
        value = values[name]
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError(f"Configured {spec['label']} must be a string or integer.")
        value = str(value).strip()
        if spec["type"] == "int" and value:
            try:
                int(value)
            except ValueError as exc:
                raise ValueError(f"Configured {spec['label']} must be a whole number.") from exc
        result[name] = value
    return result


def _local_pipeline_targets() -> list[dict]:
    if not PIPELINE_DEFAULTS_FILE.exists():
        return []
    local = json.loads(PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(local, dict):
        raise ValueError("Local pipeline defaults must be a JSON object.")
    targets = local.get("targets") if local.get("version") == 2 else [local]
    if not isinstance(targets, list):
        raise ValueError("Local pipeline defaults require a targets list.")
    result = []
    for target in targets:
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("tenant"), str) or not target["tenant"].strip()
            or not isinstance(target.get("workspace"), str) or not target["workspace"].strip()
            or not isinstance(target.get("parameters"), dict)
            or not isinstance(target.get("workspaceAliases", []), list)
            or not all(isinstance(alias, str) and alias.strip() for alias in target.get("workspaceAliases", []))
            or not isinstance(target.get("saved", False), bool)
        ):
            raise ValueError("Local pipeline defaults require tenant, workspace and a parameters object with valid workspace aliases.")
        result.append({
            "tenant": target["tenant"].strip(), "workspace": target["workspace"].strip(),
            "parameters": _pipeline_values(target["parameters"]),
            "workspaceAliases": target.get("workspaceAliases", []), "saved": target.get("saved", False),
        })
    return result


def _pipeline_workspace_aliases(tenant: str, workspace: str) -> set[str]:
    aliases = {workspace.strip().casefold()}
    if not RAYFIN_TARGET_FILE.exists():
        return aliases
    values = {}
    for line in RAYFIN_TARGET_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.strip() in {"RAYFIN_PUBLIC_TENANT_ID", "RAYFIN_PUBLIC_WORKSPACE_ID", "FABRIC_WORKSPACE_NAME"}:
                values[key.strip()] = value.strip().strip("'\"").casefold()
    # The deployment orchestrator writes these together after resolving the workspace.
    if values.get("RAYFIN_PUBLIC_TENANT_ID") == tenant.strip().casefold():
        known = {values.get("RAYFIN_PUBLIC_WORKSPACE_ID", ""), values.get("FABRIC_WORKSPACE_NAME", "")} - {""}
        if aliases & known:
            aliases.update(known)
    return aliases


def _pipeline_target_matches(target: dict, tenant: str, aliases: set[str]) -> bool:
    known = {target["workspace"].strip().casefold()}
    known.update(alias.strip().casefold() for alias in target.get("workspaceAliases", []))
    return target["tenant"].strip().casefold() == tenant.strip().casefold() and bool(known & aliases)


def _pipeline_configurations(targets: list[dict]) -> list[tuple[dict, str]]:
    configured = os.environ.get("FABRIC_PIPELINE_PARAMS", "").strip()
    tenant = os.environ.get("FABRIC_TENANT", "").strip()
    workspace = os.environ.get("FABRIC_WORKSPACE", "").strip()
    configurations = [(target, "local configuration") for target in targets if not target.get("saved")]
    if configured:
        if not tenant or not workspace:
            raise ValueError("Pipeline defaults require a tenant and workspace (FABRIC_TENANT and FABRIC_WORKSPACE for environment defaults).")
        try:
            values = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise ValueError("FABRIC_PIPELINE_PARAMS must be a JSON object.") from exc
        if not isinstance(values, dict):
            raise ValueError("FABRIC_PIPELINE_PARAMS must be a JSON object.")
        configurations.append(({"tenant": tenant, "workspace": workspace, "parameters": _pipeline_values(values)}, "server configuration"))
    configurations.extend((target, "saved local settings") for target in targets if target.get("saved"))
    return configurations


@app.get("/api/pipeline-params")
def api_pipeline_params():
    """Expose target-specific defaults without authentication or connection jobs."""
    parameters = [dict(spec) for spec in PIPELINE_PARAM_SPEC]
    try:
        tenant = request.args.get("tenant", "").strip()
        workspace = request.args.get("workspace", "").strip()
        configurations = _pipeline_configurations(_local_pipeline_targets())
        aliases = _pipeline_workspace_aliases(tenant, workspace)
        for target, source in configurations:
            if not _pipeline_target_matches(target, tenant, aliases):
                continue
            for spec in parameters:
                value = target["parameters"].get(spec["name"])
                if value is None:
                    continue
                spec["default"] = str(value).strip()
                spec["source"] = source
    except (OSError, UnicodeError, ValueError) as exc:
        return jsonify(error=f"Could not read pipeline defaults: {exc}"), 400
    response = jsonify(pipeline="01_Pipe_Setup", parameters=parameters)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/pipeline-params")
def api_save_pipeline_params():
    data = request.get_json(silent=True)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("tenant"), str) or not data["tenant"].strip()
        or not isinstance(data.get("workspace"), str) or not data["workspace"].strip()
        or not isinstance(data.get("parameters"), dict)
        or set(data["parameters"]) - (PIPELINE_PARAM_NAMES - {"workspace_id"})
    ):
        return jsonify(error="Saving requires tenant, workspace and only non-secret setup parameters (not workspace_id or PAT)."), 400
    try:
        tenant, workspace = data["tenant"].strip(), data["workspace"].strip()
        incoming = _pipeline_values(data["parameters"])
        with _pipeline_defaults_lock:
            targets = _local_pipeline_targets()
            aliases = _pipeline_workspace_aliases(tenant, workspace)
            values = {}
            for target, _ in _pipeline_configurations(targets):
                if _pipeline_target_matches(target, tenant, aliases):
                    values.update(target["parameters"])
                    aliases.update(alias.casefold() for alias in target.get("workspaceAliases", []))
                    aliases.add(target["workspace"].strip().casefold())
            values.update(incoming)
            retained = [target for target in targets if not _pipeline_target_matches(target, tenant, aliases)]
            retained.append({
                "tenant": tenant, "workspace": workspace, "workspaceAliases": sorted(aliases),
                "parameters": values, "saved": True,
            })
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=PIPELINE_DEFAULTS_FILE.parent,
                    prefix=".pipeline-defaults-", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    existing = json.loads(PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8")) if PIPELINE_DEFAULTS_FILE.exists() else {}
                    json.dump({
                        "version": 2, "targets": retained,
                        "workflowCheckpoints": existing.get("workflowCheckpoints", {}),
                    }, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(PIPELINE_DEFAULTS_FILE)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except (UnicodeError, ValueError) as exc:
        return jsonify(error=f"Could not save pipeline defaults: {exc}"), 400
    except OSError as exc:
        return jsonify(error=f"Could not save pipeline defaults: {exc}"), 500
    response = jsonify(saved=True)
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
        "--pipeline", pipeline, "--yes", "--require-parallel-energy",
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
    energy_argv.insert(1, "--energy-read-models-only")
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
    if data.get("foundryAccountName") is not None and not isinstance(data["foundryAccountName"], str):
        raise ValueError("Foundry account name must be text.")
    account_name = text("foundryAccountName")
    if account_name:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]", account_name):
            raise ValueError("Foundry account name must be 2-64 letters, digits or hyphens, starting and ending with a letter or digit.")
        env["HYDRO_FOUNDRY_ACCOUNT_NAME"] = account_name
    project_name = text("foundryProjectName")
    if project_name:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", project_name):
            raise ValueError("Existing Foundry project name may contain only letters, digits, '.', '_' and '-'.")
        env["HYDRO_FOUNDRY_EXISTING_PROJECT_NAME"] = project_name
    if data.get("foundryNewProjectName") is not None and not isinstance(data["foundryNewProjectName"], str):
        raise ValueError("New Foundry project name must be text.")
    new_project_name = text("foundryNewProjectName")
    if new_project_name:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", new_project_name):
            raise ValueError("New Foundry project name must be 1-64 letters, digits, dots, underscores or hyphens, starting with a letter or digit.")
        if data.get("createFoundry") is not True:
            raise ValueError("A new Foundry project name requires explicit resource-creation approval.")
        env["HYDRO_FOUNDRY_NEW_PROJECT_NAME"] = new_project_name
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
    if stage == "app" and energy != "keep":
        raise ValueError("App publication preserves the prepared energy-map setting. Run Energy setup first.")

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
    if not isinstance(data, dict):
        return jsonify(error="workflow input must be an object."), 400
    resume_from = None
    resume_id = data.get("resumeJobId")
    if resume_id is not None:
        if not isinstance(resume_id, str):
            return jsonify(error="resumeJobId must be a job ID."), 400
        try:
            resume_from = JOBS.get(resume_id) or _restore_workflow(resume_id)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return jsonify(error=f"Could not read local resume checkpoint: {exc}. No steps were started."), 409
        if resume_from is None:
            return jsonify(error="Resume checkpoint is unavailable. "
                           "No steps were started; check Fabric before explicitly starting a new Full Deploy."), 409
        with resume_from.lock:
            if resume_from.workflow_steps != WORKFLOW_STEPS:
                return jsonify(
                    error="Legacy or incompatible Full Deploy checkpoint cannot be resumed by the combined workflow. "
                          f"Recorded completed stages: {resume_from.completed_steps}. Checkpoint retained; "
                          "verify Fabric before explicitly starting a new Full Deploy. No steps were started."
                ), 409
            if (resume_from.workflow_key is None or resume_from.status not in {"failed", "cancelled"}
                    or resume_from.resumed_by is not None
                    or len(resume_from.completed_steps) >= len(resume_from.workflow_steps)):
                return jsonify(error="This job has no available failed/cancelled Full Deploy checkpoint. "
                               "No steps were started."), 409
    try:
        sync_argv, sync_env = _sync_invocation(
            data, require_credentials=not (resume_from and "GitHub sync" in resume_from.completed_steps),
        )
        pipeline_argv, pipeline_env = _pipeline_invocation(data)
        deploy_argv = _deploy_invocation({**data, "energyMap": "enabled"}, stage="all")
        deploy_argv.insert(1, "--energy-read-models-only")
        deploy_env = _deploy_environment(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    sync_offset = 1
    pipeline_offset = len(SYNC_PHASES) - 1
    combined_offset = pipeline_offset + len(PIPELINE_PHASES) - 2
    steps = [
        {
            "name": "GitHub sync", "argv": sync_argv, "env": sync_env,
            "timeout": SYNC_TIMEOUT_S, "markers": SYNC_MARKERS, "phase_offset": sync_offset,
        },
        {
            "name": "Setup pipeline", "argv": pipeline_argv, "env": pipeline_env,
            "timeout": PIPELINE_TIMEOUT_S, "markers": PIPELINE_MARKERS,
            "phase_offset": pipeline_offset,
        },
        {
            "name": COMBINED_STEP, "argv": deploy_argv, "env": deploy_env or None,
            "timeout": COMBINED_TIMEOUT_S, "markers": COMBINED_MARKERS,
            "phase_offset": combined_offset,
        },
    ]
    # Hash effective invocations, never the PAT. Credentials may be supplied again
    # for a failed sync; successful sync needs no credentials on retry.
    checkpoint = [
        {"name": step["name"], "argv": step["argv"],
         "env": {key: value for key, value in (step.get("env") or {}).items() if key != "FABRIC_GIT_PAT"}}
        for step in steps
    ]
    inherited = {
        key: value for key, value in os.environ.items()
        if key.startswith(("HYDRO_", "RAYFIN_PUBLIC_"))
        and not any(secret in key for secret in ("TOKEN", "PASSWORD", "SECRET", "PAT"))
    }
    workflow_key = hashlib.sha256(json.dumps([checkpoint, inherited], sort_keys=True).encode()).hexdigest()
    if resume_from is not None and resume_from.workflow_key != workflow_key:
        return jsonify(error="Full Deploy inputs changed (target, Git source, setup or app/Foundry settings). "
                       "Restore the original inputs to resume, or explicitly start a new Full Deploy. "
                       "No steps were started."), 409
    try:
        sources = _workflow_source_keys()
        restart_at = None
        pending_notebooks = list(resume_from.pending_notebook_sync) if resume_from else []
        if resume_from:
            changed = [name for name in set(sources) | set(resume_from.source_keys)
                       if sources.get(name) != resume_from.source_keys.get(name)]
            changed_files = [name[5:] for name in changed if name.startswith("file:")]
            notebook_files = [
                path for path in changed_files if path.startswith("Notebooks/") and path.endswith(".Notebook/notebook-content.py")
                and "file:" + path in sources
            ]
            other_definitions = set(changed_files) - set(notebook_files)
            checkout_differs = False
            if notebook_files and not other_definitions:
                checkout = app_version().get_json()
                checkout_differs = (
                    checkout.get("repository") != clean_repo(str(data.get("repository", "")))
                    or checkout.get("branch") != str(data.get("branch") or "main").strip()
                )
            if changed_files and "GitHub sync" in resume_from.completed_steps:
                if data.get("syncLatest") is not True:
                    return jsonify(
                        error="Fabric definitions changed. Sync updated definitions before retrying. No steps were started.",
                        requiresSync=True, fullSyncRequired=bool(other_definitions) or checkout_differs,
                        changedNotebooks=[Path(path).parent.name.removesuffix(".Notebook") for path in sorted(notebook_files)],
                    ), 409
                if other_definitions or checkout_differs:
                    # Changed environments, pipelines, deletions and item metadata need Git integration.
                    _sync_invocation(data)
                    restart_at = 0
                    pending_notebooks = []
                else:
                    pending_notebooks = sorted(set(pending_notebooks) | {
                        Path(path).parent.name.removesuffix(".Notebook") for path in notebook_files
                    })
            if restart_at is None:
                affected = [resume_from.workflow_steps.index(name) for name in changed
                            if name in resume_from.completed_steps]
                restart_at = min(affected) if affected else None
        for step in steps:
            step.update(tenant=str(data.get("tenant", "")).strip(), workspace=str(data.get("workspace", "")).strip())
        job_id = _start_workflow(
            steps, phases=WORKFLOW_PHASES, workflow_key=workflow_key, resume_from=resume_from,
            source_keys=sources, restart_at=restart_at,
            pending_notebook_sync=pending_notebooks,
        )
    except (OSError, ValueError, TypeError) as exc:
        return jsonify(error=f"Could not prepare durable workflow checkpoint: {exc}. No steps were started."), 400
    return job_response(job_id)


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    try:
        job = JOBS.get(job_id) or _restore_workflow(job_id)
    except LegacyWorkflowCheckpoint as exc:
        notice = (
            "This previous deployment uses the older five-stage workflow. "
            "Automatic resume is unavailable; the checkpoint is retained. "
            "No new deployment has been started."
        )
        return jsonify(
            status="historical", done=True, canResume=False,
            previousStatus=exc.previous_status, completedSteps=exc.completed_steps,
            resumeBlockedReason=notice,
            lines=[
                notice,
                f"Recorded previous outcome: {exc.previous_status}.",
                f"Recorded completed stages: {exc.completed_steps}.",
            ],
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return jsonify(error=f"Could not read local workflow checkpoint: {exc}"), 409
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
        timings = job.timing_snapshot()
        completed_steps = list(job.completed_steps)
        can_resume = (job.workflow_key is not None and status in {"failed", "cancelled"}
                      and job.resumed_by is None and len(completed_steps) < len(job.workflow_steps))
        resume_component = job.workflow_steps[len(completed_steps)] if can_resume else None
    return jsonify(
        status=status,
        returncode=returncode,
        done=status != "running",
        phases=phases,
        phaseIndex=phase_index,
        phase=phases[phase_index],
        component=component,
        failedComponent=failed_component,
        completedSteps=completed_steps,
        canResume=can_resume,
        resumeComponent=resume_component,
        resumeBlockedReason=(
            "The local server stopped while this run was active. Verify remote Fabric jobs before "
            "explicitly starting another run; automatic resume could duplicate active work."
            if status == "interrupted" else None
        ),
        lines=new_lines,
        nextSince=total,
        **timings,
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
        branch=branch, repository=repository, checkout=str(REPO_ROOT),
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
