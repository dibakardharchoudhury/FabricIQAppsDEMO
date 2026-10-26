import asyncio
import json
import os
import math
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5
from typing import TYPE_CHECKING, Literal, Protocol

from azure.identity.aio import AzureCliCredential
from azure.core.credentials_async import AsyncTokenCredential
from pydantic import AwareDatetime, Field

from .contracts import (
    Contract, Evidence, ResolvedScope, ReviewRequest, SourceIdentity, WorkOrderDraft, WorkOrderEdits, utc_now,
)

if TYPE_CHECKING:
    from .foundry_supervisor import ChatRequest, ToolEvidence


class SourceFailure(RuntimeError):
    def __init__(self, message: str, *, write_attempted: bool | None = None):
        super().__init__(message)
        self.write_attempted = write_attempted


class Discovery(Contract):
    source: SourceIdentity
    cluster: str
    database: str


class CatalogRead(Contract):
    source: SourceIdentity
    result: dict[str, object]
    completed_at: AwareDatetime


class ProbeCheck(Contract):
    source: Literal["stid_telemetry", "work_orders"]
    status: Literal["passed", "failed"]
    observation_count: int | None = Field(default=None, ge=0, le=100)
    missing_sources: tuple[str, ...] | None = None


class ProbeFailure(Contract):
    source: Literal["stid_telemetry", "work_orders"]
    message: str


class ProbeResult(Contract):
    ready: bool
    source: SourceIdentity
    failures: tuple[ProbeFailure, ...]
    checks: tuple[ProbeCheck, ...] = Field(min_length=2, max_length=2)


class Bridge(Protocol):
    async def call(self, request: dict[str, object]) -> dict[str, object]: ...


