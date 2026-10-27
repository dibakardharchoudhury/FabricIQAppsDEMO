import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from datetime import timedelta

from pydantic import ValidationError
from jsonschema import Draft202012Validator

from hydro_orchestrator.contracts import utc_now
from hydro_orchestrator.foundry_supervisor import (
    ChatRequest, FoundrySupervisor, NativeBinding, RunJournal, SemanticDecision, SourceCatalog,
    strict_output_schema, semantic_output_schema,
)
from hydro_orchestrator.live_sources import SourceFailure
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
        api = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)))
        client.get_openai_client = lambda: api
        client.__aenter__.return_value = client
        with patch("hydro_orchestrator.foundry_supervisor.BoundedAIProjectClient", return_value=client):
            first = await self.owner.plan_semantically(self.request, self.journal)
            second = await self.owner.plan_semantically(self.request, self.journal)
        self.assertEqual(first, second)
        api.responses.create.assert_awaited_once()
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

    async def test_explicit_native_engine_cannot_be_silently_removed(self):
        self.owner.native_binding = NativeBinding(source=SOURCE, connections={"data-agent": "verified"})
        response = SimpleNamespace(status="completed", id="resp_plan", output_text=json.dumps(decision()))
        client = AsyncMock()
        api = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)))
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