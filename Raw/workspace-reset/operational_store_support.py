"""Shared operational-store support embedded in NB01 and NB11; no local imports."""
import hashlib
import base64
import json
import re
import struct
import time
from ontology_notebook_support import _fabric_operation_url, _fabric_result_url, _project_semantics

OPERATIONAL_STORE_NAME = "HydroOperationalStore"
OPERATIONAL_STORE_DESCRIPTION = "Hydro shared operational store; lifecycle independent of AppBackend."
OPERATIONAL_ARCHIVE_PREFIX = "Hydro archived operational store; verified replacement "
OPERATIONAL_DEMO_COUNTS = {
    "WorkOrders": 12, "MaintenanceNotifications": 6, "Inspections": 30,
    "SpareParts": 12, "Asset3DModels": 15,
}


def ontology_context_elements(parts):
    """Derive context only from actual entity declarations in the selected ontology."""
    elements = []
    seen = set()
    for part in parts:
        if not part["path"].startswith("entities/") or not part["path"].endswith(".tmdl"):
            continue
        text = base64.b64decode(part["payload"], validate=True).decode("utf-8").replace("\r\n", "\n")
        matches = list(re.finditer(
            r"(?m)^entity (?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z_0-9]*))[ \t]*$", text))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one live entity in {part['path']!r}.")
        match = matches[0]
        name = (match[1] or match[2]).replace("''", "'")
        if name in seen:
            raise RuntimeError(f"Duplicate live ontology entity {name!r}.")
        seen.add(name)
        lines = text[:match.start()].splitlines()
        description = []
        while lines and lines[-1].startswith("///"):
            description.append(lines.pop()[3:].strip())
        elements.append({
            "id": name, "is_selected": True, "display_name": name,
            "type": "ontology.entity", "description": " ".join(reversed(description)) or None,
            "children": [],
        })
    if not elements:
        raise RuntimeError("The live ontology definition contains no entity metadata; refusing stale context.")
    return elements


def refresh_ontology_context_parts(parts, live_elements, workspace_id, ontology_id, legacy_descriptions=()):
    """Keep live selections/custom metadata, removing references to genuinely retired types."""
    live_names = {element["display_name"] for element in live_elements}
    if not live_names:
        raise RuntimeError("Cannot refresh ontology context without verified live entity metadata.")
    legacy = dict(legacy_descriptions)
    result = []
    for original in parts:
        path = original["path"]
        if not path.startswith("Files/Config/draft/") or not (
                path.endswith("/ontology.json") or path.endswith("/datasource.json")):
            result.append(original)
            continue
        context = json.loads(base64.b64decode(original["payload"], validate=True))
        if not path.endswith("/ontology.json") and context.get("type") != "ontology":
            result.append(original)
            continue
        if context.get("artifactId") != ontology_id or context.get("workspaceId") != workspace_id:
            raise RuntimeError("Cannot refresh context for a different ontology or workspace.")
        existing = context.get("elements", [])
        if not isinstance(existing, list) or any(not isinstance(node, dict) for node in existing):
            raise RuntimeError("Existing ontology elements are malformed; refusing to replace custom selections.")
        authored = context.get("entitiesWithUserAuthoredDescriptions")
        if authored is not None and (
                not isinstance(authored, list) or any(not isinstance(name, str) for name in authored)):
            raise RuntimeError("User-authored ontology description metadata is malformed.")
        elements = [
            node for node in existing if node.get("type") != "ontology.entity"
            or node.get("id") in live_names or node.get("display_name") in live_names
        ]
        refreshed = set()
        for desired in live_elements:
            name = desired["display_name"]
            indices = [index for index, old in enumerate(elements)
                       if old.get("type") == "ontology.entity"
                       and (old.get("id") == name or old.get("display_name") == name)]
            if len(indices) > 1:
                raise RuntimeError(f"Duplicate saved ontology entity {name!r}; inspect its selections.")
            if not indices:
                elements.append(dict(desired))
                continue
            index = indices[0]
            old = elements[index]
            description = old.get("description")
            if desired.get("description") and (
                    description is None or description == "" or description == legacy.get(name)
                    or isinstance(authored, list) and name not in authored):
                elements[index] = {**old, "description": desired["description"]}
                refreshed.add(name)
        context["elements"] = elements
        if authored is not None:
            context["entitiesWithUserAuthoredDescriptions"] = [
                name for name in authored if name in live_names and name not in refreshed]
        result.append({**original, "payload": base64.b64encode(json.dumps(context).encode()).decode()})
    return result


def selected_ontology_entities(parts, workspace_id, ontology_id, stage="draft"):
    contexts = []
    for part in parts:
        native = part["path"].startswith(f"Files/Config/{stage}/ontologies/") and part["path"].endswith("/ontology.json")
        source = part["path"].startswith(f"Files/Config/{stage}/") and part["path"].endswith("/datasource.json")
        if native or source:
            context = json.loads(base64.b64decode(part["payload"], validate=True))
            if source and context.get("type") != "ontology":
                continue
            if context.get("artifactId") == ontology_id:
                if context.get("workspaceId") != workspace_id:
                    raise RuntimeError("Ontology context belongs to another workspace.")
                contexts.append(context)
    if len(contexts) > 1:
        raise RuntimeError("Ambiguous Ontology context.")
    if not contexts:
        return set()
    return {element["display_name"] for element in contexts[0].get("elements", [])
            if element.get("type") == "ontology.entity" and element.get("is_selected") is True}


def ontology_source_bindings(parts, workspace_id):
    """Resolve physical tables only through source identities retained by the Fabric editor."""
    texts = {part["path"]: base64.b64decode(part["payload"], validate=True).decode("utf-8").replace("\r\n", "\n")
             for part in parts}
    if len(texts) != len(parts):
        raise RuntimeError("Duplicate Ontology definition paths.")
    unquote = lambda value: value.strip().strip("'").replace("''", "'")
    expressions = {}
    for match in re.finditer(r"(?ms)^expression (.+?) =\n.*?(?=^expression |\Z)", texts.get("expressions.tmdl", "")):
        name = unquote(match[1])
        if name in expressions:
            raise RuntimeError("Duplicate Ontology source expression.")
        expressions[name] = match[0]
    bindings = {}
    for path, entity in texts.items():
        if not path.startswith("entities/") or not path.endswith(".tmdl"):
            continue
        names = re.findall(r"(?m)^entity (.+?)[ \t]*$", entity)
        if len(names) != 1:
            raise RuntimeError(f"Ambiguous Ontology entity: {path}.")
        entity_name = unquote(names[0])
        tables = re.findall(r"(?m)^\tbackingTable:\s*(.+?)[ \t]*$", entity)
        tables += re.findall(r"(?m)^\tadditionalBackingTable\n\t\ttable:\s*(.+?)[ \t]*$", entity)
        for alias in tables:
            table = texts.get(f"tables/{unquote(alias)}.tmdl", "")
            sources = re.findall(r"(?m)^\s+expressionSource:\s*(.+?)[ \t]*$", table)
            if len(sources) > 1:
                raise RuntimeError("Ambiguous Ontology table source.")
            metadata = expressions.get(unquote(sources[0]), "") if sources else table
            identity = {}
            for key in ("WorkspaceId", "ItemId", "ItemKind"):
                values = re.findall(r"(?m)^\s+annotation ONT_" + key + r" = (.+?)[ \t]*$", metadata)
                if len(values) > 1:
                    raise RuntimeError("Ambiguous Ontology source identity.")
                identity[key] = values[0] if values else None
            if identity["WorkspaceId"] != workspace_id or not identity["ItemId"]:
                continue
            kind, item_id = identity["ItemKind"], identity["ItemId"]
            if kind in ("Lakehouse", "SQLDatabase"):
                expected = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{item_id}"
                urls = re.findall(r'AzureStorage\.DataLake\(\s*"([^"]+)"', metadata)
                physical = re.findall(r"(?m)^\s+entityName:\s*(.+?)[ \t]*$", table)
                if urls != [expected] or len(physical) != 1:
                    raise RuntimeError("Ontology source identity does not match its physical binding.")
                physical_name = unquote(physical[0])
            elif kind == "KQLDatabase":
                queries = re.findall(r'AzureDataExplorer\.Contents\(\s*"(https://[^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', table)
                names = re.findall(r"(?m)^\s+annotation ONT_ItemName = (.+?)[ \t]*$", metadata)
                if len(queries) != 1 or names != [queries[0][1]]:
                    raise RuntimeError("Ontology Eventhouse source has no unambiguous physical table.")
                physical_name = queries[0][2]
            else:
                continue
            bindings.setdefault((kind, item_id), {}).setdefault(physical_name, set()).add(entity_name)
    return bindings


