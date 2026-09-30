# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "c42060fb-abbc-4fa1-ac87-ed0a5c460aaf",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI_V6",
# META       "default_lakehouse_workspace_id": "a79a4b7e-e508-4fa4-8b6f-15deadca0f34",
# META       "known_lakehouses": [
# META         {
# META           "id": "c42060fb-abbc-4fa1-ac87-ed0a5c460aaf"
# META         }
# META       ]
# META     },
# META     "environment": {}
# META   }
# META }

# MARKDOWN ********************

# # 06 — Bind Eventhouse RTI Stream to the v2 `signal_master`
#
# This notebook adds the Eventhouse time-series binding to the live v2 TMDL definition.
#
# Clean model:
#
# - RTI_004 creates structure; RTI_005 binds the static Lakehouse data.
# - RTI data stays in Eventhouse.
# - `signal_master` is the semantic bridge.
# - `opcua_node_id` links Eventhouse telemetry to the structured signal metadata.
# - No copied RTI Lakehouse table.
# - No `rti_measurements` ontology entity.


# Requires live integer `properties.generation == 2`; v1 is rejected, not migrated
# or deleted. REST `/v1` and name suffixes such as `_V9` do not identify generation.
# Reruns retain live TMDL identities, Lakehouse bindings, contextualizations, and
# custom parts. Conflicting structural/source changes fail before update.
# Complete service readback must verify the definition before output parts persist.
# Query/ingest endpoints are validated separately. RTI_007 generates telemetry
# on demand through `Pipe_Stream`; this setup step does not start streaming.
# Binding success does not prove native v2 graph association or agent runtime readiness.

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 1 — Config
#  Eventhouse time-series binding for signal_master
#  Reads shared values from rti_demo_settings
# ╚══════════════════════════════════════════════════════════════════════════╝

from pyspark.sql import functions as F

# --------------------------------------------
# LOAD SHARED RTI DEMO SETTINGS
# --------------------------------------------

settings_table_name = "rti_demo_settings"

spark.catalog.clearCache()
spark.sql(f"REFRESH TABLE {settings_table_name}")

settings_df = spark.read.table(settings_table_name)

settings = {
    row["setting_name"]: row["setting_value"]
    for row in settings_df.collect()
}


def first_setting(*names, required: bool = False) -> str:
    """
    Return the first non-empty setting value from the supplied setting names.
    This keeps the notebook compatible with older and newer setting keys.
    """
    for name in names:
        value = settings.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()

    if required:
        raise RuntimeError(
            f"Missing required setting. Tried these setting names: {list(names)}"
        )

    return ""

# --------------------------------------------
# WORKSPACE / FOLDER / ONTOLOGY
# Keep uppercase names because later 007 cells expect them.
# --------------------------------------------

WORKSPACE_ID = first_setting("workspace_id", required=True)
workspace_id = WORKSPACE_ID

workspace_folder_path = first_setting("workspace_folder_path", required=True)
target_folder_id = first_setting("target_folder_id", required=True)

ONTOLOGY_NAME = first_setting("ontology_name", "fabric_ontology_name", required=True)

# --------------------------------------------
# KEY VAULT / AUTH
# --------------------------------------------

key_vault_uri = first_setting("key_vault_uri", required=True)
key_vault_tenant_id_secret = first_setting("key_vault_tenant_id_secret", required=True)
key_vault_client_id_secret = first_setting("key_vault_client_id_secret", required=True)
key_vault_client_secret_secret = first_setting("key_vault_client_secret_secret", required=True)

# --------------------------------------------
# TARGET ONTOLOGY ENTITY
# The Eventhouse time-series binding attaches to signal_master.
# --------------------------------------------

STATIC_ENTITY_NAME = settings.get("signal_master_entity_name", "signal_master")

# --------------------------------------------
# EVENTHOUSE / KQL SOURCE
# --------------------------------------------
# Do not construct the Kusto URI from an Eventhouse or KQL DB display name.
# Eventhouse/Kusto endpoint values are either:
#   1. persisted by the Eventhouse setup step, or
#   2. resolved from Fabric Eventhouse properties in Cell 3.

EVENTHOUSE_NAME = first_setting("fabric_eventhouse_name", "eventhouse_name", required=True)
KQL_DB_NAME = first_setting("fabric_kql_db_name", "kql_database_name", "kql_db_name", required=True)
KQL_TABLE_NAME = first_setting("fabric_eventhouse_table", "eventhouse_table_name", "kql_table_name", required=True)

EVENTHOUSE_ID = first_setting("fabric_eventhouse_id", "eventhouse_id")
KQL_DATABASE_ID = first_setting("fabric_kql_db_id", "kql_database_id", "kql_db_id")
KQL_DB_ID = KQL_DATABASE_ID

cluster_query_uri = first_setting("cluster_query_uri")
cluster_ingest_uri = first_setting("cluster_ingest_uri")

CLUSTER_QUERY_URI = cluster_query_uri
CLUSTER_INGEST_URI = cluster_ingest_uri

# --------------------------------------------
# EVENTHOUSE COLUMNS
# Must match slim OPCUAEvents table:
#   event_time, opcua_node_id, value, quality
# --------------------------------------------

TIMESTAMP_COLUMN_NAME = settings.get("timeseries_timestamp_column", "event_time")
KEY_COLUMN_NAME = settings.get("timeseries_key_column", "opcua_node_id")
VALUE_COLUMN_NAME = settings.get("timeseries_value_column", "value")
QUALITY_COLUMN_NAME = settings.get("timeseries_quality_column", "quality")

print("✅ Loaded 006 v2 configuration from shared settings.")
print("✅ Workspace ID:", WORKSPACE_ID)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Static ontology entity:", STATIC_ENTITY_NAME)
print("✅ Eventhouse name:", EVENTHOUSE_NAME)
print("✅ Eventhouse ID:", EVENTHOUSE_ID if EVENTHOUSE_ID else "<will resolve in Cell 3>")
print("✅ KQL database:", KQL_DB_NAME)
print("✅ KQL database ID:", KQL_DATABASE_ID if KQL_DATABASE_ID else "<will resolve in Cell 3>")
print("✅ KQL table:", KQL_TABLE_NAME)
print("✅ Cluster query URI:", CLUSTER_QUERY_URI if CLUSTER_QUERY_URI else "<will resolve from Eventhouse properties in Cell 3>")
print("✅ Cluster ingest URI:", CLUSTER_INGEST_URI if CLUSTER_INGEST_URI else "<will resolve from Eventhouse properties in Cell 3>")
print("✅ Timestamp column:", TIMESTAMP_COLUMN_NAME)
print("✅ Key column:", KEY_COLUMN_NAME)
print("✅ Value column:", VALUE_COLUMN_NAME)
print("✅ Quality column:", QUALITY_COLUMN_NAME)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""Pure support source embedded in 004/005/006; notebooks need no local imports.

