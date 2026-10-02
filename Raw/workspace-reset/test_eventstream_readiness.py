import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import requests


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOKS = ROOT / "Notebooks"
RAW_NOTEBOOKS = ROOT / "Raw" / "RTI_Notebooks"


def source(number: str) -> str:
    path = next(NOTEBOOKS.glob(f"RTI_{number}_*.Notebook/notebook-content.py"))
    return path.read_text(encoding="utf-8")


def load_function(number: str, name: str, namespace: dict):
    tree = ast.parse(source(number))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), namespace)
    return namespace[name]


class Response:
    def __init__(self, status_code: int, payload: dict, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


class EventstreamReadinessTests(unittest.TestCase):
    def test_nb02_waits_for_update_lro(self):
        responses = [
            Response(200, {"status": "Running"}),
            Response(200, {"status": "Succeeded"}),
        ]
        clock = SimpleNamespace(time=Mock(side_effect=[0, 0, 1]), sleep=Mock())
        namespace = {
            "json": json,
            "requests": SimpleNamespace(get=Mock(side_effect=responses)),
            "time": clock,
        }
        wait = load_function("002", "wait_for_fabric_lro", namespace)

        self.assertEqual(
            wait("https://operation", "token", max_wait_seconds=10, poll_seconds=1),
            {"status": "Succeeded"},
        )
        self.assertEqual(namespace["requests"].get.call_count, 2)
        clock.sleep.assert_called_once_with(1)
        self.assertIn("wait_for_fabric_lro(operation_url, token)", source("002"))

    def test_nb02_retries_transient_connection_404(self):
        ready = {"endpoint": "sb://ready/", "entityPath": "events", "connectionString": "secret"}
        one_shot = Mock(side_effect=[http_error(404), ready])
        clock = SimpleNamespace(time=Mock(side_effect=[0, 0, 1]), sleep=Mock())
        namespace = {
            "requests": requests,
            "time": clock,
            "_get_custom_endpoint_connection_once": one_shot,
        }
        get_connection = load_function("002", "get_custom_endpoint_connection", namespace)

        self.assertEqual(
            get_connection("workspace", "eventstream", "token", "source",
                           max_wait_seconds=10, poll_seconds=1),
            ready,
        )
        self.assertEqual(one_shot.call_count, 2)
        clock.sleep.assert_called_once_with(1)

    def test_connection_retry_rejects_non_transient_errors(self):
        one_shot = Mock(side_effect=http_error(403))
        clock = SimpleNamespace(time=Mock(side_effect=[0, 0]), sleep=Mock())
        namespace = {
            "requests": requests,
            "time": clock,
            "_get_custom_endpoint_connection_once": one_shot,
        }
        get_connection = load_function("007", "get_custom_endpoint_connection", namespace)

        with self.assertRaises(requests.HTTPError):
            get_connection("workspace", "eventstream", "token", "source",
                           max_wait_seconds=10, poll_seconds=1)
        clock.sleep.assert_not_called()

    def test_raw_notebooks_compile_and_match_readiness_contract(self):
        for number in ("002", "007"):
            path = next(RAW_NOTEBOOKS.glob(f"RTI_{number}_*.ipynb"))
            notebook = json.loads(path.read_text(encoding="utf-8"))
            code = "\n".join(
                "".join(cell.get("source", []))
                for cell in notebook["cells"]
                if cell.get("cell_type") == "code"
            )
            compile(code, str(path), "exec")
            self.assertIn("def get_custom_endpoint_connection(", code)
            self.assertIn("Eventstream readiness returned HTTP", code)
        nb02 = next(RAW_NOTEBOOKS.glob("RTI_002_*.ipynb")).read_text(encoding="utf-8")
        self.assertIn("wait_for_fabric_lro(operation_url, token)", nb02)


if __name__ == "__main__":
    unittest.main()