def operational_store_name(env_suffix):
    if not isinstance(env_suffix, str) or not re.fullmatch(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", env_suffix):
        raise RuntimeError("Operational SQL requires the explicit setup environment suffix.")
    return f"RTI_Demo_SQLDatabase_{env_suffix}"


def is_operational_store_name(name):
    return name == OPERATIONAL_STORE_NAME or (
        isinstance(name, str) and re.fullmatch(r"RTI_Demo_SQLDatabase_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", name) is not None
    )


def active_operational_items(items):
    candidates = [item for item in items if item.get("type") == "SQLDatabase"
                  and is_operational_store_name(item.get("displayName"))]
    active, archived = [], []
    for item in candidates:
        match = re.fullmatch(re.escape(OPERATIONAL_ARCHIVE_PREFIX) + r"([0-9a-fA-F-]{36})\.",
                             item.get("description") or "")
        if match:
            archived.append(match[1].lower())
        else:
            active.append(item)
    if archived and (len(active) != 1 or any(target != active[0]["id"].lower() for target in archived)):
        raise RuntimeError("Archived operational SQL does not identify one available active replacement.")
    return active


def operational_source_path(parts, workspace_id, item_id, preferred_path, source_type="sql_database"):
    sources = {
        part["path"]: json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
        for part in parts
        if part["path"].startswith("Files/Config/draft/") and part["path"].endswith("/datasource.json")
    }
    matching = [path for path, source in sources.items()
                if source.get("type") == source_type and source.get("artifactId") == item_id]
    label = "operational SQL" if source_type == "sql_database" else source_type
    if len(matching) > 1:
        raise RuntimeError(f"Multiple Data Agent {label} sources reference the same item.")
    if matching:
        path = matching[0]
        if sources[path].get("workspaceId") != workspace_id:
            raise RuntimeError(f"Existing {label} source has a conflicting workspace identity.")
        return path
    if preferred_path in sources:
        raise RuntimeError(f"The preferred {label} source path belongs to another source.")
    return preferred_path

OPERATIONAL_COLUMNS = {
    "WorkOrders": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "workOrderNumber": "nvarchar(max) NOT NULL", "equipmentId": "nvarchar(max) NOT NULL",
        "instrumentId": "nvarchar(max) NULL", "opcuaNodeId": "nvarchar(max) NULL",
        "title": "nvarchar(max) NOT NULL", "description": "nvarchar(max) NULL",
        "priority": "nvarchar(max) NOT NULL", "status": "nvarchar(max) NOT NULL",
        "assignedToOid": "nvarchar(max) NULL", "createdByOid": "nvarchar(max) NOT NULL",
        "createdAt": "datetime2 NOT NULL", "dueAt": "datetime2 NULL", "completedAt": "datetime2 NULL",
    },
    "MaintenanceNotifications": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "equipmentId": "nvarchar(max) NOT NULL", "opcuaNodeId": "nvarchar(max) NULL",
        "summary": "nvarchar(max) NOT NULL", "severity": "nvarchar(max) NOT NULL",
        "status": "nvarchar(max) NOT NULL", "reportedByOid": "nvarchar(max) NOT NULL",
        "reportedAt": "datetime2 NOT NULL",
    },
    "Asset3DModels": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "equipmentId": "nvarchar(max) NOT NULL", "modelName": "nvarchar(max) NOT NULL",
        "format": "nvarchar(max) NOT NULL", "modelUrl": "nvarchar(max) NOT NULL",
        "thumbnailUrl": "nvarchar(max) NULL", "fileSizeMb": "decimal(38,10) NULL",
        "version": "nvarchar(max) NULL", "updatedByOid": "nvarchar(max) NOT NULL",
        "updatedAt": "datetime2 NOT NULL",
    },
    "Inspections": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "equipmentId": "nvarchar(max) NOT NULL", "opcuaNodeId": "nvarchar(max) NULL",
        "inspectionType": "nvarchar(max) NOT NULL", "result": "nvarchar(max) NOT NULL",
        "findings": "nvarchar(max) NULL", "inspectorOid": "nvarchar(max) NOT NULL",
        "inspectedAt": "datetime2 NOT NULL", "nextDueAt": "datetime2 NULL",
    },
    "SpareParts": {
        "id": "uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY",
        "partNumber": "nvarchar(255) NOT NULL UNIQUE", "name": "nvarchar(max) NOT NULL",
        "category": "nvarchar(max) NOT NULL", "equipmentType": "nvarchar(max) NOT NULL",
        "quantityOnHand": "int NOT NULL", "reorderLevel": "int NOT NULL",
        "storageLocation": "nvarchar(max) NOT NULL", "unitCostUsd": "decimal(38,10) NULL",
        "lastRestockedAt": "datetime2 NULL",
    },
}

WEATHER_PROJECT_ENTITIES = {
    "WeatherSource": ("weather_sources", ("source_id",)),
    "WeatherLocation": ("weather_locations", ("location_id",)),
    "WeatherArea": ("weather_areas", ("area_id",)),
    "WeatherLatestObservation": ("weather_latest_observations", ("source_id", "location_id", "observed_at_utc")),
    "WeatherLatestForecast": ("weather_latest_forecasts",
                              ("source_id", "target_kind", "target_id", "reference_time_utc", "valid_time_utc")),
    "EquipmentType": ("ontology_equipment_types", ("equipment_type_code",)),
}

PROJECT_CATALOG_ENTITIES = {
    "WeatherVariable": "weather_variables",
    "WeatherIngestionRun": "weather_ingestion_runs",
    "InstrumentClassification": "gold_instrument_classification",
}

SQL_PROJECT_ENTITIES = {
    "WorkOrder": "WorkOrders", "Inspection": "Inspections", "SparePart": "SpareParts",
    "Asset3DModel": "Asset3DModels", "MaintenanceNotification": "MaintenanceNotifications",
}

PROJECT_CORE_ENTITIES = {
    "facilities": ("silver_facilities", "facility_id"),
    "systems": ("silver_systems", "system_id"),
    "equipment": ("silver_equipment", "equipment_id"),
    "instruments": ("silver_instruments", "instrument_id"),
    "signal_master": ("silver_signal_master", "opcua_node_id"),
}

PROJECT_REQUIRED_EDGES = (
    ("systems", "facilities", "facility_id", "facility_id"),
    ("equipment", "systems", "system_id", "system_id"),
    ("instruments", "equipment", "equipment_id", "equipment_id"),
    ("signal_master", "instruments", "instrument_id", "instrument_id"),
    *((name, "equipment", "equipmentId", "equipment_id") for name in
      ("WorkOrder", "Inspection", "Asset3DModel", "MaintenanceNotification")),
    ("WorkOrder", "instruments", "instrumentId", "instrument_id"),
    *((name, "signal_master", "opcuaNodeId", "opcua_node_id") for name in
      ("WorkOrder", "Inspection", "MaintenanceNotification")),
    ("WeatherLatestObservation", "WeatherLocation", "location_id", "location_id"),
    ("WeatherLatestObservation", "WeatherSource", "source_id", "source_id"),
    ("WeatherLatestForecast", "WeatherSource", "source_id", "source_id"),
    ("WeatherLocation", "facilities", "location_id", "facility_id"),
    ("WeatherArea", "WeatherLocation", "location_id", "location_id"),
    ("WeatherLatestForecast", "WeatherLocation", "location_id", "location_id"),
    ("WeatherLatestForecast", "WeatherArea", "area_id", "area_id"),
    ("equipment", "EquipmentType", "equipment_type_code", "equipment_type_code"),
    ("SparePart", "EquipmentType", "equipmentType", "equipment_type_code"),
)


def _project_contract_objects(text, kind):
    """Read TMDL objects without consuming same-indent siblings or custom parts."""
    result = {}
    lines = text.replace("\r\n", "\n").splitlines()
    for index, line in enumerate(lines):
        match = re.fullmatch(r"([ \t]*)" + re.escape(kind) + r" (.+?)\s*", line)
        if not match:
            continue
        name = match[2].strip().strip("'").replace("''", "'")
        if name in result:
            raise RuntimeError(f"Duplicate project {kind}: {name}.")
        depth = len(match[1].expandtabs(4))
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and not candidate.lstrip().startswith("//"):
                prefix = candidate[:len(candidate) - len(candidate.lstrip())]
                if len(prefix.expandtabs(4)) <= depth:
                    break
            end += 1
        result[name] = "\n".join(lines[index:end])
    return result


def _project_contract_setting(text, key):
    text = text.replace("\r\n", "\n")
    values = re.findall(r"(?m)^[ \t]+" + re.escape(key) + r":[ \t]*(.+?)[ \t]*$", text)
    if len(values) != 1:
        raise RuntimeError(f"Project contract requires one {key} binding.")
    return values[0].strip().strip("'").replace("''", "'")


