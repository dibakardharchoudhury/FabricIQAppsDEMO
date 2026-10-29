import base64
import contextlib
import io
import json
import copy
from pathlib import Path
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import requests

from sync_workspace_from_git import (
    Fabric, configure_weather_assets, configure_weather_schedule,
    ensure_spark_runtime, verify_environment_runtimes,
    bind_notebook_definition, notebook_definition, update_notebooks_from_checkout,
    _normalized_notebook_source,
)

LAKEHOUSE_ID = "lakehouse-id"
LAKEHOUSE_NAME = "Energy_IQ_LakehouseRTI_V6"


def ipynb_definition(dependencies):
    payload = json.dumps({"metadata": {"dependencies": dependencies}}).encode("utf-8")
    return {
        "definition": {
            "parts": [
                {
                    "path": "notebook-content.ipynb",
                    "payload": base64.b64encode(payload).decode("ascii"),
                    "payloadType": "InlineBase64",
                }
            ]
        }
    }


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._body


class FakeFabric:
    def __init__(self, publish_state="Success", dependencies=None, lakehouses=None):
        self.publish_state = publish_state
        self.dependencies = dependencies if dependencies is not None else {}
        self.lakehouses = (
            lakehouses
            if lakehouses is not None
            else [{"id": LAKEHOUSE_ID, "type": "Lakehouse", "displayName": LAKEHOUSE_NAME}]
        )
        self.requests = []
        self.request_kwargs = []
        self.updates = []
        self.poll_lro = Mock(side_effect=lambda response: response)

    def list_workspace_items(self, workspace_id):
        return [
            {
                "id": "environment-id",
                "type": "Environment",
                "displayName": "Weather",
            },
            {
                "id": "weather-001",
                "type": "Notebook",
                "displayName": "Weather_001_create_lakehouse",
                "folderId": "notebooks-folder",
            },
            {
                "id": "weather-002",
                "type": "Notebook",
                "displayName": "Weather_002_fetch_area_weather",
                "folderId": "notebooks-folder",
            },
            {
                "id": "weather-003",
                "type": "Notebook",
                "displayName": "Weather_003_fetch_ukmet",
                "folderId": "notebooks-folder",
            },
            {
                "id": "weather-020",
                "type": "Notebook",
                "displayName": "Weather_020_area_calculations",
                "folderId": "notebooks-folder",
            },
            {
                "id": "weather-pipeline",
                "type": "DataPipeline",
                "displayName": "03_Pipe_Weather",
            },
            {
                "id": "weather-orchestrator",
                "type": "Notebook",
                "displayName": "Weather_Orchestrator",
                "folderId": "notebooks-folder",
            },
            *self.lakehouses,
        ]

    def list_workspace_folders(self, workspace_id):
        return [{"id": "notebooks-folder", "displayName": "Notebooks"}]

    def request(self, method, url, **kwargs):
        self.requests.append((method, url))
        self.request_kwargs.append(kwargs)
        if url.endswith("/spark/settings"):
            return FakeResponse(200, {"environment": {"runtimeVersion": "2.0"}})
        if "/items?type=Environment" in url:
            return FakeResponse(200, {"value": [{"id": "environment-id", "displayName": "Weather"}]})
        if "/sparkcompute?beta=false" in url:
            return FakeResponse(200, {"runtimeVersion": "2.0"})
        if "/jobs/Pipeline/schedules" in url and method == "GET":
            return FakeResponse(200, {"value": []})
        if "/getDefinition" in url:
            return FakeResponse(200, ipynb_definition(self.dependencies))
        if "/updateDefinition" in url:
            self.updates.append((url, kwargs["json"]))
            return FakeResponse(200)
        if method == "POST":
            return FakeResponse(200)
        return FakeResponse(
            200,
            {"properties": {"publishDetails": {"state": self.publish_state}}},
        )


