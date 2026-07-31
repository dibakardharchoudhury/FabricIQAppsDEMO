# Hydro Operations — Deployment Guide

<!-- markdownlint-disable MD029 MD033 MD060 -->

Deploy the Hydro Operations app to Microsoft Fabric. Run every command from
`HydroOperationsApp/` on **Node 24** (if your default Node differs, prefix with
`npx -y -p node@24 -c "<cmd>"`). Architecture: [README.md](README.md) · [root README](../README.md).

**Path:** build RTI env → install → configure → provision → deploy → seed & provision → live auth → start stream.

## Which scenario am I in?

Find your row — it tells you exactly what to run. A workspace always belongs to one tenant, so
“new tenant” means its workspaces are new to you as well. The only two things that change between
scenarios are **whether the SPA app registration already exists** (app regs are tenant‑scoped) and
**whether Rayfin's local state must be reset** (when the target workspace changes).

| Your situation | SPA app registration | Local Rayfin state | Do this |
|---|---|---|---|
| **First‑ever deploy — new tenant, new workspace** | **Create** it once — [§App SPA](#b-app-spa-created-once-then-automated) | fresh (nothing to reset) | Run **Steps 1–9** in order. |
| **Existing tenant, new / different workspace or capacity region** | **Reuse** the existing `RAYFIN_PUBLIC_AAD_CLIENT_ID` — don't recreate | **Reset** — [Redeploying §1](#1-reset-local-rayfin-state) | Reset → re‑point `.env` → [Redeploying §4](#4-provision-non-interactively) (`rayfin up --workspace-id <guid> --yes`) → **Steps 5–9**. |
| **Different tenant** (move the whole app elsewhere) | **Create** a new SPA in that tenant — [§App SPA](#b-app-spa-created-once-then-automated) | **Reset** — [Redeploying §1](#1-reset-local-rayfin-state) | Reset → create SPA → new `.env` → [Redeploying §2–4](#redeploying-to-a-different-tenant-workspace-or-region) → **Steps 5–9**. |
| **Same tenant, same workspace — iterating on code** | already set up | keep as‑is | Just `npm run deploy`. If the hosting hostname changed, also re‑run `npm run setup-live-auth` (Step 8). |

> **Agents:** the canonical automated runbook is
> [`.github/prompts/deploy-fresh-tenant.prompt.md`](../.github/prompts/deploy-fresh-tenant.prompt.md).
> The golden rule everywhere: **SPA redirect URIs, delegated permissions, and admin consent are done
> by `npm run setup-live-auth` — never by hand in the Entra portal.**

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
   - **Power BI Service** (resource app id `00000009-0000-0000-c000-000000000000`) → `GraphQLApi.Execute.All` (STID Lakehouse GraphQL), `Workspace.Read.All` (workspace item discovery), **`Item.Read.All`** (read the Eventhouse query URI — **required for live telemetry**; without it the app reports “No Eventhouse found”), and `Item.Execute.All` (run notebooks/pipelines from the app).

   *Fixes AADSTS650057.*
3. **Grant admin consent** for the directory (the *Grant admin consent* button). *Fixes AADSTS65001.* Needs **Privileged Role Administrator / Global Administrator**. If you can't and **user consent is allowed**, each user is prompted to consent on first sign-in instead.

`setup-live-auth` normally grants **all** of the above automatically (tenant‑wide, `AllPrincipals`), so **no in‑app consent popup appears** — do these by hand only for the exact grant the script prints it couldn't make.

Two per-cluster grants stay manual either way: give the signed-in user **KQL Database Viewer** on the Eventhouse, and allow the app origin in the Eventhouse cluster's **CORS** settings.

#### No admin rights? Hand this to your Entra admin

If you can't register apps or grant consent, none of the above blocks you — a **directory admin does it once**, then the whole rest of the deploy is yours (no admin needed again unless the app registration must change). Generate the exact, copy‑pasteable list first:

```powershell
npm run setup-live-auth:dry   # writes nothing — prints every URI/scope/consent it WOULD apply
```

Send your admin this checklist (the dry run prints the concrete values for each `<…>`):

| # | Action | On | Role the admin needs |
|---|---|---|---|
| 1 | **Create** the SPA app registration (single‑tenant, no secret) and give you the **Application (client) ID** | Entra ID → App registrations | **Application Administrator** (or self‑service app registration enabled) |
| 2 | **Authentication → Single‑page application** → add every origin from `rayfin/rayfin.yml` `allowedRedirectUris` **+** `http://localhost:5173` | that app | **Application Administrator** on the app (or make you an **Owner** — then you can do 2 yourself) |
| 3 | **API permissions** → add the delegated scopes from the list above (ADX `user_impersonation`; Power BI `GraphQLApi.Execute.All` + `Workspace.Read.All` + `Item.Read.All` + `Item.Execute.All`) | that app | **Application Administrator** on the app |
| 4 | **Grant admin consent** for the directory | that app | **Privileged Role Administrator / Global Administrator** |

Fastest split of duties: ask the admin to do **1 and 4** and make you an **Owner** of the app — then you run `npm run setup-live-auth` yourself and it applies **2 and 3** (redirect URIs + permissions) with no further admin involvement. If the admin does all four by hand, you never run `setup-live-auth`; just make sure the hosting origins in step 2 match `rayfin/rayfin.yml` after each deploy that changes the hostname. If admin consent (step 4) is impossible but **user consent is allowed** in the tenant, skip it — each signer is prompted to consent on their first sign‑in instead.

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
app (fixes **AADSTS50011**); (2) adds **Azure Data Explorer** `user_impersonation` and the
**Power BI Service / Microsoft Fabric** scopes `GraphQLApi.Execute.All`, `Workspace.Read.All`,
**`Item.Read.All`** (needed for live telemetry — the Eventhouse query URI), and `Item.Execute.All`,
then grants admin consent tenant‑wide (fixes **AADSTS650057 / 65001**). Because these are pre‑granted,
**no in‑app consent popup appears** on Seed & provision or Connect telemetry.

Where the signed‑in identity lacks a role, the script **prints the exact manual action and continues** —
complete those on the app registration in the Entra portal (see [Identities and permissions → App SPA](#b-app-spa-created-once-then-automated)).

Two things stay manual (per user/cluster): grant the signed‑in user **KQL Database Viewer** on the
Eventhouse, and allow the app origin in the Eventhouse cluster's **CORS** settings.

## 9. Start the telemetry stream

Run **`02_Pipe_Stream`** in Fabric (or click **"Start stream"** in the app) to push an OPC UA burst
into the Eventhouse. Live gauges populate once telemetry lands and Step 8 auth is in place.

## Redeploying to a different tenant, workspace, or region

Moving the app to a **new tenant, workspace, or capacity** requires resetting Rayfin's per-workspace
state and re-pointing every tenant-scoped identity — otherwise a stale `active` deployment pointer
makes `rayfin up` target the old (now non-existent) workspace and fail with a 404.

### 1. Reset local Rayfin state

```powershell
# from HydroOperationsApp/
Move-Item   rayfin/.env rayfin/.env.<old>-old -Force                 # back up the old-tenant env
Remove-Item rayfin/.deployments.json, rayfin/.env.local -ErrorAction SilentlyContinue
```

- `rayfin/.deployments.json` holds an `active` pointer to the previous workspace/backend. Left in
  place, `rayfin up` calls the **old** endpoint and fails with **404 "The provided workspace was not
  found."** Delete it (and `.env.local`) when switching tenants.
- Recreate `rayfin/.env` from `.env.example` with the **new** `FABRIC_WORKSPACE_NAME`,
  `RAYFIN_PUBLIC_WORKSPACE_ID`, `RAYFIN_PUBLIC_TENANT_ID`, and the new tenant's SPA
  `RAYFIN_PUBLIC_AAD_CLIENT_ID`. Resolve the workspace GUID by display name:

  ```powershell
  $tok = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
  (Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/workspaces" -Headers @{Authorization="Bearer $tok"}).value |
    Where-Object displayName -like '*<workspace-name>*' | Select-Object displayName,id,capacityId | Format-List
  ```

### 2. Register a fresh SPA in the new tenant

> **Same tenant, only a different workspace/region?** The SPA app registration is **tenant‑scoped**, so
> **reuse the existing `RAYFIN_PUBLIC_AAD_CLIENT_ID`** — do **not** create a new one. Keep it (and
> `RAYFIN_PUBLIC_TENANT_ID`) in the new `.env`; only `FABRIC_WORKSPACE_NAME` + `RAYFIN_PUBLIC_WORKSPACE_ID`
> change. `npm run setup-live-auth` then just **adds the new hosting origin** to the existing app and
> re‑confirms consent (already `AllPrincipals`, so it's a no‑op). Skip the `az ad app create` below.

A **different tenant** is the only case that needs a new app. App registrations are tenant-scoped — the
old client id won't work. Create one (see
[Identities → App SPA](#b-app-spa-created-once-then-automated)) and put its id in the new `.env`:

```powershell
az login --tenant <new-tenant-guid> --allow-no-subscriptions
az ad app create --display-name "Hydro Operations Fabric Client" --sign-in-audience AzureADMyOrg --query appId -o tsv
```

### 3. Point Rayfin at the new tenant

```powershell
npx rayfin logout
npx rayfin login --select     # pick the NEW tenant
npx rayfin login status       # confirm tenant + user before deploying
```

### 4. Provision non-interactively

Pass the workspace **GUID** and auto-accept so `rayfin up` never stops on the interactive
*"Enter a Fabric workspace name"* prompt (its redraw UI is easy to mis-answer when scripted):

```powershell
rayfin up --workspace-id <workspace-guid> --yes
```

`rayfin up --help` also exposes `--workspace <name>`, `--workspace-uri <portal-url>`, `--tenant <id>`,
`--dry-run`, and `--exclude-services staticHosting`. To repoint an **existing** deployment record
without re-provisioning, use `rayfin switch <workspace-name>` (it rewrites `rayfin/.env`).

Then continue at **Step 5** (`npm run rayfin:db`) → **Step 6** (`npm run deploy`) → update
`rayfin.yml` `allowedRedirectUris` with the new hosting URL → `npm run up` → **Steps 7–9**.

### Node 24 wrapper — gotchas

If your default Node isn't 24, wrap **every** command; call the binary/script **directly** inside `-c`:

```powershell
npx -y -p node@24 -c "npm run up"                              # ✅
npx -y -p node@24 -c "rayfin up --workspace-id <guid> --yes"  # ✅
```

- **Don't nest npx.** `npx -y -p node@24 -c "npx rayfin …"` fails with an npm **EUSAGE** error.
- The `-c` string runs in its **own shell at an unspecified cwd**. If it can't find `rayfin.yml`,
  put the directory inside the string: `-c "cd /d <abs-path>\HydroOperationsApp && rayfin up …"`.

### Feature & region gating (Fabric App Items preview)

Rayfin's backend is a **Fabric App Item** (preview). Creating it can fail with:

```text
Fabric API error: 403 Forbidden — The feature is not available
```

Work through these in order:

1. **Tenant setting** — Admin portal → **Tenant settings → Microsoft Fabric → "Enable Fabric App
   Items (preview)"** must be **On**. Verify it via the admin API (setting name `AppBackendTenant`):

   ```powershell
   $tok = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
   (Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/admin/tenantsettings" -Headers @{Authorization="Bearer $tok"}).tenantSettings |
     Where-Object settingName -eq 'AppBackendTenant' | Select-Object settingName,title,enabled
   ```

2. **Propagation** — after enabling, allow **~15 minutes** for the setting to take effect before
   retrying. A create call issued too soon still sees the feature as disabled and returns `403`.
3. **Region** — the preview is **not offered in every region**. If it keeps returning `403` while the
   tenant setting reads `enabled = True`, host the workspace on a capacity in a **supported region**
   (this solution has deployed successfully on **Sweden Central**; **North Europe** was rejected as
   "feature not available"). List capacities + regions, activate one in a supported region, then
   reassign the workspace (portal → **Workspace settings → License/Capacity**) and re-run `rayfin up`:

   ```powershell
   $tok = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
   (Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/capacities" -Headers @{Authorization="Bearer $tok"}).value |
     Select-Object displayName,sku,region,state | Sort-Object region | Format-Table -AutoSize
   ```

## Troubleshooting

| Problem | Fix |
| --- | --- |
| **"System cancelled the Spark session"** running RTI_011 | Its lakehouse binding is stale — re‑import RTI_011 (Fabric **source control → Update**) or re‑run `RTI_001`, then retry. |
| **"No GraphQL API found"** / STID panels empty | Run **Seed & provision** (Step 7). If STEP B reports auto‑bind failed, bind the STID tables in the portal. |
| Live signals stay empty | `02_Pipe_Stream` must have run (Step 9) **and** Step 8 live‑auth must be in place. |
| **`rayfin up`/`deploy` → static deploy `401 Unauthorized`** (backend + DB apply succeed) | Rayfin's cached Fabric token is stale/expired. `npx rayfin login --select` (pick the target tenant), then retry just the static step: `rayfin up staticapp deploy`. The backend was already provisioned, so **don't re‑run the full `up`**. |
| **Consent popup on Step 2 (Seed & provision)** | Should **not** appear anymore — `setup-live-auth` now pre‑grants all Fabric REST scopes (`GraphQLApi.Execute.All`, `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All`) AllPrincipals (tenant‑wide) on the Power BI Service resource. If you still see it (edge‑cached config), click **Accept** once; it's harmless. |
| **Connect telemetry → "No Eventhouse found in the workspace"** (STID/GraphQL works) | **Root cause (proven): an OAuth scope gap, not RBAC.** Discovery reads the Eventhouse's `queryServiceUri` via `GET /v1/workspaces/{ws}/eventhouses/{id}`, which needs **`Item.Read.All`** (or `Eventhouse.Read.All`). Without it the call returns **403 InsufficientScopes** and the app reports "No Eventhouse found" — even for a workspace admin (admin RBAC ≠ token scope). `List Items` (used to find STID's GraphQL) only needs `Workspace.Read.All`, which is why STID works but telemetry doesn't. **Fix:** the app now requests `Item.Read.All` (`src/services/fabric.ts` `FABRIC_SCOPES`) and `setup-live-auth` pre‑grants it — so **redeploy** (`npm run deploy`) *and* run `npm run setup-live-auth`. Then hard‑refresh (Ctrl+F5). If telemetry connects but shows no data, ensure the signed‑in user has **KQL Database Viewer** on the Eventhouse and the app origin is in the Eventhouse **CORS** allow‑list. |
| Sign‑in fails with **AADSTS** | Ensure your deployed hosting URL is in `rayfin/rayfin.yml` (`allowedRedirectUris`), then run `npm run setup-live-auth` (after `az login`). 50011 = redirect URI; 650057 = missing permission; 65001 = no consent. Hard‑refresh (Ctrl+F5) after. |
| KQL reachable but "no live readings" | F12 → Console: `HTTP 401` = re‑connect for the cluster scope; `HTTP 403` = grant **KQL Database Viewer**; network error with no status = **CORS** not allowing the app origin. |
| Operational writes fail with **Internal server error** | An unbounded `@text()` column maps to `NVARCHAR(MAX)`, which some ops reject. Bound it in `rayfin/data/schema.ts` and re‑run `npm run rayfin:db`. |
| Deployed into the wrong workspace | `npm run up` reads `FABRIC_WORKSPACE_NAME` from `rayfin/.env` — fix it and re‑run. |
| **`rayfin up` → 404 "The provided workspace was not found"** | Stale `active` pointer in `rayfin/.deployments.json` from a previous tenant — delete it (and `.env.local`), then re‑run. See [Redeploying to a different tenant, workspace, or region](#redeploying-to-a-different-tenant-workspace-or-region). |
| **`rayfin up` → 403 "The feature is not available"** | **Fabric App Items (preview)** not enabled/propagated, or the capacity's region doesn't support it. Enable tenant setting `AppBackendTenant`, wait ~15 min, else move the workspace to a supported‑region capacity (e.g. **Sweden Central**). See [Feature & region gating](#feature--region-gating-fabric-app-items-preview). |
| **`npx … -c "npx rayfin …"` → npm EUSAGE** | Don't nest `npx`. Call `rayfin` / `npm run …` **directly** inside the `-c` string. |
| `rayfin up` can't find `rayfin.yml` (wrong cwd) | The `-c` shell starts at an unspecified cwd — put the path in the string: `-c "cd /d <abs>\HydroOperationsApp && rayfin up …"`. |

## Reset

Delete `rayfin/.env`, `rayfin/.env.local`, and `rayfin/.deployments.json`, then redo from Step 3.
A fresh build gets a new hosting hostname — add it to `rayfin/rayfin.yml` (`allowedRedirectUris`) and
re‑run `npm run up`, then `npm run setup-live-auth` (Step 8) after the first `npm run deploy`.

> Switching **tenant/workspace/region** (not just rebuilding)? Follow
> [Redeploying to a different tenant, workspace, or region](#redeploying-to-a-different-tenant-workspace-or-region)
> — it covers resetting the `active` deployment pointer, re‑registering the SPA in the new tenant, and
> the non‑interactive `rayfin up --workspace-id <guid> --yes` command.
