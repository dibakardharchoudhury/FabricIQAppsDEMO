"""Execute a pinned stewardship contract in a schema-enabled Fabric Lakehouse.

Import in a Fabric Spark notebook bound to the target Gold Lakehouse. No driver-side
collection of source data, automatic retries, ontology writes or in-place model edits.
"""

import base64
import json
import time
import urllib.request
from datetime import datetime, timezone
from functools import reduce
from urllib.parse import urlparse

from .contract import ContractError, canonical_json, fingerprint, load_approved, semantic_definition, source_path, table_names, verify_schema

FABRIC_API = "https://api.fabric.microsoft.com"
SPARK_TYPES = {"string": "string", "bigint": "int64", "boolean": "boolean"}
# ECMAScript trim's exact whitespace set, so browser simulations match Spark.
TRIM_PATTERN = (
    r"^[\x09-\x0d \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+"
    r"|[\x09-\x0d \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$"
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ContractError("Fabric read redirected unexpectedly; refusing to forward credentials.")


def read_live_ontology(ontology, token):
    """Recheck live numeric generation and the exact reviewed definition, including LROs."""
    opener = urllib.request.build_opener(_NoRedirect())

    def request(url, method="GET"):
        parsed = urlparse(url)
        if f"{parsed.scheme}://{parsed.netloc}" != FABRIC_API or parsed.username or parsed.password or parsed.fragment:
            raise ContractError("Untrusted Fabric operation URL.")
        req = urllib.request.Request(url, data=b"{}" if method == "POST" else None, method=method, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "x-ms-fabric-skill": "e2e-medallion-architecture",
        })
        with opener.open(req, timeout=30) as response:
            body = response.read(10_000_001)
            if len(body) > 10_000_000:
                raise ContractError("Ontology response exceeds the 10 MB definition limit.")
            return response.status, response.headers, json.loads(body) if body else {}

    prefix = f"{FABRIC_API}/v1/workspaces/{ontology['workspaceId']}"
    _, _, metadata = request(f"{prefix}/ontologies/{ontology['itemId']}")
    generation = metadata.get("properties", {}).get("generation")
    if type(generation) is not int or generation != 2:
        raise ContractError("Live ontology is not numeric generation 2; replacement must be explicitly planned.")
    status, headers, definition = request(f"{prefix}/items/{ontology['itemId']}/getDefinition", "POST")
    if status == 202:
        location = headers.get("Location")
        if not location:
            raise ContractError("Ontology definition LRO has no location.")
        deadline = time.monotonic() + 120
        while True:
            if time.monotonic() >= deadline:
                raise ContractError("Ontology definition read timed out.")
            _, _, state = request(location)
            if state.get("status") == "Succeeded":
                break
            if state.get("status") in ("Failed", "Cancelled"):
                raise ContractError(f"Ontology definition read {state['status']}.")
            time.sleep(1)
        parsed = urlparse(location)
        result_url = parsed._replace(path=parsed.path.rstrip("/") + "/result").geturl()
        _, _, definition = request(result_url)
    parts = definition.get("definition", {}).get("parts", [])
    if not parts or any(not isinstance(p, dict) or set(("path", "payload", "payloadType")) - set(p) for p in parts):
        raise ContractError("Ontology has incomplete definition parts.")
    if len({p["path"] for p in parts}) != len(parts):
        raise ContractError("Ontology has duplicate definition part paths.")
    if any(p["path"].startswith(("EntityTypes/", "RelationshipTypes/")) for p in parts) or not any(p["path"].endswith(".tmdl") for p in parts):
        raise ContractError("Only generation-2 TMDL ontology definitions are supported.")
    normalized = [{"path": p["path"], "payload": p["payload"], "payloadType": p["payloadType"]} for p in parts]
    if fingerprint(sorted(normalized, key=lambda p: p["path"])) != ontology["definitionHash"]:
        raise ContractError("Ontology definition changed since stewardship review. Rebind and reapprove.")


def inspect_sources(spark, sources):
    """Return metadata only, for the app's Refresh inventory import."""
    inventory = []
    for source in sources:
        observed = spark.read.format("delta").load(source_path(source)).schema
        columns = []
        for field in observed.fields:
            kind = SPARK_TYPES.get(field.dataType.simpleString())
            if not kind:
                raise ContractError(f"{source['id']}.{field.name}: unsupported {field.dataType.simpleString()}; conform Silver to string/bigint/boolean first.")
            columns.append({"name": field.name, "type": kind})
        inventory.append({**source, "columns": columns})
    return {"sources": inventory}


