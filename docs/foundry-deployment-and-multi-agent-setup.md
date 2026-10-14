# Foundry deployment and multi-agent setup

How the Hydro multi-agent setup fits together, and how to deploy all of its Foundry parts with **one
action**: the local deploy app or one command. Both run the same engine,
[`deploy_fabric_app.py`](../Raw/workspace-reset/deploy_fabric_app.py).

## Deploy in 60 seconds

**One-time setup** (Section 3 has the full checklist): `az login`, and install the Azure Developer CLI (`azd`).
The Foundry extensions are installed for you. Everything else, including the Foundry project itself, can be created
by the deploy when you tick **Create the Foundry project if none exists**.

| | Local deploy app | Command line |
|---|---|---|
| 1 | Double-click [`Start Fabric Demo.cmd`](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd) | |
| 2 | Pick tenant + workspace. In the **Foundry** block leave fields blank (discovered) or fill them in; tick *Create the Foundry project* only if none exists | Optional env vars, Section 6 |
| 3 | Press **Deploy app** | `python Raw\workspace-reset\deploy_fabric_app.py --tenant <tenant> --workspace <workspace> --push-config` |
| 4 | Wait for `SUCCESS` and `DEPLOYED_APP_URL`, then open the app and test it | same |

That single run does everything on the Foundry side: the project (only if you opted in), connections, the four
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
| Model | `gpt-5.6-sol` by default | Existing model deployment | `HYDRO_FOUNDRY_MODEL` | Must already exist |

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
| 1. Foundry project | Finds the one Foundry project. If none exists **and you opted in**, creates it with the Azure Developer CLI template `Azure-Samples/azd-ai-starter-basic`: resource group, Foundry account and project, the model deployment, a container registry with its project connection, Application Insights with its connection, Log Analytics. Waits for your role assignments to take effect | A project exists, or you did not opt in |
| 2. Checks | One Foundry project, model ready, one linked Application Insights, ontology generation 2, exactly one matching published Data Agent | never |
| 3. Connections | Writes `hydro-fabric-iq` and `hydro-fabric-ontology` and reads them back | never |
| 4. Prompt agents | Exports the four definitions, creates a new version only if a definition changed, reads each back | definition unchanged |
| 5. Image | Fingerprints every file the [Dockerfile](../HydroOperationsService/Dockerfile) packages; builds `hydro-orchestrator:src-<fingerprint>` in the project's ACR with `az acr build` | that tag already exists |
| 6. Configuration | Composes `HYDRO_ORCHESTRATOR_CONFIG` and `HYDRO_FABRIC_SOURCE_CONFIG` from the verified target | never (always compared) |
| 7. Publish | Sets the azd environment and runs `azd deploy hydro-orchestrator`; waits until the active version has the exact image digest and configuration | live agent already matches |
| 8. Verify | The existing gate: active, numeric version, immutable digest, source identity and native binding all match | never |

The local app shows these as separate progress stages: *Foundry project*, *Foundry agents*, *Hosted Agent*.

Consequences:

- **Code changes deploy themselves.** The fingerprint covers the Python service, the Node bridge and the TypeScript
  files copied into the image. A change anywhere there triggers one build and one new Hosted Agent version on the next
  deploy; an unchanged tree costs a single registry lookup.
- **Target changes redeploy the agent.** A new workspace, capacity, AppBackend, ontology or publishable key changes the
  configuration, so the agent is republished with fresh values (never copied from another target).
- **The right Foundry project is always the one published to.** `azure.yaml` pins one project. When that is your
  target, the repository is the azd project. For any other target (including a project the deploy just created) a
  generated, untracked azd project under `~/.fabric-demo/hosted-agent/` carries the same file with the target's
  endpoint. No tracked file is edited and a deploy can never reach the wrong project.
- **Creation is opt-in and repeatable.** The new project lives in `~/.fabric-demo/foundry-project/<name>`, where the name
  is derived from the tenant and workspace, so a rerun reuses the same resources instead of creating duplicates.