def verify_project_ontology_contract(parts, workspace_id, lakehouse_id, sql_db_item_id):
    """Pure definition readback gate; live numeric generation must be checked by the caller."""
    if not all(isinstance(value, str) and value.strip()
               for value in (workspace_id, lakehouse_id, sql_db_item_id)) or lakehouse_id == sql_db_item_id:
        raise RuntimeError("Project contract requires authoritative distinct source identities.")
    texts = {p["path"]: base64.b64decode(p["payload"], validate=True).decode("utf-8").replace("\r\n", "\n")
             for p in parts}
    if len(texts) != len(parts):
        raise RuntimeError("Duplicate project definition paths.")
    if not {"database.tmdl", "model.tmdl", "namespaces/default.tmdl"}.issubset(texts):
        raise RuntimeError("Project contract requires complete v2 TMDL root parts.")
    entities = {e["display_name"] for e in ontology_context_elements(parts)}
    ontology_source_bindings(parts, workspace_id)
    required = {name: (physical, (key,), "Lakehouse", lakehouse_id)
                for name, (physical, key) in PROJECT_CORE_ENTITIES.items()}
    required.update({name: (physical, keys, "Lakehouse", lakehouse_id)
                     for name, (physical, keys) in WEATHER_PROJECT_ENTITIES.items()})
    required.update({name: (physical, ("id",), "SQLDatabase", sql_db_item_id)
                     for name, physical in SQL_PROJECT_ENTITIES.items()})
    if not set(required).issubset(entities):
        raise RuntimeError("Missing required project entities: " + ", ".join(sorted(set(required) - entities)))
    aliases = {}
    properties = {}
    core_source_tables = {}
    for name, (physical, keys, kind, item) in required.items():
        entity = texts.get(f"entities/{name}.tmdl", "")
        alias = _project_contract_setting(entity, "backingTable")
        aliases[name] = alias
        table = texts.get(f"tables/{alias}.tmdl", "")
        actual_physical = _project_contract_setting(table, "entityName")
        if name in PROJECT_CORE_ENTITIES:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", actual_physical):
                raise RuntimeError(f"Invalid core source table identity for {name}.")
            physical = actual_physical
            core_source_tables[name] = physical
        elif actual_physical != physical:
            raise RuntimeError(f"Incorrect backing table for {name}.")
        primary_parts = [
            {"path": "expressions.tmdl", "payload": base64.b64encode(
                texts.get("expressions.tmdl", "").encode()).decode()},
            {"path": f"entities/{name}.tmdl", "payload": base64.b64encode(
                f"entity {name}\n\tbackingTable: {alias}\n".encode()).decode()},
            {"path": f"tables/{alias}.tmdl", "payload": base64.b64encode(table.encode()).decode()},
        ]
        primary_bindings = ontology_source_bindings(primary_parts, workspace_id)
        if name not in primary_bindings.get((kind, item), {}).get(physical, set()):
            raise RuntimeError(f"Unverified authoritative source binding for {name}.")
        properties[name] = _project_contract_objects(entity, "property")
        columns = _project_contract_objects(table, "column")
        needed = set(keys)
        if len(keys) > 1:
            needed.add("ontology_key")
        if name in SQL_PROJECT_ENTITIES:
            needed.update(OPERATIONAL_COLUMNS[physical])
        for source, target, source_key, target_key in PROJECT_REQUIRED_EDGES:
            if name == source:
                needed.add(source_key)
            if name == target:
                needed.add(target_key)
        expected_key = "ontology_key" if len(keys) > 1 else keys[0]
        if _project_contract_setting(entity, "keyProperty") != expected_key:
            raise RuntimeError(f"Incorrect project key for {name}.")
        for prop in needed:
            if prop not in properties[name] or prop not in columns:
                raise RuntimeError(f"Missing required property/column {name}.{prop}.")
            if (_project_contract_setting(properties[name][prop], "valueColumn") != f"{alias}.{prop}"
                    or _project_contract_setting(columns[prop], "sourceColumn") != prop):
                raise RuntimeError(f"Incorrect backing property binding for {name}.{prop}.")
        for ref_kind, ref_name in (("entity", name), ("table", alias)):
            if not re.search(r"(?m)^ref " + ref_kind + r" (?:'?" + re.escape(ref_name) + r"'?)[ \t]*$",
                             texts["model.tmdl"]):
                raise RuntimeError(f"Missing project model reference for {ref_name}.")
    physical_edges = _project_contract_objects(texts.get("relationships.tmdl", ""), "relationship")
    semantic_edges = _project_contract_objects(texts.get("entityRelationships.tmdl", ""), "entityRelationship")
    found = set()
    for block in semantic_edges.values():
        source = _project_contract_setting(block, "fromEntity")
        target = _project_contract_setting(block, "toEntity")
        if (source, target) not in {(s, t) for s, t, _, _ in PROJECT_REQUIRED_EDGES}:
            continue
        physical_name = _project_contract_setting(block, "relationship")
        backing = physical_edges.get(physical_name)
        if backing is None:
            raise RuntimeError(f"Dangling required relationship {source} -> {target}.")
        from_column = _project_contract_setting(backing, "fromColumn")
        to_column = _project_contract_setting(backing, "toColumn")
        for edge in PROJECT_REQUIRED_EDGES:
            s, t, sk, tk = edge
            if (source, target) == (s, t) and (
                    from_column, to_column) == (f"{aliases[s]}.{sk}", f"{aliases[t]}.{tk}"):
                found.add(edge)
    missing = set(PROJECT_REQUIRED_EDGES) - found
    if missing:
        raise RuntimeError("Missing required project relationship bindings: " + repr(sorted(missing)))
    return {
        "status": "verified", "workspace_id": workspace_id, "lakehouse_id": lakehouse_id,
        "sql_db_item_id": sql_db_item_id, "required_entity_count": len(required),
        "entity_count": len(entities), "required_relationship_count": len(found),
        "relationship_count": len(semantic_edges), "scope": "definition_readback_only",
        "core_source_tables": core_source_tables,
    }


def _project_core_source_tables(spark, equipment_table):
    """Use setup's explicit identities, never table-name discovery or suffix inference."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", equipment_table):
        raise RuntimeError("Equipment relationship source requires an explicit safe table identity.")
    tables = {name: physical for name, (physical, _) in PROJECT_CORE_ENTITIES.items()}
    if spark.catalog.tableExists("rti_demo_settings"):
        settings_rows = spark.table("rti_demo_settings").limit(100001).collect()
        if len(settings_rows) > 100000:
            raise RuntimeError("Relationship settings exceed bounded validation limit 100000.")
        values = {}
        names = {f"silver_{name}_table": name for name in tables}
        for row in settings_rows:
            row = row.asDict(recursive=True)
            setting = row.get("setting_name")
            if setting not in names:
                continue
            if setting in values:
                raise RuntimeError(f"Duplicate authoritative source setting {setting}.")
            value = row.get("setting_value")
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
                raise RuntimeError(f"Malformed authoritative source setting {setting}.")
            values[setting] = value
        if set(values) != set(names):
            raise RuntimeError("Missing authoritative core table settings for relationship validation.")
        tables = {entity: values[setting] for setting, entity in names.items()}
        if equipment_table != PROJECT_CORE_ENTITIES["equipment"][0] and equipment_table != tables["equipment"]:
            raise RuntimeError("Equipment argument conflicts with authoritative source settings.")
    else:
        tables["equipment"] = equipment_table
    return tables


def prepare_project_relationship_sources(spark, equipment_table="silver_equipment"):
    """Persist explicit relationship keys from the producers' verified identities."""
    core_tables = _project_core_source_tables(spark, equipment_table)
    equipment_table = core_tables["equipment"]
    def reject(query, message):
        if spark.sql(query).limit(1).count():
            raise RuntimeError(message)
    reject(
        f"SELECT equipment_type_code FROM `{equipment_table}` "
        "GROUP BY equipment_type_code HAVING equipment_type_code IS NULL "
        "OR COUNT(DISTINCT equipment_type_name) <> 1 OR COUNT(*) <> COUNT(equipment_type_name)",
        "Equipment types require a nonnull code with one authoritative type name.")
    spark.sql(
        "CREATE TABLE IF NOT EXISTS ontology_equipment_types "
        "(equipment_type_code STRING, equipment_type_name STRING) USING DELTA "
        "TBLPROPERTIES ('hydro.businessProjection' = 'equipment-types-v1')")
    properties = dict(spark.sql("SHOW TBLPROPERTIES ontology_equipment_types").collect())
    if properties.get("hydro.businessProjection") != "equipment-types-v1":
        raise RuntimeError("Existing ontology_equipment_types is not the managed equipment-type projection.")
    reject(
        "SELECT equipment_type_code FROM ontology_equipment_types "
        "GROUP BY equipment_type_code HAVING equipment_type_code IS NULL OR COUNT(*) <> 1",
        "Equipment-type projection has invalid or duplicate identities.")
    spark.sql(
        "MERGE INTO ontology_equipment_types t USING "
        f"(SELECT DISTINCT equipment_type_code,equipment_type_name FROM `{equipment_table}`) s "
        "ON t.equipment_type_code=s.equipment_type_code "
        "WHEN MATCHED THEN UPDATE SET t.equipment_type_name=s.equipment_type_name "
        "WHEN NOT MATCHED THEN INSERT *")
    projections = {
        "weather_areas": {"location_id": "get_json_object(metadata_json, '$.location_id')"},
        "weather_latest_forecasts": {
            "location_id": "CASE WHEN target_kind='location' THEN target_id END",
            "area_id": "CASE WHEN target_kind='area' THEN target_id END",
        },
    }
    reject("SELECT 1 FROM weather_latest_forecasts WHERE target_kind IS NULL "
           "OR target_kind NOT IN ('location','area') OR target_id IS NULL",
           "Weather forecast target identity is missing or unsupported.")
    for table, columns in projections.items():
        existing = {field.name: field.dataType.simpleString() for field in spark.table(table).schema.fields}
        for column, expression in columns.items():
            if column not in existing:
                spark.sql(f"ALTER TABLE `{table}` ADD COLUMNS (`{column}` STRING)")
            elif existing[column] != "string":
                raise RuntimeError(f"Existing relationship key {table}.{column} is not a string.")
            reject(
                f"SELECT 1 FROM `{table}` WHERE `{column}` IS NOT NULL "
                f"AND NOT (`{column}` <=> ({expression}))",
                f"Existing relationship key {table}.{column} conflicts with its source identity.")
            spark.sql(f"UPDATE `{table}` SET `{column}` = {expression} "
                      f"WHERE NOT (`{column}` <=> ({expression}))")
            reject(f"SELECT 1 FROM `{table}` WHERE NOT (`{column}` <=> ({expression}))",
                   f"Relationship-key readback failed for {table}.{column}.")
    for query, message in (
        (f"SELECT 1 FROM weather_locations w LEFT ANTI JOIN `{core_tables['facilities']}` f "
         "ON w.location_id=f.facility_id", "Weather location has no authoritative facility identity."),
        ("SELECT 1 FROM weather_areas a LEFT ANTI JOIN weather_locations l "
         "ON a.location_id=l.location_id", "Weather area has no registered representative location."),
        ("SELECT 1 FROM weather_latest_forecasts f LEFT ANTI JOIN weather_locations l "
         "ON f.location_id=l.location_id WHERE f.target_kind='location'",
         "Location forecast has no registered location."),
        ("SELECT 1 FROM weather_latest_forecasts f LEFT ANTI JOIN weather_areas a "
         "ON f.area_id=a.area_id WHERE f.target_kind='area'",
         "Area forecast has no registered weather area."),
    ):
        reject(query, message)


