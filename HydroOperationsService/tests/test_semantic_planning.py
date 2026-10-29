import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from datetime import timedelta
from time import perf_counter

from azure.identity.aio import AzureCliCredential
from pydantic import ValidationError
from jsonschema import Draft202012Validator

from hydro_orchestrator.contracts import SourceIdentity, utc_now
from hydro_orchestrator.foundry_supervisor import (
    ChatRequest, FoundrySupervisor, NativeBinding, RunJournal, SemanticDecision, SourceCatalog,
    strict_output_schema, semantic_output_schema,
)
from hydro_orchestrator.live_sources import NodeSourceBridge, SourceFailure
from test_durable_service import SOURCE


def decision(role="qa", kind="facilities", reader=True):
    native = role == "fabric-iq"
    return {
        "mode": "execute", "reason": "Use the minimum governed capability for this task.",
        "clarification": None, "proposal_requested": False, "charts_requested": False,
        "proposal_priority": "Medium", "equipment_selection": None, "reuse_prior_evidence": False,
        "plan": {
            "steps": [{
                "specialist": role, "question": "List every facility with its canonical ID and name.",
                "reason": "Complete the requested population without unrelated telemetry.",
                "nativeSource": "data-agent" if native else None, "requiresSelection": False,
                "requiresVerifiedIssue": False, "execution": "reader" if reader else "agent",
                "reads": [{
                    "tool": "query_assets", "arguments_json": '{"entity":"facilities"}',
                    "evidence_kind": kind,
                }] if reader else [],
            }],
            "answer_contract": {
                "version": 3,
                "requirements": [{
                    "evidence": f"{kind}_population_{'native' if native else 'direct'}", "purpose": "facts",
                }],
                "views": [{
                    "id": "facilities", "title": "Facilities", "kind": kind, "scope": "population",
                    "presentation": "native_records_table" if native else "direct_records_table",
                }],
            },
        },
    }


def catalog():
    return {
        "tools": [{
            "name": "query_assets", "parameters": {
                "type": "object", "properties": {"entity": {"enum": ["facilities"]}},
                "required": ["entity"], "additionalProperties": False,
            },
        }],
        "context": {
            "domain_contract_version": 3, "datasets": [{
                "key": kind, "source": "native" if kind == "native_claims" else "lakehouse",
                "entity": kind, "identityFields": ["facility_id"],
                "presentations": ["records_table"], "evidenceScopes": ["population"],
                "evidenceAuthorities": ["native"] if kind == "native_claims" else ["direct"],
            } for kind in ("facilities", "native_claims")],
        },
    }


class SemanticPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.request = ChatRequest(
            run_id=uuid4(), source=SOURCE, question="How many sites do we operate?",
            deadline=utc_now() + timedelta(minutes=5),
        )
        self.owner = FoundrySupervisor(
            "https://test.services.ai.azure.com/api/projects/test", AsyncMock(), AsyncMock(),
            AsyncMock(), SOURCE, self.root, analysis_model="test-model", semantic_planning=True,
        )
        self.journal = RunJournal(self.root / "receipts")
        self.journal.save("tool_catalog", catalog())

    async def asyncTearDown(self):
        self.temp.cleanup()

    def test_strict_schema_is_valid_and_requires_every_property(self):
        schema = strict_output_schema(SemanticDecision.model_json_schema(by_alias=True))
        Draft202012Validator.check_schema(schema)
        def verify(value):
            if isinstance(value, dict):
                if "properties" in value:
                    self.assertEqual(set(value["required"]), set(value["properties"]))
                    self.assertIs(value["additionalProperties"], False)
                self.assertNotIn("default", value)
                for child in value.values():
                    verify(child)
            elif isinstance(value, list):
                for child in value:
                    verify(child)
        verify(schema)

    def test_planner_schema_constrains_evidence_to_exact_catalog_identifiers(self):
        schema = semantic_output_schema(SourceCatalog.model_validate(catalog()))
        Draft202012Validator.check_schema(schema)
        evidence = schema["$defs"]["EvidenceRequirement"]["properties"]["evidence"]["enum"]
        self.assertIn("facilities_population_direct", evidence)
        self.assertIn("native_claims_population_native", evidence)
        self.assertNotIn("invented_population_direct", evidence)

    def test_quality_investigation_draft_does_not_require_a_mechanical_rca(self):
        planned = decision("fabric-iq", "native_claims", reader=False)
        planned["proposal_requested"] = True
        planned["plan"]["steps"].append({
            "specialist": "work-order", "question": "Prepare review-only telemetry-quality investigation drafts.",
            "reason": "Use verified quality and work coverage without asserting a mechanical fault.",
            "nativeSource": None, "requiresSelection": False, "requiresVerifiedIssue": False,
            "execution": "agent", "reads": [],
        })
        validated = SemanticDecision.model_validate(planned)
        self.assertEqual([step.specialist for step in validated.plan.steps], ["fabric-iq", "work-order"])
        planned["plan"]["steps"][-1]["requiresVerifiedIssue"] = True
        with self.assertRaisesRegex(ValidationError, "must follow a checked RCA"):
            SemanticDecision.model_validate(planned)

    def test_analysis_is_an_explicit_semantic_choice_not_an_extra_agent_hop(self):
        for reader in (True, False):
            with self.subTest(reader=reader):
                planned = decision(reader=reader)
                self.owner.semantic_decision = SemanticDecision.model_validate(planned)
                self.assertFalse(self.owner.needs_analysis())
                planned["analysis_requested"] = True
                self.owner.semantic_decision = SemanticDecision.model_validate(planned)
                self.assertTrue(self.owner.needs_analysis())

    def test_repeated_fabric_retrieval_cannot_replay_the_same_original_question(self):
        self.owner.native_binding = NativeBinding(source=SOURCE, connections={"data-agent": "verified"})
        planned = decision("fabric-iq", "native_claims", reader=False)
        planned["plan"]["steps"].append(dict(planned["plan"]["steps"][0]))
        with self.assertRaisesRegex(SourceFailure, "original Fabric answer once"):
            self.owner.validate_semantic_plan(
                SemanticDecision.model_validate(planned), SourceCatalog.model_validate(catalog()),
            )

    def test_checked_rca_report_does_not_invoke_another_briefing_model(self):
        from hydro_orchestrator.foundry_supervisor import SpecialistResult
        planned = decision()
        planned["analysis_requested"] = True
        self.owner.semantic_decision = SemanticDecision.model_validate(planned)
        self.owner.specialists.append(SpecialistResult(
            role="rca", agent_name="hydro-rca-agent", version="1", response_id="rca-1",
            duration_ms=1, input_digest="a" * 64, text="", report={"observations": []},
        ))
        self.assertFalse(self.owner.needs_analysis())

    async def test_semantic_intent_not_phrase_classifier_owns_requirements(self):
        self.journal.save("semantic_decision", {"decision": decision()})
        request = await self.owner.plan_semantically(self.request, self.journal)
        self.assertEqual(request.required_evidence_kinds, ("facilities",))
        self.assertEqual(request.required_specialists, ("qa",))
        self.assertEqual(request.native_sources, ())
        self.assertFalse(self.owner.proposal_requested(request))
        self.owner.bridge.call.assert_not_called()
        plan = self.owner.semantic_decision.plan
        self.assertEqual(plan.steps[0].execution, "reader")
        self.assertEqual(plan.steps[0].reads[0].arguments(), {"entity": "facilities"})

    async def test_model_decision_is_journaled_and_not_replanned_on_resume(self):
        response = SimpleNamespace(status="completed", id="resp_plan", output_text=json.dumps(decision()))
        client = AsyncMock()
        api = AsyncMock()
        api.responses.create.return_value = response
        api.__aenter__.return_value = api
        client.get_openai_client = lambda: api
        client.__aenter__.return_value = client
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=client):
            first = await self.owner.plan_semantically(self.request, self.journal)
            second = await self.owner.plan_semantically(self.request, self.journal)
        self.assertEqual(first, second)
        api.responses.create.assert_awaited_once()
        api.__aexit__.assert_awaited_once()
        self.assertEqual(self.journal.read("semantic_decision")["response_id"], "resp_plan")
        self.assertFalse(api.responses.create.call_args.kwargs["store"])

    async def test_sparky_alias_does_not_require_literal_data_agent_in_question(self):
        self.owner.native_binding = NativeBinding(source=SOURCE, connections={"data-agent": "verified"})
        self.journal.save("semantic_decision", {"decision": decision("fabric-iq", "native_claims", False)})
        request = await self.owner.plan_semantically(
            self.request.model_copy(update={"question": "Sparky, which sites do we operate?"}), self.journal,
        )
        self.assertEqual(request.native_sources, ("data-agent",))
        self.assertEqual(request.required_specialists, ())
        self.assertEqual(request.required_evidence_kinds, ("native_claims",))

    async def test_invalid_agent_reads_get_one_correction_before_any_source_execution(self):
        invalid = decision()
        invalid["plan"]["steps"][0]["execution"] = "agent"
        client, api = AsyncMock(), AsyncMock()
        client.__aenter__.return_value = client
        api.__aenter__.return_value = api
        client.get_openai_client = lambda: api
        api.responses.create.side_effect = [
            SimpleNamespace(status="completed", id="resp_invalid", output_text=json.dumps(invalid)),
            SimpleNamespace(status="completed", id="resp_corrected", output_text=json.dumps(decision())),
        ]
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=client):
            first = await self.owner.plan_semantically(self.request, self.journal)
            self.assertEqual(await self.owner.plan_semantically(self.request, self.journal), first)
        self.assertEqual(api.responses.create.await_count, 2)
        api.__aexit__.assert_awaited_once()
        feedback = json.loads(api.responses.create.call_args_list[1].kwargs["input"])["validation_feedback"]
        self.assertEqual(feedback["response_id"], "resp_invalid")
        self.assertIn("Reader steps require explicit reads", feedback["errors"][0]["msg"])
        self.assertEqual(self.journal.read("semantic_decision")["response_id"], "resp_corrected")
        self.assertEqual(self.journal.read("semantic_decision")["model_round_count"], 2)
        self.assertEqual(self.journal.read("semantic_planning_attempt:1")["response_id"], "resp_invalid")
        self.assertEqual(self.journal.read("semantic_planning_attempt:2")["response_id"], "resp_corrected")
        self.owner.bridge.call.assert_not_called()
        self.assertFalse(self.owner.evidence)
        self.assertFalse(self.owner.specialists)

    async def test_unconfigured_planner_fails_before_client_or_source_execution(self):
        self.owner.analysis_model = None
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient") as client:
            with self.assertRaisesRegex(SourceFailure, "configured planning model"):
                await self.owner.plan_semantically(self.request, self.journal)
        client.assert_not_called()
        self.owner.bridge.call.assert_not_called()
        self.assertIsNone(self.journal.read("semantic_decision"))

    async def test_repeated_invalid_plan_fails_closed_after_one_correction(self):
        invalid = decision()
        invalid["plan"]["steps"][0]["reads"][0]["arguments_json"] = '{"entity":"secrets"}'
        client, api = AsyncMock(), AsyncMock()
        client.__aenter__.return_value = client
        api.__aenter__.return_value = api
        client.get_openai_client = lambda: api
        api.responses.create.side_effect = [
            SimpleNamespace(status="completed", id=f"resp_invalid_{index}", output_text=json.dumps(invalid))
            for index in (1, 2)
        ]
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=client):
            with self.assertRaisesRegex(SourceFailure, "after one bounded correction"):
                await self.owner.plan_semantically(self.request, self.journal)
        self.assertEqual(api.responses.create.await_count, 2)
        self.assertIsNone(self.journal.read("semantic_decision"))
        self.owner.bridge.call.assert_not_called()
        self.assertFalse(self.owner.evidence)
        self.assertFalse(self.owner.specialists)

    async def test_interrupted_plan_correction_resumes_without_reissuing_committed_attempt(self):
        invalid = decision()
        invalid["plan"]["steps"][0]["execution"] = "agent"
        client, api = AsyncMock(), AsyncMock()
        client.__aenter__.return_value = client
        api.__aenter__.return_value = api
        client.get_openai_client = lambda: api
        api.responses.create.side_effect = [
            SimpleNamespace(status="completed", id="resp_invalid", output_text=json.dumps(invalid)),
            TimeoutError("Interrupted correction"),
        ]
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=client):
            with self.assertRaises(TimeoutError):
                await self.owner.plan_semantically(self.request, self.journal)
            api.responses.create.reset_mock()
            api.responses.create.side_effect = None
            api.responses.create.return_value = SimpleNamespace(
                status="completed", id="resp_resumed", output_text=json.dumps(decision()),
            )
            await self.owner.plan_semantically(self.request, self.journal)
        api.responses.create.assert_awaited_once()
        self.assertEqual(self.journal.read("semantic_decision")["model_round_count"], 2)
        self.assertEqual(self.journal.read("semantic_planning_attempt:1")["response_id"], "resp_invalid")
        self.assertFalse(self.owner.evidence)

    async def test_planner_http_client_closes_on_timeout_without_a_successful_decision(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        api = AsyncMock()
        api.__aenter__.return_value = api
        api.responses.create.side_effect = TimeoutError("Injected model timeout")
        client.get_openai_client = lambda: api
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=client):
            with self.assertRaises(TimeoutError):
                await self.owner.plan_semantically(self.request, self.journal)
        api.__aexit__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()
        self.assertIsNone(self.journal.read("semantic_decision"))

    async def test_explicit_native_engine_cannot_be_silently_removed(self):
        self.owner.native_binding = NativeBinding(source=SOURCE, connections={"data-agent": "verified"})
        response = SimpleNamespace(status="completed", id="resp_plan", output_text=json.dumps(decision()))
        client = AsyncMock()
        api = AsyncMock()
        api.responses.create.return_value = response
        api.__aenter__.return_value = api
        client.get_openai_client = lambda: api
        client.__aenter__.return_value = client
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=client):
            with self.assertRaisesRegex(SourceFailure, "operator-selected native source"):
                await self.owner.plan_semantically(
                    self.request.model_copy(update={"native_sources": ("data-agent",)}), self.journal,
                )

    def test_unknown_dataset_tool_arguments_and_native_binding_fail_closed(self):
        for mutate in ("dataset", "arguments", "binding"):
            raw = decision("fabric-iq", "native_claims", False) if mutate == "binding" else decision()
            if mutate == "dataset":
                raw["plan"]["answer_contract"]["requirements"][0]["evidence"] = "invented_population_direct"
                raw["plan"]["answer_contract"]["views"][0]["kind"] = "invented"
            if mutate == "arguments":
                raw["plan"]["steps"][0]["reads"][0]["arguments_json"] = '{"entity":"secrets"}'
            with self.subTest(mutate=mutate), self.assertRaises(SourceFailure):
                self.owner.validate_semantic_plan(SemanticDecision.model_validate(raw), SourceCatalog.model_validate(catalog()))

    def test_no_proposal_without_work_specialist_and_no_unchecked_issue_gate(self):
        raw = decision()
        raw["proposal_requested"] = True
        with self.assertRaises(ValidationError):
            SemanticDecision.model_validate(raw)
        raw["plan"]["steps"][0].update(specialist="work-order", execution="agent", reads=[],
                                       requiresVerifiedIssue=True)
        with self.assertRaises(ValidationError):
            SemanticDecision.model_validate(raw)

    def test_clarification_has_no_plan_or_sources(self):
        raw = decision()
        raw.update(mode="clarify", plan=None, clarification="Which facility should I investigate?")
        result = SemanticDecision.model_validate(raw)
        self.assertIsNone(result.plan)
        raw["clarification"] = None
        with self.assertRaises(ValidationError):
            SemanticDecision.model_validate(raw)


