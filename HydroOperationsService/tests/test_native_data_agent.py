import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from azure.core.credentials import AccessToken

from hydro_orchestrator.live_sources import SourceFailure
from hydro_orchestrator.native_data_agent import (
    NativeDataAgentCall, call_native_data_agent, native_response, quality_work_evidence, table_rows,
)
from hydro_orchestrator.source_auth import SourceAuthorizationError


class NativeDataAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.workspace, self.agent, self.capacity = uuid4(), uuid4(), uuid4()
        self.endpoint = "https://test.services.ai.azure.com/api/projects/hydro"
        self.target = (f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{self.workspace}"
                       f"/dataagents/{self.agent}/agent")
        self.text = "Original native answer.\r\n\r\n**All 15 turbines**.\n"
        self.response = {
            "id": "resp_native", "status": "completed", "model": "actual-native-model", "error": None,
            "output": [
                {"type": "reasoning", "summary": [{"text": "Private provider reasoning."}]},
                {"type": "message", "role": "assistant", "content": [{
                    "type": "output_text", "text": self.text, "annotations": [
                        {"type": "file_citation", "file_id": "file_native", "filename": "result_0.json"},
                    ],
                }]},
            ],
        }
        self.artifact = {
            "columns": [{"name": "equipment_id"}, {"name": "count"}],
            "rows": [["asset-1", 3], ["asset-2", 0]],
            "query": "SELECT equipment_id, count FROM authoritative_results",
            "datasource_artifact_id": str(uuid4()), "datasource_workspace_id": str(self.workspace),
            "datasource_type": "LakehouseTables",
        }
        self.requests = []
        self.credential = AsyncMock()
        self.credential.get_token.return_value = AccessToken("verified-user-token", 4_000_000_000)
        self.overrides = {}

    def respond(self, request):
        self.requests.append(request)
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path](request)
        if "/connections/" in path:
            return httpx.Response(200, json={
                "type": "RemoteTool", "target": self.target, "metadata": {"type": "fabric_iq_preview"},
            })
        if path == f"/v1/workspaces/{self.workspace}":
            return httpx.Response(200, json={"id": str(self.workspace), "capacityId": str(self.capacity)})
        if path.endswith("/clusterDetails"):
            return httpx.Response(200, json={"clusterUrl": "https://current.analysis.windows.net"})
        if path.endswith("/generatemwctokenv2"):
            return httpx.Response(200, json={"TargetUriHost": f"{self.capacity.hex}.pbidedicated.windows.net"})
        if path.endswith("/responses") or "/responses/" in path:
            return httpx.Response(200, json=self.response)
        if path.endswith("/files/file_native/content"):
            return httpx.Response(200, json=self.artifact)
        self.fail(f"Unexpected request {request.method} {request.url}")

    async def invoke(self, **kwargs):
        original_client = httpx.AsyncClient
        with patch("hydro_orchestrator.native_data_agent.httpx.AsyncClient",
                   side_effect=lambda **options: original_client(
                       transport=httpx.MockTransport(self.respond), **options)):
            return await call_native_data_agent(
                self.endpoint, "verified-connection", self.workspace, "Original question?",
                self.credential, **kwargs,
            )

    async def test_exact_input_text_raw_files_and_typed_rows_without_reasoning_or_mcp(self):
        result = await self.invoke()
        payload = result.source_payload()
        self.assertEqual(payload["response"], self.text)
        self.assertEqual(result.arguments, {"input": "Original question?"})
        self.assertEqual(result.request_id, "resp_native")
        self.assertEqual(json.loads(result.documents[0]["content"]), self.artifact)
        self.assertEqual(result.tables[0]["rows"], [{"equipment_id": "asset-1", "count": 3},
                                                   {"equipment_id": "asset-2", "count": 0}])
        self.assertNotIn("Private provider reasoning", json.dumps(result.result))
        execution = next(r for r in self.requests if r.url.path.endswith("/responses"))
        self.assertEqual(json.loads(execution.content)["input"], "Original question?")
        self.assertEqual(execution.headers["x-ms-ai-aiskill-stage"], "production")
        self.assertFalse(any("/mcp/" in r.url.path for r in self.requests))
        scopes = [c.args[0] for c in self.credential.get_token.call_args_list]
        self.assertIn("https://ai.azure.com/.default", scopes)
        self.assertIn("https://api.fabric.microsoft.com/.default", scopes)
        self.assertIn("https://analysis.windows.net/powerbi/api/.default", scopes)

    async def test_workspace_scoped_connection_is_validated_without_reusing_its_host(self):
        self.target = self.target.replace("api.fabric.microsoft.com",
                                          f"{self.workspace.hex}.zc7.w.api.fabric.microsoft.com")
        await self.invoke()
        self.assertTrue(any(r.url.host == f"{self.capacity.hex}.pbidedicated.windows.net" for r in self.requests))

    async def test_new_capacity_is_discovered_for_every_invocation(self):
        await self.invoke()
        old_capacity = self.capacity
        self.capacity = uuid4()
        self.requests.clear()
        await self.invoke()
        self.assertFalse(any(old_capacity.hex in r.url.host for r in self.requests))
        self.assertTrue(any(self.capacity.hex in r.url.host for r in self.requests))

    async def test_other_workspace_or_untrusted_connection_fails_before_source_requests(self):
        for target in (
            self.target.replace(str(self.workspace), str(uuid4())),
            self.target.replace("api.fabric.microsoft.com", "attacker.example"),
            self.target + "?other=source",
        ):
            with self.subTest(target=target):
                self.target = target
                self.requests.clear()
                with self.assertRaises(SourceFailure):
                    await self.invoke()
                self.assertEqual(len(self.requests), 1)

    async def test_untrusted_discovery_origin_never_receives_a_token(self):
        path = "/powerbi/globalservice/v201606/clusterDetails"
        self.overrides[path] = lambda _: httpx.Response(200, json={"clusterUrl": "https://attacker.example"})
        with self.assertRaises(SourceFailure):
            await self.invoke()
        self.assertFalse(any(r.url.host == "attacker.example" for r in self.requests))

    async def test_redirect_is_a_hard_failure_not_a_credential_redirect(self):
        path = f"/v1/workspaces/{self.workspace}"
        self.overrides[path] = lambda _: httpx.Response(302, headers={"Location": "https://attacker.example"})
        with self.assertRaisesRegex(SourceFailure, "HTTP 302"):
            await self.invoke()
        self.assertFalse(any(r.url.host == "attacker.example" for r in self.requests))

    async def test_no_delegated_token_never_falls_back_to_agent_identity(self):
        self.credential.get_token.side_effect = SourceAuthorizationError("Missing current user lease")
        with self.assertRaises(SourceAuthorizationError):
            await self.invoke()
        self.assertFalse(self.requests)

    async def test_failed_or_incomplete_response_never_becomes_an_answer(self):
        for status in ("failed", "incomplete", "cancelled"):
            with self.subTest(status=status):
                self.response["status"] = status
                self.requests.clear()
                with self.assertRaises(SourceFailure):
                    await self.invoke()
                self.assertFalse(any("/files/" in r.url.path for r in self.requests))

    async def test_pending_response_is_polled_before_artifact_retrieval(self):
        path = (f"/webapi/capacities/{self.capacity}/workloads/ML/AISkill/Automatic/v1"
                f"/workspaces/{self.workspace}/artifacts/{self.agent}/responses")
        self.overrides[path] = lambda _: httpx.Response(200, json={"id": "resp_native", "status": "in_progress"})
        with patch("hydro_orchestrator.native_data_agent.asyncio.sleep", new_callable=AsyncMock):
            await self.invoke()
        self.assertTrue(any(r.method == "GET" and "/responses/resp_native" in r.url.path for r in self.requests))

    async def test_missing_original_text_cannot_succeed(self):
        self.response["output"] = []
        with self.assertRaisesRegex(SourceFailure, "no original answer"):
            await self.invoke()

    async def test_duplicate_citations_download_once_and_non_table_json_is_preserved(self):
        content = self.response["output"][1]["content"][0]
        content["annotations"].append(dict(content["annotations"][0]))
        self.artifact = {"native_chart_data": [1, 2]}
        result = await self.invoke()
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(result.tables, [])

    async def test_ambiguous_or_malformed_artifact_is_an_explicit_failure(self):
        for artifact in (
            {"columns": ["id", "id"], "rows": [["one", "two"]]},
            {"columns": ["id"], "rows": [["one", "extra"]]},
            {"columns": ["id"], "rows": ["not-a-row"]},
        ):
            with self.subTest(artifact=artifact):
                self.artifact = artifact
                with self.assertRaises(SourceFailure):
                    await self.invoke()

    def test_equal_duplicate_columns_are_losslessly_retained_in_raw_artifact(self):
        self.assertEqual(table_rows({"columns": ["id", "id"], "rows": [["one", "one"]]}), [{"id": "one"}])
        self.assertEqual(table_rows({"columns": ["tag", "tag"], "rows": [["pressure", "asset-1"]]}),
                         [{"tag": "pressure", "tag__2": "asset-1"}])

    def test_sse_and_json_encoded_sse_keep_the_terminal_native_envelope(self):
        event = 'data: ' + json.dumps({"type": "response.completed", "response": self.response}) + "\n\n"
        for response in (
            httpx.Response(200, text=event, headers={"Content-Type": "text/event-stream"}),
            httpx.Response(200, json=event),
        ):
            response.request = httpx.Request("POST", "https://native.example/responses")
            self.assertEqual(native_response(response), self.response)


class NativeQualityWorkEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime(2026, 10, 27, 21, tzinfo=timezone.utc)
        self.tables = [
            {"source_type": "LakehouseTables", "source_id": "registry", "query": "registry query",
             "columns": ["equipment_id", "opcua_node_id"],
             "rows": [{"equipment_id": "canonical-asset-A", "opcua_node_id": "opaque.signal.alpha"}]},
            {"source_type": "Kusto", "source_id": "telemetry", "query": "telemetry query",
             "columns": ["opcua_node_id", "bad_sample_count"],
             "rows": [{"opcua_node_id": "opaque.signal.alpha", "bad_sample_count": 7,
                       "earliest_bad_event_time": "2026-10-27T01:00:00Z",
                       "latest_bad_event_time": "2026-10-27T20:00:00Z"}]},
            {"source_type": "SQLDatabase", "source_id": "work", "query": "work query",
             "columns": ["equipmentId", "workOrderNumber", "status"],
             "rows": [{"equipmentId": "canonical-asset-A", "workOrderNumber": "WO-1",
                       "opcuaNodeId": "different.signal.beta", "status": "Draft"},
                      {"equipmentId": "canonical-asset-A", "workOrderNumber": "WO-2", "status": "Completed"}]},
        ]
        self.proof = {"degraded": False, "combineStep": {"succeeded": True}, "dataSourcesQueried": [
            {"dataSourceId": table["source_id"], "generatedQuery": table["query"], "succeeded": True}
            for table in self.tables
        ]}

    def call(self):
        return NativeDataAgentCall("native", {"input": "Unmodified question"}, {
            "output": [
                {"type": "function_call_output", "output": json.dumps(self.proof)},
                {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": "Untouched native answer."},
                ]},
            ],
        }, [], self.tables)

    def test_exact_key_projection_keeps_equipment_level_work_without_rewriting_answer(self):
        call = self.call()
        bundle = quality_work_evidence(call, self.clock)
        self.assertEqual(bundle["affected_signals"][0]["equipment_id"], "canonical-asset-A")
        self.assertEqual(bundle["affected_signals"][0]["bad_sample_count"], 7)
        self.assertEqual([x["work_order_number"] for x in bundle["open_work_orders"]], ["WO-1"])
        self.assertEqual(bundle["coverage"]["affected_signal_count"], 1)
        self.assertEqual(bundle["window_end_utc"], self.clock.isoformat())
        self.assertEqual(call.source_payload()["response"], "Untouched native answer.")

    def test_missing_source_unmatched_nodes_and_conflicting_mappings_are_not_complete(self):
        self.tables.pop()
        self.assertIsNone(quality_work_evidence(self.call(), self.clock))
        self.setUp()
        self.tables[1]["rows"][0]["opcua_node_id"] = "unmapped-node"
        with self.assertRaisesRegex(SourceFailure, "no verified signal"):
            quality_work_evidence(self.call(), self.clock)
        self.setUp()
        self.tables[0]["rows"].append({"equipment_id": "another-asset", "opcua_node_id": "opaque.signal.alpha"})
        with self.assertRaisesRegex(SourceFailure, "conflicting equipment"):
            quality_work_evidence(self.call(), self.clock)

    def test_failed_degraded_or_different_query_receipts_cannot_certify_completeness(self):
        for change in (
            lambda: self.proof.update(degraded=True),
            lambda: self.proof["combineStep"].update(succeeded=False),
            lambda: self.proof["dataSourcesQueried"][0].update(succeeded=False),
            lambda: self.proof["dataSourcesQueried"][0].update(generatedQuery="a different query"),
        ):
            self.setUp()
            change()
            with self.assertRaisesRegex(SourceFailure, "complete source-read"):
                quality_work_evidence(self.call(), self.clock)

    def test_row_limited_reads_cannot_certify_population_coverage(self):
        for index, query in ((0, "SELECT TOP 10 * FROM registry"), (1, "events | take 10"),
                             (2, "SELECT * FROM orders OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY")):
            self.setUp()
            self.tables[index]["query"] = query
            with self.assertRaisesRegex(SourceFailure, "row-limited source"):
                quality_work_evidence(self.call(), self.clock)
