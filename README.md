# Fabric IQ RTI Demo — Synthetic Energy Dataset, Ontology & Real‑Time Intelligence

An end‑to‑end **Microsoft Fabric** solution built on a **fully synthetic** hydropower dataset. One
**Data Pipeline** stands up the whole environment: a medallion Lakehouse, an Eventhouse telemetry
stream, a Fabric IQ ontology with live time‑series bindings, a Real‑Time Dashboard, a Data Agent,
and an Operations Agent (Teams alerts). A companion React app ([`HydroOperationsApp/`](HydroOperationsApp/README.md))
composes it all on one screen.

> [!NOTE]
> All data is synthetic — no real plant or customer data.

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
| Data Agent | `RTI_Demo_Agent_V6` |
| Dashboard | `RTI_Demo_OPCUA_TelemetryStats_V6` |
| Operations Agent | `RTI_Demo_OpsAgent_V6` |

Pipelines are **not** versioned (one of each per workspace): `Pipe_Setup`, `Pipe_Stream`, `Pipe_SendEmailAlert`.

## Notebooks

| Notebook | Role | In setup DAG |
|---|---|:---:|
| **RTI_001_create_lakehouse_SelfContained** | Foundation: creates the Lakehouse, seeds STID master data into `Files/bronze/stid/`, derives names, writes `rti_demo_settings`, exits the lakehouse name. | Stage 1 |
| **RTI_002_Setup_Eventhouse_Only** | Eventhouse + KQL DB + `OPCUAEvents` + Eventstream (custom endpoint → Eventhouse). | ✅ |
| **RTI_003_ingest_transform_medallion_SelfContained** | Bronze → Silver → Gold transforms; builds `silver_signal_master`. | ✅ |
| **RTI_004_build_ontology_mapping_rti_structured** | Deploys the ontology (5 entities, 4 relationships) + time‑series properties. | ✅ |
| **RTI_005_entity_DataBinding_rti_structured** | Static Lakehouse data bindings + relationship contextualizations. | ✅ |
| **RTI_006_TimeSeriesBinding_RTI_signal** | Binds `OPCUAEvents` telemetry to `signal_master`. | ✅ |
| **RTI_007_generate_and_ingest_OPCUA_Stream** | On‑demand OPC UA telemetry generator (run via `Pipe_Stream`). | — |
| **RTI_008_build_realtime_dashboard** | Real‑Time Dashboard over `OPCUAEvents`. | ✅ |
| **RTI_009_build_data_agent** | Data Agent over the ontology. | ✅ |
| **RTI_010_build_operations_agent** | Operations Agent + `Pipe_SendEmailAlert` for Teams/email alerts. | ✅ |
| **RTI_011_seed_sql_wire_graphql_agent** | On‑demand: seeds the app's SQL tables, creates + binds the STID GraphQL API, adds the SQL DB as a Data Agent source. Run by the app's **Seed & provision** button. | — |
| **RTI_Orchestrator_Setup** | Stage 2 driver: attaches the Lakehouse via `%%configure`, then runs NB02–06, 08–10 in one Spark session. | Stage 2 |

> [!NOTE]
> `RTI_000` is documentation only. `*_shortcut` / non‑self‑contained variants are legacy reference copies, not wired into `Pipe_Setup`. Readable `.ipynb` mirrors live in [`Raw/RTI_Notebooks/`](Raw/RTI_Notebooks/).

## Prerequisites (one‑time)

The demo is otherwise self‑contained — **no ADLS, shortcut, or cloud connection.** You only grant the
executing **Service Principal (SPN)** access and flip a couple of tenant switches:

| # | Grant / setting | What it needs |
|:--:|---|---|
| 1 | **Key Vault secrets** | SPN has **Key Vault Secrets User** — *Get* on `tenantid`, `clientid`, `clientsecret`. |
| 2 | **Workspace access** | SPN has **Contributor** (or higher) on the Fabric workspace. |
| 3 | **Tenant settings** *(Admin portal)* | **Service principals can use Fabric APIs** (SPN in the allowed security group) **+ Copilot / AI** on a supported capacity — required by `RTI_009` / `RTI_010`. |
| 4 | **Private endpoint to Key Vault** | Only if the vault blocks public access — add a managed private endpoint in *Workspace settings → Networking* and approve it on the vault. |

