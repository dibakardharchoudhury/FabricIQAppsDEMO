# Fabric IQ RTI Demo – Mock Energy Dataset & Ontology Pipeline

## Purpose

This repository contains a **fully synthetic energy dataset** and an **end-to-end Fabric pipeline** that demonstrates:
- Ingestion from raw files and seeded telemetry
- Transformation through a medallion architecture (Bronze/Silver/Gold)
- Real-time streaming via Eventstream with OPC UA–like telemetry
- Ontology binding with semantic relationships
- Live time-series data integration with Eventhouse

The scenario is designed to test **Microsoft Fabric** and **Fabric IQ** end-to-end capabilities including:
- Lakehouse (Bronze/Silver/Gold)
- Streaming (Eventstream with OPC UA–like telemetry)
- Eventhouse & KQL Database
- Ontology (entity types, relationships, data bindings)
- Graph (asset topology & relationships)
- Data Agent & Operations Agent

**All data is fully synthetic and mirrors common industrial data landscapes** (engineering, operations, maintenance, documents) while containing no real plant or customer data.

---

## Notebook Execution Order

The complete pipeline is implemented across **9 notebooks (RTI_000–RTI_008)**, with each playing a specific role:

| Notebook | Role |
|----------|------|
| **RTI_000** | Dataset / documentation notebook. Provides full context and architecture overview. |
| **RTI_001** | Creates shared foundation and writes `rti_demo_settings` (single source of truth). |
| **RTI_002** | Creates structured/static demo source data and seeds signal metadata. |
| **RTI_003** | Transforms structured source data into Lakehouse silver tables. |
| **RTI_004** | Builds and deploys ontology entity and relationship definitions. |
| **RTI_005** | Adds static Lakehouse DataBindings and relationship contextualizations. |
| **RTI_006** | Configures Eventstream/Eventhouse and generates OPC UA telemetry. |
| **RTI_007** | Adds Eventhouse TimeSeries DataBinding to `signal_master`. |
| **RTI_008** | Builds and deploys the Real-Time Dashboard over `OPCUAEvents` (telemetry stats). |
| **OntologyAgent** | Uses the finished ontology as the semantic access layer for questions and analysis. |

---

### RTI_000 – Documentation & Overview
**Notebook:** `RTI_000_sampleEnergyDataset_Doc`

Provides context for the entire end-to-end process, dataset layout, and architecture overview.

---

### RTI_001 – Initialize Workspace & Lakehouse
**Notebook:** `RTI_001_create_lakehouse_shortcut`

**Goal:** Bootstrap the Fabric workspace and create a shared foundation that all later notebooks depend on.

**Key concept:** Creates or resolves the core Fabric items and writes the shared settings table `rti_demo_settings`, which becomes the **single source of truth** for all subsequent notebooks. This avoids reintroducing hardcoded workspace, item, folder, or table names in notebooks 002–007.

**Configuration written to `rti_demo_settings` table:**
- `workspace_id`, `workspace_folder_path`, `target_folder_id`
- `lakehouse_name`, `lakehouse_id`
- `ontology_name`
- `eventhouse_name`, `kql_database_name`, `eventhouse_table_name`
- `eventstream_name`
- Silver table names (silver_facilities, silver_systems, silver_equipment, silver_instruments, silver_signal_master)
- Key Vault settings

**Key steps:**
1. **Configuration & settings table**
   - Defines common names/IDs (Lakehouse, Eventhouse, Eventstream, ontology, table names, Key Vault secrets)
   - Writes shared table `rti_demo_settings` in the Lakehouse (Delta table)

2. **Service Principal auth via Key Vault**
   - Uses `notebookutils.credentials.getSecret` to read SPN credentials
   - Obtains Entra ID token for Fabric REST API (`https://api.fabric.microsoft.com/.default`)

3. **Workspace & Lakehouse creation (Fabric REST)**
   - Ensures workspace folder path `joa/RTI_Demo` exists (idempotent)
   - Ensures Lakehouse `Energy_IQ_LakehouseRTI` exists and records its ID

4. **ADLS Gen2 connection & shortcut**
   - Creates cloud connection (Service Principal) to ADLS account
   - Creates shortcut: `Files/bronze` → ADLS path `https://ontologyjoa.dfs.core.windows.net/dataiq/bronze`

