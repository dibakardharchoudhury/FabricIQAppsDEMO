# Copilot instructions — FabricIQAppsDEMO

## Deploying HydroOperationsApp (Rayfin) — don't reinvent the wheel
This repo already automates deployment. Before doing ANY deploy / new-tenant / redeploy work:
- From the repository root, use this as the **only agent deployment command**:
  `python Raw/workspace-reset/deploy_fabric_app.py --tenant <tenant> --workspace <workspace> --push-config`.
  Add `--client-id <guid>` only when discovery is ambiguous or the user provides one. Do not run
  bare `npm run deploy`, `rayfin up`, or a hand-assembled sequence instead.
- **Follow [HydroOperationsApp/DEPLOY.md](../HydroOperationsApp/DEPLOY.md)** (the numbered steps +
  the "Redeploying to a different tenant, workspace, or region" section).
- For a fresh tenant/workspace/region, follow the **`/deploy-fresh-tenant`** prompt
  ([.github/prompts/deploy-fresh-tenant.prompt.md](prompts/deploy-fresh-tenant.prompt.md)).
- **Golden rule:** SPA redirect URIs, delegated permissions, and admin consent are all done by
  **`npm run setup-live-auth`** (idempotent). Do NOT add redirect URIs / API permissions / grant
  consent by hand in the Entra portal, and do NOT hand-edit `rayfin/rayfin.yml`
  `allowedRedirectUris`. Before deploying, preserve every redirect currently registered in Entra;
  after deploying, add only the current hosting origin. Never remove an existing Entra SPA redirect
  and never recreate a historical origin merely because it remains in local configuration.
  Only fall back to manual portal steps for the exact action the script prints it lacks a role for.
- Before any build or deploy, run **`npm run validate-env`**. Never deploy with an empty
  `RAYFIN_PUBLIC_AAD_CLIENT_ID`; stop before changing Rayfin state and request the tenant SPA id.
- Never delete deployment state. When changing targets, move only `rayfin/.env`,
  `rayfin/.env.local`, and `rayfin/.deployments.json` into a uniquely created temporary backup.
- Never reuse or hardcode a generated `pbidedicated.windows.net` endpoint across workspaces,
  capacities, regions, or tenants. The orchestrator compares saved state with the target
  workspace's current capacity and rotates stale state automatically.
- A routine same-workspace deploy is not static-only: the orchestrator must reapply AppBackend
  runtime/CORS settings and DAB/SQL configuration, then validate browser-equivalent preflights for
  `/graphql` and `/api/auth/v1/token`. Missing CORS headers or a stale endpoint is a hard failure;
  do not bypass it or report success from the hosted HTML page alone.
- The only genuinely manual step is creating the SPA app registration (`az ad app create`), because
  an app registration is tenant-scoped.

## Node 24 wrapper (Windows)
The one-shot Python orchestrator resolves/downloads Node 24 and invokes the repository-local Rayfin
CLI itself. Agents must not wrap or reconstruct those commands.

## Git
`main` is wired to Fabric git integration — `git fetch` and merge any Fabric commit-back before
pushing. Commit AND push after changes. `rayfin/.env*` and `.deployments.json*` are gitignored.
