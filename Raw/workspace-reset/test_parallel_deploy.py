import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import deploy_fabric_app as DEPLOY
import deployment_locks as LOCKS
import parallel_deploy as PARALLEL
import provision_foundry_agents as FOUNDRY
import sync_workspace_from_git as SYNC
from test_webapp_server import SERVER, PIPELINE_PARAMS


TENANT = "10000000-0000-0000-0000-000000000001"
WORKSPACES = [f"20000000-0000-0000-0000-00000000000{index}" for index in (1, 2, 3)]


class ParallelValidationTests(unittest.TestCase):
    def payload(self, **changes):
        return {
            "action": "/api/setup-energy", "workspaces": ["One", "", "  "],
            "data": {"tenant": TENANT}, **changes,
        }

    def test_blanks_are_ignored_and_one_two_three_targets_are_supported(self):
        for targets, expected in (
            (["One", "", "  "], ["One"]), (["", " Two "], ["Two"]),
            (["One", "Two"], ["One", "Two"]), (["One", "Two", "Three"], ["One", "Two", "Three"]),
        ):
            with self.subTest(targets=targets):
                self.assertEqual(PARALLEL.validate_request(self.payload(workspaces=targets))[1], expected)

    def test_invalid_targets_and_unknown_actions_are_rejected(self):
        for change in (
            {"workspaces": []}, {"workspaces": ["", " "]}, {"workspaces": ["a", "A"]},
            {"workspaces": ["a", "b", "c", "d"]}, {"workspaces": "a"},
            {"workspaces": ["a", None]}, {"action": "/api/unknown"}, {"data": []},
            {"data": {"tenant": TENANT, "resumeJobId": "wrong-global-checkpoint"}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                PARALLEL.validate_request(self.payload(**change))

    def test_parallel_deletion_requires_explicit_mode_and_every_target_confirmation(self):
        for data in (
            {"tenant": TENANT},
            {"tenant": TENANT, "dryRun": "true"},
            {"tenant": TENANT, "dryRun": False},
            {"tenant": TENANT, "dryRun": False, "confirmedWorkspaces": ["One"]},
            {"tenant": TENANT, "dryRun": False, "confirmedWorkspaces": ["Two", "One"]},
            {"tenant": TENANT, "dryRun": False, "confirmedWorkspaces": ["One", 2]},
            {"tenant": TENANT, "dryRun": False, "confirmedWorkspaces": ["One", "Other"]},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                PARALLEL.validate_request(self.payload(action="/api/delete", workspaces=["One", "", "Two"], data=data))
        for dry_run in (True, False):
            payload = self.payload(action="/api/delete", workspaces=["One", "", "Two"], data={
                "tenant": TENANT, "dryRun": dry_run,
                "confirmedWorkspaces": [] if dry_run else [" ONE ", "two"],
            })
            self.assertEqual(PARALLEL.validate_request(payload)[1], ["One", "Two"])

    def test_multi_target_foundry_accepts_a_shared_project_without_changing_it(self):
        for action in ("/api/setup-foundry", "/api/full-workflow"):
            for key in ("foundryProjectName", "foundryProjectResourceId", "foundryEndpoint", "foundryNewProjectName"):
                with self.subTest(action=action, key=key):
                    _, _, data = PARALLEL.validate_request(self.payload(
                        action=action, workspaces=["a", "b"], data={"tenant": TENANT, key: "shared-project"},
                    ))
                    for workspace in WORKSPACES:
                        self.assertEqual(PARALLEL.target_settings(data, workspace)[key], "shared-project")

    def test_separate_project_names_are_stable_short_bounded_and_do_not_approve_creation(self):
        names = []
        for workspace in WORKSPACES:
            data = {"tenant": TENANT, "separateFoundryProjects": True,
                    "foundryProjectName": "shared-base", "createFoundry": False}
            result = PARALLEL.target_settings(data, workspace)
            name = result["foundryProjectName"]
            self.assertRegex(name, r"^shared-base-[0-9a-f]{8}$")
            self.assertEqual(result, PARALLEL.target_settings(data, workspace))
            self.assertFalse(result["createFoundry"])
            self.assertEqual(result["foundryNewProjectName"], "")
            self.assertNotIn("separateFoundryProjects", result)
            names.append(name)
            creating = PARALLEL.target_settings({**data, "createFoundry": True}, workspace)
            self.assertEqual(creating["foundryNewProjectName"], name)
        self.assertEqual(len(set(names)), 3)
        result = PARALLEL.target_settings({
            "separateFoundryProjects": True, "foundryProjectName": "a" * 128,
        }, WORKSPACES[0])
        self.assertEqual(len(result["foundryProjectName"]), 64)
        for settings in ({"separateFoundryProjects": "true"},
                         {"separateFoundryProjects": True, "foundryProjectResourceId": "/project"},
                         {"separateFoundryProjects": True, "foundryProjectName": "has spaces"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                PARALLEL.validate_request(self.payload(data={"tenant": TENANT, **settings}))

    def test_name_and_id_aliases_stop_the_entire_batch_before_any_worker(self):
        fabric = Mock()
        fabric.resolve_workspace_id.return_value = (WORKSPACES[0], "One")
        with patch.object(SYNC, "Fabric", return_value=fabric), patch.object(PARALLEL, "run_target") as run:
            with self.assertRaisesRegex(ValueError, "same Fabric workspace"):
                PARALLEL.run_batch(self.payload(workspaces=["One", WORKSPACES[0]]))
            run.assert_not_called()


class ParallelApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for guard in (
            patch.object(SERVER, "PIPELINE_DEFAULTS_FILE", self.root / "settings.json"),
            patch.dict(SERVER.JOBS, {}, clear=True),
        ):
            guard.start()
            self.addCleanup(guard.stop)
        self.client = SERVER.app.test_client()

    def test_start_uses_existing_engine_and_keeps_pat_off_the_command_line(self):
        payload = {
            "action": "/api/sync", "workspaces": ["", "Second", "Third"],
            "data": {"tenant": TENANT, "repository": "owner/repo", "pat": "private-pat"},
        }
        with patch.object(SERVER, "_start", return_value="batch") as start:
            response = self.client.post("/api/parallel", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        argv, environment = start.call_args.args[:2]
        self.assertEqual(Path(argv[0]).name, "parallel_deploy.py")
        self.assertNotIn("private-pat", " ".join(argv))
        self.assertEqual(json.loads(environment[PARALLEL.PAYLOAD_ENV]), payload)
        self.assertEqual(start.call_args.kwargs["parallel_action"], "/api/sync")

    def test_bad_pipeline_inputs_are_rejected_before_start(self):
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/parallel", json={
                "action": "/api/run-pipeline", "workspaces": ["One", "Two"],
                "data": {"tenant": TENANT, "parameters": {}},
            })
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()

    def test_partial_delete_confirmation_starts_no_workers(self):
        with patch.object(SERVER, "_start") as start:
            response = self.client.post("/api/parallel", json={
                "action": "/api/delete", "workspaces": ["One", "Two", "Three"],
                "data": {"tenant": TENANT, "dryRun": False, "confirmedWorkspaces": ["One", "Two"]},
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("every selected workspace", response.get_json()["error"])
        start.assert_not_called()

    def test_all_modules_are_admitted_in_parallel_with_blank_slots_ignored(self):
        for action in sorted(PARALLEL.ACTIONS):
            with self.subTest(action=action), patch.object(SERVER, "_start", return_value="batch") as start:
                response = self.client.post("/api/parallel", json={
                    "action": action, "workspaces": ["", "Two", "Three"],
                    "data": {
                        "tenant": TENANT, "repository": "owner/repo", "connectionId": "yes",
                        "parameters": PIPELINE_PARAMS, "dryRun": True,
                    },
                })
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(start.call_args.kwargs["parallel_action"], action)

    def test_parallel_jobs_remain_exclusive_against_unrelated_requests(self):
        with patch.object(SERVER.threading, "Thread"):
            first = self.client.post("/api/parallel", json={
                "action": "/api/setup-energy", "workspaces": ["One", "Two"], "data": {"tenant": TENANT},
            })
            second = self.client.post("/api/setup-energy", json={"tenant": TENANT, "workspace": "Other"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        saved = SERVER.PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8")
        self.assertIn("parallelCheckpoints", saved)
        self.assertNotIn(PARALLEL.PAYLOAD_ENV, saved)

    def test_independent_status_logs_and_restart_checkpoint(self):
        job = SERVER.Job(["Resolve", "Deploy", "Done"])
        job.parallel_action = "/api/setup-energy"
        SERVER.JOBS[job.id] = job
        events = [
            {"workspace": WORKSPACES[0], "name": "One", "tenant": TENANT, "status": "succeeded",
             "done": True, "lines": ["one-log"], "jobId": "child-one"},
            {"workspace": WORKSPACES[1], "name": "Two", "tenant": TENANT, "status": "failed",
             "done": True, "lines": ["two-log"], "jobId": "child-two"},
        ]
        process = Mock(stdout=io.StringIO("\n".join(PARALLEL.EVENT_PREFIX + json.dumps(event) for event in events)),
                       returncode=1)
        with patch.object(SERVER.subprocess, "Popen", return_value=process), patch.object(SERVER.threading, "Timer"):
            SERVER._worker(job, ["fake.py"], None, 10, [])
        state = self.client.get(f"/api/jobs/{job.id}").get_json()
        self.assertEqual(state["status"], "failed")
        self.assertEqual([item["status"] for item in state["workspaceRuns"]], ["succeeded", "failed"])
        self.assertEqual([item["lines"] for item in state["workspaceRuns"]], [["one-log"], ["two-log"]])
        incremental = self.client.get(f"/api/jobs/{job.id}", query_string={
            "workspaceSince": json.dumps({WORKSPACES[0]: 1, WORKSPACES[1]: 0}),
        }).get_json()
        self.assertEqual([item["lines"] for item in incremental["workspaceRuns"]], [[], ["two-log"]])
        self.assertNotIn("one-log", SERVER.PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))
        SERVER.JOBS.clear()
        restored = self.client.get(f"/api/jobs/{job.id}").get_json()
        self.assertEqual(restored["status"], "failed")
        self.assertEqual([item["status"] for item in restored["workspaceRuns"]], ["succeeded", "failed"])

    def test_cancel_marks_only_the_selected_workspace(self):
        job = SERVER.Job(["Resolve", "Deploy", "Done"])
        job.parallel_action = "/api/setup-energy"
        job.workspace_runs = {workspace: {"workspace": workspace, "tenant": TENANT} for workspace in WORKSPACES}
        SERVER.JOBS[job.id] = job
        for workspace in WORKSPACES:
            (self.root / ".fabric-demo" / "parallel-deployments" / TENANT / workspace).mkdir(parents=True)
        with patch.object(Path, "home", return_value=self.root):
            response = self.client.post(f"/api/parallel/{job.id}/cancel-workspace", json={"workspace": WORKSPACES[1]})
        self.assertEqual(response.status_code, 200)
        flags = list(self.root.rglob("cancel"))
        self.assertEqual([path.parent.name for path in flags], [WORKSPACES[1]])

    def test_execution_mapping_never_changes_logical_workflow_arguments(self):
        checkout = self.root / "isolated"
        directory = checkout / "Raw" / "workspace-reset"
        directory.mkdir(parents=True)
        (directory / "deploy_fabric_app.py").touch()
        logical = [str(SERVER.DEPLOY_SCRIPT), "--workspace", WORKSPACES[0]]
        with patch.dict(os.environ, {"FABRIC_PARALLEL_CHECKOUT": str(checkout)}):
            mapped, cwd = SERVER._execution_context(logical)
        self.assertEqual(logical[0], str(SERVER.DEPLOY_SCRIPT))
        self.assertEqual(mapped, [str(directory / "deploy_fabric_app.py"), "--workspace", WORKSPACES[0]])
        self.assertEqual(cwd, directory)

    def test_full_deploy_resumes_across_isolated_checkouts_without_repeating_core(self):
        payload = {
            "tenant": TENANT, "workspace": WORKSPACES[0], "repository": "owner/repo",
            "parameters": PIPELINE_PARAMS, "pat": "private-pat",
        }
        with patch.dict(os.environ, {"FABRIC_PARALLEL_CHECKOUT": str(self.root / "first")}), \
                patch.object(SERVER.threading, "Thread"), \
                patch.object(SERVER, "_start_workflow", wraps=SERVER._start_workflow) as start:
            response = self.client.post("/api/full-workflow", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        job = SERVER.JOBS[response.get_json()["jobId"]]
        steps = start.call_args.args[0]
        with patch.object(SERVER, "_run_workflow_step", side_effect=[0, 0, 23]):
            SERVER._workflow_worker(job, steps)
        SERVER.JOBS.clear()
        with patch.dict(os.environ, {"FABRIC_PARALLEL_CHECKOUT": str(self.root / "second")}), \
                patch.object(SERVER.threading, "Thread"), \
                patch.object(SERVER, "_start_workflow", wraps=SERVER._start_workflow) as start:
            response = self.client.post("/api/full-workflow", json={**payload, "pat": "", "resumeJobId": job.id})
        self.assertEqual(response.status_code, 200, response.get_json())
        resumed = SERVER.JOBS[response.get_json()["jobId"]]
        with patch.object(SERVER, "_run_workflow_step", return_value=0) as run:
            SERVER._workflow_worker(resumed, start.call_args.args[0])
        self.assertEqual([call.args[1] for call in run.call_args_list], [SERVER.COMBINED_STEP])
        self.assertNotIn("private-pat", SERVER.PIPELINE_DEFAULTS_FILE.read_text(encoding="utf-8"))


class ParallelIsolationTests(unittest.TestCase):
    def test_short_checkout_preserves_previous_workspace_state_for_seeding(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "parallel-deployments" / TENANT / WORKSPACES[0]
            directory.mkdir(parents=True)
            previous = directory / "checkouts" / "previous"
            saved = json.dumps({"checkout": str(previous)})
            (directory / "latest.json").write_text(saved, encoding="utf-8")
            deploy = Mock()
            deploy.secure_private_directory.side_effect = lambda path: path.mkdir(parents=True, exist_ok=True)
            with patch.object(Path, "home", return_value=root), \
                    patch.object(PARALLEL, "checked") as checked, \
                    patch.object(PARALLEL, "seed_rayfin", return_value=True) as seed:
                checkout = PARALLEL.prepare_checkout(deploy, directory, TENANT, WORKSPACES[0], "commit")
            self.assertEqual(checkout.parent, root / ".fabric-demo" / "parallel-checkouts")
            self.assertNotIn(TENANT, str(checkout))
            self.assertEqual(len(checkout.name), 32)
            self.assertEqual(checked.call_args_list[-1].args[0][-1], "commit")
            seed.assert_called_once_with(deploy, previous, checkout, TENANT, WORKSPACES[0])
            self.assertEqual((directory / "latest.json").read_text(encoding="utf-8"), saved)

    def test_preparation_cancellation_does_not_start_a_module(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "cancel").touch()
            payload = {
                "workspace": WORKSPACES[0], "name": "One", "directory": str(directory),
                "action": "/api/setup-energy", "data": {"tenant": TENANT},
            }
            server = Mock()
            with patch.dict(os.environ, {PARALLEL.PAYLOAD_ENV: json.dumps(payload)}), \
                    patch.object(PARALLEL, "load_server", return_value=server), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(PARALLEL.worker(), 1)
            server.app.test_client.return_value.post.assert_not_called()

    def test_unexpected_worker_exit_is_not_certified_as_a_deployment_failure_or_success(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            process = Mock(stdout=io.StringIO("Worker crashed\n"), wait=Mock(return_value=1))
            context = Mock(__enter__=Mock(return_value=process), __exit__=Mock(return_value=False))
            output = io.StringIO()
            with patch.object(PARALLEL, "prepare_checkout", return_value=directory / "checkout"), \
                    patch.object(PARALLEL, "worker_environment", return_value={}), \
                    patch.object(PARALLEL.subprocess, "Popen", return_value=context), \
                    contextlib.redirect_stdout(output):
                result = PARALLEL._run_target(
                    Mock(), directory, "commit", TENANT, WORKSPACES[0], "One", "/api/setup-energy", {},
                    resume=False, sync_latest=False,
                )
            self.assertEqual(result, 1)
            self.assertIn('"status": "interrupted"', output.getvalue())
            self.assertIn("Verify remote Fabric jobs", output.getvalue())

    def test_entra_guard_refreshes_redirects_after_another_worker_publishes(self):
        redirects = ["https://existing.example"]

        def publish(command, tenant):
            time.sleep(.1)
            redirects.append(f"https://{command[0]}.example")
            return "published"

        with TemporaryDirectory() as temporary, \
                patch.object(LOCKS, "lock_directory", return_value=Path(temporary)), \
                patch.dict(os.environ, {"HYDRO_PARALLEL_DEPLOY": "1"}), \
                patch.object(DEPLOY, "current_rayfin_target", return_value=({
                    "RAYFIN_PUBLIC_AAD_CLIENT_ID": "client",
                }, None)), \
                patch.object(DEPLOY, "read_entra_spa_redirects_with_reauth", side_effect=lambda *args: list(redirects)), \
                patch.object(DEPLOY, "write_rayfin_redirects", side_effect=lambda values: redirects.__setitem__(slice(None), values)), \
                patch.object(DEPLOY, "_run_rayfin_deployment", side_effect=publish):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda name: DEPLOY.run_rayfin_deployment([name], TENANT), ["one", "two"]))
        self.assertEqual(results, ["published", "published"])
        self.assertTrue({"https://existing.example", "https://one.example", "https://two.example"}.issubset(redirects))

    def test_parallel_foundry_discovery_rejects_a_sole_unrelated_project(self):
        subscription = str(uuid.uuid4())
        identity = f"/subscriptions/{subscription}/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/account/projects/unrelated"
        deploy = Mock(DeployError=RuntimeError)
        deploy._public_config_value.side_effect = lambda values, key: values.get(key, "")
        deploy.current_rayfin_target.return_value = ({}, None)
        deploy.az.side_effect = lambda *args: list(args)
        deploy.run_capture.side_effect = [
            json.dumps([{"id": identity}]),
            json.dumps({"id": identity, "properties": {"endpoints": {
                "AI Foundry API": "https://account.services.ai.azure.com/api/projects/unrelated",
            }}}),
        ]
        environment = {key: value for key, value in os.environ.items() if not key.startswith("HYDRO_FOUNDRY_")}
        with patch.dict(os.environ, {**environment, "HYDRO_PARALLEL_DEPLOY": "1"}, clear=True), \
                patch.object(FOUNDRY, "resolve_subscription", return_value=subscription), \
                self.assertRaisesRegex(RuntimeError, "sole unrelated project"):
            FOUNDRY.resolve_project(deploy, TENANT, WORKSPACES[0])
        deploy.run_stream.assert_not_called()

    def test_seed_only_matching_target_and_do_not_replace_source_yaml(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "HydroOperationsApp" / "rayfin"
            destination = root / "destination" / "HydroOperationsApp" / "rayfin"
            source.mkdir(parents=True)
            destination.mkdir(parents=True)
            (source / ".env").write_text(
                f"RAYFIN_PUBLIC_TENANT_ID={TENANT}\nRAYFIN_PUBLIC_WORKSPACE_ID={WORKSPACES[0]}\n", encoding="utf-8",
            )
            (source / ".deployments.json").write_text('{"target":"one"}', encoding="utf-8")
            (source / "rayfin.yml").write_text("stale-source", encoding="utf-8")
            (destination / "rayfin.yml").write_text("current-source", encoding="utf-8")
            self.assertFalse(PARALLEL.seed_rayfin(DEPLOY, root / "source", root / "destination", TENANT, WORKSPACES[1]))
            self.assertFalse((destination / ".env").exists())
            self.assertTrue(PARALLEL.seed_rayfin(DEPLOY, root / "source", root / "destination", TENANT, WORKSPACES[0]))
            self.assertEqual((destination / "rayfin.yml").read_text(), "current-source")

    def test_worker_credentials_and_hosted_state_are_private_per_workspace(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(DEPLOY, "secure_private_directory", side_effect=lambda path, **kwargs: path.mkdir(
                parents=True, exist_ok=True,
            )), patch.object(DEPLOY, "isolated_azure_cli_config", return_value=root / "absent"), \
                    patch.dict(os.environ, {
                        "AZURE_CONFIG_DIR": str(root / "absent"),
                        "RAYFIN_PUBLIC_WORKSPACE_ID": "wrong",
                        "HYDRO_FOUNDRY_PROJECT_ENDPOINT": "https://wrong.example",
                    }):
                environments = [
                    PARALLEL.worker_environment(DEPLOY, root / workspace, root / workspace / "checkout", TENANT, {})
                    for workspace in WORKSPACES
                ]
            for environment in environments:
                self.assertNotIn("RAYFIN_PUBLIC_WORKSPACE_ID", environment)
                self.assertNotIn("HYDRO_FOUNDRY_PROJECT_ENDPOINT", environment)
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(str(DEPLOY.isolated_azure_cli_config(TENANT)), environment["AZURE_CONFIG_DIR"])
            self.assertEqual(len({environment["AZURE_CONFIG_DIR"] for environment in environments}), 3)
            self.assertEqual(len({environment["HYDRO_STATE_DIR"] for environment in environments}), 3)

    def test_shared_project_resources_have_disjoint_stable_workspace_names(self):
        suffixes = [LOCKS.foundry_resource_suffix(workspace) for workspace in WORKSPACES]
        self.assertEqual(len(set(suffixes)), 3)
        for workspace, suffix in zip(WORKSPACES, suffixes):
            self.assertEqual(suffix, LOCKS.foundry_resource_suffix(workspace.upper()))
            self.assertLessEqual(len("hydro-work-order-agent" + suffix), 63)
        with self.assertRaises(ValueError):
            LOCKS.foundry_resource_suffix("not-a-workspace-guid")

    def test_resource_guard_serializes_processes_not_the_entire_batch(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = (
                "import sys,time; from pathlib import Path; import deployment_locks as locks;"
                "locks.lock_directory=lambda:Path(sys.argv[1]);"
                "name=sys.argv[2]; path=Path(sys.argv[1])/'events';"
                "\nwith locks.resource_lock('tenant','entra'):\n"
                " with path.open('a') as f: f.write('start '+name+'\\n')\n"
                " time.sleep(.3)\n"
                " with path.open('a') as f: f.write('end '+name+'\\n')\n"
            )
            processes = [subprocess.Popen([sys.executable, "-c", script, str(root), str(index)],
                                         cwd=PARALLEL.SCRIPT_DIR) for index in range(2)]
            for process in processes:
                self.assertEqual(process.wait(timeout=15), 0)
            events = (root / "events").read_text().splitlines()
            self.assertTrue(events[0].startswith("start "))
            self.assertEqual(events[1], events[0].replace("start ", "end "))
            self.assertEqual(events[3], events[2].replace("start ", "end "))

    def test_actual_workers_overlap_with_independent_files_and_partial_failure(self):
        self.exercise_workers("/api/setup-energy", {})

    def test_delete_workers_overlap_and_preserve_explicit_dry_run_or_confirmed_deletion(self):
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                self.exercise_workers("/api/delete", {
                    "dryRun": dry_run, "confirmedWorkspaces": [] if dry_run else WORKSPACES,
                })

    def exercise_workers(self, action, settings):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "source"
            scripts = repository / "Raw" / "workspace-reset"
            scripts.mkdir(parents=True)
            rayfin = repository / "HydroOperationsApp" / "rayfin"
            rayfin.mkdir(parents=True)
            (rayfin / "rayfin.yml").write_text("parent configuration", encoding="utf-8")
            stub = (
                "import json,os,sys,time; from pathlib import Path\n"
                "workspace=sys.argv[sys.argv.index('--workspace')+1]\n"
                "root=Path(__file__).resolve().parents[2]\n"
                "gate=Path(os.environ['PARALLEL_TEST_BARRIER']); gate.mkdir(exist_ok=True)\n"
                "(gate/workspace).touch(); deadline=time.monotonic()+20\n"
                "while len(list(gate.iterdir()))<3 and time.monotonic()<deadline: time.sleep(.02)\n"
                "assert len(list(gate.iterdir()))==3, 'Workers did not run concurrently'\n"
                "start=time.time(); print('Energy: setup',flush=True)\n"
                "time.sleep(1)\n"
                "(root/'result.json').write_text(json.dumps({'workspace':workspace,'start':start,'end':time.time(),'argv':sys.argv}))\n"
                "print('ENERGY_SETUP_SUCCESS',flush=True)\n"
                f"sys.exit(1 if workspace=={WORKSPACES[2]!r} else 0)\n"
            )
            (scripts / "deploy_fabric_app.py").write_text(stub, encoding="utf-8")
            (scripts / "delete_workspace_items.py").write_text(stub, encoding="utf-8")
            PARALLEL.checked(["git", "init", "--quiet", str(repository)])
            PARALLEL.checked(["git", "-C", str(repository), "add", "."])
            PARALLEL.checked([
                "git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Isolated worker fixture",
            ])
            fabric = Mock()
            fabric.resolve_workspace_id.side_effect = [(workspace, f"Target {index}")
                                                      for index, workspace in enumerate(WORKSPACES)]

            def environment(deploy, directory, checkout, tenant, payload):
                return {
                    **os.environ, "PYTHONUNBUFFERED": "1",
                    "FABRIC_PARALLEL_CHECKOUT": str(checkout),
                    "FABRIC_UI_PIPELINE_DEFAULTS_FILE": str(directory / "checkpoints.json"),
                    "PARALLEL_TEST_BARRIER": str(root / "barrier"),
                    PARALLEL.PAYLOAD_ENV: json.dumps(payload),
                }

            output = io.StringIO()
            with patch.object(PARALLEL, "REPO_ROOT", repository), \
                    patch.object(Path, "home", return_value=root), \
                    patch.object(DEPLOY, "secure_private_directory", side_effect=lambda path, **_:
                                 path.mkdir(parents=True, exist_ok=True)), \
                    patch.object(PARALLEL, "worker_environment", side_effect=environment), \
                    patch.object(SYNC, "Fabric", return_value=fabric), contextlib.redirect_stdout(output):
                result = PARALLEL.run_batch({
                    "action": action, "workspaces": WORKSPACES,
                    "data": {"tenant": TENANT, **settings},
                })
            self.assertEqual(result, 1)
            records = [json.loads(path.read_text()) for path in root.glob(
                ".fabric-demo/parallel-checkouts/*/result.json",
            )]
            self.assertEqual({record["workspace"] for record in records}, set(WORKSPACES))
            self.assertLess(max(record["start"] for record in records), min(record["end"] for record in records))
            if action == "/api/delete":
                expected, forbidden = ("--dry-run", "--yes") if settings["dryRun"] else ("--yes", "--dry-run")
                for record in records:
                    self.assertIn(expected, record["argv"])
                    self.assertNotIn(forbidden, record["argv"])
            self.assertEqual((rayfin / "rayfin.yml").read_text(), "parent configuration")
            self.assertIn("2/3 workspaces succeeded", output.getvalue())


if __name__ == "__main__":
    unittest.main()
