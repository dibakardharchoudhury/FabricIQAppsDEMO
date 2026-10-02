# Fabric IQ RTI Demo — Synthetic Energy Dataset, Ontology v2 & Real‑Time Intelligence

An end‑to‑end **Microsoft Fabric** solution built on a **fully synthetic** hydropower dataset. One
**Data Pipeline** stands up the whole environment: a medallion Lakehouse, an Eventhouse telemetry
stream, a Fabric IQ Ontology v2 with live time-series bindings, and a Real-Time Dashboard.
Data Agent and Operations Agent provisioning are enabled by default, retaining the original
playbook, actions, Teams, and email-alert capabilities while adopting Ontology v2. Service failures
remain failures, not successful static capability skips. A companion React app
([`HydroOperationsApp/`](HydroOperationsApp/README.md)) composes the data on one screen.

> [!NOTE]
> All data is synthetic — no real plant or customer data.
> The project is v2-only. Agent availability depends on Fabric product support; a successful
> core-only setup or item creation does not certify agent execution or alert delivery.

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
Graph materialization is optional in the Fabric generation-2 experience but required for the app's
native graph canvas, tree, and scopes. In the selected ontology choose **Manage graph → select eligible
entities/relationships → Continue → Materialize**. The canonical deployment orchestrator follows
Fabric's authoritative Ontology-to-GraphIndex item lineage and generates the explicit
`RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` JSON (`workspaceId`, `ontologyId`, `graphModelId`).
An operator may supply the same verified binding as a fallback when lineage is unavailable.
Ownership is never inferred from names, a sole graph, or sampled structure.
No published ontology-owned materialization REST endpoint is established; this remains a manual
portal prerequisite. Missing graph data is an error, not an empty successful query or a reason to
fabricate STID/FK topology. See [the native graph contract](docs/knowledge-graph.md).

| V2 definition part | Purpose |
| --- | --- |
| `database.tmdl` | Root with `compatibilityLevel: 1000000`. |
| `model.tmdl`, `namespaces/default.tmdl` | Model and required default namespace; fresh creation submits both alongside the database root. |
| `entities/*.tmdl` | Entity keys, scalar/time-series properties, and backing configurations. |
| `tables/*.tmdl` | Lakehouse Direct Lake or Eventhouse backing-table/source metadata. |
| `entityRelationships.tmdl` | Semantic entity relationships. |
| `relationships.tmdl` | Physical table joins referenced by semantic relationships or time-series backing; not additional semantic edges. |

Two agent-policy `01_Pipe_Setup` parameters are persisted in `rti_demo_settings`:

| Parameter | Default | Policy |
| --- | --- | --- |
| `ontology_data_agent_mode` | `enabled` | Verify v2 draft/published source identity and require the real facility-record MCP smoke check; failed or inconclusive checks fail required execution. |
| `ontology_operations_agent_mode` | `enabled` | Attempt Operations Agent, playbook, actions, Teams configuration, and email-alert pipeline provisioning against the verified v2 ontology. |

Both accept `auto`, `enabled`, or `disabled`. `auto` is a backward-compatible alias for `enabled`:
both attempt the actual capability, rather than returning a static blocked status. `disabled` is
the explicit opt-out for that agent. No mode bypasses generation-2 verification or falls back to a
generation-1 ontology. Required-agent failures propagate through RTI_009, RTI_010, RTI_011, and setup;
status/reason reporting is diagnostic, not a substitute for failure. RTI_011 can seed SQL and
provision GraphQL with the Data Agent explicitly disabled; otherwise its agent extension is required.
Successful configuration still does not prove agent execution or alert delivery.

RTI_009 tracks publication separately from functional validation. Required success is
`data_agent_deployment_status=ready`, `data_agent_publication_status=published`, and
`data_agent_runtime_status=verified`, with live generation `2`. The setup orchestrator requires
all three values, plus `ops_agent_deployment_status=configured` after RTI_010, or `skipped` paired
with the respective explicit `disabled` mode. Publication-only `published` is not readiness.