5. **Persist shared settings**
   - Writes `rti_demo_settings` as Delta table (single source of truth)

**Output:** Lakehouse ready with bronze data accessible via shortcut; all later notebooks read from `rti_demo_settings`.

---

### RTI_002 – Setup Streaming Infrastructure
**Notebook:** `RTI_002_Setup_Eventhouse_Only`

**Goal:** Create the streaming backbone and seed the static plant metadata that becomes ontology entities.

**Key concept:** The structured model hierarchy is: **facility → system → equipment → instrument → signal_master**. The `signal_master` table is critical because it acts as the **bridge between static asset metadata and RTI telemetry** through the `opcua_node_id` field.

**Important:** Sensors/signals are defined in the metadata source files that feed this structured layer. To add sensors, add them to those metadata sources so they appear in `silver_signal_master` after RTI_003.

**Signal identity:** `opcua_node_id`

**Key steps:**

1. **Load and extend shared settings**
   - Reads `rti_demo_settings` and adds Key Vault secret names

2. **Eventhouse & KQL DB (Fabric REST)**
   - Creates Eventhouse `RTI_Demo_Eventhouse`
   - Creates KQL database `RTI_Demo_Eventhouse` attached to Eventhouse
   - Polls for Kusto query/ingest URIs
   - Creates KQL table `OPCUAEvents` with slim schema:
     - `event_time` (datetime)
     - `opcua_node_id` (string)
     - `value` (real)
     - `quality` (string)

3. **Eventstream & Custom Endpoint**
   - Creates Eventstream `RTI_Demo_Eventstream`
   - Adds `CustomEndpoint` source (`OPCUA_CustomEndpoint`)
   - Wires to Eventhouse destination targeting `OPCUAEvents` table
   - Retrieves connection string for later telemetry ingestion

4. **Seed signal metadata into Silver**
   - Generates synthetic signal metadata for devices T001–T005
   - Writes `silver_instruments`: tall/slim instrument metadata
   - Writes `silver_signal_master`: normalized signal master containing:
     - `opcua_node_id` (unique identifier for each signal)
     - `tag`, `instrument_id`, `equipment_id`, `system_id`, `facility_id`
     - `unit`, `is_active`, `signal_type`

**Output:** Streaming infrastructure ready with signal master model seeded; `silver_signal_master` ready for ontology binding.

---

### RTI_003 – Ingest & Transform (Bronze → Silver → Gold)
**Notebook:** `RTI_003_ingest_transform_medallion`

**Goal:** Transform all bronze files into conformed silver tables that become the source for ontology generation.

**Key concept:** This notebook creates the clean Lakehouse tables used by the ontology generation step. The most critical table is **`silver_signal_master`**, which contains the static signal metadata that bridges to RTI telemetry.

**Core silver tables (essential for ontology):**
- `silver_facilities`
- `silver_systems`
- `silver_equipment`
- `silver_instruments`
- `silver_signal_master` (each active sensor/signal = one row with `opcua_node_id` identity)

**`silver_signal_master` schema:**
- `opcua_node_id` (identity key)
- `tag`, `instrument_id`, `equipment_id`, `system_id`, `facility_id`
- `unit`, `is_active`, `signal_type`

**Additional silver tables created:**

From `bronze/sap/` (maintenance):
- `silver_workorders` – SAP PM–like work orders
- `silver_notifications` – SAP notification records

From `bronze/opcua/` (historical telemetry):
- `silver_opcua_measurements` – historical OPC UA time-series (Delta table)

From `bronze/common_library/` (standards):
- `silver_common_library_classes` – tag class definitions
- `silver_common_library_tag_rules` – tag naming rules

From `bronze/solv/` (design limits):
- `silver_equipment_limits` – engineering envelopes for pressure, temperature, flow

From `bronze/pid/` (P&ID topology):
- `silver_pid_elements` – parsed elements (equipment/instruments)
- `silver_pid_connections` – process connections

From `bronze/documents/` (unstructured):
- `silver_documents` – document index
- `silver_annotations` – annotations
- `silver_3d_model_metadata` – 3D model metadata

