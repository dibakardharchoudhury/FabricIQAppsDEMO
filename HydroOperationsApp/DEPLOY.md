# Hydro Operations — Deployment Guide

<!-- markdownlint-disable MD029 MD033 MD060 -->

Deploy the Hydro Operations app to Microsoft Fabric. Run every command from
`HydroOperationsApp/` on **Node 24** (if your default Node differs, prefix with
`npx -y -p node@24 -c "<cmd>"`). Architecture: [README.md](README.md) · [root README](../README.md).

**Path:** build RTI env → install → configure → provision → deploy → seed & provision → live auth → start stream.

### Persistent Foundry agents

The canonical Python orchestrator also provisions the five Hydro Prompt Agents from
`src/services/copilot/agentDefinitions.ts` and verifies definition readback before publishing
the SPA. It uses the existing Foundry project selected by `HYDRO_FOUNDRY_PROJECT_ENDPOINT`
(or the saved public project endpoint); discovery must find one matching project in the
current subscription. `HYDRO_FOUNDRY_MODEL` selects an existing model deployment, defaulting
to `gpt-5-mini`. It does not create a new Foundry account or silently select an ambiguous project.

Separate Fabric IQ connections target the published Data Agent matching the live selected
generation-2 ontology and the Ontology endpoint directly. The Data Agent keeps direct-table
routing; its ontology query limitation does not gate those reads. Connection permissions and
`Foundry User` RBAC remain required.
`setup-live-auth` adds the Foundry Agent Service delegated scope and `DataAgent.Execute.All`;
do not configure the SPA permissions manually. Agent definition readback is not runtime
certification. Live acceptance must exercise direct-source Q&A, delegation, Fabric IQ, and
human-approved SQL creation independently of hosting checks.

The provisioner also resolves the existing project's single linked AppInsights resource
and exports its public resource ID for the crew's diagnostics link. Missing or ambiguous
monitoring links fail explicitly rather than pointing at an inferred resource. This does
not certify telemetry ingestion. The SPA shows real streamed execution receipts and a
response-scoped Logs query; full service spans remain in Application Insights.

#### October 7, 2026 acceptance status

The canonical deployment provisioned and read back Supervisor v1, Q&A v2, RCA v2,
Work Order v2, and Fabric IQ v1. The separate Data Agent and Ontology connections
use project-scoped delegated authentication. Older bridge resources were not deleted.
The SPA bundle was published, backend/CORS POST checks passed, and all 39 pre-existing
SPA redirect URIs were preserved. **The deployment exited with failure, not SUCCESS:**
the current identity could configure permissions but could not grant consent.

An Entra administrator must grant consent on **Hydro Operations Fabric Client**
(`3ccdb72d-6c81-4459-b9fd-d9293c242555`) for the already configured scopes:

- Microsoft Foundry Agent Service / Azure Machine Learning Services: `user_impersonation`.
- Power BI Service / Microsoft Fabric: `DataAgent.Execute.All`.

This is the exact manual action printed by `setup-live-auth` after its automated
grant attempts returned `Authorization_RequestDenied`; do not replace the SPA or edit
redirects. After consent, rerun the canonical deployment command and interactive acceptance.

Independent delegated CLI tests, which do **not** prove SPA consent or browser readiness:

- The persistent Supervisor returned a real Q&A delegation (about 5.8 seconds for that
  routing response alone, not end-to-end Q&A).
- Fabric IQ invoked the direct Ontology endpoint and returned the three facilities
  Foyers, Pitlochry, and Sloy (about 67 seconds). No Data Agent invocation was used
  for that question. This does not certify every ontology query or repair the known
  Data Agent-to-Ontology limitation.
- Fabric IQ separately invoked the published Data Agent and returned a reported open
  work-order count of 10 from operational SQL (about 45 seconds). Its result included
  an upstream output-moderation processing-error annotation. The count was not
  independently reconciled with SQL and does not certify execution provenance.

Browser answer parity, end-to-end latency, chart interaction, and edited Yes/No
approval followed by SQL readback remain unverified. Do not infer them from the
successful build, cloud definition readback, or these bounded endpoint tests.

Later verification confirmed Foundry consent for the current user after interactive
approval; only `DataAgent.Execute.All` remained missing. Tenant-wide admin consent is
not the only supported route: the validator also accepts complete per-user grants
for the current identity. A subsequent browser HTTP 400 was separately reproduced
as missing explicit message types in the Responses input. Typed message input and
tool-output continuation passed live HTTP 200 checks; the client correction requires
redeployment and must not be described as already active on the hosted page.
The Data Agent execution path now requests `DataAgent.Execute.All` explicitly alongside
its existing Fabric scopes. If tenant policy permits personal consent, asking a Data
Agent question presents the **Execute data agents** permission once; warm-up never opens
a popup. Ordinary telemetry and direct Q&A reads do not request this additional scope.

## Ontology v2 prerequisite and capability boundaries

The RTI setup and app require **Ontology v2 only** (`properties.generation == 2`).
An existing v1 item is rejected, not converted by an app redeploy. Provision a fresh workspace or
an unused `env_suffix` through the RTI setup workflow, then resolve the new ontology and dependent
sources. REST `/v1` URLs and environment suffixes such as `V9` are not ontology generations.

