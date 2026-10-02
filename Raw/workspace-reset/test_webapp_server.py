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
        starter = "_start_workflow" if endpoint == "/api/full-workflow" else "_start"
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
                    "parameters": {
                        "key_vault_uri": "https://vault.vault.azure.net/",
                        "alert_email_to": "operations@example.test",
                    },
                },
            ),
            ("/api/deploy-app", target),
            (
                "/api/full-workflow",
                {
                    **target,
                    "repository": "owner/repository",
                    "connectionId": "yes",
                    "parameters": {
                        "key_vault_uri": "https://vault.vault.azure.net/",
                        "alert_email_to": "operations@example.test",
                    },
                },
            ),
        ]
        for endpoint, payload in actions:
            with self.subTest(endpoint=endpoint):
                self.assert_exclusive_action(endpoint, payload)

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
        self.assertEqual(
            specs["ops_agent_teams_team_id"]["default"],
            "c480320e-9204-474b-9b2c-54a53e94f220",
        )
        self.assertEqual(
            specs["ops_agent_teams_channel_id"]["default"],
            "19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2",
        )
        self.assertEqual(
            specs["ops_agent_run_as_user"]["default"],
            "admin@mngenvmcap218279.onmicrosoft.com",
        )

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
            '<input id="pp_${p.name}" type="text"',
            html,
        )
        self.assertIn('${p.name === "alert_email_to" ? "required" : ""}', html)
        self.assertNotIn('type="${p.name === "alert_email_to" ? "email" : "text"}"', html)

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
                    self.assertNotIn("ontology_data_agent_mode", parameters)
                    self.assertNotIn("ontology_operations_agent_mode", parameters)
                    log = "\n".join(job["lines"])
                    if state == "Failed":
                        self.assertIn(reason, log)
                        self.assertNotIn("run COMPLETED", log)
                    else:
                        self.assertIn("run COMPLETED", log)
                finally:
                    SERVER.JOBS.clear()

    def test_pipeline_does_not_forward_hidden_agent_mode_flags(self):
        with patch.object(SERVER, "_start", return_value="job-id") as start:
            response = self.client.post("/api/run-pipeline", json={
                "tenant": "tenant.example", "workspace": "DEV",
                "parameters": {
                    "key_vault_uri": "https://vault.vault.azure.net/",
                    "alert_email_to": "operations@example.test",
                    "ontology_data_agent_mode": "force",
                    "ontology_operations_agent_mode": "disabled",
                },
            })
        self.assertEqual(response.status_code, 200)
        forwarded = json.loads(start.call_args.args[1]["FABRIC_PIPELINE_PARAMS"])
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

    def test_full_workflow_builds_existing_commands_in_serial_order(self):
        payload = {
            "tenant": "tenant.example",
            "workspace": "Demo Workspace",
            "repository": "owner/repository",
            "branch": "feature/demo",
            "directory": "/",
            "connectionId": "yes",
            "pat": "test-pat",
            "parameters": {
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
            "GitHub sync", "Setup pipeline", "Fabric app deploy",
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

    def test_full_workflow_validates_every_step_before_starting(self):
        base = {
            "tenant": "tenant.example",
            "workspace": "Demo Workspace",
            "repository": "owner/repository",
            "connectionId": "yes",
            "parameters": {
                "key_vault_uri": "https://vault.vault.azure.net/",
                "alert_email_to": "operations@example.test",
            },
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
        self.assertIn("Each step starts only after the previous step succeeds", page)
        self.assertIn("later changes", page)
        self.assertLess(page.index('id="tabDeploy"'), page.index('id="tabWorkflow"'))
        self.assertLess(page.index('id="tabWorkflow"'), page.index('id="tabDelete"'))

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
