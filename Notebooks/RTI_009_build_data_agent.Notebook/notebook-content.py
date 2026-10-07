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
# selected Ontology v2, then verifies it with a sample query. Existing sources and
# custom configuration are preserved on reruns.
#
# > **Known Fabric limitation (September 2026):** Ontology v2 support in Data Agents
# > is pending. Publication can succeed while the final ontology query fails with
# > "This API version is not supported for the specified Ontology item."
# > Rerun verification after the Fabric product fix is available.
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
        if not path.endswith("/datasource.json"):
            continue
        if part.get("payloadType") != "InlineBase64":
            raise RuntimeError(f"Cannot verify agent data source {path!r}: expected InlineBase64.")
        ds = json.loads(base64.b64decode(part["payload"], validate=True).decode("utf-8"))
        if not isinstance(ds, dict):
            raise RuntimeError(f"Cannot verify agent data source {path!r}: expected a JSON object.")
        if not isinstance(ds.get("type"), str) or not ds["type"].strip():
            raise RuntimeError(f"Cannot verify agent data source {path!r}: missing source type.")
        if ds["type"].lower() != "ontology":
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
        if source_part is None or decode_payload(source_part.get("payload", "")) != expected:
            raise RuntimeError(
                f"Data Agent source readback at {path!r} does not match the submitted source; "
                "source retention and publish success are unverified."
            )
    return parts


