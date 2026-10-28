"""Offline-only map Data Agent contracts; never executes the notebook's runtime cell.

python3 -m unittest discover -s Raw/workspace-reset -p test_map_agent_provisioning.py -v
python3 Raw/workspace-reset/test_map_agent_provisioning.py --generate-mirror
"""

import ast
import base64
import copy
import json
import math
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

from test_energy_ingestion import canonical_to_ipynb

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "Notebooks/Geo_002_publish_map_agent.Notebook/notebook-content.py"
MIRROR = ROOT / "Raw/RTI_Notebooks/Geo_002_publish_map_agent.ipynb"
PLATFORM = NOTEBOOK.with_name(".platform")
WORKSPACE = str(UUID(int=1))
LAKEHOUSE = str(UUID(int=2))
AGENT = str(UUID(int=3))
OPERATION = str(UUID(int=4))
RUN_ID = str(UUID(int=5))
FOLDER = str(UUID(int=8))


class RequestError(OSError):
    pass


def load_helpers():
    allowed = {"base64", "binascii", "hashlib", "json", "re", "time", "copy", "datetime", "urllib.parse", "uuid"}
    selected = []
    for node in ast.parse(NOTEBOOK.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import) and all(alias.name in allowed for alias in node.names):
            selected.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module in allowed:
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            selected.append(node)
        elif isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) and target.id.isupper() for target in node.targets):
            selected.append(node)
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)
    namespace["time"] = types.SimpleNamespace(sleep=Mock(), monotonic=Mock(return_value=0))
    return namespace


class Response:
    def __init__(self, body=None, status=200, headers=None, events=None):
        self.body, self.status_code = body, status
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.events = events
        self.closed = False

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return copy.deepcopy(self.body)

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size):
        yield json.dumps(self.body).encode()

    def iter_lines(self, chunk_size):
        for line in self.events or []:
            yield line.encode()


class HelpersTest(unittest.TestCase):
    def setUp(self):
        self.ns = load_helpers()
        self.error = self.ns["ProvisioningError"]
        self.context = {
            "currentWorkspaceId": WORKSPACE, "defaultLakehouseWorkspaceId": WORKSPACE,
            "defaultLakehouseId": LAKEHOUSE, "defaultLakehouseName": "Hydro_GeoContext_V6",
        }
        self.binding = self.ns["binding_from_context"]("", "V6", self.context)
        self.binding["onelake_host"] = "onelake.example.invalid"
        fake_requests = types.ModuleType("requests")
        fake_requests.RequestException = RequestError
        self.request_patch = patch.dict(sys.modules, {"requests": fake_requests})
        self.request_patch.start()
        self.addCleanup(self.request_patch.stop)

    def call(self, name, *args, **kwargs):
        return self.ns[name](*args, **kwargs)

    def agent(self, **overrides):
        item = {
            "id": AGENT, "type": "DataAgent", "workspaceId": WORKSPACE,
            "displayName": "Hydro_Map_Agent_V6", "description": self.call("owned_description", self.binding),
            "folderId": FOLDER,
        }
        return {**item, **overrides}

    def lakehouse(self):
        return {
            "id": LAKEHOUSE, "workspaceId": WORKSPACE, "type": "Lakehouse", "displayName": "Hydro_GeoContext_V6",
            "properties": {
                "oneLakeTablesPath": f"https://onelake.example.invalid/{WORKSPACE}/{LAKEHOUSE}/Tables",
                "sqlEndpointProperties": {
                    "id": str(UUID(int=6)), "connectionString": "endpoint.datawarehouse.fabric.microsoft.com",
                    "provisioningStatus": "Success",
                },
            },
        }

    def minimal_parts(self):
        return {
            self.ns["ROOT_PART"]: self.call("encode_part", self.ns["ROOT_PART"], {"$schema": self.ns["DEFINITION_SCHEMA"]}),
            self.ns["DRAFT_STAGE_PART"]: self.call("encode_part", self.ns["DRAFT_STAGE_PART"], {
                "$schema": self.ns["STAGE_SCHEMA"], "aiInstructions": "", "experimental": {"enableExperimentalFeatures": False},
            }),
            ".platform": self.call("encode_part", ".platform", {
                "metadata": {"type": "DataAgent", "displayName": self.binding["agent_name"]},
                "config": {"version": "2.0", "logicalId": str(UUID(int=7))},
            }),
        }

    def desired_parts(self):
        return {part["path"]: part for part in self.call("desired_definition", self.minimal_parts(), self.binding)["parts"]}

    def api(self, session=None):
        return self.ns["FabricMapAgentAPI"](session or Mock(), self.binding, lambda: "offline-only-token")


