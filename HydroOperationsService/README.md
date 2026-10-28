# Hydro durable orchestration: local and hosted validation milestones

## Native answer fidelity

The hosted semantic workflow sends the original user question unchanged to the
pinned Fabric IQ agent. It accepts exactly one selected-source tool call and
displays the original Data Agent `response` without table reconstruction or an
additional briefing model for native-only answers. The audit retains the raw
Foundry response and a SHA-256 digest of the original answer.

Mixed retrieval and work-order workflows also display that original Markdown
unchanged and fully expanded beside the editable approval cards. Native result-file
projections remain available to evidence checks and the audit, but are not rendered
as a second, sparse table. No additional briefing model is invoked or replayed when
an original native answer exists, including mixed draft workflows.
Verified prose-only answers are native evidence too; the workflow does not require
or invent a business-record table to accept them.
Independent source tables and explicitly
requested charts retain their existing presentation and exact source values.

A tool-processing diagnostic is not itself a content-filter refusal. A diagnostic
can be accepted only when Foundry completed successfully, the final reply matches
the source answer, and explicit final content-filter decisions report no block.
Missing safety decisions, actual filtering, source failures, changed questions,
multiple tool calls, and rewritten answers fail explicitly. No filter is disabled
and no failure falls back to direct tables. Legacy structured evidence workflows
retain their existing schema contracts.

The Data Agent keeps one ontology context and the operational SQL execution
adapter. An ontology-only capability test exposes reference and telemetry queries
but not operational SQL records, so the SQL adapter is not a redundant source.
Multi-source requests use independent source-native queries and exact canonical
key matching instead of requiring a federation tool. Telemetry freshness uses
query execution UTC time, not the newest available reading in an old snapshot.

## Agent access and current source policy

The existing sign-in session acquires delegated source access implicitly.
Consent or an expired sign-in can still require the identity provider's
interaction; there is no separate per-agent connection workflow. A failed
source preflight never invokes the agent or replays an approval.

The semantic planner selects ontology-backed Fabric Data Agent execution for
cross-source business questions and explicit native requests. Exact governed
reads and independent checks use direct tools. Native source failure never
silently changes authority. Historical matching ("had BAD") retains matching
samples separately from the latest state.

## Current minimal-agent execution architecture

The production controller combines **a tool-less structured planner**
with deterministic Microsoft Agent Framework `Executor` / `WorkflowBuilder`
stages. It is not a manager agent chatting with every specialist. The semantic
decision selects capabilities, explicit source reads, conditions and answer
requirements; code validates the catalog, identities, permissions, evidence,
budgets and human-approved writes. Legacy phrase-based compatibility routing
still exists; it is not the production semantic planner.
Normally planning is one model call. A typed/catalog validation rejection permits
one feedback correction within the same 60-second planning budget, before any
source execution. Both candidates are immutable audited receipts; recovery
reuses committed attempts and the supervisor reports the actual model-call
count. Repeated invalid plans fail closed rather than triggering source or
write retries. Missing planning configuration fails before opening a client.

| Capability | When it is needed | Execution boundary |
|---|---|---|
| Fabric IQ | Ontology relationships, synonyms and cross-source business facts | Verified native Data Agent Responses tool; **no intervening Fabric IQ prompt model**. Original answer and SHA remain unchanged. |
| Q&A | Exact direct reads, adaptive source queries and explicitly requested independent verification | Deterministic reader when arguments are known; scoped query agent only for adaptive retrieval. No RCA or maintenance-authoring skills. |
| Weather | Forecast/observation facts and vendor/issue/time-aware comparisons | Reader by default; one optional tool-less briefing for requested interpretation. Scoped Weather prompt agent only for genuinely dependent/adaptive queries. |
| RCA | Requested fault investigation and competing evidence-backed hypotheses | Separate reasoning agent with checked, source-referenced structured assessment. Not a compulsory telemetry-quality or drafting stage. |
| Work Order | Requested maintenance review or editable draft preparation | Separate scoped reasoning agent and deterministic coverage/draft validators. No direct production-write tool; approval remains a separate authenticated application boundary. |

Registered capabilities are not mandatory invocations. Existing compatibility
prompt agents are retained, not deleted or substituted for the native tool.
Only selected **agent-mode** capabilities require remote agent/toolbox version
readback. Reader-only plans need no specialist metadata or model credentials
for retrieval; unused Weather/RCA/Work Order/native resources cannot block them.
Selected immutable versions remain pinned across recovery, and a changed plan
cannot silently expand a saved manifest.

