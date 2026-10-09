# Hydro durable orchestration: local and hosted validation milestones

For the full October 7-8 implementation history, agent roles, hosted/local
distinction and production gates, see the
[Foundry and multi-agent implementation record](../docs/foundry-multi-agent-implementation.md).

## Current production boundary

This service is the implementation packaged in the Foundry Hosted Agent
`hydro-orchestrator`. Hosted Agent v22 runs the Microsoft Agent Framework workflow
for Chief, Gauge, Sleuth, Fixer and Sparky. The Fabric app is a thin authenticated
client: it submits bounded requests, renders backend-owned NDJSON execution events
and certified answers, and collects explicit human work-order decisions. It does
not run a browser-side supervisor or specialist tool loop.

The active v22 image is
`sha256:8bdaedf511cc0919d1c46eb38875c0c4486b2dfc324c0ff8816406b5fdb1262a`
(`validated-20261009155318`, ACR run `dts`). The source configuration digest is
`210d1ce53296ddb304613a7dcb87b22ee7a017073ef48b23e7fba7fcfa82644b`.
Production writes remain disabled in the Hosted Agent. Work-order proposals are
editable drafts until an explicit, separately validated human approval; no
acceptance test created production work.

The supported-runtime regression on October 9 passed all 129 backend tests under
Node 24.21.0 and Pyright with zero errors or warnings. The companion SPA cleanup
passed 378 frontend tests, typecheck, lint, environment validation and production
build under the same Node 24 runtime.

## Thin-client contract

Foundry, the Hosted Data Agent route and both Battle panes require the hosted
Invocations transport configured by `RAYFIN_PUBLIC_FOUNDRY_INVOCATIONS_URL`.
There is no browser coordinator fallback. Missing hosted configuration is an
explicit unavailable state rather than permission to execute local orchestration.

The backend owns intent normalization using the existing canonical helpers,
literal table/CSV presentation, chart validation and specialist execution receipts.
The SPA displays that contract without narrative repair, table inference,
automatic charts or mutation routing. `RAYFIN_PUBLIC_ORCHESTRATOR_SOURCE_DIGEST`
must match the runtime's complete source configuration; the explicit selected
ontology binding must belong to the same workspace.

Each client keeps a session ID and only the preceding accepted run ID, not
analytical results in browser storage. The backend loads at most one historical
display from that same signed-in user/source, bounded to 30 minutes and 16,000
characters. Historical text is context, never fresh evidence or approval.
Failed runs/new chats clear the client's preceding run ID. Old cards cannot
submit after their conversation resets.

Human decisions verify run/card identity. Explicit disabled/blocked no-write
receipts differ from uncertain outcomes. The separate `reconcile` operation
requires an existing identical approval intent and can only check the original
submission: it cannot initiate SQL creation. No automatic submission retry exists.

Validation of this candidate passed 117 backend tests, Pyright, 93 focused
transport/runtime/source tests and 143 related frontend regressions, typecheck,
focused lint, environment validation and production build. Vite still reports
large-chunk and mixed static/dynamic-import warnings. The subsequent state
bootstrap/source checks passed 43 targeted tests and Pyright; the container HOME
correction passed the 21 source/packaging tests and Pyright.

The existing Foundry coordinator is published through immutable images. Versions
1-4 exposed incomplete configuration and hosted session-volume permission issues.
Version 5 verified startup, non-root state initialization, CORS, fail-closed
ingress and same-session file persistence. Version 7 is active on ACR run `dta`,
`hydro-orchestrator:validated-20261009090642`, digest
`sha256:1a8ce284f0b0de8bed4a4a243efcb2097ffaac391ff1c9bbcb6f6ee3c7a3dfc7`.
It adds authenticated NDJSON execution streaming and backend-owned Chief/specialist
handoff events. Its deployed boundary accepted the streamed Foundry invocation
envelope and returned the intended sanitized HTTP 401 for fake delegated source
credentials; the exact synthetic session was then stopped without deleting its
state.

The October 9 canonical Fabric deployment exported the active invocation endpoint,
verified Chief v13, Gauge v15, Sleuth v17, Fixer v15 and Sparky v12, reapplied the
AppBackend runtime/database settings, and passed `/graphql` and
`/api/auth/v1/token` browser-equivalent preflight and POST checks. It reported
`SUCCESS` with the protected sign-in gate. This proves deployment and ingress
readiness, not signed-user source execution or the ten-prompt application
acceptance matrix.

