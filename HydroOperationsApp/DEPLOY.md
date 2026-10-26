# Deploy Hydro Operations

Use the **local deployment app** for guided setup. It calls the same
[`Raw/workspace-reset/deploy_fabric_app.py`](../Raw/workspace-reset/deploy_fabric_app.py)
engine used by command-line operators. Do not assemble a separate Rayfin,
Entra or SQL deployment sequence.

## Quick start: one path for each job

1. From the repository root, run `python Raw\workspace-reset\launch.py`.
   In the browser, **sign in**, then enter the **tenant** and **workspace** on the left.
2. Choose the task below. Keep automatic settings unless the app reports an
   ambiguity or your administrator has given you a specific resource ID.
3. Follow the progress panel. If it stops, resolve the named prerequisite and
   retry that module. **Do not delete/reset the workspace to fix a deployment.**

| What you need | Do this |
|---|---|
| Update an already prepared app | **Deploy app**. Normally no other inputs are needed. |
| Update agents | **Set up Foundry agents**, then **Deploy app**. |
| Initialize an empty workspace | **Full Deploy**. Review the source, supply Key Vault URI, Teams team/channel and alert email, then run. Core setup includes Weather and energy sources. Existing Foundry resources are reused by default. |
| Create Foundry resources for the first time | Expand **Create a new project instead (billable)** and explicitly approve creation. Enter your approved new model deployment name, version, SKU and capacity. |

### What you actually need to enter

| Setting | Normal behavior |
|---|---|
| Tenant / workspace | Tenant may be remembered. All workspace fields start empty on every page load, even if browser storage or local configuration contains a previous workspace. Enter the workspace yourself; the Core/Full Deploy workspace fields then mirror it. |
| Repository / branch | Filled from this checkout. |
| Key Vault URI / Teams destinations | Restored from saved values for this target, or configured defaults. Otherwise ask the resource owner; the app does not invent destinations. |
| Alert email | Suggests the signed-in user's email when the tenant GUID matches. Confirm or replace it. |
| Notebook credentials / runtime | Uses secret **names** `tenantid`, `clientid`, `clientsecret`, the signed-in deploying user, and 3600 seconds. Current settings are summarized above **Change notebook defaults (optional)**. |
| Azure subscription | Auto-selected only when it matches the target tenant and is the active signed-in subscription or the sole match. Otherwise choose from the loaded list. |
| Foundry project / model | Reuses saved settings for this target, then verified discovery. Supply the project resource ID or deployment name only when needed. |
| SPA / registry / project endpoint | Automatic discovery; optional overrides are grouped together. |
| New-project region / model catalog name | Defaults to the Fabric capacity's region / deployment name. Version, SKU and capacity must be approved for your subscription and region; they are not guessed. |

The two Foundry forms share remembered non-secret overrides for each target.
The normal Foundry form shows only **Azure subscription**: confirm its automatic
selection, then run setup. Existing project/model selection and troubleshooting
overrides stay collapsed unless discovery asks you to choose a resource.
New-project creation is a separate, collapsed path; opening it does not authorize
spending. Its model settings are separate from existing-project choices. While
creation is selected, saved existing project, endpoint and registry overrides are
not sent to provisioning. Switching back restores those choices.
**Create new resources is always off again after changing targets or reloading**;
it is never a remembered spending authorization.
No PAT, token or credential secret value is saved by these forms.

Core/Full Deploy edits (including Key Vault URI and Teams IDs) are saved
automatically in the gitignored
[`pipeline-defaults.local.json`](../Raw/workspace-reset/webapp/pipeline-defaults.local.json)
on this machine, not just in browser storage. Wait for **saved on this machine**
before closing the page. Reloading or using another browser restores these values.
Targets remain isolated; clearing a field is also saved. Workspace names and IDs
share settings only when their relationship is known from the deployment engine's
resolved local target configuration, then that alias is remembered. Unknown targets
stay blank rather than borrowing another target's destinations. Existing single-target
configuration and browser-only entries are migrated when saved. File/read/write
errors are shown in the form; saving preferences never starts a connection test.
Foundry overrides and sidebar preferences still use browser storage.
Workspace selection is never restored automatically. Saved Key Vault/Teams values
remain on disk and load when you enter their tenant/workspace. Until then, setup
shows a workspace-entry prompt and deployment actions remain disabled.