- **Production writes are preserved.** The live agent's `production_writes_enabled` is kept. Set
  `HYDRO_PRODUCTION_WRITES_ENABLED=true|false` (or the form's *Work-order writes*) to change it deliberately. A
  brand-new agent defaults to `false`.
- **Versions are never deleted.** Each publish is a new immutable version.
- The first run after pulling this feature builds and publishes once, because no fingerprint-tagged image exists yet.

## 3. One-time checklist

**Tools**

- Python 3.11+, Azure CLI, and the Azure Developer CLI `azd >= 1.35.0` (Node 24 is downloaded by the deployer).
- The azd extensions `azure.ai.agents` and `microsoft.foundry` are installed automatically when missing.
- `az login --tenant <tenant>`. If `azd` is signed out, the deployer opens its sign-in for you.
- No local Docker: images build remotely in ACR.

**Permissions**

- To **create** a Foundry project: Owner or Contributor on the subscription (the template creates a resource group and
  role assignments).
- To publish into an **existing** project: rights to create agent versions in it, and to run builds in its registry
  (for example Contributor on the registry). Chat users need **Foundry User** on the project.

**Either already exists, or you tick *Create the Foundry project* (Section 4)**

- A Foundry resource and project in the **same Entra tenant as the Fabric workspace**, in the capacity's region, with
  **public network access** (the browser calls it directly).
- A ready model deployment (default `gpt-5.6-sol`), **exactly one** linked Application Insights resource, and an Azure
  Container Registry connected to the project as a `ContainerRegistry` connection.

**Always required (not created by this deploy)**

- A generation-2 ontology with its graph materialized and `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` resolved
  ([knowledge-graph.md](knowledge-graph.md)), plus the published Data Agent `RTI_Demo_Agent_*`.

## 4. Local deploy app, step by step

1. Launch [`Start Fabric Demo.cmd`](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd) (`python launch.py` on any OS).
   It installs missing Python packages, signs in to Azure and opens `http://127.0.0.1:5000`.
2. Choose the tenant and workspace. Add a **SPA client id** only if discovery is ambiguous or you were given one.
3. **Deploy app** tab (or the same fields in the **Full workflow** tab), *Foundry* block. All fields are optional:

   | Field | Use it when |
   |---|---|
   | Foundry project endpoint | You have several Foundry projects, or want a specific one |
   | Model deployment | Not `gpt-5.6-sol` |
   | Container registry | The project has several registry connections |
   | Work-order writes | You want to enable or disable the human-approved write (default: keep the current setting) |
   | **Create the Foundry project if none exists** | You have no Foundry project. This **creates billable Azure resources** (account, project, model deployment, container registry, Application Insights) |
   | Region for the new project | Only when creating; default is the Fabric capacity's region |

   Values are validated by the local server and passed to the deployer as environment variables; nothing secret is
   collected. Blank fields fall back to the environment the app was launched with, then to discovery.
4. Fresh data environment: use the **Full workflow** tab (GitHub sync, `01_Pipe_Setup`, deploy), or run those first.
   An ordinary update needs none of that.
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

**Rollback.** Do not delete versions. Reactivate the last good one by redeploying its image digest with its
preserved configuration (Appendix A, steps 2 to 4), then rerun the deployer.

## 6. Settings

| Variable | Purpose | Default |
|---|---|---|
| `HYDRO_FOUNDRY_PROJECT_ENDPOINT` | Foundry project to use | Saved public endpoint, else the single discoverable project |
| `HYDRO_FOUNDRY_MODEL` | Model deployment for the agents | `gpt-5.6-sol` |
| `HYDRO_ACR_NAME` | Registry to build in | The project's only `ContainerRegistry` connection |
| `HYDRO_PRODUCTION_WRITES_ENABLED` | Allow the human-approved work-order write | Preserved from the live agent; `false` for a new one |
| `HYDRO_FOUNDRY_CREATE` | `true` creates a Foundry project when none exists (billable) | off |
| `HYDRO_FOUNDRY_LOCATION` | Azure region of the new project | The Fabric capacity's region |
| `HYDRO_FOUNDRY_PROJECT_NAME` | Name of the new project's azd environment | `hydro-foundry-<hash of tenant and workspace>` |
| `HYDRO_FOUNDRY_MODEL_VERSION` / `_SKU` / `_CAPACITY` | Model deployment details when creating | `2026-07-09` / `GlobalStandard` / `500` (the values of the working deployment) |
| `HYDRO_STATE_DIR` | Where generated azd projects live | `~/.fabric-demo` |
| `HYDRO_SPA_DISPLAY_NAME` | SPA discovery/creation name | `Hydro Operations Fabric Client` |

The local app sets the first six for you from its form; it does not read secrets.

Runtime configuration is generated, never edited by hand. `HYDRO_ORCHESTRATOR_CONFIG` carries `source`,
`authorization`, `project_endpoint`, `native_binding` and `production_writes_enabled` (`source.generation` must be `2`;
the data-agent connection must be `hydro-fabric-iq`). `HYDRO_FABRIC_SOURCE_CONFIG` carries the seven item GUIDs,
`api_url` and `publishable_key` of the target. The azd environment `hydro-orchestrator` lives in the git-ignored
`.azure/` folder and is created automatically from the live project on a new machine.
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
| `Foundry definition readback failed for <agent>` | The live definition differs from the exported one after creation. Rerun; if persistent compare with `agentDefinitions.ts`. |
| Browser 401 from Foundry | Foundry resource is in another tenant, or the user lacks **Foundry User**. |
| Chat shows *Source limitations: the written briefing could not be verified* | Not a deployment fault: the briefing failed its checks and only verified tables are shown. |

## 8. Known limits

**No deployment of this feature has been run.** It was verified by unit tests (fakes only; no cloud calls) and by
read-only checks:

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

- Creating a project provisions billable resources and is irreversible from this tool; remove them with `azd down` in
  `~/.fabric-demo/foundry-project/<name>` (this deletes everything in that environment).
- `az acr build` needs network access to the registry; private-only registries need a different build route.
- Hosting readiness is not agent execution readiness; interactive acceptance remains a separate check.

## Appendix A: the same steps by hand

Only for diagnosis or recovery; the deployer does all of this. Run from the repository root.

1. **Build** (`<tag>` is the deployer's `src-…`, or any unique tag):
   ```powershell
   az acr build --registry <acr> --image hydro-orchestrator:<tag> --platform linux/amd64 --file HydroOperationsService/Dockerfile .
   $digest = az acr repository show --name <acr> --image hydro-orchestrator:<tag> --query digest -o tsv
   ```
2. **Image and settings** in the azd environment `hydro-orchestrator` (`azd env new hydro-orchestrator` first on a new machine):
   ```powershell
   azd env set HYDRO_ORCHESTRATOR_IMAGE "<acr>.azurecr.io/hydro-orchestrator@$digest" --environment hydro-orchestrator
   azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME gpt-5.6-sol --environment hydro-orchestrator
   azd env set HYDRO_RUNTIME_STATE_DIR /home/session/hydro --environment hydro-orchestrator
   ```
3. **Configurations**: build both JSON values from the deployer's target state with the script below, then apply them.
4. **Publish and check**:
   ```powershell
   azd deploy hydro-orchestrator --environment hydro-orchestrator --no-prompt
   azd ai agent show --environment hydro-orchestrator --output json   # active, numeric version, your digest
   ```
5. **Rerun the deployer** so it verifies the agent and updates the SPA.

<details>
<summary>Step 3 script (<code>compose.py</code>, save outside the repository)</summary>

```python
import argparse, json, pathlib, subprocess, sys
sys.path.insert(0, "Raw/workspace-reset")
from provision_foundry_agents import source_configuration_digest
from deploy_hosted_agent import compose_runtime_configuration

p = argparse.ArgumentParser()
p.add_argument("--kusto-uri", required=True)        # Eventhouse query URI (origin only)
p.add_argument("--project-endpoint", required=True)
p.add_argument("--writes", action="store_true")
p.add_argument("--apply", action="store_true")
a = p.parse_args()

env = {}
for line in pathlib.Path("HydroOperationsApp/rayfin/.env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); v = v.strip()
        env[k.strip()] = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'" else v
tenant, workspace = env["RAYFIN_PUBLIC_TENANT_ID"], env["RAYFIN_PUBLIC_WORKSPACE_ID"]
ontology = json.loads(env["RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING"])["ontologyId"]
orchestrator, fabric = compose_runtime_configuration(
    tenant=tenant, workspace=workspace, ontology=ontology, spa_client_id=env["RAYFIN_PUBLIC_AAD_CLIENT_ID"],
    kusto_origin=a.kusto_uri, project_endpoint=a.project_endpoint, values=env,
    source_digest=source_configuration_digest(tenant, workspace, ontology, env), production_writes=a.writes)
for key, value in (("HYDRO_ORCHESTRATOR_CONFIG", orchestrator), ("HYDRO_FABRIC_SOURCE_CONFIG", fabric)):
    text = json.dumps(value, separators=(",", ":"))
    if a.apply:
        subprocess.run(["azd", "env", "set", key, text, "--environment", "hydro-orchestrator"], check=True)
    else:
        print(key, text.replace(env["RAYFIN_PUBLIC_PUBLISHABLE_KEY"], "pk-<masked>"))
```

Run it first without `--apply` to review, then with `--apply`.
</details>

## Appendix B: Fabric-side agents

The Fabric Data Agent (`RTI_Demo_Agent_<suffix>`, RTI_009) and the Ontology and Eventhouse Operations Agents
(`RTI_Demo_OpsAgent_<suffix>`, RTI_010) are provisioned by the setup notebooks, always against ontology v2 with the
separate playbooks, actions, Teams and email-alert wiring retained. Configuration or hosted-page success does not
certify their execution or delivery. See the [README](../README.md#ontology-generations-and-optional-agents).

## References

[Deployer](../Raw/workspace-reset/deploy_fabric_app.py) ·
[Foundry provisioner](../Raw/workspace-reset/provision_foundry_agents.py) ·
[Hosted agent publisher](../Raw/workspace-reset/deploy_hosted_agent.py) ·
[Launcher guide](../Raw/workspace-reset/README.md) · [App deployment guide](../HydroOperationsApp/DEPLOY.md) ·
[Implementation record](foundry-multi-agent-implementation.md) · [Service runbook](../HydroOperationsService/README.md)