Two bounded capacity resume/read/suspend windows verified live numeric generation
2 and the matching 13-part published Data Agent definition, restoring the original
Paused state afterward. The operator subsequently reported the capacity active;
leave it active. No source schema/data or production work order was changed.
The earlier hosted coordinator delegated SQL probe explicitly returned
`EXCHANGE_NOT_ENABLED`. The repository opt-in has now been applied by the
canonical deployment, but signed-user hosted SQL execution still requires live
acceptance; the existing embedded SQL/WO path remains separate and working.
Graph renewal in both legitimate CLI/azd
contexts received a CAE `InteractionRequired / TokenCreatedWithOutdatedPolicies`
challenge. Do not bypass normal authentication. Hosted state access, signed-SPA
execution, platform credential redaction, live SQL/native-source execution,
session recovery and latency remain acceptance gates. No production work order
was created by deployment or smoke testing.

The default HTTP service has **no configured source/agent adapters**, returns HTTP 503 for
readiness/run submission, and reports `live_fabric_connected: false`. It does not
substitute bundled data for Fabric. Synthetic adapters exist only in tests.
The four framework executors are application steps, not evidence that four Foundry
agents have executed. No production UI is pointed at this service.
Separate read-only source and Foundry RCA adapters now verify live Fabric identities,
exercise source access and invoke the existing persistent Sleuth agent. They are not a switch to production orchestration.

## Existing Foundry agents through Agent Framework

The separate `FoundrySupervisor` now uses the supported
`agent_framework.foundry.FoundryAgent` provider to execute the existing Chief,
Gauge, Sleuth, Fixer and explicitly selected Sparky capabilities. Each run pins
read-back agent names, versions, model deployment and declared tool schemas.
Python executes the existing function callbacks; no browser routing was added.

- Authenticated local `/chat/runs` submit/status/answer/evidence endpoints run
  independently of the submit connection. The CLI does not configure production
  source tools automatically.
- Immutable source receipts and specialist handoffs are persisted atomically.
  Handoff reuse includes the evidence and prior-specialist context, so final
  verification cannot accidentally reuse pre-investigation Gauge output.
- Investigation/maintenance review requires a final Gauge handoff. Source reads
  are coalesced; verification does not imply a second unchanged source read.
- Chief can submit an already-specified sequence in one ordered tool-call batch.
  SDK dispatch is sequential before asynchronous delegation validation; specialist
  source calls remain concurrent. Accepted RCA/work-review completions terminate
  their SDK loop through supported middleware rather than generating extra prose.
- Invalid source arguments and invalid structured completions each have one
  bounded correction. Real source/authentication failures are never retried as
  formatting mistakes or converted into empty results.
- Cells must reference actual scalar fields in the same source row. An invalid
  Chief answer gets one tool-free `FoundryChatClient` projection with a strict
  schema enumerating actual evidence/field/pointer combinations. This uses the
  verified Chief model deployment without creating another persistent agent.
  The audit distinguishes Chief's output from the projection's response identity.
- Tables are the default. Charts require explicit intent, numeric series and
  nonblank compatible units. An explicit chart request cannot silently disappear
  when the source receipts contain a numeric measure with verified units;
  otherwise a source-derived inability is reported. Cross-source rows require
  matching equipment or signal identity in every contributing source row, even
    when those identities are not display columns. Generic asset/operational reads
    retain private identities from the exact filtered/capped source rows before
    projection; these do not become display columns or model reference fields.
    Missing or contradictory identities do not prove a join. Missing-source
    warnings survive presentation.

**Live evidence, October 8:** Chief v12 coordinated Gauge v14 -> Sleuth v16 ->
Fixer v14 -> Gauge v14 against six real T005 measurements. Exact source cells,
five requested columns, zero charts and zero writes passed in **133.67 seconds**.
A later pass, exercising the structured projection and an RCA completion
correction, took **170.02 seconds**. Earlier repeat failures remain failures;
these bounded passes do not establish reliability or an acceptable latency SLA.
Stale telemetry and work/inspection sources not requested remained explicit.

