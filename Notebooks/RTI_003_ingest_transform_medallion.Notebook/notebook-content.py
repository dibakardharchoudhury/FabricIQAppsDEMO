# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "0b04eeff-ee6c-4830-a6f3-b5afd7a822c9",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI_V3",
# META       "default_lakehouse_workspace_id": "6f64157c-cd3d-4ce3-9cca-3e74fb2c367f",
# META       "known_lakehouses": [
# META         {
# META           "id": "0b04eeff-ee6c-4830-a6f3-b5afd7a822c9"
# META         }
# META       ]
# META     }
# META   }
# META }

# MARKDOWN ********************

# 
# # 03 – Ingest & Transform (PySpark)
# ## Bronze via Lakehouse Shortcut → Silver → Gold
# 
# This notebook assumes:
# 
# - Notebook **01_create_lakehouse_shortcut** has been run.
# - A shortcut exists at **Files/bronze** pointing to your ADLS dataset.
# 
# This notebook will:
# 
# 1. Load **Bronze** data via Lakehouse shortcut  
# 2. Build **Silver** conformed tables  
# 3. Build **Gold** KPIs (e.g., limit breaches, equipment health)  


# MARKDOWN ********************

# ## Notebook Setup – Attach the Lakehouse
# 
# Before running this:
# 
# 1. In the left pane → **Add item**
# 2. Choose **Lakehouse**
# 3. Select **OilGas_IQ_LakehouseRTI**
# 4. Click **Add**
# 
# This makes `Files/` and `Tables/` available in this notebook.


# CELL ********************

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

# --------------------------------------------
# APPLY SETTINGS USED BY THIS NOTEBOOK
# --------------------------------------------

workspace_id = settings["workspace_id"]
workspace_folder_path = settings["workspace_folder_path"]
target_folder_id = settings["target_folder_id"]

lakehouse_name = settings["lakehouse_name"]
lakehouse_id = settings["lakehouse_id"]

shortcut_name = settings["shortcut_name"]
shortcut_parent_path = settings["shortcut_parent_path"]
bronze_root = f"{shortcut_parent_path}/{shortcut_name}"

silver_facilities_table = settings["silver_facilities_table"]
silver_systems_table = settings["silver_systems_table"]
silver_equipment_table = settings["silver_equipment_table"]
silver_instruments_table = settings["silver_instruments_table"]
silver_signal_master_table = settings["silver_signal_master_table"]

print("✅ Workspace ID:", workspace_id)
print("✅ Workspace folder path:", workspace_folder_path)
print("✅ Target folder ID:", target_folder_id)
print("✅ Lakehouse:", lakehouse_name)
print("✅ Lakehouse ID:", lakehouse_id)
print("✅ Using bronze path:", bronze_root)
print("✅ Silver facilities table:", silver_facilities_table)
print("✅ Silver systems table:", silver_systems_table)
print("✅ Silver equipment table:", silver_equipment_table)
print("✅ Silver instruments table:", silver_instruments_table)
print("✅ Silver signal master table:", silver_signal_master_table)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# 
# ---------------------
# INGEST & TRANSFORM — SILVER LAYER
# 
# ---------------------------------------------


# CELL ********************

# =========================
# STID - Facilities, Systems, Equipment, Instruments, Signal Master
# Source-of-truth for structured RTI ontology IDs:
#   bronze/stid/facilities_stid.csv
#   bronze/stid/systems_stid.csv
#   bronze/stid/equipment_stid.csv
#   bronze/stid/instruments_stid.csv
#
# The instruments file carries the OPC UA node ids and instrument/equipment/system/facility keys.
# silver_signal_master is derived from silver_instruments so ontology relationships stay aligned.
# =========================

from pyspark.sql import functions as F

facilities_df  = spark.read.option("header", True).csv(f"{bronze_root}/stid/facilities_stid.csv")
systems_df     = spark.read.option("header", True).csv(f"{bronze_root}/stid/systems_stid.csv")
equipment_df   = spark.read.option("header", True).csv(f"{bronze_root}/stid/equipment_stid.csv")
instruments_df = spark.read.option("header", True).csv(f"{bronze_root}/stid/instruments_stid.csv")

silver_equipment_df = (
    equipment_df
    .withColumn("install_date", F.to_date("install_date", "yyyy-MM-dd"))
    .withColumn("criticality", F.col("criticality").cast("int"))
    .withColumn("is_active", F.col("status") == "ACTIVE")
)

