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

# # 04 – Build Ontology v2 TMDL for Structured Data and RTI
#
# This notebook builds the configured `ontology_name` as a generation 2 item:
#
# - static/structured entities are sourced from Lakehouse tables
# - RTI telemetry stays in Eventhouse
# - `signal_master` is the semantic bridge between structured metadata and RTI
# - `event_time`, `value`, and `quality` are defined as time-series properties on `signal_master`
#
# Fresh creation explicitly supplies `database.tmdl`, `model.tmdl`, and
# `namespaces/default.tmdl`; entities and relationships are emitted as TMDL.
# The live resource must report integer `properties.generation == 2`. Existing v1
# items are rejected, not migrated or deleted; use a separate v2 target.
# REST `/v1` and deployment suffixes such as `_V9` are not ontology generation.
#
# Reruns retain live TMDL identities, bindings, and custom parts. Conflicting
# structural/source changes fail before update. Output parts are persisted only
# after service readback verifies the complete definition.
# RTI_005 adds Lakehouse bindings; RTI_006 adds Eventhouse time-series bindings.
# This notebook creates neither a Lakehouse telemetry copy nor native graph
# association. Verified TMDL does not establish Data Agent or Operations Agent readiness.

# CELL ********************

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG – Ontology for structured data + direct Eventhouse RTI binding
# Reads shared settings from rti_demo_settings
# ══════════════════════════════════════════════════════════════════════════════

from pyspark.sql import functions as F

USE_MANUAL_TABLE_LIST = True

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
    "silver_facilities_table",
    "silver_systems_table",
    "silver_equipment_table",
    "silver_instruments_table",
    "silver_signal_master_table",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
]

missing_settings = [name for name in required_settings if name not in settings]
if missing_settings:
    raise RuntimeError(
        f"Missing required settings in '{settings_table_name}': {missing_settings}"
    )

# --------------------------------------------
# CORE WORKSPACE / FOLDER / LAKEHOUSE SETTINGS
# --------------------------------------------

workspace_id = settings["workspace_id"]
workspace_folder_path = settings["workspace_folder_path"]
target_folder_id = settings["target_folder_id"]

lakehouse_name = settings["lakehouse_name"]
lakehouse_id = settings["lakehouse_id"]

# --------------------------------------------
# AUTHENTICATION SETTINGS
# --------------------------------------------

key_vault_uri = settings["key_vault_uri"]
key_vault_tenant_id_secret = settings["key_vault_tenant_id_secret"]
key_vault_client_id_secret = settings["key_vault_client_id_secret"]
key_vault_client_secret_secret = settings["key_vault_client_secret_secret"]

# --------------------------------------------
# EVENTHOUSE / RTI SOURCE SETTINGS
# Used for direct Eventhouse RTI time-series binding
# --------------------------------------------

fabric_eventhouse_name = settings["eventhouse_name"]
fabric_kql_db_name = settings["kql_database_name"]
fabric_eventhouse_table = settings["eventhouse_table_name"]

# --------------------------------------------
# STRUCTURED SOURCE TABLES
# Produced by setup / medallion notebooks
# --------------------------------------------

SILVER_FACILITIES_TABLE = settings["silver_facilities_table"]
SILVER_SYSTEMS_TABLE = settings["silver_systems_table"]
SILVER_EQUIPMENT_TABLE = settings["silver_equipment_table"]
SILVER_INSTRUMENTS_TABLE = settings["silver_instruments_table"]
SILVER_SIGNAL_MASTER_TABLE = settings["silver_signal_master_table"]

# --------------------------------------------
# ONTOLOGY DEPLOYMENT NAME
# --------------------------------------------

ONTOLOGY_NAME = settings["ontology_name"]

# --------------------------------------------
# STATIC ENTITY TABLES FOR ONTOLOGY
# Keep RTI stream rows out of Lakehouse ontology entities.
# Eventhouse binding happens directly through signal_master/opcua_node_id.
# --------------------------------------------

MANUAL_TABLE_LIST = [
    SILVER_FACILITIES_TABLE,
    SILVER_SYSTEMS_TABLE,
    SILVER_EQUIPMENT_TABLE,
    SILVER_INSTRUMENTS_TABLE,
    SILVER_SIGNAL_MASTER_TABLE,
]

print("✅ Loaded 004 configuration from shared settings.")
print("✅ Workspace ID:", workspace_id)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Lakehouse:", lakehouse_name)
print("✅ Lakehouse ID:", lakehouse_id)
print("✅ Ontology name:", ONTOLOGY_NAME)
print("✅ Eventhouse:", fabric_eventhouse_name)
print("✅ KQL database:", fabric_kql_db_name)
print("✅ Eventhouse table:", fabric_eventhouse_table)
print("✅ Manual ontology source tables:", MANUAL_TABLE_LIST)

# Edit these together when using a different set of entities.
# Entity names come from source table names with the leading silver_ removed.
OWN_PK_OVERRIDES = {
    "facilities": "facility_id", "systems": "system_id",
    "equipment": "equipment_id", "instruments": "instrument_id",
    "signal_master": "opcua_node_id",
}
ENTITY_PARENT_MAP = {
    "systems": "facilities", "equipment": "systems",
    "instruments": "equipment", "signal_master": "instruments",
}
REL_NAME_OVERRIDES = {
    ("systems", "facilities"): "systems_in_facilities",
    ("equipment", "systems"): "equipment_in_systems",
    ("instruments", "equipment"): "instruments_on_equipment",
    ("signal_master", "instruments"): "signals_from_instruments",
}
SIGNAL_MASTER_ENTITY = settings.get("signal_master_entity_name", "signal_master")
RTI_TIMESERIES_PROPERTIES = [
    {"name": "event_time", "dataType": "DateTime"},
    {"name": "value", "dataType": "Double"},
    {"name": "quality", "dataType": "String"},
]

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# ══════════════════════════════════════════════════════════════════════════════
# PREP – Validate static ontology source tables from structured metadata
# ══════════════════════════════════════════════════════════════════════════════

