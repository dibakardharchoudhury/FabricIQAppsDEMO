"""Published Fabric Data Agent Responses transport; never a direct-table fallback."""

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import time
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4

import httpx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

from .live_sources import SourceFailure
from .source_auth import SourceAuthorizationError

logger = logging.getLogger(__name__)
FABRIC_ORIGIN = "https://api.fabric.microsoft.com"
PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class NativeDataAgentTimeout(SourceFailure):
    pass


class NativeDataAgentTransportError(SourceFailure):
    pass


def trusted_origin(value: object, suffix: str) -> str:
    if not isinstance(value, str):
        raise SourceFailure("Fabric workload discovery returned no origin.")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(suffix)
            or parsed.username or parsed.password or parsed.port not in (443, None)
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise SourceFailure("Fabric workload discovery returned an untrusted origin.")
    return f"https://{parsed.hostname}"


def data_agent_id(connection: dict, workspace_id: UUID) -> UUID:
    target = connection.get("target")
    if (connection.get("type") != "RemoteTool"
            or connection.get("metadata", {}).get("type") != "fabric_iq_preview"
            or not isinstance(target, str)):
        raise SourceFailure("Foundry connection is not the verified Fabric Data Agent type.")
    parsed = urlparse(target)
    scoped_host = re.fullmatch(
        workspace_id.hex + r"\.z[0-9a-f]{2}\.w\.api\.fabric\.microsoft\.com", parsed.hostname or "",
    )
    match = re.fullmatch(
        rf"/v1/mcp/workspaces/{workspace_id}/dataagents/([0-9a-f-]{{36}})/agent", parsed.path, re.I,
    )
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (443, None)
            or parsed.query or parsed.fragment or not match
            or not (parsed.hostname == "api.fabric.microsoft.com" or scoped_host)):
        raise SourceFailure("Foundry connection changed the bound Data Agent workspace or endpoint.")
    return UUID(match[1])


def native_response(response: httpx.Response) -> dict:
    response.raise_for_status()
    if "text/event-stream" in response.headers.get("content-type", ""):
        value = response.text
    else:
        value = response.json()
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        for line in reversed(value.splitlines()):
            if not line.startswith("data: ") or line[6:] == "[DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("type") in ("response.completed", "response.failed", "response.incomplete"):
                result = event.get("response")
                if isinstance(result, dict):
                    return result
    raise SourceFailure("Fabric Responses returned no inspectable response envelope.")


async def streamed_native_response(response: httpx.Response) -> dict:
    response.raise_for_status()
    if "text/event-stream" not in response.headers.get("content-type", ""):
        await response.aread()
        return native_response(response)
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            payload, data = "\n".join(data), []
            if payload == "[DONE]":
                break
            event = json.loads(payload)
            if not isinstance(event, dict):
                raise NativeDataAgentTransportError("Fabric returned an invalid streaming event.")
            if event.get("type") == "error":
                raise NativeDataAgentTransportError("Fabric returned a streaming execution error.")
            if event.get("type") in ("response.completed", "response.failed", "response.incomplete"):
                result = event.get("response")
                if not isinstance(result, dict):
                    raise NativeDataAgentTransportError("Fabric returned an invalid terminal response.")
                return result
    raise NativeDataAgentTransportError("Fabric stream ended without a terminal response.")


def table_rows(artifact: dict) -> list[dict[str, object]]:
    columns, rows = artifact.get("columns"), artifact.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise SourceFailure("Native Data Agent result file has no typed table.")
    names = [column.get("name") if isinstance(column, dict) else column for column in columns]
    if not names or any(not isinstance(name, str) or not name for name in names):
        raise SourceFailure("Native Data Agent result file has invalid column names.")
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise SourceFailure("Native Data Agent result file contains a malformed row.")
        record: dict[str, object] = {}
        for name, value in zip(names, row, strict=True):
            if not isinstance(name, str):
                raise SourceFailure("Native Data Agent result file has an invalid column name.")
            if name in record:
                if record[name] == value:
                    continue
                if name in {"id", "equipment_id", "equipmentId", "opcua_node_id", "opcuaNodeId",
                            "instrument_id", "instrumentId", "facility_id", "system_id"}:
                    raise SourceFailure("Native Data Agent result has ambiguous duplicate identity values.")
                original_name, suffix = name, 2
                while name in record:
                    name = f"{original_name}__{suffix}"
                    suffix += 1
            record[name] = value
        result.append(record)
    return result


