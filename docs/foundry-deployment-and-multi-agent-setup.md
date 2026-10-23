# Foundry deployment and multi-agent setup

How the Hydro multi-agent setup fits together, and how to deploy all of its Foundry parts with **one
action**: the local deploy app or one command. Both run the same engine,
[`deploy_fabric_app.py`](../Raw/workspace-reset/deploy_fabric_app.py).

## Start a deployment

**One-time setup** (Section 3 has the full checklist): `az login`, and install the Azure Developer CLI (`azd`).
The Foundry extensions are installed for you. The Foundry project and supporting resources can be created
by the deploy when you tick **Create/reuse a target-specific Foundry project when none is selected** and supply
the model settings available in your subscription/region. No original demo tenant or workspace is required.

| | Local deploy app | Command line |
|---|---|---|
| 1 | Double-click [`Start Fabric Demo.cmd`](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd) | |
| 2 | Pick tenant + workspace, subscription and Foundry/model settings. Opt into target-specific project creation when needed | Target-specific env vars, Section 6 |
| 3 | Press **Deploy app** | `python Raw\workspace-reset\deploy_fabric_app.py --tenant <tenant> --workspace <workspace> --push-config` |
| 4 | Wait for `SUCCESS` and `DEPLOYED_APP_URL`, then open the app and test it | same |

Once the prerequisites below are satisfied, that single run does the Foundry deployment: the project (only if you opted in), connections, the four
prompt agents, the **Hosted Agent image build and publish**, and the final verification. Nothing needs to be run by
hand. **It deploys into the selected Fabric workspace**, exactly as before; pick the target deliberately.

## 1. The multi-agent setup

```text
Operator ─► SPA (Fabric-hosted) ─ signed-in user's delegated tokens ─►  hydro-orchestrator  (Foundry Hosted Agent)
                                                                         Supervisor: deterministic plan → serial
                                                                         specialist steps → evidence certification
                                                                         → grounded briefing
                                       ┌────────────── Foundry Responses ────────────────┐
                                       ▼                 ▼                  ▼             ▼
                               hydro-qa-agent   hydro-rca-agent   hydro-work-order-agent   hydro-fabric-iq-agent
                                  (Gauge)          (Sleuth)            (Fixer)                 (Sparky)
        Specialist tool calls are executed by the hosted runtime (Node source bridge) under the user's tokens:
        STID GraphQL · Eventhouse KQL · AppBackend SQL · published Data Agent / Ontology via Foundry connections
```

| Component | Foundry name | Kind | Defined in | Deployed by |
|---|---|---|---|---|
| Supervisor | `hydro-orchestrator` | Hosted agent (Python 3.12 + Node 24 container, Microsoft Agent Framework, `invocations` protocol) | [`HydroOperationsService/`](../HydroOperationsService), [`azure.yaml`](../azure.yaml) | Deployer (build in ACR, publish with azd) |
| Gauge | `hydro-qa-agent` | Prompt agent | [`agentDefinitions.ts`](../HydroOperationsApp/src/services/copilot/agentDefinitions.ts) | Deployer |
| Sleuth | `hydro-rca-agent` | Prompt agent | same | Deployer |
| Fixer | `hydro-work-order-agent` | Prompt agent | same | Deployer |
| Sparky | `hydro-fabric-iq-agent` | Prompt agent | same | Deployer |
| Data Agent connection | `hydro-fabric-iq` | Project `RemoteTool` connection (user Entra token) | provisioner | Deployer |
| Ontology connection | `hydro-fabric-ontology` | Project `RemoteTool` connection (user Entra token) | provisioner | Deployer |
| Model | Selected/discovered in the target account; no fixed deployment name | Model deployment | `HYDRO_FOUNDRY_MODEL` | Existing project: must exist; opt-in creation: template provisions the specified catalog model/version/SKU/capacity |

The Supervisor is the hosted runtime, **not a prompt agent**. There is no separate "Chief" prompt agent in the
current code; earlier records that mention Chief or five agents are historical.

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

## 2. What one deploy does on the Foundry side

Phase 3 of the deployer's eight phases ([`provision_foundry_agents.py`](../Raw/workspace-reset/provision_foundry_agents.py),
[`deploy_hosted_agent.py`](../Raw/workspace-reset/deploy_hosted_agent.py)):

