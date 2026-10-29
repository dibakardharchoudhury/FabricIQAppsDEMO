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
# After binding readback, the existing ontology-owned GraphModel is discovered from
# authoritative lineage, repaired to project static `signal_master` fields without
# Eventhouse time-series values, refreshed, and verified with the Operations Agent
# GQL selector. Fabric's initial Manage graph child association remains a one-time
# portal prerequisite because no public REST API creates it.

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
import time
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


def _pin_eventhouse_table_metadata(table, workspace_id, item_id, item_name, workspace_name, columns):
    """Retain the source identity/schema emitted by Fabric's Eventhouse binding editor."""
    partitions = _object_spans(table, "partition", 4)
    if len(partitions) != 1:
        raise RuntimeError("Eventhouse source metadata requires exactly one partition.")
    partition = partitions[0][3]
    original = partition
    values = {
        "ONT_WorkspaceId": workspace_id, "ONT_ItemId": item_id, "ONT_ItemKind": "KQLDatabase",
        "ONT_ItemName": item_name, "ONT_WorkspaceName": workspace_name,
    }
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
            raise RuntimeError(f"Missing authoritative Eventhouse source metadata: {key}.")
        pattern = r"(?m)^\t\tannotation " + key + r" = (.+?)[ \t]*$"
        existing = re.findall(pattern, partition)
        if len(existing) > 1 or (existing and key in ("ONT_WorkspaceId", "ONT_ItemId", "ONT_ItemKind")
                                 and existing[0].casefold() != value.casefold()):
            raise RuntimeError(f"Conflicting Eventhouse source metadata: {key}.")
        if existing:
            partition = re.sub(pattern, lambda _match: f"\t\tannotation {key} = {value}", partition)
        else:
            partition = partition.rstrip() + f"\n\n\t\tannotation {key} = {value}\n"
    timestamps = re.findall(r"(?m)^\t\tannotation ONT_PinnedAtUtc = (.+?)[ \t]*$", partition)
    if len(timestamps) > 1:
        raise RuntimeError("Duplicate Eventhouse source pin timestamp.")
    if not timestamps:
        partition = partition.rstrip() + "\n\n\t\tannotation ONT_PinnedAtUtc = " + time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n"
    table = table.replace(original, partition.rstrip() + "\n\n", 1)
    schema = {"version": 1, "columns": [{"name": name, "dataType": dtype} for name, dtype in columns]}
    pattern = r"(?m)^\tannotation ONT_SourceSchema = (.+?)[ \t]*$"
    existing = re.findall(pattern, table)
    if len(existing) > 1:
        raise RuntimeError("Duplicate Eventhouse source schema.")
    value = json.dumps(schema, separators=(",", ":"))
    if existing:
        if json.loads(existing[0]) != schema:
            raise RuntimeError("Eventhouse source schema does not match the verified physical columns.")
    else:
        table = table.rstrip() + f"\n\n\tannotation ONT_SourceSchema = {value}\n"
    return table


def _parts_by_path(parts):
    result = {part["path"]: part for part in parts}
    if len(result) != len(parts):
        raise RuntimeError("Duplicate ontology definition part paths")
    return result