from pyspark.sql import functions as F

def table_exists(table_name: str) -> bool:
    try:
        return spark.catalog.tableExists(table_name)
    except Exception:
        return False

required_tables = [
    SILVER_FACILITIES_TABLE,
    SILVER_SYSTEMS_TABLE,
    SILVER_EQUIPMENT_TABLE,
    SILVER_INSTRUMENTS_TABLE,
    SILVER_SIGNAL_MASTER_TABLE,
]

missing_tables = [
    table_name
    for table_name in required_tables
    if not table_exists(table_name)
]

if missing_tables:
    raise RuntimeError(
        "Required ontology source tables are missing. "
        f"Run Notebook 003 first. Missing tables: {missing_tables}"
    )

required_columns_by_table = {
    SILVER_FACILITIES_TABLE: {
        "facility_id",
    },
    SILVER_SYSTEMS_TABLE: {
        "system_id",
        "facility_id",
    },
    SILVER_EQUIPMENT_TABLE: {
        "equipment_id",
        "system_id",
        "facility_id",
    },
    SILVER_INSTRUMENTS_TABLE: {
        "opcua_node_id",
        "tag",
        "instrument_id",
        "equipment_id",
        "system_id",
        "facility_id",
        "unit",
        "is_active",
    },
    SILVER_SIGNAL_MASTER_TABLE: {
        "opcua_node_id",
        "tag",
        "instrument_id",
        "equipment_id",
        "system_id",
        "facility_id",
        "unit",
        "is_active",
        "signal_type",
    },
}

validation_rows = []

for table_name in required_tables:
    df = spark.read.table(table_name)
    actual_columns = set(df.columns)
    missing_columns = sorted(required_columns_by_table[table_name] - actual_columns)

    validation_rows.append({
        "table_name": table_name,
        "row_count": df.count(),
        "missing_columns": ", ".join(missing_columns),
        "status": "OK" if not missing_columns else "MISSING_COLUMNS",
    })

validation_df = spark.createDataFrame(validation_rows)

display(validation_df.orderBy("table_name"))

bad_rows = validation_df.where(F.col("status") != "OK").count()
if bad_rows > 0:
    raise RuntimeError(
        "One or more ontology source tables are missing required columns. "
        "See validation output above."
    )

print("✅ Ontology source tables validated.")
print("✅ No static ontology source tables were overwritten.")

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
import uuid
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


def _select_attached_graph_model(ontology_id, workspace_id, lineage, workspace_items):
    related_items = lineage.get("items") if isinstance(lineage, dict) else None
    relations = lineage.get("relations") if isinstance(lineage, dict) else None
    if not isinstance(related_items, list) or not isinstance(relations, list):
        raise RuntimeError("Ontology downstream lineage response is malformed")
    related_by_id = {
        str(item["id"]): item for item in related_items
        if isinstance(item, dict) and item.get("id")
    }
    workspace_by_id = {
        str(item["id"]): item for item in workspace_items
        if isinstance(item, dict) and item.get("id")
    }
    graph_ids = {
        str(relation["itemId"])
        for relation in relations
        if isinstance(relation, dict)
        and str(relation.get("dependentOnItemId", "")).casefold() == ontology_id.casefold()
        and relation.get("relationType") == "CascadeDelete"
        and related_by_id.get(str(relation.get("itemId")), {}).get("type") == "GraphIndex"
        and str(related_by_id[str(relation["itemId"])].get("workspaceId", "")).casefold()
        == workspace_id.casefold()
        and workspace_by_id.get(str(relation.get("itemId")), {}).get("type") == "GraphModel"
    }
    if len(graph_ids) > 1:
        raise RuntimeError("Ontology lineage identifies multiple attached GraphModels")
    if not graph_ids:
        raise RuntimeError(
            "The generation-2 Ontology has no attached GraphModel. Use Manage graph once to "
            "establish the service-owned Ontology-to-GraphIndex lineage, then rerun RTI_006. "
            "Graph ownership is never inferred from names or a sole workspace graph."
        )
    return next(iter(graph_ids))


def _graph_json_parts(parts):
    required = {"graphType.json", "dataSources.json", "graphDefinition.json"}
    by_path = _parts_by_path(parts)
    missing = required - set(by_path)
    if missing:
        raise RuntimeError(f"GraphModel definition is missing required parts: {sorted(missing)}")
    decoded = {}
    for path, part in by_path.items():
        if not (path.endswith(".json") or path == ".platform"):
            continue
        try:
            value = json.loads(_decode_part(part))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"GraphModel definition part {path} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"GraphModel definition part {path} must contain a JSON object")
        decoded[path] = value
    return by_path, decoded


def _unique_graph_entry(values, predicate, description):
    matches = [value for value in values if isinstance(value, dict) and predicate(value)]
    if len(matches) > 1:
        raise RuntimeError(f"GraphModel contains multiple {description} entries")
    return matches[0] if matches else None


def _graph_uuid(graph_model_id, role):
    try:
        namespace = uuid.UUID(graph_model_id)
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("GraphModel identity must be a GUID") from exc
    return str(uuid.uuid5(namespace, role))


