import base64
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("provision_foundry_agents", Path(__file__).with_name("provision_foundry_agents.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def definition(stage="published", workspace="workspace", ontology="ontology"):
    return {"definition": {"parts": [{
        "path": f"Files/Config/{stage}/ontology-test/datasource.json",
        "payload": base64.b64encode(json.dumps({
            "type": "ontology", "workspaceId": workspace, "artifactId": ontology,
        }).encode()).decode(),
    }]}}


class PublishedIdentityTests(unittest.TestCase):
    def test_linked_insights_identity_is_required_and_never_inferred_by_name(self):
        resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/demo/providers/Microsoft.Insights/components/demo"
        connection = {"properties": {"category": "AppInsights", "target": resource_id}}
        self.assertEqual(module.linked_application_insights([connection]), resource_id)
        for invalid in [
            [],
            [{"properties": {"category": "RemoteTool", "target": resource_id}}],
            [{"properties": {"category": "AppInsights", "target": "https://untrusted.example"}}],
            [connection, {"properties": {"category": "AppInsights", "target": resource_id + "-other"}}],
        ]:
            with self.assertRaises(RuntimeError):
                module.linked_application_insights(invalid)

    def test_regional_definition_operations_are_polled_on_canonical_origin(self):
        path = "/v1/operations/46823fb9-2ddd-4c7b-8089-068435f979f0"
        for host in ["api.fabric.microsoft.com", "wabi-us-central-b-primary-redirect.analysis.windows.net"]:
            self.assertEqual(module.definition_operation_url("https://" + host + path), "https://api.fabric.microsoft.com" + path)
        for invalid in ["https://example.org" + path, "http://api.fabric.microsoft.com" + path,
                        "https://api.fabric.microsoft.com/not-an-operation"]:
            with self.assertRaises(RuntimeError):
                module.definition_operation_url(invalid)

    def test_exact_published_v2_identity_required(self):
        module.verify_published_identity(definition(), "workspace", "ontology")
        for invalid in [definition(stage="draft"), definition(workspace="other"), definition(ontology="other")]:
            with self.assertRaises(RuntimeError):
                module.verify_published_identity(invalid, "workspace", "ontology")

    def test_malformed_source_is_not_ignored(self):
        malformed = definition()
        malformed["definition"]["parts"][0]["payload"] = "not-base64"
        with self.assertRaises(ValueError):
            module.verify_published_identity(malformed, "workspace", "ontology")


if __name__ == "__main__":
    unittest.main()
