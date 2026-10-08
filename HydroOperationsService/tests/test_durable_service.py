import asyncio
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import httpx
import uvicorn
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from agent_framework import FileCheckpointStorage
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hydro_orchestrator.contracts import (
    Approval, Assessment, Evidence, Observation, Proposal, ReviewRequest, SourceIdentity, utc_now,
)
from hydro_orchestrator.service import LocalRunner, create_app
from hydro_orchestrator.store import Conflict, Store


TOKEN = "local-validation-test-token-not-a-secret"
SOURCE = SourceIdentity(tenant_id=uuid4(), workspace_id=uuid4(), ontology_id=uuid4(), generation=2)
BODY = {"equipment_id": "TEST_T005", "title": "Local validation only", "priority": "Low"}
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class TestAdapters:
    """Synthetic provider; never contacts Foundry, Fabric or production SQL."""

    def __init__(self):
        self.reads = self.investigations = self.proposals = 0
        self.fail_investigation = False
        self.delay = 0

    async def read(self, request: ReviewRequest) -> Evidence:
        self.reads += 1
        await asyncio.sleep(self.delay)
        clock = utc_now()
        return Evidence(
            request=request, source=request.source, equipment_id=request.equipment_id,
            read_completed_at=clock,
            observations=(Observation(
                evidence_id="test-reading", metric="temperature", value=75.0, unit="C",
                event_time=clock - timedelta(hours=2), quality="BAD",
            ),),
            open_work_numbers=("TEST-WO-1",), missing_sources=("inspections",),
        )

    async def investigate(self, evidence: Evidence) -> Assessment:
        self.investigations += 1
        if self.fail_investigation:
            raise OSError("Injected source/model outage")
        return Assessment(
            evidence=evidence, observations=("test-reading",),
            hypotheses=("sensor_or_ingestion", "equipment_condition"),
            missing_evidence=("fresh_measurements", "approved_limits", "inspection_results"),
        )

    async def propose(self, assessment: Assessment) -> Proposal:
        self.proposals += 1
        return Proposal(
            request=assessment.evidence.request, assessment=assessment,
            description="Operator-requested inspection; no physical cause established.",
            expires_at=utc_now() + timedelta(minutes=15),
        )


def wait_for(client: TestClient, run_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}", headers=HEADERS)
        response.raise_for_status()
        run = response.json()
        if run["status"] == expected:
            return run
        if run["status"] == "failed":
            raise AssertionError(run["error"])
        time.sleep(0.02)
    raise AssertionError(f"Run did not reach {expected}: {run}")


def decision(run: dict, approved: bool = True) -> dict:
    proposal = Proposal.model_validate(run["proposal"])
    return Approval(approved=approved, proposal_digest=proposal.digest()).model_dump()


class DurableApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.adapters = TestAdapters()

    def tearDown(self):
        self.temp.cleanup()

    def app(self):
        return create_app(self.root, TOKEN, adapters=self.adapters, source=SOURCE)

    def submit(self, client: TestClient, run_id: str) -> dict:
        response = client.post("/runs", json=BODY, headers={**HEADERS, "Idempotency-Key": run_id})
        self.assertEqual(response.status_code, 202, response.text)
        return wait_for(client, run_id, "waiting")

    def test_restart_restores_approval_without_repeating_completed_agents(self):
        run_id = str(uuid4())
        with TestClient(self.app()) as first:
            waiting = self.submit(first, run_id)
            self.assertEqual(waiting["proposal"]["assessment"]["conclusion"], "cause_undetermined")
            self.assertEqual(waiting["proposal"]["request"]["priority"], "Low")
            events = first.get(f"/runs/{run_id}/events", headers=HEADERS).json()
            self.assertTrue(events)
            cursor = events[-1]["sequence"]
        self.assertEqual((self.adapters.reads, self.adapters.investigations, self.adapters.proposals), (1, 1, 1))
        self.adapters = TestAdapters()
        with TestClient(self.app()) as second:
            response = second.post(f"/runs/{run_id}/approval", json=decision(waiting), headers=HEADERS)
            self.assertEqual(response.status_code, 202, response.text)
            completed = wait_for(second, run_id, "completed")
            self.assertFalse(completed["outcome"]["production_write_executed"])
            self.assertEqual(completed["outcome"]["status"], "validation_work_recorded")
            self.assertEqual((self.adapters.reads, self.adapters.investigations, self.adapters.proposals), (0, 0, 0))
            subsequent = second.get(f"/runs/{run_id}/events?after={cursor}", headers=HEADERS).json()
            self.assertTrue(subsequent)
            self.assertTrue(all(event["sequence"] > cursor for event in subsequent))

    def test_duplicate_submissions_and_approvals_produce_one_validation_write(self):
        run_id = str(uuid4())
        with TestClient(self.app()) as client:
            waiting = self.submit(client, run_id)
            self.submit(client, run_id)
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(
                    lambda _: client.post(f"/runs/{run_id}/approval", json=decision(waiting), headers=HEADERS),
                    range(8),
                ))
            self.assertTrue(all(response.status_code == 202 for response in responses))
            wait_for(client, run_id, "completed")
            changed = client.post(f"/runs/{run_id}/approval", json=decision(waiting, False), headers=HEADERS)
            self.assertEqual(changed.status_code, 409)
        with closing(sqlite3.connect(self.root / "runs.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM validation_work").fetchone()[0], 1)
        self.assertEqual(self.adapters.reads, 1)

    def test_rejection_never_writes_and_cannot_be_reversed(self):
        run_id = str(uuid4())
        with TestClient(self.app()) as client:
            waiting = self.submit(client, run_id)
            client.post(f"/runs/{run_id}/approval", json=decision(waiting, False), headers=HEADERS).raise_for_status()
            completed = wait_for(client, run_id, "completed")
            self.assertEqual(completed["outcome"]["status"], "rejected")
            self.assertEqual(client.post(
                f"/runs/{run_id}/approval", json=decision(waiting), headers=HEADERS
            ).status_code, 409)
        with closing(sqlite3.connect(self.root / "runs.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM validation_work").fetchone()[0], 0)

    def test_auth_unconfigured_sources_and_idempotency_mismatch_fail_explicitly(self):
        with TestClient(create_app(self.root, TOKEN)) as client:
            self.assertEqual(client.get("/readyz").status_code, 401)
            self.assertEqual(client.get("/readyz", headers=HEADERS).status_code, 503)
            self.assertFalse(client.get("/healthz").json()["live_fabric_connected"])
            self.assertEqual(client.post(
                "/runs", json=BODY, headers={**HEADERS, "Idempotency-Key": str(uuid4())}
            ).status_code, 503)
        with TestClient(self.app()) as client:
            run_id = str(uuid4())
            waiting = self.submit(client, run_id)
            self.assertEqual(client.post(
                "/runs", json={**BODY, "priority": "High"},
                headers={**HEADERS, "Idempotency-Key": run_id},
            ).status_code, 409)
            self.assertEqual(client.post(
                f"/runs/{run_id}/approval",
                json={**decision(waiting), "proposal_digest": "0" * 64}, headers=HEADERS,
            ).status_code, 409)
            self.assertEqual(client.post(
                "/runs", json={**BODY, "tenant_id": str(uuid4())},
                headers={**HEADERS, "Idempotency-Key": str(uuid4())},
            ).status_code, 422)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.adapters = TestAdapters()
        self.request = ReviewRequest(**BODY, source=SOURCE, run_id=uuid4())
        self.store.create(self.request)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_failed_step_retries_from_checkpoint_not_from_start(self):
        self.adapters.fail_investigation = True
        runner = LocalRunner(self.store, self.adapters, SOURCE)
        with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
            await runner.execute(self.request.run_id)
        self.assertEqual(self.store.get(self.request.run_id).status, "failed")
        self.assertEqual(self.adapters.reads, 1)
        self.adapters.fail_investigation = False
        self.store.retry(self.request.run_id)
        await LocalRunner(Store(self.root), self.adapters, SOURCE).execute(self.request.run_id)
        self.assertEqual(self.store.get(self.request.run_id).status, "waiting")
        self.assertEqual(self.adapters.reads, 1)
        self.assertEqual(self.adapters.investigations, 2)

    async def test_timeout_is_failed_not_empty_evidence_and_retries_are_bounded(self):
        self.adapters.delay = 0.1
        runner = LocalRunner(self.store, self.adapters, SOURCE, step_timeout=0.01)
        for attempt in range(3):
            if attempt:
                self.store.retry(self.request.run_id)
            with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
                await runner.execute(self.request.run_id)
            failed = self.store.get(self.request.run_id)
            self.assertEqual(failed.status, "failed")
            self.assertIsNone(failed.proposal)
        with self.assertRaises(Conflict):
            self.store.retry(self.request.run_id)

    async def test_source_change_prevents_resuming_old_evidence(self):
        runner = LocalRunner(
            self.store, self.adapters,
            SourceIdentity(**{**SOURCE.model_dump(), "workspace_id": uuid4()}),
        )
        with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
            await runner.execute(self.request.run_id)
        self.assertEqual(self.store.get(self.request.run_id).status, "failed")
        self.assertEqual(self.adapters.reads, 0)

    async def test_crash_after_atomic_write_does_not_duplicate_on_replay(self):
        await LocalRunner(self.store, self.adapters, SOURCE).execute(self.request.run_id)
        waiting = self.store.get(self.request.run_id)
        approval = Approval(approved=True, proposal_digest=waiting.proposal.digest())
        self.store.approve(self.request.run_id, approval)

        class CrashAfterWrite(Store):
            def record_decision(self, proposal, approval):
                super().record_decision(proposal, approval)
                raise OSError("Injected crash after committed validation write")

        with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
            await LocalRunner(CrashAfterWrite(self.root), self.adapters, SOURCE).execute(self.request.run_id)
        self.store.retry(self.request.run_id)
        await LocalRunner(Store(self.root), self.adapters, SOURCE).execute(self.request.run_id)
        self.assertEqual(self.store.get(self.request.run_id).status, "completed")
        with closing(sqlite3.connect(self.root / "runs.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM validation_work").fetchone()[0], 1)

    async def test_contracts_reject_fabricated_refs_causes_units_and_generation(self):
        evidence = await self.adapters.read(self.request)
        assessment = await self.adapters.investigate(evidence)
        for change in ({"observations": ("invented",)}, {"conclusion": "bearing_failure"}):
            with self.assertRaises(ValidationError):
                Assessment.model_validate({**assessment.model_dump(), **change})
        for generation in (1, "2", 2.0, True):
            with self.assertRaises(ValidationError):
                SourceIdentity.model_validate({**SOURCE.model_dump(), "generation": generation})
        for change in ({"unit": ""}, {"value": float("nan")}, {"event_time": "2026-10-08T12:00:00"}):
            with self.assertRaises(ValidationError):
                Observation.model_validate({**evidence.observations[0].model_dump(), **change})
        with self.assertRaises(ValidationError):
            Evidence.model_validate({**evidence.model_dump(), "equipment_id": "WRONG"})

    async def test_checkpoint_save_failure_stops_before_read_or_write(self):
        with patch.object(FileCheckpointStorage, "save", new=AsyncMock(side_effect=OSError("Disk unavailable"))):
            with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
                await LocalRunner(self.store, self.adapters, SOURCE).execute(self.request.run_id)
        self.assertEqual(self.store.get(self.request.run_id).status, "failed")
        self.assertEqual(self.adapters.reads, 0)

    async def test_recovery_after_final_checkpoint_before_run_journal_completion(self):
        await LocalRunner(self.store, self.adapters, SOURCE).execute(self.request.run_id)
        waiting = self.store.get(self.request.run_id)
        self.store.approve(
            self.request.run_id, Approval(approved=True, proposal_digest=waiting.proposal.digest())
        )

        class CrashBeforeJournal(Store):
            def finish(self, outcome):
                raise OSError("Injected interruption after final framework checkpoint")

        with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
            await LocalRunner(CrashBeforeJournal(self.root), self.adapters, SOURCE).execute(self.request.run_id)
        self.store.retry(self.request.run_id)
        await LocalRunner(Store(self.root), self.adapters, SOURCE).execute(self.request.run_id)
        self.assertEqual(self.store.get(self.request.run_id).status, "completed")
        self.assertEqual(self.adapters.reads, 1)

    async def test_corrupt_checkpoint_cannot_silently_resume_an_older_snapshot(self):
        await LocalRunner(self.store, self.adapters, SOURCE).execute(self.request.run_id)
        waiting = self.store.get(self.request.run_id)
        self.store.approve(
            self.request.run_id, Approval(approved=True, proposal_digest=waiting.proposal.digest())
        )
        checkpoint = next((self.root / "checkpoints" / str(self.request.run_id)).glob("*.json"))
        checkpoint.write_text("{invalid", encoding="utf-8")
        with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
            await LocalRunner(self.store, self.adapters, SOURCE).execute(self.request.run_id)
        self.assertEqual(self.store.get(self.request.run_id).status, "failed")
        with closing(sqlite3.connect(self.root / "runs.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM validation_work").fetchone()[0], 0)

    async def test_expired_approval_is_rejected_but_rejection_is_still_allowed(self):
        await LocalRunner(self.store, self.adapters, SOURCE).execute(self.request.run_id)
        waiting = self.store.get(self.request.run_id)
        digest = waiting.proposal.digest()
        with patch("hydro_orchestrator.store.utc_now", return_value=utc_now() + timedelta(hours=1)):
            with self.assertRaises(Conflict):
                self.store.approve(self.request.run_id, Approval(approved=True, proposal_digest=digest))
            self.store.approve(self.request.run_id, Approval(approved=False, proposal_digest=digest))

    async def test_unexpected_adapter_error_is_visible_not_stuck_running(self):
        with patch.object(self.adapters, "read", new=AsyncMock(side_effect=TypeError("Adapter bug"))):
            runner = LocalRunner(self.store, self.adapters, SOURCE)
            with self.assertLogs("hydro_orchestrator.service", level="ERROR"):
                runner.schedule(self.request.run_id)
                for _ in range(100):
                    if self.store.get(self.request.run_id).status == "failed":
                        break
                    await asyncio.sleep(0.02)
                await runner.close()
        self.assertEqual(self.store.get(self.request.run_id).status, "failed")
        self.assertIn("TypeError", self.store.get(self.request.run_id).error)

    async def test_hard_process_exit_recovers_persisted_evidence_in_new_process(self):
        script = """
import asyncio, os, sys
from pathlib import Path
from test_durable_service import TestAdapters
from hydro_orchestrator.contracts import ReviewRequest, Approval
from hydro_orchestrator.service import LocalRunner
from hydro_orchestrator.store import Store
root, request, phase = Path(sys.argv[1]), ReviewRequest.model_validate_json(sys.argv[2]), sys.argv[3]
class Crash(TestAdapters):
    async def investigate(self, evidence):
        os._exit(17)
class NoRepeatedRead(TestAdapters):
    async def read(self, request):
        raise RuntimeError("Completed source read was incorrectly repeated after restart")
store = Store(root)
adapter = Crash() if phase == "crash" else NoRepeatedRead()
runner = LocalRunner(store, adapter, request.source)
asyncio.run(runner.execute(request.run_id))
run = store.get(request.run_id)
assert run.status == "waiting", run.error
store.approve(request.run_id, Approval(approved=True, proposal_digest=run.proposal.digest()))
asyncio.run(runner.execute(request.run_id))
assert store.get(request.run_id).status == "completed", store.get(request.run_id).error
"""
        command = [sys.executable, "-c", script, str(self.root), self.request.model_dump_json()]
        first = await asyncio.to_thread(
            subprocess.run, [*command, "crash"], cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(first.returncode, 17, first.stderr)
        self.assertEqual(self.store.get(self.request.run_id).status, "running")
        recovered = await asyncio.to_thread(
            subprocess.run, [*command, "resume"], cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(self.store.get(self.request.run_id).status, "completed")


class HttpListenerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapters = TestAdapters()
        self.adapters.delay = 0.2
        app = create_app(Path(self.temp.name), TOKEN, adapters=self.adapters, source=SOURCE)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        self.task = asyncio.create_task(self.server.serve())
        async with asyncio.timeout(5):
            while not self.server.started:
                if self.task.done():
                    await self.task
                await asyncio.sleep(0.01)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        self.server.should_exit = True
        await asyncio.wait_for(self.task, timeout=5)
        self.temp.cleanup()

    async def test_disconnect_does_not_cancel_run_and_progress_is_pollable(self):
        run_id = str(uuid4())
        async with httpx.AsyncClient(base_url=self.url) as browser:
            response = await browser.post(
                "/runs", json=BODY, headers={**HEADERS, "Idempotency-Key": run_id}
            )
            self.assertEqual(response.status_code, 202, response.text)
        async with httpx.AsyncClient(base_url=self.url, headers=HEADERS) as reconnected:
            async with asyncio.timeout(10):
                while True:
                    response = await reconnected.get(f"/runs/{run_id}")
                    response.raise_for_status()
                    run = response.json()
                    self.assertNotEqual(run["status"], "failed", run["error"])
                    if run["status"] == "waiting":
                        break
                    await asyncio.sleep(0.02)
            events = (await reconnected.get(f"/runs/{run_id}/events")).json()
            self.assertTrue(events)
            self.assertEqual(self.adapters.reads, 1)
            self.assertEqual(run["proposal"]["request"]["title"], BODY["title"])

    async def test_http_health_does_not_claim_live_agent_connectivity(self):
        async with httpx.AsyncClient(base_url=self.url) as client:
            health = await client.get("/healthz")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["mode"], "local-validation")
            self.assertFalse(health.json()["live_fabric_connected"])
            self.assertEqual((await client.get("/readyz")).status_code, 401)


if __name__ == "__main__":
    unittest.main()