def _ensure_static_graph_projection(
    parts,
    graph_model_id,
    source_name,
    source_path,
    entity_name,
    key_name,
    property_types,
    relationship_name,
    target_entity_name,
    target_key_name,
    excluded_properties,
):
    by_path, decoded = _graph_json_parts(parts)
    graph_type = decoded["graphType.json"]
    data_sources = decoded["dataSources.json"]
    graph_definition = decoded["graphDefinition.json"]
    node_types = graph_type.get("nodeTypes")
    edge_types = graph_type.get("edgeTypes")
    sources = data_sources.get("dataSources")
    node_tables = graph_definition.get("nodeTables")
    edge_tables = graph_definition.get("edgeTables")
    for name, value in (
        ("graphType.nodeTypes", node_types), ("graphType.edgeTypes", edge_types),
        ("dataSources.dataSources", sources), ("graphDefinition.nodeTables", node_tables),
        ("graphDefinition.edgeTables", edge_tables),
    ):
        if not isinstance(value, list):
            raise RuntimeError(f"GraphModel {name} must be an array")
    if not isinstance(property_types, dict) or key_name not in property_types:
        raise RuntimeError("Static graph property types must include the entity key")
    if set(property_types) & set(excluded_properties):
        raise RuntimeError("Time-series exclusions cannot also be static graph properties")

    changed = False
    source = _unique_graph_entry(sources, lambda value: value.get("name") == source_name,
                                 f"data source named {source_name!r}")
    expected_source = {"path": source_path}
    if source:
        if source.get("type") != "DeltaTable" or source.get("properties") != expected_source:
            raise RuntimeError(f"Existing graph data source {source_name!r} points elsewhere")
    else:
        sources.append({"name": source_name, "type": "DeltaTable", "properties": expected_source})
        changed = True

    node = _unique_graph_entry(
        node_types,
        lambda value: entity_name in value.get("labels", [])
        if isinstance(value.get("labels"), list) else False,
        f"node type labelled {entity_name!r}",
    )
    if node:
        alias = node.get("alias")
        if not isinstance(alias, str) or not alias:
            raise RuntimeError(f"Graph node type {entity_name!r} has no alias")
        if node.get("primaryKeyProperties") != [key_name]:
            raise RuntimeError(f"Graph node type {entity_name!r} has a different primary key")
    else:
        alias = _graph_uuid(graph_model_id, f"node-type:{entity_name}")
        node = {
            "primaryKeyProperties": [key_name],
            "alias": alias,
            "labels": [entity_name],
            "properties": [],
        }
        node_types.append(node)
        changed = True
    properties = node.get("properties")
    if not isinstance(properties, list):
        raise RuntimeError(f"Graph node type {entity_name!r} properties must be an array")
    names = [value.get("name") for value in properties if isinstance(value, dict)]
    if len(names) != len(set(names)):
        raise RuntimeError(f"Graph node type {entity_name!r} contains duplicate properties")
    retained = [
        value for value in properties
        if not isinstance(value, dict) or value.get("name") not in excluded_properties
    ]
    if len(retained) != len(properties):
        node["properties"] = properties = retained
        changed = True
    existing_properties = {
        value.get("name"): value for value in properties if isinstance(value, dict)
    }
    for name, data_type in property_types.items():
        current = existing_properties.get(name)
        if current:
            if current.get("type") != data_type:
                raise RuntimeError(
                    f"Graph property {entity_name}.{name} has type {current.get('type')!r}, "
                    f"expected {data_type!r}"
                )
        else:
            properties.append({"name": name, "type": data_type})
            changed = True

    node_table = _unique_graph_entry(
        node_tables, lambda value: value.get("nodeTypeAlias") == alias,
        f"node table for {entity_name!r}",
    )
    if node_table:
        if node_table.get("dataSourceName") != source_name:
            raise RuntimeError(f"Graph node table {entity_name!r} uses another data source")
    else:
        node_table = {
            "nodeTypeAlias": alias,
            "id": _graph_uuid(graph_model_id, f"node-table:{entity_name}"),
            "dataSourceName": source_name,
            "propertyMappings": [],
        }
        node_tables.append(node_table)
        changed = True
    mappings = node_table.get("propertyMappings")
    if not isinstance(mappings, list):
        raise RuntimeError(f"Graph node table {entity_name!r} propertyMappings must be an array")
    mapping_names = [
        value.get("propertyName") for value in mappings if isinstance(value, dict)
    ]
    if len(mapping_names) != len(set(mapping_names)):
        raise RuntimeError(f"Graph node table {entity_name!r} contains duplicate mappings")
    retained = [
        value for value in mappings
        if not isinstance(value, dict) or value.get("propertyName") not in excluded_properties
    ]
    if len(retained) != len(mappings):
        node_table["propertyMappings"] = mappings = retained
        changed = True
    existing_mappings = {
        value.get("propertyName"): value for value in mappings if isinstance(value, dict)
    }
    for name in property_types:
        current = existing_mappings.get(name)
        expected = {"propertyName": name, "sourceColumn": name}
        if current:
            if current != expected:
                raise RuntimeError(f"Graph property mapping {entity_name}.{name} points elsewhere")
        else:
            mappings.append(expected)
            changed = True

    target = _unique_graph_entry(
        node_types,
        lambda value: target_entity_name in value.get("labels", [])
        if isinstance(value.get("labels"), list) else False,
        f"target node type labelled {target_entity_name!r}",
    )
    if not target or not isinstance(target.get("alias"), str):
        raise RuntimeError(
            f"GraphModel must already project target entity {target_entity_name!r}"
        )
    target_alias = target["alias"]
    if target.get("primaryKeyProperties") != [target_key_name]:
        raise RuntimeError(f"Graph target {target_entity_name!r} has a different primary key")
    edge = _unique_graph_entry(
        edge_types,
        lambda value: relationship_name in value.get("labels", [])
        if isinstance(value.get("labels"), list) else False,
        f"edge type labelled {relationship_name!r}",
    )
    if edge:
        edge_alias = edge.get("alias")
        if not isinstance(edge_alias, str) or not edge_alias:
            raise RuntimeError(f"Graph edge type {relationship_name!r} has no alias")
        if (edge.get("sourceNodeType") != {"alias": alias}
                or edge.get("destinationNodeType") != {"alias": target_alias}):
            raise RuntimeError(f"Graph edge {relationship_name!r} has different endpoints")
    else:
        edge_alias = _graph_uuid(graph_model_id, f"edge-type:{relationship_name}")
        edge = {
            "additionalKeyProperties": [],
            "alias": edge_alias,
            "sourceNodeType": {"alias": alias},
            "labels": [relationship_name],
            "destinationNodeType": {"alias": target_alias},
            "properties": [],
        }
        edge_types.append(edge)
        changed = True
    edge_table = _unique_graph_entry(
        edge_tables, lambda value: value.get("edgeTypeAlias") == edge_alias,
        f"edge table for {relationship_name!r}",
    )
    expected_edge = {
        "edgeTypeAlias": edge_alias,
        "id": _graph_uuid(graph_model_id, f"edge-table:{relationship_name}"),
        "edgeIdMapping": [],
        "dataSourceName": source_name,
        "sourceNodeKeyColumns": [key_name],
        "propertyMappings": [],
        "destinationNodeKeyColumns": [target_key_name],
    }
    if edge_table:
        for field in (
            "edgeIdMapping", "dataSourceName", "sourceNodeKeyColumns",
            "propertyMappings", "destinationNodeKeyColumns",
        ):
            if edge_table.get(field) != expected_edge[field]:
                raise RuntimeError(f"Graph edge table {relationship_name!r} has incompatible {field}")
    else:
        edge_tables.append(expected_edge)
        changed = True

    replacements = {}
    if changed:
        for path in ("graphType.json", "dataSources.json", "graphDefinition.json"):
            content = json.dumps(decoded[path], ensure_ascii=False, separators=(",", ":"))
            replacements[path] = _encode_part(path, content)
    updated = [
        replacements.get(part["path"], part)
        for part in parts
    ]
    return updated, changed, {
        "nodeAlias": alias,
        "edgeAlias": edge_alias,
        "staticPropertyCount": len(property_types),
    }