# Keep silver_instruments in the tall/slim RTI signal metadata format.
# The IDs come directly from instruments_stid.csv and must match silver_signal_master.
silver_instruments_df = (
    instruments_df
    .select(
        F.col("opcua_node_id"),
        F.col("tag"),
        F.col("instrument_id"),
        F.col("equipment_id"),
        F.col("system_id"),
        F.col("facility_id"),
        F.col("unit"),
        F.col("instrument_type"),
    )
    .withColumn("is_active", F.lit(True))
    .dropDuplicates(["opcua_node_id"])
)

# Build signal_master from the same STID instrument source.
# This prevents signal_master/instruments/equipment from drifting apart.
silver_signal_master_df = (
    silver_instruments_df
    .select(
        F.col("opcua_node_id"),
        F.col("tag"),
        F.col("instrument_id"),
        F.col("equipment_id"),
        F.col("system_id"),
        F.col("facility_id"),
        F.col("unit"),
        F.col("is_active"),
        F.col("instrument_type"),
    )
    .withColumn(
        "signal_type",
        F.when(F.col("instrument_type").isNotNull(), F.lower(F.col("instrument_type")))
         .when(F.lower(F.col("tag")).contains("temp"), F.lit("temperature"))
         .when(F.lower(F.col("tag")).contains("pressure"), F.lit("pressure"))
         .when(F.lower(F.col("tag")).contains("speed"), F.lit("speed"))
         .when(F.lower(F.col("tag")).contains("power"), F.lit("power"))
         .when(F.lower(F.col("tag")).contains("vibration"), F.lit("vibration"))
         .otherwise(F.lit("generic"))
    )
    .drop("instrument_type")
    .dropDuplicates(["opcua_node_id"])
)

(
    facilities_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(silver_facilities_table)
)

(
    systems_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(silver_systems_table)
)

(
    silver_equipment_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(silver_equipment_table)
)

(
    silver_instruments_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(silver_instruments_table)
)

(
    silver_signal_master_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(silver_signal_master_table)
)

print("✅ STID Silver Tables created/refreshed from bronze/stid source files.")
print(f"✅ Facilities table: {silver_facilities_table} rows={facilities_df.count()}")
print(f"✅ Systems table: {silver_systems_table} rows={systems_df.count()}")
print(f"✅ Equipment table: {silver_equipment_table} rows={silver_equipment_df.count()}")
print(f"✅ Instruments table: {silver_instruments_table} rows={silver_instruments_df.count()}")
print(f"✅ Signal master table: {silver_signal_master_table} rows={silver_signal_master_df.count()}")

display(silver_equipment_df.orderBy("equipment_id"))
display(silver_instruments_df.orderBy("equipment_id", "instrument_id"))
display(silver_signal_master_df.orderBy("equipment_id", "tag"))


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from pyspark.sql import functions as F

# --- Bronze → DataFrames ---
sap_wo_df = spark.read.option("header", True).csv(f"{bronze_root}/sap/sap_pm_workorders.csv")
sap_notif_df = spark.read.option("header", True).csv(f"{bronze_root}/sap/sap_pm_notifications.csv")

# --- Silver: Work Orders ---
silver_wo_df = (
    sap_wo_df
        # type dates
        .withColumn("created_date",   F.to_timestamp("created_date"))
        .withColumn("planned_start",  F.to_timestamp("planned_start"))
        .withColumn("planned_finish", F.to_timestamp("planned_finish"))
        # Optional: standardize naming for ontology
        .withColumnRenamed("planned_start",  "required_start_date")
        .withColumnRenamed("planned_finish", "required_end_date")
        # optional placeholders if you want actual_end_date now
        # .withColumn("actual_end_date", F.lit(None).cast("timestamp"))
)

# --- Silver: Notifications ---
silver_notif_df = (
    sap_notif_df
        # Use created_date as notification_date in the model
        .withColumn("notification_date", F.to_timestamp("created_date"))
        .withColumn("malfunction_start", F.to_timestamp("malfunction_start"))
)

# --- Persist ---
silver_wo_df.write.mode("overwrite").saveAsTable("silver_workorders")
silver_notif_df.write.mode("overwrite").saveAsTable("silver_notifications")

print("✅ SAP Silver Tables created.")



# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

#OPC UA Telemetry

