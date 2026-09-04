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
