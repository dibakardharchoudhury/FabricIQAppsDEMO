"""Read-only native Fabric execution through the Foundry-managed MCP gateway."""

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from .live_sources import SourceFailure


class FoundryUserAuth(httpx.Auth):
    def __init__(self, credential: AsyncTokenCredential):
        self.credential = credential

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self.credential.get_token("https://ai.azure.com/.default")
        request.headers["Authorization"] = "Bearer " + token.token
        yield request


@dataclass(frozen=True)
class NativeToolboxCall:
    request_id: str
    tool_name: str
    arguments: dict[str, str]
    result: types.CallToolResult

    def source_payload(self) -> dict[str, object]:
        text = [item.text for item in self.result.content if isinstance(item, types.TextContent)]
        if not text or not any(item.strip() for item in text):
            raise SourceFailure("The native Data Agent returned no original answer text.")
        documents = []
        for item in self.result.content:
            if isinstance(item, types.EmbeddedResource) and isinstance(item.resource, types.TextResourceContents):
                resource = item.resource
                documents.append({
                    "id": str(resource.uri), "url": str(resource.uri), "title": str(resource.uri),
                    "content": resource.text, "metadata": resource.mimeType,
                })
        return {
            "response": "\n\n".join(text), "documents": documents,
            "native_mcp_result": self.result.model_dump(mode="json", by_alias=True, exclude_none=True),
        }


async def call_native_toolbox(
    endpoint: str, name: str, version: str, connection: str, question: str,
    credential: AsyncTokenCredential, *, timeout: float = 300,
) -> NativeToolboxCall:
    request_id = str(uuid4())

    async def capture(response: httpx.Response) -> None:
        nonlocal request_id
        if response.request.method == "POST" and json.loads(response.request.content).get("method") == "tools/call":
            request_id = response.headers.get("apim-request-id") or response.headers.get("x-ms-request-id") or request_id

    url = f"{endpoint}/toolboxes/{name}/versions/{version}/mcp?api-version=v1"
    async with httpx.AsyncClient(
        auth=FoundryUserAuth(credential), follow_redirects=False, timeout=timeout,
        headers={"x-ms-tool-call-id": request_id}, event_hooks={"response": [capture]},
    ) as client:
        async with streamable_http_client(url, http_client=client, terminate_on_close=False) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
                await session.initialize()
                inventory = await session.list_tools()
                if inventory.nextCursor or len(inventory.tools) != 1:
                    raise SourceFailure("The native toolbox must expose exactly one complete Data Agent query tool.")
                tool = inventory.tools[0]
                metadata = tool.model_dump(by_alias=True).get("_meta") or {}
                configuration = metadata.get("tool_configuration", {})
                if (configuration.get("original_type") != "fabric_iq_preview"
                        or configuration.get("project_connection_id") != connection
                        or configuration.get("server_label") != "fabriciq-data-agent"
                        or tool.annotations is None or tool.annotations.readOnlyHint is not True):
                    raise SourceFailure("The toolbox tool differs from the verified read-only Fabric IQ binding.")
                required = tool.inputSchema.get("required", [])
                properties = tool.inputSchema.get("properties", {})
                if (len(required) != 1 or set(properties) != set(required)
                        or properties[required[0]].get("type") != "string"):
                    raise SourceFailure("The native Data Agent query schema has no unambiguous question argument.")
                arguments = {required[0]: question}
                result = await session.call_tool(tool.name, arguments=arguments)
    if result.isError:
        raise SourceFailure("The Foundry toolbox returned a native source execution error.")
    return NativeToolboxCall(request_id, tool.name, arguments, result)
