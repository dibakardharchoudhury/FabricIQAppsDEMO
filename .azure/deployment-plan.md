# Feature workspace infrastructure deployment

> **Status:** Validated - bounded semantic plan correction; publication pending

## Current scoped architecture hardening

The existing approved tenant, FEATURE workspace, Foundry project/model and F8
capacity are unchanged. No infrastructure, permissions, source topology or
operational-data writes are requested. Publish through the canonical Python
orchestrator only, after the normal clean-branch and validation gates.

The runtime now pins only selected agent-mode capabilities; reader-only work
has no remote specialist dependency. Consecutive independent readers run in one
Framework superstep with a four-read concurrency cap and sibling cancellation.
Q&A has no RCA/maintenance-authoring skill. Models receive only their pinned
role/current-turn tool schemas. Semantic `analysis_requested` prevents an
automatic narrative model after factual retrieval, while permitting one
tool-less weather interpretation without a Weather agent loop. Planning and
briefing HTTP clients close explicitly. An absolute ten-minute execution budget
is separate from credential admission. Audit manifests identify actual
providers and session-local persistence; the UI hides unused capabilities.
Authenticated acceptance also exposed a Responses-only legacy midnight/BAD
bundle requirement on a broader freshness/diagnostic request. Semantic native
execution now uses its explicit evidence contract, consistently with the MCP
compatibility branch; legacy completeness validation remains unchanged.
Native call timeout is bounded by the overall execution budget, not the
credential admission lease.
Typed/catalog-invalid model plans receive at most one validation-feedback
correction inside the same 60-second planner budget, with no business execution
before acceptance. Immutable attempt receipts survive interrupted correction;
the supervisor reports the actual model-round count. Repeated invalid plans
fail closed. Missing planner configuration fails before client creation.

All validation checks pass for the scoped application change:
- [x] 1. Core Validation (CLI, auth, build, validate, what-if): approved tenant
  and subscription verified; environment validation, TypeScript, targeted
  ESLint and `git diff --check` pass. The final backend run completed 215 cases:
  214 passed and the opt-in live evaluator was skipped. A separate eleven-case
  real-model evaluation passed, including negation, clarification, native
  ontology routing, diagnostic-only drafts, fault-gated repairs and reader-only
  forecast comparison and the exact freshness-draft acceptance prompt.
  Final planning times were approximately 5.7-15.5 seconds;
  this is not an end-to-end latency SLO. Five pre-existing backend type
  diagnostics were reduced to four by explicitly requiring configured planning
  models before client creation; no new diagnostic was introduced.
  No Bicep or ARM infrastructure delta exists, so build/validate/what-if of an
  infrastructure template is not applicable.
- [x] 2. Docker Build (if containerized): unchanged deny-by-default Docker
  context and dependency manifests. The canonical orchestrator owns the
  source-fingerprinted ACR build and image/readiness verification; no standalone
  Docker or alternative azd deployment is authorized.
- [x] 3. Azure Policy Validation: unchanged region, SKU, networking, identity,
  permissions and existing resource boundary; no resource-policy delta.

Role Assignment Verification: no role assignments are generated or modified.
Runtime source identity, native answer fidelity and separate human-approved
write contracts remain unchanged. Current tests cover approval idempotency,
failed/expired approval and uncertain-write reconciliation with no live writes.
Twelve frontend agent/Weather contracts also pass. Production session receipts,
locks and checkpoints are not independent-replica failover or distributed
exactly-once execution. Verified centralized trace ingestion, distributed
persistence/admission and sustained load testing remain explicit rollout gates.
The first publication (app 1.0.1173 / `f1784e6`, coordinator v42) passed
authenticated facility-reader, native count, Weather and RCA checks.
The broader diagnostic draft failed at the misplaced legacy native guard before
Work Order ran. Its audit and container logs were inspected; no write occurred.
The exact prompt reproduces the failure in a new regression, which passes after
the scoped fix alongside the legacy completeness/fidelity checks.
Publication and repeat signed-browser diagnostic acceptance of that correction
remain pending; earlier successful runs do not certify it.
The v43 repeat (`340c2431-8f75-4fce-90c3-a3b70c6c97ad`) stopped before source
execution because the model put reads on an agent-mode Work Order step.
An exact cross-field regression reproduces this rejection; bounded plan repair
and interrupted-attempt recovery now pass regression and live-planner checks.

## Semantic orchestration and Fabric IQ acceptance

Current work: replace phrase-dependent hosted routing with a bounded typed
semantic plan, validated against the existing source catalog, evidence,
specialist and human-approval contracts. Inspect the published Data Agent and
Ontology metadata and compare three read-only questions through the native
Fabric IQ path and the underlying Data Agent. Preserve failures and source
provenance; do not silently substitute direct-source answers for native failures.

Target remains the approved FEATURE tenant
`ad340c84-1886-4202-a483-2da2cb9168eb`, workspace
`c7da7c28-0dee-4f45-bc9e-5628bb882732`, subscription
`dac16501-5c35-4c91-aff0-11af8de17f2e`, Sweden Central.
Use existing Foundry resources/model and canonical Python orchestrator only.
No new Azure infrastructure, identity grants, redirect changes by hand, or
automatic production writes. Earlier acceptance below is historical and does
not validate this new architecture change.

Implementation decisions: retain the existing five specialist capabilities, not
five mandatory calls. A tool-less typed planner inside the existing coordinator
interprets arbitrary language; deterministic code owns source permissions,
bounded execution, evidence verification, checkpointing and human-approved
writes. Native Fabric IQ assignments stay business-language questions and do
not receive irrelevant application tool schemas or presentation instructions.
Independent read-only steps may run concurrently; investigation and maintenance
dependencies remain explicit. No topology or availability infrastructure change.
Recipe: existing AZCLI/Python orchestrator; do not generate alternative IaC.

The user explicitly requested implementation and optimization. The focused
approval/context form returned user-unavailable with an instruction to choose
the pragmatic option; proceed on the unchanged target and requested design.

Live metadata audit: all 18 Ontology entities have descriptions and synonyms,
and six business rules exist. The audit found the Data Agent selected only five
entities and used column-list descriptions. The producer is repaired; NB009
publication and independent readback now confirm all 18 selected entities in
draft and published context. The enriched Ontology and bindings were preserved.

Preparation gates:
- [x] Confirm the role/semantic-routing design and unchanged Azure context.
- [x] Reproduce Fabric IQ failures and inspect live Ontology enrichment.
- [x] Implement typed routing and source-specific corrections with regressions.
- [x] Validate pinned Agent Framework execution, persistence and source gates.
- [x] Complete Azure validation before canonical publication.
- [ ] Record authenticated three-prompt parity, routing and timing acceptance.

