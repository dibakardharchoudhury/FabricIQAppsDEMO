# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "929854d6-f34c-4a8e-9fdc-fb8d9b0f0666",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI_V1",
# META       "default_lakehouse_workspace_id": "cea977d9-c506-4d6d-9580-2b5536b36f06",
# META       "known_lakehouses": [
# META         {
# META           "id": "929854d6-f34c-4a8e-9fdc-fb8d9b0f0666"
# META         }
# META       ]
# META     },
# META     "environment": {}
# META   }
# META }

# MARKDOWN ********************

# # 11 — Prepare shared operational SQL and publish its Data Agent source
# 
# This notebook uses `RTI_Demo_SQLDatabase_<env_suffix>`, provisioned by NB01 alongside
# the Lakehouse in `RTI_DEMO_<env_suffix>`. Stage 2 runs it automatically after both agents
# and Weather_001, avoiding concurrent ontology publication.
# Existing marked stores retain their immutable name and ID; replacement needs explicit approval.
# The canonical deployer calls it with `seed_demo_data=False`; an existing-workspace cutover
# copies/verifies original rows, preserves the original database, and blocks stale legacy writes.
# It verifies additive v2 ontology bindings before establishing that write boundary.
# Setup and the web app's **Seed & provision** action insert missing demo records only.
# Existing operational values and compatible custom Data Agent content are preserved.
# 
# 1. **Prepare** — initialize the shared schema and perform the explicitly identified legacy
#    cutover only when an explicit legacy SQL item ID is supplied. Fresh setup needs no app SQL.
# 2. **Data Agent** — adds the operational SQL Database to the live agent and
#    **republishes**, preserving existing sources and custom settings. This step is
#    independent of NB09 runtime evidence; shared-store preparation failures prevent source switching.
#
# Lakehouse asset and weather reads use the app's read-only connector; no STID API is provisioned.
# SQL seed runs independently of Data Agent availability. The SQL source step
# always extends the provisioned Data Agent with the SQL source. Real failures are surfaced after saving the per-step results.
# Exact SQL draft/published readback confirms publication, not runtime verification.
# 
# Settings are read from / written back to `rti_demo_settings`.
# 
# > Keep `SEED_SQL` below in sync with `HydroOperationsApp/sql/seed-operational-data.sql`.
# > Only the 5 tables the web app uses are seeded.


# PARAMETERS CELL ********************

# Blank overrides use the authoritative setup settings, never an AppBackend SQL name.
sql_db_item_name = ""
seed_demo_data = True
provision_shared_store = True
legacy_sql_db_item_id = ""
project_ontology_item_id = ""

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************


# CELL ********************

# =========================
# CELL 0
# Load shared settings written by RTI_001 / RTI_002 / RTI_009
# =========================

from pyspark.sql import functions as F

if not isinstance(seed_demo_data, bool):
    raise ValueError("seed_demo_data must be a Boolean notebook parameter.")
if not isinstance(provision_shared_store, bool):
    raise ValueError("provision_shared_store must be a Boolean notebook parameter.")

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

env_suffix = first_setting("env_suffix", required=True)
configured_sql_name = first_setting("operational_sql_db_name", required=True)
sql_db_item_name = str(
    sql_db_item_name or configured_sql_name
).strip()
if sql_db_item_name == "HydroOperationalStore":
    sql_db_item_name = configured_sql_name
project_ontology_item_id = str(
    project_ontology_item_id or first_setting("ontology_id", "ontology_item_id", default="")
).strip()

# Published Data Agent created by RTI_009.
data_agent_name = first_setting("data_agent_name", default=None)
data_agent_id = first_setting("data_agent_id", default=None)

# Actor id stamped on seeded rows (any valid oid; defaults to the system/zero oid).
seed_actor_oid = first_setting("seed_actor_oid", default="00000000-0000-0000-0000-000000000000")

# Key Vault names/URIs for SPN auth (written by RTI_001).
key_vault_uri = first_setting("key_vault_uri", required=True)
key_vault_tenant_id_secret = first_setting("key_vault_tenant_id_secret", required=True)
key_vault_client_id_secret = first_setting("key_vault_client_id_secret", required=True)
key_vault_client_secret_secret = first_setting("key_vault_client_secret_secret", required=True)

print("✅ Settings loaded")
print("   Workspace ID       :", workspace_id)
print("   SQL Database item   :", sql_db_item_name)
print("   Data Agent name     :", data_agent_name)
print("   Data Agent id       :", data_agent_id or "(resolve by name)")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# CELL 1
# Seed SQL -> add SQL source to the Data Agent -> republish
# =========================

import json
import time
import uuid
import base64
import struct
from typing import Optional

import requests
import notebookutils  # Fabric notebook utility

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
DATA_AGENT_ITEM_TYPE = "DataAgent"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
LRO_POLL_INTERVAL_SECONDS = 5
LRO_MAX_WAIT_SECONDS = 300

DATASOURCE_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/"
    "definition/dataSource/1.0.0/schema.json"
)
STAGE_CONFIG_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/"
    "definition/stageConfiguration/1.0.0/schema.json"
)
DRAFT_STAGE_CONFIG_PATH = "Files/Config/draft/stage_config.json"

# A Fabric SQL Database is added to a Data Agent as a `sql_database` source. Verified against
# the portal's published datasource.json: the part lives under a `sql-database-<name>` folder
# and carries a nested schema→table→column tree. (A flat table list is accepted by the REST
# updateDefinition but 404s when the agent tries to load the source at runtime.)
SQL_DATASOURCE_TYPE = "sql_database"
SQL_DATASOURCE_FOLDER = SQL_DATASOURCE_TYPE.replace("_", "-")  # "sql-database"
SQL_DATASOURCE_PATH = f"Files/Config/draft/{SQL_DATASOURCE_FOLDER}-{sql_db_item_name}/datasource.json"
SQL_SCHEMA_NAME = "dbo"

# Only the 5 operational tables the web app actually uses.
SQL_TABLES = [
    ("WorkOrders",
     "Maintenance work orders. Columns: id, workOrderNumber, equipmentId, instrumentId, "
     "opcuaNodeId, title, description, priority (Critical/High/Medium/Low), "
     "status (Draft/Planned/Scheduled/Ready/Approved/In progress/Completed), "
     "assignedToOid, createdByOid, createdAt, dueAt, completedAt."),
    ("MaintenanceNotifications",
     "Condition/alarm notifications raised against equipment. Columns: id, equipmentId, "
     "opcuaNodeId, summary, severity (Advisory/Warning/Critical), "
     "status (Open/Acknowledged), reportedByOid, reportedAt."),
    ("Inspections",
     "Field/condition inspection records. Columns: id, equipmentId, opcuaNodeId, "
     "inspectionType (VISUAL/THERMOGRAPHIC/VIBRATION/LUBRICATION), "
     "result (PASS/ATTENTION/FAIL), findings, inspectorOid, inspectedAt, nextDueAt."),
    ("SpareParts",
     "Spare-part inventory for fleet readiness. Columns: id, partNumber, name, "
     "category (BEARING/SEAL/SENSOR/VALVE/ELECTRICAL), equipmentType, quantityOnHand, "
     "reorderLevel, storageLocation, unitCostUsd, lastRestockedAt."),
    ("Asset3DModels",
     "Digital-twin / 3D model registry per equipment. Columns: id, equipmentId, "
     "modelName, format (GLB/IFC/OBJ/USDZ), modelUrl, thumbnailUrl, fileSizeMb, "
     "version, updatedByOid, updatedAt."),
]