The supported mutation scope is default-namespace entities. Unknown objects and
parts are retained, not interpreted or regenerated. Ambiguous edits fail closed.
"""
import base64
import json
import re
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


def _normal_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode_part(part):
    return _normal_text(base64.b64decode(part["payload"]).decode("utf-8"))


def _encode_part(path, text):
    return {"path": path, "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "payloadType": "InlineBase64"}


def _parts_by_path(parts):
    result = {part["path"]: part for part in parts}
    if len(result) != len(parts):
        raise RuntimeError("Duplicate ontology definition part paths")
    return result


def _identifier_parts(value):
    tokens = re.findall(r"'(?:[^']|'')*'|[^.\s]+", value.strip())
    if ".".join(tokens) != value.strip():
        raise RuntimeError(f"Unsupported or ambiguous TMDL reference: {value!r}")
    return [token[1:-1].replace("''", "'") if token.startswith("'") else token
            for token in tokens]


def _local_name(value):
    names = _identifier_parts(value)
    if len(names) == 2 and names[0] == "default":
        return names[1]
    if len(names) != 1:
        raise RuntimeError(f"Only the default namespace is managed: {value!r}")
    return names[0]


def _quote_name(name):
    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _object_spans(text, kind, depth=None):
    """Locate named objects, stopping at *any* same/outer-indent sibling."""
    text = _normal_text(text)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = []
    for index, line in enumerate(lines):
        match = re.match(r"^([ \t]*)" + re.escape(kind) + r" (.+?)\s*$", line)
        if not match:
            continue
        indentation = len(match[1].expandtabs(4))
        if depth is not None and indentation != depth:
            continue
        # '=' introduces expression/partition content, outside the identifier.
        raw_name = re.split(r"\s+=\s*|\s*=$", match[2], maxsplit=1)[0]
        name = _local_name(raw_name)
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and not candidate.lstrip().startswith("//"):
                size = len(candidate) - len(candidate.lstrip(" \t"))
                if len(candidate[:size].expandtabs(4)) <= indentation:
                    break
            end += 1
        result.append((name, offsets[index], offsets[end], text[offsets[index]:offsets[end]]))
    names = [item[0] for item in result]
    if len(names) != len(set(names)):
        raise RuntimeError(f"Duplicate {kind} object names")
    return result


def _object_block(text, kind, name, depth=None):
    return next((block for actual, _, _, block in _object_spans(text, kind, depth)
                 if actual == name), None)


def _replace_object(text, kind, name, replacement, depth=None):
    text = _normal_text(text)
    matches = [item for item in _object_spans(text, kind, depth) if item[0] == name]
    if len(matches) != 1:
        raise RuntimeError(f"Cannot uniquely find {kind} {name!r}")
    _, start, end, _ = matches[0]
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def _direct_setting(text, key):
    """Read a direct child setting, never one belonging to another object."""
    lines = _normal_text(text).splitlines()
    if not lines:
        return None
    root = len(lines[0]) - len(lines[0].lstrip(" \t"))
    root_depth = len(lines[0][:root].expandtabs(4))
    candidates = []
    for line in lines[1:]:
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        prefix = line[:len(line) - len(line.lstrip(" \t"))]
        depth = len(prefix.expandtabs(4))
        if depth <= root_depth:
            break
        candidates.append((depth, line.strip()))
    if not candidates:
        return None
    child_depth = min(depth for depth, _ in candidates)
    values = [line.split(":", 1)[1].strip() for depth, line in candidates
              if depth == child_depth and line.startswith(key + ":")]
    if len(values) > 1:
        raise RuntimeError(f"Duplicate setting {key!r}")
    return values[0] if values else None


def _property_objects(text):
    return {name: (_direct_setting(block, "dataType"), block)
            for name, _, _, block in _object_spans(text, "property", 4)}


def _require_v2_ontology(item):
    generation = (item.get("properties") or {}).get("generation")
    if type(generation) is int and generation == 1:
        raise RuntimeError("Ontology v1 is not supported. Replacement required: create a separate v2 ontology; this notebook will not delete or migrate the existing item.")
    if type(generation) is not int or generation != 2:
        raise RuntimeError(f"A live ontology with properties.generation == 2 is required; received {generation!r}")
    return 2


def _resolve_ontology_generation(item, parts):
    generation = _require_v2_ontology(item)
    paths = set(_parts_by_path(parts))
    if ("database.tmdl" not in paths or "definition.json" in paths
            or any(path.startswith(("EntityTypes/", "RelationshipTypes/")) for path in paths)):
        raise RuntimeError("A verified v2 TMDL definition is required; replacement is required for legacy definitions")
    return generation


def _list_fabric_values(url):
    initial = url
    seen = set()
    values = []
    while url:
        if (url in seen or urlsplit(url).netloc != urlsplit(initial).netloc
                or urlsplit(url).scheme != urlsplit(initial).scheme):
            raise RuntimeError("Invalid or repeated Fabric pagination URL")
        seen.add(url)
        response = api_request("GET", url)
        if response.status_code != 200:
            raise RuntimeError(f"Fabric listing failed: {response.status_code}")
        body = response.json()
        values.extend(body.get("value", []))
        next_url = body.get("continuationUri") or body.get("@odata.nextLink")
        if next_url:
            url = urljoin(initial, next_url)
        elif body.get("continuationToken"):
            split = urlsplit(initial)
            query = dict(parse_qsl(split.query))
            query["continuationToken"] = body["continuationToken"]
            url = urlunsplit(split._replace(query=urlencode(query)))
        else:
            url = None
    return values


def _created_item_result(operation_url):
    wait_for_lro(operation_url)
    response = api_request("GET", _fabric_result_url(operation_url))
    if response.status_code != 200:
        raise RuntimeError(f"Created ontology LRO result failed: {response.status_code}")
    item = response.json()
    if not item.get("id") or not item.get("displayName"):
        raise RuntimeError("Created ontology operation result is not an item")
    return item


def _fabric_operation_url(response, api_root):
    headers = {name.lower(): value for name, value in response.headers.items()}
    operation_id = headers.get("x-ms-operation-id")
    if operation_id:
        if not re.fullmatch(r"[A-Za-z0-9-]+", operation_id):
            raise RuntimeError("Invalid Fabric operation ID")
        return api_root.rstrip("/") + "/operations/" + operation_id
    location = headers.get("operation-location") or headers.get("location")
    if not location:
        raise RuntimeError("Fabric LRO has neither an operation ID nor a Location header")
    url = urljoin(api_root.rstrip("/") + "/", location)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    trusted = any(host == domain or host.endswith("." + domain)
                  for domain in ("fabric.microsoft.com", "analysis.windows.net", "api.powerbi.com"))
    if parsed.scheme != "https" or not trusted or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise RuntimeError("Fabric LRO Location is not a trusted HTTPS Fabric endpoint")
    return url


def _fabric_result_url(operation_url):
    parsed = urlsplit(operation_url)
    return urlunsplit(parsed._replace(path=parsed.path.rstrip("/") + "/result"))


def _validated_definition_response(response, operation):
    if response.status_code != 200:
        raise RuntimeError(f"{operation} failed: HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{operation} returned invalid or empty JSON") from exc
    definition = body.get("definition") if isinstance(body, dict) else None
    parts = definition.get("parts") if isinstance(definition, dict) else None
    if not isinstance(parts, list) or not parts:
        raise RuntimeError(f"{operation} must return a nonempty definition.parts array; refusing a destructive empty baseline")
    for part in parts:
        if (not isinstance(part, dict) or not isinstance(part.get("path"), str)
                or not part["path"].strip() or part.get("payloadType") != "InlineBase64"
                or not isinstance(part.get("payload"), str)):
            raise RuntimeError(f"{operation} returned a malformed definition part")
        try:
            decoded = base64.b64decode(part["payload"], validate=True).decode("utf-8")
            if part["path"].endswith(".json") or part["path"] == ".platform":
                json.loads(decoded)
        except ValueError as exc:
            raise RuntimeError(f"{operation} returned invalid payload for {part['path']}") from exc
    paths = set(_parts_by_path(parts))
    if ".platform" not in paths or "database.tmdl" not in paths or "definition.json" in paths:
        raise RuntimeError(f"{operation} must return v2 TMDL root parts; legacy items require replacement")
    return body


def _get_fabric_ontology_definition(api_root, workspace_id, ontology_id):
    response = api_request(
        "POST", f"{api_root.rstrip('/')}/workspaces/{workspace_id}/ontologies/{ontology_id}/getDefinition")
    if response.status_code == 202:
        operation_url = _fabric_operation_url(response, api_root)
        wait_for_lro(operation_url)
        response = api_request("GET", _fabric_result_url(operation_url), timeout=120)
        return _validated_definition_response(response, "Ontology getDefinition LRO result")
    return _validated_definition_response(response, "Ontology getDefinition")


def _structural_lines(text):
    """An object-scoped readback contract: mappings cannot migrate to siblings."""
    stack = []
    result = Counter()
    for line in _normal_text(text).splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        prefix = line[:len(line) - len(line.lstrip(" \t"))]
        depth = len(prefix.expandtabs(4))
        statement = line.strip()
        match = re.match(r"(ref (?:entity|table|namespace)|entity|table|column|property|entityRelationship|relationship) (.+)$", statement)
        if match:
            statement = match[1] + " " + ".".join(_identifier_parts(match[2]))
        else:
            match = re.match(r"(keyProperty|backingTable|fromEntity|toEntity|valueColumn|orderingColumn|fromColumn|toColumn|table|relationship):\s*(.+)$", statement)
            if match:
                names = _identifier_parts(match[2])
                if len(names) > 1 and names[0] == "default":
                    names = names[1:]
                statement = match[1] + ": " + ".".join(names)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, statement))
        result[tuple(value for _, value in stack)] += 1
    return result


def _json_contains(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _json_contains(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_json_contains(value, candidate) for candidate in actual) for value in expected)
    return expected == actual


def _verify_definition(expected, actual):
    wanted = _parts_by_path(expected)
    retained = _parts_by_path(actual)
    for path, part in wanted.items():
        if path not in retained:
            raise RuntimeError(f"Fabric did not retain definition part {path}")
        before, after = _decode_part(part), _decode_part(retained[path])
        if path.endswith(".tmdl"):
            missing = _structural_lines(before) - _structural_lines(after)
            if missing:
                raise RuntimeError(f"Fabric did not retain submitted objects/settings in {path}: {list(missing)[:3]}")
        elif path.endswith(".json") or path == ".platform":
            if not _json_contains(json.loads(before), json.loads(after)):
                raise RuntimeError(f"Fabric did not retain submitted JSON in {path}")
        elif before != after:
            raise RuntimeError(f"Fabric changed unmanaged part {path}")


def _merge_gen2_structure(live_parts, desired_parts):
    result = _parts_by_path(live_parts)
    for path, desired in _parts_by_path(desired_parts).items():
        if path not in result:
            result[path] = desired
            continue
        if not path.startswith("entities/") and path not in {"entityRelationships.tmdl", "model.tmdl"}:
            continue
        live, wanted = _decode_part(result[path]), _decode_part(desired)
        if path.startswith("entities/"):
            live_entities = _object_spans(live, "entity", 0)
            wanted_entities = _object_spans(wanted, "entity", 0)
            if len(live_entities) != 1 or len(wanted_entities) != 1 or live_entities[0][0] != wanted_entities[0][0]:
                raise RuntimeError(f"Incompatible entity identity in {path}")
            if _local_name(_direct_setting(live, "keyProperty") or "") != _local_name(_direct_setting(wanted, "keyProperty") or ""):
                raise RuntimeError(f"Incompatible entity key in {path}; existing bindings were not changed")
            properties = _property_objects(live)
            for name, (dtype, block) in _property_objects(wanted).items():
                if name in properties:
                    if (properties[name][0] or "").casefold() != (dtype or "").casefold():
                        raise RuntimeError(f"Incompatible property type for {path}:{name}")
                elif _direct_setting(live, "backingTable"):
                    raise RuntimeError(f"Cannot add {name!r} to bound {path} without an explicit schema migration")
                else:
                    live = live.rstrip() + "\n\n" + block
        elif path == "entityRelationships.tmdl":
            existing = {name: block for name, _, _, block in _object_spans(live, "entityRelationship", 0)}
            for name, _, _, block in _object_spans(wanted, "entityRelationship", 0):
                pair = tuple(_local_name(_direct_setting(block, field) or "") for field in ("fromEntity", "toEntity"))
                if name in existing:
                    old_pair = tuple(_local_name(_direct_setting(existing[name], field) or "") for field in ("fromEntity", "toEntity"))
                    if old_pair != pair:
                        raise RuntimeError(f"Incompatible relationship endpoints for {name}")
                else:
                    same_endpoints = []
                    for old_name, old_block in existing.items():
                        try:
                            old_pair = tuple(_local_name(_direct_setting(old_block, field) or "")
                                             for field in ("fromEntity", "toEntity"))
                        except RuntimeError:
                            continue
                        if old_pair == pair:
                            same_endpoints.append(old_name)
                    if len(same_endpoints) > 1:
                        raise RuntimeError(f"Ambiguous existing relationship identity for {pair}")
                    if not same_endpoints:
                        live = live.rstrip() + "\n\n" + block
        else:
            for line in wanted.splitlines():
                if line.startswith("ref ") and line not in live.splitlines():
                    live = live.rstrip() + "\n" + line + "\n"
        if live != _decode_part(result[path]):
            result[path] = _encode_part(path, live)
    return list(result.values())





def _spark_api_type(data_type):
    name = str(data_type)
    mapping = {"StringType()": "String", "IntegerType()": "BigInt", "LongType()": "BigInt",
               "ShortType()": "BigInt", "ByteType()": "BigInt", "DoubleType()": "Double",
               "FloatType()": "Double", "BooleanType()": "Boolean",
               "TimestampType()": "DateTime", "DateType()": "DateTime"}
    if name not in mapping:
        raise ValueError(f"Unsupported ontology Spark type {name}; Decimal requires an explicit precision-preserving projection")
    return mapping[name]


def _resolve_own_key(entity, columns, overrides, fallback_candidates):
    if entity in overrides:
        key = overrides[entity]
        if key not in columns:
            raise RuntimeError(f"Explicit key {key!r} is missing from {entity}")
        return key
    return next((key for key in fallback_candidates if key in columns), None)


def _require_setting(block, key, expected, reference=False):
    actual = _direct_setting(block, key)
    if reference:
        def reference_parts(value):
            names = _identifier_parts(value)
            return names[1:] if len(names) > 1 and names[0] == "default" else names
        valid = actual is not None and reference_parts(actual) == reference_parts(expected)
    else:
        valid = actual == expected
    if not valid:
        raise RuntimeError(f"Incompatible {key}: expected {expected!r}, found {actual!r}")


def _append_model_ref(model, kind, name):
    for line in model.splitlines():
        match = re.fullmatch(r"ref " + re.escape(kind) + r" (.+)", line)
        if match and _local_name(match[1]) == name:
            return model
    return model.rstrip() + f"\nref {kind} {_quote_name(name)}\n"


def _unnamed_blocks(text, kind, depth):
    lines = _normal_text(text).splitlines(keepends=True)
    blocks = []
    for index, line in enumerate(lines):
        prefix = line[:len(line) - len(line.lstrip(" \t"))]
        if line.strip() != kind or len(prefix.expandtabs(4)) != depth:
            continue
        owned = [line]
        for following in lines[index + 1:]:
            indent = following[:len(following) - len(following.lstrip(" \t"))]
            if following.strip() and len(indent.expandtabs(4)) <= depth:
                break
            owned.append(following)
        blocks.append("".join(owned))
    return blocks


def _ensure_additional_backing(entity, table, relationship):
    for block in _unnamed_blocks(entity, "additionalBackingTable", 4):
        actual_table = _local_name(_direct_setting(block, "table") or "")
        actual_relationship = _local_name(_direct_setting(block, "relationship") or "")
        if actual_table == table:
            if actual_relationship != relationship:
                raise RuntimeError("Existing additional table has a different relationship")
            return entity
        if actual_relationship == relationship:
            raise RuntimeError("Existing additional relationship has a different table")
    return (entity.rstrip() + "\n\n\tadditionalBackingTable\n"
            f"\t\ttable: {_quote_name(table)}\n\t\trelationship: {_quote_name(relationship)}\n")





def _binding_block(block, desired, settings):
    # backingConfiguration has no name; its indentation must be one level below
    # the owning property/relationship, not under a resourceLink sibling.
    match = re.search(r"(?m)^([ \t]*)backingConfiguration\s*$", block)
    if match:
        indent = len(match[1].expandtabs(4))
        lines = block[match.start():].splitlines(keepends=True)
        owned = [lines[0]]
        for line in lines[1:]:
            prefix = line[:len(line) - len(line.lstrip(" \t"))]
            if line.strip() and len(prefix.expandtabs(4)) <= indent:
                break
            owned.append(line)
        existing = "".join(owned)
        for key, expected in settings.items():
            _require_setting(existing, key, expected, reference=key != "type")
        return block
    return block.rstrip() + "\n\n" + desired.rstrip() + "\n"


def _bind_physical_relationship(physical, bindings, default_name, from_column, to_column):
    if len(bindings) > 1:
        raise RuntimeError(f"Ambiguous backing relationship for {default_name}")
    name = default_name
    if bindings:
        reference = _direct_setting(bindings[0], "relationship")
        if not reference:
            raise RuntimeError(f"Missing backing relationship for {default_name}")
        name = _local_name(reference)
    existing = _object_block(physical, "relationship", name, 0)
    expected = {"fromColumn": from_column, "toColumn": to_column}
    if existing:
        for field, value in expected.items():
            _require_setting(existing, field, value, reference=True)
    elif bindings:
        raise RuntimeError(f"Missing physical relationship {name}")
    else:
        physical = physical.rstrip() + f"\n\nrelationship {_quote_name(name)}\n" + "".join(
            f"\t{field}: {value}\n" for field, value in expected.items())
    return physical, name


def _bind_lakehouse_definition(parts, entity_to_table, table_columns, workspace_id,
                              lakehouse_id, lakehouse_name, tag):
    result = _parts_by_path(parts)
    for entity in entity_to_table:
        if f"entities/{entity}.tmdl" not in result:
            raise RuntimeError(f"Missing managed entity {entity!r}; run 004 first")
    expression_name = f"DirectLake - {lakehouse_name}"
    expression_path = "expressions.tmdl"
    expressions = _decode_part(result[expression_path]) if expression_path in result else ""
    expression = _object_block(expressions, "expression", expression_name, 0)
    source_url = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}"
    if expression:
        sources = re.findall(r'AzureStorage\.DataLake\(\s*"([^"]+)"', expression)
        if len(sources) != 1 or sources[0].rstrip("/").casefold() != source_url.casefold():
            raise RuntimeError("Existing OneLake source mismatch; explicitly migrate bindings before retargeting")
    else:
        expression = (
            f"expression {_quote_name(expression_name)} =\n\t\tlet\n"
            f'\t\t    Source = AzureStorage.DataLake("{source_url}", [HierarchicalNavigation=true])\n'
            f"\t\tin\n\t\t    Source\n\tlineageTag: {tag('expression', lakehouse_id)}\n")
        expressions = expressions.rstrip() + "\n\n" + expression
        result[expression_path] = _encode_part(expression_path, expressions.lstrip("\n"))

    model = _decode_part(result["model.tmdl"])
    for name, source_table in entity_to_table.items():
        path = f"entities/{name}.tmdl"
        text = _decode_part(result[path])
        key = _local_name(_direct_setting(text, "keyProperty") or "")
        properties = _property_objects(text)
        actual_columns = table_columns.get(source_table, set())
        if key not in properties or key not in actual_columns:
            raise RuntimeError(f"Missing property/source key {name}.{key}")
        static = {prop: dtype for prop, (dtype, _) in properties.items()
                  if dtype and not dtype.casefold().startswith("timeseries<")}
        missing = set(static) - set(actual_columns)
        if missing:
            raise RuntimeError(f"Source table {source_table} lacks properties: {sorted(missing)}")
        table_path = f"tables/{name}.tmdl"
        if table_path in result:
            table = _decode_part(result[table_path])
            partitions = _object_spans(table, "partition", 4)
            if len(partitions) != 1:
                raise RuntimeError(f"Expected exactly one Direct Lake partition in {table_path}")
            partition = partitions[0][3]
            _require_setting(partition, "mode", "directLake")
            checks = {"entityName": _quote_name(source_table), "expressionSource": _quote_name(expression_name)}
            for field, expected in checks.items():
                matches = re.findall(r"(?m)^[ \t]+" + field + r":\s*(.+?)\s*$", partition)
                if len(matches) != 1 or _identifier_parts(matches[0]) != _identifier_parts(expected):
                    raise RuntimeError(f"Existing {table_path} has a different {field}")
            for annotation, value in (("ONT_WorkspaceId", workspace_id), ("ONT_ItemId", lakehouse_id),
                                      ("ONT_ItemKind", "Lakehouse")):
                matches = re.findall(r"(?m)^[ \t]+annotation " + annotation + r" = (.+?)\s*$", partition)
                if len(matches) != 1 or matches[0].casefold() != value.casefold():
                    raise RuntimeError(f"Existing {table_path} source mismatch: {annotation}")
            for prop, dtype in static.items():
                column = _object_block(table, "column", prop, 4)
                if not column:
                    raise RuntimeError(f"Existing {table_path} lacks column {prop}")
                _require_setting(column, "dataType", dtype)
                _require_setting(column, "sourceColumn", _quote_name(prop), reference=True)
        else:
            columns = "\n".join(
                f"\tcolumn {_quote_name(prop)}\n\t\tdataType: {dtype}\n"
                f"\t\tlineageTag: {tag('property', name, prop)}\n"
                f"\t\tsourceColumn: {_quote_name(prop)}\n" for prop, dtype in static.items())
            table = (
                f"table {_quote_name(name)}\n\tlineageTag: {tag('table', name)}\n\n{columns}\n"
                f"\tpartition {_quote_name(name)} = entity\n\t\tmode: directLake\n"
                f"\t\tsource\n\t\t\tentityName: {_quote_name(source_table)}\n"
                f"\t\t\texpressionSource: {_quote_name(expression_name)}\n\n"
                f"\t\tannotation ONT_WorkspaceId = {workspace_id}\n"
                f"\t\tannotation ONT_ItemId = {lakehouse_id}\n"
                f"\t\tannotation ONT_ItemKind = Lakehouse\n"
                f"\t\tannotation ONT_ItemName = {lakehouse_name}\n")
            result[table_path] = _encode_part(table_path, table)
        backing = _direct_setting(text, "backingTable")
        if backing and _local_name(backing) != name:
            raise RuntimeError(f"Existing {name} has a different backing table")
        if not backing:
            first_line, rest = text.split("\n", 1)
            text = first_line + f"\n\tbackingTable: {_quote_name(name)}\n" + rest
        for prop in static:
            block = _property_objects(text)[prop][1]
            column_ref = f"{_quote_name(name)}.{_quote_name(prop)}"
            desired = f"\t\tbackingConfiguration\n\t\t\tvalueColumn: {column_ref}\n"
            bound = _binding_block(block, desired, {"valueColumn": column_ref})
            if bound != block:
                text = _replace_object(text, "property", prop, bound, 4)
        if text != _decode_part(result[path]):
            result[path] = _encode_part(path, text)
        model = _append_model_ref(model, "table", name)

    rel_path = "entityRelationships.tmdl"
    relationships = _decode_part(result[rel_path]) if rel_path in result else ""
    physical_path = "relationships.tmdl"
    physical = _decode_part(result[physical_path]) if physical_path in result else ""
    managed_relationships = 0
    for rel_name, _, _, block in _object_spans(relationships, "entityRelationship", 0):
        source_ref, target_ref = (_direct_setting(block, field) for field in ("fromEntity", "toEntity"))
        try:
            source, target = _local_name(source_ref or ""), _local_name(target_ref or "")
        except RuntimeError:
            continue
        if source not in entity_to_table or target not in entity_to_table:
            continue
        key = _local_name(_direct_setting(_decode_part(result[f"entities/{target}.tmdl"]), "keyProperty") or "")
        if key not in table_columns[entity_to_table[source]]:
            raise RuntimeError(f"Missing relationship source column {source}.{key}")
        physical, backing_name = _bind_physical_relationship(
            physical, _unnamed_blocks(block, "backingConfiguration", 4), f"{source}_{target}",
            f"{_quote_name(source)}.{_quote_name(key)}", f"{_quote_name(target)}.{_quote_name(key)}")
        bound = _binding_block(
            block, f"\tbackingConfiguration\n\t\trelationship: {_quote_name(backing_name)}\n",
            {"relationship": _quote_name(backing_name)})
        if bound != block:
            relationships = _replace_object(relationships, "entityRelationship", rel_name, bound, 0)
        managed_relationships += 1
    if rel_path in result and relationships != _decode_part(result[rel_path]):
        result[rel_path] = _encode_part(rel_path, relationships)
    if physical.strip() and (physical_path not in result or physical != _decode_part(result[physical_path])):
        result[physical_path] = _encode_part(physical_path, physical.lstrip("\n"))
    if model != _decode_part(result["model.tmdl"]):
        result["model.tmdl"] = _encode_part("model.tmdl", model)
    return list(result.values()), len(entity_to_table), managed_relationships

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 2 — Imports and Fabric API helpers
#  Uses shared 007 config from rti_demo_settings
# ╚══════════════════════════════════════════════════════════════════════════╝

import requests
import time
import json
import base64
import uuid
from typing import Optional
from urllib.parse import urlparse
from IPython.display import display, Markdown
from notebookutils import credentials


def md(text):
    display(Markdown(text))








# ══════════════════════════════════════════════════════════════════════════════
# API config
# ══════════════════════════════════════════════════════════════════════════════

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
FABRIC_API_VERSION = "v1"
FABRIC_BASE_URL = f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300


# ══════════════════════════════════════════════════════════════════════════════
# Validate config from Cell 1
# ══════════════════════════════════════════════════════════════════════════════

required_helper_globals = [
    "WORKSPACE_ID",
    "ONTOLOGY_NAME",
    "target_folder_id",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
]

missing_helper_globals = [
    name
    for name in required_helper_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_helper_globals:
    raise RuntimeError(
        "Missing required 007 helper config values. Run the 007 config cell first. "
        f"Missing: {missing_helper_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Token cache
# ══════════════════════════════════════════════════════════════════════════════

_token_cache = {
    # scope -> {"token": str, "expires_at": float}
}


def get_spn_access_token(scope: str = "https://api.fabric.microsoft.com/.default") -> str:
    """
    Get SPN access token for Fabric/Kusto scopes.

    Uses Key Vault secret names from rti_demo_settings via the 007 config cell.
    """

    now = time.time()

    cached = _token_cache.get(scope)
    if cached and cached["token"] and now < cached["expires_at"]:
        return cached["token"]

    tenant_id = credentials.getSecret(
        key_vault_uri,
        key_vault_tenant_id_secret,
    )

    client_id = credentials.getSecret(
        key_vault_uri,
        key_vault_client_id_secret,
    )

    client_secret = credentials.getSecret(
        key_vault_uri,
        key_vault_client_secret_secret,
    )

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError("Unable to fetch SPN credentials from Key Vault.")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": scope,
    }

    response = requests.post(
        token_url,
        data=data,
        timeout=30,
    )

    response.raise_for_status()

    token_data = response.json()

    _token_cache[scope] = {
        "token": token_data["access_token"],
        "expires_at": now + token_data.get("expires_in", 3600) - 60,
    }

    return _token_cache[scope]["token"]


def get_headers(scope: str = "https://api.fabric.microsoft.com/.default") -> dict:
    return {
        "Authorization": f"Bearer {get_spn_access_token(scope)}",
        "Content-Type": "application/json",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Generic Fabric API helpers
# ══════════════════════════════════════════════════════════════════════════════

def api_request(
    method: str,
    url: str,
    data=None,
    params=None,
    timeout: int = 60,
    scope: str = "https://api.fabric.microsoft.com/.default",
) -> requests.Response:
    """
    Retryable Fabric API request.
    """

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=get_headers(scope),
                json=data,
                params=params,
                timeout=timeout,
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                print(
                    f"Rate limited. Retrying in {retry_after}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(retry_after)
                continue

            if response.status_code >= 500:
                print(
                    f"Server error {response.status_code}. Retrying in "
                    f"{RETRY_DELAY_SECONDS}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            return response

        except requests.exceptions.RequestException as ex:
            print(
                f"API request failed: {ex} "
                f"(attempt {attempt + 1}/{MAX_RETRIES})"
            )

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise

    raise RuntimeError(f"API request failed after {MAX_RETRIES} attempts: {method} {url}")


def wait_for_lro(operation_url: str) -> dict:
    """
    Poll Fabric long-running operation URL until completion.
    """

    start_time = time.time()

    while time.time() - start_time < LRO_MAX_WAIT_SECONDS:
        response = api_request(
            "GET",
            operation_url,
            timeout=60,
        )

        if response.status_code >= 400:
            print(f"❌ LRO poll failed: {response.status_code}")
            print(response.text[:3000])
            raise RuntimeError(f"LRO poll failed: {response.status_code}")

        try:
            result = response.json()
        except Exception:
            print("❌ LRO response was not valid JSON.")
            print(response.text[:3000])
            raise

        status = result.get("status", "Unknown")

        if status in ["Succeeded", "Completed"]:
            print("✅ Operation completed successfully.")
            return result

        if status in ["Failed", "Cancelled"]:
            print("Full LRO payload:")
            print(json.dumps(result, indent=2))
            raise RuntimeError(f"LRO {status}: {result.get('error', {})}")

        print(f"Operation status: {status}")
        time.sleep(LRO_POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"Operation timed out after {LRO_MAX_WAIT_SECONDS} seconds")


# ══════════════════════════════════════════════════════════════════════════════
# Workspace / ontology helpers
# ══════════════════════════════════════════════════════════════════════════════


def list_workspace_items(item_type: str | None = None) -> list:
    items = _list_fabric_values(f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}/items")
    return [item for item in items if not item_type or item.get("type", "").lower() == item_type.lower()]


def list_ontologies() -> list:
    return _list_fabric_values(f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}/ontologies")


def find_ontology_by_name(
    display_name: str,
    folder_id: Optional[str] = None,
    enforce_folder_guard: bool = True,
) -> Optional[dict]:
    """
    Find ontology by display name.

    With folder_id provided, only returns the ontology inside that folder.
    Raises if the same name exists elsewhere and no target-folder match exists.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = target_folder_id

    matches = [
        ontology
        for ontology in list_ontologies()
        if ontology.get("displayName") == display_name
    ]

    if not matches:
        return None

    matches_in_folder = [
        ontology
        for ontology in matches
        if ontology.get("folderId") == resolved_folder_id
    ]

    if matches_in_folder:
        return matches_in_folder[0]

    if enforce_folder_guard:
        first = matches[0]
        raise RuntimeError(
            f"Ontology '{display_name}' exists, but not in the target folder.\n"
            f"Existing ontology ID: {first.get('id')}\n"
            f"Existing folder ID: {first.get('folderId')}\n"
            f"Target folder ID: {resolved_folder_id}\n"
            "Run 004 against the intended folder, delete the wrong ontology, or change the ontology name."
        )

    return None