def published_agent_sources(parts: list) -> dict:
    return {
        part["path"]: json.loads(base64.b64decode(part["payload"], validate=True).decode("utf-8"))
        for part in parts
        if part["path"].startswith("Files/Config/published/") and part["path"].endswith("/datasource.json")
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
    return normalized_facility_rows(payload)


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
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        if text.startswith(("{", "[")):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                return "Malformed structured MCP response; execution cannot be verified."
            return mcp_semantic_error(decoded)
        if re.search(
            r"(?i)(ontology source unavailable|api version is not supported|"
            r"failed to (?:query|execute|access)|unable to (?:query|execute|access)|"
            r"cannot (?:query|execute|access)|^\s*error\b)", text,
        ):
            return text[:4000]
    return None


def probe_data_agent_ontology(agent_id: str, ontology_id: str, published_parts=None) -> dict:
    """Smoke-test an ontology-requested answer against independent live Lakehouse rows."""
    endpoint = f"{FABRIC_API_BASE}/v1/mcp/workspaces/{workspace_id}/dataagents/{agent_id}/agent"
    activity_id = str(uuid.uuid4())
    question = (
        f"Read-only source readiness check. Use ONLY the Ontology item {ontology_id} "
        f"in workspace {workspace_id}. Do not use SQL, KQL databases, other sources, "
        "cached answers, examples in instructions, or estimates. Query the facilities entity: "
        "return the first five rows ordered by facility_id ascending (all rows if fewer than five). "
        "Return ONLY JSON: an array of objects with exactly facility_id and facility_name. "
        "If the ontology cannot be queried, return the exact error; do not substitute another source."
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
            "verification": "ontology_facilities_smoke_v1", "lakehouse_id": lakehouse_id,
            "scope": "Source-specific functional smoke test; execution provenance is not attested.",
            "published_sources": sources,
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
        evidence["response"] = json.dumps(result)[:8000]
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
        if actual != expected:
            return {"status": "failed", "reason": "Ontology facility IDs/names do not match the live Lakehouse "
                    "reference rows.", "evidence": evidence}
        return {"status": "verified", "reason": "Ontology-requested MCP facility IDs/names exactly match independent "
                "live Lakehouse rows. Source-specific functional smoke test passed; execution provenance and "
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
AI_INSTRUCTIONS = (
    "You are an expert on industrial turbine telemetry modelled as a Fabric Ontology.\n"
    "- Real-time telemetry lives in the KQL Eventhouse and is exposed through the "
    "`signal_master` entity time-series properties (`event_time`, `value`, `quality`).\n"
    "- Static/reference data lives in the Lakehouse and is exposed through the "
    "`signal_master`, `equipment`, `facilities`, `systems` and `instruments` entities.\n"
    "- Every signal is keyed on `opcua_node_id`; join real-time readings to equipment "
    "metadata using `opcua_node_id`, and roll up to sites via "
    "`equipment_id` -> `facility_id` -> `system_id`.\n"
    "- The `unit` property on `signal_master`/`instruments` tells you whether a reading "
    "is pressure, temperature, flow, vibration or position.\n"
    "- `quality` values are GOOD (normal), UNCERTAIN (degraded) and BAD (failure). "
    "Use them to reason about equipment health.\n"
    "- Prefer answers that combine live readings with equipment/facility context. "
    "Keep answers short, clear and business-readable."
) + """

### Asset & Facility Resolution (Must Follow)

1. **Turbine / Asset naming**
- Treat all of the following as referring to the same turbine asset:
- "turbine 5", "the 5th turbine", "unit 5", "asset 5", "T005"
- Normalize turbine references as:
- **Tag:** `T00N` (e.g., N=5 → `T005`)
- **Equipment ID:** `EQUIP_RTI_T00N` (e.g., N=5 → `EQUIP_RTI_T005`)
- When a user mentions a turbine by number or T‑code, always use both:
- `equipment.tag = "T00N"` and/or
- `equipment.equipment_id = "EQUIP_RTI_T00N"`
in all `analyze_ontology` and `analyze_sql_database` queries.
2. **Signal-based resolution**
- When the user mentions an OPC UA node like `ns=2;s=T005.power_output`, `ns=2;s=T005.vibration_a`, etc.:
- Use that `opcua_node_id` to look up `signal_master`,
- From `signal_master`, take `equipment_id`, `system_id`, and `facility_id` as the authoritative context for the asset.
- Prefer this resolved `equipment_id`/`facility_id` over the raw text (e.g., "turbine 5") when constructing tool queries.
3. **Facility naming**
- Map human facility names to IDs. For this environment:
- "RTI demo plant", "RTI hydropower plant", "the demo plant", "facility 1", "site 1" →
`facility_id = "FACILITY_RTI_001"`, `facility_name = "RTI Demo Hydropower Plant"`.
- Use the normalized `facility_id` in ontology/SQL queries instead of the raw user phrase whenever facility filtering is required.
4. **Tool query rewriting**
- Before calling any `analyze_ontology` or `analyze_sql_database` tool:
- Resolve turbines and facilities using the rules above.
- Rewrite the natural-language query to use the **canonical IDs/tags** (`EQUIP_RTI_T00N`, `T00N`, `FACILITY_RTI_001`) instead of the user's free-text wording.
- This ID normalization is required and does **not** change user intent; it only maps human phrasing to model identifiers.
"""

MULTISOURCE_INSTRUCTIONS_MARKER = "### Direct Lakehouse and Eventhouse Sources"
ASSET_RESOLUTION_MARKER = "### Asset & Facility Resolution (Must Follow)"
ASSET_RESOLUTION_INSTRUCTIONS = f"""
{ASSET_RESOLUTION_MARKER}

- Resolve asset numbers/tags in Lakehouse `dbo.{first_setting("silver_equipment_table", required=True)}`:
  turbine 5 means tag T005; turbine 12 means T012. Use the actual equipment_id returned by
  the source. Do not search Ontology to resolve assets for direct-source questions.
- Resolve facility names by joining `facility_id` to
  `dbo.{first_setting("silver_facilities_table", required=True)}`. Never use a hard-coded
  demo facility name or infer the facility from the equipment identifier.
- Resolve signal IDs and units in `dbo.{first_setting("silver_signal_master_table", required=True)}`.
  Exact opcua_node_id is the telemetry join key. Combine reference lookups in one SQL query
  when they share this Lakehouse; do not make a separate analysis call per table.
"""
MULTISOURCE_INSTRUCTIONS = f"""

{MULTISOURCE_INSTRUCTIONS_MARKER}

- Keep the Ontology v2 source as the semantic model for entity relationships and ontology-specific questions.
- Use the `{lakehouse_name}` Lakehouse source for direct static/reference questions over
  `{first_setting("silver_facilities_table", required=True)}`,
  `{first_setting("silver_systems_table", required=True)}`,
  `{first_setting("silver_equipment_table", required=True)}`,
  `{first_setting("silver_instruments_table", required=True)}`, and
  `{first_setting("silver_signal_master_table", required=True)}`.
- Use the `{kql_db_name}` Eventhouse/KQL source and its `{kql_table_name}` table for live or
  historical telemetry values, quality, trends, time windows, and aggregations.
- `{kql_table_name}` columns are `event_time`, `opcua_node_id`, `value`, and `quality`.
- Join or correlate sources only through canonical keys: `opcua_node_id`, `equipment_id`,
  `facility_id`, and `system_id`. Never infer a relationship from display names.
- When the operational SQL source is present, use it for work orders, maintenance notifications,
  inspections, spare parts, and 3D model records. Do not answer those operational-record questions
  from Lakehouse or Eventhouse telemetry.
- A successful Lakehouse, Eventhouse, or SQL answer does not prove that Ontology v2 execution
  succeeded. For an explicitly ontology-only request, use only the Ontology source and return its
  real error if it cannot execute; do not silently substitute another source.
- Prefer one authoritative source when it fully answers the question. Use multiple sources only
  when the user asks for combined telemetry and asset/operational context.
"""

CROSS_SOURCE_OPERATIONAL_MARKER = "### Cross-source operational joins"
CROSS_SOURCE_OPERATIONAL_INSTRUCTIONS = f"""

{CROSS_SOURCE_OPERATIONAL_MARKER}

- SQL operational tables use camelCase `equipmentId`; Lakehouse reference tables use snake_case
  `equipment_id`. These are the same canonical equipment identifier and must be matched exactly.
- `WorkOrders` has no facility column. Never query or filter it by `facilityId`/`facility_id`.
- Open means `status NOT IN ('Completed','Cancelled')`. Never use `completedAt IS NULL`:
  a completed order can have a null completion date. Never exclude Draft or add an active-asset
  filter unless requested.
- For counts/rankings, let SQL aggregate; do not fetch every work-order detail or ask separate
  count/list questions. Retrieve one row per equipment with its open count and the overall total
  in ONE SQL query:
  SELECT equipmentId, COUNT_BIG(*) AS open_work_orders,
         SUM(COUNT_BIG(*)) OVER () AS total_open_work_orders
  FROM dbo.WorkOrders WHERE status NOT IN ('Completed','Cancelled')
  GROUP BY equipmentId ORDER BY open_work_orders DESC, equipmentId;
- Source analyzers do NOT share each other's results. For facility rollups, copy EVERY actual
  returned (equipmentId, open_work_orders) tuple as literal data INTO the Lakehouse tool's
  question, together with the requested SQL join/aggregation. Never tell it "use the previous
  result" or refer to an unattached list: the tool cannot see that context.
  Ask it to use those explicitly supplied tuples as a VALUES relation in ONE Lakehouse query.
  If it reports missing tuples, repeat once with the actual tuples included in the tool question;
  do not claim a missing schema or ask the user for data already returned by SQL. Left-join the complete
  `dbo.{first_setting("silver_equipment_table", required=True)}` inventory and
  `dbo.{first_setting("silver_facilities_table", required=True)}` on canonical keys.
  Compute asset COUNT and open-work-order SUM by facility IN SQL, not by manually counting
  prose or table rows. Include facilities with zero orders. Return facility names/type/country
  from that same join, not a separate name lookup. SQL-pair values must come from the current
  tool result, never examples or previous answers.
- Conserve counts: every SQL equipment key must map exactly once; sum of the facility counts
  must match total_open_work_orders. If keys are unmatched/duplicated or totals disagree, report
  the discrepancy and correct the join once; never silently omit an order or invent counts.
- Asset rankings use the same SQL counts, descending with tied ranks, and Lakehouse tags/facility
  names when needed. Only include zero-count assets if requested; do not narrow assets to turbines
  or a previous question's scope. Table and chart must use the SAME rows and human-readable labels.
- Format facility rollups as a compact Markdown table with `Facility`, `Type`, `Country`, `Assets`,
  and `Open WOs` columns plus a totals line, unless the user requests another format.
"""

GLOBAL_SCOPE_MARKER = "### Conversation scope"
GLOBAL_SCOPE_INSTRUCTIONS = f"""

{GLOBAL_SCOPE_MARKER}

- Unless the latest user question explicitly narrows scope, answer across all facilities and all
  assets. Do not infer scope from an application selection.
- Keep conversational follow-up meaning within the current agent session, but do not carry an
  earlier facility/asset filter into a later question unless the user explicitly refers to that
  earlier scope.
"""

RUNNING_BAD_MARKER = '### Canonical "running bad" questions'
RUNNING_BAD_INSTRUCTIONS = f"""

{RUNNING_BAD_MARKER}

- Interpret "Which turbines are running bad right now?" as literal telemetry quality `BAD`, not
  an out-of-range numeric value. Resolve every active turbine and all of its active instruments;
  do not silently narrow the request to temperature or another signal type.
- Unless the user explicitly supplies another window or signal, use a 30-minute lookback and
  select the single raw reading with greatest `event_time` for each resolved `opcua_node_id`. Do
  not average or bin values. Include a turbine when at least one signal's latest row has quality
  `BAD`, compared case-insensitively, and return every such BAD signal.
- Return turbine tag, `equipment_id`, instrument/signal identity, `opcua_node_id`, latest value,
  unit, quality, and `event_time`. Identify stale or missing telemetry instead of silently
  changing the window.
- For "what work is already open on it/them?", retrieve every work order for the affected
  equipment whose status is neither `Completed` nor `Cancelled`. Label each order as same-signal
  only when `opcuaNodeId` or `instrumentId` matches one of that turbine's BAD signals; otherwise
  label it equipment-level work. Do not claim that unrelated equipment-level work addresses a BAD
  signal.
- State this interpretation and the effective window briefly in the answer so Battle comparisons
  expose their scope.
"""

RUNNING_HOT_MARKER = '### Canonical "running hot" questions'
RUNNING_HOT_INSTRUCTIONS = f"""

{RUNNING_HOT_MARKER}

- Interpret "Which turbines are running hot right now?" as turbine temperature, not telemetry
  quality and not speed, vibration, pressure, power, or another signal type. Resolve every active
  turbine's active `turbine_temp` instrument.
- Unless the user explicitly supplies another window, use a 30-minute lookback and select the
  single raw reading with greatest `event_time` for each resolved `turbine_temp` `opcua_node_id`.
  Do not average or bin values. Rank the latest temperatures descending and, when no threshold or
  result count is supplied, return the five hottest turbines.
- Return turbine tag, `equipment_id`, `instrument_id`, `opcua_node_id`, latest temperature, unit,
  quality, and `event_time`. Identify stale or missing telemetry instead of silently changing the
  window. A high rank means hottest in the compared fleet; do not call a value abnormal,
  overheating, or unsafe unless the user supplies a threshold or an authoritative operating limit
  is available.
- For "what work is already open on it/them?", retrieve every work order for the returned equipment
  whose status is neither `Completed` nor `Cancelled`. Label each order as same-signal only when
  `opcuaNodeId` or `instrumentId` matches that turbine's temperature signal; otherwise label it
  equipment-level work. Do not claim that unrelated equipment-level work addresses temperature.
- State this interpretation, effective window, and ranking/threshold rule briefly in the answer so
  Battle comparisons expose their scope.
"""

MCP_FOLLOWUP_MARKER = "### External MCP follow-ups and visualizations"
MCP_FOLLOWUP_INSTRUCTIONS = f"""

{MCP_FOLLOWUP_MARKER}

- The external MCP client may wrap an explicitly referential follow-up in
  `<recent_user_questions>` and `<current_user_question>` tags because each published MCP tool call
  is otherwise stateless. Answer only `<current_user_question>`. Use the earlier user questions
  solely to resolve references such as `this asset`, `same signal`, `it`, or a leading `or`; never
  carry their filter into a standalone current question.
- For a minimum/maximum/latest summary of one telemetry signal, constrain the exact
  `opcua_node_id` and requested time window. Compute minimum and maximum from numeric `value`, and
  obtain the latest row by greatest `event_time` while retaining that row's `value` and `quality`.
  Return the latest timestamp. Reject and retry any result where the latest value is nonnumeric or
  falls outside the computed minimum/maximum range.
- When the user asks to plot, chart, graph, visualize, or show telemetry `visually`, a fenced `csv`
  block is mandatory. Never say that this chat cannot render a chart and never return only plotting
  instructions. For one signal use `event_time,value,quality`. For multiple signals use
  `event_time,opcua_node_id,value,quality`; include every resolved signal, keep chronological order,
  and keep the response to at most 200 total plot rows. If the raw series exceeds that bound, use
  an appropriate time bin for a visual overview unless the user explicitly requested raw/no
  averaging. Use a compact overview instead of filling the maximum: for a six-hour multi-signal
  visual, use 15-minute bins unless the user requests another granularity; do not return raw or
  near-raw points for that default overview. Query
  `{kql_table_name}` directly for the resolved exact `opcua_node_id` values and
  requested time window. For multiple signals, verify that the returned `opcua_node_id` set exactly
  matches the resolved signal set and that every signal has at least one row. If the first
  Eventhouse execution errors, returns no rows, or omits any resolved signal, retry that direct
  query once before concluding that telemetry is unavailable; never describe a partial signal set
  as the complete result and do not replace real values with an example schema. The web client
  renders the returned CSV locally. Even if Fabric also generates a native
  report/visualization file, include the fenced CSV because the external MCP result may not expose
  that file.
- The same fenced CSV requirement applies to non-telemetry charts, rankings, and work-order
  counts. Use a descriptive categorical label column and numeric measure columns, with exactly
  the same values as the answer table. Text bars and an offer to generate a chart later are not
  a chart response. Do not requery data just to format a chart.
"""

DIRECT_RUNTIME_ROUTING_MARKER = "### Authoritative source routing"
DIRECT_RUNTIME_ROUTING_INSTRUCTIONS = f"""

{DIRECT_RUNTIME_ROUTING_MARKER}

- For normal user questions, do not call the Ontology analysis tool. The attached Ontology v2 must
  remain configured for identity and future ontology capability, but its current query runtime is
  not a prerequisite, fallback, retry, or cross-check for direct-source answers.
- Use `{kql_db_name}` / `{kql_table_name}` directly for every current, latest, historical,
  time-series, quality, trend, health, or telemetry question.
- Use `{lakehouse_name}` directly for facilities, systems, equipment, instruments, signal
  metadata, hierarchy lookup, and canonical ID resolution.
- When a telemetry question names a turbine, equipment tag, or signal type rather than an exact
  `opcua_node_id`, first resolve the exact node through Lakehouse `silver_equipment` and
  `silver_signal_master`, then query Eventhouse with that node. Reuse an exact node already stated
  in the current conversation. Never treat Kusto tool schema samples, example values, or a partial
  distinct-value list as the complete signal inventory, and never report that an asset has no
  telemetry mapping without checking the Lakehouse mapping tables.
- Use the operational SQL source directly for work orders, notifications, inspections, spare
  parts, and 3D-model records. Combine SQL with Lakehouse only through the documented canonical
  keys when a facility or hierarchy rollup is required.
- Call the Ontology tool only when the current user question explicitly requests the Ontology,
  an ontology-only answer, or ontology-native semantic relationship execution. Do not infer that
  request merely because the question mentions an asset, relationship, facility, or hierarchy.
- If a direct source answers the question, return that answer without attempting Ontology and
  without adding an Ontology-runtime warning. Referential follow-ups keep the same direct-source
  routing unless the current question explicitly switches source.
- Prefer one combined query per required source. Reuse the returned rows for reconciliation,
  tables and CSV; do not perform separate schema searches, independent recounts, or repeated
  executions when a successful query already returned the necessary evidence. Retry only a
  concrete execution error, incomplete result, or failed key/total validation.
"""

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

# Ontology entities to expose to the agent (name -> column summary used as description).
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


def lakehouse_schema_map() -> dict:
    context = notebookutils.runtime.context
    if (context.get("defaultLakehouseId") != lakehouse_id
            or context.get("defaultLakehouseWorkspaceId") != workspace_id):
        raise RuntimeError("Data Agent Lakehouse schema discovery must use the configured Lakehouse.")
    schema_map = {}
    for table_name, _description in LAKEHOUSE_TABLES:
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
    for table_name, description in LAKEHOUSE_TABLES:
        columns = [
            _ds_element(
                "lakehouse_tables.column", column_name, True, [],
                data_type=data_type,
            )
            for column_name, data_type in schema_map[table_name]
        ]
        table_nodes.append(_ds_element(
            "lakehouse_tables.table", table_name, True, columns,
            description=description,
        ))
    desired = [
        _ds_element("schema_grouping", "Schemas", False, [
            _ds_element("lakehouse_tables.schema", "dbo", False, [
                _ds_element("table_grouping", "Tables", False, table_nodes),
            ]),
        ]),
    ]
    ds["elements"] = merge_source_elements(existing.get("elements", []), desired)
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
    next_section = instructions.find("\n### ", marker_index + len(marker))
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
    for marker, desired in (
        (ASSET_RESOLUTION_MARKER, ASSET_RESOLUTION_INSTRUCTIONS),
        (MULTISOURCE_INSTRUCTIONS_MARKER, MULTISOURCE_INSTRUCTIONS),
        (CROSS_SOURCE_OPERATIONAL_MARKER, CROSS_SOURCE_OPERATIONAL_INSTRUCTIONS),
        (GLOBAL_SCOPE_MARKER, GLOBAL_SCOPE_INSTRUCTIONS),
        (RUNNING_BAD_MARKER, RUNNING_BAD_INSTRUCTIONS),
        (RUNNING_HOT_MARKER, RUNNING_HOT_INSTRUCTIONS),
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

    # 3) PATCH — preserve instructions and upsert Ontology, Lakehouse and Eventhouse sources.
    existing_stage = next(
        (decode_payload(p.get("payload", "")) for p in parts if p.get("path") == DRAFT_STAGE_CONFIG_PATH),
        {},
    )
    parts = upsert_part(parts, DRAFT_STAGE_CONFIG_PATH, build_stage_obj(existing_stage))

    existing_ds = next(
        (decode_payload(p.get("payload", "")) for p in parts if p.get("path") == DATASOURCE_PATH),
        {},
    )
    parts = upsert_part(parts, DATASOURCE_PATH, build_datasource_obj(existing_ds, ontology_id))

    discovered_lakehouse_schema = lakehouse_schema_map()
    existing_lakehouse_ds = next(
        (
            decode_payload(p.get("payload", ""))
            for p in parts if p.get("path") == LAKEHOUSE_DATASOURCE_PATH
        ),
        {},
    )
    submitted_lakehouse_source = build_lakehouse_datasource_obj(
        existing_lakehouse_ds, discovered_lakehouse_schema,
    )
    parts = upsert_part(parts, LAKEHOUSE_DATASOURCE_PATH, submitted_lakehouse_source)

    existing_kusto_ds = next(
        (
            decode_payload(p.get("payload", ""))
            for p in parts if p.get("path") == KUSTO_DATASOURCE_PATH
        ),
        {},
    )
    submitted_kusto_source = build_kusto_datasource_obj(existing_kusto_ds)
    parts = upsert_part(parts, KUSTO_DATASOURCE_PATH, submitted_kusto_source)
    submitted_direct_sources = {
        LAKEHOUSE_DATASOURCE_PATH: submitted_lakehouse_source,
        KUSTO_DATASOURCE_PATH: submitted_kusto_source,
    }
    parts = update_operational_source_guidance(parts)
    for part in parts:
        if part["path"].startswith("Files/Config/draft/") and part["path"].endswith("/datasource.json"):
            ds = decode_payload(part["payload"])
            if ds.get("type") == "sql_database":
                submitted_direct_sources[part["path"]] = ds
    validate_agent_ontology_sources(parts, ontology_id, require_draft=True)

    print(f"Applying definition: {len(parts)} part(s)")
    print("   • aiInstructions        ->", DRAFT_STAGE_CONFIG_PATH)
    print("   • ontology data source  ->", DATASOURCE_PATH)
    print(f"       {len(ONTOLOGY_ELEMENTS)} entity element(s) selected.")
    print("   • Lakehouse data source ->", LAKEHOUSE_DATASOURCE_PATH)
    print(f"       {len(LAKEHOUSE_TABLES)} table(s) selected with discovered columns.")
    print("   • Eventhouse data source ->", KUSTO_DATASOURCE_PATH)
    print(f"       {kql_table_name} selected with 4 columns.")

    update_item_definition(data_agent_item_id, {"parts": parts})
    verify_agent_source_readback(
        data_agent_item_id, ontology_id, submitted_direct_sources,
    )
    print(f"✅ Data Agent '{data_agent_name}' configured (id={data_agent_item_id}).")

    # 4) PUBLISH — promote staging; verify published identity, not runtime readiness.
    enable_preview_runtime(data_agent_item_id)
    publish_data_agent(data_agent_item_id, DATA_AGENT_DESCRIPTION)
    published_parts = verify_agent_source_readback(
        data_agent_item_id, ontology_id, submitted_direct_sources, published=True,
    )
    persist_agent_status(
        "published",
        "REST publish and selected live generation 2 source identity verified; runtime probe pending.",
        publication_status="published", runtime_status="checking",
        id=data_agent_item_id, name=data_agent_name,
    )
    mcp_endpoint = (
        f"{FABRIC_API_BASE}/v1/mcp/workspaces/{workspace_id}"
        f"/dataagents/{data_agent_item_id}/agent"
    )
    print("🌐 Published — consumption endpoint:")
    print(f"   • MCP: {mcp_endpoint}")
    try:
        runtime = probe_data_agent_ontology(data_agent_item_id, ontology_id, published_parts)
    except Exception as exc:
        if not is_known_ontology_v2_product_limitation(str(exc)):
            persist_agent_status("failed", str(exc), runtime_status="failed", runtime_reason=str(exc))
            raise
        runtime = {"status": "known_product_limitation", "reason": str(exc), "evidence": {}}
    if is_known_ontology_v2_product_limitation(runtime["reason"]):
        persist_agent_status(
            "known_product_limitation", runtime["reason"],
            publication_status="published", runtime_status="known_product_limitation",
            runtime_reason=runtime["reason"], runtime_evidence=runtime["evidence"],
        )
        print("⚠️ Data Agent published, but Fabric currently rejects the verified Ontology v2 source.")
    else:
        persist_agent_status(
            "ready" if runtime["status"] == "verified" else "failed", runtime["reason"],
            publication_status="published", runtime_status=runtime["status"],
            runtime_reason=runtime["reason"], runtime_evidence=runtime["evidence"],
        )
        if runtime["status"] != "verified":
            raise RuntimeError(f"Data Agent published but ontology runtime {runtime['status']}: {runtime['reason']}")
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
