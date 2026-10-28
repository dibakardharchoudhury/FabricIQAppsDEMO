# Deploy Hydro Operations

**Recommended: use the local deployment app, "Initialize Your Fabric Demo".**
It is included in [`Raw/workspace-reset/`](../Raw/workspace-reset/) and opens at
<http://127.0.0.1:5000/>. It automates the workflow; this is not the hosted Hydro
Operations website.

- [Where to find it and how to launch it](#2-start-the-local-app)
- [Every deployment field: what to type and what it means](#local-app-parameters-what-to-type)
- [Foundry automation and resource reuse](#6-set-up-foundry-agents)

## Quick start: one path for each job

1. From the repository root, run `python Raw\workspace-reset\launch.py`.
2. Sign in and enter the tenant and workspace.
3. Choose an action:

| Goal | Action |
|---|---|
| New workspace | **Full Deploy** |
| Website update | **Deploy app** |
| Agent update | **Set up Foundry agents**, then **Deploy app** |
| Energy data refresh | **Refresh energy sources** |

**Do not reset, delete items or reimport Git to update an existing website.**
If a step fails, fix the reported prerequisite and retry that module.

### What you need to enter

| Input | Default |
|---|---|
| Tenant / workspace | Tenant may be remembered; workspace always starts empty |
| Repository / branch | This checkout; confirm the branch is pushed |
| Key Vault URI / Teams IDs | Saved for this target; unknown values stay blank |
| Alert email | Signed-in user's email when the tenant matches; confirm the recipient |
| Azure subscription | Active or sole subscription in the target tenant; otherwise choose |
| Foundry project name / Model deployment name | Saved selection, target-named project, or unambiguous discovery |
| New-project creation checkbox | Off; enable only to approve billable creation |

Core/Full Deploy values autosave on this machine in gitignored
[`pipeline-defaults.local.json`](../Raw/workspace-reset/webapp/pipeline-defaults.local.json).
Wait for **Settings saved on this machine** before closing. Values are isolated by
target; explicit clears persist. Foundry overrides and sidebar preferences use
browser storage. PATs and credential values are never saved by these forms.

Use **Hide sidebar** for more space. Connection tests run only when you click
**Test connection** in Full Deploy or GitHub Sync.

Live output follows new lines while you are near the bottom. Scroll up or select text
to read earlier output without refreshes resetting your position. Use **Jump to latest**
to return to the newest output and resume following. The log also supports keyboard scrolling.
After a local UI update, reload the page when safe; a server restart is not required.

### Find your Teams team and channel IDs

In Teams, choose the channel's **Get link to channel**:

- **Team ID:** GUID after `groupId=`, not `tenantId`.
- **Channel ID:** URL-decoded segment after `/channel/`, including the full
  `19:...@thread.tacv2` value (`%3A` → `:`, `%40` → `@`).

[Microsoft's channel-link format](https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-teams#deep-link-to-navigate-to-channel).
IDs select a destination; they do not grant access or prove delivery.

## Start here: choose your interface, not a different deployment process

The local app and CLI use the same
[`deploy_fabric_app.py`](../Raw/workspace-reset/deploy_fabric_app.py) engine.
Run the launcher from the checkout you intend to publish. The branch field
controls Fabric Git import, not local app or agent source.

## 1. Prepare access once

| Resource | Required preparation |
|---|---|
| Fabric | Active capacity, supported region, tenant API settings and workspace permissions; Git can require Admin |
| SPA | Verified target-tenant registration, redirect-update access and delegated consent |
| Key Vault | Existing SPN tenant ID, client ID and client secret; notebook read access and connectivity |
| Foundry | Project/model access, connected registry, monitoring and scoped roles; creation also needs resource/role-assignment rights |
| Teams/email | Authorized team/channel and mailbox-backed Outlook OAuth connection |

Global Administrator and subscription Owner are not routine requirements.
For scoped access without subscription-wide discovery, use the existing project's
full ARM ID in **Foundry project name** instead of its display name. Fabric and Foundry may use
different subscriptions in the same tenant.

### No admin rights? Hand this to your Entra admin

Ask resource owners for the prepared resources above, not blanket roles.
Give administrators the **exact prerequisite error** if deployment is blocked.
Never paste credential values into the launcher or Git.

`setup-live-auth` manages SPA redirects, permissions and consent. Do not hand-edit
them or remove existing redirects. App ownership alone does not grant tenant-wide
consent. `npm run setup-live-auth:dry` previews changes without writing.

## 2. Start the local app

### Where to find it and how to launch it

Use an up-to-date local checkout of **`main`**. Install Python **3.11+**, Git and
Azure CLI; Foundry also needs `azd`. On Windows, enable Python's **Add python.exe
to PATH** installer option. The engine resolves Node **24** and locked dependencies;
you do not need to choose a Node version or build the local UI.

**Windows, no command needed:** in File Explorer, open the repository's
`Raw\workspace-reset` folder and double-click
[`Start Fabric Demo.cmd`](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd).
This is a file in your local checkout, not a button on the GitHub website.

**Command-line alternative:** open a terminal at the repository root:

```powershell
python Raw\workspace-reset\launch.py
```

On macOS/Linux, run `python3 Raw/workspace-reset/launch.py` from the repository
root, or use [`start-fabric-demo.sh`](../Raw/workspace-reset/start-fabric-demo.sh).

The launcher checks Python dependencies and Azure CLI, starts the local server,
and opens <http://127.0.0.1:5000/>. If the browser does not open, enter that address
yourself. Click **Sign in** inside the page; use **Switch** if the displayed account
is wrong. Azure sign-in is performed from the page, not by typing credentials into
deployment fields.

You can close the launcher window after it reports the app is running. Closing
the browser does not stop the server. Use **Stop server** in the sidebar to stop
it, or **Restart server** after Python server changes. Restart/stop refuses while
a deployment job is running. Relaunching the launcher replaces an idle old instance
and waits for the port to be released. Do not start a second deployment against
the same target while another job is still running.

### Local app parameters: what to type

The entries below are **examples**, not tenant/workspace defaults. Replace them
with your own values. Paste full values without surrounding quotes; a long value
may be clipped visually without being truncated.

#### A. Sidebar: choose the target first

| Field | What to type | Meaning |
|---|---|---|
| **Tenant id or domain** | Your tenant GUID or `contoso.onmicrosoft.com` | The Microsoft Entra tenant containing the target Fabric workspace. Confirm the signed-in identity has access. |
| **Workspace (GUID or name)** | The workspace GUID from `/groups/<workspace-id>` in its Fabric URL, or its exact name | The existing Fabric workspace to deploy into, not a new workspace name to create. Prefer the GUID when names are duplicated. The workspace field starts empty deliberately. |

Select **Full Deploy** for a new/prepared-empty workspace. It runs **1. GitHub sync
→ 2. Core setup → 3. Foundry agents → 4. Deploy app**, stops on failure, and streams
the component's output. Core includes Weather and energy ingestion/read-model checks.
Full Deploy is not a workspace-creation wizard or a safe routine website update:
Git synchronization can remove generated items. For an existing prepared target,
use **Deploy app**, or **Set up Foundry agents** followed by **Deploy app** when
agent definitions also need updating.

#### B. Full Deploy: GitHub sync

| Field | Entry/default | Meaning |
|---|---|---|
| **Repository (owner/repo or URL)** | `dibakardharchoudhury/FabricIQAppsDEMO`, or your maintained fork | Repository containing the Fabric artifacts. Confirm the chosen source includes the latest fixes. |
| **Branch** | `main` for the canonical release | Remote branch to import into Fabric. It must be pushed and match the local source you intend to deploy. |
| **Directory (workspace root)** | `/` for this repository | Path **inside the Git repository** containing Fabric artifacts, not a Windows folder or workspace GUID. |
| **Reuse existing connection** | Leave checked when a suitable Fabric GitHub connection exists | Uses an accessible connection instead of requesting a new GitHub credential. |
| **Keep connected after sync** | Off unless persistent Fabric Git integration is wanted | Keeps the workspace linked to Git after import; otherwise the sync disconnects it. |
| **GitHub PAT**, only if shown | A GitHub token authorized for the repository | Needed to create a connection when reuse is unavailable. It is not your Azure token or SPN client secret and is never saved by the form. |

Click **Test connection** to check Git connectivity before starting the workflow.
This tests connection/initialization, not deployment readiness.
**Changing Branch does not switch the local checkout.** Fabric notebook source
comes from the selected remote branch; website and Foundry code come from the
local deployment checkout. Use current `main` for both. The launcher's **Deployment
source** line and the local version API identify the actual checkout.

#### C. Full Deploy / Core setup: Fabric parameters

| Field | Example/default | Meaning |
|---|---|---|
| **Environment suffix** | Default `V6`; for example `V20` for a new environment | Separates owned items, such as `RTI_DEMO_V20`. Reuse the existing suffix for the same environment; choose an unused suffix for a separate one. Do not change it merely to update the website. |
| **Workspace name or ID** | Copied from the sidebar; read-only | The same deployment target, not a second workspace selection. |
| **Key Vault URI** | `https://contoso-demo-kv.vault.azure.net/` | An existing vault holding notebook SPN credentials. The vault, secrets, access and connectivity must already be prepared. |
| **Teams team id** | The full GUID after `groupId=` in a Teams channel link | Team receiving Operations Agent notifications. Not the tenant ID or team display name. |
| **Teams channel id** | Full URL-decoded `19:...@thread.tacv2` value | Channel receiving notifications. Not its display name. [Find both IDs](#find-your-teams-team-and-channel-ids). |
| **Alert email recipient** | `operations@contoso.com` | Email alert destination. Review any signed-in-user suggestion; this does not set the agent's identity. |

Key Vault URI, both Teams IDs and alert email are required by the local Core/Full
Deploy form. Unknown destination values stay blank rather than being guessed.
Expand **Key Vault secret names and notebook settings** only when defaults differ:

| Field | Default | Meaning |
|---|---|---|
| **KV secret · tenant id** | `tenantid` | Name of the vault secret containing the SPN tenant ID. |
| **KV secret · client id** | `clientid` | Name of the vault secret containing the SPN application/client ID. |
| **KV secret · client secret** | `clientsecret` | Name of the vault secret containing the SPN credential. **Never paste the credential value here.** |
| **Agent run-as user** | Blank | Uses the deploying signed-in user. An explicit UPN is an identity guard, not a way to impersonate another user or grant permissions. |
| **Per-notebook timeout (secs)** | `3600` | Maximum runtime in seconds for each child notebook, not the entire deployment. |

#### D. Foundry agents: subscription, then project and model

These are the same shared fields in **Full Deploy** and **Set up Foundry agents**:

| Field | What to enter | Meaning |
|---|---|---|
| **Azure subscription** | Choose the target subscription; use **Refresh subscriptions** to load accessible choices | Where Foundry resources are found or created. Select this **before** the project/model. Fabric capacity can be in a different subscription in the same tenant. |
| **Foundry project name** | For example `hydro-demo-project` | Existing project to reuse, or the exact project name to create with approval below. For duplicate names/scoped access, a full project ARM ID can identify an existing project. |
| **Model deployment name** | For example `hydro-chat` if that deployment already exists, or a supported catalog chat model name for creation | Not an agent name or API endpoint. An existing alias must resolve to a model/version in the selected subscription; an arbitrary invented alias is not a catalog model. |
| **Check this box if you want to create a new Foundry project with the multi-agent setup.** | Leave off for reuse; check only to approve creating missing resources | Existing projects are still reused. Approval is not remembered after reload or target change. Creating resources is billable; global inference may apply. |
| **Use a pre-created app registration** | Leave off for automatic discovery/reuse or creation | Enable only to select a specific administrator-prepared SPA in the target tenant. |
| **Application (client) ID**, shown when that option is enabled | The SPA's full Application/client GUID | Not its object ID, tenant ID, or the notebook SPN secret. The registration is verified; redirects, permissions and consent setup remain automated. |

For an already prepared, unambiguous target, blank project/model fields can use
saved values or verified discovery. For a new Foundry setup, select the subscription,
enter the intended project and model names once, and approve creation if needed.
The optional app-registration controls are inside **Fabric App authentication**;
leave that section unchanged for automatic discovery/reuse.
Setup configures the specialists, connections, toolbox and hosted coordinator
automatically. You do **not** enter catalog version, deployment type, model capacity,
Fabric region, endpoint URLs, registry URLs, or individual agent definitions.
Missing permissions, ambiguous selections and unavailable quota are reported as
prerequisites, not silently replaced with another target.

### Run, confirm success, and keep settings

After reviewing the target and fields, click **Run Full Deploy**, **Set up Foundry
agents**, or **Deploy app**, according to the job table above. If a run button is
disabled, wait for target settings to load, review validation messages/required
fields, and check whether another job is still running. Follow the live progress;
do not treat a running progress bar or an accessible HTML page as completed deployment.
Final app publication must pass its endpoint/authentication/connector checks and
print **`SUCCESS`** and **`DEPLOYED_APP_URL`**. Open the reported URL: it is the
hosted Hydro Operations app, not the local `127.0.0.1:5000` deployment interface.

On failure, read the failed component and exact prerequisite in its log. Correct
that prerequisite, then retry the relevant standalone module; do not delete the
workspace or repeat Git import merely to retry app publication.

Core/Full Deploy non-secret values autosave per tenant/workspace; wait for
**Settings saved on this machine**. Foundry names and other sidebar choices use
browser storage. PATs and credential values are not saved, and creation approval
must be checked again deliberately. Changing targets must not reuse another
target's credentials or guessed resource identities.

Automation does not create the prerequisite Key Vault secrets or grant permissions
you do not hold. Follow any explicit administrator action. If using the optional
**Materialized native graph (GQL)** mode, its portal materialization remains manual;
the default live ontology-governed mode does not require that materialization.

## 3. Prepare core Fabric data, only when needed

Use **Full Deploy** for a new workspace; **Core setup** when Git import is already
complete. Supply Key Vault URI, Teams IDs and alert email.

**The vault and SPN secrets must already exist.** Default secret names are
`tenantid`, `clientid`, `clientsecret`; change them only if your vault differs.
Notebook identity defaults to the signed-in user; timeout is 3600 seconds.

Core creates Lakehouse, Eventhouse, independent operational SQL, v2 ontology,
dashboard and agents. NB11 adds missing demo records and publishes the SQL source.
After NB01 creates and binds the Lakehouses, the Core/Weather `runMultiple` DAG
and `Geo_001_ingest_energy_context` run in parallel branches of `01_Pipe_Setup`.
Energy keeps its separate, schema-disabled GeoContext Lakehouse and Spark session:
it must not inherit Core's operational default Lakehouse. The pipeline waits for
both branches; either failure stops later steps. The local job then publishes and
verifies the map read models without running ingestion a second time. The local
progress display groups both operations under **Core Setup: Fabric, Weather and
Energy**; publishing map read models is not a second notebook-ingestion stage.
Foundry remains separate. Sync from GitHub once to import the updated pipeline/notebooks;
Core refuses the old serial pipeline before starting a long run.

Notebook completion is not agent runtime acceptance. NB09's exact unsupported-v2
API response is a documented product limitation, not proof of a working query.

## 4. Choose Knowledge Graph execution

The app defaults to **Live ontology-governed sources**. It verifies the live
ontology's workspace/item/table/property/key bindings against the existing
Rayfin Lakehouse and independent SQL connectors, then joins only declared
relationships. Materialization is not required for this mode. The page reports
supported/excluded coverage, source observation times, load duration and
non-atomic cross-source consistency. It does not append inferred SQL topology.
Denied, invalid, incomplete and over-budget reads fail closed, not to mock data.
Successful work-order creation, status changes and deletion invalidate graph
snapshots immediately. Returning to the Knowledge Graph reads the committed
records without a page refresh; an already-open graph refreshes automatically
(or when the browser tab becomes visible). In-flight pre-mutation reads cannot
replace the refreshed snapshot. Native GQL mode still reflects Fabric's latest
materialization, not unmaterialized SQL changes.
On a first visit, **Connect Fabric and retry** authorizes only Fabric workspace/item
read access. The graph does not require connecting telemetry to establish this
separate ontology-read session.

For the separate **Materialized native graph (GQL)** mode, use Fabric:
**Manage graph → select eligible entities/relationships → Continue →
Materialize**. This remains a manual portal prerequisite for native execution.
The app never silently switches modes or treats source-backed instances as a GraphModel.
When Fabric returns HTTP 204 for the queryable schema, the graph path stops before loading
the ontology definition or executing queries. Readiness network checks are bounded to ten
seconds; **Retry graph query** rechecks failed readiness without waiting for a cache expiry.

If setup stops here, materialize the graph, then retry **Foundry agents → Deploy
app**. Do not reimport Git or rerun Core.

The engine resolves authoritative graph lineage. If unavailable, supply verified
`workspaceId`, `ontologyId`, `graphModelId` JSON in graph overrides. Never infer
ownership from names or remove valid time-series bindings to force eligibility.
[Graph reference](../docs/knowledge-graph.md).

## 5. Prepare energy and Weather sources

| Source | Core setup | Later refresh |
|---|---|---|
| Energy | Loads GeoContext alongside Core/Weather, then verifies twelve layers and nine area polygons | Hosted setup step 6: **Prepare map**, then **Refresh snapshots** |
| Weather | Creates tables and enables its schedule | **Connect weather** in the hosted app, or scheduled ingestion |

Weather ingestion defaults to UKMet and requires its Global Spot and Land
Observations keys/connectivity. Aurora is optional (`enable_aurora=false` by default);
its MAI key remains in Key Vault but is not read in the default flow.
Energy map chat is separate from ingestion. Neither a successful import nor app
publication proves chat or alert delivery.

Weather setup creates its tables; `03_Pipe_Weather` fetches forecasts and computes
area metrics on its six-hour schedule. It prepares shared operating areas without
Aurora, ingests and serves UKMet first, then runs Aurora only when explicitly enabled.
An opted-in Aurora failure remains visible but cannot block the preceding UKMet
serving refresh. Existing schedules are not changed by a website update.
Energy is different: its first import runs
inside Core; `04_Pipe_EnergyMap` is retained for later on-demand refreshes and no
energy schedule is enabled automatically. Network/plant snapshots cache for 24h;
reservoir statistics, Statnett and UMM refresh on each eligible run. Use
`refresh_mode=operational` for only the changing operational sources, or `all`
for every source with static-cache reuse. A recurring energy schedule is needed
only if unattended freshness is required; select its cadence deliberately.
The hosted Administration page exposes **Prepare Norwegian Energy Map** as step 6.
It uses `all` with cache reuse, not a forced national import. Routine
**Refresh snapshots** in that same step uses `operational` and skips network/plant
inventories even if their cache is old. **Advanced: full national source rebuild**
is collapsed under **Maintenance and source details** in step 6. It is the explicit
`all` + `force_refresh=true` option and may take tens of minutes.
All three reuse active runs and report actual pipeline completion, including
map-chat failures; a browser-stored flag alone does not mark setup complete.
Notebook startup, projection changes and SQL/MCP readiness still take time:
operational mode is lighter, not an instant/live feed.
Do not overlap Core ingestion and standalone energy refreshes against the same
GeoContext tables. Parallelism overlaps independent Core/energy branches, not
multiple writers to one energy snapshot.

### Map-agent publication and payload checks

Map-agent publication validates the current read-model run through its published
MCP tool. It retries unsuccessful grounding checks up to three times, 30 seconds
apart, to allow Lakehouse SQL synchronization; exhausted checks fail with the last
tool response. Publication alone is not proof of working map chat.
Retries with unchanged source Delta versions reuse the last complete owned
projection and its run ID, rather than rewriting the tables and restarting SQL
synchronization. Matching draft and published agent definitions are not republished;
the grounding canary still runs before map chat is reported ready.
Definition comparison ignores service-added optional JSON nulls, so an unchanged
source is not rewritten or republished merely because Fabric hydrated its metadata.
Projection reuse also checks the projection-contract version and refuses reuse
when any owned model table was missing before creation. Increment that version
when changing projection semantics, even if source Delta versions are unchanged.

Map queries use the GeoContext SQL connector for bounded identity/viewport
selection, then retrieve complete feature payloads through the existing
`HydroGeoFeatures` and `HydroGeoReservoirAreas` Eventhouse Delta shortcuts. No
data is copied. Geometry and property JSON must not pass through Lakehouse SQL:
its [Delta STRING mapping](https://learn.microsoft.com/fabric/data-warehouse/data-types#autogenerated-data-types-in-the-sql-analytics-endpoint)
is `varchar(8000)` and truncates larger payloads. Missing or inconsistent shortcut
payloads fail explicitly; there is no fallback to truncated SQL JSON.
Full-size reservoir polygons use indexed ring containment and bounded candidate
checks so validation preserves holes and boundary semantics without repeatedly
scanning every edge for every sample.
The map-agent projection normalizes negative/nonfinite physical measurements to
NULL, matching the map's exclusion of invalid installed capacities. Current-view
questions use the supplied browser summary; SQL queries remain authoritative for
complete imported-dataset totals, not a substitute for rendered-map membership.
The NVE inventory itself is Norwegian; its asset `country_code` values can be
NULL. Both agent-level and source-level SQL instructions prohibit using that
column as an asset-country filter. Source instructions also distinguish source
ingestion timestamps from projection build timestamps and require checking
unexpected empty results against source counts. Agent instructions alone do
not reliably constrain the separate natural-language-to-SQL generation step.
Live acceptance on October 28 verified 2,010 imported plants, 35,707.0066 MW
of known capacity and 33 plants with unknown capacity, including a formerly
failing paraphrase through hosted Map Chat. Adamselv's 50 MW, owner and NO4
price area also matched the independently queried imported source row.

## 6. Set up Foundry agents

### Persistent Foundry agents

Confirm **Azure subscription**, then run **Set up Foundry agents**. It checks five
specialists and `hydro-orchestrator` against this checkout and the selected Fabric target.
An existing project/model may still contain missing or older agents. Setup reuses matching
connections and specialists, creates versions only for changed/missing agents, builds only
missing source-fingerprinted images, and republishes the hosted coordinator only when its
image or runtime configuration differs. Each reuse/update is logged, with specialist
`current / updated / created` counts. It does not publish the website.

**Foundry setup can create Fabric resources.** Before configuring the Hosted Agent,
setup creates or reuses its required AppBackend gateway. On a fresh target this uses
Rayfin with `--exclude-services staticHosting`, so Fabric can already show the
`hydro-operations-ui` **App** item with its SQL database, SQL analytics endpoint and
GraphQL API. That is backend provisioning, not website publication or proof that
the Foundry agents are ready. Progress labels it **Fabric backend gateway**, followed
by **Foundry agents** and **Hosted Agent**. Only after Foundry setup succeeds should
**Deploy app** publish the website and run its authentication/CORS/connector checks.
`FOUNDRY_SETUP_SUCCESS` confirms configuration readback, not agent execution.

Fabric IQ connections use the Fabric API audience
`https://api.fabric.microsoft.com` and `metadata.type=fabric_iq_preview`,
matching portal-created Data Agent tools. Setup reuses an existing verified
workspace-specific Fabric endpoint only for the selected workspace and item;
fresh targets otherwise use the documented generic Fabric MCP endpoint.
Conflicting workspace-specific endpoints fail rather than choosing an arbitrary
connection. Portal-created connections are not deleted.

The app's native route pins the `hydro-fabric-iq-tools` Foundry toolbox as its
connection manifest, bound to the verified `hydro-fabric-iq` connection.
Setup creates or versions it only when
needed, preserves existing toolbox policies/skills/metadata, promotes its default
version and verifies readback. Prepared app deployment verifies it without
repairing it. Each run verifies the immutable version and connection target, then
uses the published Data Agent's native Responses interface with the original
question and the signed-in user's verified Power BI token lease. Live source discovery
verifies the deployed AppBackend origin against its workspace, item and current capacity.
Native execution rechecks the capacity before using that verified origin; capacity drift
fails and requires the canonical deployer. This avoids the private workload-routing token
operation that rejected the SPA lease. Never copy a generated capacity URL between targets.
No MCP query or intermediate Fabric IQ model runs in this
answer path; the prompt specialist and MCP utility remain for compatibility.
Native acceptance found that Fabric MCP declined ordinary cross-source questions,
while the [official Responses interface](https://learn.microsoft.com/fabric/data-science/fabric-data-agent-sdk)
successfully executed the ontology's sandboxed cross-source combine. This transport
change is explicit, not a retry/fallback to direct tables or another identity.
For compatibility-path MCP execution, the hosted runtime must forward `x-agent-foundry-call-id` on
every toolbox request, using the agent credential for the toolbox boundary and
the connection's delegated user identity downstream. A successful direct toolbox
test does not certify this hosted caller-context boundary.
The primary Responses path instead uses the existing per-request delegated leases
and application authorization; it does not claim managed MCP execution-policy enforcement.
Original source text and resource receipts are retained; downstream analysis and
approval drafts are separate, never replacements for the source answer.

The ontology supplies the associated Lakehouse and Eventhouse execution sources;
standalone duplicates of those sources are removed. The explicit operational SQL
adapter remains necessary while the ontology's SQL association supplies context
but not an independent SQL execution tool. Only the five operational tables are
selected; visible but unselected SQL control tables are not extra query sources.
Native JSON result files and their typed tables are retained without rewriting the
answer. Quality/work evidence must match successful native source-query and combine
receipts; row-limited reads, unmatched keys or failed coverage cannot certify completeness.
Dependent specialists receive typed evidence and the original source answer, not duplicate
result documents or provider execution traces. Complete artifacts remain in the audit.
The same quality/work completeness gate applies to semantically selected native steps.
Existing temporal, identity and duplicate-work checks still apply before any approved write.
Check actual tool output during compatibility-path native acceptance: a completed Foundry response
can still include an error in `raiPostToolAnnotations`. Endpoint readiness or
successful tool discovery alone does not establish a successful native answer.

- **Reuse a specific project:** enter **Foundry project name**.
  Without a name, setup uses saved settings, the target-named project,
  or the sole available project. Choose a model only for a specific deployment.
- **Duplicate project names or scoped access:** paste the full project ARM ID
  into **Foundry project name**. No separate resource-ID field is needed.
- **Fabric App authentication:** automatic registration discovery/reuse is the
  default. Select **Use a pre-created app registration** only to choose a specific
  target-tenant SPA, then enter its Application (client) ID. The choice is shared
  across Foundry, full workflow and app publication for this tenant/workspace.
  An explicit selection requires a valid GUID and is verified before deployment;
  redirect, permission and consent setup remains automated.
- **Work-order permissions:** the launcher preserves this target's current policy
  without a UI override; enabled writes require human approval.
  A new coordinator defaults to disabled writes. To deliberately change the
  existing target's policy, set the process-local
  `$env:HYDRO_PRODUCTION_WRITES_ENABLED = 'true'` (or `'false'`) before the
  canonical combined CLI command in section 7. Later deployments without this
  override preserve that live value. This does not grant SQL permissions or
  create a work order: each creation still requires a fresh draft, explicit
  human approval and backend validation/readback. Remove the process-local
  override afterwards so it cannot affect another target.
- **Automatic configuration:** project endpoints, connected registries and ontology
  graph ownership are resolved internally. The launcher does not accept graph-binding
  JSON. Missing or ambiguous prerequisites produce explicit errors; specialist
  overrides remain available through the documented command-line configuration.
- **New project:** select **Azure subscription**, enter **Foundry project name** and
  **Model deployment name**, then select **Check this box if you want to create a
  new Foundry project with the multi-agent setup.**
  The exact project name is used. A ready deployment alias in the selected
  subscription is resolved to its catalog model/version; catalog chat model
  names also work. Both standalone Foundry setup and Full Deploy use this flow.
  Approval resets on reload or target change.
- **Defaults:** target/project-hashed supporting resource names, capacity region,
  catalog default version (or the resolved deployment's version) and minimum/default
  capacity. Setup prefers regional Standard, then DataZoneStandard, then
  GlobalStandard; the form discloses global inference. Provisioned capacity is
  never automatic. Catalog availability does not guarantee quota; unavailable or
  ambiguous model identities and missing access stop setup explicitly.

Project/model preflight precedes AppBackend creation. Same-target Foundry hints
survive backend rotation. Missing/inaccessible selections fail; they are never
silently replaced.
[Architecture and configuration reference](../docs/foundry-deployment-and-multi-agent-setup.md).

## 7. Publish the Fabric app last

Choose **Deploy app**. It verifies prepared agents, publishes the website and checks
runtime/CORS, SQL and authentication. It does not rebuild agents or seed data.
The launcher preserves the target's prepared energy-map setting; app publication
does not offer a second enable/disable selector. Run **Energy setup** first to
prepare map data and enable its navigation.
Readiness requires browser-equivalent preflights and POST checks for `/graphql`
and `/api/auth/v1/token`, plus `/connectors/hydrolakehouse/graphql`,
`/connectors/hydrooperations/graphql` and, with energy enabled,
`/connectors/hydrogeo/graphql`; missing CORS headers or token HTTP 5xx fails deployment.

Both Lakehouses use separate delegated, read-only `fabric-sqlanalytics` connectors.
Operational SQL uses the delegated `fabric-sqldatabase` connector. The deployer
resolves both Lakehouse SQL endpoint IDs from live workspace metadata, validates
their distinct identities, and applies each connector with an explicit success receipt.
Energy-disabled targets omit the GeoContext connector declaration.

The **Norwegian Energy Map** uses the selected workspace's owned GeoContext
Lakehouse and its verified SQL endpoint, not a saved workspace or `V6` default.
Both Geo notebooks receive that Lakehouse binding; refresh passes its item ID and
the current workspace ID. An empty environment suffix resolves from the bound
GeoContext, while renamed Lakehouses require the verified item ID. Cross-workspace
or mismatched bindings stop before writes. The refresh pipeline runs
`Geo_002_publish_map_agent` only after successful ingestion, so map chat sees the
new read models. Routine deployment also converges missing/stale map-agent
publication independently, without rerunning ingestion or resetting core data.

No standalone `Hydro_STID_API` setup or legacy GraphQL source-rebinding is performed.
Existing standalone items are not automatically modified or deleted. Rayfin's managed
GraphQL transport is still part of its connector implementation, not the retired API.
The automated Entra SPA setup remains necessary for direct Eventhouse/live-frequency,
Fabric ontology/jobs/Data Agent and Foundry calls; it cannot be removed merely by
switching the Lakehouse transport. GeoContext external aliases are retained for
optional map Data Agent access, not browser map queries.

Canonical agent/CLI command, from the repository root:

```powershell
python Raw\workspace-reset\deploy_fabric_app.py `
  --tenant <tenant-guid-or-domain> `
  --workspace <workspace-guid-or-name> `
  --push-config
```

Add `--client-id <verified-spa-guid>` only when needed. CLI module selectors are
`--stage app`, `--stage foundry`, and `--stage energy`; no selector retains combined
setup/publication. The UI does not require GitHub write access.

`--push-config` requires a clean, pushed **non-main branch** tracking
`origin/<same-branch>`. Merge generated-origin configuration normally.
Never replace the engine with bare `npm run deploy` or `rayfin up`.

## 8. Verify the result

Require **`SUCCESS` and `DEPLOYED_APP_URL`**, then open the URL and sign in.
Test the sources, graph and agent flows you use. Validate approved writes and
Teams/email delivery separately; hosting success is not runtime acceptance.

If a draft reports **Production work-order writes are disabled; no SQL write was
attempted**, the coordinator's write-policy flag is off, not a SQL write failure.
Review the target policy using section 6. Do not retry an expired or previously
blocked card after changing it: request fresh evidence and review a new draft.

### Independent operational SQL lifecycle

NB01 creates `RTI_Demo_SQLDatabase_<suffix>`; NB11 owns additive seeding and source
publication. The `hydrooperations` connector uses that independent database,
not the AppBackend child. Routine app deployment needs no manual migrations or
reseeding. Preserve retained legacy databases.
The hosted Administration guide does not offer a second seeding action. Its
steps are sign-in, start stream, connect STID, connect telemetry and connect
weather. STID/weather can connect independently of telemetry startup. Existing
in-flight seed jobs from older app versions can still be monitored to completion;
the app no longer starts NB11 or a client-side seed fallback.
[Cutover safeguards](../docs/ontology-test-review.md#october-25-canonical-sql-naming-cutover-completed).

## Redeploying to a different tenant, workspace, or region

Prepare the new target, then use the same modules. A same-tenant move can reuse
the SPA; another tenant requires a registration verified there.

The engine detects capacity/target drift and backs up only `rayfin/.env`,
`rayfin/.env.local` and `rayfin/.deployments.json` when rotation is needed.
**Never delete deployment state, copy capacity-specific endpoints or recreate old
origins from stale configuration.** Capacity must be active; resizing, reassignment
or resume requires explicit approval.

## Faster deployment and a simpler operator path

### Independent local modules; app publication last

For website-only changes, use **Deploy app**. Dependencies, backend and unchanged
images are reused; runtime/auth checks remain mandatory.

### Speed changes and measurements

Elapsed time is shown in the UI; detailed `DEPLOY_TIMINGS` receipts remain in logs.
Historical measurements: [deployment record](../docs/ontology-test-review.md).

## Deploying the merged feature branch

Use the pushed branch containing your changes. Branch selection or website
publication does not configure provider authentication or ingest data.

## Optional isolated feature workspace without Fabric Git

`FABRIC_FEATURE_CONFIG` is for deliberate isolated bootstrap, not routine updates.
[Feature bootstrap reference](../Raw/workspace-reset/README.md).

## Troubleshooting

| Failure | Next action |
|---|---|
| Permission, consent or private endpoint | Give the exact error to the resource owner; do not weaken access controls |
| Stale agent/source configuration | Foundry setup, then app deployment |
| Native graph not materialized | Use the explicitly labelled live-source mode, or complete the portal action for native GQL; never fabricate graph ownership |
| Weather provider HTTP 401 | Credential owner checks the named provider's Key Vault key: UKMet Global Spot/Land Observations by default; `mai-weather-api-key` only for opted-in Aurora. Rerun Weather only |
| Energy/Weather source failure | Fix that source's credentials/connectivity, then refresh it |
| Paused capacity or unsupported region | Fix the target prerequisite, not the app build |
| Sign-in popup blocked | Allow normal browser sign-in and retry |

Further reference: [app architecture](README.md),
[Foundry](../docs/foundry-deployment-and-multi-agent-setup.md),
[agent acceptance](AGENT-ACCEPTANCE.md).