def get_ontology_definition(ontology_id: str) -> dict:
    return _get_fabric_ontology_definition(FABRIC_BASE_URL, WORKSPACE_ID, ontology_id)


def update_ontology_definition(
    ontology_id: str,
    definition_data: dict,
) -> dict:
    """
    Update Fabric ontology definition.

    Handles:
    - 200 with JSON body
    - 200 with empty body
    - 202 LRO
    """

    url = (
        f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}"
        f"/ontologies/{ontology_id}/updateDefinition"
    )

    response = api_request(
        "POST",
        url,
        data=definition_data,
        timeout=300,
    )

    if response.status_code == 200:
        print("Definition updated successfully.")

        if not response.text or not response.text.strip():
            return {}

        try:
            return response.json()
        except Exception:
            print("Fabric returned HTTP 200, but the response body was not valid JSON.")
            print(response.text[:1000])
            return {}

    if response.status_code == 202:
        operation_url = _fabric_operation_url(response, FABRIC_BASE_URL)

        if not operation_url:
            raise RuntimeError("Missing Location header for updateDefinition LRO.")

        result = wait_for_lro(operation_url)
        print("Definition update async LRO complete.")
        return result or {}

    print(response.text[:3000])
    raise RuntimeError(f"Failed to update ontology definition: {response.status_code}")