def _ensure_complete_static_graph_projection(
    parts,
    graph_model_id,
    entity_projections,
    relationship_projections,
):
    by_path, decoded = _graph_json_parts(parts)
    graph_type = decoded["graphType.json"]
    data_sources = decoded["dataSources.json"]
    graph_definition = decoded["graphDefinition.json"]
    collections = (
        (graph_type, "nodeTypes", "graphType.nodeTypes"),
        (graph_type, "edgeTypes", "graphType.edgeTypes"),
        (data_sources, "dataSources", "dataSources.dataSources"),
        (graph_definition, "nodeTables", "graphDefinition.nodeTables"),
        (graph_definition, "edgeTables", "graphDefinition.edgeTables"),
    )
    changed = False
    for owner, field, description in collections:
        value = owner.get(field)
        if value is None:
            owner[field] = []
            changed = True
        elif not isinstance(value, list):
            raise RuntimeError(f"GraphModel {description} must be an array or null")

    node_types = graph_type["nodeTypes"]
    edge_types = graph_type["edgeTypes"]
    sources = data_sources["dataSources"]
    node_tables = graph_definition["nodeTables"]
    edge_tables = graph_definition["edgeTables"]
    if not isinstance(entity_projections, list) or not entity_projections:
        raise RuntimeError("Complete graph projection requires at least one entity")
    if not isinstance(relationship_projections, list):
        raise RuntimeError("Complete graph relationships must be an array")

    aliases = {}
    keys = {}
    for spec in entity_projections:
        if not isinstance(spec, dict):
            raise RuntimeError("Graph entity projection must be an object")
        name = spec.get("name")
        source_name = spec.get("sourceName")
        source_path = spec.get("sourcePath")
        key_name = spec.get("keyName")
        property_types = spec.get("propertyTypes")
        excluded = set(spec.get("excludedProperties") or [])
        if not all(isinstance(value, str) and value for value in (
            name, source_name, source_path, key_name,
        )):
            raise RuntimeError("Graph entity projection has incomplete identity")
        if not isinstance(property_types, dict) or key_name not in property_types:
            raise RuntimeError(f"Graph entity {name!r} must include its key property")
        if set(property_types) & excluded:
            raise RuntimeError(
                f"Graph entity {name!r} time-series exclusions cannot be static properties"
            )

        expected_source = {"path": source_path}
        source = _unique_graph_entry(
            sources, lambda value, expected=source_name: value.get("name") == expected,
            f"data source named {source_name!r}",
        )
        if source:
            if source.get("type") != "DeltaTable" or source.get("properties") != expected_source:
                raise RuntimeError(f"Existing graph data source {source_name!r} points elsewhere")
        else:
            sources.append({
                "name": source_name, "type": "DeltaTable", "properties": expected_source,
            })
            changed = True

        node = _unique_graph_entry(
            node_types,
            lambda value, expected=name: expected in value.get("labels", [])
            if isinstance(value.get("labels"), list) else False,
            f"node type labelled {name!r}",
        )
        if node:
            alias = node.get("alias")
            if not isinstance(alias, str) or not alias:
                raise RuntimeError(f"Graph node type {name!r} has no alias")
            if node.get("primaryKeyProperties") != [key_name]:
                raise RuntimeError(f"Graph node type {name!r} has a different primary key")
        else:
            alias = _graph_uuid(graph_model_id, f"node-type:{name}")
            node = {
                "primaryKeyProperties": [key_name],
                "alias": alias,
                "labels": [name],
                "properties": [],
            }
            node_types.append(node)
            changed = True
        aliases[name] = alias
        keys[name] = key_name

        properties = node.get("properties")
        if not isinstance(properties, list):
            raise RuntimeError(f"Graph node type {name!r} properties must be an array")
        property_names = [
            value.get("name") for value in properties if isinstance(value, dict)
        ]
        if len(property_names) != len(set(property_names)):
            raise RuntimeError(f"Graph node type {name!r} contains duplicate properties")
        retained = [
            value for value in properties
            if not isinstance(value, dict) or value.get("name") not in excluded
        ]
        if len(retained) != len(properties):
            node["properties"] = properties = retained
            changed = True
        existing_properties = {
            value.get("name"): value for value in properties if isinstance(value, dict)
        }
        for property_name, data_type in property_types.items():
            current = existing_properties.get(property_name)
            if current:
                if current.get("type") != data_type:
                    raise RuntimeError(
                        f"Graph property {name}.{property_name} has type "
                        f"{current.get('type')!r}, expected {data_type!r}"
                    )
            else:
                properties.append({"name": property_name, "type": data_type})
                changed = True

        node_table = _unique_graph_entry(
            node_tables, lambda value, expected=alias: value.get("nodeTypeAlias") == expected,
            f"node table for {name!r}",
        )
        if node_table:
            if node_table.get("dataSourceName") != source_name:
                raise RuntimeError(f"Graph node table {name!r} uses another data source")
        else:
            node_table = {
                "nodeTypeAlias": alias,
                "id": _graph_uuid(graph_model_id, f"node-table:{name}"),
                "dataSourceName": source_name,
                "propertyMappings": [],
            }
            node_tables.append(node_table)
            changed = True
        mappings = node_table.get("propertyMappings")
        if not isinstance(mappings, list):
            raise RuntimeError(f"Graph node table {name!r} propertyMappings must be an array")
        mapping_names = [
            value.get("propertyName") for value in mappings if isinstance(value, dict)
        ]
        if len(mapping_names) != len(set(mapping_names)):
            raise RuntimeError(f"Graph node table {name!r} contains duplicate mappings")
        retained = [
            value for value in mappings
            if not isinstance(value, dict) or value.get("propertyName") not in excluded
        ]
        if len(retained) != len(mappings):
            node_table["propertyMappings"] = mappings = retained
            changed = True
        existing_mappings = {
            value.get("propertyName"): value for value in mappings if isinstance(value, dict)
        }
        for property_name in property_types:
            expected = {"propertyName": property_name, "sourceColumn": property_name}
            current = existing_mappings.get(property_name)
            if current:
                if current != expected:
                    raise RuntimeError(
                        f"Graph property mapping {name}.{property_name} points elsewhere"
                    )
            else:
                mappings.append(expected)
                changed = True

    for spec in relationship_projections:
        if not isinstance(spec, dict):
            raise RuntimeError("Graph relationship projection must be an object")
        name = spec.get("name")
        source_name = spec.get("source")
        target_name = spec.get("target")
        if not all(isinstance(value, str) and value for value in (
            name, source_name, target_name,
        )):
            raise RuntimeError("Graph relationship projection has incomplete identity")
        if source_name not in aliases or target_name not in aliases:
            raise RuntimeError(f"Graph relationship {name!r} has an unknown endpoint")
        source_alias, target_alias = aliases[source_name], aliases[target_name]
        edge = _unique_graph_entry(
            edge_types,
            lambda value, expected=name: expected in value.get("labels", [])
            if isinstance(value.get("labels"), list) else False,
            f"edge type labelled {name!r}",
        )
        if edge:
            edge_alias = edge.get("alias")
            if not isinstance(edge_alias, str) or not edge_alias:
                raise RuntimeError(f"Graph edge type {name!r} has no alias")
            if (edge.get("sourceNodeType") != {"alias": source_alias}
                    or edge.get("destinationNodeType") != {"alias": target_alias}):
                raise RuntimeError(f"Graph edge {name!r} has different endpoints")
        else:
            edge_alias = _graph_uuid(graph_model_id, f"edge-type:{name}")
            edge_types.append({
                "additionalKeyProperties": [],
                "alias": edge_alias,
                "sourceNodeType": {"alias": source_alias},
                "labels": [name],
                "destinationNodeType": {"alias": target_alias},
                "properties": [],
            })
            changed = True
        expected_edge = {
            "edgeTypeAlias": edge_alias,
            "id": _graph_uuid(graph_model_id, f"edge-table:{name}"),
            "edgeIdMapping": [],
            "dataSourceName": source_name,
            "sourceNodeKeyColumns": [keys[source_name]],
            "propertyMappings": [],
            "destinationNodeKeyColumns": [keys[target_name]],
        }
        edge_table = _unique_graph_entry(
            edge_tables,
            lambda value, expected=edge_alias: value.get("edgeTypeAlias") == expected,
            f"edge table for {name!r}",
        )
        if edge_table:
            for field in (
                "edgeIdMapping", "dataSourceName", "sourceNodeKeyColumns",
                "propertyMappings", "destinationNodeKeyColumns",
            ):
                if edge_table.get(field) != expected_edge[field]:
                    raise RuntimeError(
                        f"Graph edge table {name!r} has incompatible {field}"
                    )
        else:
            edge_tables.append(expected_edge)
            changed = True

    replacements = {}
    if changed:
        for path in ("graphType.json", "dataSources.json", "graphDefinition.json"):
            content = json.dumps(decoded[path], ensure_ascii=False, separators=(",", ":"))
            replacements[path] = _encode_part(path, content)
    updated = [replacements.get(part["path"], part) for part in parts]
    return updated, changed, {
        "nodeTypeCount": len(aliases),
        "edgeTypeCount": len(relationship_projections),
        "staticPropertyCount": sum(
            len(spec["propertyTypes"]) for spec in entity_projections
        ),
    }


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

