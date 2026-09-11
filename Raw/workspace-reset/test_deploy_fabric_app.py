import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).with_name("deploy_fabric_app.py")
SPEC = importlib.util.spec_from_file_location("deploy_fabric_app", MODULE_PATH)
assert SPEC and SPEC.loader
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class DeployOrderTests(unittest.TestCase):
    def test_spa_name_has_a_stable_configurable_default(self):
        self.assertEqual(DEPLOY.DEFAULT_APP_DISPLAY_NAME, "Hydro Operations Fabric Client")
        self.assertTrue(DEPLOY.APP_DISPLAY_NAME)

    def test_node24_uses_cached_runtime_without_invoking_npx(self):
        cached = Path("C:/npm-cache/_npx/node24/node_modules/node/bin/node.exe")

        with (
            patch.object(DEPLOY, "_NODE24_EXECUTABLE", None),
            patch.object(DEPLOY, "cached_node24_executable", return_value=cached),
            patch.object(DEPLOY, "command_argv") as command,
        ):
            self.assertEqual(DEPLOY.node24_executable(), cached)

        command.assert_not_called()

    def test_reuses_healthy_dependencies_without_npm_ci(self):
        with (
            patch.object(DEPLOY.shutil, "which", return_value="npx"),
            patch.object(DEPLOY, "deploy_dependencies_ready", return_value=True),
            patch.object(DEPLOY, "installed_rayfin_version", return_value="1.33.2"),
            patch.object(DEPLOY, "stop_hydro_node_tooling") as stop_tooling,
            patch.object(DEPLOY, "run_stream") as run_stream,
        ):
            DEPLOY.ensure_deploy_dependencies()

        stop_tooling.assert_not_called()
        run_stream.assert_not_called()

    def test_dependency_tree_requires_current_lockfile_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            (app_dir / "node_modules" / ".bin").mkdir(parents=True)
            for executable in ("vite.cmd", "tsc.cmd"):
                (app_dir / "node_modules" / ".bin" / executable).write_text("", encoding="utf-8")
            (app_dir / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")

            with (
                patch.object(DEPLOY, "APP_DIR", app_dir),
                patch.object(DEPLOY, "DEPENDENCY_STAMP", app_dir / "node_modules" / ".stamp"),
                patch.object(DEPLOY, "installed_rayfin_version", return_value="1.33.2"),
                patch.object(DEPLOY, "run_capture") as run_capture,
            ):
                self.assertFalse(DEPLOY.deploy_dependencies_ready())

            run_capture.assert_not_called()

    def test_npm24_hosts_npm_cli_with_node24(self):
        npm_cli = Path("C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js")
        node = Path("C:/node24/node.exe")

        with (
            patch.object(DEPLOY, "npm_cli_path", return_value=npm_cli),
            patch.object(DEPLOY, "node24_executable", return_value=node),
        ):
            command = DEPLOY.npm24("ci", "--no-audit")

        self.assertEqual(command, [str(node), str(npm_cli), "ci", "--no-audit"])

    def test_stops_only_hydro_node_tooling_before_dependency_restore(self):
        with (
            patch.object(DEPLOY.os, "name", "nt"),
            patch.object(DEPLOY, "command_argv", return_value=["powershell", "script"]) as command,
            patch.object(DEPLOY, "run_capture", return_value="101,202") as run_capture,
        ):
            DEPLOY.stop_hydro_node_tooling()

        command.assert_called_once()
        powershell_script = command.call_args.args[-1]
        self.assertIn(str(DEPLOY.APP_DIR), powershell_script)
        self.assertIn("$_.ProcessId -ne $PID", powershell_script)
        self.assertIn("IndexOf", powershell_script)
        self.assertIn("vite", powershell_script)
        self.assertIn("esbuild.exe", powershell_script)
        self.assertNotIn("Get-Process node", powershell_script)
        run_capture.assert_called_once_with(["powershell", "script"])

    def test_dependency_restore_stops_hydro_tooling_before_npm_ci(self):
        events = []

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(DEPLOY.shutil, "which", return_value="npx"),
                patch.object(DEPLOY, "deploy_dependencies_ready", return_value=False),
                patch.object(DEPLOY, "DEPENDENCY_STAMP", Path(temp_dir) / ".stamp"),
                patch.object(DEPLOY, "stop_hydro_node_tooling", side_effect=lambda: events.append("stop")),
                patch.object(DEPLOY, "npm24", return_value=["npm-ci"]),
                patch.object(DEPLOY, "run_stream", side_effect=lambda _argv, **_kwargs: events.append("npm")),
                patch.object(DEPLOY, "installed_rayfin_version", return_value="1.33.2"),
            ):
                DEPLOY.ensure_deploy_dependencies()

        self.assertEqual(events, ["stop", "npm"])

    def test_does_not_write_redirects_when_entra_snapshot_fails(self):
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id="11111111-1111-1111-1111-111111111111",
            push_config=False,
        )

        with (
            patch.object(DEPLOY, "ensure_azure_tenant"),
            patch.object(DEPLOY, "resolve_workspace", return_value=("workspace-id", "Demo Workspace")),
            patch.object(DEPLOY, "resolve_spa", return_value=args.client_id),
            patch.object(
                DEPLOY,
                "read_entra_spa_redirects_with_reauth",
                side_effect=DEPLOY.DeployError("snapshot still unavailable"),
            ),
            patch.object(DEPLOY, "write_rayfin_redirects") as write_redirects,
        ):
            with self.assertRaisesRegex(
                DEPLOY.DeployError,
                "redirect preservation cannot be guaranteed",
            ):
                DEPLOY.deploy(args)

        write_redirects.assert_not_called()

    def test_reauthenticates_after_stale_token_before_reading_redirects(self):
        stale = DEPLOY.DeployError(
            "Continuous access evaluation resulted in challenge with result: "
            "InteractionRequired and code: TokenCreatedWithOutdatedPolicies"
        )

        with (
            patch.object(
                DEPLOY,
                "read_entra_spa_redirects",
                side_effect=[stale, ["https://existing.webapp.fabricapps.net"]],
            ) as read_redirects,
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_stream") as run_stream,
            patch.object(DEPLOY, "ensure_azure_tenant") as ensure_tenant,
        ):
            redirects = DEPLOY.read_entra_spa_redirects_with_reauth("client-id", "tenant-id")

        self.assertEqual(redirects, ["https://existing.webapp.fabricapps.net"])
        self.assertEqual(read_redirects.call_count, 2)
        run_stream.assert_called_once_with(
            ["login", "--tenant", "tenant-id", "--allow-no-subscriptions", "--only-show-errors"]
        )
        ensure_tenant.assert_called_once_with("tenant-id")

    def test_reauthenticates_after_stale_token_before_discovering_spa(self):
        stale = DEPLOY.DeployError(
            "Continuous access evaluation resulted in challenge with result: "
            "InteractionRequired and code: TokenCreatedWithOutdatedPolicies"
        )
        client_id = "11111111-1111-1111-1111-111111111111"

        with (
            patch.object(DEPLOY, "existing_spa_candidate", return_value=None),
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=[stale, json.dumps([client_id])]) as run_capture,
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
            patch.object(DEPLOY, "ensure_spa_service_principal") as ensure_service_principal,
        ):
            resolved = DEPLOY.resolve_spa(None, "tenant-id")

        self.assertEqual(resolved, client_id)
        self.assertEqual(run_capture.call_count, 2)
        reauthenticate.assert_called_once_with("tenant-id", "discovering the tenant SPA app registration")
        ensure_service_principal.assert_called_once_with(client_id)

    def test_git_push_target_uses_matching_feature_upstream(self):
        with (
            patch.object(DEPLOY, "command_argv", side_effect=lambda executable, *args: [executable, *args]),
            patch.object(
                DEPLOY,
                "run_capture",
                side_effect=["feat/dibakar", "origin/feat/dibakar"],
            ),
        ):
            target = DEPLOY.current_git_push_target()

        self.assertEqual(target, ("feat/dibakar", "origin/feat/dibakar"))

    def test_git_push_target_refuses_main(self):
        with (
            patch.object(DEPLOY, "command_argv", side_effect=lambda executable, *args: [executable, *args]),
            patch.object(DEPLOY, "run_capture", return_value="main"),
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "refuses to commit or push the main branch"):
                DEPLOY.current_git_push_target()

    def test_persist_generated_origin_pushes_current_feature_branch(self):
        with (
            patch.object(
                DEPLOY,
                "current_git_push_target",
                return_value=("feat/dibakar", "origin/feat/dibakar"),
            ),
            patch.object(DEPLOY, "command_argv", side_effect=lambda executable, *args: [executable, *args]),
            patch.object(DEPLOY, "run_capture", side_effect=["changed", "0 0"]),
            patch.object(DEPLOY, "run_stream") as run_stream,
        ):
            DEPLOY.persist_generated_origin("Demo Workspace")

        self.assertEqual(run_stream.call_args_list[-1].args[0], ["git", "push", "origin", "feat/dibakar"])

    def test_fabric_token_missing_from_msal_cache_reauthenticates_once(self):
        missing = DEPLOY.DeployError(
            "ERROR: User 'admin@example.test' does not exist in MSAL token cache. Run 'az login'."
        )

        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=[missing, "fabric-token"]) as run_capture,
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
        ):
            headers = DEPLOY.fabric_headers("tenant-id")

        self.assertEqual(headers, {"Authorization": "Bearer fabric-token"})
        self.assertEqual(run_capture.call_count, 2)
        reauthenticate.assert_called_once_with("tenant-id", "accessing the Fabric workspace")

    def test_reauthentication_is_tenant_scoped_and_non_deleting(self):
        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_stream") as run_stream,
            patch.object(DEPLOY, "ensure_azure_tenant") as ensure_tenant,
            patch.object(DEPLOY.Path, "unlink", side_effect=AssertionError("must not delete cache")),
        ):
            DEPLOY.reauthenticate_azure_cli("tenant-id", "testing")

        run_stream.assert_called_once_with([
            "login", "--tenant", "tenant-id", "--allow-no-subscriptions", "--only-show-errors",
        ])
        ensure_tenant.assert_called_once_with("tenant-id")

    def test_fabric_token_authorization_error_does_not_trigger_login(self):
        denied = DEPLOY.DeployError("Authorization_RequestDenied")

        with (
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_capture", side_effect=denied),
            patch.object(DEPLOY, "reauthenticate_azure_cli") as reauthenticate,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Authorization_RequestDenied"):
                DEPLOY.fabric_headers("tenant-id")

        reauthenticate.assert_not_called()

    def test_does_not_reauthenticate_for_non_cae_redirect_failure(self):
        failure = DEPLOY.DeployError("Authorization_RequestDenied")

        with (
            patch.object(DEPLOY, "read_entra_spa_redirects", side_effect=failure),
            patch.object(DEPLOY, "run_stream") as run_stream,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Authorization_RequestDenied"):
                DEPLOY.read_entra_spa_redirects_with_reauth("client-id", "tenant-id")

        run_stream.assert_not_called()

    def test_missing_spa_stops_before_rayfin_state_is_touched(self):
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id=None,
            push_config=False,
        )
        prepare = Mock()

        with (
            patch.object(DEPLOY, "ensure_azure_tenant"),
            patch.object(DEPLOY, "resolve_workspace", return_value=("workspace-id", "Demo Workspace")),
            patch.object(DEPLOY, "resolve_spa", return_value=None),
            patch.object(DEPLOY, "write_rayfin_redirects") as write_redirects,
            patch.object(DEPLOY, "prepare_rayfin_env", prepare),
            patch.object(DEPLOY, "ensure_deploy_dependencies") as ensure_dependencies,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "usable Entra SPA"):
                DEPLOY.deploy(args)

        write_redirects.assert_not_called()
        prepare.assert_not_called()
        ensure_dependencies.assert_not_called()

    def test_reuses_existing_backend_for_static_deploy(self):
        client_id = "11111111-1111-1111-1111-111111111111"
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id=client_id,
            push_config=False,
        )

        with (
            patch.object(DEPLOY, "ensure_azure_tenant"),
            patch.object(DEPLOY, "resolve_workspace", return_value=("workspace-id", "Demo Workspace")),
            patch.object(DEPLOY, "resolve_spa", return_value=client_id),
            patch.object(DEPLOY, "read_entra_spa_redirects_with_reauth", return_value=[]),
            patch.object(DEPLOY, "write_rayfin_redirects", return_value=["http://localhost:5173"]),
            patch.object(DEPLOY, "prepare_rayfin_env", return_value=True),
            patch.object(DEPLOY, "ensure_deploy_dependencies"),
            patch.object(DEPLOY, "ensure_rayfin_login"),
            patch.object(
                DEPLOY,
                "rayfin24",
                side_effect=lambda *arguments: list(arguments),
            ),
            patch.object(
                DEPLOY,
                "run_stream",
                side_effect=[
                    "Hosting URL: https://fast.webapp.fabricapps.net",
                    "backend updated",
                    "live auth configured",
                ],
            ) as run_stream,
            patch.object(DEPLOY.requests, "get", return_value=Mock(status_code=200, headers={"Content-Type": "text/html"})),
            patch.object(DEPLOY, "validate_fabric_app"),
            patch.object(DEPLOY, "validate_spa_redirect_preservation"),
            patch.object(DEPLOY, "validate_entra_live_auth"),
        ):
            DEPLOY.deploy(args)

        self.assertEqual(run_stream.call_args_list[0].args[0], ["up", "staticapp", "deploy"])

    def test_existing_registered_origin_skips_backend_reprovisioning(self):
        hosting_url = "https://fast.webapp.fabricapps.net"
        args = argparse.Namespace(
            tenant="tenant.example",
            workspace="workspace-id",
            client_id="11111111-1111-1111-1111-111111111111",
            push_config=False,
        )

        with (
            patch.object(DEPLOY, "ensure_azure_tenant"),
            patch.object(DEPLOY, "resolve_workspace", return_value=("workspace-id", "Demo Workspace")),
            patch.object(DEPLOY, "resolve_spa", return_value=args.client_id),
            patch.object(DEPLOY, "read_entra_spa_redirects_with_reauth", return_value=[hosting_url]),
            patch.object(DEPLOY, "write_rayfin_redirects", side_effect=lambda redirects: redirects),
            patch.object(DEPLOY, "prepare_rayfin_env", return_value=True),
            patch.object(DEPLOY, "ensure_deploy_dependencies"),
            patch.object(DEPLOY, "ensure_rayfin_login"),
            patch.object(DEPLOY, "rayfin24", side_effect=lambda *arguments: list(arguments)),
            patch.object(DEPLOY, "node24_script", return_value=["setup-live-auth"]),
            patch.object(DEPLOY, "run_stream", return_value=f"Hosting URL: {hosting_url}") as run_stream,
            patch.object(DEPLOY.requests, "get", return_value=Mock(status_code=200, headers={"Content-Type": "text/html"})),
            patch.object(DEPLOY, "validate_fabric_app"),
            patch.object(DEPLOY, "validate_spa_redirect_preservation"),
            patch.object(DEPLOY, "validate_entra_live_auth"),
        ):
            DEPLOY.deploy(args)

        self.assertEqual(run_stream.call_count, 2)
        self.assertEqual(run_stream.call_args_list[0].args[0], ["up", "staticapp", "deploy"])
        self.assertEqual(run_stream.call_args_list[1].args[0], ["setup-live-auth"])

    def test_verifies_installed_rayfin_without_running_the_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            package_dir = app_dir / "node_modules" / "@microsoft" / "rayfin-cli"
            executable = package_dir / "scripts" / "main"
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            (package_dir / "package.json").write_text(
                json.dumps({"version": "1.33.2", "bin": {"rayfin": "scripts/main"}}),
                encoding="utf-8",
            )

            with patch.object(DEPLOY, "APP_DIR", app_dir):
                self.assertEqual(DEPLOY.installed_rayfin_version(), "1.33.2")

    def test_live_auth_contract_covers_foundry_and_fabric_embed(self):
        self.assertEqual(
            DEPLOY.REQUIRED_DELEGATED["7d312290-28c8-473c-a0ed-8e53749b6d6d"],
            {"user_impersonation"},
        )
        self.assertIn(
            "Fabric.Embed",
            DEPLOY.REQUIRED_DELEGATED["00000009-0000-0000-c000-000000000000"],
        )

    def test_state_rotation_moves_only_known_files_to_temp_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rayfin_dir = root / "rayfin"
            backup_root = root / "temp"
            rayfin_dir.mkdir()
            backup_root.mkdir()
            template = "\n".join(
                (
                    "FABRIC_WORKSPACE_NAME=<your Fabric workspace display name>",
                    "RAYFIN_PUBLIC_WORKSPACE_ID=<your Fabric workspace GUID>",
                    "RAYFIN_PUBLIC_AAD_CLIENT_ID=<your Entra SPA app (client) id>",
                    "RAYFIN_PUBLIC_TENANT_ID=<your Entra tenant id>",
                )
            )
            (rayfin_dir / ".env.example").write_text(template, encoding="utf-8")
            prior = {
                ".env": "old env",
                ".env.local": "old generated env",
                ".deployments.json": "{}",
            }
            for name, content in prior.items():
                (rayfin_dir / name).write_text(content, encoding="utf-8")

            with (
                patch.object(DEPLOY, "RAYFIN_DIR", rayfin_dir),
                patch.object(DEPLOY.tempfile, "gettempdir", return_value=str(backup_root)),
                patch.object(DEPLOY.Path, "unlink", side_effect=AssertionError("must not delete state")),
            ):
                DEPLOY.prepare_rayfin_env(
                    "ad340c84-1886-4202-a483-2da2cb9168eb",
                    "a79a4b7e-e508-4fa4-8b6f-15deadca0f34",
                    "Demo Workspace",
                    "22dedc54-8b7e-442c-929d-497c4df086e6",
                )

            backup_dirs = list((backup_root / "fabric-demo-rayfin-backups").iterdir())
            self.assertEqual(len(backup_dirs), 1)
            for name, content in prior.items():
                self.assertEqual((backup_dirs[0] / name).read_text(encoding="utf-8"), content)
            self.assertIn(
                "RAYFIN_PUBLIC_AAD_CLIENT_ID=22dedc54-8b7e-442c-929d-497c4df086e6",
                (rayfin_dir / ".env").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