OPERATIONAL_INSTRUCTIONS_MARKER = f"## Operational data ({sql_db_item_name} SQL)"
OPERATIONAL_INSTRUCTIONS = (
    f"\n\n{OPERATIONAL_INSTRUCTIONS_MARKER}\n"
    "In addition to the telemetry ontology, a Fabric SQL Database "
    f"('{sql_db_item_name}') holds the plant's operational records. Use it for questions "
    "about work orders, maintenance notifications, inspections, spare-part inventory and "
    "3D/digital-twin models. Every row is keyed to an asset by `equipmentId` "
    "(e.g. `EQUIP_RTI_T001`) and, where relevant, `opcuaNodeId`. Join operational rows to "
    "live telemetry / equipment context in the ontology using `equipmentId` / `opcuaNodeId`. "
    "Examples: 'open critical work orders', 'assets with FAIL inspections', "
    "'spare parts below reorder level', 'notifications in the last 24 hours'."
)

ASSET_RESOLUTION_INSTRUCTIONS_MARKER = "### Asset & Facility Resolution (Must Follow)"
ASSET_RESOLUTION_INSTRUCTIONS = r'''

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
'''

SQL_DS_INSTRUCTIONS = (
    "Operational system-of-record for the hydro fleet (Fabric SQL Database). Prefer THIS source "
    "for anything about maintenance and asset operations; use the Eventhouse/KQL source for live "
    "telemetry values. Tables (dbo schema):\n"
    "- WorkOrders: planned/active maintenance. Open means status NOT IN ('Completed','Cancelled'); "
    "Draft is open. completedAt can be null for Completed rows and must not define open. "
    "Use status, not a status whitelist. "
    "priority in (Critical, High, Medium, Low); dueAt/completedAt are datetime2.\n"
    "- MaintenanceNotifications: raised alerts. severity in (Advisory, Warning, Critical), "
    "status in (Open, Acknowledged).\n"
    "- Inspections: inspectionType in (VISUAL, THERMOGRAPHIC, VIBRATION, LUBRICATION), "
    "result in (PASS, ATTENTION, FAIL); nextDueAt drives overdue checks.\n"
    "- SpareParts: inventory; flag rows where quantityOnHand <= reorderLevel as below reorder; "
    "category in (BEARING, SEAL, SENSOR, VALVE, ELECTRICAL).\n"
    "- Asset3DModels: one digital-twin model per asset (modelUrl, format, version).\n"
    "JOIN KEYS: equipmentId identifies the asset (e.g. 'EQUIP_RTI_T001') and matches the "
    "ontology equipment/signal_master equipment_id; opcuaNodeId (e.g. 'ns=2;s=T001.vibration_a') "
    "matches the ontology signal_master opcua_node_id, so operational rows can be correlated with "
    "live readings. All timestamps are datetime2 (UTC). Do NOT invent columns beyond those listed."
)

