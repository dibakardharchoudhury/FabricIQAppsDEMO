# Feature workspace infrastructure deployment

> **Status:** Deployed

Updated: 2026-09-25

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

### Dedicated map chat (2026-09-25T06:27Z)

- 48 frontend/component/model tests, targeted ESLint, TypeScript and Node 24
  production build passed.
- 177 Python regressions passed, including 33 dedicated-agent notebook tests,
  ownership refusal, source binding and native/ipynb mirror compilation.
- Saved baseline/source job digests match the existing successful checkpoints:
  three baseline jobs and the completed Geo001 import are reused. Adding chat
  does not restart national source downloads or the demo stream.
- Live place lookup found the Adamselv plant and substation as separate exact
  matches. Type-scoped plant lookup resolves `nve-hydro:Powerplant:2` with verified
  coordinates; NO2 resolves an actual area polygon. Escaped lookup text returned
  no unintended records.
- Both UI shells passed isolated chat interaction checks: actual camera movement,
  analytical-prompt action rejection, ambiguous-place choices, missing geometry,
  filter-conflict notices, request cancellation/failure, and responsive drawer.
  Six existing map layout cases also passed.
- Existing Data Agent MCP answered a live bounded facilities question. The
  dedicated agent will not be considered ready until its published-definition
  verification and actual grounding canary pass.
- Same tenant/subscription, F4 Active; no overlapping map ingestion jobs.
  Azure resources, credentials, role assignments and Git isolation are unchanged.
- Initial cloud execution created the owned draft agent but stopped at definition
  polling: Fabric returned a backend `Location`, which the credential guard
  correctly refused. The notebook now resolves `x-ms-operation-id` to the
  canonical Fabric operation endpoint, matching repository prior art.
- Revalidation on 2026-09-25: 64 agent/bootstrap tests passed, including backend
  location rejection without an operation ID, canonical result polling and
  malformed-ID refusal. A real read-only definition request successfully returned
  the draft through the canonical `/v1/operations/{id}/result` endpoint.

### Country-balance tile (2026-09-24T14:21Z)

- 38 focused regression/component tests, TypeScript, targeted ESLint and Node 24
  production build passed. Signs, missing values, snapshot age and map-marker
  exclusion are covered.
- Both-shell fixture checks confirmed the Norway metrics stay unchanged when
  map properties/areas change. All six responsive cases confirmed three separate
  tiles and nine remaining map-layer controls.
- Live Fabric readback confirmed the field contract and metric-specific source
  timestamps. Same approved tenant/subscription; no infrastructure or role delta.

### Visible-only live frequency (2026-09-24T12:56Z)

- 35 targeted tests, TypeScript, ESLint and Node 24 production build passed.
- The exact production query/parser returned advancing live samples through
  Fabric: 49.996 Hz (1.151s old) then 49.991 Hz (0.274s old).
- Browser lifecycle checks passed for visible polling, hidden/offscreen pause,
  last-good retention, bounded failure backoff, manual retry, stopping on unmount
  and stale-source labeling. No uncaught browser errors.
- Both-shell property/area interactions and all six responsive map/tile layout
  cases passed. Live age updates are isolated to the frequency component.
- Same approved tenant/subscription; no schema, role or infrastructure changes.
  The existing Azure/permission validation applies; deploy app-only.

### Capacity tile and multi-area iteration (2026-09-24T12:00Z)

- 31 targeted tests, ESLint, TypeScript and Node 24 production build passed.
- Live polygons passed KQL validity checks. Six spatial selections, including
  overlapping/adjacent regions and no selection, retained all 390 context lines.
  The combined NO1/NO2 asset IDs exactly equal their individual set union.
- Client totals matched independent KQL sums of non-negative source values:
  NO1 2,772.7935 MW; NO2 11,651.138 MW; NO1+NO2 14,423.9315 MW.
  Unrestricted default viewport: 32,547.3546 MW, with 33 missing/invalid values
  excluded and explicitly flagged.
- Both-shell fixture interactions verified 850 -> 50 -> 250 -> 850 MW changes
  for all/NO1/NO1+NO2/Norway, no double-counting, and nine distinct colors.
- All six production-preview layout cases passed: capacity sits alongside
  frequency on desktop/tablet and stacks on mobile; existing map controls work.
