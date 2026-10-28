import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from hydro_orchestrator.live_sources import SourceFailure
from hydro_orchestrator.native_fidelity import source_answer
from hydro_orchestrator.foundry_supervisor import (
    AgentVersion, ChatRequest, FoundrySupervisor, NativeBinding, RunJournal, SpecialistResult,
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


class NativeInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_native_workflow_certifies_and_replays_without_answer_reconstruction(self):
        question, text, body = NativeFidelityTests().body()
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
        with tempfile.TemporaryDirectory() as root:
            owner = FoundrySupervisor(
                "https://test.services.ai.azure.com/api/projects/test", AsyncMock(), NodeSourceBridge(),
                SourceTools(), SOURCE, Path(root), semantic_planning=True, analysis_model="test",
                native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}),
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
            with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=project):
                answer = await owner.run(request)
                replay = await owner.run(request)
            self.assertEqual(answer, replay)
            self.assertEqual(answer.native_response, text)
            self.assertEqual(client_presentation(answer).text, text)
            self.assertEqual(journal.read("prepared_answer")["answer"]["native_response"], text)
            self.assertEqual(journal.read("answer")["native_response"], text)
            self.assertEqual(journal.read("evidence")["supervisor"]["response_id"], "planning-1")
            client.responses.create.assert_awaited_once()
            self.assertEqual(owner.tools.calls, 0)
            owner.fulfill_catalog_requirements.assert_not_awaited()
            owner.analysis_for.assert_not_awaited()

    async def test_native_semantic_execution_keeps_original_question_and_bypasses_reconstruction(self):
        question, text, body = NativeFidelityTests().body()
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
            request = ChatRequest(
                run_id=uuid4(), source=SOURCE, question=question, deadline=utc_now() + timedelta(minutes=3),
            )
            journal = RunJournal(Path(root) / "receipts")
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
