# Feature workspace infrastructure deployment

> **Status:** Validated

Updated: 2026-09-23

## 1. Goal and scope

Complete the existing Hydro Operations baseline in the isolated feature workspace
with fresh demo data. Use the repository deployment quickstart and the canonical
`Raw/workspace-reset/deploy_fabric_app.py` entry point. Do not add a Fabric Git
connection, alter STABLE, or implement the future energy-map feature in this step.

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
Live checks confirm the workspace is empty and Git-disconnected, the vault is
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

## 7. Execution checklist

- [x] Read the deployment runbooks and existing private connectivity helper.
- [x] Verify live target, policy, identity and resource inventory.
- [x] Select the supported private-vault bootstrap path and confirm required scope.
- [x] Complete resource and quota checks, recording unsupported quota APIs explicitly.
- [x] Obtain approval for the completed infrastructure plan, including resuming the existing F2 capacity (2026-09-23).
- [x] Implement only required extensions to the canonical workflow.
- [x] Mark Ready for Validation; azure-validate is loaded for the checks below.
- [ ] Execute the canonical deployment only after validation.
- [ ] Verify target-only data bindings, hosted app/auth and baseline data.

## 8. Validation checklist and proof

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
parameters at execution time. Runtime credential usability remains a deployment check.

## 9. Current checkpoint

Private-only vault credentials, scoped roles, the dedicated API group and the
approved Fabric managed private endpoint are provisioned. All 23 repository
definition items are imported without Git. The foundation notebook completed,
proving private secret retrieval and notebook authentication.

The first Stage 2 run failed when `RTI_002` queried the custom Eventstream endpoint
before it became ready. A later read returned the documented connection shape
with HTTP 200. The notebook now waits for definition LRO completion and retries
connection readiness; an unchanged definition is reused. Thirty-one targeted
readiness, bootstrap and weather-contract checks passed before retrying.

The baseline deployment is not yet complete; no application URL has been published.
