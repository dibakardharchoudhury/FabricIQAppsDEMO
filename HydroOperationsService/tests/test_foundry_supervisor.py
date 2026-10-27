import copy
import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from azure.core.credentials import AccessToken
from azure.identity.aio import AzureCliCredential
from openai import APITimeoutError, AsyncOpenAI, BadRequestError, InternalServerError
from agent_framework import AgentFrameworkException, FileCheckpointStorage
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from hydro_orchestrator.contracts import ResolvedIdentity, ResolvedScope, WorkOrderDraft, utc_now
from hydro_orchestrator.foundry_supervisor import (
    AgentVersion, AnswerPlan, BoundedAIProjectClient, CachedHandoff, ChatRequest, Delegation,
    EquipmentSelection, EvidenceDataset,
    FOUNDRY_SERVICE_CALL_TIMEOUT_SECONDS,
    FoundryAgent, FoundrySupervisor, HistoricalContext, NativeAnswer, NativeBinding, OperatorAnswerContract, RunJournal,
    SpecialistResult, ToolEvidence,
    operation_skill_guidance,
    rca_answer_limitations, rca_reference_examples, safe_exception_signature,
    transient_native_failure,
    canonical_source_arguments,
    _bounded_citation_fragments, _encoded, _native_equipment_ids, _native_count_chart_metadata, _native_markdown_tables,
    _native_markdown_tables_from_payload, initial_equipment_selection, normalize_filter_conjunctions,
    _validated_native_result,
    repair_evidence_row_descendant_paths,
    deterministic_fulfillment_route, governed_datasets, requests_new_work_order_proposal,
    requests_unsent_message, selected_equipment_from_evidence,
    updated_equipment_selection,
    with_production_write_status,
)
from hydro_orchestrator.live_sources import NodeSourceBridge, SourceFailure
from hydro_orchestrator.presentation import client_presentation
from hydro_orchestrator.service import ChatInput, create_app, follow_up_intent
from test_durable_service import SOURCE, HEADERS, TOKEN

ENDPOINT = "https://test.services.ai.azure.com/api/projects/test"


class SourceTools:
    def __init__(self):
        self.calls = 0
        self.failure = False
        self.changed_source = False
        self.extra_row = False
        self.stage = False
        self.quality_snapshot = False
        self.empty_quality_snapshot = False
        self.include_identity = False
        self.presentation = False
        self.station_power = False

    async def catalog(self, request):
        tools = [{"name": "query_telemetry", "parameters": {
            "type": "object", "properties": {"equipment_id": {"type": "string"}},
            "required": ["equipment_id"], "additionalProperties": False,
        }}, {"name": "query_work_backlog", "parameters": {
            "type": "object", "properties": {"group_by": {"type": "string", "enum": ["facility", "equipment"]}},
            "required": ["group_by"], "additionalProperties": False,
        }}]
        if self.quality_snapshot:
            tools.append({"name": "query_signal_quality_snapshot", "parameters": {
                "type": "object", "properties": {
                    "quality": {"type": "string", "enum": ["GOOD", "UNCERTAIN", "BAD"]},
                    "quality_match": {"type": "string", "enum": ["latest", "any"]},
                    "lookback": {"type": "string"},
                    "equipment_type": {"type": "string"},
                }, "required": ["quality", "lookback", "equipment_type"], "additionalProperties": False,
            }})
        if self.station_power:
            tools.append({"name": "query_station_power", "parameters": {
                "type": "object", "properties": {"lookback": {"type": "string"}},
                "required": ["lookback"], "additionalProperties": False,
            }})
        if self.stage:
            tools.append({"name": "propose_work_order", "parameters": {
                "type": "object", "properties": {
                    key: {"type": "string"} for key in ("equipment_id", "title", "description")
                }, "required": ["equipment_id", "title", "description"], "additionalProperties": False,
            }})
        return {"tools": tools, "context": {
            "mode": "test-only-no-production-access",
            "domain_contract_version": 3,
            "datasets": [
                {
                    "key": key, "source": source, "entity": entity,
                    "identityFields": identity,
                    "presentations": (
                        ["grouped_by_equipment_table", "grouped_by_equipment_bar",
                         "grouped_by_facility_table", "grouped_by_facility_bar"]
                        if key == "work_backlog"
                        else ["records_table", "records_bar", "records_line",
                              "grouped_by_equipment_table", "grouped_by_equipment_bar",
                              "grouped_by_facility_table", "grouped_by_facility_bar"]
                        if key in ("station_power", "native_claims")
                        else ["records_table"]
                    ),
                    "evidenceScopes": (
                        ["focus"] if key == "telemetry"
                        else ["population", "focus"] if key in ("work_orders", "native_claims")
                        else ["population"]
                    ),
                    "evidenceAuthorities": ["native"] if key == "native_claims" else ["direct"],
                }
                for key, source, entity, identity in (
                    ("signal_quality", "eventhouse", "OPCUAEvents", ["opcua_node_id", "event_time"]),
                    ("telemetry", "eventhouse", "OPCUAEvents", ["opcua_node_id", "event_time"]),
                    ("station_power", "eventhouse", "OPCUAEvents", ["Station"]),
                    ("work_orders", "sql", "work_orders", ["workOrderNumber"]),
                    ("work_backlog", "derived", "work_orders", ["group_id"]),
                    ("native_claims", "native", "native_claims", ["source_identity"]),
                )
            ],
        }}

    async def execute(self, name, arguments, request):
        self.calls += 1
        if self.failure:
            raise SourceFailure("Injected source outage")
        if name == "query_signal_quality_snapshot":
            quality = arguments["quality"]
            rows = [] if self.empty_quality_snapshot else [{
                "equipment_id": f"TEST_{quality}",
                "instrument_id": f"INST_{quality}",
                "opcua_node_id": f"ns=2;s={quality}.quality",
                "event_time": "2026-10-09T21:00:00Z",
                "value": 1.0,
                "unit": "state",
                "quality": quality,
                "open_work_order_count": 0,
            }]
            return ToolEvidence(
                id=f"quality-{quality.lower()}-{self.calls}", source=request.source, tool=name, arguments=arguments,
                completed_at=utc_now(), result={
                    "rows": rows, "row_count": len(rows),
                    "population": {
                        "inventory_complete": True,
                        "work_inventory_complete": True,
                        "work_coverage_equipment_ids": [row["equipment_id"] for row in rows],
                        "expected_signal_count": 90,
                        "signals_without_readings": (
                            [f"ns=2;s=T{index:03d}.signal" for index in range(1, 91)]
                            if self.empty_quality_snapshot else []
                        ),
                    },
                    "unresolved_nodes": [], "truncated": False,
                },
                row_identities={
                    f"/rows/{index}": {
                        "equipment_id": row["equipment_id"],
                        "opcua_node_id": row["opcua_node_id"],
                    } for index, row in enumerate(rows)
                },
                resolved_equipment_ids=tuple(row["equipment_id"] for row in rows),
                work_coverage_equipment_ids=tuple(row["equipment_id"] for row in rows),
                datasets=(EvidenceDataset(
                    kind="signal_quality", field="rows", authority="direct", scope="population",
                    complete=True, identity_fields=("opcua_node_id", "event_time"),
                ),),
            )
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
                datasets=(EvidenceDataset(
                    kind="work_orders", field="existing_work", authority="direct", scope="focus",
                    complete=True, identity_fields=("workOrderNumber",), role="audit",
                ),),
            )
        if name == "query_station_power":
            return ToolEvidence(
                id="station-power", source=request.source, tool=name, arguments=arguments, completed_at=utc_now(),
                result={
                    "rows": [
                        {"Station": "Foyers", "average_power_MW": 1271.1575, "samples": 1510,
                         "bad_samples": 73, "latest_event_time": "2026-10-11T04:00:00Z"},
                    ],
                    "row_count": 1, "truncated": False, "lookback": arguments["lookback"],
                    "presentation": {
                        "table_field": "rows",
                        "chart": {"kind": "bar", "x_key": "Station", "y_keys": ["average_power_MW"]},
                    },
                },
                column_units={"average_power_MW": "MW"},
                datasets=(EvidenceDataset(
                    kind="station_power", field="rows", authority="direct", scope="population",
                    complete=True, identity_fields=("Station",),
                    limitations=(
                        "Station power is a sample-weighted arithmetic mean; it is not total station output.",
                    ),
                ),),
            )
        if name == "query_work_backlog":
            group_key = "facility_id" if arguments["group_by"] == "facility" else "equipment_id"
            return ToolEvidence(
                id=f"{arguments['group_by']}-backlog", source=request.source, tool=name, arguments=arguments,
                completed_at=utc_now(),
                result={
                    "rows": [{
                        group_key: "TEST_GROUP",
                        "equipment_with_open_work": 1,
                        "open_work_order_count": 2,
                    }],
                    "row_count": 1,
                    "presentation": {
                        "table_field": "rows",
                        "chart": {"kind": "bar", "x_key": group_key, "y_keys": ["open_work_order_count"]},
                    },
                },
                column_units={
                    "equipment_with_open_work": "equipment",
                    "open_work_order_count": "work orders",
                },
                datasets=(EvidenceDataset(
                    kind="work_backlog", field="rows", authority="direct", scope="population",
                    complete=True, identity_fields=(group_key,), group_by=group_key,
                ),),
            )
        rows = [{"equipment_id": "TEST_T005", "value": 75.0, "unit": "C", "quality": "BAD"}]
        if self.extra_row:
            rows.append({"equipment_id": "TEST_T005", "value": 99.0, "unit": "MW", "quality": "GOOD"})
        result = {"rows": rows}
        if self.presentation:
            result["presentation"] = {
                "table_field": "rows",
                "chart": {"kind": "bar", "x_key": "unit", "y_keys": ["value"]},
            }
        return ToolEvidence(
            id="reading-1", source=SOURCE.model_copy(update={"workspace_id": uuid4()}) if self.changed_source else SOURCE,
            tool=name, arguments=arguments, completed_at=utc_now(),
            result=result,
            row_identities={
                f"/rows/{index}": {"equipment_id": row["equipment_id"]}
                for index, row in enumerate(rows)
            } if self.include_identity else {},
            column_units={"value": "C"},
            limitations=("No approved diagnostic limits supplied.",),
            datasets=(EvidenceDataset(
                kind="telemetry", field="rows", authority="direct", scope="focus",
                complete=True, identity_fields=("equipment_id",),
            ),),
        )


class FoundrySupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_framework_flow_preserves_question_and_completed_receipt_without_projection(self):
        self.native_text = "## Source answer\r\n\r\n**T005** belongs to the source facility.\n\n"
        self.native_text += "Original source details and limitations. " * 100
        for mode in ("native_text", "native_text_truncated"):
            with self.subTest(mode=mode):
                self.mode = mode
                self.calls.clear()
                self.payloads.clear()
                request = self.request.model_copy(update={
                    "run_id": uuid4(), "native_sources": ("data-agent",),
                    "question": "How do facilities, turbines and work orders relate?",
                })
                self.seed(request)
                owner = self.make_owner(native_binding=NativeBinding(
                    source=SOURCE, connections={"data-agent": "native-data"},
                ), analysis_model="test-model")
                answer = await owner.run(request)
                self.assertEqual(self.calls, {"hydro-fabric-iq-agent": 1})
                self.assertEqual(len(self.payloads), 1)
                self.assertEqual(self.payloads[0]["input"][0]["content"][0]["text"], request.question)
                self.assertEqual(client_presentation(answer).text, self.native_text)
                self.assertEqual(answer.native_answers[0].text, self.native_text)
                self.assertIsNone(answer.analysis)
                self.assertEqual(self.tools.calls, 0)
                journal = RunJournal(self.root / str(request.run_id) / "receipts")
                restarted = self.make_owner(native_binding=NativeBinding(
                    source=SOURCE, connections={"data-agent": "native-data"},
                ), analysis_model="test-model")
                restarted.root = self.root / f"restart-{mode}"
                self.seed(request, root=restarted.root)
                recovered = RunJournal(restarted.root / str(request.run_id) / "receipts")
                recovered.save("prepared_answer", journal.read("prepared_answer"))
                self.assertEqual(await restarted.run(request), answer)
                self.assertEqual(self.calls, {"hydro-fabric-iq-agent": 1})

    async def test_native_answer_is_returned_byte_for_byte_without_a_second_answer_writer(self):
        text = "## Facilities \u2192 turbines \u2192 work orders\r\n\r\n| ID | Name |\n| --- | --- |\n"
        text += "| T005 | Source **Markdown** |\n" + "Full source details. " * 300
        text += "\n\nSource limitation: no enforced cardinality constraint.\n"
        receipt = ToolEvidence(
            id="native-source", source=SOURCE, tool="fabriciq-data-agent", arguments={},
            completed_at=utc_now(), result={"response": text, "native_table": [{"id": "T005"}]},
        )
        self.owner.evidence = {receipt.id: receipt}
        self.owner.specialists = [SpecialistResult(
            role="fabric-iq", agent_name="hydro-fabric-iq-agent", version="4",
            response_id="foundry-response", duration_ms=10, input_digest="a" * 64,
            text="A rewritten wrapper answer that must never replace the source.",
        )]
        answer = self.owner.native_passthrough_answer(self.request)
        self.assertIsNotNone(answer)
        self.assertEqual(answer.summary, text)
        self.assertEqual(answer.native_answers[0].content_sha256, sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(answer.native_answers[0].evidence_id, receipt.id)
        self.assertEqual(client_presentation(answer).text.encode("utf-8"), text.encode("utf-8"))
        self.assertEqual(answer.tables, ())
        self.assertIsNone(answer.analysis)
        self.assertFalse(answer.production_write_executed)
        self.assertEqual(client_presentation(answer).execution_events[0].responseId, "foundry-response")
        with self.assertRaises(ValidationError):
            NativeAnswer.model_validate({
                **answer.native_answers[0].model_dump(), "text": text + " altered",
            })
        self.owner.evidence["direct-source"] = receipt.model_copy(update={
            "id": "direct-source", "tool": "query_assets", "result": {"rows": []},
        })
        self.assertIsNone(self.owner.native_passthrough_answer(self.request))

    async def test_three_quality_workflows_pass_full_request_admission(self):
        base = "Which turbines had BAD telemetry quality today, and what work is already open for these?"
        for suffix, roles in (
            ("", ()),
            (" Based on this data, tell me which WOs need to be created and help me with the drafts.",
             ("work-order",)),
            (" Based on this data, do a RCA and tell me which WOs need to be created and help me with the drafts.",
             ("rca", "work-order")),
        ):
            with self.subTest(roles=roles):
                question = base + suffix
                intent = await self.bridge.call({"action": "chat_intent", "question": question})
                request = ChatRequest(
                    run_id=uuid4(), source=SOURCE, question=question,
                    deadline=self.request.deadline, **intent,
                )
                self.assertEqual(request.native_sources, ())
                self.assertEqual(request.required_specialists, ("qa", *roles))
                self.assertIn("signal_quality", request.required_evidence_kinds)
                self.assertIn("work_orders", request.required_evidence_kinds)
                native_intent = await self.bridge.call({
                    "action": "chat_intent", "question": question, "native_sources": ["data-agent"],
                })
                native_request = ChatRequest(
                    run_id=uuid4(), source=SOURCE, question=question,
                    deadline=self.request.deadline, **native_intent,
                )
                self.assertEqual(native_request.native_sources, ("data-agent",))
                self.assertEqual(native_request.required_specialists, roles)
                self.assertEqual(native_request.required_evidence_kinds, ("native_claims",))

    async def test_live_version_readback_includes_weather_function_contract(self):
        def read_agent(request):
            name = request.url.path.rsplit("/", 1)[-1]
            version = next(item for item in self.versions.values() if item.name == name)
            return httpx.Response(200, json={
                "name": name,
                "versions": {"latest": {
                    "version": version.version,
                    "definition": {"kind": "prompt", "model": version.model, "tools": list(version.tools)},
                }},
            })

        owner = self.make_owner(native_binding=NativeBinding(
            source=SOURCE, connections={"data-agent": "native-data"},
        ))
        journal = RunJournal(self.root / "live-readback")
        network = httpx.AsyncClient(transport=httpx.MockTransport(read_agent))
        self.addAsyncCleanup(network.aclose)
        with patch("hydro_orchestrator.foundry_supervisor.httpx.AsyncClient", return_value=network):
            versions = await owner.versions(journal)
        self.assertEqual(versions["weather"].name, "hydro-weather-agent")
        self.assertEqual(versions["weather"].tools[0]["name"], "hydro_query")

    async def test_every_governed_direct_dataset_scope_has_a_deterministic_route(self):
        catalog = await self.bridge.call({"action": "source_contracts"})
        datasets = catalog["context"]["datasets"]
        uncovered = {
            (dataset["key"], scope)
            for dataset in datasets
            if "direct" in dataset["evidenceAuthorities"]
            for scope in dataset["evidenceScopes"]
            if deterministic_fulfillment_route(dataset["key"], scope) is None
        }
        self.assertEqual(uncovered, set())

    def test_domain_contract_version_is_identical_at_the_source_boundary(self):
        datasets = [{
            "key": "telemetry", "source": "eventhouse", "entity": "OPCUAEvents",
            "identityFields": ["opcua_node_id", "event_time"],
            "presentations": ["records_table"],
            "evidenceScopes": ["focus"],
            "evidenceAuthorities": ["direct"],
        }]
        self.assertEqual(
            set(governed_datasets({"domain_contract_version": 3, "datasets": datasets})),
            {"telemetry"},
        )
        with self.assertRaisesRegex(SourceFailure, "version"):
            governed_datasets({"domain_contract_version": 1, "datasets": datasets})

    def test_missing_declared_evidence_fails_before_presentation(self):
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "telemetry", "scope": "focus", "authority": "direct",
                "purpose": "facts",
            }],
            "views": [{
                "id": "telemetry", "title": "Telemetry", "kind": "telemetry",
                "scope": "focus", "presentation": "direct_records_table",
            }],
        })
        self.owner.selected_equipment_ids = {"EQUIP_RTI_T005"}
        with self.assertRaisesRegex(SourceFailure, "telemetry focus evidence was not produced"):
            self.owner.contract_answer_plan(self.request)

    def test_empty_selection_omits_focus_view_with_explicit_limitation(self):
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "evidence": "telemetry_focus_direct", "purpose": "investigation",
            }],
            "views": [{
                "id": "telemetry", "title": "Selected turbine telemetry", "kind": "telemetry",
                "scope": "focus", "presentation": "direct_records_table",
            }],
        })

        plan = self.owner.contract_answer_plan(self.request)

        self.assertEqual(plan.tables, ())
        self.assertIn(
            "Selected turbine telemetry was omitted because the population evidence selected no equipment.",
            plan.limitations,
        )

    async def test_missing_focus_catalog_evidence_is_fulfilled_deterministically(self):
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "equipment", "scope": "focus", "authority": "direct",
                "purpose": "identity",
            }],
            "views": [{
                "id": "equipment", "title": "Selected equipment", "kind": "equipment",
                "scope": "focus", "presentation": "direct_records_table",
            }],
        })
        self.owner.selected_equipment_ids = {"EQUIP_RTI_T014"}
        arguments = {
            "entity": "equipment",
            "where": [{
                "column": "equipment_id", "op": "in", "value": ["EQUIP_RTI_T014"],
            }],
        }
        expected = ToolEvidence(
            id="equipment-focus", source=SOURCE, tool="query_assets", arguments=arguments,
            completed_at=utc_now(),
            result={"rows": [{"equipment_id": "EQUIP_RTI_T014", "tag": "T014"}], "truncated": False},
            datasets=(EvidenceDataset(
                kind="equipment", field="rows", authority="direct", scope="focus",
                complete=True, identity_fields=("equipment_id",),
            ),),
        )
        self.tools.execute = AsyncMock(return_value=expected)
        journal = RunJournal(self.root / str(self.request.run_id) / "receipts")
        journal.save("tool_catalog", await self.bridge.call({"action": "source_contracts"}))

        await self.owner.fulfill_catalog_requirements(self.request, journal)

        self.tools.execute.assert_awaited_once_with("query_assets", arguments, self.request)
        self.assertEqual(set(self.owner.evidence), {"equipment-focus"})
        self.owner.contract_answer_plan(self.request)

    async def test_missing_facility_population_is_fulfilled_deterministically(self):
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "facilities", "scope": "population", "authority": "direct",
                "purpose": "identity",
            }],
            "views": [{
                "id": "facilities", "title": "Facilities", "kind": "facilities",
                "scope": "population", "presentation": "direct_records_table",
            }],
        })
        arguments = {"entity": "facilities"}
        expected = ToolEvidence(
            id="facilities-population", source=SOURCE, tool="query_assets", arguments=arguments,
            completed_at=utc_now(),
            result={"rows": [{"facility_id": "FAC-01", "facility_name": "Foyers"}], "truncated": False},
            datasets=(EvidenceDataset(
                kind="facilities", field="rows", authority="direct", scope="population",
                complete=True, identity_fields=("facility_id",),
            ),),
        )
        self.tools.execute = AsyncMock(return_value=expected)
        journal = RunJournal(self.root / str(self.request.run_id) / "receipts")
        journal.save("tool_catalog", await self.bridge.call({"action": "source_contracts"}))

        await self.owner.fulfill_catalog_requirements(self.request, journal)

        self.tools.execute.assert_awaited_once_with("query_assets", arguments, self.request)
        self.assertEqual(set(self.owner.evidence), {"facilities-population"})
        self.owner.contract_answer_plan(self.request)

    async def test_missing_focus_telemetry_is_fulfilled_from_verified_signal_identity(self):
        request = self.request.model_copy(update={
            "question": "Inspect the selected turbine latest raw telemetry within the last six hours.",
        })
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "telemetry", "scope": "focus", "authority": "direct",
                "purpose": "facts",
            }],
            "views": [{
                "id": "telemetry", "title": "Selected telemetry", "kind": "telemetry",
                "scope": "focus", "presentation": "direct_records_table",
            }],
        })
        self.owner.selected_equipment_ids = {"EQUIP_RTI_T014"}
        instrument = ToolEvidence(
            id="instrument-focus", source=SOURCE, tool="query_assets",
            arguments={"entity": "instruments"}, completed_at=utc_now(),
            result={"rows": [{
                "equipment_id": "EQUIP_RTI_T014",
                "opcua_node_id": "ns=2;s=T014.turbine_temp",
            }], "truncated": False},
            datasets=(EvidenceDataset(
                kind="instruments", field="rows", authority="direct", scope="focus",
                complete=True, identity_fields=("opcua_node_id",),
            ),),
        )
        self.owner.evidence = {instrument.id: instrument}
        arguments = {
            "lookback": "6h", "aggregation": "latest",
            "opcua_node_ids": ["ns=2;s=T014.turbine_temp"],
        }
        expected = ToolEvidence(
            id="telemetry-focus", source=SOURCE, tool="query_telemetry",
            arguments=arguments, completed_at=utc_now(),
            result={"rows": [{
                "opcua_node_id": "ns=2;s=T014.turbine_temp",
                "event_time": "2026-10-13T16:00:00Z",
                "value": 74.5,
                "quality": "GOOD",
            }], "truncated": False},
            datasets=(EvidenceDataset(
                kind="telemetry", field="rows", authority="direct", scope="focus",
                complete=True, identity_fields=("opcua_node_id", "event_time"),
            ),),
        )
        self.tools.execute = AsyncMock(return_value=expected)
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("tool_catalog", await self.bridge.call({"action": "source_contracts"}))

        await self.owner.fulfill_catalog_requirements(request, journal)

        self.tools.execute.assert_awaited_once_with("query_telemetry", arguments, request)
        self.assertEqual(set(self.owner.evidence), {"instrument-focus", "telemetry-focus"})
        self.owner.contract_answer_plan(request)

    async def test_requested_chart_series_satisfies_focus_telemetry_without_a_second_all_signal_read(self):
        request = self.request.model_copy(update={
            "question": "Chart power output for the selected turbine for the past 2 hours.",
            "charts_requested": True,
        })
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "telemetry", "scope": "focus", "authority": "direct", "purpose": "facts",
            }],
            "views": [{
                "id": "telemetry", "title": "Selected telemetry", "kind": "telemetry",
                "scope": "focus", "presentation": "direct_records_line",
            }],
        })
        self.owner.selected_equipment_ids = {"EQUIP_RTI_T014"}
        series = ToolEvidence(
            id="telemetry-series", source=SOURCE, tool="query_telemetry",
            arguments={"lookback": "2h", "aggregation": "avg", "opcua_node_ids": ["ns=2;s=T014.power_output"]},
            completed_at=utc_now(),
            result={"rows": [{
                "opcua_node_id": "ns=2;s=T014.power_output", "event_time": "2026-10-13T16:00:00Z",
                "value": 12.5, "unit": "MW",
            }], "truncated": False},
            column_units={"value": "MW"},
            datasets=(EvidenceDataset(
                kind="telemetry", field="rows", authority="direct", scope="focus",
                complete=True, identity_fields=("opcua_node_id", "event_time"),
            ),),
        )
        self.owner.evidence = {series.id: series}
        self.tools.execute = AsyncMock()
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("tool_catalog", await self.bridge.call({"action": "source_contracts"}))

        await self.owner.fulfill_catalog_requirements(request, journal)

        self.tools.execute.assert_not_awaited()
        self.assertEqual(set(self.owner.evidence), {"telemetry-series"})
        plan = self.owner.contract_answer_plan(request)
        self.assertEqual(
            [(chart.kind, chart.x_key, chart.y_keys) for chart in plan.charts],
            [("line", "event_time", ("value",))],
        )

    async def test_missing_analytical_population_is_fulfilled_from_central_execution_plan(self):
        request = self.request.model_copy(update={
            "question": "Which turbines have latest signal quality BAD today?",
        })
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "signal_quality", "scope": "population", "authority": "direct",
                "purpose": "facts",
            }],
            "views": [{
                "id": "quality", "title": "Signal quality", "kind": "signal_quality",
                "scope": "population", "presentation": "direct_records_table",
            }],
        })
        arguments = {"quality": "BAD", "lookback": "today", "equipment_type": "turbine"}
        expected = ToolEvidence(
            id="quality-population", source=SOURCE, tool="query_signal_quality_snapshot",
            arguments=arguments, completed_at=utc_now(),
            result={"rows": [], "row_count": 0, "truncated": False},
            datasets=(EvidenceDataset(
                kind="signal_quality", field="rows", authority="direct", scope="population",
                complete=True, identity_fields=("opcua_node_id", "event_time"),
            ),),
        )
        self.tools.execute = AsyncMock(return_value=expected)
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("tool_catalog", await self.bridge.call({"action": "source_contracts"}))

        await self.owner.fulfill_catalog_requirements(request, journal)

        self.tools.execute.assert_awaited_once_with("query_signal_quality_snapshot", arguments, request)
        self.assertEqual(set(self.owner.evidence), {"quality-population"})
        self.owner.contract_answer_plan(request)

    def test_analysis_preserves_complete_bounded_fleet_rows_and_source_receipt(self):
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "signal_quality", "scope": "population", "authority": "direct",
                "purpose": "verification",
            }],
            "views": [{
                "id": "quality-population", "title": "Signal quality",
                "kind": "signal_quality", "scope": "population", "presentation": "direct_records_table",
            }],
        })
        rows = [{
            "equipment_id": f"EQUIP_RTI_T{index // 6 + 1:03}",
            "opcua_node_id": f"node-{index}", "quality": "BAD", "value": 42.0,
        } for index in range(90)]
        receipt = ToolEvidence(
            id="complete-quality", source=SOURCE, tool="query_signal_quality_snapshot",
            arguments={}, completed_at=utc_now(), result={"rows": rows, "truncated": False},
            datasets=(EvidenceDataset(
                kind="signal_quality", field="rows", authority="direct", scope="population",
                complete=True, identity_fields=("opcua_node_id",),
            ),),
        )
        self.owner.evidence = {receipt.id: receipt}
        plan = self.owner.contract_answer_plan(self.request)
        answer = self.owner.answer(self.request, plan, use_projected_summary=True)
        context = self.owner.analysis_context(self.request, answer)
        datasets = context["datasets"]
        assert isinstance(datasets, list)
        self.assertEqual(len(datasets), 1)
        dataset = datasets[0]
        self.assertEqual(dataset["row_count"], 90)
        self.assertFalse(dataset["rows_truncated"])
        self.assertEqual(len(dataset["rows"]), 90)
        self.assertCountEqual(
            [row["opcua_node_id"] for row in dataset["rows"]],
            [row["opcua_node_id"] for row in rows],
        )
        self.assertEqual(self.owner.evidence[receipt.id].result["rows"], rows)

    def test_reconciliation_compiles_direct_and_native_rows_without_inferred_matches(self):
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [
                {
                    "kind": "signal_quality", "scope": "population", "authority": "direct",
                    "purpose": "verification",
                },
                {
                    "kind": "native_claims", "scope": "population", "authority": "native",
                    "purpose": "facts",
                },
            ],
            "views": [{
                "id": "quality-reconciliation", "title": "Quality reconciliation",
                "kind": "signal_quality", "scope": "population",
                "presentation": "reconciliation_table",
            }],
        })
        direct = ToolEvidence(
            id="direct-quality", source=SOURCE, tool="query_signal_quality_snapshot",
            arguments={}, completed_at=utc_now(),
            result={"rows": [{
                "equipment_id": "EQUIP_RTI_T005", "quality": "BAD", "details": "brief",
            }]},
            datasets=(EvidenceDataset(
                kind="signal_quality", field="rows", authority="direct", scope="population",
                complete=True, identity_fields=("equipment_id",),
            ),),
        )
        native = ToolEvidence(
            id="native-quality", source=SOURCE, tool="fabriciq-data-agent",
            arguments={}, completed_at=utc_now(),
            result={"claims": [{
                "equipmentId": "EQUIP_RTI_T005", "quality": "BAD", "details": "x" * 2401,
            }]},
            datasets=(EvidenceDataset(
                kind="native_claims", field="claims", authority="native", scope="population",
                complete=True, identity_fields=("equipmentId",),
            ),),
        )
        self.owner.evidence = {direct.id: direct, native.id: native}
        plan = self.owner.contract_answer_plan(self.request)
        self.assertEqual(len(plan.tables), 1)
        self.assertEqual(len(plan.tables[0].rows), 2)
        self.assertIn("_source_authority", {column.key for column in plan.tables[0].columns})
        self.assertNotIn("details", {column.key for column in plan.tables[0].columns})
        self.assertTrue(any("does not infer matches" in item for item in plan.limitations))
        self.assertIn("reconciliation:quality-reconciliation", self.owner.evidence)
        answer = self.owner.answer(self.request, plan, use_projected_summary=True)
        self.assertEqual(len(answer.tables[0].rows), 2)
        self.assertEqual(self.owner.contract_answer_plan(self.request), plan)

    def test_nested_only_native_rows_remain_in_audit_without_empty_answer_rows(self):
        self.owner.answer_contract = OperatorAnswerContract.model_validate({
            "version": 3,
            "requirements": [{
                "kind": "native_claims", "scope": "focus", "authority": "native",
                "purpose": "identity",
            }],
            "views": [{
                "id": "ontology-context", "title": "Ontology context",
                "kind": "native_claims", "scope": "focus",
                "presentation": "native_records_table",
            }],
        })
        receipt = ToolEvidence(
            id="native-ontology", source=SOURCE, tool="fabriciq-ontology",
            arguments={}, completed_at=utc_now(),
            result={"claims": [
                {"identity": {"equipmentId": "EQUIP_RTI_T005"}},
                {"equipmentId": "EQUIP_RTI_T005", "facilityId": "FAC-01"},
            ]},
            datasets=(EvidenceDataset(
                kind="native_claims", field="claims", authority="native", scope="focus",
                complete=True, identity_fields=("equipmentId",),
            ),),
        )
        self.owner.evidence = {receipt.id: receipt}

        plan = self.owner.contract_answer_plan(self.request)

        self.assertEqual(len(plan.tables[0].rows), 1)
        self.assertEqual(plan.tables[0].rows[0].cells[0].key, "equipmentId")
        self.assertTrue(any("nested-only source record" in item for item in plan.limitations))

    def test_empty_verification_does_not_erase_a_prior_grounded_selection(self):
        selected = {"EQUIP_RTI_T002", "EQUIP_RTI_T008"}
        self.assertEqual(
            updated_equipment_selection(selected, set(), requires_selection=False),
            selected,
        )
        self.assertEqual(
            updated_equipment_selection(selected, {"EQUIP_RTI_T002"}, requires_selection=False),
            {"EQUIP_RTI_T002"},
        )
        self.assertEqual(
            updated_equipment_selection(selected, {"EQUIP_RTI_T010"}, requires_selection=True),
            {"EQUIP_RTI_T002", "EQUIP_RTI_T008", "EQUIP_RTI_T010"},
        )

    def test_equipment_selection_is_deterministic_across_quality_and_work_metrics(self):
        quality = ToolEvidence(
            id="quality", source=SOURCE, tool="query_signal_quality_snapshot", arguments={},
            completed_at=utc_now(), result={"rows": [
                {"equipment_id": "EQUIP_RTI_T004", "opcua_node_id": "n4", "quality": "BAD"},
                {"equipment_id": "EQUIP_RTI_T003", "opcua_node_id": "n3", "quality": "BAD"},
                {"equipment_id": "EQUIP_RTI_T003", "opcua_node_id": "n3", "quality": "BAD"},
            ]},
        )
        selected = selected_equipment_from_evidence(
            EquipmentSelection(
                metric="signal_quality_count", quality="BAD", tie_break="equipment_id_asc",
            ),
            (quality,),
        )
        self.assertEqual(selected, {"EQUIP_RTI_T003"})

        backlog = ToolEvidence(
            id="backlog", source=SOURCE, tool="query_work_backlog", arguments={"group_by": "equipment"},
            completed_at=utc_now(), result={"rows": [
                {"equipment_id": "EQUIP_RTI_T008", "open_work_order_count": 2},
                {"equipment_id": "EQUIP_RTI_T003", "open_work_order_count": 2},
            ], "open_work_orders": []},
        )
        selected = selected_equipment_from_evidence(
            EquipmentSelection(metric="open_work_order_count", tie_break="equipment_id_asc"),
            (backlog,),
        )
        self.assertEqual(selected, {"EQUIP_RTI_T003"})

    def test_native_count_table_gets_bounded_chart_metadata(self):
        units, presentation = _native_count_chart_metadata({
            "native_backlog_by_equipment": [
                {"equipmentId": "EQUIP_RTI_T002", "Open orders": "2"},
                {"equipmentId": "EQUIP_RTI_T008", "Open orders": "2"},
            ],
        })
        self.assertEqual(units, {"Open orders": "count"})
        self.assertEqual(presentation, {
            "table_field": "native_backlog_by_equipment",
            "chart": {"kind": "bar", "x_key": "equipmentId", "y_keys": ["Open orders"]},
        })
        self.assertEqual(_native_count_chart_metadata({
            "native_readings": [{"equipmentId": "EQUIP_RTI_T002", "temperature": "75.0"}],
        }), ({}, None))

    def test_redundant_filter_conjunctions_are_normalized_without_changing_conditions(self):
        conditions = [
            {"column": "status", "op": "neq", "value": "Completed"},
            {"column": "status", "op": "neq", "value": "Cancelled"},
        ]
        self.assertEqual(
            normalize_filter_conjunctions({
                "entity": "work_orders",
                "where": [conditions[0], " AND ", conditions[1]],
            }),
            {"entity": "work_orders", "where": conditions},
        )
        self.assertEqual(
            normalize_filter_conjunctions({"where": [[conditions[0]], conditions[1]]}),
            {"where": conditions},
        )
        self.assertEqual(
            normalize_filter_conjunctions({"where": conditions[0]}),
            {"where": [conditions[0]]},
        )
        invalid = {"where": [conditions[0], "or", conditions[1]]}
        self.assertIs(normalize_filter_conjunctions(invalid), invalid)
        invalid_null = {"where": [conditions[0], None, conditions[1]]}
        self.assertIs(normalize_filter_conjunctions(invalid_null), invalid_null)

    def test_all_roles_receive_nonempty_bounded_operation_skills(self):
        expected = {
            "supervisor": {"source-reconciliation", "grounded-presentation"},
            "qa": {"condition-triage", "source-reconciliation", "maintenance-planning"},
            "rca": {"root-cause-evidence", "maintenance-planning"},
            "work-order": {"maintenance-planning", "work-order-review"},
            "fabric-iq": {"source-reconciliation"},
        }
        for role, names in expected.items():
            guidance = operation_skill_guidance(role)
            self.assertEqual({item["name"] for item in guidance}, names)
            self.assertTrue(all(item["instructions"].startswith("---\nname:") for item in guidance))
            self.assertTrue(all("# " in item["instructions"] for item in guidance))

    def test_rca_reference_examples_expose_only_result_relative_paths(self):
        receipt = ToolEvidence(
            id="reading-1", source=SOURCE, tool="query_telemetry", arguments={},
            completed_at=utc_now(), result={
                "rows": [{"equipment_id": "TEST_T005", "quality": "BAD"}],
                "row_count": 1,
            }, limitations=("Not part of the result object.",),
        )
        self.assertEqual(
            rca_reference_examples({"reading-1": receipt}),
            ("reading-1:/rows/0", "reading-1:/row_count"),
        )
        self.assertNotIn("limitations", " ".join(rca_reference_examples({"reading-1": receipt})))

    def test_large_native_response_provides_bounded_source_preserving_rca_paths(self):
        response = "\n\n".join(f"Ontology fact {index}: " + "x" * 500 for index in range(12))
        fragments = _bounded_citation_fragments(response)
        self.assertEqual("".join(response.split()), "".join(fragments).replace(" ", "").replace("\n", ""))
        self.assertTrue(fragments)
        self.assertTrue(all(len(fragment) <= 1800 for fragment in fragments))
        receipt = ToolEvidence(
            id="native-1", source=SOURCE, tool="fabriciq-ontology", arguments={},
            completed_at=utc_now(),
            result={"response": response, "citation_fragments": list(fragments)},
        )
        examples = rca_reference_examples({"native-1": receipt})
        self.assertEqual(
            examples,
            tuple(f"native-1:/citation_fragments/{index}" for index in range(len(fragments))),
        )
        self.assertNotIn("native-1:/response", examples)


    def test_native_markdown_tables_are_extracted_from_document_content(self):
        payload = {
            "documents": [{
                "id": "citation-1",
                "title": "hydro-fabric-ontology",
                "url": "https://example.test/ontology",
                "content": """### Equipment

| equipment_id | facility_id | tag |
|---|---|---|
| EQUIP_RTI_T005 | FACILITY_RTI_001 | T005 |
""",
            }],
        }
        self.assertEqual(_native_markdown_tables_from_payload(payload), {
            "native_equipment": [{
                "equipment_id": "EQUIP_RTI_T005",
                "facility_id": "FACILITY_RTI_001",
                "tag": "T005",
            }],
        })

    def test_production_write_status_is_not_duplicated(self):
        summary = "An editable draft was staged; no production write was executed."
        self.assertEqual(with_production_write_status(summary, True), summary)
        self.assertEqual(
            with_production_write_status("An editable draft was staged.", True),
            "An editable draft was staged. No production writes executed.",
        )
        self.assertEqual(with_production_write_status("Read-only result.", False), "Read-only result.")



    def test_rca_answer_limitations_state_an_assessment_not_a_confirmation_and_list_what_would_confirm_it(self):
        specialist = SpecialistResult(
            role="rca", agent_name="hydro-rca-agent", version="12", response_id="response-1",
            duration_ms=10, model_round_count=1, input_digest="0" * 64, text="", report={
                "observations": [{"evidence_id": "reading-1", "path": "/rows/0"}],
                "hypotheses": [
                    {"category": "sensor_or_ingestion", "supporting": [], "contradicting": [],
                     "missing": ["independent_measurement"]},
                    {"category": "equipment_condition", "supporting": [], "contradicting": [],
                     "missing": ["inspection_evidence", "independent_measurement"]},
                ],
            },
        )
        limitations = rca_answer_limitations([specialist])
        self.assertIn("none is confirmed by a physical measurement", limitations[0])
        self.assertNotIn("undetermined", limitations[0])
        self.assertIn("To confirm a cause you would also need", limitations[1])
        self.assertIn("an independently obtained measurement", limitations[1])
        self.assertIn("qualified inspection evidence", limitations[1])

    def test_exception_signature_exposes_only_bounded_diagnostic_fields(self):
        class ProviderError(Exception):
            status_code = 400
            code = "invalid_request"
            type = "bad_request"
            param = "tools"

        provider = ProviderError("private provider response")
        wrapped = RuntimeError("private wrapper")
        wrapped.__cause__ = provider
        signature = safe_exception_signature(wrapped)
        self.assertEqual(
            signature,
            "RuntimeError <- ProviderError,status_code=400,code=invalid_request,type=bad_request,param=tools",
        )
        self.assertNotIn("private", signature)

    def test_fixer_without_rca_reuses_the_complete_native_evidence_bundle(self):
        receipt = ToolEvidence(
            id="native-quality-work", source=SOURCE, tool="fabriciq-data-agent", arguments={},
            completed_at=utc_now(),
            result={"affected_signals": [{"equipment_id": "TEST_T005", "quality": "BAD"}]},
            resolved_equipment_ids=("TEST_T005",),
            limitations=("Native receipt does not attest combined-source provenance.",),
        )
        self.owner.evidence[receipt.id] = receipt
        self.assertEqual(self.owner.model_evidence("work-order"), [receipt.receipt()])
        self.assertEqual(receipt.work_coverage_equipment_ids, ())

    def test_fixer_without_rca_preserves_selected_direct_evidence_and_population_without_mutating_audit(self):
        rows = [{"equipment_id": "TEST_T005", "quality": "BAD"},
                {"equipment_id": "TEST_T999", "quality": "GOOD"}]
        receipt = ToolEvidence(
            id="direct-quality-work", source=SOURCE, tool="query_signal_quality_snapshot", arguments={},
            completed_at=utc_now(), result={"rows": rows, "row_count": 2,
                "population": {"inventory_complete": True, "work_inventory_complete": True}},
            row_identities={f"/rows/{index}": {"equipment_id": row["equipment_id"]}
                            for index, row in enumerate(rows)},
            resolved_equipment_ids=("TEST_T005", "TEST_T999"),
            work_coverage_equipment_ids=("TEST_T005", "TEST_T999"),
        )
        self.owner.evidence[receipt.id] = receipt
        self.owner.selected_equipment_ids = {"TEST_T005"}
        projected = self.owner.model_evidence("work-order")
        self.assertEqual(projected[0]["result"]["rows"], [rows[0]])
        self.assertEqual(projected[0]["result"]["population"], receipt.result["population"])
        self.assertEqual(projected[0]["work_coverage_equipment_ids"], ("TEST_T005",))
        self.assertEqual(receipt.result["rows"], rows)
        self.owner.selected_equipment_ids.clear()
        self.assertEqual(self.owner.model_evidence("work-order"), [receipt.receipt()])

    def test_model_transport_retries_are_bounded_per_request_and_can_be_disabled_for_native(self):
        for retries in (0, 1):
            with self.subTest(retries=retries), patch(
                "azure.ai.projects.aio.AIProjectClient.get_openai_client",
            ) as factory:
                client = BoundedAIProjectClient(
                    ENDPOINT, self.credential, transport_retries=retries,
                )
                client.get_openai_client(agent_name="hydro-work-order-agent")
                self.assertEqual(factory.call_args.kwargs["max_retries"], retries)
                self.assertEqual(factory.call_args.kwargs["timeout"], 120.0)

    def test_fixer_after_rca_retains_native_table_references_without_fabricating_sql_coverage(self):
        receipt = ToolEvidence(
            id="native-quality-work", source=SOURCE, tool="fabriciq-data-agent", arguments={},
            completed_at=utc_now(),
            result={"affected_signals": [{"equipment_id": "TEST_T005", "quality": "BAD"}]},
            resolved_equipment_ids=("TEST_T005",),
        )
        self.owner.evidence[receipt.id] = receipt
        self.owner.specialists.append(SpecialistResult(
            role="rca", agent_name="hydro-rca-agent", version="12", response_id="response-1",
            duration_ms=10, model_round_count=1, input_digest="0" * 64, text="",
            report={"observations": [{"evidence_id": receipt.id, "path": "/affected_signals/0"}]},
        ))
        projected = self.owner.model_evidence("work-order")
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["result"], receipt.result)
        self.assertEqual(projected[0]["work_coverage_equipment_ids"], ())
        self.owner.selected_equipment_ids = {"TEST_T005"}
        self.assertEqual(self.owner.root_cause_scope(
            ChatRequest(run_id=uuid4(), source=SOURCE, question="Investigate the reported BAD quality.",
                        requested_at=utc_now(), deadline=utc_now() + timedelta(minutes=5)),
            RunJournal(self.root / "native-scope"),
        )["equipment_ids"], ["TEST_T005"])

    def test_fixer_model_context_projects_referenced_equipment_without_mutating_audit_evidence(self):
        rows = [
            {"equipment_id": "TEST_T005", "quality": "BAD", "payload": "x" * 1000},
            {"equipment_id": "TEST_T999", "quality": "GOOD", "payload": "y" * 1000},
        ]
        receipt = ToolEvidence(
            id="fleet-1", source=SOURCE, tool="query_signal_quality_snapshot", arguments={},
            completed_at=utc_now(), result={"rows": rows, "row_count": 2},
            row_identities={
                "/rows/0": {"equipment_id": "TEST_T005"},
                "/rows/1": {"equipment_id": "TEST_T999"},
            },
            resolved_equipment_ids=("TEST_T005", "TEST_T999"),
            work_coverage_equipment_ids=("TEST_T005", "TEST_T999"),
        )
        self.owner.evidence[receipt.id] = receipt
        self.owner.specialists.append(SpecialistResult(
            role="rca", agent_name="hydro-rca-agent", version="12", response_id="response-1",
            duration_ms=10, model_round_count=1, input_digest="0" * 64, text="large narrative " * 1000,
            report={
                "observations": [{"evidence_id": "fleet-1", "path": "/rows/0"}],
                "hypotheses": [],
            },
        ))

        projected = self.owner.model_evidence("work-order")
        specialists = self.owner.model_specialists("work-order")
        verification_evidence = self.owner.model_evidence("qa", targeted=True)
        verification_specialists = self.owner.model_specialists("qa", compact=True)

        self.assertEqual(projected[0]["result"]["rows"], [rows[0]])
        self.assertEqual(projected[0]["result"]["projected_row_count"], 1)
        self.assertEqual(verification_evidence, projected)
        self.assertEqual(self.owner.evidence["fleet-1"].result["rows"], rows)
        self.assertNotIn("text", specialists[0])
        self.assertEqual(specialists[0]["report"]["observations"][0]["path"], "/rows/0")
        self.assertEqual(
            verification_specialists[0]["report"],
            {"observations": [{"evidence_id": "fleet-1", "path": "/rows/0"}]},
        )

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = NodeSourceBridge()
        contracts = await self.bridge.call({"action": "agent_contracts"})
        self.versions = {
            role: AgentVersion(name=name, version="12", model="test", tools=tuple(contracts["tools"].get(role, [
                {"type": "fabric_iq_preview", "server_label": "fabriciq-data-agent", "project_connection_id": "native-data"},
            ]))) for role, name in contracts["names"].items()
        }
        self.versions["supervisor"] = AgentVersion(
            name="hydro-orchestrator", version="1", model="test", tools=(),
        )
        self.request = ChatRequest(
            run_id=uuid4(), source=SOURCE,
            question=(
                "Read telemetry, investigate it, "
                "prepare an editable inspection draft, and finally "
                "independently verify the result. Keep this read-only."
            ),
            deadline=utc_now() + timedelta(minutes=3),
            permitted_evidence_kinds=(
                "facilities", "equipment", "instruments", "telemetry", "signal_quality", "temperature",
                "station_power", "work_orders", "work_backlog", "inspections", "notifications",
                "spare_parts", "asset_models", "native_claims",
            ),
        )
        self.credential = AsyncMock(spec=AzureCliCredential)
        self.credential.get_token.return_value = AccessToken("test-not-a-real-token", 9999999999)
        self.tools = SourceTools()
        self.calls = {}
        self.payloads = []
        self.mode = "valid"
        self.projection_chart_indexes = None
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

    async def test_semantic_reader_runs_without_any_specialist_model_handoff(self):
        from test_semantic_planning import decision
        self.tools.quality_snapshot = True
        request = self.request.model_copy(update={"question": "Show currently unreliable signals."})
        raw = decision(kind="signal_quality")
        raw["plan"]["steps"][0]["reads"] = [{
            "tool": "query_signal_quality_snapshot",
            "arguments_json": json.dumps({"quality": "BAD", "lookback": "6h", "equipment_type": "turbine"}),
            "evidence_kind": "signal_quality",
        }]
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("semantic_decision", {
            "decision": raw, "response_id": "resp_semantic_test", "duration_ms": 12,
        })
        owner = self.make_owner(semantic_planning=True, analysis_model="test-model")
        owner.analysis_for = AsyncMock(return_value=None)
        answer = await owner.run(request)
        self.assertTrue(answer.tables)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(self.calls, {})
        self.assertEqual(answer.specialists, ())
        evidence = journal.read("evidence")
        self.assertEqual(evidence["supervisor"]["response_id"], "resp_semantic_test")
        self.assertEqual(evidence["supervisor"]["model_round_count"], 1)
        self.assertTrue(evidence["supervisor"]["output"]["model_invoked"])
        self.assertIn("specialist_handoff_1", journal.read("workflow_graph")["definition"])
        self.assertNotIn("specialist_handoff_2", journal.read("workflow_graph")["definition"])

    async def test_parallel_semantic_reads_cancel_siblings_before_failed_run_returns(self):
        from test_semantic_planning import decision
        self.tools.quality_snapshot = True
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def execute(name, arguments, request):
            if arguments["quality"] == "BAD":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            await started.wait()
            raise SourceFailure("Injected independent reader failure")

        self.tools.execute = execute
        raw = decision(kind="signal_quality")
        raw["plan"]["steps"][0]["reads"] = [{
            "tool": "query_signal_quality_snapshot",
            "arguments_json": json.dumps({"quality": quality, "lookback": "6h", "equipment_type": "turbine"}),
            "evidence_kind": "signal_quality",
        } for quality in ("BAD", "UNCERTAIN")]
        journal = RunJournal(self.root / str(self.request.run_id) / "receipts")
        journal.save("semantic_decision", {
            "decision": raw, "response_id": "resp_parallel_test", "duration_ms": 12,
        })
        owner = self.make_owner(semantic_planning=True, analysis_model="test-model")
        with self.assertRaisesRegex(SourceFailure, "Injected independent reader failure"):
            await owner.run(self.request)
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(journal.read("answer"))

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
            invalid_once = self.mode == "projection_invalid_once"
            count_only_once = self.mode == "projection_count_only_once"
            if self.mode in ("alias_once", "projection_incomplete"):
                self.mode = "valid"
            projection_plan = self.plan()
            projection_plan["summary"] = (
                "The evidence does not establish a physical fault. The returned measurements require engineering "
                "limits and corroborating evidence before a condition conclusion."
            )
            projection_plan["tables"][0]["columns"] = [
                {"key": "equipment_id", "label": "Equipment", "kind": "text"},
                *projection_plan["tables"][0]["columns"],
                {"key": "quality", "label": "Quality", "kind": "text"},
            ]
            projection_plan["tables"][0]["rows"][0]["cells"] = [
                {
                    "key": "equipment_id",
                    "source": {"evidence_id": "reading-1", "path": "/rows/0/equipment_id"},
                },
                *projection_plan["tables"][0]["rows"][0]["cells"],
                {
                    "key": "quality",
                    "source": {"evidence_id": "reading-1", "path": "/rows/0/quality"},
                },
            ]
            projection_output = {
                "summary": projection_plan["summary"],
                "chart_indexes": (
                    self.projection_chart_indexes
                    if self.projection_chart_indexes is not None
                    else [0]
                    if json.loads(payload["input"][0]["content"][0]["text"])["charts_requested"]
                    else []
                ),
            }
            if invalid_once:
                self.mode = "valid"
                projection_output.pop("chart_indexes")
            elif count_only_once:
                self.mode = "valid"
                projection_output["summary"] = "Returned 1 source row in 1 table."
            return httpx.Response(200, json={
                "id": "resp_projection", "object": "response", "created_at": 1,
                "model": "test", "status": "incomplete" if incomplete else "completed",
                "incomplete_details": {"reason": "max_output_tokens"} if incomplete else None,
                "output": [self.message(json.dumps(projection_output))],
                "parallel_tool_calls": False,
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            })
        reference = payload["agent_reference"]
        self.assertEqual(reference["version"], "12")
        self.assertNotIn("tools", payload)
        self.assertNotIn("instructions", payload)
        self.assertNotIn("text", payload)
        if reference["name"] == "hydro-fabric-iq-agent":
            native_input = payload["input"][0]["content"][0]["text"]
            self.assertNotIn("trusted_operation_skills", native_input)
            self.assertNotIn("permitted_source_catalog", native_input)
            self.assertNotIn('"operator_question"', native_input)
            self.assertNotIn("As-of UTC:", native_input)
        else:
            self.assertIn("trusted_operation_skills", json.dumps(payload["input"]))
        self.assertFalse(payload["store"])
        name = reference["name"]
        self.assertEqual(payload["max_output_tokens"], 4096)
        round_number = self.calls.get(name, 0)
        self.calls[name] = round_number + 1
        if ((self.mode == "work_order_rate_limit_once" and name == "hydro-work-order-agent" and round_number == 0)
                or (self.mode == "qa_rate_limit_after_source" and name == "hydro-qa-agent" and round_number == 1)
                or self.mode == "rate_limit_always"):
            return httpx.Response(429, headers={"retry-after": "1"}, json={
                "error": {"message": "Rate limited.", "code": "rate_limit_exceeded", "type": "error"},
            })
        if self.mode == "native_timeout_once" and name == "hydro-fabric-iq-agent" and round_number == 0:
            raise APITimeoutError(request=httpx.Request("POST", request.url))
        if self.mode == "native_mcp_transport_once" and name == "hydro-fabric-iq-agent" and round_number == 0:
            response = httpx.Response(400, request=httpx.Request("POST", request.url))
            raise BadRequestError(
                (
                    "Remote MCP server error while getting LRO task ask_ontology:test: Streamable HTTP POST "
                    "response completed without a reply to request with ID: 3"
                ),
                response=response,
                body={"code": "tool_user_error", "type": "invalid_request_error"},
            )
        if self.mode == "native_server_error_once" and name == "hydro-fabric-iq-agent" and round_number == 0:
            response = httpx.Response(500, request=httpx.Request("POST", request.url))
            raise InternalServerError(
                "Transient native service failure.",
                response=response,
                body={"code": "server_error", "type": "server_error"},
            )
        if name == "hydro-qa-agent":
            if self.mode == "backlog_capability_omitted_once":
                if round_number == 0:
                    output = [self.function("hydro_query", {
                        "tool_name": "query_telemetry", "arguments": {"equipment_id": "TEST_T005"},
                    })]
                elif round_number == 1:
                    output = [self.message("The separate reads are complete.")]
                elif round_number == 2:
                    output = [self.function("hydro_query", {
                        "tool_name": "query_work_backlog", "arguments": {"group_by": "facility"},
                    })]
                else:
                    output = [self.message("The grouped backlog is complete.")]
            elif self.mode == "standalone_station_chart":
                output = [self.function("hydro_query", {
                    "tool_name": "query_station_power", "arguments": {"lookback": "24h"},
                })] if round_number == 0 else [self.message("The station-power read is complete.")]
            elif self.mode == "independent_empty":
                if round_number < 2:
                    output = [self.function("hydro_query", {
                        "tool_name": "query_signal_quality_snapshot",
                        "arguments": {"quality": "BAD", "lookback": "6h", "equipment_type": "turbine"},
                    })] if round_number == 0 else [self.message("Population A is empty.")]
                else:
                    output = [self.function("hydro_query", {
                        "tool_name": "query_telemetry",
                        "arguments": {"equipment_id": "TEST_T005"},
                    })] if round_number % 2 == 0 else [self.message("Population B is complete.")]
            elif self.mode == "conditional_empty_with_supporting_rows":
                if round_number < 2:
                    output = [
                        self.function("hydro_query", {
                            "tool_name": "query_signal_quality_snapshot",
                            "arguments": {"quality": quality, "lookback": "6h", "equipment_type": "turbine"},
                        }) for quality in ("BAD", "UNCERTAIN")
                    ] if round_number == 0 else [self.message("The requested quality snapshots are complete.")]
                else:
                    output = [
                        self.function("hydro_query", {
                            "tool_name": "query_telemetry", "arguments": {"equipment_id": "TEST_T005"},
                        })
                    ] if round_number == 2 else [self.message("The supporting inventory check is complete.")]
            elif self.mode in ("quality_snapshot", "empty_quality_snapshot", "conditional_empty"):
                output = [
                    self.function("hydro_query", {
                        "tool_name": "query_signal_quality_snapshot",
                        "arguments": {"quality": quality, "lookback": "6h", "equipment_type": "turbine"},
                    }) for quality in ("BAD", "UNCERTAIN")
                ] if round_number == 0 else [self.message("The requested quality snapshots are complete.")]
            else:
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
            if self.mode == "qa_rate_limit_after_source" and round_number == 2:
                output = [self.message("The captured source read is complete; no physical fault is established.")]
        elif name == "hydro-rca-agent":
            ref = {"evidence_id": "reading-1", "path": "/rows/0"}
            if self.mode == "rca_duplicated_evidence_suffix":
                ref["evidence_id"] = "reading-1ing-1"
            if self.mode == "rca_missing_optional_field":
                ref["path"] = "/rows/0/instrumentId"
            report = {"observations": [ref], "hypotheses": [
                {"category": "sensor_or_ingestion", "supporting": [ref], "contradicting": [],
                 "missing": ["independent_measurement"]},
                {"category": "equipment_condition", "supporting": [], "contradicting": [],
                 "missing": ["inspection_evidence"]},
            ], "maintenance_follow_up": {
                "decision": "verified_uncovered_issue"
                if self.mode == "conditional_verified_issue" else "not_supported",
                "reason": "uncovered_equipment_issue"
                if self.mode == "conditional_verified_issue" else "missing_issue_evidence",
                "equipment_ids": ["TEST_T005"] if self.mode == "conditional_verified_issue" else [],
                "evidence": [ref] if self.mode == "conditional_verified_issue" else [],
            }}
            output = [self.function("complete_rca_assessment", report)] if round_number == 0 else [
                self.message("Structured source-referenced investigation completed.")]
            if self.mode == "rca_prose_once":
                output = [self.message("The investigation is complete.")] if round_number == 0 else [
                    self.function("complete_rca_assessment", report)
                ]
            if self.mode == "rca_schema_then_omission_then_schema":
                if round_number in (0, 2):
                    invalid_report = {
                        **report,
                        "hypotheses": [report["hypotheses"][0], report["hypotheses"][0]],
                    }
                    output = [self.function("complete_rca_assessment", invalid_report)]
                elif round_number == 1:
                    output = [self.message("The investigation is complete.")]
                else:
                    output = [self.function("complete_rca_assessment", report)]
            if self.mode == "direct_rca":
                output = [self.function("hydro_query", {
                    "tool_name": "query_telemetry", "arguments": {"equipment_id": "TEST_T005"},
                })] if round_number == 0 else [self.function("complete_rca_assessment", report)]
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
            if self.mode == "work_order_rate_limit_once" and round_number == 1:
                output = [self.function("complete_work_order_review", {
                    "decision": "needs_clarification", "reason": "The source does not establish inspection scope.",
                })]
            if self.mode in ("proposal", "supervisor_no_plan") and round_number == 0:
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
            elif self.mode == "premature_no_draft" and round_number == 0:
                output = [self.function("complete_work_order_review", {
                    "decision": "no_draft", "reason": "No additional work is justified.",
                })]
            elif self.mode == "premature_no_draft" and round_number == 1:
                output = [self.function("complete_work_order_review", {
                    "decision": "needs_clarification", "reason": "Complete work coverage is still required.",
                })]
        elif name == "hydro-fabric-iq-agent":
            native_output = (
                {"error": {"code": "output_moderation", "message": "Published Data Agent unavailable."}}
                if self.mode == "native_moderation_payload"
                else {"error": {"code": "source_unavailable", "message": "Published Data Agent unavailable."}}
                if self.mode == "native_error_payload"
                else {"response": self.native_text}
                if self.mode in ("native_text", "native_text_truncated")
                else
                {"documents": [{
                    "id": "connection-1", "content": "I'm sorry, but I cannot assist with that request.",
                    "title": "hydro-fabric-ontology", "url": "https://example.test/ontology",
                }]}
                if self.mode in ("native_refusal_once", "native_refusal_always")
                and (round_number == 0 or self.mode == "native_refusal_always")
                else
                {"@odata.context": "https://example.test/$metadata#items", "value": [{
                    "id": "connection-1", "content": "", "title": "hydro-fabric-ontology",
                    "url": "https://example.test/ontology",
                }]}
                if self.mode == "native_connection_only"
                else {"documents": [{
                    "id": "connection-1", "content": "Verified domain evidence without a canonical equipment ID.",
                    "title": "hydro-fabric-ontology", "url": "https://example.test/ontology",
                }]}
                if self.mode == "native_handoff_no_identity"
                or self.mode == "native_identity_once" and round_number == 0
                else {"rows": [{"equipment_id": "TEST_T005", "value": 75.0, "unit": "C"}]}
            )
            output = [{
                "type": "mcp_call", "id": "native-call-1", "name": "query",
                "server_label": "fabriciq-ontology"
                if self.mode == "native_wrong_source" or self.mode == "native_wrong_once" and round_number == 0
                else "fabriciq-data-agent",
                "arguments": "{}", "status": "completed", "error": None,
                "output": json.dumps(native_output),
            }, self.message("Native result.")]
            if self.mode.startswith("native_quality_"):
                end = self.request.requested_at
                bundle = {
                    "window_start_utc": end.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                    "window_end_utc": end.isoformat(),
                    "affected_signals": [], "open_work_orders": [],
                    "coverage": {
                        "telemetry_complete": True, "work_complete": True,
                        "affected_signal_count": 0, "open_work_order_count": 0,
                    },
                }
                prose = {"response": "Telemetry source returned no current-day rows; work was also checked."}
                first = copy.deepcopy(output[0])
                first["output"] = json.dumps(
                    prose if self.mode == "native_quality_multiple"
                    or self.mode == "native_quality_missing_always"
                    or self.mode == "native_quality_missing_once" and round_number == 0
                    else bundle
                )
                output = [first, self.message("Native result.")]
                if self.mode == "native_quality_multiple":
                    final = {**first, "id": "native-call-2", "output": json.dumps(bundle)}
                    output.insert(1, final)
                elif self.mode == "native_quality_conflicting":
                    final = {
                        **first, "id": "native-call-2",
                        "output": json.dumps({**bundle, "notes": "A different completed result."}),
                    }
                    output.insert(1, final)
                elif self.mode == "native_quality_incomplete" or (
                    self.mode == "native_quality_incomplete_once" and round_number == 0
                ):
                    bundle["coverage"]["work_complete"] = False
                    first["output"] = json.dumps(bundle)
                elif self.mode == "native_quality_inconsistent":
                    bundle["coverage"]["affected_signal_count"] = 1
                    first["output"] = json.dumps(bundle)
        else:
            raise AssertionError(name)
        incomplete = (
            self.mode == "qa_incomplete_once" and name == "hydro-qa-agent" and round_number == 1
            or self.mode == "native_text_truncated" and name == "hydro-fabric-iq-agent"
        )
        return httpx.Response(200, json={
            "id": f"resp_{name}_{round_number}", "object": "response", "created_at": 1,
            "model": "test", "status": "incomplete" if incomplete else "completed",
            "incomplete_details": {"reason": "max_output_tokens"} if incomplete else None,
            "output": output, "parallel_tool_calls": False,
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        })

    async def test_actual_framework_runs_existing_supervisor_and_specialists_with_shared_evidence(self):
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(self.calls["hydro-rca-agent"], 1)
        self.assertEqual(self.calls["hydro-work-order-agent"], 1)
        self.assertEqual(result.tables[0].title, "Telemetry")
        self.assertEqual(result.tables[0].rows[0].values, {
            "equipment_id": "TEST_T005", "value": 75.0, "unit": "C", "quality": "BAD",
        })
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
        self.assertEqual(len(trace), 10)
        self.assertEqual(
            [item["activity"] for item in trace],
            ["validation"] + ["tool-start", "delegation-return"] * 4 + ["validation"],
        )
        serialized = json.dumps(self.events)
        self.assertNotIn("test-not-a-real-token", serialized)
        self.assertNotIn("rows", serialized)

    async def test_deterministic_compiler_enforces_conditional_gap_review_gate(self):
        self.mode = "conditional_no_issue"
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": (
                "Read telemetry, investigate it, prepare an editable inspection draft only for an "
                "evidence-backed gap, and independently verify the result. Keep this read-only."
            ),
            "required_specialists": ("qa", "rca", "work-order"),
            "required_specialist_steps": {"qa": 2, "rca": 1, "work-order": 1},
            "required_evidence_kinds": ("telemetry",),
            "permitted_evidence_kinds": ("telemetry",),
        })
        self.seed(request)

        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("tool_catalog", await self.tools.catalog(request))
        plan = await self.owner.compile_orchestration_plan(request, journal)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        work_order_step = next(
            step for step in plan.steps
            if step.specialist == "work-order"
        )
        self.assertTrue(work_order_step.requires_verified_issue)

    async def test_canonical_data_agent_facility_reconciliation_compiles_cross_runtime(self):
        question = (
            "Ask the published Fabric Data Agent for all facility instances and every open "
            "operational SQL work order by equipment. Reconcile those claims with direct facility, "
            "equipment and work-order records, then show a facility-level backlog table and chart. "
            "Preserve unmatched IDs and source limitations instead of guessing relationships. "
            "This turn is read-only."
        )
        intent = await self.bridge.call({"action": "chat_intent", "question": question})
        request = ChatRequest.model_validate({
            **self.request.model_dump(),
            **intent,
            "run_id": uuid4(),
            "question": question,
        })
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("tool_catalog", await self.bridge.call({"action": "source_contracts"}))
        plan = await self.owner.compile_orchestration_plan(request, journal)
        native = next(
            requirement for requirement in plan.answer_contract.requirements
            if requirement.kind == "native_claims"
        )
        self.assertEqual(native.scope, "population")
        self.assertTrue(any(view.authority == "reconciled" for view in plan.answer_contract.views))

    async def test_weather_temperature_compiles_to_weather_without_turbine_evidence(self):
        question = "Show the weather forecast temperature and rainfall for the next 24 hours."
        intent = await self.bridge.call({"action": "chat_intent", "question": question})
        request = ChatRequest.model_validate({
            **self.request.model_dump(), **intent, "run_id": uuid4(), "question": question,
        })
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("tool_catalog", await self.bridge.call({"action": "source_contracts"}))
        plan = await self.owner.compile_orchestration_plan(request, journal)
        self.assertEqual([step.specialist for step in plan.steps], ["weather"])
        self.assertEqual({item.kind for item in plan.answer_contract.requirements},
                         {"weather_locations", "weather_forecasts"})
        self.assertEqual(deterministic_fulfillment_route(
            "weather_forecasts", "population"), "query_weather")

    async def test_incomplete_qa_completion_gets_one_bounded_evidence_only_retry(self):
        self.mode = "qa_incomplete_once"
        request = self.request.model_copy(update={
            "question": "Read and verify current telemetry.",
            "required_specialists": ("qa",),
            "required_specialist_steps": {"qa": 1},
        })
        self.seed(request)
        owner = self.make_owner()
        result = await owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["qa"])
        self.assertEqual(self.calls["hydro-qa-agent"], 4)
        self.assertEqual(self.tools.calls, 1)
        repair_payload = json.dumps(self.payloads)
        self.assertIn("prior completion was incomplete", repair_payload)
        self.assertIn("Do not call another source tool", repair_payload)

    async def test_foundry_service_calls_have_a_finite_network_timeout(self):
        with patch(
            "hydro_orchestrator.foundry_supervisor.FoundryAgent",
            wraps=FoundryAgent,
        ) as agent_factory:
            await self.owner.run(self.request)

        self.assertGreater(agent_factory.call_count, 0)
        self.assertTrue(all(
            call.kwargs["timeout"] == FOUNDRY_SERVICE_CALL_TIMEOUT_SECONDS
            for call in agent_factory.call_args_list
        ))

    def test_foundry_project_client_disables_hidden_sdk_retries(self):
        client = BoundedAIProjectClient(endpoint=ENDPOINT, credential=self.credential)
        with patch(
            "azure.ai.projects.aio.AIProjectClient.get_openai_client",
            return_value=self.openai,
        ) as get_openai_client:
            self.assertIs(client.get_openai_client(), self.openai)
        self.assertEqual(get_openai_client.call_args.kwargs["max_retries"], 1)
        self.assertEqual(
            get_openai_client.call_args.kwargs["timeout"],
            FOUNDRY_SERVICE_CALL_TIMEOUT_SECONDS,
        )

    def test_rca_scope_honors_named_equipment_before_population_and_with_missing_snapshot(self):
        request = self.request.model_copy(update={
            "required_evidence_kinds": ("signal_quality",),
            "resolved_scope": ResolvedScope(
                source=SOURCE, read_completed_at=utc_now(),
                matches=(ResolvedIdentity(
                    mention="T005", kind="equipment", canonical_id="TEST_T005",
                    label="T005", matched_by="tag",
                ),),
            ),
        })
        for index, rows in enumerate(([], [
            {"equipment_id": "TEST_T005"}, {"equipment_id": "TEST_T999"},
        ])):
            self.owner.evidence = {"quality": ToolEvidence(
                id="quality", source=SOURCE, tool="query_signal_quality_snapshot",
                arguments={}, completed_at=utc_now(), result={"rows": rows},
            )} if rows else {}
            with self.subTest(rows=rows):
                scope = self.owner.root_cause_scope(request, RunJournal(self.root / f"named-rca-{index}"))
                self.assertEqual(scope["equipment_ids"], ["TEST_T005"])
                self.assertEqual(scope["omitted"], 0)

    def test_rca_scope_honors_deterministic_evidence_selection_before_fleet_population(self):
        request = ChatRequest.model_validate({
            **self.request.model_dump(),
            "required_evidence_kinds": ["signal_quality"],
            "equipment_selection": {
                "metric": "signal_quality_count", "quality": "BAD", "tie_break": "equipment_id_asc",
            },
        })
        self.owner.evidence = {"quality": ToolEvidence(
            id="quality", source=SOURCE, tool="query_signal_quality_snapshot",
            arguments={}, completed_at=utc_now(), result={"rows": [
                {"equipment_id": "TEST_T005", "opcua_node_id": "node-a", "quality": "BAD"},
                {"equipment_id": "TEST_T005", "opcua_node_id": "node-b", "quality": "BAD"},
                {"equipment_id": "TEST_T999", "opcua_node_id": "node-c", "quality": "BAD"},
            ]},
        )}
        scope = self.owner.root_cause_scope(request, RunJournal(self.root / "selected-rca"))
        self.assertEqual(scope["equipment_ids"], ["TEST_T005"])

    async def test_population_requirements_are_fulfilled_before_rca_history_scope_is_frozen(self):
        order: list[str] = []
        fulfill = self.owner.fulfill_catalog_requirements

        async def record_fulfillment(request: ChatRequest, journal: RunJournal) -> None:
            order.append("fulfill")
            await fulfill(request, journal)

        async def record_history(request: ChatRequest, journal: RunJournal) -> dict[str, object] | None:
            order.append("history")
            return None

        with patch.object(self.owner, "fulfill_catalog_requirements", side_effect=record_fulfillment), patch.object(
            self.owner, "root_cause_evidence", side_effect=record_history,
        ):
            await self.owner.run(self.request)
        self.assertLess(order.index("fulfill"), order.index("history"))

    def test_explicit_resolved_equipment_seeds_conditional_handoffs_when_reads_are_empty(self):
        request = self.request.model_copy(update={
            "question": "Read notifications and open work for turbine T005, then investigate.",
            "resolved_scope": ResolvedScope(
                source=SOURCE,
                read_completed_at=utc_now(),
                matches=(ResolvedIdentity(
                    mention="T005",
                    kind="equipment",
                    canonical_id="TEST_T005",
                    label="T005",
                    matched_by="tag",
                ),),
            ),
        })
        self.assertEqual(initial_equipment_selection(request), {"TEST_T005"})
        self.assertEqual(initial_equipment_selection(self.request), set())

    async def test_rca_prose_gets_one_bounded_structured_completion_repair(self):
        self.mode = "rca_prose_once"
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.calls["hydro-rca-agent"], 2)
        repair = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-rca-agent"
            and "completion_repair" in json.dumps(payload["input"])
        )
        self.assertIn("required structured completion", json.dumps(repair["input"]))

    async def test_each_bounded_rca_completion_attempt_gets_one_schema_correction(self):
        self.mode = "rca_schema_then_omission_then_schema"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING") as logs:
            result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.calls["hydro-rca-agent"], 4)
        self.assertEqual(
            sum("complete_rca_assessment arguments rejected" in line for line in logs.output),
            2,
        )
        self.assertTrue(all("correction 1/1" in line for line in logs.output
                            if "complete_rca_assessment arguments rejected" in line))

    async def test_rca_repairs_one_duplicated_terminal_evidence_id_suffix(self):
        self.mode = "rca_duplicated_evidence_suffix"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING") as logs:
            result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(result.specialists[1].report["observations"], [{
            "evidence_id": "reading-1", "path": "/rows/0",
        }])
        self.assertTrue(any("duplicated a terminal evidence-ID suffix" in line for line in logs.output))

    async def test_rca_repairs_missing_row_descendant_to_verified_row(self):
        self.mode = "rca_missing_optional_field"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING") as logs:
            result = await self.owner.run(self.request)
        rca = next(item for item in result.specialists if item.role == "rca")
        self.assertEqual(rca.report["observations"], [{
            "evidence_id": "reading-1", "path": "/rows/0",
        }])
        self.assertTrue(any("missing row descendant fields" in line for line in logs.output))

    async def test_row_descendant_repair_does_not_accept_missing_rows(self):
        receipt = ToolEvidence(
            id="reading-1", source=SOURCE, tool="query_telemetry", arguments={},
            completed_at=utc_now(), result={"rows": [{"equipment_id": "TEST_T005"}]},
        )
        report = {"evidence_id": "reading-1", "path": "/rows/1/instrumentId"}
        repaired, count = repair_evidence_row_descendant_paths(report, {receipt.id: receipt})
        self.assertEqual(repaired, report)
        self.assertEqual(count, 0)

    async def test_actual_framework_fixer_stages_source_bound_human_approval_card_without_sql(self):
        self.mode = "proposal"
        self.tools.stage = True
        self.tools.quality_snapshot = True
        self.request = self.request.model_copy(update={
            "question": "Read BAD turbine quality today, investigate and propose a TEST_T005 signal inspection; verify.",
        })
        result = await self.owner.run(self.request)
        self.assertEqual(self.tools.calls, 3)
        self.assertEqual(len(result.proposals), 1)
        draft = result.proposals[0]
        self.assertEqual(draft.equipment_id, "TEST_T005")
        self.assertEqual(draft.priority, "Medium")
        self.assertEqual(draft.source, self.request.source)
        self.assertEqual(result.proposal_digests, {str(draft.id): draft.digest()})
        self.assertFalse(result.production_write_executed)
        self.assertEqual(result.specialists[2].model_round_count, 2)
        self.assertEqual(result.tables[0].title, "Signal Quality")
        self.assertEqual(result.tables[0].rows[0].values["equipment_id"], "TEST_BAD")
        self.assertEqual(result.tables[0].rows[0].values["quality"], "BAD")
        evidence = self.owner.evidence["proposal-1"]
        self.owner.evidence["proposal-copy"] = evidence.model_copy(update={"id": "proposal-copy"})
        self.assertEqual(len(self.owner.answer(self.request, AnswerPlan.model_validate(self.plan())).proposals), 1)
        changed = draft.model_copy(update={"description": "A different scope with the same card identity."})
        self.owner.evidence["proposal-copy"] = evidence.model_copy(update={
            "id": "proposal-copy", "result": {**evidence.result, "proposal": changed.model_dump(mode="json")},
        })
        with self.assertRaisesRegex(SourceFailure, "Conflicting source receipts"):
            self.owner.answer(self.request, AnswerPlan.model_validate(self.plan()))
        projection_payloads = [payload for payload in self.payloads if "agent_reference" not in payload]
        self.assertEqual(projection_payloads, [])
        before = dict(self.calls)
        restarted = await self.make_owner().run(self.request)
        self.assertEqual(restarted, result)
        self.assertEqual(self.calls, before)

    async def test_follow_up_runs_only_the_draft_specialist_on_the_carried_dataset(self):
        self.mode = "quality_snapshot"
        self.tools.quality_snapshot = True
        self.tools.stage = True
        first = self.request.model_copy(update={
            "run_id": uuid4(), "question": "Which turbines had BAD or UNCERTAIN telemetry quality in the last 6 hours?",
        })
        self.seed(first)
        await self.owner.run(first)
        reads_after_first, qa_runs = self.tools.calls, self.calls["hydro-qa-agent"]

        chat = ChatInput(
            question="Based on this data, prepare an inspection draft for the BAD turbine signals.",
            previous_run_id=first.run_id,
        )
        normalized, carried = await follow_up_intent(self.bridge, self.root, SOURCE, chat)
        self.assertTrue(carried)
        self.assertEqual(normalized.required_specialists, ("work-order",))

        self.mode = "proposal"
        now = utc_now()
        second = ChatRequest.model_validate({
            **self.request.model_dump(mode="python"), "run_id": uuid4(), "requested_at": now,
            "deadline": now + timedelta(minutes=3), "carried_from": first.run_id,
            "carried_evidence_ids": carried, **normalized.model_dump(),
        })
        events: list[dict[str, object]] = []
        follow_up = self.make_owner()
        follow_up.set_event_sink(events.append)
        self.seed(second)
        result = await follow_up.run(second)

        self.assertEqual([item.role for item in result.specialists], ["work-order"])
        self.assertEqual(self.calls["hydro-qa-agent"], qa_runs, "Gauge must not be asked the previous question again")
        self.assertEqual(self.tools.calls, reads_after_first + 1, "only the draft's own source call may run")
        self.assertEqual(len(result.proposals), 1)
        self.assertEqual(result.tables[0].title, "Signal Quality")
        self.assertTrue(any("Reused evidence" in item for item in result.limitations))
        self.assertTrue(any("Reusing the verified evidence" in str(event["detail"]) for event in events))

        fixer = next(payload for payload in self.payloads
                     if payload.get("agent_reference", {}).get("name") == "hydro-work-order-agent"
                     and json.loads(payload["input"][0]["content"][0]["text"]).get("carried_evidence"))
        context = json.loads(fixer["input"][0]["content"][0]["text"])
        self.assertEqual({item["id"] for item in context["carried_evidence"]}, set(carried))
        self.assertTrue(set(carried) <= {item["id"] for item in context["evidence"]})
        self.assertIn("do not re-read", context["carried_evidence_policy"])

        before = dict(self.calls)
        again = await self.make_owner().run(second)
        self.assertEqual(again, result)
        self.assertEqual(self.calls, before)

    async def test_source_failure_cannot_become_a_successful_supervisor_answer(self):
        self.tools.failure = True
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="ERROR"):
            with self.assertRaisesRegex(SourceFailure, "Injected source outage"):
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
        receipt = await self.tools.execute("query_telemetry", {"equipment_id": "TEST_T005"}, self.request)
        self.owner.evidence[receipt.id] = receipt
        for mode in ("chart", "invented", "transposed", "extra_column"):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises((SourceFailure, ValidationError)):
                    self.owner.answer(self.request, AnswerPlan.model_validate(self.plan()))


    async def test_standalone_station_chart_executes_qa_and_returns_source_owned_chart(self):
        intent = await self.bridge.call({
            "action": "chat_intent",
            "question": "Chart average power output per station over the last 24 hours.",
        })
        self.mode = "standalone_station_chart"
        self.tools.station_power = True
        request = ChatRequest.model_validate({
            **self.request.model_dump(mode="python"),
            "run_id": uuid4(),
            "question": "Chart average power output per station over the last 24 hours.",
            **intent,
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["qa"])
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(result.tables[0].rows[0].values["Station"], "Foyers")
        self.assertEqual(result.tables[0].rows[0].values["average_power_MW"], 1271.1575)
        self.assertEqual(len(result.charts), 1)
        self.assertEqual(result.charts[0].x_key, "Station")
        self.assertEqual(result.charts[0].y_keys, ("average_power_MW",))
        self.assertIn("Station=Foyers", result.summary)
        self.assertIn("average_power_MW=1271.1575", result.summary)
        self.assertIn("bad_samples=73", result.summary)
        self.assertTrue(any("not total station output" in item for item in result.limitations))






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


    async def test_credential_lease_does_not_limit_execution_and_changed_source_stops(self):
        expired = self.request.model_copy(update={"deadline": utc_now() - timedelta(seconds=1)})
        self.owner.healthy(expired)
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
            with self.assertRaisesRegex(SourceFailure, "changed its authoritative source"):
                await self.owner.run(self.request)
        journal = RunJournal(self.root / str(self.request.run_id) / "receipts")
        self.assertIsNone(journal.read('tool:{"arguments":{"equipment_id":"TEST_T005"},"name":"query_telemetry"}'))

    async def test_delegation_budget_stops_instead_of_simulating_remaining_roles(self):
        with self.assertRaisesRegex(SourceFailure, "specialist budget was exceeded"):
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
        with self.assertRaisesRegex(SourceFailure, "verified target-specific native connection binding"):
            await self.make_owner().run(request)
        self.assertNotIn("hydro-fabric-iq-agent", self.calls)
        self.calls.clear()
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual(result.specialists[0].role, "fabric-iq")
        self.assertEqual(result.tables[0].rows[0].values, {
            "equipment_id": "TEST_T005", "value": 75.0, "unit": "C",
        })
        self.assertEqual(self.tools.calls, 0)
        self.assertIn("does not attest", result.limitations[0])
        self.assertEqual(next(iter(owner.evidence.values())).resolved_equipment_ids, ("TEST_T005",))

    async def invoke_quality_source(self):
        request = self.request.model_copy(update={
            "question": "Which turbines had BAD telemetry quality today, and what work is already open for these?",
            "native_sources": ("data-agent",),
        })
        journal = RunJournal(self.root / str(request.run_id) / "receipts")
        journal.save("tool_catalog", await self.tools.catalog(request))
        owner = self.make_owner(native_binding=NativeBinding(
            source=SOURCE, connections={"data-agent": "native-data"},
        ))
        await owner.invoke("fabric-iq", request.question, request, journal, self.versions, "data-agent")
        return owner, journal

    async def test_native_quality_contract_validates_complete_step_not_each_intermediate_call(self):
        self.mode = "native_quality_multiple"
        owner, journal = await self.invoke_quality_source()
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertEqual(self.tools.calls, 0)
        self.assertEqual(list(owner.evidence), ["native-call-2"])
        result = owner.evidence["native-call-2"].result
        self.assertEqual(result["affected_signals"], [])
        self.assertEqual(len(result["native_source_output"]["native_outputs"]), 2)
        audit = journal.read("native_response:resp_hydro-fabric-iq-agent_0")
        self.assertEqual(len(audit["receipts"]), 2)

    async def test_native_quality_missing_json_gets_only_one_same_source_contract_repair(self):
        self.mode = "native_quality_missing_once"
        owner, journal = await self.invoke_quality_source()
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        self.assertEqual(self.tools.calls, 0)
        self.assertEqual(owner.evidence["native-call-1"].result["affected_signals"], [])
        self.assertIn("no current-day rows", json.dumps(
            journal.read("native_response:resp_hydro-fabric-iq-agent_0"),
        ))
        repair = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-fabric-iq-agent"
            and "native_source_repair" in json.dumps(payload["input"])
        )
        self.assertIn("verbatim", json.dumps(repair["input"]))

    async def test_native_quality_repeated_missing_json_fails_without_accepting_prose(self):
        self.mode = "native_quality_missing_always"
        with self.assertRaisesRegex(SourceFailure, "inspectable JSON bundle"):
            await self.invoke_quality_source()
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        self.assertEqual(self.tools.calls, 0)

    async def test_native_quality_conflicting_bundles_are_not_selected_or_combined(self):
        self.mode = "native_quality_conflicting"
        with self.assertRaisesRegex(SourceFailure, "inspectable JSON bundle"):
            await self.invoke_quality_source()
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertEqual(self.tools.calls, 0)

    async def test_native_quality_repeated_incomplete_coverage_propagates_after_one_source_repair(self):
        self.mode = "native_quality_incomplete"
        with self.assertRaisesRegex(SourceFailure, "coverage is incomplete"):
            await self.invoke_quality_source()
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        self.assertEqual(self.tools.calls, 0)

    async def test_native_quality_incomplete_coverage_retries_scalar_count_not_grouped_bad_rows(self):
        self.mode = "native_quality_incomplete_once"
        owner, _journal = await self.invoke_quality_source()
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        self.assertTrue(owner.evidence["native-call-1"].result["coverage"]["telemetry_complete"])
        repair = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-fabric-iq-agent"
            and "Coverage correction" in json.dumps(payload["input"])
        )
        self.assertIn("total_telemetry_rows_requested_window=count()", json.dumps(repair["input"]))
        self.assertEqual(self.tools.calls, 0)

    async def test_native_quality_inconsistent_counts_fail_without_a_source_retry(self):
        self.mode = "native_quality_inconsistent"
        with self.assertRaisesRegex(SourceFailure, "coverage is unbounded or inconsistent"):
            await self.invoke_quality_source()
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertEqual(self.tools.calls, 0)

    async def test_native_identity_enables_a_required_downstream_handoff(self):
        self.mode = "native_handoff"
        request = self.request.model_copy(update={
            "question": "Use the Data Agent to resolve T005, then inspect it directly.",
            "native_sources": ("data-agent",),
            "required_specialists": ("qa",),
        })
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["fabric-iq", "qa"])
        self.assertEqual(self.calls["hydro-qa-agent"], 2)

    async def test_required_qa_resolves_scope_when_native_prose_has_no_extractable_identity(self):
        self.mode = "native_handoff_no_identity"
        self.tools.include_identity = True
        request = self.request.model_copy(update={
            "question": "Use the Data Agent to inspect the domain, then resolve and inspect the equipment directly.",
            "native_sources": ("data-agent",),
            "required_specialists": ("qa",),
        })
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["fabric-iq", "qa"])
        self.assertEqual(self.calls["hydro-qa-agent"], 2)



    async def test_a_message_without_an_actionable_manifest_is_answered_as_conversation_not_failed(self):
        self.mode = "supervisor_no_plan"
        request = self.request.model_copy(update={"question": "Hello."})
        result = await self.owner.run(request)
        self.assertEqual((result.tables, result.specialists, result.source_read_times), ((), (), ()))
        self.assertIn("could not verify an answer from this conversation", result.summary)
        self.assertEqual(sum(self.calls.values()), 0)

    def test_native_markdown_identity_projection_is_field_bounded(self):
        self.assertEqual(
            _native_equipment_ids({
                "response": "| Field | Value |\n|---|---|\n| EquipmentId | `EQUIP_RTI_T005` |\n"
                            "| FacilityId | `FACILITY_RTI_001` |\n"
                            "- `equipment_id`: `EQUIP_RTI_T006`\n"
                            "- **Resolved equipment identity:** `EQUIP_RTI_T007`",
            }),
            {"EQUIP_RTI_T005", "EQUIP_RTI_T006", "EQUIP_RTI_T007"},
        )
        self.assertEqual(
            _native_equipment_ids({
                "response": "| Match field | Equipment canonical ID | Equipment tag | Status |\n"
                            "|---|---|---|---|\n"
                            "| tag | `EQUIP_RTI_T008` | T008 | ACTIVE |\n",
            }),
            {"EQUIP_RTI_T008"},
        )
        self.assertEqual(_native_equipment_ids({"response": "Investigate EQUIP_RTI_T006 next."}), set())

    def test_resolved_scope_canonicalizes_every_specialist_source_filter(self):
        scope = ResolvedScope(
            source=SOURCE,
            read_completed_at=self.request.requested_at + timedelta(seconds=1),
            matches=(
                ResolvedIdentity(
                    kind="equipment", mention="t09", canonical_id="EQUIP_RTI_T009",
                    label="T009", facility_id="FACILITY_RTI_002", matched_by="tag",
                ),
                ResolvedIdentity(
                    kind="facility", mention="facility 01", canonical_id="FACILITY_RTI_001",
                    label="Sloy Power Station", facility_id="FACILITY_RTI_001", matched_by="number",
                ),
            ),
        )
        request = self.request.model_copy(update={"resolved_scope": scope})
        self.assertEqual(
            canonical_source_arguments(request, {
                "entity": "equipment",
                "where": [
                    {"column": "tag", "op": "eq", "value": "t09"},
                    {"column": "facility_id", "op": "in", "value": ["facility 01"]},
                ],
                "equipment_id": "t09",
            }),
            {
                "entity": "equipment",
                "where": [
                    {"column": "tag", "op": "eq", "value": "T009"},
                    {"column": "facility_id", "op": "in", "value": ["FACILITY_RTI_001"]},
                ],
                "equipment_id": "EQUIP_RTI_T009",
            },
        )

    async def test_native_source_mismatch_does_not_fall_back_to_direct_tools(self):
        self.mode = "native_wrong_source"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertRaisesRegex(SourceFailure, "Native source mismatch"):
            await owner.run(request)
        self.assertEqual(self.tools.calls, 0)

    async def test_native_source_mismatch_gets_one_bounded_same_agent_correction(self):
        self.mode = "native_wrong_once"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["fabric-iq"])
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        repair = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-fabric-iq-agent"
            and "native_source_repair" in json.dumps(payload["input"])
        )
        self.assertIn("fabriciq-data-agent", json.dumps(repair["input"]))
        self.assertEqual(self.tools.calls, 0)

    async def test_native_timeout_is_not_retried(self):
        self.mode = "native_timeout_once"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertRaises(AgentFrameworkException):
            await owner.run(request)
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertFalse(any(
            "native_timeout_recovery" in json.dumps(payload["input"])
            for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-fabric-iq-agent"
        ))
        self.assertEqual(self.tools.calls, 0)

    async def test_native_missing_mcp_reply_gets_one_bounded_same_source_retry(self):
        self.mode = "native_mcp_transport_once"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["fabric-iq"])
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        self.assertEqual(self.tools.calls, 0)

    async def test_native_server_error_gets_one_bounded_same_source_retry(self):
        self.mode = "native_server_error_once"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["fabric-iq"])
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        self.assertEqual(self.tools.calls, 0)

    async def test_work_order_rate_limit_honors_one_provider_wait_without_replaying_source_reads(self):
        self.mode = "work_order_rate_limit_once"
        self.openai.max_retries = 1
        with patch("openai._base_client.anyio.sleep", new_callable=AsyncMock) as sleep:
            result = await self.owner.run(self.request)
        sleep.assert_awaited_once_with(1.0)
        self.assertEqual(self.calls["hydro-work-order-agent"], 2)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertFalse(any(item.tool == "propose_work_order" for item in self.owner.evidence.values()))

    async def test_repeated_model_rate_limit_fails_after_one_wait_without_source_execution(self):
        self.mode = "rate_limit_always"
        self.openai.max_retries = 1
        with patch("openai._base_client.anyio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(AgentFrameworkException):
                await self.owner.run(self.request)
        sleep.assert_awaited_once_with(1.0)
        self.assertEqual(self.calls["hydro-qa-agent"], 2)
        self.assertEqual(self.tools.calls, 0)

    async def test_per_request_rate_retry_keeps_tool_result_and_does_not_restart_source_dispatch(self):
        self.mode = "qa_rate_limit_after_source"
        self.openai.max_retries = 1
        journal = RunJournal(self.root / "quota-after-source")
        journal.save("tool_catalog", await self.tools.catalog(self.request))
        with patch("openai._base_client.anyio.sleep", new_callable=AsyncMock) as sleep:
            response, report, _ = await self.owner.invoke(
                "qa", "Read the selected equipment telemetry.", self.request,
                journal, self.versions, None,
            )
        sleep.assert_awaited_once_with(1.0)
        self.assertIsNone(report)
        self.assertTrue(response.response_id)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(self.calls["hydro-qa-agent"], 3)
        self.assertEqual(self.payloads[-2]["input"], self.payloads[-1]["input"])

    async def test_native_refusal_stops_without_same_source_correction_or_fallback(self):
        self.mode = "native_refusal_once"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertRaisesRegex(SourceFailure, "Native source refusal"):
            await owner.run(request)
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertFalse(any("native_source_repair" in json.dumps(payload["input"]) for payload in self.payloads))
        self.assertFalse(owner.evidence)
        self.assertEqual(self.tools.calls, 0)

    async def test_native_requested_identity_gets_one_bounded_same_source_correction(self):
        self.mode = "native_identity_once"
        request = self.request.model_copy(update={
            "native_sources": ("data-agent",),
            "resolved_scope": ResolvedScope(
                source=SOURCE,
                read_completed_at=utc_now(),
                matches=(ResolvedIdentity(
                    mention="T005", kind="equipment", canonical_id="TEST_T005",
                    label="T005", matched_by="tag",
                ),),
            ),
        })
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        result = await owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["fabric-iq"])
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 2)
        self.assertEqual(next(iter(owner.evidence.values())).resolved_equipment_ids, ("TEST_T005",))
        repair = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-fabric-iq-agent"
            and "native_source_repair" in json.dumps(payload["input"])
        )
        self.assertIn("omitted the operator's resolved equipment instance", json.dumps(repair["input"]))

    async def test_repeated_native_refusal_is_not_accepted_as_evidence(self):
        self.mode = "native_refusal_always"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertRaisesRegex(SourceFailure, "Native source refusal"):
            await owner.run(request)
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertEqual(self.tools.calls, 0)

    def test_native_moderation_and_error_payloads_are_not_domain_evidence(self):
        for payload in [
            "The endpoint was unavailable with an output-moderation error.",
            {"documents": [{"content": "OutputModeration: no source query completed."}]},
            {"error": {"code": "output_moderation"}},
            {"source_identity": [{"isError": True, "message": "Source unavailable."}]},
            {"native_output": {"is_error": True, "content": "Source unavailable."}},
        ]:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(SourceFailure, "Native source (refusal|failure)"):
                    _validated_native_result(json.dumps(payload))

    def test_provider_moderation_processing_error_is_not_a_source_refusal(self):
        payload = {
            "rows": [{"facility_id": "FACILITY_RTI_001"}],
            "raiPostToolAnnotations": [{
                    "error": {"errorDetails": "Error during output moderation for tool: DataAgent for general harms."},
            }],
        }
        with self.assertRaises(SourceFailure) as caught:
            _validated_native_result(json.dumps(payload))
        self.assertEqual(caught.exception.category, "native_provider_error")
        self.assertIn("not a confirmed Data Agent refusal", str(caught.exception))
        self.assertEqual(_validated_native_result(json.dumps({"row_count": 0, "rows": []}))["row_count"], 0)
        self.assertEqual(_validated_native_result(json.dumps({
            "rows": [{"equipment_id": "TEST_T005", "status": "Unavailable"}],
        }))["rows"], [{"equipment_id": "TEST_T005", "status": "Unavailable"}])

    def test_native_refusal_is_classified_before_service_errors_or_partial_evidence(self):
        for payload in [
            {"error": {"code": "output_moderation", "message": "private provider diagnostics"}},
            {"documents": [{"content": "OutputModeration: no source query completed."}]},
            {"rows": [{"facility_count": 1}], "warning": "output moderation"},
            "I'm sorry, but I cannot assist with that request.",
        ]:
            with self.subTest(payload=payload):
                with self.assertRaises(SourceFailure) as caught:
                    _validated_native_result(json.dumps(payload))
                self.assertEqual(getattr(caught.exception, "category", None), "native_refusal")
                self.assertIn("owner", str(caught.exception))
                self.assertNotIn("private provider diagnostics", str(caught.exception))

    async def test_native_service_error_payload_fails_without_fallback_or_false_completion(self):
        self.mode = "native_error_payload"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertRaisesRegex(SourceFailure, "Native source failure"):
            await owner.run(request)
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertEqual(self.tools.calls, 0)
        self.assertFalse(owner.evidence)

    async def test_native_moderation_error_stops_without_correction_or_fallback(self):
        self.mode = "native_moderation_payload"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertRaisesRegex(SourceFailure, "Native source refusal") as caught:
            await owner.run(request)
        self.assertEqual(getattr(caught.exception, "category", None), "native_refusal")
        self.assertEqual(self.calls["hydro-fabric-iq-agent"], 1)
        self.assertEqual(self.tools.calls, 0)
        self.assertFalse(owner.evidence)

    def test_ordinary_native_bad_request_is_not_retryable(self):
        response = httpx.Response(400, request=httpx.Request("POST", ENDPOINT))
        error = BadRequestError(
            "Invalid native request.",
            response=response,
            body={"code": "tool_user_error", "type": "invalid_request_error"},
        )
        self.assertIsNone(transient_native_failure(error))

    async def test_grouped_work_backlog_uses_one_bounded_capability_correction(self):
        self.mode = "backlog_capability_omitted_once"
        request = self.request.model_copy(update={
            "question": "Show a facility-level backlog table and chart.",
            "required_specialists": ("qa",),
            "required_specialist_steps": {"qa": 1},
            "charts_requested": True,
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertEqual(self.calls["hydro-qa-agent"], 4)
        self.assertEqual(self.tools.calls, 2)
        self.assertEqual(result.charts[0].x_key, "facility_id")
        self.assertEqual(result.charts[0].y_keys, ("open_work_order_count",))
        repair = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-qa-agent"
            and "source_capability_repair" in json.dumps(payload["input"])
        )
        self.assertIn("group_by=facility", json.dumps(repair["input"]))

    async def test_grouped_work_backlog_intent_is_centralized(self):
        self.assertEqual(
            (await self.bridge.call({
                "action": "work_backlog_group",
                "question": "Show a facility-level backlog table and chart.",
            }))["group_by"],
            "facility",
        )
        self.assertEqual(
            (await self.bridge.call({
                "action": "work_backlog_group",
                "question": "Choose equipment with the most open orders and chart orders-per-equipment.",
            }))["group_by"],
            "equipment",
        )
        self.assertIsNone((await self.bridge.call({
            "action": "work_backlog_group", "question": "Read open work for T005.",
        }))["group_by"])

    async def test_native_connection_metadata_is_not_accepted_as_domain_evidence(self):
        self.mode = "native_connection_only"
        request = self.request.model_copy(update={"native_sources": ("data-agent",)})
        owner = self.make_owner(native_binding=NativeBinding(source=SOURCE, connections={"data-agent": "native-data"}))
        with self.assertRaisesRegex(SourceFailure, "connection metadata without domain evidence"):
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
                request_body = {
                    "question": self.request.question,
                    "permitted_evidence_kinds": list(self.request.permitted_evidence_kinds),
                }
                submitted = await client.post("/chat/runs", json=request_body, headers=headers)
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
                replay = await client.post("/chat/runs", json=request_body, headers=headers)
                self.assertEqual(replay.status_code, 202)
                conflict = await client.post("/chat/runs", json={"question": "Different request."}, headers=headers)
                self.assertEqual(conflict.status_code, 409)
                missing_id = uuid4()
                missing = await client.get(f"/chat/runs/{missing_id}", headers=HEADERS)
                self.assertEqual(missing.status_code, 404)
                self.assertFalse((self.root / "chat" / str(missing_id)).exists())
        self.assertEqual(self.tools.calls, 1)


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
        self.assertEqual(result.tables[0].rows[0].values, {
            "equipment_id": "TEST_T005", "value": 75.0, "unit": "C", "quality": "BAD",
        })
        self.assertEqual(self.calls, before)
        self.assertEqual(self.tools.calls, 1)

    async def test_cached_handoff_cannot_replace_an_immutable_evidence_receipt(self):
        evidence = ToolEvidence(
            id="immutable-reading", source=SOURCE, tool="query_telemetry",
            arguments={"equipment_id": "TEST_T005"}, completed_at=utc_now(),
            result={"rows": [{"equipment_id": "TEST_T005", "value": 75.0}], "truncated": False},
            datasets=(EvidenceDataset(
                kind="telemetry", field="rows", authority="direct", scope="focus",
                complete=True, identity_fields=("opcua_node_id", "event_time"),
            ),),
        )
        self.owner.evidence[evidence.id] = evidence
        self.owner.run_id = self.request.run_id
        delegation = Delegation(
            specialist="qa", question="Verify T005 telemetry.", reason="Verify evidence.",
        )
        handoff_context = {
            "evidence": [evidence.receipt()],
            "completed_specialists": [],
        }
        key = "delegate:" + _encoded({
            "role": "qa", "question": delegation.question, "native": None,
            "context_digest": sha256(_encoded(handoff_context)).hexdigest(),
            "targeted_context": False,
            "maintenance_gate_required": False,
        }).decode()
        conflicting = evidence.model_copy(update={
            "result": {"rows": [{"equipment_id": "TEST_T005", "value": 76.0}], "truncated": False},
        })
        specialist = SpecialistResult(
            role="qa", agent_name=self.versions["qa"].name, version=self.versions["qa"].version,
            response_id="cached-response", duration_ms=1, model_round_count=1,
            input_digest="0" * 64, text="cached",
        )
        journal = RunJournal(self.root / str(self.request.run_id) / "receipts")
        journal.save(key, CachedHandoff(
            specialist=specialist, evidence=(conflicting,),
        ).model_dump(mode="json"))

        with self.assertRaisesRegex(SourceFailure, "replace an immutable evidence receipt"):
            await self.owner.handoff(delegation, self.request, journal, self.versions)

    async def test_one_schema_correction_precedes_source_execution_and_preserves_real_values(self):
        self.mode = "repair"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING"):
            result = await self.owner.run(self.request)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(result.tables[0].rows[0].values["value"], 75.0)
        payload = json.dumps(self.payloads)
        self.assertIn("invalid_arguments", payload)
        self.assertIn("source_executed", payload)

    async def test_deterministic_answer_contract_does_not_depend_on_model_correction(self):
        self.mode = "answer_contract_repair"

        answer = await self.owner.run(self.request)

        self.assertEqual(answer.tables[0].rows[0].values["value"], 75.0)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual(self.owner.failures, [])

    async def test_irrelevant_evidence_contract_is_corrected_before_source_or_specialist_execution(self):
        self.mode = "irrelevant_evidence_repair"
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "Which turbines have unusual telemetry?",
            "permitted_evidence_kinds": ("equipment", "telemetry"),
        })
        self.seed(request)

        answer = await self.owner.run(request)

        self.assertEqual(answer.tables[0].rows[0].values["value"], 75.0)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual(self.tools.calls, 1)

    async def test_missing_required_evidence_is_compiled_before_source_or_specialist_execution(self):
        self.mode = "missing_evidence_repair"
        self.tools.quality_snapshot = True
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "Which turbines have latest signal quality BAD today?",
            "required_evidence_kinds": ("signal_quality",),
            "permitted_evidence_kinds": ("equipment", "telemetry", "signal_quality"),
        })
        self.seed(request)

        answer = await self.owner.run(request)

        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertTrue(any(
            item.tool == "query_signal_quality_snapshot" for item in self.owner.evidence.values()
        ))
        assert self.owner.answer_contract is not None
        self.assertIn("signal_quality", {
            item.kind for item in self.owner.answer_contract.requirements
        })
        self.assertIn("signal_quality", {
            item.kind for item in self.owner.answer_contract.views
        })
        self.assertEqual(answer.proposals, ())

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

    async def test_premature_no_draft_uses_bounded_correction_without_poisoning_the_run(self):
        self.mode = "premature_no_draft"
        with self.assertLogs("hydro_orchestrator.foundry_supervisor", level="WARNING"):
            result = await self.owner.run(self.request)
        review = next(item for item in result.specialists if item.role == "work-order")
        self.assertEqual(review.report["decision"], "needs_clarification")
        self.assertFalse(self.owner.failures)

    async def test_deterministic_plan_preserves_final_verification_handoff(self):
        self.mode = "skip_verify"
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.tools.calls, 1)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertTrue(any(
            item.get("role") == "supervisor" and "Deterministic evidence verification" in str(item.get("detail", ""))
            for item in self.events
        ))

    async def test_missing_ingress_manifest_is_deterministically_recovered_before_execution(self):
        self.mode = "plan_repair"
        request = self.request.model_copy(update={
            "question": "Read telemetry, investigate concerns, and prepare an editable work-order draft.",
            "required_specialists": (),
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order"])
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual(self.calls["hydro-qa-agent"], 2)
        self.assertEqual(
            RunJournal(self.root / str(request.run_id) / "receipts").read("executor_intent")["required_specialists"],
            ["qa", "rca", "work-order"],
        )


    async def test_source_argument_repair_does_not_consume_structured_completion_repair(self):
        self.mode = "repair_query_and_report"
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(self.calls["hydro-rca-agent"], 4)

    async def test_batched_handoffs_preserve_dependencies_without_supervisor_model_calls(self):
        self.mode = "batch_handoffs"
        result = await self.owner.run(self.request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual(self.calls["hydro-rca-agent"], 1)
        self.assertEqual(self.calls["hydro-work-order-agent"], 1)
        self.assertEqual(self.tools.calls, 1)
        self.assertEqual(len(result.tables), 1)
        self.assertEqual(sum("agent_reference" not in payload for payload in self.payloads), 0)
        self.assertEqual(result.specialists[1].model_round_count, 1)
        self.assertEqual(result.specialists[2].model_round_count, 1)
        audit = RunJournal(self.root / str(self.request.run_id) / "receipts").read("evidence")
        self.assertIsNone(audit["supervisor"]["model_round_count"])
        graph = RunJournal(self.root / str(self.request.run_id) / "receipts").read("workflow_graph")
        self.assertEqual(graph["format"], "mermaid")
        self.assertIn("chief_orchestration", graph["definition"])
        self.assertIn("evidence_verification", graph["definition"])
        self.assertIn("grounded_presentation", graph["definition"])
        initial_calls = [payload for payload in self.payloads
                         if payload.get("agent_reference", {}).get("name") == "hydro-qa-agent"]
        contexts = [json.loads(payload["input"][0]["content"][0]["text"]) for payload in initial_calls]
        self.assertEqual([item["role"] for item in contexts[-1]["completed_specialists"]],
                         ["qa", "rca", "work-order"])
        rca_payload = next(payload for payload in self.payloads
                           if payload.get("agent_reference", {}).get("name") == "hydro-rca-agent")
        rca_context = json.loads(rca_payload["input"][0]["content"][0]["text"])
        self.assertEqual(rca_context["allowed_evidence_ids"], ["reading-1"])
        self.assertEqual(rca_context["allowed_evidence_references"],
                         ["reading-1:/rows/0"])
        self.assertIn("without a /result prefix",
                      rca_context["completion_constraints"]["complete_rca_assessment"]["references"])

    async def test_post_draft_fault_review_uses_deterministic_supervisor_plan_and_runs_rca(self):
        self.mode = "direct_rca"
        previous = HistoricalContext(
            run_id=uuid4(), source=SOURCE, question="Prepare an inspection draft.",
            rendered_answer="Review work-order draft\nBackend-grounded human approval",
            requested_at=utc_now() - timedelta(minutes=1),
            proposal_ids=(uuid4(),),
        )
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "Investigate whether any physical fault was established. Show the evidence gaps.",
            "previous_run_id": previous.run_id,
            "historical_context": previous,
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual([item.role for item in result.specialists], ["rca"])
        self.assertTrue(any(
            item.get("role") == "supervisor" and "Deterministic evidence verification" in str(item.get("detail", ""))
            for item in self.events
        ))
        self.assertFalse(result.proposals)
        self.assertEqual(
            result.specialists[0].report["maintenance_follow_up"],
            {
                "decision": "not_supported",
                "reason": "missing_issue_evidence",
                "equipment_ids": [],
                "evidence": [],
            },
        )
        audit = RunJournal(self.root / str(request.run_id) / "receipts").read("evidence")
        self.assertIsNone(audit["supervisor"]["model_round_count"])
        self.assertTrue(audit["supervisor"]["response_id"].startswith("deterministic-orchestrator:"))

    def test_only_current_explicit_draft_intent_authorizes_a_new_proposal(self):
        for question in (
            "Prepare one editable Low-priority inspection work-order draft.",
            "Create a work order for T005.",
            "Stage an inspection draft only if an uncovered issue is supported.",
            "I need a WO for T005.",
            "Read BAD quality, investigate and propose a T005 signal inspection; verify.",
        ):
            self.assertTrue(requests_new_work_order_proposal(question), question)
        for question in (
            "Investigate whether any physical fault was established. Keep the draft separate from saved work "
            "and do not submit it.",
            "Prepare a concise unsent notification. Do not save a work order.",
            "Do not create a work order.",
            "Review the prior draft and show the evidence gaps.",
        ):
            self.assertFalse(requests_new_work_order_proposal(question), question)

    async def test_post_draft_fault_review_removes_proposal_tool_from_current_turn(self):
        self.mode = "direct_rca"
        self.tools.stage = True
        previous = HistoricalContext(
            run_id=uuid4(), source=SOURCE,
            question="Prepare one editable inspection work-order draft.",
            rendered_answer="Review work-order draft\nBackend-grounded human approval",
            requested_at=utc_now() - timedelta(minutes=1),
            proposal_ids=(uuid4(),),
        )
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "Investigate whether any physical fault was established. Keep the draft separate from "
                        "saved work and do not submit it. Show the evidence gaps.",
            "previous_run_id": previous.run_id,
            "historical_context": previous,
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertFalse(result.proposals)
        rca_payload = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-rca-agent"
        )
        context = json.loads(rca_payload["input"][0]["content"][0]["text"])
        self.assertNotIn(
            "propose_work_order",
            [tool["name"] for tool in context["permitted_source_catalog"]["tools"]],
        )
        self.assertIn("does not request a new proposal", context["proposal_authorization"])

    async def test_terse_equipment_rca_uses_deterministic_supervisor_plan(self):
        self.mode = "direct_rca"
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "RCA for T010",
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual([item.role for item in result.specialists], ["rca"])

    async def test_multi_quality_snapshot_uses_deterministic_supervisor_plan_and_verified_rows(self):
        self.mode = "quality_snapshot"
        self.tools.quality_snapshot = True
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "Which turbines had BAD or UNCERTAIN telemetry quality in the last 6 hours?",
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual(self.calls["hydro-qa-agent"], 2)
        self.assertEqual(self.tools.calls, 2)
        self.assertEqual([item.role for item in result.specialists], ["qa"])
        self.assertEqual(len(result.tables), 1)
        self.assertEqual(
            {row.values["quality"] for table in result.tables for row in table.rows},
            {"BAD", "UNCERTAIN"},
        )
        self.assertEqual(
            sum("model" in payload and "agent_reference" not in payload for payload in self.payloads),
            0,
        )

    async def test_empty_multi_quality_snapshot_returns_certified_zero_rows(self):
        self.mode = "empty_quality_snapshot"
        self.tools.quality_snapshot = True
        self.tools.empty_quality_snapshot = True
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "Which turbines had BAD or UNCERTAIN telemetry quality in the last 6 hours?",
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual(self.tools.calls, 2)
        self.assertEqual(len(result.tables), 1)
        self.assertEqual(result.tables[0].title, "Signal Quality")
        self.assertEqual(result.tables[0].rows, ())
        self.assertIn("no matching records", result.summary.lower())
        self.assertTrue(any("90 expected signals; 90 had no reading" in item for item in result.limitations))
        self.assertFalse(any("model" in payload and "agent_reference" not in payload for payload in self.payloads))

    async def test_conditional_steps_skip_when_prior_source_selects_no_equipment(self):
        self.mode = "conditional_empty"
        self.tools.quality_snapshot = True
        self.tools.empty_quality_snapshot = True
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": (
                "Select the turbine with the most BAD signals, breaking ties by equipment ID. Investigate the selected "
                "equipment and prepare an editable inspection draft only for a verified uncovered issue. Finally "
                "independently verify the result. Keep this read-only."
            ),
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "qa"])
        self.assertNotIn("hydro-rca-agent", self.calls)
        self.assertNotIn("hydro-work-order-agent", self.calls)
        self.assertIn("no equipment could be selected", result.summary.lower())
        chief_events = [event for event in self.events if event["role"] == "supervisor"]
        self.assertTrue(any("conditional specialist step was skipped" in event["detail"].lower()
                            for event in chief_events))


    async def test_empty_independent_population_does_not_skip_sibling_population(self):
        self.mode = "independent_empty"
        self.tools.quality_snapshot = True
        self.tools.empty_quality_snapshot = True
        self.tools.include_identity = True
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": "Independently verify the signal-quality population and the selected equipment telemetry.",
            "required_specialists": ("qa",),
            "required_specialist_steps": {"qa": 2},
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertNotIn("hydro-supervisor-agent", self.calls)
        self.assertEqual([item.role for item in result.specialists], ["qa", "qa"])
        self.assertEqual(self.tools.calls, 2)
        self.assertEqual({item.tool for item in self.owner.evidence.values()}, {
            "query_signal_quality_snapshot", "query_telemetry",
        })
        equipment_table = next(
            table for table in result.tables
            if table.rows and "equipment_id" in table.rows[0].values
        )
        self.assertEqual(equipment_table.rows[0].values["equipment_id"], "TEST_T005")

    async def test_conditional_work_skips_without_verified_uncovered_issue(self):
        self.mode = "conditional_no_issue"
        self.tools.include_identity = True
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": (
                "Read telemetry and investigate it. Prepare an editable inspection draft only for a verified "
                "uncovered issue. Finally independently verify the result. Keep this read-only."
            ),
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "qa"])
        self.assertNotIn("hydro-work-order-agent", self.calls)
        chief_events = [event for event in self.events if event["role"] == "supervisor"]
        self.assertTrue(any("rca did not verify an uncovered equipment issue" in event["detail"].lower()
                            for event in chief_events))

    async def test_conditional_work_runs_for_source_referenced_uncovered_issue(self):
        self.mode = "conditional_verified_issue"
        self.tools.include_identity = True
        request = self.request.model_copy(update={
            "run_id": uuid4(),
            "question": (
                "Read telemetry and investigate it. Prepare an editable inspection draft only for a verified "
                "uncovered issue. Finally independently verify the result. Keep this read-only."
            ),
        })
        self.seed(request)
        result = await self.owner.run(request)
        self.assertEqual([item.role for item in result.specialists], ["qa", "rca", "work-order", "qa"])
        self.assertIn("hydro-work-order-agent", self.calls)
        work_order_payload = next(
            payload for payload in self.payloads
            if payload.get("agent_reference", {}).get("name") == "hydro-work-order-agent"
        )
        self.assertIn("conditional_maintenance_gate", json.dumps(work_order_payload))
        self.assertIn("Immediately call propose_work_order once", json.dumps(work_order_payload))
        rca = next(item for item in result.specialists if item.role == "rca")
        self.assertEqual(rca.report["maintenance_follow_up"]["decision"], "verified_uncovered_issue")




if __name__ == "__main__":
    unittest.main()
