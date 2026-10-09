# Foundry and multi-agent implementation record

Work period: **October 7-9, 2026**. This is the end-to-end record of the
implementation, corrections, measured results and unfinished migration, grounded
in repository commits and the linked acceptance records. It is not a release
certificate. Earlier runs are retained as historical evidence, not relabelled
as passes after a later code change.

## Current correction: Foundry-owned execution, not more browser routing

The October 8 design review rejects further expansion of the browser coordinator.
The target is an Agent Framework workflow hosted in the existing Foundry project,
reusing the deployed prompt agents rather than creating replacement specialists.
The SPA submits runs, renders typed results and execution events, and collects
human approval. Routing, retries, source execution, checkpoints and result
validation belong in the backend. This migration is **not complete**: a hosted
coordinator has been published, but its current runtime fails startup and the SPA
switch remains off.

The latest local migration wires Foundry, Data Agent and Battle to an explicitly
gated thin Invocations client. The backend now supplies literal tables, requested
charts and real specialist response/version receipts; hosted messages bypass the
old browser narrative/table/chart inference. Server intent normalization reuses
the canonical helpers rather than adding browser routing.

The thin client renews delegated leases per request, clears its ephemeral token
dictionary, preserves session affinity and sends only the preceding accepted run
ID for follow-ups. The backend loads bounded same-user/same-source historical
display context, never reclassifying it as current evidence or human approval.
Reset/failed reads clear conversation linkage, and reset cards cannot submit.
Explicit no-write failures remain distinct from uncertain creation; a separate
read-only reconciliation operation cannot perform a first create.

The migration passed 117 backend tests and Pyright, 93 focused transport/runtime/
source tests, 143 related frontend regressions, typecheck, focused lint,
environment validation and production build. Subsequent state bootstrap/source
checks passed 43 targeted tests and Pyright; the container HOME correction passed
21 source/packaging tests and Pyright. The original browser coordinator remains a
lazy rollback until the ten-prompt hosted acceptance matrix passes. No database
schema mutation or production work-order creation accompanied these changes.

