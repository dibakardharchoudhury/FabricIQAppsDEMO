import base64
import ast
import contextlib
import copy
import io
import json
import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import operational_store_support as support
from ontology_notebook_support import _verify_definition


def part(path, text):
    return {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}


class OntologySourceTopologyTests(unittest.TestCase):
    def fixtures(self, kind="Lakehouse"):
        domains = ({"facilities": "silver_facilities", "equipment": "silver_equipment"}
                   if kind == "Lakehouse" else support.SQL_PROJECT_ENTITIES)
        item_id = "lakehouse" if kind == "Lakehouse" else "sql"
        ontology = [part("expressions.tmdl",
            f'expression Operations =\n\tAzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/ws/{item_id}")\n'
            f'\tannotation ONT_WorkspaceId = ws\n\tannotation ONT_ItemId = {item_id}\n'
            f'\tannotation ONT_ItemKind = {kind}\n')]
        for entity, table in domains.items():
            ontology += [
                part(f"entities/{entity}.tmdl", f"entity {entity}\n\tbackingTable: {entity}\n"),
                part(f"tables/{entity}.tmdl", f"table {entity}\n\tpartition {entity} = entity\n"
                     f"\t\tmode: directLake\n\t\tsource\n\t\t\tentityName: {table}\n"
                     "\t\t\texpressionSource: Operations\n"),
            ]
        context = {"artifactId": "ont", "workspaceId": "ws", "elements": [
            {"display_name": name, "type": "ontology.entity", "is_selected": True}
            for name in domains
        ]}
        source_type = "lakehouse_tables" if kind == "Lakehouse" else "sql_database"
        source = {"artifactId": item_id, "workspaceId": "ws", "type": source_type,
                  "dataSourceInstructions": "Keep operator guidance.", "elements": [
                      {"type": source_type + ".table", "display_name": table, "is_selected": True}
                      for table in domains.values()
                  ]}
        agent = [
            part("Files/Config/draft/stage_config.json", json.dumps({"aiInstructions": "Existing guidance."})),
            part("Files/Config/draft/ontologies/ont/ontology.json", json.dumps(context)),
            part(f"Files/Config/draft/{item_id}-source/datasource.json", json.dumps(source)),
        ]
        return agent, ontology

    def test_removes_only_covered_sources_and_preserves_guidance_idempotently(self):
        agent, ontology = self.fixtures()
        unrelated = part("Files/Config/draft/custom/datasource.json", json.dumps({
            "type": "sql_database", "artifactId": "other", "workspaceId": "ws", "custom": True,
        }))
        agent.append(unrelated)
        before = copy.deepcopy(agent)
        result, removed = support.prune_ontology_duplicate_sources(agent, ontology, "ws", "ont")
        self.assertEqual(removed, ["Files/Config/draft/lakehouse-source/datasource.json"])
        self.assertEqual(agent, before)
        self.assertEqual(result[-1], unrelated)
        instructions = json.loads(base64.b64decode(result[0]["payload"]))["aiInstructions"]
        self.assertIn("Existing guidance.", instructions)
        self.assertIn("Keep operator guidance.", instructions)
        self.assertEqual(support.prune_ontology_duplicate_sources(result, ontology, "ws", "ont"), (result, []))
        self.assertEqual(support.selected_ontology_entities(result, "ws", "ont"), {"facilities", "equipment"})

    def test_keeps_sources_with_unbound_tables_or_unselected_entities(self):
        for mode in ("extra-table", "unselected", "custom-metadata", "wrong-item"):
            with self.subTest(mode=mode):
                agent, ontology = self.fixtures()
                context = json.loads(base64.b64decode(agent[1]["payload"]))
                source = json.loads(base64.b64decode(agent[2]["payload"]))
                if mode == "extra-table":
                    source["elements"].append({"type": "lakehouse_tables.table", "display_name": "Custom", "is_selected": True})
                elif mode == "unselected":
                    context["elements"][0]["is_selected"] = False
                elif mode == "custom-metadata":
                    source["metadata"] = {"operator": "retain"}
                else:
                    source["artifactId"] = "other"
                agent[1] = part(agent[1]["path"], json.dumps(context))
                agent[2] = part(agent[2]["path"], json.dumps(source))
                self.assertEqual(support.prune_ontology_duplicate_sources(agent, ontology, "ws", "ont"), (agent, []))

    def test_fresh_verified_v2_source_is_pruned_before_context_promotion(self):
        agent, ontology = self.fixtures()
        context = json.loads(base64.b64decode(agent[1]["payload"]))
        context["type"] = "ontology"
        agent[1] = part("Files/Config/draft/ontology-source/datasource.json", json.dumps(context))
        result, removed = support.prune_ontology_duplicate_sources(agent, ontology, "ws", "ont")
        self.assertEqual(removed, ["Files/Config/draft/lakehouse-source/datasource.json"])
        self.assertIn(agent[1], result)
        self.assertEqual(support.selected_ontology_entities(result, "ws", "ont"), {"facilities", "equipment"})
        context["type"] = "lakehouse_tables"
        agent[1] = part(agent[1]["path"], json.dumps(context))
        self.assertEqual(support.selected_ontology_entities(agent, "ws", "ont"), set())

    def test_source_promotion_does_not_allow_ambiguous_or_cross_workspace_selection(self):
        agent, _ = self.fixtures()
        context = json.loads(base64.b64decode(agent[1]["payload"]))
        context["type"] = "ontology"
        agent.append(part("Files/Config/draft/ontology-source/datasource.json", json.dumps(context)))
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            support.selected_ontology_entities(agent, "ws", "ont")
        agent.pop(1)
        context["workspaceId"] = "other"
        agent[-1] = part(agent[-1]["path"], json.dumps(context))
        with self.assertRaisesRegex(RuntimeError, "another workspace"):
            support.selected_ontology_entities(agent, "ws", "ont")

    def test_rejects_mismatched_physical_source_and_instruction_overflow(self):
        agent, ontology = self.fixtures()
        stale = copy.deepcopy(ontology)
        text = base64.b64decode(stale[0]["payload"]).decode().replace("/ws/lakehouse", "/ws/other")
        stale[0] = part(stale[0]["path"], text)
        with self.assertRaisesRegex(RuntimeError, "physical binding"):
            support.prune_ontology_duplicate_sources(agent, stale, "ws", "ont")
        agent[0] = part(agent[0]["path"], json.dumps({"aiInstructions": "x" * 15000}))
        with self.assertRaisesRegex(RuntimeError, "instruction limit"):
            support.prune_ontology_duplicate_sources(agent, ontology, "ws", "ont")

    def test_sql_context_association_does_not_remove_its_required_execution_adapter(self):
        agent, ontology = self.fixtures("SQLDatabase")
        self.assertEqual(support.prune_ontology_duplicate_sources(agent, ontology, "ws", "ont"), (agent, []))
        bindings = support.ontology_source_bindings(ontology, "ws")
        self.assertEqual(set(bindings[("SQLDatabase", "sql")]), set(support.SQL_PROJECT_ENTITIES.values()))

    def test_additional_eventhouse_backing_requires_partition_source_identity(self):
        agent, ontology = self.fixtures()
        context = json.loads(base64.b64decode(agent[1]["payload"]))
        context["elements"].append({"display_name": "signal_master", "type": "ontology.entity", "is_selected": True})
        agent[1] = part(agent[1]["path"], json.dumps(context))
        agent.append(part("Files/Config/draft/kusto/datasource.json", json.dumps({
            "type": "kusto", "artifactId": "kql", "workspaceId": "ws",
            "elements": [{"type": "kusto.table", "display_name": "OPCUAEvents", "is_selected": True}],
        })))
        ontology += [
            part("entities/signal_master.tmdl", "entity signal_master\n\tadditionalBackingTable\n"
                 "\t\ttable: OPCUAEvents\n\t\trelationship: Signals\n"),
            part("tables/OPCUAEvents.tmdl", "table OPCUAEvents\n\tpartition OPCUAEvents = m\n"
                 '\t\tmode: directQuery\n\t\tsource = AzureDataExplorer.Contents("https://cluster", "Telemetry", "OPCUAEvents")\n'
                 "\t\tannotation ONT_WorkspaceId = ws\n\t\tannotation ONT_ItemId = kql\n"
                 "\t\tannotation ONT_ItemKind = KQLDatabase\n\t\tannotation ONT_ItemName = Telemetry\n"),
        ]
        _, removed = support.prune_ontology_duplicate_sources(agent, ontology, "ws", "ont")
        self.assertIn("Files/Config/draft/kusto/datasource.json", removed)
        ontology[-1] = part(ontology[-1]["path"], base64.b64decode(ontology[-1]["payload"]).decode().replace(
            "\t\tannotation ONT_ItemId = kql\n", ""))
        _, removed = support.prune_ontology_duplicate_sources(agent, ontology, "ws", "ont")
        self.assertNotIn("Files/Config/draft/kusto/datasource.json", removed)