**Key gold tables created (examples):**
- `gold_limit_breaches` – equipment limit breaches
- `gold_equipment_health` – latest measurement + open work orders
- `gold_equipment_workorders_summary` – open/closed WO counts
- `gold_equipment_notification_events` – notification analytics
- `gold_opcua_quality_stats` – measurement quality distribution
- `gold_instrument_classification` – signal-type distribution
- `gold_pid_topology_stats` – connection statistics

**Output:** All structured domains available as silver tables; operational KPIs in gold layer; `silver_signal_master` ready for ontology binding.

---

### RTI_004 – Build Ontology from Structured Data
**Notebook:** `RTI_004_build_ontology_mapping_rti_structured`

**Goal:** Automatically generate and deploy a Fabric Ontology that models structured entities and relationships.

**Entity identity keys (how each entity is uniquely identified):**
- `facilities` → `facility_id`
- `systems` → `system_id`
- `equipment` → `equipment_id`
- `instruments` → `instrument_id`
- `signal_master` → `opcua_node_id` (special: used to connect to RTI telemetry)

**Key steps:**

1. **Entity type inference**
   - Derives entity names from table names
   - Resolves primary keys using naming heuristics + overrides (see table above)
   - Creates EntityTypes with properties typed via Spark schema
   - For `signal_master`: adds Eventhouse RTI **`timeseriesProperties`** (event_time, value, quality)

2. **signal_master special configuration**
   - Static properties from `silver_signal_master` (tag, instrument_id, equipment_id, system_id, facility_id, unit, is_active, signal_type)
   - Time-series properties: `event_time`, `value`, `quality` (will be bound to Eventhouse in RTI_007)
   - Uses `opcua_node_id` as identity key for connecting to telemetry

3. **Relationship type generation**
   - Implements hierarchy: systems → facilities, equipment → systems, instruments → equipment, signals → instruments
   - Uses join keys based on identity properties
   - Generates relationship audit for documentation
   - Final relationship path: **signal_master → instruments → equipment → systems → facilities**

4. **Deploy ontology via Fabric REST**
   - Ensures ontology item `RTI_Demo_Ontology` exists
   - Pushes definition in stages (EntityTypes first, then RelationshipTypes)
   - Verifies 5 entity types and 4 relationship types

5. **Audit tables written:**
   - `ontology_parts_latest` – ontology parts and definitions
   - `ontology_entity_audit` – entity → PK, ID parts, FK columns
   - `ontology_relationship_audit` – relationship definitions and effective join keys

**Output:** Clean structured ontology with 5 entity types and 4 relationship types; `signal_master` prepared for RTI time-series binding.

---

### RTI_005 – Static Lakehouse DataBindings
**Notebook:** `RTI_005_entity_DataBinding_rti_structured`

**Goal:** Bind structured Lakehouse tables to ontology entities and create relationship contextualizations.

**Important:** This notebook adds **static Lakehouse DataBindings only**. Eventhouse telemetry binding is added in RTI_007.

**Static bindings (Lakehouse tables → Ontology entities):**
- `facilities` → `silver_facilities`
- `systems` → `silver_systems`
- `equipment` → `silver_equipment`
- `instruments` → `silver_instruments`
- `signal_master` → `silver_signal_master` (bound by `opcua_node_id`)

**Key steps:**

1. **Static DataBindings (Lakehouse tables)**
   - Creates NonTimeSeries DataBindings from silver tables to entity properties
   - Maps `sourceColumnName` → `targetPropertyId` for all static columns
   - One DataBinding per entity

2. **Relationship Contextualizations**
   - Calculates join keys between source and target tables (shared ID columns)
   - Generates Contextualizations describing how to join entities via Lakehouse tables
   - Documents effective join keys in audit table

3. **Push updated ontology**
   - Merges DataBindings and Contextualizations with existing parts
   - Verifies 5 static DataBindings and 4 relationship Contextualizations

**Output:** All structured tables bound to ontology entities with relationship context established via Lakehouse joins.

---

### RTI_006 – Generate & Ingest Live OPC UA Stream
**Notebook:** `RTI_006_generate_and_ingest_OPCUA_Stream`

**Goal:** Configure Eventstream/Eventhouse and generate OPC UA–like telemetry for signals defined in `silver_signal_master`.

**Important:** The generated values come from the simulator code in this notebook, not from metadata files. Signals to simulate are read from `silver_signal_master` where `is_active = True`.

