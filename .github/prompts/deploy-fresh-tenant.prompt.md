---
mode: agent
description: Canonical runbook for deploying the Hydro Operations app (Rayfin) to a FRESH Fabric tenant / workspace / region. Follow the repo scripts — do NOT hand-crank SPA registration, consent, or redirect URIs.
---

# Deploy Hydro Operations to a fresh tenant / workspace / region

You are deploying (or re-deploying) `HydroOperationsApp` via **Rayfin** into a new Fabric
tenant, workspace, or region. This repo already automates almost everything. **Follow the
existing scripts and docs — do not reinvent the wheel or click through the Entra portal by
hand when a script does it.**

## Mandatory one-shot command

From the repository root, deploy through the orchestrator only:

```powershell
python Raw/workspace-reset/deploy_fabric_app.py `
  --tenant <TENANT_GUID_OR_DOMAIN> `
  --workspace <WORKSPACE_GUID_OR_NAME> `
  --push-config
```

Add `--client-id <SPA_APP_GUID>` only when SPA discovery is ambiguous or the user supplied one.
Do not run the individual commands later in this document as a replacement deployment flow; they
explain what the orchestrator owns and are retained for diagnosis or administrator handoff. Success
requires both `DEPLOYED_APP_URL=<url>` and `SUCCESS: Hydro Operations is live at <url>`.

## Which scenario (pick one)
- **New / different tenant** → run the one-shot command; the orchestrator discovers or creates the SPA.
- **Same tenant, new workspace or region** → run the one-shot command; it reuses the tenant SPA and
  safely rotates only mismatched local Rayfin state into a temporary backup.
- **Same tenant, same workspace** → run the same one-shot command; it reuses the healthy backend and
  performs a static-only update before revalidating auth and the hosted page.

## Source of truth — READ THESE FIRST, then follow them
- [HydroOperationsApp/DEPLOY.md](../../HydroOperationsApp/DEPLOY.md) — the 9-step guide + the
  "Redeploying to a different tenant, workspace, or region" section + Troubleshooting.
- [HydroOperationsApp/README.md](../../HydroOperationsApp/README.md) — architecture & data model.
- [HydroOperationsApp/scripts/setup-live-auth.mjs](../../HydroOperationsApp/scripts/setup-live-auth.mjs)
  — the idempotent script that configures the SPA (redirect URIs + delegated permissions +
  admin consent). Read its header comment; it explains every AADSTS error it prevents.

## The ONE golden rule
SPA **redirect URIs**, **delegated permissions**, and **admin consent** are all done by the
one-shot orchestrator through the idempotent `setup-live-auth` script. **Do NOT** add redirect URIs, add
API permissions, or grant consent manually in the Entra portal, and **do NOT** hand-edit
`rayfin/rayfin.yml` `allowedRedirectUris`. Snapshot and preserve every redirect currently registered
in Entra, then add only the hosting origin produced by the current deployment. Never remove an
existing Entra SPA redirect or recreate an origin found only in stale local configuration.
`setup-live-auth` applies that preserved set. Only fall back to
manual portal steps for the exact action the script prints it lacks a role to perform.

> **Do not hide an Entra authorization failure.** The required single-tenant SPA is **`Hydro
> Operations Fabric Client`**. If the operator cannot create or identify it, stop before changing
> Rayfin state and print the administrator handoff from DEPLOY.md. Never publish a bundle with an
> empty `RAYFIN_PUBLIC_AAD_CLIENT_ID`. An
> **Application Administrator / Cloud Application
> Administrator** must create the app and its enterprise application/service principal, then add
> SPA redirects/delegated permissions and grant tenant-wide admin consent (Global Administrator is
> not required — none of the scopes is directory-privileged, and consent is optional altogether
> where the tenant allows user consent). The orchestrator prints the concrete administrator handoff.
> After the admin supplies the client ID, rerun the one-shot command with `--client-id <SPA_APP_GUID>`.

## Node 24 (Windows)
The orchestrator resolves Node 24, sets the application working directory, and invokes the local
Rayfin CLI. Agents must not wrap, nest, or reconstruct its Node/npm/Rayfin commands.

## Orchestrator-owned phases (do not run separately)

1. **(Re-deploy only) Rotate local Rayfin state.** Move only `rayfin/.env`,
   `rayfin/.env.local`, and `rayfin/.deployments.json` into a uniquely created temporary backup.
   Do not delete files or directories. A stale `active` pointer in `.deployments.json` makes
   `rayfin up` **404 "workspace not found"** against the old endpoint.

2. **Resolve the tenant SPA.** Reuse an explicit/configured client ID, discover the configurable
  display name, or create a public single-tenant SPA when permissions allow. No client secret is used.

3. **Fill `rayfin/.env`** (copy from `.env.example`) — four values, no secrets:
   `FABRIC_WORKSPACE_NAME`, `RAYFIN_PUBLIC_WORKSPACE_ID` (workspace GUID),
   `RAYFIN_PUBLIC_AAD_CLIENT_ID` (the appId from step 2), `RAYFIN_PUBLIC_TENANT_ID`.
   Resolve the workspace GUID by name via `GET https://api.fabric.microsoft.com/v1/workspaces`.
  Run `npm run validate-env` and do not continue unless it passes.