@unittest.skipUnless(
    os.environ.get("HYDRO_EVALUATE_LIVE_PLANNER") == "1",
    "Opt-in evaluation calls the existing deployed planning model, never operational source tools.",
)
class LiveSemanticArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def test_minimal_capability_selection_and_semantic_boundaries(self):
        endpoint = os.environ["HYDRO_FOUNDRY_PROJECT_ENDPOINT"]
        model = os.environ["HYDRO_FOUNDRY_PLANNING_MODEL"]
        cases = [
            ("facility-facts", "List every facility with its canonical ID and name."),
            ("ontology-rollup", "For every facility, count its open work orders, including any facilities with zero."),
            ("native-alias", "Ask Sparky how work orders, equipment and facilities relate."),
            ("diagnostic-drafts",
             "Which turbines had BAD telemetry quality today, and what work is already open for these? "
             "Prepare editable telemetry-quality investigation drafts for uncovered equipment. Do not save them."),
            ("freshness-drafts",
             "Use the ontology-backed Fabric Data Agent to review the latest telemetry quality and freshness "
             "for turbines T004, T005, T007, T009, and T014. Prepare one editable, review-only diagnostic "
             "investigation work-order draft for each target with stale, missing, BAD or UNCERTAIN telemetry. "
             "Check existing open work first. This concerns data-quality investigation, not mechanical "
             "root-cause analysis or physical repair. Do not save or approve any work orders."),
            ("conditional-repair",
             "Investigate the cause of abnormal turbine temperature and prepare editable repair work orders "
             "only for verified faults not covered by open work. Do not save anything."),
            ("weather-comparison",
             "Compare Aurora and UKMet forecasts for every forecast target over the next 24 hours. "
             "Keep forecast issues and units separate and explain the differences. Do not query turbine telemetry."),
            ("no-new-draft",
             "Show existing work orders. Do not create, prepare or stage any new work-order drafts."),
            ("no-investigation",
             "Only list turbine temperature measurements. Do not investigate causes or draft maintenance."),
            ("ambiguous-action", "Send somebody out to fix it."),
            ("source-free-explanation",
             "Explain the difference between a telemetry-quality observation and a proven physical fault. "
             "Explain the workflow without claiming any current equipment facts."),
        ]
        bridge = NodeSourceBridge()
        try:
            config = await bridge.call({"action": "configuration"})
            source = SourceIdentity(**{key: config[key] for key in (
                "tenant_id", "workspace_id", "ontology_id", "configuration_digest",
            )}, generation=2)
            source_catalog = await bridge.call({"action": "source_contracts"})
            async with AzureCliCredential(tenant_id=str(source.tenant_id), process_timeout=60) as credential:
                await credential.get_token("https://ai.azure.com/.default")
                for case, question in cases:
                    with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        journal = RunJournal(root / "receipts")
                        journal.save("tool_catalog", source_catalog)
                        tools = AsyncMock()
                        owner = FoundrySupervisor(
                            endpoint, credential, bridge, tools, source, root,
                            analysis_model=model, semantic_planning=True,
                            native_binding=NativeBinding(
                                source=source, connections={"data-agent": "hydro-fabric-iq"},
                                toolbox="hydro-fabric-iq-tools",
                            ),
                        )
                        request = ChatRequest(
                            run_id=uuid4(), source=source, question=question,
                            deadline=utc_now() + timedelta(minutes=3),
                        )
                        started = perf_counter()
                        await owner.plan_semantically(request, journal)
                        planned = owner.semantic_decision
                        self.assertIsNotNone(planned)
                        steps = planned.plan.steps if planned.plan else ()
                        roles = [step.specialist for step in steps]
                        print(json.dumps({
                            "case": case, "mode": planned.mode, "roles": roles,
                            "execution": [step.execution for step in steps],
                            "analysis_requested": planned.analysis_requested,
                            "model_round_count": journal.read("semantic_decision")["model_round_count"],
                            "duration_ms": round((perf_counter() - started) * 1000),
                        }), flush=True)
                        self.assertFalse(tools.mock_calls, "Planning must not execute business reads or writes.")
                        if case in ("ontology-rollup", "native-alias"):
                            self.assertEqual(roles, ["fabric-iq"])
                        elif case == "facility-facts":
                            self.assertEqual(roles, ["qa"])
                            self.assertTrue(all(step.execution == "reader" for step in steps))
                            self.assertFalse(planned.analysis_requested)
                        elif case in ("diagnostic-drafts", "freshness-drafts"):
                            self.assertEqual(roles, ["fabric-iq", "work-order"])
                            self.assertTrue(planned.proposal_requested)
                            self.assertFalse(steps[-1].requires_verified_issue)
                        elif case == "conditional-repair":
                            self.assertIn("rca", roles)
                            self.assertIn("work-order", roles)
                            self.assertLess(roles.index("rca"), roles.index("work-order"))
                            self.assertTrue(steps[roles.index("work-order")].requires_verified_issue)
                        elif case == "weather-comparison":
                            self.assertTrue(steps)
                            self.assertEqual(set(roles), {"weather"})
                            self.assertTrue(all(step.execution == "reader" for step in steps))
                            self.assertTrue(planned.analysis_requested)
                        elif case in ("no-new-draft", "no-investigation"):
                            self.assertFalse(planned.proposal_requested)
                            self.assertFalse({"rca", "work-order", "weather"}.intersection(roles))
                            self.assertFalse(planned.analysis_requested)
                        elif case == "ambiguous-action":
                            self.assertEqual(planned.mode, "clarify")
                            self.assertFalse(planned.proposal_requested)
                        else:
                            self.assertEqual(planned.mode, "conversation")
        finally:
            await bridge.aclose()