- Same tenant/subscription confirmed. No infrastructure, role, schema or
  data-write changes; deploy only the static app through the canonical entry point.

### Areas, asset links and embedded rendering (2026-09-24T10:58Z)

- 141 Python regressions passed, including 75 ingestion/auxiliary tests:
  polygon/ring coverage, explicit foreign no-data values, conservative linking,
  revision handling, protected manual corrections and last-good publication.
- 25 frontend model tests, targeted ESLint, TypeScript and Node 24 production
  build passed. Marker sizing remains increasing through the global maximum;
  frequency/UMM are not map markers; message queries bind asset and revision.
- Real-data embedded replay used 2,549 exported features plus all nine validated
  polygons (101,487 coordinates). Owner/price-area changes did not re-index
  unchanged transmission geometry, one renderer remained alive, framebuffer
  dimensions stayed bounded, and forced context loss recovered without errors.
- Fixture interactions verified frequency tile, selected-asset-only notices,
  collapsed raw properties, property controls and reset behavior in both shells.
  Six production-preview layout cases passed across desktop/tablet/mobile.
- The same tenant/subscription/workspace remains selected. The existing capacity
  now reports **F4, Active**, changed outside this session; no resize was performed.
  Pipeline history has no active/recent duplicate import to reuse.
- No Azure infrastructure, role or policy delta. The existing feature bootstrap
  updates only the energy notebook/read models; baseline setup/stream digests
  remain isolated from the energy definition.

### Property filter iteration (2026-09-24T08:31:47Z)

- Node 24 environment validation, twenty map-model/filter tests, targeted ESLint,
  TypeScript and the production build passed. Tests cover query ordering before
  the feature cap, safe string literals, numeric bounds, missing values, reset
  isolation and keeping other layer types unchanged.
- Live KQL at 08:14:53Z validated the full-snapshot facet query and five filtering
  cases against complete baseline feature-ID sets. Plant-only, transformer-only,
  combined, missing-owner and escaped-owner cases all matched the client predicate
  exactly and preserved all 390 transmission context lines.
- Live maxima are 1,240 MW / 1,163 m / 420 kV; owner dropdowns contain 806 plant
  and 89 transformer values. No source data was written during these checks.
- Both UI shells passed isolated synthetic-fixture interaction tests for
  multi-selection/search, operation/status, all three range controls, scoped
  results, resets and retained filters while minimized. Dropdowns fit desktop,
  tablet and mobile widths and close with Escape or an outside click.
- All six production-preview layout cases passed: panel minimization releases
  space, selection survives hiding, saved visibility survives reload, and the
  existing per-layer disclosures and map canvas remain functional.
- Existing tenant/workspace/KQL endpoint resolved through Fabric REST. The
  infrastructure/permission delta is empty; use the documented app-only path.

### Compact filter details iteration (2026-09-23T16:20:46Z)

- Node 24 environment validation, targeted page ESLint and production build passed.
- All six V1/V2 desktop/tablet/mobile browser cases passed. Every filter starts
  closed with no visible supporting text; keyboard expansion reveals it, changing
  a layer preserves disclosure state, and closing hides it again.
- Actual card/panel bounds confirm collapsed details consume less vertical space.
  The desktop panel is 194px high when closed; map placement and responsive
  canvas sizing remain correct.
- Azure CLI confirms the previously approved tenant/subscription. This change
  adds no roles, permissions, data jobs or infrastructure; app-only deployment
  preserves the existing source snapshots and bindings.

### Filter placement iteration (2026-09-23T15:04Z)

- Node 24 `npm run validate-env && npm run build -- --logLevel error` passed.
- Browser checks passed in both V1 and V2 at desktop (1600px), tablet (1024px)
  and mobile (390px) widths. Actual element bounds confirm the filter panel is
  above the map and spans the layout, all twelve controls remain available, and
  toggling/unmounting still works. The canvas fills its wrapper at each size.
- The mobile canvas minimum-height selector was aligned with its MapLibre
  specificity so it no longer overflows the smaller mobile wrapper.
- Azure CLI confirms the same approved tenant/subscription and active F2.
- Static role/infrastructure review: CSS and documentation only; no role,
  source, schema or permission changes. Existing infrastructure validation applies.