The Data Agent smoke check asks only for the selected ontology's first five facility IDs/names and
compares the exact answer with independently read configured Lakehouse rows. Expected values are
not supplied in the prompt; attached SQL sources are allowed and no cached proof is required.
Evidence uses `verification=ontology_facilities_smoke_v1`, scoped to **"Source-specific functional
smoke test; execution provenance is not attested."** It does not certify cryptographic provenance,
SQL/combined-source answers, or the entire agent. Semantic/transport errors and wrong rows fail;
nonparseable, count-only, or unverifiable answers are inconclusive. Both fail required execution.

RTI_011 attaches and publishes the SQL source independently of the ontology source or NB09's
runtime result. It verifies the SQL source through draft/published readback, preserves existing
sources and custom settings, and records `data_agent_sql_source_status=published` without
overwriting NB09's ontology status. Actual SQL-source failures still fail NB11. SQL/custom parts,
custom instructions, and entity selections also survive runtime-failing NB09 reruns.

The Operations Agent is configured in a **stopped** state; `configured` does not mean a playbook has
run. The retained `OperationsAgentV1` business-configuration schema is not ontology generation 1.
The latest live configuration required only restoring the action enum from `DataPipeline` to the
original `Pipeline`; full playbook/actions/Teams/email configuration was retained, not stripped.

