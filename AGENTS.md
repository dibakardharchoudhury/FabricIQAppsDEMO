# Agent instructions

## Hydro Operations deployment

For any request to deploy, redeploy, change tenant/workspace/region, or publish
`HydroOperationsApp`, use the repository deployment orchestrator. Do not assemble
individual Rayfin, Entra, npm, or Git commands into a custom deployment flow.

From the repository root, run exactly one deployment command:

```powershell
python Raw/workspace-reset/deploy_fabric_app.py `
  --tenant <tenant-guid-or-domain> `
  --workspace <workspace-guid-or-name> `
  --push-config
```

Add `--client-id <spa-app-guid>` only when SPA discovery is ambiguous or the user
provides a specific registration. The script dynamically resolves the workspace and
SPA, validates required configuration, restores Node 24 dependencies, reuses or
provisions Rayfin state, deploys the app, runs live-auth setup, preserves Entra SPA
redirects, verifies permissions/consent, detects capacity drift, reapplies AppBackend
runtime/CORS and SQL settings, validates the generated capacity/workspace/AppBackend
URL, checks browser preflights for `/graphql` and `/api/auth/v1/token`, and persists
the current generated hosting origin.

Mandatory rules:

- Read `HydroOperationsApp/DEPLOY.md` before deployment.
- Never deploy through bare `npm run deploy`, `rayfin up`, or hand-written Entra commands.
- Never delete, reset, or broadly clean repository files or directories.
- Never hand-edit generated `.env.local`, `.deployments.json`, or Entra redirect URIs.
- Do not replace or recreate a tenant SPA when a valid client ID can be reused.
- Never copy or hardcode a `pbidedicated.windows.net` URL. It is capacity-specific and
  must be generated for the selected workspace by the orchestrator.
- Do not work around AppBackend CORS in application code or the portal. The orchestrator
  reapplies runtime settings and treats missing CORS headers as a failed deployment.
- Stop if the orchestrator reports an unresolved prerequisite or administrator action.
- A deployment is complete only when the orchestrator prints `SUCCESS` and
  `DEPLOYED_APP_URL`, after its endpoint-contract and AppBackend CORS checks pass.

For a fresh tenant, the orchestrator attempts to discover or create the configurable
SPA display-name convention (`HYDRO_SPA_DISPLAY_NAME`, default
`Hydro Operations Fabric Client`). App registration creation or tenant-wide consent
may still require an Entra administrator.