Canonical publication completed: app 1.0.1147 / `a2ccda0`, coordinator v28,
Fabric IQ v3; final `SUCCESS` and `DEPLOYED_APP_URL` were returned after endpoint,
CORS and POST readiness checks. Authenticated browser access was restored.
Two native comparisons reached the Data Agent with corrected plain-language
input and matching facility/relationship facts, but both failed with provider
output-moderation processing errors. The third native quality/work comparison
reproduced that error; its direct Fabric-editor counterpart failed generically.
Native acceptance remains blocked. No error annotation was ignored and no
fallback or write occurred. A separate inventory-reader smoke test passed in
7.8s; Weather and RCA paraphrases selected their intended specialists and
completed in 37.9s and about 81s respectively, without drafts.
See the [live acceptance record](../docs/foundry-multi-agent-implementation.md#live-publication-and-acceptance)
for exact run/response IDs. Eventhouse UI grouping remains unresolved.

### Section 7: Validation Proof - semantic implementation

Current update, 2026-10-28, commit `8f908c1`: the authenticated v1.0.1167 /
coordinator v39 path passed native count, ontology relationship and quality/work
acceptance, preserving source text and SHA. Five editable review-only
telemetry-availability drafts were verified with no production writes. This
update removes provider document/schema metadata from business datasets while
retaining immutable receipts and typed source-row projections.
All 132 supervisor tests passed; the broader run exposed an obsolete factory
fixture missing the newly required verified workload origin. After updating that
fixture and adding missing-origin rejection, all 35 source-auth tests passed.
Commands: `.venv\Scripts\python.exe -m unittest test_foundry_supervisor
test_source_auth -q`, then `-m unittest test_source_auth -q`; targeted Python
`compileall`, Pylance syntax checks, `npm run validate-env`, and `git diff --check`
passed. Pyright retains its five existing diagnostics, with no new errors.
Azure CLI confirmed the approved tenant/subscription and Active F8 capacity.
No IaC, resource type, SKU, role assignment or policy boundary changes are
requested; infrastructure what-if and a separate Docker deployment do not apply.
The canonical orchestrator owns the source-fingerprinted image build, hosted
publication, connector convergence and endpoint checks; acceptance of this latest
dataset correction remains required after that publication.
That publication succeeded as v1.0.1169 / coordinator v40. Its authenticated
review-only run completed Fabric IQ and Work Order execution, then exposed a
renderer error for timezone-less native source dates. The renderer now preserves
those dates as text, while explicitly timezone-aware strings retain timestamp
presentation. The source values and native answer are unchanged. All 137
supervisor/presentation tests pass, including the exact final table-rendering
regression; required environment validation passes and Pyright remains at the
same five baseline diagnostics. No infrastructure or authorization delta was
introduced. Canonical publication completed as v1.0.1170 / coordinator v41
(`bf9e164`) with endpoint/CORS checks passing. Authenticated run
`cc45133f-d5a0-4fe7-acbc-c8022073f2ec` verified five editable, review-only
telemetry-availability drafts for T004/T005/T007/T009/T014, no QA/RCA hop,
unchanged native source text/SHA, no metadata columns and no production writes.

An additional native parity check found a real federation-intent error: run
`41fac50c-1c86-46df-bb50-31090dca3998` reported zero work despite ten SQL rows
because a zero-inclusive count was compiled as AntiJoin. Notebook-owned source
guidance now explicitly puts positive all-parent aggregation in the federation
question and status predicates in source sub-questions; the original user
question and source answer are not rewritten by the app. Draft/published
readback preserved the ontology and operational SQL adapter. The policy remains
within 5,500 characters / 700 words; all 92 ontology/Data Agent contracts pass.
Final audited count runs `f9feb37d-9574-41f4-a789-383d760a7106` and
`6a6d4aa0-ecae-4e39-bb45-3f220c078ec6` used successful nondegraded Aggregation
receipts, returning Sloy 3 / Foyers 3 / Pitlochry 4. The unmatched-population
control `a9f32036-e43b-4212-a8d7-abb7caceee6a` correctly returned no facilities.
All preserve exact native presentation and SHA; no production writes occurred.
Current telemetry is stale; these drafts investigate availability, not a proven
mechanical fault or invented current-day BAD events.

All validation checks pass:
- [x] 1. Core Validation (CLI, auth, build, validate, what-if): existing-resource
  application update, no generated Bicep or infrastructure delta; Bicep
  validate/what-if are not applicable. Runtime/unit/build evidence is below.
- [x] 2. Docker Build (if containerized): existing orchestrator owns the ACR
  image build, context closure and image readback. Its 43 packaging/deployment
  tests pass; no standalone alternate Docker/azd deployment is used.
- [x] 3. Azure Policy Validation: no new resource type, region, networking,
  SKU or role assignment. Existing approved policy boundary is unchanged.

Role Assignment Verification: no generated or modified role assignments.
Existing delegated source credentials and native connection remain unchanged;
all source-auth checks apart from the repaired cold-start fixture passed the
full run, and the repaired production-factory/readiness fixtures pass separately.

- Six real-model typed plans compiled against the actual source catalog: count,
  Weather, ontology aliases/relationships, RCA, clarification and negated drafting.
- Full backend discovery ran 282 tests. Its only failing factory fixture still
  expected keyword admission; that fixture now verifies semantic composition.
  The affected factory and real cold-start test pass in a focused rerun.
  Other source-auth/semantic tests passed; the cold-start test now allows bounded
  SDK import time on Windows, without relaxing source/authorization assertions.
- 92 Node prompt/presentation/weather/native-error/transport tests pass.
- 111 Ontology tests and 43 hosted packaging/deployment tests pass.
- Node 24 validate-env, scoped ESLint and production TypeScript/Vite build pass;
  Python compilation and editor diagnostics pass.
- NB009 publication preserved all live dependencies and executable source
  readback. Job `e8e697e2-d370-4b85-83c5-892b6c253686` completed successfully.
  Separate REST readback confirms all 18 entities selected in both draft and
  published Data Agent context; Lakehouse, Kusto and SQL mounts remain.
- App/source runtime acceptance was attempted after canonical deployment with
  restored signed-browser access. Ordinary reader execution passed; three native
  comparisons reproduced the provider error and cannot pass acceptance.
- Final full backend rerun: all 283 tests passed in 258.226s.
- Further native diagnostics verified documented connection audiences, enabled
  default safety filters and unrestricted account network configuration.
  Existing Foundry telemetry reports safe prompt/completion categories while
  native tool outputs retain moderation-processing errors. Provider escalation
  remains required; no ticket was created without contact/authorization details.

Commands: root `.venv\Scripts\python.exe -m unittest discover -s
HydroOperationsService\tests`; affected source-auth factory/readiness and semantic
test selectors rerun separately after fixture/cold-start repair; `node --test`
with answer-presentation, weather-agent, rca-evidence and hosted-transport test
files; `python -m unittest test_ontology_agent_capabilities
test_ontology_gen2.EventhouseTests test_ontology_semantics` (UTF-8 output);
`python -m unittest test_deploy_hosted_agent`; `npm run validate-env`,
scoped `npx eslint`, `npm run build`, Python `compileall`, editor diagnostics
and `git diff --check`. Azure CLI readback confirms the approved enabled
subscription and tenant. These are local/predeployment proofs, not native
three-prompt runtime acceptance.

## Facility-count acceptance follow-up

Resolve the live planner failure discovered during implicit-access acceptance.
The existing intent producer permits facilities but requires no evidence for a
count request; compilation then injects unrequested telemetry and correctly
fails its guard. Derive unresolved count evidence from the existing permitted
dataset classification, retain population coverage for counts, and keep the
unexpected-evidence guard intact. Test the original question and paraphrases,
then repeat canonical deployment and signed-browser acceptance on the unchanged
FEATURE target below. No new permissions, infrastructure or write-policy change.
The user explicitly requested autonomous completion of the outstanding failure.
Earlier deployment/authorization receipts remain historical.

### Section 7: Validation Proof - facility-count follow-up

Validation date: 2026-10-27; existing canonical Python/Azure CLI recipe,
same approved tenant/workspace/subscription/region as the receipts below.

All validation checks pass:
- [x] Core: 128 Node source/routing/weather/authorization regressions pass,
  including the formerly failing exact browser question and six population-count
  variants. Requirements are exact `<dataset>_population_direct` contracts;
  no unrelated telemetry, specialist or native execution is introduced.
- [x] Backend: 119 supervisor tests pass with repository `PYTHONPATH` and Node 24.
  Unexpected-evidence rejection, source coverage and human approval remain intact.
- [x] Build: scoped ESLint, `npm run validate-env`, and production TypeScript/Vite
  build pass. Existing chunk/mixed-import warnings remain unchanged.
- [x] Container: 43 hosted packaging/fingerprint tests pass. Dockerfile,
  lockfiles and COPY closure are unchanged; the
  modified packaged source bridge requires a fresh ACR source-fingerprinted
  image. Its canonical build/activation is a hard deployment gate.
- [x] Static roles/policy: no identity, scope, permission, source operation,
  infrastructure, network, region or write-policy change. The earlier live
  same-target policy/project-role readbacks remain applicable. No ARM template,
  what-if or quota provisioning applies.

Repro: the added count test failed on the exact browser prompt because required
evidence was empty. After the producer fix all 128 tests pass, including the
strict complete-contract validation. Commands: Node 24 `node --import tsx --test`
over source, operational-routing, weather, orchestration, source-access,
transport and Entra tests; `python -m unittest discover -s
HydroOperationsService\tests -p test_foundry_supervisor.py` with the service's
`src` on `PYTHONPATH`; scoped ESLint; environment validation; production build.
Actual hosted facility-count acceptance passed as recorded below, independently
of these local tests.

### Hosted acceptance - facility-count follow-up

- The first canonical attempt built ACR image `src-028b83cbb98cf220` successfully
  (`dtq`) but stopped during Azure CLI registry authentication on a transient
  `RemoteDisconnected` token-network error. No validation was bypassed.
- Re-running the same canonical command reused the built image and exited 0
  with final `SUCCESS` and `DEPLOYED_APP_URL` at
  `2026-10-27T10:18:31.327005Z`, **591.469 seconds** for the successful attempt.
  URL: `https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
- Signed browser verified **v1.0.1145 / 3a64402**, coordinator **v27**.
  Five specialist definitions, all 42 redirects, source permissions and the
  explicit write-approval policy were preserved. AppBackend runtime/SQL/CORS
  and GraphQL, token and three connector preflight/POST readiness checks passed.
- In a fresh signed browser session, the exact previously failed facility-count
  question returned HTTP 200 in **13,494 ms**, with **one run request**, **zero
  sign-in popups**, no access setup control and no alert. The certified answer
  reported **3 facilities**, matching all three rendered source rows:
  `FACILITY_RTI_001`, `FACILITY_RTI_002`, `FACILITY_RTI_003`; no truncation.
- A second explicit question, “How many facilities are registered?”, returned
  HTTP 200 in **7,048 ms**, again one request, zero sign-in popups, no alert and
  the same three source rows. Session:
  `296e05fd-a9fb-4705-aff1-7449f11c950b`.
- These are two observed browser samples, not a latency SLO. The old failed run
  remains an immutable failure receipt; only deliberate post-fix tests were sent.
  No work-order action, approval, rejection or production write was performed.
- Live Azure MCP readback confirmed unchanged project-scoped Foundry User access.
  The facility-count guard failure is resolved without relaxing that guard.

## Implicit agent access

Remove the separate agent-access control from chat and comparison views.
Normal operator actions reuse/refresh the signed-in session silently; when
Entra actually requires interaction, request it within that action, with one
consent step covering the remaining audiences. Verify each audience separately
afterwards. Background warm-up and run recovery remain silent-only.

Do not broaden permissions, bypass tenant/lease validation, automatically replay
failed invocations, or weaken explicit human approval for operational writes.
Reuse the canonical repository deployment recipe and existing FEATURE target:
tenant `ad340c84-1886-4202-a483-2da2cb9168eb`, workspace
`c7da7c28-0dee-4f45-bc9e-5628bb882732`, Sweden Central, subscription
`ME-MngEnvMCAP218279-didharch-2` (`dac16501-5c35-4c91-aff0-11af8de17f2e`).
No new resources or RBAC changes. Shared runtime source changes may require
the orchestrator to rebuild the existing hosted image.

Publication/context confirmation was offered; the user was unavailable.
Continue with the existing approved FEATURE target and unchanged infrastructure.

Local verification: 43 focused token/source-access/transport tests pass,
including original-question submission exactly once after implicit consent,
background warm-up/recovery never prompting, per-audience token validation,
and explicit approval/no ambiguous-write replay. Scoped ESLint, Node 24
`npm run validate-env`, and production TypeScript/Vite build pass; existing
mixed-import/chunk warnings remain. Hosted packaging/fingerprint tests exit 0.
Azure CLI confirms the expected tenant and Enabled subscription.

Validation workflow and authenticated hosted authorization acceptance are
complete, as recorded below. This is scope-specific acceptance, not acceptance
of every orchestration intent. The facility-count failure recorded here was
resolved by the follow-up above. Earlier receipts remain historical.
No ARM provisioning or extra quota requested.

### Section 7: Validation Proof - implicit agent access

Validation date: 2026-10-27. Recipe: existing canonical Python/Azure CLI
orchestrator; no ARM templates or resource provisioning.

All validation checks pass:
- [x] Core: authenticated expected tenant/Enabled subscription, Node 24,
  configuration validation, production TypeScript/Vite build, scoped ESLint,
  and 43 focused authorization/transport tests. No ARM template validation
  or what-if is applicable to this application-only change.
- [x] Container context: 43 hosted deployment tests pass, including exact
  Docker COPY/fingerprint closure, isolated ACR context and failed-build
  propagation. Existing Dockerfile and locks are unchanged. The shared
  source-access module is packaged, so the canonical orchestrator must build
  the changed source-fingerprinted image through ACR and gate publication on
  activation. No separate hand-assembled image deployment is permitted.
- [x] Static roles: no permission, identity, role scope or source-operation
  changes; delegated audience/tenant/lease guards and human approval remain.
- [x] Policy readback: Azure MCP returned applicable assignments for the
  existing resource group. No infrastructure/SKU/region/network change.
- [x] Functional: production-build browser renders the existing chat design
  without the setup button/banner. Warm-up did not open sign-in. Temporary
  preview process stopped; no operational record or approval was submitted.

Commands: `node --import tsx --test` for source-access, Entra and transport
tests; scoped `npm exec -- eslint`; `npm run validate-env`; `npm run build`;
`.venv\Scripts\python.exe -m unittest discover -s Raw\workspace-reset -p
test_deploy_hosted_agent.py`. All exit 0. Existing chunk/mixed-import warnings
are unchanged. These local checks do not substitute for hosted acceptance below.

### Hosted acceptance - implicit agent access

- Canonical orchestrator exited 0 with final `SUCCESS` and `DEPLOYED_APP_URL`
  at `2026-10-27T09:36:54.886760Z`, total **942.031 seconds**.
  URL: `https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
- Signed browser verified **v1.0.1143 / 9197530**, zero separate access
  buttons/banners and no sign-in popup on mount.
- A normal Send of the existing six-hour BAD/UNCERTAIN telemetry question
  submitted exactly **one run request**, opened **zero sign-in popups**, and
  returned the source-backed answer in **24,972 ms**. Supervisor and Gauge
  completed; the rendered answer identified all 15 turbines with **180
  verified records**. This is one browser sample, not a latency SLO.
  Session: `8b650710-77d8-4be4-b44f-7c73618b1f1f`. No alert or work-order
  action was present; no approval/rejection/production write was submitted.
- An earlier, different facility-count question reached the runtime without
  separate access setup but failed the existing orchestration guard with
  `unexpected-evidence:telemetry`, returning HTTP 500 and no certified answer.
  Session: `1a5f69ed-7d76-4e40-8c52-949c28796ba2`;
  failed run: `28968fae-2e54-4792-8637-9b3d814ea29f`.
  Read-only session logs confirmed this planner-contract failure. The failed
  question was not resubmitted. This unrelated routing defect is not fixed or
  hidden by the authorization change.
- ACR build `dtp` succeeded; coordinator **v26** activated on
  `src-ea5c543336298b9a`. All five specialist definitions were reused.
- All 42 existing SPA redirects were preserved; runtime/SQL/CORS reapplication
  and GraphQL, token and three connector preflight/POST readiness checks passed.
- Azure MCP readback confirmed the existing project-scoped Foundry User role.
  No new permission, role assignment, resource or write-policy change.

## Knowledge Graph endpoint and edge interaction repair

Modify the existing React/Cytoscape frontend, using the existing canonical Python
deployment recipe and unchanged FEATURE target described below. No new Azure
resources, infrastructure, role grants, network settings or source permissions.
No agent runtime/image change or approved-write policy change.

Scope:
- Preserve separate signal nodes whenever any relationship other than the
  one-to-one instrument binding references them; never redirect maintenance
  relationships to a different Ontology entity type.
- Stop appending inferred SQL overlay topology in native mode, matching live
  mode's source-authoritative behavior. Keep telemetry and maintenance-health
  observations; preserve operational entities returned by Ontology execution.
- Suppress Cytoscape's black active/selected edge overlay, retaining semantic
  colors/arrowheads and existing visual design.
- Keep the selected context's governed ancestor chain readable to the facility.

Source review read the published FEATURE Ontology's 18 entity types and 17
relationship types. Equipment/instrument/signal WorkOrder keys and maintenance
signal joins are distinct and correctly typed. No binding error was established;
no Ontology relationship will be removed to hide a renderer discrepancy.

Target: tenant `ad340c84-1886-4202-a483-2da2cb9168eb`, workspace
`c7da7c28-0dee-4f45-bc9e-5628bb882732`, existing Sweden Central capacity and
subscription `dac16501-5c35-4c91-aff0-11af8de17f2e`.

Publication to this unchanged target was offered for confirmation; the user was
unavailable. Continue with the existing FEATURE target and no resource or
permission changes, consistent with the requested application repair.

### Functional verification - endpoint and interaction repair

- Status: Verified locally. The graph suite passes **114 tests**, scoped ESLint,
  Node 24 `validate-env`, and production TypeScript/Vite build pass.
- The authenticated live WO-2851 inspector confirmed equipment T008, instrument
  `INST_T008_POWER_OUTPUT`, and signal `ns=2;s=T008.power_output`; its old signal
  relationship displayed an instrument endpoint, reproducing the discrepancy.
- An explicitly synthetic actual-component browser fixture verified zero
  activated-edge overlay opacity/padding, preserved hierarchy/WO colors and
  triangle arrows, visible ancestors through the facility, dimmed siblings,
  and unchanged node positions during selection.
- Backend/agents, permissions, source queries and Ontology bindings are unchanged.
  No production write was performed. Canonical endpoint readiness and hosted
  live-source acceptance remain deployment gates, not inferred from fixtures.
- Azure CLI readback confirms the expected tenant and Enabled subscription.
  Origin/main is already an ancestor; the working branch is synchronized.

Previous receipts below describe earlier releases and are not deployment proof
for this update.

### Section 7: Validation Proof - endpoint and interaction repair

Validation date: 2026-10-27. Recipe: existing repository Python/Azure CLI
orchestrator, not a new ARM/Bicep/Terraform deployment.

All validation checks pass:
- [x] Core validation: authenticated expected tenant/Enabled subscription,
  Node 24, `npm run validate-env`, **114 passing graph tests**, scoped ESLint,
  and production TypeScript/Vite build. Existing mixed-import/chunk warnings
  remain non-blocking. ARM template validation/what-if are not applicable:
  no ARM resource or IaC changes are part of this frontend repair.
- [x] Container build: not applicable; agent/container source and dependency
  manifests are unchanged. Reuse the current source-fingerprinted image.
- [x] Static role verification and policy delta: no identity, role assignment,
  source permission, resource type/SKU, region, tag or network changes. Existing
  delegated source authentication and human-approved write enforcement remain.
- [x] Functional verification: actual physical-join regression in both modes,
  actual Cytoscape computed styles, and synthetic browser checks recorded above.
  The fixture file was deleted and its attached server stopped.

Commands executed under Node 24: `npm run test:knowledge-graph`,
`npm exec -- eslint` on the three changed graph modules and three changed test
files, `npm run validate-env`, and `npm run build`; all passed.
Azure MCP policy readback returned ten applicable assignments, including the
resource-creation block and write/delete MFA policies. This app-only publication
does not request new ARM resources or change protected infrastructure properties;
this is not a subscription-wide compliance certification.

### Hosted acceptance - endpoint and interaction repair

- Canonical orchestrator exited 0 with `SUCCESS` and `DEPLOYED_APP_URL` at
  `2026-10-27T09:01:08.737252Z`; total duration **684.906 seconds**.
  URL: `https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
- Authenticated hosted browser confirmed **v1.0.1141 / b7087f1**. The orchestrator's
  protected sign-in-gate check was followed by actual signed-in source execution;
  it was not treated as UI/data acceptance on its own.
- All 42 Entra SPA redirects were preserved. AppBackend GraphQL/token paths and
  all three connector paths passed browser-equivalent preflight/POST readiness.
  The existing five specialists and coordinator image/configuration were reused.
- The hosted graph reported **18/18 entity types and 17/17 relationship types**.
  A forced authenticated graph refresh completed in **6,217 ms** (one observed
  end-to-end browser sample, not a load-test/SLO certification).
- WO-2851's actual three relationships retain equipment, instrument and signal
  targets, respectively. `WorkOrder_for_signal` now targets
  `signal:ns=2;s=T008.power_output`, not `instrument:INST_T008_POWER_OUTPUT`.
  The inspector displays **Signal · power_output** with a signal type.
- Live computed-style checks reproduced the old hierarchy-edge overlay at
  opacity 0.25/padding 10 px, then verified the deployed hierarchy and WO edges
  have zero overlay opacity/padding when active/selected. Semantic stroke/arrow
  colors and triangle arrowheads are preserved.
- Selecting WO-2851 and then T008 retained readable facility/system ancestry;
  node positions were unchanged. The T008 scope contains 16 nodes and 19 edges.
- A separate synthetic 2,000-node/4,000-edge visual-preparation measurement
  (flow, routes and focus) took **37.86 ms** against the 250 ms budget.
  This is not a native-query, full-render or live-source latency measurement.
- Azure MCP live role readback confirmed the existing project-scoped Foundry
  User assignment. No access or infrastructure was added; no operational record
  was created, approved or deleted. Ontology bindings/relationships were unchanged.

## Knowledge Graph in-workspace loading update

Reuse the existing approved FEATURE target and canonical Python deployment
recipe. This is a frontend-only change: remove the blocking loading/error page,
render the existing graph workspace immediately, keep verified topology visible
and timestamped during refresh, and retain fail-closed clearing on failed reads.
The execution selector remains available under Advanced graph details, including
loading and error states. No query-source, model, infrastructure, RBAC, network,
redirect, source permission or approved-write policy changes are requested.

Target: tenant `ad340c84-1886-4202-a483-2da2cb9168eb`, workspace
`c7da7c28-0dee-4f45-bc9e-5628bb882732`, existing Sweden Central capacity and
subscription `dac16501-5c35-4c91-aff0-11af8de17f2e`.
The existing target was offered for confirmation; the user was unavailable.
Continue with this unchanged target for the requested application update, with
no new resources or broader permissions.

### Functional verification

- Status: Verified locally using an isolated, explicitly synthetic browser
  fixture of the actual React/Cytoscape page; no cloud source tokens or data
  were used by that fixture.
- The normal workspace, toolbar and inspector render before graph completion;
  the initial skeleton has no invented graph entities.
- A successful verified fixture renders the real canvas. During refresh the
  canvas remains mounted and the header identifies its last-verification time.
- A rejected read clears graph data and shows an inline error; explicit retry
  restores the verified canvas without replacing the workspace.
- The temporary fixture and its attached server were removed/stopped.

### Section 7: Validation Proof - Knowledge Graph loading UI

Validation performed on 2026-10-27 against the current frontend-only change.

All validation checks pass:
- [x] Core validation: CLI tenant/subscription, Node 24 environment, tests,
  scoped lint and production build. ARM validate/what-if are not applicable:
  the canonical repository workflow updates an existing Fabric application;
  no Bicep/Terraform or ARM resource changes are included.
- [x] Container build: not applicable; packaged runtime source/dependencies
  are unchanged and the canonical deployer reuses the source-fingerprinted image.
- [x] Static role verification and Azure Policy delta: no identity, role,
  resource, SKU, region, tag or network configuration changes. Existing source
  authentication and approved-write enforcement are unchanged.

- Existing tenant-scoped CLI profile confirms the expected tenant and Enabled
  subscription, `ME-MngEnvMCAP218279-didharch-2`.
- Node 24 `npm run validate-env`: passed.
- `npm run test:knowledge-graph`: **111 passed, 0 failed**.
- Scoped ESLint for the changed page and visual regression tests: passed.
- TypeScript/Vite production build: passed; existing mixed-import/chunk warnings.
- Runtime/container source is unchanged. Reuse the current image; no new
  container, IaC deployment or role expansion is required.
- Canonical publication completed at `2026-10-27T08:21:02.025155Z` in
  **540.844 seconds**, with `SUCCESS` and `DEPLOYED_APP_URL`:
  `https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
  The existing hosted-agent image/configuration was current and was not
  republished. All 42 Entra SPA redirects were preserved.
- AppBackend `/graphql`, `/api/auth/v1/token`, and all three connector paths
  passed the orchestrator's browser-equivalent preflight/POST readiness checks.
- Live RBAC readback confirms the existing project-scoped Foundry User grant
  and inherited roles; no role or permission was added.
- Authenticated hosted-browser inspection confirmed **v1.0.1139 / 91e8756**,
  one graph workspace, zero blocking placeholder cards, the in-canvas loading
  state, and Advanced graph details. The inspected tab was backgrounded:
  no new live graph-query completion or source-latency claim is made from
  that inspection. Refresh/error/retry behavior was tested separately in
  the isolated synthetic component fixture described above.

## FEATURE human-approved work-order policy restoration

The user reported that the approved-creation action is disabled. Read-only live
inspection confirmed active coordinator v24 has
`production_writes_enabled=false` for the expected FEATURE workspace. The
deployer intentionally preserves the existing policy unless the documented
`HYDRO_PRODUCTION_WRITES_ENABLED` override is supplied; this was not a SQL error.

Restore the capability to create a work order only after explicit human approval,
using `HYDRO_PRODUCTION_WRITES_ENABLED=true` with the same canonical root
deployment command. Do not edit runtime JSON, add permissions, change the SPA,
submit the screenshot's decision, or automatically create any SQL record.
The screenshot's displayed draft expiry is in the past: it must be replaced with
fresh evidence and a new review, not replayed after the setting changes.

All validation checks pass (required before deployment):
- [x] Core CLI/auth/build: expected tenant/subscription, environment validation
  and existing Node 24 production build.
- [x] Write-policy behavior: supported explicit true override and subsequent
  same-target policy preservation; existing approval/digest/expiry/source/
  readback/no-replay tests.
- [x] Container: reuse the already built unchanged source-fingerprinted image.
  No new image or source-code change is required.
- [x] Static roles and Azure Policy: no ARM/IaC, identity, network, delegated
  permission or RBAC change; the application-level approved-write flag changes.
- [x] Canonical publication and live readback: active coordinator flag true,
  unchanged target and source image; preserved redirects and endpoint readiness.

### Section 7: Work-order policy validation proof

Validation performed 2026-10-27T07:09Z through 07:13Z:

- `az account show` confirmed the expected tenant and Enabled subscription
  `dac16501-5c35-4c91-aff0-11af8de17f2e`.
- Under Node 24, `npm run validate-env` and `npm run build` passed. Vite built
  in 33.24 seconds; existing mixed-import/chunk warnings only.
- Root `.venv` ran `python -m unittest
  test_deploy_hosted_agent.ConfigurationTests
  test_deploy_hosted_agent.ConvergenceTests test_source_auth`:
  **48 tests passed in 34.596 seconds**. The targeted regression now exercises
  both deliberate false-to-true and true-to-false policy transitions, plus
  preservation of an enabled policy without an override. The delegated service
  tests cover explicit signed approval, principal isolation, idempotency,
  rejection, disabled writes, expiry and uncertain-write reconciliation.
- Static role verification: no application permission, principal, role
  assignment or source transport changes. The unchanged backend continues using
  the signed-in user's delegated SQL authority, not a newly privileged identity.
- Existing container image `src-ae2d589b20c8d8bd` was built by successful ACR
  build `dtn` and is active in v24. No packaged runtime source or dependency changed;
  no new image or ARM validate/what-if/Azure Policy resource delta is applicable.

Canonical publication completed at `2026-10-27T07:25:26.149585Z`, in
**667.016 seconds**, with `SUCCESS` and `DEPLOYED_APP_URL`:
`https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
Independent Foundry API readback confirmed active **hydro-orchestrator:25**,
`production_writes_enabled=true`, the expected FEATURE workspace and unchanged
container digest
`sha256:163da34bda6d6d359b9cf014872ff59387f5ccb4594dadad6f89c5538c265fa6`.
All five specialists were reused; no source image rebuild was needed.

The canonical deployment verified all AppBackend/three-connector preflights and
POST readiness, the current generated endpoint and required delegated scopes,
and preserved all 42 existing SPA redirects. Post-deployment read-only role
verification in the same tenant-scoped CLI profile confirmed the existing
project-scoped Foundry User grant and inherited grants; no access was added.
The ambient CLI profile required renewed authentication, so role verification
used the already-current deployment profile for the same tenant and caller.

Hosting verification reached the protected sign-in gate, not an authenticated
application session. **No production SQL creation, new browser approval, or
authenticated source execution is claimed.** No screenshot decision was retried
and no acceptance work order was submitted. Reload and request a fresh draft
before approving; the expired/disabled card must not be replayed.

## Agent authorization and source-routing repair validation

Reuse the approved FEATURE tenant/workspace, SPA, Foundry project/model,
connectors and canonical Python deployment recipe. No infrastructure, RBAC,
network, delegated scope or production-write policy changes are requested.
The repair adds explicit per-audience source authorization in shared chat/Battle,
checks renewed leases, and prevents native-only quality/work contracts from
capturing independent verification. Existing historical quality, complete
evidence, provider-refusal and human-approval boundaries remain in place.

Pre-existing overlapping edits are preserved in stash
`16a45ffe12959ac4575e9e387b57d748a9b07e0f`, not included in this release.
No weather, graph, ingestion, seed or schedule changes are part of this repair.

All validation checks pass for this scoped change:
- [x] Core CLI/auth/build validation: existing Azure CLI tenant/subscription
  preflight, environment validation, TypeScript/Vite build and scoped lint.
- [x] Behavioral validation: 149 focused application tests and the complete
  185-test supervisor/live-source/source-auth suite pass under Node 24.
- [x] Container packaging: four Docker COPY/ignore/runtime-closure/fingerprint
  tests pass under Node 24. Local Docker is installed but its daemon is not
  running; no local image-build pass is claimed. The canonical orchestrator owns the actual ACR image
  build and hosted version readiness before publishing the Fabric app.
- [x] Static roles: no new principal, delegated scope, role or role assignment.
  Existing signed-principal source checks and human write approval are unchanged.
- [x] Azure Policy/ARM what-if: not applicable; no ARM/IaC/resource/network/RBAC
  changes. This is an existing application/hosted-code publication.

### Section 7: Agent repair validation proof

The 149-test focused Node suite covers authorization, token refresh, transport,
canonical source planning and explicit native/direct verification. Commands:
`node --import tsx --test scripts\operational-routing.test.ts scripts\local-fabric-sources.test.mjs scripts\copilot-tools.test.mjs scripts\hosted-source-access.test.ts scripts\entra-tokens.test.mjs scripts\hosted-transport.test.ts`,
scoped ESLint, `npm run validate-env`, and `npm run build`.
Production build completed in 29.36 seconds with existing chunk/mixed-import
warnings only. Under Node 24.21.0,
`python -m unittest test_foundry_supervisor test_live_sources test_source_auth`
passes all 185 tests in 189.787 seconds; the four additional focused
packaging/fingerprint checks pass. An earlier Node 26 packaging failure was
resolved, not waived. Azure CLI verified the expected tenant and Enabled
subscription `dac16501-5c35-4c91-aff0-11af8de17f2e`.
Deployment SUCCESS and authenticated runtime acceptance must be recorded
separately after publication.

### Agent repair publication and acceptance boundary

The canonical root command
`python Raw\workspace-reset\deploy_fabric_app.py --tenant ad340c84-1886-4202-a483-2da2cb9168eb --workspace c7da7c28-0dee-4f45-bc9e-5628bb882732 --push-config`
completed at `2026-10-27T07:01:08.626526Z`, in 812.578 seconds, with `SUCCESS`
and `DEPLOYED_APP_URL=https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
Source commit: `f760be8`. ACR build `dtn` succeeded; coordinator version 24 is
active on `src-ae2d589b20c8d8bd`. All five specialists were unchanged/reused
(current=5, updated=0, created=0). No NB11 execution or demo DML ran.

The existing AppBackend and all three connectors applied successfully.
Browser-equivalent preflights returned HTTP 200 on `/graphql`, token and
connector paths; POST readiness returned GraphQL 200, token 400 and protected
connector 401, not a source-data acceptance claim. All 42 Entra SPA redirects
were preserved; delegated permissions and existing consent were verified.

Local browser verification displayed the new control and a resource-specific
missing-access error in 0.1 seconds with no invocation. Hosted navigation
reached the protected Fabric sign-in gate. Its Sign in action reported
`Please allow pop-ups and try again`; trusted browser clicking also failed
visibility/stability automation. The user was unavailable to complete sign-in.
No token extraction/substitution or gate bypass was attempted.

Therefore current-build hosted connector/QA/RCA/draft execution and latency
acceptance remain **blocked**, not passed. After normal browser sign-in, use
**Connect agent access** for each missing audience, then submit a fresh read/
review request. No work-order approval or production write was performed.

## October 27 UKMet-first weather follow-up

The user approved implementing the UKMet-first recommendation autonomously.
Reuse the existing FEATURE tenant/workspace, Sweden Central capacity, SPA,
connectors, Weather Environment and notebook/Lakehouse bindings. No new resource,
role, permission, provider key or target is requested.

- Prepare shared operating-area metadata using Weather_002 `prepare_only=true`,
  before any vault lookup, ingestion audit or Aurora request.
- Ingest UKMet, then publish its metrics and serving projections. Aurora is an
  explicit `enable_aurora` pipeline opt-in, default false, after UKMet is served.
  An explicitly enabled Aurora failure remains an error, but cannot prevent the
  preceding UKMet serving refresh. No provider failure is silently swallowed.
- Preserve the MAI key and historical vendor provenance. Update hosted setup and
  progress text so Aurora is no longer presented as a prerequisite.
- Update only the existing Weather_002 notebook and weather pipeline definitions,
  preserving live bindings and backing up previous definitions. Do not run Core,
  seed, reset, change schedules or reconfigure private networking.
- Publish the frontend only through the canonical deployment orchestrator in
  the existing recipe below; verify its SUCCESS/DEPLOYED_APP_URL and signed UI.

Validation:
- [x] 17 weather contract tests, including no-key preparation and UKMet-first
  dependency/opt-in regressions; generated notebook AST parity.
- [x] `validate-env`, production TypeScript/Vite build and scoped ESLint.
- [x] Pylance Python syntax check.
- [x] Independent actual UKMet run `785fcd48-77d5-414e-a2b7-30bed3b2969e`
  completed at `2026-10-27T03:03:11Z`, without running Aurora.
- [x] Two focused nested pipeline rebinding/inventory tests; mirror drift check.
- [x] Actual UKMet-first pipeline run, serving rows and authenticated app.

All validation checks pass for this scoped change:
- [x] Core CLI/auth/build: existing tenant credentials, successful real UKMet
  notebook execution, `validate-env`, focused tests, build and lint as above.
- [x] Docker build: not applicable; no agent/container source change.
- [x] Azure Policy/what-if: not applicable; no ARM/IaC/resource/network/RBAC change.
- [x] Static roles: same existing workspace item read/update/execute operations;
  no new principal or provider permission. Actual UKMet execution verifies the
  existing notebook identity can read its vault secrets and contact the provider.

The broader feature suite exposed an existing deployment-mock mismatch:
`validate_appbackend_cors` now receives `energy_enabled`, but the unchanged test's
lambda does not accept it; its expected connector call list is also outdated.
Neither production deploy code nor that test is changed by this weather work.
Record focused relevant results separately, without claiming the whole suite passes.

### Section 7: UKMet-first validation proof

- `python -m unittest discover -s Raw\workspace-reset -p test_weather_source_contracts.py`:
  17 passed, including three newly reproduced/fixed regressions.
- Focused `test_pipeline_rebinding_handles_nested_notebook_activities` and
  `test_local_inventory_has_resolvable_pipeline_references`: two passed.
- `python Raw\workspace-reset\sync_ontology_notebooks.py --check`: passed.
- `npm run validate-env`, `npm run build`, and ESLint on the three changed
  TypeScript/TSX files: passed under Node 24. Existing bundle-size/dynamic-import
  warnings remain non-fatal.
- Pylance found no syntax errors in the changed Weather_002 Python source.
- Actual isolated UKMet job completed successfully from `02:58:28Z` to
  `03:03:11Z`, without Aurora. Azure CLI confirms the same tenant and an Enabled
  subscription; no Azure subscription resource mutation is part of this change.
- Git whitespace validation passed. No identity, role, network or secret change.
- A final coupled UI correction makes UKMet the default forecast vendor even when
  retained historical Aurora rows are present. Explicit user selections and
  unavailable/empty-source behavior are preserved. Five Node weather summary/
  selection tests, `validate-env`, a fresh production build and scoped ESLint all
  passed for this correction. The initial UKMet-first app publication succeeded.
- Live pipeline preparation succeeded in 281.312 seconds and returned exactly
  `{"status":"prepared","areas":3,"aurora":"disabled"}` before UKMet ingestion.

### UKMet-first publication and live acceptance

- The complete manual pipeline run `1b8dc1af-4d75-4885-960d-782b0dcb3a85`
  completed successfully at `2026-10-27T03:27:57Z`. Preparation, UKMet ingestion,
  serving calculations and the disabled optional branch all succeeded; neither
  `Ingest_Aurora` nor its second metrics refresh executed.
- Signed-browser reads showed actual future UKMet forecasts for all three stations
  and all three representative 20 km areas. Each station had four forecast rows
  in the selected next-24-hour window and 24 observation rows in the past 24 hours.
  Areas reuse their representative station's observations; they are not areal
  measurements. Solar radiation remains unavailable rather than fabricated.
- Final canonical app publication completed at `2026-10-27T03:30:09Z` in
  416.547 seconds, with `SUCCESS` and `DEPLOYED_APP_URL`.
  Authenticated reload confirmed **1.0.1131 / 51b05d6**, UKMet selected by default,
  actual forecast/observation rows and corrected Administration prerequisite text.
- All five specialists and the existing coordinator image were reused. Forty-two
  redirects were preserved; runtime/CORS and connector readiness checks passed.
- No MAI secret deletion/replacement, identity/RBAC/network change, schedule change,
  Core/seed execution or telemetry stream start was performed. Live notebook
  Lakehouse/Environment bindings were preserved and read back.
- Final scoped checks: 17 Python weather contracts, two nested rebinding/inventory
  tests, five Node weather tests, production build and scoped lint passed.
  The unrelated broad feature-test mock mismatch above remains documented.

See [the weather acceptance receipt](../docs/weather-fabric-port-plan.md#october-27-ukmet-first-live-acceptance).

## October 27 graph freshness and fault-pulse publication

Source: `162a6cd`; same FEATURE tenant/workspace and existing canonical Python
orchestrator recipe. No infrastructure, connector projection, identity, permission,
model, agent source, source data or deployment target changes.

All validation checks pass:
- [x] Core CLI/auth/build validation: expected tenant and Enabled subscription;
      `npm run validate-env`, 108 focused graph tests, production TypeScript/Vite
      build and scoped ESLint passed on October 27.
- [x] Docker build: not applicable to this frontend-only change; the canonical
      deployer owns existing supervisor fingerprint verification and reuse.
- [x] Azure Policy validation: no ARM/IaC, region, SKU, network, RBAC or tag change;
      no resource what-if or policy mutation applies.
- [x] Static role verification: no added data operation or principal; existing
      signed-in read-only graph sources and telemetry enrichment are unchanged.

### Section 7: Validation Proof

At approximately `2026-10-27T01:57Z`, the required checks above had passed.
Azure CLI read-only verification returned tenant
`ad340c84-1886-4202-a483-2da2cb9168eb`, subscription Enabled, and the explicitly
authorized `rg_fabric/fabcapswc` capacity **Active** in Sweden Central.
The actual React/Cytoscape browser fixture verified freshness expiry without
refetch, both temporal fault pulses, reduced-motion/offscreen guards, cleanup,
separate directed parallel curves and unchanged camera on reading/selection changes.
The actual source-backed All scope also retained 225 finite distinct positions.
Detailed measurements are recorded in `docs/knowledge-graph.md`.

Publication attempt: the canonical orchestrator verified the Active FEATURE capacity,
then required Microsoft/Azure CLI sign-in renewal at existing-SPA discovery (step 2/8).
The user was unavailable, so the owned waiting process was stopped before Rayfin
publication. At that point this plan remained Validated, not newly Deployed.
Hosted acceptance was pending; the signed-in page still reported `1.0.1121 / 1673261`.
Autonomous retry confirmed Graph CAE `InteractionRequired /
TokenCreatedWithOutdatedPolicies` on the actual read-only SPA lookup, despite
a future cached-token expiry. Fresh interactive sign-in was required.

That blocker was resolved by standard browser/device authentication using the
same signed-in administrator, with no policy/permission change. Actual Graph
access passed; canonical publication succeeded at `2026-10-27T02:20:43Z` in
620.609 seconds, and signed-browser source-backed graph reads passed on
`1.0.1127 / 6efd792`.

The hosted All-scope check found a coupled fit/clipping defect. The follow-up
wraps long labels, uses canvas-sized hierarchy bounds, permits full-graph overview
zoom and scales fault halos at overview zoom. It changes no source identity,
relationship endpoint, connector, agent, permission or infrastructure. Validation
now includes 109 graph tests, production build and scoped lint; the 2,000-node /
4,000-edge visual preparation fixture completed in 33.21 ms (250 ms budget).

### Final publication and authenticated acceptance

The canonical orchestrator printed `SUCCESS` / `DEPLOYED_APP_URL` at
`2026-10-27T02:47:55Z`, after 719.344 seconds. The signed-in hosted app confirmed
**1.0.1128 / 44325bc**. All five specialists and the coordinator image
`src-5afb91f86a3d8ee5` were reused; 42 SPA redirects were preserved.
Runtime/CORS, connector preflights and protected POST readiness checks passed.

- Actual stale readings produced zero flowing edges in Selected and All scopes.
- All scope retained 225 nodes / 238 directed edges with 225 distinct finite
  positions and the unchanged directed identity hash recorded in
  [the graph acceptance receipt](../docs/knowledge-graph.md#final-hosted-acceptance-101128--44325bc).
- Full-map Fit kept the complete graph inside the canvas. Both real critical/red
  and warning/amber halos varied across time and remained visible at overview zoom.
  Reduced motion retained static halos; restoring motion resumed breathing.
- Actual work-order instrument/signal links had separate curve geometry, unchanged
  identities/endpoints and correct arrows. The stale inspector showed event age,
  last-known quality and incoming/outgoing context.
- Cold application-memory reload: 10.147 s (graph loader 5.33 s); explicit refresh:
  5.067 s; warm return: 83 ms with the observation timestamp preserved.
  These are single-session observations, not an SLA/load certification.
- Final validation: 109 focused graph tests, production build and scoped lint passed.
  No stream, seed, notebook or production business write was started.

The graph publication is accepted. Earlier native Foundry policy and Weather
credential blockers are separate and are not represented as resolved by this change.

## Knowledge Graph live-source publication

### Coverage completion and visual restoration follow-up

The completed publication expands the existing read-only Lakehouse connector, not
permissions or graph resources: signal master, weather sources/ingestion history,
classification and all missing primary scalar/identity columns. It removes the
duplicate browser seeding action from every layout, retains monitoring for older
in-flight jobs, and restores earlier fluid stroke/arrow styling with the compact
header and diagnostics below the canvas. Animation remains telemetry-driven and
bounded for large graphs.

Read-only SQL preflight on October 26 verified every new physical column and type:
90 signals, three weather sources, nine variables, five ingestion runs and 15
classification rows. Locations, areas, latest observations and latest forecasts
were readable but empty at preflight. Empty tables are not missing projection
support and must not be filled with synthetic graph instances.

The safely restored FEATURE ontology definition was checked against the updated
server/client projections: all 18 primary entity bindings and all 17 declared
relationships are compatible. This local check used synthetic empty readers;
actual signed-in connector acceptance passed as recorded below.
No SQL seed, data deletion, new role assignment or manual Entra changes were performed.

#### Coverage completion publication and authenticated acceptance

The canonical orchestrator completed at `2026-10-26T19:46:44Z` in 874.454 seconds,
printing `SUCCESS` and
`DEPLOYED_APP_URL=https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
Authenticated acceptance confirmed build `1.0.1110` / `9b67be9`.
Five specialists were reused; the changed shared source fingerprint required
supervisor image `src-c1bce63d8dac1277`, with version 15 active. All three connectors,
runtime/CORS preflights and POST readiness checks passed; 42 SPA redirects were
preserved. No NB11 execution or seed DML ran.

- All 18 entity bindings and 17 declared relationship types are included.
  Actual export: 309 unique instances / 322 unique directed links. Every entity
  type and relationship direction matches the selected contract. Source evidence
  identifies the verified Core Lakehouse and operational SQL; no GraphModel
  identity or atomic-snapshot claim is present.
- Four weather tables are readable but empty. Six ingestion runs were observed,
  rather than five at preflight. Ten inspections have signal references; no
  missing weather instances or inspection links were invented.
- The existing combined instrument/signal presentation yields 219 All-scope
  nodes / 232 links, with 219 distinct finite positions. Full export is retained.
  Earlier fluid strokes/arrows and compact controls are restored; detailed
  execution/coverage controls are collapsed below the canvas.
- Full reload plus opening Graph details: 7.728 s; loader 4.67 s.
  Explicit refresh: 5.508 s; loader 5.44 s. Warm return: 181 ms with the
  original observation timestamp. These are single-session measurements.
- Existing telemetry loaded 90 signals without starting a stream. All scope
  had 108 telemetry-bearing edges; selected T001 had eight, with verified
  changing dash offset, 3.2 px width and 10/12 dash pattern. Above 150 edges,
  animation remains bounded to the selected immediate neighborhood.
- Administration has no seed action and uses steps 1-5, with STID/telemetry
  chips 3/4. No notebook-start controls were clicked.
- Page-only ontology HTTP 403 removed the canvas and exposed the explicit
  retry control. Removing the route and retrying recovered in 5.225 s.
- Native mode rejected HTTP 204 in 2.951 s, with no canvas or silent source
  fallback. Live mode was explicitly restored afterward.

Full current receipts are in [Knowledge Graph documentation](../docs/knowledge-graph.md).
Native eligibility is still blocked; complete primary binding coverage does not
certify rule/time-series execution, atomic reads, per-user SQL RLS or production readiness.

#### Section 7: Follow-up validation proof

On October 26, the 100-test graph suite passed, followed by the two additional
visual regressions (four visual tests total). Seven connector/Administration tests
and 26 discovery/service tests passed: 135 distinct focused tests in total.
Scoped ESLint and TypeScript passed; final production `npm run build` ran its
`validate-env` prerequisite and Vite finished in 24.43 seconds, with only the
existing mixed-import/chunk-size warnings.

Azure CLI confirmed the expected tenant and Enabled subscription
`dac16501-5c35-4c91-aff0-11af8de17f2e`; live Lakehouse metadata verified SQL
endpoint `95b16845-30e6-4be8-bee2-73935396f18b` in Success state. Authenticated
read-only SQL verified the nine previously excluded bindings' columns and counts.
No new infrastructure or principal is introduced: expanded entities remain
`@authenticated('read')`, keyless and relationship-free at connector level.
The app still verifies source identities and declared ontology joins itself.
New ARM validation/what-if, role creation and a standalone Docker build are not
applicable to this existing Fabric publication; the canonical deployer owns
source fingerprinting and any shared supervisor image update.

Target: existing FEATURE workspace `c7da7c28-0dee-4f45-bc9e-5628bb882732`,
tenant `ad340c84-1886-4202-a483-2da2cb9168eb`. Existing Python AZCLI/Fabric
orchestrator recipe; no new infrastructure, role assignment, Entra registration
or graph resource. The orchestrator owns shared supervisor image fingerprinting
and any required image publication.

All validation checks pass (current publication checklist):
- [x] Load the approved existing target and canonical deployment recipe.
- [x] Core CLI/auth validation: enabled subscription and expected tenant.
- [x] Core build validation: environment validation, TypeScript and production build.
- [x] Core behavioral validation: governed source graph and native/discovery regressions.
- [x] Core target preflight: verified the existing workspace capacity assignment and source item identities.
- [x] Container validation: canonical orchestrator verified the changed shared
      supervisor image on first publication and reused it on the follow-up.
- [x] Static role review: existing signed-in connectors, read-only graph operations,
      no added permission, principal or role scope.
- [x] Policy review: no ARM/IaC resource, SKU, region, tag, network or RBAC changes.
      ARM validation/what-if does not apply to this existing Fabric app publication.

### Section 7: Validation Proof (Knowledge Graph)

Production compilation/bundling passed after `npm run validate-env`.
100 graph/ontology tests and 26 discovery/service regressions pass. The new source
graph has separate provenance, verifies physical item/table/column bindings,
joins only declared ontology relationships, bounds reads and rejects incomplete
results. User/target cache isolation and failed-refresh invalidation are tested.
A 2,000-node/1,999-edge source assembly/presentation fixture completed in 44 ms
on this machine, below its 1,000 ms regression threshold; this is not a hosted
or network latency measurement. Native source eligibility is still blocked
for FEATURE and is not represented as repaired or materialized.

Live read-only Fabric preflight confirmed workspace `ws-FabricIQAppsDemoFEATURE`
on capacity `0f4dac6b-ad68-483d-8027-3d8bf9655d53`, Core Lakehouse
`cdda0e9f-3fc2-48c1-a14e-179bd5140464`, operational SQL
`dc020068-3b9f-42b5-9437-3599f97aee4f` and ontology
`21ea4b9c-b41b-4da2-b5a1-cfee2c700987`. Parsing the safely restored real
18-entity definition verified its primary physical source/key mappings;
unsupported connector projections remain visibly excluded.

Publication used only the repository deployment orchestrator with
`--push-config`. Final deployment markers and signed-in hosted acceptance passed
as recorded below.

Commands: `npm --prefix HydroOperationsApp run validate-env`,
`npm --prefix HydroOperationsApp run build`, `npm --prefix HydroOperationsApp run
test:knowledge-graph`, `node --import tsx --test scripts\workspace-discovery.test.mjs`
from the app directory, and scoped `npx eslint` over changed TypeScript/tests.
Local production bundling passed in 20.75 seconds (existing chunk/mixed-import
warnings only). Local tooling uses Node 26; the canonical deployer remains
responsible for selecting Node 24 for the actual publication.

### First publication and authenticated acceptance

The canonical command completed at `2026-10-26T17:52:49Z` with `SUCCESS` and
`DEPLOYED_APP_URL=https://prime-twist-5b46085aac-swedencentral.webapp.fabricapps.net`.
It reused the existing backend and five specialist versions, preserved all 42
SPA redirects, reapplied runtime/SQL/three connectors, and passed all endpoint
and browser-CORS checks. Changed shared ontology code required supervisor image
`src-2825b7c3af4a018a`; hosted version 14 became active. No NB11 or seed DML ran.

Signed-in browser acceptance of build `1.0.1105` / `764188e` confirmed:
- 186 source-backed instances / 183 declared directed links.
- Coverage: 9/18 entity types and 8/17 relationship types, visibly disclosed.
- Three facilities, three systems, 15 equipment, 90 instruments, 12 work orders,
  30 inspections, 12 spare parts, 15 models and six notifications.
- JSON payload maps every edge to its declared ontology type/directed endpoints,
  includes only the verified Core Lakehouse and operational SQL source IDs, has
  a 64-character contract signature and no GraphModel identity.
- First authorized graph fetch: 4.58 s application time / 5.05 s UI wait.
  Forced refresh: 5.20 s. Warm return: 139 ms with zero connector requests,
  retaining the original source observation timestamp.

First-visit testing exposed separate app-session versus Fabric-read consent.
The follow-up adds an explicit graph-only `Connect Fabric and retry` action:
only Workspace.Read.All/Item.Read.All, no telemetry or connector scope; obsolete
authorization completions are discarded and failed authorization clears graph
caches. Production build, typecheck, scoped lint and 26 service tests pass.
The follow-up publication and final browser acceptance passed as recorded below.

### Final publication and acceptance

The canonical follow-up finished at `2026-10-26T18:11:33Z` in 489.641 seconds,
printing `SUCCESS` and the same `DEPLOYED_APP_URL`. Signed-in browser verification
confirmed build `1.0.1107` / `f499dc4`. All five specialists and supervisor image
`src-2825b7c3af4a018a` were current and reused; all 42 SPA redirects were preserved.
All backend/connector preflights and POST readiness checks passed.

- A full browser reload with cold application-memory caches rendered the graph
  in 5.804 s; the graph loader reported 4.76 s. This is not a cleared HTTP-cache
  or multi-user load test.
- Explicit refresh: 5.479 s browser wait / 5.40 s loader time.
- Immediate return from Overview: 192 ms, zero connector requests and the
  original observation timestamp retained.
- The actual Cytoscape canvas contained 186 nodes and 183 edges, with 186
  distinct finite positions, matching the disclosed 9/18 entity and 8/17
  relationship coverage.
- Selected, Facility and All scopes showed 14, 58 and 186 nodes respectively.
  Exact equipment-ID search selected the correct inspector in 306 ms; its six
  results legitimately include operational rows referencing that equipment.
- A page-scoped synthetic ontology-metadata HTTP 403 removed the canvas and
  exposed `Connect Fabric and retry`, without fallback data. Removing the test
  route and clicking that action recovered the graph in 4.843 s.
- Native mode failed honestly on HTTP 204 in 2.434 s, with zero graph execution
  requests and no source canvas. Explicitly switching back restored live mode.
- Final rerun: 100 graph/ontology tests, 26 service tests, scoped ESLint and
  TypeScript checks passed. The canonical deployment built the final source.

Native materialization remains blocked by Fabric source eligibility. Supported
live projections are genuine ontology-bound reads, not complete 18-entity
execution, an atomic snapshot, per-user SQL RLS certification or blanket
enterprise/production certification.

## October 26, 2026: V12 setup failure recovery and live verification

The current request is to verify the real deployment after NB01 rejected the
SQL creation operation URL. Preserve the existing FEATURE workspace and owned
`RTI_Demo_SQLDatabase_V12`; no reset, Git reimport, deletion, replacement SQL,
new Foundry project or new model deployment is authorized.

- Target tenant: `ad340c84-1886-4202-a483-2da2cb9168eb`.
- Target workspace: `c7da7c28-0dee-4f45-bc9e-5628bb882732`.
- Existing subscription: `dac16501-5c35-4c91-aff0-11af8de17f2e`.
- Recipe: existing Python AZCLI/Fabric REST orchestrator; no new IaC or azd environment.
- All validation checks pass:
  - [x] Read the existing approved deployment plan and target.
  - [x] Run focused SQL lifecycle, shared LRO and initializer regressions: 95 passed.
  - [x] Check all distributed notebook helpers and Raw mirrors: passed.
  - [x] Read-only Fabric checks verify V12 SQL ownership and authoritative TDS metadata.
  - [x] Verify current tenant, subscription, workspace capacity and existing SPA.
  - [x] Run `npm run validate-env` before any app build/deployment.
  - [x] Static role/policy review: no new infrastructure or role changes.
- Source-only recovery must preserve live notebook dependencies and parameters.
  Retry the existing Core setup action with suffix V12, not Full Deploy's Git import.
- Final app publication must use only
  `python Raw\workspace-reset\deploy_fabric_app.py --tenant ad340c84-1886-4202-a483-2da2cb9168eb --workspace c7da7c28-0dee-4f45-bc9e-5628bb882732 --push-config`.
- Completion requires the actual pipeline result and the orchestrator's `SUCCESS`
  and `DEPLOYED_APP_URL`; stop and report any mandatory administrator/manual prerequisite.

### Section 7: Validation Proof (current recovery)

On October 26, before live writes: 95 targeted tests passed; distributed notebook
checks and `git diff --check` passed. Fabric GET verified the existing SQL item
`2478e550-20c0-409b-832b-50f7a61f5dec` with the expected ownership description
and nonempty authoritative TDS identity. No live deployment success is claimed.
The broader, unchanged ontology suite has 29 baseline namespace-contract errors;
these are not a green full-suite result.

Live preflight verified the existing Enabled subscription, the same FEATURE
workspace on Active F8 capacity `fabcapswc` in Sweden Central, and the existing
tenant SPA `3ccdb72d-6c81-4459-b9fd-d9293c242555`. `npm run validate-env`
initially failed because the retained STABLE-target `.env` lacked the required
explicit energy-map flag. Its previous implicit disabled default was restored
as `false` without editing any generated state or target IDs; validation then
passed. The orchestrator must rotate target-specific state itself.
The previous pipeline job is terminal Failed; no concurrent setup run was found.
Static role/policy review: the patch changes Python operation handling only,
with no new IaC, Azure resource, principal, grant or policy exemption. Existing
Fabric read/definition permissions were verified live. ARM what-if, new
container builds and azd environments do not apply to this source-only repair.
The first application build identified missing locked dependencies. After
`npm --prefix HydroOperationsApp ci` restored them, `npm --prefix
HydroOperationsApp run build` passed TypeScript compilation and Vite production
bundling (25.59 seconds, existing bundle/mixed-import warnings only). This
preflight used installed Node 26; the canonical production orchestrator must
still select Node 24 itself. No dependency manifest or lockfile changed.

### First live recovery result

All three SQL-support notebooks were updated in place with exact source
readback. Their existing metadata, parameters and other definition parts were
preserved; backups are held in the session workspace. No Git import occurred.
The real local Core setup action started job `9495fe995ae64569b27a398172f5e20b`
and Fabric pipeline run `bdc421a7-ca9b-4176-8080-a0ef0c6746e3`, with suffix V12.
NB01 run `12005a6d-e736-4645-b995-0c709bdc8d26` succeeded, retaining SQL item
`2478e550-20c0-409b-832b-50f7a61f5dec` and moving it into the existing setup
folder `b309eae1-0393-4c3e-b660-9e92402a65de`.

Stage 2 then failed in NB05 with `Only the default namespace is managed: ''`;
downstream activities and energy were correctly not started. This establishes
that the earlier baseline namespace failures are actual deployment blockers.
The repair now makes direct-setting reads description-aware, inserts backing
tables beneath the entity declaration, and preserves adjacent property
descriptions. All 257 tests across ontology generation, semantics, setup contracts,
agent capabilities, SQL lifecycle and the initializer pass with Python UTF-8
mode. The extra agent-capability errors were Windows cp1252 logging failures;
UTF-8 execution resolves them without changing application behavior. This is
not an all-repository test claim. All six affected live notebook definitions
have exact repaired-source readback with their other content and metadata
preserved. Retry the same V12 core job before attempting app deployment.

### Second live recovery result: Data Agent execution blocker

Local Core job `2e770791b13b4d7daa2f0a721477ce0a` started Fabric pipeline
`29600eea-abf9-4884-a988-855072319a40`. It progressed beyond the prior
NB05 namespace failure but failed in Stage 2 notebook run
`849742ae-7849-46a2-8c00-bf0db5fa080b`:

> NB09_dataagent: Data Agent published but ontology runtime failed: The
> associated Lakehouse source cannot be queried because no SQL/Lakehouse
> query tool is available in this session.

NB11 was blocked by NB09, and the local workflow correctly stopped before
energy, Foundry or final app publication. Deployment is not complete.

Read-only checks after the failure verified:

- The V12 agent `19547f30-0cf9-484a-9e1c-0c4d39ac97c2` has both draft and
  published selected Lakehouse tables, Eventhouse telemetry, and the correct
  V12 ontology context. The Lakehouse artifact and workspace IDs match the
  target; required static tables are selected.
- The V12 Lakehouse SQL endpoint reports provisioning status `Success`.
- The selected generation-2 ontology has a downstream CascadeDelete graph
  association. A missing graph binding has not been established as this
  failure's cause; graph query/node readiness is not attested by lineage.
- A fresh delegated MCP session independently reproduced the failure: the
  agent says only the Eventhouse telemetry query tool is exposed, not a
  Lakehouse/SQL tool. HTTP success and publication therefore do not establish
  source execution readiness. No smoke-test gate was weakened.
- A Fabric portal browser opened to the target agent requires interactive
  sign-in. Inspect its source/runtime diagnostics after signing in before
  guessing a configuration change or rerunning the whole setup pipeline.

### API-only diagnosis and third recovery attempt

The portal sign-in was not required to diagnose the source definition. Microsoft's
exported datasource example and its published datasource schema use native
`lakehouse_tables.schema` roots with table children, not the initializer's
UI-only `schema_grouping` and `table_grouping` wrappers.
A guarded repair flattened only those wrappers in the existing draft Lakehouse
source, preserving semantic-node IDs, selections, descriptions and all other
agent parts, then verified and published that change. Server JSON formatting
differs from submitted formatting; semantic JSON comparison verified the target
source, with exact comparison for unrelated parts, before publication.

A fresh delegated MCP call then returned three actual facility ID/name rows
and a CSV result attachment, replacing the previously reproduced missing-tool
response. This is live functional evidence for the hierarchy repair, not an
all-source or end-to-end deployment claim.

The actual successful response also exposed a strict-parser issue: Fabric's
`Source result: ... (3 rows returned).` footer was rejected after fenced JSON.
NB09 now accepts that bounded footer while rejecting trailing errors; the
independent row-comparison gate remains in place. Both failures were reproduced
as red tests before implementation. All 259 affected regressions now pass.

NB09 generates native Lakehouse hierarchies and normalizes legacy wrappers in
context-backed reruns without changing source selections or adding duplicate
sources. Its repaired live notebook source has exact readback, retaining other
cells, dependency metadata, parameters and definition parts. The third local
Core retry is job `78af860a9d3040b3984c8e12c93383f1`, still targeting V12 without
Git import, reset or deletion. Await its terminal result before final deployment.

### Core completed; energy-only pipeline correction

The third Core pipeline, `9f644475-c3a0-4960-a5c4-0406f88c1e3a`, completed
successfully, including the previously blocked NB09 and NB11. A fresh delegated
Data Agent query subsequently executed `COUNT(*)` on V12 SQL `dbo.WorkOrders`
and returned 12 with a CSV query attachment. The existing SQL item was reused.

The following energy step first encountered a Fabric GET timeout. Retrying only
energy resolved that network failure and completed Geo_001 ingestion, but the
Git-managed pipeline then ran optional Geo_002 without a default Lakehouse.
It failed with `lakehouse workspace: missing/invalid UUID`. This was not an
ingestion failure or authorization to enable map chat.

The canonical `04_Pipe_EnergyMap` now contains source ingestion only. Explicit
`enable_map_chat` setup binds and runs the publisher separately after fresh or
reused ingestion. Ordinary energy setup does not bind/run that optional notebook.
All 130 focused feature-workspace, map-agent and initializer tests pass,
including opt-in/opt-out and fresh/reused ingestion coverage.
The live pipeline was backed up and updated in place, removing only the
unconditional optional publisher, with verified content and unrelated-part
readback. No Git reimport, data reset, notebook-source change or new chat agent
was performed. Energy-only retry `f43aba283d6b4ba18a3b43816b3f19cf` is running
pipeline instance `e914a0f3-ff53-428f-b60c-3b82e4f116e9`; await its result.

### Final live deployment receipt: October 26, 2026

Energy-only retry `f43aba283d6b4ba18a3b43816b3f19cf` completed successfully.
The pipeline completed, and serving readback verified all 12 source layers,
9 area polygons and 76 asset-message links. It printed `ENERGY_SETUP_SUCCESS`;
no optional map-chat publisher ran.

Before app publication, `npm --prefix HydroOperationsApp run validate-env`
passed. The mandatory `--push-config` command requires a clean, pushed non-main
branch, so publication used `deploy/v12-live-recovery-20261026`, not a weakened
Git guard. Multiple Foundry projects existed; the previously approved project
`ai-project-hydro-foundry-884a839f3132` was resolved by its actual ARM resource ID
in subscription `dac16501-5c35-4c91-aff0-11af8de17f2e`. The canonical orchestrator
verified that selection directly. No replacement project or model was created.

The canonical orchestrator completed at `2026-10-26T12:01:47Z`, exit code 0,
in 618.813 seconds. It selected Node 24, reused locked dependencies and the
existing `gpt-5.6-sol` model deployment, and verified persistent specialists:
QA v2, RCA v2, work-order v3, Fabric IQ v2 and Weather v1. Hosted orchestrator
version 12 became active on image tag `src-247959b77642fffb`, configured for
the actual V12 sources. The current AppBackend ID is
`0968e45b-fa21-4718-9e99-530edd07d3df`.

It reapplied backend runtime/CORS, SQL and connector configuration, completed
the repository live-auth setup, and preserved all 40 existing SPA redirects
while adding the current origin (41 required redirects verified). Actual
browser-equivalent checks passed:

| Path | Preflight | POST | Scope |
|---|---|---|---|
| `/graphql` | 200 | 200 | Backend GraphQL readiness |
| `/api/auth/v1/token` | 200 | 400 | Invalid-payload/auth transport readiness, not user token exchange |
| `/connectors/hydrolakehouse/graphql` | 200 | 401 | Protected transport readiness, not source data acceptance |
| `/connectors/hydrooperations/graphql` | 200 | 401 | Protected transport readiness, not source data acceptance |

The orchestrator printed both `SUCCESS` and:

`DEPLOYED_APP_URL=https://jade-dew-88c8451584-swedencentral.webapp.fabricapps.net`

The generated redirect configuration was committed and pushed by the engine
as `c425a1d`. An independent browser visit displayed the expected private-app
Fabric sign-in gate. Interactive signed-in SPA acceptance, all-nine-entity
connector data acceptance, business-prompt correctness, Teams/email delivery
and optional map-chat execution are not certified by this deployment receipt.
The actual Core setup, energy serving readback, Data Agent Lakehouse/SQL
queries and complete publication/transport contracts above are verified live.

## Current SQL rollout validation (October 25, 2026)

This section supersedes the historical validation below for the pending SQL
lifecycle app rollout. Keep the existing FEATURE tenant/workspace/capacity,
SPA, and Foundry project. No replacement SQL database or retirement was
authorized; preserve the existing database identity and immutable name.

- `npm run validate-env`: passed again.
- `npm run build`: passed, including TypeScript compilation and Vite build
  (34.60 seconds). This local compilation used Node 26.12.1; the canonical
  deployment orchestrator must still resolve Node 24 for its production build.
  Existing mixed-import and bundle-size warnings are nonblocking.
- Source is unchanged since the 69 focused SQL/deployment/setup tests passed;
  no broad suite was rerun.
- Static role verification: this continuation adds no IaC, Azure resources,
  identities, or role assignments. Existing target selection remains unchanged.
- Read-only Azure CLI account check verified the selected tenant. A subsequent
  read-only lookup of the existing SPA failed with the exact Entra challenge
  `InteractionRequired / TokenCreatedWithOutdatedPolicies`.
- Interactive sign-in and a replacement SQL migration were offered through the
  user-input tool; the user was unavailable. No credentials, cache manipulation,
  alternate registration, or consent workaround can substitute for that sign-in.
- No deployment command was rerun after the failed authentication prerequisite.
  Rayfin deployment state and the hosted application were not changed.

### Authentication recovery and current validation proof

The user returned and explicitly requested a fresh authentication request.
The tenant-scoped interactive login completed as the existing target-tenant
user. Initial Graph requests continued to return the CAE challenge. Subsequent
authentication used the actual Graph `WWW-Authenticate` claims challenge,
including a supported browser-flow attempt; no policy, grant, or registration
was changed. Graph later returned HTTP 200. Do not attribute recovery solely to
the scope spelling or broker selection: the final successful probe did not
require another login, so those individual causes are not established.

Final read-only checks using the refreshed tenant cache passed:

- Azure CLI `ad app show` verified the existing
  `Hydro Operations Fabric Client`, client ID
  `3ccdb72d-6c81-4459-b9fd-d9293c242555`, in the target tenant.
- Fabric workspace GET verified `ws-FabricIQAppsDemoFEATURE` and its unchanged
  assigned capacity `0f4dac6b-ad68-483d-8027-3d8bf9655d53`.
- `npm run validate-env` passed again immediately before deployment.
- The previously recorded compilation and focused tests remain applicable;
  no application source changed during authentication recovery.
- The azure-validate workflow completed proof/error resolution and authorized
  updating status only after these checks passed.

Pre-deployment validation is complete. Execute only the canonical deployment
command recorded below; its own Node 24 build, live-auth preservation,
capacity, runtime/CORS, SQL/connector and POST-readiness checks remain mandatory.
A successful authentication check is not a deployment success receipt.

### Verified canonical redeployment receipt

The canonical command completed successfully on October 25, 2026,
09:59:55-10:09:27 UTC, in 572.094 seconds (9 minutes 32 seconds), from
feature source `853e7e8`. It printed both `SUCCESS` and
`DEPLOYED_APP_URL=https://brisk-ford-6c2fad844b-swedencentral.webapp.fabricapps.net`.

The tenant-only Graph sign-in had left a placeholder default account whose ID
was the tenant ID. Before the successful run, subscription discovery was
refreshed and the existing Foundry subscription
`dac16501-5c35-4c91-aff0-11af8de17f2e` was selected after verifying its tenant
and Enabled state. No new project, resource, or grant was created.

- All five existing prompt agents were verified, including `hydro-weather-agent:1`.
  Hosted `hydro-orchestrator` retained current image `src-247959b77642fffb`.
- The verified published SQL source was reused; no NB11 rerun or demo DML occurred.
- AppBackend runtime/CORS, SQL configuration and both connectors were reapplied.
- All 40 existing SPA redirects and the configured delegated scopes were verified.
- Required browser preflights passed. `/graphql` POST returned 200; the
  unauthenticated token/connector POSTs returned their expected protected
  validation responses, not successful data-query results.
- Hosting was verified as the identity-matched protected sign-in gate. A separate
  browser reload also reached that gate; automated sign-in-button interaction
  timed out. Authenticated app data access and agent execution were not certified.
- Existing SQL identity/name and the AppBackend-managed child were retained.
  No replacement migration or database deletion was authorized. V3 was unchanged.

This supersedes the earlier authentication-blocked deployment status, not the
separately documented Weather provider or business-runtime acceptance limits.

## Current approved deployment-optimization validation (October 24, 2026)

This section supersedes the historical target below for the current request.
Reuse the existing FEATURE deployment; no new infrastructure, workspace Git import,
setup-pipeline execution, seed writes, capacity resize or resource deletion.
The user requested timing, speed and onboarding improvements and authorized resuming
only the assigned capacity if it pauses.

- Tenant: `ad340c84-1886-4202-a483-2da2cb9168eb`.
- Subscription: `dac16501-5c35-4c91-aff0-11af8de17f2e`.
- FEATURE workspace: `c7da7c28-0dee-4f45-bc9e-5628bb882732`.
- Assigned capacity: `0f4dac6b-ad68-483d-8027-3d8bf9655d53`, Sweden Central.
- Existing Foundry project: `ai-project-hydro-foundry-884a839f3132`.
- Recipe: existing Python AZCLI/REST orchestrator; no separate azd deployment.
- Command: `python Raw\workspace-reset\deploy_fabric_app.py --tenant ad340c84-1886-4202-a483-2da2cb9168eb --workspace c7da7c28-0dee-4f45-bc9e-5628bb882732 --push-config`.
- Validate deployment/hosted/UI regressions and `npm run validate-env`; then one
  measured canonical redeployment, preserving all 40 workspace items and all
  40 existing SPA redirects. AppBackend CORS/SQL/connector checks remain mandatory.
- No fresh subscription/tenant is provisioned for testing. Least-privilege guidance
  distinguishes routine operation from one-time administrator preparation.
- Hosted deployment/readback does not certify the separate blocked business prompts.

### Current validation proof

Validated checks executed October 24, 2026 (UTC), before publication:

- `python -m unittest test_deploy_fabric_app test_deploy_hosted_agent test_webapp_server`
  from `Raw\workspace-reset`: 197 passed, 13.126 seconds.
- Node 24 `npm run validate-env`: passed; then `npm run build`: passed (Vite 35.22 seconds).
- Pylance syntax checks for the deployment engine and hosted publisher: no syntax errors.
- Live GET of selected workspace verified capacity ID, F8, Sweden Central, state Active.
  ARM lineage is `/subscriptions/078b9318-a6c7-4069-9045-a1bee6102239/resourceGroups/rg_fabric/providers/Microsoft.Fabric/capacities/fabcapswc`.
  No resume was necessary. This is distinct from the selected Foundry subscription.
- Live preservation baseline: all 40 Fabric items and all 40 SPA redirects captured.
- Local loopback browser check: both advanced groups collapsed and containing creation
  controls; subscription discovery returned 92 enabled signed-in subscription entries.
- Static infrastructure/RBAC review: none changed; no provisioning, new grants or resources
  are requested. Existing immutable hosted image/version remains the publication comparison.
- No ARM/Bicep/Docker infrastructure validation applies to this tool-only change.
- First measured canonical deployment passed in 518.469 seconds (8m 38s), with all
  mandatory checks. Its Rayfin login-status diagnostic alone consumed 83.187 seconds.
- Follow-up replaces only that diagnostic with an authenticated exact-workspace read,
  using the same scoped token explicitly supplied to every Rayfin command. Live probe:
  2.000 seconds. Authentication errors and wrong-workspace responses fail closed.
  All 197 affected tests passed again (12.487 seconds); Pylance verified both
  production call sites are compatible. No configuration or infrastructure changed.
- Final canonical deployment of `b9c32c8` passed October 24, 2026, 08:26:27-08:32:18 UTC,
  in 351.375 seconds (5m 51s). Authentication was 1.484 seconds, compared with 83.187
  seconds in the first 518.469-second warm run. The observed overall reduction was
  167.094 seconds / 32.2%; other sections also reflect service-latency variation.
- Exact before/after inventory matches all 40 item IDs/types/names, all 40 prior SPA
  redirects remain, and the retired API remains absent. Capacity still Active; no resume,
  resize or reassignment. Hosted image unchanged and verified current.
- All endpoint/CORS/SQL/connector/consent checks passed. No pipeline rerun, seed writes,
  new probe resources or operational writes. Both owned browser-test servers stopped.

### All validation checks pass

- [x] Core AZCLI/auth/configuration validation: current target subscription/tenant read;
  `npm run validate-env`; focused deployment/hosted/UI tests (197 passed).
- [x] Build verification: existing app build and local zero-build deployment UI.
- [x] Static role verification: no IaC, role assignments or Azure resources changed.
- [x] Record current proof and resolve any failures before setting Validated.

Recipe adaptation: this update changes an existing Python/REST deployment tool, not ARM
infrastructure. Bicep build/ARM what-if/new Docker build are not applicable; retain the
canonical publisher's immutable image comparison and do not create probe resources.

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

### Minimal capability execution (October 28, 2026)

The [current scoped architecture proof](#current-scoped-architecture-hardening)
records the unchanged target, decisions, applicability and remaining boundaries.
Executed validation:

- `python -m unittest test_foundry_supervisor test_semantic_planning
  test_presentation test_source_auth test_durable_service -q`: 215 cases,
  214 passed and one live opt-in skipped after native-contract and plan corrections.
- With the opt-in environment and existing deployed model,
  `python -m unittest test_semantic_planning.LiveSemanticArchitectureTests -v`:
  all eleven semantic cases passed, with no business reads/writes or HTTP
  resource warnings after explicit client-lifecycle correction.
- `python -m unittest test_deploy_hosted_agent test_provision_foundry_agents -q`:
  86 packaging/provisioning regressions passed.
- Node 24 `--import tsx --test` for `agent-trace.test.ts` and
  `weather-agent.test.ts`: all 12 passed.
- `npm run validate-env`, `npm run build`, TypeScript `--noEmit`,
  targeted `eslint` for `AgentCrewTrace.tsx`, and `git diff --check` passed.
  Vite retains existing large-chunk warnings; Pylance retains exactly the
  four remaining pre-existing backend errors and introduces no new ones.

Initial authenticated publication: app 1.0.1173 / `f1784e6`, hosted coordinator
v42, toolbox `hydro-fabric-iq-tools:2`, image `src-6dc7a516a6ddd2f2`. Canonical
deployment returned `SUCCESS`; endpoint/CORS checks and all 42 preserved SPA
redirects passed. Capacity readback remained Active F8.

- Facility facts `a0ec0221-8dc5-43ee-92bf-da1175c960a8`: three records, QA reader
  only, no specialist model or interpretation; 8.3 seconds in the app.
- Native open-work counts `62c64792-8c61-4560-a359-3ed4ca1d2476`: Sloy 3,
  Foyers 3, Pitlochry 4; native-only manifest, exact original source/presentation
  text and recomputed SHA-256 match; 53.5 seconds.
- Weather `b1da5e94-f369-4732-a111-2618ebf66685`: three station and 12 forecast
  records, Weather readers plus interpretation, no prompt-agent loop; 20 seconds.
  The answer explicitly disclosed absent Aurora data and the issue-time window.
- RCA `527545ca-88a1-4e46-9d70-7f3ed2526a40`: native context then scoped RCA,
  four checked competing hypotheses, missing fresh/independent evidence and
  `maintenance_follow_up=not_supported`; no proposals or production writes.
- Diagnostic attempt `fee62150-2bc3-4f99-a191-2fd9084f6dfd` failed before Work
  Order at the legacy bundle gate. A new exact-prompt regression reproduced it.
  Four focused native regressions and the full 211-case suite pass after
  isolating that legacy schema from semantic native execution. Provider-error,
  source/equipment identity and separate draft/approval validators remain.

The subsequent v43 publication (app 1.0.1174 / `7c5ed6e`, image
`src-d408ab690422534e`) also passed canonical deployment checks. Its diagnostic
run `340c2431-8f75-4fce-90c3-a3b70c6c97ad` was rejected at typed plan validation
before dispatch, exposing the need for one bounded model correction. New
regressions verify cross-field correction, repeat catalog rejection, recovery
without reissuing a committed invalid attempt and absent-model failure.
The latest eleven-case live evaluation includes that exact diagnostic prompt;
all cases passed in one planning call each, approximately 5.7-15.5 seconds.
This tests model planning, not the pending authenticated end-to-end rerun.

### Current deployment-tool optimization (October 24, 2026)

The current target and executed validation evidence are recorded in
[Current validation proof](#current-validation-proof) above; they supersede the
historical deployments below. The azure-validate workflow completed its recipe,
build, static-role, proof and error-resolution steps before this status change.

### Administration full map update (2026-09-25T11:25Z)

- 51 focused frontend/component tests, TypeScript, ESLint and Node 24 production
  build passed.
- Both-shell browser tests exercised the actual service with isolated Fabric
  responses: the start body contains all/force parameters, reload resumes the
  same run, Map-tab actions join the pending Administration run, an active run on
  a later history page is found, and failed history reads never trigger a write.
- Source failures are shown explicitly and the section reports indeterminate
  progress, not a manufactured percentage. Completed actions can be repeated.
- Live readback confirmed the existing pipeline's notebook IDs, concurrency=1,
  sequential chat-refresh dependency and parameter forwarding. Only definition
  reads were performed; no full source import was started during validation.
- Same approved tenant/subscription, no infrastructure or permission changes.

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

## 19. Administration full map-data update

User requested a notebook/pipeline covering every map source and an Administration
section to start it. Reuse the already deployed `Geo_001_ingest_energy_context`
and `04_Pipe_EnergyMap` instead of duplicating ingestion; the pipeline already
runs `Geo_002_publish_map_agent` after successful ingestion when chat is enabled.

The new shared Administration section requests `refresh_mode="all"` and
`force_refresh=true`, displays actual job states and elapsed time, and restores
long-running map jobs after reload. It can run again after completion. Existing
Map-tab imports remain cache-aware. Local callers share the same active request;
cloud job-history checks fail closed and include pagination. Expired monitoring
tokens are renewed silently; repeated read failures are surfaced rather than
reported as success.

Live definition readback confirmed both notebook IDs, serial dependencies,
pipeline concurrency=1 and forwarding of both refresh parameters. No new
notebook, pipeline, schedule, source write or infrastructure is needed for this
UI-only change. Deployment must use the canonical app-only path. No full
national refresh is started implicitly while adding this control.

Code `3a52c20` was deployed through the canonical app-only workflow:
`deploy-20260925112700-c46abfdf`, exit code 0, `DEPLOYED_APP_URL` and `SUCCESS`.
Existing Entra live-auth contracts passed despite transient Graph retry warnings.
Hosted V1/V2 checks confirmed the Administration section, enabled start action,
all source descriptions, notebook/pipeline names and desktop/tablet/mobile layout.
Displaying Administration did not issue any job-start request. Six hosted Map
layout regressions also passed. The notebooks and pipeline were reused unchanged;
no full data refresh or recurring schedule was started by this deployment.