def verify_project_relationship_sources(
        spark, sql_connection, equipment_table="silver_equipment", *, require_current_weather=True):
    """Read-only integrity gate; False never attests weather coverage or freshness."""
    from datetime import datetime, timedelta, timezone
    import math

    if type(require_current_weather) is not bool:
        raise RuntimeError("require_current_weather must be an explicit boolean.")
    core_tables = _project_core_source_tables(spark, equipment_table)
    if spark.conf.get("spark.sql.session.timeZone") not in ("UTC", "Etc/UTC"):
        raise RuntimeError("Relationship-source validation requires a UTC Spark session.")
    now = datetime.now(timezone.utc)
    cap = 100000
    counts = {}

    def lake(table, mandatory=True):
        try:
            frame = spark.table(table)
            rows = [row.asDict(recursive=True) for row in frame.limit(cap + 1).collect()]
        except Exception as exc:
            raise RuntimeError(f"Cannot read required relationship source {table}.") from exc
        if len(rows) > cap:
            raise RuntimeError(f"Relationship source {table} exceeds bounded validation limit {cap}.")
        if mandatory and not rows:
            raise RuntimeError(f"Empty mandatory relationship source {table}.")
        counts[table] = len(rows)
        return rows

    def key(row, column, label):
        value = row.get(column)
        if value is not None and value.__class__.__name__ == "UUID":
            value = str(value)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise RuntimeError(f"Malformed relationship key {label}.{column}.")
        return value

    def index(rows, columns, label):
        result = {}
        for row in rows:
            identity = tuple(key(row, column, label) for column in columns)
            if identity in result:
                raise RuntimeError(f"Nonunique relationship key in {label}.")
            result[identity] = row
        return result

    def reference(row, column, target, label, nullable=False):
        if nullable and row.get(column) is None:
            return None
        identity = key(row, column, label)
        found = target.get((identity,))
        if found is None:
            raise RuntimeError(f"Orphan relationship {label}.{column}.")
        return found

    def timestamp(row, column, label):
        value = row.get(column)
        try:
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    raise ValueError("Text timestamp must carry UTC")
            elif isinstance(value, datetime) and value.tzinfo is None:
                # Spark TimestampType values are naive under the verified UTC session.
                value = value.replace(tzinfo=timezone.utc)
            if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
                raise ValueError("Expected UTC")
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"Malformed UTC timestamp {label}.{column}.") from exc
        return value

    facilities = index(lake(core_tables["facilities"]), ("facility_id",), "facilities")
    systems = index(lake(core_tables["systems"]), ("system_id",), "systems")
    equipment = index(lake(core_tables["equipment"]), ("equipment_id",), "equipment")
    instruments = index(lake(core_tables["instruments"]), ("instrument_id",), "instruments")
    signals = index(lake(core_tables["signal_master"]), ("opcua_node_id",), "signals")
    types = index(lake("ontology_equipment_types"), ("equipment_type_code",), "EquipmentType")
    eligible = set()
    for row in systems.values():
        reference(row, "facility_id", facilities, "systems")
    for row in equipment.values():
        facility = key(row, "facility_id", "equipment")
        reference(row, "facility_id", facilities, "equipment")
        system = reference(row, "system_id", systems, "equipment")
        if system["facility_id"] != facility:
            raise RuntimeError("Conflicting equipment system/facility relationship.")
        equipment_type = reference(row, "equipment_type_code", types, "equipment")
        if key(row, "equipment_type_name", "equipment") != key(
                equipment_type, "equipment_type_name", "EquipmentType"):
            raise RuntimeError("Conflicting authoritative equipment type name.")
        if "is_active" in row:
            if row["is_active"] is not None and type(row["is_active"]) is not bool:
                raise RuntimeError("Malformed equipment active status.")
            active = row["is_active"] is True
        elif "status" in row and (row["status"] is None or isinstance(row["status"], str)):
            active = row["status"] is not None and row["status"].upper() == "ACTIVE"
        else:
            raise RuntimeError("Equipment lacks authoritative active status for weather eligibility.")
        if active:
            eligible.add(facility)
    if require_current_weather and not eligible:
        raise RuntimeError("No eligible active-equipment facilities for initial weather coverage.")
    for row in instruments.values():
        reference(row, "equipment_id", equipment, "instruments")
    for row in signals.values():
        instrument = reference(row, "instrument_id", instruments, "signals")
        reference(row, "equipment_id", equipment, "signals")
        if row["equipment_id"] != instrument["equipment_id"]:
            raise RuntimeError("Conflicting signal instrument/equipment relationship.")

    cursor = sql_connection.cursor()
    try:
        for entity, table in SQL_PROJECT_ENTITIES.items():
            columns = ["id", "equipmentType"] if entity == "SparePart" else ["id", "equipmentId"]
            columns += [name for name in ("instrumentId", "opcuaNodeId") if name in OPERATIONAL_COLUMNS[table]]
            try:
                cursor.execute(f"SELECT TOP ({cap + 1}) " + ", ".join(f"[{c}]" for c in columns)
                               + f" FROM dbo.[{table}]")
                rows = []
                while len(rows) <= cap:
                    batch = cursor.fetchmany(min(1000, cap + 1 - len(rows)))
                    if not batch:
                        break
                    rows.extend(dict(zip(columns, values)) for values in batch)
            except Exception as exc:
                raise RuntimeError(f"Cannot read required SQL relationship source {table}.") from exc
            if len(rows) > cap:
                raise RuntimeError(f"Relationship source {table} exceeds bounded validation limit {cap}.")
            if not rows:
                raise RuntimeError(f"Empty mandatory SQL relationship source {table}.")
            counts[table] = len(rows)
            index(rows, ("id",), table)
            for row in rows:
                if entity == "SparePart":
                    reference(row, "equipmentType", types, table)
                    continue
                reference(row, "equipmentId", equipment, table)
                instrument = reference(row, "instrumentId", instruments, table, True)
                signal = reference(row, "opcuaNodeId", signals, table, True)
                if instrument and instrument["equipment_id"] != row["equipmentId"]:
                    raise RuntimeError(f"Conflicting optional instrument/equipment relationship in {table}.")
                if signal and (signal["equipment_id"] != row["equipmentId"]
                               or instrument and signal["instrument_id"] != row["instrumentId"]):
                    raise RuntimeError(f"Conflicting optional signal/equipment relationship in {table}.")
    finally:
        cursor.close()

    sources = index(lake("weather_sources"), ("source_id",), "WeatherSource")
    locations = index(lake("weather_locations"), ("location_id",), "WeatherLocation")
    areas = index(lake("weather_areas"), ("area_id",), "WeatherArea")
    for row in locations.values():
        reference(row, "location_id", facilities, "WeatherLocation")
    for row in areas.values():
        reference(row, "location_id", locations, "WeatherArea")
        try:
            metadata = json.loads(row.get("metadata_json", ""))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("WeatherArea lacks authoritative metadata location identity.") from exc
        if not isinstance(metadata, dict) or metadata.get("location_id") != row["location_id"]:
            raise RuntimeError("Conflicting WeatherArea projected location identity.")
    if require_current_weather and not eligible.issubset({identity[0] for identity in locations}):
        raise RuntimeError("Missing initial weather LOCATION coverage for eligible facilities.")
    if require_current_weather and (
            ("ukmet",) not in sources or sources[("ukmet",)].get("active") is not True):
        raise RuntimeError("Missing active registered UKMet source.")

    variables = ("precipitation", "temperature", "pressure", "relative_humidity", "dew_point",
                 "solar_radiation", "wind_speed", "wind_gust", "wind_direction")

    def usable(row):
        return any(isinstance(row.get(column), (int, float)) and not isinstance(row[column], bool)
                   and math.isfinite(row[column]) for column in variables)

    forecast_coverage = set()
    forecast_rows = lake("weather_latest_forecasts", mandatory=require_current_weather)
    forecast_keys = set()
    stale_forecasts = 0
    latest_issue = None
    for row in forecast_rows:
        reference(row, "source_id", sources, "WeatherLatestForecast")
        kind = key(row, "target_kind", "WeatherLatestForecast")
        target = key(row, "target_id", "WeatherLatestForecast")
        if kind not in ("location", "area"):
            raise RuntimeError("Unsupported weather forecast target_kind.")
        expected_location = target if kind == "location" else None
        expected_area = target if kind == "area" else None
        if row.get("location_id") != expected_location or row.get("area_id") != expected_area:
            raise RuntimeError("Conflicting weather forecast cross-kind projected keys.")
        reference(row, "location_id" if kind == "location" else "area_id",
                  locations if kind == "location" else areas, "WeatherLatestForecast")
        issue = timestamp(row, "reference_time_utc", "WeatherLatestForecast")
        valid = timestamp(row, "valid_time_utc", "WeatherLatestForecast")
        if (issue > now + timedelta(minutes=5) or valid < issue
                or row["source_id"] == "ukmet" and valid > issue + timedelta(days=14)):
            raise RuntimeError("Invalid weather forecast issue/valid-time bounds.")
        identity = (row["source_id"], kind, target, issue, valid)
        if identity in forecast_keys:
            raise RuntimeError("Nonunique weather forecast natural key.")
        forecast_keys.add(identity)
        key(row, "ontology_key", "WeatherLatestForecast")
        current = (now - timedelta(hours=13) <= issue
                   and now <= valid <= now + timedelta(hours=12))
        if not current:
            stale_forecasts += 1
        if row["source_id"] == "ukmet":
            latest_issue = max(latest_issue, issue) if latest_issue else issue
            if kind == "location" and current and usable(row):
                forecast_coverage.add(target)
    if require_current_weather and not eligible.issubset(forecast_coverage):
        raise RuntimeError("Missing usable current UKMet forecast coverage for eligible facilities.")

    observation_rows = lake("weather_latest_observations", mandatory=False)
    observation_keys = set()
    observation_coverage = set()
    observation_available = set()
    stale_observations = set()
    for row in observation_rows:
        reference(row, "source_id", sources, "WeatherLatestObservation")
        reference(row, "location_id", locations, "WeatherLatestObservation")
        observed = timestamp(row, "observed_at_utc", "WeatherLatestObservation")
        if observed > now + timedelta(minutes=5):
            raise RuntimeError("Weather observation timestamp is in the future.")
        identity = (row["source_id"], row["location_id"], observed)
        if identity in observation_keys:
            raise RuntimeError("Nonunique weather observation natural key.")
        observation_keys.add(identity)
        key(row, "ontology_key", "WeatherLatestObservation")
        if row["source_id"] == "ukmet":
            observation_available.add(row["location_id"])
            if observed >= now - timedelta(hours=24) and usable(row):
                observation_coverage.add(row["location_id"])
            elif usable(row):
                stale_observations.add(row["location_id"])
    for label, rows in (("forecast", forecast_rows), ("observation", observation_rows)):
        index(rows, ("ontology_key",), "weather " + label)
    return {
        "status": "verified" if require_current_weather else "integrity_verified_weather_not_attested",
        "scope": ("source_keys_and_initial_weather_only" if require_current_weather
                  else "source_keys_only_weather_not_attested"),
        "weather_coverage_status": "verified" if require_current_weather else "not_attested",
        "weather_freshness_status": "verified" if require_current_weather else "not_attested",
        "weather_readiness_attested": require_current_weather,
        "checked_at_utc": now.isoformat(), "row_limit_per_source": cap, "source_row_counts": counts,
        "core_source_tables": core_tables,
        "eligible_facility_count": len(eligible),
        "ukmet_forecast_covered_facility_count": len(eligible & forecast_coverage),
        "ukmet_latest_issue_utc": latest_issue.isoformat() if latest_issue else None,
        "forecast_max_issue_age_hours": 13, "observation_max_age_hours": 24,
        "forecast_coverage_horizon_hours": 12,
        "ukmet_observation_covered_facility_count": len(eligible & observation_coverage),
        "ukmet_observation_available_facility_count": len(eligible & observation_available),
        "ukmet_observation_gap_facility_count": len(eligible - observation_coverage),
        "ukmet_stale_observation_facility_count": len((eligible & stale_observations) - observation_coverage),
        "noncurrent_forecast_row_count": stale_forecasts,
        "observations_required": False,
    }


