import copy
import json
import unittest
from unittest.mock import patch
from email.message import Message
from io import BytesIO

from MasterDataManagement.contract import ContractError, fingerprint, load_approved, semantic_definition, validate, verify_schema
from MasterDataManagement.fabric_runner import read_live_ontology, run_release
from MasterDataManagement.build_artifacts import build_notebook, build_pipeline

WORKSPACE = "10000000-0000-0000-0000-000000000001"
LAKEHOUSE = "10000000-0000-0000-0000-000000000002"


def fixture():
    prop = lambda name, strategy="require_equal", priority=None: {
        "name": name, "ontologyProperty": name, "label": name.replace("_", " ").title(), "type": "string",
        "required": True, "strategy": strategy, "priority": priority or [],
    }
    return {
        "formatVersion": 1, "name": "Customer product",
        "ontology": {"workspaceId": WORKSPACE, "itemId": LAKEHOUSE, "generation": 2, "definitionHash": "a" * 64},
        "entities": [{"id": "customer", "ontologyEntity": "Customer", "label": "Customer", "goldTable": "customer",
                      "key": "customer_id", "properties": [prop("customer_id"), prop("name", "source_priority", ["crm", "erp"])]}],
        "sources": [
            {"id": sid, "label": sid, "entity": "customer", "workspaceId": WORKSPACE, "lakehouseId": LAKEHOUSE,
             "schema": "silver", "table": f"{sid}_customer", "keyColumn": "customer_id", "keyNamespace": "enterprise_customer",
             "columns": [{"name": "customer_id", "type": "string"}, {"name": "name", "type": "string"}]}
            for sid in ("crm", "erp")
        ],
        "mappings": [
            {"source": sid, "column": column, "target": column, "transform": "identity", "reason": "Conformed in Silver.", "reviewed": True}
            for sid in ("crm", "erp") for column in ("customer_id", "name")
        ],
        "relationships": [],
    }