# ---------------------------------------------------------------------------
# Idempotent operational seed — keep in sync with
# HydroOperationsApp/sql/seed-operational-data.sql.
# ---------------------------------------------------------------------------
SEED_SQL = r"""
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @ActorOid nvarchar(200) = '$(ActorOid)';
IF @ActorOid = '$' + '(ActorOid)' OR NULLIF(@ActorOid, '') IS NULL
    SET @ActorOid = '00000000-0000-0000-0000-000000000000';

MERGE dbo.WorkOrders AS target
USING (VALUES
    ('10000000-0000-0000-0000-000000000001','WO-2841','EQUIP_RTI_T001','INST_T001_VIBRATION_A','ns=2;s=T001.vibration_a','Inspect guide bearing vibration','Inspect bearing, alignment, and lubrication after elevated vibration trend.','Critical','In progress',@ActorOid,@ActorOid,'2026-07-28T08:00:00','2026-08-01T16:00:00'),
    ('10000000-0000-0000-0000-000000000002','WO-2836','EQUIP_RTI_T002','INST_T002_INLET_PRESSURE','ns=2;s=T002.inlet_pressure','Review inlet pressure variance','Validate transducer and compare against local gauge.','High','Scheduled',@ActorOid,@ActorOid,'2026-07-27T10:00:00','2026-08-03T12:00:00'),
    ('10000000-0000-0000-0000-000000000003','WO-2829','EQUIP_RTI_T003','INST_T003_TURBINE_TEMP','ns=2;s=T003.turbine_temp','Inspect turbine cooling circuit','Check strainers, flow, and temperature sensor calibration.','Medium','Ready',NULL,@ActorOid,'2026-07-26T09:30:00','2026-08-05T12:00:00'),
    ('10000000-0000-0000-0000-000000000004','WO-2848','EQUIP_RTI_T006','INST_T006_VIBRATION_D','ns=2;s=T006.vibration_d','Fjord unit driven-end vibration check','Trend review and balancing assessment on driven-end bearing.','High','Approved',@ActorOid,@ActorOid,'2026-07-29T07:00:00','2026-08-04T16:00:00'),
    ('10000000-0000-0000-0000-000000000005','WO-2851','EQUIP_RTI_T008','INST_T008_POWER_OUTPUT','ns=2;s=T008.power_output','Investigate power output dip','Correlate output with head and guide-vane position.','Medium','Planned',NULL,@ActorOid,'2026-07-29T13:20:00','2026-08-06T12:00:00'),
    ('10000000-0000-0000-0000-000000000006','WO-2853','EQUIP_RTI_T010','INST_T010_TURBINE_SPEED','ns=2;s=T010.turbine_speed','Overspeed trip test','Scheduled governor overspeed protection test.','Low','Scheduled',@ActorOid,@ActorOid,'2026-07-30T09:00:00','2026-08-08T12:00:00'),
    ('10000000-0000-0000-0000-000000000007','WO-2857','EQUIP_RTI_T011','INST_T011_TURBINE_TEMP','ns=2;s=T011.turbine_temp','Highland unit cooling inspection','Inspect heat exchanger fouling after temperature rise.','High','In progress',@ActorOid,@ActorOid,'2026-07-30T11:00:00','2026-08-02T18:00:00'),
    ('10000000-0000-0000-0000-000000000008','WO-2860','EQUIP_RTI_T013','INST_T013_VIBRATION_A','ns=2;s=T013.vibration_a','Highland unit bearing endoscopy','Borescope guide bearing following vibration advisory.','Critical','Ready',NULL,@ActorOid,'2026-07-30T14:10:00','2026-08-03T12:00:00'),
    ('10000000-0000-0000-0000-000000000009','WO-2862','EQUIP_RTI_T015','INST_T015_INLET_PRESSURE','ns=2;s=T015.inlet_pressure','Inlet strainer differential check','Verify strainer differential pressure and clean if required.','Medium','Approved',@ActorOid,@ActorOid,'2026-07-31T08:30:00','2026-08-09T12:00:00'),
    ('10000000-0000-0000-0000-000000000010','WO-2820','EQUIP_RTI_T004','INST_T004_TURBINE_TEMP','ns=2;s=T004.turbine_temp','Annual thermographic survey','Completed annual thermographic survey; no anomalies.','Low','Completed',@ActorOid,@ActorOid,'2026-07-15T08:00:00','2026-07-20T12:00:00'),
    ('10000000-0000-0000-0000-000000000011','WO-2812','EQUIP_RTI_T007','INST_T007_VIBRATION_A','ns=2;s=T007.vibration_a','Bearing lubrication refresh','Completed lubrication service on guide bearing.','Medium','Completed',@ActorOid,@ActorOid,'2026-07-10T08:00:00','2026-07-14T12:00:00'),
    ('10000000-0000-0000-0000-000000000012','WO-2867','EQUIP_RTI_T012','INST_T012_POWER_OUTPUT','ns=2;s=T012.power_output','Efficiency test after overhaul','Post-overhaul efficiency verification test.','High','Draft',NULL,@ActorOid,'2026-07-31T15:00:00','2026-08-12T12:00:00')
) AS source(id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)
ON target.workOrderNumber = source.workOrderNumber
WHEN NOT MATCHED THEN INSERT (id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)
VALUES (source.id,source.workOrderNumber,source.equipmentId,source.instrumentId,source.opcuaNodeId,source.title,source.description,source.priority,source.status,source.assignedToOid,source.createdByOid,source.createdAt,source.dueAt);

MERGE dbo.MaintenanceNotifications AS target
USING (VALUES
    ('20000000-0000-0000-0000-000000000001','EQUIP_RTI_T001','ns=2;s=T001.vibration_a','Guide bearing vibration exceeded advisory threshold','Warning','Open',@ActorOid,'2026-07-30T07:42:00'),
    ('20000000-0000-0000-0000-000000000002','EQUIP_RTI_T002','ns=2;s=T002.inlet_pressure','Inlet pressure variance requires review','Advisory','Open',@ActorOid,'2026-07-30T06:15:00'),
    ('20000000-0000-0000-0000-000000000003','EQUIP_RTI_T006','ns=2;s=T006.vibration_d','Driven-end vibration trending upward','Warning','Open',@ActorOid,'2026-07-29T18:05:00'),
    ('20000000-0000-0000-0000-000000000004','EQUIP_RTI_T008','ns=2;s=T008.power_output','Power output below expected for head','Advisory','Acknowledged',@ActorOid,'2026-07-29T12:40:00'),
    ('20000000-0000-0000-0000-000000000005','EQUIP_RTI_T011','ns=2;s=T011.turbine_temp','Bearing temperature above normal band','Warning','Open',@ActorOid,'2026-07-30T10:20:00'),
    ('20000000-0000-0000-0000-000000000006','EQUIP_RTI_T013','ns=2;s=T013.vibration_a','Vibration advisory on guide bearing','Critical','Open',@ActorOid,'2026-07-30T13:55:00')
) AS source(id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)
ON target.id = source.id
WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)
VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.summary,source.severity,source.status,source.reportedByOid,source.reportedAt);

MERGE dbo.Inspections AS target
USING (VALUES
    ('60000000-0000-0000-0000-000000000001','EQUIP_RTI_T001',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000002','EQUIP_RTI_T001','ns=2;s=T001.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000003','EQUIP_RTI_T002',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000004','EQUIP_RTI_T002','ns=2;s=T002.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000005','EQUIP_RTI_T003',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000006','EQUIP_RTI_T003',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000007','EQUIP_RTI_T004',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000008','EQUIP_RTI_T004','ns=2;s=T004.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000009','EQUIP_RTI_T005',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000010','EQUIP_RTI_T005','ns=2;s=T005.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000011','EQUIP_RTI_T006',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000012','EQUIP_RTI_T006',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000013','EQUIP_RTI_T007',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000014','EQUIP_RTI_T007','ns=2;s=T007.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000015','EQUIP_RTI_T008',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-27T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000016','EQUIP_RTI_T008','ns=2;s=T008.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-27T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000017','EQUIP_RTI_T009',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000018','EQUIP_RTI_T009',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000019','EQUIP_RTI_T010',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000020','EQUIP_RTI_T010','ns=2;s=T010.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000021','EQUIP_RTI_T011',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000022','EQUIP_RTI_T011','ns=2;s=T011.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000023','EQUIP_RTI_T012',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000024','EQUIP_RTI_T012',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000025','EQUIP_RTI_T013',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000026','EQUIP_RTI_T013','ns=2;s=T013.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000027','EQUIP_RTI_T014',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000028','EQUIP_RTI_T014','ns=2;s=T014.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000029','EQUIP_RTI_T015',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000030','EQUIP_RTI_T015',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00')
) AS source(id,equipmentId,opcuaNodeId,inspectionType,result,findings,inspectorOid,inspectedAt,nextDueAt)
ON target.id = source.id
WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,inspectionType,result,findings,inspectorOid,inspectedAt,nextDueAt)
VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.inspectionType,source.result,source.findings,source.inspectorOid,source.inspectedAt,source.nextDueAt);

MERGE dbo.SpareParts AS target
USING (VALUES
    ('70000000-0000-0000-0000-000000000001','SP-BRG-1001','Guide bearing pad set','BEARING','TURB',6,4,'Warehouse A-01',4200.0,'2026-06-10'),
    ('70000000-0000-0000-0000-000000000002','SP-BRG-1002','Thrust bearing segment','BEARING','TURB',2,3,'Warehouse A-01',5100.0,'2026-05-22'),
    ('70000000-0000-0000-0000-000000000003','SP-SEAL-2001','Main shaft seal kit','SEAL','TURB',9,5,'Warehouse A-02',880.0,'2026-06-28'),
    ('70000000-0000-0000-0000-000000000004','SP-SEAL-2002','Guide vane seal ring','SEAL','TURB',3,4,'Warehouse A-02',640.0,'2026-04-30'),
    ('70000000-0000-0000-0000-000000000005','SP-SEN-3001','Vibration probe (eddy current)','SENSOR','TURB',12,6,'Store B-11',1350.0,'2026-07-05'),
    ('70000000-0000-0000-0000-000000000006','SP-SEN-3002','RTD temperature sensor','SENSOR','TURB',20,8,'Store B-11',210.0,'2026-07-12'),
    ('70000000-0000-0000-0000-000000000007','SP-SEN-3003','Pressure transducer','SENSOR','TURB',7,6,'Store B-12',490.0,'2026-06-18'),
    ('70000000-0000-0000-0000-000000000008','SP-VAL-4001','Guide vane servo valve','VALVE','TURB',2,2,'Warehouse A-03',7300.0,'2026-03-15'),
    ('70000000-0000-0000-0000-000000000009','SP-VAL-4002','Cooling water control valve','VALVE','TURB',5,3,'Warehouse A-03',1120.0,'2026-06-02'),
    ('70000000-0000-0000-0000-000000000010','SP-ELE-5001','Excitation control card','ELECTRICAL','TURB',4,3,'Store B-20',2650.0,'2026-05-09'),
    ('70000000-0000-0000-0000-000000000011','SP-ELE-5002','Governor PLC module','ELECTRICAL','TURB',1,2,'Store B-20',3900.0,'2026-02-27'),
    ('70000000-0000-0000-0000-000000000012','SP-ELE-5003','Field cabling loom','ELECTRICAL','TURB',14,6,'Store B-21',180.0,'2026-07-20')
) AS source(id,partNumber,name,category,equipmentType,quantityOnHand,reorderLevel,storageLocation,unitCostUsd,lastRestockedAt)
ON target.partNumber = source.partNumber
WHEN NOT MATCHED THEN INSERT (id,partNumber,name,category,equipmentType,quantityOnHand,reorderLevel,storageLocation,unitCostUsd,lastRestockedAt)
VALUES (source.id,source.partNumber,source.name,source.category,source.equipmentType,source.quantityOnHand,source.reorderLevel,source.storageLocation,source.unitCostUsd,source.lastRestockedAt);

MERGE dbo.Asset3DModels AS target
USING (VALUES
    ('80000000-0000-0000-0000-000000000001','EQUIP_RTI_T001','Andritz RTI-Turbine-A twin (T001)','GLB','https://static.poly.pizza/754e9358-fff8-4285-b80f-09b68c2f3c71.glb',NULL,0.06,'v1.1',@ActorOid,'2026-07-11T12:00:00'),
    ('80000000-0000-0000-0000-000000000002','EQUIP_RTI_T002','Voith RTI-Turbine-B twin (T002)','GLB','https://static.poly.pizza/e9fc0901-7600-48f4-881d-b546f3df4cec.glb',NULL,0.03,'v1.2',@ActorOid,'2026-07-12T12:00:00'),
    ('80000000-0000-0000-0000-000000000003','EQUIP_RTI_T003','GE Vernova RTI-Turbine-C twin (T003)','GLB','https://static.poly.pizza/e81145eb-091e-4803-9523-8611bd7e24e4.glb',NULL,0.57,'v1.3',@ActorOid,'2026-07-13T12:00:00'),
    ('80000000-0000-0000-0000-000000000004','EQUIP_RTI_T004','Toshiba RTI-Turbine-D twin (T004)','GLB','https://static.poly.pizza/7e2d13c0-e9ea-47f6-a70e-576745b2d6e0.glb',NULL,2.76,'v1.4',@ActorOid,'2026-07-14T12:00:00'),
    ('80000000-0000-0000-0000-000000000005','EQUIP_RTI_T005','Hitachi Energy RTI-Turbine-E twin (T005)','GLB','https://static.poly.pizza/e6b02958-8f16-423e-8322-036b5379ef0b.glb',NULL,0.02,'v1.5',@ActorOid,'2026-07-15T12:00:00'),
    ('80000000-0000-0000-0000-000000000006','EQUIP_RTI_T006','Andritz RTI-Turbine-A twin (T006)','GLB','https://static.poly.pizza/754e9358-fff8-4285-b80f-09b68c2f3c71.glb',NULL,0.06,'v1.6',@ActorOid,'2026-07-16T12:00:00'),
    ('80000000-0000-0000-0000-000000000007','EQUIP_RTI_T007','Voith RTI-Turbine-B twin (T007)','GLB','https://static.poly.pizza/e9fc0901-7600-48f4-881d-b546f3df4cec.glb',NULL,0.03,'v1.7',@ActorOid,'2026-07-17T12:00:00'),
    ('80000000-0000-0000-0000-000000000008','EQUIP_RTI_T008','GE Vernova RTI-Turbine-C twin (T008)','GLB','https://static.poly.pizza/e81145eb-091e-4803-9523-8611bd7e24e4.glb',NULL,0.57,'v1.8',@ActorOid,'2026-07-18T12:00:00'),
    ('80000000-0000-0000-0000-000000000009','EQUIP_RTI_T009','Toshiba RTI-Turbine-D twin (T009)','GLB','https://static.poly.pizza/7e2d13c0-e9ea-47f6-a70e-576745b2d6e0.glb',NULL,2.76,'v1.9',@ActorOid,'2026-07-19T12:00:00'),
    ('80000000-0000-0000-0000-000000000010','EQUIP_RTI_T010','Hitachi Energy RTI-Turbine-E twin (T010)','GLB','https://static.poly.pizza/e6b02958-8f16-423e-8322-036b5379ef0b.glb',NULL,0.02,'v1.10',@ActorOid,'2026-07-20T12:00:00'),
    ('80000000-0000-0000-0000-000000000011','EQUIP_RTI_T011','Andritz RTI-Turbine-A twin (T011)','GLB','https://static.poly.pizza/754e9358-fff8-4285-b80f-09b68c2f3c71.glb',NULL,0.06,'v1.11',@ActorOid,'2026-07-21T12:00:00'),
    ('80000000-0000-0000-0000-000000000012','EQUIP_RTI_T012','Voith RTI-Turbine-B twin (T012)','GLB','https://static.poly.pizza/e9fc0901-7600-48f4-881d-b546f3df4cec.glb',NULL,0.03,'v1.12',@ActorOid,'2026-07-22T12:00:00'),
    ('80000000-0000-0000-0000-000000000013','EQUIP_RTI_T013','GE Vernova RTI-Turbine-C twin (T013)','GLB','https://static.poly.pizza/e81145eb-091e-4803-9523-8611bd7e24e4.glb',NULL,0.57,'v1.13',@ActorOid,'2026-07-23T12:00:00'),
    ('80000000-0000-0000-0000-000000000014','EQUIP_RTI_T014','Toshiba RTI-Turbine-D twin (T014)','GLB','https://static.poly.pizza/7e2d13c0-e9ea-47f6-a70e-576745b2d6e0.glb',NULL,2.76,'v1.14',@ActorOid,'2026-07-24T12:00:00'),
    ('80000000-0000-0000-0000-000000000015','EQUIP_RTI_T015','Hitachi Energy RTI-Turbine-E twin (T015)','GLB','https://static.poly.pizza/e6b02958-8f16-423e-8322-036b5379ef0b.glb',NULL,0.02,'v1.15',@ActorOid,'2026-07-10T12:00:00')
) AS source(id,equipmentId,modelName,format,modelUrl,thumbnailUrl,fileSizeMb,version,updatedByOid,updatedAt)
ON target.id = source.id
WHEN NOT MATCHED THEN INSERT (id,equipmentId,modelName,format,modelUrl,thumbnailUrl,fileSizeMb,version,updatedByOid,updatedAt)
VALUES (source.id,source.equipmentId,source.modelName,source.format,source.modelUrl,source.thumbnailUrl,source.fileSizeMb,source.version,source.updatedByOid,source.updatedAt);

"""


