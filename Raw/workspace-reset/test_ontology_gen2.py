"""Offline contracts; extract pure notebook functions, never execute Spark/API cells."""
import ast
import base64
import copy
import importlib.util
import json
import re
import runpy
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
SUPPORT_PATH = Path(__file__).with_name("ontology_notebook_support.py")
spec = importlib.util.spec_from_file_location("ontology_notebook_support", SUPPORT_PATH)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


def canonical(number):
    return next((ROOT / "Notebooks").glob(f"RTI_{number}*/notebook-content.py"))


def functions(number, names):
    tree = ast.parse(canonical(number).read_text(encoding="utf-8"))
    selected = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            selected[node.name] = node
    missing = set(names) - set(selected)
    if missing:
        raise AssertionError(f"Missing notebook functions: {missing}")
    namespace = dict(vars(support), uuid=uuid, datetime=datetime, timezone=timezone, Optional=Optional)
    for name in names:
        exec(compile(ast.Module(body=[selected[name]], type_ignores=[]), str(canonical(number)), "exec"), namespace)
    return namespace


def roots():
    return [
        support._encode_part(".platform", '{"metadata":{"type":"Ontology","displayName":"demo"}}'),
        support._encode_part("database.tmdl", "database demo\n"),
        support._encode_part("namespaces/default.tmdl", "namespace default\n"),
        support._encode_part("model.tmdl", "model Model\n\nref namespace default\n"),
    ]


def build_namespace():
    ns = functions("004", ["_tag", "_part", "_content", "_tmdl_type", "_build_gen2_parts"])
    ns.update(
        table_schemas=[
            {"entity": "facilities", "columns": [
                {"name": "facility_id", "dataType": "String"}, {"name": "state", "dataType": "Boolean"}]},
            {"entity": "signal_master", "columns": [
                {"name": "opcua_node_id", "dataType": "String"},
                {"name": "facility_id", "dataType": "String"},
                {"name": "state", "dataType": "String"}]},
        ],
        own_pk_map={"facilities": "facility_id", "signal_master": "opcua_node_id"},
        ENTITY_PARENT_MAP={"signal_master": "facilities"},
        SIGNAL_MASTER_ENTITY="signal_master",
        RTI_TIMESERIES_PROPERTIES=[
            {"name": "event_time", "dataType": "DateTime"},
            {"name": "value", "dataType": "Double"},
            {"name": "quality", "dataType": "String"}],
        final_property_type={"state": "DateTime"},
        make_safe_rel_name=lambda source, target: source + "_" + target,
    )
    return ns


def binding_namespace():
    ns = functions("005", ["_tag", "_entity_properties", "_bind_gen2_parts"])
    ns.update(ENTITY_TO_TABLE={"facilities": "silver_facilities", "signal_master": "silver_signal_master"},
              WORKSPACE_ID="workspace-a", LAKEHOUSE_ID="lakehouse-a", lakehouse_name="Lakehouse")
    return ns


def table_columns():
    return {"silver_facilities": {"facility_id", "state"},
            "silver_signal_master": {"opcua_node_id", "facility_id", "state"}}


def built():
    return build_namespace()["_build_gen2_parts"](roots())


def bound():
    return binding_namespace()["_bind_gen2_parts"](built(), table_columns())[0]


def event_namespace():
    ns = functions("006", ["_tmdl_content", "_tmdl_part", "_tmdl_tag", "_tmdl_setting",
                          "_property_block", "_replace_property", "_append_unique_block", "_bind_eventhouse_parts"])
    ns.update(STATIC_ENTITY_NAME="signal_master", entity_path="entities/signal_master.tmdl",
              static_table="signal_master", KQL_TABLE_NAME="OPCUAEvents",
              KEY_COLUMN_NAME="opcua_node_id", TIMESTAMP_COLUMN_NAME="event_time",
              VALUE_COLUMN_NAME="value", QUALITY_COLUMN_NAME="quality",
              CLUSTER_QUERY_URI="https://example.kusto.fabric.microsoft.com",
              KQL_DB_NAME="Telemetry", KQL_DB_ID="kql-id", WORKSPACE_ID="workspace-a")
    return ns


def edit(parts, path, transform):
    return [support._encode_part(path, transform(support._decode_part(part))) if part["path"] == path
            else part for part in parts]


def text(parts, path):
    return support._decode_part(next(part for part in parts if part["path"] == path))


class DistributionTests(unittest.TestCase):
    def test_distributor_only_writes_owned_notebooks_and_preserves_metadata(self):
        distributor = runpy.run_path(str(Path(__file__).with_name("sync_ontology_notebooks.py")))
        expected = {
            "RTI_004_build_ontology_mapping_rti_structured",
            "RTI_005_entity_DataBinding_rti_structured",
            "RTI_006_TimeSeriesBinding_RTI_signal",
        }
        self.assertEqual(set(distributor["OWNED"]), expected)
        with tempfile.TemporaryDirectory(prefix="ontology-distribution-test-") as directory:
            root = Path(directory)
            support_file = root / "Raw/workspace-reset/ontology_notebook_support.py"
            support_file.parent.mkdir(parents=True)
            support_file.write_text(SUPPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            for name in expected:
                for relative in (Path("Notebooks") / (name + ".Notebook") / "notebook-content.py",
                                 Path("Raw/RTI_Notebooks") / (name + ".ipynb")):
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((ROOT / relative).read_bytes())
            sentinel = root / "Raw/RTI_Notebooks/RTI_009_do_not_touch.ipynb"
            sentinel.write_text("owned elsewhere", encoding="utf-8")
            sample = root / "Raw/RTI_Notebooks/RTI_004_build_ontology_mapping_rti_structured.ipynb"
            before = json.loads(sample.read_text(encoding="utf-8"))
            before["metadata"]["preservation_test"] = True
            before["cells"][0]["metadata"]["preservation_test"] = "cell"
            sample.write_text(json.dumps(before), encoding="utf-8")
            canonical_sample = root / "Notebooks/RTI_004_build_ontology_mapping_rti_structured.Notebook/notebook-content.py"
            untidy = canonical_sample.read_text(encoding="utf-8").replace(
                "USE_MANUAL_TABLE_LIST = True\n", "USE_MANUAL_TABLE_LIST = True  \n") + "\n\n"
            canonical_sample.write_text(untidy, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "distribution drift"):
                distributor["synchronize"](root, check=True)
            self.assertEqual(canonical_sample.read_text(encoding="utf-8"), untidy)
            written = distributor["synchronize"](root)
            distributor["synchronize"](root, check=True)
            after = json.loads(sample.read_text(encoding="utf-8"))
            self.assertEqual(before["metadata"], after["metadata"])
            self.assertEqual(before["cells"][0]["metadata"], after["cells"][0]["metadata"])
            self.assertEqual(len(written), 6)
            self.assertNotIn(sentinel, written)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "owned elsewhere")
            formatted = canonical_sample.read_text(encoding="utf-8")
            self.assertTrue(formatted.endswith("\n"))
            self.assertFalse(formatted.endswith("\n\n"))
            self.assertTrue(all(line == line.rstrip() for line in formatted.splitlines()))
            code_cells = [section.split("# METADATA ********************", 1)[0].strip()
                          for section in formatted.split("# CELL ********************")[1:]]
            self.assertEqual(code_cells, ["".join(cell["source"]).strip()
                                         for cell in after["cells"] if cell["cell_type"] == "code"])

    def test_canonical_and_raw_parse_and_match_without_outputs(self):
        common = SUPPORT_PATH.read_text(encoding="utf-8").strip()
        for number in ("004", "005", "006"):
            code = canonical(number).read_text(encoding="utf-8")
            compile(code, str(canonical(number)), "exec")
            sections = code.split("# CELL ********************")[1:]
            cells = [section.split("# METADATA ********************")[0].strip() for section in sections]
            self.assertIn(common, cells)
            raw = json.loads(next((ROOT / "Raw/RTI_Notebooks").glob(f"RTI_{number}*.ipynb")).read_text(encoding="utf-8"))
            raw_cells = [cell for cell in raw["cells"] if cell["cell_type"] == "code"]
            self.assertEqual(cells, ["".join(cell["source"]).strip() for cell in raw_cells])
            for cell in raw_cells:
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])
                compile("".join(cell["source"]), f"raw-{number}", "exec")
            self.assertIn("_require_v2_ontology(ontology_details)", code)
            self.assertNotIn('settings.get("ontology_generation"', code)

    def test_publish_only_after_verified_readback(self):
        code = canonical("004").read_text(encoding="utf-8")
        self.assertEqual(code.count('.saveAsTable("ontology_parts_latest")'), 1)
        deployment = code[code.index('"""V2-only prerequisite:'):]
        self.assertLess(deployment.index("_require_v2_ontology(ontology_details)"), deployment.index("update_ontology_definition("))
        self.assertLess(deployment.index("_verify_definition(submitted_parts, verified_parts)"),
                        deployment.index('.saveAsTable("ontology_parts_latest")'))
        self.assertIn("for part in verified_parts:", deployment)
        self.assertNotIn('spark.read.table("ontology_parts_latest")', deployment)

    def test_legacy_authoring_and_binding_code_is_removed(self):
        for number in ("004", "005", "006"):
            code = canonical(number).read_text(encoding="utf-8")
            for obsolete in ("_merge_v1_structure", "_validate_v1_lakehouse_targets",
                             "if ONTOLOGY_GENERATION == 1", "dataBindingConfiguration",
                             "REPLACE_EXISTING_TIMESERIES_BINDING", "ontology_generate_all_parts.py"):
                self.assertNotIn(obsolete, code)
            self.assertIn("_require_v2_ontology(ontology_details)", code)