### Map extension (2026-09-23)

- [x] All pre-deployment validation checks pass.
  - [x] Azure CLI installation and authentication: CLI 2.84.0; tenant and approved subscription unchanged.
  - [x] Fabric target: FEATURE on the active Norway East F2; Git `NotConnected`.
  - [x] App typecheck, targeted ESLint, nine map-contract tests and Node 24 production build.
  - [x] Python integration regressions: 117 tests passed in the existing provisioning virtual environment.
  - [x] Browser checks: V1/V2 Map navigation, WebGL canvas, layer toggles and tab unmount; no uncaught page errors.
  - [x] Static identity review: no new Azure resources, RBAC assignments, secrets, tenant settings or permissions in this extension.
  - [x] Policy preservation: vault still RBAC-enabled with public network access `Disabled`.
  - [x] Baseline source digest matches all three existing setup/seed/stream checkpoints.

| Check | Actual command / evidence | Result |
| --- | --- | --- |
| Backend integration | Provisioning venv `python -m unittest test_energy_ingestion test_feature_workspace test_deploy_fabric_app test_agent_deployment_contract -q` | 117 passed at 2026-09-23T12:43Z; includes 51 ingestion tests, source completeness, last-good preservation, pipeline parameters/concurrency, baseline digest reuse, read-model checks, actionable service errors and bounded TLS connection retries |
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

Initial live source import, external-table readback and hosted Map-tab checks
completed successfully; see Section 10. UMM explicitly covers the last 30 publication days, not
every older active notice; cancelled/undatable/unlocated records remain in its
unplotted list. No new recurring source schedule is enabled.

The first deployment imported the two new definitions and reused the completed
baseline setup, then stopped on a Lakehouse-creation HTTP 400 before data import
or app publication. The REST reference permits only `enableSchemas=true`; a
schema-disabled Lakehouse must omit `creationPayload`, not pass `false`.
The canonical helper and notebook instructions have been corrected accordingly;
Fabric failures now include the service error code/message. No existing data or
deployment state was removed.
The corrected notebook mirror, 116-test integration suite and production build
were revalidated at 2026-09-23T12:14Z before retrying the same orchestrator.

The retry created `Hydro_GeoContext_V6`
(`7fca0386-d447-4dd2-8d9f-460396fbea7a`), bound the notebook and completed the
initial energy import (`abaad838-9981-4ee9-8ac5-8e10a345c613`). Read-model
publication then encountered an intermittent TLS EOF at the Eventhouse endpoint.
Read-only diagnostic queries confirmed both query and management HTTP 200 with
a pooled, certificate-verifying connection and bounded connection retries.
The canonical helper now uses that transport, does not retry authentication
failures or partial reads, and preserves the completed import checkpoint. Its
117-test regression suite passed before resumption; no source reimport is needed.

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
extension was subsequently deployed as described below.

## 10. Completed energy-map deployment

The canonical orchestrator completed with exit code 0, `DEPLOYED_APP_URL` and
`SUCCESS` on 2026-09-23. Static deployment ID:
`deploy-20260923125034-76c26914`. App build: `v1.0.616`.

- App: https://solar-dew-0ad1824bab-norwayeast.webapp.fabricapps.net/?ui=v2&tab=map
- GeoContext Lakehouse: `7fca0386-d447-4dd2-8d9f-460396fbea7a`.
- Ingestion notebook: `ba7f5882-5a91-45c3-a6cc-472987d479e7`.
- Pipeline: `5ea94da5-9267-4d7f-8344-9b7b42915743`.
- Completed initial import: `abaad838-9981-4ee9-8ac5-8e10a345c613`.
- Eventhouse aliases: `HydroGeoFeatures` and `HydroGeoStatus`.
- Deployed code: `0867796`, following map implementation `f393c21`.

Live KQL validation at 2026-09-23T12:56:34Z reconciled every source-status count
with the actual feature table. All twelve layers are `ready`.

