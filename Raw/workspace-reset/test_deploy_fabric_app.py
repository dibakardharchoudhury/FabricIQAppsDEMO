import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from deploy_fabric_app import (
    DeployError,
    UI_PAGE_NAMES,
    validate_hosted_ui_versions,
    validate_versioned_ui_sources,
)


class VersionedUiSourceTests(unittest.TestCase):
    def create_app(self, root: Path) -> Path:
        app_dir = root / "HydroOperationsApp"
        required = [
            "src/main.tsx",
            "src/AppV1.tsx",
            "src/AppV2.tsx",
            "src/ui-v1/components/V1Shell.tsx",
            "src/ui-v1/navigation.tsx",
            "src/ui-v2/components/V2Shell.tsx",
            "src/ui-v2/navigation.tsx",
        ]
        required.extend(
            f"src/{version}/pages/{name}"
            for version in ("ui-v1", "ui-v2")
            for name in UI_PAGE_NAMES
        )
        for relative_path in required:
            path = app_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("export {}\n", encoding="utf-8")
        (app_dir / "src/main.tsx").write_text(
            "import AppV1 from './AppV1.tsx'\n"
            "import AppV2 from './AppV2.tsx'\n"
            "const app = ui === 'v2' ? <AppV2 /> : <AppV1 />\n",
            encoding="utf-8",
        )
        return app_dir

    def test_accepts_distinct_v1_and_v2_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            validate_versioned_ui_sources(self.create_app(Path(directory)))

    def test_rejects_missing_versioned_page(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = self.create_app(Path(directory))
            (app_dir / "src/ui-v1/pages/OverviewPage.tsx").unlink()
            with self.assertRaisesRegex(DeployError, "ui-v1.*OverviewPage"):
                validate_versioned_ui_sources(app_dir)

    def test_rejects_collapsed_v2_only_route(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = self.create_app(Path(directory))
            (app_dir / "src/main.tsx").write_text(
                "import AppV2 from './AppV2.tsx'\nconst app = <AppV2 />\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DeployError, "must route ui=v2"):
                validate_versioned_ui_sources(app_dir)

    @patch("deploy_fabric_app.requests.get")
    def test_checks_both_hosted_ui_routes(self, get):
        get.return_value = SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='<div id="root"></div>',
        )

        validate_hosted_ui_versions("https://hydro.webapp.fabricapps.net")

        self.assertEqual(
            get.call_args_list,
            [
                call("https://hydro.webapp.fabricapps.net/?ui=v1", timeout=60),
                call("https://hydro.webapp.fabricapps.net/?ui=v2", timeout=60),
            ],
        )

    @patch("deploy_fabric_app.requests.get")
    def test_rejects_broken_hosted_v2_route(self, get):
        get.side_effect = [
            SimpleNamespace(
                status_code=200,
                headers={"Content-Type": "text/html"},
                text='<div id="root"></div>',
            ),
            SimpleNamespace(status_code=404, headers={"Content-Type": "text/html"}, text=""),
        ]

        with self.assertRaisesRegex(DeployError, r"\?ui=v2"):
            validate_hosted_ui_versions("https://hydro.webapp.fabricapps.net")


if __name__ == "__main__":
    unittest.main()