class OwnershipAndBindingTests(HelpersTest):
    def test_runtime_binding_derives_current_suffix_and_allows_verified_custom_names(self):
        context = {**self.context, "defaultLakehouseName": "Hydro_GeoContext_PROD_42"}
        binding = self.call("binding_from_context", "", "", context)
        self.assertEqual(binding["agent_name"], "Hydro_Map_Agent_PROD_42")
        self.assertEqual(binding["lakehouse_id"], LAKEHOUSE)
        renamed = {**context, "defaultLakehouseName": "Norwegian Energy Production"}
        binding = self.call("binding_from_context", WORKSPACE, "", renamed, LAKEHOUSE, "Hydro_Map_Agent_PROD_42")
        self.assertEqual(binding["lakehouse_name"], renamed["defaultLakehouseName"])
        self.assertEqual(binding["env_suffix"], "PROD_42")
        for target in ("", str(UUID(int=9))):
            with self.subTest(target=target), self.assertRaises(self.error):
                self.call("binding_from_context", "", "", renamed, target, "Hydro_Map_Agent_PROD_42")

    def test_exact_agent_convention_and_default_geo_binding(self):
        self.assertEqual(self.binding["agent_name"], "Hydro_Map_Agent_V6")
        self.assertEqual(self.binding["lakehouse_name"], "Hydro_GeoContext_V6")
        self.assertEqual(self.call("assert_owned_agent", self.agent(), self.binding), AGENT)
        self.call("validate_lakehouse", self.lakehouse(), self.binding)

    def test_wrong_workspace_lakehouse_and_unsafe_suffix_fail(self):
        for args in [
            (str(UUID(int=10)), "V6", self.context),
            ("", "V6", {**self.context, "defaultLakehouseName": "Energy_IQ_LakehouseRTI_V6"}),
            ("", "V6", {**self.context, "defaultLakehouseWorkspaceId": str(UUID(int=11))}),
            ("", "../other", self.context),
            ("", "V6", {**self.context, "defaultLakehouseId": ""}),
        ]:
            with self.subTest(args=args), self.assertRaises(self.error):
                self.call("binding_from_context", *args)

    def test_existing_hydro_intelligence_or_unowned_agent_is_never_adopted(self):
        cases = [
            self.agent(displayName="RTI_Demo_Agent_V6"),
            self.agent(description="Manually created agent"),
            self.agent(type="Ontology"),
            self.agent(workspaceId=str(UUID(int=12))),
            self.agent(displayName="Hydro_Map_Agent_v6"),
        ]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(self.error):
                self.call("assert_owned_agent", item, self.binding)
        self.assertIsNone(self.call("select_owned_agent", [self.agent(displayName="RTI_Demo_Agent_V6")], self.binding))
        with self.assertRaisesRegex(self.error, "not proof of ownership"):
            self.call("select_owned_agent", [self.agent(description="other owner")], self.binding)

    def test_duplicate_names_are_refused_not_first_match_wins(self):
        with self.assertRaisesRegex(self.error, "Multiple"):
            self.call("select_owned_agent", [self.agent(), self.agent(id=str(UUID(int=13)))], self.binding)

    def test_schema_enabled_or_unready_sql_endpoint_fails(self):
        item = self.lakehouse()
        item["properties"]["defaultSchema"] = "dbo"
        with self.assertRaisesRegex(self.error, "schema-disabled"):
            self.call("validate_lakehouse", item, self.binding)
        item = self.lakehouse()
        item["properties"]["sqlEndpointProperties"]["provisioningStatus"] = "InProgress"
        with self.assertRaisesRegex(self.error, "not ready"):
            self.call("validate_lakehouse", item, self.binding)
        item = self.lakehouse()
        item["properties"]["oneLakeTablesPath"] = f"https://onelake.example.invalid/{WORKSPACE}/{UUID(int=99)}/Tables"
        with self.assertRaisesRegex(self.error, "does not match"):
            self.call("validate_lakehouse", item, self.binding)

    def test_model_ownership_and_physical_location_are_required(self):
        table = self.ns["ENTITY_TABLE"]
        self.call("validate_owned_table", table, {self.ns["OWNER_PROPERTY"]: self.ns["OWNER_MARKER"]})
        with self.assertRaisesRegex(self.error, "unowned"):
            self.call("validate_owned_table", table, {})
        with self.assertRaises(self.error):
            self.call("validate_owned_table", "geo_map_features", {self.ns["OWNER_PROPERTY"]: self.ns["OWNER_MARKER"]})
        self.call("validate_table_location", f"abfss://{WORKSPACE}@onelake.example.invalid/{LAKEHOUSE}/Tables/{table}", table, self.binding)
        for location in [
            f"abfss://{WORKSPACE}@onelake.example.invalid/{LAKEHOUSE}/Tables/dbo/{table}",
            f"abfss://{UUID(int=20)}@onelake.example.invalid/{LAKEHOUSE}/Tables/{table}",
            f"abfss://{WORKSPACE}@untrusted.invalid/{LAKEHOUSE}/Tables/{table}",
            f"file:///lakehouse/default/Tables/{table}",
        ]:
            with self.subTest(location=location), self.assertRaises(self.error):
                self.call("validate_table_location", location, table, self.binding)

    def test_read_model_writer_cannot_target_operational_or_imported_tables(self):
        spark = Mock()
        models = self.ns["MapReadModels"](spark, self.binding)
        for table in ("geo_map_features", "geo_source_status", "rti_demo_settings", "silver_equipment"):
            with self.subTest(table=table), self.assertRaisesRegex(self.error, "outside"):
                models.write_owned(table, Mock())
        spark.sql.assert_not_called()

    def test_unchanged_sources_reuse_ready_projection_without_spark_rebuild(self):
        from datetime import datetime, timezone
        spark = Mock()
        models = self.ns["MapReadModels"](spark, self.binding)
        versions = {name: 7 for name in self.ns["SOURCE_TABLES"]}
        state = {"state": "ready", "read_model_run_id": RUN_ID,
                 "built_at_utc": datetime.now(timezone.utc), "source_versions_json": json.dumps(versions),
                 "entity_count": 120, "link_count": 3}
        row = Mock()
        row.asDict.return_value = state
        spark.table.return_value.limit.return_value.collect.return_value = [row]
        models.preflight = Mock()
        models.ensure_models = Mock()
        models.source_versions = Mock(return_value=versions)
        models.write_owned = Mock()
        result = models.build()
        self.assertTrue(result["projection_reused"])
        self.assertEqual(result["read_model_run_id"], RUN_ID)
        models.preflight.assert_called_once()
        models.ensure_models.assert_called_once()
        models.write_owned.assert_not_called()
        spark.conf.set.assert_not_called()
        for changed in ({**state, "state": "building"},
                        {**state, "state": "error"},
                        {**state, "source_versions_json": json.dumps({**versions, "_projection_contract": 999})},
                        {**state, "source_versions_json": "{}"}):
            row.asDict.return_value = changed
            self.assertIsNone(models.reusable_projection(versions))
        row.asDict.return_value = {**state, "entity_count": 0}
        with self.assertRaisesRegex(self.error, "invalid build metadata"):
            models.reusable_projection(versions)

    def test_reuse_rejects_source_changes_during_verification(self):
        from datetime import datetime, timezone
        models = self.ns["MapReadModels"](Mock(), self.binding)
        state = {"state": "ready", "read_model_run_id": RUN_ID, "built_at_utc": datetime.now(timezone.utc),
                 "source_versions_json": '{"source":1}', "entity_count": 1, "link_count": 0}
        row = Mock()
        row.asDict.return_value = state
        models.spark.table.return_value.limit.return_value.collect.return_value = [row]
        models.source_versions = Mock(return_value={"source": 2})
        with self.assertRaisesRegex(self.error, "changed during reuse"):
            models.reusable_projection({"source": 1})

    def test_missing_model_cannot_reuse_ready_state_after_creating_empty_tables(self):
        models = self.ns["MapReadModels"](Mock(), self.binding)
        models.preflight = Mock()
        models.ensure_models = Mock()
        models.source_versions = Mock(return_value={})
        models.spark.catalog.tableExists.return_value = False
        models.reusable_projection = Mock()
        # Stop at the fresh-build import: this offline fixture deliberately has no Spark.
        with patch.dict(sys.modules, {"pyspark": None}), self.assertRaises(ModuleNotFoundError):
            models.build()
        models.ensure_models.assert_called_once()
        models.reusable_projection.assert_not_called()

    def test_restricted_native_spn_is_rejected_without_credentials_workaround(self):
        def token(claims):
            return "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
        user = token({"idtyp": "user", "scp": "Item.ReadWrite.All"})
        self.assertEqual(self.call("check_native_identity", user), user)
        for claims in ({"idtyp": "app", "appid": "fixture"}, {"appid": "fixture", "roles": ["Lakehouse.ReadWrite.All"]}):
            with self.assertRaisesRegex(self.error, "Stop before writes"):
                self.call("check_native_identity", token(claims))
        with self.assertRaises(self.error):
            self.call("check_native_identity", "not-a-token")


