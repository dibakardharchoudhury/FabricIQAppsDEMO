# Workspace reset — automated Git sync (alternative to the manual portal steps)

Populate (or wipe-and-repopulate) a Microsoft Fabric workspace from this GitHub repo
**without clicking through the Fabric portal**. These tools drive the same Fabric
Git-integration APIs the portal uses, so the result is identical to a manual sync —
just scripted and repeatable from CI or a fresh machine. Git synchronization can remove generated
resources: a prior full `PreferRemote` import did so in V3, and setup restored them. For the current
readiness correction, use only bounded notebook-definition updates, not another full import/reset.

This automation does **not** replace the ontology-managed graph portal prerequisite: in the selected
v2 ontology choose **Manage graph → select eligible entities/relationships → Continue → Materialize**.
Set `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` JSON (`workspaceId`, `ontologyId`, `graphModelId`) for that
graph; optional `nodeTypes`/`edgeTypes` map queryable aliases to exact ontology type names/IDs.
No published ontology-owned materialization REST endpoint or ownership metadata association is
established. Do not infer ownership from names, a sole graph, or structure, or promise unattended
graph deployment. The graph canvas/tree/scopes use native topology, not GraphQL/STID fabrication.
See [native graph prerequisites and validation](../../docs/knowledge-graph.md).

Two ways to run everything:

- **CLI** — scripts for Git sync, pipeline execution, Rayfin app deployment, and deletion.
- **Local web UI** — `webapp/server.py` serves a zero-build page that runs the
  workflows and streams live progress.
- **Full workflow** — the local web UI can run GitHub sync, `01_Pipe_Setup`, and
  the approved Fabric app deploy orchestrator in one monitored serial job.

## Easiest start — just launch it (no commands to type)