class GenerationAndSchemaTests(unittest.TestCase):
    def test_explicit_key_override_wins_and_must_exist(self):
        self.assertEqual(support._resolve_own_key("instruments", {"instrument_id", "opcua_node_id"},
                                                 {"instruments": "opcua_node_id"}, ["instrument_id"]), "opcua_node_id")
        with self.assertRaisesRegex(RuntimeError, "Explicit key"):
            support._resolve_own_key("instruments", {"instrument_id"}, {"instruments": "missing"}, ["instrument_id"])

    def test_short_byte_and_decimal_are_not_silently_strings(self):
        for dtype in ("ShortType()", "ByteType()"):
            self.assertEqual(support._spark_api_type(dtype), "BigInt")
        for dtype in ("DecimalType(18,2)", "BinaryType()", "ArrayType(StringType(), True)"):
            with self.assertRaises(ValueError):
                support._spark_api_type(dtype)

    def test_entity_specific_types(self):
        parts = built()
        self.assertEqual(support._property_objects(text(parts, "entities/facilities.tmdl"))["state"][0], "boolean")
        self.assertEqual(support._property_objects(text(parts, "entities/signal_master.tmdl"))["state"][0], "string")

    def test_only_live_generation_two_is_accepted(self):
        self.assertEqual(support._resolve_ontology_generation({"properties": {"generation": 2}}, built()), 2)
        legacy = [support._encode_part("definition.json", "{}")]
        with self.assertRaisesRegex(RuntimeError, "Replacement required"):
            support._resolve_ontology_generation({"properties": {"generation": 1}}, legacy)
        for item, parts in [({"properties": {"generation": 1}}, built()),
                            ({}, legacy), ({}, built() + legacy), ({}, []),
                            ({}, built()), ({"properties": {"generation": 2}}, legacy),
                            ({"properties": {"generation": "2"}}, built()),
                            ({"properties": {"generation": True}}, legacy),
                            ({"properties": {"generation": 3}}, [])]:
            with self.assertRaises(RuntimeError):
                support._resolve_ontology_generation(item, parts)


class GenerationPersistenceTests(unittest.TestCase):
    def persistence_namespace(self):
        ns = functions("004", ["_persist_verified_generation"])
        frame = Mock()
        frame.withColumn.return_value = frame
        frame.alias.return_value = frame
        table = Mock()
        table.alias.return_value = table
        table.merge.return_value = table
        table.whenMatchedUpdate.return_value = table
        table.whenNotMatchedInsert.return_value = table
        ns.update(
            spark=SimpleNamespace(createDataFrame=Mock(return_value=frame)),
            F=SimpleNamespace(current_timestamp=Mock(return_value="server-timestamp")),
            DeltaTable=SimpleNamespace(forName=Mock(return_value=table)),
            settings_table_name="rti_demo_settings",
            settings={"unrelated": "preserved", "ontology_generation": "1"},
        )
        return ns, frame, table

    def test_upsert_only_verified_generation_and_preserve_other_settings(self):
        for generation in (2,):
            ns, frame, table = self.persistence_namespace()
            for _ in range(2):
                ns["_persist_verified_generation"](generation)
            ns["spark"].createDataFrame.assert_called_with([
                {"setting_name": "ontology_generation", "setting_value": str(generation)}])
            frame.withColumn.assert_called_with("updated_utc", "server-timestamp")
            ns["DeltaTable"].forName.assert_called_with(ns["spark"], "rti_demo_settings")
            table.merge.assert_called_with(frame, "target.setting_name = source.setting_name")
            table.whenMatchedUpdate.assert_called_with(set={
                "setting_value": "source.setting_value", "updated_utc": "source.updated_utc"})
            table.whenNotMatchedInsert.assert_called_with(values={
                "setting_name": "source.setting_name", "setting_value": "source.setting_value",
                "updated_utc": "source.updated_utc"})
            self.assertEqual(table.execute.call_count, 2)
            self.assertEqual(ns["settings"], {"unrelated": "preserved", "ontology_generation": str(generation)})

    def test_invalid_generation_and_write_failure_do_not_report_success(self):
        for generation in ("auto", "2", 0, 1, 3, None, True):
            ns, _, table = self.persistence_namespace()
            with self.assertRaises(ValueError):
                ns["_persist_verified_generation"](generation)
            ns["spark"].createDataFrame.assert_not_called()
            table.execute.assert_not_called()
        ns, _, table = self.persistence_namespace()
        table.execute.side_effect = RuntimeError("settings unavailable")
        with self.assertRaisesRegex(RuntimeError, "settings unavailable"):
            ns["_persist_verified_generation"](2)
        self.assertEqual(ns["settings"], {"unrelated": "preserved", "ontology_generation": "1"})

    def test_persistence_follows_definition_and_generation_readback(self):
        code = canonical("004").read_text(encoding="utf-8")
        deployment = code[code.index("# Only verified v2 TMDL is submitted and published."):]
        verify = deployment.index("_verify_definition(submitted_parts, verified_parts)")
        generation = deployment.index("verified_generation = _resolve_ontology_generation(")
        publication = deployment.index('.saveAsTable("ontology_parts_latest")')
        persistence = deployment.index("_persist_verified_generation(verified_generation)")
        self.assertLess(verify, generation)
        self.assertLess(generation, publication)
        self.assertLess(publication, persistence)
        self.assertIn("get_ontology(ontology_id), verified_parts)", deployment)

    def test_existing_v1_rejected_and_fresh_creation_verifies_generation_two(self):
        ns = functions("004", ["create_ontology", "ensure_ontology"])
        existing = {"id": "legacy", "properties": {"generation": 1}}
        ns.update(
            target_folder_id="folder", workspace_id="workspace",
            FABRIC_API_BASE="https://api.fabric.microsoft.com", FABRIC_API_VERSION="v1",
            display=Mock(), Markdown=lambda value: value,
            find_ontology_by_name=Mock(return_value=existing),
            get_ontology=Mock(return_value=existing),
            api_request=Mock(return_value=SimpleNamespace(
                status_code=201, json=lambda: {"id": "fresh", "properties": {"generation": 2}})),
        )
        with self.assertRaisesRegex(RuntimeError, "Replacement required"):
            ns["ensure_ontology"]("demo")
        ns["api_request"].assert_not_called()
        ns["get_ontology"].return_value = {"id": "existing-v2", "properties": {"generation": 2}}
        self.assertEqual(ns["ensure_ontology"]("demo")["properties"]["generation"], 2)
        ns["api_request"].assert_not_called()
        ns["find_ontology_by_name"].return_value = None
        self.assertEqual(ns["ensure_ontology"]("demo")["properties"]["generation"], 2)
        self.assertIn("definition", ns["api_request"].call_args.kwargs["data"])
        ns["get_ontology"].return_value = existing
        with self.assertRaisesRegex(RuntimeError, "Replacement required"):
            ns["ensure_ontology"]("demo")

    def test_create_submits_explicit_v2_tmdl_for_sync_and_async_results(self):
        for status in (201, 202):
            with self.subTest(status=status):
                ns = functions("004", ["create_ontology"])
                created = {"id": "fresh"}
                details = dict(created, properties={"generation": 2})
                ns.update(
                    target_folder_id="folder", workspace_id="workspace",
                    FABRIC_API_BASE="https://api.fabric.microsoft.com", FABRIC_API_VERSION="v1",
                    api_request=Mock(return_value=SimpleNamespace(
                        status_code=status, json=lambda: created,
                        headers={"x-ms-operation-id": "operation-id"})),
                    _created_item_result=Mock(return_value=created),
                    get_ontology=Mock(return_value=details),
                )
                self.assertEqual(ns["create_ontology"]("demo", "description"), details)
                ns["get_ontology"].assert_called_once_with("fresh")
                request = ns["api_request"].call_args
                self.assertEqual(request.args, (
                    "POST", "https://api.fabric.microsoft.com/v1/workspaces/workspace/ontologies"))
                payload = request.kwargs["data"]
                self.assertEqual(set(payload), {"displayName", "description", "folderId", "definition"})
                self.assertEqual(payload["displayName"], "demo")
                self.assertEqual(payload["description"], "description")
                self.assertEqual(payload["folderId"], "folder")
                self.assertEqual(set(payload["definition"]), {"parts"})
                parts = payload["definition"]["parts"]
                self.assertEqual(len(parts), 3)
                self.assertTrue(all(part["payloadType"] == "InlineBase64" for part in parts))
                self.assertEqual({
                    part["path"]: base64.b64decode(part["payload"], validate=True).decode("utf-8")
                    for part in parts
                }, {
                    "database.tmdl": "database\n\tcompatibilityLevel: 1000000\n",
                    "model.tmdl": "model Model\n\nref namespace default\n",
                    "namespaces/default.tmdl": "namespace default\n\tlineageTag: default\n",
                })
                if status == 202:
                    ns["_created_item_result"].assert_called_once_with(
                        "https://api.fabric.microsoft.com/v1/operations/operation-id")
                else:
                    ns["_created_item_result"].assert_not_called()
                ns["get_ontology"].return_value = dict(created, properties={"generation": 1})
                with self.assertRaisesRegex(RuntimeError, "Replacement required"):
                    ns["create_ontology"]("demo")

    def test_failed_explicit_create_never_retries_without_definition(self):
        ns = functions("004", ["create_ontology"])
        ns.update(
            target_folder_id=None, workspace_id="workspace",
            FABRIC_API_BASE="https://api.fabric.microsoft.com", FABRIC_API_VERSION="v1",
            api_request=Mock(return_value=SimpleNamespace(status_code=400)),
            get_ontology=Mock(),
        )
        with self.assertRaisesRegex(RuntimeError, "Create v2 ontology failed: HTTP 400"):
            ns["create_ontology"]("demo")
        ns["api_request"].assert_called_once()
        ns["get_ontology"].assert_not_called()
        self.assertEqual(
            {part["path"] for part in ns["api_request"].call_args.kwargs["data"]["definition"]["parts"]},
            {"database.tmdl", "model.tmdl", "namespaces/default.tmdl"},
        )