# ══════════════════════════════════════════════════════════════════════════════
# Kusto token helper for Eventhouse validation if needed later
# ══════════════════════════════════════════════════════════════════════════════

def get_kusto_token(cluster_url: str) -> str:
    """
    Get token for a Kusto/Eventhouse cluster.
    """

    if not cluster_url:
        raise RuntimeError("cluster_url is empty.")

    parsed = urlparse(cluster_url)
    resource = f"{parsed.scheme}://{parsed.netloc}"
    scope = f"{resource}/.default"

    return get_spn_access_token(scope=scope)


# ══════════════════════════════════════════════════════════════════════════════
# Resolve ontology_id for downstream 006 binding cells
# ══════════════════════════════════════════════════════════════════════════════

ontology = find_ontology_by_name(
    ONTOLOGY_NAME,
    folder_id=target_folder_id,
    enforce_folder_guard=True,
)

if ontology is None:
    raise RuntimeError(
        f"Ontology '{ONTOLOGY_NAME}' was not found in target folder '{target_folder_id}'. "
        "Run 004 first."
    )

ontology_id = ontology["id"]

print("✅ 007 API helpers loaded.")
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Ontology ID:", ontology_id)
print("✅ Workspace ID:", WORKSPACE_ID)
print("✅ Target folder ID:", target_folder_id)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 3 — Resolve ontology, Eventhouse, KQL database, and cluster URI
# ╚══════════════════════════════════════════════════════════════════════════╝

