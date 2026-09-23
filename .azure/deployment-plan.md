# Feature workspace infrastructure deployment

> **Status:** Validated

Updated: 2026-09-23

## 1. Goal and scope

The existing Hydro Operations baseline is deployed. The current approved change
adds the separate Map tab and five energy-source integrations to FEATURE, using
MapLibre with Fabric-backed data and clearly labeled rule-based UMM importance.
Use the canonical `Raw/workspace-reset/deploy_fabric_app.py` entry point. Do not
add a Fabric Git connection or alter STABLE.

The user selected MapLibre and rule-based UMM ranking on 2026-09-23. The deployment
adds one GeoContext Lakehouse (plus its managed SQL endpoint), one ingestion
notebook and one pipeline, with two Delta external-table read models in the
existing Eventhouse. It reuses the existing F2, tenant SPA, permissions and private
vault. No capacity/SKU, Azure network, secret or external hosting changes are
needed. The initial release uses manual imports with static-source caching;
recurring source schedules remain disabled.

Map validation must include source normalization/completeness tests, typed
viewport queries, worker build, UI navigation in both shells, and live Fabric
row/status counts before reporting that the integration is deployed.

## 2. Previously selected Azure context

- Tenant: `cfbb67d9-96a6-4e29-a919-1fc7cfb80776`.
- Subscription: `ME-MngEnvMCAP679828-espenb-1`
  (`d4d67f11-3766-4c12-8966-599f08e759da`).
- Region: Norway East.
- Capacity: `powergrid`, F2, `0a534351-6e2d-48d7-a257-399318eb90fb`.
- Workspace: `ws-FabricIQAppsDemoFEATURE`,
  `313d99ca-4280-4772-87cb-2c928164c428`.
- Resource group: `rg-hydro-feature-313d99ca`.
- Dedicated vault: `kv-hydro-feat-313d99ca`.
- Classification: development/demo; minimize additional persistent infrastructure.

The user reconfirmed this subscription, region, capacity and workspace on 2026-09-23.
Browser sign-in completed as `espenb@MngEnvMCAP679828.onmicrosoft.com`.
Initial preflight confirmed the workspace was empty and Git-disconnected, the vault was
private-only/RBAC-protected, and Microsoft.Network is registered. The user
approved resuming the existing F2. A subsequent ARM read returned `Active`, so
no resume action was necessary or performed by this session.

## 3. Repository sources

- `AGENTS.md` and `.github/copilot-instructions.md`.
- `HydroOperationsApp/DEPLOY.md`.
- `.github/prompts/deploy-fresh-tenant.prompt.md`.
- `Raw/workspace-reset/README.md`.
- `Raw/workspace-reset/key_vault_preflight.py`.
- `Raw/workspace-reset/feature_prerequisites.py`.
- `Raw/workspace-reset/feature_workspace.py`.

## 4. Recipe

AZCLI/REST through the existing Python deployment orchestrator. No alternative
Rayfin commands, new application hosting stack, or Fabric Git connection.

## 5. Architecture and decisions

Reuse the created workspace, capacity, vault, resource group and tenant SPA.
Respect the organization policy that disables public Key Vault access.

Repository inspection confirms that `key_vault_preflight.py` already creates,
approves and waits for a Fabric managed private endpoint targeting the vault.
The proposed extension is to initialize the three notebook credentials through
an authenticated ARM deployment with secure parameters, then use that existing
private endpoint for notebook runtime reads. No VM, public IP, custom VNet or
firewall exception is proposed.

Microsoft documents that deployment of secrets through ARM templates is a control
plane operation, distinct from runtime secret access and not affected by Key Vault
firewall rules. ARM cannot retrieve the secret value. This is supported
infrastructure provisioning, not a public data-plane access workaround.

- https://learn.microsoft.com/azure/key-vault/general/overview-vnet-service-endpoints
- https://learn.microsoft.com/azure/templates/microsoft.keyvault/vaults/secrets
- https://learn.microsoft.com/fabric/security/security-managed-private-endpoints-create

