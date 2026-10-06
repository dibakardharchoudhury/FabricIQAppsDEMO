import ast
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import requests


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "Notebooks/RTI_002_Setup_Eventhouse_Only.Notebook/notebook-content.py"


def response(status=200, payload=None, headers=None):
    item = requests.Response()
    item.status_code = status
    item._content = json.dumps(payload or {}).encode()
    item.headers.update(headers or {})
    return item


class EventstreamReadinessTests(unittest.TestCase):
    def setUp(self):
        names = {
            "await_eventstream_operation", "get_custom_endpoint_connection",
            "get_eventstream_definition", "update_eventstream_definition",
        }
        functions = [node for node in ast.parse(NOTEBOOK.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        self.http = Mock()
        self.time = Mock(monotonic=Mock(return_value=0))
        self.namespace = {
            "requests": self.http, "time": self.time, "UUID": UUID, "json": json,
            "base64": base64, "FABRIC_BASE_URL": "https://api.fabric.microsoft.com/v1",
            "get_eventstream_topology": Mock(return_value={"sources": [{"name": "source", "id": "source-id"}]}),
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(NOTEBOOK), "exec"), self.namespace)

    def test_waits_for_operation_completion_before_reading_definition(self):
        operation = "11111111-1111-4111-8111-111111111111"
        accepted = response(202, headers={"x-ms-operation-id": operation})
        self.http.get.side_effect = [
            response(payload={"status": "Running"}), response(payload={"status": "Succeeded"}),
            response(payload={"definition": {"parts": []}}),
        ]
        result = self.namespace["await_eventstream_operation"](accepted, "test-token", True)
        self.assertEqual(result.json(), {"definition": {"parts": []}})
        self.assertEqual(self.http.get.call_args.args[0], f"https://api.fabric.microsoft.com/v1/operations/{operation}/result")
        self.assertEqual(self.time.sleep.call_count, 2)

    def test_terminal_operation_failure_is_not_a_success(self):
        self.http.get.return_value = response(payload={"status": "Failed"})
        with self.assertRaisesRegex(RuntimeError, "Failed"):
            self.namespace["await_eventstream_operation"](
                response(202, headers={"Location": "https://api.fabric.microsoft.com/v1/operations/job"}), "test-token",
            )

    def test_missing_operation_location_stops_without_request(self):
        with self.assertRaisesRegex(RuntimeError, "status URL"):
            self.namespace["await_eventstream_operation"](response(202), "test-token")
        self.http.get.assert_not_called()

    def test_connection_waits_for_initial_404_and_honors_throttling(self):
        self.http.get.side_effect = [
            response(404), response(429, headers={"Retry-After": "20"}),
            response(payload={
                "fullyQualifiedNamespace": "test.servicebus.windows.net", "eventHubName": "events",
                "accessKeys": {"primaryConnectionString": "test-only-connection"},
            }),
        ]
        result = self.namespace["get_custom_endpoint_connection"]("ws", "es", "test-token", "source")
        self.assertEqual(result["entityPath"], "events")
        self.assertEqual([call.args[0] for call in self.time.sleep.call_args_list], [10, 20])

    def test_connection_auth_failure_is_not_retried(self):
        self.http.get.return_value = response(403)
        with self.assertRaises(requests.HTTPError):
            self.namespace["get_custom_endpoint_connection"]("ws", "es", "test-token", "source")
        self.assertEqual(self.http.get.call_count, 1)
        self.time.sleep.assert_not_called()

    def test_readiness_retry_is_bounded(self):
        self.http.get.return_value = response(404)
        with self.assertRaises(TimeoutError):
            self.namespace["get_custom_endpoint_connection"]("ws", "es", "test-token", "source", attempts=3)
        self.assertEqual(self.http.get.call_count, 3)
        self.assertEqual(self.time.sleep.call_count, 2)

    def test_unchanged_eventstream_does_not_restart_provisioning(self):
        definition = {"sources": [{"name": "source"}]}
        self.namespace["get_eventstream_definition"] = Mock(return_value=(definition, None))
        self.namespace["mutate_definition_add_custom_endpoint"] = lambda value: value
        self.namespace["update_eventstream_definition"]("ws", "es", "test-token")
        self.http.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