opcua_df = spark.read.json(f"{bronze_root}/opcua/opcua_telemetry_2h.jsonl")

silver_opcua_df = (
    opcua_df
        .withColumn("event_time", F.to_timestamp("event_time"))
        .withColumn("value", F.col("value").cast("double"))
)

silver_opcua_df.write.mode("overwrite").saveAsTable("silver_opcua_measurements")

print("✅ OPC UA Silver Tables created.")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark",
# META   "frozen": true,
# META   "editable": false
# META }

# CELL ********************

#Common Library

cl_classes_df = spark.read.option("header", True).csv(f"{bronze_root}/common_library/common_library_classes.csv")
cl_rules_df   = spark.read.option("header", True).csv(f"{bronze_root}/common_library/common_library_tag_rules.csv")

cl_classes_df.write.mode("overwrite").saveAsTable("silver_common_library_classes")
cl_rules_df.write.mode("overwrite").saveAsTable("silver_common_library_tag_rules")

print("✅ Common Library Silver Tables created.")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# =========================
# SOLV - Synthetic Operational Limit Values
# Synthetic engineering operating envelopes and design limits
# =========================

import pandas as pd
from pyspark.sql import functions as F

# ==========================================================
# 1. Use shared settings loaded in the 003 config cell
# ==========================================================

relative_path = f"{bronze_root}/solv/solv_sheet_equipment_limits.xlsx"

excel_path = (
    f"abfss://{workspace_id}"
    f"@onelake.dfs.fabric.microsoft.com/"
    f"{lakehouse_id}/{relative_path}"
)

silver_equipment_limits_table = settings.get(
    "silver_equipment_limits_table",
    "silver_equipment_limits"
)

base_df = pd.read_excel(excel_path)

# Clean column names
base_df.columns = [c.strip().lower().replace(" ", "_") for c in base_df.columns]

# ==========================================================
# ENGINEERING RULES USED TO GENERATE SOLV ENVELOPES
# ==========================================================

solv_rows = []

for _, row in base_df.iterrows():

    eq = row["equipment_id"]
    tag = row["tag"]
    vendor = row["vendor"]
    doc_id = row["datasheet_doc_id"]

    # PRESSURE LIMITS
    p_design = row["design_pressure_bar"]
    solv_rows.append({
        "equipment_id": eq,
        "tag": tag,
        "parameter": "pressure",
        "unit": "bar",
        "design_min": 0,
        "design_max": p_design,
        "limit_min": 0,
        "limit_max": round(0.9 * p_design, 2),
        "datasheet_doc_id": doc_id,
        "source": vendor
    })

    # TEMPERATURE LIMITS
    t_design = row["design_temp_c"]
    solv_rows.append({
        "equipment_id": eq,
        "tag": tag,
        "parameter": "temperature",
        "unit": "C",
        "design_min": -20,
        "design_max": t_design,
        "limit_min": 0,
        "limit_max": round(0.9 * t_design, 2),
        "datasheet_doc_id": doc_id,
        "source": vendor
    })

    # FLOW LIMITS
    f_design = row["design_flow_m3_h"]
    solv_rows.append({
        "equipment_id": eq,
        "tag": tag,
        "parameter": "flow",
        "unit": "m3/h",
        "design_min": 0,
        "design_max": f_design,
        "limit_min": round(0.1 * f_design, 2),
        "limit_max": round(0.9 * f_design, 2),
        "datasheet_doc_id": doc_id,
        "source": vendor
    })

# ==========================================================
# 2. Convert synthetic SOLV rows to Spark
# ==========================================================

pdf_solv = pd.DataFrame(solv_rows)
solv_df = spark.createDataFrame(pdf_solv)

# ==========================================================
# 3. Cast numeric columns and save as Silver table
# ==========================================================

silver_solv_df = (
    solv_df
    .withColumn("design_min", F.col("design_min").cast("double"))
    .withColumn("design_max", F.col("design_max").cast("double"))
    .withColumn("limit_min",  F.col("limit_min").cast("double"))
    .withColumn("limit_max",  F.col("limit_max").cast("double"))
)

(
    silver_solv_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(silver_equipment_limits_table)
)

