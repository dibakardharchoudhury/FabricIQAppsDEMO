# Deploy Hydro Operations

Use the **local deployment app** for guided setup. It calls the same
[`Raw/workspace-reset/deploy_fabric_app.py`](../Raw/workspace-reset/deploy_fabric_app.py)
engine used by command-line operators. Do not assemble a separate Rayfin,
Entra or SQL deployment sequence.

## Start here: choose your interface, not a different deployment process

| Your target | Choose in the local app |
|---|---|
| Empty workspace | **Full workflow**: Git import, core setup, selected energy sources, Foundry setup, app publication |
| Existing workspace, update website only | **Deploy app** |
| Agent code, model, project or source identity changed | **Set up Foundry agents**, then **Deploy app** |
| Add/update energy data | **Set up energy sources**, then **Deploy app** |
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
existing project's **full ARM resource ID** in Advanced Foundry settings.
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

For an empty workspace, use **Full workflow**. Supply:

- Source repository/branch and an approved saved GitHub connection, or a PAT
  only when creating a connection. The PAT is not stored on disk.
- Environment suffix, your Key Vault URI, target Teams team/channel and alert
  email recipient. Advanced settings expose secret names, run-as guard and
  notebook timeout. Blank run-as means the signed-in deploying user.
- Existing Foundry settings, or explicit opt-in to new billable resources
  with a region-supported model/version/SKU/capacity.

Core setup provisions Lakehouse, Eventhouse, independent operational SQL,
ontology generation **2**, dashboard, Data Agent and retained Operations Agent
capabilities. NB11 runs after the agents and Weather schema setup, inserts
missing operational demo records and publishes the SQL Data Agent source.
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

**Set up energy sources** imports/prepares only its ingestion artifacts, reuses
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
with repository write access. The command without `--stage` retains the combined
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

Existing `HydroOperationalStore` has an immutable legacy display name:
Fabric rejects rename with `SQLDatabaseDisplayNameCannotBeChanged`.
It can remain in the setup folder without changing identity. Matching a new name
requires a controlled replacement, exact verified copy, source/ontology/
connector/agent rebinding and a retained read-only archive. The setup/deployer
excludes a managed archive only when its marker identifies the exact available
active replacement; ambiguous or incomplete cutovers fail. Never silently create
a duplicate or delete either database. FEATURE's completed naming cutover is in
the [deployment record](../docs/ontology-test-review.md). AppBackend's retained
child database/GraphQL item is platform-managed; removing it is not a safe
operational-store cleanup.

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