| Layer | Imported records | Without verified coordinates |
| --- | ---: | ---: |
| Transmission lines | 390 | 0 |
| Regional lines | 3,706 | 0 |
| Distribution lines | 141,401 | 0 |
| Subsea cables | 8,747 | 0 |
| Masts and poles | 672,899 | 0 |
| Transformer substations | 1,549 | 0 |
| Hydropower plants | 2,010 | 171 |
| Reservoir area statistics | 9 | 3 |
| Country power balance | 1 | 0 |
| Power exchange | 15 | 1 |
| Grid frequency | 1 | 0 |
| Nord Pool UMM | 517 | 74 |
| **Total** | **831,245** | **249** |

Unlocated records are retained rather than given invented coordinates. UMM has
300 map-eligible notices and 217 unplotted notices in the imported publication
window; all use `rules-v1`, including explicit `unranked` cases.

The exact frontend query builder returned 2,549 features for its default
viewport and 269 masts for a dense Bergen viewport. The frontend runtime parsers
and GeoJSON conversion accepted those real results. The unplotted-message query
retained cancellations/unknown locations and did not turn them into active map
events. The 4,001-row sentinel / 4,000-feature browser cap remained enforced.

Hosted browser checks passed in both V1 and V2: the separate Map tab, WebGL
canvas, layer toggles, full-height map and tab unmount all worked with no uncaught
page errors. These isolated browser checks were unsigned; authenticated source
access was independently verified using the live KQL queries above. Users must
connect their own Fabric data session to display protected overlays.

Pipeline definition readback confirmed concurrency `1`, FEATURE-only notebook
and workspace bindings, `refresh_mode=all` and `force_refresh=false`. Its schedule
list is empty. FEATURE remains Git `NotConnected`; STABLE was not changed.
The baseline setup/seed/stream and successful map import were reused, not rerun:
STID still has 3 facilities, 15 equipment rows and 90 instruments; telemetry
still has 9,000 events.

Live role verification confirmed the notebook principal's Key Vault Secrets User
grant is scoped only to the dedicated vault and its Fabric role is FEATURE
Contributor. The deploying user is FEATURE Admin. Existing SPA redirects,
delegated permissions and consent passed the canonical post-deployment checks.

## 11. Map filter layout update

User-approved on 2026-09-23: move layer filters above the map in both app layouts.
This is a shared CSS layout change with no data, pipeline, authentication, role,
capacity or infrastructure changes. Keep source freshness/details, all twelve
checkboxes and the feature inspector. On small screens, the filter panel remains
scrollable above the map.

The production build and all six desktop/tablet/mobile browser cases passed.
The canonical orchestrator completed in its documented **app-only** mode
(`FABRIC_FEATURE_CONFIG` unset), with exit code 0, `DEPLOYED_APP_URL` and
`SUCCESS`. Deployment: `deploy-20260923150713-1eb24615`; code: `787e57d`.
Existing imports, pipeline definitions and baseline jobs were untouched.

All six hosted browser cases also passed: the full-width filter panel is above
the map, controls remain functional, and the canvas fits on desktop, tablet and
mobile in both layouts. No uncaught page errors were reported.

The routine blanket-consent regrant returned 403 because the current user no
longer held the required administrator role. Existing targeted grants were
already present, and all required live-auth/redirect checks passed; no additional
consent or administrator action was needed.

## 12. Compact map filter details

User-approved on 2026-09-23: show each filter's supporting text only when its
Details control is expanded. Keep checkboxes, names and accessible disclosure
controls visible; move provider/zoom guidance, freshness, counts and source
messages into the collapsed disclosure. The existing page-level status alerts
remain unchanged.

This shared UI-only change passed build, lint and responsive disclosure/placement
checks and was deployed with the canonical app-only workflow. Deployment
`deploy-20260923162346-ada8c15e` completed with exit code 0, `DEPLOYED_APP_URL`
and `SUCCESS`; deployed code is `4cb73a5`.

All six hosted browser cases also passed: supporting text starts hidden, keyboard
expansion reveals it, collapse hides it again, and the compact layout preserves
layer selection and map sizing. The existing consent grants and redirect/auth
contracts passed despite the already-documented blanket-consent warning.
Data, infrastructure and pipeline definitions were not changed.

## 13. Collapsible groups and property filters

User-approved on 2026-09-24: minimize/expand the complete Layers box and add an
independent Properties group for hydro/transformer attributes. The user explicitly
chose to preserve other selected layers as context.