**Eventhouse table schema (intentionally slim):**
- `event_time` (timestamp)
- `opcua_node_id` (signal identifier)
- `value` (numeric telemetry value)
- `quality` (GOOD/UNCERTAIN/BAD status)

**Key steps:**

1. **Configuration & Eventhouse/KQL validation**
   - Confirms Eventhouse, KQL DB, and `OPCUAEvents` table
   - Validates slim schema via Kusto `.create-merge` and `getschema`

2. **Eventstream & Custom Endpoint**
   - Ensures Eventstream `RTI_Demo_Eventstream` exists
   - Updates definition to enforce: CustomEndpoint source → DefaultStream → Eventhouse destination
   - Retrieves Custom Endpoint connection including primary SAS connection string

3. **Simulation key set from signal_master**
   - Reads `silver_signal_master` and builds key list (opcua_node_id + signal_type)
   - Filters to `is_active = True` and deduplicates by `opcua_node_id`

4. **OPC UA event generation & ingestion**
   - Generates minimal JSON events:
     - `event_time` – current UTC timestamp
     - `opcua_node_id` – unique per signal
     - `value` – synthetic numeric based on `signal_type` (temperature, pressure, vibration, etc.)
     - `quality` – GOOD/UNCERTAIN/BAD with configurable probabilities
   - Sends via HTTP/SAS to Custom Endpoint in multiple iterations
   - Simulates short live run

5. **Validation**
   - Uses Kusto query against `OPCUAEvents` with `ingestion_time()` to verify new rows
   - Prints summary: row count, first/last event_time, latest ingestion_time

**To customize:** Update the simulator logic in notebook 006 section to add drift, spikes, stuck sensors, bad-quality windows, or other anomalies.

**Output:** Live OPC UA–like events streaming into Eventhouse `OPCUAEvents` table, with each event linked to its signal via `opcua_node_id`.

---

### RTI_007 – Bind Eventhouse RTI Stream to Signal Master
**Notebook:** `RTI_007_TimeSeriesBinding_RTI_signal`

**Goal:** Add a TimeSeries DataBinding from Eventhouse table `OPCUAEvents` to `signal_master` in ontology, connecting live telemetry to the static asset hierarchy.

**Eventhouse source column → Ontology target mapping:**
- `opcua_node_id` → `signal_master.opcua_node_id` (static property for joining)
- `event_time` → `signal_master.event_time` (time-series property)
- `value` → `signal_master.value` (time-series property)
- `quality` → `signal_master.quality` (time-series property)

**Key steps:**

1. **Configuration & helpers**
   - Reads `rti_demo_settings` for ontology, Eventhouse, KQL DB names
   - Resolves ontology `RTI_Demo_Ontology` and its ID
   - Confirms slim `OPCUAEvents` schema via Kusto

2. **Validate ontology entity `signal_master`**
   - Fetches live ontology definition via `getDefinition`
   - Locates `signal_master` EntityType and verifies:
     - Static key property `opcua_node_id` exists
     - `entityIdParts` contain the `opcua_node_id` property ID
     - `timeseriesProperties` include `event_time`, `value`, `quality`
   - Builds property maps for DataBinding

3. **Build & push TimeSeries DataBinding**
   - Removes any existing Eventhouse TimeSeries DataBindings for `signal_master` (if `REPLACE_EXISTING_TIMESERIES_BINDING = True`)
   - Constructs TimeSeries DataBinding:
     - `dataBindingType = TimeSeries`
     - `timestampColumnName = event_time`
     - `sourceType = KustoTable`
     - `clusterUri`, `databaseName`, `sourceTableName` pointing to Eventhouse `OPCUAEvents`
     - Four property bindings (see mapping above)
   - Adds binding part under `EntityTypes/{signal_entity_id}/DataBindings/<guid>.json`
   - Pushes updated definition via `updateDefinition`

4. **Verification**
   - Re-reads `getDefinition` and inspects all TimeSeries DataBindings
   - Confirms exact contract:
     - SourceType = `KustoTable`, correct workspace/Eventhouse IDs
     - `timestampColumnName = event_time`
     - All four property bindings present and correct

**Output:** `signal_master` has live TimeSeries DataBinding to Eventhouse `OPCUAEvents`; RTI events are semantically bound to:
- The physical signal (opcua_node_id)
- Its instrument, equipment, system, and facility (through ontology relationships)
- Ready for downstream analytics and Agents

