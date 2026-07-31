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
- **Two Entra identities** — a **pre-provisioned notebook SPN** (secret in Key Vault, used by the pipelines) and a delegated **app SPA** (no secret, used by the browser). See [Identities and permissions](#identities-and-permissions).
- **Fabric tenant settings** (Admin, one‑time): *Service principals can use Fabric APIs* and *Copilot / AI* enabled — needed by `Pipe_Setup` and the Data Agent ([root README](../README.md)).
- **Email‑alert connection (OAuth2, one‑time, portal)** — the `Pipe_SendEmailAlert` pipeline (Operations Agent alerts, `RTI_010`) uses the **Office 365 Outlook “Send an email”** activity, which sends **from a mailbox** and therefore needs an **OAuth2** connection. It **can’t** be created from a notebook or from a Service Principal (an SP connection tests as *Online* but the activity fails with “Failed to load the connection”). Create it **once** in the portal — see [root README → Prerequisites](../README.md) item 5. `RTI_010` then auto‑detects and reuses it.

## Identities and permissions

This solution uses **two separate Entra identities** — they are not interchangeable, and each is created and configured differently.

| | **Notebook SPN** (provisioning) | **App SPA** (runtime sign-in) |
|---|---|---|
| Entra app type | Confidential client — **has a secret** | Public client / **single-page app — no secret** |
| Auth mode | **App-only** (client credentials) | **Delegated** (the signed-in user's token) |
| Used by | `Pipe_Setup` → `RTI_001` / `RTI_011` notebooks | The browser app → Eventhouse, STID GraphQL, Fabric REST |
| Secret storage | **Azure Key Vault** (3 secrets) | none — no secret is ever stored |
| Who provisions it | Platform/security admin, **before** Step 1 | Deployer, **once** (portal or `az`), then Step 8 configures it |
| Recorded in repo | KV secret **names** in `Pipe_Setup` params (not values) | `RAYFIN_PUBLIC_AAD_CLIENT_ID` in `rayfin/.env` |

### A. Notebook SPN (pre-provision first)

The pipeline only asks for **Key Vault coordinates** (vault URI + three secret *names*), so the SPN and its secrets must already exist. One-time, by an admin who can register apps and manage the vault:

1. **Register an Entra app** (or reuse one) and create a **client secret**.
2. Store three secrets in the vault — e.g. `tenantid`, `clientid`, `clientsecret` (values = the SPN's tenant id, application/client id, and client secret).
3. Grant the SPN **Key Vault Secrets User** (Get) on those secrets, **Contributor** on the Fabric workspace, and add it to the **"Service principals can use Fabric APIs"** allowed group. Full list + private-endpoint note: [root README → Prerequisites](../README.md).
4. Enter the vault URI + the three secret **names** into the `Pipe_Setup` parameters (Step 1). Notebooks read the secret *values* at run time via `notebookutils.credentials.getSecret` — **no secret enters the repo**.

### B. App SPA (created once, then automated)

The browser app signs the **user** in through a delegated SPA registration (no secret). Creating that registration is the one manual step; Step 8 (`npm run setup-live-auth`) automates everything after it.

**Create the SPA** — `az` one-liner or portal:

```powershell
az ad app create --display-name "Hydro Operations Fabric Client" --sign-in-audience AzureADMyOrg --query appId -o tsv
```

…or Entra portal → **App registrations → New registration** → name it, *Accounts in this organizational directory only* → **Register**. Copy the **Application (client) ID** into `rayfin/.env` → `RAYFIN_PUBLIC_AAD_CLIENT_ID` (Step 3). Creating an app registration needs **Application Administrator** (or tenant self-service app registration enabled).

**Step 8 then configures that app automatically:** SPA redirect URIs, the two delegated permissions, and admin consent.

**If the tenant blocks the script** (missing role or restricted consent), it prints the exact action and continues — complete these in the Entra portal on that app registration:

1. **Authentication → Add a platform → Single-page application** → add every hosting origin from `rayfin/rayfin.yml` (`allowedRedirectUris`) **and** `http://localhost:5173`. *Fixes AADSTS50011.* Needs **Application Administrator** on the app.
2. **API permissions → Add a permission → APIs my organization uses** → add these **Delegated** scopes:
   - **Azure Data Explorer** → `user_impersonation` — Eventhouse telemetry (resource app id `2746ea77-4702-4b45-80ca-3c97e680e8b7`).
   - **Power BI Service** → `GraphQLApi.Execute.All` — STID Lakehouse GraphQL (resource app id `00000009-0000-0000-c000-000000000000`).

   *Fixes AADSTS650057.*
3. **Grant admin consent** for the directory (the *Grant admin consent* button). *Fixes AADSTS65001.* Needs **Privileged Role Administrator / Global Administrator**. If you can't and **user consent is allowed**, each user is prompted to consent on first sign-in instead.
4. The Fabric REST scopes (`Workspace.Read.All`, `Item.Execute.All`) are **consented in-app** on first use, so they need no pre-grant — optionally add them under **Microsoft Fabric** to pre-consent.

Two per-cluster grants stay manual either way: give the signed-in user **KQL Database Viewer** on the Eventhouse, and allow the app origin in the Eventhouse cluster's **CORS** settings.

## 1. Build the RTI Fabric environment (in Fabric)

Open **`01_Pipe_Setup`** in your workspace, fill its parameters ([root README](../README.md)), and
run it. This creates the Lakehouse, Eventhouse, ontology, dashboard, and agents. It does **not**
create the STID GraphQL API or seed the operational SQL DB — those happen in Step 7.

> **Email alerts (one‑time):** for the Operations Agent’s email alerts to send, create an **OAuth2
> Office 365 Outlook** connection once in the portal — *Settings → Manage connections and gateways →
> Connections → **+ New** → type **Office 365 Outlook** → auth **OAuth 2.0** → **Sign in** with a
> mailbox‑enabled **shared/service** account → name it **`RTI_Office365_EmailAlert`** → Create.*
> `RTI_010` reuses it automatically (by that name, else any OAuth2 Outlook connection); a **Service
> Principal** connection won’t work (“Failed to load the connection”). Full steps and rationale:
> [root README → Prerequisites](../README.md).

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

Where the signed‑in identity lacks a role, the script **prints the exact manual action and continues** —
complete those on the app registration in the Entra portal (see [Identities and permissions → App SPA](#b-app-spa-created-once-then-automated)).

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
