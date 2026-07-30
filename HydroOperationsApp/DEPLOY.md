# Hydro Operations — Deployment Guide

<!-- markdownlint-disable MD029 MD033 -->

Follow these steps **in order** to deploy the Hydro Operations app to Microsoft Fabric. Each step is
a copy-paste command with a one-line note. For how it all fits together, see [README.md](README.md)
and the [root README](../README.md).

**The path:** build the RTI environment → install → configure env → provision the backend → deploy →
seed & provision (RTI_011) → bind the STID GraphQL API → switch on live auth → start the stream.

The app composes **three Fabric data stores** on one screen:

| Store | Source | Auth path |
| --- | --- | --- |
| **STID** (facilities/equipment/instruments) | Lakehouse **GraphQL API** item (created by `RTI_011`, bound in the portal) | Power BI Service — `GraphQLApi.Execute.All` |
| **Telemetry** (live signals) | Eventhouse `OPCUAEvents` (KQL) | Azure Data Explorer — `user_impersonation` |
| **Operational** (work orders, inspections, spare parts, 3D models) | Rayfin-managed SQL database | Rayfin data client (delegated) |

## Prerequisites

- **Node.js 24** (the app pins `>=24 <25`). If your default Node differs, prefix any command with
  `npx -y -p node@24 -c "<cmd>"` (e.g. `npx -y -p node@24 -c "npm run build"`).
- A **Microsoft Fabric workspace** on a capacity you can use, plus permission to deploy to it and to
  sign in to the owning tenant.
- **Azure CLI** (`az`) for the one-time live-auth setup in Step 8.
- An **Entra SPA app registration** (delegated, no secret) for browser sign-in.

## 1. Build the RTI Fabric environment (once, in Fabric)

The Lakehouse, Eventhouse, ontology, Real-Time Dashboard, and agents are produced by the RTI
notebooks, orchestrated by the **`01_Pipe_Setup`** data pipeline. In your Fabric workspace, open
`01_Pipe_Setup`, fill in its parameters, and run it. See the [root README](../README.md) for the
full parameter list and notebook inventory.

> This is the one part that must happen **in Fabric** — the app cannot build the Lakehouse/Eventhouse
> for you. `Pipe_Setup` does **not** create the STID GraphQL API item or seed the operational SQL DB;
> those happen in Step 7 via `RTI_011`.

## 2. Clone and install

```powershell
git clone https://github.com/dibakardharchoudhury/FabricOntologyHydro.git
cd FabricOntologyHydro/HydroOperationsApp
npm install
```

`npm install` also installs the pinned Rayfin CLI, so every `rayfin` command below just works. Don't
install the CLI globally — a global copy can drift out of sync with the project.

## 3. Configure the environment

```powershell
Copy-Item rayfin/.env.example rayfin/.env
```

Fill in the **REQUIRED** values in `rayfin/.env`:

```ini
FABRIC_WORKSPACE_NAME=<your Fabric workspace display name>
RAYFIN_PUBLIC_WORKSPACE_ID=<your Fabric workspace GUID>
RAYFIN_PUBLIC_AAD_CLIENT_ID=<your Entra SPA app (client) id>
RAYFIN_PUBLIC_TENANT_ID=<your Entra tenant id>
```

The app **discovers** most artifact ids/URIs at runtime from the workspace by display name
(`src/services/fabric.ts` → `ensureConfig`), so the `AUTO-DISCOVERED FALLBACKS` only need values if
you want to pin a specific artifact or run without runtime discovery. Never edit `.env.local` — the
build projects `RAYFIN_PUBLIC_*` into it as `VITE_RAYFIN_*` automatically. No secrets belong in this
file.

## 4. Sign in to Rayfin

```powershell
npx rayfin logout
npx rayfin login --select
npx rayfin login status
```

Pick the tenant that owns your Fabric workspace, then confirm the active identity.

## 5. Provision the Rayfin backend and SQL schema

```powershell
npm run up          # create/update AppBackend + auth + data services (workspace from FABRIC_WORKSPACE_NAME)
npm run rayfin:db   # apply rayfin/data/schema.ts to the live SQL database (creates/updates tables)
```

`rayfin:db` generates a Data API Builder config from `schema.ts` and pushes it to the remote Rayfin
item. It **creates the tables but does not insert rows** — seeding happens in Step 7 via `RTI_011`.

## 6. Deploy the app

```powershell
npm run deploy      # builds (tsc + vite, rayfin env auto-injected) and deploys the static app
```

The `prebuild` hook runs `rayfin env --framework vite` to inject `VITE_*` from the deployment before
`vite build`. When it finishes your app is **live** — with empty operational tables and no STID
GraphQL binding yet. The next steps add data and wire the live stores.

## 7. Seed & provision (RTI_011) + bind the STID GraphQL API

`RTI_011_seed_sql_wire_graphql_agent` is the **authoritative seeder**. Its `SEED_SQL` step is a T-SQL
`MERGE` (upsert) that **re-seeds and updates** all five operational tables every run.

1. Open the deployed app and sign in (avatar button).
2. Click **"Seed & provision"** in the header.

The app runs `RTI_011` in your workspace, which:

