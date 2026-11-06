"""Portable, fail-closed validation for the Silver-to-Gold stewardship contract."""

import hashlib
import json
import re
import uuid

IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
SCALARS = {"string", "int64", "boolean"}
TRANSFORMS = {"identity", "trim", "lower", "upper"}


class ContractError(ValueError):
    pass


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContractError(f"{label}: missing or unknown contract fields.")
    for key, check in fields.items():
        if not check(value[key]):
            raise ContractError(f"{label}.{key}: invalid value.")


def _text(value):
    return isinstance(value, str) and len(value) <= 2048


def _list(value):
    return isinstance(value, list) and len(value) <= 2000


def _strings(value):
    return _list(value) and all(_text(item) for item in value)


def _guid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _unique(values, label):
    if len({value.lower() for value in values}) != len(values):
        raise ContractError(f"{label}: values must be unique, ignoring case.")


def _identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise ContractError(f"{value!r}: use lower_snake_case, maximum 63 characters.")


def _label(value):
    if not value.strip() or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ContractError("Semantic labels and ontology names must be nonempty and contain no control characters.")


def validate(contract):
    _object(contract, {
        "formatVersion": lambda v: type(v) is int and v == 1, "name": _text, "ontology": lambda v: isinstance(v, dict),
        "entities": _list, "sources": _list, "mappings": _list, "relationships": _list,
    }, "contract")
    _object(contract["ontology"], {
        "workspaceId": _guid, "itemId": _guid, "generation": lambda v: type(v) is int and v == 2,
        "definitionHash": lambda v: isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) is not None,
    }, "ontology")
    _label(contract["name"])
    if len(contract["name"]) > 255:
        raise ContractError("Product names are limited to 255 characters.")
    if not contract["entities"] or not contract["sources"]:
        raise ContractError("At least one entity and source are required.")
    for entity in contract["entities"]:
        _object(entity, {
            "id": _text, "ontologyEntity": _text, "label": _text, "goldTable": _text, "key": _text, "properties": _list,
        }, "entity")
        for field in ("id", "goldTable", "key"):
            _identifier(entity[field])
        _label(entity["label"])
        _label(entity["ontologyEntity"])
        for prop in entity["properties"]:
            _object(prop, {
                "name": _text, "ontologyProperty": _text, "label": _text, "type": lambda v: isinstance(v, str) and v in SCALARS,
                "required": lambda v: type(v) is bool,
                "strategy": lambda v: v in ("require_equal", "source_priority"), "priority": _strings,
            }, "property")
            _identifier(prop["name"])
            _label(prop["label"])
            _label(prop["ontologyProperty"])
        for field in ("name", "label", "ontologyProperty"):
            _unique([p[field] for p in entity["properties"]], f"{entity['id']} properties/{field}")
        key = next((p for p in entity["properties"] if p["name"] == entity["key"]), None)
        if not key or key["type"] != "string" or not key["required"] or key["strategy"] != "require_equal":
            raise ContractError(f"{entity['id']}: key must be a required string with require_equal.")
    for field in ("id", "label", "goldTable", "ontologyEntity"):
        _unique([e[field] for e in contract["entities"]], f"entities/{field}")
    entities = {e["id"]: e for e in contract["entities"]}
    for source in contract["sources"]:
        _object(source, {
            "id": _text, "label": _text, "entity": _text, "workspaceId": _guid, "lakehouseId": _guid,
            "schema": _text, "table": _text, "keyColumn": _text, "keyNamespace": _text, "columns": _list,
        }, "source")
        for field in ("id", "schema", "table", "keyColumn"):
            _identifier(source[field])
        if source["entity"] not in entities or not source["keyNamespace"].strip():
            raise ContractError(f"{source['id']}: entity and shared key namespace are required.")
        for column in source["columns"]:
            _object(column, {"name": _text, "type": lambda v: isinstance(v, str) and v in SCALARS}, "column")
            _identifier(column["name"])
        _unique([c["name"] for c in source["columns"]], "source columns")
        if not any(c["name"] == source["keyColumn"] and c["type"] == "string" for c in source["columns"]):
            raise ContractError(f"{source['id']}: key must be a declared string column.")
    _unique([s["id"] for s in contract["sources"]], "source identifiers")
    _unique(["/".join(s[k] for k in ("workspaceId", "lakehouseId", "schema", "table")) for s in contract["sources"]], "source tables")
    sources = {s["id"]: s for s in contract["sources"]}
    decisions = {}
    for mapping in contract["mappings"]:
        _object(mapping, {
            "source": _text, "column": _text, "target": lambda v: v is None or _text(v),
            "transform": lambda v: isinstance(v, str) and v in TRANSFORMS, "reason": _text,
            "reviewed": lambda v: type(v) is bool,
        }, "mapping")
        source = sources.get(mapping["source"])
        column = next((c for c in source["columns"] if c["name"] == mapping["column"]), None) if source else None
        if not column:
            raise ContractError(f"{mapping['source']}.{mapping['column']}: stale or unknown source column.")
        if not mapping["reviewed"] or not mapping["reason"].strip():
            raise ContractError(f"{mapping['source']}.{mapping['column']}: review and rationale required.")
        pair = (mapping["source"], mapping["column"])
        if pair in decisions:
            raise ContractError("A source column has more than one decision.")
        decisions[pair] = mapping
        entity = entities[source["entity"]]
        if mapping["target"] is not None:
            prop = next((p for p in entity["properties"] if p["name"] == mapping["target"]), None)
            if not prop or prop["type"] != column["type"]:
                raise ContractError(f"{pair}: source/target type mismatch or unknown canonical property.")
        if mapping["transform"] != "identity" and column["type"] != "string":
            raise ContractError(f"{pair}: normalization requires a string.")
        if mapping["column"] == source["keyColumn"]:
            if mapping["target"] != entity["key"] or mapping["transform"] != "identity":
                raise ContractError(f"{source['id']}: key must map unchanged to canonical key.")
        elif mapping["target"] == entity["key"]:
            raise ContractError(f"{source['id']}: only the declared source key can map to canonical key.")
    for source in contract["sources"]:
        for column in source["columns"]:
            if (source["id"], column["name"]) not in decisions:
                raise ContractError(f"{source['id']}.{column['name']}: new column requires a reviewed decision.")
    for entity in contract["entities"]:
        inputs = [s for s in contract["sources"] if s["entity"] == entity["id"]]
        if not inputs or len({s["keyNamespace"] for s in inputs}) != 1:
            raise ContractError(f"{entity['id']}: an explicitly shared key namespace is required; crosswalk local IDs upstream.")
        for prop in entity["properties"]:
            ids = [m["source"] for m in contract["mappings"] if m["target"] == prop["name"]
                   and sources[m["source"]]["entity"] == entity["id"]]
            if not ids or len(ids) != len(set(ids)):
                raise ContractError(f"{entity['id']}.{prop['name']}: exactly one input per contributing source is required.")
            if prop["strategy"] == "source_priority":
                if len(prop["priority"]) != len(ids) or set(prop["priority"]) != set(ids):
                    raise ContractError(f"{entity['id']}.{prop['name']}: list each contributing source exactly once in priority.")
            elif prop["priority"]:
                raise ContractError("require_equal must not define source priority.")
    relationship_names, foreign_keys = [], set()
    components = {e: e for e in entities}

    def root(key):
        while components[key] != key:
            key = components[key]
        return key

    for relation in contract["relationships"]:
        _object(relation, {"name": _text, "ontologyRelationship": _text, "from": _text, "column": _text, "to": _text}, "relationship")
        _identifier(relation["name"])
        _label(relation["ontologyRelationship"])
        relationship_names.append(relation["name"])
        parent, child = entities.get(relation["to"]), entities.get(relation["from"])
        prop = next((p for p in child["properties"] if p["name"] == relation["column"]), None) if child else None
        if not parent or not child or child == parent or not prop or prop["type"] != "string":
            raise ContractError("Only declared, string-key, many-to-one relationships between different entities are supported.")
        pair = (relation["from"], relation["column"])
        if pair in foreign_keys:
            raise ContractError("A foreign key has multiple active relationships.")
        foreign_keys.add(pair)
        a, b = root(relation["from"]), root(relation["to"])
        if a == b:
            raise ContractError("Cyclic or multiple active relationship paths are not supported.")
        components[a] = b
    _unique(relationship_names, "relationship names")
    return contract