def _project_semantics():
    """Portable semantic catalog; contains no workspace identities or bindings."""
    return {'Asset3DModel': {'annotations': {'domain': 'asset',
                                      'modelRole': 'attachment',
                                      'preferredUsage': '3D model lookup and asset documentation',
                                      'queryHint': 'Use Asset3DModel when the question is about 3D model files, '
                                                   'versions, or previews tied to equipment.'},
                      'description': '3D model assets and renderings attached to hydro equipment. Use this '
                                     'entity for model assets, previews, and file metadata tied back to '
                                     'equipment.',
                      'properties': {'equipmentId': {'annotations': {},
                                                     'description': 'Canonical physical equipment identifier '
                                                                    'referencing equipment.equipment_id.',
                                                     'synonyms': ['equipment id', 'equipment identifier']},
                                     'fileSizeMb': {'annotations': {},
                                                    'description': 'File size in megabytes.',
                                                    'synonyms': ['file size', 'size mb']},
                                     'format': {'annotations': {},
                                                'description': 'File format of the model.',
                                                'synonyms': ['file format', 'model format']},
                                     'id': {'annotations': {},
                                            'description': 'Unique identifier for the 3D model record.',
                                            'synonyms': ['model id', 'asset model id']},
                                     'modelName': {'annotations': {},
                                                   'description': 'Display name of the 3D model.',
                                                   'synonyms': ['name', 'model title', 'asset model name']},
                                     'modelUrl': {'annotations': {},
                                                  'description': 'URL to the model file.',
                                                  'synonyms': ['model link', 'model location', 'file url']},
                                     'thumbnailUrl': {'annotations': {},
                                                      'description': 'URL to the model thumbnail.',
                                                      'synonyms': ['thumbnail link', 'preview image']},
                                     'updatedAt': {'annotations': {},
                                                   'description': 'Last update timestamp.',
                                                   'synonyms': ['updated at', 'last updated']},
                                     'updatedByOid': {'annotations': {},
                                                      'description': 'Object identifier of the user who last '
                                                                     'updated the model.',
                                                      'synonyms': ['updated by', 'updated by oid', 'editor oid']},
                                     'version': {'annotations': {},
                                                 'description': 'Model version label.',
                                                 'synonyms': ['revision', 'release', 'version label']}},
                      'synonyms': ['3D Model',
                                   'Equipment Model',
                                   'Digital Twin Model',
                                   'Model Asset',
                                   'Asset Rendering']},
     'Inspection': {'annotations': {'domain': 'maintenance',
                                    'modelRole': 'operational fact',
                                    'preferredUsage': 'inspection history and compliance review',
                                    'queryHint': 'Use Inspection for inspection activity, findings, and due '
                                                 'dates.'},
                    'description': 'equipment inspection records and outcomes. Use this entity for inspection '
                                   'history, findings, and schedule tracking.',
                    'properties': {'equipmentId': {'annotations': {},
                                                   'description': 'Canonical physical equipment identifier '
                                                                  'referencing equipment.equipment_id.',
                                                   'synonyms': ['equipment id', 'equipment identifier']},
                                   'findings': {'annotations': {},
                                                'description': 'Free-text inspection findings.',
                                                'synonyms': ['findings', 'notes']},
                                   'id': {'annotations': {},
                                          'description': 'Unique identifier for the inspection record.',
                                          'synonyms': ['inspection id', 'record id']},
                                   'inspectedAt': {'annotations': {},
                                                   'description': 'Timestamp when the inspection occurred.',
                                                   'synonyms': ['inspection time', 'inspected at']},
                                   'inspectionType': {'annotations': {},
                                                      'description': 'Type or category of inspection.',
                                                      'synonyms': ['type', 'inspection category']},
                                   'inspectorOid': {'annotations': {},
                                                    'description': 'Object identifier of the inspector.',
                                                    'synonyms': ['inspector', 'inspector oid']},
                                   'nextDueAt': {'annotations': {},
                                                 'description': 'Scheduled next inspection due time.',
                                                 'synonyms': ['next due', 'due at']},
                                   'opcuaNodeId': {'annotations': {},
                                                   'description': 'Optional canonical OPC UA signal identifier; '
                                                                  'join to signal_master.opcua_node_id when '
                                                                  'present.',
                                                   'synonyms': ['opc ua node id', 'node id']},
                                   'result': {'annotations': {},
                                              'description': 'Inspection outcome.',
                                              'synonyms': ['outcome', 'status']}},
                    'synonyms': ['Inspection Record',
                                 'Asset Inspection',
                                 'Maintenance Inspection',
                                 'Checkup',
                                 'Inspection History']},
     'InstrumentClassification': {'annotations': {'domain': 'asset',
                                                  'modelRole': 'fact',
                                                  'preferredUsage': 'signal inventory and system-level signal '
                                                                    'summary',
                                                  'queryHint': 'Use InstrumentClassification for counts of '
                                                               'signal types by system, not raw instrument '
                                                               'rows.'},
                                  'description': 'Aggregated count of signals by system and signal type. Use '
                                                 'this as the summarized signal inventory for a system.',
                                  'properties': {'ontology_key': {'annotations': {},
                                                                  'description': 'Verified SHA-256 identity of '
                                                                                 'the complete natural-key '
                                                                                 'tuple, with UTC timestamps '
                                                                                 'encoded before hashing. It is '
                                                                                 'not a measurement.',
                                                                  'synonyms': ['ontology key']},
                                                 'signal_count': {'annotations': {},
                                                                  'description': 'Count of signals of this type '
                                                                                 'for the system.',
                                                                  'synonyms': ['signal count',
                                                                               'count of signals']},
                                                 'signal_type': {'annotations': {},
                                                                 'description': 'Canonical signal category used '
                                                                                'for signal inventory and '
                                                                                'classification, not a telemetry '
                                                                                'sample value.',
                                                                 'synonyms': ['signal type', 'measurement type']},
                                                 'system_id': {'annotations': {},
                                                               'description': 'Canonical system identifier '
                                                                              'linking the record to its plant '
                                                                              'subsystem.',
                                                               'synonyms': ['system id']}},
                                  'synonyms': ['Signal Classification',
                                               'Instrument Signal Count',
                                               'System Signal Summary',
                                               'Classification Fact',
                                               'Signal Inventory Summary']},
     'MaintenanceNotification': {'annotations': {'domain': 'maintenance',
                                                 'modelRole': 'operational fact',
                                                 'preferredUsage': 'alert tracking and maintenance triage',
                                                 'queryHint': 'Use MaintenanceNotification for alerts, severity, '
                                                              'status, and reporting details.'},
                                 'description': 'Maintenance alerts and notifications raised for equipment or '
                                                'OPC UA signals. Use this entity for alert tracking, severity, '
                                                'status, and reporter context.',
                                 'properties': {'equipmentId': {'annotations': {},
                                                                'description': 'Canonical physical equipment '
                                                                               'identifier referencing '
                                                                               'equipment.equipment_id.',
                                                                'synonyms': ['equipment id',
                                                                             'equipment identifier']},
                                                'id': {'annotations': {},
                                                       'description': 'Unique identifier for the notification.',
                                                       'synonyms': ['notification id', 'alert id']},
                                                'opcuaNodeId': {'annotations': {},
                                                                'description': 'Optional canonical OPC UA signal '
                                                                               'identifier; join to '
                                                                               'signal_master.opcua_node_id when '
                                                                               'present.',
                                                                'synonyms': ['opc ua node id', 'node id']},
                                                'reportedAt': {'annotations': {},
                                                               'description': 'Timestamp when the notification '
                                                                              'was reported.',
                                                               'synonyms': ['reported time', 'report time']},
                                                'reportedByOid': {'annotations': {},
                                                                  'description': 'Object identifier of the '
                                                                                 'reporter.',
                                                                  'synonyms': ['reported by', 'reporter oid']},
                                                'severity': {'annotations': {},
                                                             'description': 'Severity level assigned to the '
                                                                            'notification.',
                                                             'synonyms': ['priority', 'severity level']},
                                                'status': {'annotations': {},
                                                           'description': 'Lifecycle or operating status '
                                                                          "recorded by this entity's source; not "
                                                                          'automatically inferred from telemetry '
                                                                          'quality.',
                                                           'synonyms': ['state', 'notification status']},
                                                'summary': {'annotations': {},
                                                            'description': 'Short notification summary.',
                                                            'synonyms': ['summary', 'message']}},
                                 'synonyms': ['Maintenance Alert',
                                              'Alert',
                                              'Notification',
                                              'Maintenance Notice',
                                              'Maintenance Alert Record']},
     'EquipmentType': {
         'description': 'Authoritative equipment category derived from the equipment registry. '
                        'Connects inventory eligibility by equipment-type code, not installed parts or verified fit.',
         'annotations': {'domain': 'asset', 'modelRole': 'dimension',
                         'preferredUsage': 'equipment categories and spare-parts inventory by category',
                         'queryHint': 'SparePart_for_equipment_type means category eligibility only. '
                                      'It does not prove a part fits a specific model, is installed, or is reserved.'},
         'synonyms': ['Equipment Category', 'Asset Type'],
         'properties': {
             'equipment_type_code': {'description': 'Canonical equipment-type code from the asset registry.'},
             'equipment_type_name': {'description': 'Authoritative display name of the equipment type.'},
         }},
     'SparePart': {'annotations': {'domain': 'maintenance',
                                   'modelRole': 'inventory',
                                   'preferredUsage': 'stock availability, replenishment, and part compatibility',
                                   'queryHint': 'Use the bound operational SQL inventory. Do not infer an '
                                                'equipment foreign key from an equipment-type label.'},
                   'description': 'Inventory of spare parts and stock levels bound to the independent '
                                  'operational SQL database. equipmentType describes compatibility, not '
                                  'ownership by a particular equipment_id.',
                   'properties': {'category': {'annotations': {},
                                               'description': 'Spare part category.',
                                               'synonyms': ['type', 'part category']},
                                  'equipmentType': {'annotations': {},
                                                    'description': 'equipment type this part supports.',
                                                    'synonyms': ['equipment type', 'asset type']},
                                  'id': {'annotations': {},
                                         'description': 'Unique identifier for the spare part record.',
                                         'synonyms': ['id']},
                                  'lastRestockedAt': {'annotations': {},
                                                      'description': 'Timestamp when the part was last '
                                                                     'restocked.',
                                                      'synonyms': ['last restock time', 'restocked at']},
                                  'name': {'annotations': {},
                                           'description': 'Spare part name.',
                                           'synonyms': ['name']},
                                  'partNumber': {'annotations': {},
                                                 'description': 'Manufacturer or catalog part number.',
                                                 'synonyms': ['part no', 'catalog number']},
                                  'quantityOnHand': {'annotations': {},
                                                     'description': 'Available quantity in stock.',
                                                     'synonyms': ['on hand', 'quantity']},
                                  'reorderLevel': {'annotations': {},
                                                   'description': 'Threshold at which reorder is needed.',
                                                   'synonyms': ['reorder threshold', 'reorder point']},
                                  'storageLocation': {'annotations': {},
                                                      'description': 'Storage location of the spare part.',
                                                      'synonyms': ['warehouse location', 'bin location']},
                                  'unitCostUsd': {'annotations': {},
                                                  'description': 'Unit cost in U.S. dollars.',
                                                  'synonyms': ['unit cost', 'cost usd']}},
                   'synonyms': ['Spare Part', 'Part', 'Inventory Item', 'Replacement Part']},
     'WeatherArea': {'annotations': {'modelRole': 'dimension',
                                     'preferredUsage': 'spatial lookup and area grouping',
                                     'queryHint': 'Use WeatherArea to group or target area-based weather '
                                                  'summaries.'},
                     'description': 'Reusable spatial weather region, typically facility-centric or '
                                    'analysis-centric. Use as the lookup dimension for areal weather queries and '
                                    'forecast summarization.',
                     'properties': {'area_id': {'annotations': {},
                                                'description': 'Unique identifier for the weather area.',
                                                'synonyms': ['area id']},
                                    'area_name': {'annotations': {},
                                                  'description': 'Display name of the weather area.',
                                                  'synonyms': ['area name']},
                                    'crs': {'annotations': {},
                                            'description': 'Coordinate reference system used by the geometry.',
                                            'synonyms': ['spatial reference', 'coordinate system']},
                                    'geometry_geojson': {'annotations': {},
                                                         'description': 'GeoJSON geometry describing the area '
                                                                        'boundary.',
                                                         'synonyms': ['geometry geojson']},
                                    'metadata_json': {'annotations': {},
                                                      'description': 'Additional area metadata encoded as JSON.',
                                                      'synonyms': ['metadata json']},
                                    'updated_at_utc': {'annotations': {},
                                                       'description': 'Last update timestamp for the area '
                                                                      'definition.',
                                                       'synonyms': ['updated at utc']}},
                     'synonyms': ['Weather Zone',
                                  'Catchment Area',
                                  'Area of Interest',
                                  'Radius Area',
                                  'Weather Region']},
     'WeatherAreaMetric': {'annotations': {'grain': 'one row per area-variable-source-run-valid time',
                                           'modelRole': 'fact',
                                           'preferredUsage': 'area rollup and coverage analysis',
                                           'queryHint': 'Use WeatherAreaMetric for area-based rollups and '
                                                        'coverage-weighted values.'},
                           'description': 'Computed area-level weather summary fact for facility-centered '
                                          'weather areas. Use this entity for areal rollups, coverage metrics, '
                                          'and area-weighted time-series analysis.',
                           'properties': {'aggregation_method': {'annotations': {},
                                                                 'description': 'Method used to compute the '
                                                                                'metric.',
                                                                 'synonyms': ['aggregation method',
                                                                              'calculation method']},
                                          'area_coverage_fraction': {'annotations': {},
                                                                     'description': 'Fraction of the area '
                                                                                    'covered by contributing '
                                                                                    'cells.',
                                                                     'synonyms': ['area coverage fraction']},
                                          'area_id': {'annotations': {},
                                                      'description': 'Weather area identifier for the metric.',
                                                      'synonyms': ['area identifier']},
                                          'area_weighted_value': {'annotations': {},
                                                                  'description': 'Area-weighted value for the '
                                                                                 'metric.',
                                                                  'synonyms': ['area weighted value']},
                                          'calculated_at_utc': {'annotations': {},
                                                                'description': 'Timestamp when the metric was '
                                                                               'calculated.',
                                                                'synonyms': ['calculated at utc']},
                                          'contributing_cell_count': {'annotations': {},
                                                                      'description': 'Number of contributing '
                                                                                     'cells.',
                                                                      'synonyms': ['contributing cells',
                                                                                   'cell count']},
                                          'cumulative_rainfall_volume_m3': {'annotations': {},
                                                                            'description': 'Cumulative rainfall '
                                                                                           'volume in cubic '
                                                                                           'meters.',
                                                                            'synonyms': ['cumulative rainfall '
                                                                                         'volume m3']},
                                          'cumulative_value': {'annotations': {},
                                                               'description': 'Cumulative value for the metric.',
                                                               'synonyms': ['cumulative value']},
                                          'data_kind': {'annotations': {},
                                                        'description': 'Kind of area metric data, such as '
                                                                       'observation or forecast.',
                                                        'synonyms': ['data kind', 'record kind']},
                                          'forecast_type': {'annotations': {},
                                                            'description': 'Forecast classification or scenario '
                                                                           'type.',
                                                            'synonyms': ['forecast type', 'scenario type']},
                                          'interval_hours': {'annotations': {},
                                                             'description': 'Forecast interval length in hours.',
                                                             'synonyms': ['interval hours']},
                                          'lead_hours': {'annotations': {},
                                                         'description': 'Forecast lead time in hours.',
                                                         'synonyms': ['lead hours']},
                                          'rainfall_volume_m3': {'annotations': {},
                                                                 'description': 'Rainfall volume in cubic '
                                                                                'meters.',
                                                                 'synonyms': ['rainfall volume m3']},
                                          'reference_time_utc': {'annotations': {},
                                                                 'description': 'Reference time associated with '
                                                                                'the forecast or rollup.',
                                                                 'synonyms': ['reference time utc']},
                                          'run_id': {'annotations': {},
                                                     'description': 'Ingestion or calculation run identifier.',
                                                     'synonyms': ['run id', 'calculation run id']},
                                          'source_id': {'annotations': {},
                                                        'description': 'Weather source used for the metric.',
                                                        'synonyms': ['source identifier']},
                                          'source_item_id': {'annotations': {},
                                                             'description': 'Source record or batch identifier '
                                                                            'for the calculated area metric.',
                                                             'synonyms': ['source record id',
                                                                          'provider item id']},
                                          'unit': {'annotations': {},
                                                   'description': 'Measurement unit of the metric.',
                                                   'synonyms': ['measurement unit']},
                                          'valid_time_utc': {'annotations': {},
                                                             'description': 'Valid time for the area metric.',
                                                             'synonyms': ['valid time utc']},
                                          'variable_id': {'annotations': {},
                                                          'description': 'Meteorological variable associated '
                                                                         'with the metric.',
                                                          'synonyms': ['variable identifier']}},
                           'synonyms': ['Area Weather Metric',
                                        'Areal Weather Summary',
                                        'Area Forecast Metric',
                                        'Area Summary',
                                        'Area Weather Fact']},
     'WeatherForecast': {'annotations': {'grain': 'one row per source-variable-location-valid time and forecast '
                                                  'horizon',
                                         'modelRole': 'fact',
                                         'preferredUsage': 'forecast trend and comparison',
                                         'queryHint': 'Use WeatherForecast for forecast history; group by '
                                                      'location, variable, lead hours, and valid time.'},
                         'description': 'Canonical historical weather forecast fact at '
                                        'source-variable-location-time grain. Use this entity for forecast trend '
                                        'analysis, horizon comparisons, and scenario evaluation.',
                         'properties': {'ensemble_member': {'annotations': {},
                                                            'description': 'Forecast ensemble member identifier.',
                                                            'synonyms': ['ensemble member']},
                                        'ingested_at_utc': {'annotations': {},
                                                            'description': 'Ingested at utc recorded by the '
                                                                           'bound WeatherForecast source.',
                                                            'synonyms': ['ingested at utc']},
                                        'interval_hours': {'annotations': {},
                                                           'description': 'Forecast interval length in hours.',
                                                           'synonyms': ['interval hours']},
                                        'latitude': {'annotations': {},
                                                     'description': 'Latitude for the forecast location.',
                                                     'synonyms': ['latitude']},
                                        'lead_hours': {'annotations': {},
                                                       'description': 'Forecast lead time in hours.',
                                                       'synonyms': ['forecast horizon hours']},
                                        'location_id': {'annotations': {},
                                                        'description': 'Location or facility identifier '
                                                                       'associated with the forecast.',
                                                        'synonyms': ['location id']},
                                        'longitude': {'annotations': {},
                                                      'description': 'Longitude for the forecast location.',
                                                      'synonyms': ['longitude']},
                                        'reference_time_utc': {'annotations': {},
                                                               'description': 'Model or reference time for the '
                                                                              'forecast.',
                                                               'synonyms': ['reference time utc']},
                                        'run_id': {'annotations': {},
                                                   'description': 'Ingestion run identifier for the forecast.',
                                                   'synonyms': ['run id', 'load run id']},
                                        'source_id': {'annotations': {},
                                                      'description': 'Weather source that produced the forecast.',
                                                      'synonyms': ['source id']},
                                        'source_item_id': {'annotations': {},
                                                           'description': 'Source record identifier for the '
                                                                          'forecast.',
                                                           'synonyms': ['source record id', 'provider item id']},
                                        'unit': {'annotations': {},
                                                 'description': 'Unit of the forecasted value.',
                                                 'synonyms': ['measurement unit']},
                                        'valid_time_utc': {'annotations': {},
                                                           'description': 'Valid time of the forecasted value.',
                                                           'synonyms': ['valid time']},
                                        'value': {'annotations': {},
                                                  'description': 'Forecasted measurement value.',
                                                  'synonyms': ['forecast value', 'predicted value']},
                                        'variable_id': {'annotations': {},
                                                        'description': 'Meteorological variable forecasted by '
                                                                       'the record.',
                                                        'synonyms': ['variable id']}},
                         'synonyms': ['Forecast Weather',
                                      'Weather Prediction',
                                      'Forecast Fact',
                                      'Weather Outlook',
                                      'Forecast History']},
     'WeatherIngestionRun': {'annotations': {'modelRole': 'operational fact',
                                             'preferredUsage': 'pipeline monitoring',
                                             'queryHint': 'Use WeatherIngestionRun to inspect run status, row '
                                                          'counts, and errors.'},
                             'description': 'Operational load and ingestion run record for weather sources and '
                                            'derived facts. Use as the operational trace for load status, '
                                            'counts, and errors.',
                             'properties': {'completed_at_utc': {'annotations': {},
                                                                 'description': 'Run completion timestamp.',
                                                                 'synonyms': ['completed at utc']},
                                            'data_kind': {'annotations': {},
                                                          'description': 'Type of weather data processed in the '
                                                                         'run.',
                                                          'synonyms': ['data kind', 'record kind']},
                                            'error_message': {'annotations': {},
                                                              'description': 'Error text when the run fails.',
                                                              'synonyms': ['error message']},
                                            'reference_time_utc': {'annotations': {},
                                                                   'description': 'Reference time associated '
                                                                                  'with the ingested data.',
                                                                   'synonyms': ['reference time utc']},
                                            'row_count': {'annotations': {},
                                                          'description': 'Number of rows processed by the run.',
                                                          'synonyms': ['row count']},
                                            'run_id': {'annotations': {},
                                                       'description': 'Unique identifier for the ingestion run.',
                                                       'synonyms': ['run id']},
                                            'source_id': {'annotations': {},
                                                          'description': 'Weather source processed by the run.',
                                                          'synonyms': ['source id']},
                                            'source_item_id': {'annotations': {},
                                                               'description': 'Source record or batch identifier '
                                                                              'associated with the run.',
                                                               'synonyms': ['source item id',
                                                                            'provider item id']},
                                            'started_at_utc': {'annotations': {},
                                                               'description': 'Run start timestamp.',
                                                               'synonyms': ['started at utc']},
                                            'status': {'annotations': {},
                                                       'description': 'Lifecycle or operating status recorded by '
                                                                      "this entity's source; not automatically "
                                                                      'inferred from telemetry quality.',
                                                       'synonyms': ['state']}},
                             'synonyms': ['Ingestion Job',
                                          'Load Run',
                                          'Pipeline Run',
                                          'Import Run',
                                          'Weather Load Run']},
     'WeatherLatestForecast': {'annotations': {'modelRole': 'snapshot',
                                               'preferredUsage': 'current-state shortcut',
                                               'queryHint': 'Use only for the latest forecast state and '
                                                            'distinguish target_kind location from area. '
                                                            'Historical forecasts and area rollups require their '
                                                            'authorized history sources.',
                                               'snapshotOf': 'WeatherForecast'},
                               'description': 'Snapshot helper for the latest forecast state by target and '
                                              'source. Use this only for current-state reads; use '
                                              'WeatherForecast and WeatherAreaMetric for history and rollups.',
                               'properties': {'calculated_at_utc': {'annotations': {},
                                                                    'description': 'Timestamp when the latest '
                                                                                   'forecast snapshot was '
                                                                                   'calculated.',
                                                                    'synonyms': ['calculated at utc']},
                                              'cumulative_precipitation': {'annotations': {},
                                                                           'description': 'Latest forecast '
                                                                                          'cumulative '
                                                                                          'precipitation.',
                                                                           'synonyms': ['cumulative '
                                                                                        'precipitation']},
                                              'cumulative_rainfall_volume_m3': {'annotations': {},
                                                                                'description': 'Latest forecast '
                                                                                               'cumulative '
                                                                                               'rainfall volume '
                                                                                               'in cubic meters.',
                                                                                'synonyms': ['cumulative '
                                                                                             'rainfall volume '
                                                                                             'm3']},
                                              'dew_point': {'annotations': {},
                                                            'description': 'Latest forecast dew point.',
                                                            'synonyms': ['dew point']},
                                              'lead_hours': {'annotations': {},
                                                             'description': 'Forecast lead time in hours.',
                                                             'synonyms': ['lead hours']},
                                              'ontology_key': {'annotations': {},
                                                               'description': 'Verified SHA-256 identity of the '
                                                                              'complete natural-key tuple, with '
                                                                              'UTC timestamps encoded before '
                                                                              'hashing. It is not a measurement.',
                                                               'synonyms': ['ontology key']},
                                              'precipitation': {'annotations': {},
                                                                'description': 'Latest forecast precipitation.',
                                                                'synonyms': ['precipitation']},
                                              'precipitation_interval_hours': {'annotations': {},
                                                                               'description': 'Duration in hours '
                                                                                              'represented by '
                                                                                              'this '
                                                                                              'precipitation '
                                                                                              'forecast value.',
                                                                               'synonyms': ['precipitation '
                                                                                            'interval hours']},
                                              'pressure': {'annotations': {},
                                                           'description': 'Latest forecast pressure.',
                                                           'synonyms': ['pressure']},
                                              'rainfall_volume_m3': {'annotations': {},
                                                                     'description': 'Latest forecast rainfall '
                                                                                    'volume in cubic meters.',
                                                                     'synonyms': ['rainfall volume m3']},
                                              'reference_time_utc': {'annotations': {},
                                                                     'description': 'Reference time for the '
                                                                                    'latest forecast.',
                                                                     'synonyms': ['reference time utc']},
                                              'relative_humidity': {'annotations': {},
                                                                    'description': 'Latest forecast relative '
                                                                                   'humidity.',
                                                                    'synonyms': ['relative humidity']},
                                              'solar_radiation': {'annotations': {},
                                                                  'description': 'Latest forecast solar '
                                                                                 'radiation.',
                                                                  'synonyms': ['solar radiation']},
                                              'source_id': {'annotations': {},
                                                            'description': 'Weather source for the latest '
                                                                           'forecast.',
                                                            'synonyms': ['source id']},
                                              'source_item_id': {'annotations': {},
                                                                 'description': 'Source item id recorded by the '
                                                                                'bound WeatherLatestForecast '
                                                                                'source.',
                                                                 'synonyms': ['source record id',
                                                                              'provider item id']},
                                              'target_id': {'annotations': {},
                                                            'description': 'Identifier in the namespace selected '
                                                                           'by target_kind, not an unconditional '
                                                                           'facility or area foreign key.',
                                                            'synonyms': ['target identifier',
                                                                         'area id / location id']},
                                              'target_kind': {'annotations': {},
                                                              'description': 'Forecast target discriminator: '
                                                                             'point location or spatial area. '
                                                                             'Interpret target_id in this '
                                                                             'context.',
                                                              'synonyms': ['target type', 'destination type']},
                                              'temperature': {'annotations': {},
                                                              'description': 'Latest forecast temperature.',
                                                              'synonyms': ['temperature']},
                                              'valid_time_utc': {'annotations': {},
                                                                 'description': 'Valid time for the latest '
                                                                                'forecasted value.',
                                                                 'synonyms': ['valid time utc']},
                                              'wind_direction': {'annotations': {},
                                                                 'description': 'Latest forecast wind direction.',
                                                                 'synonyms': ['wind direction']},
                                              'wind_gust': {'annotations': {},
                                                            'description': 'Latest forecast wind gust.',
                                                            'synonyms': ['wind gust']},
                                              'wind_speed': {'annotations': {},
                                                             'description': 'Latest forecast wind speed.',
                                                             'synonyms': ['wind speed']}},
                               'synonyms': ['Latest Forecast',
                                            'Current Forecast',
                                            'Forecast Snapshot',
                                            'Weather Outlook Snapshot',
                                            'Latest Forecast Snapshot']},
     'WeatherLatestObservation': {'annotations': {'modelRole': 'snapshot',
                                                  'preferredUsage': 'current-state shortcut',
                                                  'queryHint': 'Use only for the latest observation state. '
                                                               'Historical trends require the authorized '
                                                               'weather_observations history source.',
                                                  'snapshotOf': 'WeatherObservation'},
                                  'description': 'Snapshot helper for the latest weather observation state by '
                                                 'source and location. Use this only for current-state reads; '
                                                 'use WeatherObservation for history and trends.',
                                  'properties': {'calculated_at_utc': {'annotations': {},
                                                                       'description': 'Timestamp when the latest '
                                                                                      'snapshot was calculated.',
                                                                       'synonyms': ['calculated at utc']},
                                                 'dew_point': {'annotations': {},
                                                               'description': 'Latest observed dew point.',
                                                               'synonyms': ['dew point']},
                                                 'location_id': {'annotations': {},
                                                                 'description': 'Canonical registered weather '
                                                                                'point identifier; do not infer '
                                                                                'a facility association without '
                                                                                'a verified mapping.',
                                                                 'synonyms': ['location id']},
                                                 'observed_at_utc': {'annotations': {},
                                                                     'description': 'Timestamp of the latest '
                                                                                    'observed reading.',
                                                                     'synonyms': ['observed at utc']},
                                                 'ontology_key': {'annotations': {},
                                                                  'description': 'Verified SHA-256 identity of '
                                                                                 'the complete natural-key '
                                                                                 'tuple, with UTC timestamps '
                                                                                 'encoded before hashing. It is '
                                                                                 'not a measurement.',
                                                                  'synonyms': ['ontology key']},
                                                 'precipitation': {'annotations': {},
                                                                   'description': 'Latest observed '
                                                                                  'precipitation.',
                                                                   'synonyms': ['precipitation']},
                                                 'pressure': {'annotations': {},
                                                              'description': 'Latest observed pressure.',
                                                              'synonyms': ['pressure']},
                                                 'relative_humidity': {'annotations': {},
                                                                       'description': 'Latest observed relative '
                                                                                      'humidity.',
                                                                       'synonyms': ['relative humidity']},
                                                 'solar_radiation': {'annotations': {},
                                                                     'description': 'Latest observed solar '
                                                                                    'radiation.',
                                                                     'synonyms': ['solar radiation']},
                                                 'source_id': {'annotations': {},
                                                               'description': 'Weather source for the latest '
                                                                              'observation.',
                                                               'synonyms': ['source id']},
                                                 'temperature': {'annotations': {},
                                                                 'description': 'Latest observed temperature.',
                                                                 'synonyms': ['temperature']},
                                                 'wind_direction': {'annotations': {},
                                                                    'description': 'Latest observed wind '
                                                                                   'direction.',
                                                                    'synonyms': ['wind direction']},
                                                 'wind_gust': {'annotations': {},
                                                               'description': 'Latest observed wind gust.',
                                                               'synonyms': ['wind gust']},
                                                 'wind_speed': {'annotations': {},
                                                                'description': 'Latest observed wind speed.',
                                                                'synonyms': ['wind speed']}},
                                  'synonyms': ['Current Weather',
                                               'Latest Weather Reading',
                                               'Weather Snapshot',
                                               'Current Observation',
                                               'Latest Observation Snapshot']},
     'WeatherLocation': {'annotations': {'domain': 'weather',
                                         'modelRole': 'dimension',
                                         'preferredUsage': 'point geography and weather location identity'},
                         'description': 'Registered point weather location with geographic coordinates. This is '
                                        'a location lookup, not an observation or a guarantee of an available '
                                        'provider feed.',
                         'properties': {'elevation_m': {'annotations': {},
                                                        'description': 'Elevation m recorded by the bound '
                                                                       'WeatherLocation source.',
                                                        'synonyms': ['elevation m']},
                                        'latitude': {'annotations': {},
                                                     'description': 'Latitude recorded by the bound '
                                                                    'WeatherLocation source.',
                                                     'synonyms': ['latitude']},
                                        'location_id': {'annotations': {},
                                                        'description': 'Canonical registered weather point '
                                                                       'identifier; do not infer a facility '
                                                                       'association without a verified mapping.',
                                                        'synonyms': ['location id']},
                                        'location_name': {'annotations': {},
                                                          'description': 'Location name recorded by the bound '
                                                                         'WeatherLocation source.',
                                                          'synonyms': ['location name']},
                                        'longitude': {'annotations': {},
                                                      'description': 'Longitude recorded by the bound '
                                                                     'WeatherLocation source.',
                                                      'synonyms': ['longitude']},
                                        'metadata_json': {'annotations': {},
                                                          'description': 'Metadata json recorded by the bound '
                                                                         'WeatherLocation source.',
                                                          'synonyms': ['metadata json']},
                                        'updated_at_utc': {'annotations': {},
                                                           'description': 'Updated at utc recorded by the bound '
                                                                          'WeatherLocation source.',
                                                           'synonyms': ['updated at utc']}},
                         'synonyms': ['Weather Location',
                                      'Weather Station Location',
                                      'Forecast Point',
                                      'Observation Location']},
     'WeatherObservation': {'annotations': {'grain': 'one row per source-variable-location-observation time',
                                            'modelRole': 'fact',
                                            'preferredUsage': 'historical trend and aggregation',
                                            'queryHint': 'Use WeatherObservation for measured weather history; '
                                                         'aggregate by location and variable before '
                                                         'summarizing.'},
                            'description': 'Canonical historical weather observation fact at '
                                           'source-variable-location-time grain. Use this entity for trend '
                                           'analysis, anomaly detection, and time-series comparisons.',
                            'properties': {'ingested_at_utc': {'annotations': {},
                                                               'description': 'Ingested at utc recorded by the '
                                                                              'bound WeatherObservation source.',
                                                               'synonyms': ['ingested at utc']},
                                           'latitude': {'annotations': {},
                                                        'description': 'Latitude for the observation location.',
                                                        'synonyms': ['latitude']},
                                           'location_id': {'annotations': {},
                                                           'description': 'Location or facility identifier '
                                                                          'associated with the observation.',
                                                           'synonyms': ['location id']},
                                           'longitude': {'annotations': {},
                                                         'description': 'Longitude for the observation location.',
                                                         'synonyms': ['longitude']},
                                           'observed_at_utc': {'annotations': {},
                                                               'description': 'Time when the observation was '
                                                                              'recorded.',
                                                               'synonyms': ['observation time', 'observed at']},
                                           'quality': {'annotations': {},
                                                       'description': 'Quality flag for the observation.',
                                                       'synonyms': ['data quality', 'quality flag']},
                                           'run_id': {'annotations': {},
                                                      'description': 'Ingestion run identifier for the '
                                                                     'observation.',
                                                      'synonyms': ['run id', 'load run id']},
                                           'source_id': {'annotations': {},
                                                         'description': 'Weather source that produced the '
                                                                        'observation.',
                                                         'synonyms': ['source id']},
                                           'source_item_id': {'annotations': {},
                                                              'description': 'Source record identifier for the '
                                                                             'observation.',
                                                              'synonyms': ['source record id',
                                                                           'provider item id']},
                                           'unit': {'annotations': {},
                                                    'description': 'Unit of the observed value.',
                                                    'synonyms': ['measurement unit']},
                                           'value': {'annotations': {},
                                                     'description': 'Observed measurement value.',
                                                     'synonyms': ['observation value', 'measured value']},
                                           'variable_id': {'annotations': {},
                                                           'description': 'Meteorological variable measured by '
                                                                          'the observation.',
                                                           'synonyms': ['variable id']}},
                            'synonyms': ['Observed Weather',
                                         'Weather Reading',
                                         'Observation Fact',
                                         'Weather Sample',
                                         'Observation History']},
     'WeatherSource': {'annotations': {'modelRole': 'dimension',
                                       'preferredUsage': 'lookup and provenance',
                                       'queryHint': 'Use WeatherSource for source metadata and filtering, not '
                                                    'for weather measurements.'},
                       'description': 'Canonical catalog of weather providers and feed metadata. Use as the '
                                      'lookup dimension for source identity, provenance, licensing, and provider '
                                      'status.',
                       'properties': {'active': {'annotations': {},
                                                 'description': 'Whether the source is currently active.',
                                                 'synonyms': ['enabled', 'active']},
                                      'endpoint': {'annotations': {},
                                                   'description': 'Source endpoint or feed location.',
                                                   'synonyms': ['endpoint']},
                                      'license': {'annotations': {},
                                                  'description': 'License or usage terms for the weather feed.',
                                                  'synonyms': ['license']},
                                      'provider': {'annotations': {},
                                                   'description': 'Provider organization or vendor name.',
                                                   'synonyms': ['vendor', 'provider name']},
                                      'source_id': {'annotations': {},
                                                    'description': 'Unique identifier for the weather source.',
                                                    'synonyms': ['source id', 'provider id']},
                                      'source_name': {'annotations': {},
                                                      'description': 'Display name of the weather source.',
                                                      'synonyms': ['source name', 'provider name']},
                                      'source_type': {'annotations': {},
                                                      'description': 'Type of weather source, such as forecast '
                                                                     'or observation feed.',
                                                      'synonyms': ['source type', 'feed type']},
                                      'updated_at_utc': {'annotations': {},
                                                         'description': 'Last update timestamp for the source '
                                                                        'metadata.',
                                                         'synonyms': ['updated at utc']}},
                       'synonyms': ['Weather Feed',
                                    'Weather Provider',
                                    'Forecast Source',
                                    'Observation Source',
                                    'Provider Catalog',
                                    'Feed Catalog']},
     'WeatherVariable': {'annotations': {'modelRole': 'dimension',
                                         'preferredUsage': 'lookup and semantic normalization',
                                         'queryHint': 'Use WeatherVariable to decode variable IDs, canonical '
                                                      'units, and aggregation intent.'},
                         'description': 'Canonical catalog of standardized meteorological variables, units, and '
                                        'aggregation semantics. Use as the lookup dimension for weather '
                                        'measurements and forecast variables.',
                         'properties': {'aggregation_kind': {'annotations': {},
                                                             'description': 'Preferred aggregation semantics for '
                                                                            'the variable.',
                                                             'synonyms': ['aggregation kind', 'rollup kind']},
                                        'canonical_name': {'annotations': {},
                                                           'description': 'Canonical variable name used in the '
                                                                          'IQ layer.',
                                                           'synonyms': ['canonical name', 'standard name']},
                                        'canonical_unit': {'annotations': {},
                                                           'description': 'Canonical measurement unit for the '
                                                                          'variable.',
                                                           'synonyms': ['unit', 'canonical unit']},
                                        'description': {'annotations': {},
                                                        'description': 'Human-readable variable definition.',
                                                        'synonyms': ['description']},
                                        'updated_at_utc': {'annotations': {},
                                                           'description': 'Last update timestamp for the '
                                                                          'variable metadata.',
                                                           'synonyms': ['updated at utc']},
                                        'variable_id': {'annotations': {},
                                                        'description': 'Unique identifier for the meteorological '
                                                                       'variable.',
                                                        'synonyms': ['variable id', 'metric id']}},
                         'synonyms': ['Weather Metric',
                                      'Weather Measure',
                                      'Meteorological Variable',
                                      'Weather Parameter',
                                      'Variable Catalog']},
     'WorkOrder': {'annotations': {'domain': 'maintenance',
                                   'modelRole': 'operational fact',
                                   'preferredUsage': 'work management and task lifecycle',
                                   'queryHint': 'Use WorkOrder for task status, scheduling, assignment, and '
                                                'equipment or instrument targeting.'},
                   'description': 'Operational work orders raised for hydro equipment and instrumentation. Use '
                                  'this entity for the lifecycle of maintenance tasks and dispatch work.',
                   'properties': {'assignedToOid': {'annotations': {},
                                                    'description': 'Object identifier of the assigned user, when '
                                                                   'present.',
                                                    'synonyms': ['assigned to', 'assignee oid']},
                                  'completedAt': {'annotations': {},
                                                  'description': 'Timestamp when the work order was completed.',
                                                  'synonyms': ['completion time', 'completed at']},
                                  'createdAt': {'annotations': {},
                                                'description': 'Timestamp when the work order was created.',
                                                'synonyms': ['created time', 'created at']},
                                  'createdByOid': {'annotations': {},
                                                   'description': 'Object identifier of the creator.',
                                                   'synonyms': ['created by', 'creator oid']},
                                  'description': {'annotations': {},
                                                  'description': 'Work order description.',
                                                  'synonyms': ['description']},
                                  'dueAt': {'annotations': {},
                                            'description': 'Due timestamp for the work order.',
                                            'synonyms': ['due time', 'deadline']},
                                  'equipmentId': {'annotations': {},
                                                  'description': 'Canonical physical equipment identifier '
                                                                 'referencing equipment.equipment_id.',
                                                  'synonyms': ['equipment id', 'equipment identifier']},
                                  'id': {'annotations': {},
                                         'description': 'Unique identifier for the work order.',
                                         'synonyms': ['id']},
                                  'instrumentId': {'annotations': {},
                                                   'description': 'Optional physical instrument identifier '
                                                                  'referencing instruments.instrument_id.',
                                                   'synonyms': ['instrument id', 'instrument identifier']},
                                  'opcuaNodeId': {'annotations': {},
                                                  'description': 'Optional canonical OPC UA signal identifier; '
                                                                 'join to signal_master.opcua_node_id when '
                                                                 'present.',
                                                  'synonyms': ['opc ua node id', 'node id']},
                                  'priority': {'annotations': {},
                                               'description': 'Work order priority.',
                                               'synonyms': ['urgency', 'priority level']},
                                  'status': {'annotations': {},
                                             'description': 'Lifecycle or operating status recorded by this '
                                                            "entity's source; not automatically inferred from "
                                                            'telemetry quality.',
                                             'synonyms': ['state', 'work order status']},
                                  'title': {'annotations': {},
                                            'description': 'Short title of the work order.',
                                            'synonyms': ['title']},
                                  'workOrderNumber': {'annotations': {},
                                                      'description': 'Business work order number.',
                                                      'synonyms': ['work order no', 'order number']}},
                   'synonyms': ['Work Order', 'Maintenance Work Order', 'Job', 'Task', 'Work Request']},
     'equipment': {'annotations': {'agentGuide': 'Canonical equipment entity. Prefer this over signal_master '
                                                 'when the question is about the physical machine or asset '
                                                 'record.',
                                   'modelRole': 'equipment registry',
                                   'preferredUsage': 'equipment identity, maintenance targeting, and inventory',
                                   'queryHint': 'Use equipment for physical asset identity and status. Use '
                                                'signal_master for signal identity and telemetry.'},
                   'description': 'Hydro equipment registry attached to facilities and systems. Use this entity '
                                  'for physical equipment identity, status, manufacturer, model, and maintenance '
                                  'targeting. Do not use it as the signal master.',
                   'properties': {'criticality': {'annotations': {},
                                                  'description': 'Recorded equipment criticality used to '
                                                                 'prioritize maintenance; not an inferred '
                                                                 'telemetry severity.',
                                                  'synonyms': ['criticality']},
                                  'equipment_id': {'annotations': {},
                                                   'description': 'Canonical physical equipment identifier. Use '
                                                                  'it to reconcile affected signals with '
                                                                  'operational records.',
                                                   'synonyms': ['equipment id', 'asset id']},
                                  'equipment_type_code': {'annotations': {},
                                                          'description': 'Controlled equipment-class code; '
                                                                         'distinguish this code from its '
                                                                         'human-readable type name.',
                                                          'synonyms': ['type code', 'asset class code']},
                                  'equipment_type_name': {'annotations': {},
                                                          'description': 'Human-readable equipment '
                                                                         'classification, not a unique physical '
                                                                         'equipment identifier.',
                                                          'synonyms': ['type name', 'asset class name']},
                                  'facility_id': {'annotations': {},
                                                  'description': 'Canonical facility identifier linking the '
                                                                 'record to its hydro site or plant.',
                                                  'synonyms': ['facility identifier', 'plant id']},
                                  'install_date': {'annotations': {},
                                                   'description': 'Recorded installation date of the physical '
                                                                  'equipment.',
                                                   'synonyms': ['installation date', 'commission date']},
                                  'is_active': {'annotations': {},
                                                'description': 'Whether the registry record is active. This is '
                                                               'not a measurement of current health or telemetry '
                                                               'freshness.',
                                                'synonyms': ['active', 'enabled']},
                                  'manufacturer': {'annotations': {},
                                                   'description': 'Manufacturer recorded for the physical '
                                                                  'equipment.',
                                                   'synonyms': ['manufacturer']},
                                  'model': {'annotations': {},
                                            'description': 'Manufacturer model designation of the physical '
                                                           'equipment, not a 3D attachment.',
                                            'synonyms': ['equipment model']},
                                  'status': {'annotations': {},
                                             'description': 'Lifecycle or operating status recorded by this '
                                                            "entity's source; not automatically inferred from "
                                                            'telemetry quality.',
                                             'synonyms': ['equipment status', 'asset status']},
                                  'system_id': {'annotations': {},
                                                'description': 'Canonical system identifier linking the record '
                                                               'to its plant subsystem.',
                                                'synonyms': ['system identifier', 'unit id']},
                                  'tag': {'annotations': {},
                                          'description': 'Engineering tag recorded by the source; retain '
                                                         'canonical identifiers for joins rather than relying on '
                                                         'tag text.',
                                          'synonyms': ['equipment tag', 'asset tag']}},
                   'synonyms': ['Equipment', 'Machine', 'Turbine Equipment', 'Unit Equipment']},
     'facilities': {'annotations': {'domain': 'asset',
                                    'modelRole': 'dimension',
                                    'preferredUsage': 'site lookup and geographic context',
                                    'queryHint': 'Use facilities when grouping or filtering by plant/site, and '
                                                 'when joining equipment, systems, or weather location context.'},
                    'description': 'Hydro generation facility master data. Site and plant identity, geography, '
                                   'and asset hierarchy. WeatherLocation is a separate source-bound entity; do '
                                   'not assume a weather location exists for every facility.',
                    'properties': {'commissioned_date': {'annotations': {},
                                                         'description': 'Commissioning date or date label for '
                                                                        'the facility.',
                                                         'synonyms': ['commissioned date', 'commission date']},
                                   'country': {'annotations': {},
                                               'description': 'Country where the facility is located.',
                                               'synonyms': ['country', 'nation']},
                                   'elevation_m': {'annotations': {},
                                                   'description': 'Elevation in meters for the weather location.',
                                                   'synonyms': ['elevation', 'elevation meters']},
                                   'facility_id': {'annotations': {},
                                                   'description': 'Canonical facility identifier linking the '
                                                                  'record to its hydro site or plant.',
                                                   'synonyms': ['facility id', 'site id', 'plant id']},
                                   'facility_name': {'annotations': {},
                                                     'description': 'Display name of the facility.',
                                                     'synonyms': ['facility name', 'site name', 'plant name']},
                                   'lat': {'annotations': {},
                                           'description': 'Facility latitude in decimal degrees.',
                                           'synonyms': ['latitude']},
                                   'latitude': {'annotations': {},
                                                'description': 'Latitude for the facility or weather location.',
                                                'synonyms': ['lat', 'latitude']},
                                   'location_id': {'annotations': {},
                                                   'description': 'Weather location identifier associated with '
                                                                  'the facility, when present.',
                                                   'synonyms': ['location id']},
                                   'location_name': {'annotations': {},
                                                     'description': 'Weather location display name associated '
                                                                    'with the facility, when present.',
                                                     'synonyms': ['location name']},
                                   'lon': {'annotations': {},
                                           'description': 'Facility longitude in decimal degrees.',
                                           'synonyms': ['longitude']},
                                   'longitude': {'annotations': {},
                                                 'description': 'Longitude for the facility or weather location.',
                                                 'synonyms': ['lon', 'longitude']},
                                   'metadata_json': {'annotations': {},
                                                     'description': 'Additional facility or location metadata '
                                                                    'encoded as JSON.',
                                                     'synonyms': ['metadata', 'json metadata']},
                                   'type': {'annotations': {},
                                            'description': 'facilities type or operating class.',
                                            'synonyms': ['facility type', 'plant type']},
                                   'updated_at_utc': {'annotations': {},
                                                      'description': 'Last update timestamp for the facility or '
                                                                     'weather location record.',
                                                      'synonyms': ['updated at', 'last updated']}},
                    'synonyms': ['Plant', 'Power Station', 'Hydro Plant', 'Hydropower Facility', 'Site']},
     'instruments': {'annotations': {'domain': 'asset',
                                     'modelRole': 'instrument registry',
                                     'preferredUsage': 'instrument inventory and physical equipment hierarchy',
                                     'queryHint': 'Use instruments for physical instrumentation and '
                                                  'instrument_id joins. Use signal_master for OPC UA telemetry.'},
                     'description': 'Physical instrument registry attached to equipment, systems, and '
                                    'facilities. Use signal_master for canonical signal identity and telemetry '
                                    'history.',
                     'properties': {'equipment_id': {'annotations': {},
                                                     'description': 'Canonical physical equipment identifier. '
                                                                    'Use it to reconcile affected signals with '
                                                                    'operational records.',
                                                     'synonyms': ['equipment id', 'asset id']},
                                    'event_time': {'annotations': {},
                                                   'description': 'Timestamp of the telemetry sample. Preserve '
                                                                  "this sample's alignment with value and "
                                                                  'quality and state the time zone.',
                                                   'synonyms': ['event timestamp', 'sample time']},
                                    'facility_id': {'annotations': {},
                                                    'description': 'Canonical facility identifier linking the '
                                                                   'record to its hydro site or plant.',
                                                    'synonyms': ['facility id', 'plant id']},
                                    'instrument_id': {'annotations': {},
                                                      'description': 'Physical instrument identifier used for '
                                                                     'the signal-to-instrument relationship.',
                                                      'synonyms': ['signal id', 'point id', 'tag id']},
                                    'instrument_type': {'annotations': {},
                                                        'description': 'Recorded physical instrumentation '
                                                                       'classification; use signal_type for the '
                                                                       'canonical signal category.',
                                                        'synonyms': ['signal type', 'instrument class']},
                                    'is_active': {'annotations': {},
                                                  'description': 'Whether the registry record is active. This is '
                                                                 'not a measurement of current health or '
                                                                 'telemetry freshness.',
                                                  'synonyms': ['active', 'enabled']},
                                    'opcua_node_id': {'annotations': {},
                                                      'description': 'Canonical OPC UA signal identifier shared '
                                                                     'by the static registry and telemetry event '
                                                                     'source.',
                                                      'synonyms': ['OPC UA node', 'node id', 'OPC node']},
                                    'quality': {'annotations': {},
                                                'description': 'Quality of the same telemetry sample as '
                                                               'event_time and value. A BAD sample is not proof '
                                                               'of a mechanical fault.',
                                                'synonyms': ['data quality', 'quality flag']},
                                    'signal_type': {'annotations': {},
                                                    'description': 'Canonical signal category used for signal '
                                                                   'inventory and classification, not a '
                                                                   'telemetry sample value.',
                                                    'synonyms': ['signal class', 'measurement type']},
                                    'system_id': {'annotations': {},
                                                  'description': 'Canonical system identifier linking the record '
                                                                 'to its plant subsystem.',
                                                  'synonyms': ['system id', 'unit id']},
                                    'tag': {'annotations': {},
                                            'description': 'Engineering tag recorded by the source; retain '
                                                           'canonical identifiers for joins rather than relying '
                                                           'on tag text.',
                                            'synonyms': ['asset tag', 'signal tag', 'point tag']},
                                    'unit': {'annotations': {},
                                             'description': 'Engineering unit of the signal or measurement. '
                                                            'Interpret values in this unit and do not silently '
                                                            'mix incompatible units.',
                                             'synonyms': ['measurement unit']},
                                    'value': {'annotations': {},
                                              'description': 'Historical telemetry value for this signal and '
                                                             'event_time, interpreted using its canonical unit.',
                                              'synonyms': ['signal value', 'sample value', 'telemetry value']}},
                     'synonyms': ['Instrument',
                                  'Physical Instrument',
                                  'Equipment Instrument',
                                  'Sensor Registry']},
     'signal_master': {'annotations': {'agentGuide': 'Canonical signal master entity. Prefer this over equipment '
                                                     'when the question is about OPC UA points, tags, units, '
                                                     'telemetry, or signal classification.',
                                       'modelRole': 'signal master',
                                       'preferredUsage': 'signal inventory, point lookup, and telemetry history',
                                       'queryHint': 'Use signal_master for signal identity and historical '
                                                    'telemetry. BAD today means any BAD sample in the stated '
                                                    'time window, not only the latest sample. Reconcile work '
                                                    'orders by equipment_id and opcua_node_id.'},
                       'description': 'Canonical OPC UA signal registry keyed by opcua_node_id. Static Lakehouse '
                                      'identity and equipment context are stitched to Eventhouse telemetry by '
                                      'that same identifier. event_time, value, and quality are aligned '
                                      'time-series properties, not independent latest scalar values.',
                       'properties': {'equipment_id': {'annotations': {},
                                                       'description': 'Canonical physical equipment identifier. '
                                                                      'Use it to reconcile affected signals with '
                                                                      'operational records.',
                                                       'synonyms': ['equipment id', 'asset id']},
                                      'event_time': {'annotations': {},
                                                     'description': 'Timestamp of the telemetry sample. Preserve '
                                                                    "this sample's alignment with value and "
                                                                    'quality and state the time zone.',
                                                     'synonyms': ['event timestamp', 'sample time']},
                                      'facility_id': {'annotations': {},
                                                      'description': 'Canonical facility identifier linking the '
                                                                     'record to its hydro site or plant.',
                                                      'synonyms': ['facility id', 'plant id']},
                                      'instrument_id': {'annotations': {},
                                                        'description': 'Physical instrument identifier used for '
                                                                       'the signal-to-instrument relationship.',
                                                        'synonyms': ['signal id', 'point id', 'tag id']},
                                      'instrument_type': {'annotations': {},
                                                          'description': 'Recorded physical instrumentation '
                                                                         'classification; use signal_type for '
                                                                         'the canonical signal category.',
                                                          'synonyms': ['signal type', 'instrument class']},
                                      'is_active': {'annotations': {},
                                                    'description': 'Whether the registry record is active. This '
                                                                   'is not a measurement of current health or '
                                                                   'telemetry freshness.',
                                                    'synonyms': ['active', 'enabled']},
                                      'opcua_node_id': {'annotations': {},
                                                        'description': 'Canonical OPC UA signal identifier '
                                                                       'shared by the static registry and '
                                                                       'telemetry event source.',
                                                        'synonyms': ['OPC UA node', 'node id', 'OPC node']},
                                      'quality': {'annotations': {},
                                                  'description': 'Quality of the same telemetry sample as '
                                                                 'event_time and value. A BAD sample is not '
                                                                 'proof of a mechanical fault.',
                                                  'synonyms': ['data quality', 'quality flag']},
                                      'signal_type': {'annotations': {},
                                                      'description': 'Canonical signal category used for signal '
                                                                     'inventory and classification, not a '
                                                                     'telemetry sample value.',
                                                      'synonyms': ['signal class', 'measurement type']},
                                      'system_id': {'annotations': {},
                                                    'description': 'Canonical system identifier linking the '
                                                                   'record to its plant subsystem.',
                                                    'synonyms': ['system id', 'unit id']},
                                      'tag': {'annotations': {},
                                              'description': 'Engineering tag recorded by the source; retain '
                                                             'canonical identifiers for joins rather than '
                                                             'relying on tag text.',
                                              'synonyms': ['asset tag', 'signal tag', 'point tag']},
                                      'unit': {'annotations': {},
                                               'description': 'Engineering unit of the signal or measurement. '
                                                              'Interpret values in this unit and do not silently '
                                                              'mix incompatible units.',
                                               'synonyms': ['measurement unit']},
                                      'value': {'annotations': {},
                                                'description': 'Historical telemetry value for this signal and '
                                                               'event_time, interpreted using its canonical '
                                                               'unit.',
                                                'synonyms': ['signal value', 'sample value', 'telemetry value']}},
                       'synonyms': ['Signal Master', 'Signal Registry', 'Tag', 'Signal', 'Sensor', 'Point']},
     'systems': {'annotations': {'domain': 'asset',
                                 'modelRole': 'dimension',
                                 'preferredUsage': 'system grouping and asset hierarchy',
                                 'queryHint': 'Use systems when aggregating equipment or instruments by '
                                              'subsystem or plant.'},
                 'description': 'Operational systems belonging to hydro facilities, such as turbine or plant '
                                'subsystems. Use this entity as the system-level grouping above equipment and '
                                'instruments.',
                 'properties': {'facility_id': {'annotations': {},
                                                'description': 'Canonical facility identifier linking the record '
                                                               'to its hydro site or plant.',
                                                'synonyms': ['facility id', 'plant id']},
                                'oag_rds_system_code': {'annotations': {},
                                                        'description': 'Operational or external code for the '
                                                                       'system.',
                                                        'synonyms': ['system code',
                                                                     'RDS code',
                                                                     'external system code']},
                                'system_id': {'annotations': {},
                                              'description': 'Canonical system identifier linking the record to '
                                                             'its plant subsystem.',
                                              'synonyms': ['system id', 'unit id']},
                                'system_name': {'annotations': {},
                                                'description': 'Display name of the system.',
                                                'synonyms': ['system name', 'unit name']}},
                 'synonyms': ['Turbine System', 'Asset System', 'Production System', 'Unit', 'Subsystem']}}


