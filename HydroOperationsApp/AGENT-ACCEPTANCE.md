# Hydro Intelligence: implementation, routing and acceptance

This report distinguishes implemented behavior, observed runtime results and proposals.
**The ten-flow suite is not yet accepted.** Deployment success and unit tests are not
proof of agent answers, causal diagnosis, SQL mutations or notification delivery.
Deployment entry points: [DEPLOY.md](DEPLOY.md). Source architecture: [README.md](README.md).
Operator-ready prompts: [ten copy-and-paste multi-agent tests](AGENT-TEST-PROMPTS.md).
Consolidated change history and architecture:
[October 7-8 Foundry and multi-agent implementation record](../docs/foundry-multi-agent-implementation.md).

## October 9 hosted orchestration status

**Current deployed Fabric app: 1.0.798 (`d4d1dfc`). Current Hosted Agent: v22.**
The active image is
`sha256:8bdaedf511cc0919d1c46eb38875c0c4486b2dfc324c0ff8816406b5fdb1262a`
(`validated-20261009155318`, ACR run `dts`). The canonical Fabric deployment
reported `SUCCESS`, restored AppBackend runtime/CORS and passed browser-equivalent
preflight and POST checks for `/graphql` and `/api/auth/v1/token`.

The browser now uses only the authenticated Hosted Agent Invocations transport
for Foundry and Battle orchestration. The old browser supervisor/tool loop and
its Administration prompt/tool/table controls have been removed. The application
still retains Battle enablement, editable project identity, runtime provenance,
embedded SQL/work-order approval UI and the server-side source bridge.

| Prompt | Live result |
|---|---|
| 1 | Work-order draft path exercised, rejected and reconciled without a production write. |
| 2 | 42 source rows, 6 tables; Gauge, Sleuth and final Gauge; 37 seconds. |
| 3 | Five-row chart; Chief and Gauge; 32 seconds. |
| 4 | Correct source-grounded empty result; Chief and Gauge; 17 seconds. |
| 5 | Impossible 500 ms deadline failed closed in 1.2 seconds. |
| 6 | Data Agent through Sparky; 1 row; 55 seconds. |
| 7 | Ontology v2 through Sparky; 1 row; 22 seconds. |
| 8 | Combined Data Agent and Ontology; two Sparky calls, 2 rows; 43 seconds. |
| 9 | Primary plus same-chat follow-up passed; 20 rows, 3 tables; 54 + 34 seconds. |
| 10 | Primary draft succeeded and was rejected. The v21 follow-up succeeded on retry in 79 seconds with Chief, Sleuth and Gauge, no Fixer handoff and no second proposal. One prior attempt failed closed on a bounded Sleuth inference stall. |

V22 deterministically surfaces the validated conclusion that no physical fault
is established and cause remains undetermined, together with validated evidence
gaps. Unit and contract tests cover that presentation. The final signed-browser
v22 presentation rerun is still outstanding because browser-tool discovery timed
out; CLI credentials are not SPA-issued delegated tokens and were not substituted.
Therefore this report does not relabel the full ten-flow suite as accepted.

Current regression evidence on Node 24.21.0: 129/129 backend tests, Pyright with
zero errors/warnings, 378/378 frontend tests, typecheck, lint, environment
validation and production build.

**Current deployed release: 1.0.775 (`41d458a`).** Canonical deployment returned
`SUCCESS` on October 8 with verified agent definitions, all 39 redirects preserved,
and effective current-operator consent. Both backend preflights returned HTTP 200;
GraphQL POST returned 200 and the deliberately incomplete token POST returned 400,
as required by the readiness contract. The built bundle contains version 1.0.775.
After this deployment, the previously authenticated browser returned to Fabric's
private hosting sign-in gate and the integrated browser blocked the popup.
The existing broker had no opener; normal pointer/keyboard sign-in attempts did
not restore access. The operator was unavailable to complete sign-in. Therefore
browser build identity, the exact low-stock recovery, the final-candidate matrix,
follow-ups and Battle/Data Agent parity remain unverified. No access control was
bypassed and no additional Entra permission was introduced.

**Earlier 1.0.758 (`4c0e1a1`) evidence:** canonical deployment completed on
October 8 with verified agent definitions Chief v9, Gauge v11, Sleuth v13, Fixer v11,
and Sparky v8. Runtime/CORS and POST checks passed; all 39 redirects were preserved.
Browser build identity was verified. Consent is valid for the current operator only;
tenant-wide enterprise consent requires an administrator. Latest targeted reruns are recorded below.
The deployed source-derived correction passed 170 targeted regressions, typecheck, lint, the canonical
production build and two built-bundle checks. Its browser build identity was verified.
These checks do not turn the 1.0.742 runtime failures below into passes.
The earlier 1.0.737 rerun completed **ten scenarios / twenty turns**, and was **not accepted**.
Telemetry was 90/90 fresh at 05:46 UTC, but subsequently stopped advancing around
06:05 UTC; the app correctly changed its header to stale. Do not treat the earlier
freshness observation as current health.
Creation/configuration readback is verified for the five Foundry agents, but complete
multi-agent runtime correctness is not yet certified.

### Latest hosted verification

**October 9 Agent Framework checkpoint (`fe48419`):** the canonical deployment
completed with `SUCCESS` and reused its tenant-scoped Azure CLI cache without an
interactive login. Hosted `hydro-orchestrator` v7 is active on immutable image
digest `sha256:1a8ce284f0b0de8bed4a4a243efcb2097ffaac391ff1c9bbcb6f6ee3c7a3dfc7`.
The deployed bundle contains the exact invocation endpoint and verified v2 source
digest `210d1ce53296ddb304613a7dcb87b22ee7a017073ef48b23e7fba7fcfa82644b`.
All 121 backend tests, Pyright, dependency checks, 427 frontend tests, typecheck,
lint and production build passed. A real read-only diagnostic verified live
generation 2 and six current T005 STID/telemetry observations, but delegated
AppBackend work-order exchange failed explicitly as `AUTH_FAILED`; it did not
become an empty-work answer. A separate real Sleuth v17 invocation completed in
11.08 seconds with response `resp_0aee7ed7f1e3af01016ac89c07bd9c81908d12b83424af4a56`
and retained missing work/inspection evidence and `cause_undetermined`.
Protected-hosting and local MSAL popups are blocked in the unattended automation
browser, so signed-user streaming, SQL authorization and the ten-prompt matrix
remain unaccepted. The legacy browser coordinator and advanced browser control
plane must remain only until those gates pass; this checkpoint is not cleanup
authorization or enterprise runtime certification.

**Latest deployment attempts:** source commit `c601e34` was pushed only to
`feat/dibakar`. The canonical deployer stopped at step 1 because Azure CLI was
signed into a different tenant than `ad340c84-1886-4202-a483-2da2cb9168eb`.
The supported local deployment app's **Switch** flow subsequently completed.
The same canonical command then deployed 1.0.766 successfully. The subsequent
universal-presentation release 1.0.768 also passed the canonical deployment and
backend contract checks; its authenticated browser build identity was verified.
This resolved
deployment-operator sign-in, not an additional SPA/SQL consent requirement.
Hosted browser sign-in initially stopped at the protected popup, then recovered
through the shared broker. That historical blocker no longer prevents testing.

**Universal presentation, deployed in 1.0.768:** 115 targeted Node tests,
typecheck, targeted lint, environment validation and production build passed.
The shared renderer covers Foundry, Data Agent, Battle and the legacy panel:
structured records remain tables and suitable charts are automatic. A real local
component with synthetic BAD/UNCERTAIN receipts displayed all nine rows in two
tables, four charts and two resolving source links, without malformed model prose.
Table-only mode retained nine rows; narrow/wide viewports had no page overflow.
These fixture checks are not live-agent acceptance. Production build still reports
existing large-chunk warnings. At that checkpoint Teams progress delivery was
unverified because self-chat creation was rejected. The later message-endpoint
recovery is recorded below; this historical blocker is no longer current.

**1.0.768 live reproduction, not accepted:** the exact BAD/UNCERTAIN question
completed in 47.6 seconds. Four tables retained 3 BAD and 6 UNCERTAIN signal
rows plus 2 and 7 related-work rows; four source links resolved. However, Chief's
prose said seven turbines while the source rows contained eight distinct
equipment IDs. It also repeated an unsolicited follow-up menu. A follow-up
correction now derives simple snapshot answers from source receipts and removes
repeated work-count charts from each measurement-unit group. Its 117 targeted
tests, typecheck and targeted lint passed; a new deployment/rerun is required.

**1.0.769 targeted hosted recovery:** canonical deployment and backend contract
checks passed; the authenticated browser confirmed `33167af`. The exact
BAD/UNCERTAIN rerun completed in 36.8 seconds with four source tables (3/2/6/7
rows), eight unit-separated measurement charts and resolving receipt links.
The final summary used actual read clocks and latest-per-signal semantics,
retained stale-data warnings, and no longer contained the invented turbine count
or follow-up menu. No additional browser consent prompt was observed on these
two successive hosted runs. This does not certify tenant-wide consent or the
ten-flow matrix, which is being run separately.

**1.0.769 compound observations:** scenario 1 completed in 1m45s with Chief,
Gauge, Sleuth and Fixer, seven tool receipts and a no-draft outcome. Scenario 2
completed in 3m05s with all five agents, ontology retrieval, six telemetry rows
and an empty scoped work read. Both retained explicit stale-data/causal limits.
Neither is accepted as complete presentation: checked RCA observation and
hypothesis tables were hidden when raw source tables took precedence.

**Follow-up grounding/presentation correction, not yet deployed:** application
trace markers distinguish checked RCA/reconciliation tables from model tables;
the checked tables are retained alongside raw receipts without promoting quoted
native claims to verified findings. Model chart calls must match exact returned
source projections or deterministic record counts. Invented values, duplicate
amplification and incompatible/unknown measurement-unit combinations are rejected
before rendering. Labels come from source fields, not model diagnostic titles.
Factual Q&A summaries use source receipts; source-free factual answers fail
explicitly. Typed work-review outcomes cannot be replaced by Chief's prose.
These controls do not certify causal relevance, native internal provenance or a
zero-hallucination/zero-defect guarantee.
The expanded 123-test targeted suite, typecheck and targeted lint passed before
deployment. Hosted acceptance of this follow-up remains pending.
The first canonical deployment attempt stopped at hosting verification with a
connection reset. No readiness check was bypassed. The subsequent retry stopped
before deployment because the newly requested RCA roadmap was an uncommitted
tracked document; that documentation must be committed before retrying.
The clean-tree retry completed with `SUCCESS`; backend preflight/POST checks and
authenticated browser build identity passed.

**1.0.771 compound reruns:** scenario 1 completed in 1m38s (four agents, ten
tables including observations/hypotheses, ten charts); scenario 2 completed in
3m22s (all five agents, three tables, six charts). Receipt links resolved.
Scenario 3 completed its five-agent flow in 3m11s but **failed chart acceptance**:
after rejecting a model `order_count` column, the completion check used telemetry
charts instead of the requested orders-per-equipment chart. This is not a pass.
The local correction now derives that requested chart from operational records,
provides exact derived-column feedback, and keeps other automatic source charts
alongside explicit charts. Further presentation fixes withhold provisional
model prose and render nested RCA observations as field/value rows with literal
identifiers and exact pointers. All 138 affected tests, typecheck and targeted
lint passed. The corrections deployed as 1.0.772 with canonical readiness checks
and authenticated build identity verified.

