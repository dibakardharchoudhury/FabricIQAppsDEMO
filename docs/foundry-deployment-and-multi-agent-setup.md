# Foundry deployment and multi-agent setup

**Operator steps:** [deployment guide](../HydroOperationsApp/DEPLOY.md).
This page covers agent architecture and configuration.
Both UI and CLI use [`deploy_fabric_app.py`](../Raw/workspace-reset/deploy_fabric_app.py).

The app reads Core/Weather and GeoContext through separate read-only Rayfin
`fabric-sqlanalytics` connectors; operational SQL uses `fabric-sqldatabase`.
Eventhouse telemetry/live frequency remains delegated KQL. Optional map Data Agent
external aliases remain available, but browser GeoContext reads do not depend on them.
No standalone STID GraphQL API is provisioned or rebound. Rayfin's managed connector
transport still uses GraphQL internally. The automatically resolved Entra SPA remains
required for Eventhouse, Fabric ontology/jobs/Data Agent and Foundry's delegated-user
authorization; connector migration does not replace those identities.

### Finding Weather in Foundry

Open the project's **Agents → hydro-weather-agent**. It is a prompt specialist
behind `hydro-orchestrator`, not a separate hosted service. Playground responses
do not prove source freshness or connector execution.
[Target-specific deployment and acceptance evidence](ontology-test-review.md#latest-canonical-deployment-and-authenticated-acceptance-hosted-agent-10).

## Start a deployment

1. Start the local app from the intended pushed checkout.
2. Complete Core setup, including the verified generation-2 ontology and published Data Agent.
3. Confirm the subscription; run **Set up Foundry agents**.
4. Run **Deploy app**; require `SUCCESS` and `DEPLOYED_APP_URL`, then test the app.

Existing resources are reused. New-project creation requires a chat model name
and explicit resource-creation approval. Website-only updates need **Deploy app** only.

## 1. The multi-agent setup

```text
Operator ─► SPA (Fabric-hosted) ─ signed-in user's delegated tokens ─►  hydro-orchestrator  (Foundry Hosted Agent)
                                                                         Supervisor: deterministic plan → serial
                                                                         specialist steps → evidence certification
                                                                         → grounded briefing
                                       ┌────────────── Foundry Responses ────────────────┐
                                       ▼                 ▼                  ▼             ▼
                               hydro-qa-agent   hydro-rca-agent   hydro-work-order-agent   hydro-fabric-iq-agent   hydro-weather-agent
                                  (Gauge)          (Sleuth)            (Fixer)                 (Sparky)              (Gale)
        Specialist tool calls are executed by the hosted runtime (Node source bridge) under the user's tokens:
        Asset + Weather Lakehouse connector · Eventhouse KQL · hydrooperations shared-SQL connector · published Data Agent connection
```

| Component | Foundry name | Kind | Defined in | Deployed by |
|---|---|---|---|---|
| Supervisor | `hydro-orchestrator` | Hosted agent (Python 3.12 + Node 24 container, Microsoft Agent Framework, `invocations` protocol) | [`HydroOperationsService/`](../HydroOperationsService), [`azure.yaml`](../azure.yaml) | Deployer (build in ACR, publish with azd) |
| Gauge | `hydro-qa-agent` | Prompt agent | [`agentDefinitions.ts`](../HydroOperationsApp/src/services/copilot/agentDefinitions.ts) | Deployer |
| Sleuth | `hydro-rca-agent` | Prompt agent | same | Deployer |
| Fixer | `hydro-work-order-agent` | Prompt agent | same | Deployer |
| Sparky | `hydro-fabric-iq-agent` | Prompt agent | same | Deployer |
| Weather | `hydro-weather-agent` | Prompt agent | same | Deployer |
| Data Agent connection | `hydro-fabric-iq` | Project `RemoteTool` connection (user Entra token) | provisioner | Deployer |
| Ontology connection | `hydro-fabric-ontology` | Project `RemoteTool` connection (user Entra token) | provisioner | Deployer |
| Model | Selected/discovered in the target account; no fixed deployment name | Model deployment | `HYDRO_FOUNDRY_MODEL` | Existing project: must exist; opt-in creation: template provisions the specified catalog model/version/SKU/capacity |

The Supervisor is the hosted runtime, **not a prompt agent**. There is no separate "Chief" prompt agent in the
current code; earlier records that describe a separate Chief prompt agent are historical.

**Roles**

- **Gauge**: facts, telemetry, existing work, charts, requested verification. Read-only.
- **Sleuth**: investigation and RCA. Must cite real evidence IDs and JSON-pointer references and offer competing
  hypotheses. Causes are reported as probable, with likelihood and what would confirm them, never as confirmed.
- **Fixer**: decides whether new work is warranted and stages an editable work-order draft. Cannot write SQL.
- **Sparky**: retrieval from the published Fabric Data Agent, only when the operator explicitly asks for it.

**Request flow**

1. The SPA posts to the Hosted Agent's Invocations endpoint with short-lived delegated tokens. The runtime verifies
   signature, tenant, audience, SPA client and one user identity across all resources.
2. The Supervisor compiles a typed answer contract and a plan of one to eight steps, then runs the specialists
   **serially**, passing earlier reports and immutable evidence forward.
3. Specialists call governed tools. The hosted runtime executes the reads with the user's own tokens, so the agents
   can read nothing the user could not.
4. The runtime certifies receipts, validates every table cell against its source receipt, then asks the model for a
   grounded briefing that must pass identifier/number checks (up to three attempts). A rejected briefing is replaced
   by the verified tables plus a visible *Source limitations* note.
5. Proposed work orders return as editable cards. Nothing is created until the user approves a card, and that write
   path is enabled only by `production_writes_enabled`.

Run state is local filesystem state in the hosted container (`HYDRO_RUNTIME_STATE_DIR`), not a distributed store.

## 2. What the independent Foundry setup module does

The separate setup stage (also used by combined CLI deployment;
[`provision_foundry_agents.py`](../Raw/workspace-reset/provision_foundry_agents.py),
[`deploy_hosted_agent.py`](../Raw/workspace-reset/deploy_hosted_agent.py)):

| Step | Behaviour | Skipped when |
|---|---|---|
| Preflight | Verifies tenant/subscription and resolves project/model before backend creation; validates catalog defaults for an approved new project | never |
| 0. Backend identity | Creates a missing Fabric AppBackend and its SQL/GraphQL dependencies without static hosting | Target already has one |
| 1. Foundry project | Reuses the verified project, or creates target-named resources using `Azure-Samples/azd-ai-starter-basic` with explicit resource-creation approval | Existing project |
| 2. Checks | One Foundry project, model ready, one linked Application Insights, ontology generation 2, exactly one matching published Data Agent | never |
| 3. Connections | Compares `hydro-fabric-iq` and `hydro-fabric-ontology` with the live project connections; writes and verifies only missing or changed connections | source/authentication already matches |
| 4. Prompt agents | Compares the five exported source definitions with live versions, ignoring additional server-managed fields; creates and verifies only missing or changed versions | definition unchanged |
| 5. Image | Fingerprints every file the [Dockerfile](../HydroOperationsService/Dockerfile) packages; builds `hydro-orchestrator:src-<fingerprint>` in the project's ACR with `az acr build` | that tag already exists |
| 6. Configuration | Composes `HYDRO_ORCHESTRATOR_CONFIG` and `HYDRO_FABRIC_SOURCE_CONFIG` from the verified target | never (always compared) |
| 7. Publish | Creates or refreshes the azd environment's project, tenant and registry identity from live metadata, applies runtime settings and publishes; waits until the active version has the exact image digest and configuration | live agent already matches |
| 8. Verify | The existing gate: active, numeric version, immutable digest, source identity and native binding all match | never |

The local app shows these as separate progress stages: *Foundry project*,
*Fabric backend gateway*, *Foundry agents*, *Hosted Agent*. Backend preparation
uses Rayfin with `--exclude-services staticHosting`: the Fabric workspace can
already show the `hydro-operations-ui` **App** item, its SQL database and SQL
analytics endpoint, and the GraphQL API. The Hosted Agent needs the backend's
identity, API URL and publishable key in its verified source configuration.
These resources do not mean the website was published or that the agents are ready.
The standalone Foundry action returns after verified multi-agent configuration;
the separate **Deploy app** action publishes the website afterward.

An existing project and model do not prove that the five specialists and hosted coordinator
exist or match this checkout and Fabric target. Setup checks live state, but an unchanged
setup performs no connection updates, specialist version creation, image builds or hosted
publishes. It logs each reused specialist and a `current / updated / created` summary.
Changed instructions, tools or model update only the affected specialists. A target/configuration
change can require a hosted version without rebuilding an already available image.

Entering the same saved project name retains its verified ARM ID for a direct metadata read.
On first selection by name, only matching project names receive detailed metadata requests;
unrelated projects are not inspected. An explicitly named model deployment is read directly,
without listing other models, and validated once during project preflight.

Consequences:

- **Code changes deploy themselves.** The fingerprint covers the Python service, the Node bridge and the TypeScript
  files copied into the image. A change anywhere there triggers one build and one new Hosted Agent version on the next
  Foundry setup; an unchanged tree requires a single registry lookup. Final app
  publication never builds or publishes the image. App-only verification treats local
  fingerprint drift as advisory after live compatibility checks succeed; it reuses the
  existing runtime, not the newly edited hosted-service code.
- **Build context is exact and temporary.** The deployer stages only that fingerprinted Dockerfile/COPY manifest
  before invoking ACR, preserving relative paths and verifying its image tag. The CLI never packages the entire
  checkout, dependency trees, generated deployment state or unrelated files. It logs file/byte counts and removes
  the staged directory after success or failure; failed builds still stop deployment. The CLI uses
  absolute context/Dockerfile paths from a stable working directory, and `--no-logs` waits for the
  ACR result without Windows-incompatible Unicode log streaming. The remote build timeout is
  20 minutes; the run ID/status and subsequent image digest readback remain required.
- **Target changes redeploy the agent.** A new workspace, capacity, AppBackend, ontology or publishable key changes the
  configuration, so the agent is republished with fresh values (never copied from another target).
- **No tracked deployment target.** `azure.yaml` uses `${AZURE_AI_PROJECT_ENDPOINT}`, not a live demo endpoint.
  Every publish uses a generated, untracked azd project under `~/.fabric-demo/hosted-agent/`, keyed by the full
  verified endpoint. Repository-local azd environments and registry hints cannot redirect a deployment.
- **Creation is opt-in and repeatable.** The new project lives in `~/.fabric-demo/foundry-project/<name>`, where the name
  is derived from tenant, subscription, workspace and the requested project name, plus the
  account name when supplied. The exact requested project name is passed as
  `AZURE_AI_PROJECT_NAME`. The local app's optional **Foundry account name** maps to
  `HYDRO_FOUNDRY_ACCOUNT_NAME` and template setting `AZURE_AI_ACCOUNT_NAME`; it names
  the parent Azure AI Services resource, not the child project. Blank account names
  preserve the existing automatic naming/discovery behavior. A matching existing
  project in the selected account is reused on rerun. Account names are validated
  before provisioning and verified in provisioning readback; conflicting explicit
  project identities fail rather than falling back to another account. Creation
  remains explicitly opt-in. This setting does not rename/move resources or adopt
  an arbitrary existing account in another resource group for a missing project.
  Retain this state for reruns; on another machine explicitly
  select the existing endpoint instead of opting into another project. No tenant-specific name is embedded in source.
- **One target per hosted coordinator.** The stable `hydro-*` names are application contracts, not tenant identities.
  Deploying another workspace into the same explicitly selected Foundry project rebinds that coordinator and its
  connections. Use a separate target-specific project for each concurrently deployed Fabric workspace.
- **Production writes are preserved.** The live agent's `production_writes_enabled` is kept. Set
  process-local `HYDRO_PRODUCTION_WRITES_ENABLED=true|false` before the canonical combined CLI deployment to change it deliberately. A
  brand-new agent defaults to `false`. An unreadable or non-boolean live policy stops deployment unless you explicitly
  choose a write policy; it is never silently reset or coerced from a string.
- **Versions are never deleted.** Each publish is a new immutable version.
- The first run after pulling this feature builds and publishes once, because no fingerprint-tagged image exists yet.

## 3. One-time checklist

**Tools**

- Python 3.11+, Azure CLI, and the Azure Developer CLI `azd >= 1.35.0` (Node 24 is downloaded by the deployer).
- For manual command-line setup, restore the Python dependencies from the repository root:
  `python -m pip install -r Raw\workspace-reset\requirements.txt`. The local launcher handles this itself.
- The azd extensions `azure.ai.agents` and `microsoft.foundry` are installed automatically when missing.
- `az login --tenant <tenant>`. If `azd` is signed out, the deployer opens its sign-in for you.
- No local Docker: images build remotely in ACR.

**Permissions**

- To **create** a Foundry project: permission to create the subscription's resources **and assign roles**
  (for example Owner, or Contributor plus Role Based Access Control Administrator at the appropriate scope).
  Contributor alone cannot create the template's role assignments.
- To publish into an **existing** project: rights to create agent versions in it, and to run builds in its registry
  (for example Contributor on the registry). Chat users need **Foundry User** on the project.

**Either already exists, or you tick *Create the Foundry project* (Section 4)**

- A Foundry resource and project in the **same Entra tenant as the Fabric workspace**, with
  **public network access** (the browser calls it directly). New projects default to the capacity's region;
  model availability and quota must be checked for the selected region.
- A compatible ready OpenAI chat model deployment (tool calling and structured output required), **exactly one**
  linked Application Insights resource, and an Azure
  Container Registry connected to the project as a `ContainerRegistry` connection.
  There is no hardcoded model/version/SKU/capacity. For new projects, supply project and model
  deployment names and explicitly approve resource creation. Existing deployment aliases are resolved
  in the selected subscription to their underlying model/version; catalog chat model names also work.
  The live regional catalog supplies a unique default version when no reference version is selected,
  and a best-effort 75,000 TPM allocation using that model/SKU's token units and available regional quota.
  If 75,000 TPM is unavailable, the largest supported lower allocation is used. Missing catalog token-unit
  metadata is reported explicitly and retains a supported catalog default; allocation is verified after creation.
  Existing allocations and explicit capacity overrides are preserved. Pay-as-you-go SKU preference is Standard, DataZoneStandard,
  then GlobalStandard, with global inference disclosed in the form. Provisioned capacity is never
  automatic. Environment overrides remain available; ambiguous identities or missing reliable defaults
  stop before creation. Azure provisioning rejects unavailable models, regions or quota.

**Always required (not created by this deploy)**

- A verified generation-2 ontology, resolved as `RAYFIN_PUBLIC_ONTOLOGY_ID`, plus the published
  Data Agent `RTI_Demo_Agent_*`. Native graph materialization and
  `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` are optional and used only for the materialized native
  instance-graph view ([knowledge-graph.md](knowledge-graph.md)), not Foundry source identity.
- A ready model with a verifiable positive token allocation. 75,000 TPM is a preference, not a
  prerequisite; smaller allocations continue with an explicit throttling advisory.

## 4. Local deploy app, step by step

| Path | Input |
|---|---|
| Reuse resources | Confirm Azure subscription |
| Reuse a specific project/model | Enter its name under **Project and model choices**; otherwise use saved or automatic selection |
| Create a new project | Project name + model deployment name + explicit resource-creation approval |

The new-project form contains just those two name fields. Region, version, SKU,
capacity, supporting resource names, connections, monitoring and agent deployment
are automatic. No model-settings questionnaire is shown. Existing-project reuse
is unchanged. **Fabric App authentication** retains the optional pre-created SPA
choice; endpoint, registry, graph and write-policy overrides are not exposed in
the form. See [environment variables](#6-settings) for specialist overrides.

On a fresh workspace, run **Full Deploy**. Graph materialization does not gate
Foundry setup or app deployment. `FOUNDRY_SETUP_SUCCESS` verifies configuration, not business
execution. Final publication requires `SUCCESS` and `DEPLOYED_APP_URL`.

The UI does not require GitHub write access. CLI `--push-config` requires a clean,
pushed non-main branch. Job limits: Foundry 90 minutes; app publication 30 minutes.

## 5. Command line

```powershell
python Raw\workspace-reset\deploy_fabric_app.py --tenant <tenant-guid-or-domain> `
  --workspace <workspace-guid-or-name> --push-config
```

Add `--client-id <spa-app-guid>` only if SPA discovery is ambiguous. Run it from the repository root. It is idempotent:
rerun it after any change, to any part of the stack.

**Verify**

- Output ends with `SUCCESS` and `DEPLOYED_APP_URL`.
- Output contains `Hosted Agent hydro-orchestrator version <n> is active on src-…` (when it published) or
  `Hosted agent hydro-orchestrator is current` (when nothing changed).
- **Administration → Agent runtime settings** shows the project endpoint, Invocations URL, runtime version, image
  digest and source digest.

**Optional demo walkthrough, not additional setup gates:** try a direct-source question,
a multi-step question (Gauge → Sleuth → Fixer), and a Fabric IQ question through Sparky.
Exercise Teams/email only when demonstrating those actions; provisioning does not send test alerts.
Native graph materialization is needed only for the optional native instance-graph view.

NB09 publishes and verifies source readback without an automatic MCP facilities query or
answer-format gate. NB11's final SQL-source publication follows. The app uses
Supervisor → Sparky → Fabric Responses afterward. This synthetic-data demo does
not require an automatic SQL/KQL/combined-source runtime matrix or delivery certification
during setup. Source-identity and publication readback checks still protect the intended bindings.

NB11 preserves existing agent content: empty definitions cannot become SQL-only replacements,
and instructions are never silently truncated. Guidance that exceeds the existing
15,000-character service limit is reported before agent updates. All NB11 settings writes
use the existing bounded Delta-conflict retry pattern so its agent phase can coexist with NB10;
non-concurrency failures remain errors.

**Rollback.** Keep existing versions and deployment state. Use the last known-good source in a separate checkout and
run the same canonical command against the intended target; it reuses/builds that source's image and verifies the
current target configuration. Do not bypass the engine with a hand-published image or copy old cross-workspace settings.

## 6. Settings

| Variable | Purpose | Default |
|---|---|---|
| `HYDRO_AZURE_SUBSCRIPTION_ID` | Subscription for discovery and creation | Explicit choice, else explicit project ARM ID / same-target prepared subscription, else active CLI subscription; verified enabled in the selected tenant without changing the global CLI account |
| `HYDRO_FOUNDRY_PROJECT_RESOURCE_ID` | Full existing Foundry project ARM ID | Same-target prepared project ID, if available. Direct identity/endpoint readback avoids subscription-wide project discovery and fails rather than replacing an inaccessible selected project |
| `HYDRO_FOUNDRY_EXISTING_PROJECT_NAME` | Simple existing project-name override | No override; explicit name takes precedence over saved project hints. Duplicate names require a full ARM ID |
| `HYDRO_FOUNDRY_NEW_PROJECT_NAME` | Exact project name for approved creation | Required by the two-name UI; overrides saved/previous project hints, reuses only a matching named project, and participates in the stable per-target state key |
| `HYDRO_ONTOLOGY_GRAPH_BINDING` | Operator-verified graph JSON passed into target configuration | Authoritative lineage, else same-target saved binding; old workspace bindings are ignored |
| `HYDRO_FOUNDRY_PROJECT_ENDPOINT` | Foundry project to use | Saved endpoint from the same tenant/workspace/subscription, else unambiguous discovery (or dedicated creation when opted in) |
| `HYDRO_FOUNDRY_MODEL` | Model deployment for the agents | Same-target saved selection, else exactly one ready chat-capable deployment; required when creating |
| `HYDRO_ACR_NAME` | Registry to build in | The project's only `ContainerRegistry` connection |
| `HYDRO_PRODUCTION_WRITES_ENABLED` | Allow the human-approved work-order write | Preserved from the live agent; `false` for a new one |
| `HYDRO_FOUNDRY_CREATE` | `true` creates/reuses a target-specific project when no endpoint is selected | off |
| `HYDRO_FOUNDRY_LOCATION` | Azure region of the new project | The Fabric capacity's region |
| `HYDRO_FOUNDRY_PROJECT_NAME` | Optional explicit azd environment name for creation | `hydro-foundry-<hash of tenant, subscription and workspace>`; keep an override unique per target |
| `HYDRO_FOUNDRY_MODEL_NAME` | Catalog model override when creating a custom-named deployment | The supplied catalog name, otherwise the underlying model resolved from a ready deployment alias in the selected subscription |
| `HYDRO_FOUNDRY_MODEL_VERSION` / `_SKU` / `_CAPACITY` | Model deployment overrides when creating | Resolved reference version or unique catalog default; Standard, DataZoneStandard, then GlobalStandard; minimum allowed or default capacity. No automatic provisioned capacity or guessed model identity |
| `HYDRO_STATE_DIR` | Where generated azd projects live | `~/.fabric-demo` |
| `HYDRO_SPA_DISPLAY_NAME` | SPA discovery/creation name | `Hydro Operations Fabric Client` |

The local app shows only subscription for the normal reuse path. Project/model choices,
troubleshooting and model deployment overrides are collapsed in both forms; it does not read secrets.
An older deployment without saved subscription identity must rediscover its project; supply the existing endpoint
or project name if discovery is ambiguous. Discovery prefers the exact target convention
`ai-project-hydro-foundry-<hash of tenant, subscription and workspace>` over unrelated projects.
Do not opt into new project creation as a substitute for selecting an existing deployment.

Runtime configuration is generated, never edited by hand. `HYDRO_ORCHESTRATOR_CONFIG` carries `source`,
`authorization`, `project_endpoint`, `native_binding` and `production_writes_enabled` (`source.generation` must be `2`;
the data-agent connection must be `hydro-fabric-iq`). `HYDRO_FABRIC_SOURCE_CONFIG` carries eight identity GUIDs,
including the independent `operational_sql_database_id`,
`api_url` and `publishable_key` of the target. The azd environment `hydro-orchestrator` lives in the git-ignored
`.azure/` folder of the generated target project and is created automatically from live metadata.
`/health` and `/readiness` only report that the process awaits an invocation, not source access or agent readiness.

## 7. Troubleshooting

| Message | Meaning and fix |
|---|---|
| `Select exactly one existing Foundry project…` | Several or no projects match. Set the project endpoint, or tick *Create the Foundry project* (`HYDRO_FOUNDRY_CREATE=true`) if there is none. |
| `Set HYDRO_FOUNDRY_LOCATION to the Azure region…` | Creating a project, but the Fabric capacity region could not be read. Enter the region (for example `swedencentral`). |
| `azd provision` fails on the model deployment | The model, version, SKU or quota is unavailable in that region. Set `HYDRO_FOUNDRY_MODEL_VERSION`, `_SKU` or `_CAPACITY`, or another region, then rerun; the same azd environment is reused. |
| `The new Foundry project … is not yet usable by the signed-in user…` | Role assignments are still propagating. Wait a few minutes and rerun. |
| `The selected Foundry model deployment is not ready.` | Fix the model deployment or set `HYDRO_FOUNDRY_MODEL`. |
| `…must have one linked Application Insights resource` | Link exactly one; it is never inferred by name. |
| `Live numeric Ontology generation 2 is required…` | The selected ontology is not a live v2 item. No fallback exists. |
| `Exactly one published Data Agent must match the verified v2 ontology.` | Publish `RTI_Demo_Agent_*` against the selected ontology; remove stale ones. |
| `Could not determine the container registry…` | Project has zero or several registry connections. Set `HYDRO_ACR_NAME`. |
| `Required command 'azd' was not found on PATH.` | Install `azd`, then the two extensions (Section 3). |
| `az acr build` fails with authorization | Your identity cannot run builds in the registry. Grant the role, then rerun. |
| `azd deploy` reports a missing extension | `azd extension install azure.ai.agents` and `microsoft.foundry`. |
| `Hosted Agent … did not become active with the expected image and configuration within 15 minutes: <reason>` | The new version failed to start or its settings differ. Check `azd ai agent monitor`, fix, rerun. |
| `Rayfin created the backend but did not record its identity…` | Fresh-target bootstrap did not produce `rayfin/.env` values. Stop and report the output. |
| `HYDRO_PRODUCTION_WRITES_ENABLED must be true or false.` | Fix the value. |
| `Cannot preserve the live production-write policy…` | Review the existing agent, then deliberately set process-local `HYDRO_PRODUCTION_WRITES_ENABLED=true|false` for the canonical combined CLI deployment. The launcher preserves the policy without a UI override. No implicit policy change is made. |
| `Foundry definition readback failed for <agent>` | The live definition differs from the exported one after creation. Rerun; if persistent compare with `agentDefinitions.ts`. |
| Browser 401 from Foundry | Foundry resource is in another tenant, or the user lacks **Foundry User**. |
| Chat shows *Source limitations: the written briefing could not be verified* | Not a deployment fault: the briefing failed its checks and only verified tables are shown. |

## 8. Known limits

Portability covers **public Azure/Fabric tenants and workspaces where the required services are available**,
with sufficient permissions, active Fabric capacity, supported region/model/quota and the documented portal
prerequisites. It is not a promise to bypass tenant policy, sovereign-cloud differences, unavailable previews
or missing graph materialization for the optional native instance-graph view only.
Required service/permission blockers must be resolved in the target environment.

**The newly automated build/publish/project-creation path has not been certified by a full live deployment.**
Earlier hosted releases are recorded separately in the [implementation record](foundry-multi-agent-implementation.md);
they do not certify this automation. Its existing evidence consists of unit tests (fakes only; no cloud calls) and
the previously recorded read-only checks:

| Verified | How |
|---|---|
| The configuration the deployer would publish is identical to the deployed one | Composed from current state and compared (read-only) |
| Registry, Eventhouse origin and Hosted Agent shape are read correctly | Read-only calls to the live project |
| The Foundry project template and parameters are accepted by Azure | `azd provision --preview` (what-if; creates nothing) |
| `azd` accepts a generated project with a different endpoint | `azd package` in a scratch folder |
| The model-deployment parameter survives the template's quoting and Windows argument passing | Unit tests plus the preview |
| The launcher validates and forwards the Foundry fields and shows the new stages | Unit tests and a look at the running page (no job started) |

**Not exercised, so treat the first real run as supervised:** `az acr build`, `azd deploy`, `azd provision` (actual
creation), the fresh-target AppBackend bootstrap (`rayfin up --exclude-services staticHosting`), and creating the azd
environment on a machine that has none.

- Creating a project provisions Azure resources. This tool does not remove them; any cleanup is a separate,
  explicitly approved operation after checking ownership and dependencies.
- `az acr build` needs network access to the registry; private-only registries need a different build route.
- Hosting readiness is not agent execution readiness; interactive acceptance remains a separate check.

## Appendix A: manual operator setup (without the local web app)

Use the same engine, not a hand-built Rayfin/Entra deployment sequence.

1. Sign in to the target tenant; complete [access prerequisites](#3-one-time-checklist).
2. For a new workspace, import Git and run Core setup with Key Vault, Teams and
   email parameters. Do not reset an existing deployment for a website update.
3. Materialize the ontology graph using the [portal procedure](knowledge-graph.md).
4. Confirm the subscription and reuse existing Foundry resources. For ambiguity,
   set `HYDRO_FOUNDRY_EXISTING_PROJECT_NAME`; see [other overrides](#6-settings).

Only for explicitly approved **new resources**:

```powershell
$env:HYDRO_AZURE_SUBSCRIPTION_ID = "<target-subscription-guid>"
$env:HYDRO_FOUNDRY_CREATE = "true"
$env:HYDRO_FOUNDRY_NEW_PROJECT_NAME = "<new-project-name>"
$env:HYDRO_FOUNDRY_MODEL = "<ready-deployment-or-catalog-chat-model-name>"
```

Version, pay-as-you-go SKU and capacity use resolved live metadata. Clear previous-target
overrides before switching; never copy generated endpoints or deployment state.
Missing access is not permission to create a replacement.

From the repository root:

```powershell
python Raw\workspace-reset\deploy_fabric_app.py --tenant <tenant> --workspace <workspace> --push-config
```

Add `--client-id <guid>` only when supplied or discovery is ambiguous. Stop on
administrator prerequisites; do not hand-edit generated state.

Require `SUCCESS` and `DEPLOYED_APP_URL`, then run the
[acceptance prompts](../HydroOperationsApp/AGENT-TEST-PROMPTS.md). Verify agent/source
receipts, approved writes, Operations Agent execution and Teams/email delivery
separately; notebook or hosting success is not end-to-end acceptance.

## Appendix B: Fabric-side agents

The Fabric Data Agent (`RTI_Demo_Agent_<suffix>`, RTI_009) and the ontology-backed Operations Agent
(`RTI_Demo_OpsAgent_<suffix>`, RTI_010) are provisioned by the setup notebooks, always against ontology v2 with the
original playbook, actions, Teams and email-alert wiring retained. The separate Eventhouse-Knowledge
Operations Agent is retired, not provisioned. NB10 verifies the retained agent, checks the retired
agent's exact name/folder, saved identity and KQL source, deletes it, verifies absence, and clears
its four obsolete settings. Shared sources and delivery dependencies are not removed.
Configuration or hosted-page success does not
certify their execution or delivery. See the [README](../README.md#ontology-generations-and-optional-agents).

### Eventhouse-Knowledge agent retirement (October 23, 2026)

In `ws-vteam-demoV3` (`9c73201e-b2e5-48eb-81b9-3526d320faca`), the bounded cleanup
removed `RTI_Demo_OpsAgent_Eventhouse_V11` (`4f3e878f-afa8-40f4-a073-22813ed12d3f`)
after verifying its target-folder and KQL database source identity. A successful temporary
notebook job deleted and verified the four obsolete settings; the temporary notebook was
then deleted and its absence verified. Item readback returned HTTP 404 for the retired agent.
Exactly one Operations Agent remained in the target folder:
`RTI_Demo_OpsAgent_V11` (`3b9f2f6d-e14a-4520-810d-6a9fc9c054d4`).
Its complete definition and the shared `Pipe_SendEmailAlert` definition were unchanged
from pre-cleanup snapshots. The ontology, Lakehouse, and KQL database remained present.
The live NB10 definition was updated with the single-agent/guarded-retirement code while
preserving and verifying its cell metadata and target Lakehouse attachment.
This verifies retirement and dependency preservation, not monitoring or Teams/email delivery.

### Compact Data Agent instructions and automatic sources (October 23, 2026)

**Historical evidence, not the current provisioning policy:** the automatic NB09 MCP
facilities gate has been removed. Fabric's interactive agent can query ontology-associated
sources even when that intermediate MCP question reports no Lakehouse tool. NB09 now
verifies publication/readback without querying the agent or creating duplicate execution
sources to satisfy the smoke. Existing profiles/selections remain preserved and NB11's
later phase now reuses or directly mounts the operational SQL Database as a temporary
workaround for omitted ontology-inherited SQL execution, preserving ontology bindings,
operator instructions and custom sources. Deployment and
Foundry verify the published ontology's SQL/weather bindings, not a separate SQL profile.
The observations below remain specific to earlier sessions.

NB09 now generates 694 words / 5,287 characters of general guidance, replacing the
legacy instructions that discouraged ontology use. Ontology v2 provides semantic context;
source-native queries against its associated Lakehouse/Eventhouse are normal execution,
not a source fallback. Cross-source handoffs use exact `opcua_node_id` and
`equipment_id` / SQL `equipmentId` identifiers. SQL source-specific guidance remains
owned by NB11, which is unchanged. Custom sections and NB11's appended operational
instructions survive reruns.

The instruction-only correction initially preserved all sources but did not fix execution.
Native draft execution records then established that two **deselected direct-source
profiles** suppressed automatic attachment of the same ontology-associated sources:
`attachedCount=0`, `skippedCount=2`; no Lakehouse or KQL analysis tools were exposed.
Removing only those two unused profiles changed the trace to `attachedCount=2`,
`skippedCount=0`, restoring both source-native query tools without separately selected
tables. SQL source instructions/selections and ontology bindings were unchanged.

In `ws-vteam-demoV3`, fresh draft runs returned all three facilities and correlated the
latest-today BAD readings for T005/T009 with asset metadata and open work orders.
The published configuration now matches that tested context-only draft, with no separate
Lakehouse/Eventhouse profiles. Fresh published MCP sessions reproduced both answers and
queried inspections, notifications, spare-part associations and linked 3D models. These
bounded results do not certify every possible answer or repair the native federation
planner's stitching metadata. Associated source-native SQL/KQL is the successful route.
The compact policy also treats null/default timestamps as missing readings and requires
coverage checks; an empty recent BAD result must not be represented as proof of healthy
equipment. The published "right now" check used the 30-minute window and explicitly
qualified its empty BAD result rather than reusing the older BAD readings from today.

NB09 and Foundry connection validation recognize the current
`ontologies/<name>/ontology.json` definition and still require the matching live v2 item.
For an existing context-backed agent, NB09 preserves context and all custom sources,
does not recreate direct profiles, and does not rewrite NB11's SQL guidance. Live NB09
was synchronized without execution, preserving metadata and its Lakehouse attachment.
Do not indiscriminately delete selected or unrelated source profiles. Inspect run steps and
[downloaded ontology context](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-ontology-sources)
before changing bindings or reattaching tables.

### Recheck after a Data Agent/Ontology service fix

No ontology-generation downgrade, new agent mode, source fallback or application code switch is needed.
NB09 selects Preview Runtime, publishes and verifies the live generation-2 source identity.
It does not issue a runtime query or gate setup on a language-model response.

1. In the target workspace, attach the existing settings Lakehouse and rerun
   [RTI_009](../Notebooks/RTI_009_build_data_agent.Notebook/notebook-content.py) as the delegated notebook user
   with permission to query the agent. Do not reset the workspace or replace the ontology/Data Agent.
2. Inspect `rti_demo_settings`: expect `data_agent_deployment_status=published`,
   `data_agent_publication_status=published` and `data_agent_runtime_status=not_checked`.
   Previous runtime evidence is cleared. Actual deployment, publication and readback
   failures still propagate; interactive answers are not a setup requirement.
3. NB09 preserves compatible SQL/custom sources on rerun. NB11 mounts the operational
   SQL Database item directly, reusing a matching profile. Fabric uses that database's own
   SQL analytics endpoint, not the Lakehouse endpoint; ontology semantics/bindings and
   operator content remain intact. Its compatibility SQL-source status records
   `source_mode=direct_sql_workaround` and does not replace NB09's runtime result.
   This bypasses inherited-source expansion; it does not repair or certify that path.
4. With unchanged source identity, existing Foundry connections require no manual rebinding.
   If deploying repository changes or changing target identity, use Section 4 or 5. The app deployer verifies
   publication/identity, **not** NB09's runtime smoke. Then test an explicit Fabric IQ request through Sparky.

Interactive testing is separate from provisioning. Publication/readback does not attest execution
provenance or certify SQL/combined-source answers, the full multi-agent matrix or Operations Agent delivery.

### Operations Agent playbook and alert frequency

NB10 imports the playbook read from the working ontology-backed agent on October 23.
It retains both BAD/UNCERTAIN rules, the generated `SignalMaster` grounding model,
`Id = opcua_node_id`, projected columns, and the six direct email parameter bindings.
The Knowledge source remains the selected, verified v2 ontology. Its generated queries
still use Lakehouse SQL and Eventhouse KQL: ontology Knowledge does not mean those physical
sources are unnecessary. NB10 replaces every query endpoint, catalog, database and table
binding with the target's live metadata/settings, including queries repeated in grounding
definitions. Deployment does not read another workspace's reference agent.

The live rules use **Is BAD** and **Is UNCERTAIN**, not transition conditions. They return
individual readings without an authored cooldown. Microsoft documents a five-minute rule
evaluation cycle and explains that state conditions can signal repeatedly while satisfied.
`Id` already identifies the signal, but grouping by `opcua_node_id` alone does not throttle it.

Recommended alert policy, **not implemented by this template**:

- Send the first signal alert immediately, subject to Fabric's evaluation latency.
- Suppress repeats of that signal for 15 minutes from the last emitted notification.
- Permit immediate UNCERTAIN-to-BAD escalation, then restart its cooldown.
- If the condition persists, emit at most one reminder per cooldown period.
- Keep different signals independent. Include workspace/source identity in any shared
  deduplication key; `opcua_node_id` alone is scoped to its source, not globally unique.
- Treat recovery/new-incident reset as an explicit policy decision.

A query-side `bin(event_time, 15m)` is a fixed aggregation bucket, not a rolling cooldown;
two alerts can straddle a bucket boundary. Choosing the latest reading per signal can reduce
duplicate rows but can also hide a transient BAD event, and does not by itself suppress
notifications across evaluations. An email-pipeline-only gate will not suppress built-in
Teams notifications. Do not add undocumented playbook fields or claim suppression from
natural-language instructions.

The public Operations Agent guidance documents **Becomes** transition conditions, which can
avoid repeats while a state persists, but these are not timed reminders or a strict 15-minute
cooldown. Use the portal's supported rule authoring/Copilot flow to generate and inspect a
candidate, and validate both Teams and email behavior before adopting it. A timed cooldown
needs a verified native mechanism or a durable, atomic shared notification gate covering
both channels. Retiring the separate Eventhouse-Knowledge agent does not change the retained
ontology agent's alert frequency or SQL/KQL grounding.

See [Operations Agent rule semantics](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/operations-agent#understand-operations-agent-rules).

## References

[Deployer](../Raw/workspace-reset/deploy_fabric_app.py) ·
[Foundry provisioner](../Raw/workspace-reset/provision_foundry_agents.py) ·
[Hosted agent publisher](../Raw/workspace-reset/deploy_hosted_agent.py) ·
[Launcher guide](../Raw/workspace-reset/README.md) · [App deployment guide](../HydroOperationsApp/DEPLOY.md) ·
[Implementation record](foundry-multi-agent-implementation.md) · [Service runbook](../HydroOperationsService/README.md)