Consecutive fully specified, independent reader steps share one Framework
superstep, capped at **four concurrent source reads**. RCA, native calls,
selection-dependent work and approval gates are never parallelized against
their dependencies. Failure cancels and drains sibling reader tasks before
returning; partial evidence cannot become a successful answer. The whole run,
including planning, catalog/version discovery and presentation, has an absolute
ten-minute execution budget measured from its immutable request clock.
Credential-admission lease time is not a per-stage execution deadline.
Native Responses calls use their bounded call timeout inside that absolute
execution budget, not the remaining admission lease.

Semantic native execution preserves the provider's original answer and typed
artifacts under the selected native evidence contract. It does not impose the
legacy BAD-since-midnight quality/work JSON schema on arbitrary freshness,
UNCERTAIN, historical-window or diagnostic questions. Provider errors,
immutable source identities, requested equipment coverage and separate
maintenance/approval checks still fail closed.

Models see only tool schemas permitted by their pinned role and current turn.
Unrequested chart tools and unauthorized proposal tools are omitted; callbacks
and the shared bridge independently enforce permission. `analysis_requested`
is a typed semantic choice, not an automatic second model after factual
retrieval. Simple facts, counts, charts and pure draft staging do not need a
rewriter. Reader-only weather comparison can request interpretation without
introducing a Weather agent loop.
Planning and briefing HTTP clients are closed explicitly on success, failure
and cancellation rather than leaving their connection pools to garbage collection.

The execution audit includes the accepted capability/provider manifest,
version pins, limits and persistence boundary. The UI shows only participating
capabilities, distinguishing application readers from model calls. Agent
Framework's warning that registered-agent tool schemas are omitted from the
request is expected: schemas are verified on the remote agent definition;
local `FunctionTool` callbacks provide dispatch, not replacement declarations.

**Enterprise boundary:** current receipts, checkpoints, locks and recovery use
the hosting session's filesystem. This supports recovery within that session,
not independent-replica failover, global admission control or a distributed
exactly-once scheduler. Linked Application Insights and response IDs do not
prove trace ingestion or establish a latency SLO. Cross-replica persistence,
centralized admission/retention, verified OpenTelemetry export and sustained
load/quality evaluations remain production rollout requirements. No new cloud
resources, diagnostic export, data ingestion or operational writes are enabled
implicitly by this hardening.