- MERGE-upserts WorkOrders / MaintenanceNotifications / Inspections / SpareParts / Asset3DModels,
- creates the STID **GraphQL API** item, and
- adds the hydro-operations SQL database as a source on the Data Agent.

### Bind the STID GraphQL API (one manual step)

`RTI_011` creates an **empty** GraphQL API item. Open it in the Fabric portal once and add the STID
Lakehouse tables (`Facilities`, `Systems`, `Equipment`, `Instruments`) as its data source. The app
then discovers the endpoint at runtime — leave `RAYFIN_PUBLIC_STID_GRAPHQL_URL` blank.

> **Fallback (no RTI_011):** if `RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_NAME` is blank, the app runs an
> idempotent client-side self-seeder that inserts demo rows only when a table is empty. For direct
> SQL, run [`sql/seed-operational-data.sql`](sql/seed-operational-data.sql) with `sqlcmd`
> (`-v ActorOid=<your-oid>`) and verify with [`sql/validate-seed.sql`](sql/validate-seed.sql).

## 8. Switch on live auth (telemetry + STID GraphQL)

The browser queries the Eventhouse (KQL) and the STID GraphQL API directly, each needing a delegated
Entra permission that interactive consent can fail to establish cleanly. Do this **once** per fresh
deploy (a from-scratch rebuild gets a new hosting hostname):

```powershell
az login                  # as an owner / Application Administrator (privileged role for consent)
npm run setup-live-auth   # or: npm run setup-live-auth:dry  to preview first
```

`scripts/setup-live-auth.mjs` is idempotent and does two steps:

| Step | What it fixes |
| --- | --- |
| **1. SPA redirect URIs** | Registers the Fabric hosting origin(s) from `rayfin/rayfin.yml` + `localhost:5173` as **SPA redirect URIs**. Prevents **AADSTS50011** (redirect mismatch). |
| **2. Delegated permissions + consent** | Adds **Azure Data Explorer** `user_impersonation` (telemetry) and **Power BI Service** `GraphQLApi.Execute.All` (STID GraphQL), then creates the tenant consent grant. Prevents **AADSTS650057** (invalid resource) / **AADSTS65001** (not consented). |

Two things stay **manual** (per user/cluster, not per deploy): grant the signed-in user a **KQL
Database Viewer** role on the Eventhouse, and allow the app origin in the Eventhouse cluster's
**CORS** settings. Use `--redirect-only` / `--grant-only` to run a single step.

## 9. Start the telemetry stream

Run the **`02_Pipe_Stream`** pipeline (wraps `RTI_007`) in Fabric to push an OPC UA telemetry burst
into the Eventhouse. In the app you can also click **"Start stream"** in the header, which triggers
the same pipeline. Live asset gauges populate once telemetry lands and Step 8 auth is in place.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| **"System cancelled the Spark session"** when running RTI_011 | RTI_011's lakehouse binding is stale. The repo binds it to the correct lakehouse and NB01 re-binds it at setup time — re-import RTI_011 (Fabric **source control → Update**) or re-run Stage 1 (`RTI_001`) so the binding refreshes, then retry. |
| **"No GraphQL API found"** / STID panels empty | Run **Seed & provision** (Step 7) so `RTI_011` creates the GraphQL API item, then **bind** the STID tables to it in the portal. |
| Live asset signals stay empty | `02_Pipe_Stream` must have run (Step 9) **and** Step 8 live-auth must be in place. |
| **"Connect"** / sign-in popup fails with an `AADSTS` error | Run `npm run setup-live-auth` (after `az login`). **AADSTS50011** = origin not a registered SPA redirect URI (step 1). **AADSTS650057** = app lacks the ADX or Power BI delegated permission (step 2). **AADSTS65001** = no admin consent (step 2 grants it). Entra edge-caches config — hard-refresh (Ctrl+F5) after. |
| Telemetry in KQL but app shows "no live readings" | Browser reaches KQL but the query fails. Open **F12 → Console** and look for `[kql]` / `[fabricToken]`. `HTTP 401` = wrong token audience — re-connect so the popup consents to the cluster scope. `HTTP 403` = grant the user **KQL Database Viewer** on the Eventhouse. A network/`catch` error with no HTTP status = **CORS** isn't allowing the app origin on the Eventhouse cluster. |
| Operational writes fail with an **Internal server error** | A `@text()` column without a `max` maps to `NVARCHAR(MAX)`, which some operations reject. Only `partNumber` is bounded today — bound the offending column in `rayfin/data/schema.ts` and re-run `npm run rayfin:db`. |
| Deployed into the wrong workspace | `npm run up` reads `FABRIC_WORKSPACE_NAME` from `rayfin/.env`. Fix it there and re-run. |

## Reset and start over

Delete `rayfin/.env`, `rayfin/.env.local`, and `rayfin/.deployments.json` for a true from-scratch
build, then redo from Step 3. `npm run up` re-writes the `RAYFIN_PUBLIC_*` deployment keys; you
re-add the required values from your workspace/Entra app. Because a fresh build gets a **new hosting
hostname**, re-run `npm run setup-live-auth` (Step 8) after the first `npm run deploy` of every fresh
deploy to re-register the redirect URI and re-grant the delegated permissions.