def load_approved(raw, expected_sha256):
    """The expected hash comes from protected pipeline config, never from the uploaded file."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ContractError("A separately approved SHA-256 digest is required.")
    if not isinstance(raw, (str, bytes)) or len(raw) > 2_000_000:
        raise ContractError("Contract must be JSON, maximum 2 MB.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    contract = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ContractError(f"Invalid JSON constant: {value}")))
    if fingerprint(contract) != expected_sha256:
        raise ContractError("Contract differs from the independently approved pipeline digest.")
    return validate(contract)


def source_path(source):
    if not _guid(source["workspaceId"]) or not _guid(source["lakehouseId"]):
        raise ContractError("Source workspace and Lakehouse must be canonical GUIDs.")
    _identifier(source["schema"])
    _identifier(source["table"])
    return (f"abfss://{source['workspaceId']}@onelake.dfs.fabric.microsoft.com/"
            f"{source['lakehouseId']}/Tables/{source['schema']}/{source['table']}")


def verify_schema(source, observed):
    expected = {column["name"]: column["type"] for column in source["columns"]}
    if expected != observed:
        added = sorted(set(observed) - set(expected))
        removed = sorted(set(expected) - set(observed))
        changed = sorted(k for k in set(expected) & set(observed) if expected[k] != observed[k])
        raise ContractError(f"{source['id']}: schema drift; added={added}, removed={removed}, changed={changed}. Steward review required.")


def table_names(contract, digest):
    return {e["id"]: f"gold.mdm_{e['goldTable']}_{digest[:16]}" for e in contract["entities"]}


def semantic_definition(contract, digest, workspace_id, lakehouse_id):
    """Return a new model definition, never a partial PATCH of an existing model."""
    validate(contract)
    if not _guid(workspace_id) or not _guid(lakehouse_id) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ContractError("Verified Gold workspace/Lakehouse GUIDs and release digest are required.")
    if fingerprint(contract) != digest:
        raise ContractError("Semantic definition digest does not match the contract.")

    def quoted(value):
        return "'" + value.replace("'", "''") + "'"

    tables = table_names(contract, digest)
    parts = {
        "definition.pbism": canonical_json({"version": "4.0", "settings": {}}),
        "definition/database.tmdl": "database Concord\n\tcompatibilityLevel: 1702\n",
        "definition/model.tmdl": (
            "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
            "\tdiscourageImplicitMeasures\n\tsourceQueryCulture: en-US\n"
            + "".join(f"\tref table {quoted(e['label'])}\n" for e in contract["entities"])
        ),
        "definition/expressions.tmdl": (
            "expression MdmGold =\n\t\tlet\n"
            f'\t\t\tSource = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}", [HierarchicalNavigation=true])\n'
            "\t\tin\n\t\t\tSource\n"
        ),
    }
    entities = {entity["id"]: entity for entity in contract["entities"]}
    for entity in contract["entities"]:
        lines = [f"/// One golden record per {entity['key']}; immutable approved release {digest}.", f"table {quoted(entity['label'])}"]
        keys = {entity["key"]} | {r["column"] for r in contract["relationships"] if r["from"] == entity["id"]}
        for prop in entity["properties"]:
            lines += [
                f"\tcolumn {quoted(prop['label'])}", f"\t\tdataType: {prop['type']}",
                f"\t\tsourceColumn: {prop['name']}", "\t\tsummarizeBy: none",
            ]
            if prop["name"] in keys:
                lines.append("\t\tisHidden")
        lines += [
            f"\tpartition {quoted(entity['label'])} = entity", "\t\tmode: directLake", "\t\tsource",
            f"\t\t\tentityName: {tables[entity['id']].split('.')[1]}", "\t\t\tschemaName: gold", "\t\t\texpressionSource: MdmGold",
        ]
        parts[f"definition/tables/{entity['id']}.tmdl"] = "\n".join(lines) + "\n"
    relations = []
    for relation in contract["relationships"]:
        source, target = entities[relation["from"]], entities[relation["to"]]
        fk = next(p for p in source["properties"] if p["name"] == relation["column"])
        pk = next(p for p in target["properties"] if p["name"] == target["key"])
        relations += [
            f"relationship {relation['name']}",
            f"\tfromColumn: {quoted(source['label'])}.{quoted(fk['label'])}",
            f"\ttoColumn: {quoted(target['label'])}.{quoted(pk['label'])}",
            "\tfromCardinality: many", "\ttoCardinality: one", "\tcrossFilteringBehavior: oneDirection",
        ]
    if relations:
        parts["definition/relationships.tmdl"] = "\n".join(relations) + "\n"
    return parts