class TargetedNotebookSyncTests(unittest.TestCase):
    def test_markdown_normalization_does_not_change_executable_cells(self):
        source = '# CELL ********************\n\nvalue = """\n#\n# text\n"""\n'
        self.assertEqual(_normalized_notebook_source(source), source)
        self.assertNotEqual(
            _normalized_notebook_source(source),
            _normalized_notebook_source(source.replace("#\n# ", "# # ")),
        )

    def test_fabric_markdown_serialization_is_accepted_and_not_uploaded_again(self):
        name = "Geo_001_ingest_energy_context"
        source = (Path(__file__).resolve().parents[2] / "Notebooks" / f"{name}.Notebook" /
                  "notebook-content.py").read_text(encoding="utf-8")
        original = {"parts": [{"path": "notebook-content.py", "payload": base64.b64encode(
            (source + "\nold_code = True\n").encode()).decode(), "payloadType": "InlineBase64"}]}
        bind_notebook_definition(original, {"default_lakehouse": "existing-lakehouse"}, {})
        state = copy.deepcopy(original)
        fab = Mock()
        fab.list_workspace_items.return_value = [{"id": "notebook", "type": "Notebook", "displayName": name}]

        def update(method, url, **kwargs):
            state.clear()
            state.update(copy.deepcopy(kwargs["json"]["definition"]))
            submitted = base64.b64decode(state["parts"][0]["payload"]).decode()
            saved = submitted.replace("#\n# ", "# # ").replace("\n", "\r\n")
            state["parts"][0]["payload"] = base64.b64encode(saved.encode()).decode()
            return FakeResponse(200)

        fab.request.side_effect = update
        with patch("sync_workspace_from_git.notebook_definition", side_effect=lambda *args: copy.deepcopy(state)):
            update_notebooks_from_checkout(fab, "workspace", [name])
            update_notebooks_from_checkout(fab, "workspace", [name])
        self.assertEqual(fab.request.call_count, 1)

    def test_readback_rejects_changed_code_and_bindings(self):
        name = "Geo_002_publish_map_agent"
        for before_text, after_text in (
            ("def publish_map_agent(api, models):", "def publish_map_agent(api, wrong):"),
            ("existing-lakehouse", "different-lakehouse"),
        ):
            with self.subTest(changed=before_text):
                original = {"parts": [{"path": "notebook-content.py", "payload": base64.b64encode(
                    b"# METADATA ********************\n\n# META {}\n\nold = True\n").decode()}]}
                bind_notebook_definition(original, {"default_lakehouse": "existing-lakehouse"}, {})
                state = copy.deepcopy(original)
                fab = Mock()
                fab.list_workspace_items.return_value = [{"id": "notebook", "type": "Notebook", "displayName": name}]

                def update(method, url, **kwargs):
                    state.clear()
                    state.update(copy.deepcopy(kwargs["json"]["definition"]))
                    submitted = base64.b64decode(state["parts"][0]["payload"]).decode()
                    self.assertIn(before_text, submitted)
                    state["parts"][0]["payload"] = base64.b64encode(
                        submitted.replace(before_text, after_text).encode()).decode()
                    return FakeResponse(200)

                fab.request.side_effect = update
                with patch("sync_workspace_from_git.notebook_definition", side_effect=lambda *args: copy.deepcopy(state)):
                    with self.assertRaisesRegex(RuntimeError, "readback failed"):
                        update_notebooks_from_checkout(fab, "workspace", [name])

    def test_only_changed_code_is_written_and_live_bindings_and_other_parts_survive(self):
        name = "Geo_002_publish_map_agent"
        source = (Path(__file__).resolve().parents[2] / "Notebooks" / f"{name}.Notebook" /
                  "notebook-content.py").read_text(encoding="utf-8")
        original = {"parts": [
            {"path": "notebook-content.py", "payload": base64.b64encode(source.encode()).decode(),
             "payloadType": "InlineBase64"},
            {"path": "custom.json", "payload": base64.b64encode(b'{"keep":true}').decode(),
             "payloadType": "InlineBase64"},
        ]}
        bind_notebook_definition(original, {"default_lakehouse": "existing-lakehouse"}, {"environmentId": "existing-env"})
        old_source = base64.b64decode(original["parts"][0]["payload"]).decode().replace(
            "def publish_map_agent(api, models):", "def publish_map_agent(api, models, old_canary):")
        original["parts"][0]["payload"] = base64.b64encode(old_source.encode()).decode()
        state = copy.deepcopy(original)
        fab = Mock()
        fab.list_workspace_items.return_value = [{"id": "notebook", "type": "Notebook", "displayName": name}]
        def update(method, url, **kwargs):
            self.assertEqual(method, "POST")
            self.assertIn("/notebooks/notebook/updateDefinition", url)
            state.clear()
            state.update(copy.deepcopy(kwargs["json"]["definition"]))
            return FakeResponse(200)
        fab.request.side_effect = update
        with patch("sync_workspace_from_git.notebook_definition", side_effect=lambda *args: copy.deepcopy(state)):
            update_notebooks_from_checkout(fab, "workspace", [name])
            self.assertEqual(fab.request.call_count, 1)
            updated = base64.b64decode(state["parts"][0]["payload"]).decode()
            self.assertIn("existing-lakehouse", updated)
            self.assertIn("existing-env", updated)
            self.assertIn("def publish_map_agent(api, models):", updated)
            self.assertEqual(state["parts"][1], original["parts"][1])
            update_notebooks_from_checkout(fab, "workspace", [name])
        self.assertEqual(fab.request.call_count, 1)

    def test_readback_failure_is_not_reported_as_success(self):
        fab = Mock()
        name = "Geo_002_publish_map_agent"
        fab.list_workspace_items.return_value = [{"id": "notebook", "type": "Notebook", "displayName": name}]
        old_source = "# METADATA ********************\n\n# META {}\n\nold = True\n"
        original = {"parts": [{"path": "notebook-content.py", "payload": base64.b64encode(old_source.encode()).decode()}]}
        fab.request.return_value = FakeResponse(200)
        with patch("sync_workspace_from_git.notebook_definition", side_effect=lambda *args: copy.deepcopy(original)):
            with self.assertRaisesRegex(RuntimeError, "readback failed"):
                update_notebooks_from_checkout(fab, "workspace", [name])


