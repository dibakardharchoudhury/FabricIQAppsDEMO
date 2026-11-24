"""Optional local Delta integration tests; no Fabric credentials or remote writes."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from MasterDataManagement.contract import ContractError, fingerprint
from MasterDataManagement.fabric_runner import run_release
from MasterDataManagement.test_contract import LAKEHOUSE, WORKSPACE, fixture


class SparkReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        from delta import configure_spark_with_delta_pip
        cls.temp = tempfile.TemporaryDirectory(prefix="concord-spark-")
        os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
        builder = (SparkSession.builder.master("local[2]").appName("Concord local release tests")
                   .config("spark.ui.enabled", "false")
                   .config("spark.ui.showConsoleProgress", "false")
                   .config("spark.sql.shuffle.partitions", "2")
                   .config("spark.databricks.delta.snapshotPartitions", "2")
                   .config("spark.driver.host", "127.0.0.1")
                   .config("spark.sql.warehouse.dir", f"{cls.temp.name}/warehouse")
                   .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
                   .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"))
        jars = os.environ.get("MDM_TEST_DELTA_JARS")
        cls.spark = (builder.config("spark.jars", jars) if jars else configure_spark_with_delta_pip(builder)).getOrCreate()
        cls.spark.sparkContext.setLogLevel("ERROR")
        cls.spark.sql("CREATE SCHEMA IF NOT EXISTS gold")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.contract = fixture()
        self.paths = {}
        self.context = {"defaultLakehouseId": LAKEHOUSE, "defaultLakehouseWorkspaceId": WORKSPACE}
        self.source_temp = tempfile.TemporaryDirectory(dir=self.temp.name, prefix="sources-")
        self.write_source("crm", [("C1", "CRM name"), ("C2", None)])
        self.write_source("erp", [("C1", "ERP name"), ("C2", "ERP fallback"), ("C3", "ERP only")])
        self.path_mock = patch("MasterDataManagement.fabric_runner.source_path", side_effect=lambda s: self.paths[s["id"]])
        self.ontology_mock = patch("MasterDataManagement.fabric_runner.read_live_ontology")
        self.path_mock.start()
        self.live = self.ontology_mock.start()

    def tearDown(self):
        self.path_mock.stop()
        self.ontology_mock.stop()
        for table in self.spark.catalog.listTables("gold"):
            self.spark.sql(f"DROP TABLE gold.`{table.name}`")
        self.source_temp.cleanup()

    def write_source(self, sid, rows, schema="customer_id string, name string"):
        path = str(Path(self.source_temp.name) / sid)
        self.spark.createDataFrame(rows, schema).write.format("delta").mode("overwrite").save(path)
        self.paths[sid] = path

    def run_release(self):
        digest = fingerprint(self.contract)
        return run_release(self.spark, json.dumps(self.contract), digest, WORKSPACE, LAKEHOUSE, self.context, "test-token")

    def assert_no_gold(self):
        self.assertEqual(self.spark.catalog.listTables("gold"), [])

    def test_complete_release_provenance_and_exclusive_replay(self):
        self.contract["mappings"][1]["transform"] = "trim"
        self.write_source("crm", [("C1", "\t\u00a0 CRM name \ufeff"), ("C2", None)])
        receipt = self.run_release()
        tables = json.loads(receipt["tables_json"])
        rows = {r["customer_id"]: r["name"] for r in self.spark.table(tables["customer"]).collect()}
        self.assertEqual(rows, {"C1": "CRM name", "C2": "ERP fallback", "C3": "ERP only"})
        lineage = self.spark.table(tables["customer"] + "_lineage").filter("property = 'name'").collect()
        self.assertEqual({r.record_key: r.source for r in lineage}, {"C1": "crm", "C2": "erp", "C3": "erp"})
        self.assertEqual({r.record_key: r.source_version for r in lineage}, {"C1": 1, "C2": 0, "C3": 0})
        self.assertEqual(receipt["status"], "gold_committed")
        self.assertEqual(receipt["semantic_model_status"], "definition_prepared_not_deployed")
        self.assertEqual(self.spark.table(receipt["commit_table"]).count(), 1)
        with self.assertRaisesRegex(ContractError, "already exists"):
            self.run_release()
        self.assertEqual(self.spark.table(tables["customer"]).count(), 3)

    def test_conflict_blocks_every_gold_write(self):
        prop = self.contract["entities"][0]["properties"][1]
        prop.update(strategy="require_equal", priority=[])
        with self.assertRaisesRegex(ContractError, "conflicting source values"):
            self.run_release()
        self.assert_no_gold()

    def test_drift_duplicate_null_and_required_values_block_every_write(self):
        for rows, schema, error in [
            ([("C1", "Name", "new")], "customer_id string, name string, new_column string", "schema drift"),
            ([("C1", "Name"), ("C1", "Other")], "customer_id string, name string", "duplicate business keys"),
            ([(None, "Name")], "customer_id string, name string", "null or blank"),
            ([("\t\u00a0\ufeff", "Name")], "customer_id string, name string", "null or blank"),
            ([("C4", None)], "customer_id string, name string", "missing required"),
        ]:
            with self.subTest(error=error):
                # Restore the original schema, then add a column only in the drift case.
                if error == "schema drift":
                    extra = str(Path(self.source_temp.name) / "crm_drift")
                    self.spark.createDataFrame(rows, schema).write.format("delta").save(extra)
                    self.paths["crm"] = extra
                else:
                    self.write_source("crm", rows, schema)
                with self.assertRaisesRegex(ContractError, error):
                    self.run_release()
                self.assert_no_gold()

    def test_orphan_declared_relationship_blocks_all_entities(self):
        self.contract["entities"].append({
            "id": "sales_order", "ontologyEntity": "Order", "label": "Sales order", "goldTable": "sales_order", "key": "order_id",
            "properties": [
                {"name": name, "ontologyProperty": name, "label": name, "type": "string", "required": True,
                 "strategy": "require_equal", "priority": []} for name in ("order_id", "customer_id")
            ],
        })
        self.contract["sources"].append({
            **self.contract["sources"][0], "id": "orders", "label": "Orders", "table": "orders",
            "keyColumn": "order_id", "keyNamespace": "enterprise_order", "entity": "sales_order",
            "columns": [{"name": "order_id", "type": "string"}, {"name": "customer_id", "type": "string"}],
        })
        self.contract["mappings"] += [
            {"source": "orders", "column": name, "target": name, "transform": "identity", "reviewed": True, "reason": "Declared key"}
            for name in ("order_id", "customer_id")
        ]
        self.contract["relationships"] = [{
            "name": "order_customer", "ontologyRelationship": "OrderCustomer", "from": "sales_order", "column": "customer_id", "to": "customer",
        }]
        self.write_source("orders", [("O1", "UNKNOWN")], "order_id string, customer_id string")
        with self.assertRaisesRegex(ContractError, "orphan foreign keys"):
            self.run_release()
        self.assert_no_gold()

    def test_source_version_is_pinned_when_silver_changes_before_commit(self):
        calls = 0

        def recheck(*_):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.write_source("crm", [("C1", "Changed during run"), ("C2", "New CRM value")])

        self.live.side_effect = recheck
        receipt = self.run_release()
        table = json.loads(receipt["tables_json"])["customer"]
        rows = {r["customer_id"]: r["name"] for r in self.spark.table(table).collect()}
        self.assertEqual(rows["C1"], "CRM name")
        self.assertEqual(rows["C2"], "ERP fallback")
        self.assertEqual(json.loads(receipt["source_versions_json"])["crm"], 0)

    def test_publication_failure_leaves_no_commit_marker_and_does_not_retry(self):
        digest = fingerprint(self.contract)
        with patch.object(self.spark, "createDataFrame", side_effect=RuntimeError("simulated commit storage failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated commit"):
                self.run_release()
        self.assertFalse(self.spark.catalog.tableExists(f"gold.mdm_release_{digest[:16]}"))
        self.assertTrue(self.spark.catalog.tableExists(f"gold.mdm_customer_{digest[:16]}"))
        with self.assertRaisesRegex(ContractError, "already exists"):
            self.run_release()


if __name__ == "__main__":
    unittest.main()