def _semantic_description(text, start, indent):
    lines = text[:start].splitlines()
    description = []
    while lines and lines[-1].startswith(indent + "///"):
        description.append(lines.pop()[len(indent) + 3:].strip())
    return " ".join(reversed(description)).strip()


def _enrich_semantic_block(block, metadata, indent):
    child = indent + "\t"
    additions = []
    synonyms = {
        value.strip("'").replace("''", "'").casefold()
        for value in re.findall(r"(?m)^" + re.escape(child) + r"synonym (.+?)[ \t]*$", block)
    }
    for synonym in metadata.get("synonyms", []):
        if synonym.casefold() not in synonyms:
            additions.append(child + "synonym " + _quote_name(synonym))
            synonyms.add(synonym.casefold())
    annotations = set(re.findall(
        r"(?m)^" + re.escape(child) + r"annotation ([A-Za-z_][A-Za-z_0-9]*)[ \t]*=", block))
    for key, value in metadata.get("annotations", {}).items():
        if key not in annotations:
            if "\n" in value or "\r" in value:
                raise RuntimeError(f"Multiline semantic annotation {key} needs explicit TMDL authoring.")
            additions.append(f"{child}annotation {key} = {value}")
    if not additions:
        return block
    lines = block.splitlines(keepends=True)
    trailing = []
    while lines and (not lines[-1].strip() or lines[-1].startswith(indent + "///")):
        trailing.append(lines.pop())
    return "".join(lines).rstrip() + "\n\n" + "\n".join(additions) + "\n" + "".join(reversed(trailing))