This is an app-only change: query existing Delta external tables read-only,
without modifying schemas, imports, schedules, roles or infrastructure. Facets
come from all imported plant/transformer rows. Filters apply in KQL before the
feature cap; a matching client predicate also prevents stale responses from
showing features excluded by the currently selected properties.

Verified live fields: plant main owner (806 values), installed capacity maximum
1,240 MW, gross head maximum 1,163 m, operation boolean, price area and three
plant statuses. Transformer data has 89 owners, source-layer code 5, voltage
maximum 420 kV and network-level codes 0/1/2/3/5/6/7. Missing values are retained
in unrestricted views and handled explicitly by narrowed filters.

Panel visibility is remembered locally. Property controls include group resets,
a visible active-filter count, multi-select dropdowns and min/max sliders. No
source refresh is triggered by filtering or changing panel visibility.
The existing capacity-sized plant markers now read the same verified
`installed_capacity_mw` field used by filtering, rather than an absent legacy key.
The canonical app-only deployment completed with exit code 0,
`DEPLOYED_APP_URL` and `SUCCESS`. Deployed code: `d80aa51`; deployment ID:
`deploy-20260924083504-6db99ed9`.

All six hosted layout cases passed for V1/V2 desktop, tablet and mobile:
the Layers box disappears when minimized, restores without changing selections,
remains minimized after reload until explicitly expanded, and leaves the map
correctly sized. The full property-control interaction tests used isolated local
fixtures; actual facet values and predicate results were independently verified
against Fabric as recorded in Section 7.

Existing redirects, permissions and targeted consent grants passed the
post-deployment checks. The routine blanket-consent warning did not require any
administrator action. No infrastructure, pipeline or source data was changed.

## 14. Geographic areas, selected-asset messages and embedded rendering

User-approved on 2026-09-24:

- Move frequency to a snapshot tile above the map.
- Use Norway NO1-NO5 reservoir areas plus Norway/Sweden/Finland/Denmark country
  coverage; foreign reservoir figures must be explicitly unavailable.
- Scale hydro markers monotonically by installed capacity. The user chose
  uniform transformer markers with unknown capacity, not a voltage proxy.
- Collapse raw Source/GIS properties at the end of the selected feature.
- Persist a separate, evidence-backed market-message/asset link table and show
  only the selected asset's messages.
- Keep MapLibre and harden rendering inside Fabric. The supplied screenshot
  shows the whole embedded app renderer crashing, not a zero-result map.

The existing notebook adds two atomic, schema-validated Delta outputs:
`geo_reservoir_areas` and `geo_market_asset_links`; no new Lakehouse, capacity,
identities or schedules. The canonical feature bootstrap exposes both through
Delta external tables and preserves baseline setup/seed/stream checkpoints.
Country geometry is pinned public-domain Natural Earth 1:10m; price areas use
NVE Nettomraader/NLOD. Linking is conservative and reports unmatched coverage.

Renderer changes isolate/version layers, strip bulk raw metadata from viewport
responses, bound framebuffer allocation and defer offscreen updates. The exact
native Fabric process crash was not reproduced locally; a cross-origin embedded
replay with the real 2,549-feature snapshot and 101,487 polygon coordinates stayed
responsive through Owner/price-area filtering and recovered from deliberate
WebGL context loss. Unchanged transmission geometry was not re-indexed.

The canonical feature deployment completed with exit code 0,
`DEPLOYED_APP_URL` and `SUCCESS`. Code: `7c67d9f`; deployment ID:
`deploy-20260924111642-ead538b7`; energy job:
`278a1dbd-2948-41fd-8207-36a389f4397d`.

Live readback at 2026-09-24T11:18:52Z confirmed:

| Surface | Result |
| --- | --- |
| Imported facts | 831,249 records; 12 source statuses ready |
| Reservoir geometry | Nine Polygon/MultiPolygon features: NO1-NO5 and NO/SE/FI/DK; 3,129,257 geometry bytes |
| Foreign reservoir figures | Explicit no-data flags and null filling values |
| Asset-message links | 199 evidence-backed, revision-specific rows in `geo_market_asset_links` |
| Selected-asset query | Sample returned 10 notices, each matching the selected asset and exact linked revision; nonexistent asset returned none |
| Frequency | 50.009 Hz in the refreshed imported snapshot |
| Property filters | Five exact live predicate comparisons passed, preserving all 390 context transmission lines |
| Viewport memory | Response reduced from 8,597,370 to 3,771,752 bytes (56.1% smaller); area geometry is separate |
| Hosted UI | All six V1/V2 desktop/tablet/mobile layout, tile and panel cases passed |
| Baseline | Setup, seed and stream reused; 3 facilities, 15 equipment, 90 instruments and 9,000 telemetry rows retained |

