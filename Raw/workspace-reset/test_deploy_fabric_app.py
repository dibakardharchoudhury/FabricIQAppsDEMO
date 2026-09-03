import argparse
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).with_name("deploy_fabric_app.py")
SPEC = importlib.util.spec_from_file_location("deploy_fabric_app", MODULE_PATH)
assert SPEC and SPEC.loader
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class DeployOrderTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
