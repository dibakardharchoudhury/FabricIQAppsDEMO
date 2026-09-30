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

# CELL ********************

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — V2 structured ontology Lakehouse and relationship bindings
# Reads shared values from rti_demo_settings
# Requires live integer properties.generation == 2; existing v1 is rejected,
# never migrated or deleted. RTI_004 must already have created the v2 structure.
# Adds named TMDL data sources, entity dataBinding blocks, and relationship
# contextualizations; no legacy EntityTypes/RelationshipTypes JSON is emitted.
# Reruns merge into live TMDL and preserve Eventhouse bindings and custom parts.
# Conflicts fail before writes; complete service readback verifies output parts.
# Binding verification does not prove native graph association or agent readiness.
# ══════════════════════════════════════════════════════════════════════════════

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

required_settings = [
    "workspace_id",
    "workspace_folder_path",
    "target_folder_id",
    "lakehouse_id",
    "lakehouse_name",
    "ontology_name",
    "eventhouse_name",
    "kql_database_name",
    "eventhouse_table_name",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
    "silver_facilities_table",
    "silver_systems_table",
    "silver_equipment_table",
    "silver_instruments_table",
    "silver_signal_master_table",
]

missing_settings = [
    name
    for name in required_settings
    if name not in settings or settings[name] in (None, "")
]

if missing_settings:
    raise RuntimeError(
        f"Missing required settings in '{settings_table_name}': {missing_settings}"
    )

# --------------------------------------------
# CORE ITEM SETTINGS
# Keep both upper/lower variable names because later 005 cells may use either.
# --------------------------------------------

workspace_id = settings["workspace_id"]
WORKSPACE_ID = workspace_id

workspace_folder_path = settings["workspace_folder_path"]
target_folder_id = settings["target_folder_id"]

lakehouse_name = settings["lakehouse_name"]
lakehouse_id = settings["lakehouse_id"]
LAKEHOUSE_ID = lakehouse_id

ONTOLOGY_NAME = settings["ontology_name"]

# --------------------------------------------
# KEY VAULT / AUTH SETTINGS
# --------------------------------------------

key_vault_uri = settings["key_vault_uri"]
key_vault_tenant_id_secret = settings["key_vault_tenant_id_secret"]
key_vault_client_id_secret = settings["key_vault_client_id_secret"]
key_vault_client_secret_secret = settings["key_vault_client_secret_secret"]

# --------------------------------------------
# LAKEHOUSE DATA BINDING SETTINGS
# --------------------------------------------

SOURCE_SCHEMA = None
TABLE_PREFIX = settings.get("silver_table_prefix", "silver_")

SILVER_FACILITIES_TABLE = settings["silver_facilities_table"]
SILVER_SYSTEMS_TABLE = settings["silver_systems_table"]
SILVER_EQUIPMENT_TABLE = settings["silver_equipment_table"]
SILVER_INSTRUMENTS_TABLE = settings["silver_instruments_table"]
SILVER_SIGNAL_MASTER_TABLE = settings["silver_signal_master_table"]

MANUAL_TABLE_LIST = [
    SILVER_FACILITIES_TABLE,
    SILVER_SYSTEMS_TABLE,
    SILVER_EQUIPMENT_TABLE,
    SILVER_INSTRUMENTS_TABLE,
    SILVER_SIGNAL_MASTER_TABLE,
]

# --------------------------------------------
# RTI / EVENTHOUSE SETTINGS
# --------------------------------------------

fabric_eventhouse_name = settings["eventhouse_name"]
fabric_kql_db_name = settings["kql_database_name"]
fabric_eventhouse_table = settings["eventhouse_table_name"]

# Optional: only present if 002 persisted it after creating/reusing the Eventhouse.
EVENTHOUSE_ID = settings.get("eventhouse_id", "")

# Keep this variable because later cells may expect it.
cluster_query_uri = settings.get(
    "cluster_query_uri",
    f"https://{fabric_kql_db_name}.kusto.fabric.microsoft.com"
)