class StructureTests(unittest.TestCase):
    def test_custom_relationship_names_preserve_original_naming_contract(self):
        ns = functions("004", ["make_safe_rel_name"])
        ns["REL_NAME_OVERRIDES"] = {("systems", "facilities"): "systems_in_facilities"}
        for source, target, expected in (
            ("systems", "facilities", "systems_in_facilities"),
            ("pumps", "sites", "pumps_to_sites"),
            ("pump-devices", "site groups", "pump_devices_to_site_group"),
            ("a" * 26, "sites", "a" * 26),
        ):
            with self.subTest(source=source, target=target):
                self.assertEqual(ns["make_safe_rel_name"](source, target), expected)

    def test_quoted_model_references_are_not_duplicated_on_structure_rerun(self):
        initial = edit(bound(), "model.tmdl", lambda s: s.replace(
            "ref entity facilities", "ref entity 'facilities'").replace(
            "ref entity signal_master", "ref entity default.'signal_master'").replace(
            "ref namespace default", "ref namespace 'default'"))
        result = build_namespace()["_build_gen2_parts"](initial)
        self.assertEqual(result, initial)

    def test_fresh_and_bound_rerun_preserve_all_parts_and_identities(self):
        initial = bound()
        initial += [support._encode_part("rules.tmdl", "rule keep\n\tlineageTag: custom\n"),
                    support._encode_part("metrics.tmdl", "metric retained\n\texpression: 42\n"),
                    support._encode_part("entities/custom.tmdl", "entity custom\n\tkeyProperty: id\n")]
        initial = edit(initial, "entities/signal_master.tmdl", lambda s: s.replace(
            "lineageTag: " + build_namespace()["_tag"]("entity", "signal_master"), "lineageTag: existing-ui-id"))
        result = build_namespace()["_build_gen2_parts"](initial)
        self.assertEqual(support._parts_by_path(initial), support._parts_by_path(result))
        support._verify_definition(initial, result)

    def test_incompatible_key_type_and_relationship_rejected_without_mutation(self):
        initial = bound()
        for mutate in (
            lambda ns: ns["own_pk_map"].update(signal_master="facility_id"),
            lambda ns: ns["table_schemas"][1]["columns"][2].update(dataType="Double"),
            lambda ns: ns["table_schemas"][1]["columns"].append({"name": "new", "dataType": "String"}),
        ):
            ns = build_namespace()
            mutate(ns)
            before = copy.deepcopy(initial)
            with self.assertRaises(RuntimeError):
                ns["_build_gen2_parts"](initial)
            self.assertEqual(before, initial)
        incompatible = edit(initial, "entityRelationships.tmdl",
                            lambda s: s.replace("toEntity: facilities", "toEntity: signal_master"))
        with self.assertRaisesRegex(RuntimeError, "endpoints"):
            build_namespace()["_build_gen2_parts"](incompatible)

    def test_unbound_additive_property_and_unmanaged_siblings(self):
        initial = built()
        initial = edit(initial, "entities/signal_master.tmdl",
                       lambda s: s + "\n\tresourceLink portal\n\t\turl: https://example.test\n")
        ns = build_namespace()
        ns["table_schemas"][1]["columns"].append({"name": "new property", "dataType": "String"})
        result = ns["_build_gen2_parts"](initial)
        entity = text(result, "entities/signal_master.tmdl")
        self.assertIn("resourceLink portal", entity)
        self.assertIn("new property", support._property_objects(entity))

    def test_existing_relationship_name_and_identity_are_reused(self):
        initial = edit(bound(), "entityRelationships.tmdl",
                       lambda s: s.replace("entityRelationship signal_master_facilities", "entityRelationship 'UI relationship'"))
        self.assertEqual(build_namespace()["_build_gen2_parts"](initial), initial)


