import copy
import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from azure.core.credentials import AccessToken
from azure.identity.aio import AzureCliCredential
from openai import AsyncOpenAI
from agent_framework import FileCheckpointStorage
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from hydro_orchestrator.contracts import WorkOrderDraft, utc_now
from hydro_orchestrator.foundry_supervisor import (
    AgentVersion, AnswerPlan, ChatRequest, FoundrySupervisor, NativeBinding, RunJournal, ToolEvidence,
)
from hydro_orchestrator.live_sources import NodeSourceBridge, SourceFailure
from hydro_orchestrator.service import create_app
from test_durable_service import SOURCE, HEADERS, TOKEN

ENDPOINT = "https://test.services.ai.azure.com/api/projects/test"


class SourceTools:
    def __init__(self):
        self.calls = 0
        self.failure = False
        self.changed_source = False
        self.extra_row = False
        self.stage = False

    async def catalog(self, request):
        tools = [{"name": "query_telemetry", "parameters": {
            "type": "object", "properties": {"equipment_id": {"type": "string"}},
            "required": ["equipment_id"], "additionalProperties": False,
        }}]
        if self.stage:
            tools.append({"name": "propose_work_order", "parameters": {
                "type": "object", "properties": {
                    key: {"type": "string"} for key in ("equipment_id", "title", "description")
                }, "required": ["equipment_id", "title", "description"], "additionalProperties": False,
            }})
        return {"tools": tools, "context": {"mode": "test-only-no-production-access"}}

    async def execute(self, name, arguments, request):
        self.calls += 1
        if self.failure:
            raise SourceFailure("Injected source outage")
        if name == "propose_work_order":
            clock = utc_now()
            draft = WorkOrderDraft(
                id=uuid4(), run_id=request.run_id, source=request.source, **arguments,
                priority=request.proposal_priority, work_read_at=clock,
                expires_at=clock + timedelta(minutes=15), existing_work=(),
            )
            return ToolEvidence(
                id="proposal-1", source=request.source, tool=name, arguments=arguments, completed_at=clock,
                result={"proposal": draft.model_dump(mode="json"), "proposal_digest": draft.digest(),
                        "confirmation_required": True, "production_write_executed": False},
                resolved_equipment_ids=(draft.equipment_id,), work_coverage_equipment_ids=(draft.equipment_id,),
            )
        rows = [{"equipment_id": "TEST_T005", "value": 75.0, "unit": "C", "quality": "BAD"}]
        if self.extra_row:
            rows.append({"equipment_id": "TEST_T005", "value": 99.0, "unit": "MW", "quality": "GOOD"})
        return ToolEvidence(
            id="reading-1", source=SOURCE.model_copy(update={"workspace_id": uuid4()}) if self.changed_source else SOURCE,
            tool=name, arguments=arguments, completed_at=utc_now(),
            result={"rows": rows},
            column_units={"value": "C"},
            limitations=("No approved diagnostic limits supplied.",),
        )


class FoundrySupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = NodeSourceBridge()
        contracts = await self.bridge.call({"action": "agent_contracts"})
        self.versions = {
            role: AgentVersion(name=name, version="12", model="test", tools=tuple(contracts["tools"].get(role, [
                {"type": "fabric_iq_preview", "server_label": "fabriciq-data-agent", "project_connection_id": "native-data"},
                {"type": "fabric_iq_preview", "server_label": "fabriciq-ontology", "project_connection_id": "native-ontology"},
            ]))) for role, name in contracts["names"].items()
        }
        self.request = ChatRequest(
            run_id=uuid4(), source=SOURCE, question="Read BAD signal quality; investigate, review work and verify.",
            deadline=utc_now() + timedelta(minutes=3),
        )
        self.credential = AsyncMock(spec=AzureCliCredential)
        self.credential.get_token.return_value = AccessToken("test-not-a-real-token", 9999999999)
        self.tools = SourceTools()
        self.calls = {}
        self.payloads = []
        self.mode = "valid"
        self.network = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.openai = AsyncOpenAI(api_key="test-not-a-real-key", base_url=f"{ENDPOINT}/openai/v1/",
                                 http_client=self.network, max_retries=0)
        self.client_patch = patch("azure.ai.projects.aio.AIProjectClient.get_openai_client", return_value=self.openai)
        self.client_patch.start()
        self.events = []
        self.owner = self.make_owner()
        self.owner.set_event_sink(self.events.append)
        self.seed(self.request)

    async def asyncTearDown(self):
        self.client_patch.stop()
        await self.openai.close()
        self.temp.cleanup()

    def make_owner(self, **kwargs):
        return FoundrySupervisor(ENDPOINT, self.credential, self.bridge, self.tools, SOURCE, self.root, **kwargs)

    def seed(self, request, root=None):
        journal = RunJournal((root or self.root) / str(request.run_id) / "receipts")
        journal.save("agent_versions", {role: item.model_dump(mode="json") for role, item in self.versions.items()})

    def function(self, name, arguments):
        return {"type": "function_call", "id": f"fc_{uuid4().hex}", "call_id": f"call_{uuid4().hex}",
                "name": name, "arguments": json.dumps(arguments), "status": "completed"}

    def message(self, text):
        return {"type": "message", "id": f"msg_{uuid4().hex}", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}]}

    def plan(self):
        plan = {
            "summary": "A model-authored diagnosis must not become the operator summary.",
            "tables": [{
                "id": "measurements", "title": "Measurements",
                "columns": [{"key": "value", "label": "Value", "kind": "number"},
                            {"key": "unit", "label": "Unit", "kind": "text"}],
                "rows": [{"cells": [
                    {"key": "value", "source": {"evidence_id": "reading-1", "path": "/rows/0/value"}},
                    {"key": "unit", "source": {"evidence_id": "reading-1", "path": "/rows/0/unit"}},
                ]}],
            }],
            "charts": [], "limitations": [],
        }
        if self.mode == "invented":
            plan["tables"][0]["rows"][0]["cells"][0]["source"]["evidence_id"] = "invented-source"
        elif self.mode == "transposed":
            plan["tables"][0]["rows"][0]["cells"][0]["source"]["path"] = "/rows/0/unit"
        elif self.mode == "extra_column":
            plan["tables"][0]["rows"][0]["cells"].append(copy.deepcopy(plan["tables"][0]["rows"][0]["cells"][0]))
        elif self.mode == "mixed_rows":
            plan["tables"][0]["rows"][0]["cells"][1]["source"]["path"] = "/rows/1/unit"
        elif self.mode in ("alias_once", "projection_incomplete") and self.calls.get("hydro-supervisor-agent") == 5:
            plan["tables"][0]["columns"][0]["key"] = "temperature"
            plan["tables"][0]["rows"][0]["cells"][0]["key"] = "temperature"
        elif self.mode in ("chart", "bad_axis"):
            plan["charts"] = [{"table_id": "measurements", "kind": "bar", "x_key": "unit",
                               "y_keys": ["unit" if self.mode == "bad_axis" else "value"]}]
        elif self.mode.startswith("native"):
            for cell in plan["tables"][0]["rows"][0]["cells"]:
                cell["source"]["evidence_id"] = "native-call-1"
        return plan

    def respond(self, request):
        payload = json.loads(request.content)
        self.payloads.append(payload)
        if "agent_reference" not in payload:
            self.assertEqual(payload["model"], "test")
            self.assertNotIn("tools", payload)
            incomplete = self.mode == "projection_incomplete"
            if self.mode in ("alias_once", "projection_incomplete"):
                self.mode = "valid"
            return httpx.Response(200, json={
                "id": "resp_projection", "object": "response", "created_at": 1,
                "model": "test", "status": "incomplete" if incomplete else "completed",
                "incomplete_details": {"reason": "max_output_tokens"} if incomplete else None,
                "output": [self.message(json.dumps(self.plan()))],
                "parallel_tool_calls": False,
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            })
        reference = payload["agent_reference"]
        self.assertEqual(reference["version"], "12")
        self.assertNotIn("tools", payload)
        self.assertNotIn("text", payload)
        self.assertFalse(payload["store"])
        name = reference["name"]
        round_number = self.calls.get(name, 0)
        self.calls[name] = round_number + 1
        if name == "hydro-supervisor-agent":
            if self.mode == "batch_handoffs" and round_number == 0:
                output = [self.function("delegate_to_agent", {
                    "specialist": role, "question": question, "reason": "Execute the requested sequence.",
                    "native_source": None,
                }) for role, question in [
                    ("qa", "Read and verify quality."), ("rca", "Investigate quality."),
                    ("work-order", "Review existing work."), ("qa", "Read and verify quality."),
                ]]
            elif self.mode == "batch_handoffs":
                output = [self.message(json.dumps(self.plan()))]
            elif self.mode.startswith("native") and round_number == 0:
                output = [self.function("delegate_to_agent", {
                    "specialist": "fabric-iq", "question": "Use the Data Agent to return the requested rows as JSON.",
                    "reason": "Use the explicitly selected native source.", "native_source": "data-agent",
                })]
            elif self.mode.startswith("native"):
                output = [self.message(json.dumps(self.plan()))]
            elif self.mode == "skip_verify" and round_number >= 3:
                output = [self.message(json.dumps(self.plan()))]
            elif round_number < 4:
                roles = ["qa", "rca", "work-order", "qa"]
                questions = ["Read and verify quality.", "Investigate quality.", "Review existing work.",
                             "Read and verify quality."]
                output = [self.function("delegate_to_agent", {
                    "specialist": roles[round_number], "question": questions[round_number],
                    "reason": "Execute the assigned capability.", "native_source": None,
                })]
            else:
                output = [self.message(json.dumps(self.plan()))]
        elif name == "hydro-qa-agent":
            output = [self.function("hydro_query", {"tool_name": "query_telemetry",
                                                  "arguments": {"equipment_id": "TEST_T005"}})] if round_number % 2 == 0 else [
                self.message("Actual BAD quality sample; no physical fault is established.")]
            if self.mode == "batch" and round_number % 2 == 0:
                output.append(self.function("hydro_query", {"tool_name": "query_telemetry",
                                                         "arguments": {"equipment_id": "TEST_T005"}}))
            if self.mode in ("repair", "repeat_invalid") and round_number == 0:
                output = [self.function("hydro_query", {"tool_name": "query_telemetry", "arguments": {
                    "equipment_id": "TEST_T005", "equipmentId": "TEST_T005",
                }})]
            elif self.mode in ("repair", "repeat_invalid") and round_number == 1:
                arguments = {"equipment_id": "TEST_T005"}
                if self.mode == "repeat_invalid":
                    arguments["equipmentId"] = "TEST_T005"
                output = [self.function("hydro_query", {"tool_name": "query_telemetry", "arguments": arguments})]
        elif name == "hydro-rca-agent":
            ref = {"evidence_id": "reading-1", "path": "/rows/0"}
            report = {"observations": [ref], "hypotheses": [
                {"category": "sensor_or_ingestion", "supporting": [ref], "contradicting": [],
                 "missing": ["independent_measurement"]},
                {"category": "equipment_condition", "supporting": [], "contradicting": [],
                 "missing": ["inspection_evidence"]},
            ]}
            output = [self.function("complete_rca_assessment", report)] if round_number == 0 else [
                self.message("Structured source-referenced investigation completed.")]
            if self.mode == "repair_query_and_report":
                if round_number == 0:
                    output = [self.function("hydro_query", {
                        "tool_name": "query_telemetry",
                        "arguments": {"equipment_id": "TEST_T005", "equipmentId": "TEST_T005"},
                    })]
                elif round_number == 1:
                    output = [self.function("hydro_query", {
                        "tool_name": "query_telemetry", "arguments": {"equipment_id": "TEST_T005"},
                    })]
                elif round_number == 2:
                    report["observations"][0]["path"] = "/data/rows/0"
                    output = [self.function("complete_rca_assessment", report)]
                elif round_number == 3:
                    output = [self.function("complete_rca_assessment", report)]
        elif name == "hydro-work-order-agent":
            output = [self.function("complete_work_order_review", {
                "decision": "needs_clarification", "reason": "Inspection scope is not supplied by the source.",
            })] if round_number == 0 else [self.message("No SQL write executed.")]
            if self.mode == "proposal" and round_number == 0:
                output = [self.function("hydro_query", {"tool_name": "propose_work_order", "arguments": {
                    "equipment_id": "TEST_T005", "title": "Inspect BAD signal",
                    "description": "Inspect signal quality; no physical diagnosis is established.",
                }})]
            if self.mode == "repair_review" and round_number == 0:
                output = [self.function("complete_work_order_review", {
                    "decision": "needs_clarification", "reason": "X" * 1001,
                })]
            elif self.mode == "repair_review" and round_number == 1:
                output = [self.function("complete_work_order_review", {
                    "decision": "needs_clarification", "reason": "Missing scope.",
                })]
        elif name == "hydro-fabric-iq-agent":
            output = [{
                "type": "mcp_call", "id": "native-call-1", "name": "query",
                "server_label": "fabriciq-ontology" if self.mode == "native_wrong_source" else "fabriciq-data-agent",
                "arguments": "{}", "status": "completed", "error": None,
                "output": json.dumps({"rows": [{"equipment_id": "TEST_T005", "value": 75.0, "unit": "C"}]}),
            }, self.message("Native result.")]
        else:
            raise AssertionError(name)
        return httpx.Response(200, json={
            "id": f"resp_{name}_{round_number}", "object": "response", "created_at": 1,
            "model": "test", "status": "completed", "output": output, "parallel_tool_calls": False,
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        })

    async def test_actual_framework_runs_existing_supervisor_and_specialists_with_shared_evidence(self):
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(self.calls["hydro-rca-agent"], 1)
        self.assertEqual(self.calls["hydro-work-order-agent"], 1)
        self.assertEqual(result.tables[0].rows[0].values, {"value": 75.0, "unit": "C"})
        self.assertNotIn("diagnosis", result.summary)
        self.assertIn("No approved diagnostic", result.limitations[0])
        self.assertFalse(result.production_write_executed)
        self.assertEqual(result.charts, ())
        specialist_events = [event for event in self.events if event["role"] != "supervisor"]
        self.assertEqual(
            [(event["role"], event["status"]) for event in specialist_events],
            [
                ("qa", "running"), ("qa", "completed"),
                ("rca", "running"), ("rca", "completed"),
                ("work-order", "running"), ("work-order", "completed"),
                ("qa", "running"), ("qa", "completed"),
            ],
        )
        for running, completed in zip(specialist_events[::2], specialist_events[1::2], strict=True):
            self.assertEqual(running["id"], completed["id"])
            self.assertEqual(running["parentId"], f"{self.request.run_id}:chief")
            self.assertEqual(running["parentCallId"], completed["parentCallId"])
            self.assertEqual(completed["agentName"], completed["label"])
            self.assertTrue(str(completed["responseId"]).startswith("resp_"))
        chief_events = [event for event in self.events if event["role"] == "supervisor"]
        self.assertEqual(chief_events[0]["status"], "running")
        self.assertEqual(chief_events[-1]["status"], "completed")
        trace = chief_events[-1]["trace"]
        self.assertEqual(len(trace), 8)
        self.assertEqual(
            [item["activity"] for item in trace],
            ["tool-start", "delegation-return"] * 4,
        )
        serialized = json.dumps(self.events)
        self.assertNotIn("test-not-a-real-token", serialized)
        self.assertNotIn("rows", serialized)

    async def test_actual_framework_fixer_stages_source_bound_human_approval_card_without_sql(self):
        self.mode = "proposal"
        self.tools.stage = True
        self.request = self.request.model_copy(update={
            "question": "Read BAD quality, investigate and propose a TEST_T005 signal inspection; verify.",
        })
        result = await self.owner.run(self.request)
        self.assertEqual(self.tools.calls, 2)
        self.assertEqual(len(result.proposals), 1)
        draft = result.proposals[0]
        self.assertEqual(draft.equipment_id, "TEST_T005")
        self.assertEqual(draft.priority, "Medium")
        self.assertEqual(draft.source, self.request.source)
        self.assertEqual(result.proposal_digests, {str(draft.id): draft.digest()})
        self.assertFalse(result.production_write_executed)
        self.assertEqual(result.tables[0].rows[0].values, {"value": 75.0, "unit": "C"})
        evidence = self.owner.evidence["proposal-1"]
        self.owner.evidence["proposal-copy"] = evidence.model_copy(update={"id": "proposal-copy"})
        self.assertEqual(len(self.owner.answer(self.request, AnswerPlan.model_validate(self.plan())).proposals), 1)
        changed = draft.model_copy(update={"description": "A different scope with the same card identity."})
        self.owner.evidence["proposal-copy"] = evidence.model_copy(update={
            "id": "proposal-copy", "result": {**evidence.result, "proposal": changed.model_dump(mode="json")},
        })
        with self.assertRaisesRegex(SourceFailure, "Conflicting source receipts"):
            self.owner.answer(self.request, AnswerPlan.model_validate(self.plan()))
        self.assertTrue(all("text" not in payload for payload in self.payloads))
        before = dict(self.calls)
        restarted = await self.make_owner().run(self.request)
        self.assertEqual(restarted, result)
        self.assertEqual(self.calls, before)

    async def test_source_failure_cannot_become_a_successful_supervisor_answer(self):
        self.tools.failure = True
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="ERROR"):
            with self.assertRaisesRegex(SourceFailure, "validation failed"):
                await self.owner.run(self.request)
        self.assertIsNone(RunJournal(self.root / str(self.request.run_id) / "receipts").read("answer"))
        self.assertNotIn("hydro-rca-agent", self.calls)
        qa_events = [event for event in self.events if event["role"] == "qa"]
        self.assertEqual([event["status"] for event in qa_events], ["running", "error"])
        self.assertEqual(qa_events[0]["parentCallId"], qa_events[1]["parentCallId"])
        chief_events = [event for event in self.events if event["role"] == "supervisor"]
        self.assertEqual(chief_events[-1]["status"], "running")
        self.assertTrue(chief_events[-1]["trace"][-1]["failed"])

    async def test_unrequested_charts_wrong_cells_and_invented_sources_fail_closed(self):
        for mode in ("chart", "invented", "transposed", "extra_column"):
            with self.subTest(mode=mode):
                self.mode = mode
                self.calls.clear()
                request = self.request.model_copy(update={"run_id": uuid4()})
                self.seed(request)
                with self.assertRaises((SourceFailure, ValidationError)):
                    await self.make_owner().run(request)

    async def test_explicit_chart_is_typed_and_unrelated_chart_is_not_added(self):
        self.mode = "chart"
        request = self.request.model_copy(update={"charts_requested": True})
        result = await self.owner.run(request)
        self.assertEqual(len(result.charts), 1)
        self.assertEqual(result.charts[0].y_keys, ("value",))

    async def test_explicit_chart_cannot_be_silently_omitted_when_source_units_exist(self):
        receipt = await self.tools.execute("query_telemetry", {}, self.request)
        self.owner.evidence[receipt.id] = receipt
        request = self.request.model_copy(update={"charts_requested": True})
        with self.assertRaisesRegex(SourceFailure, "silently omit"):
            self.owner.answer(request, AnswerPlan.model_validate(self.plan()))
        omitted_table = self.plan()
        omitted_table["tables"] = []
        with self.assertRaisesRegex(SourceFailure, "silently omit"):
            self.owner.answer(request, AnswerPlan.model_validate(omitted_table))

    async def test_chart_inability_is_derived_from_actual_missing_source_units(self):
        receipt = await self.tools.execute("query_telemetry", {}, self.request)
        for unit in (None, "", " "):
            with self.subTest(unit=unit):
                source = receipt.model_copy(update={
                    "column_units": {}, "result": {"rows": [{"value": 75.0, "unit": unit}]},
                })
                self.owner.evidence[source.id] = source
                request = self.request.model_copy(update={"charts_requested": True})
                result = self.owner.answer(request, AnswerPlan.model_validate(self.plan()))
                self.assertEqual(result.charts, ())
                self.assertIn("no numeric measure with verified units", result.limitations[-1])
                if unit is not None:
                    self.mode = "chart"
                    with self.assertRaisesRegex(SourceFailure, "consistent source units"):
                        self.owner.answer(request, AnswerPlan.model_validate(self.plan()))
                    self.mode = "valid"

    async def test_cross_source_rows_require_authoritative_join_identity_without_displaying_it(self):
        first = await self.tools.execute("query_telemetry", {}, self.request)
        second = first.model_copy(update={
            "id": "work-1", "tool": "query_operations", "result": {
                "rows": [{"equipmentId": "TEST_T005", "status": "Scheduled"}],
            },
        })
        plan = self.plan()
        plan["tables"][0]["columns"][1] = {"key": "status", "label": "Status", "kind": "text"}
        plan["tables"][0]["rows"][0]["cells"][1] = {
            "key": "status", "source": {"evidence_id": second.id, "path": "/rows/0/status"},
        }
        self.owner.evidence = {first.id: first, second.id: second}
        result = self.owner.answer(self.request, AnswerPlan.model_validate(plan))
        self.assertEqual(result.tables[0].rows[0].values, {"value": 75.0, "status": "Scheduled"})
        for row in ({"status": "Scheduled"}, {"equipmentId": "", "status": "Scheduled"},
                    {"equipmentId": "TEST_T006", "status": "Scheduled"}):
            with self.subTest(row=row):
                self.owner.evidence[second.id] = second.model_copy(update={"result": {"rows": [row]}})
                with self.assertRaisesRegex(SourceFailure, "identit"):
                    self.owner.answer(self.request, AnswerPlan.model_validate(plan))
        self.owner.evidence[first.id] = first.model_copy(update={
            "result": {"rows": [{"opcua_node_id": "TEST_T005.temp", "value": 75.0}]},
        })
        self.owner.evidence[second.id] = second.model_copy(update={
            "result": {"rows": [{"opcuaNodeId": "TEST_T005.temp", "status": "Scheduled"}]},
        })
        result = self.owner.answer(self.request, AnswerPlan.model_validate(plan))
        self.assertEqual(result.tables[0].rows[0].values["status"], "Scheduled")

    async def test_station_mean_retains_literal_cells_and_only_explicitly_requested_mw_chart(self):
        receipt = await self.tools.execute("query_telemetry", {}, self.request)
        receipt = receipt.model_copy(update={
            "tool": "query_station_power",
            "result": {"rows": [{"Station": "North", "average_power_MW": 8 / 3}]},
            "column_units": {"average_power_MW": "MW"},
        })
        self.owner.evidence = {receipt.id: receipt}
        plan = self.plan()
        table = plan["tables"][0]
        table["columns"] = [{"key": "Station", "label": "Station", "kind": "text"},
                            {"key": "average_power_MW", "label": "Mean power", "kind": "number"}]
        table["rows"] = [{"cells": [
            {"key": key, "source": {"evidence_id": receipt.id, "path": f"/rows/0/{key}"}}
            for key in ("Station", "average_power_MW")
        ]}]
        answer = self.owner.answer(self.request, AnswerPlan.model_validate(plan))
        self.assertEqual(answer.charts, ())
        self.assertEqual(answer.tables[0].rows[0].values, {"Station": "North", "average_power_MW": 8 / 3})
        requested = self.request.model_copy(update={"charts_requested": True})
        with self.assertRaisesRegex(SourceFailure, "chart"):
            self.owner.answer(requested, AnswerPlan.model_validate(plan))
        plan["charts"] = [{"table_id": table["id"], "kind": "bar", "x_key": "Station",
                           "y_keys": ["average_power_MW"]}]
        answer = self.owner.answer(requested, AnswerPlan.model_validate(plan))
        self.assertEqual(len(answer.charts), 1)
        self.assertEqual(answer.tables[0].rows[0].values["average_power_MW"], 8 / 3)
        with self.assertRaisesRegex(SourceFailure, "chart"):
            self.owner.answer(self.request, AnswerPlan.model_validate(plan))

    async def test_projected_cross_source_rows_use_private_identity_but_never_display_it(self):
        first = await self.tools.execute("query_telemetry", {}, self.request)
        first = first.model_copy(update={
            "result": {"rows": [{"value": 75.0}]},
            "row_identities": {"/rows/0": {"equipment_id": "TEST_T005"}},
        })
        second = first.model_copy(update={
            "id": "work-1", "tool": "query_operations",
            "result": {"rows": [{"status": "Scheduled"}]},
            "row_identities": {"/rows/0": {"equipmentId": "TEST_T005"}},
        })
        plan = self.plan()
        plan["tables"][0]["columns"][1] = {"key": "status", "label": "Status", "kind": "text"}
        plan["tables"][0]["rows"][0]["cells"][1] = {
            "key": "status", "source": {"evidence_id": second.id, "path": "/rows/0/status"},
        }
        self.owner.evidence = {first.id: first, second.id: second}
        result = self.owner.answer(self.request, AnswerPlan.model_validate(plan))
        self.assertEqual(result.tables[0].rows[0].values, {"value": 75.0, "status": "Scheduled"})
        self.assertNotIn("row_identities", first.receipt())
        schema = self.owner.answer_schema()
        self.assertNotIn("equipment_id", json.dumps(schema))
        for identity in ("TEST_T006", " "):
            self.owner.evidence[second.id] = second.model_copy(update={
                "row_identities": {"/rows/0": {"equipmentId": identity}},
            })
            with self.assertRaisesRegex(SourceFailure, "identit"):
                self.owner.answer(self.request, AnswerPlan.model_validate(plan))

    async def test_deadline_and_changed_source_stop_before_model_calls(self):
        expired = self.request.model_copy(update={"deadline": utc_now() - timedelta(seconds=1)})
        with self.assertRaisesRegex(SourceFailure, "deadline"):
            await self.owner.run(expired)
        self.assertEqual(self.calls, {})
        changed = self.request.model_copy(update={"source": SOURCE.model_copy(update={"workspace_id": uuid4()})})
        with self.assertRaisesRegex(SourceFailure, "deployment identity"):
            await self.make_owner().run(changed)

    async def test_run_id_cannot_be_reused_for_a_changed_question(self):
        await self.owner.run(self.request)
        changed = self.request.model_copy(update={"question": "A different question."})
        with self.assertRaisesRegex(SourceFailure, "immutable request"):
            await self.make_owner().run(changed)

    async def test_changed_provider_identity_is_not_committed_as_evidence(self):
        self.tools.changed_source = True
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="ERROR"):
            with self.assertRaisesRegex(SourceFailure, "validation failed"):
                await self.owner.run(self.request)
        journal = RunJournal(self.root / str(self.request.run_id) / "receipts")
        self.assertIsNone(journal.read('tool:{"arguments":{"equipment_id":"TEST_T005"},"name":"query_telemetry"}'))

    async def test_delegation_budget_stops_instead_of_simulating_remaining_roles(self):
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="ERROR"):
            with self.assertRaisesRegex(SourceFailure, "validation failed"):
                await self.make_owner(max_delegations=1).run(self.request)
        self.assertNotIn("hydro-rca-agent", self.calls)

    async def test_corrupt_receipt_is_not_replaced_by_an_empty_fallback(self):
        journal = RunJournal(self.root / "corrupt")
        journal.save("test", {"value": 1})
        file = next(journal.root.glob("*.json"))
        file.write_text("invalid-json", encoding="utf8")
        with self.assertRaises(json.JSONDecodeError):
            journal.read("test")

    async def test_shared_work_review_contract_rejects_extra_fields_and_blank_reasons(self):
        for payload in ({"decision": "no_draft", "reason": ""},
                        {"decision": "no_draft", "reason": "Scope", "invented": True}):
            with self.assertRaises(SourceFailure):
                await self.bridge.call({"action": "validate_work_review", "report": json.dumps(payload)})

    async def test_native_source_requires_verified_binding_and_a_matching_execution_receipt(self):
        self.mode = "native"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="ERROR"):
            with self.assertRaisesRegex(SourceFailure, "validation failed"):
                await self.make_owner().run(request)
        self.assertNotIn("hydro-fabric-iq-agent", self.calls)
        self.calls.clear()
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual(result.specialists[0].role, "fabric-iq")
        self.assertEqual(result.tables[0].rows[0].values, {"value": 75.0, "unit": "C"})
        self.assertEqual(self.tools.calls, 0)
        self.assertIn("does not attest", result.limitations[0])

    async def test_native_source_mismatch_does_not_fall_back_to_direct_tools(self):
        self.mode = "native_wrong_source"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="ERROR"):
            with self.assertRaisesRegex(SourceFailure, "validation failed"):
                await owner.run(request)
        self.assertEqual(self.tools.calls, 0)

    async def test_partial_receipt_after_process_stop_is_discarded_not_used_as_evidence(self):
        journal = RunJournal(self.root / "partial")
        journal.root.mkdir(parents=True)
        from hashlib import sha256
        temporary = journal.root / f"{sha256(b'test').hexdigest()}.pending"
        temporary.write_bytes(b"uncommitted")
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING"):
            journal.save("test", {"value": 1})
        self.assertEqual(journal.read("test"), {"value": 1})
        self.assertFalse(temporary.exists())

    async def test_concurrent_identical_tool_requests_are_coalesced(self):
        self.mode = "batch"
        result = await self.owner.run(self.request)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(result.tables[0].rows[0].values["value"], 75.0)

    async def test_authenticated_http_submission_survives_request_completion_and_replays_one_result(self):
        run_id = self.request.run_id

        def factory(root):
            self.seed(self.request, root)
            return FoundrySupervisor(ENDPOINT, self.credential, self.bridge, self.tools, SOURCE, root)

        app = create_app(self.root, TOKEN, source=SOURCE, supervisor_factory=factory)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                unauthorized = await client.post("/chat/runs", json={"question": self.request.question},
                                                  headers={"Idempotency-Key": str(run_id)})
                self.assertEqual(unauthorized.status_code, 401)
                headers = {**HEADERS, "Idempotency-Key": str(run_id)}
                submitted = await client.post("/chat/runs", json={"question": self.request.question}, headers=headers)
                self.assertEqual(submitted.status_code, 202, submitted.text)
                deadline = asyncio.get_running_loop().time() + 20
                while True:
                    status = (await client.get(f"/chat/runs/{run_id}", headers=HEADERS)).json()
                    if status["state"] == "completed":
                        break
                    self.assertNotEqual(status["state"], "failed", status)
                    self.assertLess(asyncio.get_running_loop().time(), deadline)
                    await asyncio.sleep(0.02)
                result = await client.get(f"/chat/runs/{run_id}/answer", headers=HEADERS)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["tables"][0]["rows"][0]["values"]["value"], 75.0)
                audit = await client.get(f"/chat/runs/{run_id}/evidence", headers=HEADERS)
                self.assertEqual(audit.json()["receipts"][0]["id"], "reading-1")
                replay = await client.post("/chat/runs", json={"question": self.request.question}, headers=headers)
                self.assertEqual(replay.status_code, 202)
                conflict = await client.post("/chat/runs", json={"question": "Different request."}, headers=headers)
                self.assertEqual(conflict.status_code, 409)
                missing_id = uuid4()
                missing = await client.get(f"/chat/runs/{missing_id}", headers=HEADERS)
                self.assertEqual(missing.status_code, 404)
                self.assertFalse((self.root / "chat" / str(missing_id)).exists())
        self.assertEqual(self.tools.calls, 1)

    async def test_numeric_value_and_unit_cannot_come_from_different_signal_rows(self):
        self.mode = "mixed_rows"
        self.tools.extra_row = True
        with self.assertRaisesRegex(SourceFailure, "different source rows"):
            await self.owner.run(self.request)

    async def test_failure_after_final_agent_response_recovers_without_repeating_agents_or_sources(self):
        save = FileCheckpointStorage.save
        journal = RunJournal(self.root / str(self.request.run_id) / "receipts")

        async def fail_after_answer(storage, checkpoint):
            if journal.read("prepared_answer") is not None:
                raise OSError("Injected final-checkpoint outage")
            return await save(storage, checkpoint)

        with patch.object(FileCheckpointStorage, "save", fail_after_answer):
            with self.assertRaisesRegex(RuntimeError, "checkpoint failed"):
                await self.owner.run(self.request)
        self.assertIsNone(journal.read("answer"))
        self.assertIsNotNone(journal.read("prepared_answer"))
        before = dict(self.calls)
        result = await self.make_owner().run(self.request)
        self.assertEqual(result.tables[0].rows[0].values, {"value": 75.0, "unit": "C"})
        self.assertEqual(self.calls, before)
        self.assertEqual(self.tools.calls, 1)

    async def test_one_schema_correction_precedes_source_execution_and_preserves_real_values(self):
        self.mode = "repair"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING"):
            result = await self.owner.run(self.request)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(result.tables[0].rows[0].values["value"], 75.0)
        payload = json.dumps(self.payloads)
        self.assertIn("invalid_arguments", payload)
        self.assertIn("source_executed", payload)

    async def test_repeated_invalid_arguments_stop_without_touching_sources(self):
        self.mode = "repeat_invalid"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING"):
            with self.assertRaisesRegex(SourceFailure, "validation failed"):
                await self.owner.run(self.request)
        self.assertEqual(self.tools.calls, 0)

    async def test_malformed_work_completion_gets_one_bounded_schema_correction(self):
        self.mode = "repair_review"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING"):
            result = await self.owner.run(self.request)
        review = next(item for item in result.specialists if item.role == "work-order")
        self.assertEqual(review.report["reason"], "Missing scope.")
        self.assertEqual(self.tools.calls, 1)

    async def test_backend_completes_verification_when_chief_omits_the_final_handoff(self):
        self.mode = "skip_verify"
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(self.calls["hydro-supervisor-agent"], 4)
        self.assertIn("Independently verify", json.dumps(self.payloads[-1]))

    async def test_projection_schema_allows_only_actual_source_field_pointer_pairs(self):
        receipt = await self.tools.execute("query_telemetry", {"equipment_id": "TEST_T005"}, self.request)
        self.owner.evidence[receipt.id] = receipt
        schema = self.owner.answer_schema()
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(self.plan())
        for mode in ("invented", "transposed"):
            with self.subTest(mode=mode):
                self.mode = mode
                self.assertTrue(list(validator.iter_errors(self.plan())))
        self.mode = "valid"
        invalid_pointer = self.plan()
        invalid_pointer["tables"][0]["rows"][0]["cells"][0]["source"]["path"] = "/data/rows/0/value"
        self.assertTrue(list(validator.iter_errors(invalid_pointer)))

    async def test_source_argument_repair_does_not_consume_structured_completion_repair(self):
        self.mode = "repair_query_and_report"
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(self.calls["hydro-rca-agent"], 4)

    async def test_batched_handoffs_preserve_dependencies_without_intermediate_chief_calls(self):
        self.mode = "batch_handoffs"
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.calls["hydro-supervisor-agent"], 2)
        self.assertEqual(self.calls["hydro-rca-agent"], 1)
        self.assertEqual(self.calls["hydro-work-order-agent"], 1)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(result.specialists[1].model_round_count, 1)
        self.assertEqual(result.specialists[2].model_round_count, 1)
        audit = RunJournal(self.root / str(self.request.run_id) / "receipts").read("evidence")
        self.assertEqual(audit["supervisor"]["model_round_count"], 2)
        graph = RunJournal(self.root / str(self.request.run_id) / "receipts").read("workflow_graph")
        self.assertEqual(graph["format"], "mermaid")
        self.assertIn("chief_orchestration", graph["definition"])
        self.assertIn("gauge_verification", graph["definition"])
        self.assertIn("grounded_presentation", graph["definition"])
        initial_calls = [payload for payload in self.payloads
                         if payload.get("agent_reference", {}).get("name") == "hydro-qa-agent"]
        contexts = [json.loads(payload["input"][0]["content"][0]["text"]) for payload in initial_calls]
        self.assertEqual([item["role"] for item in contexts[-1]["completed_specialists"]],
                         ["qa", "rca", "work-order"])

    async def test_incomplete_projection_cannot_commit_even_if_its_json_parses(self):
        self.mode = "projection_incomplete"
        with self.assertRaisesRegex(SourceFailure, "unverified execution"):
            await self.owner.run(self.request)
        self.assertIsNone(RunJournal(self.root / str(self.request.run_id) / "receipts").read("answer"))

    async def test_completion_only_correction_preserves_sources_and_does_not_repeat_specialists(self):
        self.mode = "alias_once"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING"):
            result = await self.owner.run(self.request)
        self.assertEqual(result.tables[0].rows[0].values["value"], 75.0)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.calls["hydro-supervisor-agent"], 5)
        self.assertNotIn("agent_reference", self.payloads[-1])
        self.assertIn("source_receipts", json.dumps(self.payloads[-1]))
        audit = RunJournal(self.root / str(self.request.run_id) / "receipts").read("evidence")
        self.assertEqual(audit["answer_projection"]["response_id"], "resp_projection")
        self.assertEqual(audit["answer_projection"]["model"], "test")
        self.assertNotEqual(audit["supervisor"]["response_id"], audit["answer_projection"]["response_id"])


if __name__ == "__main__":
    unittest.main()
