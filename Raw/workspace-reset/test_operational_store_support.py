import base64
import copy
import json
import unittest
from unittest.mock import patch

import operational_store_support as support
from ontology_notebook_support import _verify_definition


def part(path, text):
    return {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}


class ProjectExtensionTests(unittest.TestCase):
    def setUp(self):
        self.parts = [part("database.tmdl", "database\n"), part("model.tmdl", "model Model\n"),
                      part("unknown.json", '{"keep":"custom"}')]
        for entity in ("facilities", "systems", "equipment", "instruments", "signal_master"):
            self.parts.append(part(f"entities/{entity}.tmdl", f"entity {entity}\n\tbackingTable: {entity}\n"))
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
            if original["path"] != "model.tmdl":
                self.assertEqual(indexed[original["path"]], original)
        self.assertEqual(result, self.extend(result))
        text = base64.b64decode(indexed["tables/HydroProject_WeatherLatestForecast.tmdl"]["payload"]).decode()
        self.assertIn("entityName: weather_latest_forecasts", text)
        self.assertNotIn("entityName: WeatherLatestForecast", text)
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
