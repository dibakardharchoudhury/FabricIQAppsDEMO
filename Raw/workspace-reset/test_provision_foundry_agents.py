import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

import requests

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
    def test_transient_get_failures_retry_with_bounded_backoff(self):
        response = Mock(status_code=200)
        with patch.object(module.requests, "request", side_effect=[
            requests.ConnectionError("DNS unavailable"), requests.Timeout("read timed out"), response,
        ]) as request, patch.object(module.time, "sleep") as sleep:
            self.assertIs(module.request_with_read_retry("GET", "https://example.test/agents", timeout=60), response)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(sleep.call_args_list, [call(2), call(4)])

    def test_exhausted_read_failure_propagates(self):
        error = requests.ConnectionError("DNS unavailable")
        with patch.object(module.requests, "request", side_effect=error) as request, patch.object(module.time, "sleep") as sleep:
            with self.assertRaises(requests.ConnectionError) as caught:
                module.request_with_read_retry("GET", "https://example.test/agents", timeout=60)
            self.assertIs(caught.exception, error)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(sleep.call_args_list, [call(2), call(4), call(8)])

    def test_mutations_and_certificate_failures_are_never_retried(self):
        for method, error in [
            ("POST", requests.ConnectionError("unknown write outcome")),
            ("PUT", requests.Timeout("unknown write outcome")),
            ("GET", requests.exceptions.SSLError("invalid certificate")),
        ]:
            with self.subTest(method=method, error=type(error).__name__):
                with patch.object(module.requests, "request", side_effect=error) as request, patch.object(module.time, "sleep") as sleep:
                    with self.assertRaises(type(error)):
                        module.request_with_read_retry(method, "https://example.test/agents", timeout=60)
                    self.assertEqual(request.call_count, 1)
                    sleep.assert_not_called()

    def test_http_errors_are_not_retried_or_hidden(self):
        for status in (401, 403, 404, 429, 500):
            response = Mock(status_code=status)
            with patch.object(module.requests, "request", return_value=response) as request, patch.object(module.time, "sleep") as sleep:
                self.assertIs(module.request_with_read_retry("GET", "https://example.test/agents", timeout=60), response)
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

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

    def test_hosted_invocations_url_requires_active_named_runtime(self):
        endpoint = "https://demo.services.ai.azure.com/api/projects/hydro"
        agent = {
            "name": "hydro-orchestrator",
            "versions": {"latest": {"status": "active", "definition": {"kind": "hosted"}}},
        }
        self.assertEqual(
            module.hosted_invocations_url(agent, endpoint),
            endpoint + "/agents/hydro-orchestrator/endpoint/protocols/invocations?api-version=v1",
        )
        for invalid in [
            {**agent, "name": "other"},
            {**agent, "versions": {"latest": {"status": "inactive", "definition": {"kind": "hosted"}}}},
            {**agent, "versions": {"latest": {"status": "active", "definition": {"kind": "prompt"}}}},
        ]:
            with self.assertRaises(RuntimeError):
                module.hosted_invocations_url(invalid, endpoint)
        with self.assertRaises(RuntimeError):
            module.hosted_invocations_url(agent, "http://untrusted.example")

    def test_hosted_runtime_identity_requires_immutable_digest(self):
        agent = {"versions": {"latest": {
            "version": "7",
            "definition": {"container_configuration": {
                "image": f"demo.azurecr.io/hydro-orchestrator@sha256:{'a' * 64}",
            }},
        }}}
        self.assertEqual(module.hosted_runtime_identity(agent), ("7", f"sha256:{'a' * 64}"))
        for invalid in [
            {},
            {"versions": {"latest": {**agent["versions"]["latest"], "version": "candidate"}}},
            {"versions": {"latest": {**agent["versions"]["latest"], "definition": {
                "container_configuration": {"image": "demo.azurecr.io/hydro-orchestrator:latest"},
            }}}},
        ]:
            with self.assertRaises(RuntimeError):
                module.hosted_runtime_identity(invalid)

    def test_source_digest_matches_runtime_configuration_contract(self):
        ids = [f"{index:08x}-1111-4111-8111-{index:012x}" for index in range(1, 8)]
        values = {
            "RAYFIN_PUBLIC_EVENTHOUSE_ID": ids[3],
            "RAYFIN_PUBLIC_KQL_DATABASE_ID": ids[4],
            "RAYFIN_PUBLIC_STID_GRAPHQL_ID": ids[5],
            "RAYFIN_PUBLIC_ITEM_ID": ids[6],
            "RAYFIN_PUBLIC_API_URL": f"https://{'a' * 32}.pbidedicated.windows.net/workload",
            "RAYFIN_PUBLIC_PUBLISHABLE_KEY": "pk-runtime-contract",
        }
        config = {
            "tenant_id": ids[0], "workspace_id": ids[1], "ontology_id": ids[2],
            "eventhouse_id": ids[3], "database_id": ids[4], "graphql_id": ids[5],
            "appbackend_id": ids[6], "api_url": values["RAYFIN_PUBLIC_API_URL"],
            "publishable_key": values["RAYFIN_PUBLIC_PUBLISHABLE_KEY"],
        }
        expected = hashlib.sha256(json.dumps(config, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(module.source_configuration_digest(ids[0], ids[1], ids[2], values), expected)
        for key in values:
            with self.subTest(missing=key):
                invalid = {**values, key: ""}
                with self.assertRaises(RuntimeError):
                    module.source_configuration_digest(ids[0], ids[1], ids[2], invalid)

    def test_active_hosted_source_must_match_selected_deployment(self):
        tenant = "00000001-1111-4111-8111-000000000001"
        workspace = "00000002-1111-4111-8111-000000000002"
        ontology = "00000003-1111-4111-8111-000000000003"
        digest = "a" * 64
        source = {
            "tenant_id": tenant, "workspace_id": workspace, "ontology_id": ontology,
            "generation": 2, "configuration_digest": digest,
        }
        agent = {"versions": {"latest": {"definition": {"environment_variables": {
            "HYDRO_ORCHESTRATOR_CONFIG": json.dumps({"source": source}),
        }}}}}
        module.verify_hosted_source(agent, tenant, workspace, ontology, digest)
        for invalid in [
            {},
            {"versions": {"latest": {"definition": {"environment_variables": {
                "HYDRO_ORCHESTRATOR_CONFIG": "not-json",
            }}}}},
            {"versions": {"latest": {"definition": {"environment_variables": {
                "HYDRO_ORCHESTRATOR_CONFIG": json.dumps({"source": {**source, "workspace_id": tenant}}),
            }}}}},
        ]:
            with self.assertRaises(RuntimeError):
                module.verify_hosted_source(invalid, tenant, workspace, ontology, digest)

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
