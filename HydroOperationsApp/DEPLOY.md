# Hydro Operations — Deployment Guide

<!-- markdownlint-disable MD029 MD033 -->

Deploy the Hydro Operations app to Microsoft Fabric. Run every command from
`HydroOperationsApp/` on **Node 24** (if your default Node differs, prefix with
`npx -y -p node@24 -c "<cmd>"`). Architecture: [README.md](README.md) · [root README](../README.md).

**Path:** build RTI env → install → configure → provision → deploy → seed & provision → live auth → start stream.

## Prerequisites

- **Node.js 24** (the app pins `>=24 <25`).
- A **Fabric workspace** on a usable capacity, with permission to deploy.
- **Azure CLI** (`az`) for the one‑time live‑auth step (Step 8).
- An **Entra SPA app registration** (delegated, no secret) for browser sign‑in.
- **Fabric tenant settings** (Admin, one‑time): *Service principals can use Fabric APIs* and *Copilot / AI* enabled — needed by `Pipe_Setup` and the Data Agent ([root README](../README.md)).

## 1. Build the RTI Fabric environment (in Fabric)

Open **`01_Pipe_Setup`** in your workspace, fill its parameters ([root README](../README.md)), and
run it. This creates the Lakehouse, Eventhouse, ontology, dashboard, and agents. It does **not**
create the STID GraphQL API or seed the operational SQL DB — those happen in Step 7.

## 2. Clone and install

```powershell
git clone https://github.com/dibakardharchoudhury/FabricOntologyHydro.git
cd FabricOntologyHydro/HydroOperationsApp
npm install
```

`npm install` installs the pinned Rayfin CLI — don't install it globally.

## 3. Configure

```powershell
Copy-Item rayfin/.env.example rayfin/.env
```

Fill the four **required** values in `rayfin/.env` (no secrets belong here):

```ini
FABRIC_WORKSPACE_NAME=<Fabric workspace display name>
RAYFIN_PUBLIC_WORKSPACE_ID=<Fabric workspace GUID>
RAYFIN_PUBLIC_AAD_CLIENT_ID=<Entra SPA app (client) id>
RAYFIN_PUBLIC_TENANT_ID=<Entra tenant id>
```

Most artifact ids/URIs are **discovered at runtime** by workspace display name; the
`AUTO-DISCOVERED FALLBACKS` only need values if you want to pin something. Never edit `.env.local`
(the build writes `VITE_RAYFIN_*` into it automatically).

## 4. Sign in to Rayfin

```powershell
npx rayfin logout
npx rayfin login --select   # pick the tenant that owns your workspace
npx rayfin login status
```

## 5. Provision the backend and SQL schema

```powershell
npm run up          # create/update AppBackend + auth + data services
npm run rayfin:db   # apply rayfin/data/schema.ts to the live SQL database (creates tables, no rows)
```

## 6. Deploy the app

```powershell
npm run deploy      # builds (tsc + vite, rayfin env auto‑injected) and deploys the static app
```

The deploy prints the **hosting URL**. Add it to `rayfin/rayfin.yml` under
`services.auth.allowedRedirectUris` (replace hostnames left over from another tenant), then re‑run
`npm run up` — both Rayfin sign‑in and Step 8 read the allowed origins from there. The app is now live
with empty operational tables and no STID binding yet.

## 7. Seed & provision (RTI_011)

`RTI_011` is the authoritative seeder — its `MERGE` re‑seeds all five tables safely on every run.

1. Open the deployed app and sign in (avatar button).
2. Click **"Seed & provision"** in the header.

It runs `RTI_011` in your workspace, which upserts the operational tables, creates + **auto‑binds**
the STID **GraphQL API** to the Lakehouse SQL endpoint, and adds the SQL DB as a Data Agent source.
The app discovers the GraphQL endpoint at runtime — leave `RAYFIN_PUBLIC_STID_GRAPHQL_URL` blank.

> **If auto‑bind fails** (see the notebook's STEP B output): open the GraphQL API item in the Fabric
> portal once and add the STID tables (`Facilities`, `Systems`, `Equipment`, `Instruments`).
>
> **Fallback (no RTI_011):** if `RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_NAME` is blank, the app self‑seeds
> demo rows only when a table is empty. For direct SQL, run
> [`sql/seed-operational-data.sql`](sql/seed-operational-data.sql) (`-v ActorOid=<your-oid>`) and
> verify with [`sql/validate-seed.sql`](sql/validate-seed.sql).

## 8. Switch on live auth (once per fresh deploy)

The browser calls the Eventhouse (KQL) and STID GraphQL directly, each needing a delegated Entra
permission. Run once after the first deploy of a fresh build (a rebuild gets a new hosting hostname):

```powershell
az login                  # as an owner / Application Administrator
npm run setup-live-auth   # or setup-live-auth:dry to preview
```

`scripts/setup-live-auth.mjs` is idempotent: (1) reads the hosting origins from `rayfin/rayfin.yml`
(`allowedRedirectUris`) plus `localhost:5173` and registers them as **SPA redirect URIs** on the Entra
app (fixes **AADSTS50011**); (2) adds **Azure Data Explorer** `user_impersonation`
and **Power BI Service** `GraphQLApi.Execute.All`, then grants consent (fixes **AADSTS650057 / 65001**).

Two things stay manual (per user/cluster): grant the signed‑in user **KQL Database Viewer** on the
Eventhouse, and allow the app origin in the Eventhouse cluster's **CORS** settings.

## 9. Start the telemetry stream

Run **`02_Pipe_Stream`** in Fabric (or click **"Start stream"** in the app) to push an OPC UA burst
into the Eventhouse. Live gauges populate once telemetry lands and Step 8 auth is in place.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| **"System cancelled the Spark session"** running RTI_011 | Its lakehouse binding is stale — re‑import RTI_011 (Fabric **source control → Update**) or re‑run `RTI_001`, then retry. |
| **"No GraphQL API found"** / STID panels empty | Run **Seed & provision** (Step 7). If STEP B reports auto‑bind failed, bind the STID tables in the portal. |
| Live signals stay empty | `02_Pipe_Stream` must have run (Step 9) **and** Step 8 live‑auth must be in place. |
| Sign‑in fails with **AADSTS** | Ensure your deployed hosting URL is in `rayfin/rayfin.yml` (`allowedRedirectUris`), then run `npm run setup-live-auth` (after `az login`). 50011 = redirect URI; 650057 = missing permission; 65001 = no consent. Hard‑refresh (Ctrl+F5) after. |
| KQL reachable but "no live readings" | F12 → Console: `HTTP 401` = re‑connect for the cluster scope; `HTTP 403` = grant **KQL Database Viewer**; network error with no status = **CORS** not allowing the app origin. |
| Operational writes fail with **Internal server error** | An unbounded `@text()` column maps to `NVARCHAR(MAX)`, which some ops reject. Bound it in `rayfin/data/schema.ts` and re‑run `npm run rayfin:db`. |
| Deployed into the wrong workspace | `npm run up` reads `FABRIC_WORKSPACE_NAME` from `rayfin/.env` — fix it and re‑run. |

## Reset

Delete `rayfin/.env`, `rayfin/.env.local`, and `rayfin/.deployments.json`, then redo from Step 3.
A fresh build gets a new hosting hostname — add it to `rayfin/rayfin.yml` (`allowedRedirectUris`) and
re‑run `npm run up`, then `npm run setup-live-auth` (Step 8) after the first `npm run deploy`.