class SparkRuntimeTests(unittest.TestCase):
    def test_existing_runtime_two_requires_no_write(self):
        request = Mock(return_value=FakeResponse(200, {"environment": {"runtimeVersion": "2.0"}}))
        ensure_spark_runtime(request, "workspace-id")
        request.assert_called_once_with(
            "GET", "https://api.fabric.microsoft.com/v1/workspaces/workspace-id/spark/settings"
        )

    def test_upgrade_preserves_other_settings_and_verifies_readback(self):
        request = Mock(side_effect=[
            FakeResponse(200, {"environment": {"runtimeVersion": "1.3", "name": ""},
                               "pool": {"customizeComputeEnabled": True}}),
            FakeResponse(200),
            FakeResponse(200, {"environment": {"runtimeVersion": "2.0"}}),
        ])
        ensure_spark_runtime(request, "workspace-id")
        self.assertEqual([call.args[0] for call in request.call_args_list], ["GET", "PATCH", "GET"])
        self.assertEqual(request.call_args_list[1].kwargs["json"],
                         {"environment": {"runtimeVersion": "2.0"}})

    def test_missing_or_malformed_runtime_fails_without_writing(self):
        for body in ({}, {"environment": []}, {"environment": {"runtimeVersion": 2}}):
            with self.subTest(body=body):
                request = Mock(return_value=FakeResponse(200, body))
                with self.assertRaisesRegex(SystemExit, "missing environment.runtimeVersion"):
                    ensure_spark_runtime(request, "workspace-id")
                self.assertEqual(request.call_count, 1)

    def test_runtime_read_or_update_failure_propagates(self):
        for responses in (
            [FakeResponse(403, text="forbidden")],
            [FakeResponse(200, {"environment": {"runtimeVersion": "1.3"}}),
             FakeResponse(400, text="invalid settings")],
            [FakeResponse(200, {"environment": {"runtimeVersion": "1.3"}}),
             FakeResponse(200), FakeResponse(503, text="unavailable")],
        ):
            with self.subTest(responses=responses):
                with self.assertRaisesRegex(SystemExit, "failed"):
                    ensure_spark_runtime(Mock(side_effect=responses), "workspace-id")

    def test_success_response_with_old_readback_is_rejected(self):
        request = Mock(side_effect=[
            FakeResponse(200, {"environment": {"runtimeVersion": "1.3"}}),
            FakeResponse(200),
            FakeResponse(200, {"environment": {"runtimeVersion": "1.3"}}),
        ])
        with self.assertRaisesRegex(SystemExit, "readback failed"):
            ensure_spark_runtime(request, "workspace-id")

    def test_all_environment_pages_must_publish_runtime_two(self):
        request = Mock(side_effect=[
            FakeResponse(200, {"value": [{"id": "weather"}],
                               "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces/workspace-id/items?continuationToken=next"}),
            FakeResponse(200, {"runtimeVersion": "2.0"}),
            FakeResponse(200, {"value": [{"id": "other"}]}),
            FakeResponse(200, {"runtimeVersion": "1.3"}),
        ])
        with self.assertRaisesRegex(SystemExit, "other requires published Runtime 2.0"):
            verify_environment_runtimes(request, "workspace-id")
        self.assertEqual(request.call_count, 4)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_environment_inventory_and_compute_failures_are_not_suppressed(self):
        for responses in (
            [FakeResponse(403, text="forbidden")],
            [FakeResponse(200, {"wrong": []})],
            [FakeResponse(200, {"value": [{}]})],
            [FakeResponse(200, {"value": [{"id": "weather"}]}), FakeResponse(503, text="unavailable")],
            [FakeResponse(200, {"value": [{"id": "weather"}]}), FakeResponse(200, {})],
            [FakeResponse(200, {"value": [], "continuationUri": "https://other.invalid/items"})],
        ):
            with self.subTest(responses=responses), self.assertRaises(SystemExit):
                verify_environment_runtimes(Mock(side_effect=responses), "workspace-id")