class DefinitionTests(HelpersTest):
    def test_official_lakehouse_definition_selects_only_governed_tables_columns(self):
        source = self.call("build_source_definition", self.binding)
        self.assertEqual(source["type"], "lakehouse")
        self.assertEqual(source["artifactId"], LAKEHOUSE)  # not the SQL endpoint ID
        self.assertEqual(source["workspaceId"], WORKSPACE)
        self.assertEqual(source["elements"][0]["type"], "lakehouse_tables.schema")
        self.assertFalse(source["elements"][0]["is_selected"])
        selected = self.call("selected_tables_and_columns", source)
        expected = {table: {name for name, _ in fields} for table, fields in self.ns["SELECTED_SCHEMAS"].items()}
        self.assertEqual(selected, expected)
        self.assertEqual(set(selected), {"geo_map_agent_entities", "geo_map_agent_links", "geo_map_agent_state", "geo_source_status"})
        self.assertNotIn("geometry_json", selected["geo_map_agent_entities"])
        self.assertNotIn("properties_json", selected["geo_map_agent_entities"])
        self.assertNotIn("geo_map_features", selected)

    def test_definition_is_deterministic_and_preserves_server_metadata(self):
        original = self.minimal_parts()
        before = copy.deepcopy(original)
        one = self.call("desired_definition", original, self.binding)
        two = self.call("desired_definition", original, self.binding)
        self.assertEqual(one, two)
        self.assertEqual(original, before)
        parts = {part["path"]: part for part in one["parts"]}
        self.assertEqual(parts[".platform"], original[".platform"])
        stage = self.call("decode_part", parts[self.ns["DRAFT_STAGE_PART"]])
        self.assertFalse(stage["experimental"]["enableExperimentalFeatures"])
        self.assertEqual(stage["aiInstructions"], self.ns["AI_INSTRUCTIONS"])
        self.call("inspect_definition", parts, self.binding, require_selection=True)

    def test_foreign_sources_and_unapproved_parts_are_refused(self):
        path = self.call("source_part_path", self.binding)
        for field, value in [("artifactId", str(UUID(int=90))), ("workspaceId", str(UUID(int=91))),
                             ("type", "ontology"), ("displayName", "Synthetic_STID")]:
            parts = self.desired_parts()
            source = self.call("decode_part", parts[path])
            source[field] = value
            parts[path] = self.call("encode_part", path, source)
            with self.subTest(field=field), self.assertRaises(self.error):
                self.call("desired_definition", parts, self.binding)
        parts = self.desired_parts()
        parts["Files/Config/draft/ontology-RTI_Demo/datasource.json"] = self.call("encode_part", "other", {})
        with self.assertRaisesRegex(self.error, "Unexpected"):
            self.call("desired_definition", parts, self.binding)

    def test_unknown_tables_broad_schema_selection_and_geometry_are_refused(self):
        for mutation in ("table", "schema", "geometry"):
            parts = self.desired_parts()
            path = self.call("source_part_path", self.binding)
            source = self.call("decode_part", parts[path])
            schema = source["elements"][0]
            if mutation == "schema":
                schema["is_selected"] = True
            elif mutation == "table":
                schema["children"][0]["display_name"] = "silver_equipment"
            else:
                schema["children"][0]["children"][0]["display_name"] = "geometry_json"
            parts[path] = self.call("encode_part", path, source)
            with self.subTest(mutation=mutation), self.assertRaises(self.error):
                self.call("inspect_definition", parts, self.binding)

    def test_published_readback_must_match_selected_source_and_instructions(self):
        parts = self.desired_parts()
        for path, part in list(parts.items()):
            if "/draft/" in path:
                target = path.replace("/draft/", "/published/")
                parts[target] = {**part, "path": target}
        self.call("inspect_definition", parts, self.binding, True, "published")
        parts[self.ns["PUBLISHED_STAGE_PART"]] = self.call("encode_part", self.ns["PUBLISHED_STAGE_PART"], {"aiInstructions": "wrong"})
        with self.assertRaisesRegex(self.error, "instructions"):
            self.call("inspect_definition", parts, self.binding, True, "published")

    def test_invalid_base64_duplicate_parts_and_legacy_definition_do_not_become_empty_success(self):
        with self.assertRaises(self.error):
            self.call("decode_part", {"payloadType": "InlineBase64", "payload": "not base64"})
        root = self.minimal_parts()[self.ns["ROOT_PART"]]
        with self.assertRaisesRegex(self.error, "duplicate"):
            self.call("definition_parts", {"definition": {"parts": [root, root]}})
        with self.assertRaisesRegex(self.error, "Unexpected"):
            self.call("inspect_definition", {"DataAgentV1.json": self.call("encode_part", "DataAgentV1.json", {})}, self.binding)