@dataclass(frozen=True)
class NativeDataAgentCall:
    request_id: str
    arguments: dict[str, str]
    result: dict
    documents: list[dict]
    tables: list[dict]
    tool_name: str = "FabricDataAgent.Responses"

    def source_payload(self) -> dict[str, object]:
        texts = [
            block["text"] for item in self.result.get("output", [])
            if item.get("type") == "message" and item.get("role") == "assistant"
            for block in item.get("content", [])
            if block.get("type") == "output_text" and isinstance(block.get("text"), str)
        ]
        if not texts or not any(text.strip() for text in texts):
            raise SourceFailure("The native Data Agent returned no original answer text.")
        return {
            "response": "".join(texts), "documents": self.documents,
            "tables": self.tables, "native_response": self.result,
        }


def quality_work_evidence(call: NativeDataAgentCall, requested_at: datetime) -> dict | None:
    """Project verified native tables, not prose, into the existing quality/work contract."""
    required = {
        "registry": ("LakehouseTables", {"equipment_id", "opcua_node_id"}),
        "telemetry": ("Kusto", {"opcua_node_id", "bad_sample_count"}),
        "work": ("SQLDatabase", {"equipmentId", "workOrderNumber", "status"}),
    }
    candidates = {}
    for role, (source_type, columns) in required.items():
        matches = [
            table for table in call.tables if table.get("source_type") == source_type
            and columns.issubset({
                c.get("name") if isinstance(c, dict) else c for c in table.get("columns", [])
            })
        ]
        if not matches:
            return None
        candidates[role] = matches
    selected = {}
    for item in reversed(call.result.get("output", [])):
        if item.get("type") != "function_call_output" or not isinstance(item.get("output"), str):
            continue
        try:
            proof = json.loads(item["output"])
        except json.JSONDecodeError:
            continue
        if (not isinstance(proof, dict) or proof.get("degraded") is not False
                or not isinstance(proof.get("combineStep"), dict)
                or proof["combineStep"].get("succeeded") is not True):
            continue
        reads = proof.get("dataSourcesQueried")
        if not isinstance(reads, list):
            continue
        matched = {
            role: [table for table in tables if any(
                read.get("dataSourceId") == table.get("source_id") and read.get("succeeded") is True
                and isinstance(read.get("generatedQuery"), str) and isinstance(table.get("query"), str)
                and " ".join(read["generatedQuery"].split()) == " ".join(table["query"].split())
                for read in reads if isinstance(read, dict)
            )] for role, tables in candidates.items()
        }
        if all(matched.values()):
            if any(len(tables) != 1 for tables in matched.values()):
                raise SourceFailure("Native quality/work evidence has ambiguous source tables.")
            selected = {role: tables[0] for role, tables in matched.items()}
            break
    if not selected:
        raise SourceFailure("Native quality/work tables lack successful complete source-read and combine receipts.")
    for role, table in selected.items():
        query = table["query"]
        bounded = (re.search(r"\|\s*(?:take|limit|top|sample|sample-distinct)\b", query, re.I)
                   if role == "telemetry" else
                   re.search(r"\b(?:TOP\s*\(?\s*\d+|LIMIT\s+\d+|OFFSET\s+\d+|FETCH\s+(?:FIRST|NEXT)\s+\d+)", query, re.I))
        if bounded:
            raise SourceFailure("Native quality/work evidence contains a row-limited source query.")
    registry = {}
    for row in selected["registry"]["rows"]:
        node, equipment = row.get("opcua_node_id"), row.get("equipment_id")
        if not isinstance(node, str) or not isinstance(equipment, str):
            raise SourceFailure("Native signal registry has invalid canonical identities.")
        if node in registry and registry[node].get("equipment_id") != equipment:
            raise SourceFailure("Native signal registry has conflicting equipment mappings.")
        registry[node] = row
    signals = []
    equipment_ids = set()
    for row in selected["telemetry"]["rows"]:
        node = row.get("opcua_node_id")
        if not isinstance(node, str) or node not in registry:
            raise SourceFailure("Native BAD telemetry has no verified signal-to-equipment mapping.")
        mapping = registry[node]
        equipment_ids.add(mapping["equipment_id"])
        signals.append({
            "equipment_id": mapping["equipment_id"], "opcua_node_id": node,
            "instrument_id": mapping.get("instrument_id"), "unit": mapping.get("unit"),
            "bad_sample_count": row.get("bad_sample_count"),
            "first_bad_utc": row.get("first_bad_utc", row.get("earliest_bad_event_time")),
            "last_bad_utc": row.get("last_bad_utc", row.get("latest_bad_event_time")),
        })
    orders = []
    for row in selected["work"]["rows"]:
        equipment = row.get("equipmentId")
        if not isinstance(equipment, str):
            raise SourceFailure("Native work records have no canonical equipment identity.")
        status = row.get("status")
        if equipment not in equipment_ids or isinstance(status, str) and status.lower() in ("completed", "cancelled"):
            continue
        orders.append({
            **row, "equipment_id": equipment, "work_order_number": row.get("workOrderNumber"),
            "opcua_node_id": row.get("opcuaNodeId"), "instrument_id": row.get("instrumentId"),
        })
    end = requested_at.astimezone(timezone.utc)
    return {
        "window_start_utc": end.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        "window_end_utc": end.isoformat(), "affected_signals": signals, "open_work_orders": orders,
        "coverage": {"telemetry_complete": True, "work_complete": True,
                     "affected_signal_count": len(signals), "open_work_order_count": len(orders)},
    }