No transformer capacity was inferred. Source/GIS details are fetched on asset
selection and collapsed at the end. Existing auth/redirect contracts passed
without a new administrator action. After refreshing the Fabric item, the user
confirmed that Owner/Price area filtering now leaves the map and embedded app
visible. The observed crash is resolved in that environment; its original
native-process failure was not reproduced locally.

## 15. Visible capacity and area selection

User-approved on 2026-09-24: add a capacity tile alongside frequency, assign each
reservoir area a distinct color, and allow single/multi-area selection. The user
explicitly chose to filter plants/transformers and the total inside selected
areas while retaining other layers as context.

Existing polygons are validated by KQL geospatial operations. The spatial
predicate uses their union before the feature cap, with an explicit error result
for missing/invalid selected geometry. No data, schemas, infrastructure or
permissions change. Use the canonical app-only deployment.

The tile sums unique plotted hydro plants with finite, non-negative installed
MW, scales MW/GW/TW, and discloses missing/invalid values and truncation. Real data
contains negative source capacities; these are excluded, not subtracted or
converted to invented positive values. The total waits for the matching query
result after viewport/filter changes. Country production and transformer
voltages/capacities are not mixed into it.
The canonical app-only deployment completed with exit code 0,
`DEPLOYED_APP_URL` and `SUCCESS`. Code: `24cf6a7`; deployment:
`deploy-20260924120453-02b04d3c`.

All six hosted V1/V2 desktop/tablet/mobile checks passed, including adjacent
frequency/capacity tiles and stacked mobile layout. Full area-selection/tile
interactions were tested with isolated fixtures, and real spatial results/totals
were verified independently against Fabric. Existing auth and redirect checks
passed; no source data, schedules or infrastructure were changed.

## 16. Live frequency while viewed

User-approved on 2026-09-24: make the frequency tile live only while the page is
open, with no always-on infrastructure or stored live history.

Statnett's public endpoint returned fresh per-second measurements but no browser
CORS header. A documented, mapped KQL `externaldata` query successfully retrieved
advancing live samples through the existing Eventhouse: 50.013 and 50.033 Hz five
seconds apart, both less than a second old. No new permissions or resources are
needed; the public HTTP request carries no browser/provider credentials.

An isolated tile polls about every five seconds while visible, stops on
hidden/offscreen/offline/unmount, uses 12-second request bounds and bounded error
backoff, and labels observation age and stale/failure states. It does not redraw
map layers each second or re-label the old Delta snapshot as live. Use the
canonical app-only deployment after validation.

The canonical app-only run completed with exit code 0, `DEPLOYED_APP_URL` and
`SUCCESS`: code `58ce033`, deployment `deploy-20260924125947-34d75e67`.
Post-deployment reads through the exact production query returned 49.976 Hz
(0.443s source age), then 49.971 Hz (0.585s source age) five seconds later.
All six hosted V1/V2 responsive map/tile checks passed. Existing consent and
redirect contracts remain valid; no administrator action or new infrastructure
was required.

## 17. Country power-balance tile

User-approved on 2026-09-24: remove country balance from the map and display it
as a separate tile. Retain the existing Norway-wide Fabric snapshot semantics;
only frequency is live. No source ingestion, schema, permission or infrastructure
change is needed.

Verified source fields: production 16,017 MW, consumption 14,213 MW, net exchange
-1,804 MW. Source signs remain unchanged. Observation times differ by metric,
some are absent, and the tile exposes that rather than inventing one common
measurement time. Missing metrics are unavailable, not zero.

Code `c812a3e` was deployed through the canonical app-only workflow:
`deploy-20260924142341-f68ffd68`, exit code 0, `DEPLOYED_APP_URL` and `SUCCESS`.
All six hosted V1/V2 responsive cases confirmed the three-tile layout and absence
of the country-balance map toggle. Existing source data, live-frequency behavior,
permissions and infrastructure were preserved.