class LakehouseTests(unittest.TestCase):
    def test_m_comments_preserve_urls_and_strings_and_allow_quotes_in_nested_comments(self):
        literal = '"https://host//path ""/* literal */"""'
        call = 'AzureStorage.DataLake("https://host/workspace/lakehouse")'
        source = f'Source = {literal}, /* " quote /* nested */ */ Live = {call} // trailing'
        result = support._without_m_comments(source)
        self.assertIn(literal, result)
        self.assertIn(call, result)
        self.assertNotIn("nested", result)
        self.assertNotIn("trailing", result)
        for invalid in ("/* unfinished", "Source */"):
            with self.subTest(source=invalid), self.assertRaisesRegex(RuntimeError, "M source comment"):
                support._without_m_comments(invalid)

    def test_commented_onelake_source_does_not_validate_a_different_source(self):
        initial = edit(bound(), "expressions.tmdl", lambda s: s.replace(
            'Source = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/workspace-a/lakehouse-a", [HierarchicalNavigation=true])',
            '// AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/workspace-a/lakehouse-a")\n'
            '\t\t    Source = OtherSource'))
        with self.assertRaisesRegex(RuntimeError, "source mismatch"):
            binding_namespace()["_bind_gen2_parts"](initial, table_columns())

    def test_fresh_rerun_crlf_and_unrelated_parts(self):
        initial = built() + [support._encode_part("entities/other.tmdl", "entity other\n\tkeyProperty: id\n")]
        initial = edit(initial, "entityRelationships.tmdl", lambda s: s + "\nentityRelationship unrelated\n\tfromEntity: other\n\ttoEntity: other\n")
        initial = [dict(part, payload=base64.b64encode(support._decode_part(part).replace("\n", "\r\n").encode()).decode())
                   for part in initial]
        ns = binding_namespace()
        first, entities, relationships = ns["_bind_gen2_parts"](initial, table_columns())
        second, _, _ = ns["_bind_gen2_parts"](first, table_columns())
        self.assertEqual((entities, relationships), (2, 1))
        self.assertEqual(first, second)
        self.assertEqual(next(p for p in initial if p["path"] == "entities/other.tmdl"),
                         next(p for p in second if p["path"] == "entities/other.tmdl"))
        support._verify_definition(first, second)

    def test_resource_link_is_not_swallowed_and_quoted_names(self):
        initial = built()
        initial = edit(initial, "entities/facilities.tmdl", lambda s: s.replace("entity facilities", "entity 'facilities'")
                       .replace("keyProperty: facility_id", "keyProperty: 'facility_id'")
                       .replace("property state", "property 'state'")
                       + "\n\tresourceLink portal\n\t\turl: https://example.test\n")
        initial = edit(initial, "entityRelationships.tmdl",
                       lambda s: s.replace("fromEntity: signal_master", "fromEntity: default.'signal_master'")
                       .replace("toEntity: facilities", "toEntity: 'default'.'facilities'"))
        result = binding_namespace()["_bind_gen2_parts"](initial, table_columns())[0]
        entity = text(result, "entities/facilities.tmdl")
        property_block = support._property_objects(entity)["state"][1]
        resource = support._object_block(entity, "resourceLink", "portal", 4)
        self.assertIn("backingConfiguration", property_block)
        self.assertNotIn("resourceLink", property_block)
        self.assertNotIn("backingConfiguration", resource)
        self.assertIn("url: https://example.test", resource)

    def test_target_changes_fail_without_mutating_input(self):
        initial = bound()
        for key in ("WORKSPACE_ID", "LAKEHOUSE_ID"):
            ns = binding_namespace()
            ns[key] = "different"
            before = copy.deepcopy(initial)
            with self.assertRaisesRegex(RuntimeError, "mismatch"):
                ns["_bind_gen2_parts"](initial, table_columns())
            self.assertEqual(initial, before)
        for replacement in (
            lambda s: s.replace("ONT_WorkspaceId = workspace-a", "ONT_WorkspaceId = stale"),
            lambda s: s.replace("ONT_ItemId = lakehouse-a", "ONT_ItemId = stale"),
            lambda s: s.replace("sourceColumn: state", "sourceColumn: stale"),
        ):
            stale = edit(initial, "tables/facilities.tmdl", replacement)
            with self.assertRaises(RuntimeError):
                binding_namespace()["_bind_gen2_parts"](stale, table_columns())

    def test_existing_expression_siblings_are_preserved(self):
        initial = built() + [support._encode_part("expressions.tmdl", "expression unrelated = 42\n\tlineageTag: untouched\n")]
        result = binding_namespace()["_bind_gen2_parts"](initial, table_columns())[0]
        self.assertIn("expression unrelated = 42\n\tlineageTag: untouched", text(result, "expressions.tmdl"))

    def test_physical_relationship_conflict_rejected(self):
        stale = edit(bound(), "relationships.tmdl",
                     lambda s: s.replace("fromColumn: signal_master.facility_id", "fromColumn: signal_master.state"))
        with self.assertRaisesRegex(RuntimeError, "fromColumn"):
            binding_namespace()["_bind_gen2_parts"](stale, table_columns())

    def test_renamed_physical_relationship_is_reused_and_validated(self):
        initial = edit(bound(), "relationships.tmdl", lambda s: s.replace(
            "relationship signal_master_facilities", "relationship 'UI authored join'"))
        initial = edit(initial, "entityRelationships.tmdl", lambda s: s.replace(
            "relationship: signal_master_facilities", "relationship: 'UI authored join'"))
        before = copy.deepcopy(initial)
        result = binding_namespace()["_bind_gen2_parts"](initial, table_columns())[0]
        self.assertEqual(result, initial)
        support._verify_definition(initial, result)
        for transform in (
            lambda s: s.replace("fromColumn: signal_master.facility_id", "fromColumn: signal_master.state"),
            lambda s: "",
        ):
            with self.subTest(transform=transform):
                stale = edit(initial, "relationships.tmdl", transform)
                with self.assertRaises(RuntimeError):
                    binding_namespace()["_bind_gen2_parts"](stale, table_columns())
        self.assertEqual(initial, before)


