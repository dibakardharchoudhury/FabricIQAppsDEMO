import importlib.util
import http.client
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).with_name("launch.py")
SPEC = importlib.util.spec_from_file_location("fabric_demo_launch", MODULE_PATH)
assert SPEC and SPEC.loader
LAUNCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCH)


class FakeResponse:
    def __init__(self, body: str = ""):
        self.body = body
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False

    def read(self):
        return self.body.encode("utf-8")

    def close(self):
        self.closed = True


class ExistingServerTests(unittest.TestCase):
    def test_launch_uses_selected_sources_and_preserves_original_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            here = Path(root) / "development" / "Raw" / "workspace-reset"
            server = Path(root) / "release" / "Raw" / "workspace-reset" / "webapp" / "server.py"
            with (
                patch.object(LAUNCH, "HERE", here),
                patch.object(LAUNCH, "server_path", return_value=server),
                patch.object(LAUNCH.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
                patch.object(LAUNCH.shutil, "which", return_value="az"),
                patch.object(LAUNCH, "stop_existing_server"),
                patch.object(LAUNCH.subprocess, "Popen") as start,
                patch.object(LAUNCH.urllib.request, "urlopen", side_effect=[
                    ConnectionResetError(10054, "Connection forcibly closed"),
                    http.client.RemoteDisconnected("Server closed the connection"),
                    TimeoutError("Server still starting"),
                    Mock(),
                ]) as urlopen,
                patch.object(LAUNCH.time, "sleep"),
                patch.object(LAUNCH.webbrowser, "open"),
                patch.dict(LAUNCH.os.environ, {}, clear=True),
            ):
                start.return_value.poll.return_value = None
                self.assertEqual(LAUNCH.main(), 0)
            self.assertEqual(urlopen.call_count, 4)
            self.assertEqual(start.call_args.args[0], [LAUNCH.sys.executable, str(server)])
            self.assertEqual(run.call_args.args[0][-1], str(server.parent.parent / "requirements.txt"))
            self.assertEqual(
                start.call_args.kwargs["env"]["FABRIC_UI_PIPELINE_DEFAULTS_FILE"],
                str(here / "webapp" / "pipeline-defaults.local.json"),
            )

    def test_default_launcher_uses_its_own_server(self):
        with tempfile.TemporaryDirectory() as root, patch.object(LAUNCH, "HERE", Path(root)):
            self.assertEqual(LAUNCH.server_path(), LAUNCH.SERVER)

    def test_configured_clean_checkout_supplies_the_server(self):
        with tempfile.TemporaryDirectory() as root:
            here = Path(root)
            checkout = here / "release"
            server = checkout / "Raw" / "workspace-reset" / "webapp" / "server.py"
            server.parent.mkdir(parents=True)
            server.write_text("# test server\n", encoding="utf-8")
            (here / "launcher.local.json").write_text(json.dumps({"checkout": str(checkout)}), encoding="utf-8")
            with patch.object(LAUNCH, "HERE", here), patch.object(
                LAUNCH.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=""),
            ) as verify:
                self.assertEqual(LAUNCH.server_path(), server)
            self.assertEqual(verify.call_args.args[0][:3], ["git", "-C", str(checkout)])

    def test_modified_configured_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            here = Path(root)
            server = here / "Raw" / "workspace-reset" / "webapp" / "server.py"
            server.parent.mkdir(parents=True)
            server.write_text("# test server\n", encoding="utf-8")
            (here / "launcher.local.json").write_text(json.dumps({"checkout": root}), encoding="utf-8")
            with patch.object(LAUNCH, "HERE", here), patch.object(
                LAUNCH.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=" M server.py"),
            ), self.assertRaisesRegex(ValueError, "modified tracked files"):
                LAUNCH.server_path()

    def test_invalid_configuration_does_not_fall_back_to_the_old_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            here = Path(root)
            (here / "launcher.local.json").write_text('{"checkout": null}', encoding="utf-8")
            with patch.object(LAUNCH, "HERE", here), self.assertRaisesRegex(ValueError, "checkout path"):
                LAUNCH.server_path()

    def test_no_existing_server_needs_no_shutdown(self):
        with patch.object(
            LAUNCH.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError(10061, "not running")),
        ) as urlopen:
            LAUNCH.stop_existing_server()

        urlopen.assert_called_once_with(LAUNCH.APP_URL, timeout=3)

    def test_existing_demo_server_is_stopped_before_launch(self):
        urlopen = Mock(
            side_effect=[
                FakeResponse("<title>Initialize Your Fabric Demo</title>"),
                FakeResponse(),
                urllib.error.URLError(ConnectionRefusedError(10061, "stopped")),
            ]
        )
        with patch.object(LAUNCH.urllib.request, "urlopen", urlopen):
            LAUNCH.stop_existing_server()

        shutdown_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(shutdown_request.full_url, LAUNCH.APP_URL + "api/shutdown")
        self.assertEqual(shutdown_request.method, "POST")
        self.assertEqual(urlopen.call_args_list[0].kwargs["timeout"], 3)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 3)

    def test_shutdown_poll_retries_resets_until_the_port_is_closed(self):
        for error in (ConnectionResetError(10054, "Connection forcibly closed"),
                      urllib.error.URLError(ConnectionResetError(10054, "Connection forcibly closed")),
                      http.client.RemoteDisconnected("Closing"),
                      TimeoutError("Closing")):
            with self.subTest(error=error):
                urlopen = Mock(side_effect=[
                    FakeResponse("<title>Initialize Your Fabric Demo</title>"),
                    FakeResponse(),
                    error,
                    FakeResponse(),
                    urllib.error.URLError(ConnectionRefusedError(10061, "stopped")),
                ])
                with patch.object(LAUNCH.urllib.request, "urlopen", urlopen), patch.object(LAUNCH.time, "sleep"):
                    LAUNCH.stop_existing_server()
                self.assertEqual(urlopen.call_count, 5)

    def test_shutdown_post_reset_is_followed_by_port_verification(self):
        urlopen = Mock(side_effect=[
            FakeResponse("<title>Initialize Your Fabric Demo</title>"),
            ConnectionResetError(10054, "Connection forcibly closed"),
            FakeResponse(),
            urllib.error.URLError(ConnectionRefusedError(10061, "stopped")),
        ])
        with patch.object(LAUNCH.urllib.request, "urlopen", urlopen), patch.object(LAUNCH.time, "sleep"):
            LAUNCH.stop_existing_server()
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(urlopen.call_args_list[1].args[0].method, "POST")

    def test_persistent_shutdown_resets_fail_instead_of_claiming_the_port_is_free(self):
        urlopen = Mock(side_effect=[
            FakeResponse("<title>Initialize Your Fabric Demo</title>"), FakeResponse(),
            *[ConnectionResetError(10054, "Still closing") for _ in range(30)],
        ])
        with (
            patch.object(LAUNCH.urllib.request, "urlopen", urlopen),
            patch.object(LAUNCH.time, "sleep"),
            patch.object(LAUNCH, "fail", side_effect=RuntimeError) as fail,
        ):
            with self.assertRaises(RuntimeError):
                LAUNCH.stop_existing_server()
        fail.assert_called_once()
        self.assertIn("did not stop", fail.call_args.args[0])

    def test_unidentified_resetting_service_is_not_stopped(self):
        with (
            patch.object(LAUNCH.urllib.request, "urlopen",
                         side_effect=ConnectionResetError(10054, "Connection forcibly closed")) as urlopen,
            patch.object(LAUNCH, "fail", side_effect=RuntimeError) as fail,
        ):
            with self.assertRaises(RuntimeError):
                LAUNCH.stop_existing_server()
        fail.assert_called_once()
        urlopen.assert_called_once()

    def test_initial_http_error_is_not_treated_as_an_unused_port(self):
        with (
            patch.object(LAUNCH.urllib.request, "urlopen", side_effect=urllib.error.HTTPError(
                LAUNCH.APP_URL, 500, "Internal Server Error", {}, None,
            )) as urlopen,
            patch.object(LAUNCH, "fail", side_effect=RuntimeError) as fail,
        ):
            with self.assertRaises(RuntimeError):
                LAUNCH.stop_existing_server()
        fail.assert_called_once()
        urlopen.assert_called_once()

    def test_running_job_refuses_shutdown(self):
        urlopen = Mock(side_effect=[
            FakeResponse("<title>Initialize Your Fabric Demo</title>"),
            urllib.error.HTTPError(LAUNCH.APP_URL + "api/shutdown", 409, "Conflict", {}, None),
        ])
        with (
            patch.object(LAUNCH.urllib.request, "urlopen", urlopen),
            patch.object(LAUNCH, "fail", side_effect=RuntimeError) as fail,
        ):
            with self.assertRaises(RuntimeError):
                LAUNCH.stop_existing_server()
        self.assertIn("running a job", fail.call_args.args[0])
        self.assertEqual(urlopen.call_count, 2)

    def test_unrelated_service_on_port_is_not_stopped(self):
        with (
            patch.object(
                LAUNCH.urllib.request,
                "urlopen",
                return_value=FakeResponse("<title>Another application</title>"),
            ),
            patch.object(LAUNCH, "fail", side_effect=RuntimeError) as fail,
        ):
            with self.assertRaises(RuntimeError):
                LAUNCH.stop_existing_server()

        fail.assert_called_once_with(
            "Port 5000 is already used by another application. Stop it, then retry."
        )


if __name__ == "__main__":
    unittest.main()
