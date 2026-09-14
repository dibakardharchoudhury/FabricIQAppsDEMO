import contextlib
import io
import unittest
from unittest.mock import Mock

from sync_workspace_from_git import configure_weather_assets


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class FakeFabric:
    def __init__(self, publish_state="Success"):
        self.publish_state = publish_state
        self.requests = []
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
        ]

    def list_workspace_folders(self, workspace_id):
        return [{"id": "notebooks-folder", "displayName": "Notebooks"}]

    def request(self, method, url):
        self.requests.append((method, url))
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

        self.assertFalse(any(method == "POST" for method, _ in fabric.requests))
        self.assertIn("mai-weather-api-key", output.getvalue())

    def test_git_update_publishes_environment(self):
        fabric = FakeFabric()
        with contextlib.redirect_stdout(io.StringIO()):
            configure_weather_assets(fabric, "workspace-id", git_updated=True)

        posts = [url for method, url in fabric.requests if method == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertIn("/environments/environment-id/staging/publish?beta=false", posts[0])
        fabric.poll_lro.assert_called_once()

    def test_weather_notebooks_must_be_in_notebooks_folder(self):
        fabric = FakeFabric()
        items = fabric.list_workspace_items("workspace-id")
        items[-1]["folderId"] = None
        fabric.list_workspace_items = Mock(return_value=items)

        with self.assertRaisesRegex(SystemExit, "not in workspace folder 'Notebooks'"):
            configure_weather_assets(fabric, "workspace-id", git_updated=False)


if __name__ == "__main__":
    unittest.main()