| Step | Behaviour | Skipped when |
|---|---|---|
| 0. Backend identity | If the target has no AppBackend yet, creates it first (without static hosting) so the Hosted Agent can be configured against its identity | The target already has one |
| 1. Foundry project | Verifies the selected subscription belongs to the Fabric tenant. Uses an explicitly selected or same-target saved project; otherwise discovers one, or **if you opted in** provisions a target-specific project with `Azure-Samples/azd-ai-starter-basic`: resource group, account/project, specified model, connected registry/monitoring and Log Analytics. Unrelated projects are not reused when creation is selected. Waits for role propagation | A project is selected, or you did not opt in |
| 2. Checks | One Foundry project, model ready, one linked Application Insights, ontology generation 2, exactly one matching published Data Agent | never |
| 3. Connections | Writes `hydro-fabric-iq` and `hydro-fabric-ontology` and reads them back | never |
| 4. Prompt agents | Exports the four definitions, creates a new version only if a definition changed, reads each back | definition unchanged |
| 5. Image | Fingerprints every file the [Dockerfile](../HydroOperationsService/Dockerfile) packages; builds `hydro-orchestrator:src-<fingerprint>` in the project's ACR with `az acr build` | that tag already exists |
| 6. Configuration | Composes `HYDRO_ORCHESTRATOR_CONFIG` and `HYDRO_FABRIC_SOURCE_CONFIG` from the verified target | never (always compared) |
| 7. Publish | Creates or refreshes the azd environment's project, tenant and registry identity from live metadata, applies runtime settings and publishes; waits until the active version has the exact image digest and configuration | live agent already matches |
| 8. Verify | The existing gate: active, numeric version, immutable digest, source identity and native binding all match | never |

The local app shows these as separate progress stages: *Foundry project*, *Foundry agents*, *Hosted Agent*.

Consequences:

- **Code changes deploy themselves.** The fingerprint covers the Python service, the Node bridge and the TypeScript
  files copied into the image. A change anywhere there triggers one build and one new Hosted Agent version on the next
  deploy; an unchanged tree costs a single registry lookup.
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
3. **Deploy app** tab (or the same fields in the **Full workflow** tab), *Foundry* block:

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
4. Fresh data environment: use the **Full workflow** tab (GitHub sync, `01_Pipe_Setup`, deploy), or run those first.
   An ordinary update needs none of that. The workflow does not materialize the native graph: on a fresh target,
   complete the selected ontology's **Manage graph → select eligible entities/relationships → Continue → Materialize**
   flow and resolve the explicit graph binding before app deployment can succeed. If Full workflow stops at this
   prerequisite after setup, complete it and resume with **Deploy app**, not a workspace reset or another full sync.
5. Press **Deploy app** and watch the stages. Expect extra minutes when the project is created (about 10) or the image
   builds. The job limit is 90 minutes. The UI never passes `--push-config`, so commit `rayfin/rayfin.yml` yourself.
6. Finish when you see `SUCCESS` and `DEPLOYED_APP_URL`, then test the app.

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
| `HYDRO_AZURE_SUBSCRIPTION_ID` | Subscription for discovery and creation | Active subscription, verified enabled in the selected tenant; no global CLI account change |
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
the data-agent connection must be `hydro-fabric-iq`). `HYDRO_FABRIC_SOURCE_CONFIG` carries the seven item GUIDs,
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
   Teams and alert-recipient configuration. Always provision the Data Agent and both Operations Agents; do not
   skip agent branches. For an existing target, do not reset or recreate it for a routine deployment.
3. **Verify Fabric agents.** Check NB09's published source identity and runtime result (Appendix B).
   NB10 configures both Operations Agents stopped; inspect playbooks/actions, Teams and email wiring separately.
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

The Fabric Data Agent (`RTI_Demo_Agent_<suffix>`, RTI_009) and the Ontology and Eventhouse Operations Agents
(`RTI_Demo_OpsAgent_<suffix>`, RTI_010) are provisioned by the setup notebooks, always against ontology v2 with the
separate playbooks, actions, Teams and email-alert wiring retained. Configuration or hosted-page success does not
certify their execution or delivery. See the [README](../README.md#ontology-generations-and-optional-agents).

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

## References

[Deployer](../Raw/workspace-reset/deploy_fabric_app.py) ·
[Foundry provisioner](../Raw/workspace-reset/provision_foundry_agents.py) ·
[Hosted agent publisher](../Raw/workspace-reset/deploy_hosted_agent.py) ·
[Launcher guide](../Raw/workspace-reset/README.md) · [App deployment guide](../HydroOperationsApp/DEPLOY.md) ·
[Implementation record](foundry-multi-agent-implementation.md) · [Service runbook](../HydroOperationsService/README.md)