def _project_business_rules():
    return [
        ("Hydro_AnyBadSampleInWindow",
         "BAD telemetry analysis must include any BAD-quality sample in the requested time window, "
         "not only each signal's latest sample. State the evaluated time window and time zone.",
         {"signal_master": ("opcua_node_id", "event_time", "quality")}),
        ("Hydro_AlignedTelemetryHistory",
         "Join static signal identity and telemetry by the same canonical opcua_node_id. "
         "Keep event_time, value, and quality aligned to the same historical event; "
         "never combine independently selected latest values into a fictitious sample.",
         {"signal_master": ("opcua_node_id", "event_time", "value", "quality")}),
        ("Hydro_NoDuplicateMaintenance",
         "Reconcile affected signals with physical equipment and existing work orders by canonical "
         "equipment_id and opcua_node_id. Before proposing a new work order, identify existing "
         "open work covering the same issue and avoid duplicate maintenance.",
         {"signal_master": ("equipment_id", "opcua_node_id"),
          "WorkOrder": ("equipmentId", "opcuaNodeId", "status")}),
        ("Hydro_EvidenceBeforeRootCause",
         "BAD telemetry quality indicates an evidence-quality problem, not proof of mechanical failure. "
         "Separate observations, root-cause hypotheses, supporting evidence, and missing evidence. "
         "Do not present a hypothesis as a confirmed root cause.",
         {"signal_master": ("quality", "event_time", "value"), "equipment": ("equipment_id",)}),
        ("Hydro_OperatorApprovedWorkOrders",
         "Work-order recommendations are drafts. Do not create, modify, assign, or close operational "
         "records without the operator's explicit approval and successful authorized execution. "
         "Configuration or publication success is not evidence that a write or delivery occurred.",
         {"WorkOrder": ("title", "description", "status", "equipmentId")}),
        ("Hydro_WeatherEvidenceLimits",
         "Latest weather snapshots are for current-state questions, not historical trends. "
         "Report source, location or area, timestamp, units, and forecast horizon. "
         "Missing weather or failed ingestion is unavailable evidence, not zero weather or proof "
         "of causation for equipment faults.",
         {"WeatherLatestObservation": ("source_id", "location_id", "observed_at_utc"),
          "WeatherLatestForecast": ("source_id", "target_kind", "target_id", "valid_time_utc")}),
    ]