"""
ontology_api_helpers.py – Fabric deployment helpers for RTI structured ontology
════════════════════════════════════════════════════════════════════════════════
Fabric Ontology API helpers for deploying the structured ontology with direct
Eventhouse RTI binding.

Expected config variables from the 004 config cell:
- workspace_id
- target_folder_id
- key_vault_uri
- key_vault_tenant_id_secret
- key_vault_client_id_secret
- key_vault_client_secret_secret
- ONTOLOGY_NAME
"""

import requests
import time
import json
from typing import Optional
from IPython.display import display, Markdown

# ── API / retry config ───────────────────────────────────────────────────────

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
FABRIC_API_VERSION = "v1"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

# ── Validate required config from shared settings ─────────────────────────────

required_helper_config = [
    "workspace_id",
    "key_vault_uri",
    "key_vault_tenant_id_secret",
    "key_vault_client_id_secret",
    "key_vault_client_secret_secret",
]

missing_helper_config = [
    name
    for name in required_helper_config
    if name not in globals() or globals().get(name) in (None, "")
]

if missing_helper_config:
    raise RuntimeError(
        "Missing required ontology API helper config values: "
        f"{missing_helper_config}. Run the 004 config cell first."
    )

if "target_folder_id" not in globals() or not target_folder_id:
    print("⚠️ target_folder_id is not defined. Ontology folder guard will not be applied.")


