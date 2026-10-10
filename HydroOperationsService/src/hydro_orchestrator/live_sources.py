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

from .contracts import Contract, Evidence, ReviewRequest, SourceIdentity, WorkOrderDraft, WorkOrderEdits, utc_now

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
    result_marker = b"\n__HYDRO_RESULT__="

    def __init__(self, node: str | None = None):
        self.node = node or os.environ.get("HYDRO_LOCAL_NODE", "node")
        self.script = Path(__file__).resolve().parents[3] / "HydroOperationsApp" / "scripts" / "local-fabric-sources.mjs"

    async def call(self, request: dict[str, object]) -> dict[str, object]:
        process = await asyncio.create_subprocess_exec(
            self.node, str(self.script), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(75):
                stdout, _stderr = await process.communicate(json.dumps(request).encode())
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if len(stdout) > 2 * 1024 * 1024:
            raise SourceFailure("Local source process response exceeds its bounded size.")
        _prefix, marker, payload = stdout.rpartition(self.result_marker)
        if not marker:
            raise SourceFailure("Local source process returned an invalid response; output suppressed.")
        try:
            envelope = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SourceFailure("Local source process returned an invalid response; output suppressed.") from error
        if not isinstance(envelope, dict):
            raise SourceFailure("Local source process did not return an object.")
        if process.returncode != 0 or envelope.get("ok") is not True:
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
    supported_tools = frozenset(("query_assets", "query_operations", "query_telemetry",
                                "query_station_power", "run_kql", "propose_work_order")) | snapshot_tools

    def __init__(self, sources: LiveSources):
        self.sources = sources

    def require_request(self, request: "ChatRequest") -> None:
        if request.source != self.sources.discovery.source or utc_now() >= request.deadline:
            raise SourceFailure("Source identity changed or the source request deadline expired.")

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
        from .foundry_supervisor import ToolEvidence

        self.require_request(request)
        if name not in self.supported_tools:
            raise SourceFailure("This source tool has no configured backend implementation.")
        credential, discovery = self.sources.credential, self.sources.discovery
        tokens = {"fabric": (await credential.get_token("https://api.fabric.microsoft.com/.default")).token}
        if name in ("query_assets", "query_operations", "propose_work_order") or name in self.snapshot_tools:
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
        resolved: tuple[str, ...] = ()
        work_coverage: tuple[str, ...] = ()
        limitations: tuple[str, ...] = ()
        if name in self.snapshot_tools:
            column_units, resolved, work_coverage, limitations = self._snapshot_attestation(result.result)
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
                equipment_filters = [
                    item for item in where if isinstance(item, dict)
                    and item.get("column") == "equipmentId" and item.get("op") == "eq"
                    and isinstance(item.get("value"), str) and item["value"]
                ]
                other_filters = [item for item in where if item not in equipment_filters]
                open_status_filters = {
                    (item.get("column"), item.get("op"), item.get("value"))
                    for item in other_filters if isinstance(item, dict)
                }
                complete_scope = (
                    not other_filters
                    or open_status_filters == {
                        ("status", "neq", "Completed"),
                        ("status", "neq", "Cancelled"),
                    }
                )
                if (len(equipment_filters) == 1 and complete_scope and "limit" not in arguments
                        and public_result.get("truncated") is False):
                    work_coverage = (equipment_filters[0]["value"],)
        if name == "propose_work_order":
            draft = self._work_draft(public_result, request, arguments, result.completed_at)
            public_result["proposal"] = draft.model_dump(mode="json")
            public_result["proposal_digest"] = draft.digest()
            resolved = work_coverage = (draft.equipment_id,)
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
                            work_coverage_equipment_ids=work_coverage, limitations=limitations)

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
    def _snapshot_attestation(
        result: dict[str, object],
    ) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        population, rows = result.get("population"), result.get("rows")
        if (not isinstance(population, dict) or population.get("inventory_complete") is not True
                or population.get("work_inventory_complete") is not True or not isinstance(rows, list)
                or result.get("row_count") != len(rows)):
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
        if result.get("truncated"):
            limitations.append("Snapshot output was truncated; returned rows are not the full selected population.")
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