class EventhouseTests(unittest.TestCase):
    def test_source_annotations_and_query_must_match_not_just_contain_expected_values(self):
        ns = event_namespace()
        initial, _ = ns["_bind_eventhouse_parts"](bound())
        for transform in (
            lambda s: s.replace("ONT_ItemId = kql-id", "ONT_ItemId = kql-id-other"),
            lambda s: s.replace("ONT_WorkspaceId = workspace-a", "ONT_WorkspaceId = workspace-a-other"),
            lambda s: s.replace("ONT_ItemKind = KQLDatabase", "ONT_ItemKind = KQLDatabaseOther"),
            lambda s: s.replace('Source = AzureDataExplorer.Contents(',
                                '// Source = AzureDataExplorer.Contents(').replace(
                                    '\t\t\t\tin\n', '\t\t\t\t  Source = OtherSource\n\t\t\t\tin\n'),
        ):
            with self.subTest(transform=transform):
                stale = edit(initial, "tables/OPCUAEvents.tmdl", transform)
                before = copy.deepcopy(stale)
                with self.assertRaisesRegex(RuntimeError, "Eventhouse source"):
                    ns["_bind_eventhouse_parts"](stale)
                self.assertEqual(stale, before)

    def test_existing_telemetry_columns_require_exact_source_and_type(self):
        ns = event_namespace()
        initial, _ = ns["_bind_eventhouse_parts"](bound())
        for column, dtype in (("event_time", "dateTime"), ("opcua_node_id", "string"),
                              ("value", "double"), ("quality", "string")):
            block = support._object_block(text(initial, "tables/OPCUAEvents.tmdl"), "column", column, 4)
            for replacement in (
                block.replace(f"sourceColumn: {column}", "sourceColumn: wrong_column"),
                block.replace(f"sourceColumn: {column}",
                              f"sourceColumn: {'quality' if column == 'opcua_node_id' else 'opcua_node_id'}"),
                block.replace(f"dataType: {dtype}", "dataType: int64"),
                "",
            ):
                with self.subTest(column=column, replacement=replacement):
                    stale = edit(initial, "tables/OPCUAEvents.tmdl", lambda s: support._replace_object(
                        s, "column", column, replacement, 4))
                    before = copy.deepcopy(stale)
                    with self.assertRaises(RuntimeError):
                        ns["_bind_eventhouse_parts"](stale)
                    self.assertEqual(stale, before)

    def test_existing_telemetry_requires_single_source_partition(self):
        ns = event_namespace()
        initial, _ = ns["_bind_eventhouse_parts"](bound())
        for transform in (
            lambda s: s + (
                '\n\tpartition Other = m\n\t\tmode: directQuery\n'
                '\t\tsource = AzureDataExplorer.Contents("https://other", "Other", "Other")\n'),
            lambda s: support._replace_object(s, "partition", "OPCUAEvents", "", 4),
            lambda s: s.replace("mode: directQuery", "mode: import\n\t\t// mode: directQuery"),
        ):
            with self.subTest(transform=transform):
                stale = edit(initial, "tables/OPCUAEvents.tmdl", transform)
                before = copy.deepcopy(stale)
                with self.assertRaises(RuntimeError):
                    ns["_bind_eventhouse_parts"](stale)
                self.assertEqual(stale, before)
        renamed = edit(initial, "tables/OPCUAEvents.tmdl",
                       lambda s: s.replace("partition OPCUAEvents = m", "partition 'UI partition' = m"))
        result, changed = ns["_bind_eventhouse_parts"](renamed)
        self.assertFalse(changed)
        self.assertEqual(renamed, result)

    def test_renamed_telemetry_relationship_is_reused_and_validated(self):
        ns = event_namespace()
        initial, _ = ns["_bind_eventhouse_parts"](bound())
        initial = edit(initial, "relationships.tmdl", lambda s: s.replace(
            "relationship signal_master_OPCUAEvents", "relationship 'UI telemetry join'"))
        initial = edit(initial, "entities/signal_master.tmdl", lambda s: s.replace(
            "relationship: signal_master_OPCUAEvents", "relationship: 'UI telemetry join'"))
        result, changed = ns["_bind_eventhouse_parts"](initial)
        self.assertFalse(changed)
        self.assertEqual(initial, result)
        support._verify_definition(initial, result)
        for transform in (
            lambda s: s.replace("toColumn: signal_master.opcua_node_id", "toColumn: signal_master.state"),
            lambda s: support._replace_object(s, "relationship", "UI telemetry join", "", 0),
        ):
            with self.subTest(transform=transform):
                stale = edit(initial, "relationships.tmdl", transform)
                before = copy.deepcopy(stale)
                with self.assertRaises(RuntimeError):
                    ns["_bind_eventhouse_parts"](stale)
                self.assertEqual(stale, before)

    def test_standalone_timestamp_is_bound_and_readback_required(self):
        ns = event_namespace()
        parts, _ = ns["_bind_eventhouse_parts"](bound())
        entity = text(parts, "entities/signal_master.tmdl")
        timestamp = ns["_property_block"](entity, "event_time")
        self.assertIn("dataType: TimeSeries<dateTime>", timestamp)
        self.assertIn("valueColumn: OPCUAEvents.event_time", timestamp)
        self.assertIn("orderingColumn: OPCUAEvents.event_time", timestamp)
        missing = edit(parts, "entities/signal_master.tmdl", lambda value: value.replace(
            "valueColumn: OPCUAEvents.event_time", "valueColumn: OPCUAEvents.value"))
        with self.assertRaises(RuntimeError):
            support._verify_definition(parts, missing)
        rerun, changed = ns["_bind_eventhouse_parts"](parts)
        self.assertFalse(changed)
        self.assertEqual(parts, rerun)

    def test_fresh_rerun_and_resource_link_with_quoted_property(self):
        initial = edit(bound(), "entities/signal_master.tmdl",
                       lambda s: s.replace("property quality", "property 'quality'")
                       + "\n\tresourceLink portal\n\t\turl: https://example.test\n")
        ns = event_namespace()
        first, changed = ns["_bind_eventhouse_parts"](initial)
        self.assertIn("tables/OPCUAEvents.tmdl", changed)
        second, changed = ns["_bind_eventhouse_parts"](first)
        self.assertFalse(changed)
        self.assertEqual(first, second)
        entity = text(first, "entities/signal_master.tmdl")
        prop = ns["_property_block"](entity, "quality")
        self.assertIn("orderingColumn: OPCUAEvents.event_time", prop)
        self.assertNotIn("resourceLink", prop)
        resource = support._object_block(entity, "resourceLink", "portal", 4)
        self.assertNotIn("backingConfiguration", resource)
        self.assertIn("url: https://example.test", resource)
        support._verify_definition(first, second)
        self.assertEqual(build_namespace()["_build_gen2_parts"](first), first)
        self.assertEqual(binding_namespace()["_bind_gen2_parts"](first, table_columns())[0], first)

    def test_crlf_and_source_mismatch(self):
        ns = event_namespace()
        first, _ = ns["_bind_eventhouse_parts"](bound())
        crlf = [support._encode_part(part["path"], support._decode_part(part).replace("\n", "\r\n")) for part in first]
        _, changed = ns["_bind_eventhouse_parts"](crlf)
        self.assertFalse(changed)
        for key in ("WORKSPACE_ID", "KQL_DB_ID", "CLUSTER_QUERY_URI"):
            other = event_namespace()
            other[key] = "different"
            with self.assertRaises(RuntimeError):
                other["_bind_eventhouse_parts"](first)

    def test_quoted_additional_backing_and_unrelated_backings_are_preserved(self):
        ns = event_namespace()
        first, _ = ns["_bind_eventhouse_parts"](bound())
        first = edit(first, "entities/signal_master.tmdl",
                     lambda s: s.replace("table: OPCUAEvents", "table: default.'OPCUAEvents'")
                     .replace("relationship: signal_master_OPCUAEvents", "relationship: 'signal_master_OPCUAEvents'")
                     + "\n\tadditionalBackingTable\n\t\ttable: Other\n\t\trelationship: OtherJoin\n")
        second, changed = ns["_bind_eventhouse_parts"](first)
        self.assertFalse(changed)
        self.assertEqual(first, second)


class ReadbackTests(unittest.TestCase):
    def test_missing_property_relationship_and_mapping_fail(self):
        expected = bound()
        mutations = [
            ("entities/facilities.tmdl", lambda s: support._replace_object(s, "property", "state", "", 4)),
            ("entityRelationships.tmdl", lambda s: ""),
            ("relationships.tmdl", lambda s: ""),
            ("entities/facilities.tmdl", lambda s: s.replace("valueColumn: facilities.state", "valueColumn: facilities.facility_id")),
        ]
        for path, transform in mutations:
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                support._verify_definition(expected, edit(expected, path, transform))

    def test_mapping_under_wrong_sibling_fails(self):
        expected = bound()
        actual = edit(expected, "entities/facilities.tmdl", lambda s: s.replace(
            "\t\tbackingConfiguration\n\t\t\tvalueColumn: facilities.state",
            "\tresourceLink portal\n\t\tbackingConfiguration\n\t\t\tvalueColumn: facilities.state"))
        with self.assertRaises(RuntimeError):
            support._verify_definition(expected, actual)

    def test_crlf_readback_and_duplicate_paths(self):
        expected = bound()
        actual = [support._encode_part(part["path"], support._decode_part(part).replace("\n", "\r\n")) for part in expected]
        support._verify_definition(expected, actual)
        with self.assertRaises(RuntimeError):
            support._verify_definition(expected, actual + [actual[0]])