# ── Token cache ───────────────────────────────────────────────────────────────

_token_cache = {
    "token": None,
    "expires_at": 0.0,
}


def get_spn_access_token() -> str:
    """
    Fetch SPN token from Key Vault and cache it until 60 seconds before expiry.

    Uses Key Vault secret names from rti_demo_settings/config:
    - key_vault_tenant_id_secret
    - key_vault_client_id_secret
    - key_vault_client_secret_secret
    """

    now = time.time()

    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    tenant_id = notebookutils.credentials.getSecret(
        key_vault_uri,
        key_vault_tenant_id_secret,
    )

    client_id = notebookutils.credentials.getSecret(
        key_vault_uri,
        key_vault_client_id_secret,
    )

    client_secret = notebookutils.credentials.getSecret(
        key_vault_uri,
        key_vault_client_secret_secret,
    )

    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://api.fabric.microsoft.com/.default",
        },
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


# ── Core request helper ───────────────────────────────────────────────────────

def api_request(
    method: str,
    url: str,
    data=None,
    params=None,
    timeout: int = 60,
) -> requests.Response:
    """
    Retryable API request with:
    - 429 backoff
    - 5xx retry
    - SPN auth header refresh per attempt
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
                wait = int(resp.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                print(
                    f"Rate limited — retrying in {wait}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                print(
                    f"Server error {resp.status_code} — retrying in "
                    f"{RETRY_DELAY_SECONDS}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            return resp

        except requests.exceptions.RequestException as ex:
            print(
                f"Request exception: {ex} "
                f"(attempt {attempt + 1}/{MAX_RETRIES})"
            )

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                raise

    raise RuntimeError(f"API request failed after {MAX_RETRIES} attempts: {method} {url}")


# ── LRO polling ───────────────────────────────────────────────────────────────

def wait_for_lro(operation_url: str) -> dict:
    """
    Poll a Fabric long-running operation until Succeeded/Completed/Failed/Cancelled.
    """

    start = time.time()

    while time.time() - start < LRO_MAX_WAIT_SECONDS:
        resp = api_request("GET", operation_url, timeout=60)

        if resp.status_code >= 400:
            print(f"❌ LRO poll failed: {resp.status_code}")
            print(resp.text[:3000])
            raise RuntimeError(f"LRO poll failed: {resp.status_code}")

        try:
            result = resp.json()
        except Exception:
            print("❌ LRO poll response was not valid JSON.")
            print(resp.text[:3000])
            raise

        status = result.get("status", "Unknown")

        if status in ("Succeeded", "Completed"):
            print(f"✅ LRO completed: {status}")
            return result

        if status in ("Failed", "Cancelled"):
            error = result.get("error", {})
            raise RuntimeError(f"LRO {status}: {error}")

        print(
            f"  ⏳ LRO status: {status} — polling again in "
            f"{LRO_POLL_INTERVAL_SECONDS}s"
        )

        time.sleep(LRO_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"LRO timed out after {LRO_MAX_WAIT_SECONDS}s — last URL: {operation_url}"
    )


# ── Ontology API functions ────────────────────────────────────────────────────

def list_ontologies() -> list:
    return _list_fabric_values(f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}/workspaces/{workspace_id}/ontologies")


def get_ontology(ontology_id: str) -> Optional[dict]:
    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{workspace_id}/ontologies/{ontology_id}"
    )

    resp = api_request("GET", url)

    if resp.status_code == 200:
        return resp.json()

    print(f"Could not retrieve ontology {ontology_id}: {resp.status_code}")
    print(resp.text[:3000])

    return None


def find_ontology_by_name(
    display_name: str,
    folder_id: Optional[str] = None,
    enforce_folder_guard: bool = True,
) -> Optional[dict]:
    """
    Find ontology by display name.

    If folder_id is supplied, return only the ontology in that folder.

    If an ontology with the same display name exists outside the target folder
    and no matching ontology exists inside the target folder, raise a clear
    error instead of silently reusing the wrong item.
    """

    resolved_folder_id = folder_id

    if resolved_folder_id is None:
        resolved_folder_id = globals().get("target_folder_id")

    matches = [
        ont
        for ont in list_ontologies()
        if ont.get("displayName") == display_name
    ]

    if not matches:
        return None

    if not resolved_folder_id:
        return matches[0]

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
            f"Ontology '{display_name}' already exists, but not in the target folder.\n"
            f"Existing ontology ID: {first.get('id')}\n"
            f"Existing folder ID: {first.get('folderId')}\n"
            f"Target folder ID: {resolved_folder_id}\n"
            "For a clean from-scratch test, delete the existing ontology or change the ontology name."
        )

    return None


def create_ontology(display_name: str, description: str = "", folder_id: Optional[str] = None) -> dict:
    """Select v2 explicitly through TMDL, then verify live generation metadata."""
    url = f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}/workspaces/{workspace_id}/ontologies"
    data = {
        "displayName": display_name,
        "description": description,
        "definition": {"parts": [
            _encode_part("database.tmdl", "database\n\tcompatibilityLevel: 1000000\n"),
            _encode_part("model.tmdl", "model Model\n\nref namespace default\n"),
            _encode_part("namespaces/default.tmdl", "namespace default\n\tlineageTag: default\n"),
        ]},
    }
    folder = target_folder_id if folder_id is None else folder_id
    if folder:
        data["folderId"] = folder
    response = api_request("POST", url, data=data)
    if response.status_code == 201:
        created = response.json()
    elif response.status_code == 202:
        created = _created_item_result(_fabric_operation_url(response, f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"))
    else:
        raise RuntimeError(f"Create v2 ontology failed: HTTP {response.status_code}")
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError("Create ontology returned no item ID")
    details = get_ontology(created["id"])
    _require_v2_ontology(details)
    return details


def ensure_ontology(display_name: str, description: str = "", folder_id: Optional[str] = None) -> dict:
    folder = target_folder_id if folder_id is None else folder_id
    existing = find_ontology_by_name(display_name=display_name, folder_id=folder, enforce_folder_guard=True)
    if existing:
        details = get_ontology(existing["id"])
        _require_v2_ontology(details)
        return details
    return create_ontology(display_name, description=description, folder_id=folder)


def get_ontology_definition(ontology_id: str) -> dict:
    return _get_fabric_ontology_definition(
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}", workspace_id, ontology_id)


def update_ontology_definition(
    ontology_id: str,
    definition_data: dict,
) -> dict:
    """
    Push ontology definition to Fabric.

    Handles:
    - 202 Accepted with LRO polling
    - 200 OK with JSON body
    - 200 OK with empty body
    """

    url = (
        f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}"
        f"/workspaces/{workspace_id}"
        f"/ontologies/{ontology_id}/updateDefinition"
    )

    response = api_request(
        "POST",
        url,
        data=definition_data,
        timeout=300,
    )

    if response.status_code == 202:
        operation_url = _fabric_operation_url(response, f"{FABRIC_API_BASE}/{FABRIC_API_VERSION}")

        if not operation_url:
            raise RuntimeError(
                "Fabric returned 202 Accepted but no LRO Location header was found."
            )

        lro_result = wait_for_lro(operation_url)
        print("✅ Definition update async LRO complete")

        return lro_result or {}

    if response.status_code == 200:
        print("✅ Definition updated successfully")

        if not response.text or not response.text.strip():
            return {}

        try:
            return response.json()
        except Exception:
            print("⚠️ Fabric returned HTTP 200, but the response body was not JSON.")
            print(response.text[:3000])
            return {}

    print(f"❌ Failed to update definition: {response.status_code}")
    print(response.text[:3000])

    raise RuntimeError(f"Update definition failed: {response.status_code}")


print("✅ Ontology API helpers loaded.")
print("✅ Workspace ID:", workspace_id)
print("✅ Target folder ID:", globals().get("target_folder_id"))
print("✅ Key Vault URI:", key_vault_uri)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""V2-only prerequisite: reject legacy items before any definition or settings writes."""
ontology = ensure_ontology(display_name=ONTOLOGY_NAME, folder_id=target_folder_id)
ontology_id = ontology["id"]
ontology_details = get_ontology(ontology_id)
_require_v2_ontology(ontology_details)
live_parts = get_ontology_definition(ontology_id)["definition"]["parts"]
ONTOLOGY_GENERATION = _resolve_ontology_generation(ontology_details, live_parts)
print("Verified live ontology generation 2")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""Read source schemas and construct v2 entity/key metadata. No definitions are published here."""
from collections import Counter
import pandas as pd
from IPython.display import display, Markdown


def clean_entity_name(table_name):
    name = re.sub(r"^silver_", "", table_name)
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return (name if re.match(r"^[A-Za-z]", name) else "E_" + name)[:26]


def singular(name):
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith(("ses", "xes")):
        return name[:-2]
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def make_safe_rel_name(source, target):
    if (source, target) in REL_NAME_OVERRIDES:
        return REL_NAME_OVERRIDES[(source, target)]
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", f"{source}_to_{target}")
    return candidate[:26].rstrip("_") or "relationship"


def spark_to_api_type(data_type):
    return _spark_api_type(data_type)


source_tables = (MANUAL_TABLE_LIST if USE_MANUAL_TABLE_LIST else
                 [table.name for table in spark.catalog.listTables() if table.name.startswith("silver_")])
if not source_tables:
    raise RuntimeError("No source tables selected for the v2 ontology")
table_schemas = [
    {"table": name, "entity": clean_entity_name(name),
     "columns": [{"name": field.name, "dataType": spark_to_api_type(field.dataType)}
                 for field in spark.read.table(name).schema.fields]}
    for name in source_tables
]
column_counts = Counter(column["name"] for table in table_schemas for column in table["columns"]
                        if column["name"].endswith("_id"))
own_pk_map = {}
for table in table_schemas:
    entity = table["entity"]
    columns = [column["name"] for column in table["columns"]]
    matching = [name for name in columns if name.endswith("_id") and
                (name[:-3] in entity or entity in name[:-3])]
    unique = [name for name in columns if name.endswith("_id") and column_counts[name] == 1]
    key = _resolve_own_key(entity, columns, OWN_PK_OVERRIDES,
                           [f"{entity}_id", f"{singular(entity)}_id"] + matching + unique)
    if key is None:
        raise RuntimeError(f"No source key for {entity}; configure OWN_PK_OVERRIDES explicitly")
    own_pk_map[entity] = key
print(f"Prepared {len(table_schemas)} v2 source schemas; column types are entity-specific")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""Construct and publish v2 TMDL while preserving live bindings and custom parts."""

