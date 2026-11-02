import copy
import unittest

from jsonschema import Draft202012Validator

from hydro_orchestrator.foundry_supervisor import (
    FoundrySupervisor, SourceCatalog, SPECIALIST_MAX_OUTPUT_TOKENS, SpecialistResult, ToolEvidence,
    _encoded, model_catalog, model_tool_schema, rca_reference_examples,
)
from hydro_orchestrator.contracts import utc_now
from hydro_orchestrator.live_sources import NodeSourceBridge
from test_durable_service import SOURCE


class TokenEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_role_catalog_is_smaller_without_losing_authorized_columns_or_semantics(self):
        catalog = SourceCatalog.model_validate(await NodeSourceBridge().call({"action": "source_contracts"}))
        original = copy.deepcopy(catalog.model_dump(mode="json"))
        projected = model_catalog(catalog)
        self.assertLess(len(_encoded(projected)), len(_encoded(original)) * 0.97)
        self.assertEqual(projected["context"], original["context"])

        work_catalog = catalog.model_copy(update={
            "tools": tuple(tool for tool in catalog.tools if tool.name in {
                "query_assets", "query_operations", "query_work_backlog", "query_telemetry",
                "query_signal_quality_snapshot", "query_turbine_temperature_snapshot", "propose_work_order",
            }),
        })
        work = model_catalog(work_catalog, include_datasets=False)
        before = work_catalog.model_dump(mode="json")
        self.assertLess(len(_encoded(work)), len(_encoded(before)) * 0.75)
        self.assertNotIn("weather_entities", work["context"])
        self.assertNotIn("datasets", work["context"])
        for key in ("asset_entities", "operations_entities", "semantics"):
            self.assertEqual(work["context"][key], before["context"][key])
        self.assertEqual(catalog.model_dump(mode="json"), original)

        for tool in catalog.tools:
            compact = model_tool_schema(tool.parameters)
            Draft202012Validator.check_schema(compact)
            if tool.name not in ("query_assets", "query_operations", "query_weather"):
                continue
            group = {"query_assets": "asset_entities", "query_operations": "operations_entities",
                     "query_weather": "weather_entities"}[tool.name]
            for entity in catalog.context[group]:
                for column in (entity["columns"][0]["name"], "NOT_A_DECLARED_COLUMN"):
                    arguments = {"entity": entity["key"], "columns": [column]}
                    self.assertEqual(
                        Draft202012Validator(tool.parameters).is_valid(arguments),
                        Draft202012Validator(compact).is_valid(arguments),
                        (tool.name, entity["key"], column),
                    )

    def test_schema_factoring_preserves_validation_of_filter_columns_and_does_not_mutate_input(self):
        enum = {"enum": [f"canonical_column_{index}" for index in range(20)]}
        schema = {
            "type": "object",
            "properties": {"column": enum, "value_column": enum},
            "required": ["column"], "additionalProperties": False,
        }
        original = copy.deepcopy(schema)
        compact = model_tool_schema(schema)
        self.assertLess(len(_encoded(compact)), len(_encoded(schema)))
        for arguments in (
            {"column": "canonical_column_0"},
            {"column": "canonical_column_19", "value_column": "canonical_column_1"},
            {"column": "unknown"}, {"column": "canonical_column_0", "value_column": "unknown"},
            {"column": "canonical_column_0", "extra": True}, {},
        ):
            self.assertEqual(
                Draft202012Validator(schema).is_valid(arguments),
                Draft202012Validator(compact).is_valid(arguments),
            )
        self.assertEqual(schema, original)

    def test_catalog_pruning_keeps_unknown_context_and_tools_cannot_gain_access(self):
        catalog = SourceCatalog(
            tools=(),
            context={"weather_entities": [{"key": "weather_forecasts"}],
                     "operator_extension": {"policy": "retain this"},
                     "datasets": [{"entity": "weather_forecasts"}, {"entity": "native_claims"}]},
        )
        projected = model_catalog(catalog)
        self.assertEqual(projected["tools"], [])
        self.assertEqual(projected["context"]["operator_extension"], {"policy": "retain this"})
        self.assertEqual(projected["context"]["datasets"], [{"entity": "native_claims"}])
        self.assertIn("weather_entities", catalog.context)

    def test_two_prompts_reserve_at_least_one_third_less_completion_capacity(self):
        roles = ("qa", "qa", "rca", "work-order", "qa", "qa")
        previous = 2 * len(roles) * 4096
        current = 2 * sum(SPECIALIST_MAX_OUTPUT_TOKENS[role] for role in roles)
        self.assertLessEqual(current, previous * 2 / 3)
        self.assertGreaterEqual(SPECIALIST_MAX_OUTPUT_TOKENS["rca"], 3072)
        self.assertGreaterEqual(SPECIALIST_MAX_OUTPUT_TOKENS["work-order"], 3072)

    def test_projected_work_evidence_keeps_original_json_pointer_indices(self):
        owner = object.__new__(FoundrySupervisor)
        rows = [{"equipment_id": "OTHER", "quality": "GOOD"},
                {"equipment_id": "TARGET", "quality": "BAD"}]
        receipt = ToolEvidence(
            id="read-1", source=SOURCE, tool="query_signal_quality_snapshot", arguments={},
            completed_at=utc_now(), result={"rows": rows, "row_count": 2},
            row_identities={"/rows/0": {"equipment_id": "OTHER"}, "/rows/1": {"equipment_id": "TARGET"}},
            resolved_equipment_ids=("OTHER", "TARGET"), work_coverage_equipment_ids=("OTHER", "TARGET"),
        )
        owner.evidence, owner.specialists, owner.carried_ids = {receipt.id: receipt}, [], set()
        owner.selected_equipment_ids = {"TARGET"}
        projected = owner.model_evidence("work-order")
        self.assertEqual(projected[0]["result"]["rows"], [None, rows[1]])
        self.assertEqual(projected[0]["result"]["projected_row_count"], 1)
        self.assertEqual(receipt.result["rows"], rows)

    def test_native_fixer_handoff_preserves_the_complete_affected_population_and_citation_indices(self):
        owner = object.__new__(FoundrySupervisor)
        registry = [
            {"equipment_id": f"UNIT_{index}", "opcua_node_id": f"SIGNAL_{index}", "metadata": "catalog " * 200}
            for index in range(20)
        ]
        readings = [{"opcua_node_id": f"SIGNAL_{index}", "quality": "BAD", "value": index} for index in range(20)]
        work = [{"equipmentId": f"UNIT_{index}", "workOrderNumber": f"WO-{index}"} for index in range(20)]
        receipt = ToolEvidence(
            id="native-1", source=SOURCE, tool="fabriciq-data-agent", arguments={},
            completed_at=utc_now(), resolved_equipment_ids=tuple(f"UNIT_{index}" for index in range(20)),
            result={"native_table_0": registry, "native_table_1": readings, "native_table_2": work,
                    "affected_signals": [{"equipment_id": "UNIT_7"}],
                    "response": "Original native answer.", "population": {"inventory_complete": True}},
        )
        report = {"observations": [{"evidence_id": receipt.id, "path": "/native_table_0/7/equipment_id"}]}
        owner.evidence, owner.carried_ids, owner.selected_equipment_ids = {receipt.id: receipt}, set(), set()
        owner.specialists = [SpecialistResult(
            role="rca", agent_name="hydro-rca-agent", version="1", response_id="rca-1",
            duration_ms=1, input_digest="a" * 64, text="", report=report,
        )]
        original = copy.deepcopy(receipt.result)
        projected = owner.model_evidence("work-order")[0]
        self.assertLess(len(_encoded(projected)), len(_encoded(receipt.receipt())) * 0.3)
        for field in ("native_table_0", "native_table_1", "native_table_2"):
            self.assertEqual(projected["result"][field], [None] * 7 + [original[field][7]])
            self.assertEqual(projected["result"]["projected_row_counts"][field], 1)
        self.assertEqual(projected["resolved_equipment_ids"], ("UNIT_7",))
        self.assertEqual(projected["result"]["population"], original["population"])
        self.assertEqual(projected["result"]["response"], original["response"])
        self.assertEqual(receipt.result, original)
        self.assertIn("native-1:/native_table_1/0", rca_reference_examples(owner.evidence))

        receipt.result["affected_signals"].append({"equipment_id": "UNIT_9"})
        projected = owner.model_evidence("work-order")[0]
        self.assertEqual(projected["result"]["native_table_0"][9], registry[9])
        self.assertEqual(projected["result"]["native_table_1"][9], readings[9])
        self.assertEqual(projected["resolved_equipment_ids"], ("UNIT_7", "UNIT_9"))

        del receipt.result["affected_signals"]
        projected = owner.model_evidence("work-order")[0]
        self.assertEqual(projected["result"]["native_table_0"], registry,
                         "Citations alone cannot narrow an unspecified population.")

        owner.selected_equipment_ids = {"UNIT_7"}
        receipt.result["unknown_identity"] = [{"equipment_id": {"not": "a canonical string"}}]
        receipt.result["native_table_2"].append({"note": "Unresolved source row; retain it."})
        projected = owner.model_evidence("work-order")[0]
        self.assertEqual(projected["result"]["unknown_identity"], receipt.result["unknown_identity"])
        self.assertEqual(projected["result"]["native_table_2"][20],
                         {"note": "Unresolved source row; retain it."})