class OperationalItemLifecycleTests(unittest.TestCase):
    def prepare(self, existing=True, owned=True, move_succeeds=True,
                creation_headers=None, operation_statuses=("Succeeded",)):
        self.item = {
            "id": "operational-sql", "workspaceId": "workspace", "type": "SQLDatabase",
            "displayName": support.OPERATIONAL_STORE_NAME, "folderId": None,
            "description": support.OPERATIONAL_STORE_DESCRIPTION if owned else "Unrelated database",
            "properties": {"serverFqdn": "authoritative.database.fabric.microsoft.com",
                           "databaseName": "unchanged-physical-database"},
        }
        self.calls = []
        statuses = iter(operation_statuses)
        def request(method, path, payload=None):
            self.calls.append((method, path, payload))
            body = self.item
            status = 200
            if method == "GET" and path == "workspaces/workspace/items":
                body = {"value": [copy.deepcopy(self.item)] if existing else []}
            elif method == "GET" and path.startswith("workspaces/workspace/items?rootFolderId="):
                body = {"value": [copy.deepcopy(self.item)] if self.item["folderId"] == "demo-folder" else []}
            elif method == "POST" and path == "workspaces/workspace/sqlDatabases":
                self.item.update(payload)
                status = 202 if creation_headers is not None else 201
            elif method == "GET" and "operations/" in path and not path.split("?")[0].endswith("/result"):
                body = {"status": next(statuses)}
            elif method == "PATCH":
                self.item.update(payload)
            elif method == "POST" and path.endswith("/move"):
                if move_succeeds:
                    self.item["folderId"] = payload["targetFolderId"]
            return Mock(status_code=status, json=lambda: copy.deepcopy(body),
                        headers=creation_headers or {}, text=json.dumps(body))
        return request

    def test_marked_legacy_item_keeps_its_immutable_name_and_moves_without_recreation(self):
        item = support.ensure_operational_item(
            self.prepare(), "workspace", env_suffix="V11", folder_id="demo-folder")
        self.assertEqual(item["id"], "operational-sql")
        self.assertEqual(item["displayName"], "HydroOperationalStore")
        self.assertEqual(item["folderId"], "demo-folder")
        self.assertEqual(item["properties"]["databaseName"], "unchanged-physical-database")
        self.assertIn(("GET", "workspaces/workspace/items?rootFolderId=demo-folder&recursive=false", None),
                      self.calls)
        self.assertFalse(any(method == "POST" and path.endswith("/sqlDatabases")
                             for method, path, _ in self.calls))
        self.assertFalse(any(method == "PATCH" for method, _, _ in self.calls))

    def test_fresh_setup_creates_named_item_and_moves_it_into_the_demo_folder(self):
        item = support.ensure_operational_item(
            self.prepare(existing=False), "workspace", env_suffix="V12", folder_id="demo-folder")
        self.assertEqual(item["displayName"], "RTI_Demo_SQLDatabase_V12")
        self.assertIn(("POST", "workspaces/workspace/sqlDatabases", {
            "displayName": "RTI_Demo_SQLDatabase_V12",
            "description": support.OPERATIONAL_STORE_DESCRIPTION}), self.calls)
        self.assertEqual(item["folderId"], "demo-folder")

    def test_async_sql_creation_uses_shared_fabric_operation_url_handling(self):
        base = "https://api.fabric.microsoft.com/v1"
        regional = "https://wabi-region-redirect.analysis.windows.net/v1/operations/op?tenant=test"
        for headers, operation_url in (
            ({"Location": base + "/operations/0acd697c-1550-43cd-b998-91bfbfbd47c6"},
             base + "/operations/0acd697c-1550-43cd-b998-91bfbfbd47c6"),
            ({"Location": base + "/operations/operation-id"}, base + "/operations/operation-id"),
            ({"location": "/v1/operations/op"}, base + "/operations/op"),
            ({"Operation-Location": "operations/op"}, base + "/operations/op"),
            ({"Location": regional}, regional),
            ({"X-MS-Operation-ID": "operation-id"}, base + "/operations/operation-id"),
            ({"Location": regional, "x-ms-operation-id": "operation-id"}, base + "/operations/operation-id"),
        ):
            with self.subTest(headers=headers), patch.object(support.time, "sleep") as sleep:
                item = support.ensure_operational_item(
                    self.prepare(existing=False, creation_headers=headers,
                                 operation_statuses=("Running", "Succeeded")),
                    "workspace", env_suffix="V12", folder_id="demo-folder")
                self.assertEqual(item["folderId"], "demo-folder")
                self.assertEqual(sum(path == operation_url for _, path, _ in self.calls), 2)
                result_url = operation_url.replace("?tenant=test", "/result?tenant=test") \
                    if "?" in operation_url else operation_url + "/result"
                self.assertIn(("GET", result_url, None), self.calls)
                sleep.assert_called_once_with(3)

    def test_async_sql_creation_rejects_missing_and_untrusted_operation_urls(self):
        for headers in (
            {}, {"Location": "http://api.fabric.microsoft.com/v1/operations/op"},
            {"Location": "https://fabric.microsoft.com.evil.test/operations/op"},
            {"Location": "https://user@api.fabric.microsoft.com/v1/operations/op"},
        ):
            with self.subTest(headers=headers), self.assertRaises(RuntimeError):
                support.ensure_operational_item(
                    self.prepare(existing=False, creation_headers=headers),
                    "workspace", env_suffix="V12", folder_id="demo-folder")
            self.assertFalse(any("operations/" in path for _, path, _ in self.calls))

    def test_async_sql_creation_failure_does_not_move_or_report_success(self):
        for status in ("Failed", "Cancelled"):
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, "SQL creation failed"):
                support.ensure_operational_item(
                    self.prepare(existing=False, creation_headers={
                        "Location": "https://api.fabric.microsoft.com/v1/operations/operation-id"
                    }, operation_statuses=(status,)),
                    "workspace", env_suffix="V12", folder_id="demo-folder")
            self.assertFalse(any(path.endswith("/move") for _, path, _ in self.calls))

    def test_async_sql_creation_timeout_does_not_restart_creation(self):
        request = self.prepare(existing=False, creation_headers={
            "Location": "https://api.fabric.microsoft.com/v1/operations/operation-id"
        })
        with patch.object(support.time, "monotonic", side_effect=(0, 301)):
            with self.assertRaisesRegex(RuntimeError, "exceeded five minutes"):
                support.ensure_operational_item(
                    request, "workspace", env_suffix="V12", folder_id="demo-folder")
        self.assertEqual(sum(method == "POST" for method, _, _ in self.calls), 1)

    def test_embedded_sql_request_adapters_preserve_absolute_operation_urls(self):
        root = Path(__file__).resolve().parents[2]
        for name in ("RTI_001_create_lakehouse_SelfContained", "RTI_001_create_lakehouse_shortcut",
                     "RTI_011_seed_sql_wire_graphql_agent"):
            with self.subTest(notebook=name):
                notebook = root / "Notebooks" / (name + ".Notebook") / "notebook-content.py"
                tree = ast.parse(notebook.read_text(encoding="utf-8"))
                function = next(node for node in ast.walk(tree)
                                if isinstance(node, ast.FunctionDef) and node.name == "shared_sql_request")
                namespace = {"requests": Mock(), "api_request": Mock(), "access_token": "test-token",
                             "get_spn_access_token": Mock(return_value="test-token"),
                             "FABRIC_API_BASE": "https://api.fabric.microsoft.com"}
                exec(compile(ast.Module(body=[function], type_ignores=[]), str(notebook), "exec"), namespace)
                client = namespace["api_request"] if name.startswith("RTI_011") else namespace["requests"].request
                for path, expected in (
                    ("workspaces/workspace/items", "https://api.fabric.microsoft.com/v1/workspaces/workspace/items"),
                    ("https://api.fabric.microsoft.com/v1/operations/op", "https://api.fabric.microsoft.com/v1/operations/op"),
                    ("https://wabi-region-redirect.analysis.windows.net/v1/operations/op/result?tenant=test",
                     "https://wabi-region-redirect.analysis.windows.net/v1/operations/op/result?tenant=test"),
                ):
                    namespace["shared_sql_request"]("GET", path)
                    self.assertEqual(client.call_args.args[1], expected)
                self.assertFalse(any(isinstance(node, ast.ImportFrom) and
                                     node.module == "ontology_notebook_support" for node in ast.walk(tree)))

    def test_unrelated_legacy_name_is_not_adopted(self):
        with self.assertRaisesRegex(RuntimeError, "unrelated"):
            support.ensure_operational_item(
                self.prepare(owned=False), "workspace", env_suffix="V11", folder_id="demo-folder")
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_failed_folder_readback_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "folder readback"):
            support.ensure_operational_item(
                self.prepare(move_succeeds=False), "workspace", env_suffix="V11", folder_id="demo-folder")

    def test_names_require_an_explicit_safe_suffix(self):
        self.assertEqual(support.operational_store_name("V11"), "RTI_Demo_SQLDatabase_V11")
        for suffix in ("", None, "../V11", "V11;drop", "_V11"):
            with self.subTest(suffix=suffix), self.assertRaises(RuntimeError):
                support.operational_store_name(suffix)
        self.assertTrue(support.is_operational_store_name("RTI_Demo_SQLDatabase_V11"))
        self.assertFalse(support.is_operational_store_name("hydro-operations-ui"))

    def test_sql_source_path_survives_display_name_changes(self):
        path = "Files/Config/draft/HydroOperationalStore/datasource.json"
        source = {"type": "sql_database", "artifactId": "sql", "workspaceId": "workspace",
                  "custom": "preserve"}
        parts = [part(path, json.dumps(source))]
        self.assertEqual(support.operational_source_path(
            parts, "workspace", "sql", "Files/Config/draft/new-name/datasource.json"), path)
        self.assertEqual(json.loads(base64.b64decode(parts[0]["payload"])), source)

    def test_archived_sql_is_excluded_only_for_its_exact_available_replacement(self):
        target = {"id": "11111111-2222-4333-8444-555555555555", "type": "SQLDatabase",
                  "displayName": "RTI_Demo_SQLDatabase_V11", "description": support.OPERATIONAL_STORE_DESCRIPTION}
        archived = {"id": "original", "type": "SQLDatabase", "displayName": "HydroOperationalStore",
                    "description": support.OPERATIONAL_ARCHIVE_PREFIX + target["id"] + "."}
        self.assertEqual(support.active_operational_items([archived, target]), [target])
        with self.assertRaisesRegex(RuntimeError, "active replacement"):
            support.active_operational_items([archived])
        with self.assertRaisesRegex(RuntimeError, "active replacement"):
            support.active_operational_items([archived, {**target, "id": "other"}])
        self.assertEqual(support.active_operational_items([
            {**archived, "description": support.OPERATIONAL_STORE_DESCRIPTION}, target
        ]), [{**archived, "description": support.OPERATIONAL_STORE_DESCRIPTION}, target])

    def test_duplicate_sql_sources_and_conflicting_workspace_are_rejected(self):
        source = {"type": "sql_database", "artifactId": "sql", "workspaceId": "other"}
        paths = [f"Files/Config/draft/{name}/datasource.json" for name in ("old", "new")]
        with self.assertRaisesRegex(RuntimeError, "workspace identity"):
            support.operational_source_path([part(paths[0], json.dumps(source))], "workspace", "sql", paths[1])
        source["workspaceId"] = "workspace"
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            support.operational_source_path(
                [part(path, json.dumps(source)) for path in paths], "workspace", "sql", paths[1])

    def test_seed_only_inserts_and_matches_the_sql_reference(self):
        root = Path(__file__).resolve().parents[2]
        notebook = root / "Notebooks" / "RTI_011_seed_sql_wire_graphql_agent.Notebook" / "notebook-content.py"
        assignment = next(node for node in ast.parse(notebook.read_text(encoding="utf-8")).body
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "SEED_SQL"
                                  for target in node.targets))
        seed = ast.literal_eval(assignment.value)
        reference = (root / "HydroOperationsApp" / "sql" / "seed-operational-data.sql").read_text(encoding="utf-8")
        standalone_body = reference[reference.index("SET NOCOUNT ON;"):].partition("COMMIT TRANSACTION;")[0]
        self.assertEqual(seed.strip(), standalone_body.replace("BEGIN TRANSACTION;\n", "", 1).strip())
        self.assertEqual(seed.count("WHEN NOT MATCHED THEN INSERT"), 5)
        self.assertNotIn("WHEN MATCHED", seed)

    def test_seed_checks_existing_records_before_commit(self):
        connection = Mock()
        connection.cursor.return_value.fetchmany.side_effect = (
            [[("existing", "original")]] * 5 + [
                [("existing", "original")] + [(f"new-{i}", "demo") for i in range(support.OPERATIONAL_DEMO_COUNTS[table] - 1)]
                for table in support.OPERATIONAL_COLUMNS])
        receipt = support.seed_operational_records(connection, "INSERT MISSING DEMO ROWS")
        self.assertTrue(receipt["existing_records_preserved"])
        self.assertEqual(receipt["tables"]["WorkOrders"], {"before": 1, "after": 12})
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()

    def test_seed_rolls_back_if_an_existing_field_changes(self):
        connection = Mock()
        connection.cursor.return_value.fetchmany.side_effect = (
            [[("existing", "original")]] * 5 + [[("existing", "modified")]] * 5)
        with self.assertRaisesRegex(RuntimeError, "changed existing WorkOrders"):
            support.seed_operational_records(connection, "UNEXPECTED MUTATION")
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()

    def test_fresh_notebook_initializes_and_seeds_without_a_legacy_database(self):
        root = Path(__file__).resolve().parents[2]
        notebook = root / "Notebooks" / "RTI_011_seed_sql_wire_graphql_agent.Notebook" / "notebook-content.py"
        preparation = next(node for node in ast.parse(notebook.read_text(encoding="utf-8")).body
                           if isinstance(node, ast.Try) and any(
                               isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                               and call.func.id == "ensure_operational_item" for call in ast.walk(node)))
        namespace = {
            "provision_shared_store": True, "seed_demo_data": True, "env_suffix": "V11",
            "sql_db_item_name": support.operational_store_name("V11"), "legacy_sql_db_item_id": "",
            "project_ontology_item_id": "ontology", "workspace_id": "workspace", "get_sql_token": Mock(),
            "ensure_operational_item": Mock(return_value={
                "id": "sql", "displayName": support.operational_store_name("V11")}),
            "first_setting": Mock(return_value="folder"), "OPERATIONAL_STORE_NAME": support.OPERATIONAL_STORE_NAME,
            "operational_store_name": support.operational_store_name, "operational_connection": Mock(),
            "initialize_operational_schema": Mock(), "extend_selected_project_ontology": Mock(return_value={}),
            "resolve_sql_database": Mock(return_value=("sql", "server", "physical-database")),
            "operational_item": Mock(), "copy_operational_records": Mock(), "seed_sql_database": Mock(),
            "step_results": {}, "step_errors": [], "json": json,
        }
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(ast.Module(body=[preparation], type_ignores=[]), str(notebook), "exec"), namespace)
        self.assertEqual(namespace["step_errors"], [])
        namespace["operational_item"].assert_not_called()
        namespace["copy_operational_records"].assert_not_called()
        namespace["seed_sql_database"].assert_called_once_with("server", "physical-database")


class RelationshipSourceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.spark = Mock()
        self.spark.sql.return_value.limit.return_value.count.return_value = 0
        self.spark.sql.return_value.collect.return_value = [
            ("hydro.businessProjection", "equipment-types-v1")]
        self.spark.table.return_value.schema.fields = []

    def test_explicit_foreign_keys_are_persisted_without_changing_fact_identity(self):
        support.prepare_project_relationship_sources(self.spark)
        statements = [call.args[0] for call in self.spark.sql.call_args_list]
        text = "\n".join(statements)
        self.assertIn("get_json_object(metadata_json, '$.location_id')", text)
        self.assertIn("CASE WHEN target_kind='location' THEN target_id END", text)
        self.assertIn("CASE WHEN target_kind='area' THEN target_id END", text)
        self.assertIn("MERGE INTO ontology_equipment_types", text)
        self.assertIn("LEFT ANTI JOIN silver_facilities", text)
        self.assertNotIn("ontology_key =", text)
        self.assertNotIn("DROP ", text)

    def test_unowned_type_projection_is_not_adopted(self):
        self.spark.sql.return_value.collect.return_value = []
        with self.assertRaisesRegex(RuntimeError, "not the managed"):
            support.prepare_project_relationship_sources(self.spark)
        self.assertFalse(any("MERGE" in call.args[0] for call in self.spark.sql.call_args_list))

    def test_conflicting_existing_relationship_column_type_fails(self):
        field = Mock()
        field.name = "location_id"
        field.dataType.simpleString.return_value = "int"
        self.spark.table.return_value.schema.fields = [field]
        with self.assertRaisesRegex(RuntimeError, "not a string"):
            support.prepare_project_relationship_sources(self.spark)
        self.assertFalse(any(call.args[0].startswith("UPDATE ")
                             for call in self.spark.sql.call_args_list))

    def test_ambiguous_source_type_identity_fails_before_any_write(self):
        self.spark.sql.return_value.limit.return_value.count.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "one authoritative type name"):
            support.prepare_project_relationship_sources(self.spark)
        self.assertEqual(len(self.spark.sql.call_args_list), 1)


