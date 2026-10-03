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
# This documentation describes a **fully synthetic energy dataset** and the **end‑to‑end Fabric pipeline** that takes it from raw files and seeded telemetry to a **generation 2 ontology with verified TMDL bindings to Lakehouse and Eventhouse data**.
# 
# The scenario is implemented across these canonical notebooks (names remain unchanged):
# - `RTI_000_sampleEnergyDataset_Doc` (this document)
# - `RTI_001_create_lakehouse_shortcut`
# - `RTI_001_create_lakehouse_SelfContained` (local seed alternative)
# - `RTI_002_Setup_Eventhouse_Only`
# - `RTI_003_ingest_transform_medallion`
# - `RTI_003_ingest_transform_medallion_SelfContained` (local seed alternative)
# - `RTI_004_build_ontology_mapping_rti_structured`
# - `RTI_005_entity_DataBinding_rti_structured`
# - `RTI_006_TimeSeriesBinding_RTI_signal`
# - `RTI_007_generate_and_ingest_OPCUA_Stream`
# - `RTI_008_build_realtime_dashboard`
# - `RTI_009_build_data_agent`
# - `RTI_010_build_operations_agent`
# - `RTI_011_seed_sql_wire_graphql_agent` (on demand after app/SQL provisioning)
# - `RTI_Orchestrator_Setup` (Stage 2 setup DAG)
# 
# The dataset and notebooks together are used to test **Microsoft Fabric** and **Fabric IQ** end‑to‑end capabilities:
# - Lakehouse (Bronze/Silver/Gold)
# - Streaming (Eventstream with OPC UA–like telemetry)
# - Eventhouse & KQL DB
# - Ontology v2 (TMDL entities, relationships, and data bindings)
# - Native ontology-owned graph, enriched by KQL telemetry and operational SQL
# - Required v2 Data Agent and full Operations Agent provisioning, with explicit opt-out
#
# **V2-only contract:** every selected live ontology must report integer
# `properties.generation == 2`. Names such as `*_V9`, Fabric REST `/v1`, and ADLS Gen2
# do not identify ontology generation. Existing v1 items are rejected, not migrated or
# deleted; provision a separate v2 target rather than converting the old item.
# Fresh v2 creation explicitly supplies `database.tmdl`, `model.tmdl`, and
# `namespaces/default.tmdl`. Reruns preserve live TMDL, identities, bindings, and
# custom parts and verify service readback before publishing local output parts.
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
# - In `Pipe_Setup`, RTI_001 runs as Stage 1 and rebinds child notebooks before
#   `RTI_Orchestrator_Setup` starts Stage 2. Its `%%configure` default Lakehouse and
#   `useRootDefaultLakehouse` arguments make all children inherit the root session.
# 
# ---
# 
# ### 2. RTI_002 – Eventhouse & KQL DB
# 
# **Notebook:** `RTI_002_Setup_Eventhouse_Only`
# 
# **Goal:** Create / reuse Eventhouse and KQL DB resources and publish their identifiers.
# RTI_003 prepares the structured signal metadata; RTI_007 configures Eventstream
# and generates live telemetry. These are separate setup/streaming responsibilities.
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
# After RTI_002:
# - Eventhouse and KQL DB are available for the downstream binding and streaming steps.
# - Completing this notebook alone does not establish live ingestion or agent readiness.
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
# **Goal:** Build the configured **Fabric Ontology v2** with structured entities,
# semantic relationships, and time-series properties ready for subsequent bindings.
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
#    - Sets the TMDL `keyProperty` to the entity's own primary key.
# 
# 3. **Entity type generation**
#    - For each table, creates an `entities/<name>.tmdl` entity with:
#      - typed `property` declarations inferred from the entity's Spark schema.
#      - `keyProperty` and stable `lineageTag` identities.
#    - For `signal_master`, adds **Eventhouse RTI time-series properties**:
#      - `event_time` (`TimeSeries<dateTime>`)
#      - `value` (`TimeSeries<double>`)
#      - `quality` (`TimeSeries<string>`)
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
#    - Writes `ontology_parts_latest` only after verified service readback, with:
#      - `.platform`, `database.tmdl`, `model.tmdl`, `namespaces/default.tmdl`
#      - `entities/<name>.tmdl`
#      - `entityRelationships.tmdl`
#    - Writes `ontology_entity_audit` (entity → PK, ID parts, FK columns).
# 
# 6. **Deploy ontology via Fabric REST**
#    - Creates a fresh v2 item with explicit TMDL database/model/default namespace,
#      or resolves the existing item **in the target folder**.
#    - Requires live integer `properties.generation == 2`; v1 is rejected without
#      conversion or deletion. Missing/ambiguous generation fails closed.
#    - Merges generated structure into the live definition, retaining identities,
#      bindings, custom TMDL, and custom parts. Conflicts fail before update.
#    - Verifies the complete definition readback before persisting output parts:
#      - 5 entity types (facilities, systems, equipment, instruments, signal_master).
#      - 4 relationship types (forming the full chain signal → instrument → equipment → system → facility).
# 
# After RTI_004:
# - A **v2 TMDL structured ontology** exists, with `signal_master` time-series
#   properties and semantic relationships. RTI_005/006 perform the data bindings.
# 
# ---
# 
# ### 5. RTI_005 – V2 Lakehouse Bindings & Relationship Contextualizations
# 
# **Notebook:** `RTI_005_entity_DataBinding_rti_structured`
# 
# **Goal:** Add TMDL Lakehouse bindings and relationship contextualizations to the
# live v2 definition, without replacing its custom content or time-series bindings.
# 
# **Key steps**
# 
# 1. **Load settings & ontology**
#    - Reads `rti_demo_settings` for Lakehouse & table names.
#    - Resolves the configured ontology and requires live integer generation 2.
#    - Reads `ontology_entity_audit` and `ontology_relationship_audit`.
# 
# 2. **Static DataBindings (Lakehouse tables)**
#    - For each entity in `{facilities, systems, equipment, instruments, signal_master}`:
#      - Confirms the corresponding silver table exists and has the required columns.
#      - Adds a TMDL `dataBinding` to `entities/<name>.tmdl`, referencing the
#        Lakehouse data source and mapping source columns to named properties.
#      - Retains existing property identities and unrelated binding blocks.
# 
# 3. **Relationship Contextualizations**
#    - For each relationship in the ontology (from `ontology_relationship_audit`):
#      - Calculates **join keys** between source and target tables (shared ID columns).
#      - Adds TMDL **contextualization** blocks describing the join to
#        `entityRelationships.tmdl`; no legacy `RelationshipTypes/**` JSON is emitted.
# 
# 4. **Push updated ontology definition**
#    - Reads and merges the live TMDL data sources, bindings, and contextualizations,
#      preserving custom parts and existing Eventhouse time-series content on rerun.
#    - Pushes updated definition via `updateDefinition` (with LRO support).
#    - Verifies service readback matches the intended complete definition, including:
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
# ### Streaming companion: RTI_007 – Generate & Ingest Live OPC UA–Like Stream
# 
# **Notebook:** `RTI_007_generate_and_ingest_OPCUA_Stream`
#
# Run on demand after setup using `Pipe_Stream`; it is described here for telemetry
# context, but RTI_006 binding below runs first in the setup DAG.
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
# After RTI_007:
# - Live OPC UA–like events are streaming into `OPCUAEvents` in Eventhouse.
# - Each event includes `opcua_node_id` that matches a row in `silver_signal_master`, enabling semantic linking.
# 
# ---
# 
# ### 6. RTI_006 – Bind Eventhouse RTI Stream to `signal_master`
# 
# **Notebook:** `RTI_006_TimeSeriesBinding_RTI_signal`
# 
# **Goal:** Add a **v2 TMDL time-series binding** from Eventhouse `OPCUAEvents` to
# `signal_master`, using `opcua_node_id` as the semantic key. RTI_006 runs during
# setup before the on-demand RTI_007 stream described above.
# 
# **Key steps**
# 
# 1. **Configuration & helpers**
#    - Reads `rti_demo_settings` for ontology name, Eventhouse, KQL DB, and table names.
#    - Resolves:
#      - Configured ontology name and `ontology_id`, requiring live integer generation 2.
#      - Eventhouse item and KQL DB item, ensuring they are in the target folder.
#      - Eventhouse **query URI**.
#    - Confirms the slim `OPCUAEvents` schema via Kusto (`event_time`, `opcua_node_id`, `value`, `quality`).
# 
# 2. **Validate ontology entity `signal_master`**
#    - Fetches live ontology definition via `getDefinition`.
#    - Locates the `signal_master` TMDL entity and checks:
#      - Static key property `opcua_node_id` exists.
#      - `keyProperty` is `opcua_node_id`.
#      - `TimeSeries<...>` properties include `event_time`, `value`, `quality`.
#    - Uses named TMDL properties and preserves live identities for the binding.
# 
# 3. **Build and push Eventhouse TimeSeries DataBinding**
#    - Merges the Eventhouse DirectQuery table, join and backing configurations into live TMDL,
#      retaining static bindings, contextualizations, and all unrelated/custom parts.
#    - Joins by `opcua_node_id`; binds standalone `event_time`, `value`, and `quality`.
#      Each time-series property has its own `valueColumn` and uses
#      `OPCUAEvents.event_time` as its `orderingColumn`, including `event_time` itself.
#    - Validates Kusto query/ingest endpoints separately; one is not inferred from the other.
#    - Rejects incompatible existing source/binding changes before `updateDefinition`.
# 
# 4. **Verification**
#    - Re‑reads `getDefinition` and verifies the entire submitted v2 definition,
#      including source identity, property mappings, timestamp, and preserved parts.
#    - Persists verified output parts only after successful readback.
# 
# After RTI_006:
# - `signal_master` has a **live TimeSeries DataBinding** to the Eventhouse `OPCUAEvents` table.
# - Every RTI event (`opcua_node_id`, `event_time`, `value`, `quality`) is semantically bound to:
#   - The structured signal (`signal_master` row)
#   - Its instrument, equipment, system, and facility (through ontology relationships)
#   - The semantic contract describes both structure and telemetry; this does not
#     establish native graph association, agent query readiness, or automation support.
#
# ### 8. RTI_008 – Dashboard
# Builds the realtime dashboard using KQL/Eventhouse resources independently of
# agent availability. Streaming is started separately through `Pipe_Stream`.
#
# ### 9. RTI_009 – v2 Data Agent
# Requires the selected live ontology and every retained ontology source to report
# integer `properties.generation == 2`. Both item ID and workspace must match.
# The same definition also selects the five curated Lakehouse silver master tables
# and the Eventhouse `OPCUAEvents` table as direct complementary sources. Existing
# Ontology instructions remain intact; an idempotent routing block assigns semantic
# relationship questions to Ontology v2, static/master queries to Lakehouse, telemetry
# queries to Eventhouse, and later operational-record queries to the SQL source added
# by RTI_011. Cross-source correlation uses only `opcua_node_id`, `equipment_id`,
# `facility_id`, and `system_id`, never display-name inference.
# Agent provisioning is always attempted; there is no mode or skip flag.
# Draft identity is read back before publish and published-stage identity must also
# be verified before publication is recorded separately. A delegated notebook-user MCP
# probe asks for the first five facility IDs/names and compares exact content against
# independently read live Lakehouse rows, without including expected values in the
# prompt. The question requests only the selected ontology; attached Lakehouse,
# Eventhouse, and SQL sources do not satisfy or bypass this source-specific check.
# All sources and custom configuration are preserved. Semantic errors fail even if `isError=false`,
# except for Fabric's exact documented temporary unsupported-Ontology-v2 response;
# wrong rows fail, and non-JSON/count-only answers are inconclusive. This is source-specific
# functional smoke evidence, not execution attestation or a synthetic receipt. Publication and evidence
# are retained on runtime failure; no static v2 skip or source fallback is used.
# Existing v1 sources are rejected, never reused, migrated, or deleted.
#
# ### 10. RTI_010 – Complete Operations Agent provisioning
# Provisioning always attempts the selected live v2 ontology source, authored
# BAD/UNCERTAIN playbook, Teams destination, parameterized email action and
# `Pipe_SendEmailAlert` pipeline; there is no mode or skip flag. No fallback that
# drops business components can report success.
# Actual HTTP/LRO/readback failures persist failure and raise. Missing Outlook OAuth2
# connection requires interactive sign-in and fails setup after scaffolding creation.
# Success means `configured`, STOPPED by default; queries, monitoring, Teams and email
# delivery still require live verification. `OperationsAgentV1` names the agent format,
# not the ontology generation.
#
# ### 11. RTI_011 – SQL seed, GraphQL, and required SQL Data Agent source
# SQL seeding and GraphQL setup run independently of Data Agent availability.
# Unless explicitly disabled, the extension requires a published RTI_009 agent with
# verified runtime evidence, rejects missing/blocked/failed/inconclusive agents and v1,
# and verifies exact draft/published
# ontology and SQL-source identities. Independent step outcomes are saved, but required
# failures fail the notebook. Prior functional smoke evidence is retained when the exact
# verified ontology source is unchanged on the same agent/workspace, and invalidated
# when that configuration changes. SQL and combined-source runtime remain unverified.
#
# Setup runs both agents only after NB06 has verified Ontology bindings and the native graph.
# Before enabling Weather it requires
# NB09 `ready` with separate `published`/runtime `verified` evidence and NB10 `configured`,
# or each capability's explicit disabled/skipped
# result. Missing, blocked or failed evidence cannot become green setup success.
#
# ### Application consumption and graph boundary
# HydroOperationsApp reads v2 TMDL and queries the ontology-managed GraphModel
# using native GQL. Its native nodes and relationships define the Knowledge Graph;
# Eventhouse KQL telemetry and operational SQL enrich those existing entities.
# Use the selected ontology's **Manage graph** workflow once to establish its
# service-owned GraphModel child; Fabric exposes no public API for that initial
# selection/association. The final NB06 phase then preserves and repairs the attached
# definition, excludes only Eventhouse time-series fields from the static projection,
# invokes `refreshGraph`, verifies readback/queryable types, and executes the Operations
# Agent GQL selector. The deployment orchestrator generates
# `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` from authoritative lineage. Ownership is not
# inferred from names or similar schemas. Missing setup or failed native queries show
# an explicit error, never a GraphQL/Lakehouse topology fallback.
# See [Knowledge Graph setup](../../docs/knowledge-graph.md) for alias mapping and limits.
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
# - Operations Agent reasoning scenarios (live v2 runtime verification still required)
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
# - Potential modeling constraints (not automatically provisioned by the five-entity model)
# - Data quality validation
# - Candidate agent questions (query support/readiness must be verified separately)
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
# - Potential additional Operations Agent decisions beyond the restored quality playbook
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
# Provides source data for potential asset-topology modeling; native ontology v2
# graph association/GQL is not verified or provisioned by these notebooks.
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
# The deployed v2 model contains the five structured entities and four relationships
# described in RTI_004; telemetry is time-series properties, not a Measurement entity.
# The extended blueprint below includes **candidate** WorkOrder, Document, and P&ID
# relationships, not a claim that RTI_004 creates them or that native GQL is available.
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
# These are candidate application/query scenarios, not verified agent capabilities.
# Use the v2 ontology-owned native graph, enriched by KQL and operational SQL;
# agent publication and successful setup alone do not prove these queries can execute.
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