class ContractTests(unittest.TestCase):
    def test_hash_is_independently_required_and_any_edit_invalidates_it(self):
        contract = fixture()
        digest = fingerprint(contract)
        self.assertEqual(load_approved(json.dumps(contract), digest), contract)
        with self.assertRaisesRegex(ContractError, "separately approved"):
            load_approved(json.dumps(contract), "")
        contract["mappings"][0]["reason"] = "Edited after approval"
        with self.assertRaisesRegex(ContractError, "differs"):
            load_approved(json.dumps(contract), digest)

    def test_duplicate_json_fields_and_nonfinite_constants_are_rejected(self):
        with self.assertRaisesRegex(ContractError, "Duplicate"):
            load_approved('{"a":1,"a":2}', "a" * 64)
        with self.assertRaisesRegex(ContractError, "Invalid JSON constant"):
            load_approved('{"a":NaN}', "a" * 64)

    def test_schema_drift_blocks_add_remove_and_retype_even_excluded_fields(self):
        source = fixture()["sources"][0]
        verify_schema(source, {"customer_id": "string", "name": "string"})
        for observed in ({"customer_id": "string"}, {"customer_id": "string", "name": "boolean"},
                         {"customer_id": "string", "name": "string", "new_column": "string"}):
            with self.assertRaisesRegex(ContractError, "schema drift"):
                verify_schema(source, observed)

    def test_unsafe_ambiguous_unreviewed_and_legacy_contracts_fail(self):
        mutations = [
            lambda c: c["sources"][0].update(table="customers; DROP TABLE gold"),
            lambda c: c["sources"][0].update(keyNamespace="crm_local_id"),
            lambda c: c["mappings"][0].update(transform="lower"),
            lambda c: c["mappings"][0].update(reviewed=False),
            lambda c: c["mappings"][0].update(reason=""),
            lambda c: c["mappings"].append(copy.deepcopy(c["mappings"][0])),
            lambda c: c["entities"][0]["properties"][1].update(priority=["crm"]),
            lambda c: c["ontology"].update(generation=1),
            lambda c: c.update(formatVersion=True),
            lambda c: c["sources"][0]["columns"].append({"name": "new_column", "type": "string"}),
            lambda c: c["entities"][0]["properties"][1].update(label="Injected\n\tpartition evil"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                contract = fixture()
                mutation(contract)
                with self.assertRaises(ContractError):
                    validate(contract)

    def test_semantic_model_is_bound_to_release_and_escapes_labels(self):
        contract = fixture()
        contract["entities"][0]["label"] = "Customer's master"
        digest = fingerprint(contract)
        parts = semantic_definition(contract, digest, WORKSPACE, LAKEHOUSE)
        self.assertIn("table 'Customer''s master'", parts["definition/tables/customer.tmdl"])
        self.assertIn(f"entityName: mdm_customer_{digest[:16]}", parts["definition/tables/customer.tmdl"])
        self.assertIn("mode: directLake", parts["definition/tables/customer.tmdl"])
        self.assertIn(f"/{WORKSPACE}/{LAKEHOUSE}", parts["definition/expressions.tmdl"])
        self.assertIn("compatibilityLevel: 1702", parts["definition/database.tmdl"])
        self.assertNotIn("relationships.tmdl", parts)

    def test_no_spark_read_or_write_before_hash_runtime_identity_and_ontology_check(self):
        contract = fixture()
        spark = unittest.mock.Mock()
        with self.assertRaisesRegex(ContractError, "differs"):
            run_release(spark, json.dumps(contract), "b" * 64, WORKSPACE, LAKEHOUSE, {}, "token")
        with self.assertRaisesRegex(ContractError, "bound"):
            run_release(spark, json.dumps(contract), fingerprint(contract), WORKSPACE, LAKEHOUSE, {}, "token")
        context = {"defaultLakehouseId": LAKEHOUSE, "defaultLakehouseWorkspaceId": WORKSPACE}
        with patch("MasterDataManagement.fabric_runner.read_live_ontology", side_effect=ContractError("offline")):
            with self.assertRaisesRegex(ContractError, "offline"):
                run_release(spark, json.dumps(contract), fingerprint(contract), WORKSPACE, LAKEHOUSE, context, "token")
        self.assertEqual(spark.mock_calls, [])

    def test_artifacts_have_bound_lakehouse_valid_cells_and_no_freely_overridden_approval_parameter(self):
        digest = fingerprint(fixture())
        notebook = build_notebook(WORKSPACE, LAKEHOUSE, "Gold Lakehouse", digest)
        binding = notebook["metadata"]["dependencies"]["lakehouse"]
        self.assertEqual(binding["default_lakehouse"], LAKEHOUSE)
        self.assertEqual(binding["default_lakehouse_workspace_id"], WORKSPACE)
        for cell in notebook["cells"]:
            self.assertIn("metadata", cell)
            if cell["cell_type"] == "code":
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])
                compile("".join(cell["source"]), "<notebook>", "exec")
        activity = build_pipeline(WORKSPACE, LAKEHOUSE)["properties"]["activities"][0]
        self.assertEqual(activity["policy"]["retry"], 0)
        self.assertEqual(activity["typeProperties"]["parameters"], {})
        self.assertIn(digest, "".join(notebook["cells"][1]["source"]))

    def test_live_v2_read_checks_generation_fingerprint_lro_origin_and_skill_header(self):
        parts = [{"path": "entities/Customer.tmdl", "payload": "ZW50aXR5IEN1c3RvbWVy", "payloadType": "InlineBase64"}]
        ontology = {**fixture()["ontology"], "definitionHash": fingerprint(parts)}
        operation = "https://api.fabric.microsoft.com/v1/operations/test"

        class Response(BytesIO):
            def __init__(self, value, status=200, location=None):
                super().__init__(json.dumps(value).encode())
                self.status, self.headers = status, Message()
                if location:
                    self.headers["Location"] = location

        def verify_responses(responses, expected_error=None):
            with patch("MasterDataManagement.fabric_runner.urllib.request.build_opener") as factory:
                factory.return_value.open.side_effect = responses
                if expected_error:
                    with self.assertRaisesRegex(ContractError, expected_error):
                        read_live_ontology(ontology, "not-a-real-token")
                else:
                    read_live_ontology(ontology, "not-a-real-token")
                for call in factory.return_value.open.call_args_list:
                    self.assertEqual(call.args[0].get_header("X-ms-fabric-skill"), "e2e-medallion-architecture")
                return factory.return_value.open.call_count

        count = verify_responses([
            Response({"properties": {"generation": 2}}), Response({}, 202, operation),
            Response({"status": "Succeeded"}), Response({"definition": {"parts": parts}}),
        ])
        self.assertEqual(count, 4)
        for generation in (1, "2", True, None):
            verify_responses([Response({"properties": {"generation": generation}})], "numeric generation 2")
        verify_responses([Response({"properties": {"generation": 2}}),
                          Response({"definition": {"parts": [{**parts[0], "payload": "changed"}]}})], "changed since")
        verify_responses([Response({"properties": {"generation": 2}}), Response({}, 202, "https://untrusted.example/operation")], "Untrusted")


if __name__ == "__main__":
    unittest.main()
