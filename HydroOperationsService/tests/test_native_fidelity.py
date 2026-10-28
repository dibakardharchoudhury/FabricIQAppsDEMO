import copy
import json
import tempfile
import unittest
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from hydro_orchestrator.live_sources import SourceFailure
from hydro_orchestrator.native_fidelity import source_answer
from hydro_orchestrator.native_data_agent import NativeDataAgentCall
from hydro_orchestrator.foundry_supervisor import (
    AgentVersion, ChatRequest, EvidenceDataset, FoundrySupervisor, NativeBinding,
    OperatorAnswerContract, RunJournal, SemanticDecision, SpecialistResult, ToolEvidence,
)
from hydro_orchestrator.contracts import utc_now
from hydro_orchestrator.presentation import client_presentation
from hydro_orchestrator.live_sources import NodeSourceBridge
from test_durable_service import SOURCE
from test_foundry_supervisor import SourceTools
from test_semantic_planning import decision


class NativeFidelityTests(unittest.TestCase):
    def body(self):
        question = "How are facilities, turbines and work orders related?"
        text = "Facility → System → Turbine → Work order.\n\n| Asset | Work |\n|---|---|\n| T001 | WO-1 |\n"
        return question, text, {
            "id": "response-1", "status": "completed", "error": None,
            "output": [{
                "type": "mcp_call", "id": "call-1", "server_label": "fabriciq-data-agent",
                "status": "completed", "error": None,
                "arguments": json.dumps({"userQuestion": question}),
                "output": json.dumps({"response": text}),
            }, {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text.strip()}],
            }],
            "content_filters": [{
                "source_type": "completion", "blocked": False,
                "content_filter_results": {"violence": {"filtered": False, "severity": "safe"}},
            }],
        }

    def test_exact_source_markdown_is_returned_not_the_model_completion(self):
        question, text, body = self.body()
        payload, original = source_answer(body, question, "fabriciq-data-agent")
        self.assertEqual(original, text)
        self.assertEqual(payload["response"], text)
        self.assertEqual(len(payload["source_answer_sha256"]), 64)

    def test_diagnostic_does_not_override_identical_completed_safe_final_answer(self):
        question, text, body = self.body()
        payload = json.loads(body["output"][0]["output"])
        payload["raiPostToolAnnotations"] = [{"error": {
            "errorDetails": "Error during output moderation for tool: DataAgent_Test for general harms.",
        }}]
        body["output"][0]["output"] = json.dumps(payload)
        before = copy.deepcopy(body)
        with self.assertLogs("hydro_orchestrator.native_fidelity", level="WARNING"):
            result, original = source_answer(body, question, "fabriciq-data-agent")
        self.assertEqual(original, text)
        self.assertNotIn("raiPostToolAnnotations", result)
        self.assertEqual(body, before, "Raw audit must retain all original diagnostics.")

    def test_blocked_missing_safety_errors_question_changes_and_rewrites_are_rejected(self):
        question, _, body = self.body()
        cases = []
        changed = copy.deepcopy(body)
        changed["status"] = "incomplete"
        cases.append(changed)
        changed = copy.deepcopy(body)
        changed["content_filters"][0]["blocked"] = True
        cases.append(changed)
        changed = copy.deepcopy(body)
        changed["content_filters"] = []
        cases.append(changed)
        changed = copy.deepcopy(body)
        changed["content_filters"][0]["content_filter_results"]["violence"]["filtered"] = True
        cases.append(changed)
        changed = copy.deepcopy(body)
        changed["output"][0]["arguments"] = json.dumps({"userQuestion": question + " Return JSON."})
        cases.append(changed)
        changed = copy.deepcopy(body)
        changed["output"][1]["content"][0]["text"] = "A rewritten summary."
        cases.append(changed)
        changed = copy.deepcopy(body)
        changed["output"][0]["error"] = {"code": "access_denied"}
        cases.append(changed)
        changed = copy.deepcopy(body)
        changed["output"].insert(0, copy.deepcopy(changed["output"][0]))
        cases.append(changed)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(SourceFailure):
                source_answer(case, question, "fabriciq-data-agent")


