import importlib.util
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

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body.encode("utf-8")


class ExistingServerTests(unittest.TestCase):
    def test_no_existing_server_needs_no_shutdown(self):
        with patch.object(
            LAUNCH.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("not running"),
        ) as urlopen:
            LAUNCH.stop_existing_server()

        urlopen.assert_called_once_with(LAUNCH.APP_URL, timeout=1)

    def test_existing_demo_server_is_stopped_before_launch(self):
        urlopen = Mock(
            side_effect=[
                FakeResponse("<title>Initialize Your Fabric Demo</title>"),
                FakeResponse(),
                urllib.error.URLError("stopped"),
            ]
        )
        with patch.object(LAUNCH.urllib.request, "urlopen", urlopen):
            LAUNCH.stop_existing_server()

        shutdown_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(shutdown_request.full_url, LAUNCH.APP_URL + "api/shutdown")
        self.assertEqual(shutdown_request.method, "POST")

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
