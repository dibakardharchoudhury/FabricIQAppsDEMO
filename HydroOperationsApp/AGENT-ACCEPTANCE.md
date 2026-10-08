# Hydro Intelligence: implementation, routing and acceptance

This report distinguishes implemented behavior, observed runtime results and proposals.
**The ten-flow suite is not yet accepted.** Deployment success and unit tests are not
proof of agent answers, causal diagnosis, SQL mutations or notification delivery.
Deployment entry points: [DEPLOY.md](DEPLOY.md). Source architecture: [README.md](README.md).

**Current deployed release: 1.0.737 (`946c743`).** On October 8 the owner-restored
capacity was verified Active at F8, and canonical deployment completed successfully.
The RCA presentation and priority-scope corrections in that release are deployed.
The October 8 rerun completed **ten scenarios / twenty turns**, and is **not accepted**.
Telemetry was 90/90 fresh at 05:46 UTC, but subsequently stopped advancing around
06:05 UTC; the app correctly changed its header to stale. Do not treat the earlier
freshness observation as current health.
Creation/configuration readback is verified for the five Foundry agents, but complete
multi-agent runtime correctness is not yet certified.

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
    identity/coverage reads, investigation or staging, and final acknowledgement). Bounds and actual source failures are
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

### Latest hosted results: October 8, release 1.0.737

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

Latest source fixes (deployment and hosted reruns still pending):

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

These checks do not supersede the failed hosted scenarios below/above. Maintenance
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
  interactive Azure sign-in and was stopped while awaiting it; no `SUCCESS` was observed.
  The acceptance browser remained on 1.0.737.
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

- Deploy the final presentation correction and repeat the structured RCA flow. Local runtime tests reject
  prose/invalid reports, exclude Chief's unsupported threshold, propagate a source failure
  even after report submission, and preserve a single failed-turn history entry.
  The 1.0.731 hosted reference/claim boundary passed, but causal relevance is unvalidated
  and the complete ten-flow suite remains unaccepted. Historical 1.0.729 RCA remains failed.
- Resume the assigned Fabric capacity through its authorized owner before deployment or
  additional hosted acceptance. Do not silently choose another capacity or endpoint.
- Resolve native Data Agent output-moderation and Ontology/native timeout cases with
  their service traces, then repeat scenarios 2-5. Do not substitute a direct query and
  label the native-source workflow successful.
- Verify ingestion before calling conditions current: the final hosted checks showed
  0/90 signals fresh, with latest source events around 20:20 UTC. Successful reads do
  not make those readings live.
- Verify the new deterministic proposal-priority policy in hosted compound flows. Approval
  phase/race logic has unit coverage and rejection is hosted-tested; a new hosted
  approved SQL write was deliberately not performed in this acceptance suite.
- Complete physical microphone recognition and tenant-wide consent for enterprise
  rollout. Browser feature detection and permission notice are not audio recognition.
- Benchmark repeated cold/warm loads and agent latency under representative capacity;
  single-run improvements are not p95/SLA evidence.

Do not claim zero bugs, scientifically established causation or notification delivery
readiness from these checks.