class FabricRequestTests(unittest.TestCase):
    def test_get_retries_transient_connection_reset(self):
        fabric = Fabric.__new__(Fabric)
        fabric._session = Mock()
        fabric._token = Mock(return_value="token")
        response = FakeResponse(200, {"status": "Succeeded"})
        fabric._session.request.side_effect = [
            requests.ConnectionError("connection reset"),
            response,
        ]

        with patch("sync_workspace_from_git.time.sleep") as sleep:
            actual = fabric.request("GET", "https://example.test/operation")

        self.assertIs(actual, response)
        self.assertEqual(fabric._session.request.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_post_does_not_retry_ambiguous_transport_failure(self):
        fabric = Fabric.__new__(Fabric)
        fabric._session = Mock()
        fabric._token = Mock(return_value="token")
        fabric._session.request.side_effect = requests.ConnectionError("connection reset")

        with (
            patch("sync_workspace_from_git.time.sleep") as sleep,
            self.assertRaises(requests.ConnectionError),
        ):
            fabric.request("POST", "https://example.test/update", json={"value": 1})

        self.assertEqual(fabric._session.request.call_count, 1)
        sleep.assert_not_called()


class WeatherProvisioningTests(unittest.TestCase):
    def test_native_source_binding_avoids_python_to_ipynb_conversion(self):
        code = '# Fabric notebook source\n\n# METADATA ********************\n\n# META {\n# META   "dependencies": {}\n# META }\n\n# CELL ********************\nprint("unchanged")\n'
        part = {"path": "notebook-content.py", "payload": base64.b64encode(code.encode()).decode(), "payloadType": "InlineBase64"}
        definition = {"parts": [part]}
        lakehouse = {"default_lakehouse": "target-id"}
        environment = {"environmentId": "target-env"}
        self.assertTrue(bind_notebook_definition(definition, lakehouse, environment))
        self.assertEqual(definition["format"], "fabricGitSource")
        source = base64.b64decode(part["payload"]).decode()
        self.assertIn('"default_lakehouse": "target-id"', source)
        self.assertIn('print("unchanged")', source)
        self.assertFalse(bind_notebook_definition(definition, lakehouse, environment))

    def test_notebook_result_uses_the_same_canonical_operation_as_polling(self):
        operation_id = "11111111-1111-4111-8111-111111111111"
        pending = FakeResponse(202)
        pending.headers = {"Location": "https://backend.example.invalid/operation", "x-ms-operation-id": operation_id}
        definition = ipynb_definition({})
        fabric = Mock()
        fabric.request.side_effect = [pending, FakeResponse(200, definition)]
        self.assertEqual(notebook_definition(fabric, "workspace", "notebook"), definition["definition"])
        self.assertEqual(fabric.request.call_args.args, (
            "GET", f"https://api.fabric.microsoft.com/v1/operations/{operation_id}/result",
        ))

    def test_weather_schedule_is_created_every_six_hours(self):
        fabric = FakeFabric()

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(fabric, "workspace-id", "weather-pipeline")

        post_indexes = [
            index
            for index, (method, url) in enumerate(fabric.requests)
            if method == "POST" and "/jobs/Pipeline/schedules" in url
        ]
        self.assertEqual(len(post_indexes), 1)
        body = fabric.request_kwargs[post_indexes[0]]["json"]
        self.assertTrue(body["enabled"])
        self.assertEqual(body["configuration"]["type"], "Cron")
        self.assertEqual(body["configuration"]["interval"], 360)

    def test_weather_schedule_starts_offset_from_a_model_run(self):
        fabric = FakeFabric()

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(fabric, "workspace-id", "weather-pipeline")

        post_index = next(
            index
            for index, (method, url) in enumerate(fabric.requests)
            if method == "POST" and "/jobs/Pipeline/schedules" in url
        )
        start = datetime.fromisoformat(
            fabric.request_kwargs[post_index]["json"]["configuration"]["startDateTime"]
            .replace("Z", "+00:00")
        )
        minutes_past_midnight = start.hour * 60 + start.minute
        self.assertEqual(minutes_past_midnight % 360, 200)

    def test_matching_weather_schedule_is_reused(self):
        fabric = FakeFabric()
        matching = FakeResponse(
            200,
            {"value": [{"id": "schedule-id", "enabled": True,
                        "configuration": {
                            "type": "Cron",
                            "interval": 360,
                            "localTimeZoneId": "UTC",
                            "startDateTime": "2026-09-16T03:20:00Z",
                            "endDateTime": "2036-09-16T03:20:00Z",
                        }}]},
        )
        original_request = fabric.request
        fabric.request = Mock(
            side_effect=lambda method, url, **kwargs: matching
            if method == "GET" and "/jobs/Pipeline/schedules" in url
            else original_request(method, url, **kwargs)
        )

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(fabric, "workspace-id", "weather-pipeline")

        self.assertEqual(fabric.request.call_count, 1)

    def test_enabled_schedule_is_disabled_when_bindings_are_not_ready(self):
        fabric = FakeFabric()
        existing = FakeResponse(
            200,
            {"value": [{"id": "schedule-id", "enabled": True,
                        "configuration": {
                            "type": "Cron",
                            "interval": 360,
                            "localTimeZoneId": "UTC",
                            "startDateTime": "2026-09-16T03:20:00Z",
                            "endDateTime": "2036-09-16T03:20:00Z",
                        }}]},
        )
        original_request = fabric.request
        fabric.request = Mock(
            side_effect=lambda method, url, **kwargs: existing
            if method == "GET" and "/jobs/Pipeline/schedules" in url
            else original_request(method, url, **kwargs)
        )

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(
                fabric, "workspace-id", "weather-pipeline", enabled=False
            )

        patch_calls = [
            call for call in fabric.request.call_args_list
            if call.args[0] == "PATCH" and "/jobs/Pipeline/schedules/" in call.args[1]
        ]
        self.assertEqual(len(patch_calls), 1)
        self.assertFalse(patch_calls[0].kwargs["json"]["enabled"])

    def test_provisioning_keeps_schedule_disabled_when_bindings_are_ready(self):
        fabric = FakeFabric()

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        schedule_posts = [
            kwargs for (method, url), kwargs in zip(fabric.requests, fabric.request_kwargs)
            if method == "POST" and "/jobs/Pipeline/schedules" in url
        ]
        self.assertEqual(len(schedule_posts), 1)
        self.assertFalse(schedule_posts[0]["json"]["enabled"])

    def test_wrong_offset_weather_schedule_is_updated(self):
        fabric = FakeFabric()
        existing = FakeResponse(
            200,
            {"value": [{"id": "schedule-id", "enabled": True,
                        "configuration": {
                            "type": "Cron",
                            "interval": 360,
                            "localTimeZoneId": "UTC",
                            "startDateTime": "2026-09-16T03:00:00Z",
                        }}]},
        )
        original_request = fabric.request
        fabric.request = Mock(
            side_effect=lambda method, url, **kwargs: existing
            if method == "GET" and "/jobs/Pipeline/schedules" in url
            else original_request(method, url, **kwargs)
        )

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(fabric, "workspace-id", "weather-pipeline")

        patch_calls = [
            call for call in fabric.request.call_args_list
            if call.args[0] == "PATCH" and "/jobs/Pipeline/schedules/" in call.args[1]
        ]
        self.assertEqual(len(patch_calls), 1)

    def test_wrong_timezone_weather_schedule_is_updated(self):
        fabric = FakeFabric()
        existing = FakeResponse(
            200,
            {"value": [{"id": "schedule-id", "enabled": True,
                        "configuration": {
                            "type": "Cron",
                            "interval": 360,
                            "localTimeZoneId": "Europe/Oslo",
                            "startDateTime": "2026-09-16T03:20:00Z",
                            "endDateTime": "2036-09-16T03:20:00Z",
                        }}]},
        )
        original_request = fabric.request
        fabric.request = Mock(
            side_effect=lambda method, url, **kwargs: existing
            if method == "GET" and "/jobs/Pipeline/schedules" in url
            else original_request(method, url, **kwargs)
        )

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(fabric, "workspace-id", "weather-pipeline")

        patch_calls = [
            call for call in fabric.request.call_args_list
            if call.args[0] == "PATCH" and "/jobs/Pipeline/schedules/" in call.args[1]
        ]
        self.assertEqual(len(patch_calls), 1)

    def test_matching_schedule_is_retained_and_stale_duplicate_is_deleted(self):
        fabric = FakeFabric()
        existing = FakeResponse(200, {"value": [
            {"id": "valid", "enabled": True, "configuration": {
                "type": "Cron", "interval": 360, "localTimeZoneId": "UTC",
                "startDateTime": "2026-09-16T03:20:00Z",
                            "endDateTime": "2036-09-16T03:20:00Z",
            }},
            {"id": "stale", "enabled": True, "configuration": {
                "type": "Cron", "interval": 240, "localTimeZoneId": "UTC",
                "startDateTime": "2026-09-16T03:00:00Z",
            }},
        ]})
        original_request = fabric.request
        fabric.request = Mock(side_effect=lambda method, url, **kwargs: existing
            if method == "GET" and "/jobs/Pipeline/schedules" in url
            else original_request(method, url, **kwargs))

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(fabric, "workspace-id", "weather-pipeline")

        writes = [(call.args[0], call.args[1]) for call in fabric.request.call_args_list if call.args[0] != "GET"]
        self.assertEqual([method for method, _ in writes], ["DELETE"])
        self.assertTrue(writes[0][1].endswith("/stale"))

    def test_multiple_stale_schedules_repairs_one_and_deletes_extras(self):
        fabric = FakeFabric()
        existing = FakeResponse(200, {"value": [
            {"id": "keep", "enabled": False, "configuration": {}},
            {"id": "extra-one", "enabled": True, "configuration": {"type": "Cron", "interval": 240}},
            {"id": "extra-two", "enabled": True, "configuration": {"type": "Cron", "interval": 120}},
        ]})
        original_request = fabric.request
        fabric.request = Mock(side_effect=lambda method, url, **kwargs: existing
            if method == "GET" and "/jobs/Pipeline/schedules" in url
            else original_request(method, url, **kwargs))

        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_schedule(fabric, "workspace-id", "weather-pipeline")

        writes = [(call.args[0], call.args[1]) for call in fabric.request.call_args_list if call.args[0] != "GET"]
        self.assertEqual([method for method, _ in writes], ["PATCH", "DELETE", "DELETE"])
        self.assertTrue(writes[0][1].endswith("/keep"))
        self.assertEqual({url.rsplit("/", 1)[-1] for _, url in writes[1:]}, {"extra-one", "extra-two"})

    def test_already_published_environment_is_not_republished_without_git_update(self):
        fabric = FakeFabric()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        self.assertFalse(
            any(method == "POST" and "/staging/publish" in url for method, url in fabric.requests)
        )
        self.assertIn("mai-weather-api-key", output.getvalue())
        self.assertIn("ukmet-global-spot-api-key", output.getvalue())
        self.assertIn("ukmet-land-observations-api-key", output.getvalue())

    def test_git_update_publishes_environment(self):
        fabric = FakeFabric()
        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=True)

        posts = [
            url
            for method, url in fabric.requests
            if method == "POST" and "/staging/publish" in url
        ]
        self.assertEqual(len(posts), 1)
        self.assertIn("/environments/environment-id/staging/publish?beta=false", posts[0])

    def test_weather_notebooks_must_be_in_notebooks_folder(self):
        fabric = FakeFabric()
        items = fabric.list_workspace_items("workspace-id")
        items[4]["folderId"] = None
        fabric.list_workspace_items = Mock(return_value=items)

        with self.assertRaisesRegex(SystemExit, "not in workspace folder 'Notebooks'"):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

    def test_git_import_unbinding_is_repaired(self):
        fabric = FakeFabric()
        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        self.assertEqual(len(fabric.updates), 5)
        for _, body in fabric.updates:
            self.assertEqual(body["definition"]["format"], "ipynb")
            part = body["definition"]["parts"][0]
            content = json.loads(base64.b64decode(part["payload"]))
            dependencies = content["metadata"]["dependencies"]
            self.assertEqual(dependencies["lakehouse"]["default_lakehouse"], LAKEHOUSE_ID)
            self.assertEqual(dependencies["lakehouse"]["default_lakehouse_name"], LAKEHOUSE_NAME)
            self.assertEqual(dependencies["environment"]["environmentId"], "environment-id")

    def test_rebinding_preserves_notebook_cells_and_unrelated_metadata(self):
        fabric = FakeFabric()
        content = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "cells": [{"cell_type": "code", "source": ["print('preserve me')"],
                       "metadata": {}, "execution_count": None, "outputs": []}],
            "metadata": {"custom": "preserved", "dependencies": {"custom": {"id": "keep"}}},
        }
        platform = {"path": ".platform", "payload": "e30=", "payloadType": "InlineBase64"}
        original_request = fabric.request

        def request(method, url, **kwargs):
            if "/getDefinition" in url:
                definition = ipynb_definition({})
                parts = definition["definition"]["parts"]
                parts[0]["payload"] = base64.b64encode(json.dumps(content).encode()).decode()
                parts.append(dict(platform))
                return FakeResponse(200, definition)
            return original_request(method, url, **kwargs)

        fabric.request = request
        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        self.assertEqual(len(fabric.updates), 5)
        for _, body in fabric.updates:
            self.assertEqual(body["definition"]["format"], "ipynb")
            parts = body["definition"]["parts"]
            updated = json.loads(base64.b64decode(parts[0]["payload"]))
            self.assertEqual(updated["cells"], content["cells"])
            self.assertEqual(updated["nbformat"], content["nbformat"])
            self.assertEqual(updated["nbformat_minor"], content["nbformat_minor"])
            self.assertEqual(updated["metadata"]["custom"], "preserved")
            self.assertEqual(updated["metadata"]["dependencies"]["custom"], {"id": "keep"})
            self.assertEqual(parts[1], platform)

    def test_notebooks_already_bound_are_not_rewritten(self):
        bound = {
            "lakehouse": {
                "default_lakehouse": LAKEHOUSE_ID,
                "default_lakehouse_name": LAKEHOUSE_NAME,
                "default_lakehouse_workspace_id": "workspace-id",
                "known_lakehouses": [{"id": LAKEHOUSE_ID}],
            },
            "environment": {"environmentId": "environment-id", "workspaceId": "workspace-id"},
        }
        fabric = FakeFabric(dependencies=bound)
        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        self.assertEqual(fabric.updates, [])

    def test_schedule_is_configured_after_notebook_dependencies(self):
        fabric = FakeFabric()
        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=True)

        schedule_index = next(
            index for index, (method, url) in enumerate(fabric.requests)
            if method == "POST" and "/jobs/Pipeline/schedules" in url
        )
        dependency_indexes = [
            index for index, (method, url) in enumerate(fabric.requests)
            if method == "POST" and ("/staging/publish" in url or "/updateDefinition" in url)
        ]
        self.assertTrue(dependency_indexes)
        self.assertGreater(schedule_index, max(dependency_indexes))

    def test_missing_lakehouse_defers_binding_to_rti_001(self):
        fabric = FakeFabric(lakehouses=[])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        self.assertEqual(fabric.updates, [])
        self.assertIn("RTI_001 will bind the weather notebooks", output.getvalue())
        schedule_posts = [
            index for index, (method, url) in enumerate(fabric.requests)
            if method == "POST" and "/jobs/Pipeline/schedules" in url
        ]
        self.assertEqual(len(schedule_posts), 1)
        self.assertFalse(fabric.request_kwargs[schedule_posts[0]]["json"]["enabled"])

    def test_duplicate_weather_notebooks_are_rejected(self):
        fabric = FakeFabric()
        items = fabric.list_workspace_items("workspace-id")
        items.append({**items[1], "id": "weather-001-duplicate"})
        fabric.list_workspace_items = Mock(return_value=items)

        with self.assertRaisesRegex(SystemExit, "duplicate notebook.*Weather_001_create_lakehouse"):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)


if __name__ == "__main__":
    unittest.main()