class NodeSourceBridge:
    """One long-lived Node worker multiplexed by request id.

    The worker pays module start-up once instead of on every contract/source call. It is bound to the event loop
    and source configuration that started it, is replaced after a crash, and no call is ever replayed after a
    worker failure because some actions (work-order approval) write.
    """

    result_marker = b"__HYDRO_RESULT__="
    request_limit = 262144
    response_limit = 2 * 1024 * 1024
    call_timeout = 75

    def __init__(self, node: str | None = None):
        self.node = node or os.environ.get("HYDRO_LOCAL_NODE", "node")
        self.script = Path(__file__).resolve().parents[3] / "HydroOperationsApp" / "scripts" / "local-fabric-sources.mjs"
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._environment: str | None = None
        self._pending: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._sequence = 0

    @staticmethod
    def _environment_key() -> str:
        return repr(os.environ.get("HYDRO_FABRIC_SOURCE_CONFIG"))

    def _fail_pending(self, message: str) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(SourceFailure(message))

    async def _read_responses(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        failure = "Local source process exited before returning a response."
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    failure = "Local source process response exceeds its bounded size."
                    break
                if not line:
                    break
                _prefix, marker, payload = line.rpartition(self.result_marker)
                if not marker:
                    continue
                if len(payload) > self.response_limit:
                    failure = "Local source process response exceeds its bounded size."
                    break
                try:
                    envelope = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    failure = "Local source process returned an invalid response; output suppressed."
                    break
                identifier = envelope.get("id") if isinstance(envelope, dict) else None
                if not isinstance(identifier, int):
                    failure = "Local source process returned an unroutable response; output suppressed."
                    break
                future = self._pending.pop(identifier, None)
                if future is not None and not future.done():
                    future.set_result(envelope)
        finally:
            # A worker that cannot be trusted to route responses is discarded; the next call starts a clean one.
            if self._process is process:
                self._process = None
            self._fail_pending(failure)
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def _ensure_worker(self) -> asyncio.subprocess.Process:
        loop = asyncio.get_running_loop()
        if self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        assert self._lock is not None
        async with self._lock:
            environment = self._environment_key()
            process = self._process
            if (process is not None and process.returncode is None and self._loop is loop
                    and self._environment == environment):
                return process
            await self.aclose()
            process = await asyncio.create_subprocess_exec(
                self.node, str(self.script), "--serve", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=4 * 1024 * 1024,
            )
            self._process, self._loop, self._environment = process, loop, environment
            self._reader = loop.create_task(self._read_responses(process))
            return process

    async def aclose(self) -> None:
        process, reader, loop = self._process, self._reader, self._loop
        self._process = self._reader = None
        if process is None:
            return
        self._fail_pending("Local source process was closed.")
        if loop is not asyncio.get_running_loop():
            # The owning loop is gone, so its reader cannot finish; closing stdin makes the worker exit itself.
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        try:
            async with asyncio.timeout(2):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
        if reader is not None:
            await asyncio.gather(reader, return_exceptions=True)

    async def call(self, request: dict[str, object]) -> dict[str, object]:
        encoded = json.dumps({"id": 0, "request": request}).encode()
        if len(encoded) > self.request_limit:
            raise SourceFailure("Local source request exceeds its bounded size.")
        process = await self._ensure_worker()
        assert process.stdin is not None
        self._sequence += 1
        identifier = self._sequence
        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        try:
            if process.returncode is not None or self._process is not process:
                raise SourceFailure("Local source process exited before accepting the request.")
            process.stdin.write(json.dumps({"id": identifier, "request": request}).encode() + b"\n")
            await process.stdin.drain()
            async with asyncio.timeout(self.call_timeout):
                envelope = await future
        except (BrokenPipeError, ConnectionResetError) as error:
            raise SourceFailure("Local source process exited before accepting the request.") from error
        finally:
            self._pending.pop(identifier, None)
        if envelope.get("ok") is not True:
            attempted = envelope.get("write_attempted")
            raise SourceFailure(str(envelope.get("error", "Local source process failed.")),
                                write_attempted=attempted if isinstance(attempted, bool) else None)
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise SourceFailure("Local source result is not an object.")
        return result


class LiveSources:
    """Delegated, local-only reads. This is not a complete ReviewAdapters provider."""

    def __init__(self, credential: AsyncTokenCredential, bridge: Bridge, discovery: Discovery):
        self.credential, self.bridge, self.discovery = credential, bridge, discovery

    @classmethod
    async def open(cls, bridge: Bridge | None = None) -> "LiveSources":
        bridge = bridge or NodeSourceBridge()
        config = await bridge.call({"action": "configuration"})
        tenant = config.get("tenant_id")
        digest = config.get("configuration_digest")
        if not isinstance(tenant, str) or not isinstance(digest, str):
            raise SourceFailure("Source tenant or configuration digest is missing.")
        credential = AzureCliCredential(tenant_id=tenant, process_timeout=30)
        return await cls._connect(credential, bridge, config)

    @classmethod
    async def open_delegated(cls, credential: AsyncTokenCredential, bridge: Bridge) -> "LiveSources":
        config = await bridge.call({"action": "configuration"})
        return await cls._connect(credential, bridge, config)

    @classmethod
    async def _connect(
        cls, credential: AsyncTokenCredential, bridge: Bridge, config: dict[str, object],
    ) -> "LiveSources":
        try:
            tenant, digest = config.get("tenant_id"), config.get("configuration_digest")
            if not isinstance(tenant, str) or not isinstance(digest, str):
                raise SourceFailure("Source tenant or configuration digest is missing.")
            token = await credential.get_token("https://api.fabric.microsoft.com/.default")
            metadata = await bridge.call({
                "action": "discover", "configuration_digest": digest, "tokens": {"fabric": token.token},
            })
            discovery = Discovery.model_validate(metadata)
            if str(discovery.source.tenant_id) != tenant or discovery.source.configuration_digest != digest:
                raise SourceFailure("Discovery changed the configured source identity.")
            return cls(credential, bridge, discovery)
        except BaseException:
            await credential.close()
            raise

    async def close(self) -> None:
        await self.credential.close()

    async def _call(self, equipment_id: str, action: str) -> dict[str, object]:
        scopes = ("https://api.fabric.microsoft.com/.default",
                  "https://analysis.windows.net/powerbi/api/.default", f"{self.discovery.cluster}/.default")
        tokens = await asyncio.gather(*(self.credential.get_token(scope) for scope in scopes))
        return await self.bridge.call({
            "action": action, "equipment_id": equipment_id, "cluster": self.discovery.cluster,
            "configuration_digest": self.discovery.source.configuration_digest,
            "tokens": dict(zip(("fabric", "graphql", "kusto"), (token.token for token in tokens), strict=True)),
        })

    async def probe(self, equipment_id: str) -> ProbeResult:
        result = ProbeResult.model_validate(await self._call(equipment_id, "probe"))
        if result.source != self.discovery.source:
            raise SourceFailure("Probe returned a different source identity.")
        if {check.source for check in result.checks} != {"stid_telemetry", "work_orders"}:
            raise SourceFailure("Probe did not check each required source.")
        failed = {check.source for check in result.checks if check.status == "failed"}
        if {failure.source for failure in result.failures} != failed or len(result.failures) != len(failed):
            raise SourceFailure("Probe failures do not match the failed source checks.")
        if result.ready != (not result.failures and all(check.status == "passed" for check in result.checks)):
            raise SourceFailure("Probe readiness contradicts its source checks.")
        return result

    async def resolve_scope(self, question: str) -> dict[str, object] | None:
        fabric, graphql = await asyncio.gather(
            self.credential.get_token("https://api.fabric.microsoft.com/.default"),
            self.credential.get_token("https://analysis.windows.net/powerbi/api/.default"),
        )
        result = await self.bridge.call({
            "action": "resolve_identity_scope",
            "question": question,
            "configuration_digest": self.discovery.source.configuration_digest,
            "tokens": {"fabric": fabric.token, "graphql": graphql.token},
        })
        if result.get("matched") is False:
            return None
        scope = ResolvedScope.model_validate(result)
        if scope.source != self.discovery.source:
            raise SourceFailure("Semantic identity resolution changed the verified source identity.")
        return scope.model_dump(mode="json")

    async def read(self, request: ReviewRequest) -> Evidence:
        if request.source != self.discovery.source:
            raise SourceFailure("Requested source does not match the verified local source configuration.")
        result = await self._call(request.equipment_id, "read")
        return Evidence.model_validate({**result, "request": request})

    async def read_telemetry_only(self, request: ReviewRequest) -> Evidence:
        if request.source != self.discovery.source:
            raise SourceFailure("Requested source does not match the verified local source configuration.")
        result = await self._call(request.equipment_id, "telemetry_only")
        evidence = Evidence.model_validate({**result, "request": request})
        if "work_orders_not_requested" not in evidence.missing_sources or evidence.open_work_numbers:
            raise SourceFailure("Telemetry-only response misrepresented work-order coverage.")
        return evidence


class FabricBackendTools:
    """Read-only shared source tools, not a hosted chat/approval provider."""

    snapshot_tools = frozenset(("query_signal_quality_snapshot", "query_turbine_temperature_snapshot"))
    supported_tools = frozenset(("query_assets", "query_operations", "query_weather", "query_telemetry",
                                "query_work_backlog", "query_station_power",
                                "run_kql", "propose_work_order")) | snapshot_tools

    def __init__(self, sources: LiveSources):
        self.sources = sources

    def require_request(self, request: "ChatRequest") -> None:
        if request.source != self.sources.discovery.source:
            raise SourceFailure("Source identity changed.")

    async def approve(
        self, draft: WorkOrderDraft, edits: WorkOrderEdits, principal_id: UUID, creation_id: UUID,
        *, allow_create: bool,
    ) -> dict[str, object]:
        if draft.source != self.sources.discovery.source:
            raise SourceFailure("Approval differs from the freshly verified source identity.")
        tokens = {
            "fabric": (await self.sources.credential.get_token("https://api.fabric.microsoft.com/.default")).token,
            "graphql": (await self.sources.credential.get_token("https://analysis.windows.net/powerbi/api/.default")).token,
        }
        read = CatalogRead.model_validate(await self.sources.bridge.call({
            "action": "approve_work_order", "draft": draft.model_dump(mode="json"),
            "edits": edits.model_dump(mode="json"), "principal_id": str(principal_id),
            "creation_id": str(creation_id), "allow_create": allow_create, "human_approved": True,
            "tokens": tokens, "configuration_digest": draft.source.configuration_digest,
        }))
        record = read.result.get("record")
        if (read.source != draft.source or not draft.work_read_at <= read.completed_at <= utc_now()
                or not isinstance(record, dict)
                or record.get("id") != str(creation_id)
                or record.get("workOrderNumber") != f"WO-{creation_id}"
                or record.get("equipmentId") != draft.equipment_id
                or record.get("createdByOid") != str(principal_id)
                or record.get("instrumentId") != draft.instrument_id
                or record.get("opcuaNodeId") != draft.opcua_node_id
                or record.get("title") != edits.title.strip()
                or record.get("description") != edits.description.strip()
                or record.get("priority") != edits.priority or record.get("status") != "Draft"):
            raise SourceFailure("Created work readback does not match the exact approved identity and fields.")
        return record

    async def catalog(self, request: "ChatRequest") -> dict[str, object]:
        from .foundry_supervisor import SourceCatalog

        self.require_request(request)
        declared = SourceCatalog.model_validate(await self.sources.bridge.call({"action": "source_contracts"}))
        tools = tuple(tool for tool in declared.tools if tool.name in self.supported_tools)
        if {tool.name for tool in tools} != self.supported_tools:
            raise SourceFailure("The shared tool catalog is missing an implemented backend capability.")
        return SourceCatalog(tools=tools, context=declared.context).model_dump(mode="json")

    async def execute(
        self, name: str, arguments: dict[str, object], request: "ChatRequest",
    ) -> "ToolEvidence":
        from .foundry_supervisor import EvidenceDataset, ToolEvidence

        self.require_request(request)
        if name not in self.supported_tools:
            raise SourceFailure("This source tool has no configured backend implementation.")
        credential, discovery = self.sources.credential, self.sources.discovery
        tokens = {"fabric": (await credential.get_token("https://api.fabric.microsoft.com/.default")).token}
        if name in ("query_assets", "query_operations", "query_weather", "query_work_backlog", "query_telemetry",
                    "propose_work_order") or name in self.snapshot_tools:
            tokens["graphql"] = (await credential.get_token("https://analysis.windows.net/powerbi/api/.default")).token
        if name in ("query_telemetry", "query_station_power", "run_kql") or name in self.snapshot_tools:
            tokens["kusto"] = (await credential.get_token(f"{discovery.cluster}/.default")).token
        result = CatalogRead.model_validate(await self.sources.bridge.call({
            "action": "tool_read", "tool": name, "arguments": arguments, "tokens": tokens,
            "cluster": discovery.cluster, "configuration_digest": request.source.configuration_digest,
            "proposal_priority": request.proposal_priority,
        }))
        self.require_request(request)
        if result.source != request.source or not request.requested_at <= result.completed_at <= utc_now():
            raise SourceFailure("The source read changed identity or returned an invalid completion clock.")
        digest = sha256(json.dumps({"name": name, "arguments": arguments}, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        column_units: dict[str, str] = {}
        if name == "query_weather":
            column_units = {
                "precipitation": "mm", "cumulative_precipitation": "mm",
                "rainfall_volume_m3": "m3", "cumulative_rainfall_volume_m3": "m3",
                "temperature": "degC", "dew_point": "degC", "pressure": "hPa",
                "relative_humidity": "%", "solar_radiation": "W/m2",
                "wind_speed": "m/s", "wind_gust": "m/s", "wind_direction": "degree",
            }
        resolved: tuple[str, ...] = ()
        work_coverage: tuple[str, ...] = ()
        limitations: tuple[str, ...] = ()
        if name in self.snapshot_tools:
            column_units, resolved, work_coverage, limitations = self._snapshot_attestation(result.result)
        elif name == "query_work_backlog":
            column_units, resolved, work_coverage, limitations = self._work_backlog_attestation(result.result)
        elif name == "query_telemetry":
            column_units = self._telemetry_attestation(result.result)
        elif name == "query_station_power":
            column_units = self._station_power_attestation(result.result)
            semantics = result.result.get("semantics")
            completed_at = result.result.get("read_completed_at_utc")
            if not isinstance(semantics, str) or not semantics.strip() or len(semantics) > 1000:
                raise SourceFailure("Station power aggregation semantics are missing or invalid.")
            if not isinstance(completed_at, str) or not completed_at.strip() or len(completed_at) > 100:
                raise SourceFailure("Station power read-completion time is missing or invalid.")
            limitations = (
                f"Station power semantics: {semantics.strip()}",
                f"Station power source read completed at {completed_at.strip()}; compare each latest_event_time "
                "with this clock to assess freshness.",
            )
        public_result = dict(result.result)
        if name == "query_operations" and arguments.get("entity") == "work_orders":
            where = arguments.get("where")
            if isinstance(where, list):
                equipment_filters = []
                for item in where:
                    if not isinstance(item, dict) or item.get("column") != "equipmentId":
                        continue
                    value = item.get("value")
                    if item.get("op") == "eq" and isinstance(value, str) and value:
                        equipment_filters.append((item, (value,)))
                    elif (item.get("op") == "in" and isinstance(value, list) and value
                          and all(isinstance(candidate, str) and candidate for candidate in value)
                          and len(set(value)) == len(value)):
                        equipment_filters.append((item, tuple(value)))
                equipment_filter_items = [item for item, _equipment_ids in equipment_filters]
                other_filters = [item for item in where if item not in equipment_filter_items]
                open_status_filters = [
                    (item.get("column"), item.get("op"), item.get("value"))
                    for item in other_filters if isinstance(item, dict)
                ]
                required_open_status_filters = (
                    ("status", "neq", "Completed"),
                    ("status", "neq", "Cancelled"),
                )
                complete_scope = (
                    not other_filters
                    or (len(open_status_filters) == len(required_open_status_filters)
                        and all(item in open_status_filters for item in required_open_status_filters))
                )
                if (len(equipment_filters) == 1 and complete_scope and "limit" not in arguments
                        and public_result.get("truncated") is False):
                    work_coverage = equipment_filters[0][1]
        if name == "propose_work_order":
            draft = self._work_draft(public_result, request, arguments, result.completed_at)
            public_result["proposal"] = draft.model_dump(mode="json")
            public_result["proposal_digest"] = draft.digest()
            resolved = work_coverage = (draft.equipment_id,)
        raw_datasets = public_result.pop("_evidence_datasets", None)
        if not isinstance(raw_datasets, list) or not raw_datasets:
            raise SourceFailure("Source read omitted its governed evidence dataset descriptors.")
        datasets = tuple(EvidenceDataset.model_validate(item) for item in raw_datasets)
        if any(
            dataset.field not in public_result
            or not isinstance(public_result[dataset.field], list)
            for dataset in datasets
        ):
            raise SourceFailure("Evidence dataset descriptors do not match returned source collections.")
        identities = public_result.pop("_row_identities", {})
        if not isinstance(identities, dict):
            raise SourceFailure("Private source row identities are malformed.")
        rows = public_result.get("rows")
        if identities and (not isinstance(rows, list)
                           or set(identities) != {f"/rows/{index}" for index in range(len(rows))}):
            raise SourceFailure("Private source row identities do not match the returned rows.")
        return ToolEvidence(id=f"{request.run_id}:{digest}", source=result.source, tool=name,
                            arguments=arguments, completed_at=result.completed_at, result=public_result,
                            row_identities=identities,
                            column_units=column_units, resolved_equipment_ids=resolved,
                            work_coverage_equipment_ids=work_coverage, limitations=limitations,
                            datasets=datasets)

    @staticmethod
    def _work_draft(
        result: dict[str, object], request: "ChatRequest", arguments: dict[str, object], completed_at: AwareDatetime,
    ) -> WorkOrderDraft:
        proposal, work = result.get("proposal"), result.get("existing_work")
        if (not isinstance(proposal, dict) or not isinstance(work, list)
                or result.get("staged") is not True or result.get("confirmation_required") is not True
                or result.get("production_write_executed") is not False):
            raise SourceFailure("Proposal did not attest an in-memory draft with complete work coverage.")
        values = {
            "equipment_id": proposal.get("equipmentId"), "instrument_id": proposal.get("instrumentId"),
            "opcua_node_id": proposal.get("opcuaNodeId"), "title": proposal.get("title"),
            "description": proposal.get("description"), "priority": proposal.get("priority"),
        }
        if (values["equipment_id"] != arguments.get("equipment_id")
                or values["priority"] != request.proposal_priority
                or any(arguments.get(key) is not None and values[key] != arguments[key]
                       for key in ("instrument_id", "opcua_node_id"))
                or values["title"] != str(arguments.get("title", "")).strip()
                or values["description"] != str(arguments.get("description", "")).strip()):
            raise SourceFailure("Proposal changed its resolved target, operator priority or requested content.")
        work_clock = result.get("work_read_at")
        draft_id = uuid5(request.run_id, json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False))
        from pydantic import TypeAdapter
        clock = TypeAdapter(AwareDatetime).validate_python(work_clock)
        if not request.requested_at <= clock <= completed_at:
            raise SourceFailure("Proposal work coverage is not fresh for this invocation.")
        return WorkOrderDraft.model_validate({
            **values, "id": draft_id, "run_id": request.run_id, "source": request.source,
            "work_read_at": clock, "expires_at": clock + timedelta(minutes=15), "existing_work": work,
        })

    @staticmethod
    def _station_power_attestation(result: dict[str, object]) -> dict[str, str]:
        rows = result.get("rows")
        if (not isinstance(rows, list) or type(result.get("row_count")) is not int
                or result.get("row_count") != len(rows) or result.get("truncated") is not False):
            raise SourceFailure("Station power omitted complete source rows.")
        stations: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise SourceFailure("Station power returned an invalid source row.")
            station, value = row.get("Station"), row.get("average_power_MW")
            samples, bad_samples = row.get("samples"), row.get("bad_samples")
            if (not isinstance(station, str) or not station.strip() or station in stations
                    or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0
                    or isinstance(bad_samples, bool) or not isinstance(bad_samples, int)
                    or not 0 <= bad_samples <= samples):
                raise SourceFailure("Station power requires unique stations, finite MW values and valid sample counts.")
            stations.add(station)
        # The shared source calculation converts authoritative W/kW/MW/GW metadata to MW.
        return {"average_power_MW": "MW"} if rows else {}

    @staticmethod
    def _work_backlog_attestation(
        result: dict[str, object],
    ) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        rows, orders, unmatched = result.get("rows"), result.get("open_work_orders"), result.get("unmatched")
        grouping = result.get("group_by")
        presentation = result.get("presentation")
        semantics, completed_at = result.get("semantics"), result.get("read_completed_at_utc")
        if (not isinstance(rows, list) or not isinstance(orders, list) or not isinstance(unmatched, list)
                or grouping not in {"equipment", "facility"}
                or presentation != {
                    "table_field": "rows",
                    "chart": {
                        "kind": "bar",
                        "x_key": "facility_id" if grouping == "facility" else "equipment_id",
                        "y_keys": ["open_work_order_count"],
                    },
                }
                or not isinstance(semantics, str) or not semantics.strip()
                or not isinstance(completed_at, str) or not completed_at.strip()):
            raise SourceFailure("Work backlog omitted grouping, rows, coverage, semantics or completion time.")
        group_ids: set[str] = set()
        total_orders = 0
        for row in rows:
            if not isinstance(row, dict):
                raise SourceFailure("Work backlog returned an invalid grouped row.")
            group_id = row.get("facility_id" if grouping == "facility" else "equipment_id")
            order_count = row.get("open_work_order_count")
            equipment_count = row.get("equipment_with_open_work") if grouping == "facility" else None
            if (not isinstance(group_id, str) or not group_id.strip() or group_id in group_ids
                    or isinstance(order_count, bool) or not isinstance(order_count, int)
                    or order_count < 0
                    or (grouping == "facility" and (
                        isinstance(equipment_count, bool) or not isinstance(equipment_count, int)
                        or equipment_count < 0 or equipment_count > order_count
                    ))):
                raise SourceFailure("Work backlog requires unique groups and valid nonnegative counts.")
            group_ids.add(group_id)
            total_orders += order_count
        equipment_ids: list[str] = []
        work_numbers: set[str] = set()
        for order in orders:
            if not isinstance(order, dict):
                raise SourceFailure("Work backlog returned an invalid work-order row.")
            equipment_id, work_number, status = (
                order.get("equipmentId"), order.get("workOrderNumber"), order.get("status"),
            )
            if (not isinstance(equipment_id, str) or not equipment_id.strip()
                    or not isinstance(work_number, str) or not work_number.strip() or work_number in work_numbers
                    or not isinstance(status, str) or status.lower() in {"completed", "cancelled"}):
                raise SourceFailure("Work backlog coverage contains invalid or closed work.")
            work_numbers.add(work_number)
            if equipment_id not in equipment_ids:
                equipment_ids.append(equipment_id)
        if total_orders != len(orders):
            raise SourceFailure("Work backlog grouped counts do not conserve the open-work inventory.")
        limitations = (
            f"Work backlog semantics: {semantics.strip()}",
            f"Work backlog source reads completed at {completed_at.strip()}; cross-source reads are not atomic.",
        )
        if unmatched:
            limitations += ("Some work orders could not be mapped to a returned facility and remain in UNMAPPED.",)
        identities = tuple(equipment_ids)
        units = {"open_work_order_count": "work orders"}
        if grouping == "facility":
            units["equipment_with_open_work"] = "equipment"
        return (
            units,
            identities,
            identities,
            limitations,
        )

    @staticmethod
    def _telemetry_attestation(result: dict[str, object]) -> dict[str, str]:
        # A series is chartable only when every row carries the same registered instrument unit.
        rows = result.get("rows")
        if not isinstance(rows, list) or not rows:
            return {}
        units = {row.get("unit") for row in rows if isinstance(row, dict)}
        if len(units) != 1 or not all(isinstance(row, dict) for row in rows):
            return {}
        unit = next(iter(units))
        return {"value": unit.strip()} if isinstance(unit, str) and unit.strip() else {}

    @staticmethod
    def _snapshot_attestation(
        result: dict[str, object],
    ) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        population, rows = result.get("population"), result.get("rows")
        if (not isinstance(population, dict) or population.get("inventory_complete") is not True
                or population.get("work_inventory_complete") is not True or not isinstance(rows, list)
                or result.get("row_count") != len(rows) or result.get("truncated") is not False):
            raise SourceFailure("Snapshot omitted complete metadata/work population evidence.")
        coverage = population.get("work_coverage_equipment_ids")
        if (not isinstance(coverage, list) or any(not isinstance(value, str) or not value.strip() for value in coverage)
                or len(set(coverage)) != len(coverage)):
            raise SourceFailure("Snapshot work coverage identities are invalid.")
        resolved: list[str] = []
        units: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise SourceFailure("Snapshot returned an invalid source row.")
            equipment, unit, value = row.get("equipment_id"), row.get("unit"), row.get("value")
            if (not isinstance(equipment, str) or not equipment.strip() or equipment not in coverage
                    or not isinstance(unit, str) or not unit.strip() or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or not isinstance(row.get("open_work_orders"), list)
                    or any(not isinstance(row.get(key), str) or not row[key].strip()
                           for key in ("instrument_id", "opcua_node_id"))):
                raise SourceFailure("Snapshot value, units, resolved identity or work coverage is invalid.")
            if equipment not in resolved:
                resolved.append(equipment)
            units.add(unit)
        limitations = []
        if population.get("signals_without_readings"):
            limitations.append("Some mapped signals have no reading in the requested window.")
        if result.get("unresolved_nodes"):
            limitations.append("Some telemetry nodes could not be resolved to active metadata.")
        # A mixed-unit BAD fleet cannot attest a single value-column unit for charts.
        column_units = {"value": next(iter(units))} if len(units) == 1 else {}
        identities = tuple(resolved)
        return column_units, identities, identities, tuple(limitations)


async def probe_live_sources(equipment_id: str) -> bool:
    sources = await LiveSources.open()
    try:
        result = await sources.probe(equipment_id)
        print(result.model_dump_json(indent=2))
        return result.ready
    finally:
        await sources.close()
