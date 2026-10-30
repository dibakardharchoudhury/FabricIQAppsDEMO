# Local deployment app and workspace automation

**Deploying the project? Start with the [short deployment guide](../../HydroOperationsApp/DEPLOY.md).**
For the local app, see [where to find and launch it](../../HydroOperationsApp/DEPLOY.md#2-start-the-local-app)
and [what to type in every field](../../HydroOperationsApp/DEPLOY.md#local-app-parameters-what-to-type).
This file is the detailed CLI/reference material, not a second required checklist.

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

NB04/NB06 and NB11 apply the shared, source-independent semantic catalog: entity/property
descriptions, synonyms, usage/query guidance, and scoped TMDL business rules. Existing operator
metadata, bindings, and unknown parts survive reruns; readback verifies descriptions as well as
structural settings. Rules ground consumers but do not enforce database constraints or certify
agent execution. NB06 now only discovers graph ownership: it does not rewrite a portal-selected
graph, force the five-type projection, or run refresh/GQL jobs as part of ontology binding.

The app's **Ontology model** view reads the live v2 type/relationship definitions; it does not
require materialization and does not pretend those types are individual physical assets.
NB11 declares weather-location to facility identity explicitly, using the weather producer's
`location_id = facility_id` mapping. Weather-area `location_id` comes from its persisted metadata;
forecast `location_id` and `area_id` are separate nullable keys derived from `target_kind`, never
unqualified joins on polymorphic `target_id`. Preparation verifies source identities and existing
values before binding these columns. Weather refreshes retain these keys.

Equipment and spare-parts inventory connect through an authoritative **EquipmentType** dimension.
This means inventory eligibility by category, not installed parts, reservations or verified fit for
a particular model. Work orders, inspections, notifications and 3D attachments retain their explicit
equipment/signal bindings. Variable catalogs, ingestion-run logs and aggregated instrument
classifications are no longer standalone business entity types; their physical tables remain.
Only marked, source-matching project definitions are retired. Custom annotations, added properties
or custom relationships stop curation for an explicit migration. Unknown definition parts and
compatible custom content on retained entities remain intact.

Two ways to run everything:

Both NB01 Lakehouse setup variants provision `RTI_Demo_SQLDatabase_<env_suffix>` as an
independent Fabric SQLDatabase in `RTI_DEMO_<env_suffix>`, alongside the Lakehouse
and Eventhouse. Stage 2 runs the standard initial UKMet refresh, then NB11's mandatory
`model` phase after core bindings. It inserts only missing demo rows, verifies actual SQL
asset/signal/instrument keys and weather coverage, and builds/reads back the complete
16-type/21-relationship baseline before either agent can publish. Compatible custom content
is preserved. Both agents require a successful selected-model receipt and recheck the live
complete definition. NB11's subsequent mandatory `agent` phase only verifies the same model
and publishes operational guidance after NB09; it never reseeds, rewrites the ontology or
attaches the ontology-associated SQL Database a second time.
The extension requires readable, nonempty existing agent content and preserves complete custom
instructions rather than silently truncating them to the service's 15,000-character limit.
Overflow stops before agent updates. Operational settings and SQL-publication status share
bounded retries for Delta concurrency conflicts with other setup branches; other failures propagate.

NB09 no longer runs the automatic MCP facilities smoke or blocks setup on an answer format.
It verifies publication/readback and records runtime as `not_checked`. The app's later
Supervisor → Sparky → Fabric Responses route and NB10's Teams/email actions are exercised
interactively as needed, not added as exhaustive setup checks for this synthetic-data demo.
Standalone app/deployer calls retain `setup_phase="all"`. Neither creation
nor seeding depends on an app-owned database. Existing marked `HydroOperationalStore`
items are moved without changing identity; child SQL endpoints move with them.
Fabric SQLDatabase names cannot be changed (`SQLDatabaseDisplayNameCannotBeChanged`).
Existing items keep their immutable name unless a controlled, field-verified replacement
migration is performed. A retained write-blocked archive identifies its exact replacement
and is excluded from active-source discovery only while that replacement is available.
Seed reruns preserve existing values. On an existing workspace, the app deployer's NB11 bootstrap performs an
explicit, field-verified copy/cutover from the retained app-owned database, preserves
custom Data Agent content and applies the writable `hydrooperations` connector.
Do not rerun full setup or Git import to perform this upgrade. See the
[independent-store deployment contract](../../HydroOperationsApp/DEPLOY.md#independent-operational-sql-lifecycle).

For **app deployment only**, start with the
[deployment interface chooser](../../HydroOperationsApp/DEPLOY.md#start-here-choose-your-interface-not-a-different-deployment-process).
The local **Deploy app** action and command-line deployment use the same
`deploy_fabric_app.py` orchestrator. A normal app update does not require workspace
deletion, reset, full Git import or rerunning data setup. Access/consent actions reported
as blocked and ontology graph materialization remain explicit operator prerequisites.

- **CLI** — scripts for Git sync, pipeline execution, Rayfin app deployment, and deletion.
- **Local web UI** — `webapp/server.py` serves a zero-build page that runs the
  workflows and streams live progress.
- **Full Deploy** — the local web UI runs GitHub sync, then `01_Pipe_Setup`, then one
  canonical deployment process that overlaps Energy finalization with Foundry setup.
  App publication waits for both. Each completed component is checkpointed; the
  combined component reruns idempotently if either branch or publication fails.

## Optional: up to three workspaces in parallel

In the sidebar, enable **Deploy to multiple workspaces in parallel** and enter one,
two or three workspace names/GUIDs in the same tenant. Empty slots are ignored,
including an empty first slot. Full Deploy, GitHub Sync, Core setup, Energy setup,
Foundry setup, Deploy app, Delete workspace items and connection tests fan out only
to the populated targets. Every module button shows the selected workspace count.
Deletion defaults to dry run. To actually delete items, repeat every selected
workspace name/GUID, one per line in the displayed order; the backend rejects the
entire batch unless all targets are confirmed. A failed deletion requires fresh
confirmation of that target's canonical GUID before retrying. Retrying a failed
dry run remains a dry run even if the form's checkbox has since changed.

The form settings apply to every selected workspace. Tenant setup defaults remain
available regardless of workspace name; explicit form values take precedence.
For multiple targets, leave the shared Foundry project field blank: each workspace
uses its own saved binding or convention-named project. A sole unrelated project
is not a safe fallback. Creating missing Foundry resources still requires the
existing explicit creation checkbox; parallel mode does not approve billable creation.

Each worker uses a separate checkout, Azure CLI cache, hosted-agent state and
Rayfin deployment files under the current user's private
`~/.fabric-demo/parallel-deployments/` directory. Only matching tenant/workspace
state is reused; the main checkout's generated files are not changed. Workers use
one committed source snapshot, so commit source changes first. Existing generated
changes to `HydroOperationsApp/rayfin/rayfin.yml` need not be discarded. State and
checkouts are retained, not deleted after completion.

Each workspace has its own status, timings, live output and cancellation control.
One failure does not stop the other workspaces. After the batch finishes, retry
only the failed/cancelled workspace; Full Deploy retains its successful component
checkpoints and requires explicit synchronization when definitions changed. A
server interruption is not proof that remote Fabric jobs stopped: verify those
jobs before starting another run. Cancellation stops owned local processes, not
necessarily already-submitted remote Fabric jobs.

Workspace names/GUID aliases are resolved before deployment; duplicate targets
are rejected. Shared Entra mutations and Foundry bootstrap operations are locked
across processes, and Entra redirects are reread inside the mutation lock to avoid
losing another workspace's newly published origin. Workspace pipelines, builds and
agent deployment work otherwise overlap. Fabric capacity, Spark concurrency and
Azure quotas still apply; three targets do not imply three times the throughput.

## Easiest start — just launch it (no commands to type)

One-time setup on the machine: install **[Python](https://www.python.org/downloads/)**
(3.11 or later; the local web UI uses `datetime.UTC`)
(on Windows, tick *"Add python.exe to PATH"*), the
**[Azure CLI](https://aka.ms/installazurecli)**, and Git.
Foundry setup also requires `azd`. The deployment engine resolves Node 24 and the
locked npm dependencies automatically.

Then open `Raw\workspace-reset` in your local repository checkout and launch the web app:

- **Windows** — double-click **`Start Fabric Demo.cmd`**.
- **macOS** — double-click **`start-fabric-demo.sh`** (first time you may need to make
  it runnable: in Terminal run `chmod +x start-fabric-demo.sh`), or run
  `bash start-fabric-demo.sh`.
- **Linux** — run `bash start-fabric-demo.sh` (or `chmod +x start-fabric-demo.sh`
  once, then `./start-fabric-demo.sh`).
- **Any OS** — the shims above are thin wrappers around one cross-platform launcher,
  so you can equally run `python launch.py` (Windows) / `python3 launch.py` (macOS/Linux).

The launcher installs missing **Python packages**. The **Deploy app** action separately restores
the locked npm packages (including Rayfin) under Node 24. The launcher starts the app and
**opens your browser at `http://127.0.0.1:5000`** automatically. Click **Sign in**
inside the page to authenticate with Azure. Once the launcher reports the app is
running, you can close its window; the server keeps running. Use the sidebar's
**Stop server** or **Restart server** controls, which refuse while a job is running.

For the simplest deployment, choose the target tenant/workspace, then use **Refresh
subscriptions** instead of copying a subscription GUID. The list shows tenant IDs and does
not switch Azure subscriptions. Use **Deploy app** for an existing prepared workspace; only
use **Full Deploy** when Git sync and data setup are actually needed.
**Set up Foundry agents** is a separate module before final app publication;
it owns project/model/registry settings and explicitly billable first-time
provisioning. Blank overrides use verified discovery, never another tenant's saved target.

The **Core setup** and **Full Deploy** setup forms share non-secret parameter
values and remember workspace overrides in this browser. Reusable defaults from
the same tenant populate even before a workspace is entered or when a new workspace
name is selected; an existing workspace's explicit overrides take precedence.
This includes the environment suffix, Key Vault URI and secret *names*,
Teams destinations, alert recipient, run-as guard, and timeout. It never saves
PATs, tokens, or secret values. Changing workspace names keeps the setup fields
visible while loading defaults; changing tenants does not reuse the previous
tenant's values. Returning restores explicit overrides, including intentionally
blank values. The workspace field always follows the sidebar and is never inherited
from defaults. Clearing this site's
browser storage removes remembered values.

When the target tenant GUID matches the signed-in user, an otherwise unset alert
recipient is suggested from that user's email. Review it before running; it is
independent of the optional run-as guard. Unknown Key Vault and Teams destinations
remain blank rather than being guessed.

For preconfigured local installations, start the server with `FABRIC_TENANT`,
`FABRIC_WORKSPACE`, and `FABRIC_PIPELINE_PARAMS` (a JSON object using the pipeline
parameter names). Only declared non-secret parameters are exposed, and these
defaults are available to all workspace names in the configured tenant
(case-insensitively), with matching-workspace overrides taking precedence.
Saved browser edits take precedence. Invalid configured
defaults produce a visible error, not a silently empty form. Restart the server
after changing its environment.

This machine can also keep non-secret defaults in the gitignored
`webapp/pipeline-defaults.local.json`, with `tenant`, `workspace`, and a
`parameters` object using the pipeline parameter names. These values supply defaults
within that tenant, across workspace names, browsers and server restarts. Environment
`FABRIC_PIPELINE_PARAMS` configuration takes precedence; browser edits still
override defaults. Never put tokens, PATs or credential secret values in this file.
Invalid local configuration produces a visible error. The Teams ID fields link
directly to Microsoft's channel-link documentation; see also
[how to get the IDs](../../HydroOperationsApp/DEPLOY.md#find-your-teams-team-and-channel-ids).

Foundry setup selects the **Azure subscription** first, followed by one
**Foundry project name** and **Model deployment name** for
both reuse and approved creation. Enter them once; setup configures the agents,
connections and hosted orchestrator automatically. A full project ARM ID can still
disambiguate duplicate names or scoped access. A matching subscription is selected
automatically when unambiguous. Endpoints, connected registries and ontology graph
ownership are resolved internally. Catalog name, version, deployment type,
capacity and region are not form inputs. No graph-binding JSON is required.
**Fabric App authentication** offers **Use a pre-created app registration** when
an administrator has prepared a specific target-tenant SPA. Otherwise registration
discovery/reuse or creation is automatic. Selecting this option requires a valid
Application (client) ID; unchecking it stops sending the ID, including any saved value.
This choice is shared across Foundry, full workflow and app publication, scoped to
the tenant/workspace. Registration verification and automated auth setup remain required.
The launcher preserves the target's existing work-order write policy; it does not
offer or submit a write-permission override. Enabled writes still require human approval.
**Check this box if you want to create a new Foundry project with the multi-agent
setup.** is explicit, billable approval, never remembered. It does not discard or
disable either shared name; existing projects
are reused even with approval enabled. A missing project uses the exact
project name entered above. A deployment alias is resolved to its underlying
model/version in the selected subscription; a catalog chat model name also works.
Region, model version, pay-as-you-go SKU and minimum/default capacity are automatic.
Regional Standard is preferred, followed by DataZoneStandard and GlobalStandard;
the form discloses global inference. Provisioned capacity is never automatic.
Quota, access and unresolved/ambiguous model identities fail explicitly, not by guessing.

The launcher can use a separate clean deployment checkout without overwriting local
development edits. Set `checkout` in gitignored `launcher.local.json` beside
`launch.py`. The configured checkout must have no tracked source modifications.
The launcher permits only unstaged additions of Fabric hosting origins inside
`HydroOperationsApp/rayfin/rayfin.yml`'s existing `allowedRedirectUris` list, so
generated redirects from a prior deployment do not block startup. It preserves
that file; staged edits, redirect removals and other configuration changes still
block startup, with the modified paths included in the error. This startup check
does not verify a hosting origin or bypass deployment/authentication checks. The
launcher retains the original local pipeline settings file; restarting the server
preserves that settings path. The version API reports the actual source checkout.
Relaunching waits for the previous server to release its port. Windows connection
resets during shutdown/startup are retried within bounded waits, not reported as
an unhandled traceback. A running deployment job still prevents shutdown.
Existing saved project ARM IDs migrate into the project-name field; retired browser
endpoint/registry/graph overrides are no longer submitted. Other choices are shared
between the standalone and full-workflow forms and saved per tenant/workspace. See the
[three-step quick start and defaults table](../../HydroOperationsApp/DEPLOY.md#quick-start-one-path-for-each-job).

For the merged Weather/energy-map changes, launch from an updated `main`
checkout and select **Branch = `main`** when using Git sync or
**Full Deploy**; those branch fields prefill from the current local checkout. **Deploy app**
verifies prepared Foundry specialists, including Weather, without creating or
publishing agents. It does not update Fabric notebook source or run Weather
ingestion. **Refresh energy sources** prepares PR #19 without a hidden feature
profile; routine app publication preserves the same target's map setting.
Explicit map enablement verifies the twelve live serving layers before build.
Map Data Agent chat remains a separate prerequisite. See the
[feature-branch deployment matrix and remaining prerequisites](../../HydroOperationsApp/DEPLOY.md#deploying-the-merged-feature-branch)
before treating either workflow as complete end-to-end readiness.

Every app deployment now records its eight section durations, including the failed section
if it stops. The local progress panel shows the final timing table; `DEPLOY_TIMINGS=` in the
log is the machine-readable receipt. Full-workflow Git sync and setup-pipeline time are
separate, not hidden inside that app-only total.
See [speed measurements and least-privilege handoff](../../HydroOperationsApp/DEPLOY.md#faster-deployment-and-a-simpler-operator-path)
for the routine operator path, one-time administrator tasks, and the manual graph prerequisite.
Neither an app deployment nor a timing receipt certifies business-agent execution or delivery.
The October 24 FEATURE verification measured a warm app redeploy at **5m 51s**,
versus **8m 38s** before the authentication-diagnostic replacement. All 40 workspace
items and 40 SPA redirects were preserved. The linked timing comparison separates
the directly measured authentication saving from other service-latency variation;
it is not a fresh-workspace/setup-pipeline timing guarantee.

The October 29 Full Deploy baseline was **70m 35.688s**: Git sync 677.922s,
Core setup 1381.391s, energy finalization 540.922s, Foundry 932.890s and app
publication 702.407s. Git sync now avoids an unchanged Weather Environment rebuild
only after comparing its authoritative published/staged library inventories,
compute settings and external-library exports. Fresh/changed Environments still
publish. Fabric's exact HTTP 404 `EnvironmentLibrariesNotFound` response from the
published library inventory also requires publication, even if Environment metadata
reports `Success` and Git has no new changes. Other comparison read failures stop
the sync. The current engine also overlaps
Energy finalization with Foundry in one post-Core process, eliminating three serial
orchestrator launches and their repeated target/SPA/configuration preflight. It reuses
the freshly verified backend for static publication, but still reapplies backend
runtime/CORS/database settings and runs all endpoint/authentication checks. It also overlaps
immutable image preparation with fresh backend provisioning, batches connector application with individual receipt
validation, and avoids duplicate definition/token reads inside read-only Foundry
verification. Energy setup consolidates serving queries and redundant notebook
actions while preserving freshness and publication checks. Detailed substeps now
emit `DEPLOY_SUBSTEP_TIMING=` records; concurrent durations are not additive.
See the linked speed section for the complete substep audit and remaining
service-bound waits. These are regression-tested mechanical reductions, not a
measured new fresh-workspace deployment duration.

Live verification of the new **combined post-Core stage** succeeded on the prepared
FEATURE target in **14m 05.266s**: Energy 284.921s and Foundry 79.671s ran concurrently,
then app publication and all required runtime/CORS/connector/authentication checks
completed. All 45 registered redirects were retained. This did not repeat Git/Core,
create a backend or rebuild the unchanged image, so it is **not** a fresh Full Deploy
benchmark. See the deployment guide's live-measurement table for the exact scope.

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

The setup pipeline and **Core setup** form have no Data Agent or Operations Agent mode flags.
The project always attempts agent provisioning against Ontology v2; existing v1 items are rejected
rather than reused. The original Data Agent, ontology-backed Operations Agent, playbook/actions, Teams,
and email-alert provisioning are retained. Real failures propagate through RTI_009/010/011 and
setup. Product support and execution must still be checked in the target tenant; restored
implementation is not live certification. See
[ontology generation policies](../../README.md#ontology-generations-and-optional-agents).

Successful NB09 completion is **deployment `published`, publication `published`, runtime
`not_checked`**, with generation `2`; publication is not reported as runtime readiness.
No automatic MCP query, exact facilities comparison or answer-format gate runs during setup.
Multi-source agents and compatible SQL/custom content/selections are preserved. NB09 does not
create separate Lakehouse/Eventhouse profiles to work around an early MCP answer, and does not
wait for NB11's later operational-guidance publication.
After a Fabric service fix, rerun NB09 against the existing settings Lakehouse to republish and
verify source identity/readback, then test questions interactively if desired. Previous runtime
evidence is cleared; no code switch or workspace reset is needed. See the
[recheck procedure](../../docs/foundry-deployment-and-multi-agent-setup.md#recheck-after-a-data-agentontology-service-fix)
for exact status keys, preservation guarantees and separate acceptance gates.
NB10 leaves the ontology-backed Operations Agent stopped; configuration does not prove delivery.

The ontology-backed agent now uses the October 23 working playbook: two SQL/KQL-grounded
BAD/UNCERTAIN rules, `Id` mapped from `opcua_node_id`, and direct email action bindings.
The Knowledge source remains the verified v2 ontology. The notebook resolves the target
Lakehouse SQL endpoint and KQL database query URI from Fabric, rather than copying endpoints
from the reference workspace. This requires the target Lakehouse SQL endpoint to be ready.
All copies of the queries in the playbook's grounding definitions are rebound together.
The imported conditions remain state-based; **no 15-minute cooldown is implemented**.
See the [alert policy guidance](../../docs/foundry-deployment-and-multi-agent-setup.md#operations-agent-playbook-and-alert-frequency).

The existing `ops_agent_copy_playbook=false` setting remains supported for the ontology-backed
agent when an operator chooses portal generation. The default remains `true`.
The separate Eventhouse-Knowledge Operations Agent is retired. RTI_010 first verifies the
retained ontology agent, then finds only `RTI_Demo_OpsAgent_Eventhouse_<suffix>` in the target
folder. It checks saved identity and the exact target KQL database Knowledge source before
deletion, requires an absence readback, and removes the four `eventhouse_ops_agent_*` settings.
Missing retired agents are safe on rerun; identity conflicts or deletion/readback/settings
cleanup failures propagate. Fresh RTI_001 runs no longer seed a name for the retired agent.
The shared Eventhouse, silver tables, ontology agent's SQL/KQL grounding, Teams destination,
Outlook connection, and `Pipe_SendEmailAlert` remain unchanged.
The ontology agent remains stopped by default, and API/readback failures never cause the notebook to
silently remove the playbook, Teams delivery, or actions.

NB11 mounts the operational SQL Database directly as a temporary execution workaround when
ontology-inherited SQL is omitted at runtime. It reuses any matching profile and binds the
SQL Database item, whose own SQL analytics endpoint executes queries. Ontology bindings, existing sources, custom
settings, instructions and selections are retained, with draft/published readback. Missing
agents and real SQL/API failures still fail setup; no automatic query is performed.
Compatibility status keys remain `data_agent_sql_source_status/reason`, with
`data_agent_sql_source_mode=direct_sql_workaround`, leaving NB09's status untouched.
The app deployer/Foundry verify ontology SQL/weather bindings; app-only publication neither
requires this source profile nor mutates it. Publication/readback does not verify query execution.

NB09's general Data Agent policy is bounded to 850 words / 6,500 characters before
operator or NB11 additions. Historical "had BAD today" questions require every BAD event
from UTC midnight through query time, including signals now GOOD; latest-row filtering is
reserved for current-state questions. This distinction must be carried into tool questions.
Ontology v2 supplies semantic context; its associated Lakehouse
and Eventhouse execute source-native queries. Correlate telemetry/reference rows by
`opcua_node_id`, then operational SQL rows by `equipment_id` / `equipmentId` and, where
applicable, `opcuaNodeId`. Instructions cannot create missing source access or runtime
capabilities. NB11 retains ownership of SQL source-specific guidance. NB09 replaces only
its owned instruction sections and known legacy preamble, preserving custom headings,
settings and NB11's `## Operational data (...)` section. An instruction-only live correction
must preserve all source definitions/selections, ontology bindings and published parts;
test the edited draft separately. A successful published query does not validate different
draft selections, and publishing can promote those unrelated draft changes.

For an existing context-backed agent, NB09 recognizes
`Files/Config/<stage>/ontologies/<name>/ontology.json`, verifies its matching live v2
identity, and preserves it without rewriting NB11's SQL source guidance. The Foundry
connection validator recognizes this published shape too. NB09 preserves ontology-associated
sources and existing compatible profiles/selections without adding duplicate execution sources.
A failed intermediate MCP facilities answer is not treated as proof that the ontology needs
different source attachments. Interactive Fabric queries can use the ontology-associated sources
without a separately attached SQL Database. NB11's later phase publishes operational guidance only.

Geo001 applies its existing bounded retry budget to transient ArcGIS error envelopes
as well as HTTP errors. In the reported reservoir failure, NVE returned HTTP 200 with
ArcGIS code 500, `Service not started`; this is not a boundary-validation failure.
Persistent provider failures still fail setup, with the provider reason and attempt
archive path included in the pipeline error. NB08 similarly retries transient read-only
Kusto queries, including HTTP 520; invalid queries, permission failures and management
writes are not replayed.

Lakehouse datasource elements must use native `lakehouse_tables.schema` roots
containing `lakehouse_tables.table` nodes and their columns. Do not serialize
UI-only `schema_grouping` / `table_grouping` wrappers into this hierarchy:
the live V12 agent published those wrappers successfully but exposed no Lakehouse
query tool. Removing only the wrappers restored actual facility query results.
NB09 now generates the native hierarchy and normalizes legacy wrappers on reruns,
including context-backed agents, while retaining schema/table/column IDs,
selections, descriptions, custom source fields and unrelated definition parts.
Its smoke test also accepts Fabric's observed JSON result-file footer; arbitrary
trailing errors still fail, and results must still match independent Lakehouse rows.
Publication alone remains insufficient proof of readiness.

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

For a fresh, isolated feature workspace **without Fabric Git integration**, use
the optional `FABRIC_FEATURE_CONFIG` mode of the existing app orchestrator.
See [the feature-workspace runbook](../../HydroOperationsApp/DEPLOY.md#optional-isolated-feature-workspace-without-fabric-git).
It imports local definitions with target-specific notebook references, creates
approved dedicated prerequisites, and runs setup/seed/stream without introducing a
second app deployment command. It does not copy source workspace data or credentials.
Fabric setup runs before public artifact discovery and Foundry provisioning. The
bootstrap verifies workspace Runtime 2.0 and rejects incompatible published
Environment overrides before setup or Geo notebook execution. A confirmed terminal
failed/cancelled map-publication job can be retried on the next explicit invocation;
active or indeterminate jobs are resumed rather than duplicated. The final feature
seed explicitly enables demo seeding and fails if an expected operational table is empty.
Private-only Key Vaults use secure ARM initialization followed by the same managed
private-endpoint preflight described below. Public network access stays disabled;
no separate VM or Fabric Git connection is needed.

`01_Pipe_Setup` chains the `RTI_*` notebooks, and nearly all of them call
`notebookutils.credentials.getSecret(key_vault_uri, ...)` to load a service principal. **A Key
Vault holding that principal is a hard prerequisite** — create one with three secrets whose
default names are `tenantid`, `clientid` and `clientsecret`, give the identity that starts the
pipeline permission to read them (for example *Key Vault Secrets User*), and add the service
principal to the workspace as Contributor or Admin. The tenant must also leave *Service principals
can call Fabric public APIs* enabled.

All project Spark notebooks require **Fabric Runtime 2.0 (Spark 4.1 / Python 3.13)**,
including Weather, with no older-runtime fallback. Both Git sync and the standalone
pipeline runner set the workspace default to 2.0 and verify readback before proceeding;
they patch only the runtime setting, preserving unrelated pool and session configuration.
They also verify every workspace Environment's **published** Spark compute runtime, across
all inventory pages, and stop on an older or unreadable override rather than executing
notebooks against it. They never downgrade or silently replace an operator's Environment.
Unattached notebooks inherit that default. Weather notebooks attach to the Git-tracked
`Weather` Environment, whose `Setting/Sparkcompute.yml` also explicitly selects 2.0.
Its Python 3.13-compatible libraries use the official Runtime 2.0 NumPy 2.4.1 / pandas
2.3.3 baseline, xarray 2025.10.1, shapely 2.1.2, pyproj 3.7.2 and adlfs 2025.8.0.
Zarr 2.18.7 / numcodecs 0.15.1 retain Aurora's existing Zarr-2 reader contract.
The previous NumPy-1 / pandas-2.1 pins are not compatible with this runtime; do not restore
them or downgrade the runtime to make publication pass. Publication failures propagate.
No custom pool ID is copied between workspaces. See the official
[Runtime 2.0](https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-2-0) and
[migration guide](https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-2-0-migration-guide).
On October 23, 2026, the local app successfully imported commit `87fde47` into
`ws-FabricIQAppsDemoFEATURE` and published Weather on Runtime 2.0. Authoritative
readback verified the workspace default, the published Environment runtime and all eight
library versions above; 74 focused regression tests passed. A separate read-only Weather
notebook then executed under Python 3.13.11 / Spark 4.1, importing all Weather packages and
passing geometry and in-memory Zarr-2 roundtrip checks. The initial workspace-folder HTTP 401
was resolved by identifying the exact `clientid` principal through approved Fabric private
connectivity and granting it Contributor on the target workspace. Lakehouse creation then
completed successfully; Key Vault public access remained disabled.
Fresh-workspace testing also exposed an Eventstream creation race: HTTP 202 is acceptance,
not completed provisioning. NB02 now waits for the Fabric operation and reads its authoritative
item result instead of attempting to find an unfinished item within 30 seconds. Item lookup
also follows every inventory page. Failed or malformed operations still fail setup; no item
is deleted or recreated to recover. These bounded checks do not certify the remaining setup,
agent execution/delivery, native graph materialization or authenticated application runtime.
The next rerun exposed a false source-mismatch error when Fabric returned valid native TMDL
without private `ONT_*` annotations. Static binding identity is checked against the exact
OneLake workspace/item URL, partition expression and physical table/column mappings;
time-series bindings use the exact cluster/database/table resolved from target Fabric metadata.
Private annotations are no longer authored or required, but conflicting ones, if present,
still fail. An existing Direct Lake table with a missing source expression is rejected rather
than silently retargeted. Unknown parts, existing mappings and strict readback verification
remain preserved. The [official Ontology v2 definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition)
provides the native backing-table contract.

The subsequent FEATURE setup run completed the other notebook activities but failed NB09's
strict ontology-only smoke with `Ontology query capability is unavailable in this environment.`
This is **not** the narrowly allowed unsupported-API-version response, so that run failed rather
than suppressing the error. The old smoke forbade SQL and KQL, contrary to the current agent
instructions permitting source-native execution on ontology-associated sources. A separate delegated
CLI diagnostic permitting the associated Lakehouse returned three facility ID/name rows in 46 seconds.
NB09 now tests that intended ontology-context/static-binding contract, excluding operational SQL,
KQL and unrelated sources, and still compares exact records with the independent live-row oracle.
It also handles Fabric MCP's actual CSV result attachment/generated-file footer rather than
misclassifying a successful query as malformed JSON. The evidence label is
`ontology_context_facilities_smoke_v1`; this bounded check does not attest execution provenance,
native ontology traversal, native graph readiness, telemetry/SQL joins, or browser access.
All real execution, publication, transport, semantic and reconciliation failures still propagate.
The corrected full setup pipeline then completed successfully through the local UI. Live Lakehouse
SQL readback independently confirmed the Data Agent was ready/published and its bounded static smoke
was verified. A separate NB09 follow-up persisted a complete 4,784-byte JSON receipt, including exact
expected/returned facility rows, below the SQL endpoint's 8,000-byte string readback limit. These
results do not certify SQL/telemetry joins, native traversal or agent delivery.

A later October 29 fresh setup received `[]` with `isError=false` from the published MCP endpoint,
although the independent live Lakehouse reference contained three facilities. The same published
probe later returned the exact three records without changing sources or bindings. NB09 therefore
retries this narrow empty-result case once, after 10 seconds, using a fresh MCP session and the
unchanged question. It rereads the live reference and retains the initial failure in the receipt.
Repeated emptiness still fails setup; wrong records, malformed output, semantic errors and
authorization/transport failures are not retried or downgraded. This does not establish why the
Fabric runtime initially returned no rows or certify capabilities beyond the bounded smoke.

The selected ontology's **Manage graph** page also reported all five entity types as
**Ineligible**, with **Unsupported Datasource** details. Its displayed completed materialization
contained zero projected node/edge types; this is not a usable native graph. Bounded
same-Lakehouse SQL-source representation probes did not clear that validation. Only the
task-modified source parts and notebook functions were then restored and verified; no source
retarget, speculative authoring change, or graph fallback was retained. The FEATURE application
has not been deployed or runtime-accepted. Reusing the existing Foundry project would also
rebind its stable workspace connections; use an isolated target-specific project before publishing
another concurrently hosted workspace. A native UI-created, task-owned one-entity ontology did
show an eligible Lakehouse binding. Adding its schema field alone to the target did not resolve
eligibility; the public definition update also omitted the native source annotations. Both bounded
target experiments were restored with exact guards and verified readback; no speculative source
change was retained.

The first actual app deployment reused the SPA, preserved its 39 redirects, and provisioned a new
FEATURE AppBackend and isolated Foundry project/model without changing the original project.
It then stopped because the hosted source digest requires the independent STID GraphQL item that
NB11 had not yet created. The canonical flow now bootstraps current NB11 after AppBackend/schema
creation and before Foundry, with Boolean `seed_demo_data=False`. GraphQL and Data Agent SQL-source
publication remain mandatory; existing operational rows and demo-seed status are preserved.
The engine verifies the live notebook code, Runtime 2.0 and idle state before starting, bounds its
job wait to 15 minutes, propagates failures, and refreshes the verified public source identity.
An ordinary deployment with an existing verified GraphQL ID avoids this extra notebook run.
The actual source-only retry then completed NB11 successfully: independent SQL counts before and
after remained zero in all five operational tables, while GraphQL and the Data Agent SQL source
were configured/published and four target-specific Foundry Prompt Agents passed definition readback.
The first Hosted Agent image build stayed in local repository-root packaging without submitting an
ACR build. Only that verified, task-owned CLI packaging process was stopped; no Azure build or
resource was removed. The canonical builder now stages exactly its existing fingerprinted manifest
in a temporary context (33 files / 1,064,697 bytes for this revision), verifies the same image tag,
and cleans the context on either success or failure. The staged retry submitted ACR run `dt1`,
which succeeded in 2m19s; local Unicode log streaming then failed and temporary-directory cleanup
encountered a Windows working-directory lock. The builder now waits with `--no-logs` and a
20-minute remote build timeout, launching from the stable repository directory with absolute
staged paths. The next actual local **Deploy app** retry reused the verified image and completed
with `SUCCESS` at `https://brisk-ford-6c2fad844b-swedencentral.webapp.fabricapps.net`
(build 1.0.975, Hosted Agent v1). All 40 required SPA redirects were preserved; GraphQL, token
and connector preflight/POST readiness passed. Normal Fabric browser sign-in then loaded three
facilities, 15 equipment records and 90 instruments through the connector before SQL demo seeding.
Explicit **Seed & provision** still seeds demo data later. Full runtime acceptance, including
the target ontology's currently ineligible native graph, remains a separate gate.
Hosted acceptance also exposed an obsolete compatibility path that drew a graph from Lakehouse
rows while native graph loading failed. That path is removed: graph canvas, tree and scopes now
require actual verified native entities and relationships, and retries do not depend on STID.
Operational SQL and connector-backed reference/weather pages remain independently available.

The final local **Deploy app** run published build **1.0.979 (`346a95c`)**, with
canonical `SUCCESS`, endpoint/CORS readiness and all 40 SPA redirects preserved.
Actual authenticated browser reads on that build returned HTTP 200, no query errors
and complete pagination for all nine Lakehouse connector entities: three facilities,
three systems, 15 equipment records, 90 instruments, nine weather variables and
currently empty weather areas, locations, observations and forecasts.
Explicit **Seed & provision** completed; independent SQL counts are 12 work orders,
six maintenance notifications, 30 inspections, 12 spare parts and 15 3D models.
**Start stream** produced 90 fresh signals during acceptance; the bounded run later
ended and the app correctly labels those readings stale, not currently fresh.

The FEATURE Data Agent also answered actual read-only portal questions combining
Lakehouse/Ontology mappings, Eventhouse telemetry and operational SQL. The positive
UNCERTAIN-quality case returned three Sloy signals, correctly matched T001's
`vibration_a` to `WO-2841`, distinguished T002's different-signal work order and
reported no work order for T004. Independent SQL readback matched the cited
work-order records. This bounded case is not execution-provenance attestation,
comprehensive combined-source certification or Hosted/Operations Agent acceptance.
Operations actions and Teams/email delivery remain untested.

Weather ingestion is **blocked by the provider credential**, not a Runtime downgrade:
Weather_002 failed with Aurora STAC HTTP 401 on Runtime 2.0. A bounded Python 3.13.11
probe received `invalid subscription key` with both the existing query parameter
and the subscription-header placement. No authentication-placement workaround was
retained. An operator must verify or replace `mai-weather-api-key` in `akvfabcapnew`
for this exact Aurora/STAC service, then rerun **Connect weather**. Do not put the
secret in logs or chat. The failing owned pipeline is cancelled; the diagnostic
completed and its original notebook definition and description were restored with
exact readback. Weather_003 and Weather_020 did not execute in that failed pipeline.
The workspace default and published Weather Environment both read back Runtime 2.0.
Earlier portal inspection reported ineligible/unsupported source bindings, but the
final authenticated browser verification returned actual native topology from the
explicitly bound FEATURE GraphModel: metadata, queryable schema and both GQL queries
returned HTTP 200, and the graph canvas/tree/scopes displayed native entities with
operational overlays. The repeated GQL read returned **201 native nodes and 198
native relationships**, execution code `00000`, without continuation; the **All**
scope displayed 174 nodes after signal presentation and operational enrichment.
This is live graph evidence, not the removed STID fallback.
It does not establish an automated materialization endpoint or explain the earlier
portal eligibility warning. No existing binding or resource was deleted.

Missing native graph binding now stops the app engine before
AppBackend/Foundry provisioning, with the manual prerequisite instead of a late JSON parse failure.
This guard does not make ineligible entities eligible or certify nonempty topology from binding
JSON alone. All 48 pre-existing ARM resource IDs across
the two subscriptions were rechecked and remained present.

The Git sync verifies that
`Weather_001_create_lakehouse`, `Weather_002_fetch_area_weather`,
`Weather_003_fetch_ukmet`, and `Weather_020_area_calculations` are in the workspace `Notebooks`
folder and binds all four to the project Lakehouse. The fetch notebooks read
`mai-weather-api-key`, `ukmet-global-spot-api-key`, and `ukmet-land-observations-api-key` from the
same Key Vault passed to `Pipe_Setup`. The provisioner prints these names but intentionally never
requests, reads, or stores their values.

Provisioning also creates `03_Pipe_Weather` with its UTC schedule disabled. Stage 2 enables the
six-hour schedule only after every setup activity, including initial UKMet ingestion, current
eligible-facility coverage and full ontology validation, succeeds and `enable_weather_schedule`
is true (the default). Setting it false disables future scheduling, not the required initial
UKMet bootstrap; feature workspaces also need valid UKMet credentials. Aurora remains optional.
The Stage 2 root notebook inherits the published Weather Environment so nested weather stages
use the same compatible Spark session and default Lakehouse. The schedule aligns to the
00/06/12/18 UTC model runs both vendors derive from. Each run executes area preparation,
UKMet and `Weather_020_area_calculations`, then optional Aurora and another serving refresh.
The serving stage rebuilds area metrics
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

The **Core setup** action accepts either the target workspace display name or GUID in
the sidebar. Its workspace parameter mirrors that value, and the runner resolves it
to the canonical GUID before starting `01_Pipe_Setup`. The local action then runs
energy preparation and read-model verification in the same exclusive job; a
pipeline failure prevents energy setup. The raw `run_pipeline.py` CLI still runs
only the named Fabric pipeline, not the local app's composed core action.

**Full Deploy** is separate from the collapsible **Deployment Actions** group.
The group contains every individual module, including the energy-only maintenance
action, and each entry has an icon. **Hide sidebar** expands the content area;
the always-visible **Show sidebar** control restores navigation and target fields.
The sidebar preference is remembered in this browser.

**Full Deploy** composes the existing actions without replacing their engines.
It is self-contained, uses the sidebar target, and shares remembered non-secret
core and Foundry settings with the individual modules.

1. **Sync from GitHub** using its own repository, branch, directory, reusable
   connection/PAT settings, and connection test.
2. **Core setup** runs `01_Pipe_Setup`, including the existing parallel Core/Weather
   and Geo_001 branches.
3. **Energy + Foundry + app deployment** uses one `deploy_fabric_app.py` process.
   Energy publishes/verifies the completed Core import while Foundry sets up independently;
   website publication waits for both. Core/Geo_001 are not rerun. Target/SPA and
   dependency preflight run once; only the parent writes Rayfin configuration.

The individual actions remain available for running or troubleshooting a single
component. **Refresh energy sources** updates only energy data without rerunning
core setup or Foundry. No separate energy opt-in is needed during core setup or
Full Deploy.

Connection testing is manual only: click **Test connection** in either the
**Full Deploy** GitHub section or **Sync from GitHub**. Each tests its own source
settings and displays results inline without navigating away or copying values
between screens. Loading the page, signing in, editing/blurring fields,
switching modules, and changing **Reuse existing connection** never start a
connection-test job. Only one explicit connection test can run at a time.
Running sync or Full Deploy still validates Git connectivity as part of the action
you explicitly start.

If core setup reports **SQL creation returned an unexpected operation location**,
the SQL database may already have been created even though the notebook failed.
Do not delete it or increment the environment suffix to work around the failure.
The setup notebooks now use the shared Fabric long-running-operation handler:
operation-ID headers, relative URLs and trusted regional URLs are supported,
and result URLs retain their query parameters. Update the affected notebooks
before retrying core setup with the **same suffix** so the existing owned database
is reused and moved into its setup folder. Do not rerun a full Git import just to
retry this stage. Local regression tests are not a successful live deployment;
verify the real pipeline and each subsequent stage independently.

A Stage 2 `Only the default namespace is managed: ''` error can also mean
entity settings were hidden by leading TMDL descriptions, not that the ontology
uses an unsupported namespace. The shared reader now skips leading descriptions,
backing tables are inserted under the entity declaration, and binding updates
preserve the following property's description. The default-namespace and source
identity checks remain enforced. Apply the updated NB04/NB05/NB06 definitions and
retry core setup with the same suffix; do not replace the ontology or SQL store.

The steps run strictly in order under one exclusive job. Each child keeps its
existing validation, timeout, environment-only secrets, and live output. If a
step exits unsuccessfully, the combined log identifies the failed component and
records that later steps were not started. The progress display exposes detailed
Sync, Pipeline, and Deploy subphases, and **Cancel running jobs** terminates the
currently active child process.

After a failed or cancelled **Full Deploy**, use **Retry from … and continue**.
For example, an Energy or Foundry failure retains successful GitHub sync and
`01_Pipe_Setup`, then retries the combined Energy/Foundry/app component.
Only whole, successfully completed components are retained; a partially completed
failed/cancelled component runs again through its existing idempotent command.
Completed component/subphase timings remain visible; the overall timer measures
the current retry attempt.

Resume requires the original effective tenant, workspace, Git source/connection,
pipeline parameters, SPA and Foundry/app settings. Changed inputs reject the retry
without starting anything; restore those inputs or explicitly choose **Run Full
Deploy** to start over. A PAT is never saved or included in checkpoint identity;
enter it again only when retrying an incomplete Git sync that needs one.
The browser remembers only the last Full Deploy job ID and reconnects after page
reload. Full Deploy checkpoints and measured timings are saved atomically in the
existing gitignored `webapp/pipeline-defaults.local.json` settings file (or its
`FABRIC_UI_PIPELINE_DEFAULTS_FILE` override). Only job identity, successful component
prefix, status, timings and input/source hashes are saved, never PATs, child
environments or live logs. Failed/cancelled runs can resume after restarting the
local server. A checkpoint that was still running when the server stopped is
reported as interrupted, not safe to retry automatically: check Fabric's Monitoring
hub before starting another run. The older five-stage checkpoint layout explicitly
refuses automatic resume and retains its recorded completion information; its
boundaries cannot safely certify completion of the new combined component.
Reloading such a run displays **Previous deployment history**, including its recorded
outcome and completed stages, without a new failure, live progress, or any deployment
being started. Explicit resume still refuses the incompatible checkpoint.
Older runs have no durable checkpoint. For an older
Energy failure, use **Refresh energy sources**, then **Set up Foundry agents** and
**Deploy app**, rather than repeating successful Core setup. Energy setup now compares
the Git-managed Geo notebooks with this checkout and updates changed code only,
preserving their live bindings, before retrying. Missing/corrupt checkpoints
explicitly refuse resume, never silently start a new deployment.

Resume compares component source digests, not the global Git commit: unrelated
documentation/local-UI changes do not invalidate completed setup. Energy retry
detects changed `Geo_001`/`Geo_002` source and offers **Sync updated definitions and
resume**. It updates only changed existing notebooks from the local checkout through
Fabric definition APIs, retaining their Lakehouse/Environment bindings and unknown parts,
then verifies readback before retrying Energy. Source comparison accounts for Fabric's
Markdown blank-comment serialization and line endings; executable code, binding metadata
and other retained parts must still match. It does not reconnect Git, import the
workspace again or repeat successful Core setup for a Geo-only fix. A changed core
notebook instead restarts at Setup pipeline after the targeted update. The checkout must
match the selected repository/branch. Environment, pipeline, item-metadata changes,
deletions or a different checkout require explicit full Git sync/setup; push those
changes to the selected branch first. Local launcher/UI or unrelated documentation
changes need no Fabric sync. Changes to completed Energy/Foundry component sources
invalidate that component and later stages. Pending notebook updates survive a failed
sync/restart and are retried before any setup command, not silently forgotten.

Full Deploy shows a running elapsed timer and final duration for every section
and its expanded substeps. Core setup also separates the setup-pipeline and
energy-publication component durations. Timers use the server's monotonic clock,
refresh with the one-second progress poll, and freeze on success, failure or
cancellation. Section totals sum their actual local component runtimes; substep
intervals are bounded by observed progress log markers, not individual remote
Fabric notebook executions. Omitted markers display **Not reported**, and steps
that have not started display **Not started**. Lost monitoring shows the last
observed duration rather than claiming the task has stopped. Full Deploy stores
measured timings with its checkpoint; other module timings remain in memory and
cannot be reconstructed after a server restart.

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

Energy ingestion and map-agent publication belong to **Refresh energy sources**.
The app-publication phase checks existing serving data without running `Geo_001` or
`Geo_002`, rebuilding their projections or republishing the map agent. The combined
CLI flow still prepares Energy once when explicitly enabled, before app publication.

1. Reuse the validated locked npm dependencies under Node 24 (restoring them only when missing or
  invalid), validate the active Azure CLI tenant, and resolve the exact workspace GUID/name. If the
  selected identity is missing from the Azure CLI token cache or its token is stale, deployment opens
  tenant-scoped Microsoft sign-in and retains that recovery in an isolated per-tenant cache for later
  deploys. Missing Node/npm/npx is reported with an install link.
  The resolved capacity is checked when visible: a non-Active/unknown state stops before
  configuration changes. Missing capacity-list permission/visibility is explicitly warned,
  without adding a new operator-role requirement; final endpoint checks still apply.
  The launcher never resumes, resizes or reassigns a paused capacity.
2. Reuse the tenant's `Hydro Operations Fabric Client` SPA, create it when absent,
  or use the optional client ID entered in the form. If discovery, reuse, or creation is blocked,
  stop before changing Rayfin state and print an administrator handoff. A deployment never ships
  with an empty SPA client ID.
3. Reuse matching active Rayfin state only when its generated API URL targets the workspace's
  current capacity. A workspace/capacity/tenant mismatch moves only the three Rayfin state files
  into a unique temporary backup, then generates and validates a fresh ignored `rayfin/.env`.
  Existing state is not deleted.
   In the independent **Set up Foundry agents** module (or the combined CLI flow),
   the deployer converges the **Foundry** side: the
   Fabric IQ connections, the five specialist prompt agents (including weather), and the `hydro-orchestrator` Hosted Agent. The
   Hosted Agent image is built in the project's ACR only when the packaged source changed, and a new version is
   published with azd only when the live agent differs; a target with no AppBackend yet gets one created first.
   Use the form's Foundry block (or `HYDRO_FOUNDRY_PROJECT_ENDPOINT`, `HYDRO_FOUNDRY_MODEL`, `HYDRO_ACR_NAME`,
   `HYDRO_PRODUCTION_WRITES_ENABLED` in the launch environment). Select the Azure subscription with the form or
   `HYDRO_AZURE_SUBSCRIPTION_ID`; the engine verifies that it is enabled in the Fabric tenant. New target-specific
   project creation is an explicit, billable opt-in requiring only project and model deployment names.
   Both forms resolve settings from live model/catalog metadata, never the demo tenant.
   Work-order writes are enabled by default behind explicit human approval, including redeployments
   of older disabled coordinators. Set `HYDRO_PRODUCTION_WRITES_ENABLED=false` for an intentional
   read-only deployment; this does not change chat/draft generation into a write operation.
   Requires `azd`; missing
   `azure.ai.agents` and `microsoft.foundry` extensions are installed
   by the deployer. Foundry uses the verified `RAYFIN_PUBLIC_ONTOLOGY_ID` independently of
   graph binding; native graph materialization does not gate **Set up Foundry agents** or **Deploy app**.
   New models prefer 75,000 TPM within the selected model/SKU's available regional quota, falling back
   to a supported lower allocation. Existing positive allocations are accepted without automatic resizing.
   The final app module only reads prepared Foundry definitions/runtime identities;
   it does not discover projects, bootstrap SQL, build images or publish agents.
   Local packaged-code fingerprint drift is advisory after live identity/authentication/runtime
   compatibility checks succeed. It does not mean Foundry is missing or require another setup
   run just to publish the app. Hosted-service code changes still require Foundry setup to deploy them.
   For the optional native instance graph only, pass a fallback operator-verified binding through
   `HYDRO_ONTOLOGY_GRAPH_BINDING`; do not hand-edit generated state. See
   [Foundry deployment and multi-agent setup](../../docs/foundry-deployment-and-multi-agent-setup.md).
4. Sign Rayfin into the target tenant, provision the AppBackend and SQL schema, build
   and deploy static hosting, apply the generated hosting origin to backend auth, then always
   reapply AppBackend runtime/CORS settings and the DAB/SQL configuration. This also runs for an
   unchanged hosting origin so managed-service restarts cannot leave stale runtime settings.
   The same flow configures the read-only, delegated `hydrolakehouse` Rayfin connector for
   STID/weather. It resolves the selected Lakehouse's SQL analytics endpoint ID from live
   metadata, reapplies the connector, and rejects missing/skipped/partial generation or apply
   receipts. No extra form parameters or manual GraphQL binding are needed for SPA reads.
   An empty tenant-scoped Azure CLI cache never shadows an existing signed-in identity:
   the engine selects a scoped cache only when its profile contains the target tenant,
   then still verifies the active account's exact tenant before changing Fabric state.
   Child Python processes use UTF-8 output. ACR waits with `--no-logs` because the
   Windows Azure CLI isolated interpreter can ignore Python encoding environment variables.
   Run ID/status and image digest readback verify the build without Unicode log streaming.
5. Run `npm run setup-live-auth` for SPA redirects, delegated ADX/Fabric permissions,
  and consent, then verify the live app returns HTML over HTTP 200. The final hard checks ensure
  the generated API URL targets the current capacity/workspace/AppBackend and send
  browser-equivalent CORS preflights to `/graphql` and `/api/auth/v1/token`. Backend warm-up is
  retried with bounded backoff. The checks also send a minimal GraphQL query and a deliberately
  incomplete token request; missing CORS headers, GraphQL failure, or a persistent token HTTP 5xx
  fails deployment.
  The Lakehouse connector also requires a successful browser-equivalent CORS preflight and an
  HTTP 401 authentication challenge from its unauthenticated POST; missing routes, server errors
  and missing CORS fail deployment. The engine explicitly reports
  `LAKEHOUSE_CONNECTOR_DATA_ACCEPTANCE=not-performed`. Verify all nine entities separately through
  normal Fabric browser sign-in, including complete pagination and seeded reference rows.
  Empty weather tables are legitimate, not invented data. Azure CLI direct exchange is not a
  supported acceptance path: requesting `Item.Execute.All` returned API-owner preauthorization
  error `AADSTS65002`, not an operator-consent prerequisite. Browser acceptance proves only the
  tested session's access. The existing Fabric GraphQL API remains for
  the hosted crew's independent verification; SQL, agents, telemetry and native graph are unchanged.
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
  Explorer `user_impersonation`, Foundry Agent Service `user_impersonation`, and
  Power BI Service `GraphQLApi.Execute.All` (Rayfin managed connector transport),
  `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All`, `DataAgent.Execute.All`
  and `Fabric.Embed`. No standalone STID GraphQL API is required.
3. **Application Administrator / Cloud Application Administrator:** select **Grant admin consent
  for the directory**. None of these scopes is directory-privileged, so Global Administrator and
  Privileged Role Administrator are not required. Per-user `Principal` consent works only for that
  user; suppressing the prompt for everyone requires tenant-wide `AllPrincipals` consent.
4. Enter the administrator-provided Application (client) ID in the local app's optional **SPA
  client id** field only if automatic discovery cannot select it, then rerun the
  canonical deployer. Do not hand-edit generated environment files. The workflow
  configures and validates redirects, scopes, and consent automatically.

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
