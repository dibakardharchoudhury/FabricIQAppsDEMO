# Fabric IQ RTI Demo — Synthetic Energy Dataset, Ontology v2 & Real‑Time Intelligence

An end‑to‑end **Microsoft Fabric** solution built on a **fully synthetic** hydropower dataset. One
**Data Pipeline** stands up the whole environment: a medallion Lakehouse, an Eventhouse telemetry
stream, a Fabric IQ Ontology v2 with live time-series bindings, and a Real-Time Dashboard.
Optional agent steps report supported, blocked, or skipped capabilities separately; they do not
guarantee a Data Agent or Operations Agent exists. A companion React app
([`HydroOperationsApp/`](HydroOperationsApp/README.md)) composes the data on one screen.

> [!NOTE]
> All data is synthetic — no real plant or customer data.
> The project is v2-only. Agent availability depends on Fabric product support; a successful
> core setup does not imply that optional agents are ready.

## Ontology generations and optional agents

The canonical `RTI_004`, `RTI_005`, and `RTI_006` notebook names remain unchanged, including their
readable mirrors in [`Raw/RTI_Notebooks/`](Raw/RTI_Notebooks/). This project supports **Ontology v2
only**. Authoring, binding, application discovery, and agent configuration require the live item's
`properties.generation` to be `2`. Existing generation-1 items are rejected with a replacement-required
error, not reused or updated through a legacy path. Use a fresh workspace or an unused environment
suffix to provision v2 without destroying an existing deployment. This is not an in-place migration.
Fabric REST URLs still use `/v1`; the REST API version is not the ontology generation.

Generation 2 uses TMDL entity definitions, Direct Lake backing tables, physical relationships,
and Eventhouse time-series backing configurations. Definition updates preserve unrelated live
parts and existing bindings; incompatible changes must be resolved explicitly rather than silently
rebuilding a populated ontology. The application reads generation-2 TMDL contracts only.
Graph materialization is optional in generation 2, so missing graph data must not
be confused with an empty, successfully queried ontology.

| V2 definition part | Purpose |
| --- | --- |
| `database.tmdl` | Root with `compatibilityLevel: 1000000`. |
| `model.tmdl`, `namespaces/default.tmdl` | Model and required default namespace; fresh creation submits both alongside the database root. |
| `entities/*.tmdl` | Entity keys, scalar/time-series properties, and backing configurations. |
| `tables/*.tmdl` | Lakehouse Direct Lake or Eventhouse backing-table/source metadata. |
| `entityRelationships.tmdl` | Semantic entity relationships. |
| `relationships.tmdl` | Physical table joins referenced by semantic relationships or time-series backing; not additional semantic edges. |

Two optional `01_Pipe_Setup` parameters are persisted in `rti_demo_settings`:

| Parameter | Default | Policy |
| --- | --- | --- |
| `ontology_data_agent_mode` | `auto` | Report v2 ontology-source integration as blocked until product support is verified; never fall back to a v1 source. |
| `ontology_operations_agent_mode` | `auto` | Report v2 playbook integration as blocked; never create a legacy playbook. |

Both accept `auto`, `enabled`, or `disabled`. `enabled` is an explicit opt-in, **not** a workaround
for a product defect or proof of agent readiness. Data Agent enablement attempts the v2 ontology
source and propagates service failures. Operations Agent enablement fails before writes until a
verified v2 playbook automation contract is available. `disabled` avoids configuring that agent. Agent notebooks
report capability status and reason separately from core setup completion, and
`RTI_011_seed_sql_wire_graphql_agent` can seed SQL and provision GraphQL without a Data Agent.

