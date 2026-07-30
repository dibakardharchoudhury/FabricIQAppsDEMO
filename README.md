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
| Bronze shortcut | `Files/bronze` → ADLS | `Files/bronze` | ❌ |
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
| **RTI_001_create_lakehouse_shortcut** | Foundation: resolves workspace/Lakehouse, creates the ADLS shortcut, derives all names, and writes `rti_demo_settings` (**single source of truth**). | ✅ |
| **RTI_002_Setup_Eventhouse_Only** | Creates Eventhouse + KQL DB + `OPCUAEvents` table + Eventstream (Custom Endpoint → Eventhouse); seeds signal metadata into silver. | ✅ |
| **RTI_003_ingest_transform_medallion** | Bronze → Silver → Gold transforms; produces `silver_signal_master` and the structured silver/gold tables. | ✅ |
| **RTI_004_build_ontology_mapping_rti_structured** | Builds & deploys the ontology (5 entity types, 4 relationship types); adds time-series *properties* on `signal_master`. | ✅ |
| **RTI_005_entity_DataBinding_rti_structured** | Static Lakehouse DataBindings + relationship contextualizations. | ✅ |
| **RTI_006_TimeSeriesBinding_RTI_signal** | Adds the **Eventhouse TimeSeries DataBinding** from `OPCUAEvents` to `signal_master`. | ✅ |
| **RTI_007_generate_and_ingest_OPCUA_Stream** | **On-demand OPC UA stream generator** (simulated telemetry → Custom Endpoint). Run via `Pipe_Stream`, **excluded** from the setup DAG. | ❌ |
| **RTI_008_build_realtime_dashboard** | Builds & deploys the Real-Time Dashboard over `OPCUAEvents`. | ✅ |
| **RTI_009_build_data_agent** | Builds & deploys the Data Agent over the ontology. | ✅ |
| **RTI_010_build_operations_agent** | Builds the Operations Agent + `Pipe_SendEmailAlert` pipeline for Teams/email alerts. | ✅ |
| **RTI_Orchestrator_Setup** | Runs the setup DAG (NB01–06, 08–10) in **one Spark session** via `notebookutils.notebook.runMultiple`. Launched by `Pipe_Setup`. | (is the driver) |

### Data Pipelines (`Orchestrator_Pipelines/`)

| Pipeline | Purpose |
|----------|---------|
| **`01_Pipe_Setup`** | Single Notebook activity → runs `RTI_Orchestrator_Setup`. **Supplies all environment values as Base parameters** (see next section). This is the one-click "build the environment" entry point. |
| **`02_Pipe_Stream`** | Runs `RTI_007_generate_and_ingest_OPCUA_Stream` on demand to push a burst of live telemetry. Reads everything from `rti_demo_settings`. |
| **`Pipe_SendEmailAlert`** | Created by `RTI_010`; triggered by the Operations Agent to send Teams/email alerts. Not run directly by users. |

---

## ⚙️ `Pipe_Setup` Base parameters (must be keyed in)

`Pipe_Setup` runs a single Notebook activity that points at **`RTI_Orchestrator_Setup`**. The orchestrator's parameter cell ships **blank on purpose**, so **the pipeline is the single source of truth**. You must add the following rows under the Notebook activity's **Settings → Base parameters** (see the pipeline UI):

> **Critical:** the **Name** must be the *full* notebook parameter variable name (the UI truncates long names visually — the stored value must be complete, e.g. `key_vault_tenant_id_secret_name`, not `key_vault_tenant_id_s…`).

| # | Name (exact) | Type | Example value | Notes |
|---|---|---|---|---|
| 1 | `env_suffix` | String | `V5` | The one environment lever; drives every artifact name. |
| 2 | `workspace_id` | String | `19f3d588-1585-4f3b-bb59-5abaf90c193a` | GUID from behind `/groups/` in the Fabric URL. |
| 3 | `key_vault_uri` | String | `https://akvfabcapnew.vault.azure.net/` | SPN secrets live here (executing identity needs *get secret*). |
| 4 | `key_vault_tenant_id_secret_name` | String | `tenantid` | Secret **name**, not value. |
| 5 | `key_vault_client_id_secret_name` | String | `clientid` | Secret **name**, not value. |
| 6 | `key_vault_client_secret_name` | String | `clientsecret` | Secret **name**, not value. |
| 7 | `adls_account_url` | String | `https://didharchadlsg2.dfs.core.windows.net` | Seed dataset storage account. |
| 8 | `adls_subpath` | String | `/dataiq/bronze` | Root that contains `bronze/stid`, `bronze/sap`, … |
| 9 | `connection_name` | String | `ontologydidharch-connection` | Cloud connection feeding the bronze shortcut. |
| 10 | `ops_agent_teams_team_id` | String | `c480320e-9204-474b-9b2c-54a53e94f220` | Teams team the Operations Agent posts to. |
| 11 | `ops_agent_teams_channel_id` | String | `19:1-SLGOg6PFivKoyqZrKeH-PG-5JGjwATvoVAEyAr8jA1@thread.tacv2` | Teams channel for alerts. |
| 12 | `ops_agent_run_as_user` | String | `admin@mngenvmcap218279.onmicrosoft.com` | **Optional** — blank ⇒ the deploying user. |
| 13 | `per_notebook_timeout_secs` | Int | `3600` | Orchestrator-only DAG knob (per-child timeout). **Not** forwarded to NB01. |

**How the values flow:**

