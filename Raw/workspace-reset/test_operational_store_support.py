import base64
import ast
import contextlib
import copy
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import operational_store_support as support
from ontology_notebook_support import _verify_definition


def part(path, text):
    return {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}


class OperationalItemLifecycleTests(unittest.TestCase):
    def prepare(self, existing=True, owned=True, move_succeeds=True):
        self.item = {
            "id": "operational-sql", "workspaceId": "workspace", "type": "SQLDatabase",
            "displayName": support.OPERATIONAL_STORE_NAME, "folderId": None,
            "description": support.OPERATIONAL_STORE_DESCRIPTION if owned else "Unrelated database",
            "properties": {"serverFqdn": "authoritative.database.fabric.microsoft.com",
                           "databaseName": "unchanged-physical-database"},
        }
        self.calls = []
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
                status = 201
            elif method == "PATCH":
                self.item.update(payload)
            elif method == "POST" and path.endswith("/move"):
                if move_succeeds:
                    self.item["folderId"] = payload["targetFolderId"]
            return Mock(status_code=status, json=lambda: copy.deepcopy(body))
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