class NativeMixedWorkflowTests(unittest.TestCase):
    def owner(self, root):
        owner = FoundrySupervisor(
            "https://test.services.ai.azure.com/api/projects/test", AsyncMock(), NodeSourceBridge(),
            SourceTools(), SOURCE, Path(root), semantic_planning=True, analysis_model="test",
        )
        owner.answer_contract = OperatorAnswerContract.model_validate(
            decision("fabric-iq", "native_claims", False)["plan"]["answer_contract"],
        )
        text = "## BAD telemetry today\n\n| Turbine | Open work |\n|---|---|\n| T005 | None |\n"
        records = {
            "registry": [{"equipment_id": "EQUIP_RTI_T005", "opcua_node_id": "T005.speed", "unit": "rpm"}],
            "quality": [{"equipment_id": "EQUIP_RTI_T005", "bad_sample_count": 8}],
            "work": [{"equipmentId": "EQUIP_RTI_T001", "workOrderNumber": "WO-1", "status": "Ready"}],
        }
        owner.evidence["native-read"] = ToolEvidence(
            id="native-read", source=SOURCE, tool="fabriciq-data-agent", arguments={},
            completed_at=utc_now(),
            result={"response": text, "source_answer_sha256": sha256(text.encode()).hexdigest(), **records},
            datasets=tuple(EvidenceDataset(
                kind="native_claims", field=field, authority="native", scope="population",
                complete=True, identity_fields=("source_identity",),
            ) for field in records),
        )
        owner.specialists = [
            SpecialistResult(
                role=role, agent_name=f"hydro-{role}-agent", version="4",
                response_id=f"response-{role}", duration_ms=1, input_digest="a" * 64, text="Completed.",
            ) for role in ("fabric-iq", "work-order")
        ]
        return owner, text

    def test_mixed_workflow_displays_original_native_answer_without_sparse_projection(self):
        with tempfile.TemporaryDirectory() as root:
            owner, text = self.owner(root)
            request = ChatRequest(
                run_id=uuid4(), source=SOURCE, deadline=utc_now() + timedelta(minutes=3),
                question="Which turbines had BAD telemetry today? Check open work and help with drafts.",
            )
            self.assertIsNone(owner.native_answer(request), "This must exercise the multi-specialist path.")
            plan = owner.contract_answer_plan(request)
            self.assertEqual(plan.tables, (), "The original answer must not require a second table projection.")
            answer = owner.answer(request, plan, use_projected_summary=True)
            self.assertEqual(answer.native_answers[0].text, text)
            presentation = client_presentation(answer).text
            self.assertEqual(presentation.count(text), 1)
            self.assertNotIn("opcua\\_node\\_id", presentation, "Raw result-file columns must not replace the answer.")
            self.assertNotIn("workOrderNumber", presentation)
            self.assertEqual(owner.evidence["native-read"].result["quality"][0]["bad_sample_count"], 8)

    def test_structured_only_native_results_still_have_a_visible_projection(self):
        with tempfile.TemporaryDirectory() as root:
            owner, _ = self.owner(root)
            receipt = owner.evidence["native-read"]
            result = {key: value for key, value in receipt.result.items() if key != "response"}
            owner.evidence[receipt.id] = receipt.model_copy(update={"result": result})
            request = ChatRequest(
                run_id=uuid4(), source=SOURCE, question="Show the returned records.",
                deadline=utc_now() + timedelta(minutes=3),
            )
            plan = owner.contract_answer_plan(request)
            self.assertEqual(len(plan.tables), 1)
            presentation = client_presentation(owner.answer(request, plan, use_projected_summary=True)).text
            self.assertIn("opcua\\_node\\_id", presentation)

    def test_original_text_does_not_bypass_required_source_completeness(self):
        with tempfile.TemporaryDirectory() as root:
            owner, _ = self.owner(root)
            receipt = owner.evidence["native-read"]
            owner.evidence[receipt.id] = receipt.model_copy(update={
                "datasets": tuple(dataset.model_copy(update={"complete": False}) for dataset in receipt.datasets),
            })
            request = ChatRequest(
                run_id=uuid4(), source=SOURCE, question="Show quality and draft work.",
                deadline=utc_now() + timedelta(minutes=3),
            )
            with self.assertRaisesRegex(SourceFailure, "incomplete"):
                owner.contract_answer_plan(request)


class NativeInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_native_workflow_certifies_and_replays_without_answer_reconstruction(self):
        question, text, body = NativeFidelityTests().body()
        version = AgentVersion(
            name="hydro-fabric-iq-tools", version="4", model="native-fabric-responses",
            tools=({"type": "fabric_iq_preview", "project_connection_id": "native-data",
                    "server_label": "fabriciq-data-agent"},),
        )
        call = NativeDataAgentCall("native-source-1", {"input": question}, {
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": text},
            ]}],
        }, [], [])
        with tempfile.TemporaryDirectory() as root:
            owner = FoundrySupervisor(
                "https://test.services.ai.azure.com/api/projects/test", AsyncMock(), NodeSourceBridge(),
                SourceTools(), SOURCE, Path(root), semantic_planning=True, analysis_model="test",
                native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"},
                                             toolbox=version.name),
            )
            owner.versions = AsyncMock(return_value={
                "supervisor": AgentVersion(name="hydro-orchestrator", version="1", model="test", tools=()),
                "fabric-iq": version,
            })
            owner.fulfill_catalog_requirements = AsyncMock(side_effect=AssertionError("Native answer was rebuilt."))
            owner.analysis_for = AsyncMock(side_effect=AssertionError("Native answer was rewritten."))
            request = ChatRequest(
                run_id=uuid4(), source=SOURCE, question=question, deadline=utc_now() + timedelta(minutes=3),
            )
            journal = RunJournal(Path(root) / str(request.run_id) / "receipts")
            journal.save("semantic_decision", {
                "decision": decision("fabric-iq", "native_claims", False),
                "response_id": "planning-1", "duration_ms": 1,
            })
            with patch("hydro_orchestrator.foundry_supervisor.call_native_data_agent",
                       new_callable=AsyncMock, return_value=call) as native:
                answer = await owner.run(request)
                replay = await owner.run(request)
            self.assertEqual(answer, replay)
            self.assertEqual(answer.native_response, text)
            self.assertEqual(client_presentation(answer).text, text)
            self.assertEqual(journal.read("prepared_answer")["answer"]["native_response"], text)
            self.assertEqual(journal.read("answer")["native_response"], text)
            self.assertEqual(journal.read("evidence")["supervisor"]["response_id"], "planning-1")
            native.assert_awaited_once()
            self.assertEqual(native.call_args.args[3], question)
            self.assertEqual(answer.specialists[0].model_round_count, 0)
            self.assertEqual(owner.tools.calls, 0)
            owner.fulfill_catalog_requirements.assert_not_awaited()
            owner.analysis_for.assert_not_awaited()

    async def test_native_semantic_execution_keeps_original_question_and_bypasses_reconstruction(self):
        question, text, body = NativeFidelityTests().body()
        text = "## Telemetry findings\n\nEQUIP_RTI_T005 had BAD speed telemetry today. No open same-signal work was returned.\n"
        body["output"][0]["output"] = json.dumps({"response": text})
        body["output"][1]["content"][0]["text"] = text.strip()
        version = AgentVersion(
            name="hydro-fabric-iq-agent", version="4", model="test",
            tools=({"type": "fabric_iq_preview", "project_connection_id": "native-data",
                    "server_label": "fabriciq-data-agent"},),
        )
        body["agent_reference"] = {"type": "agent_reference", "name": version.name, "version": version.version}
        body["tools"] = list(version.tools)
        raw = SimpleNamespace(id=body["id"], model_dump=lambda **kwargs: copy.deepcopy(body))
        client = MagicMock()
        client.__aenter__.return_value = client
        client.responses.create = AsyncMock(return_value=raw)
        project = MagicMock()
        project.__aenter__.return_value = project
        project.get_openai_client.return_value = client
        bridge = AsyncMock()
        with tempfile.TemporaryDirectory() as root:
            owner = FoundrySupervisor(
                "https://test.services.ai.azure.com/api/projects/test", AsyncMock(), bridge, AsyncMock(),
                SOURCE, Path(root), semantic_planning=True, analysis_model="test",
                native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}),
            )
            owner.semantic_decision = SemanticDecision.model_validate(decision("fabric-iq", "native_claims", False))
            owner.answer_contract = OperatorAnswerContract.model_validate(
                decision("fabric-iq", "native_claims", False)["plan"]["answer_contract"],
            )
            request = ChatRequest(
                run_id=uuid4(), source=SOURCE, question=question, deadline=utc_now() + timedelta(minutes=3),
            )
            journal = RunJournal(Path(root) / "receipts")
            journal.save("tool_catalog", await SourceTools().catalog(request))
            with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=project):
                result, _, elapsed = await owner.invoke(
                    "fabric-iq", "A planner-rephrased assignment must not replace the operator's question.",
                    request, journal, {"fabric-iq": version}, "data-agent",
                )
            client.responses.create.assert_awaited_once_with(
                input=question, store=False, extra_body={"agent_reference": body["agent_reference"]},
            )
            self.assertEqual(result.text, text)
            owner.specialists.append(SpecialistResult(
                role="fabric-iq", agent_name=version.name, version=version.version, response_id=body["id"],
                duration_ms=elapsed, text=text, input_digest=owner.invocation_inputs[body["id"]],
            ))
            answer = owner.native_answer(request)
            self.assertIsNotNone(answer)
            self.assertEqual(client_presentation(answer).text, text)
            self.assertEqual(answer.tables, ())
            self.assertIsNone(answer.analysis)
            self.assertEqual(bridge.call.await_count, 1)
            self.assertEqual(journal.read("native_response:" + body["id"])["response"], body)
            owner.specialists.append(SpecialistResult(
                role="work-order", agent_name="hydro-work-order-agent", version="3",
                response_id="work-review", duration_ms=1, text="Reviewed.", input_digest="a" * 64,
            ))
            self.assertIsNone(owner.native_answer(request))
            await owner.fulfill_catalog_requirements(request, journal)
            plan = owner.contract_answer_plan(request)
            self.assertEqual(plan.tables, ())
            combined = owner.answer(request, plan, use_projected_summary=True)
            self.assertEqual(combined.native_answers[0].text, text)
            self.assertEqual(client_presentation(combined).text.count(text), 1)