# ---------------------------------------------------------------------------
# SPN auth (Key Vault) — Fabric + SQL tokens — and retry / LRO helpers
# ---------------------------------------------------------------------------
_token_cache = {}


def _spn_secrets():
    tenant_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_tenant_id_secret)
    client_id = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_id_secret)
    client_secret = notebookutils.credentials.getSecret(key_vault_uri, key_vault_client_secret_secret)
    if not tenant_id or not client_id or not client_secret:
        raise Exception("Unable to fetch SPN credentials from Key Vault")
    return tenant_id, client_id, client_secret


def get_spn_token(scope: str) -> str:
    """Client-credentials token for the given resource scope (cached per scope)."""
    now = time.time()
    cached = _token_cache.get(scope)
    if cached and now < cached[1]:
        return cached[0]
    tenant_id, client_id, client_secret = _spn_secrets()
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    resp = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": scope,
        },
        timeout=60,
    )
    resp.raise_for_status()
    token_json = resp.json()
    token = token_json["access_token"]
    _token_cache[scope] = (token, now + int(token_json.get("expires_in", 3600)) - 60)
    return token


def get_fabric_token() -> str:
    return get_spn_token("https://api.fabric.microsoft.com/.default")


def get_sql_token() -> str:
    return get_spn_token("https://database.windows.net/.default")


def get_headers() -> dict:
    return {"Authorization": f"Bearer {get_fabric_token()}", "Content-Type": "application/json"}


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


def list_items_of_type(item_type: str) -> list:
    """Return all workspace items of a given type (e.g. 'SQLDatabase')."""
    url = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/items"
    items = []
    params = {"type": item_type}
    while url:
        resp = api_request("GET", url, params=params)
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get("value", []) or [])
        url = body.get("continuationUri")
        params = None
    return items


