import hashlib
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
    ANALYST_INSTRUCTIONS, AnalysisReport, ChatRequest, FoundrySupervisor, RunJournal, SpecialistResult, ToolEvidence,
)
from hydro_orchestrator.contracts import utc_now
from hydro_orchestrator.live_sources import NodeSourceBridge, SourceFailure
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


class AnalysisHarness(unittest.IsolatedAsyncioTestCase):
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


class AnalysisTests(AnalysisHarness):
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

    async def test_three_ungrounded_attempts_yield_no_briefing_with_a_visible_reason_and_nothing_saved(self):
        self.replies = [briefing(answer="EQUIP_RTI_T099 is hot."), briefing(answer="EQUIP_RTI_T098 is hot."),
                        briefing(answer="EQUIP_RTI_T097 is hot.")]
        self.assertIsNone(await self.owner.analysis_for(self.request, self.answer, self.journal))
        self.assertIsNone(self.journal.read("answer_analysis"))
        self.assertEqual(len(self.inputs), 3)
        self.assertIn("EQUIP_RTI_T097", self.owner.analysis_rejection or "")

    async def test_work_order_recommendations_must_account_for_existing_open_work(self):
        covered = briefing(recommendations=[{
            "action": "covered_by_open_work", "equipment_id": "EQUIP_RTI_T006", "priority": None,
            "title": "No new work order", "justification": "Covered by an open check.",
        }])
        uncited = briefing(recommendations=[{
            "action": "create_work_order", "equipment_id": "EQUIP_RTI_T006", "priority": "High",
            "title": "Inspect EQUIP_RTI_T006", "justification": "BAD signal at 13.992.",
        }])
        self.replies = [uncited, covered, uncited]
        self.assertIsNone(await self.owner.analysis_for(self.request, self.answer, self.journal))
        self.assertIn("WO-2848", self.inputs[1]["input"])
        self.assertIn("WO-2848", self.owner.analysis_rejection or "")

    async def test_a_third_attempt_can_still_repair_a_rejected_briefing(self):
        bad = briefing(answer="EQUIP_RTI_T099 is hot.")
        self.replies = [bad, bad, briefing()]
        report = await self.owner.analysis_for(self.request, self.answer, self.journal)
        self.assertIsNotNone(report)
        self.assertEqual(len(self.inputs), 3)
        self.assertIsNone(self.owner.analysis_rejection)

    async def test_upstream_failure_or_unconfigured_model_degrades_to_tables_only(self):
        self.replies = [httpx.Response(500, json={"error": {"message": "boom"}})]
        self.assertIsNone(await self.owner.analysis_for(self.request, self.answer, self.journal))
        self.assertIn("unavailable", self.owner.analysis_rejection or "")
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


def cause_briefing(**changes):
    return briefing(
        probable_causes=[{
            "equipment_id": "EQUIP_RTI_T014", "category": "instrument_or_sensor", "likelihood": "High",
            "cause": "Instrument fault on ns=2;s=T014.vibration_a",
            "evidence": "bad 60 of 96 samples across 12 hours, while the inspection on 2026-10-10 found a loose cable gland.",
            "to_confirm": "Check the sensor wiring and compare with an independent reading.",
        }],
        **changes,
    )


def rca_specialist():
    return SpecialistResult(
        role="rca", agent_name="hydro-rca-agent", version="1", response_id="r1", duration_ms=1,
        input_digest="0" * 64, text="", report={"hypotheses": []},
    )