print("✅ SOLV Silver table created successfully with engineering rules")
print(f"✅ Source file: {relative_path}")
print(f"✅ Silver table: {silver_equipment_limits_table}")
print(f"✅ Rows: {silver_solv_df.count()}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

#P&ID Elements & Connections refers to the extracted topology from the Piping & Instrumentation Diagram (P&ID).

pid_elements_df = spark.read.option("header", True).csv(f"{bronze_root}/pid/pid_parsed_elements.csv")
pid_conn_df     = spark.read.option("header", True).csv(f"{bronze_root}/pid/pid_parsed_connections.csv")

pid_elements_df.write.mode("overwrite").saveAsTable("silver_pid_elements")
pid_conn_df.write.mode("overwrite").saveAsTable("silver_pid_connections")

print("✅ P&ID Silver Tables created.")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from pyspark.sql.types import StructType, StructField, StringType
from pyspark.sql import functions as F

# ==========================================================
# DOCUMENTS & ANNOTATIONS INGESTION
# ==========================================================

# -------------------------------
# Bronze → Silver: DOCUMENT INDEX
# -------------------------------
# Drop Silver table to avoid schema merge issues from previous runs
spark.sql("DROP TABLE IF EXISTS silver_documents")

doc_index_df = (
    spark.read
         .option("header", True)
         .option("inferSchema", True)
         .csv(f"{bronze_root}/documents/document_index.csv")
)

(
    doc_index_df.write
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable("silver_documents")
)


# -------------------------------
# Bronze → Silver: ANNOTATIONS
# -------------------------------
spark.sql("DROP TABLE IF EXISTS silver_annotations")

annotations_df = (
    spark.read
         .option("header", True)
         .option("inferSchema", True)
         .csv(f"{bronze_root}/documents/annotations.csv")
)

(
    annotations_df.write
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable("silver_annotations")
)


# -------------------------------
# Bronze → Silver: 3D MODEL METADATA (Robust JSON ingest)
# -------------------------------
spark.sql("DROP TABLE IF EXISTS silver_3d_model_metadata")

safe_schema = StructType([
    StructField("id",            StringType(), True),
    StructField("tag",           StringType(), True),
    StructField("type",          StringType(), True),
    StructField("x",             StringType(), True),
    StructField("y",             StringType(), True),
    StructField("z",             StringType(), True),

    # Always a string to avoid Delta merge conflicts
    StructField("created_time",  StringType(), True),

    # Catch-all (remove later)
    StructField("_raw",          StringType(), True)
])

model3d_path = f"{bronze_root}/documents/3d_model_metadata.json"

model3d_df = (
    spark.read
         .schema(safe_schema)
         .option("mode", "PERMISSIVE")
         .json(model3d_path)
)

# Remove optional fields
cols = model3d_df.columns
if "_raw" in cols:
    model3d_df = model3d_df.drop("_raw")
if "_corrupt_record" in cols:
    model3d_df = model3d_df.filter(F.col("_corrupt_record").isNull()) \
                           .drop("_corrupt_record")

# Write clean table
(
    model3d_df.write
        .format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable("silver_3d_model_metadata")
)

print("✅ Documents Silver created (documents, annotations, 3D metadata).")



# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ---------------------------------------------
# GOLD LAYER
# 
# ---------------------------------------------


# CELL ********************

#Gold: Limit Breaches

gold_limit_breaches_df = spark.sql("""
    SELECT
        m.equipment_id,
        m.instrument_id,
        m.tag,
        m.event_time,
        m.value,
        l.limit_min,
        l.limit_max,
        CASE
            WHEN m.value < l.limit_min THEN 'LOW'
            WHEN m.value > l.limit_max THEN 'HIGH'
            ELSE 'OK'
        END AS breach_direction
    FROM silver_opcua_measurements m
    JOIN silver_equipment_limits l
        ON m.equipment_id = l.equipment_id
""")

gold_limit_breaches_df.write.mode("overwrite").saveAsTable("gold_limit_breaches")

print("✅Gold table: limit breaches created.")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark",
# META   "frozen": true,
# META   "editable": false
# META }

# CELL ********************

# Gold: Equipment Health - Fixed to match actual schema

gold_equipment_health_df = spark.sql("""
    WITH latest_meas AS (
        SELECT equipment_id, tag, MAX(event_time) AS last_event_time
        FROM silver_opcua_measurements
        GROUP BY equipment_id, tag
    ),
    joined AS (
        SELECT
            e.equipment_id,
            e.model as equipment_model,
            e.equipment_type_name as equipment_type,
            e.criticality,
            lm.tag,
            m.value AS latest_value,
            lm.last_event_time,
            w.workorder_id,
            w.status AS wo_status,
            w.priority AS wo_priority
        FROM silver_equipment e
        LEFT JOIN latest_meas lm
            ON e.equipment_id = lm.equipment_id
        LEFT JOIN silver_opcua_measurements m
            ON m.equipment_id = lm.equipment_id
           AND m.tag = lm.tag
           AND m.event_time = lm.last_event_time
        LEFT JOIN silver_workorders w
            ON w.equipment_id = e.equipment_id
           AND w.status NOT IN ('CLOSED', 'COMPLETED')
    )
    SELECT * FROM joined
""")

gold_equipment_health_df.write.mode("overwrite").saveAsTable("gold_equipment_health")

print("✅Gold table: equipment health created (schema fixed).")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark",
# META   "frozen": true,
# META   "editable": false
# META }

# CELL ********************

# Gold: Equipment Work Order Summary
# Aggregates work order counts and status per equipment
from pyspark.sql import functions as F

workorder_summary_df = (
    spark.table("silver_workorders")
    .groupBy("equipment_id")
    .agg(
        F.count("workorder_id").alias("total_workorders"),
        F.sum(F.when(F.col("status").isin("CLOSED", "COMPLETED"), 1).otherwise(0)).alias("closed_workorders"),
        F.sum(F.when(~F.col("status").isin("CLOSED", "COMPLETED"), 1).otherwise(0)).alias("open_workorders"),
        F.avg("priority").alias("avg_priority")
    )
)
workorder_summary_df.write.mode("overwrite").saveAsTable("gold_equipment_workorders_summary")
print("✅Gold table: equipment work order summary created.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# Gold: Equipment Notification Events
from pyspark.sql.functions import col, max as F_max, avg as F_avg, count as F_count

notif_df = spark.table("silver_notifications")

equip_notif_summary_df = (
    notif_df.groupBy("equipment_id")
    .agg(
        F_count("notification_id").alias("total_notifications"),
        F_max("notification_date").alias("last_notification_date")
    )
)
equip_notif_summary_df.write.mode("overwrite").saveAsTable("gold_equipment_notification_events")
print("✅Gold table: equipment notification events created.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# Gold: OPCUA Measurement Quality Stats
opcua_df = spark.table("silver_opcua_measurements")
gold_quality_stats = (
    opcua_df.groupBy("tag", "unit", "quality")
    .count()
)
gold_quality_stats.write.mode("overwrite").saveAsTable("gold_opcua_quality_stats")
print("✅Gold table: opcua measurement quality stats created.")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark",
# META   "frozen": true,
# META   "editable": false
# META }

# CELL ********************

# =========================
# Gold: Signal Classification per System
# Uses tall/slim RTI signal metadata
# =========================

from pyspark.sql import functions as F

signal_df = spark.table(silver_signal_master_table)

required_cols = {
    "system_id",
    "opcua_node_id",
    "signal_type",
}

missing_cols = required_cols - set(signal_df.columns)
if missing_cols:
    raise RuntimeError(
        f"'{silver_signal_master_table}' is missing required columns: {sorted(missing_cols)}"
    )

gold_inst_class_df = (
    signal_df
    .groupBy("system_id", "signal_type")
    .agg(
        F.countDistinct("opcua_node_id").alias("signal_count")
    )
)

(
    gold_inst_class_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("gold_instrument_classification")
)

print("✅ Gold table created: gold_instrument_classification")
display(gold_inst_class_df.orderBy("system_id", "signal_type"))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# Gold: PID Topology Stats - Upstream/Downstream counts per equipment (fix join using tag <-> equipment_id)
from pyspark.sql import functions as F

pid_conn_df = spark.table("silver_pid_connections")
equip_df = spark.table("silver_equipment")

# Join for from_equipment_id
from_join = pid_conn_df.join(equip_df.select(F.col("tag").alias("from_tag_equip"), F.col("equipment_id")), pid_conn_df.from_tag == F.col("from_tag_equip"), "left")
# Join for to_equipment_id
to_join = pid_conn_df.join(equip_df.select(F.col("tag").alias("to_tag_equip"), F.col("equipment_id")), pid_conn_df.to_tag == F.col("to_tag_equip"), "left")

downstream = from_join.groupBy("equipment_id").count().withColumnRenamed("count", "downstream_connections")
upstream = to_join.groupBy("equipment_id").count().withColumnRenamed("count", "upstream_connections")

pid_stats = downstream.join(upstream, on="equipment_id", how="outer").fillna(0)

pid_stats.write.mode("overwrite").saveAsTable("gold_pid_topology_stats")
print("✅Gold table: pid topology stats created (equipment_id join fix).")


# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# Enhanced Validation for Silver and Gold Tables - Spark Catalog Version
import pandas as pd
from pyspark.sql.utils import AnalysisException

# --- Static list of tables and their descriptions ---
table_infos = [
    # Silver
    ("silver_facilities", "Facility master data (physical locations/campuses)", "silver"),
    ("silver_systems", "System definitions (collections/groups of equipment, e.g., heating system)", "silver"),
    ("silver_equipment", "Master list of all equipment (pumps, valves, motors, etc.)", "silver"),
    ("silver_instruments", "All instrument tags and analog/digital status", "silver"),
    ("silver_workorders", "SAP maintenance work order records", "silver"),
    ("silver_notifications", "SAP event/notification records (alerts, failures, issues)", "silver"),
    #("silver_opcua_measurements", "Raw time-series telemetry/measurements (OPC UA)", "silver"),
    ("silver_equipment_limits", "Operational and design limits for equipment (SOLV)", "silver"),
    ("silver_pid_elements", "Element-level structure extracted from P&ID diagrams", "silver"),
    ("silver_pid_connections", "Connectivity extracted from P&ID diagrams", "silver"),
    ("silver_documents", "Loaded document index (engineering/maintenance/docs)", "silver"),
    ("silver_annotations", "Annotation records for those documents", "silver"),
    ("silver_3d_model_metadata", "3D model positional/metadata integration", "silver"),
    ("silver_common_library_classes", "Common library - tag class definitions", "silver"),
    ("silver_common_library_tag_rules", "Common library - tag rule definitions", "silver"),
    # Gold
    #("gold_limit_breaches", "Sensor readings exceeding safe/engineered limits", "gold"),
   # ("gold_equipment_health", "Latest sensor/workorder summary by equipment", "gold"),
    ("gold_equipment_workorders_summary", "Aggregated work order status counts for each equipment", "gold"),
    ("gold_equipment_notification_events", "Total/last notification event per equipment", "gold"),
    #("gold_opcua_quality_stats", "Counts of OPC UA measurement quality per tag/unit", "gold"),
    ("gold_instrument_classification", "Analog vs Digital instrument counts per system", "gold"),
    ("gold_pid_topology_stats", "Upstream/downstream equipment connectivity stats (from P&ID)", "gold")
]
expected_tables = [t[0] for t in table_infos]

# --- Discover all actual tables (from Spark catalog) ---
catalog_tables = [t.name for t in spark.catalog.listTables() if t.name.startswith("silver_") or t.name.startswith("gold_")]
actual_tables = set(catalog_tables)

# --- Build validation structure ---
validation_results = []
for tbl, desc, layer in table_infos:
    exists = tbl in actual_tables
    row_count = None
    if exists:
        try:
            row_count = spark.table(tbl).count()
        except Exception as ex:
            row_count = 'ERR'
    else:
        row_count = 'MISSING'
    validation_results.append({
        'Layer': layer.title(),
        'Table': tbl,
        'Exists': '✅' if exists else '❌',
        'Row Count': row_count,
        'Purpose': desc
    })
# --- Also find extra tables (created but not expected) ---
for tbl in actual_tables:
    if tbl not in expected_tables:
        try:
            row_count = spark.table(tbl).count()
        except Exception as ex:
            row_count = 'ERR'
        validation_results.append({
            'Layer': 'Unknown',
            'Table': tbl,
            'Exists': 'Extra',
            'Row Count': row_count,
            'Purpose': '(Table not in static expected list)'
        })

# --- Display as a visually appealing dataframe ---
df = pd.DataFrame(validation_results)
df = df[['Layer', 'Table', 'Exists', 'Row Count', 'Purpose']]
df.sort_values(by=['Layer', 'Table'], inplace=True)
from IPython.display import display
print("\nValidation Summary (Silver & Gold Tables):")
display(df.style.set_properties(**{'text-align': 'left'}).set_table_styles(
    [{'selector': 'th', 'props': [('font-size', '110%'), ('text-align', 'left'),('background-color', '#f2f2f2')]}]
))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