def _enrich_project_semantics(parts):
    result = _parts_by_path(parts)
    catalog = _project_semantics()
    present = {}
    for name, metadata in catalog.items():
        path = f"entities/{name}.tmdl"
        if path not in result:
            continue
        text = _decode_part(result[path])
        entities = _object_spans(text, "entity", 0)
        if len(entities) != 1 or entities[0][0] != name:
            raise RuntimeError(f"Semantic metadata cannot change ambiguous entity identity in {path}.")
        properties = _object_spans(text, "property", 4)
        present[name] = {item[0] for item in properties}
        for prop, start, end, block in reversed(properties):
            spec = metadata.get("properties", {}).get(prop)
            if not spec:
                continue
            replacement = _enrich_semantic_block(block, spec, "\t")
            if spec.get("description") and not _semantic_description(text, start, "\t"):
                replacement = "\t/// " + spec["description"] + "\n" + replacement
            text = text[:start] + replacement + text[end:]
        entity = _object_spans(text, "entity", 0)[0]
        replacement = _enrich_semantic_block(entity[3], metadata, "")
        if metadata.get("description") and not _semantic_description(text, entity[1], ""):
            replacement = "/// " + metadata["description"] + "\n" + replacement
        text = text[:entity[1]] + replacement + text[entity[2]:]
        if text != _decode_part(result[path]):
            result[path] = _encode_part(path, text)
    if "model.tmdl" not in result:
        raise RuntimeError("Project semantics require the existing TMDL model.")
    model = _decode_part(result["model.tmdl"])
    for name, statement, references in _project_business_rules():
        if not set(references).issubset(present):
            continue
        rule = [f"rule {name}", f"\tstatement: {statement}"]
        for entity, properties in references.items():
            if not set(properties).issubset(present[entity]):
                raise RuntimeError(f"Business rule {name} references missing properties of {entity}.")
            rule.extend(("", f"\truleReferencedEntity {entity}", "\t\tpropertyScope: specific", ""))
            rule.extend(f"\t\truleReferencedProperty {prop}" for prop in properties)
        path = f"rules/{name}.tmdl"
        wanted = "\n".join(rule) + "\n"
        if path in result:
            if _structural_lines(wanted) - _structural_lines(_decode_part(result[path])):
                raise RuntimeError(f"Existing project business rule {name} conflicts; preserve and review it explicitly.")
        else:
            result[path] = _encode_part(path, wanted)
        model = _append_model_ref(model, "rule", name)
    if model != _decode_part(result["model.tmdl"]):
        result["model.tmdl"] = _encode_part("model.tmdl", model)
    return list(result.values())