print("✅ Loaded 005 configuration from shared settings.")
print("✅ Workspace ID:", WORKSPACE_ID)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Lakehouse:", lakehouse_name)
print("✅ Lakehouse ID:", LAKEHOUSE_ID)
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Eventhouse name:", fabric_eventhouse_name)
print("✅ Eventhouse ID:", EVENTHOUSE_ID if EVENTHOUSE_ID else "<not found in settings>")
print("✅ KQL database:", fabric_kql_db_name)
print("✅ Eventhouse table:", fabric_eventhouse_table)
print("✅ Cluster query URI:", cluster_query_uri)
print("✅ Lakehouse binding tables:", MANUAL_TABLE_LIST)

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
                match = re.fullmatch(r"ref (entity|table|namespace) (.+)", line)
                if match:
                    live = _append_model_ref(live, match[1], _local_name(match[2]))
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


def _without_m_comments(text):
    """Ignore M comments without treating quoted URLs or escaped quotes as comments."""
    tokens = re.compile(r'"(?:[^"]|"")*"|//[^\r\n]*|/\*|\*/', re.DOTALL)
    comment_tokens = re.compile(r"/\*|\*/")
    depth = 0
    end = 0
    output = []
    while match := (comment_tokens if depth else tokens).search(text, end):
        if not depth:
            output.append(text[end:match.start()])
        token = match[0]
        if token == "/*":
            if not depth:
                output.append(" ")
            depth += 1
        elif token == "*/":
            if not depth:
                raise RuntimeError("Unexpected M source comment terminator")
            depth -= 1
        elif not depth and token.startswith('"'):
            output.append(token)
        end = match.end()
    if depth:
        raise RuntimeError("Unterminated M source comment")
    output.append(text[end:])
    return "".join(output)


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
        sources = re.findall(r'AzureStorage\.DataLake\(\s*"([^"]+)"', _without_m_comments(expression))
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

# ══════════════════════════════════════════════════════════════════════════════
# Fabric Ontology API helpers for v2 TMDL bindings
# Uses shared 005 config from rti_demo_settings
# ══════════════════════════════════════════════════════════════════════════════

import requests
import time
import json
from typing import Optional
from IPython.display import display, Markdown
from notebookutils import credentials

# --------------------------------------------
# API config
# --------------------------------------------

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
FABRIC_API_VERSION = "v1"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

# --------------------------------------------
# Validate required config
# --------------------------------------------

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
        "Missing required config values. Run the 005 config cell first. "
        f"Missing: {missing_helper_globals}"
    )

# --------------------------------------------
# Token cache
# --------------------------------------------

_token_cache = {
    "token": None,
    "expires_at": 0.0,
}


def get_spn_access_token() -> str:
    """
    Fetch SPN token from Key Vault and cache it until 60 seconds before expiry.
    Secret names come from rti_demo_settings via the 005 config cell.
    """

    now = time.time()

    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

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

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "https://api.fabric.microsoft.com/.default",
    }

    resp = requests.post(
        token_url,
        data=data,
        timeout=30,
    )

    resp.raise_for_status()

    token_data = resp.json()

    _token_cache["token"] = token_data["access_token"]
    _token_cache["expires_at"] = now + token_data.get("expires_in", 3600) - 60

    return _token_cache["token"]


def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_spn_access_token()}",
        "Content-Type": "application/json",
    }


# --------------------------------------------
# Core request helper
# --------------------------------------------