Credential values must remain in memory/secure ARM parameters only: no values in
source, config files, logs, command-line arguments, notebook definitions or outputs.
Metadata-based retries must not rotate existing credentials silently. Verify
actual credential usability from a Fabric notebook over the private endpoint.
Do not disable, exempt or bypass policy.

The app deploy, Entra redirects/consent, SQL schema, notebook setup, seed,
GraphQL and telemetry checks remain under the canonical orchestrator.
Scheduled ingestion and outbound alerts remain disabled for the feature environment.

### Final implementation sequence

1. Verify the existing `powergrid` capacity is active; do not resize it.
2. Reuse the existing vault with public access disabled and its RBAC protection.
3. Create the previously approved notebook-only identity and its dedicated API
   group; preserve all existing tenant policy and scope grants to FEATURE/vault.
4. Provision `tenantid`, `clientid` and `clientsecret` using an ARM template with
   secure parameters. ARM returns only metadata, not secret values.
5. Call the existing private-endpoint preflight from feature bootstrap, approve
   only the connection matching the FEATURE request, and wait for readiness.
6. Import the checkout's items, run setup, deploy the app, seed SQL/GraphQL and
   start a finite synthetic stream through the existing orchestrator.
7. Verify actual secret reads from Fabric, table/telemetry access, auth readiness,
   disabled schedules and target-only references. Stop on any policy/role gate.

## 6. Resource inventory and limits

Evidence collected 2026-09-23:

| Resource | Existing / additional | Limit/capacity evidence |
| --- | --- | --- |
| Fabric capacity | Reuse one F2; zero new capacities or SKU changes | Initially paused; subsequent ARM GET returned Active. Still assigned to FEATURE. No capacity mutation performed. |
| Key Vault | One in Norway East; zero new vaults | `az quota list` for Microsoft.KeyVault returned BadRequest (unsupported). Resource Graph counts one Norway East vault and three Sweden Central vaults. Reuse the owned Norway East vault. |
| Secret records | Three additions to the dedicated vault | Microsoft Key Vault service-limits documentation specifies no count limit; three writes are below the documented 300 create transactions per 10 seconds. |
| Managed private endpoint | Zero in FEATURE; add one | Fabric REST returned an empty endpoint list; vault has no private endpoint connections. Managed endpoint support is documented for Fabric capacities. The reviewed documentation exposes no adjustable workspace endpoint quota; service admission is checked during creation. |
| VMs, user VNets, public IPs, new managed identities | Zero | Not part of this architecture; no Compute/Network capacity increase is required in the user subscription. |
| Notebook app/SP and dedicated API group | Zero matching identities; add one each | Graph lookup confirms no matching names. Existing tenant SPA is reused. Graph directory/service limits remain enforced by the creation APIs. |
| Vault RBAC / workspace role | Two vault-scoped grants and one FEATURE Contributor grant | Caller has inherited Owner. Grant checks and exact-scope verification are already implemented. No subscription-wide role is granted to the notebook. |

Commands: `az quota list --scope .../Microsoft.KeyVault/locations/norwayeast`,
scoped `az graph query`, Fabric workspace/items/private-endpoint/capacity GETs,
`az keyvault show`, `az provider show --namespace Microsoft.Network`, and Graph
app/group lookups. Quotas are not claimed to be unlimited when an API is unsupported.

Service references:
- https://learn.microsoft.com/azure/key-vault/general/service-limits
- https://learn.microsoft.com/fabric/security/security-managed-private-endpoints-overview
- https://learn.microsoft.com/rest/api/microsoftfabric/fabric-capacities/resume

## 7. Validation proof

### Map extension (2026-09-23)

- [x] All pre-deployment validation checks pass.
  - [x] Azure CLI installation and authentication: CLI 2.84.0; tenant and approved subscription unchanged.
  - [x] Fabric target: FEATURE on the active Norway East F2; Git `NotConnected`.
  - [x] App typecheck, targeted ESLint, nine map-contract tests and Node 24 production build.
  - [x] Python integration regressions: 115 tests passed in the existing provisioning virtual environment.
  - [x] Browser checks: V1/V2 Map navigation, WebGL canvas, layer toggles and tab unmount; no uncaught page errors.
  - [x] Static identity review: no new Azure resources, RBAC assignments, secrets, tenant settings or permissions in this extension.
  - [x] Policy preservation: vault still RBAC-enabled with public network access `Disabled`.
  - [x] Baseline source digest matches all three existing setup/seed/stream checkpoints.

