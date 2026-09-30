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
