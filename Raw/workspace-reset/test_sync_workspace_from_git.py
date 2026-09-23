import base64
import contextlib
import io
import json
import unittest
from datetime import datetime
from unittest.mock import Mock

from sync_workspace_from_git import configure_weather_assets, configure_weather_schedule, notebook_definition

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
            *self.lakehouses,
        ]

    def list_workspace_folders(self, workspace_id):
        return [{"id": "notebooks-folder", "displayName": "Notebooks"}]

    def request(self, method, url, **kwargs):
        self.requests.append((method, url))
        self.request_kwargs.append(kwargs)
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


class WeatherProvisioningTests(unittest.TestCase):
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

        self.assertEqual(len(fabric.updates), 4)
        for _, body in fabric.updates:
            part = body["definition"]["parts"][0]
            content = json.loads(base64.b64decode(part["payload"]))
            dependencies = content["metadata"]["dependencies"]
            self.assertEqual(dependencies["lakehouse"]["default_lakehouse"], LAKEHOUSE_ID)
            self.assertEqual(dependencies["lakehouse"]["default_lakehouse_name"], LAKEHOUSE_NAME)
            self.assertEqual(dependencies["environment"]["environmentId"], "environment-id")

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
