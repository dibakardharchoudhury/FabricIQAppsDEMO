# Fabric IQ RTI Demo – Mock Energy Dataset, Ontology & Real-Time Intelligence

## Purpose

This repository contains a **fully synthetic energy dataset** and an **end-to-end Microsoft Fabric solution** that demonstrates:

- Ingestion from raw files and seeded telemetry
- Transformation through a medallion architecture (Bronze/Silver/Gold)
- Ontology binding with semantic relationships (Fabric IQ)
- Real-time streaming via Eventstream with OPC UA–like telemetry into Eventhouse
- Live time-series binding of telemetry to ontology entities
- A Real-Time Dashboard, a Data Agent, and an Operations Agent (Teams alerts)
- **One-click orchestrated setup** via a notebook DAG driven from a Data Pipeline

**All data is fully synthetic** and mirrors common industrial data landscapes (engineering, operations, maintenance, documents) while containing no real plant or customer data.

---

## Environment model (`env_suffix`) — read this first

The entire demo is **parameterised by a single lever, `env_suffix`** (e.g. `V5`). Every versioned artifact name derives from it, so you can stand up parallel environments (`V5`, `V6`, `DEV`, …) in the same or different workspaces without editing any notebook.

- `env_suffix` and the other environment values are **injected by the `Pipe_Setup` pipeline** — they are **not** hardcoded in the notebooks. The notebook parameter cells intentionally ship with **blank defaults** and a fail-fast guard.
- **`RTI_001` is the single source of truth.** It derives every name from `env_suffix` and writes the shared **`rti_demo_settings`** Delta table. Notebooks `002`–`010` read everything from that table.

### Derived artifact names (for `env_suffix = V5`)

| Setting | Pattern | Example (`V5`) | Versioned? |
|---|---|---|---|
| Workspace folder | `RTI_DEMO_{env_suffix}` | `RTI_DEMO_V5` | ✅ |
| Lakehouse | `Energy_IQ_LakehouseRTI_{env_suffix}` | `Energy_IQ_LakehouseRTI_V5` | ✅ |
| Ontology | `RTI_Demo_Ontology_{env_suffix}` | `RTI_Demo_Ontology_V5` | ✅ |
| Eventhouse | `RTI_Demo_Eventhouse_{env_suffix}` | `RTI_Demo_Eventhouse_V5` | ✅ |
| KQL database | `RTI_Demo_Eventhouse_{env_suffix}` | `RTI_Demo_Eventhouse_V5` | ✅ |
| Eventstream | `RTI_Demo_Eventstream_{env_suffix}` | `RTI_Demo_Eventstream_V5` | ✅ |
| Data Agent | `RTI_Demo_Agent_{env_suffix}` | `RTI_Demo_Agent_V5` | ✅ |
| Dashboard | `RTI_Demo_OPCUA_TelemetryStats_{env_suffix}` | `RTI_Demo_OPCUA_TelemetryStats_V5` | ✅ |
| Operations Agent | `RTI_Demo_OpsAgent_{env_suffix}` | `RTI_Demo_OpsAgent_V5` | ✅ |
| Eventhouse table | `OPCUAEvents` (fixed) | `OPCUAEvents` | ❌ |
| Settings table | `rti_demo_settings` (fixed) | `rti_demo_settings` | ❌ |
| Bronze folder | `Files/bronze` (seeded) | `Files/bronze` | ❌ |
| Setup pipeline | `Pipe_Setup` (fixed) | `Pipe_Setup` | ❌ |
| Stream pipeline | `Pipe_Stream` (fixed) | `Pipe_Stream` | ❌ |
| Alert pipeline | `Pipe_SendEmailAlert` (fixed) | `Pipe_SendEmailAlert` | ❌ |

> The three **pipeline names are intentionally not versioned** — one of each per workspace.

---

## Artifacts & items inventory

### Notebooks (`Notebooks/`)