md("## 🔎 Resolving ontology and Eventhouse/KQL source")


# ══════════════════════════════════════════════════════════════════════════════
# Helper: find Fabric item by ID or by name, guarded by target folder
# ══════════════════════════════════════════════════════════════════════════════

def resolve_workspace_item(
    item_type: str,
    display_name: str,
    configured_id: str = "",
    target_folder_id: str | None = None,
) -> dict:
    """
    Resolve a Fabric workspace item by configured ID first, then by display name.

    If resolving by name, do not silently use same-name items outside target_folder_id.
    """

    items = list_workspace_items(item_type)

    if configured_id:
        match_by_id = next(
            (
                item
                for item in items
                if item.get("id") == configured_id
            ),
            None,
        )

        if not match_by_id:
            raise RuntimeError(
                f"{item_type} configured ID was not found in workspace.\n"
                f"Configured ID: {configured_id}\n"
                f"Expected display name: {display_name}"
            )

        item_folder_id = match_by_id.get("folderId")

        if target_folder_id and item_folder_id != target_folder_id:
            raise RuntimeError(
                f"{item_type} '{display_name}' was found by configured ID, "
                "but it is not in the target folder.\n"
                f"Item ID: {configured_id}\n"
                f"Existing folder ID: {item_folder_id}\n"
                f"Target folder ID: {target_folder_id}"
            )

        return match_by_id

    matches = [
        item
        for item in items
        if item.get("displayName") == display_name
    ]

    if not matches:
        raise RuntimeError(
            f"{item_type} '{display_name}' not found in workspace."
        )

    if target_folder_id:
        matches_in_folder = [
            item
            for item in matches
            if item.get("folderId") == target_folder_id
        ]

        if matches_in_folder:
            return matches_in_folder[0]

        first = matches[0]

        raise RuntimeError(
            f"{item_type} '{display_name}' exists, but not in the target folder.\n"
            f"Existing item ID: {first.get('id')}\n"
            f"Existing folder ID: {first.get('folderId')}\n"
            f"Target folder ID: {target_folder_id}\n"
            "Resolve the duplicate/wrong-root item before continuing."
        )

    return matches[0]


