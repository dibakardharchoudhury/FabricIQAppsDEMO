import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from azure.core.credentials import AccessToken
from azure.identity.aio import AzureCliCredential
from pydantic import ValidationError

from hydro_orchestrator.contracts import Approval, Assessment, Proposal, ReviewRequest, utc_now
from hydro_orchestrator.foundry_rca import FoundryRca
from hydro_orchestrator.live_sources import NodeSourceBridge, SourceFailure
from hydro_orchestrator.service import LocalRunner
from hydro_orchestrator.store import Store
from test_durable_service import BODY, SOURCE, TestAdapters


ENDPOINT = "https://test.services.ai.azure.com/api/projects/test"


class FoundryRcaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.request = ReviewRequest(**BODY, source=SOURCE, run_id=uuid4())
        self.evidence = (await TestAdapters().read(self.request)).model_copy(update={
            "open_work_numbers": (),
            "work_orders_read_at": None,
            "missing_sources": ("fresh_telemetry", "work_orders_not_requested", "inspections_not_requested"),
        })
        self.bridge = NodeSourceBridge()
        self.tool = (await self.bridge.call({"action": "rca_contract"}))["tool"]
        self.credential = AsyncMock(spec=AzureCliCredential)
        self.credential.get_token.return_value = AccessToken("test-token-not-real", 9999999999)
        self.mode = "valid"
        self.invocations = 0
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.adapter = FoundryRca(ENDPOINT, self.credential, self.bridge, self.client)

    async def asyncTearDown(self):
        await self.client.aclose()

    def respond(self, request):
        if request.method == "GET":
            tool = copy.deepcopy(self.tool)
            if self.mode == "schema_drift":
                tool["parameters"]["properties"]["observations"]["minItems"] = 0
            return httpx.Response(200, json={"name": "hydro-rca-agent", "versions": {"latest": {
                "version": "13", "definition": {"kind": "prompt", "tools": [tool]},
            }}})
        self.invocations += 1
        payload = json.loads(request.content)
        self.assertNotIn("tools", payload)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["agent_reference"]["version"], "13")
        self.assertEqual(payload["tool_choice"], {"type": "function", "name": "complete_rca_assessment"})
        if self.mode == "http_failure":
            return httpx.Response(503, json={"error": {"message": "private upstream body"}})
        if self.mode == "timeout":
            raise httpx.ReadTimeout("Injected model deadline", request=request)
        receipts = json.loads(payload["input"][0]["content"].rsplit("\n", 1)[1])
        reference = {"evidence_id": receipts[0]["id"], "path": "/rows/0"}
        report = {"observations": [reference], "hypotheses": [
            {"category": "sensor_or_ingestion", "supporting": [reference], "contradicting": [],
             "missing": ["independent_measurement"]},
            {"category": "equipment_condition", "supporting": [], "contradicting": [],
             "missing": ["inspection_evidence"]},
        ]}
        if self.mode == "invented_reference":
            reference["evidence_id"] = "not-a-real-source"
        elif self.mode == "invented_path":
            reference["path"] = "/rows/99"
        elif self.mode == "free_diagnosis":
            report["diagnosis"] = "Confirmed bearing fault"
        elif self.mode == "duplicate_hypotheses":
            report["hypotheses"][1]["category"] = "sensor_or_ingestion"
        call = {"type": "function_call", "name": "complete_rca_assessment", "arguments": json.dumps(report)}
        output = [call]
        if self.mode == "narrative":
            output = [{"type": "message", "content": [{"text": "Equipment is failing."}]}]
        elif self.mode == "duplicate_calls":
            output = [call, call]
        elif self.mode == "wrong_tool":
            call["name"] = "hydro_query"
        return httpx.Response(200, headers={"x-request-id": "test-request"}, json={
            "id": "resp_test", "status": "incomplete" if self.mode == "incomplete" else "completed",
            "output": output, "usage": {"input_tokens": 200, "output_tokens": 100},
        })

    async def test_shared_schema_and_references_produce_bound_undetermined_assessment(self):
        result = await self.adapter.investigate(self.evidence)
        self.assertEqual(result.conclusion, "cause_undetermined")
        self.assertEqual(result.evidence, self.evidence)
        self.assertEqual(result.observations, ("test-reading",))
        self.assertEqual(result.agent_receipt.requested_version, "13")
        self.assertEqual(result.agent_receipt.response_id, "resp_test")
        self.assertEqual(result.agent_receipt.input_tokens, 200)
        self.assertTrue({"approved_limits", "matched_baseline", "fresh_measurements", "maintenance_scope",
                         "inspection_evidence"} <= set(result.missing_evidence))
        self.assertEqual(self.invocations, 1)

    async def test_recovered_assessment_revalidates_every_source_pointer_and_agent_identity(self):
        assessment = await self.adapter.investigate(self.evidence)
        for target, change in (
            ("observation", {"evidence_id": "invented"}),
            ("observation", {"path": "/rows/99"}),
            ("observation", {"path": "/rows/0/invented_field"}),
            ("supporting", {"path": "/rows/99"}),
            ("contradicting", {"evidence_id": "invented"}),
            ("agent", {"agent_name": "unrelated-agent"}),
        ):
            with self.subTest(target=target, change=change):
                payload = assessment.model_dump(mode="json")
                if target == "agent":
                    payload["agent_receipt"].update(change)
                elif target == "observation":
                    payload["report"]["observations"][0].update(change)
                else:
                    payload["report"]["hypotheses"][0][target] = [
                        {**payload["report"]["observations"][0], **change},
                    ]
                with self.assertRaises(ValidationError):
                    Assessment.model_validate(payload)

    async def test_schema_drift_stops_before_model_invocation(self):
        self.mode = "schema_drift"
        with self.assertRaisesRegex(SourceFailure, "schema differs"):
            await self.adapter.investigate(self.evidence)
        self.assertEqual(self.invocations, 0)

    async def test_shared_parser_rejects_invented_evidence_diagnoses_and_duplicate_hypotheses(self):
        for mode in ("invented_reference", "invented_path", "free_diagnosis", "duplicate_hypotheses"):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(SourceFailure):
                    await self.adapter.investigate(self.evidence)

    async def test_incomplete_narrative_duplicate_or_unexpected_calls_are_not_assessments(self):
        for mode in ("incomplete", "narrative", "duplicate_calls", "wrong_tool"):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(SourceFailure):
                    await self.adapter.investigate(self.evidence)

    async def test_http_failure_and_timeout_do_not_retry_or_expose_upstream_body(self):
        self.mode = "http_failure"
        with self.assertRaisesRegex(SourceFailure, "HTTP 503") as caught:
            await self.adapter.investigate(self.evidence)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.invocations, 1)
        self.mode = "timeout"
        with self.assertRaises(httpx.ReadTimeout):
            await self.adapter.investigate(self.evidence)
        self.assertEqual(self.invocations, 2)

    async def test_changed_evidence_cannot_reuse_an_invocation_receipt(self):
        result = await self.adapter.investigate(self.evidence)
        changed = result.model_dump()
        changed["evidence"]["observations"][0]["value"] = 999.0
        with self.assertRaisesRegex(ValidationError, "immutable evidence"):
            Assessment.model_validate(changed)

    async def test_checkpoint_recovery_preserves_structured_report_without_second_invocation(self):
        rca = self.adapter
        evidence = self.evidence.model_copy(update={
            "work_orders_read_at": self.evidence.read_completed_at,
            "missing_sources": ("fresh_telemetry", "inspections_not_requested"),
        })

        class Adapters(TestAdapters):
            async def read(self, request):
                return evidence

            async def investigate(self, source):
                return await rca.investigate(source)

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.create(self.request)
            runner = LocalRunner(store, Adapters(), SOURCE, step_timeout=15)
            await runner.execute(self.request.run_id)
            waiting = store.get(self.request.run_id)
            self.assertEqual(waiting.status, "waiting", waiting.error)
            self.assertIsNotNone(waiting.proposal.assessment.report)
            store.approve(self.request.run_id, Approval(
                approved=False, proposal_digest=waiting.proposal.digest(),
            ))
            await runner.close()
            restored = LocalRunner(store, Adapters(), SOURCE, step_timeout=15)
            await restored.execute(self.request.run_id)
            result = store.get(self.request.run_id)
            await restored.close()
            self.assertEqual(result.status, "completed", result.error)
            self.assertEqual(result.outcome.status, "rejected")
            self.assertEqual(self.invocations, 1)

    async def test_telemetry_only_investigation_cannot_become_an_approvable_proposal(self):
        assessment = await self.adapter.investigate(self.evidence)
        with self.assertRaisesRegex(ValidationError, "work-order coverage"):
            Proposal(request=self.request, assessment=assessment, description="Inspection",
                     expires_at=utc_now() + timedelta(minutes=10))

    async def test_proposal_expiry_cannot_refresh_old_work_order_coverage(self):
        assessment = await TestAdapters().investigate(await TestAdapters().read(self.request))
        with self.assertRaisesRegex(ValidationError, "15 minutes"):
            Proposal(request=self.request, assessment=assessment, description="Inspection",
                     expires_at=assessment.evidence.work_orders_read_at + timedelta(minutes=16))

    async def test_slower_telemetry_cannot_extend_the_work_read_approval_window(self):
        evidence = await TestAdapters().read(self.request)
        work_clock = evidence.read_completed_at - timedelta(minutes=2)
        evidence = evidence.model_copy(update={"work_orders_read_at": work_clock})
        assessment = await TestAdapters().investigate(evidence)
        with self.assertRaisesRegex(ValidationError, "15 minutes"):
            Proposal(request=self.request, assessment=assessment, description="Inspection",
                     expires_at=evidence.read_completed_at + timedelta(minutes=15))
        proposal = Proposal(request=self.request, assessment=assessment, description="Inspection",
                            expires_at=work_clock + timedelta(minutes=15))
        self.assertEqual(proposal.expires_at - evidence.read_completed_at, timedelta(minutes=13))

    async def test_endpoint_validation_rejects_untrusted_or_ambiguous_urls(self):
        for endpoint in ("https://example.com/api/projects/test", ENDPOINT + "?redirect=1",
                         ENDPOINT.replace("/test", "/.."), ENDPOINT.replace("https:", "http:")):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                FoundryRca(endpoint, self.credential, self.bridge, self.client)


if __name__ == "__main__":
    unittest.main()