def _verify_semantic_descriptions(expected, actual):
    retained = _parts_by_path(actual)
    managed = {f"entities/{name}.tmdl" for name in _project_semantics()}
    for path, part in _parts_by_path(expected).items():
        if path not in managed:
            continue
        before, after = _decode_part(part), _decode_part(retained[path])
        for kind, depth, indent in (("entity", 0, ""), ("property", 4, "\t")):
            objects = {name: start for name, start, _, _ in _object_spans(after, kind, depth)}
            for name, start, _, _ in _object_spans(before, kind, depth):
                description = _semantic_description(before, start, indent)
                if description and (name not in objects
                                    or _semantic_description(after, objects[name], indent) != description):
                    raise RuntimeError(f"Fabric did not retain the description of {path}:{name}.")


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
            if candidate.strip() and (
                    not candidate.lstrip().startswith("//") or candidate.lstrip().startswith("///")):
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
    start = next((index for index, line in enumerate(lines)
                  if line.strip() and not line.lstrip().startswith("//")), None)
    if start is None:
        return None
    lines = lines[start:]
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
        match = re.match(r"(ref (?:entity|table|namespace|rule)|entity|table|column|property|entityRelationship|relationship|synonym|rule|ruleReferencedEntity|ruleReferencedProperty|ruleReferencedRelationship) (.+)$", statement)
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
    _verify_semantic_descriptions(expected, actual)


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
    edge_aliases = {}
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
        edge_aliases[name] = edge_alias
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
        "nodeAliases": aliases,
        "edgeAliases": edge_aliases,
        "staticPropertyCounts": {
            spec["name"]: len(spec["propertyTypes"]) for spec in entity_projections
        },
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
    return _enrich_project_semantics(list(result.values()))





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
        if any(f"tables/{name}.tmdl" in result for name in entity_to_table):
            raise RuntimeError("Existing Direct Lake tables have no verifiable source expression")
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
                if matches and (len(matches) != 1 or matches[0].casefold() != value.casefold()):
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
                f"\t\t\texpressionSource: {_quote_name(expression_name)}\n")
            result[table_path] = _encode_part(table_path, table)
        backing = _direct_setting(text, "backingTable")
        if backing and _local_name(backing) != name:
            raise RuntimeError(f"Existing {name} has a different backing table")
        if not backing:
            entity = _object_block(text, "entity", name, 0)
            if not entity:
                raise RuntimeError(f"Missing managed entity declaration {name!r}")
            first_line, rest = entity.split("\n", 1)
            text = _replace_object(
                text, "entity", name, first_line + f"\n\tbackingTable: {_quote_name(name)}\n" + rest, 0)
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
        source_calls = re.findall(
            r'AzureDataExplorer\.Contents\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)',
            _without_m_comments(partition))
        if source_calls != [(CLUSTER_QUERY_URI, KQL_DB_NAME, event_table)]:
            raise RuntimeError(f"Existing {event_path} points to another Eventhouse source; inspect it before replacing")
        for annotation, expected in (("ONT_ItemId", KQL_DB_ID), ("ONT_WorkspaceId", WORKSPACE_ID),
                                     ("ONT_ItemKind", "KQLDatabase")):
            values = re.findall(r"(?m)^[ \t]+annotation " + annotation + r" = (.+?)\s*$", table_text)
            if values and values != [expected]:
                raise RuntimeError(f"Existing {event_path} has a different Eventhouse source: {annotation}")
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
        result[event_path] = (
            f"table {event_table}\n\tlineageTag: {_tmdl_tag('event-table', KQL_DB_ID, event_table)}\n\n"
            + column_text + "\n"
            + f"\tpartition {event_table} = m\n\t\tmode: directQuery\n"
              f"\t\tsource =\n\t\t\t\tlet\n"
              f'\t\t\t\t  Source = AzureDataExplorer.Contents("{CLUSTER_QUERY_URI}", "{KQL_DB_NAME}", "{event_table}")\n'
              f"\t\t\t\tin\n\t\t\t\t  Source\n")

    result[event_path] = _pin_eventhouse_table_metadata(
        result[event_path], WORKSPACE_ID, KQL_DB_ID, KQL_DB_NAME, WORKSPACE_NAME, columns,
    )
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