def run_release(spark, raw_contract, expected_sha256, target_workspace_id, target_lakehouse_id, runtime_context, fabric_token):
    """Return a committed release receipt after validating ALL entities before ANY writes.

    expected_sha256 must be supplied by independently protected pipeline configuration.
    Runtime notebook identity is checked, and each Silver read is pinned to a Delta version.
    """
    contract = load_approved(raw_contract, expected_sha256)
    if (runtime_context.get("defaultLakehouseId") != target_lakehouse_id
            or runtime_context.get("defaultLakehouseWorkspaceId") != target_workspace_id):
        raise ContractError("Notebook must be bound to the exact selected Gold Lakehouse and workspace.")
    # Validate target GUIDs before using them in the generated definition or any writes.
    definition = semantic_definition(contract, expected_sha256, target_workspace_id, target_lakehouse_id)
    read_live_ontology(contract["ontology"], fabric_token)
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    source_frames, versions = {}, {}
    outputs, provenances, cached = {}, {}, []
    tables = table_names(contract, expected_sha256)
    marker = f"gold.mdm_release_{expected_sha256[:16]}"
    destinations = [marker] + list(tables.values()) + [f"{table}_lineage" for table in tables.values()]
    if len(set(destinations)) != len(destinations):
        raise ContractError("Release destination table names collide.")
    for table in destinations:
        if spark.catalog.tableExists(table):
            raise ContractError(f"{table} already exists. Inspect the release receipt or incomplete run; never overwrite/retry blindly.")
    try:
        for source in contract["sources"]:
            path = source_path(source)
            version = DeltaTable.forPath(spark, path).history(1).select("version").head()["version"]
            versions[source["id"]] = int(version)
            frame = spark.read.format("delta").option("versionAsOf", version).load(path)
            verify_schema(source, {field.name: SPARK_TYPES.get(field.dataType.simpleString(), field.dataType.simpleString())
                                   for field in frame.schema.fields})
            key = F.col(source["keyColumn"])
            if frame.filter(key.isNull() | (F.length(F.regexp_replace(key, TRIM_PATTERN, "")) == 0)).limit(1).count():
                raise ContractError(f"{source['id']}: null or blank business keys block publication.")
            if frame.groupBy(source["keyColumn"]).count().filter(F.col("count") > 1).limit(1).count():
                raise ContractError(f"{source['id']}: duplicate business keys block publication; deduplicate in Silver.")
            source_frames[source["id"]] = frame
        for entity in contract["entities"]:
            projections = []
            inputs = [s for s in contract["sources"] if s["entity"] == entity["id"]]
            for source in inputs:
                fields = [F.col(source["keyColumn"]).alias("__mdm_key"), F.lit(source["id"]).alias("__mdm_source")]
                for prop in entity["properties"]:
                    mapping = next((m for m in contract["mappings"] if m["source"] == source["id"] and m["target"] == prop["name"]), None)
                    dtype = {"string": "string", "int64": "long", "boolean": "boolean"}[prop["type"]]
                    value = F.col(mapping["column"]) if mapping else F.lit(None).cast(dtype)
                    if mapping and prop["type"] == "string":
                        if mapping["transform"] != "identity":
                            value = F.regexp_replace(value, TRIM_PATTERN, "")
                        if mapping["transform"] == "lower":
                            value = F.lower(value)
                        if mapping["transform"] == "upper":
                            value = F.upper(value)
                        value = F.when(value == "", None).otherwise(value)
                    fields.append(value.alias(prop["name"]))
                projections.append(source_frames[source["id"]].select(*fields))
            union = reduce(lambda left, right: left.unionByName(right), projections)
            aggregations = []
            for index, prop in enumerate(entity["properties"]):
                mapped = [m for m in contract["mappings"] if m["target"] == prop["name"]
                          and any(s["id"] == m["source"] for s in inputs)]
                source_ids = prop["priority"] if prop["strategy"] == "source_priority" else sorted(m["source"] for m in mapped)
                rank = F.create_map(*[item for i, sid in enumerate(source_ids) for item in (F.lit(sid), F.lit(i))])
                column_map = F.create_map(*[item for m in mapped for item in (F.lit(m["source"]), F.lit(m["column"]))])
                version_map = F.create_map(*[item for sid in source_ids for item in (F.lit(sid), F.lit(versions[sid]))])
                value = F.col(prop["name"])
                candidate = F.when(value.isNotNull(), F.struct(
                    rank[F.col("__mdm_source")].alias("rank"), F.col("__mdm_source").alias("source"),
                    column_map[F.col("__mdm_source")].alias("column"), value.alias("value"),
                    version_map[F.col("__mdm_source")].alias("source_version"),
                ))
                aggregations += [
                    F.min(candidate).alias(f"winner_{index}"),
                    F.size(F.collect_set(value)).alias(f"distinct_{index}"),
                ]
            grouped = union.groupBy("__mdm_key").agg(*aggregations).persist()
            cached.append(grouped)
            if not grouped.limit(1).count():
                raise ContractError(f"{entity['id']}: empty golden entity; refusing an empty release.")
            for index, prop in enumerate(entity["properties"]):
                if prop["required"] and grouped.filter(F.col(f"distinct_{index}") == 0).limit(1).count():
                    raise ContractError(f"{entity['id']}.{prop['name']}: missing required values block publication.")
                if prop["strategy"] == "require_equal" and grouped.filter(F.col(f"distinct_{index}") > 1).limit(1).count():
                    raise ContractError(f"{entity['id']}.{prop['name']}: conflicting source values require a steward decision.")
            outputs[entity["id"]] = grouped.select(*[
                F.col(f"winner_{index}.value").alias(prop["name"]) for index, prop in enumerate(entity["properties"])
            ])
            provenance = []
            for index, prop in enumerate(entity["properties"]):
                winner = f"winner_{index}"
                provenance.append(grouped.filter(F.col(f"{winner}.value").isNotNull()).select(
                    F.col("__mdm_key").alias("record_key"), F.lit(prop["name"]).alias("property"),
                    F.col(f"{winner}.source").alias("source"), F.col(f"{winner}.column").alias("source_column"),
                    F.col(f"{winner}.source_version").alias("source_version"), F.lit(expected_sha256).alias("release_hash"),
                ))
            provenances[entity["id"]] = reduce(lambda a, b: a.unionByName(b), provenance)
        entities = {e["id"]: e for e in contract["entities"]}
        for relation in contract["relationships"]:
            fk, pk = relation["column"], entities[relation["to"]]["key"]
            left = outputs[relation["from"]].select(F.col(fk).alias("__fk")).filter(F.col("__fk").isNotNull())
            right = outputs[relation["to"]].select(F.col(pk).alias("__pk"))
            if left.join(right, left["__fk"] == right["__pk"], "left_anti").limit(1).count():
                raise ContractError(f"{relation['name']}: orphan foreign keys block publication.")
        # Recheck ontology immediately before publication; source frames stay on pinned Delta versions.
        read_live_ontology(contract["ontology"], fabric_token)
        spark.conf.set("spark.sql.parquet.vorder.default", "true")
        spark.conf.set("spark.databricks.delta.optimizeWrite.enabled", "true")
        spark.conf.set("spark.databricks.delta.optimizeWrite.binSize", "1g")
        spark.sql("CREATE SCHEMA IF NOT EXISTS gold")
        counts = {}
        for entity in contract["entities"]:
            entity_id, table = entity["id"], tables[entity["id"]]
            frame = outputs[entity_id]
            counts[entity_id] = frame.count()
            frame.write.format("delta").mode("errorifexists").saveAsTable(table)
            actual = spark.table(table)
            if actual.count() != counts[entity_id] or actual.dtypes != frame.dtypes:
                raise ContractError(f"{table}: Gold readback failed; no commit marker will be written.")
            lineage = provenances[entity_id]
            lineage.write.format("delta").mode("errorifexists").saveAsTable(f"{table}_lineage")
            if spark.table(f"{table}_lineage").count() != lineage.count():
                raise ContractError(f"{table}: provenance readback failed; no commit marker will be written.")
        encoded = {"definition": {"parts": [
            {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}
            for path, text in definition.items()
        ]}}
        receipt = {
            "release_hash": expected_sha256, "created_at": datetime.now(timezone.utc).isoformat(),
            "tables_json": canonical_json(tables), "counts_json": canonical_json(counts),
            "source_versions_json": canonical_json(versions), "contract_json": canonical_json(contract),
            "semantic_definition_json": canonical_json(encoded),
        }
        schema = StructType([StructField(key, StringType(), False) for key in receipt])
        spark.createDataFrame([receipt], schema).write.format("delta").mode("errorifexists").saveAsTable(marker)
        verified = spark.table(marker).collect()
        if len(verified) != 1 or verified[0].asDict() != receipt:
            raise ContractError("Release marker readback failed. Do not bind a semantic model to this run.")
        return {**receipt, "commit_table": marker, "status": "gold_committed", "semantic_model_status": "definition_prepared_not_deployed"}
    finally:
        for frame in cached:
            frame.unpersist()