```
Pipe_Setup (Base parameters)
        │  (injected at runtime, right below the tagged parameters cell)
        ▼
RTI_Orchestrator_Setup   ── forwards 12 params (nb01_args) via runMultiple ──►  RTI_001
        │                                                                         │  writes
        │                                                                         ▼
        └───────── runs NB02–06, 08–10 in one Spark session ───────────►  rti_demo_settings
                                                                                  ▲
                                                             NB02–10 read everything here
```

- Only **NB01** receives parameters (`nb01_args`, the first 12 rows above). `per_notebook_timeout_secs` stays in the orchestrator (DAG timeout).
- `NB02`–`NB10` read **only** from `rti_demo_settings`.
- The orchestrator (and NB01) **fail fast** with `Missing required parameter(s): …` if any required value is blank (`ops_agent_run_as_user` is exempt).

### 🔑 Required: attach a default lakehouse to the orchestrator

`runMultiple` refuses to run child notebooks whose default lakehouse differs from the root's. The orchestrator therefore calls `runMultiple(..., {"useRootDefaultLakehouse": True})`, which makes **every child inherit the orchestrator's default lakehouse** (ignoring stale pins such as `…_V3`).

For this to work, the orchestrator must itself be attached to the workspace lakehouse:

1. Open **`RTI_Orchestrator_Setup`** in Fabric.
2. In the Lakehouse explorer, **add / set the default lakehouse** to `Energy_IQ_LakehouseRTI_{env_suffix}` (e.g. `Energy_IQ_LakehouseRTI_V5`).
3. Save. (The binding carries a workspace-specific GUID, so it is **not** committed to git — set it per workspace.)

> On a brand-new workspace the lakehouse must exist before the orchestrated run (day-0 bootstrap). `RTI_001` is idempotent and will resolve/ensure it; create the lakehouse once, attach the orchestrator to it, then run `Pipe_Setup`.

---

## Setup DAG (dependency graph)

`RTI_Orchestrator_Setup` runs this DAG in a single Spark session (VNet cold start paid once). Independent branches run in parallel (`concurrency = 4`).

```
NB01_lakehouse
 ├─► NB02_eventhouse ─┬─► NB06_tsbind        (also depends on NB04)
 │                    ├─► NB08_dashboard
 │                    └─(NB06 needs NB02 + NB04)
 └─► NB03_medallion ──► NB04_ontology ─┬─► NB05_entitybind
                                       ├─► NB06_tsbind
                                       └─► NB09_dataagent ──► NB10_opsagent
```

- **NB07 is excluded** from setup — run it on demand from `Pipe_Stream`.

---

## `rti_demo_settings` — the handoff contract

`RTI_001` writes this Delta table (via an explicit OneLake ABFS path, so it does not depend on a default-lakehouse binding). Key columns:

- **Identity / placement:** `env_suffix`, `workspace_id`, `workspace_folder_path`, `lakehouse_name`, `lakehouse_id`, `target_folder_id`
- **Storage & shortcut:** `adls_account_url`, `adls_subpath`, `shortcut_name`, `shortcut_parent_path`, `connection_name`
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
Derives all names from `env_suffix`; resolves/creates the workspace folder, Lakehouse, ADLS cloud connection, and the `Files/bronze` shortcut; authenticates the SPN from Key Vault; and writes `rti_demo_settings`. The parameters cell blanks the **12 injected** values and keeps static data-model config; a guard in the derive cell fails fast if a required injected value is missing.

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

## Dataset Layout in ADLS / OneLake

```
<adls_subpath>/              # e.g. /dataiq/bronze
├── stid/                    # Engineering master data
├── sap/                     # Maintenance & work management
├── opcua/                   # Time-series telemetry (historical sample)
├── common_library/          # Standards & rules
├── solv/                    # Design & engineering limits
├── pid/                     # P&ID diagrams & parsed outputs
└── documents/               # Engineering documents & metadata
```

All raw data lands under **`Files/bronze`** in the Lakehouse via the shortcut created in RTI_001.

---

## Medallion Architecture

- **Bronze** – raw ingested data (shortcut to ADLS); as-received fidelity.
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
1. Ensure the workspace lakehouse exists and **attach `RTI_Orchestrator_Setup` to it** (see "Required: attach a default lakehouse").
2. Fill the **`Pipe_Setup` Base parameters** (table above).
3. Run **`Pipe_Setup`** → orchestrator runs NB01–06, 08–10 in one Spark session.
4. When you want live data, run **`Pipe_Stream`** (NB07).

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
Change **`env_suffix`** in the `Pipe_Setup` Base parameters (e.g. `V6`); re-attach the orchestrator to the new lakehouse; run `Pipe_Setup`.

---

## Notes & Limitations

- All data is **fully synthetic**; P&ID parsing is simulated via prepared CSVs; 3D data is metadata-only.
- Notebooks are idempotent where feasible; run in a clean demo folder for best reproducibility.
- Verify the `rti_demo_settings` table after RTI_001; monitor Eventhouse ingestion during RTI_007.
- The `Pipe_Setup` / orchestrator parameter cells must show the **"parameters"** badge in Fabric for injection to work — verify after a git sync.
- Pipeline Notebook activities reference notebooks by **GUID**, so pipelines do not auto-repoint across `env_suffix` workspaces.

---

## Related Resources

- **RTI_000** – full dataset & pipeline documentation notebook.
- **`Raw/RTI_Notebooks/*.ipynb`** – readable mirrors of the Fabric notebooks (kept in sync with `Notebooks/…/notebook-content.py`).
- Fabric docs – Lakehouse, Eventhouse/KQL, Eventstream, Ontology (Fabric IQ), Data/Operations Agents.