workspace_response = api_request("GET", f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}")
workspace_response.raise_for_status()
workspace_metadata = workspace_response.json()
if workspace_metadata.get("id") != WORKSPACE_ID or not workspace_metadata.get("displayName"):
    raise RuntimeError("Cannot verify the Eventhouse binding's workspace identity.")
WORKSPACE_NAME = workspace_metadata["displayName"]
updated_parts, changed_paths = _bind_eventhouse_parts(live_parts)
semantic_parts = _enrich_project_semantics(updated_parts)
previous_parts = {part["path"]: part for part in updated_parts}
changed_paths.update(part["path"] for part in semantic_parts if part != previous_parts.get(part["path"]))
updated_parts = semantic_parts
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

# CELL ********************

# Graph materialization is a separate portal prerequisite, not part of telemetry binding.
# Retain authoritative ownership discovery without editing or refreshing GraphModel scope.
from datetime import datetime, timezone
from urllib.parse import quote

lineage_response = api_request(
    "GET",
    f"{FABRIC_BASE_URL}/workspaces/{WORKSPACE_ID}/items/"
    f"{quote(ontology_id, safe='')}/relations/downstream?beta=true",
    timeout=120,
)
lineage_response.raise_for_status()
lineage = lineage_response.json()
if (not isinstance(lineage, dict) or not isinstance(lineage.get("items"), list)
        or not isinstance(lineage.get("relations"), list)):
    raise RuntimeError("Ontology downstream lineage response is malformed.")