| Check | Actual command / evidence | Result |
| --- | --- | --- |
| Backend integration | Provisioning venv `python -m unittest test_energy_ingestion test_feature_workspace test_deploy_fabric_app test_agent_deployment_contract -q` | 115 passed; includes 51 ingestion tests, source completeness, last-good preservation, pipeline parameters/concurrency, baseline digest reuse and read-model location/status checks |
| Frontend | Node 24 `npm run typecheck`, `node --import tsx --test scripts/energy-map-model.test.ts`, targeted ESLint, `npm run validate-env && npm run build` | Passed; locally bundled lazy MapLibre worker, nine tests; existing large-chunk warning remains |
| Browser | Session-only Playwright `check-map.mjs` against the production preview | Both UI shells passed; browser rendering is verified locally, not yet on the deployed map |
| Azure / Fabric | `az account show`, `az version`, read-only workspace, capacity and Git-connection API calls | Exact approved target; active F2; no Git connection |
| Private vault | `az keyvault show` projected policy fields | `publicNetworkAccess=Disabled`, `enableRbacAuthorization=true` |
| Checkpoint compatibility | Instantiate map-enabled config and compare its baseline digest with persisted job keys | Digest `890f0fc086cbf71989507f10945f5129794c3c6ab472cba6cc27101ac65d4289`; three existing baseline jobs match |
| Patch / upstream | `git diff --check`, `git fetch origin` and `git rev-list --left-right --count HEAD...origin/main` | No whitespace errors; no new upstream main commits to merge |

This extension has no Bicep/ARM resource delta, container or Azure policy change,
so Bicep compilation, ARM template validation/what-if and Docker build are not
applicable. The existing Python/REST deployment recipe is preserved; its baseline
ARM proof remains below. Public source requests are anonymous. The app uses the
existing delegated Fabric/KQL permissions and requires the signed-in user's
read access to the GeoContext Lakehouse as well as the Eventhouse.

Initial live source import, external-table readback and the hosted Map tab remain
post-deployment gates. UMM explicitly covers the last 30 publication days, not
every older active notice; cancelled/undatable/unlocated records remain in its
unplotted list. No new recurring source schedule is enabled.

### Completed baseline execution checklist

- [x] Read the deployment runbooks and existing private connectivity helper.
- [x] Verify live target, policy, identity and resource inventory.
- [x] Select the supported private-vault bootstrap path and confirm required scope.
- [x] Complete resource and quota checks, recording unsupported quota APIs explicitly.
- [x] Obtain approval for the completed infrastructure plan, including resuming the existing F2 capacity (2026-09-23).
- [x] Implement only required extensions to the canonical workflow.
- [x] Mark Ready for Validation; azure-validate is loaded for the checks below.
- [x] Execute the canonical deployment only after validation.
- [x] Verify target-only data bindings, hosted app/auth and baseline data.

## 8. Baseline validation checklist and proof

- [x] Python bootstrap/private-endpoint regression suite.
- [x] Azure CLI identity, workspace/capacity, provider and policy checks.
- [x] Generated secure ARM template validation and what-if with dummy values only.
- [x] Node 24 environment validation and production app build.
- [x] Static role review: only vault-scoped secrets roles, target workspace Contributor,
  and the dedicated group in the public-Fabric-API allow list.

Recipe adaptation: this is an existing Python/REST orchestrator, not an AZD,
Bicep or container project. Use the generated JSON template for ARM validation
instead of scaffolding unrelated Bicep/AZD/Docker infrastructure.

Validated under the azure-validate workflow on 2026-09-23.