Core ontology, SQL/GraphQL, dashboard, weather, and app-hosting completion do not establish agent
readiness. Agent provisioning is always attempted; there are no agent mode or skip flags. Data
Agent provisioning verifies v2 source identity/readback. Operations Agent provisioning retains
separate Ontology/Eventhouse playbooks/actions, Teams, and email-alert setup. Real failures
propagate through RTI_009/010/011 and setup. Latest live attempts verified source
publication and stopped-agent configuration, not complete capability readiness: the ontology MCP
question failed with an unsupported API version even though its envelope said `isError: false`.
NB09 records only that exact temporary product response as `known_product_limitation`; publication
remains `published`. This is not healthy ontology runtime evidence. External product issues are not
claimed resolved.
NB09 `data_agent_publication_status=published` and NB10 `configured` record source/configuration
outcomes, not runtime execution. A successful bounded facility-record smoke check sets Data Agent
deployment `ready` and runtime `verified`; unrelated failed or inconclusive checks propagate through
NB09/setup. NB11's SQL source is configured independently of ontology runtime readiness and
preserves NB09's status.
NB10 leaves the agent stopped; `configured` verifies readback, not execution or delivery.
See the [ontology and agent policy](../README.md#ontology-generations-and-optional-agents).
The deployment orchestrator's `SUCCESS` verifies deployment/backend checks and hosting
availability, which may be an identity-matched Fabric private-hosting sign-in gate rather than
the application shell. It does not verify interactive authenticated application acceptance,
agent execution, alert delivery, or native graph readiness.

The last completed canonical deployment reported `SUCCESS` at 16:06 UTC at the existing
`https://icy-twist-acc301b27a-swedencentral.webapp.fabricapps.net` host, with backend/SQL checks and
all 32 existing SPA redirects preserved. The protected-hosting gate passed; the separate browser
authentication popup prevented interactive app acceptance. Subsequent native/API/model checks
passed independently, while full five-entity graph projection still returned HTTP 400.
See the [latest V3 acceptance record](../docs/knowledge-graph.md#latest-v3-acceptance-september-30-2026).

A final configuration refresh attempted at 17:27 UTC on September 30 stopped during SPA discovery
while waiting for Azure CLI browser sign-in. With the operator unavailable, that attempt was
cancelled before Rayfin state, build, or deployment changes. The 16:06 deployment remains in place;
the separately applied notebook updates and their verified failure-path result remain live.
Resume the repository's canonical deployment command after signing in with the requested tenant
account; do not replace deployment state or work around authentication using copied token caches.

For these corrections, use bounded notebook-definition updates, not another full `PreferRemote`
Git import: an earlier full import removed generated resources, subsequently restored through setup.
This acceptance record does not authorize a new import, reset, deployment, or alert send.

### Native graph materialization

Graph sources require keys and Delta/mirrored bindings. No published ontology-owned
projection/materialization REST endpoint is established. On October 2, 2026, a clean deployment
verified that a fresh generation-2 Ontology exposes an attached service-owned GraphModel child.
`RTI_006` followed the authoritative Ontology-to-GraphIndex lineage, authored the complete static
five-entity/four-relationship GraphModel definition, verified readback, observed successful
automatic refresh, and verified the Operations Agent GQL selector. If that attached child or its
authoritative lineage is unavailable, setup fails; use **Manage graph → select eligible entities and
relationships → Continue → Materialize** as the explicit operator fallback.

**Historical product behavior (2026-09-30):** the portal-authored full five-entity projection failed with HTTP 400
`ModelValidationError` / `InvalidPropertyType`, reporting `INVALID` for the TimeSeries properties
`event_time`, `value`, and `quality`. Selecting the native **facilities, systems, equipment, and
instruments** subgraph plus its **three hierarchy relationships** in Manage graph successfully
materialized **111 nodes / 108 edges**. Keep the original **five-entity / four-relationship ontology
and its TimeSeries properties intact**. The automated definition avoids that historical validation
failure by excluding only those three Eventhouse-bound properties from static GraphModel mappings;
it does not delete the Ontology properties or fabricate GraphQL topology.

Before building the app, the canonical deployment orchestrator resolves the selected generation-2
ontology's authoritative downstream `CascadeDelete` relation to its same-workspace `GraphIndex`,
verifies the corresponding GraphModel item, and generates `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING`
with `workspaceId`, `ontologyId`, and `graphModelId`. This is lineage discovery, not inference by
name, sole-graph presence, or sampled structure. If authoritative lineage is unavailable, an
operator may supply the same verified JSON fallback. In `.env`, single-quote the complete value:

```dotenv
RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING='{"workspaceId":"<workspace-guid>","ontologyId":"<ontology-guid>","graphModelId":"<managed-graph-guid>"}'
```

Keep those outer single quotes when adding alias maps: an unquoted `#` starts a dotenv comment,
including inside JSON double quotes, and can truncate exact ontology names containing `#`.

Use the repository's **`npm run env`** producer, not bare `rayfin env`, for frontend exports.
Rayfin 1.36's environment writer escapes JSON quotes/backslashes but its reader does not undo
them; repeated CLI rewrites otherwise corrupt the binding. The repository producer validates
the binding and workspace, reverses only that bounded known encoding, and writes canonical
single-quoted JSON in the primary source plus a freshly generated Vite `.env.local`. JSON Unicode
escapes preserve alias punctuation, including `#`, apostrophes, and `$`, through dotenv/Vite.
`prebuild`/`predev` run this producer; the canonical orchestrator also runs it after each deployment
command, including failures, because Rayfin can rewrite configuration after the build.
Do not repair generated `.env.local` by hand or relax the browser's strict JSON parser.
The outer quotes are removed by dotenv and are not part of the JSON.
Optional `nodeTypes` and
`edgeTypes` maps resolve `getQueryableGraphType` aliases to exact ontology type names or IDs when
projected labels differ. Do not guess a namespace-label delimiter. Public REST metadata does not
expose the ownership association; graph names, sole-graph discovery, and structural similarity
are not substitutes for an operator-confirmed binding through the selected ontology.

The app validates live numeric generation `2`, TMDL, graph metadata, queryable types/endpoints, and
native responses using GET `graphModels/{id}/getQueryableGraphType?beta=true` and POST
`graphModels/{id}/executeQuery?beta=true` under the workspace REST path. It follows opaque string
`result.nextPage` continuations and rejects warnings/errors, truncation, malformed/dangling results,
or more than 2,000 nodes/4,000 edges. Native results take precedence over the progressive
Ontology-bound STID view; both retain KQL/SQL context. Only declared ontology bindings supply
progressive relationships, never arbitrary physical foreign keys.
See [the graph contract and acceptance checks](../docs/knowledge-graph.md).

## Agent one-shot deployment

GitHub Copilot and other same-machine agents must run the repository orchestrator from the repository
root instead of assembling the numbered commands below:

```powershell
python Raw/workspace-reset/deploy_fabric_app.py `
  --tenant <tenant-guid-or-domain> `
  --workspace <workspace-guid-or-name> `
  --push-config
```

Use `--client-id <spa-app-guid>` only when SPA discovery is ambiguous. The script owns environment
validation, Node 24, Rayfin state reuse/provisioning, static deployment, SPA setup, redirect
preservation, permission/consent checks, hosted-page availability verification, and generated-origin
persistence. Interactive authenticated application acceptance remains a separate browser check.
The remaining numbered sections document those phases for operators and troubleshooting.

The repository locks the Rayfin CLI and SDK release set to **1.36.0**. The current Fabric
static-hosting service rejects the older 1.33.2 CLI with a runtime-settings HTTP 400 requiring
at least 1.35.0-alpha.1413. Updating only the CLI is insufficient: its static-hosting access-control
preflight also requires a compatible auth SDK, provider, client, and core package.
Use the locked repository dependency through the orchestrator, not an older globally installed CLI.

The orchestrator supplies a tenant-scoped Azure CLI Fabric token to each Rayfin child process
through the CLI's supported `RAYFIN_TOKEN` mechanism. It does not persist that token in environment
files or depend on Rayfin's separate MSAL cache, which can report a signed-in account while its
token is expired or unavailable after an SDK upgrade. Token acquisition is repeated before each
deployment command; Azure CLI sign-in and Conditional Access requirements still apply.

> [!IMPORTANT]
> **Browser sign-in requires a tenant-scoped Entra SPA.** `Hydro Operations Fabric Client` is the
> deployer's deterministic default display name for discovery/creation, not an Entra platform
> requirement. Override it with `HYDRO_SPA_DISPLAY_NAME`, or provide the SPA client ID directly.
> Runtime code uses only the dynamically resolved `RAYFIN_PUBLIC_AAD_CLIENT_ID`.
> The local deployer and `npm run setup-live-auth` attempt to create/configure it, but they cannot
> bypass tenant policy or directory roles. Deployment now stops before changing Rayfin state when
> no usable SPA client ID is available; it never publishes a bundle with broken browser sign-in.
> Use
> [No admin rights? Hand this to your Entra admin](#no-admin-rights-hand-this-to-your-entra-admin):
> an **Application Administrator / Cloud Application Administrator** creates the app and adds its
> SPA redirects and delegated permissions, and the same role grants tenant-wide admin consent
> (none of these scopes is directory-privileged, so Global Administrator is not required).
> Register the deployed
> `*.webapp.fabricapps.net` origin recorded in `rayfin/rayfin.yml` `allowedRedirectUris`.

## Which scenario am I in?

Find your row — it tells you exactly what to run. A workspace always belongs to one tenant, so
“new tenant” means its workspaces are new to you as well. The only two things that change between
hosting scenarios are **whether the SPA app registration already exists** (app regs are tenant‑scoped)
and **whether Rayfin's local state must be reset** (when the target workspace changes). Separately,
every target needs its own ontology-managed graph and matching explicit binding for graph features.

| Your situation | SPA app registration | Local Rayfin state | Do this |
|---|---|---|---|
| **First‑ever deploy — new tenant, new workspace** | The orchestrator discovers or creates it; an admin may still be required — [§App SPA](#b-app-spa-created-once-then-automated) | fresh (nothing to rotate) | Run the **agent one-shot deployment** above. |
| **Existing tenant, new / different workspace or capacity region** | **Reuse** the existing `RAYFIN_PUBLIC_AAD_CLIENT_ID` — don't recreate | The orchestrator backs up and rotates target-specific state | Run the **agent one-shot deployment** above; add `--client-id` only if discovery is ambiguous. |
| **Different tenant** (move the whole app elsewhere) | Create or discover a SPA in that tenant; never reuse a cross-tenant registration | The orchestrator backs up and rotates target-specific state | Run the **agent one-shot deployment** above against the new tenant/workspace. |
| **Same tenant, same workspace — iterating on code** | already set up | reused automatically | Run the **agent one-shot deployment** above. It deploys, reapplies live auth, and preserves existing redirects. |

> **Agents:** the canonical automated runbook is
> [`.github/prompts/deploy-fresh-tenant.prompt.md`](../.github/prompts/deploy-fresh-tenant.prompt.md).
> The golden rule everywhere: **SPA redirect URIs, delegated permissions, and admin consent are done
> by `npm run setup-live-auth` first. Use the documented portal fallback only for actions the
> operator lacks directory rights to perform.**

## Prerequisites

- **Node.js/npm/npx** on PATH. Commands run under Node 24 (the app pins `>=24 <25`);
  the local **Deploy app** action downloads Node 24 through npx and restores all locked packages.
- A **Fabric workspace** on a usable capacity, with permission to deploy.
- A current **Azure CLI** (`az`) for tenant discovery and the one‑time live‑auth step (Step 8).
  Microsoft Entra [CAE claim challenges](https://learn.microsoft.com/entra/identity-platform/app-resilience-continuous-access-evaluation)
  require clients to bypass rejected cached tokens. The orchestrator always starts with the user's
  current Azure CLI session; it does not reuse a saved deployment login. Only if Entra returns a CAE
  challenge does it sign in under `%TEMP%\fabric-demo-azure-cli\<tenant>`, preserving any previous
  recovery directory under a timestamped `.stale-*` name. It never logs out, clears, or changes the
  user's normal `%USERPROFILE%\.azure` cache or another tenant's directory.
  If a clean login is rejected during pre-deployment checks, deployment stops before Rayfin state
  changes. Final Entra checks use the same bounded recovery after deployment; persistent rejection
  then fails validation with guidance to upgrade the CLI or review Conditional Access. See Microsoft's
  [Azure CLI sign-in guidance](https://learn.microsoft.com/cli/azure/authenticate-azure-cli-interactively).
- **Two Entra identities** — a **pre-provisioned notebook SPN** (secret in Key Vault, used by the pipelines) and a delegated **app SPA** (no secret, used by the browser). See [Identities and permissions](#identities-and-permissions).
- **Fabric tenant settings** (Admin, one‑time): *Service principals can use Fabric APIs* and *Copilot / AI* enabled — needed by `Pipe_Setup` and the Data Agent ([root README](../README.md)).
- **Email alerts:** the Outlook email activity needs a mailbox-backed OAuth2 connection, not a notebook service-principal connection. RTI_010 retains Operations Agent, playbook/actions, Teams, and `Pipe_SendEmailAlert` provisioning against v2. Create the mailbox connection and configure destinations as described in the [root README](../README.md); verify actual delivery separately.

## Identities and permissions

This solution uses **two separate Entra identities** — they are not interchangeable, and each is created and configured differently.

| | **Notebook SPN** (provisioning) | **App SPA** (runtime sign-in) |
|---|---|---|
| Entra app type | Confidential client — **has a secret** | Public client / **single-page app — no secret** |
| Auth mode | **App-only** (client credentials) | **Delegated** (the signed-in user's token) |
| Used by | `Pipe_Setup` → `RTI_001` / `RTI_011` notebooks | The browser app → Eventhouse, STID GraphQL, Fabric REST |
| Secret storage | **Azure Key Vault** (3 secrets) | none — no secret is ever stored |
| Who provisions it | Platform/security admin, **before** Step 1 | Deployer, **once** (portal or `az`), then Step 8 configures it |
| Recorded in repo | KV secret **names** in `Pipe_Setup` params (not values) | `RAYFIN_PUBLIC_AAD_CLIENT_ID` in `rayfin/.env` |

### A. Notebook SPN (pre-provision first)

The pipeline only asks for **Key Vault coordinates** (vault URI + three secret *names*), so the SPN and its secrets must already exist. One-time, by an admin who can register apps and manage the vault:

1. **Register an Entra app** (or reuse one) and create a **client secret**.
2. Store three secrets in the vault — e.g. `tenantid`, `clientid`, `clientsecret` (values = the SPN's tenant id, application/client id, and client secret).
3. Grant the SPN **Key Vault Secrets User** (Get) on those secrets, **Contributor** on the Fabric workspace, and add it to the **"Service principals can use Fabric APIs"** allowed group. Full list + private-endpoint note: [root README → Prerequisites](../README.md).
4. Enter the vault URI + the three secret **names** into the `Pipe_Setup` parameters (Step 1). Notebooks read the secret *values* at run time via `notebookutils.credentials.getSecret` — **no secret enters the repo**.

### B. App SPA (created once, then automated)

The browser app signs the **user** in through a delegated SPA registration (no secret). Creating that registration is the one manual step; Step 8 (`npm run setup-live-auth`) automates everything after it.

**Create the SPA** — `az` one-liner or portal:

```powershell
az ad app create --display-name "Hydro Operations Fabric Client" --sign-in-audience AzureADMyOrg --query appId -o tsv
```

…or Entra portal → **App registrations → New registration** → name it, *Accounts in this organizational directory only* → **Register**. Copy the **Application (client) ID** into `rayfin/.env` → `RAYFIN_PUBLIC_AAD_CLIENT_ID` (Step 3). Creating an app registration needs **Application Administrator** (or tenant self-service app registration enabled).

**Step 8 then configures that app automatically:** SPA redirect URIs, the two delegated permissions, and admin consent.

#### Why the enterprise application / service principal is required

The SPA has two related Entra objects with different jobs:

| Object | Purpose | Configuration stored there |
|---|---|---|
| **App registration (application object)** | Defines the browser application's identity and OAuth contract | Application (client) ID, single-tenant audience, SPA redirect URIs, and requested delegated API permissions |
| **Enterprise application (service principal)** | Tenant-local instance of that application | Tenant-wide or per-user delegated consent grants linking this client to the Azure Data Explorer and Power BI/Fabric resource service principals |

The browser authenticates as the signed-in **user**, not as an app-only daemon. The SPA has no
secret and the service principal gets no Fabric workspace role. It is required because Microsoft
Graph stores `oauth2PermissionGrants` against the client service principal. Without it,
`az ad sp show --id <client-id>` and consent creation fail even if the app registration exists.
Create the missing tenant instance with:

```powershell
az ad sp create --id <spa-client-id>
```

The complete live-auth contract is:

- **SPA redirect URIs on the app registration:** every URI already registered in Entra, plus the
  current `https://<host>.webapp.fabricapps.net` deployment origin and `http://localhost:5173` for
  local development. Existing Entra redirects are never removed. Historical origins found only in
  local `rayfin.yml` configuration are not recreated.
- **Requested delegated permissions on the app registration:** Azure Data Explorer
  `user_impersonation`; Power BI Service/Microsoft Fabric `GraphQLApi.Execute.All`,
  `Workspace.Read.All`, `Item.Read.All`, and `Item.Execute.All`.
- **Consent on the enterprise application/service principal:** tenant-wide `AllPrincipals`
  admin consent is the enterprise target. If tenant policy allows user consent, a per-user
  `Principal` grant works only for that consenting user.
- **Separate user/cluster controls:** grant each signed-in user KQL Database Viewer on the
  Eventhouse and allow each deployed hosting origin in Eventhouse CORS. These are not permissions
  granted to the SPA service principal.

> **Configured does not mean consented.** The API permissions page can list all five delegated
> scopes while runtime tokens still cannot contain them. The app registration's list only declares
> what the SPA may request. Consent is a separate `oauth2PermissionGrant` on the enterprise
> application. In Entra, the **Status** column must show **Granted for &lt;tenant&gt;** for tenant-wide
> readiness. A blank Status means consent is missing; a disabled **Grant admin consent** button means
> the signed-in administrator lacks a consent-granting directory role. Per-user consent may exist for
> someone else and still fail verification for the deployment operator or other users.

**If the tenant blocks the script** (missing role or restricted consent), it prints the exact action and continues — complete these in the Entra portal on that app registration:

1. **Authentication → Add a platform → Single-page application** → preserve every existing redirect and add the current hosting origin from `rayfin/rayfin.yml` (`allowedRedirectUris`) **and** `http://localhost:5173`. Do not remove existing redirects. *Fixes AADSTS50011.* Needs **Application Administrator** on the app.
2. **API permissions → Add a permission → APIs my organization uses** → add these **Delegated** scopes:
   - **Azure Data Explorer** → `user_impersonation` — Eventhouse telemetry (resource app id `2746ea77-4702-4b45-80ca-3c97e680e8b7`).
   - **Power BI Service** (resource app id `00000009-0000-0000-c000-000000000000`) → `GraphQLApi.Execute.All` (STID Lakehouse GraphQL), `Workspace.Read.All` (workspace item discovery), **`Item.Read.All`** (read the Eventhouse query URI — **required for live telemetry**; without it the app reports “No Eventhouse found”), and `Item.Execute.All` (run notebooks/pipelines from the app).

   *Fixes AADSTS650057.*
3. **Grant admin consent** for the directory (the *Grant admin consent* button). *Fixes AADSTS65001.* Needs **Application Administrator / Cloud Application Administrator**. If you can't and **user consent is allowed**, each user is prompted to consent on first sign-in instead — all five scopes are user-consentable, so this step is optional in most tenants.

`setup-live-auth` normally grants **all** of the above automatically (tenant‑wide, `AllPrincipals`),
so no in-app consent popup should appear after setup. If Entra serves edge-cached configuration for
a minute or two, a one-time **Accept** prompt is harmless. Do these steps by hand only for the exact
grant the script prints it could not make.

Two per-cluster grants stay manual either way: give the signed-in user **KQL Database Viewer** on the Eventhouse, and allow the app origin in the Eventhouse cluster's **CORS** settings.

#### No admin rights? Hand this to your Entra admin

If you can't register apps or grant consent, none of the above blocks you — a **directory admin does it once**, then the whole rest of the deploy is yours (no admin needed again unless the app registration must change). Generate the exact, copy‑pasteable list first:

```powershell
npm run setup-live-auth:dry   # writes nothing — prints every URI/scope/consent it WOULD apply
```

Send your admin this checklist (the dry run prints the concrete values for each `<…>`):

| # | Action | On | Role the admin needs |
|---|---|---|---|
| 1 | **Create** the SPA app registration (single‑tenant, no secret), ensure its **enterprise application/service principal** exists (`az ad sp create --id <spa-client-id>`), and give you the **Application (client) ID** | Entra ID → App registrations / Enterprise applications | **Application Administrator** (or self‑service app registration enabled) |
| 2 | **Authentication → Single‑page application** → preserve every existing redirect and add the current origin from `rayfin/rayfin.yml` `allowedRedirectUris` **+** `http://localhost:5173` | that app | **Application Administrator** on the app (or make you an **Owner** — then you can do 2 yourself) |
| 3 | **API permissions** → add the delegated scopes from the list above (ADX `user_impersonation`; Power BI `GraphQLApi.Execute.All` + `Workspace.Read.All` + `Item.Read.All` + `Item.Execute.All`) | that app | **Application Administrator** on the app |
| 4 | **Grant admin consent** for the directory | that app | **Application Administrator / Cloud Application Administrator** |

Fastest split of duties: ask the admin to do **1 and 4** and make you an **Owner** of the app — then you run `npm run setup-live-auth` yourself and it applies **2 and 3** (redirect URIs + permissions) with no further admin involvement. If the admin does all four by hand, you never run `setup-live-auth`; just make sure the hosting origins in step 2 match `rayfin/rayfin.yml` after each deploy that changes the hostname. If admin consent (step 4) is impossible but **user consent is allowed** in the tenant, skip it — each signer is prompted to consent on their first sign‑in instead.

## 1. Build the RTI Fabric environment (in Fabric)

Open **`01_Pipe_Setup`** in your workspace, fill its parameters ([root README](../README.md)), and
run it. This creates the Lakehouse, Eventhouse, v2 ontology, and dashboard, and attempts Data Agent
and both Operations Agent deployments without mode or skip flags. Real failures fail setup; NB09
alone reports the exact documented temporary unsupported-Ontology-v2 product response without
claiming runtime readiness. It does **not**
create the STID GraphQL API or seed the operational SQL DB — those happen in Step 7.

> **Operations Agent provisioning includes playbook/actions, Teams, and email-alert wiring.**
> Configure the run-as identity, Teams destination, and Outlook OAuth2 connection as described in
> the root README. Check `ops_agent_deployment_status` and its reason, then separately verify real
> execution and delivery. No mode permits a generation-1 ontology fallback.

## 2. Clone and install

```powershell
git clone https://github.com/dibakardharchoudhury/FabricIQAppsDEMO.git
cd FabricIQAppsDEMO/HydroOperationsApp
npm ci
```

`npm ci` installs the exact versions in `package-lock.json`, including the repository-local Rayfin
CLI — don't install Rayfin globally. The local **Deploy app** action runs this automatically when
`node_modules` is missing, invalid, or does not match the current lockfile. Otherwise it reuses the
verified dependency tree, avoiding a clean reinstall on every deployment. This manual command is
only for direct CLI development/deployment.

## 3. Configure

```powershell
Copy-Item rayfin/.env.example rayfin/.env
```

Fill the four **required** values in `rayfin/.env` (no secrets belong here):

```ini
FABRIC_WORKSPACE_NAME=<Fabric workspace display name>
RAYFIN_PUBLIC_WORKSPACE_ID=<Fabric workspace GUID>
RAYFIN_PUBLIC_AAD_CLIENT_ID=<Entra SPA app (client) id>
RAYFIN_PUBLIC_TENANT_ID=<Entra tenant id>
```

Validate them before provisioning or deploying. The same check runs automatically before every
development server and production build, including deployments started by the local app or Copilot:

```powershell
npm run validate-env
```

Most artifact ids/URIs are **discovered at runtime** by workspace display name; the
`AUTO-DISCOVERED FALLBACKS` only need values if you want to pin something. Never edit `.env.local`
(the build writes `VITE_RAYFIN_*` into it automatically).

The canonical orchestrator refreshes these public data pointers from **the selected workspace's
metadata before every build**, including when it reuses a healthy AppBackend. It never carries
the template's V6 data names or a previous workspace's KQL/GraphQL endpoints into a V3 deployment.
It updates only the source `rayfin/.env`; Rayfin remains responsible for generated `.env.local`
and deployment state. Metadata-resolution failures stop before source writes or state rotation.

- **Telemetry:** use a configured Eventhouse name only when it is found in the same configured
  tenant/workspace; otherwise require a unique `RTI_Demo_Eventhouse` or suffix-derived name such
  as `RTI_Demo_Eventhouse_V<n>` / `RTI_Demo_Eventhouse_VJOA_2`.
  An ontology-managed Eventhouse is not a fallback. KQL database selection verifies the live
  `properties.parentEventhouseItemId`, then uses that database's `displayName` and
  `queryServiceUri`—not a same-named database belonging to another Eventhouse. Multiple eligible
  databases require a unique configured `RAYFIN_PUBLIC_KQL_DATABASE` name within that parent.
- **STID:** resolve the target's `Hydro_STID_API` (or a target-verified configured
  `RAYFIN_PUBLIC_STID_GRAPHQL_NAME`). If it has not been created by RTI_011 yet, clear stale
  GraphQL URL/ID overrides and let runtime discovery find the later-created API. The deployment
  does not invent an API item or query an old endpoint.
- Pipeline/notebook, Lakehouse, and dashboard pointers are similarly target-scoped. Missing
  optional artifacts clear old IDs; unverified Lakehouse SQL endpoint overrides are cleared.
  Ambiguous eligible names fail closed rather than selecting the first workspace item.
- The orchestrator generates `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING` from the selected generation-2
  ontology's authoritative Fabric item lineage when exactly one same-workspace materialized
  GraphModel is related. Multiple lineage matches fail closed. A supplied binding remains an
  operator-verified fallback when lineage is unavailable; its workspace and referenced
  Ontology/GraphModel identities must match. The orchestrator never selects a graph by name,
  sole-graph presence, or sampled structure and never materializes or changes the graph.

### Optional: the Azure AI Foundry copilot

The Copilot page ships two engines, not two ontology generations. **Data Agent** requires supported
v2 onboarding and a published source matching the selected live v2 ontology/workspace; the app
verifies this before invoking MCP and propagates runtime failures. **Foundry** is a separate engine
that runs an Azure AI Foundry model in the browser with tools scoped to the
Lakehouse `silver_*` tables, the Eventhouse and the app database. Add these to `rayfin/.env` to
enable it — the engine toggle only appears when both are set:

```ini
RAYFIN_PUBLIC_FOUNDRY_ENDPOINT=https://<resource>.services.ai.azure.com/openai/v1/responses
RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT=<model deployment name>
```

Set the complete model inference URL. The app sends every model request to this exact value; it
does not replace the host or append an API route. For an `AIServices` resource, use the URL ending
in `/openai/v1/responses`. Do not use the Foundry project endpoint ending in
`/api/projects/<project-name>`; that URL is for project SDK and management operations.

The deployed values seed **Administration → Foundry Copilot**. Changes made there are stored in the
current browser and apply to the next question without rebuilding or redeploying the app. The
deployment value is sent as the Responses API `model`; there is no separate API-version setting.

**The Foundry resource MUST live in the same Entra tenant as the Fabric workspace.** The app's MSAL
authority is pinned to `RAYFIN_PUBLIC_TENANT_ID`, so a resource in any other tenant rejects the
token with 401 no matter what RBAC you assign — Azure evaluates RBAC in the resource's *own* home
tenant. Check with `az account list --all` before creating it; a personal or corp subscription is
easy to pick by accident. Put it in the workspace capacity's region too (Sweden Central here).

One manual step after creation, because it is resource-scoped rather than tenant-scoped: grant each
app user the **`Cognitive Services OpenAI User`** role on the resource. The delegated Entra scope
authorizes the audience, not the data-plane call.

No CORS configuration is needed — the data plane already returns `Access-Control-Allow-Origin: *`
and permits `Authorization` on POST. Do keep the resource on **public network access**: a private
endpoint or a "selected networks" firewall cuts the browser off.

No key is ever placed in the browser. Because the tools run as the signed-in user, the copilot
cannot read anything that user could not read in Fabric.

## 4. Sign in to Rayfin

```powershell
npx rayfin logout
npx rayfin login --select   # pick the tenant that owns your workspace
npx rayfin login status
```

## 5. Provision the backend and SQL schema

```powershell
npm run up          # create/update AppBackend + auth + data services
npm run rayfin:db   # apply rayfin/data/schema.ts to the live SQL database (creates tables, no rows)
```

## 6. Deploy the app

> [!NOTE]
> This command documents the underlying operator phase. Agents must invoke the repository
> orchestrator from the root; they must not run this phase directly.

```powershell
npm run deploy      # builds (tsc + vite, repository env producer) and deploys the static app
```

The local **Deploy app** action uses this static-only command when its saved AppBackend still exists
in the selected workspace. It runs full `rayfin up` for a fresh or changed target, and updates the
backend after every static deployment, even when the generated hosting origin was already
registered. This reapplies persisted runtime/CORS settings and the database configuration after a
managed-service restart.

The one-shot orchestrator does not report `SUCCESS` from the hosted HTML page alone. It also checks
that the generated API URL contains the workspace's current capacity, workspace, and AppBackend
ids, then sends browser-equivalent CORS preflights to both `/graphql` and
`/api/auth/v1/token`, a minimal GraphQL POST, and a deliberately incomplete token POST. Transient
backend warm-up and token HTTP 5xx responses are retried with bounded backoff; missing
`Access-Control-Allow-Origin`, required headers, GraphQL readiness, or persistent token 5xx remains
a deployment failure.

### Protected hosting availability versus application acceptance

Rayfin 1.36 protected static hosting can return **HTTP 401 JSON** to a generic `Accept: */*`
request while returning **HTTP 200 HTML** to `Accept: text/html`. That HTML can be Fabric's
**Sign in to continue** private-hosting gate, not the Hydro Operations application bundle.
The orchestrator requests HTML without following redirects and accepts only an identifiable
Hydro Operations production app shell or the recognized Fabric gate. For a gate, its bootstrap
must point to the official Fabric broker and match the deployment's tenant, workspace, and
validated AppBackend ID. A bare 401, redirect, arbitrary HTML, malformed gate, or mismatched
identity still fails verification.

The output distinguishes `HOSTING_VERIFICATION=protected-sign-in-gate` from
`HOSTING_VERIFICATION=app-shell`, and prints `INTERACTIVE_APP_ACCEPTANCE=not-performed`.
The gate result verifies guarded hosting availability; **the application bundle/UI was not
loaded**. Even the app-shell result does not establish JavaScript execution or authenticated
UI behavior. The final `SUCCESS` reports deployment checks, not completed browser acceptance.

Preserve the CLI-generated `services.staticHosting.assetAccess: protected` setting. Do not
make hosting public, hand-edit generated state/redirects, or enable external Entra exchange to
make a probe pass. An Azure CLI Fabric bearer token is not a substitute for the hosting browser
sign-in flow. For interactive acceptance, open the deployed URL, complete Fabric sign-in with an
authorized account, then verify that Hydro Operations loads and its required authenticated
features work. Record that acceptance separately from the orchestrator's read-only hosting probe.

The orchestrator records the generated **hosting URL** and runs the idempotent `setup-live-auth`
workflow. It preserves every existing Entra SPA redirect registration and adds only the current
hosting origin plus `localhost:5173`; never remove existing registrations or recreate historical
origins merely because they remain in local configuration. Do not hand-edit `rayfin/rayfin.yml`
`allowedRedirectUris` or run a separate `npm run up` to configure redirects. Manual portal changes
are permitted only for the exact prerequisite action the script reports it lacks rights to perform.
After successful deployment checks, a fresh app still needs operational seeding and STID binding
through Step 7; hosting success does not establish native graph readiness.

## 7. Seed & provision (RTI_011)

`RTI_011` is the authoritative seeder — its `MERGE` re‑seeds all five tables safely on every run.

1. Open the deployed app and sign in (avatar button).
2. Click **"Seed & provision"** in the header.

It runs `RTI_011` in your workspace, which upserts the operational tables, creates + **auto‑binds**
the STID **GraphQL API** to the Lakehouse SQL endpoint. It also always adds SQL to the live Data
Agent, preserving existing sources and custom configuration and verifying exact draft and published
SQL-source readback. Missing agents and extension failures fail the notebook even if SQL/GraphQL
writes already succeeded. Successful configuration does not certify agent runtime. RTI_011 does
not require or reinterpret NB09's ontology runtime status; the bounded NB09 smoke check does not
attest execution provenance or SQL/combined-source readiness. The revised NB09 live limitation
path was validated once with full multi-source
preservation; neither NB11 nor full setup was executed in that bounded run. See the
[authorized failure-path evidence](../docs/knowledge-graph.md#authorized-live-nb09-failure-path-validation).
The app discovers the GraphQL endpoint at runtime — leave `RAYFIN_PUBLIC_STID_GRAPHQL_URL` blank.

> **If auto‑bind fails** (see the notebook's STEP B output): open the GraphQL API item in the Fabric
> portal once and add the STID tables (`Facilities`, `Systems`, `Equipment`, `Instruments`).
>
> **Fallback (no RTI_011):** if `RAYFIN_PUBLIC_POSTSEED_NOTEBOOK_NAME` is blank, the app self‑seeds
> demo rows only when a table is empty. For direct SQL, run
> [`sql/seed-operational-data.sql`](sql/seed-operational-data.sql) (`-v ActorOid=<your-oid>`) and
> verify with [`sql/validate-seed.sql`](sql/validate-seed.sql).

## 8. Switch on live auth (once per fresh deploy)

The browser calls the Eventhouse (KQL) and STID GraphQL directly, each needing a delegated Entra
permission. Run once after the first deploy of a fresh build (a rebuild gets a new hosting hostname):

```powershell
az login                  # as an owner / Application Administrator
npm run setup-live-auth   # or setup-live-auth:dry to preview
```

`scripts/setup-live-auth.mjs` is idempotent: (1) reads the current hosting origin from `rayfin/rayfin.yml`
(`allowedRedirectUris`), preserves every SPA redirect already in Entra, and adds only that current
origin plus `localhost:5173` as **SPA redirect URIs** on the Entra
app (fixes **AADSTS50011**); (2) adds **Azure Data Explorer** `user_impersonation` and the
**Power BI Service / Microsoft Fabric** scopes `GraphQLApi.Execute.All`, `Workspace.Read.All`,
**`Item.Read.All`** (needed for live telemetry — the Eventhouse query URI), and `Item.Execute.All`,
plus **Microsoft Cognitive Services** `user_impersonation` (the Foundry copilot),
then grants admin consent tenant‑wide (fixes **AADSTS650057 / 65001**). Because these are pre‑granted,
**no in‑app consent popup appears** on Seed & provision or Connect telemetry.

Where the signed‑in identity lacks a role, the script **prints the exact manual action and continues** —
complete those on the app registration in the Entra portal (see [Identities and permissions → App SPA](#b-app-spa-created-once-then-automated)).

Two things stay manual (per user/cluster): grant the signed‑in user **KQL Database Viewer** on the
Eventhouse, and allow the app origin in the Eventhouse cluster's **CORS** settings.

## 9. Start the telemetry stream

Run **`02_Pipe_Stream`** in Fabric (or click **"Start stream"** in the app) to push an OPC UA burst
into the Eventhouse. Live gauges populate once telemetry lands and Step 8 auth is in place.

## Redeploying to a different tenant, workspace, or region

Moving the app to a **new tenant, workspace, or capacity** requires resetting Rayfin's per-workspace
state and re-pointing every tenant-scoped identity — otherwise a stale `active` deployment pointer
makes `rayfin up` target the old (now non-existent) workspace and fail with a 404.

### 1. Rotate local Rayfin state

```powershell
# from HydroOperationsApp/
$backup = Join-Path ([IO.Path]::GetTempPath()) ("fabric-demo-rayfin-backup-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $backup | Out-Null
Get-Item rayfin/.env, rayfin/.env.local, rayfin/.deployments.json -ErrorAction SilentlyContinue |
  Move-Item -Destination $backup
```

- `rayfin/.deployments.json` holds an `active` pointer to the previous workspace/backend. Left in
  place, `rayfin up` calls the **old** endpoint and fails with **404 "The provided workspace was not
  found."** Move it (and `.env.local`) into the temporary backup when switching tenants; do not
  delete either file.
- A workspace capacity move also invalidates the capacity-specific `RAYFIN_PUBLIC_API_URL`, even
  when the AppBackend item still exists. The one-shot orchestrator compares the saved API URL with
  the workspace's current `capacityId`, backs up the three state files above, and performs a full
  reprovision when they differ.
- Recreate `rayfin/.env` from `.env.example` with the **new** `FABRIC_WORKSPACE_NAME`,
  `RAYFIN_PUBLIC_WORKSPACE_ID`, `RAYFIN_PUBLIC_TENANT_ID`, and the new tenant's SPA
  `RAYFIN_PUBLIC_AAD_CLIENT_ID`. Run `npm run validate-env` before continuing. Resolve the workspace
  GUID by display name:

  ```powershell
  $tok = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
  (Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/workspaces" -Headers @{Authorization="Bearer $tok"}).value |
    Where-Object displayName -like '*<workspace-name>*' | Select-Object displayName,id,capacityId | Format-List
  ```

### 2. Register a fresh SPA in the new tenant

> **Same tenant, only a different workspace/region?** The SPA app registration is **tenant‑scoped**, so
> **reuse the existing `RAYFIN_PUBLIC_AAD_CLIENT_ID`** — do **not** create a new one. Keep it (and
> `RAYFIN_PUBLIC_TENANT_ID`) in the new `.env`; only `FABRIC_WORKSPACE_NAME` + `RAYFIN_PUBLIC_WORKSPACE_ID`
> change. `npm run setup-live-auth` then just **adds the new hosting origin** to the existing app and
> re‑confirms consent (already `AllPrincipals`, so it's a no‑op). Skip the `az ad app create` below.

A **different tenant** is the only case that needs a new app. App registrations are tenant-scoped — the
old client id won't work. Create one (see
[Identities → App SPA](#b-app-spa-created-once-then-automated)) and put its id in the new `.env`:

```powershell
az login --tenant <new-tenant-guid> --allow-no-subscriptions
az ad app create --display-name "Hydro Operations Fabric Client" --sign-in-audience AzureADMyOrg --query appId -o tsv
```

### 3. Point Rayfin at the new tenant

```powershell
npx rayfin logout
npx rayfin login --select     # pick the NEW tenant
npx rayfin login status       # confirm tenant + user before deploying
```

### 4. Provision non-interactively

Pass the workspace **GUID** and auto-accept so `rayfin up` never stops on the interactive
*"Enter a Fabric workspace name"* prompt (its redraw UI is easy to mis-answer when scripted):

```powershell
rayfin up --workspace-id <workspace-guid> --yes
```

`rayfin up --help` also exposes `--workspace <name>`, `--workspace-uri <portal-url>`, `--tenant <id>`,
`--dry-run`, and `--exclude-services staticHosting`. To repoint an **existing** deployment record
without re-provisioning, use `rayfin switch <workspace-name>` (it rewrites `rayfin/.env`).

For an agent-driven deployment, return to the repository root and run the one-shot orchestrator with
the new tenant/workspace instead of continuing these phases individually. It provisions the schema,
deploys the static app, preserves and extends SPA redirects, configures live auth, and verifies the
hosted app shell or identity-matched protected sign-in gate. Interactive authenticated application
acceptance remains separate.

### Node 24 wrapper — gotchas

If your default Node isn't 24, wrap **every** command; call the binary/script **directly** inside `-c`:

```powershell
npx -y -p node@24 -c "npm run up"                              # ✅
npx -y -p node@24 -c "rayfin up --workspace-id <guid> --yes"  # ✅
```

- **Don't nest npx.** `npx -y -p node@24 -c "npx rayfin …"` fails with an npm **EUSAGE** error.
- The `-c` string runs in its **own shell at an unspecified cwd**. If it can't find `rayfin.yml`,
  put the directory inside the string: `-c "cd /d <abs-path>\HydroOperationsApp && rayfin up …"`.

### Feature & region gating (Fabric App Items preview)

Rayfin's backend is a **Fabric App Item** (preview). Creating it can fail with:

```text
Fabric API error: 403 Forbidden — The feature is not available
```

Work through these in order:

1. **Tenant setting** — Admin portal → **Tenant settings → Microsoft Fabric → "Enable Fabric App
   Items (preview)"** must be **On**. Verify it via the admin API (setting name `AppBackendTenant`):

   ```powershell
   $tok = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
   (Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/admin/tenantsettings" -Headers @{Authorization="Bearer $tok"}).tenantSettings |
     Where-Object settingName -eq 'AppBackendTenant' | Select-Object settingName,title,enabled
   ```

2. **Propagation** — after enabling, allow **~15 minutes** for the setting to take effect before
   retrying. A create call issued too soon still sees the feature as disabled and returns `403`.
3. **Region** — the preview is **not offered in every region**. If it keeps returning `403` while the
   tenant setting reads `enabled = True`, host the workspace on a capacity in a **supported region**
   (this solution has deployed successfully on **Sweden Central**; **North Europe** was rejected as
   "feature not available"). List capacities + regions, activate one in a supported region, then
   reassign the workspace (portal → **Workspace settings → License/Capacity**) and re-run `rayfin up`:

   ```powershell
   $tok = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
   (Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/capacities" -Headers @{Authorization="Bearer $tok"}).value |
     Select-Object displayName,sku,region,state | Sort-Object region | Format-Table -AutoSize
   ```

## Update the app version

The version shown in the Hydro Operations UI comes from `package.json`.

From the `HydroOperationsApp` folder, run:

```powershell
npm version 1.0.2 --no-git-tag-version
```

## Troubleshooting

| Problem | Fix |
| --- | --- |
| **"System cancelled the Spark session"** running RTI_011 | Its lakehouse binding is stale — re‑import RTI_011 (Fabric **source control → Update**, or script it via [Raw/workspace-reset](../Raw/workspace-reset/README.md)) or re‑run `RTI_001`, then retry. |
| **"No GraphQL API found"** / STID panels empty | Run **Seed & provision** (Step 7). If STEP B reports auto‑bind failed, bind the STID tables in the portal. |
| Live signals stay empty | `02_Pipe_Stream` must have run (Step 9) **and** Step 8 live‑auth must be in place. |
| **Static deploy `401 Unauthorized`** | Rayfin's cached Fabric token is stale/expired. An operator can refresh it with `npx rayfin login --select`; agents rerun the one-shot orchestrator, which owns login recovery and state reuse. |
| **Consent popup on Step 2 (Seed & provision)** | Should **not** appear anymore — `setup-live-auth` now pre‑grants all Fabric REST scopes (`GraphQLApi.Execute.All`, `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All`) AllPrincipals (tenant‑wide) on the Power BI Service resource. If you still see it (edge‑cached config), click **Accept** once; it's harmless. |
| **Connect telemetry → "No Eventhouse found in the workspace"** (STID/GraphQL works) | **Root cause (proven): an OAuth scope gap, not RBAC.** Discovery reads the Eventhouse's `queryServiceUri` via `GET /v1/workspaces/{ws}/eventhouses/{id}`, which needs **`Item.Read.All`** (or `Eventhouse.Read.All`). Without it the call returns **403 InsufficientScopes** and the app reports "No Eventhouse found" — even for a workspace admin (admin RBAC ≠ token scope). `List Items` (used to find STID's GraphQL) only needs `Workspace.Read.All`, which is why STID works but telemetry doesn't. **Fix:** the app now requests `Item.Read.All` (`src/services/fabric.ts` `FABRIC_SCOPES`) and `setup-live-auth` pre‑grants it. Agents rerun the one-shot orchestrator, then hard-refresh (Ctrl+F5). If telemetry connects but shows no data, ensure the signed-in user has **KQL Database Viewer** on the Eventhouse and the app origin is in the Eventhouse **CORS** allow-list. |
| Sign‑in fails with **AADSTS** | Ensure your deployed hosting URL is in `rayfin/rayfin.yml` (`allowedRedirectUris`), then run `npm run setup-live-auth` (after `az login`). 50011 = redirect URI; 650057 = missing permission; 65001 = no consent. Hard‑refresh (Ctrl+F5) after. |
| Local **Deploy app** says the identity is missing from the MSAL token cache | The deployer opens `az login --tenant <selected-tenant>` and retries the Fabric token once automatically. Complete the browser sign-in; it does not switch the configured tenant or rotate Rayfin state. |
| Verification says scopes are configured but consent is missing | On **App registrations → Hydro Operations Fabric Client → API permissions**, inspect **Status**, not just the permission rows. It must say **Granted for &lt;tenant&gt;**. Use **Grant admin consent for &lt;tenant&gt;** with a consent-granting admin role, or allow the intended user to consent in-app if tenant policy permits. |
| KQL reachable but "no live readings" | F12 → Console: `HTTP 401` = re‑connect for the cluster scope; `HTTP 403` = grant **KQL Database Viewer**; network error with no status = **CORS** not allowing the app origin. |
| Operational writes fail with **Internal server error** | An unbounded `@text()` column maps to `NVARCHAR(MAX)`, which some ops reject. Bound it in `rayfin/data/schema.ts` and re‑run `npm run rayfin:db`. |
| Deployed into the wrong workspace | `npm run up` reads `FABRIC_WORKSPACE_NAME` from `rayfin/.env` — fix it and re‑run. |
| **`rayfin up` → 404 "The provided workspace was not found"** | Stale `active` pointer in `rayfin/.deployments.json` from a previous tenant — delete it (and `.env.local`), then re‑run. See [Redeploying to a different tenant, workspace, or region](#redeploying-to-a-different-tenant-workspace-or-region). |
| **`rayfin up` → 403 "The feature is not available"** | **Fabric App Items (preview)** not enabled/propagated, or the capacity's region doesn't support it. Enable tenant setting `AppBackendTenant`, wait ~15 min, else move the workspace to a supported‑region capacity (e.g. **Sweden Central**). See [Feature & region gating](#feature--region-gating-fabric-app-items-preview). |
| **`npx … -c "npx rayfin …"` → npm EUSAGE** | Don't nest `npx`. Call `rayfin` / `npm run …` **directly** inside the `-c` string. |
| `rayfin up` can't find `rayfin.yml` (wrong cwd) | The `-c` shell starts at an unspecified cwd — put the path in the string: `-c "cd /d <abs>\HydroOperationsApp && rayfin up …"`. |

## Rotate deployment state

Never delete deployment state. When switching targets, move only `rayfin/.env`,
`rayfin/.env.local`, and `rayfin/.deployments.json` into a uniquely named temporary backup. The
one-shot orchestrator performs this rotation, preserves every existing Entra SPA redirect, and adds
only the current generated hosting origin.

> Switching **tenant/workspace/region** (not just rebuilding)? Follow
> [Redeploying to a different tenant, workspace, or region](#redeploying-to-a-different-tenant-workspace-or-region)
> — it covers resetting the `active` deployment pointer, re‑registering the SPA in the new tenant, and
> the non‑interactive `rayfin up --workspace-id <guid> --yes` command.