The September 29 report described generation-2 onboarding limitations and
[Fabric known issue 1970](https://support.fabric.microsoft.com/known-issues/) covers Operations Agent
playbook-generation timeouts. Later September 30 attempts published the sources and configured the
stopped Operations Agent, but **healthy ontology runtime remains blocked**. Do not infer product
resolution from publication, an estimated fix date, or the restored implementation.

**Live failure-path validation is complete:** revised NB09 job
`b1818634-f77e-4dec-ab55-982460ecb101` correctly failed on the unsupported Ontology API semantic error
despite HTTP 200 / `isError: false`. Delta readback verified deployment/runtime `failed`,
publication and SQL-source `published`, and enabled mode. Draft and published sources retained all
five SQL tables, all five ontology elements, and custom content; a subsequent SQL-only MCP query
still returned exactly `{"workOrderCount":12}`. Only NB09 ran once after bounded NB09/NB11/setup
definition updates; **full setup and NB11 were not rerun after the new gate**. This validates
truthful failure and preservation, not a successful ontology smoke test.
See [current and historical live evidence](docs/knowledge-graph.md#latest-v3-acceptance-september-30-2026).
Full five-entity graph projection still fails, and Operations Agent activation/delivery and
interactive application acceptance remain unverified.

`RTI_001` remaps downstream notebook attachments, and the orchestrator passes
`useRootDefaultLakehouse: True`. Rebinding failures now stop setup. Standalone notebook sessions
must be restarted after attachment changes. Importing separate `_gen2`-suffixed notebooks does not
wire them into the canonical rebinding list or setup DAG.

For validation, run the ontology regression tests under [`Raw/workspace-reset/`](Raw/workspace-reset/)
and the app's `test:knowledge-graph` suite. A live acceptance check should use a separately named
test ontology in the currently authorized **ws-vteam-demoV3** workspace (tenant
`ad340c84-1886-4202-a483-2da2cb9168eb`), verify fresh authoring and a bound rerun, and remove only the
test artifacts. Never test preservation by rebuilding a user's existing ontology.

Shared authoring helpers are maintained in
[`ontology_notebook_support.py`](Raw/workspace-reset/ontology_notebook_support.py).
After changing them, run `python Raw\workspace-reset\sync_ontology_notebooks.py --sync`
from the repository root to refresh only the canonical 004/005/006 embedded copies and Raw mirrors.

Keep notebook prose inside a `CELL` or `MARKDOWN` section. Fabric `METADATA` sections must
contain only `# META`-prefixed JSON and blank lines until the next section marker; ordinary
Python comments there cause Git sync `PyToIPynbFailure`, even when Python compilation passes.
The ontology setup contract tests validate this boundary across all canonical notebooks.
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
| Data Agent (enabled by default) | `RTI_Demo_Agent_V6` |
| Dashboard | `RTI_Demo_OPCUA_TelemetryStats_V6` |
| Operations Agent (enabled by default) | `RTI_Demo_OpsAgent_V6` |

`V6`, `V9`, and other environment suffixes distinguish demo instances; they do not select an
ontology generation. Every supported ontology is generation 2.
The setup/stream/weather pipelines are **not** versioned. RTI_010 retains creation and wiring of
`Pipe_SendEmailAlert` as part of Operations Agent provisioning.

## Notebooks

| Notebook | Role | In setup DAG |
|---|---|:---:|
| **RTI_001_create_lakehouse_SelfContained** | Foundation: creates the Lakehouse, seeds STID master data into `Files/bronze/stid/`, derives names, writes `rti_demo_settings`, exits the lakehouse name. | Stage 1 |
| **RTI_002_Setup_Eventhouse_Only** | Eventhouse + KQL DB + `OPCUAEvents` + Eventstream (custom endpoint → Eventhouse). | ✅ |
| **RTI_003_ingest_transform_medallion_SelfContained** | Bronze → Silver → Gold transforms; builds `silver_signal_master`. | ✅ |
| **RTI_004_build_ontology_mapping_rti_structured** | Creates/verifies Ontology v2 TMDL (5 entities, 4 semantic relationships) + time‑series properties; preserves existing bindings on safe reruns. | ✅ |
| **RTI_005_entity_DataBinding_rti_structured** | Direct Lake backing tables, scalar-property bindings, and physical joins referenced by semantic relationships. | ✅ |
| **RTI_006_TimeSeriesBinding_RTI_signal** | Binds `OPCUAEvents` telemetry to `signal_master`, including the standalone `event_time` property. | ✅ |
| **RTI_007_generate_and_ingest_OPCUA_Stream** | On‑demand OPC UA telemetry generator (run via `Pipe_Stream`). | — |
| **RTI_008_build_realtime_dashboard** | Two‑page Real‑Time Dashboard over `OPCUAEvents`: *Hydro Telemetry* (Station/Turbine filters, one chart per sensor group) + *OPC UA Telemetry*. Deploys from a definition file; shortcuts the silver tables into the Eventhouse so filters come from data. | ✅ |
| **RTI_009_build_data_agent** | Provisions the Data Agent with matching live v2 source/readback, then requires bounded real MCP facility-record validation; failed or inconclusive results fail required execution. | ✅ |
| **RTI_010_build_operations_agent** | Provisions the Operations Agent, playbook, actions, Teams configuration, and email-alert pipeline against v2 by default; required failures propagate. | ✅ |
| **RTI_011_seed_sql_wire_graphql_agent** | On-demand SQL seeding and STID GraphQL setup; extends the required verified v2-backed Data Agent with SQL unless explicitly disabled. Run by **Seed & provision**. | — |
| **RTI_Orchestrator_Setup** | Stage 2 driver: attaches the Lakehouse via `%%configure`, runs NB02–06, 08–10 and Weather_001, then enables Weather ingestion after all activities succeed. | Stage 2 |

NB09 and NB10 report separately gated agent capabilities but both persist status in the same
`rti_demo_settings` Delta table. Their required execution order is **NB06 → NB09 → NB10**:
serialize the two status `MERGE` operations to avoid `DELTA_CONCURRENT_APPEND`. Other independent
setup branches remain parallel. Agent failures in `enabled` or `auto` mode fail setup; only an
explicit `disabled` mode permits a capability skip. Status-write failures also fail setup.

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
| 3 | **Tenant settings** *(Admin portal)* | **Service principals can use Fabric APIs** (SPN in the allowed security group); Copilot / AI settings are additional prerequisites for the default-enabled agents, not a workaround for v2 service limitations. |
| 4 | **Private endpoint to Key Vault** | Only if the vault blocks public access — add a managed private endpoint in *Workspace settings → Networking* and approve it on the vault. |
| 5 | **Tenant settings for the companion app** *(Admin portal)* | **Enable Fabric App Items (preview)** — without it `rayfin up` gets `403 FeatureNotAvailable`. Add **Users can sync workspace items with GitHub repositories** if you populate the workspace via Git; the generic Git switch alone is not enough. |
| 6 | **Capacity region** | *Fabric App (preview)* is unavailable in some regions (West US 3, East US 2, UK South, North Europe, …) and a capacity's region is fixed at creation. Sweden Central covers Fabric App, Ontology, Digital twin builder and the Operations agent. See [region availability](https://learn.microsoft.com/fabric/admin/region-availability). |

> [!IMPORTANT]
> **Email-alert delivery prerequisite (OAuth2).**
>
> RTI_010 retains Operations Agent and `Pipe_SendEmailAlert` provisioning. Email delivery requires
> the mailbox connection below; core-only setup with Operations Agent explicitly disabled does not
> validate this capability.
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
> RTI_010 wires the alert pipeline to that connection. If a connection
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
   | `ontology_data_agent_mode` | `enabled` | Default; `auto` also attempts verified v2 configuration; only `disabled` skips. |
   | `ontology_operations_agent_mode` | `enabled` | Default; `auto` also attempts agent/playbook/actions/alert provisioning; only `disabled` skips. |
   | `ops_agent_teams_team_id` | `c480320e-…` | Target team for Operations Agent Teams delivery. |
   | `ops_agent_teams_channel_id` | `19:…@thread.tacv2` | Target channel for Operations Agent Teams delivery. |
   | `ops_agent_run_as_user` | `admin@…onmicrosoft.com` | Optional guard for the delegated run-as identity; blank uses the deploying user. |
   | `alert_email_to` | `operations@contoso.com` | `To` recipient for `Pipe_SendEmailAlert`. Required by the local launcher; RTI_010 falls back to run-as/deploying user when blank. |
   | `per_notebook_timeout_secs` | `3600` | Per‑child DAG timeout. |

   > [!IMPORTANT]
   > Tenant-specific Operations Agent values ship blank. Enter the Team ID, channel ID, and alert email recipient for each environment; run-as may remain blank to use the deploying user. Enter each **full** name (the UI truncates long names visually); the child notebooks fail fast if a required value is missing.

2. **Run `Pipe_Setup`.** Stage 1 (`RTI_001`) creates the Lakehouse and exits its name; Stage 2 (orchestrator) attaches it and runs the rest — no manual lakehouse pinning. Use a fresh workspace or unused suffix if the target ontology is v1. Data Agent and Operations Agent provisioning are independent branches after NB06, so one product failure does not prevent the other attempt; any enabled agent that fails its contract still fails setup. Inspect status/reasons and verify execution/delivery separately from configuration.
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
Knowledge Graph retains progressive loading: available Lakehouse/STID entities are interpreted through
the v2 Ontology's declared entity and relationship bindings while its native graph is loading.
Verified native GraphModel results take precedence when available. Both paths retain Eventhouse
readings and SQL operational overlays, with distinct provenance rather than presenting bound rows as
native GQL results. Published REST metadata does not expose ontology ownership, so native queries
require an explicit operator binding; names and structural similarity
are not evidence. Latest production-parser/model checks passed with 111 native nodes / 108 edges
and 174 enriched nodes / 171 edges using real KQL/SQL and empty STID topology inputs. Protected
hosting checks passed, but these API/model results do not certify interactive app behavior. See
[`docs/knowledge-graph.md`](docs/knowledge-graph.md). Deploy steps:
[`HydroOperationsApp/DEPLOY.md`](HydroOperationsApp/DEPLOY.md).