async def call_native_data_agent(
    endpoint: str, connection: str, workspace_id: UUID, question: str,
    credential: AsyncTokenCredential, *, timeout: float = 300,
    verified_workload_origin: str | None = None,
    include_artifacts: bool = True,
) -> NativeDataAgentCall:
    phase = "connection verification"
    correlation = str(uuid4())
    try:
        async with asyncio.timeout(timeout), httpx.AsyncClient(
            timeout=timeout, follow_redirects=False,
        ) as client:
            tokens: dict[str, AccessToken] = {}

            async def request(method: str, url: str, scope: str, *, json: object = None) -> httpx.Response:
                token = tokens.get(scope)
                if token is None or token.expires_on <= time() + 30:
                    token = await credential.get_token(scope)
                    tokens[scope] = token
                response = await client.request(
                    method, url, headers={"Authorization": "Bearer " + token.token, **headers}, json=json,
                )
                response.raise_for_status()
                return response

            async def responses_request(method: str, url: str, *, json: object = None) -> dict:
                token = tokens.get(PBI_SCOPE)
                if token is None or token.expires_on <= time() + 30:
                    token = await credential.get_token(PBI_SCOPE)
                    tokens[PBI_SCOPE] = token
                async with client.stream(
                    method, url, headers={"Authorization": "Bearer " + token.token, **headers}, json=json,
                ) as response:
                    return await streamed_native_response(response)

            headers: dict[str, str] = {"ActivityId": correlation}
            foundry_connection = await request(
                "GET", f"{endpoint}/connections/{quote(connection, safe='')}?api-version=v1",
                "https://ai.azure.com/.default",
            )
            agent = data_agent_id(foundry_connection.json(), workspace_id)
            phase = "workspace capacity discovery"
            workspace = (await request(
                "GET", f"{FABRIC_ORIGIN}/v1/workspaces/{workspace_id}", FABRIC_ORIGIN + "/.default",
            )).json()
            if workspace.get("id", "").lower() != str(workspace_id):
                raise SourceFailure("Fabric discovery changed the bound workspace identity.")
            capacity = UUID(workspace["capacityId"])
            if verified_workload_origin is not None:
                phase = "bound workload validation"
                origin = trusted_origin(verified_workload_origin, ".pbidedicated.windows.net")
                if urlparse(origin).hostname != f"{capacity.hex}.pbidedicated.windows.net":
                    raise SourceFailure("Native workload origin differs from the live workspace capacity; redeploy.")
            else:
                phase = "cluster discovery"
                cluster = (await request(
                    "GET", FABRIC_ORIGIN + "/powerbi/globalservice/v201606/clusterDetails", PBI_SCOPE,
                )).json()
                cluster_origin = trusted_origin(cluster.get("clusterUrl"), ".analysis.windows.net")
                phase = "workload routing"
                routing = (await request(
                    "POST", cluster_origin + "/metadata/v201606/generatemwctokenv2", PBI_SCOPE,
                    json={"capacityObjectId": str(capacity), "workspaceObjectId": str(workspace_id),
                          "workloadType": "ML"},
                )).json()
                host = routing.get("TargetUriHost")
                origin = trusted_origin("https://" + host if isinstance(host, str) else None,
                                        ".pbidedicated.windows.net")
            base = (f"{origin}/webapi/capacities/{capacity}/workloads/ML/AISkill/Automatic/v1"
                    f"/workspaces/{workspace_id}/artifacts/{agent}")
            headers.update({
                "Accept": "application/json", "x-ms-workload-resource-moniker": str(uuid4()),
                "x-ms-ai-assistant-scenario": "aiskill", "x-ms-ai-aiskill-stage": "production",
                "X-Taxonomy-TrafficType": "Production", "x-llm-service-tier": "default",
            })
            phase = "published native execution"
            arguments = {"input": question}
            # The official Fabric Responses client injects this compatibility model;
            # Fabric selects and reports its actual runtime model in the response.
            response = await responses_request(
                "POST", base + "/responses?api-version=2024-05-01-preview",
                json={"model": "gpt-5.1", **arguments},
            )
            while response.get("status") in ("queued", "in_progress"):
                identity = response.get("id")
                if not isinstance(identity, str) or not identity:
                    raise SourceFailure("The native response has no polling identity.")
                await asyncio.sleep(1)
                response = await responses_request(
                    "GET", f"{base}/responses/{quote(identity, safe='')}?api-version=2024-05-01-preview",
                )
            if response.get("status") != "completed" or response.get("error"):
                raise NativeDataAgentTransportError("The published native Data Agent did not complete successfully.")
            identity, output = response.get("id"), response.get("output")
            if not isinstance(identity, str) or not identity or not isinstance(output, list):
                raise SourceFailure("The native Data Agent returned an invalid response identity or output.")
            retained = {key: response[key] for key in (
                "id", "status", "model", "usage", "error", "created_at", "completed_at",
            ) if key in response}
            retained["output"] = [item for item in output if item.get("type") != "reasoning"]
            documents, tables, seen = [], [], set()
            phase = "native result artifact retrieval"
            for item in output if include_artifacts else ():
                if item.get("type") != "message" or item.get("role") != "assistant":
                    continue
                for block in item.get("content", []):
                    for annotation in block.get("annotations", []):
                        if annotation.get("type") != "file_citation":
                            continue
                        file_id = annotation.get("file_id")
                        if not isinstance(file_id, str) or not re.fullmatch(r"file_[A-Za-z0-9_-]+", file_id):
                            raise SourceFailure("The native answer contains an invalid result file identity.")
                        if file_id in seen:
                            continue
                        seen.add(file_id)
                        if len(seen) > 20:
                            raise SourceFailure("The native answer exceeds the bounded result artifact count.")
                        artifact = await request(
                            "GET", f"{base}/aiassistant/openai/files/{file_id}/content"
                            "?api-version=2024-05-01-preview", PBI_SCOPE,
                        )
                        if len(artifact.content) > 5_000_000:
                            raise SourceFailure("A native result artifact exceeds the evidence size limit.")
                        filename = annotation.get("filename", file_id)
                        mime = artifact.headers.get("content-type", "").split(";")[0]
                        is_json = mime == "application/json" or str(filename).endswith(".json")
                        is_text = is_json or mime.startswith("text/") or str(filename).endswith(".csv")
                        documents.append({
                            "id": file_id, "title": filename,
                            "content": artifact.text if is_text else base64.b64encode(artifact.content).decode("ascii"),
                            "metadata": "application/json" if is_json else mime or "application/octet-stream",
                            "encoding": "utf-8" if is_text else "base64",
                        })
                        if is_json:
                            value = artifact.json()
                            if isinstance(value, dict) and ("columns" in value or "rows" in value):
                                tables.append({
                                    "id": file_id, "columns": value.get("columns"), "rows": table_rows(value),
                                    "query": value.get("query"), "source_type": value.get("datasource_type"),
                                    "source_id": value.get("datasource_artifact_id"),
                                    "workspace_id": value.get("datasource_workspace_id"),
                                })
            call = NativeDataAgentCall(identity, arguments, retained, documents, tables)
            call.source_payload()
            return call
    except SourceAuthorizationError:
        raise
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as error:
        status = f" HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else ""
        logger.error("Native Data Agent %s failed: %s%s; correlation %s",
                     phase, type(error).__name__, status, correlation)
        failure = NativeDataAgentTimeout if isinstance(error, (TimeoutError, httpx.TimeoutException)) else NativeDataAgentTransportError
        raise failure(
            f"Native Data Agent {phase} failed ({type(error).__name__}{status}; correlation {correlation})."
        ) from error