# ══════════════════════════════════════════════════════════════════════════════
# Resolve ontology
# ══════════════════════════════════════════════════════════════════════════════

ontology = find_ontology_by_name(
    ONTOLOGY_NAME,
    folder_id=target_folder_id,
    enforce_folder_guard=True,
)

if ontology is None:
    raise RuntimeError(
        f"Ontology '{ONTOLOGY_NAME}' not found in target folder '{target_folder_id}'. "
        "Run 004 first."
    )

ontology_id = ontology["id"]

md(f"✅ Ontology `{ONTOLOGY_NAME}` found in target folder: `{ontology_id}`")


# ══════════════════════════════════════════════════════════════════════════════
# Resolve KQL database
# ══════════════════════════════════════════════════════════════════════════════

configured_kql_database_id = ""

if "KQL_DATABASE_ID" in globals() and KQL_DATABASE_ID:
    configured_kql_database_id = KQL_DATABASE_ID
elif "KQL_DB_ID" in globals() and KQL_DB_ID:
    configured_kql_database_id = KQL_DB_ID

kql_db_item = resolve_workspace_item(
    item_type="KqlDatabase",
    display_name=KQL_DB_NAME,
    configured_id=configured_kql_database_id,
    target_folder_id=target_folder_id,
)

KQL_DB_ID = kql_db_item["id"]
KQL_DATABASE_ID = KQL_DB_ID

md(f"✅ KQL DB `{KQL_DB_NAME}` found in target folder: `{KQL_DB_ID}`")


# ══════════════════════════════════════════════════════════════════════════════
# Resolve Eventhouse
# ══════════════════════════════════════════════════════════════════════════════

parent_eventhouse_id = (
    kql_db_item.get("properties", {}) or {}
).get("parentEventhouseItemId")

configured_eventhouse_id = ""

if "EVENTHOUSE_ID" in globals() and EVENTHOUSE_ID:
    configured_eventhouse_id = EVENTHOUSE_ID

if parent_eventhouse_id:
    EVENTHOUSE_ID = parent_eventhouse_id

    eventhouses = list_workspace_items("Eventhouse")

    eventhouse_item = next(
        (
            item
            for item in eventhouses
            if item.get("id") == EVENTHOUSE_ID
        ),
        None,
    )

    if eventhouse_item is None:
        raise RuntimeError(
            f"KQL DB points to Eventhouse ID '{EVENTHOUSE_ID}', "
            "but that Eventhouse item was not found in the workspace."
        )

    if eventhouse_item.get("folderId") != target_folder_id:
        raise RuntimeError(
            f"Parent Eventhouse for KQL DB '{KQL_DB_NAME}' is not in the target folder.\n"
            f"Eventhouse ID: {EVENTHOUSE_ID}\n"
            f"Existing folder ID: {eventhouse_item.get('folderId')}\n"
            f"Target folder ID: {target_folder_id}"
        )

else:
    eventhouse_item = resolve_workspace_item(
        item_type="Eventhouse",
        display_name=EVENTHOUSE_NAME,
        configured_id=configured_eventhouse_id,
        target_folder_id=target_folder_id,
    )

    EVENTHOUSE_ID = eventhouse_item["id"]

md(f"✅ Eventhouse `{EVENTHOUSE_NAME}` found in target folder: `{EVENTHOUSE_ID}`")


# ══════════════════════════════════════════════════════════════════════════════
# Resolve Eventhouse Kusto URIs
# ══════════════════════════════════════════════════════════════════════════════
# The Eventhouse display name is not a Kusto hostname.
# Always prefer the real runtime URI returned by Fabric Eventhouse properties.
# Persisted settings are allowed as a fallback, but the notebook must never
# manufacture a URI from the Eventhouse or KQL DB display name.

eventhouse_url = (
    f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}"
    f"/eventhouses/{EVENTHOUSE_ID}"
)

eventhouse_resp = api_request("GET", eventhouse_url)
eventhouse_resp.raise_for_status()

eventhouse_details = eventhouse_resp.json()
eventhouse_props = eventhouse_details.get("properties", {}) or {}

resolved_cluster_query_uri = eventhouse_props.get("queryServiceUri")
resolved_cluster_ingest_uri = eventhouse_props.get("ingestionServiceUri")

configured_cluster_query_uri = ""
configured_cluster_ingest_uri = ""

if "cluster_query_uri" in globals() and cluster_query_uri:
    configured_cluster_query_uri = str(cluster_query_uri).strip()
elif "CLUSTER_QUERY_URI" in globals() and CLUSTER_QUERY_URI:
    configured_cluster_query_uri = str(CLUSTER_QUERY_URI).strip()

if "cluster_ingest_uri" in globals() and cluster_ingest_uri:
    configured_cluster_ingest_uri = str(cluster_ingest_uri).strip()
elif "CLUSTER_INGEST_URI" in globals() and CLUSTER_INGEST_URI:
    configured_cluster_ingest_uri = str(CLUSTER_INGEST_URI).strip()

CLUSTER_QUERY_URI = resolved_cluster_query_uri or configured_cluster_query_uri
CLUSTER_INGEST_URI = resolved_cluster_ingest_uri or configured_cluster_ingest_uri

if not CLUSTER_QUERY_URI:
    raise RuntimeError(
        "Could not resolve Eventhouse queryServiceUri. "
        "The Eventhouse item was found, but Fabric did not return a queryServiceUri "
        "and no cluster_query_uri setting was available. Wait for Eventhouse provisioning "
        "to finish and rerun this cell."
    )

if not CLUSTER_INGEST_URI:
    print(
        "⚠️ Eventhouse ingestionServiceUri was not returned and no cluster_ingest_uri "
        "setting was available. Continuing because this notebook only needs the query URI "
        "for schema verification and binding metadata."
    )

# Keep lowercase aliases for compatibility with any later cells.
cluster_query_uri = CLUSTER_QUERY_URI
cluster_ingest_uri = CLUSTER_INGEST_URI

# ══════════════════════════════════════════════════════════════════════════════
# Final confirmation
# ══════════════════════════════════════════════════════════════════════════════

md(f"✅ Eventhouse query URI: `{CLUSTER_QUERY_URI}`")
md(f"✅ RTI table target: `{KQL_TABLE_NAME}`")
md(f"✅ Timestamp column: `{TIMESTAMP_COLUMN_NAME}`")
md(f"✅ Key column: `{KEY_COLUMN_NAME}`")
md(f"✅ Value column: `{VALUE_COLUMN_NAME}`")
md(f"✅ Quality column: `{QUALITY_COLUMN_NAME}`")

print("✅ 007 source resolution complete.")
print("✅ Ontology ID:", ontology_id)
print("✅ Eventhouse ID:", EVENTHOUSE_ID)
print("✅ KQL DB ID:", KQL_DB_ID)
print("✅ Cluster query URI:", CLUSTER_QUERY_URI)
print("✅ KQL table:", KQL_TABLE_NAME)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

_item_response = api_request("GET", f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}/ontologies/{ontology_id}")
if _item_response.status_code != 200:
    raise RuntimeError("Cannot inspect ontology generation")