Core energy setup runs source ingestion and serving-model verification only.
The optional map-chat publisher is a separate, explicitly enabled component;
an energy refresh must not run an unbound chat notebook or create a chat agent.

**Navigation:** **Full Deploy** is always a separate top-level choice. Expand
**Deployment Actions** for individual modules and maintenance actions.
Use **Hide sidebar** to give the form the full content width; **Show sidebar**
remains available above it. The sidebar preference is remembered in this browser.
Connection tests are never automatic. Click **Test connection** either in
**Full Deploy → GitHub sync** or **Sync from GitHub** to test in place without
deploying. Each uses its own source fields and shows its own results.
Editing target/source fields, signing in, or selecting a module cannot start
a background connection test.

**One unavoidable manual prerequisite:** after core ontology setup, materialize
the selected ontology's graph in Fabric (**Manage graph → select eligible
entities/relationships → Continue → Materialize**). Foundry and final app setup
can stop until this is complete. Complete the portal action, then retry the
failed module; do not rerun Git import or core setup.

**Completion:** the deployment log must print `SUCCESS` and `DEPLOYED_APP_URL`.
Open that URL and sign in. A successful website deployment is not proof of agent
execution, Teams/email delivery or complete data ingestion.

The sections below are the access checklist and detailed reference, not extra
steps to perform for every deployment.

### Find your Teams team and channel IDs

1. Open the intended team and channel in Microsoft Teams.
2. Select the channel's **More options (...) → Get link to channel / Copy link**.
3. From that link, copy the GUID in the `groupId` query parameter into **Teams
   team id**. Do not use `tenantId`.
4. Copy the path segment immediately after `/channel/` into **Teams channel id**,
   URL-decoding it first: `%3A` becomes `:` and `%40` becomes `@`. Keep the whole
   `19:...@thread.tacv2` identifier, not the channel's display name.

