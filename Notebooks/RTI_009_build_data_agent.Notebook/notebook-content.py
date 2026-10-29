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

# # 09 — Build a Data Agent over Ontology v2
#
# Uses `rti_demo_settings` to configure and publish a Fabric **Data Agent** with the
# selected Ontology v2, then verifies publication and source readback. Ontology context guides
# queries against its associated Lakehouse/Eventhouse; operational SQL guidance stays
# owned by NB11. Existing execution sources, custom selections and SQL guidance are
# retained. Setup does not invoke the agent or require a particular answer format.
#
# > **Known Fabric response:** After verified v2 publication, only the exact response
# > "This API version is not supported for the specified Ontology item" is reported as
# > a known product limitation. Other publication, source, runtime and readback failures
# > propagate. Successful publication alone does not verify cross-source answers.
# > A genuinely empty facility result is retried once after 10 seconds in a fresh
# > MCP session, only when independent live reference rows are nonempty. The retry
# > must return the exact reference records; repeated emptiness still fails setup.
#
# Configuration and verification details: [deployment guide](../../Raw/workspace-reset/README.md#prerequisites).


# CELL ********************

# =========================
# CELL 0
# Load shared settings written by RTI_001 / RTI_002
# =========================

from pyspark.sql import functions as F

settings_table_name = "rti_demo_settings"

spark.catalog.clearCache()
spark.sql(f"REFRESH TABLE {settings_table_name}")

settings = {
    row["setting_name"]: row["setting_value"]
    for row in spark.read.table(settings_table_name).collect()
}


def first_setting(*names, required: bool = False, default: str = None):
    """Return the first non-empty value among the given setting names."""
    for name in names:
        value = settings.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    if required:
        raise RuntimeError(f"Missing required setting. Tried: {list(names)}")
    return default


workspace_id = first_setting("workspace_id", required=True)
target_folder_id = first_setting("target_folder_id", required=True)

ontology_name = first_setting("ontology_name", "fabric_ontology_name", required=True)
lakehouse_id = first_setting("lakehouse_id", required=True)
lakehouse_name = first_setting("lakehouse_name", default="Energy_IQ_LakehouseRTI_V3")
kql_db_id = first_setting("fabric_kql_db_id", "kql_database_id", "kql_db_id", required=True)
kql_db_name = first_setting("fabric_kql_db_name", "kql_database_name", required=True)
kql_table_name = first_setting(
    "fabric_eventhouse_table", "eventhouse_table_name", "kql_table_name",
    default="OPCUAEvents",
)

# Key Vault names/URIs for SPN auth (written by RTI_001).
key_vault_uri = first_setting("key_vault_uri", required=True)
key_vault_tenant_id_secret = first_setting("key_vault_tenant_id_secret", required=True)
key_vault_client_id_secret = first_setting("key_vault_client_id_secret", required=True)
key_vault_client_secret_secret = first_setting("key_vault_client_secret_secret", required=True)

data_agent_name = first_setting("data_agent_name", required=True)

print("✅ Settings loaded")
print("   Workspace ID     :", workspace_id)
print("   Target folder ID :", target_folder_id)
print("   Ontology name    :", ontology_name)
print("   Lakehouse name   :", lakehouse_name)
print("   KQL DB name      :", kql_db_name)
print("   KQL table        :", kql_table_name)
print("   Data Agent name  :", data_agent_name)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 1
# Build the Data Agent definition, deploy the DataAgent item, persist settings
# =========================

import json
import time
import uuid
import base64
import re
from typing import Optional

import requests
import notebookutils  # Fabric notebook utility

# BEGIN ONTOLOGY SOURCE TOPOLOGY SUPPORT
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
# END ONTOLOGY SOURCE TOPOLOGY SUPPORT

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
DATA_AGENT_ITEM_TYPE = "DataAgent"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

# The ontology is attached as an "ontology" data source. The draft part folder
# is `ontology-<ontology_name>` — matching the layout Fabric writes for a
# published ontology-backed agent.
DATASOURCE_TYPE = "ontology"
DRAFT_STAGE_CONFIG_PATH = "Files/Config/draft/stage_config.json"
DATASOURCE_PATH = f"Files/Config/draft/{DATASOURCE_TYPE}-{ontology_name}/datasource.json"
LAKEHOUSE_DATASOURCE_TYPE = "lakehouse_tables"
LAKEHOUSE_DATASOURCE_PATH = (
    f"Files/Config/draft/lakehouse-tables-{lakehouse_name}/datasource.json"
)
KUSTO_DATASOURCE_TYPE = "kusto"
KUSTO_DATASOURCE_PATH = (
    f"Files/Config/draft/{KUSTO_DATASOURCE_TYPE}-{kql_db_name}/datasource.json"
)

STAGE_CONFIG_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/"
    "definition/stageConfiguration/1.0.0/schema.json"
)
DATASOURCE_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/"
    "definition/dataSource/1.0.0/schema.json"
)

DATA_AGENT_DESCRIPTION = (
    "Hydro operations agent for facilities, assets, OPC UA telemetry, signal quality, "
    "and work orders across the governed Lakehouse, Eventhouse, SQL database, and "
    f"verified Ontology v2 '{ontology_name}'."
)[:256]  # Fabric item description max length is 256 chars


# -------------------------------------------------------------------------
# SPN auth (Key Vault) + retry / LRO helpers
# -------------------------------------------------------------------------
_token_cache = {"token": None, "expires_at": 0.0}


def get_spn_access_token_for_fabric() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    tenant_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_tenant_id_secret)
    client_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_id_secret)
    client_secret = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_secret_secret)
    if not tenant_id or not client_id or not client_secret:
        raise Exception("Unable to fetch SPN credentials from Key Vault")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "https://api.fabric.microsoft.com/.default",
    }
    resp = requests.post(token_url, data=data, timeout=60)
    resp.raise_for_status()
    token_json = resp.json()
    _token_cache["token"] = token_json["access_token"]
    _token_cache["expires_at"] = now + int(token_json.get("expires_in", 3600)) - 60
    return _token_cache["token"]


def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_spn_access_token_for_fabric()}",
        "Content-Type": "application/json",
    }


def api_request(method: str, url: str, data=None, params=None, timeout=60):
    """Retry wrapper for Fabric REST calls (429 + 5xx)."""
    last_response = None
    for _ in range(MAX_RETRIES):
        response = requests.request(
            method=method, url=url, headers=get_headers(),
            json=data, params=params, timeout=timeout,
        )
        last_response = response
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
            print(f"Rate limited. Retrying in {wait}s.")
            time.sleep(wait)
            continue
        if response.status_code >= 500:
            print(f"Server error {response.status_code}. Retrying.")
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        return response
    return last_response


def wait_for_lro(operation_url: str) -> dict:
    """Poll a Fabric long-running-operation URL until terminal."""
    start = time.time()
    while time.time() - start < LRO_MAX_WAIT_SECONDS:
        response = api_request("GET", operation_url, timeout=60)
        if response.status_code not in (200, 202):
            raise RuntimeError(f"LRO polling failed: {response.status_code} {response.text}")
        try:
            result = response.json()
        except ValueError:
            result = {"status": "Unknown"}
        status = result.get("status", "Unknown")
        if status in ("Succeeded", "Completed"):
            return result
        if status in ("Failed", "Cancelled"):
            raise RuntimeError(json.dumps(result, indent=2))
        print(f"⏳ LRO status: {status}")
        time.sleep(LRO_POLL_INTERVAL_SECONDS)
    raise TimeoutError("LRO polling timed out.")