---

### RTI_008 – Build & Deploy the Real-Time Dashboard
**Notebook:** `RTI_008_build_realtime_dashboard`

**Goal:** Replicate the reference RTI dashboard as a Fabric **Real-Time Dashboard** over the Eventhouse `OPCUAEvents` table, adapted to our slim schema.

**Key concept:** The reference dashboard grouped by `facility_id` / `equipment_id`, columns that do **not** exist in our slim `OPCUAEvents` table (`event_time`, `opcua_node_id`, `value`, `quality`). Instead of adding a Kusto lookup table or shortcut, the notebook parses the hierarchy already encoded in `opcua_node_id` (`ns=2;s=T001.inlet_pressure`):
- `Turbine = extract(@'s=([^.]+)\.', 1, opcua_node_id)` → `T001`..`T005`
- `Signal  = extract(@'\.([^.]+)$', 1, opcua_node_id)` → `inlet_pressure`, `power_output`, ...

Since the demo data is a single facility (`FACILITY_RTI_001`) with 5 turbines, the two hardcoded per-facility timecharts become per-turbine timecharts (T001, T002).

**Tiles (schema_version 77):**
1. Total Count of Events (card)
2. Events per 30 minutes (table)
3. Sample 1000 Rows (table)
4. Equipment Health by Turbine (table — Good/Bad/Uncertain counts)
5. Equipment Health by Turbine (stacked bar)
6. Events per 30 minutes (timechart)
7. Turbine T001 – Signal Values (timechart)
8. Turbine T002 – Signal Values (timechart)

All tiles honor a `Time range` duration parameter (`_startTime`/`_endTime`).

**Key steps:**

1. Reads `rti_demo_settings` for the live `cluster_query_uri`, `fabric_kql_db_id`, and KQL DB name (written by RTI_002).
2. Injects those values into the dashboard definition so the data source points at this workspace's Eventhouse.
3. Writes an importable copy to `Files/dashboards/` in the Lakehouse.
4. Deploys it as a Fabric **KQLDashboard** item via REST (SPN auth from Key Vault). If the deploy call fails, it prints manual-import steps for the file it wrote (New → Real-Time Dashboard → Manage → Replace with file).
5. Persists `dashboard_name` (and `dashboard_id` when deployed) back to `rti_demo_settings`.

**Static artifact:** A ready-to-import copy is also checked in at `RTI_DEMO_V3/Dashboards/RTI_Demo_OPCUA_TelemetryStats.Dashboard.json` (with `__CLUSTER_QUERY_URI__` / `__KQL_DB_NAME__` / `__KQL_DB_ID__` placeholders resolved by the notebook, or re-pointed to your Eventhouse on manual import).

**Output:** A Real-Time Dashboard in the `RTI_DEMO_V3` folder showing live OPC UA telemetry statistics.

---

## Dataset Layout in ADLS / OneLake

```
fabric_iq_oilgas_mock/
├── bronze/
│   ├── stid/                    # Engineering master data
│   ├── sap/                     # Maintenance & Work management
│   ├── opcua/                   # Time-series telemetry (historical sample)
│   ├── common_library/          # Standards & rules
│   ├── solv/                    # Design & engineering limits
│   ├── pid/                     # P&ID diagrams & parsed outputs
│   └── documents/               # Engineering documents & metadata
├── silver/                      # Cleaned & conformed data
├── gold/                        # Derived operational signals
├── scripts/
└── docs/
```

All raw data lands under **`Files/bronze`** in the Lakehouse via shortcut created in RTI_001.

---

## Medallion Architecture

### Bronze Layer
- Raw ingested data from all sources
- Minimal transformation
- Represents as-received data fidelity

### Silver Layer
- Cleaned and conformed tables
- Stable IDs and relationships
- Primary binding layer for Ontology
- Ready for analytics and entity binding

### Gold Layer
- Derived operational signals
- Latest measurements with status
- Design-limit comparisons
- Health indicators and aggregations

---

## Fabric IQ Semantic Model

### Entity Types
- **Facility** – physical locations
- **System** – systems within facilities
- **Equipment** – equipment master (pumps, valves, compressors)
- **Instrument** – instruments/sensors on equipment
- **Signal** – via `signal_master` (live OPC UA nodes)
- **Measurement** – time-series data bound to signals
- **WorkOrder** – maintenance work orders
- **Notification** – operational notifications
- **Document** – engineering documents
- **Annotation** – human knowledge capture