## 18. Dedicated map chat and verified navigation

User-approved on 2026-09-24, resumed on 2026-09-25: add a right-side map chat
using a dedicated Fabric Data Agent, not a new Foundry resource. Requests to see
an asset resolve against Fabric before moving the map. The user authorized
clearing conflicting property/area filters with a visible explanation.

`Geo_002_publish_map_agent` creates owned typed map projections and publishes
`Hydro_Map_Agent_V6` using the existing GeoContext Lakehouse and user execution
identity. It does not replace Hydro Intelligence's `RTI_Demo_Agent_V6`.
`enable_map_chat` is an explicit opt-in; source and agent definition digests are
separate. The completed setup/seed/stream and Geo001 import checkpoints were
verified unchanged against persisted state. Future map refreshes run the agent
publisher after ingestion; current provisioning can run Geo002 alone.

Frontend scope includes a bounded map-only conversation, source/filter context,
strict terminal navigation markers, parameterized name lookup, exact identifier
and coordinate verification, candidate choices for ambiguous names, cancellation,
and keyboard/mobile drawer behavior. Analytical prompts do not move the map.
Fresh frequency is fetched on demand only for frequency questions.

The existing Fabric Data Agent answered a live readiness question. The dedicated
agent's publication and data-grounding canary remain live deployment gates.
No new Azure resource, secret, role, capacity or Fabric Git connection is required.

Initial Geo002 run `0817c5b5-572d-4da7-8408-3456d32b7f05` failed at LRO URL
handling before projection writes or app publication. The owned draft agent is
`edee345d-ed8d-4726-a22b-099e8f004cbc`; it will be reused, not replaced. The
corrected notebook retains strict credential-destination checks. Chat is not yet
declared deployed until the canonical retry and grounding checks succeed.

The corrected run was recorded as `977577aa-61c4-4b20-ae60-5b0dc349f6ba`.
Its local monitor lost a read connection while the cloud job remained active.
Feature API GETs now retry that same read up to twice; uncertain writes are never
retried. The durable job checkpoint is retained, and an unchanged failed map
publisher cannot be silently resubmitted. Agent/bootstrap regression coverage
includes these transport and failure-resume cases.

The publisher then completed and the app was deployed as
`deploy-20260925101127-1e991814`. SQL readback confirmed 831,258 typed entity
records, 199 links and ready run `5bc2e725-341d-4772-b2ce-f4b91449036a`.
Post-publication natural-language checks exposed a prompt/schema mismatch:
the SQL-grounded agent was given KQL view text, and its navigation example
omitted requested capacity columns. The client now provides concise typed SQL
schema hints instead of KQL, and the publisher's examples include capacity and
explicit full-dataset plant counts. An independent SQL query confirmed Adamselv
is 50 MW and the projection contains 2,010 plant rows. Final natural-language
verification is repeated after this correction, not inferred from deployment
success or the state-table canary alone.

Final deployment `deploy-20260925104827-450ac9db` completed through the canonical
workflow with exit code 0, `DEPLOYED_APP_URL` and `SUCCESS`. Code: `a8f9355`;
publisher job: `90183971-aff7-40a2-a1a2-06de29072fad`; dedicated agent:
`edee345d-ed8d-4726-a22b-099e8f004cbc`.

Post-deployment questions through the exact app prompt and published MCP endpoint
returned the verified complete count of 2,010 hydropower plants, and Adamselv's
owner `STATKRAFT ENERGI AS`, capacity 50 MW, price area NO4 and correct navigation
reference `nve-hydro:Powerplant:2`. The agent distinguished stored frequency from
live samples and correctly reported transformer MW/MVA capacity as unknown.
The earlier schema/context failure is resolved for these acceptance cases.

Both hosted UI shells open and close the right-side drawer, restore keyboard
focus, and fit desktop/tablet/mobile widths. The client navigation path was tested
with real Fabric identifier/bounds lookups and isolated browser interaction tests.
Baseline setup/seed/stream and source imports remained reused. Existing STID still
has 3 facilities, 15 equipment and 90 instruments; telemetry readback now contains
27,000 rows from prior workspace activity, not a stream restarted by chat deployment.
