# Copilot instructions — FabricIQAppsDEMO

## Ontology v2 only

Use the [v2-only contract](../README.md#ontology-generations-and-optional-agents) for code and docs.
Require live numeric generation 2, explicit TMDL authoring, safe binding preservation, and verified
readback. Never restore generation-1 ontology or agent-source fallbacks. Retain the original
Operations Agent playbook/actions capabilities against the verified v2 ontology. REST `/v1`, app layout names,
and environment suffixes are not ontology generations.

Agent modes default to `enabled`; backward-compatible `auto` also attempts actual provisioning,
and only explicit `disabled` opts out. Preserve Data Agent, Operations Agent, playbook/actions,
Teams, email-alert provisioning, and standalone `event_time` binding. Required failures propagate
through RTI_009/010/011 and setup. Data Agent sources require matching verified v2 identity.
SQL/GraphQL-only success requires explicit Data Agent disablement; configuration or hosted-page
success does not certify agent execution or delivery. The app's native graph
requires the selected ontology's **Manage graph → select eligible entities/relationships → Continue
→ Materialize** flow and explicit `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` JSON. No ownership is inferred
from names, sole-graph discovery, or structure. Native topology is authoritative; KQL/SQL only enrich
actual native entities. GraphQL/STID is not a canvas/tree/scope dependency or topology fallback.
No published ontology-owned materialization REST endpoint is established; keep this portal
prerequisite separate from automated deployment and agent runtime readiness.

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
  `/graphql` and `/api/auth/v1/token` plus real POST readiness. Missing CORS headers, GraphQL
  failures, token HTTP 5xx responses, or a stale endpoint is a hard failure; do not bypass it or
  report success from the hosted HTML page alone.
- Treat an explicit `--client-id` that cannot be verified in the target tenant as a hard
  pre-deployment failure. Never publish a bundle with an unverified SPA GUID.
- SPA app registration is tenant-scoped and may require administrator action. Ontology-managed
  graph materialization is a separate manual portal prerequisite; do not promise unattended graph deployment.

## Node 24 wrapper (Windows)
The one-shot Python orchestrator resolves/downloads Node 24 and invokes the repository-local Rayfin
CLI itself. Agents must not wrap or reconstruct those commands.

## Git
`main` is wired to Fabric git integration — `git fetch` and merge any Fabric commit-back before
pushing. Commit AND push after changes. `rayfin/.env*` and `.deployments.json*` are gitignored.
