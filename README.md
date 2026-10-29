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

## Deploy using the local deployment app

The recommended interface is **Initialize Your Fabric Demo**, included in
[`Raw/workspace-reset/`](Raw/workspace-reset/). It runs locally; it is not the hosted
Hydro Operations website.

1. Use an up-to-date local checkout of **`main`**.
2. In Windows File Explorer, open `Raw\workspace-reset` in that checkout and
   double-click [`Start Fabric Demo.cmd`](Raw/workspace-reset/Start%20Fabric%20Demo.cmd).
   Alternatively, run `python Raw\workspace-reset\launch.py` from the repository root.
3. Open <http://127.0.0.1:5000/>, sign in, and enter the target tenant/workspace.
4. Choose **Full Deploy** for a new workspace, or **Deploy app** for a website
   update to an already prepared workspace.

**Read the [local app launch instructions](HydroOperationsApp/DEPLOY.md#2-start-the-local-app)
and [field-by-field parameter guide](HydroOperationsApp/DEPLOY.md#local-app-parameters-what-to-type).**
They explain prerequisites, example entries, defaults, Foundry project/model setup,
saved settings, progress, and what remains manual. You do not need to assemble
individual notebook, Rayfin, or agent-provisioning commands.

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
The shared semantic catalog adds entity/property descriptions, synonyms, and usage/query guidance;
scoped business rules use documented `rules/{name}.tmdl` parts and `ref rule` model references.
Operator metadata is preserved. Rules guide consumers; they are not enforced SQL constraints or
evidence that an agent executed an action.
Graph materialization is optional in the Fabric generation-2 experience but required for the app's
native graph canvas, tree, and scopes. Ontology-backed Data Agent execution does not universally
require materialization. No published ontology-owned materialization REST operation is established.
Use **Manage graph → select eligible entities/relationships → Continue → Materialize** in the selected
ontology. A key, a supported Delta/mirrored source, and a single backing table are graph eligibility
requirements; do not remove the working `signal_master` Eventhouse stitch to make it eligible.
Prefer the four static asset types and their three hierarchy relationships for a focused initial
projection. `RTI_006_TimeSeriesBinding_RTI_signal` verifies ontology bindings and semantic metadata,
then reads authoritative graph lineage without rewriting scope, submitting refresh jobs, or claiming
graph runtime verification. Missing materialization is recorded as a separate portal prerequisite;
ambiguous ownership and real binding/API failures still propagate.
The canonical deployment orchestrator then generates the explicit
`RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` JSON (`workspaceId`, `ontologyId`, `graphModelId`).
An operator may supply the same verified binding as a fallback when lineage is unavailable.
Ownership is never inferred from names, a sole graph, or sampled structure.
Missing lineage or graph data is an error, not an empty successful query or a reason to fabricate
STID/FK topology. See [the native graph contract](docs/knowledge-graph.md).

| V2 definition part | Purpose |
| --- | --- |
| `database.tmdl` | Root with `compatibilityLevel: 1000000`. |
| `model.tmdl`, `namespaces/default.tmdl` | Model and required default namespace; fresh creation submits both alongside the database root. |
| `entities/*.tmdl` | Entity keys, scalar/time-series properties, and backing configurations. |
| `tables/*.tmdl` | Lakehouse Direct Lake or Eventhouse backing-table/source metadata. |
| `entityRelationships.tmdl` | Semantic entity relationships. |
| `relationships.tmdl` | Physical table joins referenced by semantic relationships or time-series backing; not additional semantic edges. |

`01_Pipe_Setup` always attempts the Data Agent and the ontology-backed Operations Agent; it has no agent mode
parameters or skip flags. No path bypasses generation-2 verification or falls back to a generation-1
ontology. Agent configuration and source readback failures propagate through RTI_009, RTI_010,
RTI_011, and setup. The one temporary exception is Fabric's exact
`This API version is not supported for the specified Ontology item` response after NB09 has verified
the published v2 source identity. NB09 records that response as a known product limitation without
claiming runtime readiness. Successful configuration still does not prove agent execution or alert
delivery.

Stage 2 completes the business ontology **before either agent is published**:
core bindings and the initial UKMet refresh precede NB11's `model` phase, which prepares
operational SQL, inserts only missing demo rows, checks actual cross-source keys/weather
coverage and verifies all required bindings and relationships by readback. The baseline is
16 meaningful business types and 21 relationships; compatible custom content is retained.
NB09 and NB10 each check the successful selected-model receipt and its live definition.
NB11's later `agent` phase only adds the operational SQL execution adapter; it cannot seed
or mutate the ontology. Both phases and both agents are mandatory setup activities.
Failed agent execution therefore cannot leave a newly built ontology limited to five core types.

The separate Eventhouse-Knowledge Operations Agent is retired and is never provisioned.
After verifying the retained ontology agent, RTI_010 removes only the exact
`RTI_Demo_OpsAgent_Eventhouse_<suffix>` in the target folder with the matching KQL database
Knowledge source, verifies its absence, and deletes its four `eventhouse_ops_agent_*` settings.
Identity conflicts and deletion/readback failures fail setup rather than deleting another agent.
The original ontology Knowledge source, SQL/KQL playbook grounding, Teams destination,
`Pipe_SendEmailAlert`, Outlook connection, Eventhouse, and silver tables are retained.

RTI_009 tracks publication separately from functional validation. Healthy runtime status remains
`data_agent_deployment_status=ready`, `data_agent_publication_status=published`, and
`data_agent_runtime_status=verified`, with live generation `2`. The exact temporary unsupported
Ontology-v2 API response is recorded as `known_product_limitation`; publication alone is never
misreported as readiness.

Fabric can promote an ontology `datasource.json` into an
`ontologies/<name>/ontology.json` context during publication. NB09 verifies that conversion
by the selected live v2 workspace/item identity, not the old path alone. It requires unchanged
entity selections, descriptions and custom content; only the known schema/type conversion,
omitted empty defaults and generated authored-description markers are accepted. Missing,
ambiguous or lossy source readback still fails setup before any runtime readiness claim.
NB09 retains or creates explicit execution adapters for the selected ontology's Lakehouse
and Eventhouse. Ontology association supplies semantic bindings but does not reliably expose
SQL/KQL query tools in every session. New Lakehouse adapters include the authoritative
ontology-bound tables, including weather, alongside the core reference tables.
NB11 retains these adapters when publishing the operational SQL source. Existing source
identities, selections, custom metadata and source-specific guidance remain intact.

The setup/runtime split is intentional for this synthetic-data demo, not a missing dependency:
NB09's bounded MCP smoke checks facilities before NB11 publishes the final SQL adapter.
The app later invokes the Data Agent through Supervisor → Sparky → Fabric Responses.
These transports do not need to be unified, and setup does not run an exhaustive SQL/KQL/
combined-source answer matrix. NB10 configures the Operations Agent and its delivery actions
without sending Teams/email notifications as part of provisioning; delivery is exercised when
demonstrating those actions, not made another setup gate.

The Data Agent smoke check uses the selected ontology as semantic context and permits source-native
SQL on its associated Lakehouse static binding. It compares the first five facility IDs/names with
independently read configured Lakehouse rows. Expected values are not supplied in the prompt;
operational SQL, KQL and unrelated sources are excluded, while their configurations remain preserved.
Exact query-result CSV attachments are supported, including the generated-file footer returned by
Fabric MCP. Receipts retain source identities and exact reference/returned rows, not full source
schemas; diagnostic response excerpts are bounded and explicitly marked when truncated.
Evidence uses `verification=ontology_context_facilities_smoke_v1`, scoped to
**"Ontology-context facilities functional smoke test; execution provenance, native ontology traversal,
telemetry and operational SQL are not attested."** It does not certify cryptographic provenance,
SQL/combined-source answers, native graph readiness, or the entire agent. Semantic/transport errors and wrong rows fail;
nonparseable, count-only, or unverifiable answers are inconclusive. Both fail required execution.

RTI_011 attaches and publishes the SQL source independently of the ontology source or NB09's
runtime result. It verifies the SQL source through draft/published readback, preserves existing
sources and custom settings, and records `data_agent_sql_source_status=published` without
overwriting NB09's ontology status. Actual SQL-source failures still fail NB11. SQL/custom parts,
custom instructions, and entity selections also survive runtime-failing NB09 reruns.

An ontology SQL binding describes business semantics and source identity; it is not proof
that a published Data Agent has an executable SQL tool for those tables. Retain the verified
SQL execution adapter unless an ontology-only operational SQL execution test establishes
equivalent capability. This does not create duplicate ontology entities or copy SQL data.

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
| **RTI_002_Setup_Eventhouse_Only** | Eventhouse + KQL DB + `OPCUAEvents` + Eventstream (custom endpoint → Eventhouse); waits for definition-update completion and bounded source-connection readiness on fresh rebuilds. | ✅ |
| **RTI_003_ingest_transform_medallion_SelfContained** | Bronze → Silver → Gold transforms; builds `silver_signal_master`. | ✅ |
| **RTI_004_build_ontology_mapping_rti_structured** | Creates/verifies Ontology v2 TMDL (5 entities, 4 semantic relationships) + time‑series properties; preserves existing bindings on safe reruns. | ✅ |
| **RTI_005_entity_DataBinding_rti_structured** | Direct Lake backing tables, scalar-property bindings, and physical joins referenced by semantic relationships. | ✅ |
| **RTI_006_TimeSeriesBinding_RTI_signal** | Binds `OPCUAEvents` telemetry to `signal_master`, including standalone `event_time`; enriches semantics/rules and reads graph ownership without changing portal-selected materialization scope. | ✅ |
| **RTI_007_generate_and_ingest_OPCUA_Stream** | On‑demand OPC UA telemetry generator (run via `Pipe_Stream`). | — |
| **RTI_008_build_realtime_dashboard** | Two‑page Real‑Time Dashboard over `OPCUAEvents`: *Hydro Telemetry* (Station/Turbine filters, one chart per sensor group) + *OPC UA Telemetry*. Deploys from a definition file; shortcuts the silver tables into the Eventhouse so filters come from data. | ✅ |
| **RTI_009_build_data_agent** | Provisions the Data Agent with matching live v2 source/readback, then performs bounded real MCP facility-record validation; only the exact temporary unsupported-Ontology-v2 product response is non-fatal. | ✅ |
| **RTI_010_build_operations_agent** | Provisions the ontology-backed Operations Agent, playbook/actions, Teams, and email-alert pipeline against v2; retires the verified obsolete Eventhouse-Knowledge agent/settings without deleting shared dependencies; failures propagate. | ✅ |
| **RTI_011_seed_sql_wire_graphql_agent** | Builds and verifies the complete SQL/weather business model before agents (`model` phase); publishes the SQL execution adapter afterward (`agent` phase), without a STID API. Standalone app/deployer calls retain `all`; `seed_demo_data=False` preserves operational rows. | — |
| **RTI_Orchestrator_Setup** | Stage 2 driver: shares the Weather Environment/default Lakehouse, runs core bindings and initial UKMet ingestion, completes the model before both agents, then enables the schedule after all required activities succeed. | Stage 2 |

NB09 and NB10 persist diagnostic agent status in the same `rti_demo_settings` Delta table. Their
execution order is **NB06 → NB09 → NB10**:
serialize the two status `MERGE` operations to avoid `DELTA_CONCURRENT_APPEND`. Other independent
setup branches remain parallel. There are no agent-mode skip branches. Status-write failures also
fail setup.

> [!NOTE]
> `RTI_000` is documentation only. `*_shortcut` / non‑self‑contained variants are legacy reference copies, not wired into `Pipe_Setup`. Readable `.ipynb` mirrors live in [`Raw/RTI_Notebooks/`](Raw/RTI_Notebooks/).

> [!TIP]
> Changing, adding or embedding a Real‑Time Dashboard? See [`docs/dev-dashboards.md`](docs/dev-dashboards.md).
> `RTI_008` and its `.ipynb` mirror are **generated** — edit `Raw/RTI_Notebooks/tools/build_rti_008.py`
> or the definition JSON, then re‑run the generator.
>
> Building or extending the application Knowledge Graph? See
> [`docs/knowledge-graph.md`](docs/knowledge-graph.md) for its Ontology relationship, source
> federation, operational scenarios, performant 2D presentation, health semantics, and
> RDF/OWL path.

The companion app also has an optional **Map** tab for real Norwegian energy
context, separate from the synthetic demo. It imports NVE grid/hydropower/reservoir
data, Statnett measurements and Nord Pool UMM into a dedicated GeoContext Lakehouse
and serves viewport-bounded map layers through Eventhouse. See
[Operations Map](HydroOperationsApp/README.md#operations-map) for provisioning and
source-freshness behavior. SafeSky, OGN and Altitude Angel are not included.

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
> the mailbox connection below. There is no Operations Agent skip flag.
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

**Recommended:** follow the [short deployment guide](HydroOperationsApp/DEPLOY.md)
and launch `python Raw\workspace-reset\launch.py` from the repository root.
The guided flow separates data/energy/Foundry setup from final app publication.
Existing-workspace website updates use **Deploy app**, not Git import or core setup.
The following pipeline details are a manual Fabric reference.

1. Open **`01_Pipe_Setup`** and fill its **pipeline parameters**:

   | Parameter | Example | Notes |
   |---|---|---|
   | `env_suffix` | `V6` | The one environment lever. |
   | `workspace_id` | `19f3d588-…` | GUID after `/groups/` in the Fabric URL. |
   | `key_vault_uri` | `https://myvault.vault.azure.net/` | |
   | `key_vault_tenant_id_secret_name` | `tenantid` | Secret **name**, not value. |
   | `key_vault_client_id_secret_name` | `clientid` | Secret **name**, not value. |
   | `key_vault_client_secret_name` | `clientsecret` | Secret **name**, not value. |
   | `ops_agent_teams_team_id` | `c480320e-…` | Target team for Operations Agent Teams delivery. |
   | `ops_agent_teams_channel_id` | `19:…@thread.tacv2` | Target channel for Operations Agent Teams delivery. |
   | `ops_agent_run_as_user` | `admin@…onmicrosoft.com` | Optional guard for the delegated run-as identity; blank uses the deploying user. |
   | `alert_email_to` | `operations@contoso.com` | `To` recipient for `Pipe_SendEmailAlert`. Required by the local launcher; RTI_010 falls back to run-as/deploying user when blank. |
   | `per_notebook_timeout_secs` | `3600` | Per‑child DAG timeout. |

   > [!IMPORTANT]
   > Tenant-specific Key Vault, Team ID, channel ID and run-as defaults are blank.
   > Supply your own destinations; the launcher checks Teams/email inputs before
   > starting Spark. Blank run-as uses the deploying user. Enter each **full**
   > value (the UI truncates long values visually).

2. **Run `Pipe_Setup`.** Stage 1 (`RTI_001`) creates the Lakehouse and exits its name; Stage 2 attaches it in the published Weather Environment — no manual lakehouse pinning. Use a fresh workspace or unused suffix if the target ontology is v1. Core bindings plus initial UKMet ingestion precede complete model preparation. Both agents independently require that verified complete model; an agent failure cannot block model construction. Real notebook failures still fail setup; NB09 alone reports the exact documented temporary unsupported-Ontology-v2 response without claiming readiness. Inspect status/reasons and verify execution/delivery separately from configuration.
3. **Run `Pipe_Stream`** whenever you want a burst of live telemetry.
4. **`03_Pipe_Weather` runs automatically every six hours** (03:20/09:20/15:20/21:20 UTC, aligned to the 00/06/12/18 UTC model runs both vendors derive from). `Weather_Orchestrator` shares one Spark session across area preparation, UKMet ingestion and `Weather_020_area_calculations`, rebuilding vendor- and forecast-type-specific metrics, enforcing retention and refreshing the app's wide serving tables. Aurora is opt-in (`enable_aurora=false` by default) and runs afterward with another metrics refresh. Workspace provisioning creates the schedule; Stage 2 enables it only after successful setup.

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
connector), telemetry (Eventhouse KQL), and operational records (Rayfin SQL) on one screen. Its
Knowledge Graph requires verified native GraphModel entities and relationships for canvas, tree
and scopes. Lakehouse/STID rows never substitute for missing or failed native topology.
Eventhouse readings and SQL operational overlays enrich only actual native entities; operational
pages remain independently available. Published REST metadata does not expose ontology ownership, so native queries
require an explicit operator binding; names and structural similarity
are not evidence. Latest production-parser/model checks passed with 111 native nodes / 108 edges
and 174 enriched nodes / 171 edges using real KQL/SQL and empty STID topology inputs. Protected
hosting checks passed, but these API/model results do not certify interactive app behavior. See
[`docs/knowledge-graph.md`](docs/knowledge-graph.md). Deploy steps:
[`HydroOperationsApp/DEPLOY.md`](HydroOperationsApp/DEPLOY.md).

Start with that guide's interface chooser: the local deployment web app and the
operator/agent command line use the same deployment orchestrator. For the Foundry
multi-agent setup module (including the Hosted Agent), followed by final app-only
publication, see
[`docs/foundry-deployment-and-multi-agent-setup.md`](docs/foundry-deployment-and-multi-agent-setup.md). Manual portal work
is limited to documented access/setup prerequisites and ontology-managed graph materialization.
Deployment targets are not pinned to the original demo: select the tenant/workspace and Azure subscription,
then supply or discover the target's Foundry project/model/registry. New project creation requires explicit
model/version/SKU/capacity inputs; the Foundry and full-workflow forms expose them.
Energy-source setup is also explicit, without requiring a hidden bootstrap profile.
Routine app publication preserves the target's energy setting and verifies prepared
agents without provisioning them. Generated endpoints, item GUIDs and azd state
are resolved and verified for that target, not copied from another workspace.
For agent responsibilities, routing, evidence-based RCA, proposed maintenance specialists,
and the ten-flow acceptance status, see
[`HydroOperationsApp/AGENT-ACCEPTANCE.md`](HydroOperationsApp/AGENT-ACCEPTANCE.md).