> [!IMPORTANT]
> **5 · Email‑alert connection (OAuth2) — the one step you must do by hand.**
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
> `RTI_010` then **auto‑detects and reuses** it (by that name, else any OAuth2 Outlook connection) and
> wires it into the pipeline — unattended from then on. Pin a specific one with the
> `alert_email_connection_id` setting. If a connection later shows *“Failed to load”*, its token
> expired — open it and **Edit → Sign in** to refresh.

> [!NOTE]
> The **Hydro Operations web app** signs users in with a **second, separate identity** — the
> single-tenant SPA app registration **`Hydro Operations Fabric Client`** (no secret), distinct
> from this notebook SPN. The local deployer attempts to create and configure it, but ordinary
> users in locked-down tenants commonly cannot create app registrations, add SPA redirect URIs
> or delegated permissions, or grant tenant-wide admin consent. This does **not** fail AppBackend
> or static-host deployment; it produces degraded-auth warnings, and browser sign-in/live Fabric
> data remain unavailable until configured. An **Application Administrator /
> Cloud Application Administrator** must create/configure the app, and a **Privileged Role
> Administrator / Global Administrator** must grant admin consent. Use the copy-pasteable handoff
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
   | `ops_agent_teams_team_id` | `c480320e-…` | Teams team for alerts. |
   | `ops_agent_teams_channel_id` | `19:…@thread.tacv2` | Teams channel for alerts. |
   | `ops_agent_run_as_user` | `admin@…onmicrosoft.com` | Optional — blank ⇒ deploying user. |
   | `per_notebook_timeout_secs` | `3600` | Per‑child DAG timeout. |

   > [!IMPORTANT]
   > The pipeline ships with the author's **example defaults** — replace **every** value for a new tenant. Enter each **full** name (the UI truncates long names visually); the child notebooks' own parameter cells ship blank and fail fast if a required value is missing.

2. **Run `Pipe_Setup`.** Stage 1 (`RTI_001`) creates the Lakehouse and exits its name; Stage 2 (orchestrator) attaches it and runs the rest — no manual lakehouse pinning.
3. **Run `Pipe_Stream`** whenever you want a burst of live telemetry.

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

- **Sensors/signals:** edit the STID source CSVs, re‑run `Pipe_Setup` (rebuilds silver + ontology), then `Pipe_Stream`.
- **Telemetry values:** edit the simulator in `RTI_007` (ranges, quality, drift/spikes).
- **Signal schema:** keep `RTI_004` (ontology properties) ↔ `RTI_002` (`OPCUAEvents`) ↔ `RTI_007` (payload) ↔ `RTI_006` (binding) aligned.
- **New environment:** change `env_suffix` and re‑run `Pipe_Setup`.

## Notes

- All data is synthetic (P&ID parsing and 3D data are simulated/metadata‑only). **Where it lives:** STID master data + medallion silver/gold tables in the **Lakehouse** (`Energy_IQ_LakehouseRTI_V6`); live OPC UA telemetry in the **Eventhouse** KQL DB (`OPCUAEvents`); the app's operational records — work orders, maintenance notifications, inspections, spare parts, 3D‑model metadata — in the **Fabric SQL Database** (seeded by `RTI_011`).
- Pipeline notebook activities reference notebooks by **GUID** — pipelines don't auto‑repoint across workspaces.

## Companion app

[`HydroOperationsApp/`](HydroOperationsApp/README.md) — a React + Rayfin app that joins STID (Lakehouse
GraphQL), telemetry (Eventhouse KQL), and operational records (Rayfin SQL) on one screen. Deploy steps: [`HydroOperationsApp/DEPLOY.md`](HydroOperationsApp/DEPLOY.md).
