# Copilot instructions — FabricOntologyHydro

## Deploying HydroOperationsApp (Rayfin) — don't reinvent the wheel
This repo already automates deployment. Before doing ANY deploy / new-tenant / redeploy work:
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
- The only genuinely manual step is creating the SPA app registration (`az ad app create`), because
  an app registration is tenant-scoped.

## Node 24 wrapper (Windows)
App pins `node >=24 <25`. Prefix commands: `npx -y -p node@24 -c "<cmd>"`. Inside `-c`: never nest
`npx`; embed `cd /d C:\DBA\VSCodeRepo\FabricOntologyHydro\HydroOperationsApp && …` (the `-c` shell
starts at an unspecified cwd).

## Git
`main` is wired to Fabric git integration — `git fetch` and merge any Fabric commit-back before
pushing. Commit AND push after changes. `rayfin/.env*` and `.deployments.json*` are gitignored.
