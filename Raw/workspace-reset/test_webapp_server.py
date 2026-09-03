import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