def curate_project_catalog_entities(parts):
    """Retire only managed catalog/ingestion types, never their physical data."""
    indexed = {part["path"]: part for part in parts}
    if len(indexed) != len(parts):
        raise RuntimeError("Catalog curation requires unambiguous definition paths.")
    decode = lambda part: base64.b64decode(part["payload"]).decode("utf-8")
    texts = {path: decode(part) for path, part in indexed.items()}
    retired_edges = {
        "WeatherIngestionRun_from_source": ("WeatherIngestionRun", "WeatherSource"),
        "InstrumentClassification_for_system": ("InstrumentClassification", "systems"),
    }
    removed = set()
    removed_entities = set()
    catalog = _project_semantics()
    for entity, physical in PROJECT_CATALOG_ENTITIES.items():
        paths = (f"entities/{entity}.tmdl", f"tables/HydroProject_{entity}.tmdl")
        if not any(path in texts for path in paths):
            continue
        if not all(path in texts for path in paths):
            raise RuntimeError(f"Cannot retire incomplete managed catalog {entity}.")
        for path in paths:
            if not re.search(r"(?m)^\tannotation HydroProjectExtension = 1\s*$", texts[path]):
                raise RuntimeError(f"Cannot retire unowned catalog definition {path}.")
            annotations = set(re.findall(r"(?m)^\s+annotation (\w+) =", texts[path]))
            if annotations - {"HydroProjectExtension", "domain", "modelRole", "preferredUsage",
                              "queryHint", "agentGuide"}:
                raise RuntimeError(f"Custom annotations in {path} require an explicit migration.")
            properties = set(re.findall(r"(?m)^\t(?:property|column) (\w+)\s*$", texts[path]))
            if properties - set(catalog[entity]["properties"]):
                raise RuntimeError(f"Custom properties in {path} require an explicit migration.")
        if not re.search(r"(?m)^\tbackingTable: HydroProject_" + entity + r"\s*$", texts[paths[0]]):
            raise RuntimeError(f"Cannot retire rebound catalog {entity}.")
        if not re.search(r"(?m)^\s+entityName: " + physical + r"\s*$", texts[paths[1]]):
            raise RuntimeError(f"Cannot retire catalog {entity} with a different physical source.")
        for path, text in texts.items():
            if path in paths or path == "model.tmdl":
                continue
            for block in re.split(r"(?m)(?=^(?:entityRelationship|rule) )", text):
                if re.search(r"(?m)^\s*(?:fromEntity|toEntity|ruleReferencedEntity):? " + entity + r"\s*$", block):
                    name = block.splitlines()[0].split(" ", 1)[-1]
                    if path != "entityRelationships.tmdl" or name not in retired_edges:
                        raise RuntimeError(f"Custom reference to catalog {entity} requires an explicit migration.")
        removed.update(paths)
        removed_entities.add(entity)
        texts["model.tmdl"] = re.sub(
            r"(?m)^ref (?:entity " + entity + r"|table HydroProject_" + entity + r")[ \t]*\r?\n?",
            "", texts["model.tmdl"])
    owned_edges = {name for name, pair in retired_edges.items() if pair[0] in removed_entities}
    for name in owned_edges:
        block = re.search(
            r"(?ms)^entityRelationship " + name + r"[ \t]*\r?\n.*?(?=^\S|\Z)",
            texts.get("entityRelationships.tmdl", ""))
        if block and any(not re.search(r"(?m)^\t" + field + ": " + entity + r"\s*$", block[0])
                         for field, entity in zip(("fromEntity", "toEntity"), retired_edges[name])):
            raise RuntimeError(f"Catalog relationship {name} has custom endpoints; explicit migration required.")
    for path, prefix, names in (
        ("entityRelationships.tmdl", "entityRelationship", owned_edges),
        ("relationships.tmdl", "relationship", {"HydroProject_" + name for name in owned_edges}),
    ):
        if path in texts:
            for name in names:
                texts[path] = re.sub(
                    r"(?ms)^" + prefix + " " + re.escape(name) + r"[ \t]*\r?\n.*?(?=^\S|\Z)",
                    "", texts[path])
    return [
        original if texts[path] == decode(original) else {
            **original, "payload": base64.b64encode(texts[path].encode()).decode()}
        for path, original in indexed.items() if path not in removed
    ]


def project_identity_expression(keys):
    """Encode every natural-key member, including UTC timestamps, before hashing."""
    if not keys or any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key) for key in keys):
        raise RuntimeError("Project identity requires explicit safe source columns.")
    members = ", ".join(f"'{key}', `{key}`" for key in keys)
    return ("sha2(to_json(named_struct(" + members + "), map("
            "'timeZone', 'UTC', 'timestampFormat', 'yyyy-MM-dd HH:mm:ss.SSSSSS', "
            "'ignoreNullFields', 'false')), 256)")

def project_source_expressions(workspace, lakehouse, database):
    return [
        f'expression {name} =\n\t\tlet\n\t\t    Source = AzureStorage.DataLake('
        f'"https://onelake.dfs.fabric.microsoft.com/{workspace}/{item}", [HierarchicalNavigation=true])'
        "\n\t\tin\n\t\t    Source\n"
        for name, item in (("HydroProjectOperations", database), ("HydroProjectLakehouse", lakehouse))
    ]


