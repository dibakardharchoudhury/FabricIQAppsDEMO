"""Bounded launcher fan-out; every worker uses the existing module API and CLI."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
EVENT_PREFIX = "PARALLEL_DEPLOY_EVENT "
PAYLOAD_ENV = "FABRIC_PARALLEL_PAYLOAD"
ACTIONS = {
    "/api/sync", "/api/run-pipeline", "/api/setup-energy",
    "/api/setup-foundry", "/api/deploy-app", "/api/full-workflow",
    "/api/delete", "/api/test-connection",
}
OUTPUT_LOCK = threading.Lock()


def save_state(path: Path, record: dict) -> None:
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_request(value: object) -> tuple[str, list[str], dict]:
    if not isinstance(value, dict):
        raise ValueError("Parallel deployment requires a JSON object.")
    action = value.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("Parallel mode supports Full Deploy, deployment modules, deletion and connection tests.")
    raw = value.get("workspaces")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3 or any(not isinstance(item, str) for item in raw):
        raise ValueError("Provide up to three workspace slots as strings.")
    workspaces = [item.strip() for item in raw if item.strip()]
    if not workspaces:
        raise ValueError("Enter at least one workspace; empty slots are ignored.")
    if len({item.casefold() for item in workspaces}) != len(workspaces):
        raise ValueError("Each workspace must appear only once.")
    data = value.get("data")
    if not isinstance(data, dict):
        raise ValueError("Deployment settings must be a JSON object.")
    if not isinstance(data.get("tenant"), str) or not data["tenant"].strip():
        raise ValueError("A tenant is required.")
    if data.get("resumeJobId"):
        raise ValueError("Use the workspace's retry control to resume a parallel Full Deploy.")
    if action == "/api/delete":
        if type(data.get("dryRun")) is not bool:
            raise ValueError("Deletion requires an explicit boolean dryRun setting.")
        if not data["dryRun"]:
            confirmations = data.get("confirmedWorkspaces")
            if (not isinstance(confirmations, list)
                    or any(not isinstance(item, str) for item in confirmations)
                    or [item.strip().casefold() for item in confirmations]
                    != [item.casefold() for item in workspaces]):
                raise ValueError("Confirm every selected workspace, in order, before deleting any items.")
    if len(workspaces) > 1 and action in {"/api/setup-foundry", "/api/full-workflow"}:
        if any(data.get(key) for key in (
            "foundryProjectName", "foundryProjectResourceId", "foundryEndpoint", "foundryNewProjectName",
        )):
            raise ValueError(
                "Leave the shared Foundry project field blank for multiple workspaces. "
                "Each target requires its own project; the account and model can be shared."
            )
    if len(workspaces) > 1 and data.get("ontologyGraphBinding"):
        raise ValueError("A native graph binding belongs to one workspace and cannot be shared across parallel targets.")
    return action, workspaces, dict(data)


def emit(workspace: str, name: str, **state) -> None:
    with OUTPUT_LOCK:
        print(EVENT_PREFIX + json.dumps({"workspace": workspace, "name": name, **state}), flush=True)


def load_server():
    spec = importlib.util.spec_from_file_location("parallel_launcher_server", SCRIPT_DIR / "webapp" / "server.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the existing launcher API.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checked(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def seed_rayfin(deploy, source: Path, destination: Path, tenant: str, workspace: str) -> bool:
    env_file = source / "HydroOperationsApp" / "rayfin" / ".env"
    if not env_file.is_file():
        return False
    values = deploy.read_rayfin_env(env_file)
    if (deploy._public_config_value(values, "RAYFIN_PUBLIC_TENANT_ID").casefold() != tenant.casefold()
            or deploy._public_config_value(values, "RAYFIN_PUBLIC_WORKSPACE_ID").casefold() != workspace.casefold()):
        return False
    target = destination / "HydroOperationsApp" / "rayfin"
    for name in (".env", ".env.local", ".deployments.json"):
        path = env_file.parent / name
        if path.is_file():
            shutil.copy2(path, target / name)
    return True


def prepare_checkout(deploy, directory: Path, tenant: str, workspace: str, commit: str) -> Path:
    checkout = directory / "checkouts" / uuid.uuid4().hex
    checkout.parent.mkdir(parents=True, exist_ok=True)
    checked(["git", "clone", "--config", "core.longpaths=true", "--quiet", "--shared",
             "--no-checkout", str(REPO_ROOT), str(checkout)])
    checked(["git", "-C", str(checkout), "checkout", "--quiet", "--detach", commit])
    state = directory / "latest.json"
    sources = []
    if state.exists():
        previous = json.loads(state.read_text(encoding="utf-8"))
        sources.append(Path(previous["checkout"]))
    sources.append(REPO_ROOT)
    for source in sources:
        if seed_rayfin(deploy, source, checkout, tenant, workspace):
            break
    return checkout


def worker_environment(deploy, directory: Path, checkout: Path, tenant: str, payload: dict) -> dict[str, str]:
    environment = dict(os.environ)
    # Never inherit another target's public artifact IDs or shared Foundry overrides.
    for key in list(environment):
        if (key.startswith(("RAYFIN_PUBLIC_", "HYDRO_FOUNDRY_"))
                or key in {"FABRIC_FEATURE_CONFIG", "HYDRO_ONTOLOGY_GRAPH_BINDING"}):
            environment.pop(key)
    azure = directory / "azure"
    if not azure.exists():
        source = deploy.isolated_azure_cli_config(tenant)
        if not (source / "azureProfile.json").is_file():
            source = Path(os.environ.get("AZURE_CONFIG_DIR") or Path.home() / ".azure")
        deploy.secure_private_directory(azure)
        if source.is_dir():
            shutil.copytree(source, azure, dirs_exist_ok=True)
        deploy.secure_private_directory(azure, recursive=True)
    environment.update(
        PYTHONUNBUFFERED="1",
        FABRIC_PARALLEL_CHECKOUT=str(checkout),
        FABRIC_UI_PIPELINE_DEFAULTS_FILE=str(directory / "checkpoints.json"),
        HYDRO_PARALLEL_DEPLOY="1",
        HYDRO_PARALLEL_AZURE_CONFIG_DIR=str(azure),
        AZURE_CONFIG_DIR=str(azure),
        HYDRO_STATE_DIR=str(directory / "hosted-state"),
    )
    environment[PAYLOAD_ENV] = json.dumps(payload)
    return environment


def worker() -> int:
    payload = json.loads(os.environ.pop(PAYLOAD_ENV))
    server = load_server()
    workspace, name = payload["workspace"], payload["name"]
    client = server.app.test_client()
    data = payload["data"]
    if (Path(payload["directory"]) / "cancel").exists():
        emit(workspace, name, status="cancelled", done=True, returncode=1,
             canResume=bool(payload.get("resumeJobId")), lines=["Cancelled before starting a deployment module."])
        return 1
    if payload["action"] == "/api/full-workflow" and payload.get("resumeJobId"):
        data["resumeJobId"] = payload["resumeJobId"]
        if payload.get("syncLatest"):
            data["syncLatest"] = True
    response = client.post(payload["action"], json=data)
    started = response.get_json()
    if response.status_code != 200 or not started.get("jobId"):
        emit(workspace, name, status="failed", done=True, returncode=1,
             requiresSync=bool(started.get("requiresSync")), canResume=bool(payload.get("resumeJobId")),
             lines=[started.get("error", f"Launcher returned HTTP {response.status_code}.")])
        return 1
    job_id = started["jobId"]
    since = 0
    while True:
        cancel = Path(payload["directory"]) / "cancel"
        if cancel.exists():
            client.post("/api/jobs/cancel-all")
        response = client.get(f"/api/jobs/{job_id}?since={since}")
        state = response.get_json()
        if response.status_code != 200:
            raise RuntimeError(state.get("error", "Could not read the workspace job."))
        emit(workspace, name, jobId=job_id, **state)
        since = state["nextSince"]
        if state["done"]:
            return 0 if state["status"] == "succeeded" else 1
        time.sleep(1)


def run_target(deploy, directory: Path, commit: str, tenant: str, workspace: str, name: str,
               action: str, data: dict, *, resume: bool = False, sync_latest: bool = False) -> int:
    from deployment_locks import resource_lock

    with resource_lock(tenant, "workspace:" + workspace, timeout=8 * 3600):
        return _run_target(
            deploy, directory, commit, tenant, workspace, name, action, data,
            resume=resume, sync_latest=sync_latest,
        )


def _run_target(deploy, directory: Path, commit: str, tenant: str, workspace: str, name: str,
                action: str, data: dict, *, resume: bool, sync_latest: bool) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    cancel = directory / "cancel"
    if cancel.exists():
        cancel.unlink()
    emit(workspace, name, tenant=tenant, status="running", done=False, phases=["Preparing isolated checkout", "Done"],
         phaseIndex=0, dryRun=data.get("dryRun"),
         lines=["Preparing workspace-specific deployment files and credentials."])
    try:
        checkout = prepare_checkout(deploy, directory, tenant, workspace, commit)
        latest = directory / "latest.json"
        previous = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {}
        payload = {
            "workspace": workspace, "name": name, "directory": str(directory), "action": action,
            "data": {**data, "tenant": tenant, "workspace": workspace},
        }
        if resume:
            if previous.get("action") != action or not previous.get("jobId"):
                raise ValueError("No matching Full Deploy checkpoint exists for this workspace.")
            payload.update(resumeJobId=previous["jobId"], syncLatest=sync_latest)
        record = {"checkout": str(checkout), "action": action, "tenant": tenant, "workspace": workspace}
        if resume:
            record["jobId"] = previous["jobId"]
        saved_job_id = None
        environment = worker_environment(deploy, directory, checkout, tenant, payload)
        terminal = False
        with subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "--worker"],
            cwd=SCRIPT_DIR, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip("\n")
                if line.startswith(EVENT_PREFIX):
                    state = json.loads(line[len(EVENT_PREFIX):])
                    terminal = terminal or bool(state.get("done"))
                    record.update({key: state[key] for key in ("jobId", "status") if key in state})
                    if "jobId" in state and state["jobId"] != saved_job_id:
                        save_state(latest, record)
                        saved_job_id = state["jobId"]
                    with OUTPUT_LOCK:
                        print(line, flush=True)
                else:
                    emit(workspace, name, lines=[line])
            result = process.wait()
        if not terminal:
            record["status"] = "interrupted"
            emit(workspace, name, status="interrupted", done=True, returncode=result or 1,
                 lines=["The worker stopped before reporting a final result. Verify remote Fabric jobs before retrying."])
            result = result or 1
        save_state(latest, record)
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        emit(workspace, name, status="failed", done=True, returncode=1, lines=[f"Deployment stopped: {exc}"])
        return 1


def run_batch(payload: dict) -> int:
    action, targets, data = validate_request(payload)
    import deploy_fabric_app as deploy
    from sync_workspace_from_git import Fabric

    tenant = deploy.resolve_tenant_id(data["tenant"].strip())
    deploy.prefer_tenant_scoped_azure_cli_config(tenant)
    fabric = Fabric(tenant)
    resolved = [(str(uuid.UUID(workspace)), name)
                for workspace, name in (fabric.resolve_workspace_id(target) for target in targets)]
    if len({workspace.casefold() for workspace, _ in resolved}) != len(resolved):
        raise ValueError("Two entries identify the same Fabric workspace. No deployment was started.")
    root = Path.home() / ".fabric-demo" / "parallel-deployments" / tenant
    deploy.secure_private_directory(root)
    dirty = checked(["git", "-C", str(REPO_ROOT), "diff", "--name-only", "HEAD"]).splitlines()
    dirty.extend(checked(["git", "-C", str(REPO_ROOT), "ls-files", "--others", "--exclude-standard"]).splitlines())
    if any(path != "HydroOperationsApp/rayfin/rayfin.yml" for path in dirty):
        raise ValueError("Commit source changes before parallel deployment; workers use one committed source snapshot.")
    commit = checked(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(
                run_target, deploy, root / workspace.lower(), commit, tenant, workspace, name, action, data,
                resume=bool(payload.get("resume")), sync_latest=bool(payload.get("syncLatest")),
            )
            for workspace, name in resolved
        ]
        results = [future.result() for future in futures]
    succeeded = sum(result == 0 for result in results)
    print(f"Parallel deployment completed: {succeeded}/{len(results)} workspaces succeeded.", flush=True)
    return 0 if succeeded == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    import deploy_fabric_app as deploy

    try:
        if args.worker:
            return worker()
        return run_batch(json.loads(os.environ.pop(PAYLOAD_ENV)))
    except (OSError, ValueError, RuntimeError, deploy.DeployError) as exc:
        print(f"Parallel deployment stopped: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
