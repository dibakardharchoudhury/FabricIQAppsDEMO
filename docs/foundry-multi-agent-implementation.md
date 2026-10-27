# Foundry and multi-agent implementation record

This record covers the original **October 7-11, 2026** implementation and later
dated follow-ups, corrections, measured results and unfinished acceptance, grounded
in repository commits and linked records. It is not a release certificate.
Earlier runs remain historical evidence, not passes after a later code change.

## October 26 end-to-end failure audit and repair

The signed FEATURE browser on build **1.0.1112 / `36b08ed`** reproduced the compound
BAD-quality/open-work/draft failure in about **30.8 seconds**. Run
`b2a843c8-479c-4b0d-ba1a-d2469967db8c` failed inside Gauge's source callback:
`Matching-quality aggregate is missing, conflicting, or outside the verified observation window.`
Read-only Eventhouse reproduction established the root cause: Kusto's unmatched
left-join string is `""`, not `null`. The zero-match verifier rejected normal source
output; telemetry quality and the multi-agent framework were not fabricated.

The repair accepts that string absence only for a zero count with absent matching
numeric/date fields. Positive counts still require bounded, ordered timestamps,
finite matching values and the requested quality. The latest sample remains
separate from historical matching samples; `"had BAD today"` does not mean
`"latest BAD"`. Browser and hosted reads share the same query/validator and fixed
source-read UTC window. Missing readings, zero matches, stale readings, incomplete
metadata/work coverage and source outages remain distinct.

Ordinary operational requests use governed Lakehouse/SQL connectors and Eventhouse
tools through Gauge. Exact-sentence automatic Data Agent routing has been removed.
Explicit Data Agent selection is preserved through admission and follow-ups;
native-only intent cannot acquire an inferred direct-source workflow at the
executor boundary. Mixed native/direct requests still retain their requested
verification stages. Draft handoffs cover the affected evidence set and retain
complete SQL coverage, canonical identity, physical-fault uncertainty and human
approval gates. A real callback failure now ends the Framework function loop on
its first error; the separately bounded argument-correction response remains.

**Architecture conclusion: keep the existing deterministic `WorkflowBuilder`.**
Microsoft's [workflow patterns](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/)
allow explicit ordered/concurrent dependencies; a Magentic or group-chat manager
is not required. [FoundryAgent](https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/agent-services/foundry)
uses the pinned remote agent's instructions/tool schemas and local callback dispatch.
The SDK warning that supplied tool declarations are omitted is expected, not the
failure; per-run tool overrides are not a repair. The active Fabric specialist
already uses [`fabric_iq_preview`](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq),
not the deprecated classic Fabric tool. Native Data Agent preview execution has
service/JSON-completeness limitations and is not an enterprise SLA. Its failure
must not become a successful direct-source fallback.

The audit verified existing coordinator **v16** and QA **v2**, RCA **v2**, Work Order
**v3**, Fabric IQ **v2**, Weather **v1** in the already selected Foundry project,
all using `gpt-5.6-sol`. No new project, model deployment or framework migration is
needed. Local validation passed **101** Node source/routing contracts and **189**
Python supervisor/service/auth/source tests under Python 3.12 and Node 24.21.0
(the two isolated Docker-closure tests were rerun after correcting their missing
operational SQL fixture/configuration). Pyright reported zero diagnostics;
TypeScript, changed-file ESLint, environment validation and production build passed.
Unit/type/build results and publication/live acceptance are separate gates.
The workspace's `fabcapswc` capacity was checked **Active**. Weather remains blocked
by the invalid Aurora subscription credential/private-vault access; changing
authentication headers or fabricating empty weather as successful ingestion is not a fix.

### First publication exposed a separate model-rate boundary

Canonical publication of `0e5a2d5` completed with `SUCCESS`/`DEPLOYED_APP_URL`,
all three connector/runtime preflight checks and all **42** existing SPA redirects
preserved. It reused all five prompt specialists, building only the changed
coordinator image `src-d0ca478878436eed` (ACR run `dte`) and activating **v17**.
The signed browser verified app **1.0.1113** and both source connections.

The original compound request then completed Gauge but failed in Fixer at
**36.4 seconds**: run `fed6e899-28d0-4967-960b-cd50cfef1759` received
`rate_limit_exceeded` HTTP 429 from Foundry. The existing DataZoneStandard model
deployment reports **60,000 tokens/minute and 60 requests/minute**. This is not
the repaired Kusto failure and was not certified as success or draft creation.

The follow-up repair keeps direct evidence in a no-RCA work-order handoff,
projecting only verified selected equipment when available and preserving source
population/provenance metadata. Empty scope retains its evidence rather than
silently projecting it away. Complete immutable receipts are not mutated.
The first follow-up's rate-limit recovery honored a numeric provider `Retry-After`/`retry-after-ms`
of at most **60 seconds**, once, inside the existing completion budget and run
deadline. It emits explicit application progress and reuses captured evidence.
There is no retry after a completed assessment or staged proposal, no production
write replay and no quota/resource change. Missing/invalid/over-budget retry
headers, hard insufficient quota, repeated throttling and source failures still
fail explicitly. The full **110-test** supervisor suite and Pyright passed;
publication and live acceptance of this follow-up were separate gates.

Coordinator **v18**, image `src-39224ae5f38120fd` (ACR `dtf`) and signed app
**1.0.1114 / `bad7b55`** were published successfully. The original request again
completed Gauge. Run `52a2b87b-a0a0-45c1-8a08-6d51359e6ec7` failed at **53.2 seconds**:
Fixer honored the provider's **15.660-second** wait, its next model call succeeded,
then the following call hit another 429. No draft/write was certified.