class ApiTests(unittest.TestCase):
    def test_lro_operation_id_preferred_over_regional_location(self):
        ns = functions("004", ["_fabric_operation_url"])
        response = SimpleNamespace(headers={
            "Location": "https://wabi-region-redirect.analysis.windows.net/v1/operations/regional",
            "X-MS-Operation-ID": "operation-id",
        })
        self.assertEqual(ns["_fabric_operation_url"](response, "https://api.fabric.microsoft.com/v1"),
                         "https://api.fabric.microsoft.com/v1/operations/operation-id")

    def test_lro_relative_and_regional_urls_and_query_preservation(self):
        ns = functions("004", ["_fabric_operation_url", "_fabric_result_url"])
        for location, expected in (
            ("/v1/operations/op", "https://api.fabric.microsoft.com/v1/operations/op"),
            ("operations/op", "https://api.fabric.microsoft.com/v1/operations/op"),
            ("https://wabi-region-redirect.analysis.windows.net/v1/operations/op?tenant=test",
             "https://wabi-region-redirect.analysis.windows.net/v1/operations/op?tenant=test"),
        ):
            result = ns["_fabric_operation_url"](
                SimpleNamespace(headers={"Location": location}), "https://api.fabric.microsoft.com/v1")
            self.assertEqual(result, expected)
        self.assertEqual(
            ns["_fabric_result_url"]("https://wabi-region-redirect.analysis.windows.net/v1/operations/op?tenant=test"),
            "https://wabi-region-redirect.analysis.windows.net/v1/operations/op/result?tenant=test")
        for location in ("https://fabric.microsoft.com.evil.test/op", "http://api.fabric.microsoft.com/op",
                         "https://user@api.fabric.microsoft.com/op"):
            with self.assertRaises(RuntimeError):
                ns["_fabric_operation_url"](SimpleNamespace(headers={"Location": location}),
                                            "https://api.fabric.microsoft.com/v1")

    def test_pagination_follows_uri_then_escaped_token(self):
        api = Mock(side_effect=[
            SimpleNamespace(status_code=200, json=lambda: {"value": [{"id": "1"}], "continuationUri": "?page=2"}),
            SimpleNamespace(status_code=200, json=lambda: {"value": [{"id": "2"}], "continuationToken": "a+/="}),
            SimpleNamespace(status_code=200, json=lambda: {"value": [{"id": "3"}]}),
        ])
        ns = functions("004", ["_list_fabric_values"])
        ns["api_request"] = api
        result = ns["_list_fabric_values"]("https://api.fabric.microsoft.com/v1/items?type=Ontology")
        self.assertEqual([item["id"] for item in result], ["1", "2", "3"])
        self.assertIn("continuationToken=a%2B%2F%3D", api.call_args.args[1])
        self.assertIn("type=Ontology", api.call_args.args[1])

    def test_listing_errors_and_loops_fail_not_empty_success(self):
        for response in (
            SimpleNamespace(status_code=403),
            SimpleNamespace(status_code=200, json=lambda: {"continuationUri": "https://other.test/items"}),
            SimpleNamespace(status_code=200, json=lambda: {"continuationUri": "https://api.fabric.microsoft.com/items"}),
        ):
            ns = functions("004", ["_list_fabric_values"])
            ns["api_request"] = Mock(return_value=response)
            with self.assertRaises(RuntimeError):
                ns["_list_fabric_values"]("https://api.fabric.microsoft.com/items")

    def test_async_create_returns_operation_result_item_not_operation_id(self):
        ns = functions("004", ["_created_item_result"])
        wait = Mock(return_value={"id": "operation-id", "status": "Succeeded"})
        api = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {"id": "ontology-id", "displayName": "demo"}))
        ns.update(wait_for_lro=wait, api_request=api)
        self.assertEqual(ns["_created_item_result"]("https://api.fabric.microsoft.com/v1/operations/op")["id"], "ontology-id")
        api.assert_called_once_with("GET", "https://api.fabric.microsoft.com/v1/operations/op/result")
        code = canonical("004").read_text(encoding="utf-8")
        self.assertIn("created = _created_item_result(_fabric_operation_url(", code)


class DefinitionReadSafetyTests(unittest.TestCase):
    def response(self, status=200, body=None, invalid_json=False):
        parse = Mock(side_effect=ValueError("invalid JSON")) if invalid_json else Mock(return_value=body)
        return SimpleNamespace(
            status_code=status, json=parse, headers={"x-ms-operation-id": "operation-id"})

    def namespace(self, number, responses):
        ns = functions(number, ["_validated_definition_response", "_get_fabric_ontology_definition",
                                "get_ontology_definition"])
        ns.update(
            api_request=Mock(side_effect=responses),
            wait_for_lro=Mock(return_value={"status": "Succeeded"}),
            FABRIC_API_BASE="https://api.fabric.microsoft.com", FABRIC_API_VERSION="v1",
            FABRIC_BASE_URL="https://api.fabric.microsoft.com/v1",
            workspace_id="workspace", WORKSPACE_ID="workspace",
        )
        return ns

    def malformed_bodies(self):
        part = support._encode_part(".platform", "{}")
        return [
            None, [], {}, {"definition": None}, {"definition": {}},
            {"definition": {"parts": None}}, {"definition": {"parts": {}}},
            {"definition": {"parts": []}}, {"definition": {"parts": [None]}},
            {"definition": {"parts": [{"path": ".platform"}]}},
            {"definition": {"parts": [dict(part, path="")]}},
            {"definition": {"parts": [dict(part, payloadType="Unknown")]}},
            {"definition": {"parts": [dict(part, payload="not-base64")]}},
            {"definition": {"parts": [support._encode_part(".platform", "not-json")]}},
            {"definition": {"parts": [part]}},
            {"definition": {"parts": [part, part]}},
        ]

    def test_all_readers_raise_on_http_auth_and_lro_result_failures(self):
        for number in ("004", "005", "006"):
            for status in (401, 403, 404, 429, 500, 204):
                for asynchronous in (False, True):
                    with self.subTest(number=number, status=status, asynchronous=asynchronous):
                        responses = [self.response(status)]
                        if asynchronous:
                            responses.insert(0, self.response(202))
                        ns = self.namespace(number, responses)
                        with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                            ns["get_ontology_definition"]("existing-ontology")
            ns = self.namespace(number, [RuntimeError("authentication failed")])
            with self.assertRaisesRegex(RuntimeError, "authentication failed"):
                ns["get_ontology_definition"]("existing-ontology")
            ns = self.namespace(number, [self.response(202)])
            ns["wait_for_lro"].side_effect = RuntimeError("LRO Failed")
            with self.assertRaisesRegex(RuntimeError, "LRO Failed"):
                ns["get_ontology_definition"]("existing-ontology")
            self.assertEqual(ns["api_request"].call_count, 1)

    def test_all_readers_reject_malformed_successful_direct_and_lro_responses(self):
        for number in ("004", "005", "006"):
            for asynchronous in (False, True):
                failures = [self.response(body=body) for body in self.malformed_bodies()]
                failures.append(self.response(invalid_json=True))
                for index, failure in enumerate(failures):
                    with self.subTest(number=number, asynchronous=asynchronous, malformed=index):
                        responses = [self.response(202), failure] if asynchronous else [failure]
                        ns = self.namespace(number, responses)
                        with self.assertRaises(RuntimeError):
                            ns["get_ontology_definition"]("existing-ontology")

    def test_valid_root_only_starters_and_existing_content_are_retained(self):
        v1 = [support._encode_part(".platform", "{}"), support._encode_part("definition.json", "{}")]
        custom = support._encode_part("custom.json", '{"keep":"unchanged"}')
        for number in ("004", "005", "006"):
            rejected = self.namespace(number, [self.response(body={"definition": {"parts": v1}})])
            with self.assertRaisesRegex(RuntimeError, "legacy items require replacement"):
                rejected["get_ontology_definition"]("existing-v1")
            for parts in (roots(), bound() + [custom]):
                for asynchronous in (False, True):
                    body = {"definition": {"parts": parts}}
                    responses = [self.response(body=body)]
                    if asynchronous:
                        responses.insert(0, self.response(202))
                    ns = self.namespace(number, responses)
                    self.assertIs(ns["get_ontology_definition"]("existing-ontology"), body)
        self.assertEqual(build_namespace()["_build_gen2_parts"](built() + [custom]), built() + [custom])

    def write_fragment(self):
        tree = ast.parse(canonical("004").read_text(encoding="utf-8"))

        def assignment(name):
            return next(node for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))

        live_read = assignment("live_parts")
        dispatch = assignment("ONTOLOGY_GENERATION")
        required = next(node for node in tree.body if isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "_require_v2_ontology")
        construction = assignment("submitted_parts")
        update = next(node for node in tree.body if isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                      and node.value.func.id == "update_ontology_definition")
        publish = next(node for node in tree.body if isinstance(node, ast.Expr)
                       and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                       and node.value.func.attr == "saveAsTable"
                       and node.value.args[0].value == "ontology_parts_latest")
        # Execute only these extracted orchestration statements, with all external
        # boundaries mocked; never run a notebook cell or initialize Spark.
        return compile(ast.Module(body=[required, live_read, dispatch, construction, update, publish], type_ignores=[]),
                       "004-prerequisite-order", "exec")

    def test_failed_prerequisite_reads_cannot_reach_actual_update_or_publication(self):
        fragment = self.write_fragment()
        failures = [[self.response(403)], [self.response(500)], [self.response(invalid_json=True)],
                    [self.response(202), self.response(403)], [self.response(202), self.response(invalid_json=True)]]
        failures.extend([self.response(body=body)] for body in self.malformed_bodies())
        for index, responses in enumerate(failures):
            with self.subTest(failure=index):
                ns = self.namespace("004", responses)
                ns.update(
                    ontology_id="existing-v2", ontology_details={"properties": {"generation": 2}},
                    settings={},
                    _build_gen2_parts=Mock(), update_ontology_definition=Mock(),
                    spark=Mock(), pd=SimpleNamespace(DataFrame=Mock()), parts_rows=[],
                )
                with self.assertRaises(RuntimeError):
                    exec(fragment, ns)
                ns["update_ontology_definition"].assert_not_called()
                ns["_build_gen2_parts"].assert_not_called()
                ns["spark"].createDataFrame.assert_not_called()
                ns["pd"].DataFrame.assert_not_called()

    def test_v1_rejected_before_read_update_or_publication_in_all_notebooks(self):
        ns = self.namespace("004", [])
        ns.update(
            ontology_id="existing-v1", ontology_details={"properties": {"generation": 1}},
            _build_gen2_parts=Mock(), update_ontology_definition=Mock(), spark=Mock(),
        )
        with self.assertRaisesRegex(RuntimeError, "Replacement required"):
            exec(self.write_fragment(), ns)
        ns["api_request"].assert_not_called()
        ns["_build_gen2_parts"].assert_not_called()
        ns["update_ontology_definition"].assert_not_called()
        ns["spark"].createDataFrame.assert_not_called()
        for number in ("005", "006"):
            code = canonical(number).read_text(encoding="utf-8")
            cells = [section.split("# METADATA ********************")[0]
                     for section in code.split("# CELL ********************")[1:]]
            guard = next(cell for cell in cells if "_require_v2_ontology(ontology_details)" in cell)
            tree = ast.parse(guard)
            required = next(node for node in tree.body if isinstance(node, ast.Expr)
                            and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                            and node.value.func.id == "_require_v2_ontology")
            read = next(node for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "live_parts" for target in node.targets))
            self.assertLess(required.lineno, read.lineno)
            read_mock = Mock()
            environment = dict(vars(support), ontology_details={"properties": {"generation": 1}},
                               get_ontology_definition=read_mock)
            with self.assertRaisesRegex(RuntimeError, "Replacement required"):
                exec(compile(ast.Module(body=[required, read], type_ignores=[]), number, "exec"), environment)
            read_mock.assert_not_called()


