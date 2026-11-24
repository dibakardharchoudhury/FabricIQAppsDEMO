"""Build an isolated, one-shot Silver conformance notebook for the Hydro demo pilot."""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

SOURCE_SCHEMAS = {
    "facilities": {
        "facility_id": "string", "facility_name": "string", "type": "string",
        "country": "string", "lat": "string", "lon": "string", "commissioned_date": "string",
    },
    "systems": {
        "system_id": "string", "facility_id": "string", "system_name": "string",
        "oag_rds_system_code": "string",
    },
    "equipment": {
        "equipment_id": "string", "facility_id": "string", "system_id": "string",
        "equipment_type_code": "string", "equipment_type_name": "string", "tag": "string",
        "manufacturer": "string", "model": "string", "criticality": "int",
        "install_date": "date", "status": "string", "is_active": "boolean",
    },
}

CONFORMANCE_CODE = '''import json
import notebookutils
from delta.tables import DeltaTable
from pyspark.sql import functions as F

context = notebookutils.runtime.context
if (context.get("defaultLakehouseId") != target_lakehouse_id
        or context.get("defaultLakehouseWorkspaceId") != target_workspace_id):
    raise RuntimeError("Pilot notebook is not bound to its approved isolated Lakehouse.")

receipt_path = "Files/master-data/pilot-silver-receipt.json"
if notebookutils.fs.exists(receipt_path):
    raise RuntimeError("Pilot already has a Silver receipt; inspect it instead of rerunning.")
for entity in expected_schemas:
    if spark.catalog.tableExists(f"silver.{entity}"):
        raise RuntimeError(f"silver.{entity} already exists; never overwrite an incomplete pilot.")

frames, versions, counts = {}, {}, {}
try:
    for entity, expected in expected_schemas.items():
        path = (f"abfss://{source_workspace_id}@onelake.dfs.fabric.microsoft.com/"
                f"{source_lakehouse_id}/Tables/silver_{entity}")
        version = int(DeltaTable.forPath(spark, path).history(1).select("version").head()["version"])
        frame = spark.read.format("delta").option("versionAsOf", version).load(path)
        if dict(frame.dtypes) != expected:
            raise RuntimeError(f"{entity}: source schema changed; review before copying.")
        key_name = {"facilities": "facility_id", "systems": "system_id", "equipment": "equipment_id"}[entity]
        key = F.col(key_name)
        if frame.filter(key.isNull() | (F.length(F.trim(key)) == 0)).limit(1).count():
            raise RuntimeError(f"{entity}: missing business keys.")
        if frame.groupBy(key_name).count().filter(F.col("count") > 1).limit(1).count():
            raise RuntimeError(f"{entity}: duplicate business keys.")
        if entity == "equipment":
            # Explicit, lossless integer widening; the date is retained as ISO text
            # for inventory and excluded from Gold pending date-type support.
            frame = frame.withColumn("criticality", F.col("criticality").cast("long"))
            frame = frame.withColumn("install_date", F.date_format("install_date", "yyyy-MM-dd"))
        frames[entity] = frame.persist()
        counts[entity] = frame.count()
        if not counts[entity]:
            raise RuntimeError(f"{entity}: empty source.")
        versions[entity] = version
    for child, fk, parent, pk in [
        ("equipment", "system_id", "systems", "system_id"),
        ("systems", "facility_id", "facilities", "facility_id"),
    ]:
        foreign = frames[child].select(F.col(fk).alias("fk"))
        primary = frames[parent].select(F.col(pk).alias("pk"))
        if foreign.filter(F.col("fk").isNull()).limit(1).count():
            raise RuntimeError(f"{child}: missing {fk}.")
        if foreign.join(primary, foreign.fk == primary.pk, "left_anti").limit(1).count():
            raise RuntimeError(f"{child}: orphan {fk}.")
    spark.sql("CREATE SCHEMA IF NOT EXISTS silver")
    for entity, frame in frames.items():
        table = f"silver.{entity}"
        frame.write.format("delta").mode("errorifexists").saveAsTable(table)
        actual = spark.table(table)
        if actual.count() != counts[entity] or actual.dtypes != frame.dtypes:
            raise RuntimeError(f"{table}: copied Silver readback failed.")
    receipt = {
        "status": "silver_verified", "data_classification": "hosted_hydro_demo",
        "source_workspace_id": source_workspace_id, "source_lakehouse_id": source_lakehouse_id,
        "target_workspace_id": target_workspace_id, "target_lakehouse_id": target_lakehouse_id,
        "source_versions": versions, "counts": counts,
        "schemas": {entity: dict(frame.dtypes) for entity, frame in frames.items()},
        "conformance": {"equipment.criticality": "int to bigint (lossless)",
                        "equipment.install_date": "date to ISO yyyy-MM-dd text; excluded from Gold"},
    }
    notebookutils.fs.mkdirs("Files/master-data")
    notebookutils.fs.put(receipt_path, json.dumps(receipt, sort_keys=True), False)
    if json.loads(notebookutils.fs.head(receipt_path, 100000)) != receipt:
        raise RuntimeError("Silver receipt readback failed.")
finally:
    for frame in frames.values():
        frame.unpersist()
notebookutils.notebook.exit(json.dumps(receipt))
'''