### Relationship Types
- Facility **HAS_SYSTEM** System
- System **HAS_EQUIPMENT** Equipment
- Equipment **HAS_INSTRUMENT** Instrument
- Instrument **EMITS** Measurement (via Eventhouse TimeSeries binding)
- Equipment **HAS_WORKORDER** WorkOrder
- Equipment **HAS_DOCUMENT** Document
- Equipment **CONNECTS_TO** Equipment (P&ID topology)

---

## Validation & Use Case Scenarios

Example queries enabled by this architecture:

- **Which equipment shows abnormal vibration and has open work orders?**
  - Join signal trends → equipment conditions → maintenance status

- **Which pumps exceed 90% of design pressure?**
  - Compare live measurements → equipment limits → identify at-risk assets

- **Show all assets connected downstream of a failed valve.**
  - Query P&ID topology → navigate graph relationships

- **Which instruments violate tag naming standards?**
  - Match `silver_instruments` → `silver_common_library_tag_rules`

- **For a given signal, show its live trend, related equipment, work orders, and documents.**
  - Traverse ontology: signal → instrument → equipment → facility + linked work orders + documents

---

## Domain-Level Data References

### STID – Engineering Master Data
**Files:** `facilities_stid.csv`, `systems_stid.csv`, `equipment_stid.csv`, `instruments_stid.csv`

Defines engineering hierarchy: Facility → System → Equipment → Instrument. Provides stable identifiers and tag names used across all domains.

### SAP – Maintenance & Work Management
**Files:** `sap_pm_workorders.csv`, `sap_pm_notifications.csv`

Simulates SAP PM extracts linking operational conditions to maintenance actions.

### OPC UA – Time-Series Telemetry
**Files:** `opcua_telemetry_2h.jsonl`

Simulates historical sensor data (pressure, temperature, flow, vibration, position) including normal operation and injected anomalies.

### Common Library – Standards & Rules
**Files:** `common_library_classes.csv`, `common_library_tag_rules.csv`

Represents engineering standards defining required/optional properties per equipment class and tag naming rules.

### SOLV Sheet – Design & Engineering Limits
**Files:** `solv_sheet_equipment_limits.xlsx`

Stores design pressure, temperature, and flow limits linking each equipment to its datasheet.

### P&ID Diagrams
**Files:** `pid_sep_train_1.png`, `pid_sep_train_1.pdf`, `pid_parsed_elements.csv`, `pid_parsed_connections.csv`

Visual engineering topology with mock parsed outputs representing diagram extraction.

### Engineering Documents & Metadata
**Files:** `system_overview_*.pdf`, `DOC-DS-*_datasheet.pdf`, `document_index.csv`, `annotations.csv`, `3d_model_metadata.json`

Unstructured engineering context for document-to-asset linking and annotations.

---

## Key Concepts

### Ontology Binding
Static Lakehouse tables are bound to ontology entities via DataBindings, while relationship contextualizations describe how to join entities through actual table columns.

### Time-Series Binding
The `OPCUAEvents` Eventhouse table is bound to the `signal_master` entity via a TimeSeries DataBinding, enabling live telemetry to be semantically associated with:
- The physical signal (opcua_node_id)
- Its instrument, equipment, system, and facility (through relationships)
- Downstream analytics and Agents

### Data Pipeline
- Raw files → Bronze (ADLS shortcut)
- Bronze → Silver (cleansed, conformed)
- Silver → Gold (derived analytics)
- Silver → Ontology (entity/relationship definitions)
- Silver → Lakehouse DataBindings (static)
- Eventhouse → TimeSeries DataBindings (live)

---

## Setup & Configuration

### Prerequisites
- Microsoft Fabric workspace with Lakehouse and Eventhouse capabilities
- Service Principal with appropriate permissions
- Azure Key Vault containing SPN credentials
- ADLS Gen2 account containing bronze dataset

### Execution
1. Run notebooks in order: RTI_001 → RTI_008
2. Each notebook reads from `rti_demo_settings` for configuration
3. All notebooks are idempotent where feasible (safe to rerun)