def get_item_definition(item_id: str) -> dict:
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


# The Data Agent uses the SAME generic /items/{id}/getDefinition + /updateDefinition endpoints
# as RTI_009 (its create/patch flow). Only publish has a type-specific endpoint (below).
def publish_data_agent(item_id: str, published_description: str = "") -> None:
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
    encoded = {"path": path, "payload": encode_payload(obj), "payloadType": "InlineBase64"}
    for i, part in enumerate(parts):
        if part.get("path") == path:
            parts[i] = encoded
            return parts
    parts.append(encoded)
    return parts


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

"""Shared operational-store support embedded in NB01 and NB11; no local imports."""
import hashlib
import base64
import json
import re
import struct
import time

OPERATIONAL_STORE_NAME = "HydroOperationalStore"
OPERATIONAL_STORE_DESCRIPTION = "Hydro shared operational store; lifecycle independent of AppBackend."
OPERATIONAL_ARCHIVE_PREFIX = "Hydro archived operational store; verified replacement "
OPERATIONAL_DEMO_COUNTS = {
    "WorkOrders": 12, "MaintenanceNotifications": 6, "Inspections": 30,
    "SpareParts": 12, "Asset3DModels": 15,
}


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


def operational_source_path(parts, workspace_id, item_id, preferred_path):
    sources = {
        part["path"]: json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
        for part in parts
        if part["path"].startswith("Files/Config/draft/") and part["path"].endswith("/datasource.json")
    }
    matching = [path for path, source in sources.items()
                if source.get("type") == "sql_database" and source.get("artifactId") == item_id]
    if len(matching) > 1:
        raise RuntimeError("Multiple Data Agent SQL sources reference the same operational item.")
    if matching:
        path = matching[0]
        if sources[path].get("workspaceId") != workspace_id:
            raise RuntimeError("Existing operational SQL source has a conflicting workspace identity.")
        return path
    if preferred_path in sources:
        raise RuntimeError("The preferred operational SQL source path belongs to another source.")
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
    "WeatherVariable": ("weather_variables", ("variable_id",)),
    "WeatherLocation": ("weather_locations", ("location_id",)),
    "WeatherArea": ("weather_areas", ("area_id",)),
    "WeatherLatestObservation": ("weather_latest_observations", ("source_id", "location_id", "observed_at_utc")),
    "WeatherLatestForecast": ("weather_latest_forecasts",
                              ("source_id", "target_kind", "target_id", "reference_time_utc", "valid_time_utc")),
    "WeatherIngestionRun": ("weather_ingestion_runs", ("run_id",)),
    "InstrumentClassification": ("gold_instrument_classification", ("system_id", "signal_type")),
}

SQL_PROJECT_ENTITIES = {
    "WorkOrder": "WorkOrders", "Inspection": "Inspections", "SparePart": "SpareParts",
    "Asset3DModel": "Asset3DModels", "MaintenanceNotification": "MaintenanceNotifications",
}

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
        return alias
    aliases = {}
    for name, physical in SQL_PROJECT_ENTITIES.items():
        aliases[name] = add_entity(name, physical, sql_columns[physical], ("id",), "HydroProjectOperations")
    for name, (physical, keys) in WEATHER_PROJECT_ENTITIES.items():
        aliases[name] = add_entity(name, physical, weather_columns[physical], keys, "HydroProjectLakehouse")
    relationships = []
    semantic = []
    def edge(name, source, target, source_key, target_key):
        source_table = aliases[source]
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
    edge("WeatherIngestionRun_from_source", "WeatherIngestionRun", "WeatherSource", "source_id", "source_id")
    edge("InstrumentClassification_for_system", "InstrumentClassification", "systems", "system_id", "system_id")
    for path, wanted in additions.items():
        if path in texts and texts[path] != wanted:
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

from collections import Counter
from urllib.parse import urljoin, urlsplit, urlunsplit

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

def _append_model_ref(model, kind, name):
    for line in model.splitlines():
        match = re.fullmatch(r"ref " + re.escape(kind) + r" (.+)", line)
        if match and _local_name(match[1]) == name:
            return model
    return model.rstrip() + f"\nref {kind} {_quote_name(name)}\n"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# STEP A — Provision/migrate the independent store, then optionally seed it
# ---------------------------------------------------------------------------
def _parse_conn_string(conn: str) -> tuple:
    """Extract (server, database) from an ADO.NET-style connection string."""
    server = database = None
    for part in (conn or "").split(";"):
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key in ("data source", "server", "address", "addr", "network address"):
            server = value
        elif key in ("initial catalog", "database"):
            database = value
    return server, database


def resolve_sql_database() -> tuple:
    """Return the exact Fabric SQL Database requested by sql_db_item_name.

    Never fall back to another SQL Database. Multiple Rayfin apps can exist
    in the same workspace, so choosing the first database is unsafe.
    """
    item = find_item_by_name(sql_db_item_name, item_type="SQLDatabase")

    if not item:
        candidates = list_items_of_type("SQLDatabase")
        available = [
            candidate.get("displayName")
            for candidate in candidates
            if candidate.get("displayName")
        ]

        raise RuntimeError(
            f"SQL Database '{sql_db_item_name}' was not found. "
            f"Available SQL Databases: {available}. "
            "Refusing to seed a different database."
        )

    item_id = item["id"]

    resp = api_request(
        "GET",
        f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/sqlDatabases/{item_id}"
    )

    props = {}
    if resp is not None and resp.status_code == 200 and resp.content:
        props = (resp.json() or {}).get("properties", {}) or {}

    server = props.get("serverFqdn")
    database = props.get("databaseName")

    if (not server or not database) and props.get("connectionString"):
        cs_server, cs_database = _parse_conn_string(props["connectionString"])
        server = server or cs_server
        database = database or cs_database

    if not server or not database:
        raise RuntimeError(
            f"Could not resolve the connection information for SQL Database "
            f"'{sql_db_item_name}'."
        )

    return item_id, server, database


def fetch_sql_table_schema(server: str, database: str, tables: list) -> dict:
    """Return {table: [(column, data_type), ...]} for the given dbo tables, ordered by
    ORDINAL_POSITION — used to build the Data Agent's nested schema→table→column tree.
    Read-only INFORMATION_SCHEMA query; it never touches the seed data."""
    import pyodbc  # available in the Fabric Spark runtime

    if not tables:
        return {}
    token = get_sql_token()
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256  # driver attribute for AAD access-token auth
    driver = "ODBC Driver 18 for SQL Server"
    conn_str = (
        f"Driver={{{driver}}};Server={server};Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60"
    )
    placeholders = ",".join("?" for _ in tables)
    connection = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = ? AND TABLE_NAME IN ({placeholders}) "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            SQL_SCHEMA_NAME, *tables,
        )
        schema = {t: [] for t in tables}
        for table_name, column_name, data_type in cursor.fetchall():
            schema.setdefault(table_name, []).append((column_name, data_type))
        cursor.close()
        return schema
    finally:
        connection.close()


def seed_sql_database(server: str, database: str) -> dict:
    """Insert missing demo rows and verify existing records in one SQL transaction."""
    import pyodbc  # available in the Fabric Spark runtime

    token = get_sql_token()
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256  # driver attribute for AAD access-token auth

    driver = "ODBC Driver 18 for SQL Server"
    conn_str = (
        f"Driver={{{driver}}};Server={server};Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60"
    )
    connection = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
    try:
        receipt = seed_operational_records(connection, SEED_SQL.replace("$(ActorOid)", seed_actor_oid))
        print("OPERATIONAL_SEED_RECEIPT=" + json.dumps(receipt, sort_keys=True))
        return receipt
    finally:
        connection.close()