class FolderPlacementTests(HelpersTest):
    def api_in_folder(self, folders=None, existing=None):
        api = self.api(Mock(request=Mock(return_value=Response())))
        folder = {"id": FOLDER, "displayName": "RTI_DEMO_V6", "workspaceId": WORKSPACE}
        api.list_items = Mock(side_effect=lambda kind: (
            [folder] if folders is None else folders
        ) if kind == "folders" else ([] if existing is None else [existing]))
        return api

    def test_existing_root_agent_moves_without_recreation(self):
        api = self.api_in_folder(existing=self.agent(folderId=None))
        api.get = Mock(side_effect=[self.agent(folderId=None), self.agent()])
        result = api.ensure_agent()
        self.assertEqual(result["id"], AGENT)
        self.assertEqual(result["folderId"], FOLDER)
        api.session.request.assert_called_once()
        request = api.session.request.call_args
        self.assertEqual(request.args, ("POST", f"{self.ns['FABRIC_BASE']}/v1/workspaces/{WORKSPACE}/items/{AGENT}/move"))
        self.assertEqual(request.kwargs["json"], {"targetFolderId": FOLDER})

    def test_correctly_placed_agent_reruns_are_read_only(self):
        api = self.api_in_folder(existing=self.agent())
        api.get = Mock(return_value=self.agent())
        self.assertEqual(api.ensure_agent()["folderId"], FOLDER)
        self.assertEqual(api.ensure_agent()["folderId"], FOLDER)
        api.session.request.assert_not_called()

    def test_new_agent_is_created_in_resolved_folder(self):
        api = self.api_in_folder()
        api.session.request.return_value = Response(self.agent(), status=201)
        api.get = Mock(return_value=self.agent())
        self.assertEqual(api.ensure_agent()["folderId"], FOLDER)
        api.session.request.assert_called_once()
        self.assertEqual(api.session.request.call_args.kwargs["json"]["folderId"], FOLDER)

    def test_missing_ambiguous_or_nested_demo_folder_never_mutates(self):
        folder = {"id": FOLDER, "displayName": "RTI_DEMO_V6"}
        for folders in ([], [folder, {**folder, "id": str(UUID(int=9))}],
                        [{**folder, "parentFolderId": str(UUID(int=9))}]):
            with self.subTest(folders=folders):
                api = self.api_in_folder(folders, self.agent())
                with self.assertRaisesRegex(self.error, "folder"):
                    api.ensure_agent()
                api.session.request.assert_not_called()

    def test_folder_readback_failure_is_not_reported_as_success(self):
        api = self.api_in_folder(existing=self.agent(folderId=None))
        api.get = Mock(return_value=self.agent(folderId=None))
        with self.assertRaisesRegex(self.error, "folder readback"):
            api.ensure_agent()

    def test_folder_lookup_paginates_before_resolving_target(self):
        session = Mock(request=Mock(side_effect=[
            Response({"value": [], "continuationToken": "next/+"}),
            Response({"value": [{"id": FOLDER, "displayName": "RTI_DEMO_V6"}]}),
        ]))
        api = self.api(session)
        self.assertEqual(api.resolve_folder(), FOLDER)
        self.assertIn("/folders?continuationToken=next%2F%2B", session.request.call_args.args[1])

    def test_folder_name_uses_the_current_environment_suffix(self):
        api = self.api_in_folder(folders=[
            {"id": str(UUID(int=9)), "displayName": "RTI_DEMO_V6"},
            {"id": FOLDER, "displayName": "RTI_DEMO_PROD_42"},
        ])
        api.binding = {**self.binding, "env_suffix": "PROD_42"}
        self.assertEqual(api.resolve_folder(), FOLDER)
        api.session.request.assert_not_called()

    def test_folder_writes_cannot_target_another_agent_or_folder(self):
        api = self.api()
        api.agent_id, api.folder_id = AGENT, FOLDER
        for item, body in ((str(UUID(int=9)), {"targetFolderId": FOLDER}),
                           (AGENT, {"targetFolderId": str(UUID(int=9))}),
                           (AGENT, {"targetFolderId": None})):
            with self.subTest(item=item, body=body), self.assertRaises(self.error):
                api.request("POST", f"/v1/workspaces/{WORKSPACE}/items/{item}/move", body)
        with self.assertRaisesRegex(self.error, "created"):
            api.request("POST", f"/v1/workspaces/{WORKSPACE}/dataAgents", {
                "displayName": self.binding["agent_name"], "description": self.call("owned_description", self.binding),
                "folderId": str(UUID(int=9)),
            })
        api.session.request.assert_not_called()


