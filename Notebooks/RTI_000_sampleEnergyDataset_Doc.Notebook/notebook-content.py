# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "983f16bc-f041-4dd7-b56d-0f078359e3a6",
# META       "default_lakehouse_name": "Energy_IQ_LakehouseRTI",
# META       "default_lakehouse_workspace_id": "6f64157c-cd3d-4ce3-9cca-3e74fb2c367f",
# META       "known_lakehouses": [
# META         {
# META           "id": "983f16bc-f041-4dd7-b56d-0f078359e3a6"
# META         }
# META       ]
# META     }
# META   }
# META }

# MARKDOWN ********************

# 
# 
# # Fabric IQ –  Mock Dataset
# 
# ## Purpose
# This documentation describes a **fully synthetic energy dataset** and the **end‑to‑end Fabric pipeline** that takes it from raw files and seeded telemetry all the way to a **live ontology bound to Eventhouse RTI streams**.
# 
# The scenario is implemented across these notebooks (RTI_000–RTI_007):
# - `RTI_000_sampleEnergyDataset_Doc` (this document)
# - `RTI_001_create_lakehouse_shortcut`
# - `RTI_002_Setup_And_Structured_Data`
# - `RTI_003_ingest_transform_medallion`
# - `RTI_004_build_ontology_mapping_rti_structured`
# - `RTI_005_entity_DataBinding_rti_structured`
# - `RTI_006_generate_and_ingest_OPCUA_Stream`
# - `RTI_007_TimeSeriesBinding_RTI_signal`
# 
# The dataset and notebooks together are used to test **Microsoft Fabric** and **Fabric IQ** end‑to‑end capabilities:
# - Lakehouse (Bronze/Silver/Gold)
# - Streaming (Eventstream with OPC UA–like telemetry)
# - Eventhouse & KQL DB
# - Ontology (entity types, relationships, data bindings)
# - Graph (asset topology & relationships)
# - Data Agent & Operations Agent
# 
# All data is **fully synthetic** and mirrors common **industrial data landscapes** (engineering, operations, maintenance, documents) while containing **no real plant or customer data**.
# 
# ---
# 
# ## End‑to‑End Process: From Seeded Data to Finished Ontology
# 
# This section summarizes what each RTI notebook does and how the data flows from raw files to a fully bound ontology.
# 
# ### 0. Dataset Layout in ADLS / OneLake
# 
# The mock dataset lives in ADLS Gen2 in a `bronze` folder structure that is surfaced into Fabric via a Lakehouse shortcut:
# 
# ```
# fabric_iq_oilgas_mock/
# ├── bronze/
# │   ├── stid/
# │   ├── sap/
# │   ├── opcua/
# │   ├── common_library/
# │   ├── solv/
# │   ├── pid/
# │   └── documents/
# ├── silver/
# ├── gold/
# ├── scripts/
# └── docs/
# ```
# 
# The logical domains are:
# - Engineering master data (STID‑like)
# - ERP / Maintenance (SAP PM–like)
# - Real‑time telemetry (OPC UA–like)
# - Engineering standards (Common Library / CFIHOS‑style)
# - Design limits (SOLV sheets)
# - Engineering documents (PDFs, P&IDs)
# - Asset topology (P&ID parsed outputs)
# 
# All raw data lands under **`Files/bronze`** in the Lakehouse via a shortcut created in RTI_001.
# 
# ---
# 
# ### 1. RTI_001 – Create Lakehouse & ADLS Shortcut
# 
# **Notebook:** `RTI_001_create_lakehouse_shortcut`
# 
# **Goal:** Bootstrap the Fabric workspace so all later notebooks can share the same configuration and data source.
# 
# **Key steps**
# 1. **Configuration & settings table**
#    - Defines common names/IDs (Lakehouse, Eventhouse, Eventstream, ontology, table names, Key Vault secrets, etc.).
#    - Writes a shared table `rti_demo_settings` in the Lakehouse (Delta table) with rows like:
#      - `workspace_id`, `workspace_folder_path`
#      - `lakehouse_name`, `lakehouse_id`
#      - `eventhouse_name`, `kql_database_name`, `eventstream_name`, `eventhouse_table_name`
#      - `silver_facilities_table`, `silver_systems_table`, `silver_equipment_table`, `silver_instruments_table`, `silver_signal_master_table`
#      - Key Vault configuration (URI + secret names, **not** secret values).
# 
# 2. **Service Principal auth via Key Vault**
#    - Uses `notebookutils.credentials.getSecret` to read SPN credentials from Azure Key Vault.
#    - Obtains an Entra ID token for the **Fabric REST API** (`https://api.fabric.microsoft.com/.default`).
# 
# 3. **Workspace folder & Lakehouse creation (Fabric REST)**
#    - Ensures a workspace folder path `joa/RTI_Demo` exists (idempotent).
#    - Ensures a **Lakehouse** `Energy_IQ_LakehouseRTI` exists in that folder and records its ID.
# 
# 4. **ADLS Gen2 connection & shortcut**
#    - Creates or reuses a shareable cloud connection (Service Principal) to the ADLS account containing the mock dataset.
#    - Creates or reuses a **shortcut**:
#      - `Files/bronze` → ADLS path `https://ontologyjoa.dfs.core.windows.net/dataiq/bronze`
# 
# 5. **Persist shared settings**
#    - Writes `rti_demo_settings` as a Delta table with all of the above values.
#    - All later notebooks read from this table instead of redefining settings.
# 
# After RTI_001:
# - The Lakehouse `Energy_IQ_LakehouseRTI` exists.
# - `Files/bronze` points to the raw dataset in ADLS.
# - `rti_demo_settings` is the single source of truth for IDs and names.
# 
# ---
# 
# ### 2. RTI_002 – Eventhouse, KQL DB, Eventstream & Signal Seeding
# 
# **Notebook:** `RTI_002_Setup_And_Structured_Data`
# 
# **Goal:** Create / reuse the streaming backbone (Eventhouse + KQL DB + Eventstream) and seed a tall/slim signal metadata model that drives simulation and ontology.
# 
# **Key steps**
# 
# 1. **Load and extend shared settings**
#    - Reads `rti_demo_settings` and adds Key Vault secret names for SPN auth.
#    - Rewrites `rti_demo_settings` so all notebooks see a consistent configuration.
# 
# 2. **Eventhouse & KQL DB (Fabric REST)**
#    - Ensures an **Eventhouse** `RTI_Demo_Eventhouse` exists in the target folder.
#    - Ensures a **KQL database** `RTI_Demo_Eventhouse` is attached to that Eventhouse.
#    - Polls the Eventhouse until **Kusto query/ingest URIs** are available, storing them in settings.
#    - Creates or reuses a slim KQL table `OPCUAEvents` with schema:
#      - `event_time` (datetime)
#      - `opcua_node_id` (string)
#      - `value` (real)
#      - `quality` (string)
# 
# 3. **Eventstream & Custom Endpoint**
#    - Creates or reuses an **Eventstream** `RTI_Demo_Eventstream`.
#    - Uses Eventstream `getDefinition`/`updateDefinition` to:
#      - Add a `CustomEndpoint` source (`OPCUA_CustomEndpoint`).
#      - Add a `DefaultStream` wired to that source.
#      - Add an **Eventhouse destination** targeting the `OPCUAEvents` table.
#    - Retrieves the **Custom Endpoint connection** (Service Bus namespace, Event Hub name, connection string) used later for sending OPC UA–like telemetry.
# 
# 4. **Seed signal metadata into Silver**
#    - Uses a `SIM_CONFIG` definition to generate synthetic signal metadata:
#      - Devices `T001–T005`, sensors (temperature, power, speed, inlet pressure, vibration, etc.).
#      - Anomaly patterns (ramping vibration, spikes, etc.).
#    - Writes:
#      - `silver_instruments`: tall/slim instrument metadata (one row per OPC UA node).
#      - `silver_signal_master`: normalized signal master table derived from `silver_instruments` with:
#        - `opcua_node_id`, `tag`, `instrument_id`, `equipment_id`, `system_id`, `facility_id`, `unit`, `is_active`, `signal_type`.
# 
# After RTI_002:
# - Eventhouse + KQL DB + table `OPCUAEvents` exist and are ready for streaming.
# - Eventstream is wired from Custom Endpoint to Eventhouse.
# - A **signal master** model (`silver_signal_master`) is available for ontology and simulation.
# 
# ---
# 
# ### 3. RTI_003 – Ingest & Transform (Bronze → Silver → Gold)
# 
# **Notebook:** `RTI_003_ingest_transform_medallion`
# 
# **Goal:** Transform all **bronze** files into conformed **silver** tables and derived **gold** analytical tables in the Lakehouse.
# 
# **Key silver tables**
# 
# From `bronze/stid/` (engineering master data):
# - `silver_facilities` – facility master data (physical locations).
# - `silver_systems` – systems within facilities.
# - `silver_equipment` – equipment master (pumps, valves, compressors…).
# - `silver_instruments` – tall/slim instrument metadata (aligned with RTI signal model).
# 
# From `bronze/sap/` (maintenance):
# - `silver_workorders` – SAP PM‑like work orders with typed dates.
# - `silver_notifications` – SAP notification records.
# 
# From `bronze/opcua/` (historical telemetry sample):
# - `silver_opcua_measurements` – historical OPC UA time‑series (Delta table).
# 
# From `bronze/common_library/` (standards):
# - `silver_common_library_classes` – tag class definitions.
# - `silver_common_library_tag_rules` – tag naming rules.
# 
# From `bronze/solv/` (design limits):
# - Reads `solv_sheet_equipment_limits.xlsx` and generates `silver_equipment_limits` with engineering envelopes for pressure, temperature, flow.
# 
# From `bronze/pid/` (P&ID topology):
# - `silver_pid_elements` – parsed elements (equipment/instruments in P&ID).
# - `silver_pid_connections` – parsed process connections.
# 
# From `bronze/documents/` (unstructured docs):
# - `silver_documents` – document index.
# - `silver_annotations` – annotations.
# - `silver_3d_model_metadata` – 3D model metadata (robust JSON ingestion).
# 
# **Gold tables** (examples)
# 
# - `gold_limit_breaches` – joins `silver_opcua_measurements` to `silver_equipment_limits` to flag limit breaches.
# - `gold_equipment_health` – latest measurement + open work orders per equipment.
# - `gold_equipment_workorders_summary` – open/closed WO counts per equipment.
# - `gold_equipment_notification_events` – notification counts and latest dates.
# - `gold_opcua_quality_stats` – measurement quality distribution per tag.
# - `gold_instrument_classification` – signal‑type counts per system.
# - `gold_pid_topology_stats` – upstream/downstream connection counts per equipment.
# 
# RTI_003 also writes a **validation summary** of all silver/gold tables to help confirm the medallion layer is complete.
# 
# After RTI_003:
# - All structured domains (STID, SAP, OPC UA history, SOLV, P&ID, documents, common library) are available as silver tables.
# - Gold tables provide ready‑to‑consume operational KPIs.
# 
# ---
# 
# ### 4. RTI_004 – Build Ontology from Structured Data
# 
# **Notebook:** `RTI_004_build_ontology_mapping_rti_structured`
# 
# **Goal:** Automatically generate and deploy a **Fabric Ontology** (`RTI_Demo_Ontology`) that models structured entities and their relationships, and prepares the entity model for RTI binding.
# 
# **Key steps**
# 
# 1. **Load settings & choose source tables**
#    - Reads `rti_demo_settings` to get Lakehouse, ontology name, and silver table names.
#    - Uses a **manual table list** for ontology:
#      - `silver_facilities`
#      - `silver_systems`
#      - `silver_equipment`
#      - `silver_instruments`
#      - `silver_signal_master`
# 
# 2. **Infer entity types and primary keys**
#    - Derives **entity names** from table names (`silver_facilities` → `facilities`, etc.).
#    - Resolves an **own PK** for each entity (using naming heuristics + overrides):
#      - `facilities` → `facility_id`
#      - `systems` → `system_id`
#      - `equipment` → `equipment_id`
#      - `instruments` → `instrument_id`
#      - `signal_master` → `opcua_node_id` (override)
#    - Sets `entityIdParts = [own PK]` (no composite identity keys).
# 
# 3. **Entity type generation**
#    - For each table, creates an **EntityType** with:
#      - `properties` – one ontology property per Lakehouse column (typed via Spark schema).
#      - `entityIdParts` – property IDs corresponding to the PK.
#      - `displayNamePropertyId` – typically the PK.
#    - For `signal_master`, adds **Eventhouse RTI `timeseriesProperties`**:
#      - `event_time` (DateTime)
#      - `value` (Double)
#      - `quality` (String)
#    - RTI telemetry **remains in Eventhouse**; there is **no copied “measurement” entity** in Lakehouse.
# 
# 4. **Relationship type generation**
#    - Implements a clean hierarchy:
#      - `systems` → `facilities`
#      - `equipment` → `systems`
#      - `instruments` → `equipment`
#      - `signal_master` → `instruments`
#    - Uses join keys based on identity properties (e.g., `equipment_id`, `system_id`, `facility_id`).
#    - Generates relationship names like `systems_in_facilities`, `equipment_in_systems`, `instruments_on_equipment`, `signals_from_instruments`.
#    - Writes **`ontology_relationship_audit`** documenting effective join keys.
# 
# 5. **Persist ontology parts to Lakehouse**
#    - Writes `ontology_parts_latest` (one row per ontology part) with:
#      - `.platform`, `definition.json` root parts
#      - EntityType definitions
#      - RelationshipType definitions
#    - Writes `ontology_entity_audit` (entity → PK, ID parts, FK columns).
# 
# 6. **Deploy ontology via Fabric REST**
#    - Uses helper functions to:
#      - Ensure ontology item `RTI_Demo_Ontology` exists **in the target folder**.
#      - Push definition in **two stages**:
#        1. Root + EntityTypes
#        2. Root + EntityTypes + RelationshipTypes
#    - Verifies via `getDefinition` that the final ontology has:
#      - 5 entity types (facilities, systems, equipment, instruments, signal_master).
#      - 4 relationship types (forming the full chain signal → instrument → equipment → system → facility).
# 
# After RTI_004:
# - A clean **structured ontology** exists, with `signal_master` prepared to hold RTI `timeseriesProperties` and to connect to the rest of the asset hierarchy.
# 
# ---
# 
# ### 5. RTI_005 – Static Lakehouse DataBindings & Relationship Contextualizations
# 
# **Notebook:** `RTI_005_entity_DataBinding_rti_structured`
# 
# **Goal:** Bind structured Lakehouse tables to ontology entities and create relationship contextualizations based on existing identity keys.
# 
# **Key steps**
# 
# 1. **Load settings & ontology**
#    - Reads `rti_demo_settings` for Lakehouse & table names.
#    - Resolves ontology `RTI_Demo_Ontology` and its ID.
#    - Reads `ontology_entity_audit` and `ontology_relationship_audit`.
# 
# 2. **Static DataBindings (Lakehouse tables)**
#    - For each entity in `{facilities, systems, equipment, instruments, signal_master}`:
#      - Confirms the corresponding silver table exists and has the required columns.
#      - Creates a `NonTimeSeries` **DataBinding** from the Lakehouse table to entity properties, mapping:
#        - `sourceColumnName` → `targetPropertyId` (for all static columns).
#      - Writes one DataBinding per entity under:
#        - `EntityTypes/{entityId}/DataBindings/<guid>.json`
# 
# 3. **Relationship Contextualizations**
#    - For each relationship in the ontology (from `ontology_relationship_audit`):
#      - Calculates **join keys** between source and target tables (shared ID columns).
#      - Generates a **Contextualization** that describes how to join the source entity to the target entity using Lakehouse tables and key bindings.
#    - Writes one Contextualization per relationship under:
#      - `RelationshipTypes/{relationshipId}/Contextualizations/<guid>.json`
# 
# 4. **Push updated ontology definition**
#    - Merges new DataBinding and Contextualization parts with the existing ontology parts.
#    - Pushes updated definition via `updateDefinition` (with LRO support).
#    - Verifies that the ontology now contains:
#      - Entity definitions
#      - Relationship definitions
#      - 5 static DataBindings
#      - 4 relationship Contextualizations
# 
# After RTI_005:
# - All **structured tables** are bound to ontology entities.
# - Semantic relationships (facility/system/equipment/instrument/signal) are **contextualized** back to actual Lakehouse tables and columns.
# 
# ---
# 
# ### 6. RTI_006 – Generate & Ingest Live OPC UA–Like Stream
# 
# **Notebook:** `RTI_006_generate_and_ingest_OPCUA_Stream`
# 
# **Goal:** Generate live OPC UA–like telemetry for the signals defined in `silver_signal_master` and stream it into the **Eventhouse** via the Eventstream Custom Endpoint.
# 
# **Key steps**
# 
# 1. **Configuration & Eventhouse/KQL validation**
#    - Reloads `rti_demo_settings` and re‑confirms:
#      - Eventhouse and KQL DB (IDs and URIs).
#      - `OPCUAEvents` KQL table exists with the **slim schema** (event_time, opcua_node_id, value, quality).
#    - Validates the table schema via Kusto `.create-merge` and `getschema`.
# 
# 2. **Eventstream definition & Custom Endpoint**
#    - Ensures the Eventstream `RTI_Demo_Eventstream` exists in the target folder.
#    - Updates definition to enforce:
#      - `CustomEndpoint` source → `DefaultStream` → Eventhouse destination.
#    - Retrieves the **Custom Endpoint connection** including a primary SAS connection string.
# 
# 3. **Simulation key set from signal_master**
#    - Reads `silver_signal_master` and builds a key list:
#      - Each key: `opcua_node_id` + `signal_type`.
#    - Filters to `is_active = True` and deduplicates by `opcua_node_id`.
# 
# 4. **Slim OPC UA event generator & HTTP sender**
#    - Builds HTTP POST target for Eventstream’s Event Hub endpoint from connection string.
#    - For each signal key, generates a minimal JSON event:
#      - `event_time` – current UTC timestamp.
#      - `opcua_node_id` – unique per signal.
#      - `value` – synthetic numeric based on `signal_type` (temperature, pressure, vibration, etc.).
#      - `quality` – `GOOD`/`UNCERTAIN`/`BAD` with configurable probabilities.
#    - Sends events via HTTP/SAS to the Custom Endpoint in **multiple iterations**, simulating a short live run.
# 
# 5. **Eventhouse ingestion validation**
#    - Uses a Kusto query against `OPCUAEvents` with `ingestion_time()` to verify that **new rows** arrived after the simulation start.
#    - Prints summary:
#      - New row count
#      - First and last `event_time`
#      - Latest `ingestion_time`
# 
# After RTI_006:
# - Live OPC UA–like events are streaming into `OPCUAEvents` in Eventhouse.
# - Each event includes `opcua_node_id` that matches a row in `silver_signal_master`, enabling semantic linking.
# 
# ---
# 
# ### 7. RTI_007 – Bind Eventhouse RTI Stream to `signal_master`
# 
# **Notebook:** `RTI_007_TimeSeriesBinding_RTI_signal`
# 
# **Goal:** Add a **TimeSeries DataBinding** from the Eventhouse table `OPCUAEvents` to `signal_master` in the ontology, using `opcua_node_id` as the semantic key.
# 
# **Key steps**
# 
# 1. **Configuration & helpers**
#    - Reads `rti_demo_settings` for ontology name, Eventhouse, KQL DB, and table names.
#    - Resolves:
#      - Ontology `RTI_Demo_Ontology` and `ontology_id`.
#      - Eventhouse item and KQL DB item, ensuring they are in the target folder.
#      - Eventhouse **query URI**.
#    - Confirms the slim `OPCUAEvents` schema via Kusto (`event_time`, `opcua_node_id`, `value`, `quality`).
# 
# 2. **Validate ontology entity `signal_master`**
#    - Fetches live ontology definition via `getDefinition`.
#    - Locates `signal_master` EntityType and checks:
#      - Static key property `opcua_node_id` exists.
#      - `entityIdParts` contain the `opcua_node_id` property ID.
#      - `timeseriesProperties` include `event_time`, `value`, `quality`.
#    - Builds property maps for use in DataBinding:
#      - Static: `opcua_node_id` → property ID
#      - Time‑series: `event_time`, `value`, `quality` → property IDs
# 
# 3. **Build and push Eventhouse TimeSeries DataBinding**
#    - Removes any existing Eventhouse TimeSeries DataBindings for `signal_master` (if `REPLACE_EXISTING_TIMESERIES_BINDING = True`).
#    - Constructs a new **TimeSeries DataBinding**:
#      - `dataBindingType = TimeSeries`
#      - `timestampColumnName = event_time`
#      - `sourceTableProperties`:
#        - `sourceType = KustoTable`
#        - `workspaceId = <workspace>`
#        - `itemId = <Eventhouse ID>`
#        - `clusterUri = <Eventhouse query URI>`
#        - `databaseName = RTI_Demo_Eventhouse`
#        - `sourceTableName = OPCUAEvents`
#      - `propertyBindings`:
#        - `opcua_node_id` (Eventhouse column) → static property `signal_master.opcua_node_id`
#        - `event_time` → timeseries property `signal_master.event_time`
#        - `value` → timeseries property `signal_master.value`
#        - `quality` → timeseries property `signal_master.quality`
#    - Adds the binding part under:
#      - `EntityTypes/{signal_entity_id}/DataBindings/<guid>.json`
#    - Pushes updated definition via `updateDefinition`.
# 
# 4. **Verification**
#    - Re‑reads `getDefinition` and inspects all TimeSeries DataBindings under `signal_master`.
#    - Confirms **exact contract**:
#      - SourceType = `KustoTable`, `workspaceId` = workspace, `itemId` = Eventhouse ID.
#      - `clusterUri`, `databaseName`, `sourceTableName` match `OPCUAEvents`.
#      - `timestampColumnName = event_time`.
#      - Exactly four property bindings matching the mapping above.
# 
# After RTI_007:
# - `signal_master` has a **live TimeSeries DataBinding** to the Eventhouse `OPCUAEvents` table.
# - Every RTI event (`opcua_node_id`, `event_time`, `value`, `quality`) is semantically bound to:
#   - The structured signal (`signal_master` row)
#   - Its instrument, equipment, system, and facility (through ontology relationships)
#   - Downstream analytics and Agents can reason over **both structure and live telemetry**.
# 
# ---
# 
# ## Original Domain‑Level Data Description
# 
# *(This section restates the earlier domain‑by‑domain description of the mock dataset for reference.)*
# 
# ### 1. STID – Engineering Master Data
# **Path:** `bronze/stid/`
# 
# Files:
# - `facilities_stid.csv`
# - `systems_stid.csv`
# - `equipment_stid.csv`
# - `instruments_stid.csv`
# 
# **Purpose**
# - Defines the *engineering hierarchy*: Facility → System → Equipment → Instrument
# - Provides stable identifiers and tag names used across all other domains
# 
# **Typical attributes**
# - Equipment: manufacturer, model, criticality, install date, status
# - Instruments: tag, type (PT/TT/FT/VT/ZT), unit, OPC UA node ID
# 
# ---
# 
# ### 2. SAP – Maintenance & Work Management
# **Path:** `bronze/sap/`
# 
# Files:
# - `sap_pm_workorders.csv`
# - `sap_pm_notifications.csv`
# 
# **Purpose**
# - Simulates SAP PM extracts
# - Enables linking operational conditions to maintenance actions
# 
# **Key relationships**
# - Equipment → WorkOrder
# - WorkOrder → Notification
# 
# ---
# 
# ### 3. OPC UA – Time‑Series Telemetry (Historical Sample)
# **Path:** `bronze/opcua/`
# 
# Files:
# - `opcua_telemetry_2h.jsonl`
# 
# **Purpose**
# - Simulates historical sensor data (pressure, temperature, flow, vibration, position)
# - Includes normal operation and injected anomalies
# 
# **Schema (simplified)**
# - event_time
# - tag
# - instrument_id
# - equipment_id
# - system_id
# - facility_id
# - value, unit, quality
# 
# This data is suitable for:
# - Eventstream/Eventhouse validation
# - Near‑real‑time monitoring examples
# - Operations Agent reasoning
# 
# ---
# 
# ### 4. Common Library – Standards & Rules
# **Path:** `bronze/common_library/`
# 
# Files:
# - `common_library_classes.csv`
# - `common_library_tag_rules.csv`
# 
# **Purpose**
# - Represents engineering standards (CFIHOS‑style)
# - Defines required/optional properties per equipment class
# - Defines tag naming rules using regex‑like patterns
# 
# **Usage in Fabric IQ**
# - Ontology constraints
# - Data quality validation
# - Agent reasoning (e.g., “Which tags violate standards?”)
# 
# ---
# 
# ### 5. SOLV Sheet – Design & Engineering Limits
# **Path:** `bronze/solv/`
# 
# Files:
# - `solv_sheet_equipment_limits.xlsx`
# 
# **Purpose**
# - Stores design pressure, temperature, and flow limits
# - Links each equipment item to its datasheet document
# 
# **Typical use cases**
# - Alarm threshold comparison
# - Operations Agent decisions
# - Engineering context in analytics
# 
# ---
# 
# ### 6. P&ID Diagrams
# **Path:** `bronze/pid/`
# 
# Files:
# - `pid_sep_train_1.png`
# - `pid_sep_train_1.pdf`
# - `pid_parsed_elements.csv`
# - `pid_parsed_connections.csv`
# 
# **Purpose**
# - Provides visual engineering topology (P&ID)
# - Includes *mock parsed outputs* representing diagram extraction
# 
# **Parsed outputs**
# - Elements: equipment & instruments found in diagram
# - Connections: process‑flow relationships between equipment
# 
# Used to build **graph topology** in Fabric IQ.
# 
# ---
# 
# ### 7. Engineering Documents & Metadata
# **Path:** `bronze/documents/`
# 
# Files:
# - `system_overview_separation_train_1.pdf`
# - `DOC-DS-<equipment_id>_datasheet.pdf`
# - `document_index.csv`
# - `annotations.csv`
# - `3d_model_metadata.json`
# 
# **Purpose**
# - Provides unstructured engineering context
# - Enables document‑to‑asset linking
# - Supports annotations and human knowledge capture
# 
# ---
# 
# ## Medallion Architecture
# 
# ### Bronze
# - Raw ingested data from all sources
# - Minimal transformation
# 
# ### Silver
# - Cleaned and conformed tables
# - Stable IDs and relationships
# - Primary binding layer for Ontology
# 
# ### Gold
# - Derived operational signals
# - Latest measurements
# - Design‑limit comparisons
# - Health indicators
# 
# ---
# 
# ## Fabric IQ Modeling Blueprint
# 
# ### Entity Types
# - Facility
# - System
# - Equipment
# - Instrument
# - Signal (via `signal_master`)
# - Measurement (as Eventhouse time‑series bound to signals)
# - WorkOrder
# - Notification
# - Document
# - Annotation
# 
# ### Relationship Types
# - Facility HAS_SYSTEM System
# - System HAS_EQUIPMENT Equipment
# - Equipment HAS_INSTRUMENT Instrument
# - Instrument EMITS Measurement (via Eventhouse TimeSeries binding)
# - Equipment HAS_WORKORDER WorkOrder
# - Equipment HAS_DOCUMENT Document
# - Equipment CONNECTS_TO Equipment (P&ID)
# 
# ---
# 
# ## Example Validation Scenarios
# 
# - Which equipment shows abnormal vibration and has open work orders?
# - Which pumps exceed 90% of design pressure?
# - Show all assets connected downstream of a failed valve.
# - Which instruments violate tag naming standards?
# - For a given signal, show its live trend, related equipment, work orders, and documents.
# 
# ---
# 
# ## Notes & Limitations
# - All data is **synthetic**.
# - P&ID parsing is simulated via prepared CSV outputs.
# - 3D data is metadata‑only (no geometry).
# - Many steps are idempotent (safe to rerun) but assume a clean demo workspace/folder for best reproducibility.
# 
# ---
# 
# ## Intended Audience
# - Fabric IQ evaluations
# - Oil & Gas industry demos
# - Architecture workshops
# - Partner and customer proof‑of‑concepts
# 
# ---
# 
# **End of document**

