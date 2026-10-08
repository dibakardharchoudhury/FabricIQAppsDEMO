import asyncio
import json
import os
from pathlib import Path
from typing import Literal, Protocol

from azure.identity.aio import AzureCliCredential
from pydantic import Field

from .contracts import Contract, Evidence, ReviewRequest, SourceIdentity


class SourceFailure(RuntimeError):
    pass


class Discovery(Contract):
    source: SourceIdentity
    cluster: str
    database: str


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
        try:
            envelope = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SourceFailure("Local source process returned an invalid response; output suppressed.") from error
        if not isinstance(envelope, dict):
            raise SourceFailure("Local source process did not return an object.")
        if process.returncode != 0 or envelope.get("ok") is not True:
            raise SourceFailure(str(envelope.get("error", "Local source process failed.")))
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise SourceFailure("Local source result is not an object.")
        return result


class LiveSources:
    """Delegated, local-only reads. This is not a complete ReviewAdapters provider."""

    def __init__(self, credential: AzureCliCredential, bridge: Bridge, discovery: Discovery):
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
        try:
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


async def probe_live_sources(equipment_id: str) -> bool:
    sources = await LiveSources.open()
    try:
        result = await sources.probe(equipment_id)
        print(result.model_dump_json(indent=2))
        return result.ready
    finally:
        await sources.close()