children = {
    str(relation.get("itemId"))
    for relation in lineage["relations"]
    if isinstance(relation, dict)
    and str(relation.get("dependentOnItemId", "")).casefold() == ontology_id.casefold()
    and relation.get("relationType") == "CascadeDelete"
}
attached = any(
    isinstance(item, dict) and item.get("type") == "GraphIndex"
    and str(item.get("id")) in children for item in lineage["items"]
)
graph_model_id = (
    _select_attached_graph_model(ontology_id, WORKSPACE_ID, lineage, list_workspace_items())
    if attached else ""
)
graph_status = "configured-not-runtime-verified" if graph_model_id else "portal-prerequisite"
graph_status_rows = [
    ("ontology_graph_status", graph_status),
    ("ontology_graph_workspace_id", WORKSPACE_ID),
    ("ontology_graph_ontology_id", ontology_id),
    ("ontology_graph_model_id", graph_model_id),
    ("ontology_graph_verified_at", ""),
    ("ontology_graph_configuration_read_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
]
spark.createDataFrame(graph_status_rows, ["setting_name", "setting_value"]).createOrReplaceTempView(
    "ontology_graph_status_updates")
spark.sql(
    f"""
    MERGE INTO {settings_table_name} AS target
    USING ontology_graph_status_updates AS source
    ON target.setting_name = source.setting_name
    WHEN MATCHED THEN UPDATE SET target.setting_value = source.setting_value
    WHEN NOT MATCHED THEN INSERT (setting_name, setting_value)
      VALUES (source.setting_name, source.setting_value)
    """
)
print("Verified v2 ontology bindings and semantic metadata; graph status:", graph_status)
print("Graph scope is unchanged. In the selected ontology, use Manage graph, select eligible "
      "entities and relationships, Continue, and Materialize. Multiple-backing-table and "
      "unbound entities are not eligible. Do not remove telemetry bindings to make them eligible.")
print("Ontology/Data Agent execution does not universally require a materialized graph. "
      "The app's native graph canvas/tree/scopes do require an explicitly bound materialized graph; "
      "ownership configuration is not graph-query or agent-runtime certification.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