class RestTests(HelpersTest):
    def test_matching_draft_and_published_contract_do_not_republish(self):
        parts = self.desired_parts()
        for path, part in list(parts.items()):
            if "/draft/" in path:
                published_path = path.replace("/draft/", "/published/")
                parts[published_path] = self.call("encode_part", published_path, self.call("decode_part", part))
        for stage in ("draft", "published"):
            path = self.call("source_part_path", self.binding, stage)
            source = self.call("decode_part", parts[path])
            source["elements"][0]["children"][0]["children"][0]["description"] = None
            parts[path] = self.call("encode_part", path, source)
        api = self.api()
        api.get_definition = Mock(return_value=parts)
        api.verify_owned_target = Mock()
        api.request = Mock()
        api.configure_and_publish()
        api.request.assert_not_called()
        api.get_definition.assert_called_once()

    def test_matching_draft_with_missing_or_changed_publication_still_publishes(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                parts = self.desired_parts()
                published = copy.deepcopy(parts)
                for path, part in list(parts.items()):
                    if "/draft/" in path:
                        published_path = path.replace("/draft/", "/published/")
                        published[published_path] = self.call("encode_part", published_path, self.call("decode_part", part))
                initial = copy.deepcopy(published if stale else parts)
                if stale:
                    path = self.ns["PUBLISHED_STAGE_PART"]
                    initial[path] = self.call("encode_part", path, {"aiInstructions": "older instructions"})
                api = self.api()
                api.get_definition = Mock(side_effect=[initial, published])
                api.verify_owned_target = Mock()
                api.request = Mock(return_value=Response())
                api.configure_and_publish()
                api.request.assert_called_once()
                self.assertTrue(api.request.call_args.args[1].endswith("/staging/publish"))

    def test_discovery_paginates_and_encodes_tokens(self):
        session = Mock(request=Mock(side_effect=[
            Response({"value": [], "continuationToken": "a/b+=c"}),
            Response({"value": [self.agent()]}),
        ]))
        api = self.api(session)
        self.assertEqual(api.list_items("dataAgents"), [self.agent()])
        self.assertIn("continuationToken=a%2Fb%2B%3Dc", session.request.call_args.args[1])
        self.assertEqual(session.request.call_args.kwargs["headers"]["x-ms-fabric-skill"], "spark-cli")

    def test_foreign_continuations_redirects_and_unowned_writes_are_blocked(self):
        api = self.api()
        for path in [
            "https://attacker.invalid/v1/operations/" + OPERATION,
            f"/v1/workspaces/{UUID(int=99)}/dataAgents",
            f"/v1/workspaces/{WORKSPACE}/../other/dataAgents",
        ]:
            with self.subTest(path=path), self.assertRaises(self.error):
                api.checked_url(path)
        with self.assertRaisesRegex(self.error, "Unowned"):
            api.request("POST", f"/v1/workspaces/{WORKSPACE}/dataAgents/{AGENT}/updateDefinition", {})
        with self.assertRaisesRegex(self.error, "read-only"):
            api.request("POST", f"/v1/workspaces/{WORKSPACE}/dataAgents", {}, read_only=True)
        api.session.request.assert_not_called()

    def test_owned_reuse_never_posts_create_or_touches_hydro_intelligence(self):
        session = Mock(request=Mock(side_effect=[
            Response({"value": [{"id": FOLDER, "displayName": "RTI_DEMO_V6"}]}),
            Response({"value": [
                self.agent(displayName="RTI_Demo_Agent_V6", id=str(UUID(int=88)), description="Hydro Intelligence"),
                self.agent(),
            ]}),
            Response(self.agent()),
        ]))
        api = self.api(session)
        self.assertEqual(api.ensure_agent()["id"], AGENT)
        self.assertEqual(api.agent_id, AGENT)
        self.assertTrue(all(call.args[0] == "GET" for call in session.request.call_args_list))

    def test_mutations_not_retried_and_403_never_success(self):
        session = Mock(request=Mock(return_value=Response({"errorCode": "Forbidden"}, 403)))
        api = self.api(session)
        api.folder_id = FOLDER
        with self.assertRaisesRegex(self.error, "Forbidden"):
            api.request("POST", f"/v1/workspaces/{WORKSPACE}/dataAgents", {
                "displayName": self.binding["agent_name"], "description": self.call("owned_description", self.binding),
                "folderId": FOLDER,
            }, accepted=(201, 202))
        session.request.assert_called_once()
        self.ns["time"].sleep.assert_not_called()

    def test_read_retry_is_bounded_and_closes_prior_responses(self):
        first = Response({}, 429, {"Retry-After": "99999"})
        session = Mock(request=Mock(side_effect=[first, Response({"value": []})]))
        api = self.api(session)
        self.assertEqual(api.list_items("dataAgents"), [])
        self.assertTrue(first.closed)
        self.ns["time"].sleep.assert_called_once_with(30.0)

    def test_202_without_operation_location_is_never_publish_success(self):
        api = self.api()
        with self.assertRaisesRegex(self.error, "202 response"):
            api.wait_operation(Response(status=202))
        api.session.request.assert_not_called()

    def test_lro_result_is_read_only_after_success(self):
        api = self.api(Mock(request=Mock(side_effect=[
            Response({"status": "Running"}), Response({"status": "Succeeded"}),
            Response({"definition": {"parts": []}}),
        ])))
        response = Response(status=202, headers={"Location": f"{self.ns['FABRIC_BASE']}/v1/operations/{OPERATION}"})
        result = api.wait_operation(response, result=True)
        self.assertIn("definition", result)
        self.assertTrue(api.session.request.call_args.args[1].endswith("/result"))
        self.assertTrue(all(call.args[0] == "GET" for call in api.session.request.call_args_list))

    def test_lro_uses_operation_id_instead_of_the_backend_location(self):
        api = self.api(Mock(request=Mock(side_effect=[
            Response({"status": "Succeeded"}), Response({"definition": {"parts": []}}),
        ])))
        response = Response(status=202, headers={
            "x-ms-operation-id": OPERATION,
            "Location": f"https://wabi-regional-backend.analysis.windows.net/v1/operations/{OPERATION}",
        })
        api.wait_operation(response, result=True)
        self.assertTrue(response.closed)
        self.assertEqual([call.args[1] for call in api.session.request.call_args_list], [
            f"{self.ns['FABRIC_BASE']}/v1/operations/{OPERATION}",
            f"{self.ns['FABRIC_BASE']}/v1/operations/{OPERATION}/result",
        ])
        self.assertTrue(all(call.kwargs["headers"]["x-ms-fabric-skill"] == "spark-cli"
                            for call in api.session.request.call_args_list))

    def test_lro_supports_canonical_operation_location_header(self):
        api = self.api(Mock(request=Mock(return_value=Response({"status": "Succeeded"}))))
        api.wait_operation(Response(status=202, headers={"Operation-Location": f"/v1/operations/{OPERATION}"}))
        self.assertEqual(api.session.request.call_args.args[1], f"{self.ns['FABRIC_BASE']}/v1/operations/{OPERATION}")

    def test_lro_never_forwards_credentials_to_backend_without_operation_id(self):
        api = self.api()
        with self.assertRaisesRegex(self.error, "non-Fabric"):
            api.wait_operation(Response(status=202, headers={
                "Location": f"https://wabi-regional-backend.analysis.windows.net/v1/operations/{OPERATION}",
            }))
        api.session.request.assert_not_called()

    def test_invalid_operation_id_never_falls_back_to_location(self):
        api = self.api()
        with self.assertRaisesRegex(self.error, "operation ID"):
            api.wait_operation(Response(status=202, headers={
                "x-ms-operation-id": "../other",
                "Location": f"{self.ns['FABRIC_BASE']}/v1/operations/{OPERATION}",
            }))
        api.session.request.assert_not_called()


class InstructionsAndModelsTests(HelpersTest):
    def test_norwegian_inventory_questions_do_not_filter_nullable_asset_country(self):
        instructions = self.ns["AI_INSTRUCTIONS"]
        self.assertIn("country_code is not an asset-country filter", instructions)
        self.assertIn("zero asset result", instructions)
        question, query = next((question, query) for question, query in self.ns["FEWSHOTS"]
                               if "Norwegian hydropower plants" in question)
        self.assertIn("COUNT_BIG(*)", query)
        self.assertIn("SUM(e.installed_capacity_mw)", query)
        self.assertIn("e.layer_id='hydro-plants'", query)
        self.assertNotIn("country_code", query)
        source = self.call("build_source_definition", self.binding)
        self.assertIn("country_code is not an asset-country filter", source["dataSourceInstructions"])
        self.assertIn("Norway", source["dataSourceInstructions"])
        self.assertIn("geo_source_status.row_count", source["dataSourceInstructions"])
        columns = source["elements"][0]["children"][0]["children"]
        country = next(column for column in columns if column["display_name"] == "country_code")
        self.assertIn("not an asset-country filter", country["description"])

    def test_navigation_lookup_includes_requested_asset_facts(self):
        examples = self.ns["FEWSHOTS"]
        navigation = next(query for question, query in examples if "Adamselv for navigation" in question)
        self.assertIn("e.installed_capacity_mw", navigation)
        self.assertIn("e.owner", navigation)
        self.assertIn("e.price_area", navigation)
        plant_count = next(query for question, query in examples if "complete imported dataset" in question)
        self.assertIn("COUNT_BIG(*)", plant_count)
        self.assertIn("e.layer_id='hydro-plants'", plant_count)
        self.assertNotIn("has_geometry", plant_count)
        self.assertIn("omitted column is not evidence", self.ns["AI_INSTRUCTIONS"])

    def test_grounding_freshness_viewport_and_untrusted_provider_constraints(self):
        instructions = self.ns["AI_INSTRUCTIONS"]
        for requirement in (
            "UNTRUSTED DATA", "capped/truncated", "COUNT_BIG", "whole-dataset",
            "last_success_at", "state='building'", "Only", "actual source timestamp",
            "NOT MW/MVA", "IMPORT", "30-day PUBLICATION", "rules-v1",
            "NOT official Nord Pool", "Unranked/null differs from low", "BOTH",
            "frontend independently re-reads",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, instructions)
        self.assertLess(len(instructions), 15000)

    def test_navigation_marker_requires_verified_unambiguous_ids_and_has_no_coordinate_action(self):
        instructions = self.ns["AI_INSTRUCTIONS"]
        self.assertIn('<!--map-focus:{"feature_id":', instructions)
        self.assertIn('"layer_id":', instructions)
        self.assertIn("<=512", instructions)
        self.assertIn("Only when the user requests", instructions)
        self.assertIn("Never invent IDs, coordinates or bounds", instructions)
        self.assertIn("If a name is ambiguous", instructions)
        self.assertIn("no coordinates, URLs, commands or extra keys", instructions)
        self.assertIn("is_navigable=true", instructions)
        for _, query in self.ns["FEWSHOTS"]:
            self.assertTrue(query.startswith("SELECT "))
            self.assertNotIn("INSERT", query)
            self.assertNotIn("UPDATE", query)
        self.assertIn("l.message_version=m.message_version", self.ns["FEWSHOTS"][-1][1])

    def test_slim_schema_preserves_needed_facts_without_geometry_properties_or_capacity_guess(self):
        fields = dict(self.ns["ENTITY_FIELDS"])
        for column in ("feature_id", "layer_id", "label", "owner", "price_area", "plant_status",
                       "geographic_precision", "importance_method", "importance_reason"):
            self.assertEqual(fields[column], "string")
        for column in ("installed_capacity_mw", "gross_head_m", "voltage_kv", "longitude", "latitude",
                       "min_lon", "min_lat", "max_lon", "max_lat", "filling_fraction", "frequency_hz"):
            self.assertEqual(fields[column], "double")
        self.assertEqual(fields["in_operation"], "boolean")
        self.assertEqual(fields["source_layer"], "long")
        self.assertEqual(fields["message_version"], "long")
        self.assertEqual(fields["observed_at_utc"], "timestamp")
        self.assertNotIn("geometry_json", fields)
        self.assertNotIn("properties_json", fields)
        source = NOTEBOOK.read_text()
        projection = source.split("def entity_projection(", 1)[1].split("def validate_entity_metrics", 1)[0]
        self.assertIn('(layer == "hydro-plants") & (value >= 0)', projection)
        self.assertNotIn('prop("voltage_kv", "double") *', projection)
        self.assertEqual(set(self.ns["MODEL_SCHEMAS"]), {"geo_map_agent_entities", "geo_map_agent_links", "geo_map_agent_state"})

    def test_physical_measure_projection_excludes_negative_and_nonfinite_source_values(self):
        projection = next(node for node in ast.parse(NOTEBOOK.read_text()).body
                          if isinstance(node, ast.FunctionDef) and node.name == "entity_projection")
        loop = next(node for node in projection.body if isinstance(node, ast.For)
                    and isinstance(node.iter, ast.Tuple)
                    and [value.value for value in node.iter.elts] == [
                        "installed_capacity_mw", "gross_head_m", "mean_annual_production_gwh_1991_2020"])
        code = compile(ast.Module(body=[loop], type_ignores=[]), str(NOTEBOOK), "exec")

        class Column:
            def __init__(self, value):
                self.value = value

            def __eq__(self, other):
                return Column(self.value == other)

            def __ge__(self, other):
                return Column(self.value is not None and self.value >= other)

            def __lt__(self, other):
                return Column(self.value is not None and self.value < other)

            def __and__(self, other):
                return Column(self.value and other.value)

            def __invert__(self):
                return Column(not self.value)

        functions = types.SimpleNamespace(
            isnan=lambda value: Column(value.value is not None and math.isnan(value.value)),
            when=lambda condition, value: value.value if condition.value else None,
        )
        for layer in ("hydro-plants", "transformers"):
            for value in (50.125, 0.0, -0.01, -100.0, float("nan"), float("inf"), -float("inf"), None):
                with self.subTest(layer=layer, value=value):
                    context = {"F": functions, "layer": Column(layer), "expressions": {},
                               "prop": lambda name, kind: Column(value)}
                    exec(code, context)
                    expected = value if layer == "hydro-plants" and value is not None and math.isfinite(value) and value >= 0 else None
                    self.assertEqual(set(context["expressions"].values()), {expected})
        instructions = self.ns["AI_INSTRUCTIONS"]
        self.assertIn("projected as NULL, never zero", instructions)
        self.assertIn("Do not replace it with a SQL bounding-box aggregate", instructions)

    def test_projection_metrics_fail_on_empty_partial_or_duplicate_models(self):
        good = {"rows": 20, "identities": 20, "invalid": 0, "area_rows": 9, "asset_rows": 10}
        self.call("validate_entity_metrics", good)
        for changes in ({"rows": 0}, {"identities": 19}, {"invalid": 1}, {"area_rows": 8}, {"asset_rows": 0}):
            with self.subTest(changes=changes), self.assertRaises(self.error):
                self.call("validate_entity_metrics", {**good, **changes})

    def test_property_projection_uses_one_json_tuple_with_every_required_root(self):
        projection = next(node for node in ast.parse(NOTEBOOK.read_text(encoding="utf-8")).body
                          if isinstance(node, ast.FunctionDef) and node.name == "entity_projection")
        assignments = [node for node in projection.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id in (
                           "flat_properties", "property_names",
                       ) for target in node.targets)]
        namespace = {}
        exec(compile(ast.Module(body=assignments, type_ignores=[]), "property-keys", "exec"), namespace)
        requested = {
            node.args[0].value.split(".")[0] for node in ast.walk(projection)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "prop" and isinstance(node.args[0], ast.Constant)
        }
        self.assertTrue(requested <= set(namespace["property_names"]))
        self.assertTrue(namespace["flat_properties"] <= set(namespace["property_names"]))
        parsers = [node for node in ast.walk(projection) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == "json_tuple"]
        self.assertEqual(len(parsers), 1)
        self.assertEqual(ast.literal_eval(parsers[0].args[0]), "properties_json")