The coordinator was published in the existing project. Versions 1-4 exposed
incomplete configuration and hosted session-volume permission issues. Version 5
verified startup, non-root state initialization, CORS, fail-closed ingress and
same-session file persistence. Version 7 is active on ACR run `dta`, tag
`validated-20261009090642`, digest
`sha256:1a8ce284f0b0de8bed4a4a243efcb2097ffaac391ff1c9bbcb6f6ee3c7a3dfc7`.
It adds authenticated NDJSON execution streaming and backend-owned Chief/specialist
handoff events. Its deployed boundary accepted the streamed Foundry invocation
envelope and returned the intended sanitized HTTP 401 for fake delegated source
credentials. The exact synthetic session was stopped while retaining state. The
published coordinator is visible in
[Foundry v7](https://ai.azure.com/nextgen/r/2sFlAVw1TJGv8BGvjeF_Lg,rg_ai,,foundryfabriciqappsdemo-resource,foundryfabriciqappsdemo/build/agents/hydro-orchestrator/build?version=7).

The October 9 canonical Fabric deployment exported the active invocation endpoint,
verified Chief v13, Gauge v15, Sleuth v17, Fixer v15 and Sparky v12, reapplied the
AppBackend runtime/database settings, and passed browser-equivalent CORS and POST
checks for `/graphql` and `/api/auth/v1/token`. It reported `SUCCESS` with the
protected sign-in gate. This proves deployment and ingress readiness, not
signed-user source execution or the ten-prompt application acceptance matrix.
The redundant `hydro-data-agent-bridge` was verified as unpublished and
unreferenced by the repository or retained agents, then deleted. Chief, Gauge,
Sleuth, Fixer, Sparky and the hosted `hydro-orchestrator` remain.

Two bounded capacity resume/read/suspend windows reverified live numeric
generation 2, exact native connection targets and the matching 13-part published
Data Agent definition, restoring Paused after each window. The operator has since
reported capacity active; leave it active. Identity readback is not native query
execution. Telemetry disclosed `fresh_telemetry` missing, and the hosted
coordinator's earlier delegated work read failed explicitly with
`EXCHANGE_NOT_ENABLED`. The repository opt-in has now been applied by the
canonical deployment, but signed-user hosted SQL execution still requires live
acceptance; the existing embedded SQL/WO path remains separate and working.
Both legitimate CLI and azd Graph credential renewal
received CAE `InteractionRequired / TokenCreatedWithOutdatedPolicies`; normal
authentication renewal remains necessary, with no bypass or copied caches.
Signed-SPA source execution, live SQL/native answers, platform-wide payload
redaction, recovery, full chat/Battle parity and latency remain unaccepted.

Read-only Foundry readback confirmed the existing enabled agents: Chief v12,
Gauge v14, Sleuth v16, Fixer v14 and Sparky v11. Chief exposes
`delegate_to_agent`; Gauge/Sleuth/Fixer expose application-executed function tools.
These definitions explain why publishing agents alone did not create a durable
server-side workflow. Keep these identities and their verified source bindings.

### Actual SDK integration and current live boundary

The local service now uses `agent-framework-foundry` 1.13.1 alongside framework
core 1.19.0. `FoundrySupervisor` executes the existing version-pinned agents and
their declared functions in Python. Authenticated local chat-run endpoints,
atomic receipts, source-read coalescing, contextual specialist reuse, mandatory
post-review Gauge verification and strict source-built answers are implemented.

Foundry rejects request-level structured formatting when an existing agent
reference is supplied. An invalid final Chief output therefore gets one
tool-free model-client projection using the read-back Chief model deployment.
Its strict JSON schema enumerates actual evidence IDs, literal field keys and
valid source pointers. No new persistent formatting agent is created. Chief and
projection response identities remain separate in the audit; source failures
cannot enter the formatting-correction path.

Read-only live compound run `ae230899-ddd5-4b64-845e-90e8e2fb426a` passed in
**133.67 seconds**: Gauge v14 -> Sleuth v16 -> Fixer v14 -> Gauge v14, with six
exact source rows, five requested columns, no charts and no SQL writes.
Run `9994d766-2fb4-4751-916a-fcf7de72dda1` also passed, including a bounded RCA
completion correction and the typed presentation stage, in **170.02 seconds**.
Earlier compound runs failed on omitted verification, extra display columns,
invalid pointers and an incorrectly shared correction budget. Those failures
are retained, not relabelled as passes. These two passes do not establish
production reliability or acceptable performance.

Further performance corrections use SDK-ordered batched handoffs and supported
middleware termination after accepted structured RCA/work review. Dispatch must
be sequential before asynchronous validation; locking after validation alone
did not preserve batch order. Live run `3bc0428f-1c7e-48fe-91fb-08083b8bc85e`
passed exact six-row/five-column checks in **133.73 seconds**, with two Chief
model rounds, one actual source read, one RCA model round and two Fixer rounds
including a correction. Runtime model-round counts are included in the audit.
Latency remains unaccepted; these counters do not replace a performance SLA.

The preceding approval/image milestone's complete backend suite passed **114 tests in 200.815 seconds**, with zero Pyright errors
or warnings. Source-bound proposal, signed decision, rejection, uncertainty
reconciliation and authenticated approval-audit tests are included. The actual
Agent Framework Fixer staging test also rejects conflicting duplicate card IDs;
its focused follow-up passed. The combined shared-tool/source suite passed
**69 tests** (29 backend-source and 40 existing shared-tool tests), with focused
bridge lint and environment validation clean. Existing frontend validation passed
216 tests, typecheck, lint, environment validation and production build.
Foundry now hosts active `hydro-orchestrator` v7 on immutable digest
`sha256:1a8ce284f0b0de8bed4a4a243efcb2097ffaac391ff1c9bbcb6f6ee3c7a3dfc7`.
The explicit Chief -> Gauge -> presentation graph and packaged operation skills
passed full backend regression, were included in the published image, and are
wired into the deployed SPA. Ten-prompt signed-user acceptance is still pending,
so the lazy browser coordinator remains a temporary rollback. Local and
hosted-session files are not a distributed durability or tenant-isolation
guarantee.

The diagnostic `EXCHANGE_NOT_ENABLED` result belongs to its direct CLI-token
exchange. It does **not** mean the app's embedded Fabric session or existing
human-approved WO creation is broken. Those working paths are preserved.
The hosted coordinator's delegated SQL authorization remains a separate live gate;
the existing embedded SQL/WO flow is working and preserved.
No database/Eventhouse/Lakehouse schema, production records or source data changed.
No production work order was created during deployment or smoke testing.

### Source-grounded approval and hosting preparation

Fixer may now stage editable, source-bound work cards after fresh active-target
and complete open-work verification. It cannot submit SQL. Explicit signed human
decisions use the existing WorkOrder API/schema, stable creation IDs, immutable
intent/result receipts and exact approved-field readback. An uncertain previous
write is read/reconciled, never automatically submitted again. Production writes
remain disabled; tests use simulated SQL. This is not distributed idempotency.

The direct SQL exchange failure has a concrete configuration prerequisite:
Rayfin requires `services.auth.fabric.externalEntraExchange: true`. The installed
1.36 configuration type and official direct-token documentation confirm that
location. It is now set and regression-tested in the repository, but not applied
to the live AppBackend. Effective delegated `Item.Execute.All`/item Execute access
and live readback remain separate acceptance gates; normal embedded Fabric SSO
continues unchanged.

At the earlier hosting-preparation milestone, minimal infrastructure reused the existing Foundry project/model and
specialists. One Basic authenticated ACR, its ManagedIdentity project connection
and the project principal's AcrPull assignment were provisioned. No hosted
coordinator version had yet been deployed. The manifest declares the actual Invocations
2.0.0 protocol and explicit runtime/source/state configuration. Foundry's session
contract identifies `/home/session` as the session-persistent HOME; continuity
requires `agent_session_id`, is session-scoped and is not global durability.

At that milestone, source discovery was blocked by the existing Fabric capacity's
`CapacityNotActive` response. Workspace and generic item metadata are readable
and confirm the selected ontology still exists. Its live numeric generation
could not be reverified while capacity was paused. The later bounded verification
and current hosted startup failures are recorded above. No ontology replacement,
source fallback, schema change or source-data mutation was performed.

Image-only native ACR build `dt4` subsequently succeeded. Its Linux-amd64 image
`hydro-orchestrator:validated-20261009024916` has digest
`sha256:12caea5e3cc87b1fe17b81297dde4c12942b2becf85fbfde919a7ac62c330fa7`.
The build executes backend imports and the packaged Node contract probe as
nonroot UID 10001, without source tokens or live queries. Actual compilation
exposed missing legacy-builder architecture defaults, an unexecuted Docker
heredoc and omission of the existing npm peer policy; these were corrected and
the 21 source/packaging tests rerun successfully. A stalled preview `azd publish`
was stopped before a hosted version was created; native ACR built the image
from only 26 explicitly allowlisted runtime files. Temporary staging directories
and named upload archives were cleaned. Compilation is not a hosted rollout
or source/runtime/latency certification.

### Delegated request isolation, not a hosted rollout

The chosen integration direction reuses short-lived delegated source tokens from
the existing SPA rather than adding a confidential OBO registration.
`source_auth.py` verifies tenant-issued RS256 signatures, configured audiences,
SPA identity, delegated user consistency and token expiry. `LiveSources` and
the existing RCA client now accept the shared asynchronous credential protocol;
the tested delegated opening path does not instantiate CLI authentication.

The `create_delegated_app` HTTP boundary verifies credentials before execution,
isolates receipt namespaces by user and source, enforces the supplied credential
and request identity, bounds concurrency/deadlines, sanitizes validation errors
and clears leases on exit. Its raw POST protocol supports run invocation and
same-user completed/failed-run evidence retrieval. Fifteen signed-token/HTTP tests cover
these boundaries, including expiry, cross-user audit denial, credential/namespace
substitution, oversized input, saturation, timeout, source failures and failure
audit storage errors. Authorized failures include a server-assigned run ID and
sanitized category/source/clock audit, never raw exception bodies or tokens.
Audit storage errors explicitly report that the audit is unavailable.
The earlier complete 100-test backend run took 176.939 seconds; these are transport/SDK
tests, not production source, hosted or latency acceptance.

Read-only resource-permission metadata resolved the existing SPA's configured
Fabric `Item.Execute.All`, GraphQL/Data Agent execution and Foundry
`user_impersonation` grants. This confirms configuration only, not effective
consent, signed SPA token acquisition or AppBackend SQL exchange.
No login popup, credential copying, permission/registration change or source
schema/data change was made.

An explicit request factory now composes live delegated discovery, the existing
supervisor and seven read-only backend tools: asset metadata, operational
records, templated telemetry, guarded KQL, latest signal-quality snapshots and
latest raw turbine-temperature snapshots and station-power means. Station power
reuses the shared metadata-based W/kW/MW/GW conversion and sample-weighted mean,
attests MW without claiming total generation or work coverage, and adds no
automatic chart or browser routing. Shared schemas and row shaping were
extracted into the existing pure query module, with compatibility re-exports;
no new TypeScript module or browser routing was added. Backend JSON schemas
derive column constraints from the same catalog, rejecting SQL/asset alias
mixups before authentication or source I/O. Catalog row identities are explicitly
declared rather than guessed from the first display column.

Asset/operational readers require complete bounded pagination and preserve
actual typed fields. Telemetry uses the existing authoritative Kusto parser and
rejects over-bound results. SQL source callbacks request only the Fabric token;
real exchange failures propagate rather than becoming empty work.
Tests use synthetic source/SDK boundaries, including the factory composition;
they do not attest live SPA token relay or SQL execution.

Snapshot calculations are now shared between the original browser caller and
the backend, rather than duplicated. BAD remains latest signal quality BAD;
hot ranking remains latest raw temperature with authoritative instrument units.
The backend requires complete bounded metadata/work reads and derives coverage
from actual returned data. Browser work coverage remains explicitly unattested.
Invalid readings, ambiguous metadata and missing/mixed temperature units fail
instead of producing misleading rankings. Source/display/copilot validation
passed 90 targeted Node tests (23 source, 27 display, 40 copilot); the backend
source suite passed 15 tests. No production snapshot or SQL pass is claimed.

Answer validation now rejects cross-source rows without matching actual
equipment or signal identity in every contributing source row. This closes the
unsafe projected-away identity case. Generic asset/operational readers now keep
private row identities aligned through filtering, limiting and projection.
The backend removes them from model/display result data and uses them only for
join validation/audit. Missing or contradictory identities still fail closed.
Explicit charts cannot silently disappear
when numeric measures have verified source units; otherwise the absence of
those units produces a source-derived limitation. Blank chart units fail.

The candidate has no complete production provider or accepted hosted deployment.
An explicit `--serve-delegated` entrypoint now validates runtime source/policy
configuration, checks the Node-derived digest/identities before binding, owns
the JWKS client's lifespan and forwards explicit matching native bindings.
It requires explicit runtime source JSON rather than local deployment-file
fallback. A real subprocess HTTP test confirms startup, truthful uncertified
health, invalid-body rejection and process cleanup. The current targeted
authorization/runtime suite passes 17 tests. After the station-power addition,
the targeted backend source suite passed 19 tests plus the station answer/chart
integration test (20 combined), the Node source suite passed 26 tests, and 40
existing shared/browser tool tests passed. Backend Pyright, application typecheck
and focused lint are clean. Aggregate sample-count/numeric overflow is rejected,
and answer integration preserves exact MW cells while enforcing chart intent.
The first new Node
test used an invalid Kusto response fixture; correcting the fixture to the
actual `Tables` envelope made the suite pass without weakening the parser.
Runtime/private-identity changes are covered by the complete-suite record above;
the subsequent station-power addition is covered by the targeted checks, not a
new full-suite run. These tests do not certify live source access.

The mixed-runtime container package now has a deny-by-default Dockerfile-specific
context, locked builder dependencies, the actual Node runtime import closure,
Python 3.12/Node 24 and a non-root writable local state directory. Isolated
contract/configuration execution and COPY/context tests passed; the 59 pinned
Python Linux-amd64 wheels were checked. No actual image build or hosted
deployment occurred, and no registry was provisioned. The local Docker engine
is unavailable and the selected subscription has no existing registry.
The existing Foundry child project ARM identity was independently read back.
A request to provision one billable registry for remote builds could not obtain
an operator response; no new registry or other Azure resource was created.
Platform credential redaction, production provider composition, distributed
durability, SQL authorization, approval/write integration and thin SPA/Battle
transport remain incomplete. No placeholder hosted agent was published and no
existing browser implementation was deleted before replacement parity.
Live fleet snapshot acceptance, generic-read chart-unit provenance and native-source
configuration remain additional gates before this provider replaces the app runtime.

Current local corrections, still uncommitted:

- SQLite completion requires the exact transactionally committed outcome. Late
  failures or replacement outcomes cannot overwrite a completed run. Waiting
  proposals must match the persisted request and executing state.
- Reconstructed RCA contracts revalidate the specialist identity, every
  supporting/contradictory/observation pointer, selected observations and missing
  evidence against the same receipt supplied to Foundry. Live-response validation
  alone was insufficient for recovered checkpoints.
- The bounded service returns a source-built answer with keyed, strictly typed
  columns/rows and resolvable row citations, separate from the full evidence audit.
  It does not accept model-authored cell values or recover columns from CSV.
  This covers the existing inspection-review contract, not all free-form flows.
- Shared presentation defaults to tables, hides technical ledgers in an
  expandable audit and shows requested charts without adding unrelated automatic
  count charts. No client-side source-column selection or new routing logic was
  retained. This is compatibility presentation, not the orchestration replacement.

The latest user preference **supersedes automatic charts for all numeric outputs**.
Charts must answer an explicit request with verified source data; table-only
requests take precedence. Required charts must not be silently omitted.

Repository inventory found **58 newly tracked paths since October 7**, all still
present: 39 in the app, 16 in the service, two in workspace-reset and one in docs.
No tracked fixture, log, session-state or checkpoint path was found by the targeted
inventory. New redundant presentation helper/test files were consolidated into
the existing modules. Existing imports/deployment references still require the
browser modules until the backend replacement passes acceptance; do not delete
them just to reduce the count or preserve two permanent orchestration paths.

Development authentication uses the existing Azure CLI session. `azd` successfully
reused it with its supported `auth.useAzCliAuth` setting in session-local
configuration. No passwords, access tokens or refresh tokens were copied.
User-session reuse cannot override expiration or Conditional Access.
Foundry runtime identity and delegated Fabric/SQL source authorization remain
separate production gates; a managed identity must not silently substitute for
delegated access.

## 1. Are we using Microsoft Agent Framework?

**Yes in the new local Python backend; no in the currently hosted Fabric app.**

| Surface | Current implementation | Verified boundary |
|---|---|---|
| Hosted Fabric app | Release **1.0.775 / `41d458a`**, browser-coordinated persistent Foundry Prompt Agents | Deployment/readiness passed. Latest browser acceptance is blocked by an expired private-hosting session and blocked sign-in popup; full matrix not accepted |
| Local orchestration service | Actual **Microsoft Agent Framework 1.19.0** executors, workflow edges and file checkpoints, with a SQLite run/approval journal | Durable recovery and approval tests; not distributed or production hosting |
| Backend live sources | Separate read-only Fabric adapter with source-identity checks | Live v2 identity and six T005 telemetry samples read; samples stale; diagnostic CLI SQL exchange unavailable, not an app SQL failure |
| Backend live agent execution | Existing Chief v12, Gauge v14, Sleuth v16 and Fixer v14 through the supported Agent Framework provider | Two bounded telemetry-only compound passes; hosted source tools, approval and broad performance acceptance remain incomplete |
| Backend proposal/SQL/SPA integration | Not complete | Default HTTP service deliberately returns 503 for readiness/run submission without adapters |
| Latest chart comparison correction | Deployed in **1.0.766**, locally regression-tested | Historical/current numerical comparison and shared KQL response validation; no hosted acceptance yet |

The four local workflow executors are application steps, not four newly created
Foundry agents. Installing Agent Framework did not automatically migrate the SPA,
solve Fabric authorization, make SQL writes idempotent, or establish a latency SLA.
No additional cloud hosting was provisioned for this service.

### Why change the orchestration architecture?

The original browser design reused the signed-in user's delegated Fabric access,
existing read tools and human-approved work-order cards. Persistent specialist
identities made role separation possible, but the browser still owned routing,
in-memory conversation/approval state and source execution.

Testing exposed problems beyond prompts: interrupted state, inconsistent response
formats, missing or stale sources, incomplete compound steps and long serial
round-trips. More routing instructions alone do not fix these problems. The
durable backend foundation addresses state and recovery; typed evidence and
deterministic validation address response integrity. Authentication, production
writes, live provider composition and performance remain separate work.

Agent Framework should have been assessed earlier once durable multi-step
execution became a requirement. The current correction is a migration in progress,
not a claim that the browser design has already been replaced.

## 2. What was changed, and in what order?

Commit IDs identify source milestones, not automatic proof of deployment.
Current changes are kept on **`feat/dibakar`**; this documentation update does not
merge into or push `main`.

| Period / representative commits | Implementation and correction | Evidence or remaining limit |
|---|---|---|
| Oct 7: `c9e870f`, `dcd9d33`, `a01523b`, `f48440d` | Aligned BAD/HOT semantics and shared response instructions, fitting the Data Agent prompt limit | BAD is latest signal quality; hottest is a ranking, not a fault threshold |
| Oct 7: `66094e5`, `a6b20aa` | Added supervised workflows, persistent agents, shared evidence presentation and editable WO approval cards | Agent proposals do not write SQL |
| Oct 7: `f64c127`, `695f2ee`, `d7e5827`, `9d21073`, `581fe05` | Corrected project-scoped native connections, Responses route/input contract and delegated consent/tool arguments | Definitions and connection setup are not runtime certification |
| Oct 7: `9a81e14`, `5cdf370`, `f7d62e3` | Added event-driven crew animation, correlated receipts, deferred page bundles and accurate Battle failure/queue states | Animation reflects execution events; it is not proof of successful work |
| Oct 7: `2d1fb34`, `e0ad6fb`, `c6d4a2a`, `c8edfe4`, `2057e77` | Hardened query arguments, named conversation flow, compound completion, work review and layout | Model mistakes and external failures still require explicit handling |
| Oct 8: `cf74231`, `03f886b`, `d044499`, `7d03d4a`, `946c743` | Rendered source-derived station values; enforced structured RCA; retained native claims separately; constrained draft priority | Unsupported diagnostic prose is excluded; causation remains unproven |
| Oct 8: `1f9c02b`, `1b597f3` | Added early assigned-capacity checks and documented the live inactive-capacity result | Deployment stops on an observed inactive capacity; it does not resume or replace it |
| Oct 8: `4cb39bd`, `b99a4f7`, `314a9ba`, `33e76ea`, `08e7dcd`, `75aa712` | Preserved inventory evidence, concise cards, requested inspection reads, native source selection and reserved required handoffs | Full compound runs exposed failures that narrower tests had missed |
| Oct 8: `d024940`, `d619241`, `77f3cd5` | Corrected operational dates, required chart output and real RCA reads, bounded final-report repair and rejected invented KQL aliases | Validation errors can be repaired locally; real source failures propagate |
| Oct 8: `1a1d88b`, `d846702`, `3db6439`, `89309b7` | Required complete direct populations, ontology instance reads and source-checked fleet/facility reconciliation | Native-selected subsets or schema-only results cannot satisfy independent verification |
| Oct 8: `12b23a0`, `8a9220b`, `ab9c4b6`, `3bab58c` | Removed self-referential native publication requests; decoded explicit JSON/business headers, literal node IDs and units | Missing/ambiguous native fields remain unverified, not guessed |
| Oct 8: `05583ca`, `4c0e1a1` | Recorded bounded fleet recovery and preserved actual displayed context for follow-up specialists | Hosted follow-up retained semantics but did not yet show a full old/new delta table |
| Oct 8: `a8a5e46` | Implemented isolated Agent Framework workflow, typed handoffs, durable journal, checkpoints and approval recovery | Local validation ledger only; no production WO creation |
| Oct 8: `c0dde3b` | Implemented live read-only Fabric identity, telemetry and paginated work-source boundary | Live SQL diagnostic stopped at disabled exchange; no empty-work fallback |
| Oct 8: `1f466ab` | Added real Sleuth adapter, schema readback, version pinning, evidence digest and invocation receipts; tied approval expiry to work coverage | Real telemetry-only RCA succeeded; complete live workflow remains blocked |
| Oct 8: `cc34d26`, not hosted | Added five-minute in-memory station comparison and shared strict KQL result parsing; fixed lost comparisons after additional reads and unsupported ambiguous-window verification | 62 targeted Node tests, 34 backend tests, app build and lint passed |
| Latest local source correction, not hosted | Preserved SQL's actual completion clock instead of the later combined read clock; rejected oversized pages | Final 63-test Node run and 11-test affected Python RCA suite passed; type/syntax checks clean |

The [acceptance report](../HydroOperationsApp/AGENT-ACCEPTANCE.md) retains
individual release results, failure examples and later targeted recoveries.

### Latest chart, authentication and Administration correction

**Subsequent universal presentation (deployed in 1.0.768 / `64e4d8f`):** every response
surface, including the legacy Data Agent panel, now uses the shared table/chart
renderer. Snapshot, asset, operational, station and row-returning native/future
tools are supported. Actual receipts override malformed model data; tables remain
visible with automatic charts, and links target exact tool receipts. Unambiguous
flattened Markdown and native CSV/JSON can render; ambiguous data is explicitly
diagnosed, not guessed. Invalid or partially streamed structured payloads are
not dumped into the narrative. Unknown units stay separated, missing cells are
not zero-filled, failed sources invalidate earlier evidence, and native/source
charts share the 12-panel bound.

Verification: 115 targeted Node tests, typecheck, targeted lint, environment
validation and production build passed. A synthetic nine-row BAD/UNCERTAIN
fixture rendered two real tables, four charts and two working source links.
Table-only mode retained all rows; narrow/wide layouts had no page overflow.
This is not a live-source or full orchestration pass. Existing production
large-chunk warnings remain. The initial hosted popup blocker has since
resolved; authenticated testing can proceed. At that checkpoint no Teams progress
message had been sent because self-chat creation was rejected. The existing
self-chat messages endpoint subsequently worked, as recorded below; this earlier
blocker is no longer current. App notification delivery remains unverified.

The first hosted 1.0.768 reproduction completed in 47.6 seconds and displayed
the source tables/citations correctly, but failed grounding: Chief claimed seven
turbines although the receipts contained eight distinct equipment IDs. The
follow-up fix replaces simple, Q&A-only snapshot prose with source-derived
summaries; compound RCA/native/work-review synthesis keeps its existing checked
path. Derived work counts stay in tables rather than creating redundant charts
for every measurement unit. The expanded 117-test suite, typecheck and targeted
lint passed. This failure is retained, not relabelled as an accepted run.
The correction deployed as 1.0.769 / `33167af`; its exact hosted rerun completed
in 36.8 seconds, retained all source rows and links, and generated eight
unit-separated measurement charts. The false count and follow-up menu were gone.
The broader compound matrix remains a separate acceptance gate.

### Stronger grounding and RCA presentation follow-up

Hosted scenarios 1 and 2 exercised four and five agents respectively (1m45s and
3m05s). They exposed a presentation defect: raw-source precedence suppressed the
application-checked RCA observation/hypothesis tables. The follow-up uses a typed
application trace activity to retain those tables. Model/native quoted tables do
not become verified merely because they appear in the same answer.

Chart calls now undergo deterministic source validation before display. Every
column/value must match a returned row projection, including row multiplicity,
or an application-derived record-count dataset. Mixed measurement units and
unknown-unit cross-signal combinations are rejected. The application generates
factual labels so a model cannot rename a measurement chart into a proven-fault
claim. Rejected charts have an explicit trace and bounded correction path.

Factual Q&A uses source-derived summaries. A factual turn with no executed source
evidence fails explicitly rather than presenting model prose as data. Non-data
capability/failure questions receive a qualified static explanation; they do not
certify source health. Native completion still requires its matching verified
execution receipt, but does not attest the native agent's internal query
provenance. Work-review summaries use typed outcomes, not unverified claims of
SQL creation.

RCA remains evidence-checked investigation, not automatic physical-cause proof.
References must resolve to actual returned data; unknown IDs/paths, invented
diagnostic thresholds and free-text confidence/diagnosis fields are rejected.
Competing explanations, contradictory observations and missing engineering
evidence remain explicit. Source availability, freshness, appropriate baselines,
approved limits and qualified engineering review cannot be manufactured by
prompting or by adopting Agent Framework.

The correction deployed successfully in 1.0.771 after a transient hosting
connection reset and a clean-tree retry. Hosted scenarios 1/2 retained actual
RCA observation and hypothesis tables. Scenario 3 exposed a separate defect:
telemetry charts falsely satisfied an orders-per-equipment chart request after
a rejected model chart. The next correction scopes that completion to actual
work-inventory counts and preserves other automatic source charts. Rejection
feedback identifies exact supported columns, including `record_count`.

Further local presentation hardening withholds provisional model prose while
keeping real tool/agent progress visible, unescapes literal Markdown identifiers
correctly, and expands structured RCA observations into field/value rows instead
of JSON blobs. The affected 138-test suite, typecheck and targeted lint passed.
These changes deployed as 1.0.772. Scenario 3 then completed in 2m30s and its
equipment chart matched all eleven returned work records (two each for T002/T008,
one each for seven other equipment IDs). Other automatic source charts remained.
This recovery is not yet a hosted matrix pass.

Broader 1.0.772 acceptance found a genuine filter bug: the model supplied
`value: "reorderLevel"` for a numeric low-stock comparison, and lexical fallback
returned twelve parts instead of the four at/below their row-specific limits.
The correction adds explicit catalog-validated `value_column`, disallows mixed
numeric/text ordering, and tests below/equal/above, missing quantities/limits,
zero, invalid columns and ambiguous operands. The six-file affected suite passed
177 tests plus typecheck/lint; this filter correction deployed in 1.0.773.
The subsequent 40-test tool suite includes an exact twelve-record stock fixture
that returns the four observed at/below-limit parts, including equality.
Native fleet comparison also remained explicitly incomplete because its returned
tables were not recognized. These runs are recorded as failures/limitations,
not converted to passes by finished agent badges.
The captured native payload used bullet records and omitted temperature OPC node
IDs despite the table request; these missing identities were not fabricated.

The subsequent presentation correction parses explicit labeled bullet records,
preserves every work order/None record, and creates unit-separated charts from
literal inline measurements. Ambiguous records fail visibly instead of being
guessed. A browser fixture of that actual captured response showed all 15 rows in
three tables and six charts, correct five-order status/priority counts, table-only
parity, and no wide/narrow page overflow. Native results remain labeled as not
independently verified.

The expanded reconciliation suite also exposed and fixed duplicate JSON ingestion
by generic and strict native parsers. Native JSON is now validated exactly once.
Literal BAD/work identities from supported bullet records can be compared, while
missing temperature node IDs remain explicitly unverified. All 200 affected
tests, typecheck and targeted lint passed. This follow-up deployed as 1.0.775.
A transient hosting connection reset failed the first attempt; the canonical
retry passed all backend/readiness checks and preserved all 39 redirects.
Browser reload still shows protected-hosting sign-in, and the operator was
unavailable to complete it. No hosting or authorization check was bypassed.

On refresh after deployment, the Fabric private-hosting session expired and its
sign-in popup was blocked. The operator was unavailable to complete normal
sign-in. No permission was added and no hosting protection was bypassed.
Consequently the latest hosted stock readback, final-candidate compound/follow-up
matrix and Battle/Data Agent parity remain unverified. Earlier successful runs
are not relabelled as acceptance of this build.

Teams progress delivery was subsequently recovered through the verified existing
self-chat messages endpoint. Updates at 18:22 and 18:38 UTC were accepted, with
the first read back successfully. The attached reminder is session-bound; it is
not a permanent notification service or evidence that app-generated operational
notifications are delivered.
Further progress updates were accepted at 18:51, 19:08 and 19:24 UTC. The attached
reminder is stopped while testing awaits operator sign-in; there is no continuing
unattended notification scheduler.
A further update at 19:50 UTC reported the additional native presentation/parser
fixes and fixture verification. Its temporary server/script were cleaned up;
session reminders are again stopped while hosted sign-in is unavailable.

The earlier chart/authentication corrections below were locally verified and
deployed in **1.0.766**; complete hosted-agent acceptance remains pending:

- Chart/table presentation consumes actual successful `query_telemetry`/`run_kql`
  receipts, not flattened model CSV. Raw evidence stays collapsed. Non-numeric
  data remains a real table; missing cells are never zero-filled. Signals/units
  remain separate, chart panels are bounded at 12, and all returned rows remain
  available in the table. Truncation, stale/unknown timestamps and failed reads
  are explicit. Pre-failure data cannot reappear through model prose or old charts.
- A chart complaint can be completed without another formatting-only model round.
  Native handoff claims and work/RCA outcomes remain in the checked compound
  response instead of being overwritten by a chart-only summary.
- MSAL reuses active/unique target-tenant accounts and cached tokens, including
  explicit Connect actions. Same-resource interactions coalesce; different
  resources serialize and retry silently first. Ordinary network/configuration
  failures propagate instead of prompting for consent. Discovery asks for read
  scopes; job execution and direct Data Agent execution request their own scopes.
- Administration labels the existing collapsed controls **Advanced agent
  settings**. Deployed defaults need no manual edits. Tool/catalog/prompt controls
  are optional restrictions, not Entra grants or work-order approval.
- The local Agent Framework contract now supports durable `no_draft` and
  `needs_clarification` outcomes, including replay and post-commit recovery.
  This does not implement the missing live Fixer adapter or production SQL path.

Validation: 106 targeted Node tests; all 42 backend Python tests; app build,
typecheck and lint. A local browser mounted the real dashboard with 250 synthetic
source rows: one SVG chart, 250 table rows, raw CSV initially collapsed, no
invented model value, and no horizontal overflow at a 390-pixel viewport.
Tab state was checked using DOM click dispatch; native browser pointer automation
stalled, so this is not a physical interaction or hosted-agent acceptance claim.
Source commit `c601e34` was pushed to `feat/dibakar`. The first canonical attempt
stopped at the initial tenant check. The supported local app's **Switch** sign-in
then completed, and the same canonical command deployed build **1.0.766 /
`059f6ee`**. All 39 redirects were preserved; agent versions remained Chief v9,
Gauge v11, Sleuth v13, Fixer v11 and Sparky v8. Both browser-equivalent preflights
returned HTTP 200, GraphQL POST returned 200, and the deliberately incomplete
token POST returned 400. Hosting verification was `protected-sign-in-gate`;
the deployer explicitly reported `INTERACTIVE_APP_ACCEPTANCE=not-performed`.
The integrated browser then reported "Please allow pop-ups and try again" when
opening the official Fabric sign-in broker. No hosting/authentication bypass was
introduced. Current-operator consent was verified; tenant-wide consent still
requires an authorized administrator.

The existing browser Rayfin SQL session still creates approved work orders; it
does not need an additional SQL consent for these changes. Backend delegated
exchange is a separate migration prerequisite and remains unchanged.
The earlier inference audience and the current persistent Agent Service audience
are different. Initial permission for the latter is not permission for every
specialist separately. Cached grants should be reused; MFA, revocation, genuinely
new resources and tenant policy can still require interaction. No new registration
or broader permission was introduced by this correction.

## 3. Which agents exist, when do they run, and how?

The five definitions below were read back during the latest recorded deployment.
They are **Prompt Agents**: persisted model configuration, instructions and tool
definitions, invoked through Foundry Responses. They are not continuously running
workers or five Agent Framework-hosted processes.

| Agent / deployed version | Invoke for | Tools and knowledge | Authority |
|---|---|---|---|
| **Chief**, `hydro-supervisor-agent`, v9 | Decompose a Foundry request; sequence required capabilities and review completeness | `delegate_to_agent`, conversation context, shared evidence policy, specialist results | Coordinates; no database write authority |
| **Gauge**, `hydro-qa-agent`, v11 | Facts, telemetry, existing work, inventory, charts and requested factual verification | `hydro_query` with catalog-governed STID/SQL reads, KQL templates and validation, station-power/quality snapshots and presentation tools | Read/visualize, not create work |
| **Sleuth**, `hydro-rca-agent`, v13 | Investigation, diagnosis review and RCA follow-ups | Read tools plus `complete_rca_assessment`; evidence references, competing hypotheses and missing-evidence rules | Proposes explanations, not proven causes or operational control actions |
| **Fixer**, `hydro-work-order-agent`, v11 | Explicit or conditional new-work review | Identity/coverage reads, `propose_work_order`, `complete_work_order_review` | Editable proposal or explicit no-draft/clarification result; no model-issued SQL write |
| **Sparky**, `hydro-fabric-iq-agent`, v8 | Explicit native Data Agent or selected-ontology retrieval | Separate `fabriciq-data-agent` and `fabriciq-ontology` connections using `fabric_iq_preview` | Native retrieval; cannot silently replace one source with the other |

The source catalog supplies schema/allow-list knowledge, **not embedded business
records**. TypeScript implements query validation, evidence checks, routing,
rendering and approval state. Python deployment scripts provision/read back
definitions; setup notebooks provision Fabric data, ontology and native agents.
No validated OEM/manual retrieval corpus or numerical causal model is attached
to Sleuth.

### Current hosted flow

```text
Operator -> browser -> Chief / Foundry Responses
  -> specialist invocation -> allowed source tools -> specialist result
  -> Chief -> next required specialist
  -> application evidence validation -> concise answer / editable cards
  -> explicit card approval -> application validation -> SQL Draft write
```

Handoffs return through Chief. This is not direct specialist-to-specialist A2A
transport or Foundry Workflows. The browser still dispatches direct tools under
the operator's identity; closing/reloading it loses in-memory conversation state.

**Example:** "Which turbines are BAD, what work exists, investigate the worst,
and prepare only uncovered work" routes Gauge -> Sleuth -> Fixer, with Chief
coordinating each return. Requested independent verification adds Gauge after
the review. A covered issue can correctly produce **no draft**.

### Native Data Agent and Battle

- A read-only question in Data Agent mode invokes that native agent directly;
  it is not automatically a Chief/Sparky workflow.
- Recognized WO requests use the explicitly labelled **shared Foundry approval
  path**, including both Battle entries. This is not two independent mutation
  implementations or proof that the native Data Agent creates SQL work.
- Battle preserves each execution's actual receipts, errors and approval state.
- Fabric IQ is appropriate for an explicitly requested published Data Agent
  answer or selected-ontology identities/relationships/native semantics. It is
  not required just because the question mentions SQL, assets or charts.
- A mixed native/direct comparison requires separate source receipts and
  independent direct inventories. Native failure stays a failure; a successful
  direct query cannot be relabelled as native execution.

## 4. RCA and work-order safeguards

### Scientific RCA

The intended sequence is symptom/identity/time -> measurement quality and
freshness -> comparable baseline and operating conditions -> competing hypotheses
with supporting/contradictory observations -> missing evidence and qualified
discriminating checks -> cautious conclusion.

Implemented safeguards require two to four hypothesis categories, real
current-turn receipt IDs and valid JSON-pointer references. The application
renders referenced values; free-text diagnoses and invented thresholds are not
accepted as evidence. Chief reviews workflow completeness; Gauge can recheck
identities, units, clocks and work coverage. Neither an additional model opinion
nor a valid pointer proves causal relevance.

The current conclusion is **cause undetermined**. Approved limits, matched
baselines, versioned procedures, relevant operating conditions and engineering
review remain necessary before stronger diagnostic claims.

BAD means data quality, not mechanical damage or downtime. HOT defaults to a
latest-raw-temperature ranking, not one-minute averages or an approved alarm
limit. Explicit operator windows/counts/thresholds remain significant. Station
power is a sample-weighted mean, not total generation or energy.

### Work analysis versus work creation

Keep simple "what work is open?" reads in Gauge to avoid an unnecessary model
hop. Separate complex work analysis from proposals as permissions/capabilities:
a future read-only Ledger can assess recurrence, coverage and backlog quality;
Fixer prepares the editable card. Only the explicitly approved application
transaction may create a SQL Draft.

Cards remain the actionable draft; duplicate narrative is reduced. Priority
comes from an explicit new-work instruction or defaults to Medium, not from a
High-priority filter on existing orders. The operator can edit it.
Identity/coverage are revalidated on approval. A write timeout is uncertain and
must not be blindly retried. Card rejection is tested; this acceptance work did
not perform a new hosted approved SQL write.

## 5. What Agent Framework now does locally

```text
Authenticated local request + idempotency key -> durable run journal
  -> ReadEvidence -> Investigate -> PrepareProposal
  -> persisted human approval -> reject OR atomic local validation outcome
```

The actual implementation imports Agent Framework `Executor`, `WorkflowBuilder`,
`WorkflowContext`, handler/response-handler decorators and `FileCheckpointStorage`.
The application adds typed immutable contracts and a SQLite journal.

- Completed stages recover from checkpoints; disconnect does not cancel a run.
- Approval is bound to a proposal digest, recorded decision and expiry.
- A completed work read is mandatory, including a valid zero-row read. Expiry
  cannot exceed 15 minutes after that read; recovery cannot renew stale coverage.
  SQL's completion clock is captured independently of parallel telemetry. Two
  minutes spent waiting for telemetry leave 13 minutes, not a fresh 15 minutes.
- Checkpoint-save failure stops further execution. Corruption is not silently
  skipped. Allowed checkpoint types are explicitly listed.
- Atomic local outcome/validation records cover the crash window around final
  workflow completion, without replaying the local operation.
- One process owns the state directory; concurrency is bounded to four workflows.
  Explicit retry is limited to two attempts; distributed recovery, automatic
  retry/circuit-breaker policy and production identity are not implemented.

### Live adapters and actual blockers

The read adapter verifies exact workspace/ontology/KQL identities, numeric
ontology generation 2, Eventhouse parentage and current AppBackend capacity.
It maps equipment/instruments/units through STID and validates KQL completion.
Work reads paginate with duplicate/cursor/completeness checks; only
Completed/Cancelled are excluded. Source failure does not become "no open work."

Live results:

1. Six T005 measurements were returned and explicitly marked stale.
2. The new hosted coordinator's Rayfin delegated SQL exchange returned
   **`EXCHANGE_NOT_ENABLED`**; the embedded app's working SQL/WO path is separate.
3. The inspected CLI token lacked delegated **`Item.Execute.All`**. Requesting
   that scope through Azure CLI returned **`AADSTS65002`**, a first-party client
   preauthorization restriction, not a proven customer-consent fix.
4. The supported Rayfin silent-login alternative had no usable cached login.
5. No production exchange flag, interactive Rayfin login, app-only fallback or
   browser-token extraction was used to bypass these blockers.

The independent RCA diagnostic verifies the persisted Sleuth schema, pins its
version and forces the structured completion tool. Foundry rejected request-level
`tools` with an agent reference; the correction uses the verified persisted tool.
The returned report uses the SPA's actual validator and retains a source-input
digest plus service response/request IDs.

The real Sleuth v13 call took **14.38 seconds**, with **2,392 input / 1,728 output
tokens**, on those six stale samples. This was an explicit telemetry-only call:
no SQL read, no proposal, no multi-agent success claim. Full invocation identifiers,
diagnostic commands and source prerequisites are in the
[service runbook](../HydroOperationsService/README.md).

## 6. Tests and performance: what the evidence says

| Evidence | Result | What it does not prove |
|---|---|---|
| Latest targeted Node run | **63/63** across orchestrator, source bridge and station comparison | Real cloud availability or ten hosted prompt passes |
| Latest local app validation/build | `validate-env`, TypeScript, Vite build and lint passed; large-chunk warnings remain | Cold-load SLA, deployed version or absence of all regressions |
| Last recorded backend suite | **34 Python tests**, clean Pyright and dependency check | Production identity, SQL exactly-once effects or distributed durability |
| Subsequent affected backend suite | **11 RCA tests**, including the added independent SQL-clock expiry regression; clean Pyright and syntax check | A new full-suite or live SQL acceptance run |
| Historical full hosted matrix, 1.0.752 | Ten compound first prompts plus two follow-ups; **not accepted** | Latest-build ten-of-ten acceptance |
| Targeted 1.0.756 fleet rerun | **3m54s**; eight BAD signals, five temperature ranks, six work records reconciled | Native execution provenance, fresh equipment condition or low latency |
| Targeted 1.0.758 chart/RCA and follow-up | **1m40s + 24.7s**; historical aggregation context preserved | Full numerical old/new comparison in the hosted UI |
| Real backend Sleuth call | **14.38s model time**, structured report validated | End-to-end source latency or multiple agents executing |

Local tests cover actual orchestration loops with mocked service boundaries,
invalid filters/envelopes, source failures, missing required steps, native-source
selection, chart/evidence consistency and history invalidation. Backend tests
exercise real checkpoint/SQLite/HTTP/process boundaries with synthetic providers.
These are different evidence classes; their counts must not be added up as live
acceptance.

The latest local station comparison derives old/new means, sample/BAD counts,
timestamps and qualified differences from source data without another model call.
Its active-SPA snapshot expires after five minutes and cannot survive chat reset,
failed refresh or changed source/configuration. It is not persisted browser data.
The extracted strict KQL parser rejects failed/partial/ambiguous envelopes in both
the browser generic query path and local source bridge.
Additional verification reads cannot discard the comparison. Multiple distinct
station datasets cannot be relabelled as a verified historical comparison through
model prose; separate source summaries and an explicit limitation are rendered.

UI corrections include deferred route bundles, an optional separate crew rail,
collapsed receipts, card-first proposals, accurate failure states and
reduced-motion support. A hosted 1200x900 check measured a 320px crew rail beside
807px messages with no overlap/overflow. Warm authenticated reload on 1.0.752 was
2090.4ms; route-shell readiness was 1374ms/914ms/31ms/358ms for
Overview/Telemetry/Maintenance/Intelligence. These are individual shell measurements,
not cold login, full data readiness or p95.

### The ten operator scenarios

Use the exact [copy-and-paste prompts](../HydroOperationsApp/AGENT-TEST-PROMPTS.md)
and the [per-flow historical results](../HydroOperationsApp/AGENT-ACCEPTANCE.md#latest-hosted-verification).

| # | Compound scope | Recorded 1.0.752 outcome / later qualification |
|---|---|---|
| 1 | BAD -> investigation -> gap review -> verification | Completed; no justified new draft; stale readings disclosed |
| 2 | Ontology identity -> direct evidence -> RCA -> work review | Ontology HTTP 500; targeted 1.0.753 rerun recovered |
| 3 | Native WO inventory -> direct verification/chart -> RCA -> gap review | Completed with eleven open orders and no duplicate draft |
| 4 | Native BAD/HOT/work -> independent reconciliation -> investigation | Native publication-request refusal; bounded comparison later passed on 1.0.756 |
| 5 | Ontology + native WO -> direct facility backlog/chart | Completed, eleven orders and facility counts 4/4/3; targeted later rerun recorded |
| 6 | Low parts stock -> risk investigation -> conditional work | Four parts retained; no invented BOM mapping or draft |
| 7 | Telemetry/work -> downtime investigation | No outage inferred from BAD or stale evidence |
| 8 | Notifications/work -> inconsistency investigation -> gap review | Empty scoped reads retained; no invented issue or send |
| 9 | Station chart -> scientific review -> follow-up verification | Initial chart matched; follow-up semantic qualification, later context fix; hosted delta comparison pending |
| 10 | Exact-title Low draft -> coverage check -> reject -> fault review | Card rejected/disabled; no saved-work or established-fault claim |

None of these rows certifies a new approved SQL creation or notification delivery.
The complete suite still needs one repeat on the same final hosted candidate,
including follow-ups and failure/recovery behavior.

## 7. Additional agents: recommended, not created

| Proposed capability | Trigger and purpose | Prerequisites |
|---|---|---|
| **Ledger - Work Analysis** | Complex recurrence, duplicate coverage, ageing and closure quality | Read-only WO/history/inspection evidence and consistent status semantics |
| **Steward - Maintenance Planning** | Assemble a feasible plan from verified findings | Qualified procedures, skills/crew availability, task dependencies |
| **Quartermaster - Spare Parts** | Check compatibility, availability and reorder risk | Authoritative BOM, stock/reservations, substitutes and lead times |
| **Scheduler - Downtime Planning** | Evaluate feasible outage/maintenance windows | Dispatch/load constraints, calendars, redundancy and duration estimates |
| **Relay - Notifications** | Communicate approved findings/work plans | Verified recipients, approved templates, durable outbox and delivery receipts |
| **Verifier - Evidence Assurance** | High-impact recommendations or conflicting sources | Deterministic evidence/policy checks; model critique only as an additional aid |

Prioritize work analysis and evidence assurance. Add parts/downtime planning only
when their authoritative integrations exist; stock rows alone are insufficient.
Do not introduce another LLM call for every ordinary factual query.
The existing **two Fabric Operations Agents** retain their playbook/actions,
Teams and email capabilities. These proposed specialists do not replace them,
and provisioning/configuration is not execution or delivery certification.

## 7.1 What is needed for a proper, evidence-based RCA?

This is a proposed RCA roadmap, not a claim that the integrations or diagnostic
validation below have been implemented. A better model or Agent Framework alone
cannot supply missing engineering evidence.

### Define the incident before diagnosing it

Capture an incident ID, verified asset/instrument IDs, observed symptom,
event start/end, time zone, operating mode and the question to be answered.
Distinguish a signal-quality problem, a measurement excursion, a production
change and a confirmed equipment failure. They are not interchangeable.

Historical RCA does **not** require historical readings to be fresh now.
It requires trustworthy, sufficiently complete data around the incident.
Current-condition claims separately require fresh reads. Neither a threshold
exceedance nor freshness alone proves a physical cause.

| Evidence needed | Purpose and minimum contract | Current boundary |
|---|---|---|
| Incident-window historian data | Before/during/after readings, original timestamps, sampling interval, units, quality, missing periods and ingestion clocks; suitable resolution for the phenomenon | Latest/scalar telemetry reads exist; their presence alone does not establish sufficient historical coverage or resolution |
| Instrument integrity | Calibration history, sensor identity/location, range, maintenance, gateway/ingestion health and clock synchronization | Instrument metadata exists; calibration and ingestion diagnostics need authoritative integrations |
| Operating context | Synchronized load, dispatch, operating mode, starts/stops, relevant environmental/process conditions and alarm/event sequence | Existing telemetry/weather are not a complete control-state or sequence-of-events record |
| Comparable healthy baseline | Reviewed known-good periods for the same asset or approved comparable assets, matched by load/mode/context, with coverage and variability | No engineering-validated matched-baseline contract is configured |
| Approved criteria | Versioned manufacturer/engineering criteria with applicable asset/model, unit, regime, effective dates, approver and provenance | No approved diagnostic-limit source is configured; numerical limits must not be invented |
| Asset dependencies | Authoritative equipment/instrument/process relationships with source identity and effective configuration | Use verified v2 ontology/native relationships where available; never fabricate topology from names or missing links |
| Maintenance and inspections | Work scope, actual findings, measurements, completion evidence, component changes and chronology relative to the event | Open work and inspections exist; open/Draft/Planned status is not evidence that a repair or inspection occurred |
| Independent corroboration | Relevant inspection/test evidence, independent measurement, or a separately validated diagnostic method | It must be supplied or integrated; a second agent repeating the first is not independent evidence |
| Confirmed past cases | Reviewed failures and healthy counterexamples, with known causes and resolution evidence | Needed to evaluate diagnostic accuracy and abstention; synthetic fixtures are insufficient |

### Investigation workflow to implement

1. **Scope and source checks:** resolve authoritative identities; bind every read
   to the incident/window; report unavailable sources and quality/coverage gaps.
2. **Deterministic evidence extraction:** build a synchronized event timeline,
   calculate source-backed features/changes, compare approved matched baselines
   and preserve methods, units, parameters and exact contributing records.
   A model must not invent intermediate calculations.
3. **Competing explanations:** assess instrumentation/ingestion, operating
   conditions, sampling effects and physical condition separately. Record
   supporting, contradictory and missing evidence for each. A reference must
   both exist and be relevant; pointer validation alone checks only the former.
4. **Discriminating evidence plan:** state which missing observation would
   distinguish candidates and which approved source/test could supply it.
   Unavailable integrations remain evidence requests, not imaginary tool calls.
5. **Independent verification:** check scope, calculations, contradictory evidence,
   chronology and source completeness with deterministic checks and qualified
   engineering review. Another LLM is supplementary, not the acceptance authority.
6. **Controlled disposition:** retain "undetermined" when candidates cannot be
   distinguished. Any physical testing, maintenance or operational change follows
   approved engineering procedures and the existing human-approval boundary.
7. **Closure and learning:** capture approved inspection/remediation results,
   whether the symptom recurred, the final reviewed cause and links to all
   evidence. Re-evaluate earlier hypotheses rather than treating a saved WO as
   confirmation.

### Separate outcome states

| State | What may be claimed | Promotion requirement |
|---|---|---|
| Observed anomaly | An actual source-backed deviation or quality issue in a stated scope | Valid data/identity, coverage disclosure and a reproducible comparison |
| Supported hypothesis | A candidate consistent with selected evidence, with uncertainty retained | Relevant corroboration, explicit alternatives and contradictory checks |
| Engineering-reviewed probable cause | A qualified engineering judgement, not an LLM certainty score | Approved review criteria, sufficient corroboration and an accountable reviewer |
| Confirmed cause | A reviewed causal conclusion within a stated boundary | Approved confirmatory evidence and documented disposition under the diagnostic policy |
| Undetermined | Available evidence does not distinguish candidates | Remains a valid outcome; record the specific blocking evidence |

These are proposed typed workflow states. The current application does not
implement these promotion gates and must not relax its "cause undetermined"
boundary merely because a model selects a stronger label. Numerical confidence
requires a validated, calibrated method and evaluation evidence; it is not the
model's self-reported confidence.

### Prioritized delivery

- **P0 - trustworthy inputs:** verify ingestion/source health and timestamp
  integrity; define incident scope; inventory available historical data and
  engineering-owned criteria. Preserve historical evidence even when live
  freshness fails.
- **P1 - a narrow asset/incident pilot:** integrate approved baseline/context/
  inspection evidence for one agreed failure mode; implement reproducible
  calculations and an evidence-completeness matrix.
- **P2 - reviewed RCA cases:** add typed case state, hypothesis/evidence links,
  reviewer decisions, versioned diagnostic policy and closure evidence to the
  durable service. This is separate from the existing WO SQL schema.
- **P3 - production acceptance:** evaluate reviewed real incidents and healthy
  counterexamples, stale/missing/conflicting sources, calibration drift,
  cross-asset confusion, recovery/replay and tenant isolation. Measure false
  assertions, unsupported recommendations, missed incidents, abstention,
  provenance coverage and end-to-end latency. Engineering/product owners must
  approve the thresholds before a readiness claim.

The next discovery decision is the pilot asset/failure mode and the authoritative
owners/availability of historian, operating-context, baseline and inspection
data. Implementation and validation duration depends on those inputs and access
approvals; a credible production RCA schedule cannot be inferred from UI test
duration. Adding more agents before these contracts exist increases complexity
without establishing a cause.

## 8. Deployment and local operation

There is one deployment engine, with two supported operator interfaces:

1. From the repository root:

   ```powershell
   python Raw\workspace-reset\deploy_fabric_app.py `
     --tenant <tenant-guid-or-domain> `
     --workspace <workspace-guid-or-name> `
     --push-config
   ```

2. Launch [Start Fabric Demo.cmd](../Raw/workspace-reset/Start%20Fabric%20Demo.cmd),
   select the target and **Deploy app**. The local UI calls that same orchestrator.

Use `--client-id` only for a supplied registration or ambiguous discovery.
The orchestrator owns Node 24, environment validation, source/SPA discovery,
agent provisioning/readback, backend/schema settings, publication, supported
live-auth setup, redirect preservation and endpoint/CORS/POST checks. Do not
assemble a separate npm/Rayfin/Entra deployment sequence. Require `SUCCESS` and
`DEPLOYED_APP_URL`, then validate actual runtime behavior.

Manual prerequisites remain explicit: authorized Fabric/Entra setup, any exact
administrator action reported by live-auth setup, data seeding/ingestion, and
selected v2 ontology **Manage graph -> select eligible entities/relationships ->
Continue -> Materialize**. Graph binding must come from authoritative item
lineage or an operator-verified explicit fallback; no established public
ontology-owned materialization REST endpoint is assumed.

Source data, native topology, agent runtime and notification delivery are separate
gates. Local SPA development is not deployment. The local Python service is not
published by this command and must not be exposed as a ready enterprise backend.
Follow [DEPLOY.md](../HydroOperationsApp/DEPLOY.md) for the full runbook and the
[local launcher guide](../Raw/workspace-reset/README.md) for its UI.

## 9. Remaining production migration gates

1. Approve and implement supported backend delegated/OBO authorization, including
   SQL exchange, scopes and item permissions. The observed Azure CLI restriction
   cannot be solved by assuming another login or customer admin consent will work.
2. Finish live proposal/provider composition and required native-source adapters.
   Keep source identity, failure propagation and structured evidence intact.
3. Add authorized production SQL idempotency and uncertain-write reconciliation;
   local SQLite exactly-once validation is not remote transaction proof.
4. Choose approved durable hosting/storage, retention, tenant isolation and recovery
   guarantees; add bounded retry/circuit-breaker and operational observability.
5. Connect the SPA to durable run IDs, progress, reconnect and approval endpoints.
   Preserve concise cards and actual agent invocation receipts.
6. Restore/verify ingestion freshness and required operational integrations.
   Do not present stale source readings as current health.
7. Complete authenticated hosted chart/comparison checks, then repeat all ten compound flows,
   follow-ups, controlled write tests and relevant failure/recovery tests on one
   candidate. Benchmark repeated cold/warm loads and end-to-end latency.
8. Add notification outbox/delivery verification and complete enterprise consent
   and physical microphone testing where required.
9. Integrate any new backend hosting/authentication into the canonical deployer.
   Do not create a second deployment path.

**The overall enterprise-grade implementation remains incomplete.** The evidence
above records concrete progress and blockers without claiming zero bugs, diagnosed
physical causes, complete native execution provenance or delivered alerts.

## 10. Implementation and operating references

- [Foundry runtime/authentication/tool guide](foundry-copilot.md)
- [Acceptance evidence and routing policy](../HydroOperationsApp/AGENT-ACCEPTANCE.md)
- [Ten operator prompts](../HydroOperationsApp/AGENT-TEST-PROMPTS.md)
- [Agent definitions](../HydroOperationsApp/src/services/copilot/agentDefinitions.ts)
- [Browser orchestrator](../HydroOperationsApp/src/services/copilot/foundry.ts)
- [Tool execution boundary](../HydroOperationsApp/src/services/copilot/tools.ts)
- [Agent provisioner](../Raw/workspace-reset/provision_foundry_agents.py)
- [Canonical deployment orchestrator](../Raw/workspace-reset/deploy_fabric_app.py)
- [Local service runbook](../HydroOperationsService/README.md)
- [Agent Framework workflow](../HydroOperationsService/src/hydro_orchestrator/workflow.py)
- [Typed service contracts](../HydroOperationsService/src/hydro_orchestrator/contracts.py)
- [Live sources](../HydroOperationsService/src/hydro_orchestrator/live_sources.py)
- [Version-pinned RCA adapter](../HydroOperationsService/src/hydro_orchestrator/foundry_rca.py)