def project_ontology_source_metadata(parts, workspace, lakehouse, database):
    """Pin authoritative source metadata on shared expressions, as Fabric retains it."""
    workspace_id = workspace.get("id")
    sources = {item.get("id"): item for item in (lakehouse, database)}
    if (not workspace_id or not workspace.get("displayName")
            or lakehouse.get("type") != "Lakehouse" or database.get("type") != "SQLDatabase"
            or len(sources) != 2 or None in sources):
        raise RuntimeError("Ontology source metadata requires authoritative workspace, Lakehouse and SQL identities.")
    for item in sources.values():
        if item.get("workspaceId", workspace_id) != workspace_id:
            raise RuntimeError("Ontology source metadata belongs to another workspace.")
    decode = lambda part: base64.b64decode(part["payload"]).decode("utf-8").replace("\r\n", "\n")
    indexed = {part["path"]: part for part in parts}
    if len(indexed) != len(parts) or "expressions.tmdl" not in indexed:
        raise RuntimeError("Ontology source metadata requires unambiguous shared expressions.")
    expression_items = {}
    domains = {name: lakehouse["id"] for name in
               ("facilities", "systems", "equipment", "instruments", "signal_master", *WEATHER_PROJECT_ENTITIES)}
    domains.update({name: database["id"] for name in SQL_PROJECT_ENTITIES})
    for entity, item_id in domains.items():
        text = decode(indexed[f"entities/{entity}.tmdl"])
        backing = re.findall(r"(?m)^\s*backingTable:\s*(.+?)\s*$", text)
        if len(backing) != 1:
            raise RuntimeError(f"Cannot pin an ambiguous ontology backing table for {entity}.")
        table = backing[0].strip("'").replace("''", "'")
        partition = decode(indexed[f"tables/{table}.tmdl"])
        expressions = re.findall(r"(?m)^\s*expressionSource:\s*(.+?)\s*$", partition)
        if len(expressions) != 1:
            raise RuntimeError(f"Cannot pin an ambiguous ontology source for {entity}.")
        name = expressions[0].strip("'").replace("''", "'")
        if name in expression_items and expression_items[name] != item_id:
            raise RuntimeError("An ontology expression cannot identify two different source items.")
        expression_items[name] = item_id
    original = decode(indexed["expressions.tmdl"])
    seen = set()
    def pin(match):
        block = match[0]
        name = match[1].strip("'").replace("''", "'")
        if name not in expression_items:
            return block
        if name in seen:
            raise RuntimeError(f"Duplicate ontology source expression {name}.")
        seen.add(name)
        item = sources[expression_items[name]]
        expected = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{item['id']}"
        urls = re.findall(r'AzureStorage\.DataLake\(\s*"([^"]+)"', block)
        if len(urls) != 1 or urls[0].rstrip("/").casefold() != expected.casefold():
            raise RuntimeError(f"Ontology expression {name} does not match its authoritative source identity.")
        properties = item.get("properties", {})
        sql_endpoint = (properties.get("sqlEndpointProperties", {}).get("connectionString")
                        if item["type"] == "Lakehouse" else properties.get("serverFqdn"))
        sql_database = item.get("displayName") if item["type"] == "Lakehouse" else properties.get("databaseName")
        values = {
            "ONT_WorkspaceId": workspace_id, "ONT_ItemId": item["id"], "ONT_ItemKind": item["type"],
            "ONT_ItemName": item.get("displayName"), "ONT_WorkspaceName": workspace["displayName"],
            "ONT_SqlEndpoint": sql_endpoint, "ONT_SqlDatabase": sql_database,
        }
        for key, value in values.items():
            if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
                raise RuntimeError(f"Missing or invalid authoritative ontology source metadata: {key}.")
            pattern = r"(?m)^\tannotation " + key + r" = (.+?)[ \t]*$"
            existing = re.findall(pattern, block)
            if len(existing) > 1:
                raise RuntimeError(f"Duplicate ontology source metadata {name}.{key}.")
            if existing:
                if key in ("ONT_WorkspaceId", "ONT_ItemId", "ONT_ItemKind") and existing[0].casefold() != value.casefold():
                    raise RuntimeError(f"Conflicting ontology source metadata {name}.{key}.")
                block = re.sub(pattern, lambda _match: f"\tannotation {key} = {value}", block)
            else:
                block = block.rstrip() + f"\n\n\tannotation {key} = {value}\n"
        pinned = re.findall(r"(?m)^\tannotation ONT_PinnedAtUtc = (.+?)[ \t]*$", block)
        if len(pinned) > 1:
            raise RuntimeError(f"Duplicate ontology source pin timestamp for {name}.")
        if not pinned:
            block = block.rstrip() + "\n\n\tannotation ONT_PinnedAtUtc = " + time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n"
        return block.rstrip() + "\n\n"
    updated = re.sub(r"(?ms)^expression (.+?) =\n.*?(?=^expression |\Z)", pin, original)
    if seen != set(expression_items):
        raise RuntimeError("An ontology source expression is missing or cannot be verified.")
    if updated == original:
        return parts
    result = []
    for part in parts:
        if part["path"] == "expressions.tmdl":
            part = {**part, "payload": base64.b64encode(updated.encode()).decode()}
        result.append(part)
    return result


