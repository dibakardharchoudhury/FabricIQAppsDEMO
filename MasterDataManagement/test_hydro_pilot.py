import ast
import unittest

from .hydro_pilot import CONFORMANCE_CODE, SOURCE_SCHEMAS, build_draft, build_notebook


class HydroPilotTests(unittest.TestCase):
    def test_notebook_is_bound_and_valid_python(self):
        source, target, workspace = "1" * 8 + "-1111-1111-1111-" + "1" * 12, "2" * 8 + "-2222-2222-2222-" + "2" * 12, "3" * 8 + "-3333-3333-3333-" + "3" * 12
        notebook = build_notebook(workspace, source, workspace, target, "Concord_MDM_Pilot")
        self.assertEqual(notebook["metadata"]["dependencies"]["lakehouse"]["default_lakehouse"], target)
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])
                ast.parse("".join(cell["source"]))

    def test_original_lakehouse_cannot_be_target(self):
        guid = "11111111-1111-1111-1111-111111111111"
        with self.assertRaises(ValueError):
            build_notebook(guid, guid, guid, guid, "Original")

    def test_expected_schema_and_non_destructive_conformance_are_explicit(self):
        self.assertEqual(SOURCE_SCHEMAS["equipment"]["criticality"], "int")
        self.assertEqual(SOURCE_SCHEMAS["equipment"]["install_date"], "date")
        self.assertIn('option("versionAsOf", version)', CONFORMANCE_CODE)
        self.assertIn('cast("long")', CONFORMANCE_CODE)
        self.assertIn('date_format("install_date", "yyyy-MM-dd")', CONFORMANCE_CODE)
        self.assertIn('mode("errorifexists")', CONFORMANCE_CODE)
        self.assertNotIn('mode("overwrite")', CONFORMANCE_CODE)
        self.assertNotIn("DROP ", CONFORMANCE_CODE)
        self.assertNotIn("saveAsTable(table)", CONFORMANCE_CODE[:CONFORMANCE_CODE.index('spark.sql("CREATE SCHEMA')])

    def test_generated_draft_cannot_be_mistaken_for_approval(self):
        guid = "11111111-1111-1111-1111-111111111111"
        schemas = {name: dict(fields) for name, fields in SOURCE_SCHEMAS.items()}
        schemas["equipment"].update(criticality="bigint", install_date="string")
        receipt = {
            "status": "silver_verified", "data_classification": "hosted_hydro_demo",
            "target_workspace_id": guid, "target_lakehouse_id": guid,
            "schemas": schemas, "counts": {name: 1 for name in schemas},
        }
        ontology = {"workspaceId": guid, "itemId": guid, "generation": 2, "definitionHash": "a" * 64}
        draft = build_draft(receipt, ontology)
        self.assertTrue(all(not mapping["reviewed"] for mapping in draft["mappings"]))
        date = next(mapping for mapping in draft["mappings"] if mapping["column"] == "install_date")
        self.assertIsNone(date["target"])
        self.assertEqual(len(draft["relationships"]), 2)
        receipt["status"] = "not_verified"
        with self.assertRaises(ValueError):
            build_draft(receipt, ontology)


if __name__ == "__main__":
    unittest.main()