An additional exact-data live run, `3bc0428f-1c7e-48fe-91fb-08083b8bc85e`,
passed in **133.73 seconds** with two Chief model rounds, one source read, one
RCA model round, and two Fixer rounds including a bounded completion correction.
The audit now records actual specialist/Chief model-round counts. This is a
measured round-trip reduction, not production latency acceptance.

The earlier SDK milestone's complete backend run passed **106 tests in 200.848 seconds**, Pyright and dependency
compatibility checks. SDK tests mock network boundaries; they do not certify
hosted execution. Production source authorization,
hosted image execution, distributed durability, human-approved SQL writes and SPA/Battle
integration are not accepted. A gated thin SPA/Battle consumer now exists locally;
the later hosted publication and startup failures are recorded above.

## Delegated invocation boundary

`create_delegated_app` is a tested HTTP composition boundary, **not a configured
or deployed production service**. It requires an explicit source identity,
authorization policy and asynchronous supervisor/source factory. The existing
local CLI and Fabric app do not enable it automatically.

`create_fabric_delegated_app` now supplies a concrete per-request factory:
verify live Fabric discovery, reject changed identity, construct the existing
`FoundrySupervisor` with `FabricBackendTools`, and close the delegated lease.
The read-only candidate exposes `query_assets`, `query_operations`,
`query_telemetry`, `run_kql`, `query_signal_quality_snapshot` and
`query_turbine_temperature_snapshot` and `query_station_power`. It is not the complete production provider.
Snapshots reuse the existing shared calculations: BAD means latest signal
quality BAD; hot rankings use latest raw temperature, not averages. Complete
bounded asset/work reads precede backend snapshot coverage attestations. Unit
and equipment coverage metadata derive from returned source data. The existing
browser reuses the calculations but does not attest complete work coverage.
Station power reuses the source-side unit conversion and sample-weighted means,
with an attested MW measure. It is not total station generation or energy.
The backend source tool returns rows and semantics, not an automatic chart;
the existing answer validator applies the user's explicit chart intent.
The station-power addition passed 20 focused backend tests (19 source tests plus
answer/chart integration), 26 Node source tests and 40 existing shared/browser
tool tests. The earlier 106-test full-suite result predates this addition.
Unsafe aggregate sample-count/numeric overflow is explicitly rejected.

### Source-grounded work approval candidate

`propose_work_order` is now a backend staging capability, not a SQL write.
It verifies active equipment and any selected signal against complete STID reads,
requires fresh complete equipment-filtered open-work coverage, and returns typed
editable cards with stable IDs and approval digests. Priority comes from the
typed operator input (default Medium), never model escalation. Cards expire
within 15 minutes of the work-source read.

The same signed-user `/invocations` boundary accepts `operation: "decide"` with
`run_id` and a `decision` containing `proposal_id`, `proposal_digest`, explicit
`approved`, and reviewed `edits` (title, description, priority) for approval.
Rejection contains no edits. Another user/source, changed card, expired draft,
or changed terminal decision cannot create work.

Production writes remain disabled by default. Explicit runtime
`production_writes_enabled: true` enables the separate human-decision provider,
not an agent tool. It revalidates the target and duplicate open work, uses a
stable creation ID, and verifies exact SQL readback including the signed-in
creator and approved fields. Lost acknowledgements are reconciled by ID;
an uncertain submission never automatically repeats a SQL create. Known
pre-write failures explicitly report no write. Decision intents/results are
available in the same authenticated evidence audit.

Tests use signed synthetic tokens and simulated SQL boundaries. This is not
live SQL authorization, distributed idempotency or production-write acceptance.
The existing browser approval flow has not been removed or redirected.