class MCPAndOrchestrationTests(HelpersTest):
    def test_mcp_question_argument_discovered_not_guessed(self):
        tool = {"name": "ask_actual_tool", "inputSchema": {"properties": {"question_text": {"type": "string"}},
                                                         "required": ["question_text"]}}
        self.assertEqual(self.call("mcp_question_argument", tool), "question_text")
        tool["inputSchema"]["properties"]["other"] = {"type": "string"}
        tool["inputSchema"]["required"].append("other")
        with self.assertRaisesRegex(self.error, "safely identify"):
            self.call("mcp_question_argument", tool)

    def test_sse_response_matching_and_errors(self):
        response = Response(headers={"Content-Type": "text/event-stream"}, events=[
            ": heartbeat", "", 'data: {"jsonrpc":"2.0","method":"notification"}', "",
            'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}', "",
        ])
        self.assertEqual(self.call("read_mcp_response", response, 2, 100), {"tools": []})
        with self.assertRaises(self.error):
            self.call("mcp_result", {"jsonrpc": "2.0", "id": 2, "error": {"code": -1}}, 2)
        with self.assertRaises(self.error):
            self.call("mcp_result", {"jsonrpc": "2.0", "id": 99, "result": {}}, 2)

    def test_grounding_canary_never_supplies_the_expected_nonce_to_model(self):
        mcp = self.ns["MapAgentMCP"](Mock(), self.binding, AGENT, lambda: "offline")
        mcp.rpc = Mock(side_effect=[
            {"protocolVersion": "2025-03-26"}, None,
            {"tools": [{"name": "actual-tool", "inputSchema": {"properties": {"question": {"type": "string"}}, "required": ["question"]}}]},
            {"content": [{"type": "text", "text": f"state=ready read_model_run_id={RUN_ID}"}]},
        ])
        result = mcp.verify_grounding(RUN_ID)
        self.assertEqual(result["ai_readiness"], "grounding_canary_passed")
        question = mcp.rpc.call_args.args[1]["arguments"]["question"]
        self.assertNotIn(RUN_ID, question)
        self.assertIn("geo_map_agent_state", question)
        self.assertEqual([call.args[0] for call in mcp.rpc.call_args_list],
                         ["initialize", "notifications/initialized", "tools/list", "tools/call"])

    def test_published_but_ungrounded_is_not_success(self):
        for last in ({"isError": True, "content": [{"type": "text", "text": "SQL metadata not ready"}]},
                     {"content": [{"type": "text", "text": "Ready, probably."}]}):
            mcp = self.ns["MapAgentMCP"](Mock(), self.binding, AGENT, lambda: "offline")
            mcp.rpc = Mock(side_effect=[
                {"protocolVersion": "2025-03-26"}, None,
                {"tools": [{"name": "actual-tool", "inputSchema": {"properties": {"query": {"type": "string"}}}}]},
                last, last, last,
            ])
            with self.assertRaisesRegex(self.error, "failed after 3 attempts"):
                mcp.verify_grounding(RUN_ID)
            self.assertEqual(mcp.rpc.call_count, 6)

    def test_grounding_canary_waits_for_current_sql_snapshot_without_republishing(self):
        mcp = self.ns["MapAgentMCP"](Mock(), self.binding, AGENT, lambda: "offline")
        mcp.rpc = Mock(side_effect=[
            {"protocolVersion": "2025-03-26"}, None,
            {"tools": [{"name": "actual-tool", "inputSchema": {"properties": {"query": {"type": "string"}}}}]},
            {"isError": True, "content": [{"type": "text", "text": "SQL metadata not ready"}]},
            {"content": [{"type": "text", "text": f"state=ready read_model_run_id={str(UUID(int=99))}"}]},
            {"content": [{"type": "text", "text": f"state=ready read_model_run_id={RUN_ID}"}]},
        ])
        self.assertEqual(mcp.verify_grounding(RUN_ID)["ai_readiness"], "grounding_canary_passed")
        self.assertEqual(self.ns["time"].sleep.call_count, 2)
        self.assertEqual([call.args[2] for call in mcp.rpc.call_args_list[-3:]], [3, 4, 5])

    def test_ownership_failure_precedes_read_model_or_agent_writes(self):
        api, models = Mock(), Mock()
        api.discover.side_effect = self.error("unowned dedicated agent")
        with self.assertRaises(self.error):
            self.call("publish_map_agent", api, models, Mock())
        models.preflight.assert_not_called()
        models.build.assert_not_called()
        api.ensure_agent.assert_not_called()

    def test_end_to_end_summary_only_after_readback_and_canary(self):
        api, models, mcp = Mock(), Mock(), Mock()
        api.binding = self.binding
        api.ensure_agent.return_value = self.agent()
        api.get_definition.return_value = self.minimal_parts()
        models.build.return_value = {"read_model_run_id": RUN_ID}
        mcp.verify_grounding.return_value = {"ai_readiness": "grounding_canary_passed"}
        result = self.call("publish_map_agent", api, models, lambda item_id: mcp)
        self.assertEqual(result["agent_name"], "Hydro_Map_Agent_V6")
        self.assertEqual(result["state"], "ready")
        self.assertEqual(set(result["timings_seconds"]), {
            "discovery_and_source_preflight", "read_models", "configure_and_publish", "grounding_canary",
        })
        mcp.verify_grounding.assert_called_once_with(RUN_ID)
        api.configure_and_publish.assert_called_once()
        mcp.verify_grounding.side_effect = self.error("AI tenant prerequisite missing")
        with self.assertRaisesRegex(self.error, "prerequisite"):
            self.call("publish_map_agent", api, models, lambda item_id: mcp)


