import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError

from hydro_orchestrator.contracts import AgentReceipt, Outcome, ReviewRequest, WorkReview
from hydro_orchestrator.service import LocalRunner, create_app
from hydro_orchestrator.store import Conflict, Store
from test_durable_service import BODY, HEADERS, SOURCE, TOKEN, TestAdapters, wait_for


class ReviewAdapters:
    def __init__(self, decision="no_draft"):
        self.sources = TestAdapters()
        self.decision = decision
        self.reviews = 0
        self.change_assessment = False

    async def read(self, request):
        return await self.sources.read(request)

    async def investigate(self, evidence):
        return await self.sources.investigate(evidence)

    async def propose(self, assessment):
        self.reviews += 1
        if self.change_assessment:
            assessment = assessment.model_copy(update={"missing_evidence": ("changed_by_reviewer",)})
        return WorkReview(
            assessment=assessment, decision=self.decision,
            reason="Synthetic review outcome for durability validation; not a live coverage claim.",
        )


class WorkReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.request = ReviewRequest(**BODY, source=SOURCE, run_id=uuid4())
        self.adapters = ReviewAdapters()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def assessment(self, request=None):
        evidence = await self.adapters.read(request or self.request)
        return await self.adapters.investigate(evidence)

    async def test_both_non_proposal_outcomes_complete_without_approval_or_work(self):
        for decision in ("no_draft", "needs_clarification"):
            with self.subTest(decision=decision):
                request = self.request.model_copy(update={"run_id": uuid4()})
                self.store.create(request)
                adapters = ReviewAdapters(decision)
                runner = LocalRunner(self.store, adapters, SOURCE)
                await runner.execute(request.run_id)
                await runner.close()
                run = self.store.get(request.run_id)
                self.assertEqual(run.status, "completed", run.error)
                self.assertEqual(run.outcome.status, decision)
                self.assertEqual(run.outcome.review.assessment.conclusion, "cause_undetermined")
                self.assertEqual(run.outcome.review.assessment.evidence.request, request)
                self.assertIsNone(run.proposal)
                self.assertIsNone(run.approval)
                self.assertIsNone(run.outcome.validation_work_id)
                self.assertFalse(run.outcome.production_write_executed)
                self.assertEqual(adapters.reviews, 1)
                self.assertNotIn("human_approval", {event["executor"] for event in self.store.events(request.run_id, 0)})
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM validation_work").fetchone()[0], 0)

    async def test_http_replay_and_restart_retain_decision_and_reject_approval(self):
        with TestClient(create_app(self.root, TOKEN, adapters=self.adapters, source=SOURCE)) as client:
            for _ in range(2):
                response = client.post(
                    "/runs", json=BODY,
                    headers={**HEADERS, "Idempotency-Key": str(self.request.run_id)},
                )
                self.assertEqual(response.status_code, 202, response.text)
                completed = wait_for(client, str(self.request.run_id), "completed")
                self.assertEqual(completed["outcome"]["status"], "no_draft")
            rejected = client.post(
                f"/runs/{self.request.run_id}/approval",
                json={"approved": True, "proposal_digest": "a" * 64}, headers=HEADERS,
            )
            self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.adapters.reviews, 1)
        fresh = ReviewAdapters()
        with TestClient(create_app(self.root, TOKEN, adapters=fresh, source=SOURCE)) as client:
            recovered = client.get(f"/runs/{self.request.run_id}", headers=HEADERS)
            self.assertEqual(recovered.json()["outcome"], completed["outcome"])
        self.assertEqual(fresh.reviews, 0)
        self.assertEqual(fresh.sources.reads, 0)

    async def test_failure_after_committed_review_recovers_without_reinvoking_agents(self):
        self.store.create(self.request)
        record = self.store.record_review

        def fail_after_commit(review):
            record(review)
            raise OSError("Injected stop after durable review commit")

        runner = LocalRunner(self.store, self.adapters, SOURCE)
        with patch.object(self.store, "record_review", side_effect=fail_after_commit):
            await runner.execute(self.request.run_id)
        await runner.close()
        self.assertEqual(self.store.get(self.request.run_id).status, "failed")
        self.assertIsNotNone(self.store.get(self.request.run_id).outcome)
        self.store.retry(self.request.run_id)
        fresh = ReviewAdapters()
        restored = LocalRunner(self.store, fresh, SOURCE)
        await restored.execute(self.request.run_id)
        await restored.close()
        self.assertEqual(self.store.get(self.request.run_id).status, "completed")
        self.assertEqual(fresh.reviews, 0)
        self.assertEqual(fresh.sources.reads, 0)
        self.assertIn("committed_outcome_recovered", {
            event["kind"] for event in self.store.events(self.request.run_id, 0)
        })

    async def test_changed_assessment_cannot_be_committed_as_a_review(self):
        self.adapters.change_assessment = True
        self.store.create(self.request)
        runner = LocalRunner(self.store, self.adapters, SOURCE)
        await runner.execute(self.request.run_id)
        await runner.close()
        run = self.store.get(self.request.run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("changed the verified assessment", run.error)
        self.assertIsNone(run.outcome)

    async def test_committed_review_is_idempotent_but_cannot_change_reason_or_request(self):
        self.store.create(self.request)
        self.store.start(self.request.run_id)
        review = WorkReview(assessment=await self.assessment(), decision="no_draft", reason="Test coverage only")
        original = self.store.record_review(review)
        self.assertEqual(self.store.record_review(review), original)
        with self.assertRaisesRegex(Conflict, "cannot be changed"):
            self.store.record_review(review.model_copy(update={"reason": "A different review"}))
        changed = self.request.model_copy(update={"title": "Different operator request"})
        other = WorkReview(assessment=await self.assessment(changed), decision="no_draft", reason="Test")
        with self.assertRaisesRegex(Conflict, "persisted operator request"):
            self.store.record_review(other)

    async def test_no_draft_requires_work_coverage_and_explicit_reason(self):
        assessment = await self.assessment()
        missing = assessment.model_dump()
        missing["evidence"].update(open_work_numbers=(), work_orders_read_at=None, missing_sources=("work_orders",))
        with self.assertRaisesRegex(ValidationError, "completed work-order read"):
            WorkReview(assessment=missing, decision="no_draft", reason="Unknown coverage")
        with self.assertRaisesRegex(ValidationError, "explicit reason"):
            WorkReview(assessment=assessment, decision="no_draft", reason=" ")
        clarification = WorkReview(assessment=missing, decision="needs_clarification", reason="Coverage is unavailable")
        self.assertEqual(clarification.decision, "needs_clarification")

    async def test_review_receipt_and_outcome_are_bound_to_their_inputs(self):
        assessment = await self.assessment()
        receipt = AgentReceipt(
            agent_name="hydro-work-order-agent", requested_version="11", response_id="test-response",
            input_digest=sha256(assessment.model_dump_json().encode()).hexdigest(), duration_ms=1,
        )
        review = WorkReview(assessment=assessment, decision="no_draft", reason="Test", agent_receipt=receipt)
        for change in ({"agent_name": "hydro-qa-agent"}, {"input_digest": "0" * 64}):
            with self.subTest(change=change), self.assertRaisesRegex(ValidationError, "immutable assessment"):
                WorkReview(assessment=assessment, decision="no_draft", reason="Test",
                           agent_receipt=receipt.model_copy(update=change))
        valid = Outcome(run_id=self.request.run_id, status="no_draft", review=review)
        for change in ({"review": None}, {"run_id": uuid4()}, {"validation_work_id": "not-allowed"},
                       {"status": "needs_clarification"}, {"status": "rejected"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                Outcome.model_validate({**valid.model_dump(), **change})


if __name__ == "__main__":
    unittest.main()