class ProjectExtensionTests(unittest.TestCase):
    def setUp(self):
        self.parts = [part("database.tmdl", "database\n"), part("model.tmdl", "model Model\n"),
                      part("unknown.json", '{"keep":"custom"}')]
        for entity in ("facilities", "systems", "equipment", "instruments", "signal_master"):
            self.parts.append(part(f"entities/{entity}.tmdl", f"entity {entity}\n\tbackingTable: {entity}\n"))
            self.parts.append(part(f"tables/{entity}.tmdl",
                                   f"table {entity}\n\tpartition {entity} = entity\n"
                                   f"\t\tsource\n\t\t\tentityName: silver_{entity}\n"
                                   "\t\t\texpressionSource: HydroCore\n"))
        self.parts.append(part("expressions.tmdl", support.project_source_expressions(
            "workspace", "lakehouse", "sql")[1].replace("HydroProjectLakehouse", "HydroCore")))
        self.weather = {table: dict.fromkeys(keys, "string")
                        for table, keys in support.WEATHER_PROJECT_ENTITIES.values()}
        for table, keys in support.WEATHER_PROJECT_ENTITIES.values():
            if len(keys) > 1:
                self.weather[table]["ontology_key"] = "string"
        self.weather["weather_areas"]["location_id"] = "string"
        self.weather["weather_latest_forecasts"].update(location_id="string", area_id="string")
        self.weather["ontology_equipment_types"]["equipment_type_name"] = "string"
        self.sql = {table: dict.fromkeys(columns, "string") for table, columns in support.OPERATIONAL_COLUMNS.items()}

    def extend(self, parts=None):
        return support.project_ontology_extension(
            self.parts if parts is None else parts, "workspace", "lakehouse", "sql",
            self.weather, self.sql, _verify_definition)

    def test_additive_idempotent_core_and_unknown_parts_are_byte_preserved(self):
        result = self.extend()
        indexed = {p["path"]: p for p in result}
        for original in self.parts:
            if original["path"] not in ("model.tmdl", "expressions.tmdl"):
                self.assertEqual(indexed[original["path"]], original)
            else:
                _verify_definition([original], [indexed[original["path"]]])
        self.assertEqual(result, self.extend(result))
        text = base64.b64decode(indexed["tables/HydroProject_WeatherLatestForecast.tmdl"]["payload"]).decode()
        self.assertIn("entityName: weather_latest_forecasts", text)
        self.assertNotIn("entityName: WeatherLatestForecast", text)
        self.assertNotIn("annotation ONT_", text)
        forecast = base64.b64decode(indexed["entities/WeatherLatestForecast.tmdl"]["payload"]).decode()
        self.assertEqual(forecast.count("keyProperty:"), 1)
        self.assertIn("keyProperty: ontology_key", forecast)
        physical = base64.b64decode(indexed["relationships.tmdl"]["payload"]).decode()
        self.assertIn("HydroProject_WorkOrder.equipmentId", physical)
        self.assertNotIn("SparePart.equipmentId", physical)
        self.assertNotIn("WeatherLatestForecast.target_id", physical)
        edges = base64.b64decode(indexed["entityRelationships.tmdl"]["payload"]).decode()
        self.assertEqual(edges.count("entityRelationship "), edges.count("backingConfiguration"))

    def test_business_relationships_use_explicit_discriminated_source_keys(self):
        indexed = {p["path"]: p for p in self.extend()}
        text = base64.b64decode(indexed["relationships.tmdl"]["payload"]).decode()
        for binding in (
            "HydroProject_WeatherLocation.location_id\n\ttoColumn: facilities.facility_id",
            "HydroProject_WeatherArea.location_id\n\ttoColumn: HydroProject_WeatherLocation.location_id",
            "HydroProject_WeatherLatestForecast.location_id\n\ttoColumn: HydroProject_WeatherLocation.location_id",
            "HydroProject_WeatherLatestForecast.area_id\n\ttoColumn: HydroProject_WeatherArea.area_id",
            "equipment.equipment_type_code\n\ttoColumn: HydroProject_EquipmentType.equipment_type_code",
            "HydroProject_SparePart.equipmentType\n\ttoColumn: HydroProject_EquipmentType.equipment_type_code",
        ):
            self.assertIn(binding, text)
        for retired in support.PROJECT_CATALOG_ENTITIES:
            self.assertNotIn(f"entities/{retired}.tmdl", indexed)
        self.assertNotIn("SparePart.equipmentId", text)
        self.assertNotIn("WeatherLatestForecast.target_id", text)

    def test_missing_derived_key_is_not_replaced_by_a_polymorphic_target_join(self):
        del self.weather["weather_latest_forecasts"]["location_id"]
        with self.assertRaisesRegex(RuntimeError, "requires verified source column"):
            self.extend()

    def test_verified_relationship_keys_extend_bound_parts_without_overwriting_custom_content(self):
        result = self.extend()
        for original in result:
            if original["path"] in ("entities/WeatherArea.tmdl", "tables/HydroProject_WeatherArea.tmdl"):
                text = base64.b64decode(original["payload"]).decode()
                kind = "property" if original["path"].startswith("entities/") else "column"
                text = re.sub(r"(?ms)^\t" + kind + r" location_id\n.*?(?=^\t\S|^\S|\Z)", "", text)
                original["payload"] = base64.b64encode(
                    (text + "\tannotation CustomSetting = preserve\n").encode()).decode()
        restored = self.extend(result)
        for path in ("entities/WeatherArea.tmdl", "tables/HydroProject_WeatherArea.tmdl"):
            text = base64.b64decode(next(p for p in restored if p["path"] == path)["payload"]).decode()
            self.assertIn("annotation CustomSetting = preserve", text)
            self.assertIn("location_id", text)
        self.assertEqual(restored, self.extend(restored))

    def test_catalog_retirement_does_not_remove_physical_data_or_unknown_parts(self):
        old = copy.deepcopy(self.parts)
        model = next(p for p in old if p["path"] == "model.tmdl")
        text = base64.b64decode(model["payload"]).decode()
        for name, physical in support.PROJECT_CATALOG_ENTITIES.items():
            old.extend((
                part(f"entities/{name}.tmdl", f"entity {name}\n\tbackingTable: HydroProject_{name}\n"
                     "\tannotation HydroProjectExtension = 1\n"),
                part(f"tables/HydroProject_{name}.tmdl", f"table HydroProject_{name}\n"
                     f"\tpartition HydroProject_{name} = entity\n\t\tsource\n"
                     f"\t\t\tentityName: {physical}\n\tannotation HydroProjectExtension = 1\n"),
            ))
            text += f"ref entity {name}\nref table HydroProject_{name}\n"
        model["payload"] = base64.b64encode(text.encode()).decode()
        curated = support.curate_project_catalog_entities(old)
        self.assertEqual(support.curate_project_catalog_entities(curated), curated)
        indexed = {p["path"]: p for p in curated}
        self.assertIn("unknown.json", indexed)
        self.assertEqual({p["path"] for p in curated}, {p["path"] for p in self.parts})
        old[-1]["payload"] = base64.b64encode(b"table Custom\n").decode()
        with self.assertRaisesRegex(RuntimeError, "unowned catalog"):
            support.curate_project_catalog_entities(old)

    def test_preserves_readback_lineage_and_added_compatible_content(self):
        result = self.extend()
        target = next(p for p in result if p["path"] == "entities/WorkOrder.tmdl")
        text = base64.b64decode(target["payload"]).decode()
        target["payload"] = base64.b64encode(
            (text + "\tlineageTag: server-owned\n\tannotation CustomSetting = keep\n").encode()).decode()
        self.assertEqual(self.extend(result), result)

    def test_foreign_backing_and_missing_keys_fail_without_mutating_input(self):
        before = copy.deepcopy(self.parts)
        self.weather["weather_latest_forecasts"].pop("target_kind")
        with self.assertRaisesRegex(RuntimeError, "missing source key"):
            self.extend()
        self.assertEqual(before, self.parts)

    def test_composite_identity_requires_a_physical_string_key(self):
        self.weather["weather_latest_forecasts"].pop("ontology_key")
        with self.assertRaisesRegex(RuntimeError, "verified physical ontology_key"):
            self.extend()
        self.weather["weather_latest_forecasts"]["ontology_key"] = "dateTime"
        with self.assertRaisesRegex(RuntimeError, "verified physical ontology_key"):
            self.extend()

    def test_identity_encoding_includes_all_members_and_utc_timestamp_precision(self):
        expression = support.project_identity_expression(("source_id", "location_id", "observed_at_utc"))
        self.assertIn("'source_id', `source_id`, 'location_id', `location_id`, 'observed_at_utc', `observed_at_utc`", expression)
        self.assertIn("'timeZone', 'UTC'", expression)
        self.assertIn("HH:mm:ss.SSSSSS", expression)
        self.assertIn("sha2(to_json(named_struct(", expression)
        with self.assertRaisesRegex(RuntimeError, "safe source columns"):
            support.project_identity_expression(("unsafe`",))

    def test_source_expressions_verify_workspace_and_item_after_server_roundtrip(self):
        expected = [part("expressions.tmdl", "\n".join(support.project_source_expressions("workspace", "lakehouse", "sql")))]
        result = self.extend()
        _verify_definition(expected, result)
        for wrong in (support.project_source_expressions("foreign", "lakehouse", "sql"),
                      support.project_source_expressions("workspace", "lakehouse", "foreign")):
            with self.assertRaisesRegex(RuntimeError, "did not retain submitted"):
                _verify_definition([part("expressions.tmdl", "\n".join(wrong))], result)

    def source_metadata(self, parts):
        return support.project_ontology_source_metadata(
            parts, {"id": "workspace", "displayName": "Workspace"},
            {"id": "lakehouse", "type": "Lakehouse", "displayName": "Lakehouse",
             "properties": {"sqlEndpointProperties": {"connectionString": "lakehouse.example"}}},
            {"id": "sql", "type": "SQLDatabase", "displayName": "Operations",
             "properties": {"serverFqdn": "sql.example", "databaseName": "Operations-sql"}})

    def test_source_metadata_pins_all_shared_sources_without_changing_partitions(self):
        before = self.extend()
        after = self.source_metadata(before)
        for old, new in zip(before, after):
            if old["path"] != "expressions.tmdl":
                self.assertEqual(old, new)
        expression = base64.b64decode(next(p for p in after if p["path"] == "expressions.tmdl")["payload"]).decode()
        self.assertEqual(expression.count("annotation ONT_WorkspaceId = workspace"), 3)
        self.assertEqual(expression.count("annotation ONT_ItemKind = Lakehouse"), 2)
        self.assertEqual(expression.count("annotation ONT_ItemKind = SQLDatabase"), 1)
        self.assertIn("annotation ONT_SqlDatabase = Operations-sql", expression)
        self.assertEqual(expression.count("annotation ONT_PinnedAtUtc ="), 3)
        with patch.object(support.time, "strftime", side_effect=AssertionError("A rerun must preserve source pin timestamps")):
            self.assertEqual(after, self.source_metadata(after))

    def test_explicit_operational_rebinding_preserves_all_other_parts_and_is_idempotent(self):
        before = self.source_metadata(self.extend())
        workspace = {"id": "workspace", "displayName": "Workspace"}
        lakehouse = {"id": "lakehouse", "type": "Lakehouse", "displayName": "Lakehouse",
                     "properties": {"sqlEndpointProperties": {"connectionString": "lakehouse.example"}}}
        source = {"id": "sql", "type": "SQLDatabase", "displayName": "Operations",
                  "properties": {"serverFqdn": "sql.example", "databaseName": "Operations-sql"}}
        target = {**source, "id": "new-sql", "displayName": "RTI_Demo_SQLDatabase_V11",
                  "properties": {"serverFqdn": "new.example", "databaseName": "new-database"}}
        after = support.rebind_project_operational_source(before, workspace, lakehouse, source, target)
        for old, new in zip(before, after):
            if old["path"] != "expressions.tmdl":
                self.assertEqual(old, new)
        old_text = base64.b64decode(next(p for p in before if p["path"] == "expressions.tmdl")["payload"]).decode()
        new_text = base64.b64decode(next(p for p in after if p["path"] == "expressions.tmdl")["payload"]).decode()
        self.assertIn("/workspace/new-sql", new_text)
        self.assertIn("annotation ONT_ItemId = new-sql", new_text)
        self.assertIn("annotation ONT_SqlEndpoint = new.example", new_text)
        self.assertEqual(old_text.split("expression HydroProjectLakehouse")[1],
                         new_text.split("expression HydroProjectLakehouse")[1])
        self.assertEqual(after, support.rebind_project_operational_source(after, workspace, lakehouse, source, target))
        with self.assertRaisesRegex(RuntimeError, "authoritative source identity"):
            support.rebind_project_operational_source(before, workspace, lakehouse,
                                                      {**source, "id": "foreign"}, target)

    def test_source_metadata_rejects_conflicting_identity_and_preserves_unknown_annotations(self):
        pinned = self.source_metadata(self.extend())
        expression = next(p for p in pinned if p["path"] == "expressions.tmdl")
        text = base64.b64decode(expression["payload"]).decode()
        expression["payload"] = base64.b64encode(text.replace(
            "\tannotation ONT_ItemId = lakehouse", "\tannotation ONT_ItemId = foreign", 1).encode()).decode()
        before = copy.deepcopy(pinned)
        with self.assertRaisesRegex(RuntimeError, "Conflicting ontology source metadata"):
            self.source_metadata(pinned)
        self.assertEqual(before, pinned)
        expression["payload"] = base64.b64encode(
            text.replace("\tannotation ONT_ItemId = lakehouse",
                         "\tannotation CustomMetadata = keep\n\n\tannotation ONT_ItemId = lakehouse", 1).encode()).decode()
        self.assertEqual(pinned, self.source_metadata(pinned))

    def test_source_metadata_rejects_source_expression_drift(self):
        before = self.extend()
        expression = next(p for p in before if p["path"] == "expressions.tmdl")
        expression["payload"] = base64.b64encode(
            base64.b64decode(expression["payload"]).decode().replace("/workspace/lakehouse", "/foreign/lakehouse").encode()).decode()
        with self.assertRaisesRegex(RuntimeError, "authoritative source identity"):
            self.source_metadata(before)

    def test_source_metadata_rejects_duplicate_and_missing_shared_sources(self):
        for duplicate in (False, True):
            before = self.extend()
            expression = next(p for p in before if p["path"] == "expressions.tmdl")
            text = base64.b64decode(expression["payload"]).decode()
            if duplicate:
                text += "\n" + support.project_source_expressions(
                    "workspace", "lakehouse", "sql")[1].replace("HydroProjectLakehouse", "HydroCore")
            else:
                text = text.replace("expression HydroCore =", "expression Unrelated =")
            expression["payload"] = base64.b64encode(text.encode()).decode()
            snapshot = copy.deepcopy(before)
            with self.assertRaisesRegex(RuntimeError, "Duplicate ontology source expression|expression is missing"):
                self.source_metadata(before)
            self.assertEqual(before, snapshot)


class CopyCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, *parameters):
        self.connection.commands.append((sql, parameters))
        self.rows = []
        if sql.startswith("SELECT sourceItemId"):
            self.rows = [] if self.connection.state is None else [self.connection.state]
        elif sql.startswith("SELECT name FROM sys.triggers"):
            self.rows = [(name,) for name in self.connection.triggers]
        elif sql.startswith("SELECT COLUMN_NAME"):
            self.rows = [("id",), ("value",)]
        elif sql.startswith("SELECT [id]"):
            self.rows = list(self.connection.data)
        elif sql.startswith("INSERT INTO dbo.["):
            self.connection.data.append(tuple(parameters))
        elif sql.startswith("CREATE TRIGGER"):
            self.connection.triggers.add("HydroArchive_Records")
        elif sql.startswith("INSERT INTO dbo.HydroOperationalStoreState"):
            self.connection.state = tuple(parameters)
        return self

    def fetchall(self):
        return self.rows

    def fetchmany(self, count):
        return self.rows[:count]

    def fetchone(self):
        return self.rows[0] if self.rows else None


class CopyConnection:
    def __init__(self, rows):
        self.data, self.commands, self.triggers = list(rows), [], set()
        self.state = None
        self.commits = self.rollbacks = 0

    def cursor(self):
        return CopyCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class OperationalCopyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(support, "OPERATIONAL_COLUMNS", {"Records": {"id": "string", "value": "string"}}))
        self.source = CopyConnection([("id-1", "existing"), ("id-2", "preserved")])
        self.target = CopyConnection([])

    def test_exact_copy_receipt_preserves_ids_and_establishes_legacy_write_boundary(self):
        receipt = support.copy_operational_records(self.source, self.target, "source", "target")
        self.assertEqual(self.source.data, self.target.data)
        self.assertEqual(receipt["tables"]["Records"]["rows"], 2)
        self.assertEqual(len(receipt["tables"]["Records"]["sha256"]), 64)
        self.assertTrue(any("TABLOCKX,HOLDLOCK" in sql for sql, _ in self.source.commands))
        self.assertTrue(any("THROW 51001" in sql for sql, _ in self.source.commands))
        self.assertEqual(support.copy_operational_records(self.source, self.target, "source", "target"), receipt)

    def test_conflict_rolls_back_and_never_blocks_original_writes(self):
        self.target.data = [("id-1", "different")]
        with self.assertRaisesRegex(RuntimeError, "Conflicting"):
            support.copy_operational_records(self.source, self.target, "source", "target")
        self.assertEqual(self.source.triggers, set())
        self.assertEqual(self.source.rollbacks, 1)
        self.assertEqual(self.target.rollbacks, 1)

    def test_datetime2_seventh_fractional_digit_is_not_lost_through_odbc(self):
        self.source.data = [("id-1", "2026-10-24T18:10:20.1234567")]
        with patch.object(support, "OPERATIONAL_COLUMNS", {"Records": {"id": "uniqueidentifier", "value": "datetime2"}}):
            support.copy_operational_records(self.source, self.target, "source", "target")
        self.assertEqual(self.target.data, self.source.data)
        self.assertTrue(any("CONVERT(nvarchar(33),[value],126)" in sql for sql, _ in self.source.commands))
        inserts = [(sql, values) for sql, values in self.target.commands if sql.startswith("INSERT INTO dbo.[")]
        self.assertEqual(inserts[0][1][1], "2026-10-24T18:10:20.1234567")
        self.assertNotIn("CONVERT", inserts[0][0])

    def test_incomplete_legacy_boundary_is_not_success_shaped(self):
        self.target.state = ("source", "target", json.dumps({"status": "copied"}))
        with self.assertRaisesRegex(RuntimeError, "write boundary is incomplete"):
            support.copy_operational_records(self.source, self.target, "source", "target")

    def test_same_source_and_target_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "must differ"):
            support.copy_operational_records(self.source, self.target, "same", "same")


if __name__ == "__main__":
    unittest.main()