def extend_selected_project_ontology():
    from pyspark.sql import functions as sql_functions
    from urllib.parse import urlparse

    if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", project_ontology_item_id):
        raise RuntimeError("Project ontology extension requires the explicitly selected ontology item ID.")
    base = f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}/ontologies/{project_ontology_item_id}"
    metadata = api_request("GET", base)
    metadata.raise_for_status()
    item = metadata.json()
    generation = item.get("properties", {}).get("generation")
    if item.get("id") != project_ontology_item_id or type(generation) is not int or generation != 2:
        raise RuntimeError("Project extension requires verified live numeric Ontology generation 2.")
    def definition_request(action, parts=None):
        response = api_request("POST", base + "/" + action, data={"definition": {"parts": parts}} if parts else None)
        if not response.ok:
            raise RuntimeError(f"Ontology {action} failed (HTTP {response.status_code}): {response.text}")
        if response.status_code == 202:
            location = response.headers.get("Location", "")
            parsed = urlparse(location)
            if parsed.scheme != "https" or parsed.hostname != "api.fabric.microsoft.com" or not re.fullmatch(
                    r"/v1/operations/[0-9a-fA-F-]{36}", parsed.path):
                raise RuntimeError("Ontology definition operation returned an unexpected location.")
            wait_for_lro(location)
            if action == "getDefinition":
                response = api_request("GET", location + "/result")
                response.raise_for_status()
        return response.json() if response.content else {}
    before = definition_request("getDefinition")["definition"]["parts"]
    def primitive(source_type):
        value = source_type.lower()
        if value in ("string", "varchar", "nvarchar", "uniqueidentifier", "char", "nchar"):
            return "string"
        if value in ("int", "long", "bigint", "smallint", "tinyint", "short", "byte"):
            return "int64"
        if value in ("float", "double", "real"):
            return "double"
        if value in ("boolean", "bit"):
            return "boolean"
        if value.startswith("decimal") or value in ("numeric", "money", "smallmoney"):
            return "decimal"
        if value in ("timestamp", "timestamp_ntz", "date", "datetime", "datetime2", "datetimeoffset", "smalldatetime"):
            return "dateTime"
        raise RuntimeError(f"Project ontology cannot safely project source type {source_type!r}.")
    weather_columns = {}
    for _name, (physical, keys) in WEATHER_PROJECT_ENTITIES.items():
        data = spark.read.table(physical)
        weather_columns[physical] = {field.name: primitive(field.dataType.simpleString()) for field in data.schema.fields}
        invalid = None
        for key in keys:
            condition = sql_functions.col(key).isNull()
            invalid = condition if invalid is None else invalid | condition
        if data.where(invalid).limit(1).count() or data.groupBy(*keys).count().where("count > 1").limit(1).count():
            raise RuntimeError(f"Weather ontology identity is null or nonunique for {physical}: {keys}.")
        if len(keys) > 1:
            expression = project_identity_expression(keys)
            if "ontology_key" not in data.columns:
                spark.sql(f"ALTER TABLE `{physical}` ADD COLUMNS (ontology_key STRING)")
            elif weather_columns[physical]["ontology_key"] != "string":
                raise RuntimeError(f"Existing ontology_key has an incompatible type in {physical}.")
            elif data.where(sql_functions.col("ontology_key").isNotNull()
                            & ~sql_functions.col("ontology_key").eqNullSafe(sql_functions.expr(expression))).limit(1).count():
                raise RuntimeError(f"Existing ontology_key conflicts with its natural source identity in {physical}.")
            spark.sql(f"UPDATE `{physical}` SET ontology_key = {expression} "
                      "WHERE ontology_key IS NULL")
            data = spark.read.table(physical)
            if (data.where("ontology_key IS NULL").limit(1).count()
                    or data.groupBy("ontology_key").count().where("count > 1").limit(1).count()):
                raise RuntimeError(f"Physical ontology_key verification failed for {physical}.")
            weather_columns[physical]["ontology_key"] = "string"
    schema_map = fetch_sql_table_schema(sql_server, sql_database, list(OPERATIONAL_COLUMNS))
    sql_columns = {table: {name: primitive(kind) for name, kind in columns} for table, columns in schema_map.items()}
    after = project_ontology_extension(
        before, workspace_id, first_setting("lakehouse_id", required=True), sql_db_item_id,
        weather_columns, sql_columns, _verify_definition)
    def source_item(path):
        response = api_request("GET", f"{FABRIC_API_BASE}/v1/workspaces/{workspace_id}" + path)
        response.raise_for_status()
        return response.json()
    lakehouse_id = first_setting("lakehouse_id", required=True)
    workspace_metadata = source_item("")
    lakehouse_metadata = source_item(f"/lakehouses/{lakehouse_id}")
    sql_metadata = source_item(f"/sqlDatabases/{sql_db_item_id}")
    if (workspace_metadata.get("id") != workspace_id or lakehouse_metadata.get("id") != lakehouse_id
            or sql_metadata.get("id") != sql_db_item_id):
        raise RuntimeError("Ontology source metadata readback returned a different item identity.")
    after = project_ontology_source_metadata(after, workspace_metadata, lakehouse_metadata, sql_metadata)
    after = _enrich_project_semantics(after)
    if after != before:
        definition_request("updateDefinition", after)
    readback = definition_request("getDefinition")["definition"]["parts"]
    _verify_definition(after, readback)
    print("Verified additive project ontology readback; existing native graph is preserved. "
          "Materializing additional eligible entities/relationships remains a portal prerequisite.")
    return {"status": "verified", "ontology_id": project_ontology_item_id,
            "added_entity_count": len(WEATHER_PROJECT_ENTITIES) + len(OPERATIONAL_COLUMNS)}


