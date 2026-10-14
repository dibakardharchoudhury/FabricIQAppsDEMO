import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from azure.core.credentials import AccessToken
from azure.identity.aio import AzureCliCredential
from openai import AsyncOpenAI

from hydro_orchestrator.contracts import EvidenceReference
from hydro_orchestrator.foundry_supervisor import (
    ANALYST_INSTRUCTIONS, AnalysisReport, ChatRequest, FoundrySupervisor, RunJournal,
)
from hydro_orchestrator.contracts import utc_now
from hydro_orchestrator.live_sources import NodeSourceBridge
from hydro_orchestrator.presentation import AnswerColumn, AnswerRow, AnswerTable, client_presentation
from test_durable_service import SOURCE

ENDPOINT = "https://test.services.ai.azure.com/api/projects/test"


def table(title, columns, rows):
    reference = EvidenceReference(evidence_id="receipt-1", path="/rows/0")
    return AnswerTable(
        id=title.lower().replace(" ", "-"), title=title,
        columns=tuple(AnswerColumn(key=key, label=label, kind=kind) for key, label, kind in columns),
        rows=tuple(AnswerRow(values=row, source=reference) for row in rows),
    )


QUALITY = table("Signal quality", [
    ("equipment_id", "Equipment", "text"), ("opcua_node_id", "Signal", "text"), ("quality", "Quality", "text"),
    ("value", "Value", "number"),
], [
    {"equipment_id": "EQUIP_RTI_T006", "opcua_node_id": "ns=2;s=T006.vibration_d", "quality": "BAD", "value": 13.992},
    {"equipment_id": "EQUIP_RTI_T014", "opcua_node_id": "ns=2;s=T014.vibration_a", "quality": "BAD", "value": 7.338},
])
WORK = table("Work orders", [
    ("workOrderNumber", "Work order", "text"), ("equipmentId", "Equipment", "text"), ("title", "Title", "text"),
    ("priority", "Priority", "text"), ("status", "Status", "text"),
], [
    {"workOrderNumber": "WO-2848", "equipmentId": "EQUIP_RTI_T006", "title": "Fjord unit driven-end vibration check",
     "priority": "High", "status": "Approved"},
    {"workOrderNumber": "WO-2820", "equipmentId": "EQUIP_RTI_T004", "title": "Annual thermographic survey",
     "priority": "Low", "status": "Completed"},
])
OPEN_WORK = {"EQUIP_RTI_T006": [{"work_order": "WO-2848", "title": "Fjord unit driven-end vibration check",
                                  "priority": "High", "status": "Approved", "instrument": None}]}


