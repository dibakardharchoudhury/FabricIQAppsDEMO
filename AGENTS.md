# Agent instructions

## Ontology v2 documentation and implementation contract

- This project is v2-only: require live `properties.generation == 2`; never restore v1
  authoring, parsing, agent sources, or embedded playbooks. Existing v1 items need a separately
  planned replacement, not silent reuse or destructive migration.
- Fresh ontology creation uses TMDL database/model/default-namespace parts. Preserve existing
  bindings and unknown parts on reruns, and verify readback before publication.
- REST `/v1`, definition schema versions, and `env_suffix` values such as `V9` are not
  ontology generations. Do not globally rename them to `v2`.
- Treat optional agent status separately from core setup: Data Agent `auto` reports the rollout
  block; `enabled` attempts matching v2 draft/published sources. Operations Agent automation is
  blocked pending a verified v2 playbook contract; `enabled` fails before writes.
- SQL/GraphQL setup remains independent. The app uses a v2 contract-guided GraphQL/KQL/SQL view,
  not an implicitly discovered native GraphModel. Do not claim agent or graph runtime readiness
  from item creation, notebook completion, or successful app hosting.
- Keep canonical notebook prose, Raw mirrors, and packaged workflow documents aligned with
  the [current policy](README.md#ontology-generations-and-optional-agents).

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
URL, checks browser preflights and POST readiness for `/graphql` and
`/api/auth/v1/token`, and persists the current generated hosting origin.

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
- A supplied `--client-id` must resolve to an app registration in the target tenant; never
  continue with an unverified GUID.
- A deployment is complete only when the orchestrator prints `SUCCESS` and
  `DEPLOYED_APP_URL`, after its endpoint-contract and AppBackend CORS checks pass.

For a fresh tenant, the orchestrator attempts to discover or create the configurable
SPA display-name convention (`HYDRO_SPA_DISPLAY_NAME`, default
`Hydro Operations Fabric Client`). App registration creation or tenant-wide consent
may still require an Entra administrator.