One-time setup on the machine: install **[Python](https://www.python.org/downloads/)**
(3.11 or later; the local web UI uses `datetime.UTC`)
(on Windows, tick *"Add python.exe to PATH"*), the
**[Azure CLI](https://aka.ms/installazurecli)**, and **[Node.js](https://nodejs.org/)**
(which includes npm and npx; needed only for **Deploy app**).

Then launch the web app:

- **Windows** — double-click **`Start Fabric Demo.cmd`**.
- **macOS** — double-click **`start-fabric-demo.sh`** (first time you may need to make
  it runnable: in Terminal run `chmod +x start-fabric-demo.sh`), or run
  `bash start-fabric-demo.sh`.
- **Linux** — run `bash start-fabric-demo.sh` (or `chmod +x start-fabric-demo.sh`
  once, then `./start-fabric-demo.sh`).
- **Any OS** — the shims above are thin wrappers around one cross-platform launcher,
  so you can equally run `python launch.py` (Windows) / `python3 launch.py` (macOS/Linux).

The launcher installs missing **Python packages**. The **Deploy app** action separately restores
the locked npm packages (including Rayfin) under Node 24. It signs you in to Azure if needed
(`az login` opens your browser), starts the app, and **opens your browser at
`http://127.0.0.1:5000`** automatically. Keep the window open while you use it; close
it (or press Ctrl+C) to stop.

## What manual sync this replaces

In the Fabric portal the manual flow is:

1. **Workspace → Settings → Git integration** → connect to GitHub, pick the repo,
   branch, and directory, providing a Git credential.
2. **Source control → Update all** to import every item into the workspace.
3. (Optionally) **Disconnect** so the workspace is populated but no longer Git-linked.

`sync_workspace_from_git.py` performs exactly that cycle
(`connect → initializeConnection (PreferRemote) → updateFromGit → disconnect`) over
the REST API. Because Git integration mirrors folders, items land in same-named
workspace folders (`Notebooks/`, `Orchestrator_Pipelines/`, …) and **reuses any empty
folders that already exist** — which also clears the "empty folder that won't delete"
situation.

## Prerequisites

The setup pipeline and **Run pipeline** form have no Data Agent or Operations Agent mode flags.
The project always attempts agent provisioning against Ontology v2; existing v1 items are rejected
rather than reused. The original Data Agent, Operations Agents, separate playbooks/actions, Teams,
and email-alert provisioning are retained. Real failures propagate through RTI_009/010/011 and
setup. Product support and execution must still be checked in the target tenant; restored
implementation is not live certification. See
[ontology generation policies](../../README.md#ontology-generations-and-optional-agents).

Healthy Data Agent completion is **deployment `ready`, publication `published`, runtime
`verified`**, with generation `2`; publication-only success is not reported as readiness. The real NB09 smoke
check requests the selected ontology's first five facility IDs/names, compares exact records with
independently read Lakehouse rows, and does not put expected values in the prompt. Multi-source
agents are allowed, with SQL/custom content and selections retained and no cached proof required.
Evidence is a source-specific functional smoke test; **execution provenance is not attested**, and
SQL/combined-source answers are not certified. Failed or inconclusive checks fail, except for the
exact temporary `This API version is not supported for the specified Ontology item` response after
verified publication. That response is recorded as `known_product_limitation`, not readiness.
NB10 leaves both Operations Agents stopped; configuration does not prove delivery.

The existing `ops_agent_copy_playbook=false` setting remains supported for the ontology-backed
agent when an operator chooses portal generation. The default remains `true`. RTI_010 also deploys
the Eventhouse-backed agent with its separately generated and verified OPCUAEvents KQL playbook:
one combined BAD/UNCERTAIN rule, the same parameterized Teams destination, and the same
`Pipe_SendEmailAlert` action. The KQL rule enriches each `OPCUAEvents` row from the existing
`silver_instruments` OneLake external table by `opcua_node_id`, binding `equipment_id`,
`facility_id`, and `unit` along with the event value, quality, and timestamp. This does not alter
the app's telemetry path.
Both agents remain stopped by default, and API/readback failures never cause the notebook to
silently remove the playbook, Teams delivery, or actions.

NB11 extends a live Data Agent with the operational SQL Database independently of NB09's
ontology result. No ontology lookup, attachment, generation check, or runtime proof is required.
Existing sources, custom settings, instructions, and selections are retained. Exact SQL-source
draft and published readback is required; publication is not runtime verification. Missing agents
and real SQL/API failures still fail the always-attempted extension. NB11
writes only its SQL-source status/reason, leaving NB09's deployment/publication/runtime results
untouched. Independent SQL/GraphQL results are preserved before required failure is raised.

The authorized live correction updated only NB09/NB11/setup notebook definitions, preserving live
metadata, attachments, configure/parameter cells, and unrelated parts. Only NB09 executed once:
`b1818634-f77e-4dec-ab55-982460ecb101` correctly **failed** on the actual unsupported Ontology API
semantic error despite HTTP 200 / `isError: false`. Delta readback confirmed deployment/runtime
`failed`, publication/SQL-source `published`, and enabled mode. All five SQL tables, all five
ontology elements, and custom content remained intact in draft and published definitions; a
subsequent SQL-only MCP query returned `{"workOrderCount":12}`. **At that historical validation,
full setup and NB11 were not rerun after the then-new gate. The NB11 gate has since been removed
and the independent SQL extension validated offline only.** That live record proves NB09
required-failure handling and preservation, not healthy ontology runtime or a live rerun of
the current NB11. See [the full live record](../../docs/knowledge-graph.md#authorized-live-nb09-failure-path-validation).

- **`az login`** first (optionally `az login --tenant <tenant>`). Both scripts use
  your current Azure CLI sign-in to get a Fabric token — whoever you are signed in as
  is the identity that performs the changes.
- **Workspace Admin** on the target workspace (required to connect / sync / delete).
- **Fabric tenant settings** (Admin portal → Tenant settings). Each is a hard 403
  `FeatureNotAvailable` when off, and only a Fabric administrator can change them:
  - **Enable Fabric App Items (preview)** (`AppBackendTenant`) — required by **Deploy app**;
    without it `rayfin up` cannot create the `AppBackend` item.
  - **Users can sync workspace items with GitHub repositories** (`GitHubTenantSettings`) —
    required by the Git sync. The generic *Users can synchronize workspace items with their Git
    repositories* switch is **not** sufficient; GitHub needs its own.
- **Capacity region.** *Fabric App (preview)* is unavailable in several regions — including
  West US 3, East US 2, UK South and North Europe — and the workspace must sit on a capacity in a
  supported one. A capacity's region is fixed at creation, so an unsupported one must be replaced.
  Sweden Central supports Fabric App together with Ontology, Digital twin builder and the
  Operations agent, which is what the notebooks in this repo need. See
  [Fabric region availability](https://learn.microsoft.com/fabric/admin/region-availability).
- **Workspace Admin** is also required to create a Fabric managed private endpoint
  during the pipeline Key Vault preflight. The Azure subscription must have the
  `Microsoft.Network` resource provider registered.
- Read access to the target Key Vault resource and its private endpoint connections.
  When private connectivity is needed, the signed-in identity must also be able to
  approve Key Vault private endpoint connections (for example, Key Vault Contributor,
  Contributor, or a custom role containing the required private-endpoint-connection
  read/write and approval actions). If approval is not authorized, the Fabric request
  remains pending and the pipeline does not start.
- **Python deps:** `python -m pip install -r requirements.txt`
  (`azure-identity`, `requests`, `flask`).
- **Node.js/npm/npx available on PATH** for app deployment. The launcher does not globally install
  machine software. **Deploy app** reuses a cached Node 24 runtime and the repository's existing
  `node_modules` when their lockfile fingerprint and top-level dependency tree validate. It runs
  `npm ci` from the checked-in `package-lock.json` only when that cache is missing, changed, or
  invalid. React, TypeScript, Vite, and Rayfin therefore need no separate/global installation.
  Internet/proxy access to npm and write access to `HydroOperationsApp/node_modules` are required
  only when dependencies must be restored.
- Permission to create an Entra app registration when the target tenant does not already
  contain `Hydro Operations Fabric Client`. This display name is the default discovery/creation
  convention, not an Entra requirement; override it with `HYDRO_SPA_DISPLAY_NAME`. The tenant's
  actual application/client ID is discovered dynamically or supplied in the Deploy app form.
  The deploy action reuses the existing SPA
  registration when exactly one is present. No admin role is needed when the tenant leaves
  *Users can register applications* enabled (`allowedToCreateApps`), because the creator becomes
  the app's owner and owners may set SPA redirect URIs and delegated API permissions themselves.
- Tenant-wide admin consent is **optional**. Every scope the app requests is user-consentable
  (Power BI `GraphQLApi.Execute.All`, `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All`
  and Azure Data Explorer `user_impersonation` are all `type: User`), so where the tenant permits
  user consent each person simply accepts a one-time prompt at first sign-in. Granting consent for
  the whole directory just suppresses that prompt and requires **Application Administrator** or
  **Cloud Application Administrator** — not Global Administrator. Without it the Fabric resources
  can still be created, but the orchestrator does not print `SUCCESS` until the intended user or an
  administrator grants consent and the same deployment is rerun.
- **GitHub PAT** for the sync (unless you reuse an existing connection) with **`repo`**
  scope (classic) or fine-grained **Contents: Read** on the repo. The PAT is never a
  CLI flag and never logged — it comes from an env var or a hidden prompt.

## Configuration (CLI flag > env var > interactive prompt)

Every value can come from a flag, an environment variable, or an interactive prompt —
in that order of precedence. Run either script with no arguments to be prompted for
everything required.

| Env var | Used by | Meaning |
| --- | --- | --- |
| `FABRIC_TENANT` | both | Tenant id (GUID) or domain, e.g. `contoso.onmicrosoft.com` |
| `FABRIC_WORKSPACE` | both | Workspace GUID or display name |
| `FABRIC_REPOSITORY` | sync | GitHub repo as `owner/repo` (or a bare repo name) |
| `FABRIC_OWNER` | sync | GitHub owner/org (optional if `FABRIC_REPOSITORY` includes it) |
| `FABRIC_BRANCH` | sync | Branch to sync from (default `main`) |
| `FABRIC_DIRECTORY` | sync | Repo directory mapped to the workspace root (default `/`) |
| `FABRIC_CONNECTION_ID` | sync | Reuse an existing Fabric GitHub connection instead of creating one |
| `FABRIC_GIT_PAT` / `GITHUB_PAT` | sync | GitHub PAT (only if not reusing a connection) |

## Run the setup pipeline and Key Vault preflight

`01_Pipe_Setup` chains the `RTI_*` notebooks, and nearly all of them call
`notebookutils.credentials.getSecret(key_vault_uri, ...)` to load a service principal. **A Key
Vault holding that principal is a hard prerequisite** — create one with three secrets whose
default names are `tenantid`, `clientid` and `clientsecret`, give the identity that starts the
pipeline permission to read them (for example *Key Vault Secrets User*), and add the service
principal to the workspace as Contributor or Admin. The tenant must also leave *Service principals
can call Fabric public APIs* enabled.

Weather provisioning also imports and publishes the `Weather` Fabric Environment with a
NumPy-1-compatible pinned set of `pandas`, `xarray`, `shapely`, `pyproj`, `adlfs`, `zarr` and
`numcodecs`. The Git sync verifies that
`Weather_001_create_lakehouse`, `Weather_002_fetch_area_weather`,
`Weather_003_fetch_ukmet`, and `Weather_020_area_calculations` are in the workspace `Notebooks`
folder and binds all four to the project Lakehouse. The fetch notebooks read
`mai-weather-api-key`, `ukmet-global-spot-api-key`, and `ukmet-land-observations-api-key` from the
same Key Vault passed to `Pipe_Setup`. The provisioner prints these names but intentionally never
requests, reads, or stores their values.

Provisioning also creates `03_Pipe_Weather` with its UTC schedule disabled. Stage 2 enables the
six-hour schedule only after every setup activity, including Weather schema initialization,
succeeds. The schedule aligns to the 00/06/12/18 UTC model runs both vendors derive from. Each run executes
Aurora, UKMet, and then `Weather_020_area_calculations`. The final stage rebuilds area metrics
separately for every vendor and forecast type, enforces retention, and refreshes the wide serving
tables the app reads. Downstream activities depend on `Succeeded`, so a failed ingestion stops the
pipeline before later ingestion or aggregation can publish incomplete results. Rerunning the provisioner reuses a matching schedule or repairs the existing
schedule instead of adding duplicates.

The pipeline's declared parameter defaults point at the tenant it was authored in, so
**every environment-specific parameter must be overridden** — at minimum `workspace_id`,
`key_vault_uri`. Keep the chosen `env_suffix` consistent with app discovery/configuration; example
suffixes such as `V6` and `V9` are environment names, not ontology generations. Use a fresh workspace
or unused suffix when an existing target ontology is v1: setup rejects it without migration.
`ops_agent_run_as_user`, `ops_agent_teams_team_id`, and `ops_agent_teams_channel_id` configure the
restored Operations Agent run-as identity and Teams delivery. `alert_email_to` separately configures
the `Pipe_SendEmailAlert` recipient and is required by the local launcher. A direct RTI_010 run
falls back to the configured run-as user, or the deploying user when both fields are blank. Email alerts
also require the Office 365 Outlook OAuth2 connection described in the root README. Review status/reasons and
verify actual playbook execution and delivery separately from app-deployment success.

The **Run pipeline** tab accepts either the target workspace display name or GUID in
the sidebar. Its workspace parameter mirrors that value, and the runner resolves it
to the canonical GUID before starting `01_Pipe_Setup`.

The **Full workflow** tab composes the existing actions without replacing their
implementations. It is self-contained: only the target tenant and workspace are
shared from the sidebar; Git, pipeline, and app-deployment values are entered on
the Full workflow screen itself.

1. **Sync from GitHub** using its own repository, branch, directory, reusable
   connection/PAT settings, and connection test.
2. **Run `01_Pipe_Setup`** using its own complete parameter set.
3. **Deploy the Fabric app** using `deploy_fabric_app.py` and its own optional
   SPA client ID.

The individual Sync, Pipeline, and Deploy tabs remain available for running or
troubleshooting a single component; their values are not prerequisites for a
Full workflow run.

The steps run strictly in order under one exclusive job. Each child keeps its
existing validation, timeout, environment-only secrets, and live output. If a
step exits unsuccessfully, the combined log identifies the failed component and
records that later steps were not started. The progress display exposes detailed
Sync, Pipeline, and Deploy subphases, and **Cancel running jobs** terminates the
currently active child process.

Before the pipeline starts, the app resolves the Key Vault URI against Azure resources
visible to the current `az login` identity. If public network access is enabled with a
default allow rule, no endpoint is created. Otherwise the app reuses or creates a Fabric
managed private endpoint for the Key Vault `vault` subresource, approves the matching
Key Vault connection when authorized, and waits until Fabric reports it ready. This
preflight reads network metadata only; it never reads or logs Key Vault secret values.

Creating the Fabric endpoint and approving the Azure connection are separate control-plane
operations. If the current identity lacks Azure approval rights, approve the pending request
under **Key Vault > Networking > Private endpoint connections**, then run the action again.

## Sync a workspace from Git

Fully interactive — prompts for everything:

```powershell
az login
python sync_workspace_from_git.py
```

Non-interactive with flags (PAT still comes from `FABRIC_GIT_PAT`/`GITHUB_PAT` or a
hidden prompt — never a flag):

```powershell
python sync_workspace_from_git.py `
  --tenant <tenant-guid> `
  --workspace <workspace-guid-or-name> `
  --owner dibakardharchoudhury --repository FabricOntologyHydro `
  --branch main --directory / --yes
```

Fully env-driven (keeps the PAT out of shell history):

```powershell
$env:FABRIC_TENANT     = "<tenant-guid>"
$env:FABRIC_WORKSPACE  = "<workspace-guid-or-name>"
$env:FABRIC_REPOSITORY = "dibakardharchoudhury/FabricOntologyHydro"
$env:FABRIC_GIT_PAT    = (Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText)
python sync_workspace_from_git.py --yes
```

When no reusable connection exists, the script creates one named
`FabricOntologyDemo_<UTC timestamp>` from the PAT. A successful sync retains that connection;
the next run can discover and reuse it without requesting the PAT. Failed newly created
connections are deleted. Pass `--connection-id` (or `FABRIC_CONNECTION_ID`) to select a specific
existing connection. Pass `--keep-connected` to leave the workspace Git-linked after the sync;
otherwise only the workspace link is removed and the credential connection remains available.

Flags: `--tenant --workspace --connection-id --owner --repository --branch --directory --yes --keep-connected`.

## Delete every item in a workspace (start clean)

Destructive and effectively irreversible — by default it lists what it will delete
and asks you to confirm. `--tenant` and `--workspace` are required (flag or env var).

```powershell
az login
$env:FABRIC_TENANT = "<tenant-guid>"; $env:FABRIC_WORKSPACE = "<workspace>"
python delete_workspace_items.py --dry-run    # preview only
python delete_workspace_items.py --yes        # actually delete
```

Flags: `--tenant --workspace --yes --dry-run`.

## Deploy the Hydro Operations Fabric app

The **Deploy app** tab runs the complete Rayfin application deployment against the
tenant and workspace selected in the sidebar:

1. Reuse the validated locked npm dependencies under Node 24 (restoring them only when missing or
  invalid), validate the active Azure CLI tenant, and resolve the exact workspace GUID/name. If the
  selected identity is missing from the Azure CLI token cache or its token is stale, deployment opens
  tenant-scoped Microsoft sign-in and retains that recovery in an isolated per-tenant cache for later
  deploys. Missing Node/npm/npx is reported with an install link.
2. Reuse the tenant's `Hydro Operations Fabric Client` SPA, create it when absent,
  or use the optional client ID entered in the form. If discovery, reuse, or creation is blocked,
  stop before changing Rayfin state and print an administrator handoff. A deployment never ships
  with an empty SPA client ID.
3. Reuse matching active Rayfin state only when its generated API URL targets the workspace's
  current capacity. A workspace/capacity/tenant mismatch moves only the three Rayfin state files
  into a unique temporary backup, then generates and validates a fresh ignored `rayfin/.env`.
  Existing state is not deleted.
4. Sign Rayfin into the target tenant, provision the AppBackend and SQL schema, build
   and deploy static hosting, apply the generated hosting origin to backend auth, then always
   reapply AppBackend runtime/CORS settings and the DAB/SQL configuration. This also runs for an
   unchanged hosting origin so managed-service restarts cannot leave stale runtime settings.
5. Run `npm run setup-live-auth` for SPA redirects, delegated ADX/Fabric permissions,
  and consent, then verify the live app returns HTML over HTTP 200. The final hard checks ensure
  the generated API URL targets the current capacity/workspace/AppBackend and send
  browser-equivalent CORS preflights to `/graphql` and `/api/auth/v1/token`. Backend warm-up is
  retried with bounded backoff. The checks also send a minimal GraphQL query and a deliberately
  incomplete token request; missing CORS headers, GraphQL failure, or a persistent token HTTP 5xx
  fails deployment.
  The final check also verifies the SPA redirect, every delegated scope, and its consent grant from
  Microsoft Graph. Missing SPA access, redirects, permissions, or consent blocks `SUCCESS` because
  browser sign-in and live Fabric data would otherwise be unavailable.
6. Leave generated hosting-origin changes local. The web UI never commits or pushes Git changes.
  For an intentional CLI-driven persistence step, use `--push-config`; it requires a clean
  checkout and refuses divergent or unpushed local commits.

Rayfin may open a browser account picker during the job. The setup pipeline and app
deployment remain separate actions so the pipeline's Key Vault and Teams parameters
can be reviewed before execution.

Deployment is exclusive: while it runs, the local server rejects sync, delete,
pipeline, login, and additional deploy jobs with HTTP 409. The server accepts only
loopback clients and validates local Host/Origin headers to prevent remote use,
cross-origin invocation, and DNS-rebinding through a non-local host name.

The same workflow is available from the CLI:

```powershell
python deploy_fabric_app.py `
  --tenant <tenant-guid> `
  --workspace <workspace-guid-or-name> `
  --push-config
```

Add `--client-id <spa-app-guid>` when more than one matching SPA registration exists.

### If SPA automation fails: Entra administrator handoff

The required registration is the single-tenant, no-secret SPA **`Hydro Operations Fabric Client`**.
The Fabric AppBackend and static host may already exist when registration configuration needs an
administrator, but the job remains failed and does not print `SUCCESS`. Give the generated hosting
origin and printed handoff to the tenant administrator, then rerun the same deployment after they
complete these actions:

The **app registration** defines the client ID, redirect URIs, and requested delegated scopes. Its
tenant-local **enterprise application/service principal** stores the actual delegated consent
grants. The SPA still runs as the signed-in user, has no secret, and the service principal does not
need a Fabric workspace role. Without the service principal, consent cannot be recorded and
`az ad sp show --id <spa-client-id>` returns “does not exist.”

The permission rows alone do not prove consent. In the app registration's **API permissions** page,
the **Status** column must show **Granted for &lt;tenant&gt;**. Blank Status means the scopes are only
configured, not consented. A disabled **Grant admin consent** button means the current administrator
lacks a consent-granting directory role. A per-user grant for one person does not authorize others.

Step 3 is only necessary where the tenant blocks user consent. All five scopes are user-consentable,
so otherwise each person consents for themselves at first sign-in and the app works without it.

1. **Application Administrator / Cloud Application Administrator:** create the registration if it
  does not exist, ensure its **enterprise application/service principal** exists, and add a
  **Single-page application** platform containing the generated
  `https://<host>.webapp.fabricapps.net` origin plus `http://localhost:5173`. CLI equivalent for
  the missing enterprise application: `az ad sp create --id <spa-client-id>`.
2. **Application Administrator / Cloud Application Administrator:** add delegated Azure Data
  Explorer `user_impersonation` and Power BI Service `GraphQLApi.Execute.All`,
  `Workspace.Read.All`, `Item.Read.All`, and `Item.Execute.All`.
3. **Application Administrator / Cloud Application Administrator:** select **Grant admin consent
  for the directory**. None of these scopes is directory-privileged, so Global Administrator and
  Privileged Role Administrator are not required. Per-user `Principal` consent works only for that
  user; suppressing the prompt for everyone requires tenant-wide `AllPrincipals` consent.
4. Enter the administrator-provided Application (client) ID in the local app's optional **SPA
  client id** field and redeploy, or put it in `rayfin/.env` and run `npm run setup-live-auth`.
  The workflow is idempotent and validates redirects, scopes, and consent after redeployment.

KQL Database Viewer is granted to the signed-in **user**, not this service principal. Eventhouse
CORS separately allows the deployed hosting origin; neither setting is an OAuth consent grant.

Preview the concrete redirects and scopes without writing changes:

```powershell
cd ..\..\HydroOperationsApp
npm run setup-live-auth:dry
```

See the authoritative detailed procedure in
[HydroOperationsApp/DEPLOY.md → No admin rights?](../../HydroOperationsApp/DEPLOY.md#no-admin-rights-hand-this-to-your-entra-admin).

## Web UI (zero build)

```powershell
python -m pip install -r requirements.txt
az login
python webapp\server.py     # open http://127.0.0.1:5000
```

The page has actions for Git sync, setup-pipeline execution, Hydro Operations app
deployment, and workspace deletion, each with a live progress bar, phase checklist,
and streaming log. The PAT is typed into a password field and passed only to the child
process' environment — it is never sent to a CLI flag, logged, or returned. The server
binds to `127.0.0.1` only. The delete form requires re-typing the workspace name to
confirm and defaults to dry-run.

## Security notes

- The PAT is never a command-line argument, never printed, and never returned by the
  web API — only via env var, hidden prompt, or the UI password field.
- Auto-created connections are retained after a successful sync so Fabric can securely reuse the
  stored credential. A newly created connection is deleted when its sync fails.
- Delete is guarded: confirmation prompt (CLI) / type-to-confirm + dry-run default (UI).