def build_notebook(source_workspace, source_lakehouse, target_workspace, target_lakehouse, target_name):
    for value in (source_workspace, source_lakehouse, target_workspace, target_lakehouse):
        if str(UUID(value)) != value.lower():
            raise ValueError("Exact workspace and Lakehouse GUIDs are required.")
    if source_workspace == target_workspace and source_lakehouse == target_lakehouse:
        raise ValueError("The pilot must not write to its original Hydro Lakehouse.")
    if not target_name.strip():
        raise ValueError("The verified target Lakehouse name is required.")
    parameters = "\n".join([
        f"source_workspace_id = {source_workspace!r}",
        f"source_lakehouse_id = {source_lakehouse!r}",
        f"target_workspace_id = {target_workspace!r}",
        f"target_lakehouse_id = {target_lakehouse!r}",
        f"expected_schemas = {SOURCE_SCHEMAS!r}",
    ]) + "\n"

    def cell(kind, source):
        value = {
            "cell_type": kind, "id": hashlib.sha256(source.encode()).hexdigest()[:16],
            "metadata": {}, "source": source.splitlines(keepends=True),
        }
        if kind == "code":
            value.update(outputs=[], execution_count=None)
        return value

    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Synapse PySpark", "language": "python", "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": target_lakehouse, "default_lakehouse_name": target_name,
                "default_lakehouse_workspace_id": target_workspace,
            }},
        },
        "cells": [
            cell("markdown", "# Concord Hydro demo: isolated Silver conformance\n\n"
                 "One-shot pilot over hosted demo data, not independent production feeds. "
                 "All source reads are Delta-version pinned. Original sources and ontology are untouched. "
                 "Existing targets fail closed; only a verified Silver receipt unlocks contract review. "
                 "Gold publication still requires separately approved mappings and digest.\n"),
            cell("code", parameters), cell("code", CONFORMANCE_CODE),
        ],
    }


