"""Opt-in consumption of AIGatewayPower; no provisioning or agent routing changes."""

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import ClientAuthenticationError
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .live_sources import SourceFailure

logger = logging.getLogger(__name__)
GATEWAY_ORIGIN = "https://aigatewayp-j4rqjxpeakwtc.swedencentral-01.ai.gateway.azure.com"
MODEL_ALIAS = "poweragents/gpt-5.2-chat"
GATEWAY_SCOPE = "https://ai.gateway.azure.com/.default"
ToolServer = Literal["fabric-espen-ontology", "learn", "health"]
TOOL_SERVERS: tuple[ToolServer, ...] = ("fabric-espen-ontology", "learn", "health")
REQUEST_TIMEOUT = 60.0


class GatewayFailure(SourceFailure):
    def __init__(self, message: str, *, http_status: int | None = None):
        super().__init__(message, write_attempted=False)
        self.http_status = http_status


class _Message(BaseModel):
    model_config = ConfigDict(strict=True)
    role: Literal["assistant"]
    content: str = Field(min_length=1)
    refusal: None = None


class _Choice(BaseModel):
    finish_reason: Literal["stop"]
    message: _Message


class _Completion(BaseModel):
    choices: list[_Choice] = Field(min_length=1, max_length=1)


class GatewayUserAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, credential: AsyncTokenCredential, url: str, *, mcp_discovery: bool = False):
        self.credential = credential
        self.url = url
        self.mcp_discovery = mcp_discovery
        self.sent: set[tuple[str, str]] = set()

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        # The MCP SDK can follow redirects and resume streams independently of HTTP client settings.
        if str(request.url) != self.url:
            raise httpx.ProtocolError("AI Gateway redirects require separate approval.")
        operation = ""
        if self.mcp_discovery and request.method == "POST":
            try:
                message = json.loads(request.content)
            except (ValueError, UnicodeDecodeError):
                raise httpx.ProtocolError("Invalid MCP discovery request.") from None
            if not isinstance(message, dict):
                raise httpx.ProtocolError("Invalid MCP discovery request.")
            operation = message.get("method", "")
            if operation not in ("initialize", "notifications/initialized", "tools/list"):
                raise httpx.ProtocolError("MCP tool execution is not approved.")
        elif request.method != "POST" and not (self.mcp_discovery and request.method == "GET"):
            raise httpx.ProtocolError("AI Gateway operation requires separate approval.")
        key = (request.method, operation)
        if key in self.sent:
            raise httpx.ProtocolError("AI Gateway request replay requires separate approval.")
        self.sent.add(key)
        token = await self.credential.get_token(GATEWAY_SCOPE)
        request.headers["Authorization"] = "Bearer " + token.token
        yield request


@asynccontextmanager
async def _gateway_errors() -> AsyncGenerator[None, None]:
    try:
        yield
    except* (httpx.HTTPError, McpError, ClientAuthenticationError) as group:
        pending = list(group.exceptions)
        details: list[str] = []
        status: int | None = None
        while pending:
            error = pending.pop()
            if isinstance(error, BaseExceptionGroup):
                pending.extend(error.exceptions)
            elif isinstance(error, httpx.HTTPStatusError):
                status = error.response.status_code
                details.append(f"HTTP {status}")
            elif isinstance(error, McpError):
                details.append(f"MCP code {error.error.code}")
            else:
                details.append(type(error).__name__)
        message = "AI Gateway request failed (" + "; ".join(details) + "). No retry was performed."
        if status in (401, 403):
            message += " The gateway owner must review access for the existing approved identity."
        logger.error("%s", message)
        raise GatewayFailure(message, http_status=status) from None


class AIGatewayClient:
    """Requires an existing approved credential and per-operation human approval."""

    def __init__(self, credential: AsyncTokenCredential):
        self.credential = credential

    def _client(self, url: str, *, mcp_discovery: bool = False) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=GatewayUserAuth(self.credential, url, mcp_discovery=mcp_discovery),
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )

    async def complete(
        self, prompt: str, *, max_output_tokens: int = 32, approved: bool = False,
    ) -> str:
        if approved is not True:
            raise PermissionError("Approve the exact model prompt and billable output budget before requesting it.")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 32768:
            raise ValueError("The model prompt must contain 1-32768 UTF-8 bytes.")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 3072:
            raise ValueError("The model output budget must be an integer between 1 and 3072.")
        url = GATEWAY_ORIGIN + "/default/models/openai/v1/chat/completions"
        async with _gateway_errors(), self._client(url) as client:
            response = await client.post(
                url,
                json={
                    "model": MODEL_ALIAS,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": max_output_tokens,
                },
            )
            response.raise_for_status()
            try:
                completion = _Completion.model_validate_json(response.content)
            except ValidationError:
                logger.error("AI Gateway returned no valid completed model answer.")
                raise GatewayFailure("AI Gateway returned no valid completed model answer.") from None
        answer = completion.choices[0].message.content
        if not answer.strip():
            logger.error("AI Gateway returned a blank model answer.")
            raise GatewayFailure("AI Gateway returned a blank model answer.")
        return answer

    async def list_tools(self, server: ToolServer, *, approved: bool = False) -> types.ListToolsResult:
        if approved is not True:
            raise PermissionError("Approve MCP initialization and tool listing for the exact server first.")
        if server not in TOOL_SERVERS:
            raise ValueError("Select one of the discovered gateway MCP servers.")
        url = GATEWAY_ORIGIN + f"/default/toolservers/{server}/mcp"
        async with _gateway_errors(), self._client(url, mcp_discovery=True) as client:
            async with streamable_http_client(
                url, http_client=client, terminate_on_close=False,
            ) as (read, write, _):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=REQUEST_TIMEOUT),
                ) as session:
                    await session.initialize()
                    inventory = await session.list_tools()
        if inventory.nextCursor:
            logger.error("AI Gateway returned an incomplete tool inventory; no tools were selected or executed.")
            raise GatewayFailure("AI Gateway tool inventory is incomplete; further discovery needs approval.")
        return inventory
