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