def encode_payload(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decode_payload(payload: str) -> dict:
    value = json.loads(base64.b64decode(payload, validate=True).decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Definition JSON payload must be an object; refusing an unreadable baseline.")
    return value


def definition_parts(envelope: dict, allow_empty: bool = False) -> list:
    if not isinstance(envelope, dict) or not isinstance(envelope.get("definition"), dict):
        raise RuntimeError("Invalid getDefinition envelope; expected a definition object.")
    parts = envelope["definition"].get("parts")
    if not isinstance(parts, list):
        raise RuntimeError("Invalid getDefinition parts; expected an explicit list.")
    if not parts and not allow_empty:
        raise RuntimeError("Empty existing agent definition cannot be safely replaced or verified.")
    paths = set()
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("path"), str) or not part["path"]:
            raise RuntimeError("Invalid definition part; expected an object with a non-empty path.")
        if part["path"] in paths:
            raise RuntimeError(f"Duplicate definition part path: {part['path']!r}.")
        paths.add(part["path"])
        if (part.get("payloadType") != "InlineBase64"
                or not isinstance(part.get("payload"), str) or not part["payload"]):
            raise RuntimeError(f"Unreadable definition part {part['path']!r}; expected InlineBase64 payload.")
        base64.b64decode(part["payload"], validate=True)
    return parts


# -------------------------------------------------------------------------
# Item discovery + Data Agent REST operations
# -------------------------------------------------------------------------
def find_item_by_name(display_name: str, item_type: Optional[str] = None) -> Optional[dict]:
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    params = {"type": item_type} if item_type else None
    response = api_request("GET", url, params=params)
    response.raise_for_status()
    data = response.json()
    for item in (data or {}).get("value", []) or []:
        if item.get("displayName") == display_name:
            return item
    return None


def resolve_ontology_id() -> str:
    """Return the id of the ontology item named `ontology_name` in the target folder."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    matches = []
    while url:
        response = api_request("GET", url)
        response.raise_for_status()
        body = response.json()
        matches.extend(
            it for it in body.get("value", [])
            if it.get("displayName") == ontology_name and it.get("type", "").lower() == "ontology"
        )
        url = body.get("continuationUri")
    if not matches:
        raise RuntimeError(f"Ontology '{ontology_name}' not found. Run 004–006 first.")
    in_folder = [it for it in matches if it.get("folderId") == target_folder_id]
    candidates = in_folder if target_folder_id else matches
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one Ontology {ontology_name!r} in configured target folder "
            f"{target_folder_id!r}; found {len(candidates)}."
        )
    return candidates[0]["id"]


def get_ontology_generation(ontology_id: str) -> int:
    response = api_request(
        "GET", f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/ontologies/{ontology_id}"
    )
    response.raise_for_status()
    generation = (response.json().get("properties") or {}).get("generation")
    if type(generation) is int and generation in (1, 2):
        return generation
    raise RuntimeError(
        f"Unknown live Ontology properties.generation {generation!r}. "
        "Verify the Ontology API response and update generation support before deploying agents."
    )


def agent_capability_policy(generation: int) -> dict:
    if type(generation) is not int or generation not in (1, 2):
        raise ValueError(f"Unsupported live ontology generation: {generation!r}")
    if generation != 2:
        return {
            "status": "blocked",
            "reason": "This deployment is v2-only. Replace the generation 1 ontology and agent sources "
                      "through an explicit migration before retrying; no legacy agent writes are allowed.",
        }
    return {"status": "allowed", "reason": ""}


def merge_settings_with_retry(source, attempts: int = 6) -> None:
    from delta.tables import DeltaTable

    for attempt in range(attempts):
        try:
            (DeltaTable.forName(spark, settings_table_name).alias("target")
             .merge(source.alias("source"), "target.setting_name = source.setting_name")
             .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())
            return
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            concurrent = any(marker in text for marker in (
                "ConcurrentAppendException", "ConcurrentWriteException",
                "MetadataChangedException", "ProtocolChangedException",
                "DELTA_CONCURRENT_",
            ))
            if not concurrent or attempt == attempts - 1:
                raise
            delay = 1.0 * (2 ** attempt)
            print(f"⚠️ Settings update conflicted with another notebook; retrying in {delay:.0f}s.")
            time.sleep(delay)
            spark.catalog.clearCache()
            spark.sql(f"REFRESH TABLE {settings_table_name}")


def persist_agent_status(status: str, reason: str, **details) -> None:
    global agent_deployment_result

    values = {"data_agent_deployment_status": status, "data_agent_deployment_reason": reason}
    for key, value in details.items():
        values[f"data_agent_{key}"] = json.dumps(value) if isinstance(value, dict) else str(value)
    source = spark.createDataFrame(
        [{"setting_name": k, "setting_value": v} for k, v in values.items()]
    ).withColumn("updated_utc", F.current_timestamp())
    merge_settings_with_retry(source)
    agent_deployment_result = {"status": status, "reason": reason, **details}


# BEGIN PROJECT ONTOLOGY CONTRACT
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
# END PROJECT ONTOLOGY CONTRACT


def require_complete_project_ontology(ontology_id):
    if (first_setting("project_ontology_status") != "verified"
            or first_setting("project_ontology_id") != ontology_id):
        raise RuntimeError("Run complete model preparation successfully before publishing this Data Agent.")
    return verify_project_ontology_contract(
        definition_parts(get_item_definition(ontology_id)), workspace_id, lakehouse_id,
        first_setting("operational_sql_db_id", required=True),
    )


def check_agent_capability() -> tuple:
    persist_agent_status(
        "checking", "Checking v2-only Data Agent capability; no readiness established.",
        publication_status="not_checked", runtime_status="not_checked",
        runtime_reason="", runtime_evidence="",
    )
    try:
        ontology_id = resolve_ontology_id()
        generation = get_ontology_generation(ontology_id)
        policy = agent_capability_policy(generation)
        if policy["status"] == "allowed":
            require_complete_project_ontology(ontology_id)
    except Exception as exc:
        persist_agent_status("failed", str(exc))
        raise
    if policy["status"] != "allowed":
        persist_agent_status(policy["status"], policy["reason"])
        raise RuntimeError(policy["reason"])
    return ontology_id, generation


def is_known_ontology_v2_product_limitation(reason: str) -> bool:
    return (
        "this api version is not supported for the specified ontology item"
        in str(reason).strip().lower()
    )


def require_v2_ontology(ontology_id: str) -> None:
    if get_ontology_generation(ontology_id) != 2:
        raise RuntimeError(
            f"Ontology {ontology_id!r} is not generation 2. This deployment is v2-only; "
            "migrate the legacy source explicitly before configuring or publishing agents."
        )


def is_ontology_context_path(path: str) -> bool:
    return path.startswith((
        "Files/Config/draft/ontologies/", "Files/Config/published/ontologies/",
    )) and path.endswith("/ontology.json")


def validate_agent_ontology_sources(
    parts: list, expected_ontology_id: str,
    require_draft: bool = False, require_published: bool = False,
) -> None:
    """Require every ontology source to reference the selected live v2 item, without removing parts."""
    if not expected_ontology_id:
        raise RuntimeError("A selected live v2 ontology id is required before verifying agent sources.")
    found_draft = False
    found_published = False
    for part in parts:
        path = part.get("path", "")
        context_source = is_ontology_context_path(path)
        if not context_source and not path.endswith("/datasource.json"):
            continue
        if part.get("payloadType") != "InlineBase64":
            raise RuntimeError(f"Cannot verify agent data source {path!r}: expected InlineBase64.")
        ds = json.loads(base64.b64decode(part["payload"], validate=True).decode("utf-8"))
        if not isinstance(ds, dict):
            raise RuntimeError(f"Cannot verify agent data source {path!r}: expected a JSON object.")
        if not context_source and (not isinstance(ds.get("type"), str) or not ds["type"].strip()):
            raise RuntimeError(f"Cannot verify agent data source {path!r}: missing source type.")
        if not context_source and ds["type"].lower() != "ontology":
            if "/ontology-" in path:
                raise RuntimeError(f"Malformed ontology data source {path!r}; no agent writes are allowed.")
            continue
        if not ds.get("artifactId") or ds.get("workspaceId") != workspace_id:
            raise RuntimeError(
                f"Ontology source {path!r} lacks a verifiable id in the target workspace; "
                "migrate it explicitly before configuring this v2-only agent."
            )
        if ds["artifactId"] != expected_ontology_id:
            raise RuntimeError(
                f"Agent ontology source {ds['artifactId']!r} does not match selected ontology "
                f"{expected_ontology_id!r}. Unrelated or legacy agent sources must be migrated explicitly."
            )
        require_v2_ontology(ds["artifactId"])
        found_draft = found_draft or path.startswith("Files/Config/draft/")
        found_published = found_published or path.startswith("Files/Config/published/")
    if require_draft and not found_draft:
        raise RuntimeError("No verified draft v2 ontology source exists. Run RTI_009 with verified product support first.")
    if require_published and not found_published:
        raise RuntimeError("No verified published v2 ontology source exists; publish success is unverified.")


def ontology_context_matches_source(source: dict, context: dict) -> bool:
    """Verify Fabric's datasource-to-ontology-context conversion without losing authored content."""
    schema = (
        "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/"
        "definition/ontology/1.0.0/schema.json"
    )
    if source.get("type") != "ontology" or context.get("$schema") != schema:
        return False
    expected = dict(source)
    expected.pop("$schema", None)
    expected.pop("type")
    for key, default in (("metadata", {}), ("dataSourceInstructions", None), ("userDescription", None)):
        if key in expected and expected[key] == default and key not in context:
            expected.pop(key)
    # Fabric records the descriptions supplied with the legacy entity selections as authored.
    if ("entitiesWithUserAuthoredDescriptions" not in expected
            and "entitiesWithUserAuthoredDescriptions" in context):
        expected["entitiesWithUserAuthoredDescriptions"] = [
            element["display_name"] for element in expected.get("elements", [])
            if element.get("description") and element.get("type") == "ontology.entity"
        ]
    return expected == {key: value for key, value in context.items() if key != "$schema"}


def verify_agent_source_readback(
    agent_id: str,
    ontology_id: str,
    expected_sources: Optional[dict] = None,
    published: bool = False,
) -> list:
    definition = get_item_definition(agent_id)
    parts = definition_parts(definition)
    validate_agent_ontology_sources(
        parts, ontology_id, require_draft=not published, require_published=published,
    )
    for draft_path, expected in (expected_sources or {}).items():
        path = draft_path.replace("/draft/", "/published/", 1) if published else draft_path
        source_part = next((part for part in parts if part.get("path") == path), None)
        matches = source_part is not None and decode_payload(source_part["payload"]) == expected
        if source_part is None and expected.get("type") == "ontology":
            prefix = "Files/Config/published/" if published else "Files/Config/draft/"
            contexts = [
                decode_payload(part["payload"]) for part in parts
                if part["path"].startswith(prefix) and is_ontology_context_path(part["path"])
            ]
            candidates = [
                context for context in contexts
                if context.get("artifactId") == expected.get("artifactId")
                and context.get("workspaceId") == expected.get("workspaceId")
            ]
            matches = len(candidates) == 1 and ontology_context_matches_source(expected, candidates[0])
        if not matches:
            raise RuntimeError(
                f"Data Agent source readback at {path!r} does not match the submitted source; "
                "no exact source or lossless ontology-context conversion was found; "
                "source retention and publish success are unverified."
            )
    return parts


def published_agent_sources(parts: list) -> dict:
    return {
        part["path"]: json.loads(base64.b64decode(part["payload"], validate=True).decode("utf-8"))
        for part in parts
        if part["path"].startswith("Files/Config/published/") and (
            part["path"].endswith("/datasource.json") or is_ontology_context_path(part["path"])
        )
    }


def facility_probe_reference() -> list:
    context = notebookutils.runtime.context
    if (context.get("defaultLakehouseId") != lakehouse_id
            or context.get("defaultLakehouseWorkspaceId") != workspace_id):
        raise RuntimeError("Readiness oracle must use the configured Lakehouse and workspace.")
    table = first_setting("silver_facilities_table", required=True)
    if not re.fullmatch(r"(?:dbo\.)?[A-Za-z_][A-Za-z0-9_]*", table):
        raise RuntimeError("Readiness oracle requires a local silver facilities table.")
    rows = (spark.read.table(table).select("facility_id", "facility_name")
            .orderBy("facility_id").limit(5).collect())
    return normalized_facility_rows([row.asDict() for row in rows])


def normalized_facility_rows(rows) -> list:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise ValueError("Expected one to five facility ID/name records, not a count or empty answer.")
    if any(not isinstance(row, dict) or set(row) != {"facility_id", "facility_name"}
           or any(not isinstance(value, str) or not value.strip() for value in row.values()) for row in rows):
        raise ValueError("Each facility record must contain exact nonempty string facility_id and facility_name.")
    if len({row["facility_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate facility IDs cannot verify the ontology query.")
    return sorted(rows, key=lambda row: row["facility_id"])


def facility_rows_from_mcp(result: dict) -> list:
    payload = result.get("structuredContent")
    csv_resources = [
        item["resource"]["text"] for item in result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "resource"
        and isinstance(item.get("resource"), dict)
        and item["resource"].get("mimeType") == "text/csv"
        and isinstance(item["resource"].get("text"), str)
    ]
    if csv_resources:
        import csv
        import io
        if len(csv_resources) != 1:
            raise ValueError("Readiness requires exactly one facility result attachment.")
        rows = list(csv.reader(io.StringIO(csv_resources[0]), strict=True))
        if (not rows or rows[0] != ["facility_id", "facility_name"]
                or any(len(row) != 2 for row in rows[1:])):
            raise ValueError("Facility CSV must contain only facility_id and facility_name columns.")
        records = [
            {"facility_id": row[0], "facility_name": row[1]} for row in rows[1:]
        ]
        actual = normalized_facility_rows(records) if records else []
        if payload is not None:
            structured = payload["facilities"] if isinstance(payload, dict) and set(payload) == {"facilities"} else payload
            structured_rows = [] if structured == [] else normalized_facility_rows(structured)
            if structured_rows != actual:
                raise ValueError("Structured facility rows disagree with the attached query result.")
        return actual
    if payload is None:
        text = "\n".join(
            item.get("text", "") if item.get("type") == "text"
            else item.get("resource", {}).get("text", "")
            for item in result.get("content", []) if isinstance(item, dict)
        ).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        payload = json.loads(text)
    if isinstance(payload, dict) and set(payload) == {"facilities"}:
        payload = payload["facilities"]
    return [] if payload == [] else normalized_facility_rows(payload)


def mcp_response_result(response, request_id):
    response.raise_for_status()
    if response.status_code != 200:
        raise RuntimeError(f"MCP request {request_id} returned HTTP {response.status_code} without a result.")
    if "text/event-stream" in response.headers.get("Content-Type", "").lower():
        messages = []
        for event in response.text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data and data != "[DONE]":
                messages.append(json.loads(data))
    else:
        messages = [response.json()]
    matches = [message for message in messages
               if isinstance(message, dict) and message.get("id") == request_id]
    if len(matches) != 1 or matches[0].get("jsonrpc") != "2.0":
        raise RuntimeError("MCP response has missing, mismatched, or duplicate JSON-RPC result.")
    message = matches[0]
    if message.get("error") is not None:
        raise RuntimeError("MCP JSON-RPC error: " + json.dumps(message["error"])[:4000])
    if not isinstance(message.get("result"), dict):
        raise RuntimeError("MCP response has no result object.")
    return message["result"]


def mcp_semantic_error(value):
    if isinstance(value, dict):
        if set(value) == {"facility_id", "facility_name"}:
            return None
        for key in ("error", "errorMessage", "failureReason"):
            if value.get(key):
                return str(value[key])[:4000]
        if value.get("isError") is True or str(value.get("status", "")).lower() in (
            "failed", "blocked", "error", "unavailable",
        ):
            return json.dumps(value)[:4000]
        for child in value.values():
            error = mcp_semantic_error(child)
            if error:
                return error
    elif isinstance(value, list):
        for child in value:
            error = mcp_semantic_error(child)
            if error:
                return error
    elif isinstance(value, str):
        text = value.strip()
        fenced = text.startswith("```")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        if text.startswith(("{", "[")):
            try:
                decoded, end = json.JSONDecoder().raw_decode(text)
            except json.JSONDecodeError:
                return "Malformed structured MCP response; execution cannot be verified."
            suffix = text[end:].strip()
            if fenced and suffix.startswith("```"):
                suffix = suffix[3:].strip()
            if suffix and not re.fullmatch(
                r"(?:File\(s\) [A-Za-z0-9_. ,()-]+ were generated as a result of the query\."
                r"|Source result: `[A-Za-z0-9_.-]+\.json` \(\d+ rows? returned\)\.)", suffix,
            ):
                return "Unexpected content after structured MCP response; execution cannot be verified."
            return mcp_semantic_error(decoded)
        if re.search(
            r"(?i)(ontology source unavailable|api version is not supported|"
            r"failed to (?:query|execute|access)|unable to (?:query|execute|access)|"
            r"cannot (?:query|execute|access)|^\s*error\b)", text,
        ):
            return text[:4000]
    return None


def probe_data_agent_ontology(agent_id: str, ontology_id: str, published_parts=None) -> dict:
    """Retry one transient empty answer, never substitute rows or suppress real failures."""
    outcome = probe_data_agent_ontology_once(agent_id, ontology_id, published_parts)
    evidence = outcome["evidence"]
    evidence["attempt"] = 1
    empty_answer = (
        outcome["status"] == "inconclusive"
        and evidence.get("returned_facilities") == []
        and bool(evidence.get("expected_facilities"))
    )
    if not empty_answer:
        return outcome
    prior = {
        "activity_id": evidence["activity_id"], "checked_at_utc": evidence["checked_at_utc"],
        "status": outcome["status"], "reason": outcome["reason"],
        "requests": evidence["requests"], "response": evidence["response"][:500],
        "response_truncated": evidence["response_truncated"] or len(evidence["response"]) > 500,
    }
    print("⚠️ Data Agent returned no facilities despite a nonempty live reference; "
          "retrying once in 10 seconds with a fresh MCP session.")
    time.sleep(10)
    outcome = probe_data_agent_ontology_once(agent_id, ontology_id, published_parts)
    outcome["evidence"].update({"attempt": 2, "prior_attempts": [prior]})
    return outcome


def probe_data_agent_ontology_once(agent_id: str, ontology_id: str, published_parts=None) -> dict:
    """Compare ontology-context facilities with independent live Lakehouse rows."""
    endpoint = f"{FABRIC_API_BASE}/v1/mcp/workspaces/{workspace_id}/dataagents/{agent_id}/agent"
    activity_id = str(uuid.uuid4())
    question = (
        f"Read-only source readiness check. Use ONLY the Ontology item {ontology_id} "
        f"in workspace {workspace_id} as semantic context and its associated Lakehouse "
        f"{lakehouse_id} for the static facilities binding. Source-native SQL on that associated "
        "Lakehouse is allowed; an ontology traversal endpoint is not required. "
        "Do not use operational SQL, KQL databases, unrelated sources, "
        "cached answers, examples in instructions, or estimates. Query the facilities entity: "
        "return the first five rows ordered by facility_id ascending (all rows if fewer than five). "
        "Return ONLY JSON: an array of objects with exactly facility_id and facility_name. "
        "If the associated source cannot be queried, return the exact error; do not substitute another source."
    )
    evidence = {
        "endpoint": endpoint, "agent_id": agent_id, "ontology_id": ontology_id,
        "workspace_id": workspace_id, "activity_id": activity_id, "question": question,
        "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requests": [],
    }
    try:
        if published_parts is None:
            published_parts = definition_parts(get_item_definition(agent_id))
        validate_agent_ontology_sources(published_parts, ontology_id, require_published=True)
        sources = published_agent_sources(published_parts)
        evidence.update({
            "verification": "ontology_context_facilities_smoke_v1", "lakehouse_id": lakehouse_id,
            "scope": "Ontology-context facilities functional smoke test; execution provenance, native ontology "
                     "traversal, telemetry and operational SQL are not attested.",
            "published_sources": {
                path: {key: source[key] for key in ("type", "workspaceId", "artifactId") if key in source}
                for path, source in sources.items()
            },
        })
        # MCP consumption uses the notebook user's context, not the provisioning SPN.
        token = notebookutils.credentials.getToken("pbi")
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
        if not isinstance(claims, dict) or not claims.get("scp") or claims.get("idtyp") == "app":
            raise RuntimeError("MCP readiness requires a delegated notebook-user token.")
        headers = {
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream", "ActivityId": activity_id,
        }
        next_id = 0

        def call(method, params):
            nonlocal next_id
            next_id += 1
            response = requests.post(
                endpoint, headers=headers,
                json={"jsonrpc": "2.0", "id": next_id, "method": method, "params": params},
                timeout=(15, 180), allow_redirects=False,
            )
            evidence["requests"].append({
                "method": method, "http_status": response.status_code,
                "request_id": response.headers.get("x-ms-request-id") or response.headers.get("requestId"),
            })
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            return mcp_response_result(response, next_id)

        initialized = call("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "hydro-ontology-readiness", "version": "1.0.0"},
        })
        version = initialized.get("protocolVersion")
        if not isinstance(version, str) or not version:
            raise RuntimeError("MCP initialization returned no protocol version.")
        headers["MCP-Protocol-Version"] = version
        notification = requests.post(
            endpoint, headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=(15, 30), allow_redirects=False,
        )
        notification.raise_for_status()
        if notification.status_code not in (200, 202, 204):
            raise RuntimeError(f"MCP initialization notification returned HTTP {notification.status_code}.")
        tools = call("tools/list", {})
        if tools.get("nextCursor"):
            raise RuntimeError("MCP tool listing is incomplete; cannot select a verified agent tool.")
        listed = tools.get("tools")
        if not isinstance(listed, list) or not all(isinstance(tool, dict) for tool in listed):
            raise RuntimeError("MCP tool listing is malformed.")
        expected_name = f"DataAgent_{data_agent_name}"
        selected = [tool for tool in listed if tool.get("name") == expected_name]
        if len(selected) != 1:
            raise RuntimeError(f"Expected exactly one MCP tool {expected_name!r}.")
        schema = selected[0].get("inputSchema", {})
        if (not isinstance(schema, dict) or schema.get("type") != "object"
                or not isinstance(schema.get("properties"), dict)
                or not isinstance(schema["properties"].get("userQuestion"), dict)
                or schema["properties"]["userQuestion"].get("type") != "string"
                or schema.get("required") != ["userQuestion"]):
            raise RuntimeError("MCP agent tool has an unsupported question contract.")
        evidence["tool"] = expected_name
        result = call("tools/call", {"name": expected_name, "arguments": {"userQuestion": question}})
        response_text = json.dumps(result)
        evidence["response"] = response_text[:2000]
        evidence["response_truncated"] = len(response_text) > 2000
        error = mcp_semantic_error(result)
        if error:
            return {"status": "failed", "reason": error, "evidence": evidence}
        expected = facility_probe_reference()
        evidence.update({
            "reference_table": first_setting("silver_facilities_table", required=True),
            "expected_facilities": expected,
        })
        try:
            actual = facility_rows_from_mcp(result)
        except ValueError as exc:
            return {"status": "inconclusive", "reason": str(exc), "evidence": evidence}
        evidence["returned_facilities"] = actual
        if not actual:
            return {"status": "inconclusive", "reason": "Data Agent returned an empty facility result "
                    "despite nonempty live Lakehouse reference rows.", "evidence": evidence}
        if actual != expected:
            return {"status": "failed", "reason": "Ontology facility IDs/names do not match the live Lakehouse "
                    "reference rows.", "evidence": evidence}
        return {"status": "verified", "reason": "Ontology-context MCP facility IDs/names exactly match independent "
                "live Lakehouse rows. Bounded facilities functional smoke test passed; execution provenance and "
                "other agent capabilities are not attested.", "evidence": evidence}
    except (RuntimeError, ValueError, KeyError, IndexError, requests.RequestException) as exc:
        reason = str(exc)[:4000]
        evidence["failure"] = reason
        return {"status": "failed", "reason": reason, "evidence": evidence}


def create_data_agent(display_name: str, description: str = "") -> dict:
    """Create an EMPTY Data Agent item (reuse if it already exists)."""
    existing = find_item_by_name(display_name, item_type=DATA_AGENT_ITEM_TYPE)
    if existing:
        print(f"✅ Reusing existing Data Agent: {display_name} (id={existing.get('id')})")
        return {**existing, "_created_this_run": False}

    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    body = {"displayName": display_name, "description": description, "type": DATA_AGENT_ITEM_TYPE}
    if target_folder_id:
        body["folderId"] = target_folder_id
    response = api_request("POST", url, data=body, timeout=120)

    if response.status_code in (200, 201):
        created = response.json() if response.content else {}
        print(f"✅ Created Data Agent: {display_name} (id={created.get('id')})")
        return {**created, "_created_this_run": True}
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("Create Data Agent returned 202 without Location header.")
        wait_for_lro(operation_url)
        result_response = api_request("GET", f"{operation_url}/result", timeout=120)
        result_response.raise_for_status()
        created = result_response.json()
        if not isinstance(created, dict) or not created.get("id"):
            raise RuntimeError("Create Data Agent LRO result has no item id; new-agent provenance is unverified.")
        print(f"✅ Created Data Agent (via LRO): {display_name} (id={created.get('id')})")
        return {**created, "_created_this_run": True}
    raise RuntimeError(f"Failed to create Data Agent: {response.status_code} {response.text}")


def get_item_definition(item_id: str) -> dict:
    """Read an item's definition (InlineBase64 parts) via getDefinition."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items/{item_id}/getDefinition"
    response = api_request("POST", url, timeout=120)
    if response.status_code == 200:
        envelope = response.json()
        definition_parts(envelope, allow_empty=True)
        return envelope
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("getDefinition returned 202 without Location header.")
        wait_for_lro(operation_url)
        result_response = api_request("GET", f"{operation_url}/result", timeout=120)
        if result_response.status_code == 200:
            envelope = result_response.json()
            definition_parts(envelope, allow_empty=True)
            return envelope
        raise RuntimeError(f"getDefinition result failed: {result_response.status_code} {result_response.text}")
    raise RuntimeError(f"Failed to get item definition: {response.status_code} {response.text}")


def update_item_definition(item_id: str, definition: dict) -> dict:
    """Write an item's full definition via updateDefinition."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items/{item_id}/updateDefinition"
    response = api_request("POST", url, data={"definition": definition}, timeout=300)
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("updateDefinition returned 202 without Location header.")
        return wait_for_lro(operation_url)
    raise RuntimeError(f"Failed to update item definition: {response.status_code} {response.text}")


def publish_data_agent(item_id: str, published_description: str = "") -> None:
    """Publish the DataAgent staging configuration so its endpoints go live.

    POST /v1/workspaces/{ws}/dataAgents/{id}/staging/publish (Preview) promotes the
    current staging (draft) config to the published environment. Once published, the
    agent exposes its supported MCP endpoint.
    """
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/dataAgents/{item_id}/staging/publish"
    body = {"publishedDescription": published_description[:256]} if published_description else {}
    response = api_request("POST", url, data=body, timeout=120)
    if response.status_code in (200, 201):
        print("✅ Data Agent published (staging → published).")
        return
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("Publish returned 202 without a Location; completion is unverified.")
        wait_for_lro(operation_url)
        print("✅ Data Agent published via LRO (staging → published).")
        return
    raise RuntimeError(f"Failed to publish Data Agent: {response.status_code} {response.text}")


def enable_preview_runtime(item_id: str) -> None:
    """Select Preview Runtime and verify it before publishing."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/dataAgents/{item_id}/staging/settings"
    current = api_request("GET", url, timeout=120)
    if current.status_code != 200:
        raise RuntimeError(f"Failed to read Data Agent runtime settings: {current.status_code} {current.text}")
    experimental = dict((current.json() or {}).get("experimental") or {})
    experimental["enableExperimentalFeatures"] = True
    updated = api_request("PATCH", url, data={"experimental": experimental}, timeout=120)
    if updated.status_code != 200:
        raise RuntimeError(f"Failed to enable Data Agent Preview Runtime: {updated.status_code} {updated.text}")
    verified = api_request("GET", url, timeout=120)
    settings = verified.json() if verified.status_code == 200 else {}
    if (settings.get("experimental") or {}).get("enableExperimentalFeatures") is not True:
        raise RuntimeError("Fabric Data Agent Preview Runtime could not be enabled.")
    print("✅ Data Agent Preview Runtime enabled.")


def upsert_part(parts: list, path: str, obj: dict) -> list:
    """Replace (or append) an InlineBase64 part at `path` with `obj`."""
    encoded = {"path": path, "payload": encode_payload(obj), "payloadType": "InlineBase64"}
    for i, part in enumerate(parts):
        if part.get("path") == path:
            parts[i] = {**part, **encoded}
            return parts
    parts.append(encoded)
    return parts


# -------------------------------------------------------------------------
# Agent instructions + ontology, Lakehouse and Eventhouse data sources
# -------------------------------------------------------------------------
LEGACY_AI_INSTRUCTIONS = 'You are an expert on industrial turbine telemetry modelled as a Fabric Ontology.\n- Real-time telemetry lives in the KQL Eventhouse and is exposed through the `signal_master` entity time-series properties (`event_time`, `value`, `quality`).\n- Static/reference data lives in the Lakehouse and is exposed through the `signal_master`, `equipment`, `facilities`, `systems` and `instruments` entities.\n- Every signal is keyed on `opcua_node_id`; join real-time readings to equipment metadata using `opcua_node_id`, and roll up to sites via `equipment_id` -> `facility_id` -> `system_id`.\n- The `unit` property on `signal_master`/`instruments` tells you whether a reading is pressure, temperature, flow, vibration or position.\n- `quality` values are GOOD (normal), UNCERTAIN (degraded) and BAD (failure). Use them to reason about equipment health.\n- Prefer answers that combine live readings with equipment/facility context. Keep answers short, clear and business-readable.'

AI_INSTRUCTIONS = """You are a hydro-operations assistant. Use the selected Fabric Ontology v2 as semantic context and answer from actual query results. Ordinary questions are read-only; never invent data, tools, mappings, or errors."""

MULTISOURCE_INSTRUCTIONS_MARKER = """### Ontology context and source execution"""
ASSET_RESOLUTION_MARKER = "### Asset & Facility Resolution (Must Follow)"
ASSET_RESOLUTION_INSTRUCTIONS = """### Asset & Facility Resolution (Must Follow)

Resolve tags, facilities, exact opcua_node_id values, equipment identifiers and units through ontology-associated reference data. Turbine 5 suggests tag T005, not an invented equipment_id. Use actual bindings and canonical keys, not display-name guesses or schema samples."""
MULTISOURCE_INSTRUCTIONS = """### Ontology context and source execution

Use ontology context and its associated Lakehouse for static entities/relationships, and its associated Eventhouse for telemetry. Source-native SQL/KQL on these sources is normal ontology-backed execution, not a bypass. "Use the ontology" permits this execution unless the user explicitly restricts it.
Use the existing SQL database for work orders, maintenance notifications, inspections, spare parts and 3D models. Query its connected SQL endpoint with read-only T-SQL: dbo.WorkOrders, dbo.Inspections, dbo.SpareParts, dbo.MaintenanceNotifications and dbo.Asset3DModels. The Data Agent's mounted SQL Database source uses that database's own SQL analytics endpoint, not the Lakehouse SQL analytics endpoint. Prefer this operational SQL source for a SQL-only question; its direct attachment is an execution workaround, not a replacement for ontology semantics. Discover the actual source from connected items; never invent hosts/tools or treat a missing SQL tool as zero records. Follow its source-specific instructions; do not answer operational-record questions from telemetry."""

CROSS_SOURCE_OPERATIONAL_MARKER = "### Cross-source operational joins"
CROSS_SOURCE_OPERATIONAL_INSTRUCTIONS = """### Cross-source operational joins

Resolve ontology reference keys, then query telemetry/SQL independently; never use federation. Each tool question must address only that tool's own source.
Match equipment_id/equipmentId and opcua_node_id/opcuaNodeId. Pass EVERY required returned identifier/count tuple, not previews. Retain all parents, including zero-child rows; count distinct WOs; conserve totals; report unmatched/duplicate keys.
Label source failures and successful reads; never infer data or widen windows.
SQL zero requires successful execution on the intended SQL source, not a missing tool or failed query. If SQL fails, return verified partial reads; never claim no open work or recommend additional WOs without verified SQL coverage.
"Had BAD today" means any BAD event in today's UTC window, not latest-only. Only explicit current/latest-state questions use arg_max before quality filtering. Open WorkOrders means status NOT IN ('Completed','Cancelled'); Draft is open; completedAt is not authoritative.
Retain each registered signal's quality/event_time/unit; summary counts must reconcile exactly to returned rows.
Use query execution UTC (KQL now()) as query_asof_utc, not the newest reading or clustered snapshot timestamps. Return age_seconds; readings older than 60 seconds are stale unless the operator specifies otherwise. Missing readings remain missing."""

GLOBAL_SCOPE_MARKER = "### Conversation scope"
GLOBAL_SCOPE_INSTRUCTIONS = """### Conversation scope

Use all facilities/assets unless narrowed. Resolve references; standalone questions inherit neither filters nor app selections."""

RUNNING_BAD_MARKER = '### Canonical "running bad" questions'
RUNNING_BAD_INSTRUCTIONS = """### Canonical "running bad" questions

Before any telemetry tool call, distinguish historical occurrence from current state. "Had BAD today" means ALL BAD events in that window, including signals now GOOD. For today use event_time >= startofday(now()) and event_time <= now(), filter quality == 'BAD', then count() by opcua_node_id. Never discard historical BAD events using arg_max or max(event_time). This historical rule overrides current-state defaults.
Only "running bad" or explicit current/latest quality uses latest raw rows: default 30 minutes, summarize arg_max(event_time, value, quality) by opcua_node_id, then filter BAD. For current state: Never filter quality before arg_max, prefilter signal type, or limit results. Resolve active turbine metadata and return every match.
Include tag, equipment/node/instrument, value/unit, quality and event time. Treat null/0001 timestamps as missing, not zero readings. Compare active-node coverage; report missing counts, never widen the window or equate empty BAD results with healthy turbines. Join all open work by affected equipment; do not filter work by node/instrument. Label same-signal only for matching node/instrument; otherwise equipment-level."""

RUNNING_HOT_MARKER = '### Canonical "running hot" questions'
RUNNING_HOT_INSTRUCTIONS = """### Canonical "running hot" questions

"Running hot" means turbine_temp. Unless overridden, resolve active turbine temperature signals, use a 30-minute window, take the latest raw row per signal and return the five hottest, descending. Include identifiers, temperature/unit, quality and event time; report missing/stale data. Rank alone is not abnormal/unsafe without an authoritative threshold. Join open work by equipment; distinguish same-signal from equipment-level."""

ADAPTIVE_RESPONSE_MARKER = "### Adaptive response contract"
ADAPTIVE_RESPONSE_INSTRUCTIONS = """### Adaptive response contract

Lead with the answer and material scope, time window and freshness. Adapt rendering to evidence: prose, bullets, tables or requested charts. Never force one template. Preserve identifiers, units, timestamps and status; distinguish zero, missing, stale, truncated and failed results. Separate facts from interpretation/recommendations. Keep evidence/scope consistent across Data Agent, Foundry, and Battle. End with concise sources and material limitations."""

MCP_FOLLOWUP_MARKER = "### External MCP follow-ups and visualizations"
MCP_FOLLOWUP_INSTRUCTIONS = """### External MCP follow-ups and visualizations

When supplied, answer <current_user_question>; use <recent_user_questions> only to resolve explicit references, not as current data or inherited filters.
For min/max/latest, use the same signal/window and retain the latest row's value, quality and timestamp; validate numeric consistency.
For requested charts include actual rows in fenced csv: event_time,value,quality for one signal; event_time,opcua_node_id,value,quality for multiple signals; descriptive labels/measures for other charts. Reuse answer rows, chronological for telemetry, at most 200 total. For a six-hour overview use 15-minute bins unless overridden; disclose aggregation and never silently average/truncate raw requests. Identify missing signals, not a complete result."""

DIRECT_RUNTIME_ROUTING_MARKER = "### Authoritative source routing"
DIRECT_RUNTIME_ROUTING_INSTRUCTIONS = """### Authoritative source routing

Use available tools and actual query results; prefer one authoritative source. Verify bindings/columns before declaring capability absent. Distinguish tool errors, partial reads and completed joins. Retry execution/coverage/validation failure once; never invent repairs or widen the requested window."""

COUNT_QUERY_GUIDANCE_MARKER = "### Operational count queries"
COUNT_QUERY_GUIDANCE = """
### Operational count queries

Open work orders means dbo.WorkOrders.status NOT IN ('Completed','Cancelled'); completedAt may
be null on Completed rows and is NOT an open predicate. Draft is open. For asset rankings or
counts to be mapped to facilities, execute this compact query rather than returning full detail:
SELECT equipmentId, COUNT_BIG(*) AS open_work_orders,
       SUM(COUNT_BIG(*)) OVER () AS total_open_work_orders
FROM dbo.WorkOrders WHERE status NOT IN ('Completed','Cancelled')
GROUP BY equipmentId ORDER BY open_work_orders DESC, equipmentId;
This computes the complete grouped counts and total together, with no TOP restriction.
WorkOrders has no facility_id; resolve that via the Lakehouse equipment mapping, not a SQL
schema search. Do not run another query merely to recount rows already included in this result.
"""


def update_operational_source_guidance(parts: list) -> list:
    """Refresh only notebook-owned SQL query guidance, retaining all sources and schema."""
    result = list(parts)
    for part in parts:
        path = part.get("path", "")
        if not path.startswith("Files/Config/draft/") or not path.endswith("/datasource.json"):
            continue
        ds = decode_payload(part.get("payload", ""))
        if ds.get("type") != "sql_database":
            continue
        instructions = ds.get("dataSourceInstructions", "")
        if not isinstance(instructions, str):
            raise RuntimeError("SQL source instructions are malformed; refusing to replace them.")
        ds["dataSourceInstructions"] = upsert_instruction_section(
            instructions, COUNT_QUERY_GUIDANCE_MARKER, COUNT_QUERY_GUIDANCE,
        )
        result = upsert_part(result, path, ds)
    return result

# Legacy generated descriptions, used only to recognize and migrate old notebook metadata.
# Live entity definitions below are authoritative; operator-authored descriptions are retained.
ONTOLOGY_ELEMENTS = [
    ("signal_master",
     "opcua_node_id,tag,instrument_id,equipment_id,system_id,facility_id,unit,"
     "is_active,signal_type,event_time,value,quality"),
    ("equipment",
     "equipment_id,facility_id,system_id,equipment_type_code,equipment_type_name,tag,"
     "manufacturer,model,criticality,install_date,status,is_active"),
    ("facilities",
     "facility_id,facility_name,type,country,lat,lon,commissioned_date"),
    ("systems",
     "system_id,facility_id,system_name,oag_rds_system_code"),
    ("instruments",
     "opcua_node_id,tag,instrument_id,equipment_id,system_id,facility_id,unit,"
     "instrument_type,is_active"),
]


def ontology_elements_from_definition(envelope: dict) -> list:
    """Read entity names and descriptions from the selected live ontology, never a schema sample."""
    return ontology_context_elements(definition_parts(envelope))


def get_live_ontology_elements(ontology_id: str) -> list:
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/ontologies/{ontology_id}/getDefinition"
    response = api_request("POST", url, timeout=120)
    if response.status_code == 202:
        operation_url = response.headers.get("Location")
        if not operation_url:
            raise RuntimeError("Ontology getDefinition returned 202 without Location header.")
        wait_for_lro(operation_url)
        response = api_request("GET", f"{operation_url.rstrip('/')}/result", timeout=120)
    response.raise_for_status()
    if response.status_code != 200:
        raise RuntimeError(f"Ontology getDefinition returned HTTP {response.status_code}.")
    return ontology_elements_from_definition(response.json())


def refresh_ontology_context(parts: list, ontology_id: str) -> list:
    """Refresh generated entity metadata without replacing selections, bindings or source profiles."""
    return refresh_ontology_context_parts(
        parts, get_live_ontology_elements(ontology_id), workspace_id, ontology_id, ONTOLOGY_ELEMENTS)

LAKEHOUSE_TABLES = [
    (first_setting("silver_facilities_table", required=True),
     "Facility master data keyed by facility_id."),
    (first_setting("silver_systems_table", required=True),
     "System master data keyed by system_id and related to facilities by facility_id."),
    (first_setting("silver_equipment_table", required=True),
     "Equipment master data keyed by equipment_id with facility_id, system_id and turbine tag."),
    (first_setting("silver_instruments_table", required=True),
     "Instrument metadata keyed by instrument_id and opcua_node_id with equipment context and unit."),
    (first_setting("silver_signal_master_table", required=True),
     "Per-signal metadata keyed by opcua_node_id with equipment, facility, system, tag and unit."),
]

LAKEHOUSE_SOURCE_INSTRUCTIONS = (
    "Authoritative static/reference source. Use facilities, systems, equipment, instruments and "
    "signal metadata for direct master-data questions and canonical ID resolution. Join only on "
    "facility_id, system_id, equipment_id or opcua_node_id. This source does not contain live "
    "telemetry readings or operational SQL records."
)
KUSTO_SOURCE_INSTRUCTIONS = (
    f"Authoritative telemetry source. Use {kql_table_name} for time-windowed values, quality, "
    "trends and aggregations. Filter and join by opcua_node_id; use event_time for time filters. "
    "Quality is GOOD, UNCERTAIN or BAD. Resolve asset/facility context through the Lakehouse "
    "metadata source when needed."
)


def _ds_element(type_name: str, display_name: str, is_selected: bool, children: list, **extra) -> dict:
    node = {
        "id": str(uuid.uuid4()),
        "is_selected": is_selected,
        "display_name": display_name,
        "type": type_name,
        "description": None,
        "children": children,
    }
    node.update(extra)
    return node


def merge_source_elements(existing: list, desired: list) -> list:
    """Add selected source schema without replacing saved IDs, descriptions or custom fields."""
    if not isinstance(existing, list) or any(not isinstance(element, dict) for element in existing):
        raise RuntimeError("Existing data source elements are malformed; refusing to replace them.")
    merged = list(existing)
    for node in desired:
        index = next((i for i, old in enumerate(merged)
                      if (old.get("type"), old.get("display_name")) ==
                      (node["type"], node["display_name"])), None)
        if index is None:
            merged.append(node)
        else:
            old = merged[index]
            merged[index] = {**node, **old, "is_selected": node["is_selected"], "children": merge_source_elements(
                old.get("children", []), node["children"],
            )}
    return merged


def lakehouse_schema_map(table_names=None) -> dict:
    context = notebookutils.runtime.context
    if (context.get("defaultLakehouseId") != lakehouse_id
            or context.get("defaultLakehouseWorkspaceId") != workspace_id):
        raise RuntimeError("Data Agent Lakehouse schema discovery must use the configured Lakehouse.")
    schema_map = {}
    for table_name in table_names if table_names is not None else [name for name, _ in LAKEHOUSE_TABLES]:
        if not re.fullmatch(r"(?:dbo\.)?[A-Za-z_][A-Za-z0-9_]*", table_name):
            raise RuntimeError(f"Unsupported Lakehouse table name {table_name!r}.")
        try:
            fields = spark.read.table(table_name).schema.fields
        except Exception as exc:
            raise RuntimeError(
                f"Required Data Agent Lakehouse table {table_name!r} is unavailable."
            ) from exc
        if not fields:
            raise RuntimeError(f"Required Data Agent Lakehouse table {table_name!r} has no columns.")
        schema_map[table_name] = [(field.name, field.dataType.simpleString()) for field in fields]
    return schema_map


def normalize_lakehouse_elements(elements: list) -> list:
    """Remove UI-only grouping nodes without changing source selections or identities."""
    if not isinstance(elements, list) or any(not isinstance(node, dict) for node in elements):
        raise RuntimeError("Existing Lakehouse source elements are malformed.")
    normalized = []
    for node in elements:
        children = normalize_lakehouse_elements(node["children"]) if "children" in node else []
        if node.get("type") in ("schema_grouping", "table_grouping"):
            normalized.extend(children)
        else:
            normalized.append({**node, **({"children": children} if "children" in node else {})})
    return normalized


def build_lakehouse_datasource_obj(existing: dict, schema_map: dict) -> dict:
    ds = dict(existing)
    ds["$schema"] = DATASOURCE_SCHEMA_URL
    ds["artifactId"] = lakehouse_id
    ds["workspaceId"] = workspace_id
    ds["displayName"] = lakehouse_name
    ds["type"] = LAKEHOUSE_DATASOURCE_TYPE
    ds.setdefault("dataSourceInstructions", LAKEHOUSE_SOURCE_INSTRUCTIONS)
    ds.setdefault("userDescription", "Curated RTI asset and signal master tables.")
    ds.setdefault("metadata", {})
    table_nodes = []
    for table_name, fields in schema_map.items():
        description = dict(LAKEHOUSE_TABLES).get(table_name, "Ontology-associated reference data.")
        columns = [
            _ds_element(
                "lakehouse_tables.column", column_name, True, [],
                data_type=data_type,
            )
            for column_name, data_type in fields
        ]
        table_nodes.append(_ds_element(
            "lakehouse_tables.table", table_name, True, columns,
            description=description,
        ))
    desired = [_ds_element("lakehouse_tables.schema", "dbo", False, table_nodes)]
    ds["elements"] = merge_source_elements(
        normalize_lakehouse_elements(existing.get("elements", [])), desired,
    )
    return ds


def build_kusto_datasource_obj(existing: dict) -> dict:
    ds = dict(existing)
    ds["$schema"] = DATASOURCE_SCHEMA_URL
    ds["artifactId"] = kql_db_id
    ds["workspaceId"] = workspace_id
    ds["displayName"] = kql_db_name
    ds["type"] = KUSTO_DATASOURCE_TYPE
    ds.setdefault("dataSourceInstructions", KUSTO_SOURCE_INSTRUCTIONS)
    ds.setdefault("userDescription", "Live and historical OPC UA telemetry.")
    ds.setdefault("metadata", {})
    columns = [
        _ds_element("kusto.column", "event_time", True, [], data_type="datetime"),
        _ds_element("kusto.column", "opcua_node_id", True, [], data_type="string"),
        _ds_element("kusto.column", "value", True, [], data_type="real"),
        _ds_element("kusto.column", "quality", True, [], data_type="string"),
    ]
    table = _ds_element(
        "kusto.table", kql_table_name, True, columns,
        description="OPC UA telemetry readings with event time, node, value and quality.",
    )
    desired = [_ds_element("table_grouping", "Tables", False, [table])]
    ds["elements"] = merge_source_elements(existing.get("elements", []), desired)
    return ds


def upsert_instruction_section(instructions: str, marker: str, desired: str) -> str:
    """Replace one owned section while preserving all unrelated instructions."""
    desired = desired.strip("\n")
    marker_index = instructions.find(marker)
    if marker_index < 0:
        return instructions.rstrip() + "\n\n" + desired
    section_start = instructions.rfind("\n", 0, marker_index) + 1
    boundaries = [
        instructions.find("\n" + "#" * level + " ", marker_index + len(marker))
        for level in range(1, 7)
    ]
    next_section = min((index for index in boundaries if index >= 0), default=-1)
    prefix = instructions[:section_start].rstrip()
    suffix = instructions[next_section:].lstrip("\n") if next_section >= 0 else ""
    return prefix + "\n\n" + desired + (("\n\n" + suffix) if suffix else "")


def build_stage_obj(existing: dict) -> dict:
    """Preserve custom instructions and upsert notebook-owned guidance."""
    stage = dict(existing)
    stage.setdefault("$schema", STAGE_CONFIG_SCHEMA_URL)
    instructions = stage.get("aiInstructions")
    if instructions is not None and not isinstance(instructions, str):
        raise RuntimeError("Existing agent instructions are not a string; refusing to replace custom configuration.")
    if not instructions:
        instructions = AI_INSTRUCTIONS
    elif instructions.startswith(LEGACY_AI_INSTRUCTIONS):
        instructions = AI_INSTRUCTIONS + instructions[len(LEGACY_AI_INSTRUCTIONS):]
    legacy_source_marker = "### Direct Lakehouse and Eventhouse Sources"
    if legacy_source_marker != MULTISOURCE_INSTRUCTIONS_MARKER and legacy_source_marker in instructions:
        instructions = upsert_instruction_section(
            instructions, legacy_source_marker, MULTISOURCE_INSTRUCTIONS,
        )
    for marker, desired in (
        (ASSET_RESOLUTION_MARKER, ASSET_RESOLUTION_INSTRUCTIONS),
        (MULTISOURCE_INSTRUCTIONS_MARKER, MULTISOURCE_INSTRUCTIONS),
        (CROSS_SOURCE_OPERATIONAL_MARKER, CROSS_SOURCE_OPERATIONAL_INSTRUCTIONS),
        (GLOBAL_SCOPE_MARKER, GLOBAL_SCOPE_INSTRUCTIONS),
        (RUNNING_BAD_MARKER, RUNNING_BAD_INSTRUCTIONS),
        (RUNNING_HOT_MARKER, RUNNING_HOT_INSTRUCTIONS),
        (ADAPTIVE_RESPONSE_MARKER, ADAPTIVE_RESPONSE_INSTRUCTIONS),
        (MCP_FOLLOWUP_MARKER, MCP_FOLLOWUP_INSTRUCTIONS),
        (DIRECT_RUNTIME_ROUTING_MARKER, DIRECT_RUNTIME_ROUTING_INSTRUCTIONS),
    ):
        instructions = upsert_instruction_section(instructions, marker, desired)
    if len(instructions) > 15000:
        raise RuntimeError("Preserved and appended Data Agent instructions exceed Fabric's 15,000 character limit.")
    stage["aiInstructions"] = instructions
    return stage


def build_datasource_obj(existing: dict, ontology_id: str) -> dict:
    """Ontology data source in the Fabric Data Agent shape (entity `elements`)."""
    require_v2_ontology(ontology_id)
    ds = dict(existing)
    ds.setdefault("$schema", DATASOURCE_SCHEMA_URL)
    ds["artifactId"] = ontology_id
    # Must be the ontology's real workspace GUID (empty/zero GUID is rejected).
    ds["workspaceId"] = workspace_id
    ds.setdefault("displayName", ontology_name)
    ds["type"] = DATASOURCE_TYPE
    ds.setdefault("dataSourceInstructions", None)
    ds.setdefault("userDescription", None)
    ds.setdefault("metadata", {})
    elements = ds.get("elements", [])
    if not isinstance(elements, list) or any(not isinstance(element, dict) for element in elements):
        raise RuntimeError("Existing ontology elements are malformed; refusing to replace custom selections.")
    elements = list(elements)
    for name, cols in ONTOLOGY_ELEMENTS:
        if any(element.get("id") == name or (
            element.get("type") == "ontology.entity" and element.get("display_name") == name
        ) for element in elements):
            continue
        elements.append({
            "id": name,
            "is_selected": True,
            "display_name": name,
            "type": "ontology.entity",
            "description": cols,
            "children": [],
        })
    ds["elements"] = elements
    return ds


# -------------------------------------------------------------------------
# V2-only deployment: enabled by default, no legacy fallback, errors always propagate.
# -------------------------------------------------------------------------
data_agent_item_id = None
ontology_id, ontology_generation = check_agent_capability()
persist_agent_status("deploying", "Data Agent configuration/publishing is in progress.")
try:
    get_spn_access_token_for_fabric()
    print("✅ Got Fabric access token (SPN).")

    print("✅ Resolved ontology ID:", ontology_id)

    # 1) Create (or reuse) the Data Agent item — empty, no definition.
    data_agent_item = create_data_agent(data_agent_name, DATA_AGENT_DESCRIPTION)
    data_agent_item_id = data_agent_item.get("id")
    if not data_agent_item_id:
        raise RuntimeError("Data Agent create returned no id.")

    # 2) DISCOVERY — read the live definition Fabric generated.
    definition = get_item_definition(data_agent_item_id)
    parts = definition_parts(definition, allow_empty=data_agent_item.get("_created_this_run") is True)
    validate_agent_ontology_sources(parts, ontology_id)
    print(f"🔎 Live definition has {len(parts)} part(s):")
    for part in parts:
        print("   •", part.get("path", ""))

    # 3) PATCH — preserve ontology context and its automatic source attachment when present.
    existing_stage = next(
        (decode_payload(p.get("payload", "")) for p in parts if p.get("path") == DRAFT_STAGE_CONFIG_PATH),
        {},
    )
    parts = upsert_part(parts, DRAFT_STAGE_CONFIG_PATH, build_stage_obj(existing_stage))

    context_paths = [
        part["path"] for part in parts
        if part["path"].startswith("Files/Config/draft/") and is_ontology_context_path(part["path"])
    ]
    submitted_direct_sources = {
        part["path"]: decode_payload(part["payload"])
        for part in parts if part["path"] in context_paths
    }
    if not context_paths:
        existing_ds = next(
            (decode_payload(p.get("payload", "")) for p in parts if p.get("path") == DATASOURCE_PATH),
            {},
        )
        parts = upsert_part(parts, DATASOURCE_PATH, build_datasource_obj(existing_ds, ontology_id))
        parts = update_operational_source_guidance(parts)
    for part in parts:
        if part["path"].startswith("Files/Config/draft/") and part["path"].endswith("/datasource.json"):
            ds = decode_payload(part["payload"])
            if ds.get("type") == "lakehouse_tables":
                normalized = normalize_lakehouse_elements(ds.get("elements", []))
                if normalized != ds.get("elements", []):
                    ds["elements"] = normalized
                    part["payload"] = encode_payload(ds)
                submitted_direct_sources[part["path"]] = ds
            if context_paths or ds.get("type") == "sql_database":
                submitted_direct_sources[part["path"]] = ds
    parts = refresh_ontology_context(parts, ontology_id)
    submitted_direct_sources = {}
    for part in parts:
        if part["path"].startswith("Files/Config/draft/") and (
            is_ontology_context_path(part["path"]) or part["path"].endswith("/datasource.json")
        ):
            ds = decode_payload(part["payload"])
            submitted_direct_sources[part["path"]] = ds
    validate_agent_ontology_sources(parts, ontology_id, require_draft=True)

    print(f"Applying definition: {len(parts)} part(s)")
    print("   • aiInstructions        ->", DRAFT_STAGE_CONFIG_PATH)
    if context_paths:
        print("   • verified ontology context ->", ", ".join(context_paths))
        print("       Refreshed live metadata; retained selections, custom sources and source guidance.")
    else:
        print("   • ontology data source  ->", DATASOURCE_PATH)
        print("       Entity metadata refreshed from the live ontology; custom selections preserved.")
    print("       Retained ontology-associated sources and compatible custom sources.")

    update_item_definition(data_agent_item_id, {"parts": parts})
    verify_agent_source_readback(
        data_agent_item_id, ontology_id, submitted_direct_sources,
    )
    print(f"✅ Data Agent '{data_agent_name}' configured (id={data_agent_item_id}).")

    # 4) PUBLISH — promote staging; verify published identity, not runtime readiness.
    enable_preview_runtime(data_agent_item_id)
    publish_data_agent(data_agent_item_id, DATA_AGENT_DESCRIPTION)
    verify_agent_source_readback(
        data_agent_item_id, ontology_id, submitted_direct_sources, published=True,
    )
    persist_agent_status(
        "published",
        "REST publish and selected live generation 2 source identity verified; no runtime query performed.",
        publication_status="published", runtime_status="not_checked",
        runtime_reason="Automatic query smoke is not part of setup; test the agent interactively.",
        runtime_evidence={},
        id=data_agent_item_id, name=data_agent_name,
    )
    mcp_endpoint = (
        f"{FABRIC_API_BASE}/v1/mcp/workspaces/{workspace_id}"
        f"/dataagents/{data_agent_item_id}/agent"
    )
    print("🌐 Published — consumption endpoint:")
    print(f"   • MCP: {mcp_endpoint}")
    print("✅ Publication verified. No automatic agent query; setup continues to NB11 operational guidance.")
except Exception as exc:
    persist_agent_status("failed", str(exc))
    raise


if data_agent_item_id:
    persist = {"data_agent_name": data_agent_name, "data_agent_id": data_agent_item_id}
    persist_df = (
        spark.createDataFrame([{"setting_name": k, "setting_value": str(v)} for k, v in persist.items()])
        .withColumn("updated_utc", F.current_timestamp())
    )
    merge_settings_with_retry(persist_df)
    print("✅ Persisted Data Agent settings:", persist)
    display(spark.read.table(settings_table_name).orderBy("setting_name"))

notebookutils.notebook.exit(json.dumps({
    "capability": "data_agent",
    "generation": ontology_generation,
    **agent_deployment_result,
    "data_agent_deployment_status": agent_deployment_result["status"],
    "data_agent_deployment_reason": agent_deployment_result["reason"],
}))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