def build_draft(receipt, ontology):
    """Build unreviewed mappings from a verified Silver receipt, never approval."""
    if receipt.get("status") != "silver_verified" or receipt.get("data_classification") != "hosted_hydro_demo":
        raise ValueError("A verified Hydro demo Silver receipt is required.")
    expected = {entity: dict(columns) for entity, columns in SOURCE_SCHEMAS.items()}
    expected["equipment"].update(criticality="bigint", install_date="string")
    if receipt.get("schemas") != expected or set(receipt.get("counts", {})) != set(expected):
        raise ValueError("The copied Silver schema is incomplete or changed.")
    if any(type(count) is not int or count <= 0 for count in receipt["counts"].values()):
        raise ValueError("Every pilot entity must have verified rows.")
    if ontology.get("generation") != 2 or len(ontology.get("definitionHash", "")) != 64:
        raise ValueError("A live generation-2 ontology definition fingerprint is required.")
    for value in (receipt["target_workspace_id"], receipt["target_lakehouse_id"],
                  ontology["workspaceId"], ontology["itemId"]):
        if str(UUID(value)) != value.lower():
            raise ValueError("Verified workspace, Lakehouse and ontology GUIDs are required.")
    entities, sources, mappings = [], [], []
    keys = {"facilities": "facility_id", "systems": "system_id", "equipment": "equipment_id"}
    rename = {"facilities": {"type": "facility_type", "lat": "latitude", "lon": "longitude"},
              "systems": {}, "equipment": {"tag": "equipment_tag"}}
    required = {"facilities": {"facility_id"}, "systems": {"system_id", "facility_id"},
                "equipment": {"equipment_id", "system_id"}}
    types = {"string": "string", "bigint": "int64", "boolean": "boolean"}
    for entity, columns in expected.items():
        source_id = f"hydro_{entity}"
        properties = []
        for column, dtype in columns.items():
            excluded = entity == "equipment" and column == "install_date"
            canonical = None if excluded else rename[entity].get(column, column)
            if canonical is not None:
                properties.append({
                    "name": canonical, "ontologyProperty": column,
                    "label": canonical.replace("_", " ").capitalize(),
                    "type": types[dtype], "required": column in required[entity],
                    "strategy": "require_equal", "priority": [],
                })
            mappings.append({
                "source": source_id, "column": column, "target": canonical, "transform": "identity",
                "reason": ("Retain installation date in Silver as ISO text; exclude from Gold until date-type support is reviewed."
                           if excluded else "Proposed exact mapping to the declared Hydro ontology property; preserve business values and keys."),
                "reviewed": False,
            })
        entities.append({
            "id": entity, "ontologyEntity": entity, "label": entity.capitalize(),
            "goldTable": entity, "key": keys[entity], "properties": properties,
        })
        sources.append({
            "id": source_id, "label": f"Hosted Hydro demo: {entity}", "entity": entity,
            "workspaceId": receipt["target_workspace_id"], "lakehouseId": receipt["target_lakehouse_id"],
            "schema": "silver", "table": entity, "keyColumn": keys[entity],
            "keyNamespace": f"hydro_demo_{keys[entity]}",
            "columns": [{"name": name, "type": types[dtype]} for name, dtype in columns.items()],
        })
    return {
        "formatVersion": 1, "name": "Concord Hydro demo governance pilot", "ontology": ontology,
        "entities": entities, "sources": sources, "mappings": mappings,
        "relationships": [
            {"name": "equipment_in_systems", "ontologyRelationship": "equipment_in_systems",
             "from": "equipment", "column": "system_id", "to": "systems"},
            {"name": "systems_in_facilities", "ontologyRelationship": "systems_in_facilities",
             "from": "systems", "column": "facility_id", "to": "facilities"},
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-workspace", "source-lakehouse", "target-workspace", "target-lakehouse", "target-name"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    notebook = build_notebook(args.source_workspace, args.source_lakehouse, args.target_workspace,
                              args.target_lakehouse, args.target_name)
    args.output.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(notebook, indent=2)
    (args.output / "MDM_000_prepare_hydro_silver.ipynb").write_text(raw, encoding="utf-8")
    import base64
    definition = {"definition": {"format": "ipynb", "parts": [{
        "path": "notebook-content.ipynb", "payload": base64.b64encode(raw.encode()).decode(),
        "payloadType": "InlineBase64",
    }]}}
    (args.output / "silver-definition.json").write_text(json.dumps(definition), encoding="utf-8")
    print("Prepared the isolated Silver notebook definition; no Fabric writes or jobs submitted.")


if __name__ == "__main__":
    main()