class NotebookArtifactTests(HelpersTest):
    def test_platform_identity_and_parameters(self):
        platform = json.loads(PLATFORM.read_text())
        self.assertEqual(platform["config"]["logicalId"], "b3236e30-dd23-4331-a1fc-f3cb977b2506")
        self.assertEqual(platform["metadata"]["displayName"], "Geo_002_publish_map_agent")
        self.assertEqual(platform["metadata"]["type"], "Notebook")
        source = NOTEBOOK.read_text()
        self.assertIn('workspace_id = ""', source)
        self.assertIn('env_suffix = ""', source)
        self.assertIn('geo_lakehouse_id = ""', source)
        self.assertNotIn('if __name__', source)
        self.assertNotIn("getSecret(", source)
        self.assertNotIn("notebook.run(", source)
        self.assertNotIn("notebook.runMultiple(", source)
        self.assertNotIn("SparkSession.builder", source)
        self.assertNotIn("except Exception", source)

    def test_mirror_is_exact_and_all_cells_compile(self):
        source = NOTEBOOK.read_text(encoding="utf-8")
        compile(source, str(NOTEBOOK), "exec")
        actual = json.loads(MIRROR.read_text(encoding="utf-8"))
        self.assertEqual(actual, canonical_to_ipynb(source))
        self.assertEqual(sum(cell["metadata"].get("tags") == ["parameters"] for cell in actual["cells"]), 1)
        for index, cell in enumerate(actual["cells"]):
            self.assertTrue(all(line.endswith("\n") for line in cell["source"]))
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"map agent mirror cell {index}", "exec")
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--generate-mirror"]:
        MIRROR.write_text(json.dumps(canonical_to_ipynb(NOTEBOOK.read_text(encoding="utf-8")),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Generated {MIRROR.relative_to(ROOT)}")
    else:
        unittest.main()