def briefing(**changes):
    value = {
        "answer": "EQUIP_RTI_T014 has a BAD vibration signal and no open work order; EQUIP_RTI_T006 is already covered by WO-2848.",
        "findings": ["EQUIP_RTI_T006 reports BAD ns=2;s=T006.vibration_d at 13.992.",
                     "EQUIP_RTI_T014 reports BAD ns=2;s=T014.vibration_a at 7.338."],
        "reasoning": ["WO-2848 is an approved vibration check on EQUIP_RTI_T006, so it plausibly covers that signal.",
                      "Nothing open references EQUIP_RTI_T014, so its BAD signal is uncovered."],
        "recommendations": [
            {"action": "covered_by_open_work", "equipment_id": "EQUIP_RTI_T006", "priority": None,
             "title": "No new work order", "justification": "WO-2848 already covers the vibration signal."},
            {"action": "create_work_order", "equipment_id": "EQUIP_RTI_T014", "priority": "Medium",
             "title": "Inspect EQUIP_RTI_T014 vibration signal",
             "justification": "BAD signal at 7.338 with no open work; the priority is a suggestion."},
        ],
        "caveats": ["BAD is a signal-quality state, not proof of a physical fault."],
    }
    value.update(changes)
    return value


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.replies = []
        self.inputs = []

        def respond(request):
            payload = json.loads(request.content)
            self.inputs.append(payload)
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["instructions"], ANALYST_INSTRUCTIONS)
            self.assertFalse(payload["store"])
            self.assertNotIn("tools", payload)
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            reply = self.replies.pop(0)
            if isinstance(reply, httpx.Response):
                return reply
            return httpx.Response(200, json={
                "id": f"resp_{len(self.inputs)}", "object": "response", "created_at": 1, "model": "test-model",
                "status": "completed",
                "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": json.dumps(reply), "annotations": []}]}],
                "parallel_tool_calls": False, "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            })

        network = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.openai = AsyncOpenAI(api_key="test-not-a-real-key", base_url=f"{ENDPOINT}/openai/v1/",
                                 http_client=network, max_retries=0)
        self.patch = patch("azure.ai.projects.aio.AIProjectClient.get_openai_client", return_value=self.openai)
        self.patch.start()
        credential = AsyncMock(spec=AzureCliCredential)
        credential.get_token.return_value = AccessToken("test-not-a-real-token", 9999999999)
        self.owner = FoundrySupervisor(
            ENDPOINT, credential, NodeSourceBridge(), AsyncMock(), SOURCE, self.root, analysis_model="test-model",
        )
        self.owner.open_work_by_equipment = lambda: OPEN_WORK
        from datetime import timedelta
        self.request = ChatRequest(
            run_id=uuid4(), source=SOURCE, question="Which WOs need to be opened now?",
            deadline=utc_now() + timedelta(minutes=3),
        )
        self.answer = SimpleNamespace(
            tables=(QUALITY, WORK), specialists=(), proposals=(), limitations=(), summary="2 tables.",
        )
        self.journal = RunJournal(self.root / str(self.request.run_id) / "receipts")

    async def asyncTearDown(self):
        self.patch.stop()
        await self.openai.close()
        self.temp.cleanup()

    async def test_verified_briefing_is_returned_saved_and_reused_without_a_second_model_call(self):
        self.replies = [briefing()]
        report = await self.owner.analysis_for(self.request, self.answer, self.journal)
        self.assertIsInstance(report, AnalysisReport)
        self.assertEqual([item.action for item in report.recommendations], ["covered_by_open_work", "create_work_order"])
        sent = json.loads(self.inputs[0]["input"])
        self.assertEqual(sent["operator_question"], "Which WOs need to be opened now?")
        self.assertEqual(sent["open_work_by_equipment"], OPEN_WORK)
        self.assertEqual([item["title"] for item in sent["datasets"]], ["Signal quality", "Work orders"])
        again = await self.owner.analysis_for(self.request, self.answer, self.journal)
        self.assertEqual(again, report)
        self.assertEqual(len(self.inputs), 1)

    async def test_invented_identifiers_and_numbers_are_rejected_then_corrected_once(self):
        self.replies = [
            briefing(answer="EQUIP_RTI_T099 needs WO-9999 at 91.5.", findings=[]),
            briefing(),
        ]
        report = await self.owner.analysis_for(self.request, self.answer, self.journal)
        self.assertIsNotNone(report)
        self.assertEqual(len(self.inputs), 2)
        retry = self.inputs[1]["input"]
        for fragment in ("EQUIP_RTI_T099", "WO-9999", "91.5"):
            self.assertIn(fragment, retry)
        self.assertIn("previous briefing was rejected", retry)

    async def test_two_ungrounded_attempts_yield_no_briefing_and_nothing_is_saved(self):
        self.replies = [briefing(answer="EQUIP_RTI_T099 is hot."), briefing(answer="EQUIP_RTI_T098 is hot.")]
        self.assertIsNone(await self.owner.analysis_for(self.request, self.answer, self.journal))
        self.assertIsNone(self.journal.read("answer_analysis"))
        self.assertEqual(len(self.inputs), 2)

    async def test_work_order_recommendations_must_account_for_existing_open_work(self):
        self.replies = [
            briefing(recommendations=[{
                "action": "create_work_order", "equipment_id": "EQUIP_RTI_T006", "priority": "High",
                "title": "Inspect EQUIP_RTI_T006", "justification": "BAD signal at 13.992.",
            }]),
            briefing(recommendations=[{
                "action": "covered_by_open_work", "equipment_id": "EQUIP_RTI_T006", "priority": None,
                "title": "No new work order", "justification": "Covered by an open check.",
            }]),
        ]
        self.assertIsNone(await self.owner.analysis_for(self.request, self.answer, self.journal))
        self.assertIn("WO-2848", self.inputs[1]["input"])

    async def test_upstream_failure_or_unconfigured_model_degrades_to_tables_only(self):
        self.replies = [httpx.Response(500, json={"error": {"message": "boom"}})]
        self.assertIsNone(await self.owner.analysis_for(self.request, self.answer, self.journal))
        unconfigured = FoundrySupervisor(
            ENDPOINT, self.owner.credential, NodeSourceBridge(), AsyncMock(), SOURCE, self.root,
        )
        self.assertIsNone(await unconfigured.analysis_for(self.request, self.answer, self.journal))
        self.assertEqual(len(self.inputs), 1)

    def test_a_work_order_recommendation_requires_identity_and_priority(self):
        with self.assertRaises(ValueError):
            AnalysisReport.model_validate(briefing(recommendations=[{
                "action": "create_work_order", "equipment_id": None, "priority": None,
                "title": "x", "justification": "y",
            }]))

    def test_presentation_leads_with_the_answer_and_reasoning_and_escapes_model_text(self):
        report = AnalysisReport.model_validate(briefing(findings=["Use | pipes and *stars* safely."]))
        answer = SimpleNamespace(
            analysis=report, summary="Signal quality: 2 verified records returned.", tables=(QUALITY,),
            limitations=("Source limit.",), charts=(), specialists=(), run_id=uuid4(),
        )
        text = client_presentation(answer).text
        order = [text.index(marker) for marker in (
            "## Answer", "## What the data shows", "## Why", "## Recommended actions", "## Caveats", "## Signal quality",
            "## Source limitations",
        )]
        self.assertEqual(order, sorted(order))
        self.assertIn("**Create a work order** EQUIP\\_RTI\\_T014 (Medium priority, suggested)", text)
        self.assertIn("**Already covered by open work** EQUIP\\_RTI\\_T006", text)
        self.assertIn("Use \\| pipes and \\*stars\\* safely.", text)
        self.assertIn("No work order exists until you approve a staged draft.", text)

    def test_presentation_without_a_briefing_is_unchanged(self):
        answer = SimpleNamespace(
            analysis=None, summary="Signal quality: 2 verified records returned.", tables=(QUALITY,),
            limitations=(), charts=(), specialists=(), run_id=uuid4(),
        )
        text = client_presentation(answer).text
        self.assertTrue(text.startswith("Signal quality: 2 verified records returned."))
        self.assertNotIn("## Answer", text)


if __name__ == "__main__":
    unittest.main()