step_results = {}
step_errors = []
sql_db_item_id = None
sql_server = sql_database = None
print("\n=== STEP A — Seed the operational SQL Database ===")
try:
    if provision_shared_store:
        if sql_db_item_name not in (operational_store_name(env_suffix), OPERATIONAL_STORE_NAME):
            raise RuntimeError("Shared-store provisioning requires its explicit canonical SQL item name.")
        if legacy_sql_db_item_id and (
            not isinstance(legacy_sql_db_item_id, str) or not re.fullmatch(
                r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", legacy_sql_db_item_id)
        ):
            raise RuntimeError("Legacy migration requires a valid explicit SQL item ID.")
        def shared_sql_request(method, path, payload=None):
            return api_request(
                method, path if path.startswith("https://") else f"{FABRIC_API_BASE}/v1/{path}", data=payload)
        shared_store = ensure_operational_item(
            shared_sql_request, workspace_id, env_suffix=env_suffix,
            folder_id=first_setting("target_folder_id", required=True))
        if shared_store["displayName"] != sql_db_item_name:
            raise RuntimeError(
                "The requested SQL name differs from the existing immutable item. "
                "Use its verified setup name or explicitly plan a replacement migration.")
        migration_receipt = {"target_item_id": shared_store["id"], "migration": "not_requested"}
        target_connection = operational_connection(shared_store, get_sql_token)
        try:
            initialize_operational_schema(target_connection)
            sql_db_item_id, sql_server, sql_database = resolve_sql_database()
            if not project_ontology_item_id:
                ontology_item = find_item_by_name(
                    first_setting("ontology_name", "fabric_ontology_name", required=True),
                    item_type="Ontology")
                if not ontology_item:
                    raise RuntimeError("The setup-selected Ontology was not found; refusing another target.")
                project_ontology_item_id = ontology_item["id"]
            step_results["project_ontology"] = extend_selected_project_ontology()
            if legacy_sql_db_item_id:
                legacy_store = operational_item(shared_sql_request, workspace_id, legacy_sql_db_item_id)
                source_connection = operational_connection(legacy_store, get_sql_token)
                try:
                    migration_receipt = copy_operational_records(
                        source_connection, target_connection, legacy_sql_db_item_id, shared_store["id"])
                finally:
                    source_connection.close()
        finally:
            target_connection.close()
        step_results["operational_store"] = {"status": "verified", **migration_receipt}
        print("OPERATIONAL_STORE_RECEIPT=" + json.dumps(migration_receipt, sort_keys=True))
    sql_db_item_id, sql_server, sql_database = resolve_sql_database()
    print(f"✅ Resolved SQL Database: {sql_db_item_name} (id={sql_db_item_id})")
    print(f"   Server: {sql_server}  DB: {sql_database}")
    if seed_demo_data:
        seed_receipt = seed_sql_database(sql_server, sql_database)
        step_results["sql_seed"] = {"status": "completed", "reason": "", "receipt": seed_receipt}
        print("✅ Missing operational demo records inserted; existing records preserved.")
    else:
        step_results["sql_seed"] = {
            "status": "not_requested", "reason": "Deployment bootstrap preserves operational records.",
        }
        print("Operational demo DML was not requested; existing records are unchanged.")
except Exception as exc:
    if provision_shared_store:
        spark._jvm.org.apache.log4j.LogManager.getLogger("HydroOperationalStore").error(
            f"Shared-store preparation failed: {type(exc).__name__}: {exc}")
    step_results["sql_seed"] = {"status": "failed", "reason": str(exc)}
    step_errors.append(exc)
    print("⚠️ SQL seeding did not complete:")
    print("   ", exc)
    print("   Manual fallback: run HydroOperationsApp/sql/seed-operational-data.sql against")
    print(f"   the '{sql_db_item_name}' SQL Database (Fabric → SQL query editor).")

# ---------------------------------------------------------------------------
# STEP B — Add the SQL Database as a Data Agent source and republish
# ---------------------------------------------------------------------------
def resolve_data_agent_id() -> Optional[str]:
    # Never trust a persisted data_agent_id blindly — the agent may have been deleted/rebuilt
    # (a stale id makes getDefinition 404 EntityNotFound). Verify it against the live workspace.
    agents = list_items_of_type(DATA_AGENT_ITEM_TYPE)
    if data_agent_id and any(a.get("id") == data_agent_id for a in agents):
        return data_agent_id
    by_name = next((a for a in agents if a.get("displayName") == data_agent_name), None)
    if by_name:
        return by_name["id"]
    return None


def verify_agent_source_readback(
    agent_id: str, submitted_sql_source: dict, published: bool = False,
    retained_sources: dict | None = None,
) -> list:
    parts = definition_parts(get_item_definition(agent_id))
    path = SQL_DATASOURCE_PATH.replace("/draft/", "/published/", 1) if published else SQL_DATASOURCE_PATH
    sql_part = next((part for part in parts if part["path"] == path), None)
    if sql_part is None or decode_payload(sql_part["payload"]) != submitted_sql_source:
        raise RuntimeError(
            f"SQL source readback at {path!r} does not match the submitted SQL source; "
            "source retention and publish success are unverified."
        )
    for draft_path, expected in (retained_sources or {}).items():
        retained_path = draft_path.replace("/draft/", "/published/", 1) if published else draft_path
        retained = next((part for part in parts if part["path"] == retained_path), None)
        if retained is None or decode_payload(retained["payload"]) != expected:
            raise RuntimeError(f"Legacy SQL selection/custom content readback failed at {retained_path!r}.")
    return parts


def resolve_agent_extension_target() -> tuple:
    if not data_agent_id and not data_agent_name:
        raise RuntimeError("No Data Agent configured; configure a live Data Agent before SQL extension.")
    agent_id = resolve_data_agent_id()
    if not agent_id:
        raise RuntimeError("No live Data Agent found; create or configure one before SQL extension.")
    return agent_id, {"status": "allowed", "reason": ""}


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


def build_sql_datasource_obj(existing: dict, artifact_id: str, schema_map: dict) -> dict:
    # Mirror the portal's published shape: a nested schema→table→column tree. The grouping and
    # schema nodes are is_selected=False; the actual tables and their columns are is_selected=True.
    ds = dict(existing)
    ds["$schema"] = DATASOURCE_SCHEMA_URL
    ds["artifactId"] = artifact_id
    ds["workspaceId"] = workspace_id
    ds.setdefault("dataSourceInstructions", SQL_DS_INSTRUCTIONS)
    ds["displayName"] = sql_db_item_name
    ds["type"] = SQL_DATASOURCE_TYPE
    ds.setdefault("userDescription", None)
    ds.setdefault("metadata", None)
    table_nodes = []
    for table, _desc in SQL_TABLES:
        column_nodes = [
            _ds_element("sql_database.column", col, True, [], data_type=dtype)
            for col, dtype in (schema_map.get(table) or [])
        ]
        table_nodes.append(_ds_element("sql_database.table", table, True, column_nodes))
    elements = [
        _ds_element("schema_grouping", "Schemas", False, [
            _ds_element(f"{SQL_DATASOURCE_TYPE}.schema", SQL_SCHEMA_NAME, False, [
                _ds_element("table_grouping", "Tables", False, table_nodes),
            ]),
        ]),
    ]
    ds["elements"] = merge_sql_elements(existing.get("elements", []), elements)
    return ds


def merge_sql_elements(existing: list, desired: list) -> list:
    """Add discovered schema nodes without resetting saved IDs, selections, or custom fields."""
    merged = list(existing)
    for node in desired:
        index = next((i for i, old in enumerate(merged)
                      if (old.get("type"), old.get("display_name")) ==
                      (node["type"], node["display_name"])), None)
        if index is None:
            merged.append(node)
        else:
            old = merged[index]
            merged[index] = {**node, **old, "children": merge_sql_elements(
                old.get("children", []), node["children"],
            )}
    return merged


def build_stage_obj(existing: dict) -> dict:
    """Preserve existing aiInstructions and append required guidance once."""
    stage = dict(existing)
    stage["$schema"] = STAGE_CONFIG_SCHEMA_URL
    instructions = stage.get("aiInstructions") or ""
    instructions = instructions.replace(
        OPERATIONAL_INSTRUCTIONS.replace(sql_db_item_name, OPERATIONAL_STORE_NAME),
        OPERATIONAL_INSTRUCTIONS,
    )
    if OPERATIONAL_INSTRUCTIONS_MARKER not in instructions:
        instructions += OPERATIONAL_INSTRUCTIONS
    if ASSET_RESOLUTION_INSTRUCTIONS_MARKER not in instructions:
        instructions += ASSET_RESOLUTION_INSTRUCTIONS
    stage["aiInstructions"] = instructions[:15000]
    return stage


def extend_data_agent_sql_source(agent_id: str) -> dict:
    global sql_db_item_id, sql_server, sql_database, SQL_DATASOURCE_PATH
    definition = get_item_definition(agent_id)
    parts = definition_parts(definition, allow_empty=True)
    if not sql_db_item_id:
        # Resolve id even if seeding failed, so the source can still be wired.
        sql_db_item_id = (find_item_by_name(sql_db_item_name, item_type="SQLDatabase") or {}).get("id")
    if not sql_db_item_id:
        raise RuntimeError(f"SQL Database item '{sql_db_item_name}' not found; cannot wire source.")

    print(f"✅ Data Agent id: {agent_id}")

    # Reference the SQLDatabase item id directly — this is exactly what the portal's
    # "Add data → SQL database" flow stores. Do NOT swap in the SQL analytics/warehouse
    # endpoint id; that item isn't what the Data Agent binds to for a Fabric SQL Database.
    sql_source_artifact_id = sql_db_item_id
    print(f"✅ SQL Database source artifact id: {sql_source_artifact_id}")

    # Enumerate the operational tables' columns so the source carries the same nested
    # schema→table→column tree the portal writes (a flat table list 404s at load time).
    if not sql_server or not sql_database:
        _sid, sql_server, sql_database = resolve_sql_database()
    schema_map = fetch_sql_table_schema(sql_server, sql_database, [t for t, _ in SQL_TABLES])
    filled = sum(1 for t, _ in SQL_TABLES if schema_map.get(t))
    print(f"✅ Enumerated columns for {filled}/{len(SQL_TABLES)} operational table(s)")

    SQL_DATASOURCE_PATH = operational_source_path(
        parts, workspace_id, sql_db_item_id, SQL_DATASOURCE_PATH)
    sql_part_path = SQL_DATASOURCE_PATH

    existing_ds = next(
        (
            decode_payload(p.get("payload", ""))
            for p in parts
            if p.get("path") == sql_part_path
        ),
        {},
    )

    submitted_sql_source = build_sql_datasource_obj(
        existing_ds, sql_source_artifact_id, schema_map,
    )
    parts = upsert_part(parts, sql_part_path, submitted_sql_source)
    retained_sql_sources = {}
    if provision_shared_store and not step_errors:
        def deselect_legacy_tables(nodes):
            for node in nodes:
                if node.get("type") == "sql_database.table" and node.get("display_name") in OPERATIONAL_COLUMNS:
                    node["is_selected"] = False
                    for column in node.get("children", []):
                        column["is_selected"] = False
                else:
                    deselect_legacy_tables(node.get("children", []))
        for part in parts:
            if part["path"].startswith("Files/Config/draft/") and part["path"].endswith("/datasource.json"):
                source = decode_payload(part["payload"])
                if source.get("type") == "sql_database" and source.get("artifactId") == legacy_sql_db_item_id:
                    deselect_legacy_tables(source.get("elements", []))
                    part["payload"] = encode_payload(source)
                    retained_sql_sources[part["path"]] = source

    existing_stage = next(
        (decode_payload(p.get("payload", "")) for p in parts if p.get("path") == DRAFT_STAGE_CONFIG_PATH),
        {},
    )
    parts = upsert_part(parts, DRAFT_STAGE_CONFIG_PATH, build_stage_obj(existing_stage))

    print(f"Applying definition: {len(parts)} part(s)")
    print("   • SQL data source ->", sql_part_path, f"({len(SQL_TABLES)} table(s), nested column tree)")
    update_item_definition(agent_id, {"parts": parts})
    verify_agent_source_readback(agent_id, submitted_sql_source, retained_sources=retained_sql_sources)

    enable_preview_runtime(agent_id)
    publish_data_agent(agent_id, "Operational SQL source added.")
    verify_agent_source_readback(agent_id, submitted_sql_source, published=True, retained_sources=retained_sql_sources)
    print("🌐 Data Agent republished with operational SQL source.")
    return {
        "status": "published",
        "reason": "Publish completed with the exact submitted SQL source confirmed in draft and published "
                  "readback. Runtime answers are not verified.",
    }


def persist_sql_extension_status(result: dict) -> None:
    from delta.tables import DeltaTable

    values = {
        "data_agent_sql_source_status": result["status"],
        "data_agent_sql_source_reason": result["reason"],
    }
    source = spark.createDataFrame(
        [{"setting_name": k, "setting_value": v} for k, v in values.items()]
    ).withColumn("updated_utc", F.current_timestamp())
    (DeltaTable.forName(spark, settings_table_name).alias("target")
     .merge(source.alias("source"), "target.setting_name = source.setting_name")
     .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())


print("\n=== STEP B — Data Agent SQL source ===")
step_results["data_agent_sql_source"] = {
    "status": "failed", "reason": "SQL source step did not complete; inspect the raised exception.",
}
try:
    if provision_shared_store and step_errors:
        raise RuntimeError(
            f"Shared-store preparation failed: {step_errors[0]}; Data Agent sources were not changed."
        ) from step_errors[0]
    agent_id, agent_policy = resolve_agent_extension_target()
    step_results["data_agent_sql_source"] = (
        extend_data_agent_sql_source(agent_id) if agent_id else agent_policy
    )
except Exception as exc:
    step_results["data_agent_sql_source"] = {"status": "failed", "reason": str(exc)}
    step_errors.append(exc)
finally:
    # Persist every outcome; failures are raised after the independent step results are saved.
    persist_sql_extension_status(step_results["data_agent_sql_source"])
    print(json.dumps({"steps": step_results}))

if provision_shared_store and step_errors:
    raise RuntimeError(
        f"Shared operational store setup failed: {step_errors[0]}; inspect the persisted failure reason."
    ) from step_errors[0]

# ---------------------------------------------------------------------------
# Persist discovered ids back to rti_demo_settings
# ---------------------------------------------------------------------------
persist = {}

if provision_shared_store:
    persist["operational_sql_db_id"] = sql_db_item_id
    persist["operational_sql_db_name"] = sql_db_item_name
    if legacy_sql_db_item_id:
        persist["operational_sql_migration_receipt"] = json.dumps(migration_receipt, sort_keys=True)

if seed_demo_data:
    persist["operational_sql_seed_status"] = step_results["sql_seed"]["status"]
    seed_result = step_results["sql_seed"]
    persist["operational_sql_seed_receipt"] = json.dumps(
        seed_result["receipt"] if seed_result["status"] == "completed" else seed_result,
        sort_keys=True,
    )

if persist:
    from delta.tables import DeltaTable

    persist_df = (
        spark.createDataFrame([{"setting_name": k, "setting_value": str(v)} for k, v in persist.items()])
        .withColumn("updated_utc", F.current_timestamp())
    )
    settings_delta_table = DeltaTable.forName(spark, settings_table_name)
    (
        settings_delta_table.alias("target")
        .merge(persist_df.alias("source"), "target.setting_name = source.setting_name")
        .whenMatchedUpdate(set={"setting_value": "source.setting_value", "updated_utc": "source.updated_utc"})
        .whenNotMatchedInsert(
            values={
                "setting_name": "source.setting_name",
                "setting_value": "source.setting_value",
                "updated_utc": "source.updated_utc",
            }
        )
        .execute()
    )
    print("\n✅ Persisted settings:", persist)
    display(spark.read.table(settings_table_name).orderBy("setting_name"))

print(json.dumps({"status": "completed" if not step_errors else "failed", "steps": step_results}))
if step_errors:
    raise RuntimeError("Operational setup failed; inspect the per-step results above.") from step_errors[0]
notebookutils.notebook.exit(json.dumps({
    "status": "completed",
    "reason": "Operational steps completed; Data Agent SQL source published.",
    "capability": "operational_setup",
    "agent_ready": False,
    "steps": step_results,
}))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
