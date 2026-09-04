---
mode: agent
description: Canonical runbook for deploying the Hydro Operations app (Rayfin) to a FRESH Fabric tenant / workspace / region. Follow the repo scripts — do NOT hand-crank SPA registration, consent, or redirect URIs.
---

# Deploy Hydro Operations to a fresh tenant / workspace / region

You are deploying (or re-deploying) `HydroOperationsApp` via **Rayfin** into a new Fabric
tenant, workspace, or region. This repo already automates almost everything. **Follow the
existing scripts and docs — do not reinvent the wheel or click through the Entra portal by
hand when a script does it.**

## Which scenario (pick one)
- **New / different tenant** → create a new SPA (step 2) + reset local state (step 1) → full **Canonical flow** below.
- **Same tenant, new workspace or region** → **reuse** the SPA client id, reset local state, read
  [Same tenant, different workspace/region](#same-tenant-different-workspaceregion), then run Canonical-flow steps 3–8.
- **Same tenant, same workspace (iterating on code)** → no reset, no SPA change: `npm run deploy`;
  re-run `npm run setup-live-auth` only if the hosting hostname changed.

## Source of truth — READ THESE FIRST, then follow them
- [HydroOperationsApp/DEPLOY.md](../../HydroOperationsApp/DEPLOY.md) — the 9-step guide + the
  "Redeploying to a different tenant, workspace, or region" section + Troubleshooting.
- [HydroOperationsApp/README.md](../../HydroOperationsApp/README.md) — architecture & data model.
- [HydroOperationsApp/scripts/setup-live-auth.mjs](../../HydroOperationsApp/scripts/setup-live-auth.mjs)
  — the idempotent script that configures the SPA (redirect URIs + delegated permissions +
  admin consent). Read its header comment; it explains every AADSTS error it prevents.

## The ONE golden rule
SPA **redirect URIs**, **delegated permissions**, and **admin consent** are all done by
`npm run setup-live-auth` (STEP 1 + STEP 2 in that script). **Do NOT** add redirect URIs, add
API permissions, or grant consent manually in the Entra portal, and **do NOT** hand-edit
`rayfin/rayfin.yml` `allowedRedirectUris`. Snapshot and preserve every redirect currently registered
in Entra, then add only the hosting origin produced by the current deployment. Never remove an
existing Entra SPA redirect or recreate an origin found only in stale local configuration.
`setup-live-auth` applies that preserved set. Only fall back to
manual portal steps for the exact action the script prints it lacks a role to perform.

> **Do not hide an Entra authorization failure.** The required single-tenant SPA is **`Hydro
> Operations Fabric Client`**. If the operator cannot create/configure it, continue the Fabric
> AppBackend and static-host deployment, report degraded-auth warnings, and print the administrator
> handoff from DEPLOY.md. Do not report browser sign-in or live Fabric data as ready. An
> **Application Administrator / Cloud Application
> Administrator** must create the app and its enterprise application/service principal, then add
> SPA redirects/delegated permissions and grant tenant-wide admin consent (Global Administrator is
> not required — none of the scopes is directory-privileged, and consent is optional altogether
> where the tenant allows user consent). Run
> `npm run setup-live-auth:dry` to print the concrete hosting origins and scopes. After the admin
> supplies the client ID, set `RAYFIN_PUBLIC_AAD_CLIENT_ID` and rerun the idempotent live-auth flow.

## Node 24 wrapper (Windows) — gotchas
Default Node here is newer than the app's pin (`>=24 <25`). Prefix commands:
`npx -y -p node@24 -c "<cmd>"`. Inside `-c`:
- **Don't nest npx** (`-c "npx rayfin …"` → npm EUSAGE). Call `rayfin` / `npm run` directly.
- The `-c` shell starts at an **unspecified cwd**; embed the cd:
  `-c "cd /d C:\DBA\VSCodeRepo\FabricOntologyHydro\HydroOperationsApp && rayfin up …"`.

## Canonical flow (fresh / new-tenant)

1. **(Re-deploy only) Reset local Rayfin state.** Back up + delete `rayfin/.env`,
   `rayfin/.env.local`, `rayfin/.deployments.json`. A stale `active` pointer in
   `.deployments.json` makes `rayfin up` **404 "workspace not found"** against the old endpoint.

2. **Create the SPA app registration** (the only genuinely manual step — an app reg is
   tenant-scoped). Make sure `az` is in the TARGET tenant first:
   ```powershell
   az login --tenant <TENANT_GUID> --allow-no-subscriptions
   az ad app create --display-name "Hydro Operations Fabric Client" --sign-in-audience AzureADMyOrg --query appId -o tsv
   ```
   (No secret — it's a public SPA. Needs Application Administrator or self-service app reg.)

3. **Fill `rayfin/.env`** (copy from `.env.example`) — four values, no secrets:
   `FABRIC_WORKSPACE_NAME`, `RAYFIN_PUBLIC_WORKSPACE_ID` (workspace GUID),
   `RAYFIN_PUBLIC_AAD_CLIENT_ID` (the appId from step 2), `RAYFIN_PUBLIC_TENANT_ID`.
   Resolve the workspace GUID by name via `GET https://api.fabric.microsoft.com/v1/workspaces`.

4. **Point Rayfin at the tenant:** `rayfin logout` → `rayfin login --select` (pick the tenant
   that owns the workspace) → `rayfin login status`.

5. **Provision non-interactively** (the interactive "Enter a Fabric workspace name" TUI redraw
   is NOT captured by the terminal tool — always pass the id):
   ```powershell
   npx -y -p node@24 -c "cd /d C:\DBA\VSCodeRepo\FabricOntologyHydro\HydroOperationsApp && rayfin up --workspace-id <WORKSPACE_GUID> --yes"
   ```
   Then `npm run rayfin:db` (SQL schema) and `npm run deploy` (static hosting — this records the
   new `*.webapp.fabricapps.net` hosting origin into `rayfin.yml`).

6. **Configure the SPA — run the script, don't click:**
   ```powershell
   az login --tenant <TENANT_GUID> --allow-no-subscriptions   # if not already
   npm run setup-live-auth          # STEP 1 redirect URIs + STEP 2 perms + consent (idempotent)
   npm run setup-live-auth:dry      # preview only; --redirect-only / --grant-only to scope
   ```
   It reads `RAYFIN_PUBLIC_AAD_CLIENT_ID` / `TENANT_ID` from `rayfin/.env` and the hosting
  current origin from `rayfin/rayfin.yml`, then preserves all existing Entra redirects and adds it
  plus `localhost:5173`; it never removes an existing redirect
  Fabric-hosting origins, and
   grants ADX `user_impersonation` + the Power BI / Microsoft Fabric scopes
   `GraphQLApi.Execute.All`, `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All`
   (all `AllPrincipals`, tenant-wide). `Item.Read.All` is what lets the app read the Eventhouse
   query URI — without it live telemetry fails with "No Eventhouse found". If it lacks a role it
   prints the exact portal action and continues. **If the user has no admin rights at all**, run
   `npm run setup-live-auth:dry` to print every URI/scope/consent, then point them to
   [DEPLOY.md → "No admin rights? Hand this to your Entra admin"](../../HydroOperationsApp/DEPLOY.md#no-admin-rights-hand-this-to-your-entra-admin)
   — fastest split: the admin creates the SPA + grants consent and makes the user an **Owner**, then
   `setup-live-auth` applies redirect URIs + permissions with no further admin involvement.

7. **Two per-cluster grants that stay manual** (no API for them here): give the signed-in user
   **KQL Database Viewer** on the Eventhouse, and add the app origin to the Eventhouse cluster's
   **CORS** allow-list. These only matter once `01_Pipe_Setup` has created the Eventhouse.

8. **Finish** with DEPLOY.md Steps 1 (`01_Pipe_Setup` in Fabric), 7 (seed SQL + wire GraphQL via
   `RTI_011`), 8 (already covered by `setup-live-auth`), 9 (start the OPC-UA stream). A brand-new
   workspace is EMPTY of RTI artifacts, so live telemetry/STID panels stay blank until
   `01_Pipe_Setup` and the stream run.
   - **No in-app consent popup** should appear on **Seed & provision** or **Connect telemetry** —
     `setup-live-auth` pre-grants all Fabric REST scopes (`GraphQLApi.Execute.All`,
     `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All`) tenant-wide. If one still shows
     (Entra edge-cached the config for a minute or two), tell the user to click **Accept**; it's
     harmless, NOT an error.

## Same tenant, different workspace/region (the common redeploy)
If the target is the SAME tenant as a prior deploy (only the workspace or capacity region changed):
- **REUSE the existing SPA** `RAYFIN_PUBLIC_AAD_CLIENT_ID` — an app registration is tenant-scoped, so
  do NOT run `az ad app create`. Keep the same client id + `RAYFIN_PUBLIC_TENANT_ID` in `.env`; only
  `FABRIC_WORKSPACE_NAME` + `RAYFIN_PUBLIC_WORKSPACE_ID` change.
- Still reset local state (step 1) and re-point `.env` to the new workspace GUID (resolve by name via
  `GET /v1/workspaces`). Preserve the SPA redirects currently registered in Entra, add only the new
  hosting origin, and re-confirm consent (already `AllPrincipals` → no-op).
- Verify the app afterwards: `az ad app show --id <appId> --query spa.redirectUris` and the SP's
  `oauth2PermissionGrants` — Power BI `GraphQLApi.Execute.All` + `Workspace.Read.All` +
  `Item.Read.All` + `Item.Execute.All`, and ADX `user_impersonation`, all as `AllPrincipals`.

## Feature & region gating (Fabric App Items preview)
`rayfin up` creating the Rayfin item needs the **"Enable Fabric App Items (preview)"** tenant
setting (`AppBackendTenant`). If it fails with **403 "The feature is not available"**:
- Verify the setting: `GET https://api.fabric.microsoft.com/v1/admin/tenantsettings` (filter
  `settingName eq 'AppBackendTenant'`, `enabled` must be true). Allow ~15 min to propagate.
- The preview is **region-gated**. Known-good: **Sweden Central**. If the workspace's capacity is
  in an unsupported region (e.g. North Europe rejected in testing), reassign the workspace to a
  Sweden Central capacity (portal → Workspace settings → License/Capacity) and retry. List
  capacities + regions: `GET https://api.fabric.microsoft.com/v1/capacities`.

## Common failures → fix
| Symptom | Cause / Fix |
|---|---|
| `rayfin up` 404 "workspace not found" | Stale `active` pointer in `.deployments.json` → delete it (step 1). |
| `rayfin up` 403 "feature is not available" | AppBackendTenant off/propagating, or region-gated → verify setting; move to Sweden Central. |
| `rayfin up`/`deploy` static step **401 Unauthorized** (backend + DB apply already succeeded) | Cached Fabric token stale → `rayfin login --select` (target tenant), then retry ONLY `rayfin up staticapp deploy`. Do NOT re-run full `up`. After it prints the new hosting URL, `rayfin up --exclude-services staticHosting --yes` to push the redirect to the backend, then `npm run setup-live-auth`. |
| Consent popup on **Seed & provision** / **Connect telemetry** | Should NOT appear — `setup-live-auth` pre-grants all Fabric scopes tenant-wide. If it does (edge-cached config), click **Accept**; harmless. |
| **Connect telemetry → "No Eventhouse found"** (STID works) | Token lacks **`Item.Read.All`** → `GET /eventhouses/{id}` returns **403 InsufficientScopes**. Fix: redeploy the app (it now requests `Item.Read.All` in `FABRIC_SCOPES`) **and** `npm run setup-live-auth` (pre-grants it). RBAC admin ≠ OAuth scope. |
| Deployed to the wrong workspace (e.g. a `*Test` ws) | `.env` `FABRIC_WORKSPACE_NAME`/`RAYFIN_PUBLIC_WORKSPACE_ID` pointed at the wrong ws → resolve the intended ws GUID by name via `GET /v1/workspaces`, fix `.env`, re-run `rayfin up --workspace-id <guid> --yes`. |
| npm **EUSAGE** | Nested `npx` inside `-c` → call `rayfin`/`npm run` directly. |
| "Project name not found in rayfin.yml" | `-c` shell ran from repo root → embed `cd /d …\HydroOperationsApp &&`. |
| Connect popup **AADSTS50011** | Redirect URI missing → `npm run setup-live-auth` (don't add by hand). |
| **AADSTS650057** / **AADSTS65001** | Missing delegated perm / consent → `npm run setup-live-auth`. |
| `az` **AADSTS90072** | `az` in the wrong tenant → `az login --tenant <TENANT_GUID> --allow-no-subscriptions`. |

## Git note
`main` is wired to Fabric git integration → `git fetch` and merge any Fabric commit-back BEFORE
pushing. `rayfin/.env*` and `.deployments.json*` are gitignored — never commit them.