**1.0.772 scenario 3 recovery:** all five agents completed in 2m30s. The requested
equipment backlog chart now shows T002=2, T008=2, and seven other equipment IDs=1,
matching the eleven returned work records. Priority/status charts and telemetry
charts remain available. This bounded recovery is not a completed ten-flow matrix
or a latency/provenance certification.

**Further 1.0.772 results:** scenario 4 disclosed an incomplete native comparison
(no recognized native BAD/temperature/work tables) rather than declaring a match;
the initial run took 2m08s and a reproduction 3m08s. Scenario 5 reconciled eleven
open work orders into three facility groups in 2m55s. Scenario 6 exposed an actual
filter defect: `quantityOnHand <= "reorderLevel"` used the column name as a literal
and returned all twelve parts, including above-threshold stock. It is a failed
acceptance case, despite correct causal abstention and no draft/write.

The local filter correction adds explicit, catalog-validated `value_column`
operands, rejects numeric/text ordering, and preserves null/zero/equality
semantics. Low-stock instructions now use `quantityOnHand lte value_column
reorderLevel`. All 177 affected tests, typecheck and targeted lint passed.
The correction deployed successfully in 1.0.773. A subsequent 40-test tool suite
also passed, including a regression using the twelve observed stock records that
returns exactly four parts: SP-BRG-1002, SP-ELE-5002, SP-SEAL-2002 and SP-VAL-4001.
This is a recorded-source fixture, not fresh live readback. That hosted rerun is
blocked on the hosting sign-in described above.

Capturing the native response on a further 1.0.772 reproduction confirmed actual
bullet records instead of the requested tables, with temperature OPC node IDs
omitted. The application did not infer missing identities or declare parity.
Reliable native structured-output enforcement remains a separate limitation.

**Native presentation/reconciliation follow-up:** deterministic parsing now turns
the observed labeled bullet records into tables, preserves ordered-list positions,
and gives each explicitly formatted work order its own row. Explicit "None"
records remain visible but do not count as work orders. Inline numeric/unit cells
produce unit-separated charts without changing their table values. Ambiguous
nesting, continuations and duplicate fields produce an explicit parsing warning
and keep the original output available for inspection, not guessed records.

An isolated browser fixture using the captured native response rendered three
tables (3 BAD records, 5 temperature records, 5 work orders plus 2 explicit None
records) and six charts. Work status counts total five; priority counts are
Medium=2, High=1, Low=1, Critical=1. Table-only mode retains all 15 rows. Wide
(1280px) and narrow (390px) layouts had no page overflow. Native provenance labels
remain "not independently verified". This is recorded-response presentation
verification, not hosted agent execution.

The expanded native/direct reconciliation tests exposed duplicate JSON ingestion
by the generic and strict native parsers. Native JSON now enters the strict
validator once; incomplete, nested, inconsistent and count-mismatched sets remain
unverified. Literal bullet-record BAD/work identities can be compared; missing
temperature OPC nodes are not inferred from names or instrument IDs. All 200
affected tests, typecheck and targeted lint passed. These changes deployed as
1.0.775. The first attempt failed hosting verification with a connection reset;
the same canonical command then returned `SUCCESS`, with both backend preflights
200, GraphQL POST 200 and the incomplete token POST 400. All 39 redirects were
preserved. Browser reload still shows private-hosting sign-in, and the operator
was unavailable to complete it. Hosted acceptance remains blocked; no failed
readiness or authentication check was bypassed.

**Teams communication recovered:** the verified existing self-chat message
endpoint accepted updates at 18:22 and 18:38 UTC; the first message was read back
successfully. A session-attached 15-minute reminder supports further updates
while work continues. This is progress communication, not certification of the
app's operational notification delivery or a permanent scheduler. Further updates
were accepted at 18:51, 19:08 and 19:24 UTC. The session-bound reminder is stopped
while hosted testing awaits operator sign-in; no unattended schedule is claimed.
A further update at 19:50 UTC covered the additional implementation and fixture
verification. The fixture server and its temporary script were cleaned up; the
reminder is again stopped while the latest hosted sign-in remains unavailable.

**Chart/consent correction deployed in 1.0.766; local validation:** 106 targeted Node tests
and the full 42-test backend suite passed. The app build, typecheck and lint passed.
Structured source receipts now render charts/tables despite malformed model CSV;
mixed units, truncation, missing timestamps, failed-refresh invalidation and
compound native handoffs have regression coverage. Eight auth tests cover cached
reuse, error classification, concurrent interactions, cancellation and tenant
selection. The real local dashboard rendered a 250-row synthetic dataset and
switched views through DOM click dispatch, with no narrow-viewport overflow.
Native pointer automation stalled; no new hosted consent/ten-flow acceptance
is claimed. See the implementation record for precise boundaries.

**Station comparison included in 1.0.766; historical local checks:** source-derived station
comparison now preserves previous/new means, sample/BAD counts and exact clocks
without another model invocation. New regressions reproduced and fixed two cases:
additional verification reads discarded the comparison, and multiple distinct
windows left an unsupported model verification claim visible. Comparisons now use
the checked renderer even with additional reads; ambiguous datasets remain
separate with an explicit unavailable-comparison reason. New chat, failure, expiry,
changed configuration/source and an intervening non-chart answer invalidate the
previous-display snapshot. The shared KQL completion parser also rejects incomplete
browser query results. All **63 targeted Node tests** passed. The **34-test backend
Python suite** passed before an additional expiry regression; the subsequent
affected RCA suite passed **11 tests**, with a clean Python typecheck and syntax
check. The app environment check, TypeScript/Vite build and lint passed.
These are local checks, not an authenticated hosted ten-flow pass.

**Local work-coverage clock correction:** the parallel source bridge had assigned
SQL the final combined read clock. It now retains SQL pagination's own completion
clock, so slower telemetry cannot extend the 15-minute approval window.
Oversized pages also fail rather than exceeding the configured per-page bound.
Both defects were reproduced with failing tests before correction.
Production authentication remains unchanged because approval was unavailable.

**1.0.758 follow-up context correction:** direct specialists receive the actual
preceding displayed turn, labelled historical and not current source evidence.
The hosted station-chart/RCA request completed in 1m40s; its follow-up completed in
24.7s. The captured Gauge request includes the original aggregation semantics,
values and clocks, and the returned interpretation confirms sample-weighted means
including BAD samples, not total station power or energy. The rolling-window values
changed between reads. The final UI shows the new source-derived chart, not a full
old-versus-new delta reconciliation; that broader comparison remains unaccepted.
One first-turn malformed filter was visibly rejected and repaired before source I/O.
This is not evidence that every model-generated tool request is correct first time.
The full ten-flow matrix has not been rerun on 1.0.758.

**Architecture correction, local only:** the isolated
[Agent Framework service milestone](../HydroOperationsService/README.md) now has
34 passing local tests and a clean Python typecheck. Tests cover real process exit,
HTTP disconnect, source/step failure, checkpoint-write failure/corruption, concurrent
approval, rejection/expiry, and recovery after both a committed local operation and
the final workflow checkpoint. Provider adapters are synthetic and writes go only to
a local validation ledger. The service is not connected to the production UI or live
Foundry/Fabric providers; cloud hosting/authentication changes are not approved.
This is a durability-boundary proof, not a completed enterprise migration, production
WO write test or performance certification.