import base64
import uuid
import pandas as pd


def _tag(*values):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "fabric-ontology-gen2/" + "/".join(map(str, values))))


def _part(path, content):
    return {
        "path": path,
        "payload": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    }


def _content(part):
    return base64.b64decode(part["payload"]).decode("utf-8")


def _tmdl_type(api_type):
    mapping = {
        "String": "string", "BigInt": "int64", "Double": "double",
        "Boolean": "boolean", "DateTime": "dateTime",
    }
    if api_type not in mapping:
        raise ValueError(f"Unsupported Generation 2 property type: {api_type}")
    return mapping[api_type]


def _build_gen2_parts(base_parts):
    """Create only the ontology entities, properties, and semantic relationships."""
    by_path = {part["path"]: part for part in base_parts}
    required = {".platform", "database.tmdl", "namespaces/default.tmdl"}
    if not required.issubset(by_path):
        raise RuntimeError(f"Generation 2 root parts missing: {required - set(by_path)}")

    schemas = {table["entity"]: table for table in table_schemas}
    if not schemas:
        raise RuntimeError("No entity schemas selected")
    if len(schemas) != len(table_schemas):
        raise RuntimeError("Two source tables resolve to the same entity name")

    generated = []
    model_entities = []
    for entity_name, schema in schemas.items():
        key = own_pk_map.get(entity_name)
        columns = {column["name"]: column for column in schema["columns"]}
        if key not in columns:
            raise RuntimeError(f"Missing key {key!r} for {entity_name}")

        properties = []
        for col_name, column in columns.items():
            dtype = _tmdl_type(column["dataType"])
            properties.append(
                f"\tproperty {_quote_name(col_name)}\n\t\tdataType: {dtype}\n"
                f"\t\tlineageTag: {_tag('property', entity_name, col_name)}\n"
            )

        if entity_name == SIGNAL_MASTER_ENTITY:
            for ts_column in RTI_TIMESERIES_PROPERTIES:
                name = ts_column["name"]
                if name in columns:
                    raise RuntimeError(f"Signal time-series property {name!r} is already static")
                dtype = _tmdl_type(ts_column["dataType"])
                properties.append(
                    f"\tproperty {name}\n\t\tdataType: TimeSeries<{dtype}>\n"
                    f"\t\tlineageTag: {_tag('timeseries', entity_name, name)}\n"
                )

        entity = (
            f"entity {_quote_name(entity_name)}\n\tlineageTag: {_tag('entity', entity_name)}\n"
            f"\tkeyProperty: {_quote_name(key)}\n\n" + "\n".join(properties)
        )
        generated.append(_part(f"entities/{entity_name}.tmdl", entity))
        model_entities.append(f"ref entity {entity_name}")

    relationships = []
    for source, target in ENTITY_PARENT_MAP.items():
        if source not in schemas or target not in schemas:
            raise RuntimeError(f"Missing relationship endpoint: {source} -> {target}")
        target_key = own_pk_map[target]
        if target_key not in {column["name"] for column in schemas[source]["columns"]}:
            raise RuntimeError(f"{source} has no {target_key} to join {target}")
        rel_name = make_safe_rel_name(source, target)
        relationships.append(
            f"entityRelationship {rel_name}\n\tlabel: {rel_name}\n"
            f"\tlineageTag: {_tag('relationship', source, target)}\n"
            f"\tfromEntity: {source}\n\ttoEntity: {target}\n"
        )
    generated.append(_part("entityRelationships.tmdl", "\n\n".join(relationships)))
    generated.append(_part("model.tmdl", "model Model\n\n" + "\n".join(model_entities)
                           + "\n\nref namespace default\n"))
    root = [by_path[name] for name in (".platform", "database.tmdl", "namespaces/default.tmdl")]
    return _merge_gen2_structure(base_parts, root + generated)