class RootCauseEvidenceTests(AnalysisHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.request = self.request.model_copy(update={"required_evidence_kinds": ("signal_quality",)})
        self.journal.save("tool_catalog", await self.owner.bridge.call({"action": "source_contracts"}))
        self.owner.specialists = [rca_specialist()]
        self.calls = []
        self.fail = set()
        self.owner.evidence = {"snapshot": self.evidence_for("query_signal_quality_snapshot", {"quality": "BAD"}, [
            {"equipment_id": "EQUIP_RTI_T014", "opcua_node_id": "ns=2;s=T014.vibration_a", "quality": "BAD"},
        ])}
        self.owner.tools.execute = AsyncMock(side_effect=self.read)

    def evidence_for(self, tool, arguments, rows):
        digest = hashlib.sha256(json.dumps([tool, arguments], sort_keys=True).encode()).hexdigest()[:12]
        return ToolEvidence(
            id=f"{tool}-{digest}", source=SOURCE, tool=tool, arguments=arguments, completed_at=utc_now(),
            result={"rows": rows, "truncated": False}, datasets=(),
        )

    async def read(self, name, arguments, _request):
        self.calls.append((name, arguments))
        entity = arguments.get("entity", arguments.get("aggregation"))
        if entity in self.fail:
            raise SourceFailure(f"{entity} unavailable")
        rows = {
            "equipment": [{"equipment_id": "EQUIP_RTI_T014", "tag": "T014", "equipment_type_code": "TURBINE",
                           "equipment_type_name": "Turbine", "criticality": 5}],
            "instruments": [
                {"instrument_id": "INST_T014_VIBRATION_A", "equipment_id": "EQUIP_RTI_T014", "is_active": True,
                 "opcua_node_id": "ns=2;s=T014.vibration_a", "tag": "Vibration A", "unit": "mm/s"},
                {"instrument_id": "INST_T014_SPEED", "equipment_id": "EQUIP_RTI_T014", "is_active": True,
                 "opcua_node_id": "ns=2;s=T014.speed", "tag": "Speed", "unit": "rpm"},
            ],
            "inspections": [{"id": "i1", "equipmentId": "EQUIP_RTI_T014", "inspectionType": "Visual",
                             "result": "Pass", "findings": "Cable gland loose", "inspectedAt": "2026-10-10T08:00:00Z"}],
            "notifications": [{"id": "n1", "equipmentId": "EQUIP_RTI_T014", "summary": "Intermittent reading",
                               "severity": "Low", "status": "Open", "reportedAt": "2026-10-13T08:00:00Z"}],
            "spare_parts": [
                {"partNumber": "SNS-1", "name": "Vibration probe", "category": "Sensor", "equipmentType": "Turbine",
                 "quantityOnHand": 3, "reorderLevel": 2, "storageLocation": "Bay 4"},
                {"partNumber": "PMP-9", "name": "Pump seal", "category": "Seal", "equipmentType": "Pump",
                 "quantityOnHand": 9, "reorderLevel": 1, "storageLocation": "Bay 1"},
            ],
            "quality": [{"opcua_node_id": "ns=2;s=T014.vibration_a", "unit": "mm/s", "samples": 96, "good": 36,
                         "uncertain": 0, "bad": 60, "hours_with_data": 12, "hours_with_bad": 12,
                         "first_bad_time": "2026-10-13T20:00:00Z", "last_bad_time": "2026-10-14T08:00:00Z"}],
        }[entity]
        return self.evidence_for(name, arguments, rows)

    async def test_rca_reads_the_history_of_every_affected_equipment_once(self):
        context = await self.owner.root_cause_evidence(self.request, self.journal)
        by_name = {(name, arguments.get("entity", arguments.get("aggregation"))): arguments
                   for name, arguments in self.calls}
        self.assertEqual(set(by_name), {
            ("query_assets", "equipment"), ("query_assets", "instruments"), ("query_operations", "inspections"),
            ("query_operations", "notifications"), ("query_operations", "spare_parts"),
            ("query_telemetry", "quality"),
        })
        history = by_name[("query_telemetry", "quality")]
        self.assertEqual(history["lookback"], "48h")
        self.assertEqual(history["opcua_node_ids"], ["ns=2;s=T014.speed", "ns=2;s=T014.vibration_a"])
        self.assertEqual(by_name[("query_operations", "inspections")]["where"][0]["value"], ["EQUIP_RTI_T014"])
        [equipment] = context["equipment"]
        self.assertEqual(equipment["equipment_id"], "EQUIP_RTI_T014")
        self.assertEqual([signal["opcua_node_id"] for signal in equipment["signals"]],
                         ["ns=2;s=T014.vibration_a", "ns=2;s=T014.speed"])
        self.assertEqual(equipment["signals"][0]["quality_history"]["bad"], 60)
        self.assertEqual(equipment["signals"][1]["quality_history"], "no samples in the window")
        self.assertEqual(equipment["signals"][0]["unit"], "mm/s")
        self.assertEqual(equipment["inspections"][0]["findings"], "Cable gland loose")
        self.assertEqual([part["partNumber"] for part in equipment["compatible_spare_parts"]], ["SNS-1"])
        self.assertEqual(context["unavailable_sources"], [])
        self.calls.clear()
        await self.owner.root_cause_evidence(self.request, self.journal)
        self.assertEqual(self.calls, [])

    async def test_an_unreadable_source_is_reported_not_treated_as_empty(self):
        self.fail = {"inspections"}
        context = await self.owner.root_cause_evidence(self.request, self.journal)
        self.assertEqual(context["unavailable_sources"], ["Inspection records could not be read for this analysis."])
        self.assertEqual(context["equipment"][0]["inspections"], [])
        self.assertTrue(context["equipment"][0]["compatible_spare_parts"])

    async def test_no_root_cause_reads_without_affected_equipment(self):
        self.owner.evidence = {}
        journal = RunJournal(self.root / "empty" / "receipts")
        journal.save("tool_catalog", self.journal.read("tool_catalog"))
        self.assertIsNone(await self.owner.root_cause_evidence(self.request, journal))
        self.assertEqual(self.calls, [])

    async def test_scope_is_the_requested_bad_set_published_and_fixed_for_the_run(self):
        broad = self.evidence_for("query_turbine_temperature_snapshot", {}, [
            {"equipment_id": f"EQUIP_RTI_T{index:03d}", "opcua_node_id": f"ns=2;s=T{index:03d}.turbine_temp"}
            for index in range(1, 12)
        ])
        self.owner.evidence["broad"] = broad
        self.owner.selected_equipment_ids = {"EQUIP_RTI_T007"}
        scope = self.owner.root_cause_scope(self.request, self.journal)
        self.assertEqual(scope, {"equipment_ids": ["EQUIP_RTI_T014"], "omitted": 0})
        self.owner.selected_equipment_ids = {"EQUIP_RTI_T003"}
        self.assertEqual(self.owner.root_cause_scope(self.request, self.journal), scope)
        await self.owner.root_cause_evidence(self.request, self.journal)
        tools = {item.tool for item in self.owner.evidence.values()}
        self.assertTrue({"query_assets", "query_operations", "query_telemetry"} <= tools)

    async def test_scope_falls_back_to_the_named_equipment_and_caps_the_detail(self):
        named = self.request.model_copy(update={"required_evidence_kinds": ()})
        self.owner.selected_equipment_ids = {f"EQUIP_RTI_T{index:03d}" for index in range(1, 12)}
        scope = self.owner.root_cause_scope(named, RunJournal(self.root / "named" / "receipts"))
        self.assertEqual(len(scope["equipment_ids"]), 8)
        self.assertEqual(scope["omitted"], 3)

    async def test_briefing_receives_the_history_and_its_probable_causes_are_grounded(self):
        self.answer.specialists = (rca_specialist(),)
        self.replies = [cause_briefing()]
        report = await self.owner.analysis_for(self.request, self.answer, self.journal)
        sent = json.loads(self.inputs[0]["input"])
        self.assertEqual(sent["root_cause_evidence"]["window"], "48h")
        [cause] = report.probable_causes
        self.assertEqual((cause.category, cause.likelihood), ("instrument_or_sensor", "High"))

    async def test_a_cause_naming_unknown_equipment_is_rejected(self):
        invented = cause_briefing()
        invented["probable_causes"][0]["equipment_id"] = "EQUIP_RTI_T099"
        self.replies = [invented, invented, invented]
        self.assertIsNone(await self.owner.analysis_for(self.request, self.answer, self.journal))
        self.assertIn("EQUIP_RTI_T099", self.owner.analysis_rejection or "")

    async def test_non_rca_questions_send_no_root_cause_section_and_may_return_no_causes(self):
        self.owner.specialists = []
        self.replies = [briefing(probable_causes=[])]
        report = await self.owner.analysis_for(self.request, self.answer, self.journal)
        self.assertNotIn("root_cause_evidence", json.loads(self.inputs[0]["input"]))
        self.assertEqual(report.probable_causes, ())

    def test_presentation_lists_probable_causes_between_reasoning_and_actions(self):
        report = AnalysisReport.model_validate(cause_briefing())
        answer = SimpleNamespace(
            analysis=report, summary="s", tables=(QUALITY,), limitations=(), charts=(), specialists=(), run_id=uuid4(),
        )
        text = client_presentation(answer).text
        order = [text.index(marker) for marker in ("## Why", "## Probable causes", "## Recommended actions")]
        self.assertEqual(order, sorted(order))
        self.assertIn("instrument or sensor, High likelihood", text)
        self.assertIn("To confirm: Check the sensor wiring", text)


if __name__ == "__main__":
    unittest.main()