### Best Practices
- Run in clean demo workspace/folder for best reproducibility
- Verify settings table after RTI_001
- Monitor Eventhouse ingestion status during RTI_006
- Validate ontology definition after each deployment step

---

## Intended Audience

- **Fabric IQ evaluations** – comprehensive end-to-end scenario
- **Oil & Gas industry demos** – industrial asset & operations context
- **Architecture workshops** – medallion, streaming, and ontology patterns
- **Partner and customer proof-of-concepts** – production-ready example

---

## Notes & Limitations

- All data is **fully synthetic**
- P&ID parsing is simulated via prepared CSV outputs
- 3D data is metadata-only (no geometry)
- Many steps are idempotent but assume clean demo workspace
- For production use: customize domain models, add data validation, implement monitoring

---

## Customization Guide: Where to Change Things

### Add or Remove Sensors

**Where:** Update the metadata source files that feed `silver_signal_master`

**Steps:**
1. Modify the source files in `bronze/stid/` (especially instruments metadata)
2. Re-run RTI_003 to regenerate `silver_signal_master`
3. Re-run RTI_004 (ontology generation may pick up new signals)
4. Re-run RTI_006 (simulator will use new signals)

---

### Change Generated Telemetry Values

**Where:** Simulator logic in RTI_006

**Update:** The simulator code in notebook 006 section that generates value ranges and quality states

**Current:** Signal-type-based normal ranges with random quality

**To add:** Drift, spikes, stuck sensors, bad-quality windows, or other anomalies

---

### Modify Signal Schema or Properties

**Where:** Multiple notebooks need alignment

**Change sequence:**
1. Update `timeseriesProperties` in RTI_004 (ontology definition)
2. Update OPCUAEvents table schema in RTI_002 (Eventhouse)
3. Update event payload generation in RTI_006 (simulator)
4. Update property bindings in RTI_007 (TimeSeries DataBinding)

---

## Final Data Architecture

### Lakehouse (Static Metadata)
```
silver_signal_master
├── opcua_node_id (identity key)
├── tag, instrument_id, equipment_id, system_id, facility_id
├── unit, is_active, signal_type
└── [bound to ontology entity: signal_master]
```

### Eventhouse (Streaming Telemetry)
```
OPCUAEvents
├── event_time (timestamp)
├── opcua_node_id (identity key, joins to silver_signal_master)
├── value (telemetry numeric)
└── quality (GOOD/UNCERTAIN/BAD)
   [bound to ontology entity: signal_master via TimeSeries DataBinding]
```

### Ontology (Semantic Model)
```
signal_master
├── Static properties: from silver_signal_master
├── Time-series properties: event_time, value, quality (from OPCUAEvents)
├── Identity: opcua_node_id
└── Relationships:
    └── EMITS Measurement (to instrument_id)
        └── ON_INSTRUMENT (to instrument_id)
            └── PART_OF_EQUIPMENT (to equipment_id)
                └── IN_SYSTEM (to system_id)
                    └── IN_FACILITY (to facility_id)
```

This architecture enables:
- **Semantic queries:** "Show all vibration signals in System X with anomalies"
- **Asset context:** Link any signal to its full equipment/system/facility hierarchy
- **Live analytics:** Access to both static metadata and live time-series in one semantic model
- **Agent reasoning:** Agents can traverse relationships and reason over structured + streaming data

---

## Notes & Best Practices

- **Idempotent execution:** Most notebooks are safe to rerun (they use create-or-replace semantics)
- **Clean workspace:** For best reproducibility, run in a clean demo folder
- **Settings table:** All notebooks read from `rti_demo_settings` – verify after RTI_001
- **Production use:** Customize domain models, add data validation, implement monitoring
- **Eventhouse schema:** Keep slim and focused on high-velocity telemetry; move computed analytics to Gold layer in Lakehouse

---

## Related Resources

- **RTI_000** – Full dataset and pipeline documentation (comprehensive notebook)
- **RTI_001–RTI_008** – Detailed implementation notebooks with runnable code
- **Fabric documentation** – Lakehouse, Eventhouse, Ontology APIs
- **Eventhouse & KQL** – Time-series data modeling best practices
- **Fabric IQ Agents** – Operationalization and semantic reasoning
- **Word document** – `Raw/RTI_Ontology_Workflow_clean.docx` – Workflow overview and configuration details