This design follows Microsoft's [agent-versus-multiagent decision guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns),
[explicit workflow execution model](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/builder-and-execution),
[checkpoint storage guidance](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)
and [workflow observability guidance](https://learn.microsoft.com/en-us/agent-framework/workflows/observability).
Older dated deployment sections below are historical evidence, not the current
architecture or a certification of those outstanding enterprise requirements.

The opt-in live architecture evaluation uses the **existing** project/model,
calls no business source tools and creates no agents or work orders. Set
`HYDRO_EVALUATE_LIVE_PLANNER=1`, `HYDRO_FOUNDRY_PROJECT_ENDPOINT` and
`HYDRO_FOUNDRY_PLANNING_MODEL`, then run
`python -m unittest test_semantic_planning.LiveSemanticArchitectureTests -v`
with this service's `src` and `tests` on `PYTHONPATH`. It checks ten representative
paraphrased/negated intents, native fidelity routing, reader-only forecasts,
diagnostic versus fault-gated drafting and clarification. The ordinary suite
skips real-model calls.

## October 26 current audit

Latest publication: coordinator **v23**, signed app **1.0.1121 / `1673261`**.
The [v23 signed-browser record](../docs/foundry-multi-agent-implementation.md#october-27-v23-publication-and-signed-browser-retest)
includes an initial model timeout, a 75-second RCA/work-order review and a
36-second diagnostic-only draft follow-up. Native/Weather acceptance remains
blocked; these runs are not a production latency guarantee.

See the [current end-to-end failure audit and repair](../docs/foundry-multi-agent-implementation.md#october-26-end-to-end-failure-audit-and-repair).
The deployed Gauge failure was an overly strict Kusto left-join aggregate validator,
not an absent Agent Framework workflow. The source layer now accepts zero-match
string absence without weakening positive-match, timestamp or identity checks.
Ordinary operational questions use governed connectors/Eventhouse tools; explicit
native selection is retained across the Python/Node admission boundary.
Function loops stop on the first real callback error; one local input correction
remains available. The official SDK allows one transport retry per model HTTP
request, honoring provider wait/backoff while retaining existing tool-result
messages rather than restarting the agent. Native MCP transport keeps zero SDK
retries and its separate same-source correction limit for non-refusal contract
or identity errors. Provider refusals, including moderation, stop immediately:
they are never retried, repaired by another prompt, or routed to direct sources.
Service timeouts and the
ten-minute workflow deadline remain enforced; exhausted retries still fail
explicitly. This does not replay source callbacks, proposals or production writes.

Node connector reads use an explicit 60-second HTTP deadline within the
120-second source RPC budget: measured authorized connector responses exceeded
the SDK's previous 30-second default. Failures retain safe timeout/HTTP/GraphQL
codes through fleet aggregation, never empty-work substitutes. Native error
envelopes and output-moderation responses cannot become successful domain
evidence. SQL reconciliation uses the operational connector client; approval,
mutation timeout and no-replay guards are unchanged.
Fleet snapshots retain every selected source row within the existing bounded
inventory/read and 262,144-byte callback limits; a model text budget must not
silently shrink immutable evidence. Truncated or unqualified fleet snapshots
fail closed. Grounded answer context uses the same 500-row bound as certified
answer tables, rather than clipping a verified fleet to its first 80 signals.
Before RCA, the executor fulfills required governed evidence instead of leaving
population reads until final answer verification. RCA scope honors deterministic
selection and resolved named equipment before a fleet population, so a named
investigation cannot freeze an empty scope or widen to unrelated turbines.
Native validation failures log only the Foundry response ID for authorized
provider-receipt diagnostics, not raw source output or credentials.
Native refusals retain the safe `native_refusal` category through streamed,
non-streamed and detached execution, persisted failure/result recovery, and
the hosted UI. The UI requests source-owner review instead of claiming a
connectivity problem; it never displays arbitrary provider error text.
Refusal-bearing partial rows are also rejected, not certified.
No-RCA work-order handoffs preserve verified direct evidence and source
population/provenance metadata. Keep the pinned deterministic workflow and existing project.
Historical version/test counts below are not this release's runtime certificate.

## Current architecture and October 25 bounded review

Supervisor is already a **deterministic Microsoft Agent Framework workflow**, not
a manager prompt agent. It compiles the request, dispatches the required pinned
specialists, verifies evidence and presents the result. Dependent QA/RCA/work-order
steps remain ordered; independent evidence reads can run concurrently. Specialists
and the optional grounded briefing use models, not the routing controller.

The FEATURE review verified Hosted coordinator **v11**, QA **v2**, RCA **v2**,
Work Order **v3**, Fabric IQ **v2** and Weather **v1**, including the verified
generation-2 ontology and new operational SQL source. Exactly three browser
prompts were run: maintenance analysis succeeded; the native Data Agent's joined
quality/work bundle remained incomplete and failed closed; Weather/Q&A correctly
disclosed empty weather sources and skipped the unsupported affected-work check.
This is not full multi-agent acceptance or delivery/write certification.

The app-side follow-up keeps the warm hosted container on **New chat**, while
clearing follow-up context and invalidating every previous approval callback.
Target/source changes still reset the container, and app builds retain separate
session identities. Crew labels distinguish application workflow/tool activity
from Foundry events and no longer count the deterministic controller as an
additional model invocation. These app changes were initially blocked by tenant Microsoft Graph authorization.
The October 26 canonical app-only deployment subsequently published **1.0.1076**
from **`e899a6a`**, with normal browser build verification, connector/runtime
readiness checks and all 40 redirects preserved. The native joined-output and
missing Weather acceptance gates remain unresolved; publication does not make
them successful.

See the [bounded review and exact limitations](../docs/foundry-multi-agent-implementation.md#october-25-bounded-feature-workflow-review)
and the [deployment guide](../HydroOperationsApp/DEPLOY.md). Keep Foundry setup
separate from app publication; no framework migration or new project is needed.

## Current FEATURE connector deployment

The connector-retirement baseline ran SPA **1.0.1036 (`d676b9e`)** and Hosted Agent **v4** in
`ai-project-hydro-foundry-884a839f3132`; Weather is `hydro-weather-agent:1`.
Subsequent deployments and authenticated acceptance are tracked in the
[live FEATURE verification record](../docs/ontology-test-review.md#latest-canonical-deployment-and-bounded-runtime-acceptance).
The legacy `Hydro_STID_API` was removed after authenticated hosted Lakehouse/SQL
source reads, with all 40 other Fabric items preserved. Post-deletion SPA connector
reads also passed. The [deployment record](../docs/ontology-test-review.md)
separately records model HTTP 429 failures, incomplete native Data Agent coverage,
stale telemetry and the still-unaccepted compound business workflows.

## Scoped Data Agent quality/work workflows

Explicitly selected native BAD-quality/work requests documented in the
[routing contract](../HydroOperationsApp/AGENT-ACCEPTANCE.md#complete-routing-rules)
use one Sparky step against the verified ontology-connected Data Agent, followed by the
requested RCA and/or work-order specialists. Native evidence is preserved across
handoffs, including when no RCA ran or RCA cited a native table instead of a
direct `/rows` receipt. The validated native bundle is not authoritative SQL
work-coverage attestation: fresh SQL coverage, identity checks and human approval
remain mandatory for drafts. Native failure never silently switches to direct fleet retrieval. Ordinary
operational questions instead start with Gauge's governed source tools, preserving
historical quality matching and complete work coverage across paraphrases.
Weather remains a separate read-only specialist.

Validate the structured quality/work contract across the complete native step,
not separately against each intermediate Data Agent call. Accept exactly one
unambiguous complete bundle; do not combine conflicting bundles or promote
intermediate prose into evidence. Preserve all raw native receipts in the
principal/run-scoped immutable journal before validation, including rejected
responses, without logging their contents. Missing JSON or incomplete source
coverage can consume the existing single same-source correction budget. The
coverage correction requests an ungrouped scalar telemetry count for the exact
UTC window, separately from grouped BAD matches. Malformed/conflicting bundles,
inconsistent counts, unbounded results and exhausted corrections still propagate.
The budget is shared with existing native transport/identity corrections, not an
additional retry loop. Sparky must forward the
complete assignment and its machine-readable contract, with the exact as-of time,
rather than substitute the original compound operator question.
The JSON adapter removes only Fabric's exact `File(s) result_N.json ... were
generated as a result of the query.` transport footer before parsing a complete
JSON value. Arbitrary surrounding prose is not promoted to evidence, and the
original response, including the footer, remains in the immutable audit.

The published work-review schema bounds `reason` to 1000 characters and explicitly
distinguishes `needs_clarification` for unavailable condition/identity data from
`no_draft`, which still requires complete work-source coverage. An empty telemetry
window is never a healthy-equipment finding or permission to invent a target.

This describes code behavior; publication and authenticated runtime acceptance
must be recorded separately.

For the full October 7-8 implementation history, agent roles, hosted/local
distinction and production gates, see the
[Foundry and multi-agent implementation record](../docs/foundry-multi-agent-implementation.md).

## Historical v77 canonical-prompt release

Hosted Agent v77 is active from commit `30ee16e` on immutable image
`sha256:f8568200db0a496e2aa95f86651e7619f5c860f8727d3cb1e42c9192c5fb04da`
(`validated-20261012070135`, ACR run `dt2f`). It retains verified ontology
generation 2 and the separately signed, human-approved production-write path.

The release closes the browser-stream recovery gap without hiding source
failures. The first NDJSON frame binds the server-assigned run ID; if the client
disconnects, the same-session server run continues and persists its certified
answer or failure. A same-principal, same-source `result` operation reports
running state or returns that exact terminal receipt, and the SPA recovers it
without starting another orchestration. Work-order decisions remain
non-retryable. Persistent connection failure is rendered as an uncertified
failed turn, active crew states terminate as errors, and charts, suggestions
and approval cards are removed. Battle comparisons now always run serially to
avoid shared Fabric/Foundry source pressure.

Validation passed 170 backend tests plus 80 subtests under Node 24.21.0, Pyright
with zero diagnostics, 388 application/source-contract tests, TypeScript,
ESLint, environment validation and the production build. The Fabric app
`1.0.874` from the same commit deployed through the canonical orchestrator,
preserved 39 redirects and passed AppBackend GraphQL/token preflight and POST
readiness. Authenticated ten-prompt acceptance is still pending because the
private hosting session returned the protected sign-in gate after deployment;
no CLI token or authentication bypass was used.

The executable application contract now enumerates all ten canonical prompts
and verifies required specialists, minimum invocation counts, native sources,
chart intent and work-order priority. An explicitly final independent check
requires a second Gauge phase, preserving the documented
Gauge -> Sleuth -> Fixer -> Gauge route.

## Historical v74 release

Hosted Agent v74 is active from commit `258b2e5` on immutable image
`sha256:507a364ff9f8c4516f9de4a47a6f81145476d4a42fec123b87be446e21594aae`
(`validated-20261011154234`, ACR run `dt2c`). It retains live ontology
generation 2 and the separately signed, human-approved production-write path.

The release passed all 167 backend tests under Node 24.21.0 and Pyright with zero
diagnostics. It repairs one model-output mutation observed in canonical Prompt 1:
an RCA evidence ID with a duplicated terminal suffix. Repair is allowed only
when exactly one current-run evidence ID is a prefix and the appended fragment
is already that ID's terminal suffix. The repaired report still passes the
existing strict evidence-ID, JSON-pointer and scalar-size validation.

Authenticated v73 Prompt 2 had already verified the new centralized evidence
presentation: real ontology equipment/facility/instrument instance tables,
explicit zero open-work coverage with timestamp, a prominent explanatory
summary, no physical-fault claim and no production write. The v74 Prompt 1 rerun
is pending because the private Fabric SPA refresh session expired. Tokens issued
to Azure CLI were correctly rejected by the SPA-client binding and were not used
as a substitute.

## Current v72 release

Hosted Agent v72 is active from commit `35f46cc` on immutable image
`sha256:9acfa1fea4bd17ddf611c3418b48bd43ae67eb263d5bbc8760078a599f35345a`.
It requires live ontology generation 2 and has the signed, human-approved
production write path enabled. Agent tools still cannot write SQL: Fixer stages
an in-memory proposal, and only the separately signed decision request can
create and read back a record.

The v72 release passed 164 backend tests under Node 24.21.0 and Pyright with zero
diagnostics. A fresh authenticated Prompt 2 run completed Sparky, Gauge, Sleuth
and Fixer in 3m43s, staged one editable T005 inspection draft, and created no SQL
record; backend rejection disabled the card. v72 resets the single schema
correction budget for each of the two bounded Foundry completion attempts. v71
added one fail-closed same-agent retry when Sparky selected the wrong configured
native connection. Neither change substitutes direct tools for native execution.

The Fabric app remains `1.0.863` from `f91c20f`. Its work-order decision serializer
sends only `title`, `description` and `priority`. Production acceptance created
and independently read back `WO-92c4bdaa-a28f-5fd5-8142-682740eaa423` for
`EQUIP_RTI_T009`; creator identity remains available in the signed decision
response but not in the ordinary `work_orders` query schema.

## Current production boundary

This service is the implementation packaged in the Foundry Hosted Agent
`hydro-orchestrator`. Hosted Agent v66 runs the Microsoft Agent Framework workflow
for Chief, Gauge, Sleuth, Fixer and Sparky. The Fabric app is a thin authenticated
client: it submits bounded requests, renders backend-owned NDJSON execution events
and certified answers, and collects explicit human work-order decisions. It does
not run a browser-side supervisor or specialist tool loop.

The active v66 image is
`sha256:ee4088286b30d0f53c7f70a2b63cee0be0ec5616850bf17a2fba6ad7c08d8920`
(`validated-20261011202000`, ACR run `dt25`, source commit `a582502`). The source configuration digest is
`210d1ce53296ddb304613a7dcb87b22ee7a017073ef48b23e7fba7fcfa82644b`.
Production writes remain disabled in the Hosted Agent. Work-order proposals are
editable drafts until an explicit, separately validated human approval; no
acceptance test created production work.

The v66 supported-runtime regression passed all 156 backend tests and Pyright
with zero errors or warnings. The application boundary passed 172 application
contract tests using the required `tsx` loader, TypeScript, ESLint, environment
validation and production build. Fabric static deployment
`deploy-20261011093808-7f2cf801` was published through the canonical orchestrator,
which passed AppBackend CORS, browser preflight and POST-readiness checks.

Long agent/provider calls have no application wall-clock timeout. When no new
output arrives for 15 seconds, the runtime re-emits the same active running event
ID and specialist identity; it does not create a fake handoff. Grounded narrative
projection permits one schema-only repair using the same immutable receipts and
stores rejected projection evidence. Native identity extraction accepts explicit
identity fields, bullets and named Markdown equipment-ID columns. Gauge is a
source-resolution capability and cannot be skipped merely because a preceding
native response used an unrecognized prose shape.

Final live Prompt 2 on v65 completed Sparky -> Gauge -> Sleuth in 170.3 seconds,
explained the T005/Sloy/system/six-instrument identity, disclosed empty six-hour
telemetry and open-work reads with timestamps, created no proposal and performed
no write. The ten-workflow matrix and affected reruns are recorded in
[AGENT-ACCEPTANCE.md](../HydroOperationsApp/AGENT-ACCEPTANCE.md). Native/provider
latency remains outside an accepted SLA: one Data Agent call took 790.6 seconds
and one Sleuth follow-up stalled for about ten minutes while heartbeats preserved
the stream.

Rendered battle-page certification is still separate from Hosted contract
acceptance. The integrated automation browser could not complete Fabric private
hosting's popup/PKCE cookie handshake; cached shell assets rendered, but an
uncached lazy chunk correctly returned `401 Authentication required`. A normal
private-hosting sign-in is still required before claiming production UI rendering.

v60 corrects a capability-classification gap exposed by the standalone request
`Chart average power output per station over the last 24 hours.` Visualization
imperatives now require Gauge source verification when they target the declared
Hydro domain. The rule is generic for `chart`, `plot` and `visualize`; it does not
route on station power or exact prompt text. Live v60 acceptance completed
Chief -> Gauge with HTTP 200, three source rows, one station/MW chart, source-owned
aggregation/freshness semantics and no write. The longer investigation workflow
and its same-session exact-value verification also passed.

Grounded presentation uses a bounded tool-free typed projection to put a direct
conclusion, evidence meaning and material uncertainty ahead of supporting
source-annotated tables. Every nontrivial certified answer requires that
explanation. Row-count-only summaries are invalid and receive one bounded repair;
a second invalid projection fails closed rather than certifying an incomplete
table-only result. The immutable-receipt validator still resolves every table
cell, and deterministic certified-empty signal-quality results avoid a model call.
Oversized previous displays now retain a bounded grounded conclusion and source
limitations instead of returning HTTP 409; every factual follow-up still requires
fresh source reads. Narrative summaries are limited to three to five complete
sentences and 600 instructed characters.

The Supervisor no longer exposes a planning tool. A deterministic TypeScript compiler derives the ordered
plan of one to eight specialist steps from the question; the backend validates and executes
those steps serially and propagates immutable evidence and completed reports.
Ingress persists centralized `required_evidence_kinds` and
`permitted_evidence_kinds` manifests derived by the TypeScript domain semantics,
validates that every required kind is permitted, and supplies both immutable sets
to the compiler before execution. The compiler adds
any omitted required requirement and compatible view before execution, while
unrelated datasets and malformed or unsupported contracts still fail closed with
one bounded correction. The same TypeScript boundary produces strict
object-enveloped analytical execution plans for signal-quality, temperature,
station-power and grouped-backlog evidence. Verification reuses existing receipts
when they satisfy the operator's explicit quality, window, rank, equipment-type
and grouping constraints; otherwise it performs the missing governed read
deterministically without another model conversation. Catalog requirements remain
deterministically fulfilled through their governed readers. Cached handoffs cannot
replace an already restored evidence identity with a different receipt.

Final certification
after RCA/work review is deterministic application validation over attested receipts
and validated specialist contracts; it does not add a latency-prone duplicate model
call or claim that another agent ran. Prompt-specific
runtime routing shortcuts and the open-ended Chief function loop are removed.
Chief v17, Gauge v16, Sleuth v18, Fixer v16, Sparky v13 and the Hosted Agent
projection runtime use `gpt-5.6-sol`.

The final signed-browser Prompt 10 v22 acceptance passed historically on app version 1.0.800.
The primary work-order draft completed in 1m31s and was rejected by the backend
without a write. Its same-chat RCA follow-up completed in 46.6s through Chief,
Sleuth and Gauge, retained two literal source rows in two tables, created no
second proposal, and displayed the validated no-fault/undetermined conclusion
with explicit evidence gaps. It does not certify v23. The current app 1.0.803
deployment passed readiness, and direct Chief invocations verified all canonical
plans plus the BAD/UNCERTAIN and T010 RCA regressions. Protected hosting then
required normal sign-in, so current delegated-source/UI acceptance remains pending.

## Thin-client contract

The deployed native binding retains the version-pinned `hydro-fabric-iq-tools`
Foundry toolbox as a verified connection manifest, not an additional agent or
execution hop. The deterministic Agent Framework workflow verifies that binding,
resolves the selected Data Agent from its connection, and sends the exact question
to the published Fabric Data Agent's native Responses interface. It does not call
the MCP query tool or the Fabric IQ prompt-agent wrapper on this path. This is the
primary native Data Agent transport, not a fallback or a direct-table reader.
The observed MCP runtime declined ordinary cross-source questions; the official
[Fabric Responses client](https://github.com/microsoft/fabric-samples/blob/main/docs-samples/data-science/data-agent-sdk/responses-api/responses-api-notebook.ipynb)
executed the same published ontology's sandboxed cross-source combine successfully.
The deployed AppBackend origin is validated by live source discovery against its
workspace, AppBackend and capacity; native execution rechecks the workspace's current
capacity before using that origin. Capacity drift is a hard failure requiring the
canonical deployer, never a guessed host. This avoids the private workload-routing
token operation that rejected the SPA lease in hosted acceptance. Standalone SDK-style
diagnostics without a verified origin retain the SDK's discovery route.
Generated capacity hosts are never copied between targets or cached globally.
The signed-in user's existing verified Fabric, Foundry and Power BI token leases
authorize discovery, connection attestation and native execution respectively.
No managed identity fallback or new consent screen is introduced. Managed toolbox
execution policies are not claimed for this separate Responses transport: source
authorization, native read-only execution and application approval gates remain explicit.
Native result documents and provider execution traces stay in the immutable audit;
dependent specialists receive the unchanged answer and typed evidence at their original
paths, without duplicate transport envelopes. The legacy BAD-since-midnight
quality/work workflow retains its strict completeness gate; semantic requests use
their explicit native evidence contract instead of that phrase-dependent schema.
For the legacy projection, typed evidence is selected by the exact executed
queries in one successful, nondegraded combine receipt. Artifacts from failed attempts
remain in the audit but cannot pollute the verified selection or be stitched together
to imply complete coverage. Conflicting artifacts for the same read remain a failure.
Telemetry-quality investigation drafts use verified quality/coverage evidence and a
fresh duplicate-work check; they do not automatically require a mechanical RCA or
unrelated inspection/spares reads. Fault-contingent repair still requires checked RCA.
Connector failures retain credential-free source labels and codes across the worker
boundary, including `TOKEN_EXCHANGE_FAILED` from the SDK's brokered sign-in exchange.
The SDK sanitizes exchange status; that code alone does not establish a permissions
problem. An Active capacity can still reject requests with `CapacityLimitExceeded`;
retries, identity changes and
partial-source answers do not establish readiness.
Structured completion validation uses the same path-preserving evidence projection:
RCA receives its verifiable source rows, while the Work Order review-schema validator
does not receive unused receipts. Cached handoffs use the same validation path.
Complete native artifacts remain in the audit and never inflate the bounded Node
validation request.
Native Responses datasets expose actual typed result rows, never result-document
envelopes or schema markdown from provider tool traces. The original source answer,
documents and provider trace remain unchanged in the audit. Dependent analysis can
cite the row projections without turning file metadata into a business-data table.
Date-like column names alone do not establish a timestamp or a timezone. Native
dates without an explicit timezone are displayed as unchanged text; only
timezone-aware source strings receive timestamp presentation.
The Data Agent's notebook-owned cross-source guidance distinguishes all-parent
count rollups from unmatched-population queries. Zero-inclusive counts require
left-outer aggregation and distinct child keys, not an anti-join. Federation
questions express that positive aggregation intent; source-specific status
filters remain in their sub-questions. The app still sends the original user
question and preserves the native answer unchanged.
The standalone Fabric IQ prompt agent is retained for compatibility but
is not an extra model hop in the app. Setup converges the toolbox onto the stable
connection, preserves existing policies/skills/metadata, and verifies publication.

For a Fabric Data Agent-only answer, the supervisor executes one native handoff
and returns the original tool `response` without a Q&A handoff, narrative projection,
analysis-model call, Markdown escaping, or answer truncation. A sole native step
receives the original operator question rather than a planner rewrite. The answer
retains the source receipt ID and a SHA-256 digest of the original UTF-8 text;
the digest verifies text integrity, not factual accuracy. Prepared-answer recovery
revalidates this same source text. Structured-only native results retain their
existing table path, and explicitly requested charts still use validated source
records. Combined workflows display the unchanged Fabric Data Agent answer
separately from Hydro workflow analysis and approval cards. Source errors,
content-filter failures, moderation-processing failures, and work-order approval
gates remain enforced; wrapper prose never replaces a failed native receipt.
Expected MCP compatibility-path discovery, transport and delegated-authorization failures are unwrapped
from SDK task groups and reported with their execution phase and correlation ID.
Execution diagnostics retain safe status/error codes, not native answer data or
tokens. Discovery-only diagnostics retain the bounded service reason with
credentials and URLs redacted, before any user question is sent to the source.
Compatibility-path hosted toolbox requests authenticate with the agent's runtime credential and
forward the current invocation's `x-agent-foundry-call-id` unchanged. The platform
uses that context to resolve the signed-in user for the Data Agent connection.
It is not a generated trace ID, is never saved in run state, and is cleared with
the request's delegated lease. Missing hosted caller context fails before MCP
discovery; it never falls back to another identity.

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

The packaged source runtime also owns a typed domain contract for authoritative
cross-source relationships and operational semantics. Specialists receive that
contract with the tool catalog instead of reconstructing joins from prompt prose.
`query_work_backlog` is the reusable open-work capability for equipment or facility
grouping: it reads complete SQL/Lakehouse inventories, excludes Completed and
Cancelled work, retains unmatched identities, conserves source rows, and returns
attested count units plus a source-defined chart projection. The Python boundary
validates those claims before recording evidence. This capability does not add a
browser-side planner or replace the generic catalog tools used for other questions.

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

**Live evidence, October 11:** Hosted Agent v59 on immutable image
`sha256:60972cfa8d2bc0f03236804b0409798ecc2bce0cc3ab7a419338b6ee517bb7a7`
uses Chief v20, `gpt-5.6-sol`, ontology generation 2 and disabled production writes.
The backend-owned capability and minimum-step manifests prevented incomplete
compound plans and required separate Gauge handoffs for explicitly quantified
independent populations. Native
T005 identity propagated from exact Ontology fields into direct Gauge and Sleuth
handoffs; a conditional Fixer branch was skipped only after RCA found no supported
uncovered issue. The station-power prompt rendered one source-certified MW chart,
and same-chat verification reported exact values, sample/BAD counts and freshness.
The ten-prompt v59 matrix returned HTTP 200 for every primary prompt. Prompt 4
completed Sparky -> Gauge -> Gauge -> Sleuth even when both direct populations
were empty; the earlier v58 omission remains recorded as a failure.
Application execution has no wall-clock deadline; source/network/external limits
still fail explicitly.

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
  admission lease are verified before constructing a supervisor. The lease proves
  that credentials are valid when work starts; it is not an execution deadline.
  Actual source permissions
  are still enforced by the source services; this does not certify the new
  hosted delegated SQL path. The embedded app's working SQL/WO path is separate.
- State is namespaced by signed-in principal and complete configured source
  identity. The factory cannot substitute another state namespace, source or
  CLI/managed-identity credential. Returned answers must match the invocation.
- Raw tokens are not passed into model context or receipts. Validation responses
  never include raw Pydantic inputs. Leases are cleared after success, source
  failure, upstream network timeout or cancellation.
- The Hosted supervisor, every specialist handoff and the final narrative
  projection have no application wall-clock timeout. Streaming continues until a
  certified answer or explicit failure, and browser disconnect cancels abandoned
  work. Individual network/source operations remain finite and external Foundry
  or token-expiry limits still propagate explicitly.
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
  requires the verified independent `operational_sql_database_id` and uses the
  delegated writable `hydrooperations` connector for operational reads and approved
  writes. The retained original app-owned database is not an operational fallback.
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
- Failures are logged and recorded explicitly, not converted to empty evidence.
  Explicit retry is limited to two attempts;
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

The same read-only `hydrolakehouse` connector used by the SPA resolves exact equipment
and instrument IDs/units, with complete pagination and duplicate-identity rejection.
No separate `Hydro_STID_API` is discovered, configured or provisioned. The source
fingerprint pins the selected Lakehouse ID; live discovery verifies its SQL analytics
endpoint identity. AppBackend GraphQL and delegated Power BI tokens remain for
operational SQL reads and approval-gated writes. The KQL query
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
HTTP errors, upstream service limits, incomplete output, narrative answers and
unexpected/duplicate calls fail explicitly.

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