**Read-only local integration, October 8:** a separate live source reader verified
numeric ontology generation 2, current capacity and source identities, then read
six STID-mapped T005 telemetry samples. They are explicitly stale. The work-order
read failed with `EXCHANGE_NOT_ENABLED`; it did not return an empty work list.
The installed SDK also requires delegated `Item.Execute.All`, absent from the
inspected CLI Fabric token. Production authentication was not changed. Ten new
Node contract tests and seven of the Python tests cover the source boundary,
including KQL completion errors, pagination, configuration drift, cancellation and
the guarantee that a failed source cannot reach investigation or approval.
These are not new hosted prompt passes or real backend Foundry invocations.
See the [reader command and authentication prerequisites](../HydroOperationsService/README.md#live-source-adapter-verified-access-and-blocking-prerequisites).

**Live local RCA, October 8:** the new backend RCA adapter invoked the actual
Sleuth v13 against six freshly retrieved, but stale, T005 telemetry samples.
The single model call completed in **14.38 seconds** (2,392 input / 1,728 output
tokens), returned a shared-parser-validated report and retained an undetermined
cause. The explicit telemetry-only probe did not query SQL or create a proposal.
Ten additional Python tests cover live-agent schema matching, malformed responses,
invented references, immutable input receipts and checkpoint recovery without another
invocation. A completed work-source read is now independently mandatory for proposals,
and approval expiry is bounded to 15 minutes from that read.
This is not the ten-prompt hosted acceptance matrix or proof of multiple agents working
together. [Invocation IDs and reproducible command](../HydroOperationsService/README.md#live-scientific-rca-adapter).

**1.0.756 exact fleet rerun: bounded comparison passed in 3m54s.** Actual Chief,
Sparky, Gauge and Sleuth Responses invocations produced six recorded handoffs through
Chief. Native and independent direct evidence agreed on eight BAD signals, five
latest-temperature values/units/ranks and all six work records' number, equipment,
title, status and priority. Direct snapshots covered the full 90-signal and
15-temperature inventories. Timestamp precision differences were retained for all
13 signal rows; stale readings were explicitly labelled. There were no unrecognized
tables, missing population comparisons or captured mutations. This does not establish
native query execution provenance, current equipment health or physical causation.

The first deployment attempt encountered a hosting connection reset and failed.
Repeating the same canonical orchestrator returned `SUCCESS` with backend preflight
and POST checks passed; no check was bypassed. The hosted version remained 1.0.756.
At a 1200x900 viewport, the expanded crew occupied a separate 320px-wide rail beside
the 807px-wide messages pane; measured rectangles did not overlap and the document
had no horizontal overflow. Narrow-layout verification on 1.0.754 also kept crew,
messages and composer separate. These are geometry checks, not a cold-load SLA.

**Acceptance boundary:** the full ten-prompt matrix below ran on 1.0.752; targeted
ontology, facility/chart and fleet recoveries ran on 1.0.753, 1.0.754 and 1.0.756.
The entire matrix has not been rerun on the latest build. Flow 9's follow-up
aggregation-metadata qualification remains unresolved. Native compound latency is
still roughly 3-5 minutes, telemetry ingestion remains stale, and approved SQL writes,
actual notification delivery and durable server-side orchestration are not certified
by these runs. The proposed maintenance specialists below have not been created.

**The complete 1.0.752 matrix finished: ten compound first prompts plus two follow-ups.**
Actual Responses agent identities, delegations, native MCP results, direct tool
outputs and approval states were captured, not inferred from animation. There was
one terminal source failure and one completed-but-unacceptable native answer.
Therefore this is **not a ten-of-ten acceptance pass**.

Chief coordinates every row below. Repeated native calls in row 5 use the same Sparky
identity with separately verified Data Agent and Ontology connections.

| Flow | Compound request | Specialists actually invoked | Displayed duration | Observed outcome |
|---|---|---|---|---|
| 1 | Latest BAD fleet, work coverage, investigate most-BAD turbine, conditional draft, final verification | Gauge, Sleuth, Fixer, Gauge | 2m58s | Selected T004 (two BAD signals); inspected records; no evidence-backed new draft. Stale readings disclosed. |
| 2 | Ontology T005 instances, direct telemetry/work, RCA, conditional draft | Sparky | Failed before downstream steps | Actual `ask_ontology` returned upstream HTTP 500. No silent source replacement. |
| 3 | Native SQL work inventory, direct verification, equipment backlog chart, RCA, conditional draft | Sparky, Gauge, Sleuth, Fixer | 2m40s | Eleven open orders; T002/T008 have two each; ID tie-break selects T002. Existing scope covered the proposed purpose; no duplicate draft. |
| 4 | Native latest BAD/top-five raw temperature/work, independent full-population verification, investigate disagreements | Sparky, Gauge, Sleuth | 2m55s | Native agent refused a self-referential request for its own "published results"; comparison correctly remained incomplete. Not accepted. |
| 5 | Ontology facilities, native SQL backlog, direct inventory reconciliation, facility chart | Sparky twice, Gauge | 3m52s | Actual ontology instances, complete direct inventories, 11 open orders, counts 4/4/3. All eleven returned native work records matched direct fields. |
| 6 | Low stock, related work, parts-risk investigation, conditional equipment draft | Gauge, Sleuth, Fixer | 1m27s | Four low-stock parts retained. No authoritative BOM/reservations, so no invented equipment mapping or draft. |
| 7 | Equipment health/work and downtime investigation | Gauge, Sleuth | 59.1s | Evidence-limited RCA; no outage inferred from BAD quality or stale/absent measurements. |
| 8 | T005 notifications/work, investigate inconsistencies, conditional draft | Gauge, Sleuth, Fixer | 58.3s | Actual empty operational reads; no invented issue, notification or draft. |
| 9 | Station-average power chart plus investigation; verify displayed values in follow-up | Gauge, Sleuth; then Gauge | 46.7s + 23.2s | Three chart/table values, units and sample counts match the source. Follow-up received incomplete aggregation metadata and qualified its verification; not a full semantic-verification pass. |
| 10 | Exact-title Low-priority acceptance draft, independent coverage check; investigate fault claims in follow-up | Fixer, Gauge; then Sleuth | 57.0s + 51.8s | Exact title/Low card, no claimed fault, rejected and disabled. Follow-up retained that card without proposing another or claiming SQL creation. |

All completed scenarios had no measured horizontal document overflow. No captured
GraphQL mutation occurred; this is browser request evidence, not a server-side audit.
Source observations remain stale around 06:05 UTC; completed workflows do not establish
current equipment health. Native returned-field comparison does not attest underlying
SQL execution provenance or atomic reads.

**Battle on 1.0.752:** both exact-title Low-priority review cards were produced through
the explicitly labelled shared Foundry approval workflow, not independent engine
implementations. Displayed times were 67.3s and 46.8s. Both cards were rejected and
disabled; no mutation was captured. Proposed acceptance-check wording is editable
suggestion, not an approved OEM procedure.

**Navigation on 1.0.752:** authenticated document reload 2090.4ms; route-shell headings
ready in 1374ms (Overview), 914ms (Telemetry), 31ms (Maintenance), 358ms (Intelligence).
These are warm-session, shell-readiness measurements, not cold login, complete data
readiness or a latency SLA. Compound native retrieval still takes minutes.

**Follow-up source fix deployed in 1.0.753:** reject self-referential Data Agent
"published results"/publication-endpoint assignments before native execution, return
bounded correction guidance, and separate application routing rules from the business
question. The agent's connected tables are allowed; missing clock metadata is disclosed
instead of requiring a fictional publication API. Source failures still propagate.
This passes 164 related tests, production build/typecheck, lint and both bundle checks.
Canonical deployment returned `SUCCESS`, preserved 39 redirects and passed backend
preflight/POST checks. Hosted build identity is `1.0.753 / 12b23a0`.
No infrastructure or persistent-agent version changes were needed.

**1.0.753 reruns:** the exact ontology compound prompt completed all five roles in
4m14s. Actual instance values were returned; the six-hour telemetry query was empty.
Fixer staged an editable data-gap inspection proposal, not a diagnosed physical fault;
it was rejected and no mutation was captured. This point-in-time recovery does not
erase the preceding upstream HTTP 500 or certify preview endpoint reliability.

A fleet variant explicitly requiring full-population reconciliation retrieved actual
native business data in 5m3s, eliminating the earlier publication-endpoint refusal.
It exposed a separate format gap: the agent returned fenced JSON sets instead of
Markdown tables. The comparison correctly remained incomplete. A follow-up parser
accepts flat, consistently shaped JSON `rows` sets, validates declared `row_count`,
preserves nulls, and rejects malformed/nested/inconsistent data rather than guessing.
Replaying the actual native and direct receipts now compares eight BAD signals,
five temperature ranks and six matching work records while preserving timestamp
precision differences. This is offline receipt replay, not yet a hosted pass.
The correction passes 166 related tests, build/typecheck, lint and both bundle checks.

**1.0.754 hosted results:** facility reconciliation completed in 3m6s with actual
ontology instance execution, both native sources, complete direct inventories and all
eleven work records matching on returned fields. Facility counts remained 4/4/3.
The original simple prompt, "Chart average power output per station over the last
24 hours.", completed in 24.3s through Chief/Gauge. All three table/chart means,
MW units, sample counts, BAD counts and stale labels matched the actual tool dataset;
the answer explicitly distinguished sample-weighted readings from generation/energy.

The exact fleet compound prompt completed in 4m13s, but comparison remained incomplete:
its valid Markdown headers used "Raw value", "Latest temperature", "Latest timestamp
(UTC)", "Order number" and "Linked equipment_id". Explicit adapters for these literal
fields now pass replay of both real native formats against direct receipts: eight BAD
signals, five temperature ranks and six work orders, retaining timestamp precision
differences. This follow-up passed 167 related tests, build/typecheck, lint and both
bundle checks. Its hosted result follows below. No mutation was captured in either rerun.

**1.0.755 exact fleet rerun:** completed in 4m8s and compared all six work records,
but telemetry remained unverified. The native answer put instrument IDs in `Signal ID`,
literal OPC UA identifiers in `Signal node`, and temperature units inside numeric cells.
The follow-up decoder requires a literal, unambiguous OPC UA node rather than choosing
the first ID-labelled column. It preserves explicit inline units and rejects conflicts
with a separate unit column. All three captured response formats now pass offline
population/value/rank/unit/work comparison against their real direct receipts; timestamp
precision differences remain. The change passed 169 regressions, build/typecheck, lint
and two bundle checks. The bounded 1.0.756 hosted acceptance is recorded above.

### Earlier hosted failures and their corrections

**1.0.751 targeted reruns are not accepted.** The fleet run stopped on an invalid
model-generated filter array containing an empty string (3m2s). The facility run
stopped on `list_ontology_entities` with an empty `entityName` (28.0s), not on a
business-instance query. The follow-up correction validates filters before source I/O,
returns typed local validation errors for bounded repair without treating them as empty
data, and requires `ask_ontology` for instance requests. Genuine source failures still
propagate. Schema discovery alone cannot satisfy instance retrieval.

Replaying actual native output also exposed a combined `instrument_id / opcua_node_id`
column. The adapter now accepts its explicit, unambiguous literal node; ambiguous
compound identifiers remain unverified. These follow-up changes pass 163 related
tests, build/typecheck, lint and both bundle checks; hosted verification is pending.

**1.0.750 targeted reruns remain failed.** Flow 4 now used a valid native business
query and complete direct snapshots, but incorrectly required another final QA pass
after the requested verify-then-investigate sequence, exhausting Chief's six rounds
after 4m31s. Flow 5 now invoked Gauge, but restricted direct reads to native equipment
IDs (10 rather than 11 open orders), then invented facility mappings in final prose.
Its chart and prose disagreed. Actual receipts, not visual chart presence, exposed this.

The follow-up implementation requires unfiltered, unprojected, untruncated work-order,
equipment and facility inventories for facility-backlog reconciliation. It generates
the final mapping, counts and chart from the same source rows, retains unmatched IDs,
compares actual native tool tables, and never interprets an unrecognized ontology
response as zero facilities. Verify-before-investigation no longer imposes an
unrequested second QA pass; requested final and post-draft verification remain enforced.
These corrections passed 159 related regressions, build/typecheck, lint and both
bundle checks, but require deployment and hosted acceptance.

The 1.0.750 canonical deployment reapplied backend runtime/CORS and passed its endpoint
checks. Authenticated maintenance navigation returned the two actual T002 work orders
without an observed CORS error or horizontal document overflow. This does not establish
ongoing backend availability, a cold-start benchmark or accepted navigation latency.

**1.0.749 implementation (deployed; hosted acceptance in progress):** fleet reconciliation
now requires independent full-population snapshot receipts, preserving recognized
explicit windows and rank limits. Snapshot metadata must attest complete equipment
and instrument inventory via GraphQL pagination metadata; missing or partial inventory
and the telemetry row limit fail explicitly. Quality snapshots enumerate latest readings
before filtering BAD, allowing missing active signals to be disclosed.
The comparison renderer uses actual native tool tables and direct snapshot rows, not
agent paraphrases. It exposes missing/extra members, differing values, ranks, units and
timestamp precision, stale readings and exact direct-work relations. Unrecognized native
tables remain explicitly unverified; native work-order linkage is not certified by the
signal comparison. Supplied KQL checks and actual source execution are rendered from
local validation and execution receipts; using a typed alternative cannot validate the
original query. The hosted KQL probe now reports the original query's local rejection,
the actual corrected execution and its exact stale value, rather than claiming the
original was valid.

The hosted full-population read returned eight BAD signals from 90 active signals
and five hottest readings from 15 temperature signals, with no missing or unresolved
signals. Its native/direct comparison remained incomplete: Chief incorrectly
prescribed the application's local snapshot tool names to the published Data Agent.
That agent could not execute those tools. The renderer did not turn the unavailable
native claims into a comparison pass. The follow-up correction rejects such a
delegation before invocation or slot consumption, separates native business questions
from local tool instructions, and requires the independent direct population evidence
before investigating fleet disagreements.

The 1.0.749 matrix completed ten first prompts, the two follow-ups and the KQL
probe without a terminal runtime failure, but is **not a ten-flow acceptance pass**.
Flow 5 retrieved both native sources but omitted the requested Gauge/direct SQL and
asset verification, despite returning a plausible 4/4/3 facility backlog. The follow-up
correction makes explicitly requested direct reconciliation require Gauge and the
corresponding untruncated source receipts (work orders, equipment and facilities for
facility-level backlog). Native-only prose cannot satisfy that gate or stream a
premature verification claim. Partial GraphQL inventory also marks asset-query results
truncated with an unknown total, rather than certifying a complete empty match.

The ontology T005 compound flow succeeded on 1.0.749: the actual `ask_ontology`
receipt returned T005, its facility/system and six related instrument/signal rows.
Gauge, Sleuth and Fixer then completed their respective steps; no draft was justified.
Displayed duration: 3m17s. This is a successful rerun of the earlier transport failure,
not proof that the preview endpoint cannot fail again.

**1.0.747: runtime recovery is verified; answer-quality acceptance is still failed.**
The complete native/direct reconciliation prompt now returned through
Chief -> Sparky -> Chief -> Gauge -> Chief -> Sleuth -> Chief -> Gauge -> Chief,
with all eight handoffs visible and corresponding actual calls captured. It completed
in **4m17s**, with required RCA tool execution and no terminal runtime failure.
However, it did **not** deliver the requested reconciliation table. Direct verification
read only the 13 nodes identified by the native result, so it did not independently
establish fleet-wide BAD membership or the top-five ranking. Its work-order projection
omitted instrument/node linkage, preventing the requested same-signal comparison.
Do not report this as a passed reconciliation test or an independent full-fleet check.

A separate latest-reading probe returned the correct typed-tool result
(`T003.turbine_temp`, value 75.335, GOOD, timestamp `2026-10-08T06:05:01.359187Z`,
explicitly stale), but incorrectly claimed the supplied invalid KQL was valid without
executing a KQL validation tool. This is another **answer-fidelity failure**, not a
source-query failure. The local guard and correction path have runtime regression
coverage; the hosted probe chose the typed tool directly and did not exercise that guard.

The remaining work is explicit: typed, source-checked comparison output and coverage
checks must replace reliance on free-form claims of verification; native Ontology MCP
transport failures still need resolution; latency is not accepted. Browser-mediated
coordination, current-user-only consent and unverified dispatch/delivery remain separate
enterprise-readiness gaps. Additional planning/parts/downtime/notification specialists
have not been created. Full latest evidence is retained in `reconciliation-1.0.747.json`
and `kql-latest-1.0.747.json`; the full ten-prompt baseline is 1.0.745, not a fresh
ten-prompt pass on 1.0.747.

The 1.0.746 targeted conversation rerun completed flow 10's exact editable card and
physical-fault follow-up in 1m9s / 55.2s. The follow-up used four actual RCA requests,
all with `tool_choice: required`, corrected an invalid assessment and returned the
source-checked cause-undetermined report. The visible conversation retained eight
real outgoing/return handoffs across both turns; the card was rejected.

Flow 4 reached the native Data Agent and Gauge, but failed before RCA on an invalid
model-authored KQL projection: it invented `arg_max_event_time`, `arg_max_value` and
`arg_max_quality` after an unaliased `arg_max`. Eventhouse rejected that query with
HTTP 400. The 1.0.747 source guard rejects this reproduced pattern locally, tells Gauge
the real column names or the equivalent typed latest-reading tool, and uses the
existing bounded input-correction path. Real source failures still propagate.
This correction is deployed, and canonical backend/CORS readiness checks passed.
Full records of the prior failure are retained in
`rca-followup-1.0.746.json` and `reconciliation-1.0.746.json`.

The **1.0.745 compound rerun** completed all ten first prompts plus follow-ups for
flows 9 and 10: **twelve turns, three runtime failures**. Every first prompt requested
multiple specialist capabilities. Actual returned calls, not animation or model claims,
establish the following handoffs (Chief coordinates each arrow):

| Flow | Actual specialist execution | Result on 1.0.745 |
|---|---|---|
| 1: BAD, coverage, RCA, conditional work, independent check | Gauge -> Sleuth -> Fixer -> Gauge | Completed in 1m55s; no-draft decision. |
| 2: Ontology T005 identity/relationships and downstream investigation/work | Sparky; downstream stopped | Failed: native `ask_ontology` LRO returned no MCP JSON-RPC reply. |
| 3: Native backlog, direct reconciliation/chart, RCA and conditional work | Sparky -> Gauge -> Sleuth -> Fixer | Completed in 3m12s. One chart; all 11 orders matched exact source-derived counts across nine equipment IDs. |
| 4: Native/direct BAD-HOT disagreement investigation | Sparky -> Sleuth | Failed after 3m20s: plan-style assignment led to eight prose-only RCA rounds. |
| 5: Both native sources and direct facility backlog/chart | Sparky (Ontology) -> Sparky (Data Agent) -> Gauge | Completed in 2m53s. Both MCP receipts verified; chart counts exactly matched 11 direct orders across three facilities (4/4/3). |
| 6: Parts, related work, risk and conditional draft | Gauge -> Sleuth -> Fixer | Completed in 1m18s; no-draft decision. No unrequested native delegation. |
| 7: T005 telemetry/work and downtime evidence | Gauge -> Sleuth | Completed in 1m11s; no established downtime or authority to stop equipment. |
| 8: Notifications/work, investigation and conditional draft | Gauge -> Sleuth -> Fixer | Completed in 55.1s; no-draft decision, no delivery. |
| 9: Station means chart and scientific review | Gauge -> Sleuth; follow-up Gauge | Completed in 1m7s / 26.1s; one structured chart per turn. |
| 10: Exact Low-priority draft, coverage and physical-fault follow-up | Gauge -> Fixer -> Gauge; follow-up Sleuth | Card completed in 52.8s and was rejected. Follow-up failed after 33.2s: its eighth response supplied invalid `/data/rows/...` references with no correction round left. |

These are browser-displayed execution times, not a controlled concurrency/latency
benchmark. Completed does not certify every statement or causal relevance. The full
records are retained in `compound-flows-1.0.745.json`. No GraphQL mutations were
observed during the captured matrix.

Additional exact hosted checks on 1.0.745:

- The date-filter regression passed: `EQUIP_RTI_T002` inspections on/after September 8
  returned zero rows; before August 1 returned exactly the two July 21 records.
  Both actual predicates and returned IDs/timestamps were captured.
- Battle produced the exact titled Low-priority T005 card in both panes, with actual
  Chief/Fixer/Gauge calls. Both cards were rejected; no GraphQL mutation was observed.
  Displayed times were 60.3s and 69.8s. The UI explicitly says that WO requests route
  through the shared Foundry approval flow, not two independent mutation engines.
- A separate authenticated tab measured first contentful paint at 1.69s and DOM ready
  at 4.44s, with no horizontal overflow at 1024x768. This is not a cold-load or
  cross-tab navigation benchmark and does not establish a performance SLA.

The 1.0.746 RCA correction requires actual tool calls and gives a final invalid structured
report one bounded completion-only repair using existing evidence. No further reads,
weaker reference validation, automatic source retry or native-source substitution is
allowed in that repair. The flow 10 conversation rerun above verifies actual required-tool
requests; the completion-only ninth-round bound has local runtime regression coverage.

The 1.0.744 targeted rerun completed ten turns: both turns of flows 3, 4, 5 and 10,
and first turns of flows 6 and 9. No terminal runtime failure was observed in those
records, but this is **not acceptance**: flow 3 still omitted its requested backlog
chart. Its inspection query also exposed an application filter defect: numeric-prefix
parsing treated July and September ISO timestamps as the same number, 2026.
Previously returned date-filtered inspection evidence must not be relied on without
rerunning the corrected query. Full records are retained as `compound-reruns-1.0.744.json`.

The deployed source correction compares ISO timestamps chronologically, excludes missing
values from ordered comparisons and rejects malformed filter predicates. Gauge gets
one bounded in-assignment correction when it omits an explicitly requested chart
despite having source rows, including the original request and an instruction not to
fabricate missing data. A missing structured chart remains explicitly marked incomplete;
unrelated rows do not force a fabricated chart or repeated delegations. These corrections
require exact hosted revalidation; local checks alone are insufficient. The first
deployment attempt uploaded the bundle but failed the hosting availability check with
a remote connection reset. A canonical retry returned `SUCCESS`, verified backend
preflights/POST readiness and preserved all 39 redirects. Browser build identity is
1.0.745 / `d024940`; the full compound first-turn matrix is being rerun on that code.

The 1.0.742 run completed **all ten compound scenarios and twenty conversation turns**.
Six turns ended in explicit runtime failures. Other turns have content/routing defects;
fourteen non-error responses do **not** mean fourteen acceptance passes. Two browser
sessions were used; times below are the app's displayed execution times, not background-tab
polling delays or a controlled latency benchmark. Every staged test card was rejected.

| Flow | Actual first-turn agents (Chief coordinates every handoff) | First / follow-up | Observed result |
|---|---|---|---|
| 1: BAD, work coverage, RCA, conditional draft, independent check | Gauge -> Sleuth -> Fixer -> Gauge | 100s / 51s | Completed; selected T004, read inspections, retained all eight BAD-signal rows, returned no-draft review and subsequent evidence-limited reassessment. |
| 2: Ontology identity, direct evidence, RCA, conditional work | Sparky; downstream stopped on failure | 21s / 138s | Failed twice: native entity-name pattern error, then native MCP LRO ended without a JSON-RPC reply. No source substitution. |
| 3: Native backlog, SQL comparison/chart, RCA, conditional work | Sparky -> Gauge -> Gauge -> Sleuth | 218s / 29s | First turn failed before Fixer because the extra Gauge invocation exhausted delegation slots; requested chart was missing. Follow-up returned direct work coverage, not a retroactive pass. |
| 4: Native/direct BAD and HOT reconciliation, RCA | Sparky -> Sleuth | 169s / 64s | Chief incorrectly requested a plan-only RCA with no execution; eight prose rounds failed. Follow-up read sources but repeatedly submitted a malformed `/rows/1},{` pointer. |
| 5: Both native sources, direct facility backlog/chart; investigation/draft follow-up | Sparky (Ontology only) -> Gauge | 171s / 108s | First turn omitted the explicitly requested native Data Agent and mislabeled native provenance in prose; seven chart SVGs were detected rather than a concise single result. Follow-up used Sleuth -> Fixer -> Gauge and staged the exact Low-priority review card. |
| 6: Parts/reorder risk, investigation, conditional work | Sparky -> Gauge -> Sleuth -> Fixer | 165s / 47s | Returned evidence-limited no-draft review and BOM/compatibility/lead-time gaps, but native retrieval was not requested and unnecessarily increased latency. |
| 7: Telemetry/work, downtime evidence, independent verification | Gauge -> Sleuth | 125s / 36s | Did not establish downtime from BAD/stale data. Follow-up reported its empty queried window and missing dispatch/calendar sources; concise-answer and query-scope quality remain review items. |
| 8: Notifications/work, investigation, conditional draft; unsent message | Gauge -> Sleuth -> Fixer | 62s / 39s | Returned zero-row source coverage, no-draft review and an explicitly unsent notification with unresolved facts. No delivery was claimed. |
| 9: Station means chart, scientific review, independent check | Gauge -> Sleuth | 76s / 70s | Both turns rendered three MW station means, sample/BAD counts and stale timestamps. No total-generation/energy or physical-fault claim was made. The separate 1.0.741 chart-metadata reference defect still requires its corrected-release rerun. |
| 10: Exact Low-priority acceptance card, coverage; fault-evidence follow-up | Gauge -> Fixer -> Gauge | 53s / 38s | Exact editable card worked; follow-up failed because a Foundry HTTP 200 stream ended without completion. No successful RCA was inferred. |

Each first prompt asked for multiple capabilities; these are not ten isolated agent pings.
Failed native calls still count as failures, not as proof that the requested downstream
orchestration executed. Full request/return records, prompts, field values and source
errors were retained in the session evidence file `compound-flows-1.0.742.json`.

- The explicit Low-priority T005 acceptance card now appeared in 54.7 seconds, with
  its exact requested title, no claimed fault, and Chief -> Gauge -> Fixer -> Gauge
  request/return execution. The card was rejected, not saved. This repairs the
  earlier nested-arguments failure; it does not certify the whole conversation.
- Its RCA follow-up failed after 38.6 seconds: the last HTTP 200 Responses stream
  ended without a completion event. The application surfaced failure and did not
  present a successful assessment. Captured earlier requests in that turn completed;
  the underlying cause of the final incomplete stream is not yet established.
- The station-chart/RCA flow on 1.0.741 took 49.2 seconds, invoked Chief, Gauge and
  Sleuth, and rendered three source-derived station means in MW with stale timestamps
  and BAD sample counts. It also exposed an RCA validation gap: the model cited
  `/chart/inlineCsvData`. The source correction rejects chart metadata in both
  observations and hypothesis references, while preserving actual `/rows/...`
  evidence. A realistic station-tool regression passes; hosted revalidation is pending.
- RCA source identifiers now use the existing collapsible Sources section rather
  than occupying the main answer. Full evidence remains available.
- An ordinary parts request incorrectly invoked Sparky before Gauge, consuming an
  unrequested native call. The correction rejects native delegation unless the current
  user request or retained user source context names Data Agent, Ontology or Fabric IQ.
  Assistant prose cannot supply this permission. Runtime regression verifies that no
  native source is verified or invoked for the rejected delegation.
- Native ontology retrieval failed with `tool_user_error`: `list_ontology_entities`
  rejected an `entityName` that did not match its identifier pattern. The initial UI
  displayed only a generic failure. The stream reader now preserves messages from
  all supported error envelopes and incomplete-response reasons. This is a diagnostics
  correction, not a claim that ontology-native instance retrieval works.
- The native backlog compound flow exhausted four delegation slots after Chief used
  Gauge twice before Sleuth, leaving no slot for the requested Fixer review. The
  coordinator now reserves slots for still-required RCA/work/verification capabilities,
  rejecting the extra factual read before execution. Chief is instructed to include
  requested tables/charts in the first Gauge assignment; Sleuth can read its own
  missing investigation evidence. A runtime regression reproduces the five requested
  delegations and completes the four necessary handoffs without raising the budget.
- Mixed-source completion now requires each explicitly requested native source, not
  merely any successful Sparky invocation. Returned evidence carries its verified
  source identity, and unfulfilled native sources reserve delegation capacity.
- A plan-only RCA assignment that prohibits its evidence reads is rejected back to
  Chief before Sleuth runs. Invalid pointers now report actual valid sibling paths;
  the application does not silently rewrite an assessment or accept malformed evidence.

## Current implementation and limitations

Five persistent **Foundry Prompt Agent definitions** execute through the Responses API.
They are model-plus-instructions-plus-tools, not five continuously running application
processes. The browser runs the coordination loop, dispatches approved read tools using
the signed-in identity, returns their results to the requesting agent, and records actual
handoffs. Chief delegates; specialists return to Chief, not directly to one another.
This is not hosted Agent Framework orchestration, durable background execution or direct A2A.
Closing/reloading the page loses in-memory conversation state.

| Display name / deployed name | Responsibility and triggers | Actual tools and knowledge |
|---|---|---|
| **Chief / hydro-supervisor-agent** | Decompose requests, preserve source and scope, coordinate capability order, synthesize results and caveats | `delegate_to_agent`; shared evidence policy; current conversation history; returned specialist findings. No direct database write tool. |
| **Gauge / hydro-qa-agent** | Factual retrieval, current health, existing work, stock lists, charts, independent factual verification | `hydro_query`: catalog-governed asset/operations reads, KQL and telemetry templates, canonical BAD/HOT snapshots, station-power chart, visualization and 3D model retrieval. |
| **Sleuth / hydro-rca-agent** | Explicit investigation, diagnosis or root-cause analysis | The same read-only data tools; assigned evidence and scientific RCA instructions. No validated OEM manual retrieval corpus or numerical causal model is currently attached. |
| **Fixer / hydro-work-order-agent** | Explicit or conditional requests for editable new work | Read tools plus `propose_work_order` (in-memory card only) and `complete_work_order_review` (no-draft/clarification decision). SQL creation belongs to the human-approved application path, not the agent. |
| **Sparky / hydro-fabric-iq-agent** | Explicit published Data Agent or ontology-native context/query tasks | Two separate native `fabric_iq_preview` connections: `fabriciq-data-agent` and `fabriciq-ontology`. These are distinct sources, not interchangeable fallback tools. |

The source catalog describes allowed entities, columns and KQL functions; it is not a
copy of the underlying business data. Tools retrieve current records. Browser TypeScript
implements validation, query templates, visualization, orchestration and approval state.
The Python deployment orchestrator provisions/readbacks agent definitions and app resources;
it is not an RCA script or a runtime specialist. Fabric setup notebooks provision data,
ontology and native agents. Model-generated text alone cannot create a SQL work order.

Full prior conversation is provided to Chief only. Direct specialists receive their scoped
assignment, the original request as context and current-turn specialist evidence.
Sparky receives only its self-contained native retrieval assignment, not the original
compound workflow or unrelated specialist narrative. Chief must include relevant
prior-turn IDs/constraints in each self-contained assignment.
Current-turn evidence is still carried forward; measured runtime improvements need live
verification rather than inferring speed from reduced input construction.

## Complete routing rules

1. **Selected Data Agent, read-only request:** native published Data Agent runs directly.
   It is not automatically Chief -> Sparky.
2. **Selected Foundry:** Chief chooses the specialist by required capability and requested
   source, not by a forced parade of every agent.
3. **Ordinary facts/current telemetry/existing work/stock counts/charts:** Gauge.
   Reading existing work is not drafting work and does not require Fixer.
4. **Running BAD:** latest raw row per signal first, then literal quality BAD; default
   30-minute window, all matching active turbine signals. "Today" means midnight UTC.
5. **Running HOT:** latest raw turbine-temperature readings, default hottest five over
   30 minutes. Preserve explicit counts, thresholds, windows and scope. Do not average
   or silently exclude non-GOOD signals. Freshness is independent of quality/temperature.
6. **Station mean power readings:** Gauge's `query_station_power` selects exact
   `.power_output` nodes, joins authoritative station/unit metadata, converts W/kW/MW/GW
   to MW and returns a structured chart. It is a sample-weighted mean across readings,
   **not** total station generation, a time-weighted mean or energy. BAD sample counts
   are disclosed; unsupported units/mapping failures prevent a partial chart.
   For latest raw readings of other verified nodes within an explicit window,
   `query_telemetry` supports `aggregation: "latest"`: one latest value, quality and
   measurement timestamp per node, without binning. `TelemetryEnriched` has no node-ID
   column; a direct node filter on it is locally rejected before the source request.
7. **Investigate/diagnose/RCA:** Sleuth, normally after Gauge supplies the factual scope.
   Chief must not treat a factual retrieval as completed RCA.
   Explicit reassessment/review/continuation of an investigation uses the same gate.
8. **Create/prepare/propose a work order, including hyphenated "work-order":** Fixer.
   It resolves identity, checks open work and stages an editable card or an explicit
   no-draft/clarification result. It does not ask an optional-field questionnaire first.
9. **Do not save:** still permits an explicitly requested editable proposal; never
   permits a SQL write. "Do not create a work order" alone is not a mutation request.
10. **Human approval:** the card validates sign-in, target identity and duplicate coverage
    before writing a Draft. Validation and write each have a 90-second bound. Validation
    timeout cannot later start a write. Write timeout is uncertain and cannot replay.
    A late successful acknowledgement updates the card with the returned number.
11. **Explicit published Data Agent request inside Foundry:** Chief -> Sparky using the
    Data Agent connection. **Explicit selected ontology identities/relationships/native
    semantics:** Chief -> Sparky using the Ontology connection.
12. **Mixed source request:** Sparky completes only its native-source step; Gauge does
    direct verification; Sleuth investigates; Fixer evaluates proposed work as needed.
    There is no automatic native-source substitution after failure.
13. **Independent final verification requested:** Gauge after the relevant review/draft.
    Independence here means a separately invoked factual specialist, not independent
    physical measurements or a different model family.
14. **Compound workflow:** use requested capabilities in order; maximum four delegations
    and six Responses rounds for Chief/Sparky, eight for direct specialists (including
    identity/coverage reads, investigation or staging, and final acknowledgement).
    RCA may correct one locally rejected final-round assessment in one additional
    completion-only round; no new reads or relaxed evidence checks are permitted.
    Bounds and actual source failures are
    explicit; no hidden retry of failed writes or forced completion from invented data.
15. **Battle:** the same prompt reaches both configured entries. Read-only comparisons
    can be capacity-safe sequential or parallel. Recognized work-order requests use
    Foundry's approval flow on both sides, sequentially; that is **not** an independent
    Data Agent-versus-Foundry mutation comparison.
16. **Voice:** opt-in dictation appends editable input. Manual Send is required.
    Voice and typed acknowledgements never authorize SQL writes.

Open work excludes Completed/Cancelled. Exact signal/node/instrument matches are
same-signal work; other open orders on the equipment remain equipment-level work.
No mapping is guessed from model prose. Errors and stale evidence remain visible.

### When not to trigger Fabric IQ

Do not invoke Sparky just because the app runs in Fabric, a question mentions an asset,
SQL is involved, or the user wants a graph/chart. Direct telemetry and operational reads
are normally faster and provide deterministic filters. An ontology-native relationship
question or explicit cross-source reconciliation justifies Sparky; ordinary station
power charts do not. Schema discovery alone is not an instance answer.

The selected ontology must be live generation 2 with verified identity and publication.
The Data Agent ontology-source path has a known product limitation; direct table reads
and the direct Ontology connection have separate readiness. Neither graph materialization
nor agent runtime readiness follows from item creation. Native graph materialization uses
the manual ontology Manage graph flow and verified binding described in DEPLOY.

## Scientific RCA and validation

Recommended/current instruction contract:

1. State the observed symptom, equipment/signal identity and UTC interval.
2. Check freshness, units, quality, sampling gaps and whether the measurement can support
   a physical conclusion. BAD is a data-quality fact, not a mechanical diagnosis.
3. Compare with a justified baseline and comparable operating regime; identify changes
   in load, ambient conditions and other confounders when those data exist.
4. Keep multiple plausible hypotheses, including sensor/communication/data problems.
   For each, cite supporting and contradictory observations and evidence still missing.
5. State qualitative confidence with reasons. Do not fabricate failure probabilities,
   thresholds, OEM instructions or certainty from correlation.
6. Identify safe discriminating inspections/tests for qualified maintenance personnel.
   If evidence is insufficient, conclude **cause undetermined**.
7. Chief checks that the report preserves this structure, limitations and source scope.
   Gauge independently checks critical IDs, timestamps, units and work coverage when
   requested. Contradictions remain unresolved until evidence resolves them.

The 1.0.729 implementation relied on instructions and failed this contract. The subsequent
source implementation adds a bounded structured RCA report with exact source references:
unknown receipt IDs, absent JSON-pointer paths, unsupported narrative/threshold fields and
prose-only completion are rejected. Actual values are rendered by the application, not
copied from model prose. Two to four competing hypothesis categories and missing evidence
are required. Chief/Gauge free-text diagnostic claims are excluded from the final RCA.
This is **reference validation, not validation of causal relevance**. It does not certify
baseline comparability, diagnostic thresholds or a causal inference engine. The report
therefore leaves hypotheses untested and cause undetermined. Production reliability work
still needs validated baselines, versioned procedures, approved limits and engineering
review. No equipment controls are changed. A 1.0.731 hosted follow-up verified this
reference/claim boundary, not causal relevance. It also exposed duplicated chart/summary
presentation, fixed in source and regression-tested but not yet hosted-verified.

Proposal priority is now resolved from the explicit operator request before orchestration,
otherwise Medium; model-generated priority cannot escalate it. Conflicting explicit values
produce a visible error without leaving chat busy. The operator can still edit the card.
An additional regression reproduced a priority-scope bug: "List open orders where priority
is High; then prepare an inspection draft" incorrectly selected High for the new draft.
The source fix scopes priority extraction to the new-work assignment or an explicit priority
field/setter, excluding read-only filters and later coverage-check clauses. Those cases now
remain Medium while explicit new-draft High/Low directives are retained. This correction
passed the full focused frontend suite and build; hosted verification remains pending.

## Separate WO analysis from creation?

**Yes as capabilities and permissions; not necessarily an extra agent for every question.**
Gauge should keep simple existing-work queries. Introduce a read-only Work Analysis
specialist for complex recurrence, duplicate/coverage assessment, backlog risk, failure
patterns and closure quality. Keep Fixer as the proposal specialist. The approved
application transaction remains the only creation authority.

This prevents a backlog question accidentally becoming a creation request, allows
different evaluation criteria, and limits write authority. Avoid an additional LLM hop
for a simple "what work is open?" query. Analysis-to-proposal evidence must preserve
exact IDs and findings, followed by fresh validation at approval time.

## Recommended enterprise specialists (not implemented)

| Proposed name / role | Invoke when | Required grounded tools and knowledge | Boundary / reason |
|---|---|---|---|
| **Ledger - Work Analysis** | Recurring failures, duplicate coverage, backlog ageing and closure effectiveness | Read-only WO/history/inspection queries, validated status semantics, evidence joins | Separate analysis from proposals; no create/update tool. |
| **Steward - Maintenance Planning** | Convert verified findings into a feasible maintenance plan | Skill/crew calendars, maintenance plans, qualified procedures and dependencies | Coordinate parts, downtime and work; do not duplicate Chief's general routing. |
| **Quartermaster - Spare Parts** | Parts availability, reorder risk, equipment compatibility | Stock/reservations, BOM, approved substitutes, lead times, supplier records | Existing SQL stock alone cannot prove compatibility or procurement availability. Purchase/reservation needs separate approval. |
| **Scheduler - Downtime Planning** | Outage windows and maintenance scheduling | Availability, dispatch/load constraints, calendars, redundancy, duration estimates | Minimize lost generation with explicit constraints; never infer downtime from BAD quality or issue shutdown commands. |
| **Relay - Notifications** | Approved incident/work-plan communication | Verified recipients/channels, templates, durable outbox, Teams/email delivery receipts | Idempotent sends and delivery evidence; notification configuration is not delivery proof. |
| **Verifier - Evidence Assurance** | High-impact RCA/recommendations or conflicting sources | Deterministic evidence checks, policy rules, source receipt validation | Separate critical review; an extra model opinion is not independent evidence. |

Prioritize Ledger and validated RCA evidence checks before adding planning agents.
Add Quartermaster/Scheduler only when authoritative BOM/calendar/constraint data exist.
Implement Relay with durable delivery tracking before enabling unattended alerts.
Do not add all agents to every flow; route only when the requested capability needs them.
The existing two Fabric Operations Agents remain provisioned with their playbook/actions,
Teams and email capabilities; these proposed Foundry specialists do not replace them.

## Ten multi-turn acceptance flows

Each scenario runs a first turn and a follow-up in the same conversation. Reset only
between scenarios. Capture deployment/agent versions, response IDs, routes, source
receipts, UTC intervals, elapsed time, chart values, errors and proposal outcomes.
Reject test proposals; never recreate/delete the user's confirmed T002 order.
For scenarios 1-5, the full first-turn prompts remain in
[DEPLOY.md](DEPLOY.md#first-five-complex-orchestration-acceptance-scenarios).

### Historical hosted results: October 8, release 1.0.737

All twenty turns have actual response IDs, native-call identities, returned tool evidence,
answers and timings captured in the acceptance evidence. This is execution evidence,
not twenty passing answers. No test proposal was approved or saved.

| # | Scenario | First / follow-up seconds | Actual outcome and remaining defect |
|---|---|---|---|
| 1 | BAD today, investigation, conditional draft, verification | 113.8 / 18.8 | Four specialist handoffs completed, but the RCA presentation omitted some initial signal rows and explicit per-row freshness. Follow-up hit the invalid enriched node-column query. |
| 2 | Ontology identity to maintenance review | 175.7 / 213.3 | Actual `ask_ontology` returned instances. First turn failed HTTP 400 downstream; follow-up bypassed Sleuth and made unsupported diagnostic claims. Direct metadata comparison was not demonstrated. |
| 3 | Native Data Agent backlog to investigation | 159.8 / 38.0 | **All five Foundry agents executed in one turn**; native `DataAgent_RTI_Demo_Agent_V11` and direct SQL returned work inventory. The requested first-turn backlog chart and telemetry/inspection investigation were not completed. Follow-up retained both existing T002 orders and exact signal coverage. |
| 4 | Native/direct BAD and HOT reconciliation | 244.9 / 17.3 | **Wrong native source:** the request explicitly named the Data Agent, but the recorded native tool was `ask_ontology`. Completion text is not source compliance. Follow-up also failed an enriched KQL query. |
| 5 | Facility backlog, then investigation/draft | 189.3 / 38.0 | First turn called both native tools, reconciled eleven orders and displayed one chart. Follow-up failed staging because the model's work-order description exceeded the allowed length; no card was created. |
| 6 | Parts/reorder risk and work coverage | 136.7 / 61.9 | Gauge/Sleuth/Fixer ran, with no draft. Source reads contained twelve parts, but full inventory presentation and relevance were not assured. BOM/compatibility/lead-time gaps remain; no procurement capability is certified. |
| 7 | Evidence for downtime | 85.2 / 65.8 | Gauge/Sleuth returned an undetermined-cause assessment; follow-up explicitly marked stale telemetry and missing dispatch/calendar sources. This does not establish downtime or certify a scheduling integration. Follow-up table formatting was poor. |
| 8 | Notifications, work coverage, unsent message | 59.7 / 36.5 | Empty work/notification reads and no-draft review completed. First turn incorrectly added an unrequested message; follow-up failed to produce the explicitly requested message and asked for authorization again. |
| 9 | Station chart and scientific review | 42.8 / 136.3 | First turn rendered one source-derived MW chart with an undetermined-cause assessment. Follow-up rendered two changed snapshots from separate read times, with excessive extra reads/output; full concise reconciliation is not accepted. |
| 10 | Exact Low-priority draft, then physical-fault review | 79.2 / 71.6 | Exact title/Low card was staged and safely rejected. Follow-up exhausted eight RCA rounds after invalid `/data/rows` and oversized-reference submissions. |

Normal-chat Maximize/Restore via Escape preserved unsent text, with no horizontal page
overflow in the inspected state. Physical microphone recognition remains untested.
The additional Battle approval test completed both panes in 96.4 seconds (51.4 / 44.6).
Each staged the exact `Acceptance battle T005 DO NOT DISPATCH` title with Low priority;
both were rejected, never approved. Battle Maximize/Escape also preserved unsent text
without page overflow. As disclosed in the UI, these are two Foundry approval flows,
not independent Data Agent mutation execution. A fresh hosting-entry fetch still
contained `946c743`, not the pending correction commit.
The table does not certify source execution provenance beyond the recorded tool identities,
nor SQL/combined-source answer correctness merely because a native call completed.

### Fixes following this rerun

The first compound rerun on 1.0.740 failed after 129.7 seconds: Sleuth repeatedly
submitted `rows/1` rather than `/rows/1`. The generic rejection did not explain the
missing leading slash, so all eight rounds were consumed. It also tried to finish
without reading the explicitly requested inspection source. This is a failed acceptance
result, not a passed test because the error was surfaced.
The follow-up correction, deployed as 1.0.741 (`314a9ba`, Sleuth v13), constrains JSON-pointer syntax and array sizes in the actual
tool schema, gives precise leading-slash feedback, and requires a successful inspection
read before completion when that evidence was explicitly requested. Empty inspection
results remain valid evidence; they do not establish a physical fault. Local regression,
typecheck, lint, environment validation and build passed; hosted reruns remain required.

The explicit-draft / independent-verification flow on 1.0.740 also failed (62.2s).
Chief, Gauge and Fixer executed, but Fixer sent `arguments.arguments.equipment_id`
instead of the required flat `arguments.equipment_id`, twice. It then incorrectly treated
that integration error as an operator clarification. No card or SQL record was created.
The correction rejects the extra wrapper with the exact flat retry payload and prevents
an unresolved local input error from becoming a successful no-draft/clarification review.
Regression tests exercise the real proposal tool after correction, not a mock success.

Latest source fixes (deployed in 1.0.740; hosted reruns still pending):

- Chief supplies an explicit native-source selection. Sparky's Responses request restricts
  tools to that connection; the application checks a completed MCP receipt with the exact
  server label. Prose-only and mismatched-source responses fail. Only the selected source is
  verified for an invocation; both connections remain provisioned. Simple single-source
  request checks are not a general natural-language intent classifier.
- A live request using the proposed restriction returned HTTP 200 and a completed
  `DataAgent_RTI_Demo_Agent_V11` call labeled `fabriciq-data-agent`. This verifies that
  request shape against the preview service, not correctness of its reported SQL count.
- Invalid proposal field types/lengths return actionable, bounded model correction before
  target reads or card staging. Limits remain title 200 / description 4000 characters;
  source and target failures still propagate. Approval safety tests include existing
  proposal metadata and duplicate/concurrent-write prevention.
- Requested unsent notifications use source-grounded presentation rather than Chief's
  contradictory authorization request. Empty work reads retain their scoped coverage.
- Editable WO cards render first, with duplicate narrative in closed **Supporting findings
  and sources**. Read-only supporting evidence sections are collapsed without deleting data;
  normal limitations remain visible. Copy/export retains the full response.
- The animated crew defaults to a compact strip. Expanded flow occupies a separate,
  keyboard-resizable 280-420px side panel on desktop; narrow layouts use a bounded panel.
  Composer height is protected. Shared card/evidence presentation also applies to Battle.
- Local validation: **121 targeted tests passed**. Browser checks at 1440x900 and 390x844
  verified card-first order, closed narrative, width adjustment, non-overlapping panes,
  Maximize/Escape input preservation, pause, receipt expansion and rejection with no SQL
  write. The desktop compact panel measured 105px; mobile 123px. No horizontal overflow
  or page errors were observed; the existing Rayfin `useProxy` deprecation warning remains.

These checks do not supersede the failed 1.0.737 hosted scenarios. Maintenance
Planning remains a recommendation, not a sixth deployed agent or certified integration.

- Committed `bdf8301`: safe latest-per-node telemetry template, local rejection of the
  reproduced missing enriched column, and mandatory RCA gating for investigation
  reassessment/review/continuation.
- Additional corrections: explicit RCA pointer/size guidance with actionable rejection
  messages; deterministic preservation of complete returned BAD/HOT, spare-parts and
  notification inventories, including nulls, truncation and snapshot freshness; notification
  intent no longer crosses a sentence boundary from an inspection draft into "do not send".
- Local validation: **101 focused frontend tests + 2 bundle tests**, typecheck, lint,
  environment validation and production build passed; the final notification-scope change
  also passed the 29 directly relevant tests.
- These new corrections are **not hosted-certified**. The deployment attempt required
  interactive Azure sign-in and was initially stopped while awaiting it. The subsequent
  deployment completed as 1.0.740; hosted answer-quality acceptance remains separate.
- Still requiring hosted verification: native-source selection, bounded oversized-proposal
  repair and requested unsent notification completion. Further completeness work remains:
  preserve requested reconciliation/chart conclusions,
  improve long-flow latency, and rerun affected scenarios after deployment.

### Historical prompts and results

| # | First-turn objective / expected capabilities | Follow-up prompt | Observed status before the new fixes |
|---|---|---|---|
| 1 | BAD today -> Gauge -> Sleuth -> Fixer -> Gauge | "For the selected turbine, identify the strongest contradictory evidence and check whether the proposed inspection still has an uncovered purpose. Do not save." | 1.0.726 first turn completed in 3m27s, 189,460 tokens, all four returned WO IDs preserved; card rejected. Earlier attempt failed CORS. Follow-up pending. |
| 2 | Ontology T005 instances -> Sparky -> Gauge -> Sleuth -> Fixer | "Verify the IDs from your ontology answer against direct metadata. Separate missing instance evidence from a verified mismatch, then reassess the investigation." | Previous version hit 180s deadline after scope drift; corrected native scope requires rerun. |
| 3 | Native Data Agent open-work inventory -> Sparky -> Gauge -> Sleuth -> Fixer | "Recheck the selected equipment's work coverage. State whether each proposed action is already covered and explain any source discrepancy without changing data." | Earlier run retrieved ten orders but produced prose-only draft/final-answer loss; corrected workflow requires rerun. |
| 4 | Native/direct BAD and HOT reconciliation -> Sparky -> Gauge -> Sleuth | "For the largest disagreement, independently recheck the exact window and latest-raw semantics; distinguish a real data change from incompatible queries." | Pending. |
| 5 | Ontology facilities + native backlog -> Sparky -> Gauge; follow-up -> Sleuth -> Fixer -> Gauge | Use the exact second turn in DEPLOY; preserve the selected equipment and original source identities. | Pending both turns. |
| 6 | "List spare parts at or below reorder level and related open maintenance work. Investigate whether the evidence supports a parts-related risk; prepare an editable inspection draft only for a verified uncovered equipment issue. Do not order parts or save work." Gauge -> Sleuth -> Fixer | "Independently verify the chosen equipment and work coverage. State what BOM, compatibility and lead-time evidence is missing before any procurement recommendation." | Pending; no Quartermaster exists yet. |
| 7 | "Inspect T005's recent telemetry and open work, then investigate whether downtime can actually be established from those sources. Separate sensor quality from loss of production; do not infer an outage from BAD alone." Gauge -> Sleuth | "Independently verify the evidence and list the missing dispatch/calendar data needed for a downtime plan. Do not schedule or stop equipment." | Pending; no downtime/calendar integration certified. |
| 8 | "Read maintenance notifications and open work for T005. Investigate inconsistencies and prepare an editable inspection draft only if an uncovered issue is supported. Do not save or send a notification." Gauge -> Sleuth -> Fixer | "Verify notification-to-equipment and order coverage. Draft a short human-readable notification with unresolved facts marked, but do not send it." | Pending; delivery must not be claimed. |
| 9 | "Chart average power output per station over the last 24 hours. Investigate what the data quality, sampling and units permit us to conclude about differences. Do not call a low mean an equipment failure without evidence." Gauge -> Sleuth | "Independently verify chart values, units, sample counts and freshness. Explain why mean turbine readings are not total station power or energy." | Original simple chart failed formatting/unit semantics. New structured chart has local regression coverage; live flow pending. |
| 10 | "Check T005 identity and all existing open work. Prepare one editable Low-priority inspection work-order draft titled Acceptance review T005 DO NOT DISPATCH, with no claimed fault. Independently verify coverage. Do not save." Fixer -> Gauge | "Investigate whether any physical fault was established. Keep the draft separate from saved work and do not submit it. Show the evidence gaps." | Prior Battle: Foundry card worked; Data Agent side misrouted hyphenated work-order. Local routing regression fixed; both-pane rerun pending. |

Acceptance requires factual completeness, source/identity/time fidelity, usable UI,
scientific limitations and correct mutation boundaries, not merely a completed status.
Source failure is correctly surfaced behavior but is not a passed answer-quality test.
Record no-draft outcomes as valid only with specific coverage/identity evidence.

## Basic regression evidence

### Latest gate: restored capacity

- At 05:36 UTC on October 8, Fabric reported the assigned capacity Active at **F8**.
  No agent-initiated resume, resize or reassignment occurred.
- Canonical deployment published **1.0.737 / `946c743`**, returned `SUCCESS`, preserved
  39 redirects and passed `/graphql` and `/api/auth/v1/token` browser preflight/POST
  contracts. Chief 8, Gauge 10, Sleuth 11, Fixer 10 and Sparky 7 definitions were read
  back and compared with their configured definitions.
- The authenticated hosted page showed that release and **90/90 fresh signals**.
  The initial page opening included the expired protected-hosting sign-in flow, so its
  elapsed time is not a cold-load performance benchmark.
- The twenty-turn rerun is recorded above. Do not interpret this deployment record as
  completed answer-quality acceptance.

### October 8 runtime findings on 1.0.737

- **Ontology flow 2 failed its first turn (175.7 s).** Sparky's actual `ask_ontology`
  call returned T005 instance context, but Gauge then filtered `TelemetryEnriched` on
  `opcua_node_id`, which that function does not return. Eventhouse rejected the query
  with HTTP 400. The error propagated; Sleuth/Fixer did not complete.
- **Flow 2 follow-up completed but failed answer acceptance (213.3 s).** "Reassess the
  investigation" bypassed the explicit RCA gate. Chief/Gauge presented diagnostic
  threshold claims without a structured Sleuth assessment. The requested direct
  metadata comparison was also not demonstrated. Native request durations were
  **141.2 s and 147.7 s**, separate from browser rendering and direct-source time.
- Corrective source changes add a safe latest-per-node telemetry template, actionable
  local rejection for the reproduced invalid enriched filter, and RCA gating for
  reassessment/review/continuation. **97 focused tests plus two bundle tests**, typecheck,
  lint, environment validation and production build pass. These are not substitutes
  for a hosted rerun of the failed workflow.
- A separate chart/investigation first turn completed in **42.8 s**, with real
  Chief/Gauge/Sleuth execution, one chart, exact source-derived MW values and the
  required undetermined-cause boundary. It does not certify the native or work-order
  branches.

### Latest gate: capacity inactive

- Canonical deployments of `03f886b` (1.0.731) and `d044499` (1.0.732) returned
  `SUCCESS`, verified persistent agent readback and AppBackend contracts, and retained
  all 39 SPA redirects. The last agent versions are Chief 8, Gauge 10, Sleuth 11,
  Fixer 10 and Sparky 7. Current-user consent covers the operator; tenant-wide consent
  still requires an administrator.
- Before the capacity interruption, scenario 9's simple chart completed in **27.657s**:
  one source query, three station values, MW units, matching table/chart and explicit
  stale-reading labels.
- Its scientific follow-up completed in **41.675s** through Sleuth and independent
  Gauge verification. The final answer reported **cause undetermined**, retained actual
  source references and did not present an invented threshold, numerical confidence or
  validated-baseline claim. There was no page-width overflow.
- The follow-up exposed a real presentation defect: the independent read repeated an
  identical chart and station summary, and Sleuth cited a formatted summary as evidence.
  The subsequent source fix deduplicates identical visualizations and equal station
  datasets within the same window, retains both execution receipts and the latest read
  clock, keeps distinct windows separate, and rejects presentation fields as RCA evidence.
  Runtime tests exercise these cases. **This final fix is not deployed yet.**
- The native reconciliation rerun never submitted its prompt: the app reload returned
  HTTP 404 with `CapacityNotActive` at **2026-10-07T23:38:32Z**. Fabric subsequently
  confirmed workspace `9c73201e-b2e5-48eb-81b9-3526d320faca` remains assigned to
  `83981279-dc7a-4ffd-96a0-da463da8026c`, **joademoframework**, **F16**, **Sweden Central**,
  state **Inactive**. No capacity resume, resize or reassignment was performed.
- Follow-up read-only Azure investigation found the actual ARM resource in subscription
  `078b9318-a6c7-4069-9045-a1bee6102239`, resource group `rg_fabric`, state **Paused**,
  provisioning state **Succeeded**. Activity Log records a successful
  `Microsoft.Fabric/capacities/suspend/action` at **23:36:41 UTC**. This establishes an
  explicit suspension, not an app crash; it does not establish the caller's intent.
  Approval to reverse it was unavailable, so capacity was left unchanged.
- The deployment orchestrator now detects a visible non-Active assigned capacity before
  SPA, agent or Rayfin changes. Missing capacity-list visibility is explicitly warned
  rather than imposing a new capacity-admin requirement. Other API failures propagate.
  **87 deployment regression tests passed**, including early-stop/no-mutation,
  pagination, exact identity and permission-preserving cases; Pylance found no syntax
  errors in the changed Python files.
- Live verification of the unchanged canonical deployment command stopped at **step 1/8**
  with an explicit `Inactive, not Active` error and exit code 1. It did not enter SPA,
  agent or Rayfin configuration phases. This verifies the failure guard, not deployment
  success or restoration of hosting.
- Hosted scenarios 1-8 and 10 retain their historical evidence below; they were **not**
  all rerun against the new gate. Native failures are still unresolved, not repaired by
  a successful definition read. Two more scenario turns were completed before interruption,
  bringing the recorded scenario total to 36, in addition to the two Battle invocations.
- After the capacity owner resumes it, run the same canonical deployment command and
  require `SUCCESS`, then rerun native cases 2-5, duplicate-work coverage, conditional
  Medium priority, explicit Low priority and both Battle entry points. Reject test cards.
  No new test SQL record was created during these checks.
- Final local verification passed **93 focused tests plus two built-bundle tests**,
  TypeScript, ESLint, environment validation and production build. The runtime fixtures
  exercise the real coordinator and real proposal-priority tool with isolated source/model
  boundaries; they do not certify live cloud behavior. The eager JavaScript budget stayed
  below 900 KB. The existing lazy 3D chunk-size warning remains.

### Latest hosted checks: 1.0.729

There are now **34 scenario turns across the ten scenarios**, plus the two Battle
invocations and a repeat simple-chart/UI check. The records below supersede only the
specific behavior retested, not every historical failure.

| Check | Measured outcome |
|---|---|
| Deployment | Canonical orchestrator printed SUCCESS and DEPLOYED_APP_URL. GraphQL/token preflights and POST contract checks passed; all 39 redirects preserved. |
| Definitions | Supervisor 8, Q&A 10, RCA 10, Work Order 10, Fabric IQ 7, verified by definition readback (not runtime certification). |
| Scenario 1 | 112s / 88s. Both turns completed. Existing same-signal Draft was correctly treated as open coverage; no duplicate proposal. `today` telemetry executed successfully. |
| Scenario 8 | 95s / 110s. First turn staged a T005 inspection candidate; it was rejected. Follow-up returned an unsent notification with unresolved facts, rather than treating the message as a work order. |
| Scenario 9 chart | 24.2s; repeat 17.2s. One source query, three SVG bars, MW axis, exact table/chart values and original event timestamps. No hourly re-query or kW rewrite. |
| Scenario 9 RCA | 98.8s. **Failed scientific acceptance:** Sleuth invented a 5% diagnostic threshold and described a 30-day aggregate as a same-window/diurnal baseline; Gauge and Chief did not reject those unsupported assumptions. Model agreement is not validation. |
| Battle work-order entry points | 82s total sequential run. Both panes showed editable cards with the exact requested title and Low priority. Both rejected successfully; no test SQL write. These are two Foundry approval runs, not independent native mutation engines. |
| Hosted UI | Three actual chart bars, no horizontal overflow, maximize/restore worked, Escape preserved unsent input. Voice consent notice appeared; microphone capture was not started. |
| Automated checks | 76 focused tests + 3 dictation lifecycle tests + 2 final built-bundle checks passed; typecheck, lint, required-environment validation and Node 24 production build passed. |

The RCA failure is a concrete reason to add deterministic, source-referenced evidence
validation before accepting diagnostic claims. More prompt wording or another agreeing
model is insufficient. One conditional inspection run also selected High priority despite
the instruction-level Medium default; proposal defaults and diagnostic prioritization
still need deterministic enforcement. Human review remains mandatory.

### Corrective 1.0.728 runs

The suite now has **28 live turns across all ten scenarios**, including eight repeated
turns. This is not 28 passes. All test cards were rejected; no SQL work or notification
was created by the suite.

| Flow | First/follow-up seconds | Observed result |
|---|---|---|
| 1 | 161 / 16 | Four-agent first turn completed, but incorrectly justified overlapping work because an existing order was Draft. Follow-up failed because generic telemetry did not accept `today`. Both require correction, not a pass. |
| 6 | 134 / 123 | Completed both turns, correctly distinguished business restock dates from unknown synchronization freshness and refused to infer uncovered parts requirements without BOM/materials. No draft staged. |
| 8 | 109 / 89 | First turn resolved T005 and staged a review card. Follow-up misrouted a requested notification message to Fixer and withheld the message. Not accepted. |
| 9 | 28 / 75 | One station query replaced the extra hourly query, but prose still converted MW to kW and misstated one timestamp. RCA correctly declined to establish a fault, but its baseline/normal-variation claims are not scientifically certified. Chart consistency not accepted. |
| 10 | 48 / 87 | Explicit Low-priority T005 card appeared, coverage verification completed; follow-up reported no established physical fault. Test card rejected. This verifies proposal/analysis, not an SQL write. |

The next correction makes simple single-tool station chart answers source-rendered
instead of relying on a model to recopy units/numbers/times. Compound analysis remains
model-generated. `today` uses the same midnight-UTC query helper across snapshots,
telemetry and station power. Notification-message drafts are kept separate from Fixer.
The evidence policy explicitly forbids treating an unexecuted Draft as an uncovered gap.
These changes require their own hosted checks.

### Completed 1.0.727 baseline: 18 turns, not an accepted suite

These are sequential live browser runs on October 7, 2026. Times are first/follow-up
seconds, rounded. Zero-row results and stale telemetry are not automatically failures,
but completed text alone is not acceptance. No test SQL writes or notifications were sent.

| Flow | Seconds | Actual outcome requiring follow-up |
|---|---|---|
| 1 - BAD / RCA / work / verification | 111 / 12 | Failed: premature second Gauge run consumed the delegation budget; the failed turn then lost its useful conversation context. |
| 2 - Ontology T005 / direct check / RCA | 222 / 118 | Returned identity/telemetry and no-draft decision on stale evidence. Answer nevertheless mentioned a draft template that was not a card. Source provenance and consistent final wording remain acceptance gates. |
| 3 - Data Agent inventory / SQL / RCA | 197 / 79 | Both reads reported 11 open orders; explicit no-draft review cited existing T002 work and stale/truncated telemetry. One inventory chart rendered. End-to-end content review is not yet a full pass. |
| 4 - Native/direct reconciliation | 95 / 121 | Native Data Agent reported output moderation failure. Direct results followed, but cannot establish the requested native comparison. Not accepted. |
| 5 - Native facilities/backlog / draft follow-up | 187 / 29 | Native call timed out at 180s. Follow-up staged a candidate but Fixer exceeded its six-round budget; failed-workflow cleanup withdrew it. |
| 6 - Spare-parts risk / verification | 215 / 78 | Correctly refused to infer equipment coverage without BOM/materials. Incorrectly treated old restock dates as stale inventory and offered queries against unconnected procurement sources. |
| 7 - Downtime / verification | 75 / 69 | Correctly did not infer an outage from BAD/stale/truncated telemetry; zero recent station-power rows. Offered gateway/calendar queries without an integration; follow-up capability wording needs correction. |
| 8 - Notifications / investigation | 37 / 19 | Failed: initial short tag was used as a canonical ID; identity correction/repeated empty reads exhausted RCA rounds. Follow-up exhausted Chief rounds. |
| 9 - Station power / scientific RCA | Not run in this batch | Separate exact simple chart baseline took 50s and rendered correct bars but described an unnecessary hourly dataset. Correction and two-turn rerun required. |
| 10 - Explicit editable draft / verification | 23 / 15 | Failed: repeated empty work reads exhausted Gauge's budget; follow-up lost failed-turn context. |

Follow-up code corrects delegation ordering, retains explicitly incomplete turn context,
propagates failed native MCP items, and preserves confirmed approval acknowledgements.
Direct specialists have eight bounded rounds, with remaining-budget guidance, correct
tag/ID resolution, reuse of successful empty results and immediate terminal no-draft
review. Chart scope, business-date freshness and unavailable-source wording are explicit.
These are corrections to verify live, **not retroactive passes for the baseline**.

- Version **1.0.727**, commit `2057e77`, passed canonical deployment: backend GraphQL/
  token preflights and POST readiness, all 39 redirects preserved. The existing operator
  has consent; tenant-wide rollout still requires administrator consent. Definition
  readback: Supervisor 6, Q&A 8, RCA 8, Work Order 8, Fabric IQ 5.
- Hosted reload after capacity scaling: TTFB 1.601s, DOM ready 3.693s, load 3.706s,
  first contentful paint 3.748s; STID and 90 signals connected. Previously observed
  page load was 43.6s. These are individual observations, not p95 or code-only gains.
- Hosted first visits: Maintenance 900ms, Administration 882ms, Intelligence 866ms.
  Repeat visits: Overview 32ms, Maintenance 22ms, Intelligence 20ms; no horizontal overflow.
- Hosted exact simple station-power prompt took 50.1s. Three correct structured bars
  rendered, but the agent unnecessarily queried hourly values and described that different
  dataset. **Answer consistency failed.** Follow-up changes expose the rendered chart
  specification to the agent and explicitly preserve one-mean-per-station scope; rerun required.
- Hosted pre-fix map reproduced `overflow: visible` and tile `position: static`.
  Local and hosted 1.0.727 corrected component: `overflow: hidden`, tile `position: absolute`.
- Local browser fixture: no horizontal overflow at 1440px or 390px, compact flow,
  keyboard-operable height slider, 140px flow clamp, composer visible when maximized,
  Escape restores and preserves unsent text. Two real SVG bars render from structured
  chart data; no missing-dataset warning.
- Approval timeout tests cover no late validation write, retry isolation, uncertain-write
  replay prevention, late success and late failure. These are deterministic unit tests,
  not proof of a successful hosted SQL transaction.
  An additional deadline/acknowledgement race test verifies that confirmed creation cannot
  be overwritten with an uncertain status.
- The first repair release passed 94 focused tests, typecheck, lint, Node 24 production
  build and two built-bundle checks. Subsequent targeted chart/approval checks passed
  60 tests, typecheck and lint. Neither suite substitutes for the live flow matrix.
- Capacity incident: prior HTML TTFB 21.7s, tiny scripts approximately 21s, Eventhouse
  HTTP 429. A single uncached HTML fetch after scale-up took 1.46s. That is not a full
  cold/warm performance benchmark or proof that source throttling cleared.
- Opt-in voice and maximize behavior were implemented earlier; physical microphone
  and live speech-service acceptance remain unverified.

## Remaining acceptance gates

These gates reflect the latest recorded 1.0.758 deployment and local backend work;
the earlier inactive-capacity and 1.0.729-1.0.731 failures above are historical.

- Deploy and verify the latest local old/new station comparison. Its 63 targeted
  Node tests and app build passed; that does not certify the hosted follow-up.
- Repeat all ten compound flows and their follow-ups on one final hosted build.
  Targeted recoveries on 1.0.753-1.0.758 do not replace a full matrix. Native
  failures must remain visible, without direct-source substitution.
- Complete the Agent Framework migration: supported backend delegated/OBO access,
  live provider/proposal composition, production hosting/storage, SPA run/reconnect
  integration and deployment integration. The default local service is not ready
  for production; SQL exchange and named-scope acquisition remain blocked.
- Verify assigned-capacity health at each deployment. The owner restored it for
  the latest deployment; no capacity change is authorized by these instructions.
- Verify ingestion before calling conditions current: source events stopped
  advancing around 06:05 UTC during the latest hosted series, and the subsequent
  local T005 diagnostic still returned stale measurements. Successful reads do
  not make them current.
- Add approved baselines, limits and engineering review for causal relevance.
  Structured references and an undetermined conclusion are not a causal model.
- Verify authorized SQL creation, idempotency and uncertain-write reconciliation.
  Latest hosted cards preserved explicit Low priority and rejection; a new
  approved SQL write was deliberately not performed in this acceptance suite.
- Add durable notification delivery receipts and the authoritative BOM/calendar/
  constraint integrations before claiming maintenance-planning readiness.
- Complete physical microphone recognition and tenant-wide consent for enterprise
  rollout. Browser feature detection and permission notice are not audio recognition.
- Benchmark repeated cold/warm loads and agent latency under representative capacity;
  single-run improvements are not p95/SLA evidence.

Do not claim zero bugs, scientifically established causation or notification delivery
readiness from these checks.