The disabled direct-exchange prerequisite is now understood: Rayfin 1.36 requires
`services.auth.fabric.externalEntraExchange: true`, while ordinary Fabric SSO
does not. The repository configuration now opts in, without changing redirects
or database schemas. The change still requires the canonical Fabric deployment
engine to apply it; no manual runtime patch is used. A delegated owning-tenant
token with `Item.Execute.All` and item Execute access is also required. See
[direct Entra token sign-in](https://rayfin.ai/docs/auth/entra-token).

- `POST /invocations` accepts `operation: "run"`, a `chat` input and a `tokens`
  map containing short-lived Fabric/Foundry delegated tokens, plus GraphQL/Kusto
  tokens when those sources are enabled. Tokens must come from the existing SPA,
  not browser-storage extraction, CLI exchange or a new registration.
- RS256 signatures, selected tenant, configured resource audiences, SPA client,
  delegated identity, same user across resources and expiry covering the bounded
  run are verified before constructing a supervisor. Actual source permissions
  are still enforced by the source services; this does not certify the new
  hosted delegated SQL path. The embedded app's working SQL/WO path is separate.
- State is namespaced by signed-in principal and complete configured source
  identity. The factory cannot substitute another state namespace, source or
  CLI/managed-identity credential. Returned answers must match the invocation.
- Raw tokens are not passed into `ChatRequest`, model context or receipts.
  Validation responses never include raw Pydantic inputs. Leases are cleared
  after success, source failure, timeout or cancellation.
- The boundary rejects oversized bodies and full execution capacity explicitly.
  Signing-key reads are coalesced and cached; unknown key IDs cannot force an
  immediate refresh on every request.
- The returned `audit_url` is `/invocations`: retrieve evidence with another
  authenticated **POST**, `operation: "evidence"` and the returned `run_id`,
  without `chat`. Another user's completed or failed run returns HTTP 404.
- A failed authorized run returns its server-assigned `run_id` and a sanitized
  failure audit through the same authenticated POST protocol. The audit records
  source identity, clocks and failure category, not exception bodies or tokens.
  It is not a completed execution certificate. A storage failure explicitly
  reports `audit_available: false`; no audit success is invented.

Unlike the existing queued local API, this request-scoped endpoint is not a
distributed durable scheduler. Reconnection/resume, hosted protocol packaging,
platform trace/body redaction, complete production source composition and the
SPA token transport remain rollout gates. It has no WO write/approval endpoint.
The working app sign-in and human-approved WO flow remain unchanged.

### Explicit runtime startup

`python -m hydro_orchestrator --serve-delegated` starts the composed backend
without enabling the unconfigured local validation API. It requires:

- `HYDRO_ORCHESTRATOR_CONFIG`: JSON with `source`, `authorization` and
  `project_endpoint`, matching the typed `RuntimeConfiguration` contract.
  `source` requires numeric generation 2 and the exact configuration digest.
  Fabric and Foundry scopes must match the SDK resources. Optional
  `native_binding` forwards explicit matching source/connection identities to
  the existing supervisor; runtime agent-definition readback still checks the
  selected connection. Supplying a binding is not live native-source acceptance.
- `HYDRO_FABRIC_SOURCE_CONFIG`: explicit source GUIDs, the current authoritative
  AppBackend `api_url` and its public `publishable_key`. The Node bridge
  canonicalizes this configuration and computes the digest. Invalid explicit
  JSON never falls back to repository deployment files. The CLI requires this
  setting; its local diagnostic file-based configuration remains unchanged.
  Use the canonical deployer's verified target configuration, never a copied
  endpoint from another capacity or target.
- `HYDRO_RUNTIME_STATE_DIR`: an absolute directory for local receipts.
  This is still local filesystem state, not distributed persistence.

`HYDRO_RUNTIME_HOST` defaults to loopback and `HYDRO_RUNTIME_PORT` to 8088.
Deployments must provide TLS at the ingress before transporting delegated
credentials; public binding alone is not a secure hosting configuration.
Access logging is disabled for this entrypoint. The lifespan owns and closes
the JWKS HTTP client. `/health` and the Foundry contract's `/readiness` report only that the process awaits an
authorized invocation, with live-source verification and writes explicitly
false; it cannot certify source access, hosting, durability or delivery.

Seventeen authorization/runtime tests include a real subprocess listener:
missing runtime configuration stops startup, health responds, invalid
invocations are rejected, and the test terminates its exact process. No live
credentials, source reads or production records are involved.

The essential container package retains Python 3.12, Node 24 and the bridge's
sibling source layout. Its Dockerfile-specific build context is deny-by-default:
no Rayfin deployment configuration, credentials, virtual environments or
browser build assets are included. Builder dependencies use the existing locks;
the final Node package keeps only the source runtime dependency closure.
Packaging tests exercise the isolated contracts/configuration and exact COPY
boundaries, and the pinned Python wheels were checked for Linux amd64.
At the earlier image-only milestone, the Linux-amd64 image compiled in the provisioned Basic ACR. Native ACR
run `dt4` succeeded and its build log confirms backend imports and the packaged
Node contract probe passed as UID 10001 without live-source access.
The image is
`cr7pa6m5kav6cig.azurecr.io/hydro-orchestrator:validated-20261009024916`,
digest `sha256:12caea5e3cc87b1fe17b81297dde4c12942b2becf85fbfde919a7ac62c330fa7`.

Real builds exposed and corrected three packaging issues: legacy builders do
not populate automatic `TARGETARCH` or execute this Docker heredoc; npm also
requires the app's existing, nonsecret `.npmrc` peer policy. The package now
checks actual Debian architecture, uses portable inline Node dependency
pruning, and includes that exact configuration. Browser packages remain
excluded from the final runtime. Source/packaging tests passed after these fixes.
Temporary source directories and named upload archives were cleaned.

The preview `azd publish` stalled without an ACR run and was stopped; native ACR
performed image-only compilation. At that milestone, no hosted agent version or
Fabric app was deployed, Fabric discovery returned `CapacityNotActive`, and no
runtime configuration was seeded from unverified live generation. The later
verified source configuration, publications and startup failures are recorded
above. The compiled
image is not hosted-session, delegated-SQL, platform-redaction, distributed
durability or SPA/Battle acceptance.

Read-only Entra metadata confirms the existing SPA lists Fabric
`Item.Execute.All`, GraphQL/Data Agent execution and Foundry
`user_impersonation` permissions. Configured permissions are not proof of
effective consent, item authorization or successful delegated source execution.
No registration, permission, consent or authentication setting was changed.

The canonical tool definitions and row shaping were moved unchanged into the
existing browser-free `query.ts` module; browser imports are re-exported for
compatibility. No additional TypeScript helper file or browser routing was added.
The Node bridge derives entity-specific backend validation from that catalog,
rejecting wrong column aliases, duplicate selections and out-of-bound limits
before a model invocation can request the source read.

Generic asset and operational readers use declared entity identity columns,
complete bounded pagination and the shared field projection. They preserve
native types and reject partial, duplicate or malformed results rather than
claiming empty work. SQL primitives request only the Fabric token, not unused
GraphQL/Kusto tokens. Existing SDK exchange failures still propagate: these
tests do not prove that delegated SQL exchange is enabled.

The generic candidate still requires metadata discovery, supports neither fleet
snapshot helpers nor proposal/approval writes, and does not attach chart-unit or
cross-source join provenance. It is not a production SQL-independence, general
charting, native Data Agent or Battle acceptance result.

## Implemented boundary

The current local candidate also exposes authenticated
`GET /runs/{run_id}/answer` and `GET /runs/{run_id}/evidence`. The answer is generated
from a persisted proposal/assessment or committed outcome, not agent prose:

- Each column has a distinct key and declared text/number/timestamp type.
  Every row must have exactly those keys. Transposed unit/value fields, numeric
  strings in numeric columns, duplicate columns and non-finite values fail.
- Measurement cells are copied from the immutable evidence. Row citations resolve
  to the same telemetry receipt sent to the existing Foundry specialist.
- Work coverage retains only the actual returned work-order numbers. It does not
  invent titles, priorities or statuses that this bounded adapter did not return.
  The audit labels this coverage receipt as an adapter summary, not a raw SQL query.
- Queued, running and failed runs return HTTP 409 instead of a successful answer.
  Neither endpoint invokes providers again or creates a production work order.

This contract has no chart field because the bounded inspection-review input does
not request charts. General chart-request handling and the thin SPA consumer belong
to the broader hosted integration; no automatic inventory/count chart is implied.
Reconstructed live RCA assessments recheck all report pointers and the existing
specialist identity, in addition to validating live responses before persistence.
Terminal SQLite updates reject replacement outcomes and late failures.
These are local corrections, not proof of a hosted Agent Framework migration.

```text
Authenticated local request + idempotency key
  -> durable run journal
  -> evidence adapter
  -> investigation adapter
  -> proposal adapter
  -> durable no-draft/clarification outcome OR persisted human approval request
  -> reject OR atomic local validation record
```

- Explicit workflow edges replace model-directed routing for this one bounded
  inspection-review flow. Free-form chat routing and the remaining compound flows
  are not implemented here.
- Typed immutable handoffs preserve tenant/workspace/ontology/equipment identity,
  numeric generation 2, units, quality, timestamps and actual evidence references.
  No narrative-table parsing or native-source substitution is used.
- RCA conclusions are constrained to `cause_undetermined`. Schema/reference checks
  are not a causal model or proof that the agent selected relevant evidence.
- Agent Framework checkpoints save completed stages. SQLite records requests,
  approval decisions, outcomes and cursor-addressable execution events.
- Work starts independently of the HTTP request. Closing the connection does not
  cancel a run. Restart recovers queued/running work; waiting approvals remain
  waiting. Completed source reads are not repeated on checkpoint recovery.
- Model/source steps have deadlines. Failures are logged and recorded explicitly,
  not converted to empty evidence. Explicit retry is limited to two attempts;
  automatic source retry/circuit-breaker policy is not implemented.
- Approval is bound to the exact proposal digest and expiry. Decisions cannot be
  changed after recording. Concurrent duplicate approvals and replay after a
  committed operation produce one **local validation record**, never a Fabric WO.
- A proposal requires an explicit completed work-order read, including when zero
  rows were returned. Its expiry cannot exceed 15 minutes from that read. A resumed
  workflow cannot turn old coverage into a new 15-minute approval window.
  The reader records the clock when SQL pagination completes, not when a slower
  parallel telemetry request eventually finishes. Source latency cannot extend
  the work-coverage approval window.
- The terminal outcome and any local validation write commit in one transaction.
  Recovery uses that authoritative receipt if a process stops after the framework's
  final checkpoint but before the run journal is marked complete; it does not
  re-execute the operation or infer success from agent prose.
- Typed `no_draft` and `needs_clarification` outcomes bind the immutable assessment
  and any specialist receipt, bypass approval, and cannot create a validation work
  record. A no-draft decision requires completed work-source coverage. Identical
  replay returns the persisted decision; changed outcomes fail. This is a tested
  local adapter contract, not an implemented live Fixer adapter.

### Checkpoint failures are fatal

The pinned SDK logs checkpoint-save errors and continues by default. Hydro's storage
adapter retains the failure and blocks subsequent steps and successful completion.
It also refuses to silently skip a corrupted checkpoint and select an older one.
An already committed terminal business outcome can be recovered from its atomic
SQLite receipt without replaying the workflow; this recovery is explicitly journalled.
The checkpoint decoder allows only the explicit Hydro contract classes.
Checkpoint files must remain in a trusted, access-controlled local directory.

### Deliberately local

One worker process owns the state directory; a file lock rejects a second owner.
At most four workflows execute concurrently. This is not distributed hosting,
tenant isolation, Entra authentication, a managed durability SLA or a load-tested
enterprise service. The CLI binds only to `127.0.0.1`; there is no public CORS setup.
Local checkpoints contain synthetic validation evidence, not production analytical
data. Production evidence retention/access controls require a separate design.

## Live source adapter: verified access and blocking prerequisites

The Python reader uses delegated `AzureCliCredential` for local diagnostics.
A bounded Node subprocess reuses the installed Rayfin SDK, its supported
`signInWithEntraToken` helper, and the app's telemetry query builder. Tokens travel
only through process stdin; they are not command-line arguments, files, checkpoints
or log output. Rayfin sessions remain in memory and are destroyed after each read.
This is not a production OBO or managed-identity implementation.

Before reading, the adapter verifies live numeric ontology generation 2, exact
workspace/ontology/KQL identities, the KQL database's Eventhouse parent, and the
AppBackend endpoint against the workspace's current capacity and saved deployment.
It does not reconstruct or change deployment state. A configuration fingerprint
binds a request to its sources; a changed configuration fails before source reads.
The configured graph binding supplies the selected ontology ID only: this reader
does not query, fabricate or certify native graph topology.

STID resolves one exact equipment ID and its instrument IDs/units. The KQL query
returns the latest raw sample for each mapped signal within 24 hours, retains BAD
quality samples, and labels missing samples and readings older than 30 minutes.
It is not the fleet's 30-minute "running hot" query and does not invent a physical
fault or approved temperature limit. KQL completion-status errors/warnings and
truncated/ambiguous envelopes are rejected, not converted to partial success.
Work-order reads paginate up to 500 records and reject incomplete coverage,
duplicate identities or invalid cursors. Only Completed/Cancelled are excluded.
No SQL mutation method exists in this bridge.

From this service directory, using the operator's existing tenant-scoped Azure CLI
session and installed app dependencies (Node 24):

```powershell
.\.venv\Scripts\python.exe -m hydro_orchestrator --probe-live-sources EQUIP_RTI_T005
```

If the canonical deployer used an isolated Azure CLI cache, select that existing
cache through `AZURE_CONFIG_DIR`; do not copy tokens or create another login cache.
`HYDRO_LOCAL_NODE` may select the installed Node executable.
This command does not start an HTTP server, save source data or invoke agents.
It exits nonzero when any source check fails. A passed read is not evidence of
fresh ingestion; inspect `missing_sources`.

**Observed live result, October 9:** verified ontology generation 2 and six
current STID-mapped T005 measurements with no telemetry source gap. The local
diagnostic work-order read failed explicitly with **`AUTH_FAILED`**.
This does not establish a failure in the working app: the app uses embedded
Fabric authentication and `ensureSignedInWithFabric`, not this diagnostic's
direct Entra exchange. Its existing approval/write path is unchanged. The installed Rayfin
guidance identifies `services.auth.fabric.externalEntraExchange: true` as the
setting for delegated exchange. The canonical deployer has applied that setting
to the live AppBackend, but the Azure CLI diagnostic still returns `AUTH_FAILED`.
The SDK also requires delegated `Item.Execute.All`, owning-tenant identity and
item Execute permission. The inspected Fabric token from the existing CLI session
lacked that named scope; enabling the setting alone is not a verified solution.
An explicit Azure CLI scope request returned `AADSTS65002`: that first-party client
is not preauthorized for this scope. This is not repaired by repeatedly signing in
or granting customer-tenant admin consent. The documented `RayfinAuth` silent-token
alternative was also tested and had no usable cached login. A supported interactive
Rayfin identity or a properly authorized server-side delegated/OBO path is still
required; no browser token extraction, app-only substitution or auth bypass was used.
The hosted backend requires a supported, authorized delegated source path before
claiming hosted-coordinator SQL access. The embedded Fabric app SQL/WO path is
working and remains the accepted write path. Do not replace its broker login,
use app-only tokens for Rayfin exchange, or weaken its approval safeguards.

The workflow regression verifies that this failure produces a failed run with
no investigation, proposal or approvable card. It cannot become "no open work."
The diagnostic read adapter is ready for composition only after its live prerequisites
pass. The independent telemetry-only investigation below does not substitute for
the failed work-order read. The proposal adapter and SPA integration remain pending.

## Live scientific RCA adapter

The RCA adapter reads back the existing `hydro-rca-agent`, pins its returned version,
and checks its completion-tool schema against the app's shared `RCA_REPORT_TOOL`.
Foundry rejects a request-level `tools` override when an agent reference is supplied;
the adapter uses the verified persisted tool and a forced completion choice instead.
It makes one model invocation, with no automatic retry or model-directed query loop.
HTTP errors, deadlines, incomplete output, narrative answers and unexpected/duplicate
calls fail explicitly.

The returned report passes the same `parseRcaAssessment` implementation used by the
SPA. References must point into actual supplied source receipts; invented paths,
free-text diagnoses and duplicate hypotheses are rejected. The typed assessment
retains supporting/contradictory references, missing evidence, a source-input digest,
the requested agent version and the service response/request IDs. Checkpoint recovery
preserves this report without repeating the completed invocation. Reference validity
does not prove causal relevance: the conclusion remains `cause_undetermined`.

```powershell
$env:HYDRO_FOUNDRY_PROJECT_ENDPOINT = '<existing project endpoint>'
.\.venv\Scripts\python.exe -m hydro_orchestrator --probe-live-rca EQUIP_RTI_T005
```

This explicit **telemetry-only** diagnostic never reads SQL, proposes work or sends
notifications. It marks work orders/inspections as not requested rather than claiming
none exist. The proposal contract independently rejects this incomplete coverage.

**Observed live result, October 9:** Sleuth v17 processed six verified T005 samples
in one invocation lasting **11.08 seconds**, using 2,569 input and 1,265 output
tokens. Response ID: `resp_0aee7ed7f1e3af01016ac89c07bd9c81908d12b83424af4a56`;
request ID: `8f50f77b-3010-4bd2-9a1f-b97c0acfec57`. Its three competing hypotheses
remained untested; stale telemetry, missing maintenance/inspection evidence,
approved limits and matched-baseline gaps were retained. This timing is for the
model invocation, not end-to-end source discovery or an interactive performance SLA.
It is one actual specialist call, not a completed multi-agent workflow.

Older local validation proposals without an explicit work-read clock are no longer
approvable under the strengthened contract. Preserve their state for audit and start
a new run/state directory; do not rewrite receipts or delete deployment state.

## Run the checks

From this directory, with Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[test]"
.\.venv\Scripts\python.exe -m pyright --project .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test ..\HydroOperationsApp\scripts\local-fabric-sources.test.mjs
```

The 34 Python tests use the actual Agent Framework engine, file checkpoints, SQLite transactions
and an ephemeral loopback HTTP listener. Providers are synthetic: these tests do not
certify Foundry, native Fabric endpoint availability or live WO creation.
Eleven Node tests additionally cover live-reader contracts with mocked transports:
generation/capacity checks, KQL completion/partial failures, exact equipment mapping,
units, quality, freshness, source errors, work-order pagination and its independent
completion clock. Pages larger than the requested 100 records are rejected.
The RCA tests run the real shared JavaScript parser against mocked Foundry responses,
including schema drift, invalid references, output failures and typed-report recovery.
The live diagnostic above is separate evidence, not a mocked test result.
After the 34-test full Python run, an additional expiry regression was added;
the affected RCA suite now passes 11 tests, including the case where telemetry
finishes two minutes after SQL and only 13 approval minutes remain.

The fault suite covers real process exit/recovery, HTTP disconnect, interrupted
investigation, a failure after a committed local write, checkpoint-write failure,
corrupt checkpoints, duplicate/concurrent approval, rejection, expiry, changed source
identity, malformed evidence, unknown evidence references and unexpected adapter bugs.

To inspect the intentionally unconfigured HTTP service:

```powershell
$env:HYDRO_LOCAL_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe -m hydro_orchestrator
```

`GET http://127.0.0.1:8088/healthz` is liveness only. All run/readiness routes require
the local bearer token. `GET /readyz` returns 503 without configured adapters.
Do not put this token in the SPA, source control, a URL or a production configuration.

## Required before production migration

1. Approve cloud hosting/durable storage and its cost; select compatible runtime
   package versions and a managed recovery/availability model.
2. Implement and test server-side Entra authorization and the supported delegated
   access path for each Fabric/Foundry endpoint. Do not assume every native endpoint
   supports managed identity or app-only access.
3. Finish the live Foundry adapters and unblock/test the hosted read adapter's
   delegated SQL path
   against verified v2 identities, retaining source failures,
   direct-source evidence and native-source limitations. Never migrate by treating
   arbitrary model narrative as an authoritative dataset.
4. Implement production SQL idempotency and server-side approval authorization.
   The local SQLite transaction does not prove exactly-once effects against a
   separate remote database. Add a durable notification outbox and delivery receipts.
5. Wire the SPA to run IDs, progress events, reconnect and approval endpoints.
   Preserve card-first presentation; do not label application executors as actual
   Foundry invocations without their service receipts.
6. Benchmark representative workloads, reduce redundant calls/context, add bounded
   retry/circuit-breaker policies, and repeat the
   [ten compound prompts](../HydroOperationsApp/AGENT-TEST-PROMPTS.md), multi-turn and
   fault-injection suites on the same production candidate.
7. Integrate the new hosting/auth steps into the existing canonical deployer before
   any hosted rollout. There is no alternate deployment command for this service.

References: [workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/),
[checkpointing](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints),
[durable hosting](https://learn.microsoft.com/en-us/agent-framework/hosting/azure-functions).
