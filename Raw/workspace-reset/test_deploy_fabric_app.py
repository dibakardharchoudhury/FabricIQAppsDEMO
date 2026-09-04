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

        with (
            patch.object(DEPLOY.shutil, "which", return_value="npx"),
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
            patch.object(DEPLOY.Path, "home", return_value=Path("C:/Users/test")),
            patch.object(DEPLOY.Path, "unlink") as unlink,
            patch.object(DEPLOY, "az", side_effect=lambda *args: list(args)),
            patch.object(DEPLOY, "run_stream") as run_stream,
            patch.object(DEPLOY, "ensure_azure_tenant") as ensure_tenant,
        ):
            redirects = DEPLOY.read_entra_spa_redirects_with_reauth("client-id", "tenant-id")

        self.assertEqual(redirects, ["https://existing.webapp.fabricapps.net"])
        self.assertEqual(read_redirects.call_count, 2)
        self.assertEqual(unlink.call_count, 2)
        run_stream.assert_called_once_with(
            ["login", "--tenant", "tenant-id", "--only-show-errors"]
        )
        ensure_tenant.assert_called_once_with("tenant-id")

    def test_does_not_reauthenticate_for_non_cae_redirect_failure(self):
        failure = DEPLOY.DeployError("Authorization_RequestDenied")

        with (
            patch.object(DEPLOY, "read_entra_spa_redirects", side_effect=failure),
            patch.object(DEPLOY, "run_stream") as run_stream,
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "Authorization_RequestDenied"):
                DEPLOY.read_entra_spa_redirects_with_reauth("client-id", "tenant-id")

        run_stream.assert_not_called()

    def test_generates_rayfin_env_before_verifying_dependencies(self):
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
            patch.object(DEPLOY, "write_rayfin_redirects", return_value=["http://localhost:5173"]),
            patch.object(DEPLOY, "prepare_rayfin_env", prepare),
            patch.object(DEPLOY, "ensure_deploy_dependencies", side_effect=DEPLOY.DeployError("stop")),
        ):
            with self.assertRaisesRegex(DEPLOY.DeployError, "stop"):
                DEPLOY.deploy(args)

        prepare.assert_called_once_with(
            "tenant.example",
            "workspace-id",
            "Demo Workspace",
            None,
        )

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


if __name__ == "__main__":
    unittest.main()