def _persist_verified_generation(generation):
    if type(generation) is not int or generation != 2:
        raise ValueError(f"Only verified ontology generation 2 can be persisted: {generation!r}")
    generation_df = (
        spark.createDataFrame([{
            "setting_name": "ontology_generation",
            "setting_value": str(generation),
        }])
        .withColumn("updated_utc", F.current_timestamp())
    )
    (
        DeltaTable.forName(spark, settings_table_name).alias("target")
        .merge(generation_df.alias("source"), "target.setting_name = source.setting_name")
        .whenMatchedUpdate(set={
            "setting_value": "source.setting_value",
            "updated_utc": "source.updated_utc",
        })
        .whenNotMatchedInsert(values={
            "setting_name": "source.setting_name",
            "setting_value": "source.setting_value",
            "updated_utc": "source.updated_utc",
        })
        .execute()
    )
    settings["ontology_generation"] = str(generation)



# Only verified v2 TMDL is submitted and published.
from delta.tables import DeltaTable

submitted_parts = _build_gen2_parts(live_parts)
update_ontology_definition(ontology_id, {"definition": {"parts": submitted_parts}})
verified_parts = get_ontology_definition(ontology_id)["definition"]["parts"]
_verify_definition(submitted_parts, verified_parts)
verified_generation = _resolve_ontology_generation(get_ontology(ontology_id), verified_parts)

generated_at = pd.Timestamp.now().isoformat()
parts_rows = []
for part in verified_parts:
    path = part["path"]
    parts_rows.append({
        "path": path, "part_type": path.split("/")[0], "sub_type": "",
        "item_id": "", "item_name": "", "namespace": "",
        "entity_id_parts": "[]", "source_entity_id": "", "target_entity_id": "",
        "payload": part["payload"], "payload_type": part["payloadType"],
        "generated_at": generated_at,
    })
(spark.createDataFrame(pd.DataFrame(parts_rows)).write.mode("overwrite")
 .option("overwriteSchema", "true").saveAsTable("ontology_parts_latest"))

entity_rows = [{
    "entity": table["entity"], "own_pk": own_pk_map[table["entity"]],
    "entity_id_parts": json.dumps([own_pk_map[table["entity"]]]),
    "fk_cols": json.dumps([column["name"] for column in table["columns"]
                           if column["name"] in set(own_pk_map.values())
                           and column["name"] != own_pk_map[table["entity"]]]),
    "generated_at": generated_at,
} for table in table_schemas]
(spark.createDataFrame(entity_rows).write.mode("overwrite")
 .option("overwriteSchema", "true").saveAsTable("ontology_entity_audit"))
relationship_rows = [{
    "relationship": make_safe_rel_name(source, target),
    "tgt_own_pk": own_pk_map[target], "rel_name": make_safe_rel_name(source, target),
    "effective_join_keys": json.dumps([own_pk_map[target]]),
    "missing_in_src": "", "status": "Verified v2", "generated_at": generated_at,
} for source, target in ENTITY_PARENT_MAP.items()]
(spark.createDataFrame(relationship_rows,
    "relationship string, tgt_own_pk string, rel_name string, effective_join_keys string, "
    "missing_in_src string, status string, generated_at string").write.mode("overwrite")
 .option("overwriteSchema", "true").saveAsTable("ontology_relationship_audit"))
_persist_verified_generation(verified_generation)
print("Verified v2 TMDL published; existing bindings and custom parts preserved")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
