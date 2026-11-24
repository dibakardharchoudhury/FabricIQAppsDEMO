import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from azure.core.credentials import AccessToken

from hydro_orchestrator.ai_gateway import (
    AIGatewayClient, GATEWAY_ORIGIN, GATEWAY_SCOPE, GatewayFailure, GatewayUserAuth, MODEL_ALIAS,
)


class AIGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.credential = AsyncMock()
        self.credential.get_token.return_value = AccessToken("approved-test-token", 9999999999)
        self.gateway = AIGatewayClient(self.credential)
        self.requests = []

    def client(self, respond, *, mcp_discovery=False):
        url = GATEWAY_ORIGIN + (
            "/default/toolservers/learn/mcp" if mcp_discovery else "/default/models/openai/v1/chat/completions"
        )
        return httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            auth=GatewayUserAuth(self.credential, url, mcp_discovery=mcp_discovery),
            timeout=60,
            follow_redirects=False,
        )

    async def complete(self, *, status=200, payload=None):
        def respond(request):
            self.requests.append(request)
            return httpx.Response(status, json=payload if payload is not None else {
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "gateway-ready"}}],
            })

        client = self.client(respond)
        with patch.object(self.gateway, "_client", return_value=client):
            return await self.gateway.complete("Reply exactly: gateway-ready", approved=True)

    async def test_no_credential_or_request_without_per_operation_approval(self):
        for operation in (self.gateway.complete("hello"), self.gateway.list_tools("learn")):
            with self.assertRaises(PermissionError):
                await operation
        self.credential.get_token.assert_not_awaited()

    async def test_model_uses_published_passthrough_alias_and_existing_bearer_binding(self):
        self.assertEqual(await self.complete(), "gateway-ready")
        request = self.requests[0]
        self.assertEqual(str(request.url), GATEWAY_ORIGIN + "/default/models/openai/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer approved-test-token")
        self.assertEqual(json.loads(request.content), {
            "model": MODEL_ALIAS,
            "messages": [{"role": "user", "content": "Reply exactly: gateway-ready"}],
            "max_completion_tokens": 32,
        })
        self.credential.get_token.assert_awaited_once_with(GATEWAY_SCOPE)

    async def test_access_denied_preserves_status_without_retry_or_private_diagnostics(self):
        with self.assertLogs("hydro_orchestrator.ai_gateway", level="ERROR") as logs:
            with self.assertRaises(GatewayFailure) as failure:
                await self.complete(status=403, payload={"error": "Bearer private-provider-token"})
        self.assertEqual(failure.exception.http_status, 403)
        self.assertIn("gateway owner", str(failure.exception))
        self.assertNotIn("private-provider-token", str(failure.exception) + "\n".join(logs.output))
        self.assertEqual(len(self.requests), 1)

    async def test_http_success_does_not_certify_incomplete_refused_or_blank_answers(self):
        for payload in (
            {"choices": []},
            {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": " "}}]},
            {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "answer", "refusal": "refused",
            }}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(GatewayFailure):
                await self.complete(payload=payload)

    async def test_invalid_budget_and_prompt_fail_before_authentication(self):
        for budget in (0, 3073, True):
            with self.assertRaises(ValueError):
                await self.gateway.complete("hello", max_output_tokens=budget, approved=True)
        for prompt in ("", " ", "\u00c5" * 16385):
            with self.assertRaises(ValueError):
                await self.gateway.complete(prompt, approved=True)
        self.credential.get_token.assert_not_awaited()

    async def discover(self, *, next_cursor=None, rpc_error=None):
        tool = {"name": "docs_search", "inputSchema": {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
        }}

        def respond(request):
            self.assertEqual(str(request.url), GATEWAY_ORIGIN + "/default/toolservers/learn/mcp")
            self.assertEqual(request.headers["Authorization"], "Bearer approved-test-token")
            message = json.loads(request.content)
            self.requests.append(message)
            if message["method"] == "notifications/initialized":
                return httpx.Response(202)
            if message["method"] == "initialize":
                result = {"protocolVersion": message["params"]["protocolVersion"],
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "learn", "version": "1"}}
            elif message["method"] == "tools/list":
                if rpc_error:
                    return httpx.Response(200, headers={"Content-Type": "application/json"},
                                          json={"jsonrpc": "2.0", "id": message["id"], "error": rpc_error})
                result = {"tools": [tool]}
                if next_cursor:
                    result["nextCursor"] = next_cursor
            else:
                self.fail("Unexpected MCP operation")
            return httpx.Response(200, headers={"Content-Type": "application/json"},
                                  json={"jsonrpc": "2.0", "id": message["id"], "result": result})

        with patch.object(self.gateway, "_client", return_value=self.client(respond, mcp_discovery=True)):
            inventory = await self.gateway.list_tools("learn", approved=True)
        return inventory, tool

    async def test_mcp_discovery_preserves_exact_schema_and_never_calls_tools(self):
        inventory, tool = await self.discover()
        self.assertEqual(inventory.tools[0].name, tool["name"])
        self.assertEqual(inventory.tools[0].inputSchema, tool["inputSchema"])
        self.assertEqual([request["method"] for request in self.requests],
                         ["initialize", "notifications/initialized", "tools/list"])
        self.assertTrue(all(call.args == (GATEWAY_SCOPE,) for call in self.credential.get_token.call_args_list))

    async def test_partial_inventory_and_rpc_errors_fail_without_pagination_or_tool_execution(self):
        for options in (
            {"next_cursor": "next-page"},
            {"rpc_error": {"code": -32603, "message": "Bearer private-backend-token"}},
        ):
            self.requests = []
            with self.subTest(options=options), self.assertRaises(GatewayFailure) as failure:
                await self.discover(**options)
            self.assertNotIn("private-backend-token", str(failure.exception))
            self.assertEqual([request["method"] for request in self.requests],
                             ["initialize", "notifications/initialized", "tools/list"])

    async def test_mcp_sdk_redirects_and_replays_cannot_send_another_request(self):
        url = GATEWAY_ORIGIN + "/default/toolservers/learn/mcp"
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200)

        async with httpx.AsyncClient(
            auth=GatewayUserAuth(self.credential, url, mcp_discovery=True),
            transport=httpx.MockTransport(respond),
        ) as client:
            await client.post(url, json={"method": "tools/list"})
            for target, method in ((url, "tools/list"), (url, "tools/call"), (url + "/redirect", "initialize")):
                with self.subTest(target=target, method=method), self.assertRaises(httpx.ProtocolError):
                    await client.post(target, json={"method": method})
        self.assertEqual(len(requests), 1)
        self.credential.get_token.assert_awaited_once()