| Notebook | Role | Runs in `Pipe_Setup` DAG? |
|----------|------|:---:|
| **RTI_000_sampleEnergyDataset_Doc** | Documentation / architecture overview. Not executed by the orchestrator. | — |
| **RTI_001_create_lakehouse_SelfContained** | Foundation (self-contained): resolves workspace/Lakehouse, **seeds the STID CSVs into `Files/bronze/stid/`** (no ADLS/shortcut/connection), derives all names, writes `rti_demo_settings` (**single source of truth**), and **exits with the lakehouse name**. Runs as **Stage 1** of `Pipe_Setup`. | Stage 1 |
| **RTI_001_create_lakehouse_shortcut** | Legacy foundation variant that creates an ADLS Gen2 shortcut at `Files/bronze` instead of seeding. Kept for reference; **not** wired into `Pipe_Setup`. | — |
| **RTI_002_Setup_Eventhouse_Only** | Creates Eventhouse + KQL DB + `OPCUAEvents` table + Eventstream (Custom Endpoint → Eventhouse); seeds signal metadata into silver. | ✅ |
| **RTI_003_ingest_transform_medallion_SelfContained** | Bronze → Silver → Gold transforms; reads the seeded STID files from `Files/bronze` via `bronze_root`; produces `silver_signal_master` and the structured silver/gold tables. Wired into the setup DAG. | ✅ |
| **RTI_003_ingest_transform_medallion** | Legacy medallion variant that reads bronze via the ADLS Gen2 shortcut (`shortcut_parent_path`/`shortcut_name`). Kept for reference; **not** wired into the setup DAG. | — |
| **RTI_004_build_ontology_mapping_rti_structured** | Builds & deploys the ontology (5 entity types, 4 relationship types); adds time-series *properties* on `signal_master`. | ✅ |
| **RTI_005_entity_DataBinding_rti_structured** | Static Lakehouse DataBindings + relationship contextualizations. | ✅ |
| **RTI_006_TimeSeriesBinding_RTI_signal** | Adds the **Eventhouse TimeSeries DataBinding** from `OPCUAEvents` to `signal_master`. | ✅ |
| **RTI_007_generate_and_ingest_OPCUA_Stream** | **On-demand OPC UA stream generator** (simulated telemetry → Custom Endpoint). Run via `Pipe_Stream`, **excluded** from the setup DAG. | ❌ |
| **RTI_008_build_realtime_dashboard** | Builds & deploys the Real-Time Dashboard over `OPCUAEvents`. | ✅ |
| **RTI_009_build_data_agent** | Builds & deploys the Data Agent over the ontology. | ✅ |
| **RTI_010_build_operations_agent** | Builds the Operations Agent + `Pipe_SendEmailAlert` pipeline for Teams/email alerts. | ✅ |
| **RTI_Orchestrator_Setup** | **Stage 2** driver: a `%%configure` first cell attaches the lakehouse (name received from NB01's exit value), then runs the setup DAG (NB02–06, 08–10) in **one Spark session** via `notebookutils.notebook.runMultiple`. | Stage 2 |

### Data Pipelines (`Orchestrator_Pipelines/`)

| Pipeline | Purpose |
|----------|---------|
| **`01_Pipe_Setup`** | **Two staged Notebook activities:** (1) `RTI_001_create_lakehouse_SelfContained` creates the lakehouse, seeds STID, and exits with its name; (2) on success, `RTI_Orchestrator_Setup` attaches that lakehouse via `%%configure` and runs the rest. **Supplies all environment values as pipeline parameters** (see next section). One-click "build the environment" entry point. |
| **`02_Pipe_Stream`** | Runs `RTI_007_generate_and_ingest_OPCUA_Stream` on demand to push a burst of live telemetry. Reads everything from `rti_demo_settings`. |
| **`Pipe_SendEmailAlert`** | Created by `RTI_010`; triggered by the Operations Agent to send Teams/email alerts. Not run directly by users. |

---

## ⚙️ `Pipe_Setup` pipeline parameters (must be keyed in)

`Pipe_Setup` runs **two staged Notebook activities** and holds all environment values as **pipeline-level parameters** (the pipeline's **Parameters** tab — *not* per-activity Base parameters). The notebook parameter cells ship **blank on purpose**, so **the pipeline is the single source of truth**.

> **Critical:** the **Name** must be the *full* parameter variable name (the UI truncates long names visually — the stored value must be complete, e.g. `key_vault_tenant_id_secret_name`, not `key_vault_tenant_id_s…`).

| # | Name (exact) | Type | Example value | Notes |
|---|---|---|---|---|
| 1 | `env_suffix` | String | `V5` | The one environment lever; drives every artifact name. |
| 2 | `workspace_id` | String | `19f3d588-1585-4f3b-bb59-5abaf90c193a` | GUID from behind `/groups/` in the Fabric URL. |
| 3 | `key_vault_uri` | String | `https://akvfabcapnew.vault.azure.net/` | SPN secrets live here (executing identity needs *get secret*). |
| 4 | `key_vault_tenant_id_secret_name` | String | `tenantid` | Secret **name**, not value. |
| 5 | `key_vault_client_id_secret_name` | String | `clientid` | Secret **name**, not value. |
| 6 | `key_vault_client_secret_name` | String | `clientsecret` | Secret **name**, not value. |
| 7 | `ops_agent_teams_team_id` | String | `c480320e-9204-474b-9b2c-54a53e94f220` | Teams team the Operations Agent posts to. |
| 8 | `ops_agent_teams_channel_id` | String | `19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2` | Teams channel for alerts. |
| 9 | `ops_agent_run_as_user` | String | `admin@mngenvmcap218279.onmicrosoft.com` | **Optional** — blank ⇒ the deploying user. |
| 10 | `per_notebook_timeout_secs` | Int | `3600` | Orchestrator-only DAG knob (per-child timeout). |

**How the values flow:**

```
Pipe_Setup (pipeline parameters)
        │
        ├─ Stage 1 ─►  RTI_001_create_lakehouse_SelfContained   (gets the 9 env params)
        │                     │  creates lakehouse, seeds STID, writes rti_demo_settings,
        │                     │  then  notebookutils.notebook.exit(lakehouse_name)
        │                     ▼
        │              exitValue = "Energy_IQ_LakehouseRTI_V5"
        │                     │
        └─ Stage 2 ─►  RTI_Orchestrator_Setup
              lakehouseName = @activity('RTI_001_create_lakehouse_SelfContained').output.result.exitValue
              per_notebook_timeout_secs = @pipeline().parameters.per_notebook_timeout_secs
                    │
                    │  %%configure  attaches that lakehouse to the session
                    ▼
              runs NB02–06, 08–10 in one Spark session (children inherit the default lakehouse)
                    │
                    └── NB02–10 read everything from rti_demo_settings
```

- **Stage 1** (`RTI_001`) receives the 9 environment params and is the **single source of truth** for the lakehouse name — it publishes that name via `notebookutils.notebook.exit(...)`.
- **Stage 2** (orchestrator) receives just two params: `lakehouseName` (piped from Stage 1's **exit value**, so there is no duplicated naming literal) and `per_notebook_timeout_secs`.
- `NB02`–`NB10` read **only** from `rti_demo_settings`.
- NB01 and the orchestrator **fail fast** with `Missing required parameter(s): …` if any required value is blank (`ops_agent_run_as_user` is exempt).

### 🔑 How the default lakehouse is attached (no manual step)

Earlier versions required you to manually pin a default lakehouse on the orchestrator. **That is no longer needed.** In `runMultiple` all children share the **root (orchestrator) session's** default lakehouse, so the orchestrator sets it programmatically:

- Its **first code cell** is a parameterized `%%configure` that sets `defaultLakehouse` to `lakehouseName`.
- `lakehouseName` arrives from **Stage 1's exit value**, i.e. the exact lakehouse NB01 just created.
- Each activity also carries `args: {"useRootDefaultLakehouse": True}`, so every child adopts that root default (ignoring any stale saved pin such as `…_V3`).

> Why two stages? `%%configure` must be the **first code cell** and the lakehouse must **already exist** when it runs. NB01 creates the lakehouse, so it must finish in an **earlier** session (Stage 1) before the orchestrator can attach it (Stage 2). Cost: the VNet cold start is paid twice (once per stage).

---

## Setup DAG (dependency graph)

`RTI_Orchestrator_Setup` (Stage 2) runs this DAG in a single Spark session. Independent branches run in parallel (`concurrency = 4`). NB01 already ran in Stage 1 and is **not** in this DAG.

```
NB02_eventhouse ─┬─► NB06_tsbind        (also depends on NB04 + NB05)
                 └─► NB08_dashboard

NB03_medallion ──► NB04_ontology ─┬─► NB05_entitybind ──► NB06_tsbind
                                  └─► NB09_dataagent ──► NB10_opsagent
```

- **NB07 is excluded** from setup — run it on demand from `Pipe_Stream`.

---

## `rti_demo_settings` — the handoff contract

`RTI_001` writes this Delta table (via an explicit OneLake ABFS path, so it does not depend on a default-lakehouse binding). Key columns:

- **Identity / placement:** `env_suffix`, `workspace_id`, `workspace_folder_path`, `lakehouse_name`, `lakehouse_id`, `target_folder_id`
- **Storage:** `bronze_root` (`Files/bronze`)
- **Key Vault (URI + secret NAMES only, never values):** `key_vault_uri`, `key_vault_tenant_id_secret`, `key_vault_client_id_secret`, `key_vault_client_secret_secret`
- **Artifact names:** `ontology_name`, `eventhouse_name`, `kql_database_name`, `eventstream_name`, `eventhouse_table_name`, `data_agent_name`, `dashboard_name`, `ops_agent_name` (+ `fabric_*` aliases)
- **Operations Agent:** `ops_agent_run_as_user`, `ops_agent_teams_team_id`, `ops_agent_teams_channel_id`, `ops_agent_copy_playbook`
- **Silver tables:** `silver_facilities_table`, `silver_systems_table`, `silver_equipment_table`, `silver_instruments_table`, `silver_signal_master_table`, `silver_table_prefix`
- **Data-model / time-series mapping:** `signal_master_entity_name`, `timeseries_timestamp_column` (`event_time`), `timeseries_key_column` (`opcua_node_id`), `timeseries_value_column` (`value`), `timeseries_quality_column` (`quality`)
- **Eventstream components & pipelines:** `eventstream_source_name` (`OPCUA_CustomEndpoint`), `eventstream_stream_name` (`OPCUA_DefaultStream`), `eventstream_destination_name` (`Eventhouse`), `alert_pipeline_name`, `alert_pipeline_description`, `setup_pipeline_name`, `stream_pipeline_name`
- Plus IDs added after creation: `lakehouse_id`, `cluster_query_uri`, `fabric_kql_db_id`, `dashboard_id`, etc.

> **No secrets are stored in the table** — only Key Vault URIs and secret *names*.

---

## Notebook details

### RTI_000 – Documentation & Overview
Context for the whole solution: dataset layout, medallion architecture, ontology, and RTI flow. Not executed by the orchestrator.

### RTI_001 – Foundation & settings (single source of truth)
Derives all names from `env_suffix`; resolves/creates the workspace folder and Lakehouse, **seeds the four STID CSVs into `Files/bronze/stid/`** via the explicit OneLake path (self-contained — no ADLS account, cloud connection, or shortcut); authenticates the SPN from Key Vault; and writes `rti_demo_settings`. The parameters cell blanks the **9 injected** values and keeps static data-model config; a guard in the derive cell fails fast if a required injected value is missing.

### RTI_002 – Streaming backbone + seed signal metadata
Creates Eventhouse `RTI_Demo_Eventhouse_{env_suffix}`, its KQL DB, and the slim `OPCUAEvents` table (`event_time`, `opcua_node_id`, `value`, `quality`); creates Eventstream `RTI_Demo_Eventstream_{env_suffix}` with a Custom Endpoint (`OPCUA_CustomEndpoint`) → Eventhouse destination; seeds `silver_instruments` / `silver_signal_master`.

### RTI_003 – Ingest & transform (Bronze → Silver → Gold)
Transforms all bronze domains into conformed silver tables (`silver_facilities`, `silver_systems`, `silver_equipment`, `silver_instruments`, `silver_signal_master`, plus SAP/OPC UA history/common-library/SOLV/P&ID/documents) and derived gold tables. `silver_signal_master` (`opcua_node_id` identity) bridges static metadata to telemetry.

### RTI_004 – Build ontology
Generates & deploys the ontology: entity types `facilities`, `systems`, `equipment`, `instruments`, `signal_master`, with the hierarchy `signal_master → instruments → equipment → systems → facilities`. Adds time-series **properties** (`event_time`, `value`, `quality`) on `signal_master`. Verifies 5 entity types + 4 relationship types.

### RTI_005 – Static Lakehouse DataBindings
Binds silver tables to entities (NonTimeSeries DataBindings) and creates relationship contextualizations. Telemetry binding is added in NB06.

### RTI_006 – Eventhouse TimeSeries DataBinding
Adds the TimeSeries DataBinding from `OPCUAEvents` to `signal_master`: `opcua_node_id` (join key), `event_time` (timestamp), `value`, `quality`. Live telemetry becomes semantically bound to the full asset hierarchy.

### RTI_007 – Generate & ingest live OPC UA stream (on-demand)
Simulates OPC UA telemetry for `is_active` signals from `silver_signal_master` and pushes JSON events (`event_time`, `opcua_node_id`, `value`, `quality`) to the Custom Endpoint. **Excluded from setup**; run via `Pipe_Stream`. Simulation knobs (`SIM_DURATION_SECS`, `MAX_ITERATIONS`, `SLEEP_BETWEEN_ITERATIONS_SEC`) are set inside the notebook.

### RTI_008 – Real-Time Dashboard
Deploys a Fabric Real-Time Dashboard over `OPCUAEvents`, parsing turbine/signal out of `opcua_node_id`. Persists `dashboard_name`/`dashboard_id` back to settings. A ready-to-import copy is checked in at `RTI_DEMO_V3/Dashboards/RTI_Demo_OPCUA_TelemetryStats.Dashboard.json`.

### RTI_009 – Data Agent
Builds & deploys `RTI_Demo_Agent_{env_suffix}` over the ontology as the semantic access layer for natural-language questions.

### RTI_010 – Operations Agent
Builds `RTI_Demo_OpsAgent_{env_suffix}`, wires Teams targets (`ops_agent_teams_team_id` / `_channel_id`) and the run-as user, and creates the `Pipe_SendEmailAlert` pipeline used to send alerts.

---

## Dataset Layout in OneLake

```
Files/bronze/                # inside the Lakehouse
├── stid/                    # Engineering master data (seeded by RTI_001)
├── sap/                     # Maintenance & work management
├── opcua/                   # Time-series telemetry (historical sample)
├── common_library/          # Standards & rules
├── solv/                    # Design & engineering limits
├── pid/                     # P&ID diagrams & parsed outputs
└── documents/               # Engineering documents & metadata
```

All raw data lands under **`Files/bronze`** in the Lakehouse; the STID seed files are written directly by RTI_001.

---

## Medallion Architecture

- **Bronze** – raw ingested data (STID seeded into the Lakehouse); as-received fidelity.
- **Silver** – cleaned/conformed tables with stable IDs; the binding layer for the ontology.
- **Gold** – derived operational signals (latest measurements, limit comparisons, health/aggregations).

---

## Fabric IQ Semantic Model

### Entity types
`facilities`, `systems`, `equipment`, `instruments`, `signal_master` (identity `opcua_node_id`, carries the time-series properties).

### Relationship path
`signal_master → instruments → equipment → systems → facilities`
(4 relationship types: instruments→equipment, equipment→systems, systems→facilities, signal_master→instruments).

### Bindings
- **Static** (NB05): silver tables → entity properties.
- **TimeSeries** (NB06): `OPCUAEvents` → `signal_master` (`event_time`/`value`/`quality`, joined on `opcua_node_id`).

---

## How to run

### One-click setup (recommended)
1. Fill the **`Pipe_Setup` pipeline parameters** (table above) on the pipeline's **Parameters** tab.
2. Run **`Pipe_Setup`** → **Stage 1** (NB01) creates the lakehouse and exits with its name; **Stage 2** (orchestrator) attaches it via `%%configure` and runs NB02–06, 08–10 in one Spark session.
3. When you want live data, run **`Pipe_Stream`** (NB07).

### Manual / notebook-by-notebook
Run `RTI_001` first (fill its parameter cell for a standalone run), then `002`–`006`, `008`–`010`; run `007` whenever you want a telemetry burst.

---

## Customization Guide

### Add or remove sensors
1. Modify the metadata source files that feed `silver_signal_master` (STID instruments).
2. Re-run **RTI_003** (regenerates `silver_signal_master`), then **RTI_004** (ontology), then **RTI_007** (simulator uses the new signals).

### Change generated telemetry values
Edit the simulator logic in **RTI_007** (value ranges, quality probabilities, drift/spikes/stuck sensors).

### Modify the signal schema / properties
Align all of: **RTI_004** (ontology `timeseriesProperties`) → **RTI_002** (`OPCUAEvents` schema) → **RTI_007** (event payload) → **RTI_006** (TimeSeries DataBinding).

### Stand up a new environment
Change **`env_suffix`** in the `Pipe_Setup` pipeline parameters (e.g. `V6`) and run `Pipe_Setup`. No manual lakehouse attach — Stage 1 creates the lakehouse and Stage 2 attaches it automatically.

---

## Notes & Limitations

- All data is **fully synthetic**; P&ID parsing is simulated via prepared CSVs; 3D data is metadata-only.
- Notebooks are idempotent where feasible; run in a clean demo folder for best reproducibility.
- Verify the `rti_demo_settings` table after RTI_001; monitor Eventhouse ingestion during RTI_007.
- The NB01 / orchestrator parameter cells must show the **"parameters"** badge in Fabric for injection to work — verify after a git sync. The orchestrator's `%%configure` must remain the **first code cell** (Fabric fails the pipeline run otherwise).
- Pipeline Notebook activities reference notebooks by **GUID**, so pipelines do not auto-repoint across `env_suffix` workspaces.

---

## Related Resources

- **RTI_000** – full dataset & pipeline documentation notebook.
- **`Raw/RTI_Notebooks/*.ipynb`** – readable mirrors of the Fabric notebooks (kept in sync with `Notebooks/…/notebook-content.py`).
- Fabric docs – Lakehouse, Eventhouse/KQL, Eventstream, Ontology (Fabric IQ), Data/Operations Agents.
