import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from hydro_orchestrator.foundry_supervisor import (
    CONVERSATION_INSTRUCTIONS, ChatAnswer, ChatRequest, HistoricalContext, HistoricalTurn, RunJournal,
)
from hydro_orchestrator.contracts import utc_now
from hydro_orchestrator.presentation import client_presentation
from hydro_orchestrator.service import preceding_context, turn_conclusion
from test_analysis import AnalysisHarness
from test_durable_service import SOURCE


def reply(**changes):
    value = {
        "answer": "We found BAD telemetry on EQUIP_RTI_T008 and staged a draft for it.",
        "findings": ["EQUIP_RTI_T015 was the other BAD turbine.", "A draft was staged for EQUIP_RTI_T008."],
        "caveats": ["The readings are from earlier in this chat."],
    }
    value.update(changes)
    return value


def history(now, **changes):
    value = {
        "run_id": uuid4(), "source": SOURCE, "question": "Draft the work order for the other turbine.",
        "rendered_answer": "## Answer\n\nA draft is staged for EQUIP_RTI_T008.",
        "requested_at": now - timedelta(minutes=1),
        "earlier_turns": (HistoricalTurn(
            question="Which turbines had BAD telemetry quality today?",
            conclusion="EQUIP_RTI_T008 and EQUIP_RTI_T015 had BAD telemetry.", requested_at=now - timedelta(minutes=5)),),
    }
    value.update(changes)
    return HistoricalContext(**value)


class ConversationTests(AnalysisHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.instructions = CONVERSATION_INSTRUCTIONS
        now = utc_now()
        self.context = history(now)
        self.request = ChatRequest(
            run_id=uuid4(), source=SOURCE, question="Summarise what we decided in this chat.",
            deadline=now + timedelta(minutes=3), previous_run_id=self.context.run_id, historical_context=self.context,
        )
        self.journal = RunJournal(self.root / str(self.request.run_id) / "receipts")

    async def test_a_summary_is_answered_from_every_earlier_turn_with_no_source_read(self):
        self.replies = [reply()]
        answer = await self.owner.converse(self.request, self.journal)
        sent = json.loads(self.inputs[0]["input"])
        self.assertEqual(sent["operator_message"], "Summarise what we decided in this chat.")
        self.assertEqual([turn["question"] for turn in sent["earlier_turns"]],
                         ["Which turbines had BAD telemetry quality today?"])
        self.assertIn("EQUIP_RTI_T008", sent["previous_turn"]["answer"])
        self.assertEqual((answer.tables, answer.specialists, answer.source_read_times), ((), (), ()))
        self.assertEqual(answer.analysis.findings[1], "A draft was staged for EQUIP_RTI_T008.")
        self.assertEqual(self.journal.read("answer")["summary"], answer.summary)
        self.owner.tools.execute.assert_not_called()
        text = client_presentation(answer).text
        self.assertIn("## From this conversation", text)
        self.assertNotIn("verified evidence below", text)

    async def test_earlier_receipts_are_forwarded_so_a_later_turn_can_still_reuse_them(self):
        (self.root / str(self.context.run_id) / "receipts").mkdir(parents=True)
        RunJournal(self.root / str(self.context.run_id) / "receipts").save("evidence", {"receipts": [{"id": "r1"}]})
        self.replies = [reply()]
        await self.owner.converse(self.request, self.journal)
        self.assertEqual(self.journal.read("evidence"), {"receipts": [{"id": "r1"}], "conversation_only": True})

    async def test_an_ungrounded_answer_degrades_to_a_visible_reason_instead_of_failing_the_turn(self):
        invented = reply(answer="EQUIP_RTI_T099 was discussed.")
        self.replies = [invented, invented, invented]
        answer = await self.owner.converse(self.request, self.journal)
        self.assertIn("could not verify", answer.summary)
        self.assertIn("EQUIP_RTI_T099", answer.analysis.caveats[0])

    async def test_a_run_that_needs_no_source_read_is_answered_not_failed(self):
        self.replies = [reply()]
        answer = await self.owner.run(self.request)
        self.assertIsInstance(answer, ChatAnswer)
        self.assertEqual(answer.tables, ())
        self.assertEqual(len(self.inputs), 1)

    async def test_a_first_message_with_no_conversation_still_gets_an_answer(self):
        first = self.request.model_copy(update={"previous_run_id": None, "historical_context": None})
        self.replies = [reply(answer="There is no conversation yet.", findings=[], caveats=[])]
        answer = await self.owner.converse(first, RunJournal(self.root / "first" / "receipts"))
        sent = json.loads(self.inputs[0]["input"])
        self.assertEqual((sent["earlier_turns"], sent["previous_turn"]), ([], None))
        self.assertEqual(answer.summary, "There is no conversation yet.")


class ConversationChainTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, question, answer_text, context=None, age=timedelta(minutes=1)):
        now = utc_now()
        requested_at = now - age
        request = ChatRequest(
            run_id=uuid4(), source=SOURCE, question=question, deadline=requested_at + timedelta(minutes=3),
            requested_at=requested_at, previous_run_id=context.run_id if context else None, historical_context=context,
        )
        answer = ChatAnswer(
            run_id=request.run_id, source=SOURCE, requested_at=request.requested_at, source_read_times=(),
            summary=answer_text, tables=(), charts=(), limitations=(), cell_sources=(), specialists=(),
            audit_url="/x",
        )
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("request", request.model_dump(mode="json"))
        journal.save("answer", answer.model_dump(mode="json"))
        return request

    def test_the_context_accumulates_every_turn_of_the_chat_in_order(self):
        first = self.commit("Which turbines were BAD?", "T008 and T015 were BAD.")
        second = self.commit("Draft the other one.", "Drafted T008.", preceding_context(self.root, SOURCE, first.run_id))
        third = preceding_context(self.root, SOURCE, second.run_id)
        self.assertEqual(third.question, "Draft the other one.")
        self.assertEqual([turn.question for turn in third.earlier_turns], ["Which turbines were BAD?"])
        self.assertEqual(third.earlier_turns[0].conclusion, "T008 and T015 were BAD.")

    def test_only_the_latest_turns_are_kept_and_an_old_chat_is_still_usable(self):
        request = self.commit("Question 0", "Answer 0", age=timedelta(hours=3))
        for index in range(1, 9):
            request = self.commit(f"Question {index}", f"Answer {index}",
                                  preceding_context(self.root, SOURCE, request.run_id), age=timedelta(hours=3))
        context = preceding_context(self.root, SOURCE, request.run_id)
        self.assertEqual(len(context.earlier_turns), 5)
        self.assertEqual([turn.question for turn in context.earlier_turns][-1], "Question 7")

    def test_the_conclusion_is_the_direct_answer_without_supporting_sections(self):
        self.assertEqual(turn_conclusion("## Answer\n\nT008 is BAD.\n\n## What the data shows\n\n- row"), "T008 is BAD.")
        self.assertEqual(turn_conclusion("Plain summary."), "Plain summary.")
        self.assertLessEqual(len(turn_conclusion("x" * 5000)), 1500)


if __name__ == "__main__":
    unittest.main()
