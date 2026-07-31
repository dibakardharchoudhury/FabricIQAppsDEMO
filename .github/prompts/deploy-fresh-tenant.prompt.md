---
mode: agent
description: Canonical runbook for deploying the Hydro Operations app (Rayfin) to a FRESH Fabric tenant / workspace / region. Follow the repo scripts — do NOT hand-crank SPA registration, consent, or redirect URIs.
---

# Deploy Hydro Operations to a fresh tenant / workspace / region

You are deploying (or re-deploying) `HydroOperationsApp` via **Rayfin** into a new Fabric
tenant, workspace, or region. This repo already automates almost everything. **Follow the
existing scripts and docs — do not reinvent the wheel or click through the Entra portal by
hand when a script does it.**

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
`rayfin/rayfin.yml` `allowedRedirectUris`. `rayfin up` / `rayfin deploy` record the current
hosting origin into `rayfin.yml`, and `setup-live-auth` reads it from there. Only fall back to
manual portal steps for the exact action the script prints it lacks a role to perform.

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
   origins from `rayfin/rayfin.yml`, then registers `localhost:5173` + each hosting origin and
   grants ADX `user_impersonation` + Power BI `GraphQLApi.Execute.All`. If it lacks a role it
   prints the exact portal action and continues.

7. **Two per-cluster grants that stay manual** (no API for them here): give the signed-in user
   **KQL Database Viewer** on the Eventhouse, and add the app origin to the Eventhouse cluster's
   **CORS** allow-list.

8. **Finish** with DEPLOY.md Steps 1 (`01_Pipe_Setup` in Fabric), 7 (seed SQL + wire GraphQL via
   `RTI_011`), 8 (already covered by `setup-live-auth`), 9 (start the OPC-UA stream). A brand-new
   workspace is EMPTY of RTI artifacts, so live telemetry/STID panels stay blank until
   `01_Pipe_Setup` and the stream run.

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
| npm **EUSAGE** | Nested `npx` inside `-c` → call `rayfin`/`npm run` directly. |
| "Project name not found in rayfin.yml" | `-c` shell ran from repo root → embed `cd /d …\HydroOperationsApp &&`. |
| Connect popup **AADSTS50011** | Redirect URI missing → `npm run setup-live-auth` (don't add by hand). |
| **AADSTS650057** / **AADSTS65001** | Missing delegated perm / consent → `npm run setup-live-auth`. |
| `az` **AADSTS90072** | `az` in the wrong tenant → `az login --tenant <TENANT_GUID> --allow-no-subscriptions`. |

## Git note
`main` is wired to Fabric git integration → `git fetch` and merge any Fabric commit-back BEFORE
pushing. `rayfin/.env*` and `.deployments.json*` are gitignored — never commit them.
