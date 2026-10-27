import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from azure.core.credentials import AccessToken
from mcp import types

from hydro_orchestrator.live_sources import SourceFailure
from hydro_orchestrator.native_toolbox import NativeToolboxCall, call_native_toolbox


TOOL = {
    "name": "fabriciq-data-agent___DataAgent_Dynamic",
    "inputSchema": {"type": "object", "properties": {"userQuestion": {"type": "string"}},
                    "required": ["userQuestion"]},
    "annotations": {"readOnlyHint": True},
    "_meta": {"tool_configuration": {
        "original_type": "fabric_iq_preview", "project_connection_id": "hydro-fabric-iq",
        "server_label": "fabriciq-data-agent",
    }},
}


class NativeToolboxTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, *, tools=None, result=None, next_cursor=None):
        requests = []
        credential = AsyncMock()
        credential.get_token.return_value = AccessToken("delegated-test-token", 9999999999)
        question = "Which turbines?\nKeep punctuation & Norwegian names: \u00c5."

        def respond(request):
            self.assertEqual(request.headers["Authorization"], "Bearer delegated-test-token")
            self.assertEqual(str(request.url), "https://foundry.example/api/projects/hydro/toolboxes/"
                             "hydro-fabric-iq-tools/versions/2/mcp?api-version=v1")
            message = json.loads(request.content)
            requests.append(message)
            method = message["method"]
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "initialize":
                value = {"protocolVersion": message["params"]["protocolVersion"],
                         "capabilities": {"tools": {}}, "serverInfo": {"name": "Foundry", "version": "1"}}
            elif method == "tools/list":
                value = {"tools": tools if tools is not None else [copy.deepcopy(TOOL)]}
                if next_cursor:
                    value["nextCursor"] = next_cursor
            elif method == "tools/call":
                self.assertEqual(message["params"]["arguments"], {"userQuestion": question})
                value = result if result is not None else {
                    "content": [{"type": "text", "text": "**Original**\n\n| Status | Count |\n| Open | 10 |"}],
                    "isError": False,
                }
            else:
                self.fail(f"Unexpected protocol method {method}")
            return httpx.Response(200, headers={"Content-Type": "application/json",
                                               "apim-request-id": "actual-tool-execution"},
                                  json={"jsonrpc": "2.0", "id": message["id"], "result": value})

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        with patch("hydro_orchestrator.native_toolbox.httpx.AsyncClient", side_effect=client):
            call = await call_native_toolbox(
                "https://foundry.example/api/projects/hydro", "hydro-fabric-iq-tools", "2",
                "hydro-fabric-iq", question, credential,
            )
        return call, requests, credential

    async def test_native_mcp_transport_preserves_question_and_answer_without_a_model(self):
        call, requests, credential = await self.execute()
        self.assertEqual(call.request_id, "actual-tool-execution")
        self.assertEqual(call.tool_name, TOOL["name"])
        self.assertEqual(call.source_payload()["response"], "**Original**\n\n| Status | Count |\n| Open | 10 |")
        self.assertEqual([item["method"] for item in requests],
                         ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        self.assertTrue(all(item.args == ("https://ai.azure.com/.default",)
                            for item in credential.get_token.call_args_list))

    async def test_native_source_errors_are_not_successful_answers(self):
        with self.assertRaisesRegex(SourceFailure, "source execution error"):
            await self.execute(result={"content": [{"type": "text", "text": "SQL execution failed"}],
                                       "isError": True})

    async def test_unverified_or_ambiguous_tools_cannot_be_called(self):
        variants = [[], [copy.deepcopy(TOOL), copy.deepcopy(TOOL)]]
        for path, value in (
            (("annotations", "readOnlyHint"), False),
            (("_meta", "tool_configuration", "project_connection_id"), "another-connection"),
            (("_meta", "tool_configuration", "original_type"), "mcp"),
            (("inputSchema", "required"), []),
            (("inputSchema", "properties"), {"userQuestion": {"type": "integer"}}),
        ):
            tool = copy.deepcopy(TOOL)
            target = tool
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            variants.append([tool])
        for tools in variants:
            with self.subTest(tools=tools):
                with self.assertRaises(ExceptionGroup) as raised:
                    await self.execute(tools=tools)
                exceptions = list(raised.exception.exceptions)
                while len(exceptions) == 1 and isinstance(exceptions[0], BaseExceptionGroup):
                    exceptions = list(exceptions[0].exceptions)
                self.assertEqual(len(exceptions), 1)
                self.assertIsInstance(exceptions[0], SourceFailure)

    async def test_incomplete_inventory_is_rejected(self):
        with self.assertRaises(ExceptionGroup):
            await self.execute(next_cursor="more-tools")

    def test_native_resources_and_provider_annotations_survive(self):
        result = types.CallToolResult.model_validate({
            "content": [
                {"type": "text", "text": "  **Unmodified**\n| A | B |  "},
                {"type": "resource", "resource": {
                    "uri": "file:///result_0.csv", "mimeType": "text/csv", "text": "A,B\n1,2\n",
                }},
            ],
            "_meta": {"provider_annotation": "preserve"},
            "isError": False,
        })
        payload = NativeToolboxCall("request", "tool", {"userQuestion": "exact"}, result).source_payload()
        self.assertEqual(payload["response"], "  **Unmodified**\n| A | B |  ")
        self.assertEqual(payload["documents"][0]["content"], "A,B\n1,2\n")
        self.assertEqual(payload["native_mcp_result"]["_meta"]["provider_annotation"], "preserve")

    def test_no_original_answer_cannot_be_replaced_with_a_success_message(self):
        for content in ([], [{"type": "text", "text": "   "}]):
            with self.subTest(content=content), self.assertRaisesRegex(SourceFailure, "no original answer"):
                NativeToolboxCall("request", "tool", {}, types.CallToolResult.model_validate({
                    "content": content,
                })).source_payload()