The corrected transport policy uses the official OpenAI SDK's **one retry per
model HTTP request**, rather than restarting the whole agent. A throttled
follow-up therefore retains that request's existing function-result messages;
earlier source callbacks and completed assessments are not replayed. Requests
remain bounded by the service timeout and ten-minute workflow deadline. Native
MCP transport keeps **zero SDK retries**, preserving its separate shared
same-source correction limit. This follows Microsoft's
[rate-limit guidance](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/quota#rate-limit-best-practices)
on provider retry headers and bounded backoff. Persistent errors still propagate;
no quota, project or model deployment was changed. All **111** supervisor tests
and Pyright passed, including exact retained request messages and one source
dispatch after a follow-up 429. The additional **83** service/source/authorization
tests also passed.

### v19 publication and signed-browser acceptance: not passed

The canonical deployment of **`89c7dd4`** returned final
`SUCCESS`/`DEPLOYED_APP_URL` on October 26 at **22:36:59 UTC**. The signed browser
verified app **1.0.1115 / `89c7dd4`**. Coordinator **v19** is active on
`src-ad9a38b9fbbba1da` (ACR `dtg`). All five specialists were reused unchanged,
all three connectors were reapplied, and all **42** existing redirects were
preserved. Neither the project/model nor quota/capacity size was changed.

Authenticated session `6e8dd994-227e-4f40-943d-34d1dd791551` produced these results:

| Request | Observed outcome | Acceptance |
| --- | --- | --- |
| Original BAD-today/open-work/draft question | **69 seconds**; run `750f5b18-ec8e-4ddd-992a-82e54cd0b0a6` failed in Gauge with equipment metadata, instrument metadata and work-order inventory `READ_FAILED`; no partial fleet snapshot was accepted. | **Fail**; no handoff, RCA, draft or write certified. |
| Direct equipment inventory only | **61 seconds**; run `4107e374-ed74-468b-90eb-bf58428dac95` failed with `Rayfin Lakehouse asset connector read failed (READ_FAILED)`. A later bounded diagnostic repeat also failed in **63 seconds**. | **Fail**; isolates a connector read problem independently of compound planning. |
| Explicit published Data Agent identity/configured-source request, without direct tools | **51.2 seconds**; Supervisor selected Sparky alone. The answer reported the native endpoint unavailable with an output-moderation error, no verified identity/source details and zero scalar claims. | **Not a source-success pass**. The crew displayed Finished, but the answer itself disclaimed verification. Native-only routing was preserved; there was no direct fallback. |

These are real failures, not evidence that the SDK retry policy passed live
multi-agent acceptance. The source failures prevented the planned QA -> RCA ->
Work Order acceptance; repeatedly submitting it would not certify anything.
Source reads must recover before that acceptance is attempted again.

The browser initially had 31-second aborted connector requests, then successful
Lakehouse transport responses taking roughly **42-45 seconds** before STID
connected. These timings are diagnostic observations, not proof of the hosted
read failure's underlying cause. The current sanitized `READ_FAILED` does not
distinguish timeout, GraphQL rejection and other transport failures; that cause
remains unresolved. A separate Azure CLI identity's broker exchange returned
401 and is **not comparable authenticated browser acceptance**.

ARM independently confirmed `fabcapswc` **Active / Succeeded** during this
investigation. No capacity resume was necessary. The maintenance panel's
sign-in/empty placeholders likewise are not proof that operational SQL contains
zero work orders. No approval/create/save action was taken. Weather still
requires the valid Aurora credential through authorized private Key Vault
access. **Do not describe this publication as enterprise-ready or fully fixed.**

### Connector deadline and native-failure follow-up

Further diagnosis used the **actual app-delegated Power BI credential**, without
printing/storing credentials, replacing browser sign-in with CLI tokens, or
changing authentication configuration. Its broker exchange succeeded HTTP 200.
Normal, authorized one-row reads returned HTTP 200 and a real row from **both**
the Lakehouse and operational SQL connectors, consistently taking **40-43
seconds**. Introspection was denied with `HC0046`; that policy was not bypassed.
The published entity read APIs were used instead.

The Node SDK client's default was **30 seconds**, shorter than those observed
responses. Browser background execution can delay its timers, so browser
`AbortSignal.timeout(30000)` returning a later successful response is not proof
that a Node 30-second deadline is sufficient. The hosted read client now
explicitly uses **60 seconds**, inside the existing **120-second** source RPC
budget. A mock-clock regression proves the actual SDK request remains live at
40,000 and 59,999 ms, aborts at exactly 60,000 ms and is not replayed.
Timeout, HTTP status and GraphQL failures now retain safe `READ_TIMEOUT`,
`HTTP_<status>` and `GRAPHQL_QUERY_FAILED` codes through fleet aggregation rather
than collapsing back to `READ_FAILED`; raw provider details are not exposed.

Inspection also found production reconciliation constructing a non-connector
`RayfinClient` before using `client.connectors.hydrooperations`. It now constructs
the proper connector client. A real SDK/HTTP-fixture test verifies the connector
route, exact creation identity and read-only reconciliation without another
mutation. Mutation timeout/approval/reconciliation safeguards are unchanged.

Native error envelopes (`error`, `isError`, `is_error`) now fail before becoming
domain evidence; output-moderation wording is recognized as refusal rather than
a successful native claim. These checks preserve legitimate empty-count answers
and equipment whose business status is Unavailable. Structured provider errors
fail immediately without direct fallback; existing bounded same-source refusal
handling remains. No moderation controls were disabled.

Validation passed **196 Python tests**, **105 Node contracts**, Pyright and
environment validation. The canonical publication then returned final
`SUCCESS`/`DEPLOYED_APP_URL` at **23:30:34 UTC**: coordinator **v20**, image
`src-e72ed4907d5e592a` (ACR `dth`), signed app **1.0.1117 / `6b7ec1c`**.
All five specialists were reused and all **42** redirects preserved.

### Complete source evidence follow-up: acceptance not yet passed

Authenticated session `413e72bd-56be-414c-aebb-9c3f573c424d` completed the original
BAD-today/open-work/draft question through **Gauge -> Fixer in 91 seconds**.
Pending draft cards were staged; no create/approval/reject control was used and
no production write was executed. This is progress, **not an acceptance pass**:
the answer showed only **44** verified signal rows and correctly warned that it
could not substantiate all turbines in the staged population.

The shared fleet shaper still applied `truncateForModel` to the actual source
receipt, repeatedly halving wide rows until JSON fit 40,000 characters. That
removed source evidence, not merely display text. A separate 80-row operator
context bound would also omit the tail of a complete 89/90-signal population.
The repair preserves all selected bounded fleet rows, leaves existing source
inventory/read and 262,144-byte callback limits in place, and rejects truncated
or unqualified snapshot attestations instead of certifying them complete.
Evidence descriptors now honor the source's completeness flag. Grounded answer
context shares the certified table's 500-row bound. No absent rows are inferred
or certified and immutable receipt indices remain unchanged.

Regression checks cover a >40,000-character, 90-signal/15-equipment fleet,
complete operator-context rows with unchanged receipts, and rejection of both
truncated and missing-completeness snapshots. All **197 Python tests** and
**107 Node contracts** passed, with zero Pyright diagnostics, TypeScript/Vite
production build, environment validation and changed-file ESLint.
Canonical publication returned final success on **October 27 at 00:05:23 UTC**:
coordinator **v21**, image `src-2959fa46a1da34ec` (ACR `dtj`), signed app
**1.0.1118 / `f660a39`**, all five specialists reused and 42 redirects preserved.
The authorized `fabcapswc` capacity had paused; it was resumed and verified Active.

Signed session `6f760307-9b7f-464a-84b9-804bbc180af8` proved:

| Request | Actual result | Gate |
| --- | --- | --- |
| Original question after UTC midnight | 33.6 seconds; 0 today's samples, all 90 mapped signals missing in that new window, no drafts; explicitly not a healthy-telemetry claim | Boundary behavior passed |
| Same compound intent over last 24 hours | Gauge -> Fixer, 82 seconds; rendered source table contains 89 rows across all 15 turbine IDs, 12 work records, 15 unapproved diagnostic drafts; stale readings distinguished from historical BAD samples | Full-fleet coverage passed; no writes |
| Named T005 investigation/RCA | 83 seconds; Gauge/Sleuth finished, but the history bundle was absent and no diagnostic draft staged | RCA not accepted |
| Native-only facility count | 42.9 seconds; actual native error envelope rejected, Sparky/Supervisor marked Needs attention, no certified count, fallback or write; run `b6ccf45e-e5ea-49dd-86fc-054416b27a63` | Native source availability not passed |

### RCA prerequisite and scope follow-up

The named RCA exposed two concrete coordinator defects. Governed contract
fulfillment ran only after specialists, so freezing the RCA scope could precede
the required quality snapshot. When that population was absent, named equipment
was ignored; when present, it could also override a narrower named/deterministic
selection with unrelated fleet units. Three repro tests failed before repair
(including empty and over-broad named scope, deterministic selection and actual
workflow ordering).

The executor now fulfills governed requirements before RCA selection/history.
Scope prioritizes deterministic selection, then resolved named equipment, then
the requested analytical population/prior selection. Existing per-run frozen
scope, eight-equipment detail bound, source/permission checks and conditional
work gates remain. **200 Python tests** and Pyright passed. Native rejection now
logs a response ID, without raw provider output or credentials, for authorized
receipt diagnostics.

### October 27 v22 publication: operational RCA passed, native provider blocked

Canonical publication of **`bb184aa`** returned final
`SUCCESS`/`DEPLOYED_APP_URL` at **00:36:23 UTC**, after **721.750 seconds**.
The signed browser verified app **1.0.1119 / `bb184aa`**, coordinator **v22**
on image `src-b850ab2a1ec1e3fc` (ACR `dtk`). All five prompt specialists were
reused, all 42 redirects preserved, and all three connector/backend readiness
checks passed. The assigned capacity remained Active.

Signed session `00ecc4ad-7c2d-47cb-9560-40bc73a3e12b` asked which T005 signals
had BAD quality in the last 24 hours, requested RCA/open-work review and a
diagnostic work-order draft only where uncovered. **Gauge -> Sleuth -> Fixer
all completed in 97 seconds.** The answer used T005's actual six-signal quality
history, inspections, notifications, compatible spares and empty open-work
inventory. It distinguished historical BAD samples from five latest GOOD
signals and one UNCERTAIN signal; probable causes remained unconfirmed
assessments. One **T005-only**, Medium-priority diagnostic draft was staged.
No approval/rejection/create control was used and no production write occurred.
The complete 89-row signal-quality source table was retained.

The answer explicitly disclosed that the diagnostic history uses a 48-hour
context but only one hour has source data, readings are about five hours old,
and no independent measurement, ingestion-health record or approved diagnostic
limit confirms a physical cause. This is a source-backed RCA, not a certificate
of current turbine condition or full-period telemetry coverage.

Native-only facility count remains **blocked**, not passed. A separate signed
session `6dbb19a5-cecf-4445-aa96-a30cae170c34` failed in **42.2 seconds** with
an actual provider error envelope; run
`09bce05b-42b2-47f0-bf65-bc13e1842655`, response
`resp_048617c0ce336967016adff2d67a34819087c878a02a1b7d14`.
Sparky and Supervisor correctly showed **Needs attention**, with no certified
count, direct fallback or write. Supported stored-response retrieval returned
404 and was not treated as source evidence.

An independent Azure CLI diagnostic invoked the **existing** native specialist
with the same benign facility-count request, no new agent, and zero SDK retries.
Its actual MCP receipt contained an **output-moderation marker**, not facility
records (response `resp_009f71fa563f62fc016adff3c911c88194b0fb3c84b25f26cc`).
That different-client diagnostic is not signed-browser acceptance. No prompt,
model, policy or moderation control was changed to route around the refusal.
The Fabric Data Agent/service owner must review that provider-side refusal
before native business acceptance can continue.

Operational connector QA/drafting and the three-specialist RCA are now verified.
**Overall acceptance is still incomplete:** native provider execution is
blocked; Weather separately still requires the credential owner to replace the
invalid Aurora subscription key in the private vault and rerun authorized
Weather ingestion. Neither blocker is hidden by a deployment success or a
model-only answer.

## October 26 publication and main-merge readiness

Tenant Graph authorization recovered. The canonical **app-only** command published
FEATURE build **1.0.1076** from **`e899a6a`** and returned `SUCCESS` and
`DEPLOYED_APP_URL` after **665.625 seconds**. Both source connectors applied;
AppBackend preflight/POST readiness and preservation of all **40** redirects passed.
Prepared Foundry definitions were verified read-only, without agent publication.
Energy read-model checks found **12 source layers, 9 polygons and 75 asset-message
links**. Normal browser sign-in independently confirmed the deployed build,
STID connected, 90 stale signals, and a populated native graph with operational
context. No additional agent prompt or work-order decision was submitted.

The graph used one native query, but this browser's Fabric `executeQuery` request
took **49.310 seconds**. Warm navigation returned in **213 ms** without another
query. The earlier 6-8 second observations are not a service-latency guarantee;
first-load performance is still capacity/service dependent. See the
[graph evidence](knowledge-graph.md).

The request to merge into `main` is conditional on full end-to-end readiness.
That condition is **not met**, so neither `main` nor the PR states were changed:

- Exact PR **#19** tip `593b14571d4ff6ed28b37d4c41e05ad38a9451a6` and PR **#20**
  tip `67fd898e96d84281156986d315d04ec17197a586` are ancestors of the feature
  branch. Incorporation is verified; runtime success is separate.
- Both PRs report `REVIEW_REQUIRED` and `BLOCKED`. Main requires one approving
  review and approval of the most recent reviewable push. No protection bypass
  or administrative merge was attempted.
- Fresh Weather history still has no successful recovery: the October 25
  15:20 UTC run failed with Aurora HTTP 401; the subsequent 21:20 and October 26
  03:20 scheduled runs failed with `CapacityNotActive`. The assigned capacity is
  **Active now**, and deployed Weather notebook executable source matches checkout.
  No known-failing ingestion job was manually repeated.
- The October 25 native Data Agent joined-output failure and missing forecast
  acceptance remain unresolved. The existing three-prompt limit is preserved.

Publication has recovered; full functionality, native joined-output acceptance,
Weather ingestion and required independent PR approval have not. The dated
October 25 publication-blocked statements below describe history, not the
current hosting state.

## October 25 bounded FEATURE workflow review

**Recommendation: retain the existing Agent Framework workflow.** Supervisor is
the deterministic hosted control plane, not another LLM manager. The actual
`WorkflowBuilder` pipeline is request/plan compilation -> bounded specialist
handoffs -> independent evidence verification -> grounded presentation. QA -> RCA
-> work-order dependencies are deliberately ordered; independent RCA source
reads already run concurrently. Version-pinned specialists, typed contracts,
source/principal isolation, immutable receipts, checkpoints, bounded same-source
correction and separate human-reviewed writes are retained. Specialists and an
optional final grounded briefing use models; routing does not add a manager-model
round trip. Replacing this with a free-form manager/group chat would not fix the
observed Data Agent output contract or missing Weather ingestion.

This matches Microsoft's distinction between
[workflow capabilities](https://learn.microsoft.com/en-us/agent-framework/workflows/)
and [sequential/concurrent/handoff orchestration patterns](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/).
Agent Framework is already pinned at core `1.19.0` and Foundry integration
`1.13.1`; no framework, model or dependency upgrade was performed.

### Verified deployment context

The read-only canonical verifier passed for `ws-FabricIQAppsDemoFEATURE`:
project `ai-project-hydro-foundry-884a839f3132`, active Hosted coordinator **11**,
QA **2**, RCA **2**, Work Order **3**, Fabric IQ **2**, Weather **1**. The manifest's
Supervisor contract revision **1** is not the Hosted version or ontology generation.
The source configuration matches ontology `1a5f7447-81be-4b90-ae00-e27b4aeb27b4`,
live numeric generation **2**, and operational SQL
`9b3dfea4-0934-4122-aaa4-1d7f17a183bd`. The active deployment has
`production_writes_enabled=false`.

Repository-local azd state points to a different project; it was not used for
inference or deployment. Readback/session inspection used the existing isolated,
endpoint-keyed FEATURE azd project. No new Foundry project, agents, resources,
notebook run, ingestion job or capacity change was created by this review.

### Exactly three live browser prompts

| Prompt | Observed time | Result |
| --- | --- | --- |
| T005 latest telemetry/open work, RCA using inspections/notifications, read-only recommendation | 1m 17s | QA v2 and RCA v2 completed. Six telemetry rows, two inspection rows and zero matching open-work rows; stale readings and causal limits disclosed; no draft/write. Run `fc0b2bb0-3ec5-4f6a-95b2-51273d4333af`. |
| BAD-quality-today/open-work question with RCA and editable drafts | 3m 16s | Failed closed after one same-source correction. Sparky/Data Agent returned native artifacts but did not produce the complete joined quality/work bundle. RCA/Fixer did not proceed; no empty-success result, direct-source substitution or write. Run `7e16aabe-9ea2-4a44-abed-04d4c03ed4e8`. |
| Aurora/UKMet next-24h rainfall comparison and affected-equipment open work | 47.2s | Weather v1 and QA v2 completed missing-data handling: zero locations and forecasts, no affected equipment selected, open-work source explicitly not read. No invented comparison, no draft/write. Run `61d38f5b-234b-45a3-a020-c95dce11a187`. |

Times are the browser's displayed observations, not controlled cold/warm benchmarks.
Completed answer receipts, the pinned agent manifest and the failed native run's
logs/raw replies were read back from those same sessions without additional prompts.
No fourth prompt, broader evaluation suite or action-delivery test was run.

The native failure was **not** missing SQL provisioning or a supervisor routing
loop. Both saved replies set `telemetry_complete=false` and `work_complete=false`;
the correction explicitly reported 90 BAD-signal rows, 90 static mappings and
10 open work orders, but could not emit the required machine-readable joined JSON.
The raw replies also contain an output-moderation error annotation. These
artifacts do not independently attest complete source coverage or execution
provenance. Preserve the failure; do not infer missing arrays or override false
coverage flags. Native structured-output acceptance remains unresolved.

Weather missing-data handling is not a successful forecast comparison or proof
that the provider credential/pipeline is repaired. The separately diagnosed
Weather provider HTTP 401 remains an external prerequisite.

### Narrow app-side optimization, not yet published

- **New chat** now clears the preceding-run context and invalidates old approval
  and reconciliation callbacks without replacing the warm container session.
  Configuration/target changes still perform a full session reset; separate app
  builds and engines retain their existing session scopes. No analytical data or
  tokens are persisted by this change.
- Crew output now says **workflow participants**, rather than counting Supervisor
  as a model invocation, and labels non-Foundry events as application workflow/tool
  activity rather than claiming backend work runs in the browser.
- **27 focused transport/trace tests**, TypeScript, changed-file ESLint and
  environment validation passed. Tests explicitly verify one unchanged container
  ID/no extra warm-up, no previous-run carry into a new chat, rejection of old
  approve/reject/reconcile callbacks, in-flight reset guards and full-reset rotation.

No Hosted image change is required for these app-only files. Publication remains
blocked by the existing tenant Graph
`InteractionRequired / TokenCreatedWithOutdatedPolicies` challenge, independently
reconfirmed against the configured SPA. These optimizations are source-verified,
not measured in the hosted UI yet.

This is **partial runtime acceptance**, not enterprise HA or complete end-to-end
business acceptance. Session-local filesystem checkpoints do not certify
cross-worker recovery; the native Data Agent case failed, Weather data was missing,
and approved writes/Teams/email delivery were intentionally not exercised. Keep
Foundry setup separate and publish the Fabric app last through the
[canonical deployment guide](../HydroOperationsApp/DEPLOY.md).

## October 23 deployment automation and operator-instruction review

The local deploy app's **Deploy app** and **Full workflow** routes call the canonical Python deployment
engine. Foundry project creation is billable opt-in; the engine converges both Fabric IQ connections,
the four specialist prompt agents and the Hosted Agent's source-fingerprinted image/configuration.
This review corrected three rerun/selection issues:

- Existing azd environments now refresh project, tenant and registry identity from live metadata before publishing,
  not only on their first creation.
- An unreadable or non-boolean live production-write policy requires an explicit operator choice rather than a
  silent default or string-to-boolean coercion.
- A selected but undiscoverable Foundry endpoint cannot trigger creation of a different project.

The [operator guide](foundry-deployment-and-multi-agent-setup.md) now includes end-to-end manual prerequisite
steps followed by the same canonical command, removes the alternative hand-built publish/configuration recipe,
and explains how to resume a fresh local workflow after native graph materialization. Contradictory local-only
deployment prose is marked historical or replaced.

No Data Agent notebook/runtime change is needed merely because Fabric fixes the Ontology service.
NB09 already makes a fresh bounded query on every rerun; the guide documents exact status keys and the separate
Sparky acceptance gate. The recovery contract exercises failed-to-verified reruns while retaining SQL/custom
sources. Stale AST test fixtures were updated for existing notebook-owned guidance; production notebook logic
and packaged notebook mirrors are unchanged.

Validation: **251 deployment, launcher and ontology/agent contract tests passed**. This was a repository review,
not a live deployment, project creation, signed-user multi-agent acceptance run or verification of the reported
Fabric service fix. The prior same-build acceptance gates remain pending.

### Follow-up: tenant/workspace portability

The tracked `azure.yaml` no longer contains a live project endpoint. Every Hosted Agent publish uses isolated,
endpoint-keyed generated azd state. Project creation state includes tenant, subscription and workspace identity.
The selected subscription must be enabled in the Fabric tenant; tenant-domain inputs resolve to authoritative
GUIDs before deployment. Saved endpoint/model hints are target-scoped and verified, and stale registry hints
cannot select a registry outside the project's live connections.

The demo model name/version/SKU/capacity defaults were removed. Existing projects select a verified deployment
or discover one ready chat-capable deployment; creation requires explicit catalog/version/SKU/capacity values.
Both local deployment forms expose these values, subscription selection and operator-verified graph-binding JSON.
Unchecking creation explicitly overrides inherited opt-in; opting in without selecting an endpoint provisions a
target-specific project rather than rebinding an unrelated existing project. Stable `hydro-*` role names remain
application contracts; each concurrently hosted Fabric workspace needs a separate Foundry project.

Environment validation now precedes fresh AppBackend bootstrap as well as image/app publication.
The manual guide documents the same inputs and canonical engine, including Python dependency restore and the
separate graph-materialization prerequisite. No generated state, historical redirects or cloud versions were deleted.

Validation: a broader **261-test** deployment/ontology run passed, followed by a final **186-test**
deployment/launcher regression run with the additional target-isolation and graph-binding cases.
A browser fixture confirmed both forms enable/disable creation inputs and submit isolated subscription/model
payloads; it did not call cloud deployment APIs. No new tenant/workspace deployment, billable project creation or
signed-user runtime acceptance was performed. Portability remains subject to target permissions, service/region
availability, quota, active capacity and portal materialization; it is not universal cloud/runtime certification.

### Follow-up: working ontology Operations Agent playbook

Read the working `ws-vteam-demoV3` ontology agent definition on October 23 without
updating or restarting it. Its current playbook differs from the legacy embedded one:
two SQL/KQL-grounded rules, 20 grounding definitions, projected signal identity, and
direct `ActionBinding.ParameterBindings` rather than the older nested binding list.
The instructions are unchanged, but copying only those instructions would not reproduce
the working playbook. Ontology Knowledge still uses physical Lakehouse/Eventhouse queries.

NB10 now embeds the working playbook with source placeholders and resolves the target's
Lakehouse SQL endpoint/catalog, KQL query URI/database, and configured signal/event tables.
All repeated grounding queries are rebound, and current direct email bindings survive.
The Raw mirror is synchronized. The default remains stopped, and this update does not
change existing cloud items or retire the Eventhouse provisioning path.

Validation: **74 agent capability/deployment contract tests passed**. A read-only check
against live target metadata regenerated the working playbook exactly except for safe SQL
table-identifier quoting. No queries, action delivery, new deployment, or restart were
performed as part of that comparison.

The rules retain their reviewed state conditions; a timed 15-minute cooldown is **not**
implemented or certified. The operator guide distinguishes signal grouping, transitions,
fixed time buckets, and a rolling notification cooldown, including escalation and the need
to cover both Teams and email. No undocumented suppression fields were added.

## October 12 v77 canonical-prompt release

Hosted Agent v77 is active from commit `30ee16e` on immutable image
`sha256:f8568200db0a496e2aa95f86651e7619f5c860f8727d3cb1e42c9192c5fb04da`
(`validated-20261012070135`, ACR run `dt2f`). Fabric app `1.0.874` was
published from the same commit. The canonical app deployment returned
`SUCCESS`, preserved 39 SPA redirects, and passed AppBackend GraphQL/token
preflight and POST-readiness checks. The runtime retains verified ontology
generation 2 and `production_writes_enabled=true`; agent tools still cannot
write SQL.

The recovery contract now binds the server-assigned run ID in the first NDJSON
frame. A browser disconnect no longer cancels the server run: execution
continues in the same hosted session and persists either the certified answer
or an explicit failure. The signed `result` operation is isolated by principal
and verified source and returns running, completed or failed state for that
exact run. The client renews delegated source leases while polling and never
starts a replacement orchestration or retries a decision. Failed turns terminate
active crew states, suppress success-shaped charts/suggestions/cards, and state
that neither answer nor write outcome is certified. Battle execution is now
capacity-safe and serial.

Validation passed 170 backend tests plus 80 subtests, 388 application contracts,
Pyright, TypeScript, ESLint, environment validation and production build.
The executable contract now enumerates all ten canonical prompts and verifies
specialist routing and minimum counts, native source selection, chart intent and
work-order priority. Explicit final independent verification requires a second
Gauge phase, preserving Gauge -> Sleuth -> Fixer -> Gauge.
Authenticated live acceptance could not resume after deployment because the
private Fabric host presented `Sign in required` and its sign-in popup did not
complete silently. No CLI token or hosting/authentication bypass was used.
Therefore the v77 ten-prompt matrix remains pending rather than accepted.

## October 11 v74 historical release

Hosted Agent v74 is active from commit `258b2e5` on immutable image
`sha256:507a364ff9f8c4516f9de4a47a6f81145476d4a42fec123b87be446e21594aae`
(`validated-20261011154234`, ACR run `dt2c`). The Fabric app is `1.0.868`
from `a9bdfc2`; its canonical deployment preserved 39 redirects and passed
AppBackend GraphQL/token browser preflight and POST-readiness checks. The runtime
retains verified ontology generation 2 and the signed human-approved write path.

The v73 production Prompt 2 run verified centralized presentation using real
ontology equipment, facility and six-instrument instance tables, direct
telemetry, explicit timestamped zero-open-work coverage and a prominent
explanation. Canonical Prompt 1 then failed closed when Sleuth duplicated the
terminal fragment of an otherwise valid run-scoped evidence ID. This was an RCA
structured-completion defect, not an Ontology v2 source failure.

v74 adds a narrow deterministic repair: only one uniquely matching allowed
evidence ID may be restored, and only when the extra fragment is already a
terminal suffix of that ID. The unchanged strict report validator still resolves
the evidence ID and result-relative JSON pointer. Validation passed 167 backend
tests under Node 24.21.0 and Pyright with zero diagnostics.

The normal private Fabric SPA refresh session expired before the v74 Prompt 1
rerun. Azure CLI tokens were correctly rejected by the configured SPA-client
binding. No token substitution or hosting bypass was used, so the v74
ten-prompt matrix remains an explicit pending acceptance gate.

## October 11 v72 current release

Hosted Agent v72 is active from commit `35f46cc` on immutable image
`sha256:9acfa1fea4bd17ddf611c3418b48bd43ae67eb263d5bbc8760078a599f35345a`.
It uses verified ontology generation 2 and keeps the signed human-approved
production-write boundary enabled. Validation passed 164 backend tests under
Node 24.21.0 and Pyright with zero diagnostics; the unchanged application
boundary had passed 383 application/source-contract tests and its build checks.

The v69-v72 corrections are architectural, not prompt-specific:

1. Chief omissions recover only from the backend capability manifest.
2. Hosted decisions serialize only the three operator-editable fields.
3. Oversized native prose exposes bounded source-preserving citation fragments.
4. A wrong configured native connection receives one same-agent correction and
   then fails closed without direct-source substitution.
5. Each of the two bounded Foundry completion attempts receives its own single
   schema correction, while repeated invalid calls within one attempt still fail.

Authenticated Prompt 2 on v72 completed Sparky, Gauge, Sleuth and Fixer in 3m43s,
staged an editable T005 inspection draft, and performed no write. The draft was
rejected through the backend and disabled. The rendered answer explained that no
physical fault or root cause was established and listed evidence gaps. Ontology
relationship projection and explicit zero/open-work coverage remain presentation
follow-ups, so this targeted run is not relabelled as full same-build ten-prompt
acceptance.

The Fabric app remains `1.0.863` from `f91c20f`. Earlier v69 production acceptance
created and independently read back
`WO-92c4bdaa-a28f-5fd5-8142-682740eaa423` for `EQUIP_RTI_T009`.

## October 11 v66 current release

Hosted Agent v66 is active on immutable image
`sha256:ee4088286b30d0f53c7f70a2b63cee0be0ec5616850bf17a2fba6ad7c08d8920`
(`validated-20261011202000`, ACR run `dt25`, source commit `a582502`). The runtime
uses `gpt-5.6-sol`, verified ontology generation 2 and disabled production writes.
Fabric static deployment `deploy-20261011093808-7f2cf801` was published from the
same commit through the canonical deployment orchestrator.

Every nontrivial certified answer now requires a grounded explanatory summary
covering the conclusion, meaning of the strongest evidence, and material
uncertainty or next safe action. Row-count-only summaries are rejected, receive
one bounded repair over the same immutable receipts, and fail closed if repair
remains invalid. Deterministic certified-empty signal-quality answers retain
their evidence-derived explanation. The Fabric renderer no longer duplicates the
answer in an `Assessment audit` block; source annotations, execution receipts,
supporting findings, charts, tables, crew trace and approval cards remain.

Validation passed 156 backend tests, 172 application contract tests with the
required `tsx` loader, Pyright, TypeScript, ESLint, environment validation and
production build. Deployment preserved 39 SPA redirects and passed AppBackend
CORS, browser preflight and POST-readiness checks. The production host then
presented Fabric's protected sign-in gate; the integrated automation browser had
no private-hosting cookie, so rendered and delegated live-source acceptance were
not performed on v66. The v61-v65 business matrix remains prior source-backed
evidence and is not relabelled as a v66 rerun.

## October 11 v65 acceptance record

Hosted Agent v65 is active on immutable image
`sha256:62fef1a2d69aaabe50832d4c1b688ad5f85c3b09e767322fcd3125d9a2f6afba`
(`validated-20261011185000`, ACR run `dt24`, source commit `fffca87`). Chief v20
and the runtime use `gpt-5.6-sol`, verified ontology generation 2 and disabled
production writes. The Fabric app remains `1.0.842` from `b561593`.

Acceptance exposed and corrected four generic failure classes rather than adding
exact-prompt routes:

1. Visualization imperatives now require factual source verification.
2. Long-running specialists re-emit the same running event every 15 seconds,
   preserving the live stream without an application timeout.
3. A schema-invalid tool-free narrative gets one bounded repair over the same
   immutable receipts; invalid attempts remain in the audit.
4. Native identity survives field/value, bullet and named Markdown table formats.
   Required Gauge execution can resolve scope directly and is not silently skipped
   because a native response lacks an extractable identity.

The final v65 Prompt 2 run completed in 170.3 seconds through Sparky -> Gauge ->
Sleuth, explained T005's ontology context, disclosed empty six-hour telemetry and
open-work reads, created no proposal and executed no write. The Prompt 8
same-session follow-up now clearly labels itself `Unsent draft` and makes no
recipient or delivery claim. Validation passed 155 backend tests and Pyright;
the unchanged application boundary previously passed 380 application contracts,
32 packaged-source tests, TypeScript, ESLint, environment validation and build.

The ten business workflows passed across v61-v65 with affected workflows rerun
after their changes; this is not represented as a same-build ten-run matrix.
Prompt 4's native Data Agent call still took 790.6 seconds and Prompt 10's Sleuth
follow-up stalled upstream for about ten minutes. Heartbeats preserved both
streams, but no latency SLA is claimed. The integrated browser could not complete
Fabric private hosting's popup/PKCE cookie handshake: a cached shell rendered,
while an uncached battle-page chunk returned `401 Authentication required`.
Hosted browser-origin contracts are accepted; rendered battle-page certification
requires a normal private-hosting sign-in.

Cleanup found no temporary image-tag file, untracked browser coordinator or
repository-owned local server. The packaged `local-fabric-sources.mjs` bridge is
retained because it runs inside the Hosted image and owns source contracts.

## October 11 v60 release

Hosted Agent v60 is active on immutable image
`sha256:4f24fa5f3b306bffce334ccc516673d266ea3434769c4645b96c0786c1d125d5`
(`validated-20261011063055`, ACR run `dt1y`, source commit `ea2a8a2`). Chief v20
and the runtime use `gpt-5.6-sol`, verified ontology generation 2 and disabled
production writes. The Fabric app remains `1.0.842` from `b561593`; the v60 delta is packaged
Hosted source/presentation code and does not require a static-app redeployment.

The shorter chart-only request exposed a v59 gap after the ten compound prompts
had passed: chart intent was detected, but `chart`, `plot` and `visualize` were
not factual-read verbs, so a zero-specialist Chief plan could reach presentation
without evidence and fail with HTTP 500. v60 fixes the generic capability
classifier and requires Gauge for Hydro-domain visualization reads. Automated
validation passed 151 backend and 380 application contract tests plus Pyright,
TypeScript, ESLint, environment validation and production build. Live browser-
origin acceptance completed the exact short request through Chief -> Gauge with
three station rows and one MW chart. The longer Prompt 9 form and its same-session
verification follow-up also passed with exact values, units, sample/BAD counts,
freshness and no write.

The current executor has no application wall-clock timeout. A backend-owned
capability manifest rejects incomplete Chief plans before any specialist runs and
permits one bounded planning correction. A backend-owned minimum-step manifest
also enforces explicitly quantified independent factual populations. Chief must
plan each as a separate non-conditional Gauge handoff, so an empty first result
cannot suppress its sibling. Native identity handoffs preserve only
exact field-bounded equipment identities from verified Fabric IQ receipts; this
fixed the earlier case where a complete Sparky -> Gauge -> Sleuth -> conditional
Fixer plan silently skipped every downstream step after Sparky. Requested
station-power charts now use source-owned presentation metadata and deterministic
selection when exactly one verified candidate exists.

The authenticated browser-origin matrix passed all ten business outcomes, including the two
same-chat follow-ups, with current empty-window results preserved rather than
filled from stale data. Prompt 4 completed Sparky -> Gauge -> Gauge -> Sleuth:
the native Eventhouse failure remained explicit, while separate direct BAD and
hottest reads established that all 90 signals, including all 15 temperature
signals, lacked today-window readings. Prompt 2 completed Sparky -> Gauge -> Sleuth and correctly
skipped conditional Fixer. Prompt 3 reconciled 11 native/direct open orders and
rendered one equipment chart; its evidence-backed draft was rejected. Prompt 5 reconciled three ontology facilities and
facility counts 4/4/3 with one chart. Prompt 9 rendered one MW chart and verified
values, units, sample/BAD counts and freshness. Prompt 10's exact Low-priority
card was rejected by the backend; its RCA follow-up established no physical fault.
The earlier v58 Prompt 4 omission remains a recorded failure, as do two earlier
native-source-unavailable attempts before fresh Prompt 3 and Prompt 5 reruns succeeded.

## Current state: bounded Foundry-owned planner/executor

Hosted Agent v50 is active on immutable image
`sha256:25dc9db7d7e26519439243175c5cd43b6a87007db92c26fac03fec580b6c7bb8`
(`validated-20261010221658`, ACR run `dt1n`, source commit `fe55567`). It runs the Microsoft Agent Framework
workflow that coordinates Chief, Gauge, Sleuth, Fixer and Sparky. The SPA uses
one authenticated Invocations endpoint for normal Foundry chat and for both
Battle comparison routes. Real backend events drive the crew animation; the
browser neither invents handoffs nor executes specialist tools.

The architecture now uses a true bounded planner/executor rather than an open-ended
Chief function loop. Chief v17 exposes only `plan_orchestration` and returns one
complete ordered plan of one to eight steps. The backend validates each delegation
through the shared contract, executes specialists serially, injects prior reports
and immutable evidence into later steps, then deterministically certifies attested
receipts and validated specialist contracts without a redundant model verifier.
This certification is emitted as an application validation event, not represented
as a fabricated agent handoff. The runtime projects a concise grounded conclusion
and evidence interpretation into a strict typed answer, validates supporting table
cells against literal receipts, and falls back to source tables if projection
fails. No prompt
regex, BAD/UNCERTAIN shortcut, direct T010 route or post-draft routing bypass remains.
Chief v17, Gauge v16, Sleuth v18, Fixer v16, Sparky v13 and the Hosted Agent
projection runtime use `gpt-5.6-sol`.

Live v49 acceptance established that the reported physical-fault prompt now
renders a direct evidence interpretation before its source tables, while the
one-hour BAD/UNCERTAIN prompt completes without the former HTTP 409. The 409 was
caused by an automatically attached previous display exceeding the 16,000-character
history bound. v49/v50 replace that rejection with a bounded historical conclusion
and limitations plus an explicit fresh-read requirement. v50 further bounds the
operator narrative to complete concise sentences; that final contract is covered
by automated tests because the temporary signed-browser session expired on reload.

### Shared server-side domain contract and capabilities

The Hosted Agent no longer relies on specialist prose to rediscover the core data
model. `domainContract.ts` defines the authoritative cross-source identities,
cardinality and limitations for facility, equipment, instrument, telemetry,
work-order, inspection, notification, model and spare-part relationships. The
packaged Node bridge publishes that typed contract with the source schemas, and
the Python Agent Framework passes the same immutable contract to specialists.
This is server-owned runtime context; it does not restore browser orchestration.

Reusable domain capabilities sit above the bounded low-level tools. The first
general capability, `query_work_backlog`, accepts an equipment or facility
grouping and:

- reads the complete SQL work-order, Lakehouse equipment and facility inventories;
- excludes Completed and Cancelled work while retaining Draft and Planned work;
- joins only on declared identity keys and retains unmatched records;
- conserves every open work order exactly once;
- returns the literal open-work inventory with grouped counts and verified units;
- supplies an attested table/chart projection that the backend can render without
  another model-authored recount or CSV transformation.

Gauge chooses this capability from its tool schema; there is no question-text
route. Arbitrary reads remain available through the generic catalog/KQL tools,
while fleet quality, turbine temperature, station power and work backlog use
deterministic capabilities where their semantics are already known. Backend
attestation rejects closed work, duplicate identities, invalid grouping,
non-conserved counts and presentation drift before evidence reaches an agent.

The legacy `browserFoundry.ts` coordinator and its dedicated runtime suite were
removed after hosted parity dependency analysis. Administration now retains only
the editable Foundry project endpoint, optional Battle preference and read-only
Hosted Agent version/image/source provenance. Browser-editable specialist prompts,
tool allowlists and table/source switches were removed. The packaged Node source
bridge, direct query schemas, embedded SQL/work-order UI, approval cards, native
Data Agent support for the classic app surface and Battle presentation remain.

Prompt 1 through Prompt 9 and the final Prompt 10 passed the historical v22 live
hosted matrix. The final signed-browser
Prompt 10 v22 run also passed. Its primary completed in 1m31s through Chief v16,
Gauge v15 and Fixer v15 and produced exactly one editable Low-priority T005 card
with the requested title. Backend rejection confirmed that no work order was
created. The same-chat follow-up completed in 46.6s through Chief v16, Sleuth v17
and Gauge v15; Fixer and Sparky remained idle, no second proposal appeared, and
two literal source rows remained visible in two tables. One earlier v21 attempt
remains recorded as a bounded Sleuth inference stall. V22 displayed the
backend-derived conclusion:
`No physical fault is established by the returned evidence; cause remains undetermined`,
plus validated evidence-gap labels and `No production writes executed`. Authentication
used the existing target-tenant browser account and normal application connection
controls; no credential, cookie or CLI-token substitution was used.

The v23 candidate passed 135 backend tests and Pyright, plus 208 frontend tests,
typecheck, lint, environment validation and production build on Node 24. Direct
GPT-5.6 Chief invocations verified one-call plans for all ten canonical primary
prompts, both documented follow-ups and the BAD/UNCERTAIN and T010 RCA regressions.
One concurrent planning request returned an MCP HTTP 504 and passed on bounded retry.
The protected app then required normal Fabric sign-in; no interactive login or token
substitution was attempted. Consequently current source execution, answer quality,
latency, current-bundle animation, Battle parity and cards remain unaccepted.

The canonical Fabric orchestrator deployed planner commit `5a47c24` as app
1.0.803. It
reapplied AppBackend runtime/CORS, passed GraphQL and token preflight/POST
contracts, preserved 39 redirects and reported `SUCCESS`.

## Historical correction: Foundry-owned execution, not more browser routing

The October 8 design review rejected further expansion of the browser coordinator.
The target is an Agent Framework workflow hosted in the existing Foundry project,
reusing the deployed prompt agents rather than creating replacement specialists.
The SPA submits runs, renders typed results and execution events, and collects
human approval. Routing, retries, source execution, checkpoints and result
validation belong in the backend. The paragraphs below preserve the historical
migration state; the current state above supersedes their old readiness claims.

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
The follow-up `fe48419` deployment reused the persistent tenant-scoped Azure CLI
cache without an interactive login and corrected the hosted client to consume the
canonical `VITE_FABRIC_TENANT_ID` and `VITE_FABRIC_WORKSPACE_ID` exports. A real
read-only probe verified generation 2 and six current T005 telemetry observations;
delegated SQL still failed explicitly as `AUTH_FAILED`. A separate real Sleuth v17
call completed in 11.08 seconds with a typed `cause_undetermined` result. The
unattended browser could not complete protected-hosting or MSAL popups, so these
checks do not replace signed-user streaming, SQL or ten-prompt acceptance.
Hosted deployments now present a single browser control plane in Administration:
the project endpoint remains editable, while the invocation endpoint and verified
source digest are read-only and Battle remains an explicit browser preference.
The active Hosted Agent version and immutable runtime image digest are also
deployment-readback fields, so an operator can correlate streamed handoffs with
the exact runtime artifact.
Browser-editable specialist prompts, tool schemas and source catalogs are hidden
when the Hosted Agent endpoint is configured; those capabilities are owned by the
versioned Agent Framework runtime and provisioned specialists. The legacy controls
remain available only for an unhosted rollback until signed-user parity permits
deleting the browser coordinator itself.
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
and request identity, bounds concurrency and credential admission leases, sanitizes validation errors
and clears leases on exit. Its raw POST protocol supports run invocation and
same-user completed/failed-run evidence retrieval. Fifteen signed-token/HTTP tests cover
these boundaries, including expiry, cross-user audit denial, credential/namespace
substitution, oversized input, saturation, source failures and failure
audit storage errors. Authorized failures include a server-assigned run ID and
sanitized category/source/clock audit, never raw exception bodies or tokens.
Audit storage errors explicitly report that the audit is unavailable.
Hosted orchestration, specialist handoffs, native-source execution and final
narrative projection have no application wall-clock timeout. The browser keeps
the streaming request open until a certified answer or explicit failure; disconnect
cancellation still releases capacity. Finite source/network timeouts and external
Foundry or delegated-token limits are surfaced as upstream failures, not recast as
successful empty evidence.
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

The four specialist definitions below are read back on each deployment. The Supervisor is the hosted
orchestrator, not a prompt agent. They are **Prompt Agents**: persisted model configuration, instructions and tool
definitions, invoked through Foundry Responses. They are not continuously running
workers or five Agent Framework-hosted processes.

| Agent / deployed version | Invoke for | Tools and knowledge | Authority |
|---|---|---|---|
| **Supervisor**, hosted `hydro-orchestrator` (not a prompt agent) | Compile, sequence and verify the request; write the grounded briefing | Deterministic control plane, shared evidence policy, specialist receipts | Coordinates; no database write authority |
| **Gauge**, `hydro-qa-agent`, v11 | Facts, telemetry, existing work, inventory, charts and requested factual verification | `hydro_query` with catalog-governed STID/SQL reads, KQL templates and validation, station-power/quality snapshots and presentation tools | Read/visualize, not create work |
| **Sleuth**, `hydro-rca-agent`, v13 | Investigation, diagnosis review and RCA follow-ups | Read tools plus `complete_rca_assessment`; evidence references, competing hypotheses and missing-evidence rules | Proposes explanations, not proven causes or operational control actions |
| **Fixer**, `hydro-work-order-agent`, v11 | Explicit or conditional new-work review | Identity/coverage reads, `propose_work_order`, `complete_work_order_review` | Editable proposal or explicit no-draft/clarification result; no model-issued SQL write |
| **Sparky**, `hydro-fabric-iq-agent` | Explicit published Data Agent retrieval | One verified `fabriciq-data-agent` connection using `fabric_iq_preview` | Data Agent retrieval only; direct ontology-native chat is rejected |

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
The **ontology-backed Fabric Operations Agent** retains its playbook/actions,
Teams and email capabilities. The separate Eventhouse-Knowledge agent is retired without
removing shared sources or alert dependencies. These proposed specialists do not replace the retained agent,
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
