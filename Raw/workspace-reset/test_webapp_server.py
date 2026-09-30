import importlib.util
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import run_pipeline


MODULE_PATH = Path(__file__).with_name("webapp") / "server.py"
SPEC = importlib.util.spec_from_file_location("fabric_demo_server", MODULE_PATH)
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class WorkspaceActionTests(unittest.TestCase):
    def setUp(self):
        self.client = SERVER.app.test_client()

    def assert_exclusive_action(self, endpoint: str, payload: dict):
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post(endpoint, json=payload)

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(start.call_args.kwargs.get("exclusive"))

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
                    "parameters": {"key_vault_uri": "https://vault.vault.azure.net/"},
                },
            ),
            ("/api/deploy-app", target),
        ]
        for endpoint, payload in actions:
            with self.subTest(endpoint=endpoint):
                self.assert_exclusive_action(endpoint, payload)

    def test_pipeline_forwards_ontology_capability_modes(self):
        for mode in ("enabled", "auto", "disabled"):
            with self.subTest(mode=mode):
                parameters = {
                    "key_vault_uri": "https://vault.vault.azure.net/",
                    "ontology_data_agent_mode": mode,
                    "ontology_operations_agent_mode": mode,
                }
                with patch.object(SERVER, "_start", return_value="job-id") as start:
                    response = self.client.post("/api/run-pipeline", json={
                        "tenant": "tenant.example", "workspace": "DEV", "parameters": parameters,
                    })
                self.assertEqual(response.status_code, 200, response.get_json())
                forwarded = json.loads(start.call_args.args[1]["FABRIC_PIPELINE_PARAMS"])
                for name in ("ontology_data_agent_mode", "ontology_operations_agent_mode"):
                    self.assertEqual(forwarded[name], mode)

    def test_launcher_defaults_attempt_both_ontology_agents(self):
        response = self.client.get("/api/pipeline-params")
        self.assertEqual(response.status_code, 200)
        specs = {spec["name"]: spec for spec in response.get_json()["parameters"]}
        for name in ("ontology_data_agent_mode", "ontology_operations_agent_mode"):
            self.assertEqual(specs[name]["default"], "enabled")
            self.assertIn("auto: alias for enabled", specs[name]["help"])
            self.assertIn("disabled: skip", specs[name]["help"])
        self.assertIn("runtime smoke check", specs["ontology_data_agent_mode"]["help"])

    def test_pipeline_api_worker_and_poll_preserve_required_agent_failure_and_success(self):
        for state, expected_status, expected_code in (("Completed", "succeeded", 0), ("Failed", "failed", 1)):
            with self.subTest(state=state):
                fabric = Mock()
                fabric.resolve_workspace_id.return_value = ("canonical-workspace", "DEV")
                fabric.find_pipeline.return_value = ("pipeline-id", "01_Pipe_Setup")
                fabric.start_pipeline.return_value = "https://mock.invalid/job"
                reason = "Required Data Agent ontology runtime smoke failed: unsupported Ontology API"
                fabric.poll_job.return_value = {"status": state, "failureReason": {"message": reason}}

                def child_process(command, **kwargs):
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

                def start_job(argv, env_extra, timeout, phases, markers, **kwargs):
                    job = SERVER.Job(phases)
                    SERVER.JOBS[job.id] = job
                    with patch.object(SERVER.subprocess, "Popen", side_effect=child_process), patch.object(SERVER.threading, "Timer"):
                        SERVER._worker(job, argv, env_extra, timeout, markers)
                    return job.id

                try:
                    with patch.object(SERVER, "_start", side_effect=start_job):
                        response = self.client.post("/api/run-pipeline", json={
                            "tenant": "tenant.example", "workspace": "DEV",
                            "parameters": {"key_vault_uri": "https://mock.vault.azure.net/",
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
                    self.assertEqual(parameters["ontology_data_agent_mode"], "enabled")
                    log = "\n".join(job["lines"])
                    if state == "Failed":
                        self.assertIn(reason, log)
                        self.assertNotIn("run COMPLETED", log)
                    else:
                        self.assertIn("run COMPLETED", log)
                finally:
                    SERVER.JOBS.clear()

    def test_pipeline_rejects_invalid_ontology_capability_mode(self):
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/run-pipeline", json={
                "tenant": "tenant.example", "workspace": "DEV",
                "parameters": {
                    "key_vault_uri": "https://vault.vault.azure.net/",
                    "ontology_data_agent_mode": "force",
                },
            })
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()

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

    def test_deploy_page_explains_portable_backend_readiness_contract(self):
        page = (SERVER.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("Workspace or capacity changes are detected automatically", page)
        self.assertIn("CORS preflights and POST probes", page)
        self.assertIn("/graphql", page)
        self.assertIn("/api/auth/v1/token", page)

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


if __name__ == "__main__":
    unittest.main()
