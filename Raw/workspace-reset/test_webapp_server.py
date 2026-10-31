import importlib.util
import base64
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import run_pipeline


MODULE_PATH = Path(__file__).with_name("webapp") / "server.py"
SPEC = importlib.util.spec_from_file_location("fabric_demo_server", MODULE_PATH)
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)
PIPELINE_PARAMS = {
    "key_vault_uri": "https://vault.vault.azure.net/",
    "alert_email_to": "operations@example.test",
    "ops_agent_teams_team_id": "team",
    "ops_agent_teams_channel_id": "channel",
}


class FullWorkflowResumeTests(unittest.TestCase):
    def setUp(self):
        self.client = SERVER.app.test_client()
        directory = Path(__file__).parent / (".workflow-test-" + uuid.uuid4().hex)
        directory.mkdir()
        self.addCleanup(shutil.rmtree, directory)
        storage = patch.object(SERVER, "PIPELINE_DEFAULTS_FILE", directory / "settings.json")
        storage.start()
        self.addCleanup(storage.stop)
        self.jobs = patch.dict(SERVER.JOBS, {}, clear=True)
        self.jobs.start()
        self.addCleanup(self.jobs.stop)
        version = patch.object(SERVER, "app_version", return_value=Mock(get_json=Mock(
            return_value={"repository": "owner/repo", "branch": "main"})))
        version.start()
        self.addCleanup(version.stop)
        self.target = {
            "tenant": "tenant.example", "workspace": "Demo", "repository": "owner/repo",
            "parameters": PIPELINE_PARAMS, "pat": "private-test-pat", "energyMap": "enabled",
        }

    def start(self, payload=None):
        with patch.object(SERVER.threading, "Thread"):
            response = self.client.post("/api/full-workflow", json=payload or self.target)
        self.assertEqual(response.status_code, 200, response.get_json())
        return SERVER.JOBS[response.get_json()["jobId"]]

    def steps(self):
        with patch.object(SERVER, "_start_workflow", return_value="captured") as start:
            response = self.client.post("/api/full-workflow", json=self.target)
        self.assertEqual(response.status_code, 200)
        return start.call_args.args[0]

    def fail_energy(self):
        job = self.start()
        with patch.object(SERVER, "_run_workflow_step", side_effect=[0, 0, 23]):
            SERVER._workflow_worker(job, self.steps())
        return job

    def test_combined_retry_retains_sync_core_then_runs_combined_engine(self):
        failed = self.fail_energy()
        original = self.client.get(f"/api/jobs/{failed.id}").get_json()
        self.assertEqual(original["completedSteps"], ["GitHub sync", "Setup pipeline"])
        self.assertTrue(original["canResume"])
        self.assertEqual(original["resumeComponent"], SERVER.COMBINED_STEP)
        # A completed Git sync does not require the PAT again.
        resumed = self.start({**self.target, "pat": "", "resumeJobId": failed.id})
        self.assertEqual(resumed.completed_steps, failed.completed_steps)
        self.assertEqual(resumed.component_timings["Setup pipeline"], failed.component_timings["Setup pipeline"])
        self.assertEqual(resumed.phase_timings[1], failed.phase_timings[1])
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(resumed, self.steps())
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         [SERVER.COMBINED_STEP])
        self.assertEqual(resumed.status, "succeeded")
        self.assertEqual(resumed.completed_steps, resumed.workflow_steps)
        self.assertFalse(self.client.get(f"/api/jobs/{resumed.id}").get_json()["canResume"])
        self.assertFalse(self.client.get(f"/api/jobs/{failed.id}").get_json()["canResume"])
        self.assertNotIn("private-test-pat", json.dumps(original))

    def test_retry_rejects_changed_effective_inputs_without_starting_anything(self):
        failed = self.fail_energy()
        for change in (
            {"tenant": "other.example"}, {"workspace": "Other"},
            {"repository": "owner/other"}, {"branch": "other"}, {"directory": "/Other"},
            {"keepConnected": True}, {"connectionId": "different"},
            {"clientId": "11111111-1111-1111-1111-111111111111"},
            {"parameters": {**PIPELINE_PARAMS, "env_suffix": "Other"}},
            {"createFoundry": True}, {"foundryLocation": "swedencentral"},
        ):
            with self.subTest(change=change), patch.object(SERVER, "_start_workflow") as start:
                response = self.client.post("/api/full-workflow", json={
                    **self.target, **change, "resumeJobId": failed.id,
                })
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertIn("No steps were started", response.get_json()["error"])
                start.assert_not_called()

    def test_retry_chain_only_retains_verified_success_and_does_not_consume_when_busy(self):
        failed = self.fail_energy()
        busy = SERVER.Job(["Queued", "Done"])
        SERVER.JOBS[busy.id] = busy
        response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": failed.id})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(failed.resumed_by)
        busy.status = "succeeded"
        resumed = self.start({**self.target, "resumeJobId": failed.id})
        with patch.object(SERVER, "_run_workflow_step", side_effect=RuntimeError("monitor lost")):
            SERVER._workflow_worker(resumed, self.steps())
        self.assertEqual(resumed.completed_steps, ["GitHub sync", "Setup pipeline"])
        self.assertEqual(resumed.failed_component, SERVER.COMBINED_STEP)
        repeated = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": failed.id})
        self.assertEqual(repeated.status_code, 409)
        retry = self.start({**self.target, "resumeJobId": resumed.id})
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(retry, self.steps())
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         [SERVER.COMBINED_STEP])

    def test_cancellation_retries_current_step_even_if_child_returns_zero(self):
        job = self.start()
        def cancel(current, name, *args):
            current.cancel_requested = True
            return 0
        with patch.object(SERVER, "_run_workflow_step", side_effect=cancel):
            SERVER._workflow_worker(job, self.steps())
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.completed_steps, [])
        self.assertTrue(self.client.get(f"/api/jobs/{job.id}").get_json()["canResume"])
        retry = self.start({**self.target, "pat": "replacement-pat", "resumeJobId": job.id})
        self.assertEqual(retry.completed_steps, [])
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(retry, self.steps())
        self.assertEqual(run.call_count, 3)

    def test_combined_cancellation_keeps_only_verified_git_and_core(self):
        job = self.start()
        def cancel_combined(current, name, *args):
            if name == SERVER.COMBINED_STEP:
                current.cancel_requested = True
            return 0
        with patch.object(SERVER, "_run_workflow_step", side_effect=cancel_combined) as run:
            SERVER._workflow_worker(job, self.steps())
        self.assertEqual(run.call_count, 3)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.completed_steps, SERVER.WORKFLOW_STEPS[:2])
        retry = self.start({**self.target, "resumeJobId": job.id})
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(retry, self.steps())
        self.assertEqual([call.args[1] for call in run.call_args_list], [SERVER.COMBINED_STEP])

    def test_combined_failure_does_not_checkpoint_partial_branch_or_publication_success(self):
        failed = self.fail_energy()
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.returncode, 23)
        self.assertEqual(failed.failed_component, SERVER.COMBINED_STEP)
        self.assertEqual(failed.completed_steps, SERVER.WORKFLOW_STEPS[:2])
        self.assertEqual(failed.component_timings[SERVER.COMBINED_STEP]["status"], "failed")
        self.assertTrue(all(
            timing["status"] != "succeeded"
            for timing in failed.phase_timings
            if timing["name"].startswith("Combined · App publication")
        ))

    def test_legacy_checkpoint_is_rejected_without_overwriting_completed_stage_information(self):
        job = self.fail_energy()
        settings = json.loads(SERVER.PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))
        record = settings["workflowCheckpoints"][job.id]
        record.update(version=1, steps=[
            "GitHub sync", "Setup pipeline", "Energy setup", "Foundry multi-agent setup", "Fabric app deploy",
        ], completed=["GitHub sync", "Setup pipeline", "Energy setup"])
        SERVER.PIPELINE_DEFAULTS_FILE.write_text(json.dumps(settings), encoding="utf-8")
        before = SERVER.PIPELINE_DEFAULTS_FILE.read_bytes()
        SERVER.JOBS.clear()
        with patch.object(SERVER, "_start_workflow") as start:
            response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": job.id})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Legacy five-step", response.get_json()["error"])
        self.assertIn("Energy setup", response.get_json()["error"])
        self.assertIn("No steps were started", response.get_json()["error"])
        self.assertEqual(SERVER.PIPELINE_DEFAULTS_FILE.read_bytes(), before)
        start.assert_not_called()

    def test_in_memory_legacy_layout_is_rejected_before_credentials_or_execution(self):
        job = self.fail_energy()
        job.workflow_steps = ["GitHub sync", "Setup pipeline", "Energy setup",
                              "Foundry multi-agent setup", "Fabric app deploy"]
        with patch.object(SERVER, "_start_workflow") as start:
            response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": job.id})
        self.assertEqual(response.status_code, 409)
        self.assertIn("incompatible", response.get_json()["error"])
        self.assertIsNone(job.resumed_by)
        start.assert_not_called()

    def test_legacy_history_is_read_only_and_never_reported_as_a_new_failed_run(self):
        job = self.fail_energy()
        settings = json.loads(SERVER.PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))
        record = settings["workflowCheckpoints"][job.id]
        stages = ["GitHub sync", "Setup pipeline", "Energy setup",
                  "Foundry multi-agent setup", "Fabric app deploy"]
        for previous_status, completed in (
            ("succeeded", stages), ("failed", stages[:2]), ("running", stages[:1]),
        ):
            with self.subTest(previous_status=previous_status):
                record.update(version=1, steps=stages, completed=completed, status=previous_status)
                SERVER.PIPELINE_DEFAULTS_FILE.write_text(json.dumps(settings), encoding="utf-8")
                before = SERVER.PIPELINE_DEFAULTS_FILE.read_bytes()
                SERVER.JOBS.clear()
                with patch.object(SERVER, "_start_workflow") as start:
                    response = self.client.get(f"/api/jobs/{job.id}")
                    self.assertEqual(response.status_code, 200)
                    history = response.get_json()
                    self.assertEqual(history["status"], "historical")
                    self.assertEqual(history["previousStatus"], previous_status)
                    self.assertEqual(history["completedSteps"], completed)
                    self.assertTrue(history["done"])
                    self.assertFalse(history["canResume"])
                    self.assertIn("No new deployment has been started", history["resumeBlockedReason"])
                    self.assertNotIn("error", history)
                    self.assertNotIn(job.id, SERVER.JOBS)
                    retry = self.client.post("/api/full-workflow", json={
                        **self.target, "resumeJobId": job.id,
                    })
                    self.assertEqual(retry.status_code, 409)
                    start.assert_not_called()
                self.assertEqual(SERVER.PIPELINE_DEFAULTS_FILE.read_bytes(), before)

    def test_malformed_legacy_history_remains_an_explicit_error(self):
        job = self.fail_energy()
        settings = json.loads(SERVER.PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))
        settings["workflowCheckpoints"][job.id].update(version=1, completed="invalid")
        SERVER.PIPELINE_DEFAULTS_FILE.write_text(json.dumps(settings), encoding="utf-8")
        before = SERVER.PIPELINE_DEFAULTS_FILE.read_bytes()
        SERVER.JOBS.clear()
        response = self.client.get(f"/api/jobs/{job.id}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Invalid local legacy", response.get_json()["error"])
        self.assertEqual(SERVER.PIPELINE_DEFAULTS_FILE.read_bytes(), before)

    def test_combined_source_change_retains_core_and_pending_combined_step(self):
        failed = self.fail_energy()
        original = dict(failed.source_keys)
        self.assertIn(SERVER.COMBINED_STEP, original)
        self.assertIn("Energy setup", original)
        self.assertIn("Foundry multi-agent setup", original)
        with patch.object(SERVER, "_workflow_source_keys",
                          return_value={**original, SERVER.COMBINED_STEP: "changed-engine"}):
            retry = self.start({**self.target, "resumeJobId": failed.id})
        self.assertEqual(retry.completed_steps, SERVER.WORKFLOW_STEPS[:2])
        self.assertEqual(retry.source_keys[SERVER.COMBINED_STEP], "changed-engine")

    def test_combined_timeout_terminates_owned_process_and_fails_checkpoint(self):
        job = self.start()
        steps = self.steps()
        run_step = SERVER._run_workflow_step
        process = Mock(returncode=-9)
        process.poll.return_value = None
        process.stdout = io.StringIO("[3/8] concurrent preparation\n")
        timer = Mock()
        def timer_factory(timeout, callback):
            self.assertEqual(timeout, SERVER.COMBINED_TIMEOUT_S)
            timer.start.side_effect = callback
            return timer
        def run(current, name, *args):
            return run_step(current, name, *args) if name == SERVER.COMBINED_STEP else 0
        with patch.object(SERVER, "_run_workflow_step", side_effect=run), \
                patch.object(SERVER.subprocess, "Popen", return_value=process), \
                patch.object(SERVER.threading, "Timer", side_effect=timer_factory), \
                patch.object(SERVER, "_terminate_owned_process") as terminate:
            SERVER._workflow_worker(job, steps)
        terminate.assert_called_once_with(process)
        timer.cancel.assert_called_once()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.completed_steps, SERVER.WORKFLOW_STEPS[:2])
        self.assertEqual(job.failed_component, SERVER.COMBINED_STEP)
        self.assertIsNone(job.process)
        self.assertIn(f"timed out after {SERVER.COMBINED_TIMEOUT_S}", "\n".join(job.lines))

    def test_failed_checkpoint_survives_restart_without_persisting_credentials_or_output(self):
        failed = self.fail_energy()
        contents = SERVER.PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8")
        self.assertNotIn("private-test-pat", contents)
        self.assertNotIn("FABRIC_GIT_PAT", contents)
        self.assertNotIn("lines", json.loads(contents)["workflowCheckpoints"][failed.id])
        SERVER.JOBS.clear()
        restored = self.client.get(f"/api/jobs/{failed.id}").get_json()
        self.assertTrue(restored["canResume"])
        self.assertEqual(restored["completedSteps"], ["GitHub sync", "Setup pipeline"])
        resumed = self.start({**self.target, "pat": "", "resumeJobId": failed.id})
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(resumed, self.steps())
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         [SERVER.COMBINED_STEP])
        SERVER.JOBS.clear()
        old = self.client.get(f"/api/jobs/{failed.id}").get_json()
        self.assertFalse(old["canResume"])

    def test_changed_geo_notebook_syncs_before_energy_without_repeating_core(self):
        failed = self.fail_energy()
        keys = SERVER._workflow_source_keys()
        changed = {**keys, "Energy setup": "new-geo",
                   "file:Notebooks/Geo_002_publish_map_agent.Notebook/notebook-content.py": "new-geo"}
        with patch.object(SERVER, "_workflow_source_keys", return_value=changed):
            response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": failed.id})
            self.assertEqual(response.status_code, 409)
            self.assertTrue(response.get_json()["requiresSync"])
            self.assertFalse(response.get_json()["fullSyncRequired"])
            resumed = self.start({**self.target, "pat": "", "resumeJobId": failed.id, "syncLatest": True})
        self.assertEqual(resumed.completed_steps, ["GitHub sync", "Setup pipeline"])
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(resumed, self.steps())
        self.assertEqual([call.args[1] for call in run.call_args_list], [
            "Changed notebook sync", SERVER.COMBINED_STEP,
        ])
        self.assertIn("--update-notebook", run.call_args_list[0].args[2])
        self.assertEqual(resumed.pending_notebook_sync, [])

    def test_changed_core_notebook_reruns_core_but_does_not_repeat_git_import(self):
        failed = self.fail_energy()
        keys = SERVER._workflow_source_keys()
        changed = {**keys, "Setup pipeline": "new-core",
                   "file:Notebooks/RTI_009_build_data_agent.Notebook/notebook-content.py": "new-core"}
        with patch.object(SERVER, "_workflow_source_keys", return_value=changed):
            response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": failed.id})
            self.assertEqual(response.status_code, 409)
            self.assertTrue(response.get_json()["requiresSync"])
            retry = self.start({**self.target, "resumeJobId": failed.id, "syncLatest": True})
        self.assertEqual(retry.completed_steps, ["GitHub sync"])
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(retry, self.steps())
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[0].args[1], "Changed notebook sync")
        self.assertEqual(run.call_args_list[1].args[1], "Setup pipeline")

    def test_failed_targeted_sync_remains_pending_across_restart_and_runs_no_setup(self):
        failed = self.fail_energy()
        failed.pending_notebook_sync = ["Geo_002_publish_map_agent"]
        retry = self.start({**self.target, "resumeJobId": failed.id})
        with patch.object(SERVER, "_run_workflow_step", return_value=8) as run:
            SERVER._workflow_worker(retry, self.steps())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(retry.completed_steps, ["GitHub sync", "Setup pipeline"])
        SERVER.JOBS.clear()
        restored = SERVER._restore_workflow(retry.id)
        self.assertEqual(restored.pending_notebook_sync, ["Geo_002_publish_map_agent"])

    def test_pipeline_changes_require_explicit_full_git_sync(self):
        failed = self.fail_energy()
        changed = {**SERVER._workflow_source_keys(), "Setup pipeline": "new-pipeline",
                   "file:Orchestrator_Pipelines/01_Pipe_Setup.DataPipeline/pipeline-content.json": "new"}
        with patch.object(SERVER, "_workflow_source_keys", return_value=changed):
            response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": failed.id})
            self.assertTrue(response.get_json()["fullSyncRequired"])
            retry = self.start({**self.target, "resumeJobId": failed.id, "syncLatest": True})
        self.assertEqual(retry.completed_steps, [])
        self.assertEqual(retry.pending_notebook_sync, [])

    def test_active_checkpoint_after_restart_refuses_potential_duplicate_remote_run(self):
        job = self.start()
        SERVER.JOBS.clear()
        restored = self.client.get(f"/api/jobs/{job.id}").get_json()
        self.assertEqual(restored["status"], "interrupted")
        self.assertFalse(restored["canResume"])
        response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": job.id})
        self.assertEqual(response.status_code, 409)

    def test_checkpoint_write_failure_starts_no_child(self):
        with patch.object(SERVER, "_save_workflow_checkpoints", side_effect=OSError("disk unavailable")), \
                patch.object(SERVER.threading, "Thread") as thread:
            response = self.client.post("/api/full-workflow", json=self.target)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SERVER.JOBS, {})
        thread.assert_not_called()

    def test_source_digests_track_component_files_not_git_head_or_unrelated_docs(self):
        root = SERVER.PIPELINE_DEFAULTS_FILE.parent
        notebook = root / "Notebooks" / "RTI_test.Notebook" / "notebook-content.py"
        geo = root / "Notebooks" / "Geo_002_publish_map_agent.Notebook" / "notebook-content.py"
        notebook.parent.mkdir(parents=True)
        geo.parent.mkdir(parents=True)
        notebook.write_text("core", encoding="utf-8")
        geo.write_text("energy", encoding="utf-8")
        app_source = root / "HydroOperationsApp" / "src" / "app.ts"
        app_source.parent.mkdir(parents=True)
        app_source.write_text("original app", encoding="utf-8")
        foundry = root / "provision_foundry_agents.py"
        foundry.write_text("original foundry", encoding="utf-8")
        with patch.object(SERVER, "REPO_ROOT", root), \
                patch.object(SERVER, "SCRIPT_DIR", root), \
                patch.object(SERVER, "PIPELINE_SCRIPT", root / "run_pipeline.py"), \
                patch.object(SERVER, "DEPLOY_SCRIPT", root / "deploy_fabric_app.py"):
            initial = SERVER._workflow_source_keys()
            (root / "README.md").write_text("unrelated docs", encoding="utf-8")
            self.assertEqual(SERVER._workflow_source_keys(), initial)
            geo.write_text("updated energy", encoding="utf-8")
            energy_change = SERVER._workflow_source_keys()
            self.assertEqual(energy_change["Setup pipeline"], initial["Setup pipeline"])
            self.assertNotEqual(energy_change["Energy setup"], initial["Energy setup"])
            self.assertNotEqual(energy_change[SERVER.COMBINED_STEP], initial[SERVER.COMBINED_STEP])
            app_source.write_text("updated app", encoding="utf-8")
            app_change = SERVER._workflow_source_keys()
            self.assertNotEqual(app_change[SERVER.COMBINED_STEP], energy_change[SERVER.COMBINED_STEP])
            self.assertEqual(app_change["Setup pipeline"], initial["Setup pipeline"])
            foundry.write_text("updated foundry", encoding="utf-8")
            foundry_change = SERVER._workflow_source_keys()
            self.assertNotEqual(foundry_change[SERVER.COMBINED_STEP], app_change[SERVER.COMBINED_STEP])
            self.assertEqual(foundry_change["Setup pipeline"], initial["Setup pipeline"])
            notebook.write_text("updated core", encoding="utf-8")
            self.assertNotEqual(SERVER._workflow_source_keys()["Setup pipeline"], initial["Setup pipeline"])

    def test_saving_pipeline_defaults_preserves_durable_workflow_checkpoint(self):
        failed = self.fail_energy()
        response = self.client.post("/api/pipeline-params", json={
            "tenant": "tenant.example", "workspace": "Demo", "parameters": PIPELINE_PARAMS,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        SERVER.JOBS.clear()
        restored = self.client.get(f"/api/jobs/{failed.id}").get_json()
        self.assertTrue(restored["canResume"])

    def test_unavailable_or_non_retryable_checkpoint_never_falls_back_to_full_run(self):
        for resume_id in ("unknown", "", 123):
            with self.subTest(resume_id=resume_id), patch.object(SERVER, "_start_workflow") as start:
                response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": resume_id})
                self.assertIn(response.status_code, (400, 409))
                start.assert_not_called()
        for status in ("running", "succeeded", "failed"):
            job = SERVER.Job(SERVER.WORKFLOW_PHASES)
            job.status = status
            SERVER.JOBS[job.id] = job
            with self.subTest(status=status), patch.object(SERVER, "_start_workflow") as start:
                response = self.client.post("/api/full-workflow", json={**self.target, "resumeJobId": job.id})
                self.assertEqual(response.status_code, 409)
                start.assert_not_called()

    def test_frontend_retries_and_reconnects_without_automatically_restarting(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {
    id, value:id === "workflowPat" ? "secret-pat" : id, checked:false,
    addEventListener(event, handler) { this[event] = handler; },
  });
  return elements.get(id);
};
const posts = [], fetches = [], stored = new Map(), renders = [];
let nextStart = {ok:true, data:{jobId:"first"}};
let nextPoll = {status:"failed", done:true, canResume:true, resumeComponent:"Energy setup", lines:[]};
let missing = false;
const context = vm.createContext({
  $, console, confirm:() => true, refresh:() => {},
  renderPanel:(panel, job) => renders.push({...job}), setTimeout:() => {},
  collectPipelineParams:() => ({}), spaClientValue:() => "", foundryPayload:() => ({}),
  workflowConnValue:() => "",
  localStorage:{setItem:(k,v) => stored.set(k,v), getItem:k => stored.get(k)},
  postJson:async (url, body) => {posts.push({url, body}); return nextStart;},
  fetch:async url => {fetches.push(url); return {status:missing ? 404 : 200, ok:!missing, json:async () => nextPoll};},
});
vm.runInContext(
  html.slice(html.indexOf("let workflowCheckpoint"), html.indexOf("// ---- validation + wiring ----")) +
  html.slice(html.indexOf("function workflowPayload("), html.indexOf('$("restartBtn").addEventListener')),
  context,
);
const execute = source => vm.runInContext(source, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await execute("startFullWorkflow()");
  await flush();
  assert.equal(posts.length, 1);
  assert.equal(execute("workflowCheckpoint.resumeComponent"), "Energy setup");
  assert.equal($("workflowPat").value, "");
  assert.equal(execute("workflowBusy"), false);
  assert.equal(stored.get("fabric-demo.full-workflow-job.v1"), "first");
  // Reload reconnects to the saved job; no POST or implicit deployment occurs.
  execute("workflowCheckpoint=null; restoreWorkflowJob()");
  await flush();
  assert.equal(posts.length, 1);
  assert.ok(fetches.at(-1).startsWith("/api/jobs/first?since=0"));
  nextStart = {ok:false, status:409, data:{error:"Full Deploy inputs changed. No steps were started."}};
  $("workspace").value = "changed";
  await $("workflowResumeBtn").click();
  await flush();
  assert.equal(posts.length, 2);
  assert.equal(posts.at(-1).body.resumeJobId, "first");
  assert.equal(execute("workflowCheckpoint.jobId"), "first");
  assert.match(renders.at(-1).error, /inputs changed/);
  // A rejected retry never falls back to a fresh Full Deploy.
  await flush();
  assert.equal(posts.length, 2);
  nextStart = {ok:false, status:409, data:{error:"Core sources changed", requiresSync:true}};
  await $("workflowResumeBtn").click();
  await flush();
  assert.equal(execute("workflowCheckpoint.requiresSync"), true);
  $("workspace").value = "workspace";
  nextStart = {ok:true, data:{jobId:"retry"}};
  nextPoll = {status:"succeeded", done:true, canResume:false, lines:[]};
  await $("workflowSyncResumeBtn").click();
  await flush();
  assert.equal(posts.at(-1).body.resumeJobId, "first");
  assert.equal(posts.at(-1).body.syncLatest, true);
  assert.equal(posts.at(-1).body.pat, "");
  assert.equal(stored.get("fabric-demo.full-workflow-job.v1"), "retry");
  assert.equal(execute("workflowCheckpoint.canResume"), false);
  nextPoll = {
    status:"historical", done:true, canResume:false,
    resumeBlockedReason:"Previous deployment uses an older workflow. No new deployment has been started.",
    lines:["Recorded previous outcome: succeeded."],
  };
  execute("restoreWorkflowJob()");
  await flush();
  assert.equal(execute("workflowCheckpoint.status"), "historical");
  assert.equal(execute("workflowCheckpoint.canResume"), false);
  assert.equal(execute("workflowBusy"), false);
  assert.equal(renders.at(-1).status, "historical");
  assert.equal(renders.at(-1).error, undefined);
  assert.match($("workflowResumeStatus").textContent, /No new deployment has been started/);
  assert.equal(posts.length, 4);
  missing = true;
  execute("restoreWorkflowJob()");
  await flush();
  assert.equal(execute("workflowCheckpoint.status"), "interrupted");
  assert.match($("workflowResumeStatus").textContent, /Resume is unavailable/);
  assert.equal(posts.length, 4);
})().catch(error => {console.error(error); process.exitCode=1;});
"""
        result = subprocess.run(
            ["node", "-e", script, str(SERVER.STATIC_DIR / "index.html")],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class WorkspaceActionTests(unittest.TestCase):
    def setUp(self):
        self.client = SERVER.app.test_client()

    def test_only_single_workspace_actions_are_exposed(self):
        response = self.client.get("/")
        try:
            html = response.get_data(as_text=True)
        finally:
            response.close()
        for obsolete in ("parallelDeploy", "workspace2", "workspace3", "FoundryProjectMode",
                         "renderParallelRuns", "/api/parallel"):
            self.assertNotIn(obsolete, html)
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/parallel", json={
                "action": "/api/full-workflow", "workspaces": ["One", "Two"],
                "data": {"tenant": "tenant.example"},
            })
        self.assertEqual(response.status_code, 405)
        self.assertNotIn("/api/parallel", {rule.rule for rule in SERVER.app.url_map.iter_rules()})
        start.assert_not_called()

    def test_setup_modules_are_separate_from_app_publication(self):
        target = {"tenant": "tenant.example", "workspace": "Demo"}
        for endpoint, stage in (
            ("/api/setup-energy", "energy"), ("/api/setup-foundry", "foundry"), ("/api/deploy-app", "app"),
        ):
            with self.subTest(stage=stage), patch.object(SERVER, "_start", return_value="job") as start:
                response = self.client.post(endpoint, json=target)
                self.assertEqual(response.status_code, 200, response.get_json())
                argv = start.call_args.args[0]
                self.assertEqual(argv[argv.index("--stage") + 1], stage)
                self.assertNotIn("--push-config", argv)
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/deploy-app", json={**target, "createFoundry": True})
            self.assertEqual(response.status_code, 400)
            self.assertIn("Foundry multi-agent setup module", response.get_json()["error"])
            start.assert_not_called()

    def test_foundry_page_discloses_backend_creation_before_website_publication(self):
        response = self.client.get("/")
        try:
            html = response.get_data(as_text=True)
        finally:
            response.close()
        foundry_pane = html.split('<section id="paneFoundry"', 1)[1].split("</section>", 1)[0]
        self.assertIn("AppBackend with SQL/GraphQL dependencies", foundry_pane)
        self.assertIn("Does not publish the website.", foundry_pane)
        self.assertIn("A Fabric App item can already appear during backend setup.", foundry_pane)
        self.assertIn("After Foundry setup succeeds", foundry_pane)
        self.assertNotIn("Updates agents only", html)
        self.assertIn("five Foundry specialist agents", html)
        self.assertIn("Foundry account is the parent Azure resource", html)
        self.assertIn("renderSectionDuration", html)
        self.assertIn("Combined · Energy + Foundry concurrently", SERVER.WORKFLOW_PHASES)

    def test_full_workflow_prepares_energy_and_foundry_before_final_app(self):
        target = {
            "tenant": "tenant.example", "workspace": "Demo", "repository": "owner/repo", "connectionId": "yes",
            "parameters": PIPELINE_PARAMS,
            "energyMap": "enabled", "createFoundry": False,
        }
        with patch.object(SERVER, "_start_workflow", return_value="job") as start:
            response = self.client.post("/api/full-workflow", json=target)
        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        self.assertEqual([step["name"] for step in steps], [
            "GitHub sync", "Setup pipeline", SERVER.COMBINED_STEP,
        ])
        self.assertEqual(steps[2]["env"], {"HYDRO_FOUNDRY_CREATE": "false"})
        self.assertEqual(steps[2]["argv"][steps[2]["argv"].index("--stage") + 1], "all")
        self.assertEqual([step["phase_offset"] for step in steps],
                         sorted({step["phase_offset"] for step in steps}))

    def test_subscription_discovery_returns_only_enabled_metadata_without_mutating_azure(self):
        accounts = [
            {"id": "sub-a", "name": "Demo", "tenantId": "tenant-a", "state": "Enabled", "user": {"name": "private"}},
            {"id": "sub-b", "tenantId": "tenant-b", "state": "Disabled"},
        ]
        with patch.object(SERVER.subprocess, "run", return_value=Mock(
            returncode=0, stdout=json.dumps(accounts),
        )) as run:
            response = self.client.get("/api/subscriptions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"subscriptions": [
            {"id": "sub-a", "name": "Demo", "tenantId": "tenant-a"},
        ]})
        command = run.call_args.args[0]
        self.assertIn("account list", command if isinstance(command, str) else " ".join(command))
        self.assertEqual(run.call_args.kwargs["timeout"], 40)

    def test_subscription_discovery_reports_cli_and_shape_errors(self):
        for result, expected in (
            (Mock(returncode=1, stderr="Please sign in", stdout=""), 503),
            (Mock(returncode=0, stdout="not-json"), 502),
            (Mock(returncode=0, stdout="{}"), 502),
            (Mock(returncode=0, stdout="[1]"), 502),
        ):
            with self.subTest(expected=expected), patch.object(SERVER.subprocess, "run", return_value=result):
                response = self.client.get("/api/subscriptions")
                self.assertEqual(response.status_code, expected)
                self.assertTrue(response.get_json()["error"])
        with patch.object(SERVER.subprocess, "run", side_effect=subprocess.TimeoutExpired("az", 40)):
            self.assertEqual(self.client.get("/api/subscriptions").status_code, 503)

    def test_progress_shows_overall_section_and_substep_durations(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("renderDeploymentTimings", page)
        self.assertNotIn("Module timings", page)
        self.assertNotIn('aria-label="Deployment timings"', page)
        self.assertIn('class="elapsed" title="Elapsed time"', page)
        self.assertIn("renderSectionDuration", page)
        self.assertIn("job.phaseTimings?.[item.index]", page)
        self.assertIn("job.componentTimings", page)
        self.assertIn("Not reported", page)
        self.assertIn("Last observed", page)

    def test_server_timing_handles_skipped_markers_and_freezes_at_failure(self):
        clock = [100.0]
        with patch.object(SERVER.time, "monotonic", side_effect=lambda: clock[0]):
            job = SERVER.Job(["Queued", "Connecting", "Optional marker", "Importing", "Later", "Done"])
            clock[0] = 103
            job.advance_phase(1)
            clock[0] = 108
            job.advance_phase(3)
            clock[0] = 110
            snapshot = job.timing_snapshot()
            self.assertEqual(snapshot["elapsedSeconds"], 10)
            self.assertEqual(snapshot["phaseTimings"][1]["elapsedSeconds"], 5)
            self.assertEqual(snapshot["phaseTimings"][2]["status"], "unreported")
            self.assertIsNone(snapshot["phaseTimings"][2]["elapsedSeconds"])
            self.assertEqual(snapshot["phaseTimings"][3]["elapsedSeconds"], 2)
            job.finish_timing("failed")
            clock[0] = 1000
            self.assertEqual(job.timing_snapshot()["elapsedSeconds"], 10)
            self.assertEqual(job.timing_snapshot()["phaseTimings"][3]["elapsedSeconds"], 2)
            self.assertEqual(job.timing_snapshot()["phaseTimings"][3]["status"], "failed")
            self.assertEqual(job.timing_snapshot()["phaseTimings"][4]["status"], "pending")

    def test_workflow_api_reports_exact_component_and_phase_durations(self):
        clock = [100.0]
        phases = ["Queued", "Sync · Start", "Sync · Import", "Pipeline · Run", "Pipeline · Finish", "Foundry · Start", "Done"]
        steps = [{"name": name, "phase_offset": offset, "argv": [], "markers": [], "timeout": 1}
                 for name, offset in (("GitHub sync", 1), ("Setup pipeline", 3), ("Foundry multi-agent setup", 5))]
        for outcome in ("succeeded", "failed", "cancelled", "exception"):
            clock[0] = 100
            with self.subTest(outcome=outcome), patch.object(SERVER.time, "monotonic", side_effect=lambda: clock[0]):
                job = SERVER.Job(phases)

                def step(current, name, *_args):
                    if name == "GitHub sync":
                        clock[0] = 101
                        current.advance_phase(1)
                        clock[0] = 111
                        current.advance_phase(2)
                        clock[0] = 118
                        return 0
                    if name == "Setup pipeline":
                        clock[0] = 120
                        current.advance_phase(3)
                        clock[0] = 130
                        if outcome == "exception":
                            raise OSError("monitor failed")
                        if outcome == "cancelled":
                            current.cancel_requested = True
                            return -15
                        return 23 if outcome == "failed" else 0
                    current.advance_phase(5)
                    clock[0] = 140
                    return 0

                with patch.object(SERVER, "_run_workflow_step", side_effect=step):
                    SERVER._workflow_worker(job, steps)
                self.assertEqual(job.status, "failed" if outcome == "exception" else outcome)
                clock[0] = 1000
                with patch.dict(SERVER.JOBS, {job.id: job}):
                    response = self.client.get(f"/api/jobs/{job.id}?since=1").get_json()
                self.assertTrue(response["done"])
                self.assertEqual(response["elapsedSeconds"], 40 if outcome == "succeeded" else 30)
                self.assertEqual(response["componentTimings"][0]["elapsedSeconds"], 18)
                self.assertEqual(response["componentTimings"][1]["elapsedSeconds"], 12)
                self.assertEqual(response["phaseTimings"][1]["elapsedSeconds"], 10)
                self.assertEqual(response["phaseTimings"][2]["elapsedSeconds"], 7)
                self.assertEqual(response["phaseTimings"][3]["elapsedSeconds"], 10)
                self.assertEqual(response["phaseTimings"][4]["status"],
                                 "unreported" if outcome == "succeeded" else "pending")
                self.assertEqual(len(response["componentTimings"]), 3 if outcome == "succeeded" else 2)

    def test_live_output_scroll_selection_and_keyboard_during_streaming(self):
        """Exercise the real renderer and layout in an isolated headless browser, never the app/server."""
        candidates = [
            os.environ.get("FABRIC_UI_TEST_BROWSER"),
            shutil.which("chrome"), shutil.which("chromium"), shutil.which("msedge"),
            str(Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ]
        browser = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
        if not browser:
            self.skipTest("Chromium browser missing; set FABRIC_UI_TEST_BROWSER to run real scrolling tests")
        profile = Path(__file__).parent / f".ui-scroll-regression-{uuid.uuid4().hex}"
        profile.mkdir()
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {spawn} = require("node:child_process");
const [browserPath, profile, htmlPath] = process.argv.slice(1);
const browser = spawn(browserPath, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--remote-debugging-port=0", "--remote-allow-origins=*", "--user-data-dir="+profile, "about:blank",
], {stdio:["ignore", "ignore", "pipe"]});
const watchdog = setTimeout(() => browser.kill(), 30000);
let socket;
let nextId = 0;
const pending = new Map();
async function call(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {expression, returnByValue:true, awaitPromise:true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
(async () => {
  try {
    const endpoint = await new Promise((resolve, reject) => {
      let stderr = "";
      browser.stderr.on("data", data => {
        stderr = (stderr + data).slice(-16000);
        const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) resolve(match[1]);
      });
      browser.on("error", reject);
      browser.on("exit", code => reject(new Error("Browser exited: "+code+" "+stderr)));
    });
    const targets = await (await fetch("http://"+new URL(endpoint).host+"/json/list")).json();
    socket = new WebSocket(targets.find(target => target.type === "page").webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {socket.onopen=resolve; socket.onerror=reject;});
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      if (message.error) request.reject(new Error(JSON.stringify(message.error)));
      else request.resolve(message.result);
    };
    await call("Page.enable");
    await call("Page.bringToFront");
    await call("Emulation.setFocusEmulationEnabled", {enabled:true});
    await call("Emulation.setDeviceMetricsOverride", {width:1000, height:900, deviceScaleFactor:1, mobile:false});
    const html = fs.readFileSync(htmlPath, "utf8");
    const style = html.match(/<style>([\s\S]*?)<\/style>/)[1];
    const renderer = html.slice(html.indexOf("function renderPanel("), html.indexOf("let subscriptionLoad"));
    const helpers = html.slice(html.indexOf("const WORKFLOW_PHASE_GROUPS"), html.indexOf("// Starts a job"));
    await evaluate(`document.head.innerHTML = ${JSON.stringify("<style>"+style+"</style>")};
      document.body.innerHTML = '<div id="deployPanel"></div><div id="energyPanel"></div><div id="workflowPanel"></div>';
      ${helpers}\n${renderer}
      var panel = document.getElementById("deployPanel");
      var job = {status:"running", phases:["Start","Deploy"], phaseIndex:0,
        lines:Array.from({length:160}, (_,i) => "Output line "+i+" <safe & literal>")};
      renderPanel(panel, job);
      var out = panel.querySelector(".output");
      var original = out;
      var originalText = out.firstChild;
      var bottom = () => out.scrollHeight-out.clientHeight-out.scrollTop < 2;
      var update = () => {job.lines.push("Streamed "+job.lines.length); renderPanel(panel,job);};
      var check = (condition, message) => {if (!condition) throw new Error(message);};
      check(bottom(), "Initial output should follow tail");
      check(out.clientHeight <= 340 && out.scrollHeight > out.clientHeight, "Log must be bounded");
      for (var i=0;i<20;i++) {update(); check(bottom(), "Streaming should follow tail");}
      out.scrollTop=400;
      for (var i=0;i<20;i++) {
        var scrollBefore=out.scrollTop;
        update();
        check(out===panel.querySelector(".output"), "Scroll surface replaced");
        check(Math.abs(out.scrollTop-scrollBefore)<1, "Manual scroll reset by streaming: "+scrollBefore+" -> "+out.scrollTop);
        out.scrollTop+=30;
        check(out.scrollTop>scrollBefore, "Cannot scroll downward while streaming");
      }
      var range=document.createRange();
      range.setStart(originalText,20); range.setEnd(originalText,35);
      getSelection().addRange(range);
      var selected=getSelection().toString();
      for (var i=0;i<10;i++) {
        update();
        check(getSelection().toString()===selected, "Selection lost during append");
        check(getSelection().anchorNode===originalText, "Selected text node replaced");
      }
      out.scrollTop=out.scrollHeight;
      var selectedTop=out.scrollTop;
      update();
      check(out.scrollTop===selectedTop && !bottom(), "Selection at tail should pause following");
      getSelection().removeAllRanges();
      out.scrollTop=out.scrollHeight-out.clientHeight-20;
      update();
      check(bottom(), "Near-bottom should resume following");
      out.scrollTop=100;
      panel.querySelector("button").click();
      check(bottom(), "Jump to latest failed");
      for (var i=0;i<10;i++) {update();check(bottom(), "Jump must resume following");}
      check(out.textContent.includes("<safe & literal>") && !out.querySelector("safe"), "Log must remain literal text");
      renderPanel(panel,job);
      check(out.firstChild===originalText, "Clock-only refresh replaced text");
      var other=document.getElementById("energyPanel");
      renderPanel(other,{status:"running",lines:["Other module"]});
      check(other.querySelector(".output")!==out, "Module output must remain independent");
      var workflow=document.getElementById("workflowPanel");
      renderPanel(workflow,{status:"running",phases:["Sync · Start","Pipeline · Setup"],phaseIndex:0,lines:["Workflow"]});
      check(workflow.querySelector(".workflow-phase-group"), "Workflow module groups lost");
      var timed = {
        status:"running", phaseIndex:2, elapsedSeconds:100, component:"Setup pipeline", lines:["timed fixture"],
        phases:["Queued","Sync · Connecting","Pipeline · Running","Energy · Checking prerequisites","Foundry · Hosted Agent","Deploy · Provisioning app","Done"],
        phaseTimings:[
          {status:"succeeded",elapsedSeconds:1},{status:"succeeded",elapsedSeconds:20},
          {status:"running",elapsedSeconds:60},{status:"unreported",elapsedSeconds:null},
          {status:"pending",elapsedSeconds:null},{status:"pending",elapsedSeconds:null},{status:"pending",elapsedSeconds:null}],
        componentTimings:[
          {name:"GitHub sync",status:"succeeded",elapsedSeconds:23},
          {name:"Setup pipeline",status:"running",elapsedSeconds:65}]
      };
      renderPanel(workflow,timed);
      check(workflow.querySelectorAll(".workflow-phase-summary .section-duration").length===4, "Every section needs a timer");
      check(workflow.querySelectorAll(".phases .section-duration").length===5, "Every substep needs a timer");
      check(workflow.querySelector('[data-group="sync"] summary').textContent.includes("Completed · 00:23"), "Completed timer lost");
      check(workflow.querySelector('[data-group="pipeline"] summary').textContent.includes("Running · 01:05"), "Running section timer lost");
      check(workflow.querySelector(".component-durations").textContent.includes("Setup pipeline"), "Core submodule timing lost");
      check(workflow.textContent.includes("Not reported") && workflow.textContent.includes("Not started"), "Unmeasured steps must be explicit");
      timed.elapsedSeconds=106; timed.phaseTimings[2].elapsedSeconds=66; timed.componentTimings[1].elapsedSeconds=71;
      renderPanel(workflow,timed);
      check(workflow.querySelector('[data-group="pipeline"] summary').textContent.includes("Running · 01:11"), "Running timer did not advance");
      check(workflow.querySelector('[data-group="sync"] summary').textContent.includes("Completed · 00:23"), "Completed timer changed");
      timed.status="interrupted"; renderPanel(workflow,timed);
      check(workflow.textContent.includes("Last observed · 01:11"), "Interrupted monitor must not claim live timing");
      timed.status="failed"; timed.failedComponent="Setup pipeline";
      timed.componentTimings[1].status="failed"; timed.phaseTimings[2].status="failed";
      renderPanel(workflow,timed);
      check(workflow.querySelector('[data-group="pipeline"] summary').textContent.includes("Failed · 01:11"), "Failure duration lost");
      renderPanel(workflow,{status:"historical",lines:["Recorded previous outcome: succeeded."]});
      check(workflow.querySelector(".status").textContent==="Previous deployment history", "Legacy history label lost");
      check(!workflow.querySelector(".error"), "Legacy history must not be a new failure");
      check(!workflow.querySelector(".bar") && !workflow.querySelector(".elapsed"), "Legacy history must not invent live progress");
      check(workflow.querySelector(".output").textContent.includes("previous outcome: succeeded"), "Previous outcome lost");
      out.scrollTop=200;
      out.focus();
      check(document.activeElement===out, "Output must accept keyboard focus");
    `);
    await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    const before = await evaluate("out.scrollTop");
    await evaluate("var streamedUpdates=0; var stream=setInterval(() => {update();streamedUpdates++;},40)");
    await call("Input.dispatchKeyEvent", {type:"rawKeyDown", key:"PageDown", code:"PageDown", windowsVirtualKeyCode:34});
    await call("Input.dispatchKeyEvent", {type:"keyUp", key:"PageDown", code:"PageDown", windowsVirtualKeyCode:34});
    await new Promise(resolve => setTimeout(resolve, 500));
    assert.ok(await evaluate("out.scrollTop") > before, "PageDown should scroll the focused log: "+JSON.stringify(
      await evaluate("({top:out.scrollTop,focused:document.activeElement===out,documentFocus:document.hasFocus()})")));
    const keyboardTop = await evaluate("out.scrollTop");
    await evaluate("for(var i=0;i<10;i++) update()");
    assert.equal(await evaluate("out.scrollTop"), keyboardTop, "Streaming resets keyboard scroll");
    const position = await evaluate("(() => {const r=out.getBoundingClientRect();return {x:r.x+30,y:r.y+30};})()");
    await call("Input.dispatchMouseEvent", {type:"mouseWheel", ...position, deltaX:0, deltaY:200});
    await new Promise(resolve => setTimeout(resolve, 500));
    assert.ok(await evaluate("out.scrollTop") > keyboardTop, "Wheel should scroll down while streaming");
    assert.ok(await evaluate("streamedUpdates") >= 5, "Output must update during real keyboard/wheel interaction");
    await evaluate("clearInterval(stream)");
    await evaluate(`panel.querySelector("button").focus()`);
    await call("Input.dispatchKeyEvent", {type:"keyDown", key:"Enter", code:"Enter", windowsVirtualKeyCode:13, text:"\r"});
    await call("Input.dispatchKeyEvent", {type:"keyUp", key:"Enter", code:"Enter", windowsVirtualKeyCode:13});
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(await evaluate("bottom()"), true, "Keyboard jump should reach latest output");
    await evaluate(`job.status="failed";job.error="Final failure";job.returncode=1;renderPanel(panel,job);
      check(out===original && bottom(), "Completion should keep the scroll surface and follow tail");
      check(out.classList.contains("error") && out.textContent.endsWith("exit code: 1"), "Completion output lost");
      check(out.textContent.includes("[error] Final failure"), "Error output lost");`);
    console.log("PASS: streaming, manual/near-tail scroll, selection, wheel, PageDown, keyboard jump, module groups and completion");
  } finally {
    if (socket && socket.readyState === WebSocket.OPEN) {
      try {await call("Browser.close");} catch {}
      socket.close();
    }
    browser.kill();
    clearTimeout(watchdog);
  }
})().catch(error => {console.error(error); process.exitCode=1;});
"""
        try:
            result = subprocess.run(
                ["node", "-e", script, browser, str(profile.resolve()), str(SERVER.STATIC_DIR / "index.html")],
                capture_output=True, text=True, timeout=40,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            shutil.rmtree(profile)

    def assert_exclusive_action(self, endpoint: str, payload: dict):
        starter = "_start_workflow" if endpoint in ("/api/full-workflow", "/api/run-pipeline") else "_start"
        with patch.object(SERVER, starter, return_value="job-id") as start:
            response = self.client.post(endpoint, json=payload)

        self.assertEqual(response.status_code, 200, response.get_json())
        if starter == "_start":
            self.assertTrue(start.call_args.kwargs.get("exclusive"))
        else:
            self.assertEqual(start.call_count, 1)
            self.assertTrue(SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True).exclusive)

    def test_all_workspace_mutations_are_exclusive(self):
        target = {"tenant": "tenant.example", "workspace": "Demo Workspace"}
        actions = [
            (
                "/api/sync",
                {
                    **target,
                    "repository": "owner/repository",
                    "pat": "test-pat",
                },
            ),
            ("/api/delete", target),
            (
                "/api/run-pipeline",
                {
                    **target,
                    "parameters": PIPELINE_PARAMS,
                },
            ),
            ("/api/deploy-app", target),
            ("/api/setup-foundry", target),
            ("/api/setup-energy", target),
            (
                "/api/full-workflow",
                {
                    **target,
                    "repository": "owner/repository",
                    "connectionId": "yes",
                    "parameters": PIPELINE_PARAMS,
                },
            ),
        ]
        for endpoint, payload in actions:
            with self.subTest(endpoint=endpoint):
                self.assert_exclusive_action(endpoint, payload)

    def test_core_setup_includes_energy_once_and_keeps_foundry_separate(self):
        target = {"tenant": "tenant.example", "workspace": "Demo", "parameters": PIPELINE_PARAMS}
        with patch.object(SERVER, "_start_workflow", return_value="core-job") as start:
            response = self.client.post("/api/run-pipeline", json=target)
        self.assertEqual(response.status_code, 200)
        steps = start.call_args.args[0]
        self.assertEqual([step["name"] for step in steps], ["Setup pipeline", "Energy setup"])
        self.assertEqual(steps[1]["argv"][steps[1]["argv"].index("--stage") + 1], "energy")
        self.assertIn("--energy-read-models-only", steps[1]["argv"])
        self.assertEqual(start.call_args.kwargs["phases"], SERVER.CORE_SETUP_PHASES)
        self.assertLess(steps[0]["phase_offset"], steps[1]["phase_offset"])
        self.assertFalse(any(phase.startswith("Foundry") for phase in SERVER.CORE_SETUP_PHASES))
        with patch.object(SERVER, "_start_workflow", return_value="full-job") as start:
            response = self.client.post("/api/full-workflow", json={
                **target, "repository": "owner/repo", "connectionId": "yes", "energyMap": "keep",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([step["name"] for step in start.call_args.args[0]], SERVER.WORKFLOW_STEPS)

    def test_full_deploy_is_outside_collapsible_actions_and_all_actions_have_icons(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        navigation = page[page.index('<nav class="nav"'):page.index("</nav>")]
        group = navigation[navigation.index('<details id="deploymentActions"'):navigation.index("</details>")]
        self.assertIn("Deployment Actions", group)
        self.assertNotIn('id="tabWorkflow"', group)
        self.assertIn("> Full Deploy</button>", navigation)
        self.assertNotIn("Full workflow", page)
        for name in ("Sync", "Pipeline", "Energy", "Foundry", "Deploy", "Delete"):
            button = group[group.index(f'id="tab{name}"'):]
            button = button[:button.index("</button>")]
            self.assertIn("<svg", button)
        self.assertIn("Refresh energy sources", group)
        self.assertNotIn("workflowEnableEnergy", page)

    def test_sidebar_toggle_is_accessible_and_remembers_state(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('aria-controls="deploymentSidebar"', page)
        self.assertIn(".page.sidebar-collapsed .shell { grid-template-columns:minmax(0,1fr); }", page)
        code = page[page.index("function setSidebarCollapsed("):page.index("function renderFoundryFields(")]
        script = """
const assert = require("node:assert/strict");
const elements = {
  deploymentSidebar:{hidden:false},
  sidebarToggle:{setAttribute(name,value){this[name]=value},addEventListener(name,handler){this.click=handler}},
  sidebarPreferenceStatus:{textContent:""},
};
const $ = id => elements[id];
let collapsedClass=false;
const document = {querySelector:()=>({classList:{toggle:(name,value)=>{collapsedClass=value}}})};
const localStorage = {getItem:()=>"true",setItem:(key,value)=>{localStorage.saved=value}};
""" + code + """
assert.equal(elements.deploymentSidebar.hidden,true);
assert.equal(elements.sidebarToggle["aria-expanded"],"false");
assert.equal(elements.sidebarToggle.textContent,"Show sidebar");
elements.sidebarToggle.click();
assert.equal(elements.deploymentSidebar.hidden,false);
assert.equal(elements.sidebarToggle["aria-expanded"],"true");
assert.equal(collapsedClass,false);
assert.equal(localStorage.saved,"false");
localStorage.setItem=()=>{throw new Error("blocked")};
elements.sidebarToggle.click();
assert.equal(elements.deploymentSidebar.hidden,true);
assert.match(elements.sidebarPreferenceStatus.textContent,/Could not save.*blocked/);
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_connection_tests_only_start_from_explicit_buttons_in_their_own_panes(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("maybeAutoTest", page)
        self.assertNotIn("openSyncForConnectionTest", page)
        self.assertEqual(page.count('runJob("/api/test-connection"'), 1)
        code = page[
            page.index('document.querySelectorAll("input").forEach'):
            page.index('$("syncBtn").addEventListener')
        ]
        script = """
const assert = require("node:assert/strict");
const fields = new Map();
let activePane="", connTestState=null, workflowConnTestState=null;
const $ = id => {
  if(!fields.has(id)) fields.set(id,{
    value:id, checked:true, disabled:false, open:false, listeners:{},
    classList:{contains:()=>id===activePane}, focus(){},
    addEventListener(type,handler){(this.listeners[type]??=[]).push(handler)},
    dispatchEvent(event){for(const handler of this.listeners[event.type]||[])handler(event)},
  });
  return fields.get(id);
};
const inputs=["tenant","workspace","repository","branch","directory","pat","reuseConn",
              "workflowRepository","workflowBranch","workflowDirectory","workflowPat","workflowReuseConn"].map($);
const document={querySelectorAll:()=>inputs};
const results=new Map();
const refresh=()=>{}, setTestResult=(cls,text,id="connTestResult")=>results.set(id,cls);
const testValid=()=>true, workflowTestValid=()=>true;
const connValue=()=>"yes", workflowReuseChecked=()=>$("workflowReuseConn").checked;
const workflowConnValue=()=>"yes";
const requests=[];
let complete;
const runJob=async(url,payload,panel,onDone)=>{requests.push({url,payload,panel});complete=onDone};
""" + code + """
(async()=>{
  for(const field of inputs) {
    field.dispatchEvent(new Event("input"));
    field.dispatchEvent(new Event("change"));
  }
  assert.equal(requests.length,0,"Edits and reuse toggles started a test");
  await runTest();
  await runTest(true);
  assert.equal(requests.length,0,"Hidden Sync pane started a test");
  activePane="paneWorkflow";
  await $("workflowTestBtn").listeners.click[0]();
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,"/api/test-connection");
  assert.equal(requests[0].payload.repository,"workflowRepository");
  assert.equal(requests[0].payload.pat,"workflowPat");
  assert.equal(requests[0].panel,$("workflowTestPanel"));
  assert.equal(activePane,"paneWorkflow","Full Deploy test navigated away");
  assert.equal($("repository").value,"repository","Full Deploy copied values into Sync");
  await $("workflowTestBtn").listeners.click[0]();
  await $("testBtn").listeners.click[0]();
  assert.equal(requests.length,1,"Duplicate in-flight test started");
  complete({returncode:0});
  assert.equal(workflowConnTestState,"ok");
  assert.equal(results.get("workflowConnTestResult"),"ok");
  assert.equal(connTestState,null);
  activePane="paneSync";
  await $("testBtn").listeners.click[0]();
  assert.equal(requests.length,2);
  assert.equal(requests[1].payload.repository,"repository");
  assert.equal(requests[1].panel,$("testPanel"));
  complete({returncode:1});
  assert.equal(connTestState,"fail");
  assert.equal(results.get("connTestResult"),"error");
  assert.equal(workflowConnTestState,"ok");
  activePane="paneDeploy";
  await runTest();
  await runTest(true);
  assert.equal(requests.length,2,"Test started outside the source pane");
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_does_not_expose_agent_mode_flags(self):
        response = self.client.get("/api/pipeline-params")
        self.assertEqual(response.status_code, 200)
        specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
        for name in ("ontology_data_agent_mode", "ontology_operations_agent_mode"):
            self.assertNotIn(name, specs)

    def test_launcher_separates_alert_recipient_from_run_as_and_teams(self):
        response = self.client.get("/api/pipeline-params")
        self.assertEqual(response.status_code, 200)
        specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
        self.assertEqual(specs["alert_email_to"]["default"], "")
        self.assertIn("independent", specs["alert_email_to"]["help"])
        pipeline = json.loads((SERVER.REPO_ROOT / "Orchestrator_Pipelines" /
                               "01_Pipe_Setup.DataPipeline" / "pipeline-content.json").read_text())
        for name in ("key_vault_uri", "ops_agent_teams_team_id", "ops_agent_teams_channel_id",
                     "ops_agent_run_as_user"):
            self.assertEqual(specs[name]["default"], "")
            self.assertEqual(pipeline["properties"]["parameters"][name]["defaultValue"], "")

    def test_pipeline_requires_destinations_before_start_and_clears_legacy_run_as_default(self):
        for name in ("ops_agent_teams_team_id", "ops_agent_teams_channel_id"):
            with self.subTest(name=name), patch.object(SERVER, "_start") as start:
                response = self.client.post("/api/run-pipeline", json={
                    "tenant": "tenant.example", "workspace": "Demo",
                    "parameters": {**PIPELINE_PARAMS, name: ""},
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn("required for Operations Agent", response.get_json()["error"])
                start.assert_not_called()
        _, env = SERVER._pipeline_invocation({
            "tenant": "tenant.example", "workspace": "Demo", "parameters": PIPELINE_PARAMS,
        })
        self.assertEqual(json.loads(env["FABRIC_PIPELINE_PARAMS"])["ops_agent_run_as_user"], "")

    def test_parameter_defaults_are_tenant_scoped_and_allowlisted(self):
        config = {
            "FABRIC_TENANT": "tenant.example",
            "FABRIC_WORKSPACE": "Demo",
            "FABRIC_PIPELINE_PARAMS": json.dumps({
                **PIPELINE_PARAMS, "env_suffix": "V12", "per_notebook_timeout_secs": 7200,
                "workspace_id": "wrong-workspace", "pat": "must-not-be-returned",
            }),
        }
        with patch.dict(os.environ, config):
            for target, expected in (
                ("?tenant=TENANT.example&workspace=demo", "V12"),
                ("?tenant=other.example&workspace=Demo", "V6"),
                ("?tenant=tenant.example&workspace=Other", "V12"),
                ("?tenant=tenant.example", "V12"),
                ("", "V6"),
            ):
                with self.subTest(target=target):
                    response = self.client.get("/api/pipeline-params" + target)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                    self.assertEqual(specs["env_suffix"]["default"], expected)
                    self.assertNotIn("pat", specs)
                    self.assertNotIn("must-not-be-returned", response.get_data(as_text=True))
                    self.assertEqual(specs["workspace_id"]["default"], "")
                    if expected == "V12":
                        self.assertEqual(specs["per_notebook_timeout_secs"]["default"], "7200")
                        self.assertEqual(specs["key_vault_uri"]["source"], "server configuration")
        self.assertEqual(SERVER.PIPELINE_PARAM_SPEC[0]["default"], "V6")

    def test_parameter_defaults_report_invalid_configuration(self):
        for raw in ("{", "[]", '{"env_suffix":true}', '{"alert_email_to":{}}',
                    '{"per_notebook_timeout_secs":"not a number"}'):
            with self.subTest(raw=raw), patch.dict(os.environ, {
                "FABRIC_TENANT": "tenant.example", "FABRIC_WORKSPACE": "Demo",
                "FABRIC_PIPELINE_PARAMS": raw,
            }):
                response = self.client.get("/api/pipeline-params")
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.get_json()["error"])
        with patch.dict(os.environ, {
            "FABRIC_TENANT": "", "FABRIC_WORKSPACE": "", "FABRIC_PIPELINE_PARAMS": "{}",
        }):
            response = self.client.get("/api/pipeline-params")
            self.assertEqual(response.status_code, 400)
            self.assertIn("FABRIC_TENANT", response.get_json()["error"])

    def test_local_parameter_defaults_are_scoped_and_environment_takes_precedence(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline-defaults.local.json"
            path.write_text(json.dumps({
                "tenant": "tenant.example", "workspace": "Demo",
                "parameters": {**PIPELINE_PARAMS, "pat": "never-expose"},
            }), encoding="utf-8")
            with patch.object(SERVER, "PIPELINE_DEFAULTS_FILE", path), patch.dict(os.environ, {
                "FABRIC_PIPELINE_PARAMS": "", "FABRIC_TENANT": "", "FABRIC_WORKSPACE": "",
            }):
                response = self.client.get("/api/pipeline-params?tenant=TENANT.example&workspace=demo")
                self.assertEqual(response.status_code, 200)
                specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                self.assertEqual(specs["key_vault_uri"]["default"], PIPELINE_PARAMS["key_vault_uri"])
                self.assertEqual(specs["key_vault_uri"]["source"], "local configuration")
                self.assertNotIn("never-expose", response.get_data(as_text=True))
                response = self.client.get("/api/pipeline-params?tenant=other.example&workspace=demo")
                specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                self.assertEqual(specs["key_vault_uri"]["default"], "")
                with patch.dict(os.environ, {
                    "FABRIC_PIPELINE_PARAMS": '{"key_vault_uri":"https://environment.vault.azure.net/"}',
                    "FABRIC_TENANT": "tenant.example", "FABRIC_WORKSPACE": "Demo",
                }):
                    response = self.client.get("/api/pipeline-params?tenant=tenant.example&workspace=Demo")
                    specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                    self.assertEqual(specs["key_vault_uri"]["default"], "https://environment.vault.azure.net/")
                    self.assertEqual(specs["key_vault_uri"]["source"], "server configuration")

    def test_invalid_local_defaults_are_reported(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline-defaults.local.json"
            with patch.object(SERVER, "PIPELINE_DEFAULTS_FILE", path), patch.dict(os.environ, {
                "FABRIC_PIPELINE_PARAMS": "",
            }):
                for raw in ("{", "[]", '{"tenant":true,"workspace":"Demo","parameters":{}}',
                            '{"tenant":"","workspace":"Demo","parameters":{}}'):
                    path.write_text(raw, encoding="utf-8")
                    with self.subTest(raw=raw):
                        response = self.client.get("/api/pipeline-params")
                        self.assertEqual(response.status_code, 400)
                        self.assertTrue(response.get_json()["error"])

    def test_defaults_persist_across_clients_targets_and_verified_workspace_aliases(self):
        tenant, workspace_id = str(uuid.uuid4()), str(uuid.uuid4())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline-defaults.local.json"
            target_file = Path(directory) / ".env"
            target_file.write_text(
                f"RAYFIN_PUBLIC_TENANT_ID={tenant}\n"
                f"RAYFIN_PUBLIC_WORKSPACE_ID={workspace_id}\nFABRIC_WORKSPACE_NAME=Demo\n",
                encoding="utf-8",
            )
            path.write_text(json.dumps({
                "tenant": tenant, "workspace": "Demo",
                "pat": "must-not-persist",
                "parameters": {**PIPELINE_PARAMS, "pat": "must-not-persist"},
            }), encoding="utf-8")
            with (
                patch.object(SERVER, "PIPELINE_DEFAULTS_FILE", path),
                patch.object(SERVER, "RAYFIN_TARGET_FILE", target_file),
                patch.dict(os.environ, {"FABRIC_PIPELINE_PARAMS": ""}),
                patch.object(SERVER, "_start") as start,
                patch.object(SERVER.subprocess, "run") as process,
            ):
                with patch.object(SERVER, "RAYFIN_TARGET_FILE", Path(directory) / "unresolved.env"):
                    response = self.client.get("/api/pipeline-params", query_string={"tenant": tenant, "workspace": workspace_id})
                    specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                    self.assertEqual(specs["key_vault_uri"]["default"], PIPELINE_PARAMS["key_vault_uri"])
                response = self.client.get("/api/pipeline-params", query_string={"tenant": tenant, "workspace": workspace_id})
                specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                self.assertEqual(specs["key_vault_uri"]["default"], PIPELINE_PARAMS["key_vault_uri"])
                for target, values in (
                    ({"tenant": tenant, "workspace": workspace_id}, {"env_suffix": "V21", "key_vault_uri": "https://last.vault.azure.net/"}),
                    ({"tenant": tenant, "workspace": "Other"}, {"ops_agent_teams_team_id": "other-team"}),
                    ({"tenant": "other-tenant", "workspace": "Demo"}, {"ops_agent_teams_channel_id": "other-channel"}),
                ):
                    response = self.client.post("/api/pipeline-params", json={**target, "parameters": values})
                    self.assertEqual(response.status_code, 200, response.get_json())
                self.assertNotIn("must-not-persist", path.read_text(encoding="utf-8"))
                self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["targets"]), 3)
                # A different browser and later deployment target must still restore the old target.
                target_file.write_text("FABRIC_WORKSPACE_NAME=Another\n", encoding="utf-8")
                for workspace in ("DEMO", workspace_id):
                    response = SERVER.app.test_client().get("/api/pipeline-params", query_string={"tenant": tenant.upper(), "workspace": workspace})
                    self.assertEqual(response.status_code, 200, response.get_json())
                    specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                    self.assertEqual(specs["env_suffix"]["default"], "V21")
                    self.assertEqual(specs["key_vault_uri"]["default"], "https://last.vault.azure.net/")
                    self.assertEqual(specs["ops_agent_teams_team_id"]["default"], PIPELINE_PARAMS["ops_agent_teams_team_id"])
                    self.assertEqual(specs["ops_agent_teams_channel_id"]["default"], PIPELINE_PARAMS["ops_agent_teams_channel_id"])
                    self.assertEqual(specs["key_vault_uri"]["source"], "saved local settings")
                for target in (
                    {"tenant": "unseen-tenant", "workspace": "Demo"},
                ):
                    response = self.client.get("/api/pipeline-params", query_string=target)
                    specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                    self.assertEqual(specs["key_vault_uri"]["default"], "")
                    self.assertEqual(specs["ops_agent_teams_team_id"]["default"], "")
                    self.assertEqual(specs["ops_agent_teams_channel_id"]["default"], "")
                for workspace in ("Unknown", "", "brand-new-workspace"):
                    response = self.client.get("/api/pipeline-params", query_string={
                        "tenant": tenant, "workspace": workspace,
                    })
                    specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                    self.assertEqual(specs["key_vault_uri"]["default"], "https://last.vault.azure.net/")
                    self.assertEqual(specs["ops_agent_teams_team_id"]["default"], "other-team")
                    self.assertEqual(specs["ops_agent_teams_channel_id"]["default"], PIPELINE_PARAMS["ops_agent_teams_channel_id"])
                    self.assertEqual(specs["workspace_id"]["default"], "")
                response = self.client.get("/api/pipeline-params", query_string={
                    "tenant": "other-tenant", "workspace": workspace_id,
                })
                specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
                self.assertEqual(specs["key_vault_uri"]["default"], "")
                self.assertEqual(specs["ops_agent_teams_channel_id"]["default"], "other-channel")
                start.assert_not_called()
                process.assert_not_called()
                self.assertEqual(list(Path(directory).glob(".pipeline-defaults-*.tmp")), [])

    def test_saved_defaults_override_environment_but_preserve_deliberate_clears(self):
        with TemporaryDirectory() as directory, patch.object(
            SERVER, "PIPELINE_DEFAULTS_FILE", Path(directory) / "defaults.json",
        ), patch.dict(os.environ, {
            "FABRIC_TENANT": "tenant.example", "FABRIC_WORKSPACE": "Demo",
            "FABRIC_PIPELINE_PARAMS": json.dumps(PIPELINE_PARAMS),
        }):
            target = {"tenant": "tenant.example", "workspace": "Demo"}
            response = self.client.post("/api/pipeline-params", json={
                **target, "parameters": {"key_vault_uri": "", "per_notebook_timeout_secs": ""},
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            response = SERVER.app.test_client().get("/api/pipeline-params", query_string=target)
            specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
            self.assertEqual(specs["key_vault_uri"]["default"], "")
            self.assertEqual(specs["per_notebook_timeout_secs"]["default"], "")
            self.assertEqual(specs["ops_agent_teams_team_id"]["default"], PIPELINE_PARAMS["ops_agent_teams_team_id"])

    def test_tenant_defaults_load_before_workspace_entry_without_restoring_workspace_identity(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "defaults.json"
            path.write_text(json.dumps({
                "version": 2, "targets": [{
                    "tenant": "tenant.example", "workspace": "Demo", "saved": True,
                    "parameters": {**PIPELINE_PARAMS, "env_suffix": "V12"},
                }],
            }), encoding="utf-8")
            with (
                patch.object(SERVER, "PIPELINE_DEFAULTS_FILE", path),
                patch.dict(os.environ, {"FABRIC_PIPELINE_PARAMS": ""}),
                patch.object(SERVER, "_start") as start,
                patch.object(SERVER.subprocess, "run") as process,
            ):
                response = self.client.get("/api/pipeline-params", query_string={
                    "tenant": "TENANT.example", "workspace": "", "restoreTarget": "1",
                })
                self.assertEqual(response.status_code, 200, response.get_json())
                data = response.get_json()
                self.assertIsNone(data.get("restoredTarget"))
                specs = {spec["name"]: spec for spec in data["parameters"]}
                self.assertEqual(specs["workspace_id"]["default"], "")
                self.assertEqual(specs["key_vault_uri"]["default"], PIPELINE_PARAMS["key_vault_uri"])
                data = self.client.get("/api/pipeline-params", query_string={
                    "tenant": "TENANT.example", "workspace": "Demo",
                }).get_json()
                specs = {spec["name"]: spec for spec in data["parameters"]}
                self.assertEqual(specs["env_suffix"]["default"], "V12")
                for name, value in PIPELINE_PARAMS.items():
                    self.assertEqual(specs[name]["default"], value)
                for query in (
                    {"tenant": "other-tenant", "workspace": "", "restoreTarget": "1"},
                    {"tenant": "", "workspace": "", "restoreTarget": "1"},
                ):
                    with self.subTest(query=query):
                        data = self.client.get("/api/pipeline-params", query_string=query).get_json()
                        self.assertIsNone(data.get("restoredTarget"))
                        specs = {spec["name"]: spec for spec in data["parameters"]}
                        self.assertEqual(specs["key_vault_uri"]["default"], "")
                for workspace in ("Other", ""):
                    data = self.client.get("/api/pipeline-params", query_string={
                        "tenant": "tenant.example", "workspace": workspace,
                    }).get_json()
                    specs = {spec["name"]: spec for spec in data["parameters"]}
                    self.assertEqual(specs["env_suffix"]["default"], "V12")
                    self.assertEqual(specs["key_vault_uri"]["default"], PIPELINE_PARAMS["key_vault_uri"])
                    self.assertEqual(specs["workspace_id"]["default"], "")
                stored = json.loads(path.read_text(encoding="utf-8"))
                stored["targets"].append({
                    "tenant": "tenant.example", "workspace": "Second",
                    "parameters": {"key_vault_uri": "https://second.vault.azure.net/"},
                })
                path.write_text(json.dumps(stored), encoding="utf-8")
                data = self.client.get("/api/pipeline-params", query_string={
                    "tenant": "tenant.example", "workspace": "", "restoreTarget": "1",
                }).get_json()
                self.assertIsNone(data.get("restoredTarget"))
                specs = {spec["name"]: spec for spec in data["parameters"]}
                self.assertEqual(specs["workspace_id"]["default"], "")
                self.assertEqual(specs["key_vault_uri"]["default"], PIPELINE_PARAMS["key_vault_uri"])
                start.assert_not_called()
                process.assert_not_called()

    def test_defaults_save_rejects_invalid_input_and_reports_atomic_write_failure(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "defaults.json"
            with patch.object(SERVER, "PIPELINE_DEFAULTS_FILE", path), patch.dict(os.environ, {"FABRIC_PIPELINE_PARAMS": ""}):
                target = {"tenant": "tenant.example", "workspace": "Demo"}
                for data in (
                    [], {}, {**target, "parameters": {"pat": "secret"}},
                    {**target, "parameters": {"workspace_id": "wrong"}},
                    {**target, "workspace": "", "parameters": {}},
                    {**target, "parameters": {"key_vault_uri": False}},
                    {**target, "parameters": {"per_notebook_timeout_secs": "invalid"}},
                ):
                    with self.subTest(data=data):
                        response = self.client.post("/api/pipeline-params", json=data)
                        self.assertEqual(response.status_code, 400, response.get_json())
                        self.assertFalse(path.exists())
                payload = {**target, "parameters": PIPELINE_PARAMS}
                self.assertEqual(self.client.post("/api/pipeline-params", json=payload).status_code, 200)
                previous = path.read_bytes()
                with patch.object(Path, "replace", side_effect=PermissionError("write blocked")):
                    response = self.client.post("/api/pipeline-params", json={
                        **target, "parameters": {"key_vault_uri": "https://new.vault.azure.net/"},
                    })
                self.assertEqual(response.status_code, 500)
                self.assertIn("write blocked", response.get_json()["error"])
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(Path(directory).glob(".pipeline-defaults-*.tmp")), [])
                path.write_text("{", encoding="utf-8")
                self.assertEqual(self.client.post("/api/pipeline-params", json=payload).status_code, 400)
                self.assertEqual(path.read_text(encoding="utf-8"), "{")

    def test_browser_keeps_workspaces_blank_until_explicit_entry(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        function = page[page.index("async function loadPipelineParams("):page.index("function collectPipelineParams(")]
        specs = [
            {"name": name, "default": value, "source": "saved local settings"}
            for name, value in {**PIPELINE_PARAMS, "env_suffix": "V12", "workspace_id": ""}.items()
        ]
        script = """
const assert = require("node:assert/strict");
const fields = new Map();
const $ = id => fields.get(id);
for (const id of ["tenant","workspace","pipelineFields","workflowPipelineFields","targetSettingsStatus"]) {
  fields.set(id,{value:"",textContent:"",innerHTML:"",classList:{remove(){}},
    prepend(status){fields.set(status.id,status);}});
}
const document = {createElement:()=>({setAttribute(){}})};
let pipelineSpec=[], pipelineLoadSequence=0, pipelineTarget="", pipelineSavedValues={};
let renderCount=0;
const pipelineTargetKey = () => $("tenant").value.trim() && $("workspace").value.trim()
  ? JSON.stringify([$("tenant").value.trim(),$("workspace").value.trim()]) : "";
const restoreFoundrySettings = () => {};
const readPipelineSettings = () => ({});
const renderPipelineFields = prefix => {
  renderCount++;
  for(const spec of pipelineSpec) fields.set(prefix+spec.name,{value:spec.default,addEventListener(){}});
  return "";
};
const syncPipelineWorkspace = () => {
  for(const prefix of ["pp_","wf_pp_"]) $(prefix+"workspace_id").value=$("workspace").value;
};
const suggestPipelineEmail = () => {};
const updatePipelineDefaultsSummary = () => {};
const loadSubscriptions = () => {};
const refresh = () => {};
const escapeHtml = String;
const persistPipelineSettings = () => {throw new Error("Startup must not save invented values");};
const requests=[];
const specs = """ + json.dumps(specs) + """;
const saved = {parameters:specs};
const blank = {parameters:specs.map(spec=>({...spec,default:"",source:undefined}))};
global.fetch = async url => {
  requests.push(url);
  const query = new URL(url,"http://localhost").searchParams;
  assert.equal(query.get("workspace"),"");
  assert.equal(query.has("restoreTarget"),false);
  return {ok:true,json:async()=>saved};
};
""" + function + """
(async()=>{
  $("tenant").value="tenant.example";
  $("workspace").value="";
  await loadPipelineParams();
  assert.equal($("workspace").value,"");
  assert.equal($("pp_workspace_id").value,"");
  assert.equal($("wf_pp_workspace_id").value,"");
  for(const prefix of ["pp_","wf_pp_"]) {
    for(const name of ["key_vault_uri","ops_agent_teams_team_id","ops_agent_teams_channel_id"]) {
      assert.equal($(prefix+name).value,specs.find(spec=>spec.name===name).default);
    }
  }
  assert.equal(requests.length,1);
  $("workspace").value="Demo";
  global.fetch=async url=>{
    assert.equal(new URL(url,"http://localhost").searchParams.get("workspace"),"Demo");
    return {ok:true,json:async()=>saved};
  };
  await loadPipelineParams();
  assert.equal($("workspace").value,"Demo");
  assert.equal(pipelineTarget,pipelineTargetKey());
  for(const prefix of ["pp_","wf_pp_"]) {
    assert.equal($(prefix+"workspace_id").value,"Demo");
    for(const name of ["key_vault_uri","ops_agent_teams_team_id","ops_agent_teams_channel_id"]) {
      assert.equal($(prefix+name).value,specs.find(spec=>spec.name===name).default);
    }
  }
  $("workspace").value="Brand new workspace";
  global.fetch=async url=>{
    assert.equal(new URL(url,"http://localhost").searchParams.get("workspace"),"Brand new workspace");
    return {ok:true,json:async()=>saved};
  };
  await loadPipelineParams();
  for(const prefix of ["pp_","wf_pp_"]) {
    assert.equal($(prefix+"workspace_id").value,"Brand new workspace");
    assert.equal($(prefix+"key_vault_uri").value,specs.find(spec=>spec.name==="key_vault_uri").default);
    assert.equal($(prefix+"ops_agent_teams_team_id").value,specs.find(spec=>spec.name==="ops_agent_teams_team_id").default);
  }
  // A user editing the target during startup must win over the delayed response.
  $("workspace").value="";
  let respond;
  global.fetch=()=>new Promise(resolve=>{respond=resolve;});
  let previousRenders=renderCount;
  let loading=loadPipelineParams();
  $("workspace").value="New workspace";
  respond({ok:true,json:async()=>saved});
  await loading;
  assert.equal($("workspace").value,"New workspace");
  assert.equal(renderCount,previousRenders);
  // Incomplete targets share an empty storage key, but must not share responses.
  $("workspace").value="";
  previousRenders=renderCount;
  loading=loadPipelineParams();
  $("tenant").value="different-tenant";
  respond({ok:true,json:async()=>saved});
  await loading;
  assert.equal($("workspace").value,"");
  assert.equal(renderCount,previousRenders);
  $("tenant").value="tenant.example";
  global.fetch=async url=>{
    assert.equal(new URL(url,"http://localhost").searchParams.has("restoreTarget"),false);
    return {ok:true,json:async()=>blank};
  };
  await loadPipelineParams();
  assert.equal($("workspace").value,"");
  assert.match($("pipelineSettingsStatus").textContent,/Enter a workspace/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_teams_fields_link_to_microsoft_id_instructions(self):
        response = self.client.get("/api/pipeline-params")
        specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
        for name in ("ops_agent_teams_team_id", "ops_agent_teams_channel_id"):
            self.assertTrue(specs[name]["helpUrl"].startswith("https://learn.microsoft.com/"))
            self.assertIn("#deep-link-to-navigate-to-channel", specs[name]["helpUrl"])
        self.assertIn("groupId=", specs["ops_agent_teams_team_id"]["help"])
        self.assertIn("URL-decode", specs["ops_agent_teams_channel_id"]["help"])
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        code = page[page.index("function renderPipelineFields("):page.index("function updatePipelineDefaultsSummary(")]
        script = """
const assert=require("node:assert/strict");
const pipelineSpec = """ + json.dumps(list(specs.values())) + """;
const requiredPipelineFields=["key_vault_uri","ops_agent_teams_team_id","ops_agent_teams_channel_id","alert_email_to"];
const escapeHtml=value=>String(value).replaceAll("&","&amp;").replaceAll('"',"&quot;");
""" + code + """
for(const prefix of ["pp_","wf_pp_"]){
  const html=renderPipelineFields(prefix);
  assert.equal(html.split(">Find ID</a>").length-1,2);
  assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
  const visible=html.split("<details")[0];
  assert.ok(visible.includes("Required:"));
  assert.ok(visible.includes("existing Key Vault"));
  assert.ok(visible.includes("notebook read access"));
  assert.ok(visible.replace(/<[^>]*>/g," ").trim().split(/\\s+/).length <= 100);
  assert.ok(!visible.includes("defaultsSummary"));
  assert.ok(html.includes("tenant ID, client ID and client secret"));
  assert.ok(html.includes("does not create the vault or its secrets"));
  assert.ok(html.includes("<summary>Key Vault secret names and notebook settings</summary>"));
  assert.ok(!html.includes("Change notebook defaults (optional)"));
}
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_browser_parameter_storage_and_identity_suggestions(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        functions = (
            page[page.index("const spaClientIds ="):page.index("function renderFoundryFields(")]
            + page[page.index("let pipelineSpec ="):page.index("function renderPipelineFields(")]
            + page[page.index("function updatePipelineDefaultsSummary("):page.index("async function loadPipelineParams(")]
        )
        script = """
const assert = require("node:assert/strict");
const fields = new Map();
const $ = id => fields.get(id);
for (const id of ["tenant", "workspace", "targetSettingsStatus", "pp_alert_email_to", "wf_pp_alert_email_to",
  "pp_env_suffix", "wf_pp_env_suffix", "pipelineSettingsStatus", "workflowPipelineSettingsStatus"]) {
  fields.set(id, {value:"", textContent:""});
}
const stored = new Map();
const localStorage = {
  getItem:key => stored.get(key) ?? null,
  setItem:(key,value) => stored.set(key,value),
};
""" + functions + """
$("tenant").value = " tenant-A ";
$("workspace").value = " Demo ";
saveTargetSettings();
assert.equal(JSON.parse(stored.get("fabric-demo.target.v1")).workspace, undefined);
stored.set("fabric-demo.target.v1", JSON.stringify({tenant:"tenant-A",workspace:"old-workspace"}));
$("tenant").value = "";
$("workspace").value = "";
restoreTargetSettings();
assert.equal($("tenant").value, "tenant-A");
assert.equal($("workspace").value, "");
stored.set("fabric-demo.target.v1", "{");
$("tenant").value = "";
$("workspace").value = "";
restoreTargetSettings();
assert.match($("targetSettingsStatus").textContent, /Could not restore the last target/);
$("tenant").value = "tenant-A";
$("workspace").value = "Demo";
pipelineTarget = pipelineTargetKey();
pipelineSpec = [{name:"env_suffix"}, {name:"alert_email_to"}, {name:"workspace_id"}];
savePipelineField("env_suffix", "V12");
assert.equal($("wf_pp_env_suffix").value, "V12");
assert.equal(readPipelineSettings().env_suffix, "V12");
savePipelineField("workspace_id", "must-not-save");
assert.equal(readPipelineSettings().workspace_id, undefined);
pipelineIdentity = {userType:"user", tenantId:"TENANT-A", user:"admin@example.test"};
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "admin@example.test");
assert.equal($("wf_pp_alert_email_to").value, "admin@example.test");
savePipelineField("alert_email_to", "");
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "");
$("workspace").value = "Other";
savePipelineField("env_suffix", "must-not-leak");
pipelineTarget = pipelineTargetKey();
pipelineSavedValues = readPipelineSettings();
assert.deepEqual(pipelineSavedValues, {});
pipelineIdentity.tenantId = "different-tenant";
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "");
pipelineIdentity.tenantId = "tenant-a";
pipelineIdentity.userType = "servicePrincipal";
suggestPipelineEmail();
assert.equal($("pp_alert_email_to").value, "");
$("workspace").value = "DEMO";
pipelineTarget = pipelineTargetKey();
assert.equal(readPipelineSettings().env_suffix, "V12");
assert.equal(readPipelineSettings().alert_email_to, "");
localStorage.getItem = () => "{";
assert.deepEqual(readPipelineSettings(), {});
assert.match($("pipelineSettingsStatus").textContent, /Could not restore/);
localStorage.setItem = () => { throw new Error("blocked"); };
savePipelineField("env_suffix", "V13");
assert.match($("pipelineSettingsStatus").textContent, /Could not save.*blocked/);
localStorage.getItem = key => stored.get(key) ?? null;
localStorage.setItem = (key,value) => stored.set(key,value);
for (const prefix of ["deploy", "workflow"]) {
  for (const suffix of [...foundrySettingSuffixes, "FoundryCreate", "FoundryStatus"]) {
    fields.set(prefix + suffix, {value:"",checked:false,dispatchEvent(){}});
  }
}
for (const clientId of spaClientIds) {
  fields.set(clientId, {value:""});
  fields.set(clientId+"UseExisting", {checked:false});
  fields.set(clientId+"Fields", {hidden:true});
  fields.set(clientId+"Status", {textContent:""});
}
restoreFoundrySettings();
for (const clientId of spaClientIds) {
  assert.equal(spaClientValue(clientId), "");
  assert.equal($(clientId+"UseExisting").checked, false);
}
$("foundryClientId").value="12345678-1234-1234-1234-123456789abc";
$("foundryClientIdUseExisting").checked=true;
saveFoundrySettings("deploy");
for (const clientId of spaClientIds) {
  assert.equal(spaClientValue(clientId), "12345678-1234-1234-1234-123456789abc");
  assert.equal($(clientId+"Fields").hidden, false);
}
$("foundryClientIdUseExisting").checked=false;
saveFoundrySettings("deploy");
for (const clientId of spaClientIds) assert.equal(spaClientValue(clientId), "");
$("workflowFoundryModel").value="approved-model";
$("workflowFoundryCreate").checked=true;
saveFoundrySettings("workflow");
assert.equal($("deployFoundryModel").value, "approved-model");
assert.equal(JSON.parse(stored.get(foundryTarget)).FoundryCreate, undefined);
$("workspace").value="Other";
restoreFoundrySettings();
assert.equal($("workflowFoundryModel").value, "");
assert.equal($("workflowFoundryCreate").checked, false);
for (const clientId of spaClientIds) {
  assert.equal($(clientId).value, "");
  assert.equal($(clientId+"UseExisting").checked, false);
}
$("workspace").value="Demo";
restoreFoundrySettings();
assert.equal($("workflowFoundryModel").value, "approved-model");
assert.equal($("workflowFoundryCreate").checked, false);
for (const clientId of spaClientIds) {
  assert.equal($(clientId+"UseExisting").checked, false);
  assert.equal(spaClientValue(clientId), "");
}
const legacyProject = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a/projects/p";
stored.set(foundryTarget, JSON.stringify({
  FoundryProjectResourceId:legacyProject, FoundryProjectName:"duplicate-name",
  FoundryNewModel:"legacy-chat-deployment",
  clientId:"12345678-1234-1234-1234-123456789abc", FoundryEndpoint:"retired-override",
  ProductionWrites:"true",
}));
foundryTarget="";
restoreFoundrySettings();
for (const prefix of ["deploy", "workflow"]) assert.equal($(prefix+"FoundryProjectName").value, legacyProject);
for (const prefix of ["deploy", "workflow"]) assert.equal($(prefix+"FoundryModel").value, "legacy-chat-deployment");
for (const clientId of spaClientIds) assert.equal($(clientId+"UseExisting").checked, true);
saveFoundrySettings("deploy");
assert.equal(JSON.parse(stored.get(foundryTarget)).FoundryEndpoint, undefined);
assert.equal(JSON.parse(stored.get(foundryTarget)).FoundryProjectResourceId, undefined);
assert.equal(JSON.parse(stored.get(foundryTarget)).FoundryNewModel, undefined);
assert.equal(foundrySettingSuffixes.includes("ProductionWrites"), false);
assert.equal(JSON.parse(stored.get(foundryTarget)).ProductionWrites, undefined);
(async () => {
  await pipelineSaveQueue;
  const requests = [];
  global.fetch = async (url, options) => {
    assert.equal(url, "/api/pipeline-params");
    assert.equal(options.method, "POST");
    assert.equal(options.keepalive, true);
    requests.push(JSON.parse(options.body));
    return {ok:true,json:async()=>({saved:true})};
  };
  pipelineTarget = pipelineTargetKey();
  savePipelineField("env_suffix", "V24");
  $("workspace").value = "Other";
  pipelineTarget = pipelineTargetKey();
  pipelineSavedValues = {};
  savePipelineField("env_suffix", "V25");
  await pipelineSaveQueue;
  assert.equal(requests[0].workspace, "Demo");
  assert.equal(requests[0].parameters.env_suffix, "V24");
  assert.equal(requests[1].workspace, "Other");
  assert.equal(requests[1].parameters.env_suffix, "V25");
  assert.equal(requests[1].parameters.workspace_id, undefined);
  assert.match($("pipelineSettingsStatus").textContent, /saved on this machine/);
  global.fetch = async () => ({ok:false,json:async()=>({error:"disk full"})});
  savePipelineField("env_suffix", "V26");
  await pipelineSaveQueue;
  assert.match($("pipelineSettingsStatus").textContent, /Could not save.*disk full.*not durably saved/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pipeline_rejects_blank_alert_recipient(self):
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/run-pipeline", json={
                "tenant": "tenant.example", "workspace": "DEV",
                "parameters": {
                    "key_vault_uri": "https://vault.vault.azure.net/",
                    "alert_email_to": " ",
                },
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Alert email recipient is required", response.get_json()["error"])
        start.assert_not_called()

    def test_alert_recipient_uses_the_standard_styled_input(self):
        html = (Path(SERVER.__file__).with_name("static") / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<input id="${prefix}${p.name}" type="text"',
            html,
        )
        self.assertIn('${requiredPipelineFields.includes(p.name) ? "required" : ""}', html)
        self.assertNotIn('type="${p.name === "alert_email_to" ? "email" : "text"}"', html)

    def test_pipeline_rejects_old_published_environment_before_starting(self):
        fabric = Mock()
        fabric.resolve_workspace_id.return_value = ("canonical-workspace", "DEV")
        fabric.request.side_effect = [
            Mock(status_code=200, json=Mock(return_value={"environment": {"runtimeVersion": "2.0"}})),
            Mock(status_code=200, json=Mock(return_value={"value": [{"id": "weather", "displayName": "Weather"}]})),
            Mock(status_code=200, json=Mock(return_value={"runtimeVersion": "1.3"})),
        ]
        with (
            patch.object(sys, "argv", ["run_pipeline.py", "--tenant", "tenant.example",
                                      "--workspace", "DEV", "--yes", "--no-wait"]),
            patch.object(run_pipeline, "Fabric", return_value=fabric),
            patch.object(run_pipeline, "ensure_key_vault_access") as key_vault,
            self.assertRaisesRegex(SystemExit, "Weather requires published Runtime 2.0"),
        ):
            run_pipeline.main()
        key_vault.assert_not_called()
        fabric.start_pipeline.assert_not_called()

    def test_pipeline_api_worker_and_poll_preserve_required_agent_failure_and_success(self):
        source = MODULE_PATH.parents[3] / "Orchestrator_Pipelines" / "01_Pipe_Setup.DataPipeline" / "pipeline-content.json"
        definition = {"definition": {"parts": [{
            "path": "pipeline-content.json", "payload": base64.b64encode(source.read_bytes()).decode(),
        }]}}
        for state, energy_code, expected_status, expected_code in (
            ("Completed", 0, "succeeded", 0), ("Failed", 0, "failed", 1),
            ("Completed", 23, "failed", 23),
        ):
            with self.subTest(state=state, energy_code=energy_code):
                fabric = Mock()
                fabric.resolve_workspace_id.return_value = ("canonical-workspace", "DEV")
                fabric.request.side_effect = [
                    Mock(status_code=200, json=Mock(return_value={"environment": {"runtimeVersion": "2.0"}})),
                    Mock(status_code=200, json=Mock(return_value={"value": [{"id": "weather"}]})),
                    Mock(status_code=200, json=Mock(return_value={"runtimeVersion": "2.0"})),
                    Mock(status_code=200, json=Mock(return_value=definition)),
                ]
                fabric.find_pipeline.return_value = ("pipeline-id", "01_Pipe_Setup")
                fabric.start_pipeline.return_value = "https://mock.invalid/job"
                reason = "Required Data Agent ontology runtime smoke failed: unsupported Ontology API"
                fabric.poll_job.return_value = {"status": state, "failureReason": {"message": reason}}
                started_scripts = []

                def child_process(command, **kwargs):
                    started_scripts.append(Path(command[2]).name)
                    if Path(command[2]).name == "deploy_fabric_app.py":
                        self.assertEqual(command[command.index("--stage") + 1], "energy")
                        return Mock(stdout=io.StringIO("Energy: setup\n"), returncode=energy_code)
                    output = io.StringIO()
                    with (
                        patch.object(sys, "argv", command[2:]),
                        patch.dict(os.environ, kwargs["env"]),
                        patch.object(run_pipeline, "Fabric", return_value=fabric),
                        patch.object(run_pipeline, "ensure_key_vault_access"),
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
                    ):
                        returncode = run_pipeline.main()
                    return Mock(stdout=io.StringIO(output.getvalue()), returncode=returncode)

                def start_job(steps, *, phases):
                    job = SERVER.Job(phases)
                    SERVER.JOBS[job.id] = job
                    with patch.object(SERVER.subprocess, "Popen", side_effect=child_process), patch.object(SERVER.threading, "Timer"):
                        SERVER._workflow_worker(job, steps)
                    return job.id

                try:
                    with patch.object(SERVER, "_start_workflow", side_effect=start_job):
                        response = self.client.post("/api/run-pipeline", json={
                            "tenant": "tenant.example", "workspace": "DEV",
                            "parameters": {**PIPELINE_PARAMS, "key_vault_uri": "https://mock.vault.azure.net/",
                                           "alert_email_to": "operations@example.test",
                                           "ontology_data_agent_mode": "enabled",
                                           "ontology_operations_agent_mode": "enabled"},
                        })
                    self.assertEqual(response.status_code, 200)
                    job = self.client.get("/api/jobs/" + response.get_json()["jobId"]).get_json()
                    self.assertEqual(job["status"], expected_status)
                    self.assertEqual(job["returncode"], expected_code)
                    self.assertTrue(job["done"])
                    parameters = fabric.start_pipeline.call_args.args[2]
                    self.assertEqual(parameters["workspace_id"], "canonical-workspace")
                    self.assertTrue(parameters["enable_energy_map"])
                    self.assertNotIn("ontology_data_agent_mode", parameters)
                    self.assertNotIn("ontology_operations_agent_mode", parameters)
                    log = "\n".join(job["lines"])
                    if state == "Failed":
                        self.assertIn(reason, log)
                        self.assertNotIn("run COMPLETED", log)
                        self.assertEqual(started_scripts, ["run_pipeline.py"])
                    else:
                        self.assertIn("run COMPLETED", log)
                        self.assertEqual(started_scripts, ["run_pipeline.py", "deploy_fabric_app.py"])
                        if energy_code:
                            self.assertEqual(job["failedComponent"], "Energy setup")
                finally:
                    SERVER.JOBS.clear()

    def test_pipeline_does_not_forward_hidden_agent_mode_flags(self):
        with patch.object(SERVER, "_start_workflow", return_value="job-id") as start:
            response = self.client.post("/api/run-pipeline", json={
                "tenant": "tenant.example", "workspace": "DEV",
                "parameters": {
                    **PIPELINE_PARAMS,
                    "key_vault_uri": "https://vault.vault.azure.net/",
                    "alert_email_to": "operations@example.test",
                    "ontology_data_agent_mode": "force",
                    "ontology_operations_agent_mode": "disabled",
                },
            })
        self.assertEqual(response.status_code, 200)
        forwarded = json.loads(start.call_args.args[0][0]["env"]["FABRIC_PIPELINE_PARAMS"])
        self.assertNotIn("ontology_data_agent_mode", forwarded)
        self.assertNotIn("ontology_operations_agent_mode", forwarded)

    def test_background_jobs_are_exclusive_by_default(self):
        with patch.object(SERVER.threading, "Thread"):
            first_job_id = SERVER._start([], None, 1, ["Queued", "Done"], [])
            second_job_id = SERVER._start([], None, 1, ["Queued", "Done"], [])
        try:
            self.assertIsNotNone(first_job_id)
            self.assertIsNone(second_job_id)
        finally:
            SERVER.JOBS.clear()

    def test_frontend_is_never_cached(self):
        response = self.client.get("/")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        finally:
            response.close()

    def test_deploy_app_forwards_optional_spa_client_id(self):
        client_id = "11111111-1111-1111-1111-111111111111"
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post(
                "/api/deploy-app",
                json={
                    "tenant": "tenant.example",
                    "workspace": "Demo Workspace",
                    "clientId": client_id,
                },
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        argv = start.call_args.args[0]
        self.assertEqual(argv[-2:], ["--client-id", client_id])

    def test_deploy_app_omits_client_id_for_automatic_resolution(self):
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post(
                "/api/deploy-app",
                json={"tenant": "tenant.example", "workspace": "Demo Workspace"},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn("--client-id", start.call_args.args[0])

    def test_deploy_progress_exposes_backend_sync_and_cors_readiness(self):
        self.assertIn("Syncing backend settings", SERVER.DEPLOY_PHASES)
        self.assertIn("Checking endpoint and CORS readiness", SERVER.DEPLOY_PHASES)

    def test_deploy_progress_shows_each_foundry_stage_in_the_order_the_deployer_prints_them(self):
        phases = SERVER.FOUNDRY_PHASES
        for name in ("Foundry project", "Fabric backend gateway", "Foundry agents", "Hosted Agent"):
            self.assertIn(name, phases)
        indexes = [index for index, _markers in SERVER.FOUNDRY_MARKERS]
        self.assertEqual(indexes, sorted(set(indexes)))
        self.assertLess(max(indexes), len(phases) - 1)
        deployer_lines = [
            "[1/8] Checking Azure tenant", "[2/8] Resolving the tenant SPA", "[3/8] Resetting local Rayfin deployment state",
            "Foundry: project - resolving the Foundry project.",
            "Foundry: backend gateway - preparing the Fabric AppBackend identity required by the Hosted Agent.",
            "Foundry: agents - provisioning the specialist prompt agents.",
            "Foundry: Hosted Agent - converging hydro-orchestrator.",
        ]
        job, seen = SERVER.Job(phases), []
        for line in deployer_lines:
            for index, markers in SERVER.FOUNDRY_MARKERS:
                if index > job.phase_index and any(marker in line for marker in markers):
                    job.phase_index = index
            seen.append(phases[job.phase_index])
        self.assertEqual(seen, [
            "Checking prerequisites", "Resolving SPA", "Preparing source/gateway", "Foundry project",
            "Fabric backend gateway", "Foundry agents", "Hosted Agent",
        ])
        # The markers must be lines the deployer really prints, not an assumption.
        printed = (SERVER.SCRIPT_DIR / "provision_foundry_agents.py").read_text(encoding="utf-8")
        for marker in ("Foundry: project", "Foundry: agents", "Foundry: Hosted Agent"):
            self.assertIn(f'"{marker}', printed)
        deployer = (SERVER.SCRIPT_DIR / "deploy_fabric_app.py").read_text(encoding="utf-8")
        self.assertIn('"Foundry: backend gateway', deployer)

    def test_deploy_job_limit_covers_project_creation_build_and_publish(self):
        self.assertGreaterEqual(SERVER.FOUNDRY_TIMEOUT_S, 75 * 60)
        self.assertLess(SERVER.DEPLOY_TIMEOUT_S, SERVER.FOUNDRY_TIMEOUT_S)

    def test_deploy_environment_forwards_only_validated_foundry_settings(self):
        env = SERVER._deploy_environment({
            "foundryEndpoint": "https://res.services.ai.azure.com/api/projects/proj/",
            "foundryModel": "gpt-5.6-sol", "acrName": "myreg12345", "foundryLocation": "Sweden Central",
            "createFoundry": True, "productionWrites": "TRUE", "unexpected": "ignored",
        })
        self.assertEqual(env, {
            "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "https://res.services.ai.azure.com/api/projects/proj/",
            "HYDRO_FOUNDRY_MODEL": "gpt-5.6-sol", "HYDRO_ACR_NAME": "myreg12345",
            "HYDRO_FOUNDRY_LOCATION": "Sweden Central", "HYDRO_FOUNDRY_CREATE": "true",
            "HYDRO_PRODUCTION_WRITES_ENABLED": "true",
        })
        self.assertEqual(SERVER._deploy_environment({}), {})
        self.assertEqual(SERVER._deploy_environment({
            "foundryEndpoint": "  ", "createFoundry": False, "productionWrites": "", "foundryModel": None,
        }), {"HYDRO_FOUNDRY_CREATE": "false"})
        with self.assertRaisesRegex(ValueError, "explicit boolean"):
            SERVER._deploy_environment({"createFoundry": "true"})

    def test_subscription_and_model_creation_inputs_are_forwarded_without_defaults(self):
        subscription = "11111111-2222-3333-4444-555555555555"
        settings = SERVER._deploy_environment({
            "azureSubscriptionId": subscription, "foundryModelName": "catalog-model",
            "foundryModelVersion": "available-version", "foundryModelSku": "AvailableSku",
            "foundryModelCapacity": 12, "createFoundry": False,
        })
        self.assertEqual(settings, {
            "HYDRO_AZURE_SUBSCRIPTION_ID": subscription, "HYDRO_FOUNDRY_MODEL_NAME": "catalog-model",
            "HYDRO_FOUNDRY_MODEL_VERSION": "available-version", "HYDRO_FOUNDRY_MODEL_SKU": "AvailableSku",
            "HYDRO_FOUNDRY_MODEL_CAPACITY": "12", "HYDRO_FOUNDRY_CREATE": "false",
        })

    def test_project_name_is_validated_and_forwarded_as_a_simple_existing_resource_choice(self):
        self.assertEqual(SERVER._deploy_environment({"foundryProjectName": "  my-project_1.0  "}), {
            "HYDRO_FOUNDRY_EXISTING_PROJECT_NAME": "my-project_1.0",
        })
        self.assertEqual(SERVER._deploy_environment({"foundryProjectName": " "}), {})
        for invalid in ("not a name", "https://account/project", "-invalid", "a" * 129, "project;run"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "Existing Foundry project name"):
                SERVER._deploy_environment({"foundryProjectName": invalid})

    def test_account_name_is_validated_and_forwarded_without_creation_approval(self):
        self.assertEqual(SERVER._deploy_environment({"foundryAccountName": "  hydro-account  "}), {
            "HYDRO_FOUNDRY_ACCOUNT_NAME": "hydro-account",
        })
        self.assertEqual(SERVER._deploy_environment({"foundryAccountName": " "}), {})
        for invalid in ("a", "-account", "account-", "has spaces", "has.dot", "a" * 65, True):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "Foundry account name"):
                SERVER._deploy_environment({"foundryAccountName": invalid})

    def test_project_resource_id_is_explicit_validated_and_forwarded_only_to_setup(self):
        project_id = (
            "/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/demo"
            "/providers/Microsoft.CognitiveServices/accounts/hydro/projects/demo"
        )
        self.assertEqual(SERVER._deploy_environment({"foundryProjectResourceId": project_id}), {
            "HYDRO_FOUNDRY_PROJECT_RESOURCE_ID": project_id,
        })
        for invalid in ("demo", project_id + "?other=true", project_id.replace("projects/demo", "accounts/demo")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "ARM resource ID"):
                SERVER._deploy_environment({"foundryProjectResourceId": invalid})
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/deploy-app", json={
                "tenant": "tenant.example", "workspace": "Demo", "foundryProjectResourceId": project_id,
            })
            self.assertEqual(response.status_code, 400)
            start.assert_not_called()

    def test_checkout_metadata_prefills_source_and_guide_is_locally_available(self):
        values = ["100", "abcd1234", "2026-10-25T00:00:00+00:00", "feat/example",
                  "https://github.com/owner/repository.git"]
        with patch.object(SERVER.shutil, "which", return_value="git"), \
                patch.object(SERVER.Path, "exists", return_value=True), \
                patch.object(SERVER.subprocess, "run", side_effect=[Mock(stdout=value) for value in values]):
            data = self.client.get("/api/version").get_json()
        self.assertEqual((data["branch"], data["repository"]), ("feat/example", "owner/repository"))
        guide = self.client.get("/deploy-guide")
        self.assertEqual(guide.status_code, 200)
        self.assertIn("text/plain", guide.content_type)
        guide.close()

    def test_operator_graph_binding_is_passed_only_as_explicit_json(self):
        binding = {
            key: f"{index:08x}-1111-4111-8111-{index:012x}"
            for index, key in enumerate(("workspaceId", "ontologyId", "graphModelId"), start=1)
        }
        settings = SERVER._deploy_environment({"ontologyGraphBinding": json.dumps(binding)})
        self.assertEqual(json.loads(settings["HYDRO_ONTOLOGY_GRAPH_BINDING"]), binding)
        for invalid in ("not-json", "[]", "{}", '{"workspaceId":"wrong"}'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "graph binding"):
                SERVER._deploy_environment({"ontologyGraphBinding": invalid})

    def test_deploy_environment_rejects_unsafe_or_malformed_values(self):
        bad = (
            {"foundryEndpoint": "http://res.services.ai.azure.com/api/projects/p"},
            {"foundryEndpoint": "https://evil.example/api/projects/p"},
            {"foundryEndpoint": "https://res.services.ai.azure.com/api/projects/p; calc"},
            {"foundryModel": "bad model"}, {"foundryModel": "x" * 80}, {"acrName": "UPPERCASE1"},
            {"acrName": "abc"}, {"foundryLocation": "sweden;central"}, {"productionWrites": "maybe"},
            {"azureSubscriptionId": "not-a-guid"}, {"foundryModelName": "bad model"},
            {"foundryModelVersion": "v;1"}, {"foundryModelSku": "SKU with spaces"},
            {"foundryModelCapacity": "0"}, {"foundryModelCapacity": "-1"},
            {"foundryModelCapacity": "1.5"}, {"foundryModelCapacity": True},
        )
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                SERVER._deploy_environment(payload)

    def test_deploy_app_passes_foundry_settings_to_the_job_environment(self):
        payload = {"tenant": "tenant.example", "workspace": "Demo Workspace", "createFoundry": True,
                   "foundryModel": "m1", "productionWrites": "false"}
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post("/api/setup-foundry", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(start.call_args.args[1], {
            "HYDRO_FOUNDRY_MODEL": "m1", "HYDRO_FOUNDRY_CREATE": "true", "HYDRO_PRODUCTION_WRITES_ENABLED": "false",
        })
        self.assertNotIn("m1", " ".join(start.call_args.args[0]))  # settings never ride on the command line
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            self.client.post("/api/setup-foundry", json={"tenant": "tenant.example", "workspace": "w"})
        self.assertIsNone(start.call_args.args[1])  # nothing set: the server's own environment still applies

    def test_two_name_creation_is_forwarded_to_standalone_and_full_workflow(self):
        settings = {"foundryAccountName": "my-foundry-account", "foundryNewProjectName": "my-new-project",
                    "foundryModel": "approved-chat", "createFoundry": True}
        expected = {
            "HYDRO_FOUNDRY_ACCOUNT_NAME": "my-foundry-account",
            "HYDRO_FOUNDRY_NEW_PROJECT_NAME": "my-new-project",
            "HYDRO_FOUNDRY_MODEL": "approved-chat", "HYDRO_FOUNDRY_CREATE": "true",
        }
        with patch.object(SERVER, "_start", return_value="job") as start:
            response = self.client.post("/api/setup-foundry", json={
                "tenant": "tenant.example", "workspace": "Demo", **settings,
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(start.call_args.args[1], expected)
        with patch.object(SERVER, "_start_workflow", return_value="job") as start:
            response = self.client.post("/api/full-workflow", json={
                "tenant": "tenant.example", "workspace": "Demo", "repository": "o/r",
                "connectionId": "connection", "parameters": PIPELINE_PARAMS, **settings,
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(start.call_args.args[0][2]["env"], expected)
        for invalid in ("bad name", "../project", "-invalid", "x" * 65):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "New Foundry project name"):
                SERVER._deploy_environment({**settings, "foundryNewProjectName": invalid})
        with self.assertRaisesRegex(ValueError, "resource-creation approval"):
            SERVER._deploy_environment({**settings, "createFoundry": False})
        for invalid in (123, False, [], {}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "must be text"):
                SERVER._deploy_environment({**settings, "foundryNewProjectName": invalid})

    def test_invalid_foundry_settings_stop_the_request_before_any_job_starts(self):
        with patch.object(SERVER, "_start") as start, patch.object(SERVER, "_start_workflow") as workflow:
            deploy = self.client.post("/api/deploy-app", json={
                "tenant": "tenant.example", "workspace": "w", "acrName": "BAD"})
            full = self.client.post("/api/full-workflow", json={
                "tenant": "tenant.example", "workspace": "w", "repository": "o/r", "connectionId": "yes",
                "parameters": PIPELINE_PARAMS,
                "productionWrites": "maybe"})
        self.assertEqual((deploy.status_code, full.status_code), (400, 400))
        self.assertIn("Container registry", deploy.get_json()["error"])
        self.assertIn("Work-order writes", full.get_json()["error"])
        start.assert_not_called()
        workflow.assert_not_called()

    def test_full_workflow_gives_foundry_settings_to_the_combined_step_only(self):
        payload = {
            "tenant": "tenant.example", "workspace": "w", "repository": "o/r", "connectionId": "yes", "pat": "x",
            "parameters": PIPELINE_PARAMS,
            "createFoundry": True, "foundryLocation": "swedencentral",
        }
        with patch.object(SERVER, "_start_workflow", return_value="id") as start:
            response = self.client.post("/api/full-workflow", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        self.assertEqual(steps[2]["env"], {"HYDRO_FOUNDRY_CREATE": "true", "HYDRO_FOUNDRY_LOCATION": "swedencentral"})
        for step in steps[:2]:
            self.assertNotIn("HYDRO_FOUNDRY_CREATE", step["env"] or {})

    def test_combined_invocation_forwards_entire_validated_deployment_environment(self):
        guid = "11111111-1111-1111-1111-111111111111"
        graph = {"workspaceId": guid, "ontologyId": guid, "graphModelId": guid}
        payload = {
            "tenant": "tenant.example", "workspace": "w", "repository": "o/r", "connectionId": "yes",
            "parameters": PIPELINE_PARAMS, "ontologyGraphBinding": json.dumps(graph),
            "azureSubscriptionId": guid,
            "foundryEndpoint": "https://demo.services.ai.azure.com/api/projects/project",
            "foundryProjectResourceId": f"/subscriptions/{guid}/resourceGroups/rg/providers/"
                                       "Microsoft.CognitiveServices/accounts/account/projects/project",
            "foundryAccountName": "account", "foundryProjectName": "project",
            "foundryNewProjectName": "new-project", "foundryModel": "deployment",
            "foundryModelName": "gpt-4.1", "foundryModelVersion": "2025-04-14",
            "foundryModelSku": "GlobalStandard", "foundryModelCapacity": 75,
            "acrName": "demoregistry", "foundryLocation": "swedencentral",
            "createFoundry": True, "productionWrites": "false",
        }
        expected = SERVER._deploy_environment(payload)
        self.assertEqual(len(expected), 16)
        with patch.object(SERVER, "_start_workflow", return_value="id") as start:
            response = self.client.post("/api/full-workflow", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        self.assertEqual(steps[2]["env"], expected)
        self.assertTrue(all(not key.startswith("HYDRO_")
                            for step in steps[:2] for key in step["env"]))
        self.assertNotIn("demoregistry", " ".join(steps[2]["argv"]))

    def test_combined_progress_treats_parallel_branches_as_one_interval(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        offset = len(SERVER.SYNC_PHASES) - 1 + len(SERVER.PIPELINE_PHASES) - 2
        job.advance_phase(offset)
        for line in ("[1/8] prerequisites", "[2/8] SPA", "[3/8] source preparation",
                     "Energy: setup", "Foundry: agents", "Energy map read models are ready",
                     "Foundry: Hosted Agent"):
            for index, markers in SERVER.COMBINED_MARKERS:
                if any(marker in line for marker in markers):
                    job.advance_phase(offset + index - 1)
        self.assertEqual(job.phases[job.phase_index], "Combined · Energy + Foundry concurrently")
        for index, markers in SERVER.COMBINED_MARKERS:
            if any(marker in "[4/8] signing in" for marker in markers):
                job.advance_phase(offset + index - 1)
        self.assertEqual(job.phases[job.phase_index], "Combined · App publication · Signing in")
        self.assertFalse(any(phase.startswith(("Energy ·", "Foundry ·"))
                             for phase in SERVER.WORKFLOW_PHASES))

    def test_both_deploy_forms_explain_reuse_and_confirm_resource_creation(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        renderer = page[page.index("function renderSpaFields("):page.index('$("workflowFoundryFields").innerHTML')]
        script = renderer + """
console.log(JSON.stringify([
  renderFoundryFields("deploy", "foundryClientId"),
  renderFoundryFields("workflow", "workflowDeployClientId"),
]));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        forms = json.loads(result.stdout)
        for prefix, form in zip(("deploy", "workflow"), forms):
            for suffix in ("AzureSubscriptionId", "FoundryProjectName", "FoundryModel", "FoundryCreate"):
                self.assertIn(f'id="{prefix}{suffix}"', form)
            self.assertIn(f'foundryPayload("{prefix}")', page)
            normal_fields = form[:form.index('<details')]
            self.assertEqual(normal_fields.count('type="text"'), 4)
            self.assertLess(form.index(f'id="{prefix}AzureSubscriptionId"'), form.index(f'id="{prefix}FoundryProjectName"'))
            self.assertIn("FoundryCreate", normal_fields)
            self.assertIn("Automatic multi-agent setup", form)
            self.assertIn("billable", normal_fields)
            self.assertNotIn("FoundryOverrides", form)
            self.assertNotIn("FoundryNewProjectOptions", form)
            self.assertNotIn("FoundryNewModel", form)
            self.assertNotIn("FoundryNewProjectName", form)
            for suffix in ("OntologyGraphBinding", "FoundryEndpoint", "AcrName", "FoundryProjectResourceId",
                           "FoundryLocation", "FoundryModelName", "FoundryModelVersion",
                           "FoundryModelSku", "FoundryModelCapacity"):
                self.assertNotIn(f'id="{prefix}{suffix}"', form)
            self.assertNotIn("Model deployment settings", form)
            self.assertIn("Global inference may apply", normal_fields)
            self.assertNotIn("Work-order permissions", form)
            self.assertNotIn("ProductionWrites", form)
            self.assertIn("<summary>Fabric App authentication</summary>", form)
            client_id = "foundryClientId" if prefix == "deploy" else "workflowDeployClientId"
            self.assertIn(f'id="{client_id}UseExisting" type="checkbox"', form)
            self.assertIn(f'<div id="{client_id}Fields" hidden>', form)
            self.assertIn("Use a pre-created app registration", form)
            self.assertNotIn("Keep this target's current setting", form)
            self.assertIn("Foundry account is the parent Azure resource", form)
            self.assertIn("FoundryAccountName", form)
            self.assertIn("Saved project or automatic discovery", form)
            self.assertNotIn("usage charges", form)
            self.assertIn("Allow project creation (billable)", form)
            self.assertIn(f'aria-describedby="{prefix}FoundryCreateHelp"', form)
            self.assertIn(f'id="{prefix}FoundryCreateHelp"', form)
            self.assertIn("Global inference may apply. Existing projects are reused.", form)
            self.assertIn("Stable workspace suffixes isolate agents, connections and hosted state", form)
            self.assertIn("no extra model deployment is allocated", form)
            self.assertNotIn("Leave blank unless", form)
            identifiers = re.findall(r'\bid="([^"]+)"', form)
            self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertIn("Allow project creation (billable)", page)
        self.assertNotIn("Allow creating a missing Foundry project", page)
        self.assertNotIn('placeholder="gpt-5.6-sol"', page)
        self.assertNotIn("Advanced / first-time", page)
        self.assertNotIn("Advanced: secret names", page)
        handler = page[page.index('$("deployBtn").addEventListener'):page.index('function workflowPayload(')]
        self.assertNotIn("workflow", handler)

    def test_ui_help_stays_brief_without_removing_warnings(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for paragraph in re.findall(r'<p class="hint">(.*?)</p>', page, re.DOTALL):
            self.assertLessEqual(len(re.sub(r"<[^>]*>", " ", paragraph).split()), 45, paragraph)
        for spec in SERVER.PIPELINE_PARAM_SPEC:
            self.assertLessEqual(len(spec["help"].split()), 12, spec["name"])
        self.assertIn("Allow project creation (billable)", page)
        self.assertIn("Deletes every workspace item.", page)
        self.assertIn("materialize the ontology graph first", page)
        self.assertIn("Settings stay on this machine. GitHub PATs are never saved.", page)

    def test_precreated_spa_is_explicit_validated_and_used_by_each_submission(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        helpers = page[page.index("const spaClientIds ="):page.index("function renderFoundryFields(")]
        script = """
const assert = require("node:assert/strict");
const fields = {};
const $ = id => fields[id];
""" + helpers + """
for (const clientId of spaClientIds) {
  for (const suffix of ["", "UseExisting", "Fields", "Status"]) fields[clientId+suffix] = {value:"",checked:false};
  updateSpaChoice(clientId);
  assert.equal(spaClientValue(clientId), "");
  assert.equal(spaClientValid(clientId), true);
  assert.equal($(clientId).disabled, true);
  assert.equal($(clientId+"Fields").hidden, true);
  $(clientId).value = "stale-value";
  assert.equal(spaClientValue(clientId), "");
  $(clientId+"UseExisting").checked = true;
  for (const invalid of ["", "not-a-guid", "12345678-1234-1234-1234-123456789abz"]) {
    $(clientId).value = invalid;
    updateSpaChoice(clientId);
    assert.equal(spaClientValid(clientId), false);
    assert.equal($(clientId).disabled, false);
    assert.equal($(clientId+"Fields").hidden, false);
    assert.match($(clientId+"Status").textContent, /Enter a valid/);
  }
  $(clientId).value = " 12345678-1234-1234-1234-123456789ABC ";
  updateSpaChoice(clientId);
  assert.equal(spaClientValid(clientId), true);
  assert.equal(spaClientValue(clientId), "12345678-1234-1234-1234-123456789ABC");
  $(clientId+"UseExisting").checked = false;
  updateSpaChoice(clientId);
  assert.equal(spaClientValue(clientId), "");
  assert.equal(spaClientValid(clientId), true);
}
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('renderSpaFields("deployClientId")', page)
        for client_id in ("deployClientId", "foundryClientId", "workflowDeployClientId"):
            self.assertIn(f'clientId: spaClientValue("{client_id}")', page)
            self.assertIn(f'spaClientValid("{client_id}")', page)
        self.assertNotIn("appOntologyGraphBinding", page)

    def test_foundry_creation_requires_only_valid_project_and_model_names(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = page.index("function foundryValid(")
        function = page[start:page.index('for (const prefix of ["deploy", "workflow"])', start)]
        script = """
const assert = require("node:assert/strict");
const fields = {};
const $ = id => fields[id];
for (const suffix of ["FoundryCreate", "FoundryAccountName", "FoundryProjectName", "FoundryModel",
                     "FoundryCreationStatus"]) fields["deploy"+suffix] = {value:"",checked:false};
""" + function + """
assert.equal(foundryValid("deploy"), true);
for (const invalid of ["a", "-account", "account-", "has.dot", "x".repeat(65)]) {
  fields.deployFoundryAccountName.value = invalid;
  assert.equal(foundryValid("deploy"), false);
}
fields.deployFoundryAccountName.value = "my-account";
assert.equal(foundryValid("deploy"), true);
fields.deployFoundryCreate.checked = true;
assert.equal(foundryValid("deploy"), false);
assert.match(fields.deployFoundryCreationStatus.textContent, /Enter a valid model deployment name/);
fields.deployFoundryModel.value="approved";
assert.equal(foundryValid("deploy"), true);
fields.deployFoundryProjectName.value="my-project";
assert.equal(foundryValid("deploy"), true);
for (const suffix of ["FoundryProjectName", "FoundryModel"]) {
  for (const invalid of ["-invalid", "has spaces", "x".repeat(65)]) {
    const previous = fields["deploy"+suffix].value;
    fields["deploy"+suffix].value=invalid;
    assert.equal(foundryValid("deploy"), false);
    fields["deploy"+suffix].value=previous;
  }
}
assert.equal(foundryValid("deploy"), true);
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_foundry_modes_keep_reuse_automatic_and_creation_explicit(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = page.index("function foundryPayload(")
        functions = page[start:page.index('for (const prefix of ["deploy", "workflow"])', start)]
        script = """
const assert = require("node:assert/strict");
const fields = {};
const $ = id => fields[id];
const suffixes = [
  "AzureSubscriptionId", "FoundryAccountName", "FoundryProjectName",
  "FoundryModel", "FoundryCreate",
  "FoundryModeSummary", "FoundryCreationStatus",
];
""" + functions + """
for (const prefix of ["deploy", "workflow"]) {
  for (const suffix of suffixes) {
    fields[prefix + suffix] = {value:"", checked:false, disabled:false, hidden:false, open:false, textContent:""};
  }
  updateFoundryMode(prefix);
  assert.equal(foundryPayload(prefix).createFoundry, false);
  assert.equal(foundryPayload(prefix).foundryModel, "");
  assert.equal(foundryValid(prefix), true);
  $(prefix+"FoundryAccountName").value = "my-account";
  assert.equal(foundryPayload(prefix).foundryAccountName, "my-account");
  $(prefix+"FoundryProjectName").value = "existing-project-name";
  $(prefix+"FoundryModel").value = "existing-model";
  assert.equal(foundryPayload(prefix).foundryProjectResourceId, "");
  const projectId = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a/projects/p";
  $(prefix+"FoundryProjectName").value = projectId;
  assert.equal(foundryPayload(prefix).foundryProjectResourceId, projectId);
  assert.equal(foundryPayload(prefix).foundryProjectName, "");
  $(prefix+"FoundryProjectName").value = "existing-project-name";
  assert.equal(foundryPayload(prefix).foundryModel, "existing-model");
  for (const key of ["foundryEndpoint", "acrName", "ontologyGraphBinding", "productionWrites"]) {
    assert.equal(Object.hasOwn(foundryPayload(prefix), key), false);
  }
  $(prefix+"FoundryCreate").checked = true;
  updateFoundryMode(prefix);
  assert.equal(foundryValid(prefix), true);
  assert.equal($(prefix+"FoundryProjectName").disabled, false);
  assert.equal($(prefix+"FoundryModel").disabled, false);
  assert.equal($(prefix+"FoundryProjectName").value, "existing-project-name");
  assert.equal($(prefix+"FoundryModel").value, "existing-model");
  assert.match($(prefix+"FoundryModeSummary").textContent, /Create only if missing/);
  assert.equal(foundryValid(prefix), true);
  const creating = foundryPayload(prefix);
  assert.equal(creating.createFoundry, true);
  assert.equal(creating.foundryModel, "existing-model");
  assert.equal(creating.foundryProjectResourceId, "");
  assert.equal(creating.foundryProjectName, "existing-project-name");
  assert.equal(Object.hasOwn(creating, "foundryNewProjectName"), false);
  for (const key of ["foundryModelVersion", "foundryModelSku", "foundryModelCapacity",
                     "foundryLocation", "foundryModelName"]) assert.equal(Object.hasOwn(creating, key), false);
  assert.equal(Object.hasOwn(creating, "productionWrites"), false);
  $(prefix+"FoundryCreate").checked = false;
  updateFoundryMode(prefix);
  const reused = foundryPayload(prefix);
  assert.equal(reused.createFoundry, false);
  assert.equal(reused.foundryModel, "existing-model");
  assert.equal(reused.foundryProjectResourceId, "");
  assert.equal(reused.foundryProjectName, "existing-project-name");
  assert.equal(Object.hasOwn(reused, "foundryNewProjectName"), false);
}
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_workflow_progress_groups_detailed_phases(self):
        html = (MODULE_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="workflow-phase-groups"', html)
        self.assertIn('title: "GitHub Sync"', html)
        self.assertIn('title: "Core Setup: Fabric, Weather and Energy"', html)
        self.assertNotIn('title: "Core Setup: Energy Sources"', html)
        self.assertIn('title: "Fabric App Deployment"', html)
        self.assertIn("complete ontology before either Fabric agent", html)
        self.assertIn("Requires both UKMet API keys", html)
        self.assertIn('["workflowPanel", "pipelinePanel"].includes(el.id)', html)
        self.assertIn('state === "active" || state === "failed"', html)

    def test_core_progress_combines_parallel_ingestion_and_map_publication(self):
        html = (MODULE_PATH.parent / "static" / "index.html").read_text(encoding="utf-8")
        script = """
const assert = require("node:assert/strict");
""" + html[html.index("const WORKFLOW_PHASE_GROUPS ="):html.index("// mm:ss,")] + """
const phases = ["Queued", "Pipeline · Resolving workspace", "Pipeline · Running",
  "Energy · Checking prerequisites", "Energy · Preparing energy sources",
  "Energy · Verifying read models", "Foundry · Checking prerequisites", "Done"];
const render = (phaseIndex, status = "running", failedComponent) =>
  renderWorkflowPhaseGroups({}, {phases, phaseIndex, status, failedComponent});
for (const index of [1, 2, 3, 4, 5]) {
  const rendered = render(index);
  assert.equal((rendered.match(/data-group="pipeline"/g) || []).length, 1);
  assert.ok(rendered.includes('data-group="pipeline" data-state="active"'));
  assert.ok(!rendered.includes('data-group="energy"'));
  assert.ok(!rendered.includes("Preparing energy sources"));
}
assert.ok(render(2).includes("Building the complete ontology before Fabric agents; energy runs in parallel"));
assert.ok(render(4).includes("Publishing map read models"));
assert.ok(render(6).includes('data-group="pipeline" data-state="done"'));
for (const [index, component] of [[2, "Setup pipeline"], [4, "Energy setup"]]) {
  assert.ok(render(index, "failed", component).includes('data-group="pipeline" data-state="failed"'));
}
assert.ok(render(7, "succeeded").includes('data-group="pipeline" data-state="done"'));
const coreOnly = renderWorkflowPhaseGroups({}, {phases: phases.slice(0, 6),
  phaseIndex: 4, status: "running"});
assert.equal((coreOnly.match(/data-group=/g) || []).length, 1);
assert.ok(coreOnly.includes("Publishing map read models"));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_workflow_builds_three_commands_without_duplicate_core_or_providers(self):
        payload = {
            "tenant": "tenant.example",
            "workspace": "Demo Workspace",
            "repository": "owner/repository",
            "branch": "feature/demo",
            "directory": "/",
            "connectionId": "yes",
            "pat": "test-pat",
            "parameters": {
                **PIPELINE_PARAMS,
                "key_vault_uri": "https://vault.vault.azure.net/",
                "alert_email_to": "operations@example.test",
                "per_notebook_timeout_secs": "3600",
            },
            "clientId": "11111111-1111-1111-1111-111111111111",
        }
        with patch.object(SERVER, "_start_workflow", return_value="workflow-id") as start:
            response = self.client.post("/api/full-workflow", json=payload)

        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        self.assertEqual([step["name"] for step in steps], [
            "GitHub sync", "Setup pipeline", SERVER.COMBINED_STEP,
        ])
        self.assertEqual(Path(steps[0]["argv"][0]).name, "sync_workspace_from_git.py")
        self.assertEqual(Path(steps[1]["argv"][0]).name, "run_pipeline.py")
        self.assertEqual(Path(steps[2]["argv"][0]).name, "deploy_fabric_app.py")
        self.assertNotIn("test-pat", " ".join(steps[0]["argv"]))
        self.assertEqual(steps[0]["env"]["FABRIC_GIT_PAT"], "test-pat")
        self.assertEqual(
            json.loads(steps[1]["env"]["FABRIC_PIPELINE_PARAMS"])["workspace_id"],
            "Demo Workspace",
        )
        self.assertEqual(steps[2]["argv"][-2:], [
            "--client-id", "11111111-1111-1111-1111-111111111111",
        ])
        self.assertEqual(steps[2]["argv"].count("--energy-read-models-only"), 1)
        self.assertEqual(steps[2]["argv"][steps[2]["argv"].index("--stage") + 1], "all")
        self.assertEqual(steps[2]["argv"][steps[2]["argv"].index("--energy-map") + 1], "enabled")
        self.assertIn("--require-parallel-energy", steps[1]["argv"])
        self.assertEqual(steps[2]["timeout"], max(SERVER.ENERGY_TIMEOUT_S, SERVER.FOUNDRY_TIMEOUT_S)
                         + SERVER.DEPLOY_TIMEOUT_S)
        self.assertEqual([step["phase_offset"] for step in steps], [1, 6, 10])
        self.assertEqual(len(SERVER.WORKFLOW_PHASES), 19)
        self.assertEqual(steps[2]["markers"], SERVER.DEPLOY_MARKERS)

    def test_full_workflow_stops_at_failed_component_and_reports_it(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        steps = [
            {"name": name, "argv": [name], "env": None, "timeout": 1,
             "markers": [], "phase_offset": index + 1}
            for index, name in enumerate(("GitHub sync", "Setup pipeline", "Fabric app deploy"))
        ]
        with patch.object(SERVER, "_run_workflow_step", side_effect=[0, 23]) as run:
            SERVER._workflow_worker(job, steps)

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.returncode, 23)
        self.assertEqual(job.failed_component, "Setup pipeline")
        self.assertEqual(run.call_count, 2)
        self.assertIn("Setup pipeline failed with exit code 23", "\n".join(job.lines))
        self.assertNotIn("Fabric app deploy completed", "\n".join(job.lines))

    def test_full_workflow_success_reports_all_components(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        steps = [
            {"name": name, "argv": [name], "env": None, "timeout": 1,
             "markers": [], "phase_offset": index + 1}
            for index, name in enumerate(("GitHub sync", "Setup pipeline", "Fabric app deploy"))
        ]
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(job, steps)

        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.returncode, 0)
        self.assertEqual(job.phase_index, len(SERVER.WORKFLOW_PHASES) - 1)
        self.assertEqual(run.call_count, 3)
        log = "\n".join(job.lines)
        for name in ("GitHub sync", "Setup pipeline", "Fabric app deploy"):
            self.assertIn(f"{name} completed successfully", log)

    def test_real_workflow_step_returns_after_child_exits(self):
        job = SERVER.Job(["Queued", "Running", "Done"], exclusive=True)
        returncode = SERVER._run_workflow_step(
            job,
            "Test component",
            ["-c", "print('child completed')"],
            None,
            5,
            [],
            1,
        )

        self.assertEqual(returncode, 0)
        self.assertIsNone(job.process)
        self.assertIn("[Test component] child completed", job.lines)
        self.assertFalse(any("timed out" in line for line in job.lines))

    def test_full_workflow_monitor_exception_fails_instead_of_staying_running(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        steps = [{
            "name": "GitHub sync",
            "argv": ["sync"],
            "env": None,
            "timeout": 1,
            "markers": [],
            "phase_offset": 1,
        }]
        with patch.object(
            SERVER,
            "_run_workflow_step",
            side_effect=NameError("monitor state is unavailable"),
        ):
            SERVER._workflow_worker(job, steps)

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.returncode, -1)
        self.assertEqual(job.failed_component, "GitHub sync")
        self.assertIn(
            "GitHub sync monitor failed: NameError: monitor state is unavailable",
            "\n".join(job.lines),
        )

    def test_job_api_exposes_failed_workflow_component(self):
        job = SERVER.Job(SERVER.WORKFLOW_PHASES, exclusive=True)
        job.status = "failed"
        job.returncode = 9
        job.component = "Setup pipeline"
        job.failed_component = "Setup pipeline"
        SERVER.JOBS[job.id] = job
        try:
            response = self.client.get(f"/api/jobs/{job.id}")
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertEqual(body["component"], "Setup pipeline")
            self.assertEqual(body["failedComponent"], "Setup pipeline")
            self.assertTrue(body["done"])
        finally:
            SERVER.JOBS.clear()

    def test_version_git_commands_never_open_windows_consoles(self):
        completed = Mock(stdout="1\n")
        with (
            patch.object(SERVER.shutil, "which", return_value="git.exe"),
            patch.object(SERVER.subprocess, "run", return_value=completed) as run,
            patch.object(SERVER.Path, "exists", return_value=True),
        ):
            response = self.client.get("/api/version")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(run.call_count, 5)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["creationflags"], SERVER.NO_WINDOW)

    def test_full_workflow_validates_every_step_before_starting(self):
        base = {
            "tenant": "tenant.example",
            "workspace": "Demo Workspace",
            "repository": "owner/repository",
            "connectionId": "yes",
            "parameters": PIPELINE_PARAMS,
        }
        cases = (
            ({**base, "repository": ""}, "repository"),
            ({**base, "parameters": {**base["parameters"], "alert_email_to": ""}}, "Alert email"),
            ({**base, "clientId": "not-a-guid"}, "SPA client id"),
        )
        for payload, message in cases:
            with self.subTest(message=message), patch.object(SERVER, "_start_workflow") as start:
                response = self.client.post("/api/full-workflow", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.get_json()["error"])
                start.assert_not_called()

    def test_full_workflow_page_explains_serial_failure_boundary(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="workflowBtn"', page)
        self.assertIn('"/api/full-workflow"', page)
        self.assertIn("finishes Energy and Foundry concurrently before app publication; stops on failure.", page)
        self.assertLess(page.index('id="tabWorkflow"'), page.index('id="deploymentActions"'))
        self.assertLess(page.index('id="tabWorkflow"'), page.index('id="tabDelete"'))

    def test_full_workflow_page_is_self_contained_except_for_target(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        workflow_pane = page[
            page.index('<section id="paneWorkflow"'):
            page.index('<section id="paneSync"')
        ]
        for field_id in (
            "workflowRepository",
            "workflowBranch",
            "workflowDirectory",
            "workflowReuseConn",
            "workflowKeepConnected",
            "workflowPat",
            "workflowTestBtn",
            "workflowPipelineFields",
            "workflowFoundryFields",
        ):
            self.assertIn(f'id="{field_id}"', workflow_pane)
        self.assertIn('id="tenant"', page)
        self.assertIn('id="workspace"', page)
        self.assertNotIn("Configure all required fields in the other tabs", workflow_pane)
        self.assertIn('renderPipelineFields("wf_pp_")', page)
        self.assertIn('renderFoundryFields("workflow", "workflowDeployClientId")', page)

    def test_full_workflow_submission_uses_only_its_own_fields(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        handler = page[
            page.index('function workflowPayload('):
            page.index('$("restartBtn").addEventListener')
        ]
        for field_id in (
            "workflowRepository",
            "workflowBranch",
            "workflowDirectory",
            "workflowKeepConnected",
            "workflowPat",
        ):
            self.assertIn(f'$("{field_id}")', handler)
        self.assertIn('workflowConnValue()', handler)
        self.assertIn('collectPipelineParams("wf_pp_")', handler)
        self.assertIn('spaClientValue("workflowDeployClientId")', handler)
        self.assertNotIn('$("repository")', handler)
        self.assertNotIn('$("deployClientId")', handler)

    def test_deploy_page_explains_portable_backend_readiness_contract(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("verifies authentication, CORS and connectors", page)
        self.assertIn('href="/deploy-guide"', page)
        guide = (SERVER.REPO_ROOT / "HydroOperationsApp" / "DEPLOY.md").read_text(encoding="utf-8")
        self.assertIn("preflights and POST", guide)
        self.assertIn("/graphql", guide)
        self.assertIn("/api/auth/v1/token", guide)

    def test_cancel_all_jobs_terminates_only_running_jobs(self):
        running = SERVER.Job(["Queued", "Done"])
        finished = SERVER.Job(["Queued", "Done"])
        running.process = object()
        finished.status = "succeeded"
        SERVER.JOBS.update({running.id: running, finished.id: finished})
        try:
            with patch.object(SERVER, "_terminate_owned_process") as terminate:
                response = self.client.post("/api/jobs/cancel-all", json={})

            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["cancelled"], 1)
            self.assertTrue(running.cancel_requested)
            self.assertFalse(finished.cancel_requested)
            terminate.assert_called_once_with(running.process)
        finally:
            SERVER.JOBS.clear()

    def test_cancel_all_jobs_handles_job_before_process_start(self):
        running = SERVER.Job(["Queued", "Done"])
        SERVER.JOBS[running.id] = running
        try:
            with patch.object(SERVER, "_terminate_owned_process") as terminate:
                response = self.client.post("/api/jobs/cancel-all", json={})

            self.assertEqual(response.get_json()["cancelled"], 1)
            self.assertTrue(running.cancel_requested)
            terminate.assert_not_called()
        finally:
            SERVER.JOBS.clear()


class PreparedEnergyPublicationTests(unittest.TestCase):
    def test_app_publication_uses_prepared_energy_setting_without_another_selector(self):
        target = {"tenant": "tenant.example", "workspace": "Demo"}
        client = SERVER.app.test_client()
        with patch.object(SERVER, "_start", return_value="job") as start:
            response = client.post("/api/deploy-app", json=target)
        self.assertEqual(response.status_code, 200, response.get_json())
        argv = start.call_args.args[0]
        self.assertEqual(argv[argv.index("--energy-map") + 1], "keep")
        for value in ("enabled", "disabled"):
            with self.subTest(value=value), patch.object(SERVER, "_start") as start:
                response = client.post("/api/deploy-app", json={**target, "energyMap": value})
                self.assertEqual(response.status_code, 400)
                self.assertIn("preserves the prepared energy-map setting", response.get_json()["error"])
                start.assert_not_called()
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("appEnergyMap", page)
        self.assertNotIn("Energy-map navigation", page)

    def test_full_workflow_combined_engine_enables_energy(self):
        target = {
            "tenant": "tenant.example", "workspace": "Demo", "repository": "owner/repo",
            "connectionId": "yes", "parameters": PIPELINE_PARAMS, "energyMap": "enabled",
        }
        with patch.object(SERVER, "_start_workflow", return_value="job") as start:
            response = SERVER.app.test_client().post("/api/full-workflow", json=target)
        self.assertEqual(response.status_code, 200, response.get_json())
        steps = start.call_args.args[0]
        energy = next(step for step in steps if step["name"] == SERVER.COMBINED_STEP)["argv"]
        self.assertEqual(energy[energy.index("--energy-map") + 1], "enabled")
        self.assertIn("--energy-read-models-only", energy)


if __name__ == "__main__":
    unittest.main()