| Check | Command / evidence | Result | UTC time |
| --- | --- | --- | --- |
| Python regressions | `python -m unittest test_feature_prerequisites test_feature_workspace test_key_vault_preflight test_deploy_fabric_app test_weather_source_contracts test_sync_workspace_from_git test_agent_deployment_contract` from `Raw/workspace-reset` | 137 passed | 2026-09-23T07:58:30Z |
| App build | `npx --offline -p node@24 -c 'npm run validate-env && npm run build'` from `HydroOperationsApp` | Passed; existing bundle-size warning only | 2026-09-23T07:58:40Z |
| ARM validation | `az deployment group validate` with the generated validation-only secure-parameter template | Succeeded; no credentials created | 2026-09-23T07:58:50Z |
| ARM what-if | `az deployment group what-if --result-format ResourceIdOnly` using the same template | Succeeded; three secret creations only, existing vault ignored/unchanged | 2026-09-23T07:59:00Z |
| CLI / patch | `az version`, `git diff --check` | Azure CLI 2.84.0; no whitespace errors | 2026-09-23T07:58:29Z |
| Role review | Reviewed `role_assignment_spec`, `_ensure_workspace_role`, `merge_public_api_policy` and secure template contents | Vault-only roles, FEATURE Contributor, append-only dedicated API group; no notebook Graph/admin grants | 2026-09-23T07:59:00Z |

No Azure policy is removed or bypassed. Secret values are absent from templates,
outputs, source/config files and logs; actual values exist only in secure deployment
parameters at execution time. Runtime credential usability was confirmed by the
successful setup and strict seed notebook runs.

## 9. Completed baseline checkpoint

Private-only vault credentials, scoped roles, the dedicated API group and the
approved Fabric managed private endpoint are provisioned. All 23 repository
definition items are imported without Git. The foundation notebook completed,
proving private secret retrieval and notebook authentication.

The complete setup pipeline succeeded on retry. Eventstream startup readiness and
native notebook definition handling were corrected and persisted on the feature
branch. The Weather schedule remains disabled and the Operations Agent remains
stopped; external weather keys and mailbox connections were not fabricated.

`hydro-operations-ui` now exists in FEATURE:

- AppBackend: `d87864ad-ecaf-4970-8ce7-84f9b4bbcf9a`.
- Hosted app: https://solar-dew-0ad1824bab-norwayeast.webapp.fabricapps.net
- Fabric item: https://app.fabric.microsoft.com/groups/313d99ca-4280-4772-87cb-2c928164c428/appbackends/d87864ad-ecaf-4970-8ce7-84f9b4bbcf9a?ctid=cfbb67d9-96a6-4e29-a919-1fc7cfb80776

**Deployment completed on 2026-09-23.** After the administrator granted consent,
the canonical orchestrator verified the SPA redirect, required delegated scopes
and tenant-wide consent. Strict `RTI_011` completed operational SQL seeding,
GraphQL binding and operational Data Agent publication. The finite telemetry
pipeline completed and the orchestrator printed both `DEPLOYED_APP_URL` and
`SUCCESS`, exiting with code 0.

Final persisted evidence:

| Component | Result |
| --- | --- |
| Operational SQL database | `hydro-operations-ui`, `df1bbd16-d8f6-4849-9d7c-54a519c8b949`; strict seed succeeded |
| GraphQL | `Hydro_STID_API`, `889efa17-23d5-4341-b9e7-706f3c1389b7`; returned 3 facilities, 15 equipment rows and 90 instruments |
| Telemetry | 9,000 Eventhouse events; latest event `2026-09-23T10:45:11.975092Z` at final verification |
| Hosted page | HTTP 200 |
| Browser telemetry access | CORS preflight permits the exact deployed origin, POST and authorization/content-type headers |
| Workspace | 40 items, Git state `NotConnected` |
| Source STABLE | Not modified |
| Deployment configuration | Generated origin committed and pushed as `b348210` |

The private endpoint and vault-scoped credentials remain in place; Key Vault
public network access remains disabled. Weather scheduling and outbound agent
alerts remain disabled as planned. Live weather provider credentials, the optional
Foundry model endpoint and mailbox setup are separate integrations, not fabricated
as part of the fresh-demo baseline.

This completed the existing-solution feature workspace. That baseline deployment
did not include the energy-map tab or external energy-source ingestion. The map
extension is now implemented and validated as described in Section 7, but is not
yet deployed.