def api_request(
    method: str,
    url: str,
    data=None,
    params=None,
    timeout: int = 60,
) -> requests.Response:
    """
    Retryable Fabric API request.
    Handles 429, 5xx retry, and cached SPN auth.
    """

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=get_headers(),
                json=data,
                params=params,
                timeout=timeout,
            )

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                print(
                    f"Rate limited. Retrying in {retry_after}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(retry_after)
                continue

            if resp.status_code >= 500:
                print(
                    f"Server error {resp.status_code}. Retrying in "
                    f"{RETRY_DELAY_SECONDS}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            return resp

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


# --------------------------------------------
# LRO helper
# --------------------------------------------

def wait_for_lro(operation_url: str) -> dict:
    """
    Poll a Fabric long-running operation until Succeeded/Completed/Failed/Cancelled.
    Uses api_request so retry/auth behavior is consistent.
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
            error = result.get("error", {})
            raise RuntimeError(f"Operation {status}: {error}")

        print(f"Operation status: {status}")
        time.sleep(LRO_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Operation timed out after {LRO_MAX_WAIT_SECONDS} seconds: {operation_url}"
    )


# --------------------------------------------
# Ontology helpers
# --------------------------------------------

def list_ontologies() -> list:
    return _list_fabric_values(f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}/workspaces/{WORKSPACE_ID}/ontologies")


def get_ontology(ontology_id: str) -> Optional[dict]:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{WORKSPACE_ID}/ontologies/{ontology_id}"
    )

    response = api_request("GET", url)

    if response.status_code == 200:
        return response.json()

    print(f"Could not retrieve ontology {ontology_id}: {response.status_code}")
    print(response.text[:3000])

    return None


def find_ontology_by_name(
    display_name: str,
    folder_id: Optional[str] = None,
    enforce_folder_guard: bool = True,
) -> Optional[dict]:
    """
    Find ontology by display name.

    If folder_id is provided, only return a match inside that folder.
    If a same-name ontology exists outside the target folder, raise a clear error.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = target_folder_id

    matches = [
        ont
        for ont in list_ontologies()
        if ont.get("displayName") == display_name
    ]

    if not matches:
        return None

    matches_in_folder = [
        ont
        for ont in matches
        if ont.get("folderId") == resolved_folder_id
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
            "For a clean test, delete the existing ontology or change the ontology name."
        )

    return None








def get_ontology_definition(ontology_id: str) -> dict:
    return _get_fabric_ontology_definition(
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}", WORKSPACE_ID, ontology_id)


def update_ontology_definition(
    ontology_id: str,
    definition_data: dict,
) -> dict:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{WORKSPACE_ID}"
        f"/ontologies/{ontology_id}/updateDefinition"
    )

    response = api_request(
        "POST",
        url,
        data=definition_data,
        timeout=300,
    )

    if response.status_code == 200:
        print("Definition updated successfully")

        if not response.text or not response.text.strip():
            return {}

        try:
            return response.json()
        except Exception:
            print("Fabric returned HTTP 200, but the response body was not valid JSON.")
            print(response.text[:1000])
            return {}

    if response.status_code == 202:
        operation_url = _fabric_operation_url(response, f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}")

        if not operation_url:
            raise RuntimeError("Location header missing for updateDefinition LRO.")

        lro_result = wait_for_lro(operation_url)
        print("Definition update async LRO complete")
        return lro_result or {}

    print(f"Failed to update definition: {response.status_code}")
    print(response.text[:3000])

    raise RuntimeError(f"Update definition failed: {response.status_code}")


# --------------------------------------------
# Resolve ontology_id for the rest of 005
# --------------------------------------------

ontology = find_ontology_by_name(
    ONTOLOGY_NAME,
    folder_id=target_folder_id,
    enforce_folder_guard=True,
)

if ontology is None:
    raise RuntimeError(
        f"Ontology with display name '{ONTOLOGY_NAME}' was not found in target folder "
        f"'{target_folder_id}'. Run 004 first."
    )

ontology_id = ontology["id"]

print("✅ Ontology API helpers loaded.")
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

ontology_details = get_ontology(ontology_id)
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

"""Generation 2 Direct Lake binding. Unmanaged parts remain unchanged."""
import uuid


def _tag(*values):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "fabric-ontology-gen2/" + "/".join(map(str, values))))


def entity_name_from_table(table_name):
    name = re.sub(r"^silver_", "", table_name)
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not re.match(r"^[A-Za-z]", name):
        name = "E_" + name
    return name[:26]


def _entity_properties(text):
    return _property_objects(text)


def _bind_gen2_parts(parts, table_columns):
    return _bind_lakehouse_definition(
        parts, ENTITY_TO_TABLE, table_columns, WORKSPACE_ID, LAKEHOUSE_ID, lakehouse_name, _tag)


ENTITY_TO_TABLE = {entity_name_from_table(name): name for name in MANUAL_TABLE_LIST}
if len(ENTITY_TO_TABLE) != len(MANUAL_TABLE_LIST):
    raise RuntimeError("Two configured tables resolve to the same entity name")
table_columns = {name: set(spark.read.table(name).columns) for name in MANUAL_TABLE_LIST}
updated_parts, entity_count, relationship_count = _bind_gen2_parts(live_parts, table_columns)
update_ontology_definition(ontology_id, {"definition": {"parts": updated_parts}})
readback = get_ontology_definition(ontology_id).get("definition", {}).get("parts", [])
_verify_definition(updated_parts, readback)
print(f"Verified {entity_count} static bindings and {relationship_count} relationship bindings")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
