import base64
import contextlib
import io
import json
import unittest
from unittest.mock import Mock

from sync_workspace_from_git import configure_weather_assets

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
            *self.lakehouses,
        ]

    def list_workspace_folders(self, workspace_id):
        return [{"id": "notebooks-folder", "displayName": "Notebooks"}]

    def request(self, method, url, **kwargs):
        self.requests.append((method, url))
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
        items[3]["folderId"] = None
        fabric.list_workspace_items = Mock(return_value=items)

        with self.assertRaisesRegex(SystemExit, "not in workspace folder 'Notebooks'"):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

    def test_git_import_unbinding_is_repaired(self):
        fabric = FakeFabric()
        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        self.assertEqual(len(fabric.updates), 3)
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

    def test_missing_lakehouse_defers_binding_to_rti_001(self):
        fabric = FakeFabric(lakehouses=[])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            configure_weather_assets(fabric, "workspace-id", git_updated=False)

        self.assertEqual(fabric.updates, [])
        self.assertIn("RTI_001 will bind the weather notebooks", output.getvalue())


if __name__ == "__main__":
    unittest.main()