For example, `/l/channel/19%3Aexample%40thread.tacv2/General?groupId=<team-guid>`
contains team ID `<team-guid>` and channel ID `19:example@thread.tacv2`
(illustrative values only).
Microsoft documents the format in
[Deep link to navigate to channel](https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-teams#deep-link-to-navigate-to-channel).
Both ID fields link to that documentation in the local app. These IDs select a
destination; they do not grant access or prove alert delivery.

## Start here: choose your interface, not a different deployment process

| Your target | Choose in the local app |
|---|---|
| Empty workspace | **Full Deploy**: Git import, core setup (including Weather and energy), Foundry setup, app publication |
| Existing workspace, update website only | **Deploy app** |
| Agent code, model, project or source identity changed | **Set up Foundry agents**, then **Deploy app** |
| Refresh energy data only | **Deployment Actions → Refresh energy sources**; no core or Foundry rerun |
| Different tenant/workspace | Prepare that target's prerequisites, then use the same modules |

**Never reset, delete items, rerun core setup, or perform a full Git import just
to update an existing website.** Git import can remove generated workspace items.
Updating a branch field changes Fabric import, not the local app/agent source.
Run the launcher from the checkout you intend to publish.

## 1. Prepare access once

There is no zero-administrator deployment guarantee for arbitrary tenants.
Reuse prepared resources to keep the routine operator's permissions small.
Global Administrator and subscription Owner are not routine requirements.

### Least privilege: operator versus one-time administrator preparation

| Resource | Routine operator needs | Resource owner prepares once |
|---|---|---|
| Fabric workspace/capacity | Access to read, create, update and execute the items used by the selected module; Git operations can require workspace Admin | Active assigned capacity, supported region/preview availability, tenant API/Copilot settings |
| Browser SPA | Verified target-tenant client ID; permission to update the app registration when redirects change | SPA and enterprise application; delegated permission consent or the exact action reported by live-auth setup |
| Notebook identity | Approved Key Vault URI and secret **names**, not secret values | Service principal, required workspace/API access, stored credentials and private connectivity |
| Foundry | Scoped project/connection/agent operations, model use and necessary account/registry metadata reads; builds also need image-publish rights | Existing project, model quota, registry, scoped role assignments and managed-identity access |
| Final app publication | Read prepared Foundry definitions and project/connection ARM metadata; workspace/app deployment permissions | Foundry setup already completed; no project creation or ACR build rights needed for app-only publication |
| Teams/email | Target team/channel and independent alert recipient | Authorized Teams access, mailbox-backed Outlook OAuth connection and any required approvals |

Leave **Create Foundry** off when subscription/resource creation rights are
unavailable. For first-time setup with only scoped resource access, enter the
existing project's **full ARM resource ID** in the visible **Existing project resource ID** field.
The deployer verifies that exact project without listing all subscription
resources. Same-target reruns reuse and verify its saved subscription/project.
Fabric capacity and Foundry can use different subscriptions in the same tenant.

### No admin rights? Hand this to your Entra admin

Ask the appropriate owners to prepare the table above and give you the tenant,
workspace, SPA client ID, Key Vault URI/secret names, Teams destination and
Foundry project ARM ID/model. No secret belongs in the launcher or Git.

The engine runs `setup-live-auth` to preserve SPA redirects and configure/check
delegated permissions and consent. If it reports an action your identity cannot
perform, give **that exact output** to the administrator. Do not grant blanket
roles, edit redirects/permissions by hand as a first step, or remove old redirects.
App ownership does not itself authorize tenant-wide consent.
For a permission-only preview, `npm run setup-live-auth:dry` in this app folder
prints the configured changes without writing them.

## 2. Start the local app

Install Python **3.11+**, Git and Azure CLI. Node/npm must be available for app
deployment; the engine resolves **Node 24** and restores the locked packages.
Foundry setup also needs Azure Developer CLI (`azd`).

From the repository root:

```powershell
python Raw\workspace-reset\launch.py
```

Or double-click
[`Start Fabric Demo.cmd`](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd).
The browser opens <http://127.0.0.1:5000>. Sign in, then enter the target tenant
and workspace GUID/name. Do not enter an Azure subscription as the workspace.
The source repository and branch prefill from this local checkout; verify the
branch is pushed before importing it.

## 3. Prepare core Fabric data, only when needed

For an empty workspace, use **Full Deploy**. Supply:

- Source repository/branch and an approved saved GitHub connection, or a PAT
  only when creating a connection. The PAT is not stored on disk.
- Environment suffix, your Key Vault URI, target Teams team/channel and alert
  email recipient. The workspace is shown directly; notebook settings summarize
  secret names, run-as identity and timeout. Use **Change notebook defaults
  (optional)** only to override them. Blank run-as means the signed-in deploying user.
- Existing Foundry settings, or explicit opt-in to new billable resources
  with a region-supported model/version/SKU/capacity. First-time creation settings
  appear directly when you opt in; they are not hidden under Advanced.

Core setup provisions Lakehouse, Eventhouse, independent operational SQL,
ontology generation **2**, dashboard, Data Agent and retained Operations Agent
capabilities. NB11 runs after the agents and Weather schema setup, inserts
missing operational demo records and publishes the SQL Data Agent source.
The local **Core setup** action then runs energy-source preparation under the
same monitored job. **Full Deploy** reuses this exact sequence, so energy runs
once, not as an additional optional stage. Failure in either part stops later
work. Foundry setup remains a separate module.
Real setup/publication/readback failures stop the workflow. Only NB09's exact
`This API version is not supported for the specified Ontology item` response,
after verified v2 publication, is a known product limitation rather than runtime
success. There are no agent mode/skip flags.

## 4. Materialize the selected ontology graph

This remains a **manual portal prerequisite**, not a published REST automation
contract: in the selected v2 ontology choose **Manage graph -> select eligible
entities/relationships -> Continue -> Materialize**.

The ontology/Data Agent generally does not require materialization; this app's
native graph canvas/tree/scopes do. Start with eligible static asset hierarchy
entities and relationships. Do not remove valid time-series bindings merely to
make an entity eligible.

The engine uses authoritative Ontology-to-GraphIndex lineage to generate
`RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING`. If lineage is unavailable, provide an
operator-verified JSON object with `workspaceId`, `ontologyId`, `graphModelId`
in Advanced settings. Never infer ownership from names, a sole graph or structure.
See [native graph details](../docs/knowledge-graph.md).

## 5. Prepare energy and Weather sources

Energy preparation is included in local **Core setup** and **Full Deploy**.
For later data-only updates, **Refresh energy sources** imports/prepares only its ingestion artifacts, reuses
GeoContext and verifies all twelve serving layers and nine reservoir polygons.
GeoContext and its SQL endpoint belong in `RTI_DEMO_<suffix>`; placement preserves
item identity and storage paths. The module does not rerun core setup or SQL seeds.
Map visibility is preserved only for the same tenant/workspace; explicit enabling
requires live data verification. Dedicated Map Data Agent chat is a separate
prerequisite, not implied by successful energy ingestion.

Core setup creates Weather tables and enables the Weather schedule; it does not
immediately execute all provider ingestion. Run **Connect weather** in the hosted
app or wait for the schedule, with valid provider credentials and connectivity.
Provider failures must be fixed, not hidden by stale data or a successful website.

## 6. Set up Foundry agents

### Persistent Foundry agents

Use **Set up Foundry agents** after source/agent changes. Reuse the selected
project; creating a new project is a separate billable opt-in. This module
prepares/verifies connections, QA, RCA, WorkOrder, FabricIQ and Weather specialists,
and the Hosted coordinator. Unchanged images/definitions are reused.
It does **not** publish the website.

An endpoint alone may require subscription discovery; supplying the project ARM
ID avoids that discovery. Tenant, subscription, endpoint and live source identity
are still verified. A missing/inaccessible selected project is a hard failure,
not permission to create a replacement.
See [Foundry setup and runtime architecture](../docs/foundry-deployment-and-multi-agent-setup.md).

## 7. Publish the Fabric app last

Use **Deploy app**. Normally only tenant/workspace are needed; supply the existing
SPA client ID when discovery is ambiguous. Advanced graph binding and explicit
map visibility remain available. There are no Foundry provisioning controls here.

For command-line deployment from the repository root:

```powershell
python Raw\workspace-reset\deploy_fabric_app.py `
  --tenant <tenant-guid-or-domain> `
  --workspace <workspace-guid-or-name> `
  --stage app --push-config
```

Add `--client-id <verified-spa-guid>` only when needed. The local UI does not
require GitHub write access; `--push-config` is for command-line operators/agents
with repository write access. It requires a clean, pushed **non-main branch**
tracking `origin/<same-branch>`; the engine refuses automatic commits to `main`.
Use a deployment branch for generated-origin persistence, then merge the verified
configuration through your normal repository process. The command without `--stage` retains the combined
setup-and-app behavior. `--stage energy` and `--stage foundry` select the same
independent lifecycle modules as the UI, not agent exemptions.

The engine validates configuration before building, rejects empty/unverified SPA
IDs, checks prepared Foundry code/source/runtime identities read-only, reapplies
backend runtime/CORS and both connectors, preserves existing SPA redirects, runs
live-auth setup, and checks generated endpoints and browser-equivalent preflights
and POST readiness. Do not replace it with bare `npm run deploy` or `rayfin up`.
The locked Rayfin release is **1.36.2**, not a global CLI installation.

## 8. Verify the result

Require **`SUCCESS` and `DEPLOYED_APP_URL`**. Then sign in normally and check one
actual read from each source you intend to use, the native graph, energy layers
and the required agent flow. Confirm writes/alerts only through separately
approved execution and delivery checks.

A protected sign-in gate, item creation, notebook completion, connector apply,
or Foundry configuration is **not** authenticated runtime certification. Do not
report unavailable sources as connected or show previous analytical values after
a failed read. Analytical caches remain bounded, identity-keyed and in memory;
operational SQL is independent of Lakehouse/Eventhouse availability.

### Independent operational SQL lifecycle

Fresh core setup creates `RTI_Demo_SQLDatabase_<suffix>` in
`RTI_DEMO_<suffix>`, alongside Lakehouse/Eventhouse. NB01 owns creation; NB11
owns additive seed/source publication. The app uses the independent
`hydrooperations` connector, not its retained backend child database.

FEATURE's one-time replacement of the immutable legacy `HydroOperationalStore`
is complete. Routine deployment automatically
selects `RTI_Demo_SQLDatabase_V11` and applies its connector; **no manual SQL,
GraphQL, migration or seeding steps are needed**. The original SQL remains a
write-blocked archive, and the platform-managed AppBackend child is not used for
operational data. Do not delete either retained database.

Migration safeguards and the automatic stale-attachment repair are documented
in the [verified cutover record](../docs/ontology-test-review.md#october-25-canonical-sql-naming-cutover-completed),
not additional operator steps.

## Redeploying to a different tenant, workspace, or region

Prepare the new target, then run the same engine/modules. A new workspace needs
its own data, verified v2 ontology, graph binding and prepared agents before
app-only publication. A same-tenant move may reuse the tenant SPA; another tenant
requires a registration verified there.

The engine detects target/capacity drift and backs up only `rayfin/.env`,
`rayfin/.env.local` and `rayfin/.deployments.json` when rotation is required.
Never delete deployment state, copy a generated `pbidedicated.windows.net`
endpoint or recreate an old hosting origin from stale local configuration.
The assigned capacity must be active; resuming a paused capacity requires explicit
authorization and scoped rights. Do not resize or reassign capacity implicitly.

## Faster deployment and a simpler operator path

### Independent local modules; app publication last

Routine website updates do not run ingestion, SQL bootstrap, project discovery
or agent publication. Existing dependencies, backend and Hosted images are reused.
Required runtime/CORS/authentication checks remain.

### Speed changes and measurements

Every module emits `DEPLOY_TIMINGS=`; the UI renders module timing receipts.
Measure service time instead of promising an instant deploy. October 25 FEATURE:
energy **19m54s**, Foundry **3m29s**, final app **9m41s**. This is not proof of a
faster final publication than the earlier **9m32s** combined run.
Detailed receipts and honest runtime limits are in the
[FEATURE deployment record](../docs/ontology-test-review.md).

## Deploying the merged feature branch

PR #19 (energy) and #20 (Weather) are included in `feat/dibakar`; their open PRs
target `main`. Use a pushed checkout/branch containing those changes.
Selecting a branch is not source provisioning, provider authentication or
deployment. FEATURE's previously observed Weather provider HTTP 401 and dedicated
map-chat prerequisite are not resolved by publishing the website.

## Optional isolated feature workspace without Fabric Git

The existing `FABRIC_FEATURE_CONFIG` route remains for deliberate isolated
bootstrap use, not normal website updates. See the
[feature bootstrap reference](../Raw/workspace-reset/README.md).
Do not create a new profile or import/reset a populated workspace to enable energy.

## Troubleshooting

- **Permission/consent/private endpoint failure:** give the exact prerequisite
  output to its resource owner; do not widen roles, open a private vault or weaken
  Conditional Access. Complete the normal resource-specific sign-in challenge.
- **Stale agents/source identities:** run Foundry setup, then app publication;
  never skip verification or silently switch projects.
- **Weather `Aurora STAC search failed with HTTP 401`:** the provider rejected
  authentication. A merged feed/code fix or app redeploy does not repair the
  credential. Have the credential owner verify/replace `mai-weather-api-key` in
  the configured Key Vault through an authorized connection, then run the existing
  Weather pipeline once. Do not paste secrets into the launcher or keep rerunning
  ingestion with the rejected key.
- **Fabric preview/region unavailable or paused capacity:** fix the assigned
  target's prerequisite; repeated app builds will not fix it.
- **Graph ineligible/slow:** inspect native graph scope and upstream query timing;
  do not fabricate topology from SQL/STID or repeatedly rematerialize a healthy graph.
- **Energy/Weather provider failure:** fix credentials/connectivity and refresh
  that source only; do not rerun core setup.
- **Browser sign-in pop-up blocked:** allow the normal sign-in flow and retry;
  do not bypass hosting access control.

Architecture and specialist behavior: [app README](README.md),
[Foundry guide](../docs/foundry-deployment-and-multi-agent-setup.md),
[agent acceptance](AGENT-ACCEPTANCE.md). Historical measurements belong in the
[deployment record](../docs/ontology-test-review.md), not the operator checklist.
