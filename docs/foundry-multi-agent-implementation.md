# Foundry and multi-agent implementation record

Work period: **October 7-8, 2026**. This is the end-to-end record of the
implementation, corrections, measured results and unfinished migration, grounded
in repository commits and the linked acceptance records. It is not a release
certificate. Earlier runs are retained as historical evidence, not relabelled
as passes after a later code change.

## 1. Are we using Microsoft Agent Framework?

**Yes in the new local Python backend; no in the currently hosted Fabric app.**

| Surface | Current implementation | Verified boundary |
|---|---|---|
| Hosted Fabric app | Release **1.0.758 / `4c0e1a1`**, browser-coordinated persistent Foundry Prompt Agents | Deployment, selected hosted compound flows and UI checks; not complete acceptance |
| Local orchestration service | Actual **Microsoft Agent Framework 1.19.0** executors, workflow edges and file checkpoints, with a SQLite run/approval journal | Durable recovery and approval tests; not distributed or production hosting |
| Backend live sources | Separate read-only Fabric adapter with source-identity checks | Live v2 identity and six T005 telemetry samples read; samples stale; SQL access blocked |
| Backend live RCA | Version-pinned call to persistent Sleuth v13 with the shared structured-report validator | One real telemetry-only specialist invocation; not a full multi-agent provider |
| Backend proposal/SQL/SPA integration | Not complete | Default HTTP service deliberately returns 503 for readiness/run submission without adapters |
| Latest chart comparison correction | Locally implemented and regression-tested, **not deployed** | Historical/current numerical comparison and shared KQL response validation; no hosted acceptance yet |

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

These changes are locally verified, not yet hosted:

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
2. Rayfin delegated SQL exchange returned **`EXCHANGE_NOT_ENABLED`**.
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
7. Deploy/test the latest chart comparison, then repeat all ten compound flows,
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