class GraphMaterializationTests(unittest.TestCase):
    graph_id = "33333333-3333-3333-3333-333333333333"

    def graph_parts(self, include_signal=False, include_timeseries=False):
        instrument_alias = "44444444-4444-4444-4444-444444444444"
        node_types = [{
            "primaryKeyProperties": ["instrument_id"], "alias": instrument_alias,
            "labels": ["instruments"],
            "properties": [{"name": "instrument_id", "type": "STRING"}],
        }]
        sources = [{
            "name": "instruments", "type": "DeltaTable",
            "properties": {"path": "abfss://workspace@onelake.pbidedicated.windows.net/lakehouse/Tables/silver_instruments"},
        }]
        node_tables = [{
            "nodeTypeAlias": instrument_alias,
            "id": "55555555-5555-5555-5555-555555555555",
            "dataSourceName": "instruments",
            "propertyMappings": [{"propertyName": "instrument_id", "sourceColumn": "instrument_id"}],
        }]
        if include_signal:
            signal_alias = support._graph_uuid(self.graph_id, "node-type:signal_master")
            properties = [
                {"name": "opcua_node_id", "type": "STRING"},
                {"name": "instrument_id", "type": "STRING"},
            ]
            mappings = [
                {"propertyName": "opcua_node_id", "sourceColumn": "opcua_node_id"},
                {"propertyName": "instrument_id", "sourceColumn": "instrument_id"},
            ]
            if include_timeseries:
                properties.extend([
                    {"name": "event_time", "type": "INVALID"},
                    {"name": "value", "type": "INVALID"},
                    {"name": "quality", "type": "INVALID"},
                ])
                mappings.extend([
                    {"propertyName": name, "sourceColumn": name}
                    for name in ("event_time", "value", "quality")
                ])
            node_types.append({
                "primaryKeyProperties": ["opcua_node_id"], "alias": signal_alias,
                "labels": ["signal_master"], "properties": properties,
            })
            sources.append({
                "name": "signal_master", "type": "DeltaTable",
                "properties": {"path": "abfss://workspace@onelake.pbidedicated.windows.net/lakehouse/Tables/silver_signal_master"},
            })
            node_tables.append({
                "nodeTypeAlias": signal_alias,
                "id": support._graph_uuid(self.graph_id, "node-table:signal_master"),
                "dataSourceName": "signal_master", "propertyMappings": mappings,
            })
        payloads = {
            "graphType.json": {
                "$schema": "graph-type-schema", "nodeTypes": node_types, "edgeTypes": [],
            },
            "dataSources.json": {
                "$schema": "data-source-schema", "dataSources": sources,
            },
            "graphDefinition.json": {
                "$schema": "graph-definition-schema",
                "nodeTables": node_tables, "edgeTables": [],
            },
            "graphSettings.json": {
                "$schema": "settings-schema",
                "scalingConfiguration": {"sku": {"tier": "small"}},
            },
        }
        return [
            support._encode_part(path, json.dumps(value, separators=(",", ":")))
            for path, value in payloads.items()
        ]

    def repair(self, parts):
        return support._ensure_static_graph_projection(
            parts=parts,
            graph_model_id=self.graph_id,
            source_name="signal_master",
            source_path="abfss://workspace@onelake.pbidedicated.windows.net/lakehouse/Tables/silver_signal_master",
            entity_name="signal_master",
            key_name="opcua_node_id",
            property_types={
                "opcua_node_id": "STRING", "instrument_id": "STRING",
                "equipment_id": "STRING", "is_active": "BOOLEAN",
            },
            relationship_name="signals_from_instruments",
            target_entity_name="instruments",
            target_key_name="instrument_id",
            excluded_properties={"event_time", "value", "quality"},
        )

    def complete_repair(self, parts):
        base = "abfss://workspace@onelake.pbidedicated.windows.net/lakehouse/Tables"
        entities = [
            {
                "name": "facilities", "sourceName": "facilities",
                "sourcePath": f"{base}/silver_facilities", "keyName": "facility_id",
                "propertyTypes": {"facility_id": "STRING", "facility_name": "STRING"},
                "excludedProperties": [],
            },
            {
                "name": "systems", "sourceName": "systems",
                "sourcePath": f"{base}/silver_systems", "keyName": "system_id",
                "propertyTypes": {"system_id": "STRING", "facility_id": "STRING"},
                "excludedProperties": [],
            },
            {
                "name": "equipment", "sourceName": "equipment",
                "sourcePath": f"{base}/silver_equipment", "keyName": "equipment_id",
                "propertyTypes": {"equipment_id": "STRING", "system_id": "STRING"},
                "excludedProperties": [],
            },
            {
                "name": "instruments", "sourceName": "instruments",
                "sourcePath": f"{base}/silver_instruments", "keyName": "instrument_id",
                "propertyTypes": {"instrument_id": "STRING", "equipment_id": "STRING"},
                "excludedProperties": [],
            },
            {
                "name": "signal_master", "sourceName": "signal_master",
                "sourcePath": f"{base}/silver_signal_master", "keyName": "opcua_node_id",
                "propertyTypes": {
                    "opcua_node_id": "STRING", "instrument_id": "STRING",
                },
                "excludedProperties": ["value"],
            },
        ]
        relationships = [
            {"name": "systems_in_facilities", "source": "systems", "target": "facilities"},
            {"name": "equipment_in_systems", "source": "equipment", "target": "systems"},
            {"name": "instruments_on_equipment", "source": "instruments", "target": "equipment"},
            {"name": "signals_from_instruments", "source": "signal_master", "target": "instruments"},
        ]
        return support._ensure_complete_static_graph_projection(
            parts, self.graph_id, entities, relationships
        )

    @staticmethod
    def decoded(parts, path):
        return json.loads(support._decode_part(
            next(part for part in parts if part["path"] == path)
        ))

    def test_lineage_requires_service_owned_ontology_graph_relation(self):
        ontology_id = "11111111-1111-1111-1111-111111111111"
        workspace_id = "22222222-2222-2222-2222-222222222222"
        graph_id = self.graph_id
        lineage = {
            "items": [{"id": graph_id, "type": "GraphIndex", "workspaceId": workspace_id}],
            "relations": [{
                "itemId": graph_id, "dependentOnItemId": ontology_id,
                "relationType": "CascadeDelete",
            }],
        }
        items = [{"id": graph_id, "type": "GraphModel"}]
        self.assertEqual(
            support._select_attached_graph_model(ontology_id, workspace_id, lineage, items),
            graph_id,
        )
        for changed in (
            {"items": []},
            {"relations": [{**lineage["relations"][0], "relationType": "Datasource"}]},
            {"items": [{**lineage["items"][0], "type": "GraphModel"}]},
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                RuntimeError, "no attached GraphModel"
            ):
                support._select_attached_graph_model(
                    ontology_id, workspace_id, {**lineage, **changed}, items
                )
        second_id = "66666666-6666-6666-6666-666666666666"
        ambiguous = {
            "items": lineage["items"] + [{
                "id": second_id, "type": "GraphIndex", "workspaceId": workspace_id,
            }],
            "relations": lineage["relations"] + [{
                "itemId": second_id, "dependentOnItemId": ontology_id,
                "relationType": "CascadeDelete",
            }],
        }
        with self.assertRaisesRegex(RuntimeError, "multiple attached"):
            support._select_attached_graph_model(
                ontology_id, workspace_id, ambiguous,
                items + [{"id": second_id, "type": "GraphModel"}],
            )

    def test_static_signal_projection_is_added_and_unknown_parts_are_preserved(self):
        original = self.graph_parts()
        updated, changed, summary = self.repair(original)
        self.assertTrue(changed)
        self.assertEqual(summary["staticPropertyCount"], 4)
        graph_type = self.decoded(updated, "graphType.json")
        signal = next(node for node in graph_type["nodeTypes"]
                      if node["labels"] == ["signal_master"])
        self.assertEqual(signal["primaryKeyProperties"], ["opcua_node_id"])
        self.assertEqual(
            {item["name"]: item["type"] for item in signal["properties"]},
            {
                "opcua_node_id": "STRING", "instrument_id": "STRING",
                "equipment_id": "STRING", "is_active": "BOOLEAN",
            },
        )
        edge = next(item for item in graph_type["edgeTypes"]
                    if item["labels"] == ["signals_from_instruments"])
        self.assertEqual(edge["sourceNodeType"], {"alias": signal["alias"]})
        before = next(part for part in original if part["path"] == "graphSettings.json")
        after = next(part for part in updated if part["path"] == "graphSettings.json")
        self.assertIs(before, after)
        rerun, rerun_changed, rerun_summary = self.repair(updated)
        self.assertFalse(rerun_changed)
        self.assertEqual(rerun_summary, summary)
        self.assertEqual(rerun, updated)

    def test_fresh_empty_graph_model_gets_complete_static_projection(self):
        empty_payloads = {
            "graphType.json": {"$schema": "graph-type-schema", "nodeTypes": None, "edgeTypes": None},
            "dataSources.json": {"$schema": "data-source-schema", "dataSources": None},
            "graphDefinition.json": {
                "$schema": "graph-definition-schema", "nodeTables": None, "edgeTables": None,
            },
            "graphSettings.json": {"serviceOwned": {"future": True}},
        }
        parts = [
            support._encode_part(path, json.dumps(value))
            for path, value in empty_payloads.items()
        ]
        updated, changed, summary = self.complete_repair(parts)
        self.assertTrue(changed)
        self.assertEqual(summary["nodeTypeCount"], 5)
        self.assertEqual(summary["edgeTypeCount"], 4)
        self.assertEqual(summary["staticPropertyCount"], 10)
        self.assertEqual(summary["staticPropertyCounts"]["signal_master"], 2)
        self.assertEqual(
            summary["nodeAliases"]["signal_master"],
            support._graph_uuid(self.graph_id, "node-type:signal_master"),
        )
        self.assertEqual(
            summary["edgeAliases"]["signals_from_instruments"],
            support._graph_uuid(
                self.graph_id, "edge-type:signals_from_instruments"
            ),
        )
        graph_type = self.decoded(updated, "graphType.json")
        self.assertEqual(
            {node["labels"][0] for node in graph_type["nodeTypes"]},
            {"facilities", "systems", "equipment", "instruments", "signal_master"},
        )
        self.assertEqual(
            {edge["labels"][0] for edge in graph_type["edgeTypes"]},
            {
                "systems_in_facilities", "equipment_in_systems",
                "instruments_on_equipment", "signals_from_instruments",
            },
        )
        signal = next(
            node for node in graph_type["nodeTypes"]
            if node["labels"] == ["signal_master"]
        )
        self.assertNotIn("value", {item["name"] for item in signal["properties"]})
        before = next(part for part in parts if part["path"] == "graphSettings.json")
        after = next(part for part in updated if part["path"] == "graphSettings.json")
        self.assertIs(before, after)
        rerun, rerun_changed, rerun_summary = self.complete_repair(updated)
        self.assertFalse(rerun_changed)
        self.assertEqual(rerun_summary, summary)
        self.assertEqual(rerun, updated)

    def test_complete_projection_rejects_nonempty_invalid_collection_shape(self):
        parts = self.graph_parts()
        graph_type = self.decoded(parts, "graphType.json")
        graph_type["nodeTypes"] = {"unexpected": []}
        parts = [
            support._encode_part(part["path"], json.dumps(graph_type))
            if part["path"] == "graphType.json" else part
            for part in parts
        ]
        with self.assertRaisesRegex(
            RuntimeError, "graphType.nodeTypes must be an array or null"
        ):
            self.complete_repair(parts)

    def test_only_invalid_timeseries_graph_properties_are_removed(self):
        updated, changed, _ = self.repair(self.graph_parts(
            include_signal=True, include_timeseries=True,
        ))
        self.assertTrue(changed)
        graph_type = self.decoded(updated, "graphType.json")
        signal = next(node for node in graph_type["nodeTypes"]
                      if node["labels"] == ["signal_master"])
        self.assertFalse(
            {"event_time", "value", "quality"}
            & {item["name"] for item in signal["properties"]}
        )
        definition = self.decoded(updated, "graphDefinition.json")
        table = next(item for item in definition["nodeTables"]
                     if item["nodeTypeAlias"] == signal["alias"])
        self.assertFalse(
            {"event_time", "value", "quality"}
            & {item["propertyName"] for item in table["propertyMappings"]}
        )
        self.assertIn(
            "instrument_id",
            {item["propertyName"] for item in table["propertyMappings"]},
        )

    def test_conflicting_static_source_fails_closed(self):
        parts = self.graph_parts(include_signal=True)
        sources = self.decoded(parts, "dataSources.json")
        sources["dataSources"][-1]["properties"]["path"] = "abfss://different"
        parts = [
            support._encode_part(part["path"], json.dumps(sources, separators=(",", ":")))
            if part["path"] == "dataSources.json" else part
            for part in parts
        ]
        with self.assertRaisesRegex(RuntimeError, "points elsewhere"):
            self.repair(parts)


if __name__ == "__main__":
    unittest.main()