def project_ontology_extension(parts, workspace, lakehouse, database, weather_columns, sql_columns, verify_existing=None):
    """Add verified source-backed domains without changing existing core definitions."""
    parts = curate_project_catalog_entities(parts)
    by_path = {part["path"]: dict(part) for part in parts}
    if len(by_path) != len(parts) or "database.tmdl" not in by_path or "model.tmdl" not in by_path:
        raise RuntimeError("Project extension requires one unambiguous generation-2 TMDL contract.")
    decode = lambda part: base64.b64decode(part["payload"]).decode("utf-8").replace("\r\n", "\n")
    texts = {path: decode(part) for path, part in by_path.items()}
    core_tables = {}
    for entity in ("facilities", "systems", "equipment", "instruments", "signal_master"):
        text = texts.get(f"entities/{entity}.tmdl", "")
        matches = re.findall(r"(?m)^\s*backingTable:\s*([A-Za-z_][A-Za-z_0-9]*)\s*$", text)
        if len(matches) != 1:
            raise RuntimeError(f"Core {entity} backing identity is not compatible with an additive project extension.")
        core_tables[entity] = matches[0]
    additions = {}
    entities = []
    tables = []
    source_columns = {}
    def add_entity(name, physical, columns, keys, expression):
        if not set(keys).issubset(columns):
            raise RuntimeError(f"Project ontology {name} is missing source key columns {set(keys) - set(columns)}.")
        if len(keys) > 1:
            if columns.get("ontology_key") != "string":
                raise RuntimeError(f"Project ontology {name} requires its verified physical ontology_key.")
            keys = ("ontology_key",)
        alias = "HydroProject_" + name
        entity = [f"entity {name}", f"\tbackingTable: {alias}"]
        entity.extend(f"\tkeyProperty: {key}" for key in keys)
        table = [f"table {alias}"]
        for column, kind in columns.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", column):
                raise RuntimeError(f"Unsupported project source column identifier: {column!r}")
            entity.extend((f"\tproperty {column}", f"\t\tdataType: {kind}",
                           "\t\tbackingConfiguration", f"\t\t\tvalueColumn: {alias}.{column}"))
            table.extend((f"\tcolumn {column}", f"\t\tdataType: {kind}", f"\t\tsourceColumn: {column}"))
        table.extend((f"\tpartition {alias} = entity", "\t\tmode: directLake", "\t\tsource",
                      f"\t\t\tentityName: {physical}", "\t\t\tschemaName: dbo",
                      f"\t\t\texpressionSource: {expression}"))
        entity.append("\tannotation HydroProjectExtension = 1")
        table.append("\tannotation HydroProjectExtension = 1")
        additions[f"entities/{name}.tmdl"] = "\n".join(entity) + "\n"
        additions[f"tables/{alias}.tmdl"] = "\n".join(table) + "\n"
        entities.append(name)
        tables.append(alias)
        source_columns[name] = set(columns)
        return alias
    aliases = {}
    for name, physical in SQL_PROJECT_ENTITIES.items():
        aliases[name] = add_entity(name, physical, sql_columns[physical], ("id",), "HydroProjectOperations")
    for name, (physical, keys) in WEATHER_PROJECT_ENTITIES.items():
        aliases[name] = add_entity(name, physical, weather_columns[physical], keys, "HydroProjectLakehouse")
    relationships = []
    semantic = []
    def edge(name, source, target, source_key, target_key):
        for entity, column in ((source, source_key), (target, target_key)):
            if entity in source_columns and column not in source_columns[entity]:
                raise RuntimeError(f"Relationship {name} requires verified source column {entity}.{column}.")
        source_table = aliases.get(source, core_tables.get(source))
        target_table = aliases.get(target, core_tables.get(target))
        physical_name = "HydroProject_" + name
        relationships.append(
            f"relationship {physical_name}\n\tfromColumn: {source_table}.{source_key}\n"
            f"\ttoColumn: {target_table}.{target_key}\n")
        semantic.append(
            f"entityRelationship {name}\n\tfromEntity: {source}\n\ttoEntity: {target}\n"
            f"\tbackingConfiguration\n\t\trelationship: {physical_name}\n")
    for name in ("WorkOrder", "Inspection", "Asset3DModel", "MaintenanceNotification"):
        edge(f"{name}_for_equipment", name, "equipment", "equipmentId", "equipment_id")
    edge("WorkOrder_for_instrument", "WorkOrder", "instruments", "instrumentId", "instrument_id")
    for name in ("WorkOrder", "Inspection", "MaintenanceNotification"):
        edge(f"{name}_for_signal", name, "signal_master", "opcuaNodeId", "opcua_node_id")
    edge("WeatherLatestObservation_at_location", "WeatherLatestObservation", "WeatherLocation", "location_id", "location_id")
    edge("WeatherLatestObservation_from_source", "WeatherLatestObservation", "WeatherSource", "source_id", "source_id")
    edge("WeatherLatestForecast_from_source", "WeatherLatestForecast", "WeatherSource", "source_id", "source_id")
    edge("WeatherLocation_for_facility", "WeatherLocation", "facilities", "location_id", "facility_id")
    edge("WeatherArea_at_location", "WeatherArea", "WeatherLocation", "location_id", "location_id")
    edge("WeatherLatestForecast_at_location", "WeatherLatestForecast", "WeatherLocation", "location_id", "location_id")
    edge("WeatherLatestForecast_for_area", "WeatherLatestForecast", "WeatherArea", "area_id", "area_id")
    edge("equipment_of_type", "equipment", "EquipmentType", "equipment_type_code", "equipment_type_code")
    edge("SparePart_for_equipment_type", "SparePart", "EquipmentType", "equipmentType", "equipment_type_code")
    for path, wanted in additions.items():
        if path in texts and texts[path] != wanted:
            # Only explicit, source-verified columns may extend an already bound managed entity/table.
            if path in {
                "entities/WeatherArea.tmdl", "tables/HydroProject_WeatherArea.tmdl",
                "entities/WeatherLatestForecast.tmdl", "tables/HydroProject_WeatherLatestForecast.tmdl",
            }:
                kind = "property" if path.startswith("entities/") else "column"
                existing_names = set(re.findall(r"(?m)^\t" + kind + r" (\w+)\s*$", texts[path]))
                blocks = re.findall(r"(?ms)^\t" + kind + r" \w+\n.*?(?=^\t\S|^\S|\Z)", wanted)
                for block in blocks:
                    name = block.splitlines()[0].split()[-1]
                    if name not in existing_names and name in ("location_id", "area_id"):
                        texts[path] = texts[path].rstrip() + "\n\n" + block.rstrip() + "\n"
                by_path[path] = {**by_path[path], "payload": base64.b64encode(texts[path].encode()).decode()}
            if verify_existing:
                verify_existing([{"path": path, "payload": base64.b64encode(wanted.encode()).decode()}], [by_path[path]])
                continue
            # Readback adds lineage metadata. Never overwrite a pre-existing custom entity.
            normalized = "\n".join(line for line in texts[path].splitlines() if not line.strip().startswith("lineageTag:")) + "\n"
            if normalized != wanted:
                raise RuntimeError(f"Project ontology part {path} already exists with incompatible custom content.")
        elif path not in texts:
            texts[path] = wanted
    for path, blocks in (
        ("relationships.tmdl", relationships),
        ("entityRelationships.tmdl", semantic),
        ("expressions.tmdl", project_source_expressions(workspace, lakehouse, database)),
    ):
        current = texts.get(path, "")
        for block in blocks:
            header = block.splitlines()[0]
            if re.search(r"(?m)^" + re.escape(header) + r"\s*$", current):
                if verify_existing:
                    verify_existing(
                        [{"path": path, "payload": base64.b64encode(block.encode()).decode()}],
                        [{"path": path, "payload": base64.b64encode(current.encode()).decode()}])
                    continue
                existing = re.search(r"(?ms)^" + re.escape(header) + r"\s*\n(.*?)(?=^[^\s]|\Z)", current)
                clean = "\n".join(line for line in (header + "\n" + existing[1]).splitlines()
                                  if line.strip() and not line.strip().startswith("lineageTag:")) if existing else ""
                expected = "\n".join(line for line in block.splitlines() if line.strip())
                if clean != expected:
                    raise RuntimeError(f"Existing project binding {header} differs from the verified source.")
            else:
                current = current.rstrip() + "\n\n" + block
        texts[path] = current
    model = texts["model.tmdl"]
    for kind, names in (("table", tables), ("entity", entities)):
        for name in names:
            ref = f"ref {kind} {name}"
            if not re.search(r"(?m)^" + re.escape(ref) + r"\s*$", model):
                model = model.rstrip() + "\n" + ref + "\n"
    texts["model.tmdl"] = model
    for path, text in texts.items():
        if path not in by_path or text != decode(by_path[path]):
            by_path[path] = {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}
    return list(by_path.values())


def rebind_project_operational_source(parts, workspace, lakehouse, source, target):
    """Rebind only the verified operational expression after an explicit SQL copy."""
    if source.get("id") == target.get("id"):
        raise RuntimeError("Operational source replacement requires different SQL identities.")
    indexed = {part["path"]: part for part in parts}
    if len(indexed) != len(parts) or "expressions.tmdl" not in indexed:
        raise RuntimeError("Operational source replacement requires unambiguous ontology parts.")
    text = base64.b64decode(indexed["expressions.tmdl"]["payload"]).decode("utf-8").replace("\r\n", "\n")
    pattern = r"(?ms)^expression HydroProjectOperations =\n.*?(?=^expression |\Z)"
    blocks = list(re.finditer(pattern, text))
    if len(blocks) != 1:
        raise RuntimeError("Operational source replacement requires exactly one project SQL expression.")
    block = blocks[0][0]
    old_url = f"https://onelake.dfs.fabric.microsoft.com/{workspace['id']}/{source['id']}"
    new_url = f"https://onelake.dfs.fabric.microsoft.com/{workspace['id']}/{target['id']}"
    if new_url in block:
        return project_ontology_source_metadata(parts, workspace, lakehouse, target)
    project_ontology_source_metadata(parts, workspace, lakehouse, source)
    if block.count(old_url) != 1:
        raise RuntimeError("Existing operational ontology source identity must be verified before replacement.")
    replacement = block.replace(old_url, new_url)
    replacement = re.sub(r"(?m)^(\tannotation ONT_ItemId = ).+$",
                         lambda match: match[1] + target["id"], replacement)
    replacement = re.sub(r"(?m)^(\tannotation ONT_PinnedAtUtc = ).+$",
                         lambda match: match[1] + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), replacement)
    updated = text[:blocks[0].start()] + replacement + text[blocks[0].end():]
    result = [{**part, "payload": base64.b64encode(updated.encode()).decode()}
              if part["path"] == "expressions.tmdl" else part for part in parts]
    return project_ontology_source_metadata(result, workspace, lakehouse, target)


def operational_item(request, workspace, item_id):
    response = request("GET", f"workspaces/{workspace}/sqlDatabases/{item_id}")
    response.raise_for_status()
    item = response.json()
    if (item.get("id") != item_id or item.get("type") != "SQLDatabase"
            or item.get("workspaceId", workspace) != workspace):
        raise RuntimeError("Operational SQL item identity does not match the selected workspace.")
    properties = item.get("properties", {})
    if not properties.get("serverFqdn") or not properties.get("databaseName"):
        raise RuntimeError("Operational SQL item has no authoritative TDS connection identity.")
    return item


def _operational_inventory(request, workspace, folder_id=None):
    from urllib.parse import quote

    base = f"workspaces/{workspace}/items"
    if folder_id is not None:
        base += f"?rootFolderId={quote(folder_id, safe='')}&recursive=false"
    url = base
    items = []
    visited = set()
    while url:
        if url in visited or len(visited) >= 20:
            raise RuntimeError("Operational SQL inventory pagination did not terminate.")
        visited.add(url)
        response = request("GET", url)
        response.raise_for_status()
        page = response.json()
        items.extend(page["value"])
        if page.get("continuationToken"):
            separator = "&" if "?" in base else "?"
            url = base + separator + "continuationToken=" + quote(page["continuationToken"], safe="")
        else:
            url = page.get("continuationUri")
            if url:
                prefix = "https://api.fabric.microsoft.com/v1/"
                if not url.startswith(prefix + f"workspaces/{workspace}/items?"):
                    raise RuntimeError("Unexpected SQL inventory continuation workspace or host.")
                url = url[len(prefix):]
    return items


