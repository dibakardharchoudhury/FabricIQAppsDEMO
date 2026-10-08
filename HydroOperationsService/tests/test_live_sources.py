import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from azure.core.credentials import AccessToken
from azure.identity.aio import AzureCliCredential
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hydro_orchestrator.contracts import ReviewRequest
from hydro_orchestrator.live_sources import Discovery, LiveSources, NodeSourceBridge, SourceFailure
from hydro_orchestrator.service import create_app
from test_durable_service import BODY, HEADERS, SOURCE, TOKEN, TestAdapters, wait_for


def reader():
    bridge = AsyncMock()
    credential = AsyncMock(spec=AzureCliCredential)
    credential.get_token.return_value = AccessToken("synthetic-token-never-log", 9999999999)
    discovery = Discovery(
        source=SOURCE.model_copy(update={"configuration_digest": "a" * 64}),
        cluster="https://test.z.kusto.fabric.microsoft.com", database="test",
    )
    return LiveSources(credential, bridge, discovery)


class LiveSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_configuration_blocks_reads_before_auth_or_io(self):
        sources = reader()
        request = ReviewRequest(**BODY, source=SOURCE, run_id=uuid4())
        with self.assertRaisesRegex(SourceFailure, "does not match"):
            await sources.read(request)
        sources.credential.get_token.assert_not_awaited()
        sources.bridge.call.assert_not_awaited()

    async def test_exchange_failure_does_not_become_empty_work(self):
        sources = reader()
        sources.bridge.call.side_effect = SourceFailure("EXCHANGE_NOT_ENABLED")
        request = ReviewRequest(**BODY, source=sources.discovery.source, run_id=uuid4())
        with self.assertRaisesRegex(SourceFailure, "EXCHANGE_NOT_ENABLED"):
            await sources.read(request)
        self.assertEqual(sources.credential.get_token.await_count, 3)
        self.assertEqual(sources.bridge.call.call_args.args[0]["action"], "read")

    async def test_live_evidence_remains_bound_to_request(self):
        sources = reader()
        request = ReviewRequest(**BODY, source=sources.discovery.source, run_id=uuid4())
        evidence = await TestAdapters().read(request)
        payload = evidence.model_dump(mode="json", exclude={"request"})
        sources.bridge.call.return_value = payload
        self.assertEqual(await sources.read(request), evidence)
        payload["equipment_id"] = "another-asset"
        with self.assertRaises(ValidationError):
            await sources.read(request)

    async def test_probe_cannot_claim_readiness_over_failed_or_missing_sources(self):
        sources = reader()
        payload = {
            "source": sources.discovery.source.model_dump(mode="json"), "ready": False,
            "checks": [{"source": "stid_telemetry", "status": "passed"}, {"source": "work_orders", "status": "failed"}],
            "failures": [{"source": "work_orders", "message": "EXCHANGE_NOT_ENABLED"}],
        }
        sources.bridge.call.return_value = payload
        self.assertFalse((await sources.probe(BODY["equipment_id"])).ready)
        payload["ready"] = True
        with self.assertRaisesRegex(SourceFailure, "contradicts"):
            await sources.probe(BODY["equipment_id"])
        payload["ready"] = False
        payload["failures"] = []
        with self.assertRaisesRegex(SourceFailure, "do not match"):
            await sources.probe(BODY["equipment_id"])

    async def test_bridge_suppresses_invalid_child_output(self):
        process = AsyncMock()
        process.returncode = 1
        process.communicate.return_value = (b"private child output", b"private diagnostic output")
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaisesRegex(SourceFailure, "output suppressed") as caught:
                await NodeSourceBridge().call({"action": "configuration"})
        self.assertNotIn("private", str(caught.exception))

    async def test_bridge_cancellation_terminates_only_its_owned_process(self):
        process = AsyncMock()
        process.returncode = None
        process.communicate.side_effect = asyncio.CancelledError()
        from unittest.mock import Mock
        process.kill = Mock()
        with patch("asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaises(asyncio.CancelledError):
                await NodeSourceBridge().call({"action": "configuration"})
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()


class LiveFailureWorkflowTests(unittest.TestCase):
    def test_source_failure_cannot_reach_investigation_or_approval(self):
        sources = reader()
        sources.bridge.call.side_effect = SourceFailure("EXCHANGE_NOT_ENABLED")

        class Adapters(TestAdapters):
            async def read(self, request):
                return await sources.read(request)

        adapters = Adapters()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory), TOKEN, adapters=adapters, source=sources.discovery.source)
            with TestClient(app) as client:
                run_id = str(uuid4())
                client.post("/runs", json=BODY, headers={**HEADERS, "Idempotency-Key": run_id}).raise_for_status()
                run = wait_for(client, run_id, "failed")
                self.assertIn("EXCHANGE_NOT_ENABLED", run["error"])
                self.assertNotIn("synthetic-token", run["error"])
                self.assertIsNone(run["proposal"])
                self.assertEqual((adapters.investigations, adapters.proposals), (0, 0))
                result = client.post(f"/runs/{run_id}/approval", json={
                    "approved": True, "proposal_digest": "a" * 64,
                }, headers=HEADERS)
                self.assertEqual(result.status_code, 409)


if __name__ == "__main__":
    unittest.main()