ontology_details = _item_response.json()
_require_v2_ontology(ontology_details)
live_parts = get_ontology_definition(ontology_id).get("definition", {}).get("parts", [])
ONTOLOGY_GENERATION = _resolve_ontology_generation(
    ontology_details, live_parts)
print("Verified live ontology generation 2")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ╔══════════════════════════════════════════════════════════════════════════╗
#  CELL 4 — Verify Eventhouse slim RTI table schema
# ╚══════════════════════════════════════════════════════════════════════════╝

md("## 📡 Verifying Eventhouse slim RTI table schema")


# ══════════════════════════════════════════════════════════════════════════════
# Validate required globals
# ══════════════════════════════════════════════════════════════════════════════

required_schema_globals = [
    "CLUSTER_QUERY_URI",
    "KQL_DB_NAME",
    "KQL_TABLE_NAME",
    "TIMESTAMP_COLUMN_NAME",
    "KEY_COLUMN_NAME",
    "VALUE_COLUMN_NAME",
    "QUALITY_COLUMN_NAME",
    "get_spn_access_token",
]

missing_schema_globals = [
    name
    for name in required_schema_globals
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_schema_globals:
    raise RuntimeError(
        "Missing required values. Run 007 Cells 1-3 first. "
        f"Missing: {missing_schema_globals}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Kusto helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_kusto_token_for_cluster(cluster_url: str) -> str:
    if not cluster_url:
        raise RuntimeError("CLUSTER_QUERY_URI is empty.")

    parsed = urlparse(cluster_url)

    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(
            "CLUSTER_QUERY_URI must be a full Eventhouse/Kusto URL returned by Fabric. "
            f"Current value: {cluster_url}"
        )

    resource = f"{parsed.scheme}://{parsed.netloc}"
    return get_spn_access_token(scope=f"{resource}/.default")


def run_kusto_mgmt(
    cluster_query_uri: str,
    database_name: str,
    csl: str,
) -> dict:
    token = get_kusto_token_for_cluster(cluster_query_uri)

    mgmt_url = f"{cluster_query_uri.rstrip('/')}/v1/rest/mgmt"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body = {
        "db": database_name,
        "csl": csl,
    }

    response = requests.post(
        mgmt_url,
        headers=headers,
        json=body,
        timeout=120,
    )

    if response.status_code not in (200, 201):
        print("❌ Kusto management command failed.")
        print("Command:")
        print(csl)
        print("Response:")
        print(response.text[:3000])
        response.raise_for_status()

    return response.json() if response.text and response.text.strip() else {}


def kusto_result_to_records(result: dict) -> list[dict]:
    tables = result.get("Tables", []) if isinstance(result, dict) else []

    if not tables:
        return []

    primary = next(
        (
            table
            for table in tables
            if table.get("TableKind") == "PrimaryResult"
        ),
        tables[0],
    )

    columns = [
        column.get("ColumnName") or column.get("name")
        for column in primary.get("Columns", [])
    ]

    rows = primary.get("Rows", [])

    return [
        dict(zip(columns, row))
        for row in rows
    ]


def list_kql_tables(
    cluster_query_uri: str,
    database_name: str,
) -> list[str]:
    result = run_kusto_mgmt(
        cluster_query_uri,
        database_name,
        ".show tables",
    )

    records = kusto_result_to_records(result)

    return sorted([
        record.get("TableName")
        for record in records
        if record.get("TableName")
    ])


def get_kql_table_columns(
    cluster_query_uri: str,
    database_name: str,
    table_name: str,
) -> list[tuple[str, str]]:
    result = run_kusto_mgmt(
        cluster_query_uri,
        database_name,
        f".show table {table_name} schema as json",
    )

    records = kusto_result_to_records(result)

    if not records:
        available_tables = list_kql_tables(
            cluster_query_uri,
            database_name,
        )

        raise RuntimeError(
            f"KQL table '{table_name}' not found in database '{database_name}'. "
            f"Available tables: {available_tables}"
        )

    schema_json = None

    for value in records[0].values():
        if isinstance(value, str) and value.strip().startswith("{"):
            schema_json = value
            break

    if not schema_json:
        raise RuntimeError(
            f"Could not locate schema JSON for KQL table '{table_name}'. "
            f"Returned record: {records[0]}"
        )

    schema_obj = json.loads(schema_json)

    ordered_columns = schema_obj.get("OrderedColumns", [])

    return [
        (
            column.get("Name"),
            column.get("CslType") or column.get("Type"),
        )
        for column in ordered_columns
        if column.get("Name")
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Read and validate schema
# ══════════════════════════════════════════════════════════════════════════════

kql_cols = get_kql_table_columns(
    CLUSTER_QUERY_URI,
    KQL_DB_NAME,
    KQL_TABLE_NAME,
)

kql_col_names = {
    name
    for name, _ in kql_cols
}

expected_kql_cols = {
    TIMESTAMP_COLUMN_NAME,
    KEY_COLUMN_NAME,
    VALUE_COLUMN_NAME,
    QUALITY_COLUMN_NAME,
}

missing_kql_cols = sorted(expected_kql_cols - kql_col_names)
extra_kql_cols = sorted(kql_col_names - expected_kql_cols)

if missing_kql_cols:
    raise RuntimeError(
        f"KQL table '{KQL_TABLE_NAME}' is missing required slim RTI columns: "
        f"{missing_kql_cols}. Available columns: {sorted(kql_col_names)}"
    )

if extra_kql_cols:
    raise RuntimeError(
        f"KQL table '{KQL_TABLE_NAME}' has extra columns that do not belong in "
        f"the slim RTI telemetry model: {extra_kql_cols}\n\n"
        "Expected only:\n"
        f"- {TIMESTAMP_COLUMN_NAME}\n"
        f"- {KEY_COLUMN_NAME}\n"
        f"- {VALUE_COLUMN_NAME}\n"
        f"- {QUALITY_COLUMN_NAME}\n\n"
        "This usually means the table was created earlier with the old wide schema. "
        "For a clean test, delete/recreate the Eventhouse table or use a new table name."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Validate expected column types
# ══════════════════════════════════════════════════════════════════════════════

actual_type_by_col = {
    name: str(kql_type).lower()
    for name, kql_type in kql_cols
}

expected_type_by_col = {
    TIMESTAMP_COLUMN_NAME: "datetime",
    KEY_COLUMN_NAME: "string",
    VALUE_COLUMN_NAME: "real",
    QUALITY_COLUMN_NAME: "string",
}

type_mismatches = []

for column_name, expected_type in expected_type_by_col.items():
    actual_type = actual_type_by_col.get(column_name)

    if actual_type != expected_type:
        type_mismatches.append({
            "column_name": column_name,
            "expected_type": expected_type,
            "actual_type": actual_type,
        })

if type_mismatches:
    raise RuntimeError(
        f"KQL table '{KQL_TABLE_NAME}' has column type mismatches: {type_mismatches}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Display result
# ══════════════════════════════════════════════════════════════════════════════

md(f"✅ KQL table `{KQL_TABLE_NAME}` has the expected slim RTI schema.")

display(
    spark.createDataFrame(
        [
            {
                "column_name": name,
                "kql_type": kql_type,
            }
            for name, kql_type in kql_cols
        ]
    )
)

print("✅ Eventhouse RTI schema verified.")
print("✅ Expected slim columns:", sorted(expected_kql_cols))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# CELL 5 — Read and validate the Generation 2 ontology
import re
from datetime import datetime, timezone


def _tmdl_content(part):
    # Fabric may return TMDL with Windows line endings.
    return (
        base64.b64decode(part["payload"])
        .decode("utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _tmdl_part(path, content):
    return {
        "path": path,
        "payload": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    }


def _tmdl_tag(*values):
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "fabric-ontology-gen2/" + "/".join(map(str, values)),
        )
    )


def _tmdl_setting(block, name):
    return _direct_setting(block, name)


def _property_block(text, name):
    return _object_block(text, 'property', name, 4)



def _replace_property(text, name, replacement):
    return _replace_object(text, "property", name, replacement, 4)


live_parts = (
    get_ontology_definition(ontology_id)
    .get("definition", {})
    .get("parts", [])
)
live_by_path = {
    part["path"]: _tmdl_content(part) for part in live_parts
}

if len(live_by_path) != len(live_parts):
    raise RuntimeError("Duplicate definition part paths")

entity_path = f"entities/{STATIC_ENTITY_NAME}.tmdl"
entity_text = live_by_path.get(entity_path, "")

if (
    not entity_text
    or _local_name(_tmdl_setting(entity_text, "keyProperty") or "") != KEY_COLUMN_NAME
):
    raise RuntimeError(
        f"Generation 2 entity {STATIC_ENTITY_NAME!r} with key "
        f"{KEY_COLUMN_NAME!r} not found. Run 004 and 005 first."
    )

static_table = _local_name(_tmdl_setting(entity_text, "backingTable") or "")
if not static_table or f"tables/{static_table}.tmdl" not in live_by_path:
    raise RuntimeError(
        "Static Lakehouse binding is missing. Run 005 first."
    )

for name in (TIMESTAMP_COLUMN_NAME, VALUE_COLUMN_NAME, QUALITY_COLUMN_NAME):
    block = _property_block(entity_text, name)
    if not block:
        raise RuntimeError(
            f"Property {STATIC_ENTITY_NAME}.{name} is missing "
            "from the Gen2 entity definition"
        )

    actual_type = _tmdl_setting(block, "dataType")
    if not actual_type or not actual_type.casefold().startswith(
        "timeseries<"
    ):
        raise RuntimeError(
            f"{STATIC_ENTITY_NAME}.{name} must be a time-series "
            f"property; Fabric returned dataType={actual_type!r}"
        )

print(
    f"Validated Gen2 entity {STATIC_ENTITY_NAME}, "
    f"static table {static_table}, and time-series properties"
)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# CELL 6 — Bind Eventhouse telemetry to the Generation 2 entity
# Build the same TMDL parts emitted by Fabric's binding UI, preserving other parts.

def _append_unique_block(text, heading, block):
    if re.search(r"(?m)^" + re.escape(heading) + r"$", text):
        return text
    return text.rstrip() + "\n\n" + block.rstrip() + "\n"


def _bind_eventhouse_parts(parts):
    original = {path: _tmdl_content(p) for path, p in _parts_by_path(parts).items()}
    result = dict(original)
    entity = result[entity_path]
    event_table = KQL_TABLE_NAME
    event_path = f"tables/{event_table}.tmdl"
    if event_table == static_table:
        raise RuntimeError("Eventhouse table name conflicts with the static backing table")
    for identifier in (event_table, STATIC_ENTITY_NAME, KEY_COLUMN_NAME, TIMESTAMP_COLUMN_NAME,
                       VALUE_COLUMN_NAME, QUALITY_COLUMN_NAME):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", identifier):
            raise RuntimeError(f"Unsupported TMDL identifier: {identifier!r}")
    if '"' in CLUSTER_QUERY_URI or '"' in KQL_DB_NAME:
        raise RuntimeError("Source URI or KQL database name contains an unsupported quote")

    columns = [(TIMESTAMP_COLUMN_NAME, "dateTime"), (KEY_COLUMN_NAME, "string"),
               (VALUE_COLUMN_NAME, "double"), (QUALITY_COLUMN_NAME, "string")]
    # Preserve compatible UI bindings, but verify their physical column mappings.
    if event_path in result:
        table_text = result[event_path]
        partitions = _object_spans(table_text, "partition", 4)
        if len(partitions) != 1:
            raise RuntimeError(f"Existing {event_path} must have exactly one Eventhouse partition")
        partition = partitions[0][3]
        _require_setting(partition, "mode", "directQuery")
        checks = [f'AzureDataExplorer.Contents("{CLUSTER_QUERY_URI}", "{KQL_DB_NAME}", "{event_table}")',
                  f'annotation ONT_ItemId = {KQL_DB_ID}', f'annotation ONT_WorkspaceId = {WORKSPACE_ID}', 'annotation ONT_ItemKind = KQLDatabase']
        if not all(check in partition for check in checks):
            raise RuntimeError(f"Existing {event_path} points to another Eventhouse source; inspect it before replacing")
        for name, dtype in columns:
            column = _object_block(table_text, "column", name, 4)
            if not column:
                raise RuntimeError(f"Existing {event_path} is missing column {name}")
            _require_setting(column, "dataType", dtype)
            _require_setting(column, "sourceColumn", _quote_name(name), reference=True)
    else:
        column_text = "\n".join(
            f"\tcolumn {name}\n\t\tdataType: {dtype}\n"
            f"\t\tlineageTag: {_tmdl_tag('event-column', KQL_DB_ID, event_table, name)}\n"
            f"\t\tsourceColumn: {name}\n" for name, dtype in columns)
        pinned = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        result[event_path] = (
            f"table {event_table}\n\tlineageTag: {_tmdl_tag('event-table', KQL_DB_ID, event_table)}\n\n"
            + column_text + "\n"
            + f"\tpartition {event_table} = m\n\t\tmode: directQuery\n"
              f"\t\tsource =\n\t\t\t\tlet\n"
              f'\t\t\t\t  Source = AzureDataExplorer.Contents("{CLUSTER_QUERY_URI}", "{KQL_DB_NAME}", "{event_table}")\n'
              f"\t\t\t\tin\n\t\t\t\t  Source\n\n"
              f"\t\tannotation ONT_WorkspaceId = {WORKSPACE_ID}\n"
              f"\t\tannotation ONT_ItemId = {KQL_DB_ID}\n"
              f"\t\tannotation ONT_ItemKind = KQLDatabase\n"
              f"\t\tannotation ONT_ItemName = {KQL_DB_NAME}\n"
              f"\t\tannotation ONT_PinnedAtUtc = {pinned}\n")

    backings = [block for block in _unnamed_blocks(entity, "additionalBackingTable", 4)
                if _local_name(_direct_setting(block, "table") or "") == event_table]
    result["relationships.tmdl"], relationship = _bind_physical_relationship(
        result.get("relationships.tmdl", ""), backings, f"{STATIC_ENTITY_NAME}_{event_table}",
        f"{event_table}.{KEY_COLUMN_NAME}", f"{static_table}.{KEY_COLUMN_NAME}")

    model = result.get("model.tmdl", "")
    if not model.startswith("model "):
        raise RuntimeError("Generation 2 model.tmdl is missing")
    result["model.tmdl"] = _append_model_ref(model, "table", event_table)
    for prop in (TIMESTAMP_COLUMN_NAME, VALUE_COLUMN_NAME, QUALITY_COLUMN_NAME):
        block = _property_block(entity, prop)
        expected = (f"\t\tbackingConfiguration\n\t\t\ttype: timeSeries\n"
                    f"\t\t\tvalueColumn: {event_table}.{prop}\n"
                    f"\t\t\torderingColumn: {event_table}.{TIMESTAMP_COLUMN_NAME}\n")

        if not block:
            raise RuntimeError(f"Missing time-series property {prop}")
        bound = _binding_block(block, expected, {
            "type": "timeSeries", "valueColumn": f"{event_table}.{prop}",
            "orderingColumn": f"{event_table}.{TIMESTAMP_COLUMN_NAME}"})
        if bound != block:
            entity = _replace_property(entity, prop, bound)
    entity = _ensure_additional_backing(entity, event_table, relationship)
    result[entity_path] = entity
    # Mark the join key as such, as Fabric does when the binding is made in the UI.
    static_path = f"tables/{static_table}.tmdl"
    static_text = result[static_path]

    key_block = _object_block(static_text, "column", KEY_COLUMN_NAME, 4)
    if not key_block:
        raise RuntimeError(f"{static_path} has no {KEY_COLUMN_NAME} column")
    if not re.search(r"(?m)^[ \t]+isKey$", key_block):
        first, remainder = key_block.split("\n", 1)
        replacement = first + "\n\t\tisKey\n" + remainder
        result[static_path] = _replace_object(static_text, "column", KEY_COLUMN_NAME, replacement, 4)
    changed = {path for path, content in result.items() if original.get(path) != content}
    return [_tmdl_part(path, result[path]) if path in changed else part for part in parts
            for path in [part["path"]]] + [_tmdl_part(path, result[path]) for path in result if path not in original], changed


updated_parts, changed_paths = _bind_eventhouse_parts(live_parts)
if changed_paths:
    print("Updating Gen2 Eventhouse binding:", sorted(changed_paths))
    update_ontology_definition(ontology_id, {"definition": {"parts": updated_parts}})
else:
    print("Eventhouse binding already matches the configured source")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

verified_parts = get_ontology_definition(ontology_id).get("definition", {}).get("parts", [])
_verify_definition(updated_parts, verified_parts)
print("Verified Eventhouse time-series binding and all preserved ontology content")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