def ensure_operational_item(request, workspace, env_suffix, folder_id):
    wanted_name = operational_store_name(env_suffix)
    if not isinstance(folder_id, str) or not folder_id.strip():
        raise RuntimeError("Operational SQL requires the explicit setup folder identity.")
    candidates = active_operational_items([
        item for item in _operational_inventory(request, workspace)
        if item.get("displayName") in (wanted_name, OPERATIONAL_STORE_NAME)
    ])
    if len(candidates) > 1:
        raise RuntimeError("Multiple shared operational stores match the explicit setup name.")
    expected_name = candidates[0]["displayName"] if candidates else wanted_name
    if candidates:
        item = operational_item(request, workspace, candidates[0]["id"])
        if item.get("description") != OPERATIONAL_STORE_DESCRIPTION:
            raise RuntimeError("The shared SQL name is already used by an unrelated item; refusing to adopt it.")
    else:
        response = request("POST", f"workspaces/{workspace}/sqlDatabases", {
            "displayName": wanted_name, "description": OPERATIONAL_STORE_DESCRIPTION,
        })
        response.raise_for_status()
        if response.status_code == 202:
            operation_url = _fabric_operation_url(response, "https://api.fabric.microsoft.com/v1")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                operation = request("GET", operation_url)
                operation.raise_for_status()
                status = operation.json()["status"]
                if status == "Succeeded":
                    response = request("GET", _fabric_result_url(operation_url))
                    response.raise_for_status()
                    break
                if status in ("Failed", "Cancelled"):
                    raise RuntimeError(f"Shared operational SQL creation failed: {operation.text}")
                time.sleep(3)
            else:
                raise RuntimeError("Shared operational SQL creation exceeded five minutes; inspect the existing operation.")
        item_id = response.json().get("id")
        if not item_id:
            raise RuntimeError("Shared operational SQL creation did not return an item identity.")
        item = operational_item(request, workspace, item_id)
    item_path = f"workspaces/{workspace}/items/{item['id']}"
    if item["displayName"] != wanted_name:
        print(
            f"Fabric SQLDatabase names are immutable. Retaining {item['displayName']!r} "
            f"({item['id']}); {wanted_name!r} requires an explicitly approved replacement migration.")
    if not candidates or candidates[0].get("folderId") != folder_id:
        response = request("POST", item_path + "/move", {"targetFolderId": folder_id})
        response.raise_for_status()
    folder_items = _operational_inventory(request, workspace, folder_id)
    if not any(value["id"] == item["id"] and value.get("folderId") == folder_id for value in folder_items):
        raise RuntimeError("Shared operational SQL folder readback did not match setup.")
    verified = operational_item(request, workspace, item["id"])
    if verified.get("displayName") != expected_name or verified.get("description") != OPERATIONAL_STORE_DESCRIPTION:
        raise RuntimeError("Shared operational SQL naming/ownership readback did not match setup.")
    return verified


def operational_connection(item, sql_token):
    import pyodbc

    installed = pyodbc.drivers()
    driver = next((name for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")
                   if name in installed), None)
    if driver is None:
        raise RuntimeError("SQL setup requires Microsoft ODBC Driver 18 or 17. Fabric Runtime 2.0 includes Driver 18.")
    properties = item["properties"]
    token = sql_token().encode("utf-16-le")
    connection = pyodbc.connect(
        f"Driver={{{driver}}};Server={properties['serverFqdn']};Database={properties['databaseName']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60",
        attrs_before={1256: struct.pack("<I", len(token)) + token},
    )
    connection.timeout = 60
    return connection


def initialize_operational_schema(connection):
    cursor = connection.cursor()
    cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000")
    for table, columns in OPERATIONAL_COLUMNS.items():
        definitions = ", ".join(f"[{name}] {kind}" for name, kind in columns.items())
        cursor.execute(f"IF OBJECT_ID(N'dbo.{table}', N'U') IS NULL CREATE TABLE dbo.[{table}] ({definitions})")
        cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", "dbo", table)
        actual = {row[0] for row in cursor.fetchall()}
        if not set(columns).issubset(actual):
            raise RuntimeError(f"Shared operational table {table} is missing required columns; schema migration is required.")
    cursor.execute(
        "IF OBJECT_ID(N'dbo.HydroOperationalStoreState', N'U') IS NULL "
        "CREATE TABLE dbo.HydroOperationalStoreState ("
        "id int NOT NULL PRIMARY KEY CHECK (id=1), sourceItemId uniqueidentifier NOT NULL,"
        "targetItemId uniqueidentifier NOT NULL, receipt nvarchar(max) NOT NULL)"
    )
    connection.commit()


def _operational_projection(columns):
    return ",".join(
        f"CONVERT(nvarchar(33),[{column}],126) AS [{column}]" if kind.lower().startswith("datetime2")
        else f"[{column}]" for column, kind in columns.items())


def seed_operational_records(connection, seed_sql):
    """Apply unwrapped seed DML, verifying existing fields before committing."""
    connection.autocommit = False
    cursor = connection.cursor()
    try:
        cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000")
        def snapshot():
            result = {}
            for table, columns in OPERATIONAL_COLUMNS.items():
                cursor.execute(
                    f"SELECT {_operational_projection(columns)} FROM dbo.[{table}] "
                    "WITH (TABLOCKX,HOLDLOCK) ORDER BY id")
                result[table] = [tuple(row) for row in cursor.fetchmany(100_001)]
                if len(result[table]) > 100_000:
                    raise RuntimeError("Seed preservation readback exceeds 100,000 rows; use an explicit data migration.")
            return result
        before = snapshot()
        cursor.execute(seed_sql)
        after = snapshot()
        for table, rows in before.items():
            indexed = {str(row[0]): row for row in after[table]}
            if any(indexed.get(str(row[0])) != row for row in rows):
                raise RuntimeError(f"Seed changed existing {table} fields or identities; transaction rolled back.")
            if len(after[table]) < OPERATIONAL_DEMO_COUNTS[table]:
                raise RuntimeError(f"Seed left {table} below its demo population; transaction rolled back.")
        receipt = {
            "existing_records_preserved": True,
            "existing_record_sha256": hashlib.sha256(
                json.dumps(before, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
            "tables": {table: {"before": len(before[table]), "after": len(after[table])} for table in before},
        }
        connection.commit()
        return receipt
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def copy_operational_records(source, target, source_id, target_id):
    """Copy under legacy table locks, verify every field, then fail closed on old writes."""
    if source_id == target_id:
        raise RuntimeError("Legacy and independent operational SQL identities must differ.")
    source_cursor, target_cursor = source.cursor(), target.cursor()
    target_cursor.execute("SELECT sourceItemId,targetItemId,receipt FROM dbo.HydroOperationalStoreState WHERE id=1")
    state = target_cursor.fetchone()
    target.commit()
    expected_triggers = {f"HydroArchive_{table}" for table in OPERATIONAL_COLUMNS}
    source_cursor.execute("SELECT name FROM sys.triggers WHERE name LIKE 'HydroArchive[_]%'")
    triggers = {row[0] for row in source_cursor.fetchall()}
    if state:
        if str(state[0]).lower() != source_id.lower() or str(state[1]).lower() != target_id.lower():
            raise RuntimeError("Operational store migration receipt belongs to different SQL items.")
        if triggers != expected_triggers:
            raise RuntimeError("Operational copy exists but the legacy write boundary is incomplete; repair before deployment.")
        source.rollback()
        return json.loads(state[2])
    if triggers:
        raise RuntimeError("Legacy SQL has archive triggers without a matching shared-store receipt.")
    receipt = {"source_item_id": source_id, "target_item_id": target_id, "tables": {}}
    try:
        source_cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        target_cursor.execute("SET XACT_ABORT ON; SET LOCK_TIMEOUT 30000")
        for table, columns in OPERATIONAL_COLUMNS.items():
            source_cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=?", "dbo", table)
            actual = {row[0] for row in source_cursor.fetchall()}
            if actual != set(columns):
                raise RuntimeError(f"Legacy {table} has a different column set; explicit migration is required to preserve custom data.")
            selection = ",".join(f"[{column}]" for column in columns)
            # ODBC/Python datetime loses datetime2's seventh fractional digit.
            projection = _operational_projection(columns)
            source_cursor.execute(f"SELECT {projection} FROM dbo.[{table}] WITH (TABLOCKX,HOLDLOCK) ORDER BY id")
            rows = [tuple(row) for row in source_cursor.fetchmany(100_001)]
            if len(rows) > 100_000:
                raise RuntimeError(f"Legacy {table} exceeds the bounded in-memory migration limit.")
            target_cursor.execute(f"SELECT {projection} FROM dbo.[{table}] WITH (TABLOCKX,HOLDLOCK) ORDER BY id")
            existing = {str(row[0]): tuple(row) for row in target_cursor.fetchmany(100_001)}
            for row in rows:
                previous = existing.get(str(row[0]))
                if previous is not None and previous != row:
                    raise RuntimeError(f"Conflicting existing {table} row {row[0]}; no record will be overwritten.")
                if previous is None:
                    target_cursor.execute(
                        f"INSERT INTO dbo.[{table}] ({selection}) VALUES ({','.join('?' for _ in columns)})", *row)
            target_cursor.execute(f"SELECT {projection} FROM dbo.[{table}] ORDER BY id")
            copied = [tuple(row) for row in target_cursor.fetchmany(100_001)]
            if copied != rows:
                raise RuntimeError(f"Exact operational row readback failed for {table}; source remains authoritative.")
            digest = hashlib.sha256(json.dumps(rows, default=str, separators=(",", ":")).encode()).hexdigest()
            receipt["tables"][table] = {"rows": len(rows), "sha256": digest}
        for table in OPERATIONAL_COLUMNS:
            source_cursor.execute(
                f"CREATE TRIGGER dbo.[HydroArchive_{table}] ON dbo.[{table}] AFTER INSERT,UPDATE,DELETE AS "
                "BEGIN SET NOCOUNT ON; THROW 51001, 'Operational store moved. Reload the current Hydro Operations app.', 1; END"
            )
        target_cursor.execute(
            "INSERT INTO dbo.HydroOperationalStoreState(id,sourceItemId,targetItemId,receipt) VALUES(1,?,?,?)",
            source_id, target_id, json.dumps(receipt, sort_keys=True))
        # The app is not switched until NB11 succeeds. Keep the copy if the source
        # commit fails, but never report that partially established boundary as ready.
        target.commit()
        source.commit()
    except Exception:
        target.rollback()
        source.rollback()
        raise
    return receipt
