import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic import ValidationError

from hydro_orchestrator.foundry_supervisor import (
    ChatRequest, EvidenceDataset, FoundrySupervisor, RunJournal, SourceFailure, SpecialistResult, ToolEvidence,
)
from hydro_orchestrator.live_sources import NodeSourceBridge
from hydro_orchestrator.service import (
    CARRY_MAX_AGE, ChatInput, carriable_receipts, follow_up_intent, reusable_kinds, select_carried_ids,
)
from hydro_orchestrator.contracts import SourceIdentity, utc_now
from test_durable_service import SOURCE

ENDPOINT = "https://test.services.ai.azure.com/api/projects/test"


def receipt(tool, kind, *, age=timedelta(minutes=2), source=SOURCE, scope="population", complete=True,
            run="11111111-1111-4111-8111-111111111111", suffix="a", result=None):
    return ToolEvidence(
        id=f"{run}:{suffix}", source=source, tool=tool, arguments={"quality": "BAD"},
        completed_at=utc_now() - age, result=result if result is not None else {"rows": [{"equipment_id": "EQUIP_RTI_T005"}]},
        datasets=(EvidenceDataset(kind=kind, field="rows", authority="direct", scope=scope, complete=complete,
                                  identity_fields=("equipment_id",)),),
    )


def write_previous_run(root: Path, run_id, receipts):
    RunJournal(root / str(run_id) / "receipts").save("evidence", {
        "receipts": [item.model_dump(mode="json") for item in receipts], "supervisor": {},
    })


class CarriedReceiptSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = uuid4()

    def tearDown(self):
        self.temp.cleanup()

    def test_only_recent_same_source_non_draft_receipts_are_reusable(self):
        other = SourceIdentity(tenant_id=uuid4(), workspace_id=uuid4(), ontology_id=uuid4(), generation=2)
        keep = receipt("query_signal_quality_snapshot", "signal_quality", suffix="keep")
        write_previous_run(self.root, self.previous, [
            keep,
            receipt("propose_work_order", "work_orders", suffix="draft"),
            receipt("query_operations", "work_orders", source=other, suffix="foreign"),
            receipt("query_operations", "work_orders", age=CARRY_MAX_AGE + timedelta(minutes=1), suffix="old"),
            receipt("query_operations", "work_orders", age=-timedelta(minutes=5), suffix="future"),
        ])
        self.assertEqual([item.id for item in carriable_receipts(self.root, SOURCE, self.previous)], [keep.id])

    def test_missing_corrupt_or_absent_previous_runs_carry_nothing(self):
        self.assertEqual(carriable_receipts(self.root, SOURCE, None), [])
        self.assertEqual(carriable_receipts(self.root, SOURCE, self.previous), [])
        RunJournal(self.root / str(self.previous) / "receipts").save("evidence", {"request": {}})
        self.assertEqual(carriable_receipts(self.root, SOURCE, self.previous), [])
        RunJournal(self.root / str(uuid4()) / "receipts").save("evidence", {"receipts": ["not a receipt"]})

    def test_only_complete_direct_population_datasets_define_reusable_kinds(self):
        self.assertEqual(reusable_kinds(receipt("t", "signal_quality")), {"signal_quality"})
        self.assertEqual(reusable_kinds(receipt("t", "signal_quality", scope="focus")), set())
        self.assertEqual(reusable_kinds(receipt("t", "signal_quality", complete=False)), set())

    def test_selection_keeps_only_receipts_that_supply_a_needed_kind(self):
        quality = receipt("query_signal_quality_snapshot", "signal_quality", suffix="q")
        work = receipt("query_operations", "work_orders", suffix="w")
        power = receipt("query_station_power", "station_power", suffix="p")
        self.assertEqual(select_carried_ids([quality, work, power], {"signal_quality", "work_orders"}),
                         (quality.id, work.id))
        self.assertEqual(select_carried_ids([quality], {"temperature"}), ())


class ChatRequestCarryContractTests(unittest.TestCase):
    def request(self, **changes):
        previous = changes.pop("previous_run_id", None)
        return ChatRequest(run_id=uuid4(), source=SOURCE, question="Based on this data, draft the work.",
                           deadline=utc_now() + timedelta(minutes=3), previous_run_id=previous, **changes)

    def test_carried_evidence_must_come_from_the_preceding_run_and_be_distinct(self):
        previous = uuid4()
        ok = self.request(previous_run_id=previous, carried_from=previous, carried_evidence_ids=("a:1", "a:2"))
        self.assertEqual(ok.carried_evidence_ids, ("a:1", "a:2"))
        with self.assertRaises(ValidationError):
            self.request(previous_run_id=previous, carried_from=uuid4(), carried_evidence_ids=("a:1",))
        with self.assertRaises(ValidationError):
            self.request(previous_run_id=previous, carried_evidence_ids=("a:1",))
        with self.assertRaises(ValidationError):
            self.request(previous_run_id=previous, carried_from=previous)
        with self.assertRaises(ValidationError):
            self.request(previous_run_id=previous, carried_from=previous, carried_evidence_ids=("a:1", "a:1"))
        self.assertEqual(self.request().carried_evidence_ids, ())


class SupervisorCarryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = uuid4()
        self.quality = receipt("query_signal_quality_snapshot", "signal_quality", suffix="q",
                               run=str(self.previous))
        self.work = receipt("query_operations", "work_orders", suffix="w", run=str(self.previous),
                            result={"rows": [{"workOrderNumber": "WO-2851", "equipmentId": "EQUIP_RTI_T008",
                                              "status": "Planned", "title": "Investigate power output dip"}]})
        write_previous_run(self.root, self.previous, [self.quality, self.work])
        self.request = ChatRequest(
            run_id=uuid4(), source=SOURCE, question="Based on this data, tell me which WOs need to be created.",
            deadline=utc_now() + timedelta(minutes=3), previous_run_id=self.previous, carried_from=self.previous,
            carried_evidence_ids=(self.quality.id, self.work.id), required_specialists=("work-order",),
            required_specialist_steps={"work-order": 1},
        )
        self.journal = RunJournal(self.root / str(self.request.run_id) / "receipts")
        self.owner = FoundrySupervisor(ENDPOINT, AsyncMock(), NodeSourceBridge(), AsyncMock(), SOURCE, self.root)

    async def asyncTearDown(self):
        await self.owner.bridge.aclose()
        self.temp.cleanup()

    def test_carried_receipts_are_seeded_with_their_original_read_times_and_saved(self):
        self.owner.load_carried_evidence(self.request, self.journal)
        self.assertEqual(set(self.owner.evidence), {self.quality.id, self.work.id})
        self.assertEqual(self.owner.evidence[self.quality.id].completed_at, self.quality.completed_at)
        self.assertEqual(self.owner.carried_ids, {self.quality.id, self.work.id})
        self.assertEqual(len(self.journal.read("carried_evidence")["receipts"]), 2)

    def test_recovery_uses_the_saved_copy_even_if_the_previous_journal_is_gone(self):
        self.owner.load_carried_evidence(self.request, self.journal)
        (self.root / str(self.previous) / "receipts").rename(self.root / "moved")
        again = FoundrySupervisor(ENDPOINT, AsyncMock(), NodeSourceBridge(), AsyncMock(), SOURCE, self.root)
        again.load_carried_evidence(self.request, self.journal)
        self.assertEqual(again.carried_ids, self.owner.carried_ids)

    def test_absent_or_substituted_evidence_fails_closed(self):
        (self.root / str(self.previous) / "receipts").rename(self.root / "moved")
        with self.assertRaisesRegex(SourceFailure, "no longer available"):
            self.owner.load_carried_evidence(self.request, self.journal)
        (self.root / "moved").rename(self.root / str(self.previous) / "receipts")
        other = self.request.model_copy(update={"carried_evidence_ids": (f"{self.previous}:missing",)})
        with self.assertRaisesRegex(SourceFailure, "differs"):
            self.owner.load_carried_evidence(other, RunJournal(self.root / "other" / "receipts"))

    def test_a_staged_draft_can_never_be_carried(self):
        other = uuid4()
        draft = receipt("propose_work_order", "work_orders", suffix="d", run=str(other))
        write_previous_run(self.root, other, [draft])
        request = self.request.model_copy(update={
            "previous_run_id": other, "carried_from": other, "carried_evidence_ids": (draft.id,),
        })
        with self.assertRaisesRegex(SourceFailure, "earlier verified read"):
            self.owner.load_carried_evidence(request, RunJournal(self.root / "x" / "receipts"))

    def test_the_answer_discloses_reused_evidence_and_its_age(self):
        self.assertEqual(self.owner.carried_limitations(self.request), ())
        self.owner.load_carried_evidence(self.request, self.journal)
        (text,) = self.owner.carried_limitations(self.request)
        self.assertIn("Reused evidence", text)
        self.assertIn("query_signal_quality_snapshot", text)
        self.assertIn("instead of being re-read", text)

    def test_work_order_specialist_gets_the_whole_carried_dataset_unless_an_rca_narrowed_the_focus(self):
        self.assertEqual(self.owner.model_evidence("work-order"), [])
        self.owner.load_carried_evidence(self.request, self.journal)
        self.assertEqual({item["id"] for item in self.owner.model_evidence("work-order")},
                         {self.quality.id, self.work.id})
        self.owner.specialists.append(SpecialistResult(
            role="rca", agent_name="hydro-rca-agent", version="1", response_id="r", duration_ms=1,
            input_digest="a" * 64, text="", report={},
        ))
        self.assertNotEqual({item["id"] for item in self.owner.model_evidence("work-order")},
                            {self.quality.id, self.work.id})


class FollowUpIntentBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = NodeSourceBridge()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        await self.bridge.aclose()
        self.temp.cleanup()

    async def ingress(self, question, previous_receipts, *, previous=None):
        previous = previous or uuid4()
        if previous_receipts:
            write_previous_run(self.root, previous, previous_receipts)
        chat = ChatInput(question=question, previous_run_id=previous)
        return await follow_up_intent(self.bridge, self.root, SOURCE, chat)

    async def test_ingress_carries_exactly_the_receipts_the_follow_up_needs(self):
        run = "22222222-2222-4222-8222-222222222222"
        quality = receipt("query_signal_quality_snapshot", "signal_quality", suffix="q", run=run)
        work = receipt("query_operations", "work_orders", suffix="w", run=run)
        power = receipt("query_station_power", "station_power", suffix="p", run=run)
        chat, carried = await self.ingress(
            "Based on this data, tell me which WOs need to be created and help me with the drafts.",
            [quality, work, power],
        )
        self.assertEqual(carried, (quality.id, work.id))
        self.assertEqual(chat.required_specialists, ("work-order",))
        self.assertEqual(set(chat.required_evidence_kinds), {"signal_quality", "work_orders"})

    async def test_ingress_reads_fresh_when_nothing_points_back_or_nothing_is_held(self):
        run = "33333333-3333-4333-8333-333333333333"
        held = [receipt("query_signal_quality_snapshot", "signal_quality", suffix="q", run=run),
                receipt("query_operations", "work_orders", suffix="w", run=run)]
        for question, receipts in (
            ("Which WOs need to be created and help me with the drafts?", held),
            ("Based on this data, tell me which WOs need to be created and draft them.", []),
        ):
            with self.subTest(question=question):
                chat, carried = await self.ingress(question, receipts)
                self.assertEqual(carried, ())
                self.assertIn("qa", chat.required_specialists)
                self.assertIn("temperature", chat.required_evidence_kinds)

    async def test_a_stale_previous_answer_is_never_reused(self):
        run = "44444444-4444-4444-8444-444444444444"
        old = [receipt("query_signal_quality_snapshot", "signal_quality", suffix="q", run=run,
                       age=CARRY_MAX_AGE + timedelta(minutes=2)),
               receipt("query_operations", "work_orders", suffix="w", run=run, age=CARRY_MAX_AGE + timedelta(minutes=2))]
        _chat, carried = await self.ingress("Based on this data, which WOs need to be created? Draft them.", old)
        self.assertEqual(carried, ())

    async def intent(self, question, carried):
        return await self.bridge.call({"action": "chat_intent", "question": question, "carried_kinds": carried})

    async def test_a_follow_up_pointing_at_held_data_drops_the_redundant_read(self):
        result = await self.intent("Based on this data, tell me which WOs need to be created and help me with the drafts.",
                                   ["signal_quality", "work_orders"])
        self.assertIs(result["reuse_prior_evidence"], True)
        self.assertEqual(result["required_specialists"], ["work-order"])
        self.assertEqual(set(result["required_evidence_kinds"]), {"signal_quality", "work_orders"})
        self.assertNotIn("temperature", result["required_evidence_kinds"])

    async def test_without_a_pointer_a_gap_or_a_single_specialist_the_data_is_read_again(self):
        for question, carried in (
            ("Which WOs need to be created and help me with the drafts?", ["signal_quality", "work_orders"]),
            ("Based on this data, tell me which WOs need to be created and draft them.", ["station_power"]),
            ("What work is open on those?", ["signal_quality", "work_orders"]),
            ("Based on this data, draft a work order and independently verify the draft.", ["signal_quality", "work_orders"]),
        ):
            with self.subTest(question=question):
                result = await self.intent(question, carried)
                self.assertIs(result["reuse_prior_evidence"], False)

    async def test_carried_kinds_are_validated_and_the_standalone_contract_is_unchanged(self):
        from hydro_orchestrator.live_sources import SourceFailure as BridgeFailure
        for bad in ("signal_quality", ["Bad Kind"], [1], ["a"] * 41):
            with self.subTest(bad=bad), self.assertRaises(BridgeFailure):
                await self.intent("Based on this data, draft work.", bad)
        plain = await self.bridge.call({"action": "chat_intent", "question": "Which turbines are hot?"})
        self.assertNotIn("reuse_prior_evidence", plain)
        self.assertEqual(set(plain), {
            "charts_requested", "native_sources", "proposal_priority", "equipment_selection",
            "required_evidence_kinds", "permitted_evidence_kinds", "required_specialists", "required_specialist_steps",
        })


if __name__ == "__main__":
    unittest.main()