As of September 29, 2026, generation-2 Data Agent onboarding is blocked in the reported rollout,
and [Fabric known issue 1970](https://support.fabric.microsoft.com/known-issues/) covers Operations
Agent playbook-generation timeouts with the new ontology experience. Check product support in the
target tenant before opting in; do not infer support from an estimated fix date.

`RTI_001` remaps downstream notebook attachments, and the orchestrator passes
`useRootDefaultLakehouse: True`. Rebinding failures now stop setup. Standalone notebook sessions
must be restarted after attachment changes. Importing separate `_gen2`-suffixed notebooks does not
wire them into the canonical rebinding list or setup DAG.

For validation, run the ontology regression tests under [`Raw/workspace-reset/`](Raw/workspace-reset/)
and the app's `test:knowledge-graph` suite. A live acceptance check should use a separately named
test ontology in a DEV workspace, verify fresh authoring and a bound rerun, and remove only the
test artifacts. Never test preservation by rebuilding a user's existing ontology.

Shared authoring helpers are maintained in
[`ontology_notebook_support.py`](Raw/workspace-reset/ontology_notebook_support.py).
After changing them, run `python Raw\workspace-reset\sync_ontology_notebooks.py --sync`
from the repository root to refresh only the canonical 004/005/006 embedded copies and Raw mirrors.
Run the same command with `--check` to verify distribution without writing files.

## One lever: `env_suffix`

Every versioned artifact name derives from a single parameter (e.g. `V6`), so you can run parallel
environments in one workspace. `RTI_001` is the **single source of truth**: it derives all names,
writes the shared **`rti_demo_settings`** Delta table, and every other notebook reads from it.

| Artifact | Name pattern (example for `V6`) |
|---|---|
| Lakehouse | `Energy_IQ_LakehouseRTI_V6` |
| Ontology | `RTI_Demo_Ontology_V6` |
| Eventhouse / KQL DB | `RTI_Demo_Eventhouse_V6` (table `OPCUAEvents`) |
| Eventstream | `RTI_Demo_Eventstream_V6` |
| Data Agent (when supported and explicitly enabled) | `RTI_Demo_Agent_V6` |
| Dashboard | `RTI_Demo_OPCUA_TelemetryStats_V6` |
| Operations Agent (reserved name; automation blocked) | `RTI_Demo_OpsAgent_V6` |

`V6`, `V9`, and other environment suffixes distinguish demo instances; they do not select an
ontology generation. Every supported ontology is generation 2.
The setup/stream/weather pipelines are **not** versioned. `Pipe_SendEmailAlert` belongs to the
previous Operations Agent flow; the current v2-only setup does not create or wire it.

## Notebooks

| Notebook | Role | In setup DAG |
|---|---|:---:|
| **RTI_001_create_lakehouse_SelfContained** | Foundation: creates the Lakehouse, seeds STID master data into `Files/bronze/stid/`, derives names, writes `rti_demo_settings`, exits the lakehouse name. | Stage 1 |
| **RTI_002_Setup_Eventhouse_Only** | Eventhouse + KQL DB + `OPCUAEvents` + Eventstream (custom endpoint → Eventhouse). | ✅ |
| **RTI_003_ingest_transform_medallion_SelfContained** | Bronze → Silver → Gold transforms; builds `silver_signal_master`. | ✅ |
| **RTI_004_build_ontology_mapping_rti_structured** | Creates/verifies Ontology v2 TMDL (5 entities, 4 semantic relationships) + time‑series properties; preserves existing bindings on safe reruns. | ✅ |
| **RTI_005_entity_DataBinding_rti_structured** | Direct Lake backing tables, scalar-property bindings, and physical joins referenced by semantic relationships. | ✅ |
| **RTI_006_TimeSeriesBinding_RTI_signal** | Binds `OPCUAEvents` telemetry to `signal_master`. | ✅ |
| **RTI_007_generate_and_ingest_OPCUA_Stream** | On‑demand OPC UA telemetry generator (run via `Pipe_Stream`). | — |
| **RTI_008_build_realtime_dashboard** | Two‑page Real‑Time Dashboard over `OPCUAEvents`: *Hydro Telemetry* (Station/Turbine filters, one chart per sensor group) + *OPC UA Telemetry*. Deploys from a definition file; shortcuts the silver tables into the Eventhouse so filters come from data. | ✅ |
| **RTI_009_build_data_agent** | Reports the v2 Data Agent capability; explicit enablement attempts deployment with matching live v2 source and draft/published readback checks. Default `auto` is blocked. | ✅ |
| **RTI_010_build_operations_agent** | Reports the blocked/skipped v2 playbook capability. No agent, playbook, or alert pipeline is provisioned; explicit enablement fails before writes. | ✅ |
| **RTI_011_seed_sql_wire_graphql_agent** | On-demand SQL seeding and STID GraphQL setup, independent of agents. Extends SQL only on an eligible, verified v2-backed Data Agent. Run by **Seed & provision**. | — |
| **RTI_Orchestrator_Setup** | Stage 2 driver: attaches the Lakehouse via `%%configure`, runs NB02–06, 08–10 and Weather_001, then enables Weather ingestion after all activities succeed. | Stage 2 |

> [!NOTE]
> `RTI_000` is documentation only. `*_shortcut` / non‑self‑contained variants are legacy reference copies, not wired into `Pipe_Setup`. Readable `.ipynb` mirrors live in [`Raw/RTI_Notebooks/`](Raw/RTI_Notebooks/).

> [!TIP]
> Changing, adding or embedding a Real‑Time Dashboard? See [`docs/dev-dashboards.md`](docs/dev-dashboards.md).
> `RTI_008` and its `.ipynb` mirror are **generated** — edit `Raw/RTI_Notebooks/tools/build_rti_008.py`
> or the definition JSON, then re‑run the generator.
>
> Building or extending the application Knowledge Graph? See
> [`docs/knowledge-graph.md`](docs/knowledge-graph.md) for its Ontology relationship, source
> federation, operational scenarios, health semantics, and RDF/OWL path.

## Prerequisites (one‑time)

The demo is otherwise self‑contained — **no ADLS, shortcut, or cloud connection.** You only grant the
executing **Service Principal (SPN)** access and flip a couple of tenant switches:

| # | Grant / setting | What it needs |
|:--:|---|---|
| 1 | **Key Vault secrets** | SPN has **Key Vault Secrets User** — *Get* on `tenantid`, `clientid`, `clientsecret`. |
| 2 | **Workspace access** | SPN has **Contributor** (or higher) on the Fabric workspace. |
| 3 | **Tenant settings** *(Admin portal)* | **Service principals can use Fabric APIs** (SPN in the allowed security group); Copilot / AI settings are additional prerequisites for supported optional agent use, not a workaround for v2 service limitations. |
| 4 | **Private endpoint to Key Vault** | Only if the vault blocks public access — add a managed private endpoint in *Workspace settings → Networking* and approve it on the vault. |
| 5 | **Tenant settings for the companion app** *(Admin portal)* | **Enable Fabric App Items (preview)** — without it `rayfin up` gets `403 FeatureNotAvailable`. Add **Users can sync workspace items with GitHub repositories** if you populate the workspace via Git; the generic Git switch alone is not enough. |
| 6 | **Capacity region** | *Fabric App (preview)* is unavailable in some regions (West US 3, East US 2, UK South, North Europe, …) and a capacity's region is fixed at creation. Sweden Central covers Fabric App, Ontology, Digital twin builder and the Operations agent. See [region availability](https://learn.microsoft.com/fabric/admin/region-availability). |

> [!IMPORTANT]
> **Optional future/manual email-alert integration (OAuth2).**
>
> This is **not a prerequisite for the current v2 core setup**. `RTI_010` does not create an
> Operations Agent or `Pipe_SendEmailAlert`. The following connection guidance applies only if
> you separately implement and verify a supported v2 alert flow.
>
> The `Pipe_SendEmailAlert` pipeline sends mail via the **Office 365 Outlook “Send an email”** activity,
> which posts **from a mailbox** and so needs an **OAuth2** connection. It **cannot** be created from a
> notebook (the Fabric Create Connection API has no OAuth2 credential type — only interactive portal
> sign‑in makes one), and a **Service Principal** connection tests as *Online* but fails at runtime with
> *“Failed to load the connection.”*
>
> **Create it once, in the portal:** *Settings → Manage connections and gateways → Connections →
> **+ New** → type **Office 365 Outlook** → auth **OAuth 2.0** → **Sign in** with a mailbox‑enabled
> work/school account (prefer a **shared/service mailbox** for durability) → name it
> **`RTI_Office365_EmailAlert`** → **Create**.*
>
> An Outlook connection alone does not provision or validate an Operations Agent or playbook.
> A separately configured alert pipeline must explicitly use that connection. If a connection
> later shows *“Failed to load”*, open it and **Edit → Sign in** to refresh expired consent.

> [!NOTE]
> The **Hydro Operations web app** signs users in with a **second, separate identity** — the
> single-tenant SPA app registration **`Hydro Operations Fabric Client`** (no secret), distinct
> from this notebook SPN. The local deployer attempts to create and configure it, but ordinary
> users in locked-down tenants commonly cannot create app registrations, add SPA redirect URIs
> or delegated permissions, or grant tenant-wide admin consent. The name is a configurable
> discovery convention (`HYDRO_SPA_DISPLAY_NAME`); the tenant-specific client ID is dynamic.
> If no usable SPA can be resolved, deployment stops before changing Rayfin state. An **Application Administrator /
> Cloud Application Administrator** must create/configure the app and grant admin consent
> (Global Administrator is not required). Consent itself is optional where the tenant allows user
> consent, because every scope the app requests is user-consentable. Use the copy-pasteable handoff
> in [`HydroOperationsApp/DEPLOY.md` → No admin rights?](HydroOperationsApp/DEPLOY.md#no-admin-rights-hand-this-to-your-entra-admin).

## Deploy

1. Open **`01_Pipe_Setup`** and fill its **pipeline parameters**:

   | Parameter | Example | Notes |
   |---|---|---|
   | `env_suffix` | `V6` | The one environment lever. |
   | `workspace_id` | `19f3d588-…` | GUID after `/groups/` in the Fabric URL. |
   | `key_vault_uri` | `https://myvault.vault.azure.net/` | |
   | `key_vault_tenant_id_secret_name` | `tenantid` | Secret **name**, not value. |
   | `key_vault_client_id_secret_name` | `clientid` | Secret **name**, not value. |
   | `key_vault_client_secret_name` | `clientsecret` | Secret **name**, not value. |
   | `ontology_data_agent_mode` | `auto` | `auto` reports the v2 rollout block; `enabled` attempts verified v2 configuration; `disabled` skips. |
   | `ontology_operations_agent_mode` | `auto` | `auto` reports blocked; `disabled` skips; `enabled` fails before writes until a verified v2 playbook contract exists. |
   | `ops_agent_teams_team_id` | `c480320e-…` | Retained configuration; not used by the blocked v2 Operations Agent flow. |
   | `ops_agent_teams_channel_id` | `19:…@thread.tacv2` | Retained configuration; does not enable Teams alerts. |
   | `ops_agent_run_as_user` | `admin@…onmicrosoft.com` | Retained optional setting; does not start or configure an agent. |
   | `per_notebook_timeout_secs` | `3600` | Per‑child DAG timeout. |

   > [!IMPORTANT]
   > The pipeline ships with the author's **example defaults** — replace **every** value for a new tenant. Enter each **full** name (the UI truncates long names visually); the child notebooks' own parameter cells ship blank and fail fast if a required value is missing.

2. **Run `Pipe_Setup`.** Stage 1 (`RTI_001`) creates the Lakehouse and exits its name; Stage 2 (orchestrator) attaches it and runs the rest — no manual lakehouse pinning. Use a fresh workspace or unused suffix if the target ontology is v1. Review optional capability statuses separately; core completion is not agent readiness.
3. **Run `Pipe_Stream`** whenever you want a burst of live telemetry.
4. **`03_Pipe_Weather` runs automatically every six hours** (03:20/09:20/15:20/21:20 UTC, aligned to the 00/06/12/18 UTC model runs both vendors derive from). It refreshes Aurora, refreshes UKMet, then runs `Weather_020_area_calculations` to rebuild vendor- and forecast-type-specific area metrics, enforce retention, and refresh the wide serving tables the app reads. Workspace provisioning creates and enables the schedule.

## How it fits together

```
Bronze (STID seed, SAP, OPC UA, P&ID, docs)
  → Silver (conformed: facilities, systems, equipment, instruments, signal_master)
  → Gold (latest readings, limit checks, health)

Ontology:  signal_master → instruments → equipment → systems → facilities
Bindings:  static silver tables (NB05) + Eventhouse OPCUAEvents → signal_master (NB06)
```

The medallion is **data‑driven off the STID CSVs** in [`Raw/stid_rti_fixed_source_files/`](Raw/stid_rti_fixed_source_files/)
(3 facilities / 15 turbines / 90 instruments), so scaling the dataset needs no notebook changes — just re‑run `Pipe_Setup`.

## Customization

- **Sensors/signals:** edit the STID source CSVs, re-run `Pipe_Setup` (rebuilds silver and safely reconciles the v2 definition), then `Pipe_Stream`. Incompatible key/type/source changes fail explicitly instead of replacing populated bindings.
- **Telemetry values:** edit the simulator in `RTI_007` (ranges, quality, drift/spikes).
- **Signal schema:** keep `RTI_004` (ontology properties) ↔ `RTI_002` (`OPCUAEvents`) ↔ `RTI_007` (payload) ↔ `RTI_006` (binding) aligned.
- **Dashboards:** edit in the Fabric UI, download the JSON over `Raw/RTI_Notebooks/dashboards/`, re‑run the generator — [`docs/dev-dashboards.md`](docs/dev-dashboards.md).
- **New environment:** change `env_suffix` and re‑run `Pipe_Setup`.

## Notes

- All data is synthetic (P&ID parsing and 3D data are simulated/metadata‑only). **Where it lives:** STID master data + medallion silver/gold tables in the **Lakehouse** (`Energy_IQ_LakehouseRTI_V6`); live OPC UA telemetry in the **Eventhouse** KQL DB (`OPCUAEvents`); the app's operational records — work orders, maintenance notifications, inspections, spare parts, 3D‑model metadata — in the **Fabric SQL Database** (seeded by `RTI_011`).
- Pipeline notebook activities reference notebooks by **GUID** — pipelines don't auto‑repoint across workspaces.

## Companion app

[`HydroOperationsApp/`](HydroOperationsApp/README.md) — a React + Rayfin app that joins STID (Lakehouse
GraphQL), telemetry (Eventhouse KQL), and operational records (Rayfin SQL) on one screen. Its
Knowledge Graph provides selected-asset, facility, and all-entity views over that federated context;
the live v2 TMDL contract governs supported relationships projected onto Lakehouse GraphQL rows,
Eventhouse supplies fresh readings, and SQL contributes explicit external overlays. Native v2
GraphModel association is unverified and explicitly unavailable; this view is not a native GQL
result. See
[`docs/knowledge-graph.md`](docs/knowledge-graph.md). Deploy steps:
[`HydroOperationsApp/DEPLOY.md`](HydroOperationsApp/DEPLOY.md).
