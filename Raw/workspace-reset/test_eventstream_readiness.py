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

    def raise_for_status(self):
        if self.status_code >= 400:
            raise http_error(self.status_code)


def http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


class EventstreamReadinessTests(unittest.TestCase):
    def create_namespace(self, response, result=None):
        return {
            "FABRIC_BASE_URL": "https://api.fabric.microsoft.com/v1",
            "target_folder_id": "folder",
            "requests": SimpleNamespace(
                post=Mock(return_value=response),
                get=Mock(return_value=Response(200, result or {})),
            ),
            "time": SimpleNamespace(sleep=Mock()),
            "wait_for_fabric_lro": Mock(return_value={"status": "Succeeded"}),
            "fabric_get_items": Mock(return_value=[]),
        }

    def test_nb02_create_waits_for_lro_and_reads_authoritative_result(self):
        namespace = self.create_namespace(
            Response(202, {}, {"Location": "https://operation"}),
            {"id": "new-stream"},
        )
        create = load_function("002", "create_eventstream", namespace)

        self.assertEqual(create("workspace", "stream", "token"), "new-stream")
        namespace["wait_for_fabric_lro"].assert_called_once_with("https://operation", "token")
        self.assertEqual(namespace["requests"].get.call_args.args[0], "https://operation/result")
        namespace["fabric_get_items"].assert_not_called()

    def test_nb02_create_rejects_missing_operation_location(self):
        namespace = self.create_namespace(Response(202, {}))
        create = load_function("002", "create_eventstream", namespace)

        with self.assertRaisesRegex(RuntimeError, "202 without an operation URL"):
            create("workspace", "stream", "token")
        namespace["fabric_get_items"].assert_not_called()

    def test_nb02_create_propagates_failed_operation_and_result_errors(self):
        for failure in ("operation", "result"):
            with self.subTest(failure=failure):
                namespace = self.create_namespace(
                    Response(202, {}, {"Location": "https://operation"}),
                )
                if failure == "operation":
                    namespace["wait_for_fabric_lro"].side_effect = RuntimeError("provisioning failed")
                else:
                    namespace["requests"].get.return_value = Response(403, {})
                create = load_function("002", "create_eventstream", namespace)
                with self.assertRaises((RuntimeError, requests.HTTPError)):
                    create("workspace", "stream", "token")
                namespace["fabric_get_items"].assert_not_called()

    def test_nb02_create_preserves_synchronous_success(self):
        namespace = self.create_namespace(Response(201, {"id": "new-stream"}))
        create = load_function("002", "create_eventstream", namespace)

        self.assertEqual(create("workspace", "stream", "token"), "new-stream")
        namespace["wait_for_fabric_lro"].assert_not_called()

    def test_nb02_create_rejects_result_without_item_identity(self):
        namespace = self.create_namespace(Response(202, {}, {"Location": "https://operation"}))
        create = load_function("002", "create_eventstream", namespace)

        with self.assertRaisesRegex(RuntimeError, "without an item ID"):
            create("workspace", "stream", "token")
        namespace["fabric_get_items"].assert_not_called()

    def test_nb02_item_lookup_reads_all_pages_before_type_filtering(self):
        get = Mock(side_effect=[
            Response(200, {"value": [{"id": "lake", "type": "Lakehouse"}],
                           "continuationUri": "https://next-page"}),
            Response(200, {"value": [{"id": "stream", "type": "Eventstream"}]}),
        ])
        namespace = {"FABRIC_BASE_URL": "https://api.fabric.microsoft.com/v1",
                     "requests": SimpleNamespace(get=get)}
        list_items = load_function("002", "fabric_get_items", namespace)

        self.assertEqual(list_items("workspace", "token", "eventstream"),
                         [{"id": "stream", "type": "Eventstream"}])
        self.assertEqual(get.call_args.args[0], "https://next-page")

    def test_nb02_item_lookup_supports_token_only_pagination(self):
        get = Mock(side_effect=[
            Response(200, {"value": [], "continuationToken": "next-token"}),
            Response(200, {"value": [{"id": "stream", "type": "Eventstream"}]}),
        ])
        namespace = {"FABRIC_BASE_URL": "https://api.fabric.microsoft.com/v1",
                     "requests": SimpleNamespace(get=get)}
        list_items = load_function("002", "fabric_get_items", namespace)

        self.assertEqual(len(list_items("workspace", "token")), 1)
        self.assertEqual(get.call_args.kwargs["params"], {"continuationToken": "next-token"})

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
            if number == "002":
                canonical = ast.parse(source(number))
                raw = ast.parse(code)
                for name in ("fabric_get_items", "create_eventstream", "wait_for_fabric_lro"):
                    canonical_node = next(n for n in canonical.body if isinstance(n, ast.FunctionDef) and n.name == name)
                    raw_node = next(n for n in raw.body if isinstance(n, ast.FunctionDef) and n.name == name)
                    self.assertEqual(ast.dump(canonical_node), ast.dump(raw_node), name)
        nb02 = next(RAW_NOTEBOOKS.glob("RTI_002_*.ipynb")).read_text(encoding="utf-8")
        self.assertIn("wait_for_fabric_lro(operation_url, token)", nb02)


if __name__ == "__main__":
    unittest.main()
