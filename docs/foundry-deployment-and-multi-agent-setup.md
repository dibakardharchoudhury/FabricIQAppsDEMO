# Foundry deployment and multi-agent setup

How the Hydro multi-agent setup fits together, and how to deploy all of its Foundry parts with **one
action**: the local deploy app or one command. Both run the same engine,
[`deploy_fabric_app.py`](../Raw/workspace-reset/deploy_fabric_app.py).

The current FEATURE target is Foundry project
`ai-project-hydro-foundry-884a839f3132`: `hydro-orchestrator:10` and the separate
`hydro-weather-agent:1` are deployed there. Do not confuse this target-specific
project with an older repository-local azd environment or another workspace's
agents. Hosted source reads have verified the Lakehouse connector replacement and
the obsolete FEATURE `Hydro_STID_API` has been removed. See the
[current deployment and runtime acceptance record](ontology-test-review.md#latest-canonical-deployment-and-authenticated-acceptance-hosted-agent-10)
for the verified workflows and remaining source/product prerequisites; publication is
not full business runtime acceptance.

### Finding Weather in Foundry

Open the [FEATURE project in Azure portal](https://portal.azure.com/#@ad340c84-1886-4202-a483-2da2cb9168eb/resource/subscriptions/dac16501-5c35-4c91-aff0-11af8de17f2e/resourceGroups/rg-hydro-foundry-884a839f3132/providers/Microsoft.CognitiveServices/accounts/ai-account-yz6usfcwfov5q/projects/ai-project-hydro-foundry-884a839f3132/overview),
then use Foundry and select **`ai-project-hydro-foundry-884a839f3132` -> Agents**.
Its account is `ai-account-yz6usfcwfov5q`, in resource group
`rg-hydro-foundry-884a839f3132`. The separate Weather prompt agent is
**`hydro-weather-agent`, version 1**.

[Open the deployed Weather agent directly in Foundry](https://ai.azure.com/nextgen/r/2sFlAVw1TJGv8BGvjeF_Lg,rg-hydro-foundry-884a839f3132,,ai-account-yz6usfcwfov5q,ai-project-hydro-foundry-884a839f3132/build/agents/hydro-weather-agent/build?tid=ad340c84-1886-4202-a483-2da2cb9168eb).
Read-only API and portal checks on October 24 at 23:24 UTC confirmed the agent
is enabled, version 1, and displayed as **Agent running**. Its model is
`gpt-5.6-sol` and its governed function tool is `hydro_query`.

Weather is a prompt specialist executed through the hosted coordinator and its
authorized Lakehouse bridge, not another standalone hosted service. A Foundry
Playground-only conversation does not certify connector execution or forecast
freshness. The authenticated app's Weather route completed, but forecast serving
tables remain empty because scheduled Aurora ingestion fails with HTTP 401.

The older `foundryfabriciqappsdemo` project is a different deployment. Its
orchestrator version 140 and specialist versions 16/21/25/20 match the older
screenshots; that project does not contain Weather. Do not redeploy into another
workspace's project to make its agent list match FEATURE. Publication in the
correct project does not certify weather ingestion or a successful inference.

The [Ontology_Test and operational-store review](ontology-test-review.md)
records the read-only comparison and proposed FEATURE acceptance gates.

## Start a deployment

Use the [merged feature-branch deployment matrix](../HydroOperationsApp/DEPLOY.md#deploying-the-merged-feature-branch)
for PR #19/#20. The local launcher must run from the updated `feat/dibakar`
checkout; Fabric Git sync prefills the checkout's branch and repository. Confirm
that branch is pushed before importing it. **Set up Foundry agents** owns the five specialists,
including Weather, and the Hosted coordinator. **Deploy app** is the final,
read-only agent-verification and SPA-publication module, not agent provisioning.
Weather source publication/provider authentication, energy-source setup,
and manual native graph materialization remain separate prerequisites.

**One-time setup** (Section 3 has the full checklist): `az login`, and install the Azure Developer CLI (`azd`).
The Foundry extensions are installed for you. The Foundry project and supporting resources can be created
by the deploy when you tick **Create/reuse a target-specific Foundry project when none is selected** and supply
the model settings available in your subscription/region. No original demo tenant or workspace is required.

| | Local deploy app | Command line |
|---|---|---|
| 1 | Double-click [`Start Fabric Demo.cmd`](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd) | |
| 2 | Pick tenant + workspace, subscription and Foundry/model settings. Opt into target-specific project creation when needed | Target-specific env vars, Section 6 |
| 3 | Press **Set up Foundry agents** | The same engine with `--stage foundry` prepares/verifies agents, without publishing the website |
| 4 | Press **Deploy app** | `python Raw\workspace-reset\deploy_fabric_app.py --tenant <tenant> --workspace <workspace> --stage app --push-config` |
| 5 | Wait for `SUCCESS` and `DEPLOYED_APP_URL`, then open the app and test it | same |

Once the prerequisites below are satisfied, the independent Foundry module owns
the project (only if you opted in), connections, five prompt agents, Hosted image
build/publication and readback. Final app publication then verifies those prepared
definitions, code/source fingerprints and active immutable runtime, without
provisioning. Missing/stale setup stops publication. The original command without
`--stage` retains combined behavior; **Initialize everything** runs the modules
serially with selected energy setup before Foundry and the app last. Pick the
target deliberately; no project is created simply to update the website.

## 1. The multi-agent setup

```text
Operator ─► SPA (Fabric-hosted) ─ signed-in user's delegated tokens ─►  hydro-orchestrator  (Foundry Hosted Agent)
                                                                         Supervisor: deterministic plan → serial
                                                                         specialist steps → evidence certification
                                                                         → grounded briefing
                                       ┌────────────── Foundry Responses ────────────────┐
                                       ▼                 ▼                  ▼             ▼
                               hydro-qa-agent   hydro-rca-agent   hydro-work-order-agent   hydro-fabric-iq-agent   hydro-weather-agent
                                  (Gauge)          (Sleuth)            (Fixer)                 (Sparky)              (Weather)
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
| 0. Backend identity | If the target has no AppBackend yet, creates it first (without static hosting) so the Hosted Agent can be configured against its identity | The target already has one |
| 1. Foundry project | Verifies the selected subscription belongs to the Fabric tenant. Uses an explicitly selected or same-target saved project; otherwise discovers one, or **if you opted in** provisions a target-specific project with `Azure-Samples/azd-ai-starter-basic`: resource group, account/project, specified model, connected registry/monitoring and Log Analytics. Unrelated projects are not reused when creation is selected. Waits for role propagation | A project is selected, or you did not opt in |
| 2. Checks | One Foundry project, model ready, one linked Application Insights, ontology generation 2, exactly one matching published Data Agent | never |
| 3. Connections | Writes `hydro-fabric-iq` and `hydro-fabric-ontology` and reads them back | never |
| 4. Prompt agents | Exports the five definitions, creates a new version only if a definition changed, reads each back | definition unchanged |
| 5. Image | Fingerprints every file the [Dockerfile](../HydroOperationsService/Dockerfile) packages; builds `hydro-orchestrator:src-<fingerprint>` in the project's ACR with `az acr build` | that tag already exists |
| 6. Configuration | Composes `HYDRO_ORCHESTRATOR_CONFIG` and `HYDRO_FABRIC_SOURCE_CONFIG` from the verified target | never (always compared) |
| 7. Publish | Creates or refreshes the azd environment's project, tenant and registry identity from live metadata, applies runtime settings and publishes; waits until the active version has the exact image digest and configuration | live agent already matches |
| 8. Verify | The existing gate: active, numeric version, immutable digest, source identity and native binding all match | never |

The local app shows these as separate progress stages: *Foundry project*, *Foundry agents*, *Hosted Agent*.

Consequences:

- **Code changes deploy themselves.** The fingerprint covers the Python service, the Node bridge and the TypeScript
  files copied into the image. A change anywhere there triggers one build and one new Hosted Agent version on the next
  Foundry setup; an unchanged tree costs a single registry lookup. Final app
  publication never builds or publishes the image.
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
  is derived from tenant, subscription and workspace. Retain this state for reruns; on another machine explicitly
  select the existing endpoint instead of opting into another project. No tenant-specific name is embedded in source.
- **One target per hosted coordinator.** The stable `hydro-*` names are application contracts, not tenant identities.
  Deploying another workspace into the same explicitly selected Foundry project rebinds that coordinator and its
  connections. Use a separate target-specific project for each concurrently deployed Fabric workspace.
- **Production writes are preserved.** The live agent's `production_writes_enabled` is kept. Set
  `HYDRO_PRODUCTION_WRITES_ENABLED=true|false` (or the form's *Work-order writes*) to change it deliberately. A
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
  There is no hardcoded model/version/SKU/capacity. New projects require operator-supplied creation values;
  Azure provisioning rejects unavailable models, regions or quota. No silent model/region downgrade is attempted.

**Always required (not created by this deploy)**

- A generation-2 ontology with its graph materialized and `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` resolved
  ([knowledge-graph.md](knowledge-graph.md)), plus the published Data Agent `RTI_Demo_Agent_*`.

## 4. Local deploy app, step by step

1. Launch [`Start Fabric Demo.cmd`](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd) (`python launch.py` on any OS).
   It installs missing Python packages, signs in to Azure and opens `http://127.0.0.1:5000`.
2. Choose the tenant and workspace. Add a **SPA client id** only if discovery is ambiguous or you were given one.
3. **Set up Foundry agents** tab (or its section in **Full workflow**), *Foundry* block:

   | Field | Use it when |
   |---|---|
   | Azure subscription ID | Select a subscription explicitly; blank uses the active one, which must be enabled and in the selected Fabric tenant |
   | Operator-verified ontology graph binding | Supply JSON after portal materialization if authoritative lineage is unavailable; never edit generated `.env` files |
   | Foundry project endpoint | You have several Foundry projects, or want a specific one |
   | Model deployment | Existing project: select its deployment, reuse the same-target saved selection, or discover exactly one ready deployment advertising chatCompletion capability. Required when creating |
   | Container registry | Select a live project connection when several exist; arbitrary or stale registry names are rejected |
   | Work-order writes | You want to enable or disable the human-approved write (default: keep the current setting) |
   | **Create/reuse a target-specific Foundry project when none is selected** | Provision a dedicated project rather than reuse an unrelated project. This **creates billable Azure resources** |
   | Region for the new project | Only when creating; default is the Fabric capacity's region |
   | Model catalog name | Only when creating; blank uses the deployment name, otherwise separates catalog identity from a custom deployment name |
   | Model version / SKU / capacity | Required when creating; choose available values within your subscription/region quota |

   Values are validated by the local server and passed to the deployer as environment variables; nothing secret is
   collected. Blank text fields fall back to the launch environment, then target-scoped discovery where supported.
   The creation checkbox explicitly overrides an inherited `HYDRO_FOUNDRY_CREATE`; unchecked cannot opt into billing.
4. Fresh data environment: use **Full workflow** (GitHub sync, setup, selected energy, Foundry, final app), or run those modules first.
   An ordinary update needs none of that. The workflow does not materialize the native graph: on a fresh target,
   complete the selected ontology's **Manage graph → select eligible entities/relationships → Continue → Materialize**
   flow and resolve the explicit graph binding before app deployment can succeed. If Full workflow stops at this
   prerequisite after setup, complete it and resume **Set up Foundry agents**, then **Deploy app**, not another reset/sync.
5. Press **Set up Foundry agents**. Project creation/image builds belong here
   (90-minute job limit). Wait for `FOUNDRY_SETUP_SUCCESS`; configuration is verified,
   not business execution. Then press **Deploy app** (30-minute job limit).
   The local UI does not require GitHub write permission or pass `--push-config`.
   Repository owners can commit the generated hosting-origin configuration;
   agents using the canonical CLI retain `--push-config`.
6. Finish when final publication prints `SUCCESS` and `DEPLOYED_APP_URL`, then test the app.

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
- Test one direct-source question, one multi-step question (Gauge → Sleuth → Fixer) and one Fabric IQ question.
  Hosting, agent execution, graph materialization and Teams/email delivery are **separate gates**.

**Rollback.** Keep existing versions and deployment state. Use the last known-good source in a separate checkout and
run the same canonical command against the intended target; it reuses/builds that source's image and verifies the
current target configuration. Do not bypass the engine with a hand-published image or copy old cross-workspace settings.

## 6. Settings

| Variable | Purpose | Default |
|---|---|---|
| `HYDRO_AZURE_SUBSCRIPTION_ID` | Subscription for discovery and creation | Explicit choice, else explicit project ARM ID / same-target prepared subscription, else active CLI subscription; verified enabled in the selected tenant without changing the global CLI account |
| `HYDRO_FOUNDRY_PROJECT_RESOURCE_ID` | Full existing Foundry project ARM ID | Same-target prepared project ID, if available. Direct identity/endpoint readback avoids subscription-wide project discovery and fails rather than replacing an inaccessible selected project |
| `HYDRO_ONTOLOGY_GRAPH_BINDING` | Operator-verified graph JSON passed into target configuration | Authoritative lineage, else same-target saved binding; old workspace bindings are ignored |
| `HYDRO_FOUNDRY_PROJECT_ENDPOINT` | Foundry project to use | Saved endpoint from the same tenant/workspace/subscription, else unambiguous discovery (or dedicated creation when opted in) |
| `HYDRO_FOUNDRY_MODEL` | Model deployment for the agents | Same-target saved selection, else exactly one ready chat-capable deployment; required when creating |
| `HYDRO_ACR_NAME` | Registry to build in | The project's only `ContainerRegistry` connection |
| `HYDRO_PRODUCTION_WRITES_ENABLED` | Allow the human-approved work-order write | Preserved from the live agent; `false` for a new one |
| `HYDRO_FOUNDRY_CREATE` | `true` creates/reuses a target-specific project when no endpoint is selected (billable) | off |
| `HYDRO_FOUNDRY_LOCATION` | Azure region of the new project | The Fabric capacity's region |
| `HYDRO_FOUNDRY_PROJECT_NAME` | Optional explicit azd environment name for creation | `hydro-foundry-<hash of tenant, subscription and workspace>`; keep an override unique per target |
| `HYDRO_FOUNDRY_MODEL_NAME` | Catalog model name when creating a custom-named deployment | The operator-supplied deployment name |
| `HYDRO_FOUNDRY_MODEL_VERSION` / `_SKU` / `_CAPACITY` | Required model details when creating | None; no demo-environment values are substituted |
| `HYDRO_STATE_DIR` | Where generated azd projects live | `~/.fabric-demo` |
| `HYDRO_SPA_DISPLAY_NAME` | SPA discovery/creation name | `Hydro Operations Fabric Client` |

The local app exposes subscription, graph binding, endpoint/model/registry, write policy and all model-creation
settings in both forms; it does not read secrets.
An older deployment without saved subscription identity must rediscover its project; supply the existing endpoint
if discovery is ambiguous. Do not opt into new project creation as a substitute for selecting an existing deployment.

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
| `Cannot preserve the live production-write policy…` | Review the existing agent, then deliberately select enabled/disabled in the local form or set `HYDRO_PRODUCTION_WRITES_ENABLED=true|false`. No implicit policy change is made. |
| `Foundry definition readback failed for <agent>` | The live definition differs from the exported one after creation. Rerun; if persistent compare with `agentDefinitions.ts`. |
| Browser 401 from Foundry | Foundry resource is in another tenant, or the user lacks **Foundry User**. |
| Chat shows *Source limitations: the written briefing could not be verified* | Not a deployment fault: the briefing failed its checks and only verified tables are shown. |

## 8. Known limits

Portability covers **public Azure/Fabric tenants and workspaces where the required services are available**,
with sufficient permissions, active Fabric capacity, supported region/model/quota and the documented portal
prerequisites. It is not a promise to bypass tenant policy, sovereign-cloud differences, unavailable previews
or missing graph materialization. All such blockers must be resolved in the target environment.

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

- Creating a project provisions billable resources. This tool does not remove them; any cleanup is a separate,
  explicitly approved operation after checking ownership and dependencies.
- `az acr build` needs network access to the registry; private-only registries need a different build route.
- Hosting readiness is not agent execution readiness; interactive acceptance remains a separate check.

## Appendix A: manual operator setup (without the local web app)

Manual setup means an operator performs the prerequisite portal/access steps and invokes the **same deployment
engine**, not a second sequence of Rayfin/Entra/Foundry commands.

1. **Select the target.** Sign in to the intended Azure tenant, select its subscription, record the Fabric workspace,
   and check capacity availability. Complete Section 3's tool and permission checklist. If needed, sign in to azd
   interactively before starting. Do not substitute a service-principal token for the user's runtime tokens.
2. **Prepare Fabric data.** For a new environment, import/sync the repository items and run `01_Pipe_Setup` with the
   [setup prerequisites and parameters](../Raw/workspace-reset/README.md#prerequisites), including Key Vault,
   Teams and alert-recipient configuration. Always provision the Data Agent and the ontology-backed Operations Agent; do not
   skip agent branches. For an existing target, do not reset or recreate it for a routine deployment.
3. **Verify Fabric agents.** Check NB09's published source identity and runtime result (Appendix B).
   NB10 configures the ontology-backed Operations Agent stopped; inspect its playbook/actions, Teams and email wiring separately.
   It retires only the exact obsolete Eventhouse-Knowledge agent/settings after identity and deletion checks.
   NB11 must retain and publish the operational SQL source. Notebook completion alone proves no delivery.
4. **Materialize the native graph.** In the selected ontology use **Manage graph → select eligible
   entities/relationships → Continue → Materialize**. Resolve `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` from
   authoritative lineage, or provide the documented operator-verified fallback using
   [the graph binding procedure](knowledge-graph.md). If lineage is unavailable, pass the operator-verified JSON
   through `HYDRO_ONTOLOGY_GRAPH_BINDING` (or the local form); do not edit generated state. Do not infer ownership
   by name or graph count.
5. **Select Foundry.** For an existing project, verify model, registry and monitoring connections, then set its endpoint
   if discovery is ambiguous. For a new project, explicitly opt into billable resource creation:
   ```powershell
   $env:HYDRO_AZURE_SUBSCRIPTION_ID = "<subscription-guid-in-the-target-tenant>"
   $env:HYDRO_FOUNDRY_CREATE = "true" # Only when creation is intended.
   $env:HYDRO_FOUNDRY_MODEL = "<deployment-name>"
   $env:HYDRO_FOUNDRY_MODEL_NAME = "<compatible-openai-chat-model-catalog-name>"
   $env:HYDRO_FOUNDRY_MODEL_VERSION = "<available-model-version>"
   $env:HYDRO_FOUNDRY_MODEL_SKU = "<available-deployment-sku>"
   $env:HYDRO_FOUNDRY_MODEL_CAPACITY = "<positive-capacity-within-quota>"
   # Optional: $env:HYDRO_FOUNDRY_LOCATION = "<supported-azure-region>"
   # Only if authoritative graph lineage is unavailable, after portal verification:
   # $env:HYDRO_ONTOLOGY_GRAPH_BINDING = '{"workspaceId":"<guid>","ontologyId":"<guid>","graphModelId":"<guid>"}'
   ```
   For an existing project instead:
   ```powershell
   $env:HYDRO_AZURE_SUBSCRIPTION_ID = "<subscription-guid-in-the-target-tenant>"
   $env:HYDRO_FOUNDRY_CREATE = "false"
   $env:HYDRO_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
   $env:HYDRO_FOUNDRY_MODEL = "<existing-compatible-chat-deployment-name>"
   ```
   These are alternatives. If a specified endpoint cannot be found, stop and verify tenant/subscription/endpoint;
   do not use project creation to work around missing access.
   Clear environment overrides from a previous target before switching; never copy its generated URLs, GUIDs or
   registry settings. The canonical engine resolves tenant domains to GUIDs, validates the subscription's tenant,
   discovers Fabric items in the selected workspace and derives the AppBackend endpoint from its current capacity.
6. **Deploy from the repository root:**
   ```powershell
   python Raw\workspace-reset\deploy_fabric_app.py --tenant <tenant> --workspace <workspace> --push-config
   ```
   Add `--client-id <guid>` only when supplied or discovery is ambiguous. The engine owns connection/agent
   definitions, image build, azd state, SPA permissions/consent, redirects, runtime/CORS and backend checks.
   Stop on any printed administrator prerequisite; only perform the exact action it delegates to an administrator.
   Do not hand-edit generated state or manually reconstruct the publish sequence.
7. **Accept separately.** Require `SUCCESS` and `DEPLOYED_APP_URL`, normal browser sign-in, then run the
   [ten-prompt matrix](../HydroOperationsApp/AGENT-TEST-PROMPTS.md) on the deployed build. Confirm specialist
   invocations and source receipts, Fabric IQ retrieval, and explicit human approval/readback of a test work order
   only when writes are intentionally enabled. Test Operations Agent execution and Teams/email delivery separately.
   Record pending or failed gates rather than calling hosting success end-to-end acceptance.

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
NB09 selects Preview Runtime, publishes, verifies the live generation-2 source identity and issues a **new**
bounded facilities query on every run. A previous `known_product_limitation` is not a sticky skip.

1. In the target workspace, attach the existing settings Lakehouse and rerun
   [RTI_009](../Notebooks/RTI_009_build_data_agent.Notebook/notebook-content.py) as the delegated notebook user
   with permission to query the agent. Do not reset the workspace or replace the ontology/Data Agent.
2. Inspect `rti_demo_settings`: require `data_agent_deployment_status=ready`,
   `data_agent_publication_status=published` and `data_agent_runtime_status=verified`.
   Review `data_agent_runtime_reason` and `data_agent_runtime_evidence` for the fresh result.
   The exact unsupported-Ontology response remains a narrow known-product limitation **if still returned**;
   all other failures propagate. Its absence alone is not a pass.
3. NB09 preserves compatible SQL/custom sources on rerun. Run NB11 if its required operational SQL extension
   has not completed; NB11's SQL-source status does not replace NB09's runtime result.
4. With unchanged source identity, existing Foundry connections require no manual rebinding.
   If deploying repository changes or changing target identity, use Section 4 or 5. The app deployer verifies
   publication/identity, **not** NB09's runtime smoke. Then test an explicit Fabric IQ request through Sparky.

The bounded facilities smoke is functional evidence only: it does not attest execution provenance or certify
SQL/combined-source answers, the full multi-agent matrix, or Operations Agent delivery. The service fix has not
been independently verified in a target tenant by this documentation review.

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