4. **Authenticate to the target tenant.** Verify the active Azure CLI tenant and acquire the Fabric
  token; when the local MSAL token is missing or stale, perform one tenant-scoped login and retry.

5. **Provision non-interactively.** Pass the resolved workspace ID to Rayfin, apply the SQL schema,
  and deploy static hosting. For an unchanged healthy target, reuse the backend and update static
  hosting only. The resulting `*.webapp.fabricapps.net` origin is recorded in `rayfin.yml`.

6. **Configure the SPA through the orchestrator, not by clicking:**
   It reads `RAYFIN_PUBLIC_AAD_CLIENT_ID` / `TENANT_ID` from `rayfin/.env` and the hosting
  current origin from `rayfin/rayfin.yml`, then preserves all existing Entra redirects and adds it
  plus `localhost:5173`; it never removes an existing redirect
  Fabric-hosting origins, and
   grants ADX `user_impersonation` + the Power BI / Microsoft Fabric scopes
   `GraphQLApi.Execute.All`, `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All`
   (all `AllPrincipals`, tenant-wide). `Item.Read.All` is what lets the app read the Eventhouse
   query URI — without it live telemetry fails with "No Eventhouse found". If it lacks a role it
  prints the exact portal action and continues. **If the user has no admin rights at all**, point them to
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
  do not create another registration. Let the orchestrator retain the client ID and update only the
  target workspace values.
- Let the orchestrator rotate mismatched local state, preserve every Entra SPA redirect, add only the
  new hosting origin, and re-confirm consent.
- The orchestrator verifies the SPA redirects and delegated grants: Power BI
  `GraphQLApi.Execute.All` + `Workspace.Read.All` +
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
| `rayfin up` 404 "workspace not found" | Run the one-shot orchestrator. It moves stale state into a unique temporary backup and regenerates the target configuration without deleting it. |
| `rayfin up` 403 "feature is not available" | AppBackendTenant off/propagating, or region-gated → verify setting; move to Sweden Central. |
| `rayfin up`/`deploy` static step **401 Unauthorized** | Rerun the one-shot command. Its tenant-scoped Azure CLI recovery retries a recognized missing/stale token once. |
| Consent popup on **Seed & provision** / **Connect telemetry** | Should NOT appear — `setup-live-auth` pre-grants all Fabric scopes tenant-wide. If it does (edge-cached config), click **Accept**; harmless. |
| **Connect telemetry → "No Eventhouse found"** (STID works) | Token lacks **`Item.Read.All`** → `GET /eventhouses/{id}` returns **403 InsufficientScopes**. Rerun the one-shot command so deployment and live-auth validation remain atomic. RBAC admin ≠ OAuth scope. |
| Deployed to the wrong workspace (e.g. a `*Test` ws) | Rerun the one-shot command with the intended workspace name or GUID. |
| npm **EUSAGE** | An agent bypassed the orchestrator and nested npm/npx commands. Return to the one-shot command. |
| "Project name not found in rayfin.yml" | An agent bypassed the orchestrator and used the wrong working directory. Return to the one-shot command. |
| Connect popup **AADSTS50011** | Redirect URI missing. Rerun the one-shot command; do not add it by hand. |
| **AADSTS650057** / **AADSTS65001** | Missing delegated permission or consent. Rerun the one-shot command and follow only its administrator handoff if required. |
| `az` **AADSTS90072** | Azure CLI is authenticated to another tenant. Rerun the one-shot command with the correct `--tenant`; it never silently switches deployment targets. |

## Git note
`main` is wired to Fabric git integration → `git fetch` and merge any Fabric commit-back BEFORE
pushing. `rayfin/.env*` and `.deployments.json*` are gitignored — never commit them.
