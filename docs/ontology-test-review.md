# Ontology_Test review and operational-store recommendation

Reviewed on October 24, 2026. This is an assessment, not a migration or runtime
acceptance certificate. `ws-vteam-demoV3` was accessed read-only.

For the October 27 **v22 / app 1.0.1119** Foundry follow-up, see the
[dated operational RCA receipts and native provider blocker](foundry-multi-agent-implementation.md#october-27-v22-publication-operational-rca-passed-native-provider-blocked).
The older agent/runtime results below remain historical, not current acceptance.
The later [UKMet-first Weather acceptance](weather-fabric-port-plan.md#october-27-ukmet-first-live-acceptance)
supersedes the earlier all-weather ingestion blocker: UKMet works independently,
while Aurora remains disabled rather than credential-repaired.

## Decision

**The expanded domain model is useful, but do not adopt `Ontology_Test` as-is.**
Its weather, maintenance, spare-parts and 3D-model vocabulary covers more of this
project than the current core ontology. However, incorrect physical bindings,
unbacked operational relationships and application incompatibilities prevent it
from being a safe replacement.

Use its descriptions, synonyms and domain coverage as a design reference.
Extend the verified FEATURE ontology incrementally, preserving its core entity
identities, executable static/time-series stitch and existing bindings. Do not
copy V3 source IDs, replace a populated ontology or infer graph ownership.

**An independently provisioned Fabric SQL operational store is a sound target
architecture, but it is not a prerequisite imposed by the richer ontology.**
The current operational database is already a separate Fabric workspace item;
what remains coupled is its provisioning/schema lifecycle and the app data API.
Do not confuse that lifecycle change with moving data into the Lakehouse or
requiring an Azure SQL deployment.

No candidate fixes were written into V3, no FEATURE sources were switched, and
no extra databases, ontologies or agents were created during this assessment.
Those statements describe the read-only assessment. FEATURE implementation now
extends the existing ontology rather than migrating the V3 candidate. The
independent-store cutover is implemented and locally validated; its live
deployment verification and remaining acceptance limits are recorded below.

## FEATURE implementation

- Preserve the original five core entities, bindings and unknown definition parts.
- Add five operational entities and eight weather/classification entities with
  actual physical source names and 13 physically backed semantic relationships.
- Represent composite natural identities with a physical, deterministic string
  `ontology_key`; emit exactly one TMDL `keyProperty`. Preserve all natural members,
  including observation time and forecast reference/valid time.
- Provision independent operational SQL during either NB01 setup variant;
  fresh items now use `RTI_Demo_SQLDatabase_<suffix>`. FEATURE now uses
  `RTI_Demo_SQLDatabase_V11` in `RTI_DEMO_V11`; the immutable
  `HydroOperationalStore` is retained as a write-blocked archive. NB11 handles
  the explicit, verified legacy-record cutover for an existing workspace.
- Route operational reads and approved writes through the delegated
  `fabric-sqldatabase` connector. Keep the original database and every original row.
- Preserve compatible custom Data Agent SQL content, deselect only the five
  archived base tables, and verify both draft and published definitions.
- Keep native graph materialization separate; the original core projection is not
  replaced or reconstructed from SQL relationships.
- NB11 pins complete source metadata on the three verified shared TMDL
  expressions using live workspace/Lakehouse/SQL item metadata. Preserve existing
  pin timestamps and unknown annotations; reject conflicting source identities.

Fabric itself rejects the candidate's repeated `keyProperty` authoring, not just
the app parser. A single comma-separated declaration is also rejected. The app
therefore retains the same fail-closed singleton contract. NB11 backfills only the
new physical key column in the three composite-grain tables, verifies nonnull and
unique identities, and rejects conflicting pre-existing key values. The producer
notebooks preserve those keys on refresh. JSON-encoded named key members are
SHA-256 hashed with explicit UTC/microsecond timestamp formatting.

## Live FEATURE verification: October 24, 2026

The canonical deployment of source `53a454c` passed all eight deployment sections
from **19:22:03 to 19:44:42 UTC** and printed both `SUCCESS` and
`DEPLOYED_APP_URL`:
<https://brisk-ford-6c2fad844b-swedencentral.webapp.fabricapps.net>.

Verified outcomes:

- FEATURE's existing numeric-generation-2 ontology now has **18 entities,
  17 semantic relationships and 44 definition parts**. The original core
  definitions and bindings passed preservation readback. The actual Fabric
  readback is also an unchanged/idempotent input to the revised extension builder.
- NB11 completed on **Spark Runtime 2.0** in **353.181 seconds**, including queue
  time. It verified every copied field/ID and SHA-256 receipt for all five tables:

  | Operational table | Original rows preserved and copied |
  |---|---:|
  | WorkOrders | 12 |
  | Inspections | 30 |
  | SpareParts | 12 |
  | Asset3DModels | 15 |
  | MaintenanceNotifications | 6 |
  | **Total** | **75** |

- The active independent database is `HydroOperationalStore`
  (`7972fa8c-e2cd-4c9a-bdd6-053b37293f19`). The original database and rows remain.
  NB11 verified the stale-client write boundary before publishing the new source.
  No demo DML or production test work orders were requested.
- Data Agent SQL source publication passed exact draft/published readback.
  Compatible custom content was retained.
- Both delegated connectors were applied successfully. The orchestrator verified
  the generated AppBackend endpoint, browser-equivalent CORS/preflights, protected
  transport readiness and preservation of all **40 Entra SPA redirects**.
- `hydro-orchestrator:5` is active on image `src-c1c91fbfbc71e70c`.
  All five specialist prompt-agent definitions were verified, including
  `hydro-weather-agent:1`, in **`ai-project-hydro-foundry-884a839f3132`**.
  The older `foundryfabriciqappsdemo` project was not changed.
- Final read-only checks confirmed **all 48 V3 candidate definition parts and all
  45 V3 item identities unchanged**. All **40 original FEATURE item identities**
  remain. The only added Fabric items are the intended independent SQLDatabase
  and its automatically created SQL analytics endpoint.
- The original bound native graph still exposes its queryable schema of **five
  node types and four edge types**. This is a schema check, not a graph-instance
  or business-answer execution certification.

### Deployment timing

| Canonical deployment section | Seconds |
|---|---:|
| Tenant, workspace and capacity verification | 18.891 |
| SPA resolution and redirect preservation snapshot | 15.890 |
| Source setup/cutover, specialists, new Hosted Agent image and activation | 863.531 |
| Tenant-scoped Rayfin authentication | 1.657 |
| Static application build/publication | 143.000 |
| Backend runtime/database settings and both connectors | 179.125 |
| Browser sign-in permissions and consent verification | 71.547 |
| Hosting, endpoint, CORS and protected transport readiness | 65.046 |
| **Total** | **1358.687 (22m 38.687s)** |

This was a one-time operational-store cutover **and a new remote container build**,
not the previously measured warm deployment. The 353.181-second NB11 job is
included in section 3, not additional to the total. Subsequent unchanged runs
verify the published source bindings and reuse the immutable image. The later
cold-graph UI fix required a warm deployment, recorded below. The earlier
351.375-second warm measurement is documented in
[DEPLOY.md](../HydroOperationsApp/DEPLOY.md#speed-changes-and-measurements).

### Acceptance limits: do not report everything as runtime-certified

1. Additional graph entities/relationships still need the selected ontology's
   **Manage graph -> select eligible entities/relationships -> Continue ->
   Materialize** portal flow. No public materialization API was invented.
   Before the source-metadata repair below, the portal marked all 18 entities
   **Ineligible**, with **Unsupported Datasource** on the inspected core entity.
   The post-repair portal check still reports all 18 entities **Ineligible**.
   Do not describe additional projection as merely a pending materialization
   click; the existing core graph's successful execution is a separate result.
2. Hosting verification reached Fabric's protected sign-in gate, not an
   authenticated SPA session. A read-only operational connector probe using the
   existing Azure CLI token was rejected by the brokered token endpoint with
   **`Delegated scope 'Item.Execute.All' is required`**. The SPA's own delegated
   permission/consent configuration passed, but that does not add the permission
   to Azure CLI's separate client identity. A named-scope acquisition attempt was
   stopped when it did not complete; no CLI consent grants were changed and no
   authentication bypass was used. Authenticated UI/connector acceptance
   subsequently passed through normal user sign-in in a working browser tab.
   The hosted maintenance page displayed work order `WO-2841`, two T001
   inspections, its notification and all 12 spare parts. All five observed
   `hydrooperations` connector requests returned HTTP 200.
3. Successful source publication and an active Hosted Agent do not certify Data
   Agent SQL/combined-source answers, execution provenance, Weather inference or
   data freshness. No such runtime certification is claimed.

### Follow-up: source metadata and remaining browser boundary

The portal's **Unsupported Datasource** finding matches the public report in
[microsoft/skills-for-fabric#107](https://github.com/microsoft/skills-for-fabric/issues/107),
but the reported partition-only workaround did not directly round-trip here.
Adding metadata to only one table sharing an expression was rejected with
`ALMOperationBadRequest`: the shared expression was missing a valid
`ONT_WorkspaceId`. Submitting complete matching metadata across that source was
accepted, and Fabric normalized the source identity onto **`expressions.tmdl`**,
retaining only `ONT_PinnedAtUtc` on those partitions.

The final repair therefore authors the retained canonical form, without weakening
readback verification. All three managed shared expressions (core Lakehouse,
project Lakehouse, independent SQL) now have the eight source annotations, derived
from live workspace/item metadata rather than copied workspace names or endpoints.
`updateDefinition` returned HTTP 200; exact scoped readback passed for every
submitted setting and every original definition. A second authoring pass over
the actual server readback was unchanged. NB11 now applies the same helper before
publication, preserving existing pin timestamps and rejecting identity conflicts.
The related Python regression suites passed **217 tests**.

This repairs source authoring, **not a verified additional graph-materialization
result**. A working fresh portal tab subsequently confirmed that all 18 entities
still show **Ineligible**; WeatherLocation reports **Unsupported Datasource**.
The existing selected native graph nevertheless executes and renders its
previous materialized topology, with native provenance, asset navigation and SQL
context for actual native equipment. Successful cold node/edge requests took
47.233/54.733 seconds and warm requests 3.603/4.096 seconds. The old 30-second page
deadline incorrectly rejected the successful cold load; the bounded snapshot
loader now allows 150 seconds, with regression coverage for a 55-second query and
fail-closed expiry.

Normal hosted-app sign-in also succeeded in that working tab. No tokens were
injected, grants changed, graph APIs invented, or production work orders created.
Read-only Data Agent execution returned 90 active static signal mappings and
the expected SQL counts (12/30/12/15/6). However, Eventhouse returned **zero total
rows today**, with latest event time **2026-10-23T21:22:22.26164Z**; zero BAD rows
must not be presented as healthy current telemetry. These are observed responses,
not execution-provenance attestation. The deployed Weather prompt agent completed
a role-only inference check; that does not certify weather freshness or tool
execution. Extended graph materialization remains blocked by portal eligibility.
The metadata helper was exercised directly against the live API; the later
canonical deployment also published the cold-graph UI deadline fix.

### Follow-up: cold-graph deployment and Data Agent request contract

The canonical deployment of `a6b0b76` completed from **20:37:23 to 20:45:45 UTC**
in **501.671 seconds (8m 21.671s)**. It reused the verified operational SQL source
without NB11 or demo DML, and printed `SUCCESS` and `DEPLOYED_APP_URL`.

The subsequent `b62309a` request-contract deployment passed from **20:52:05 to
21:05:54 UTC**, including a new immutable Hosted Agent image and activation of
`hydro-orchestrator:6`. Remote image build `dt6` succeeded in 137.481 seconds.

| Canonical deployment section | `a6b0b76`, warm seconds | `b62309a`, new-image seconds |
|---|---:|---:|
| Tenant, workspace and capacity verification | 15.750 | 14.641 |
| SPA resolution and redirect preservation snapshot | 15.875 | 13.609 |
| Target/source verification and agent convergence | 163.718 | 389.735 |
| Tenant-scoped Rayfin authentication | 1.516 | 1.859 |
| Static application build/publication | 132.156 | 214.172 |
| Backend runtime/database settings and both connectors | 71.875 | 82.656 |
| Browser sign-in permissions and consent verification | 55.813 | 58.735 |
| Hosting, endpoint, CORS and protected transport readiness | 44.968 | 53.375 |
| **Total** | **501.671** | **828.782 (13m 48.782s)** |

All 40 original SPA redirects remained registered. The authenticated browser
loaded build `a6b0b76`; deployment checks themselves still do not certify agent
execution.

An authenticated facts workflow reached Sparky but failed closed with HTTP 500.
Logs from the **FEATURE** Hosted Agent session showed a transient native HTTP 500,
one bounded retry, and rejection of a response without the required inspectable
quality/work JSON bundle. The pre-question HTTP 422 was the deliberately invalid
empty-body cold-start warm-up, not a specialist execution.

Read-only diagnostics reproduced a Data Agent response describing an ontology
traversal stitch failure. That generated description alone does not establish
that supported SQL/KQL execution failed. The scoped request now explicitly uses
the ontology's associated source-native SQL/KQL tools, not ontology traversal;
retains one Data Agent delegation; requests only one JSON object; and checks
Lakehouse and SQL availability even when no BAD identifiers are returned.
Freshness is requested separately from the current-day BAD aggregate.

The exact revised compiled request returned a bundle in **45.6 seconds** that
passed the **unchanged** evidence validator: empty affected/work arrays, 90 active
signal mappings, 10 open work orders in the source availability aggregate, and
latest telemetry `2026-10-23T21:22:22.26164Z`. Its requested-window total field was
null and explicitly disclosed as a limitation. The source-wide open-work count is
not a count of work against affected turbines; no healthy-current-telemetry or
execution-provenance claim follows from this result. All **57** focused bridge
tests passed.

Authenticated `b62309a` acceptance then completed the facts workflow through
Sparky and Supervisor in **79 seconds**. It explicitly reported unavailable
current-day evidence, not healthy turbines, and retained the native-connection
SQL/provenance limitation. The draft and RCA/draft workflows failed closed at
Sparky in **64/76 seconds**, with the same inspectable-JSON rejection; downstream
specialists did not execute and no work order was created.

An independently reproduced parser gap rejected a valid JSON fence followed by
source notes. Parsing now accepts exactly one valid evidence bundle in explicit
JSON/unlabelled fences, ignores surrounding prose as evidence, skips other code
languages, and rejects malformed JSON or conflicting bundles. All original
identity, UTC-window, open-status, completeness and bound checks remain.
The actual captured fenced failure response now reaches the proper incomplete
coverage rejection rather than being mistaken for absent JSON. All 57 bridge
tests pass, including ambiguous/malformed/failed-coverage cases. The parser
correction was subsequently deployed as recorded below; it did not eliminate
intermittent absent-JSON responses.

The Weather specialist was actually dispatched, but its live forecast attempt
failed in **22.3 seconds** with upstream HTTP 429. Read-only ARM inspection
confirmed the shared model was then provisioned at **20,000 tokens/minute and 20
requests/minute** (`DataZoneStandard`, capacity 20). The later allocation change
and Weather acceptance are recorded below. Separately, successful Lakehouse connector reads returned no weather
locations/areas or current forecast rows. Do not claim a fresh forecast, successful
Weather tool execution, or an ingestion repair from agent publication alone.

### Structured-JSON baseline deployment and acceptance (Hosted Agent 7)

Source `2ab9cd1` passed all eight canonical deployment sections from
**21:17:58 to 21:31:29 UTC**, printing `SUCCESS` and `DEPLOYED_APP_URL`.
The authenticated SPA reports build **1.0.1051 / 2ab9cd1**. The active Hosted
Agent is **`hydro-orchestrator:7`**, image **`src-b1410f97e33d7a42`**.
All 40 original SPA redirects were preserved.

| Canonical deployment section | Seconds |
|---|---:|
| Tenant, workspace and capacity verification | 16.000 |
| SPA resolution and redirect preservation snapshot | 13.094 |
| Source verification, specialists, new Hosted Agent image and activation | 416.844 |
| Tenant-scoped Rayfin authentication | 1.547 |
| Static application build/publication | 201.921 |
| Backend runtime/database settings and both connectors | 62.844 |
| Browser sign-in permissions and consent verification | 55.860 |
| Hosting, endpoint, CORS and protected transport readiness | 42.953 |
| **Total** | **811.063 (13m 31.063s)** |

Post-deployment readback again preserved all 48 V3 definition parts, all 45 V3
item identities, all 40 original FEATURE items and the core definitions/bindings.
FEATURE retains 44 ontology definition parts and the original five-node-type,
four-edge-type native graph schema. No production test work orders were created
or approved.

After independent Sleuth and Weather throttling failures, only the existing
FEATURE `gpt-5.6-sol` deployment's `DataZoneStandard` allocation was raised from
20 to **60** using unused regional quota. ARM readback reported `Succeeded`,
**60,000 tokens/minute / 60 requests/minute**, with regional allocation 60 of
100. No new model, project or subscription quota request was created, and the
older V3 account was not changed. Immediate Weather/Fixer attempts still returned
429; the allocation change alone is not proof of a runtime repair.

Azure Monitor recorded 27,198 processed prompt tokens and 1,371 generated tokens
in the 21:43 UTC minute. A captured successful direct Data Agent result serialized
to approximately 11 KB. These observations do not establish that oversized
context caused throttling, so no speculative context rewrite or additional
deployment was performed.

Final authenticated, sequential acceptance used the three exact requested
BAD-quality prompts and one Weather availability prompt:

| Workflow | Observed result | Duration |
|---|---|---:|
| Facts: BAD quality today and existing work | Sparky and Supervisor completed; explicitly reported unavailable verified current-day evidence, not healthy turbines | About 80s |
| Facts plus work-order drafts | Failed closed at Sparky: no inspectable quality/work JSON bundle; Fixer did not execute | About 67s |
| Facts plus RCA and work-order drafts | Failed closed at Sparky with the same JSON-contract error; Sleuth/Fixer did not execute | About 73s |
| Latest available Weather forecast | Weather and Supervisor completed; both location and forecast datasets returned zero rows, explicitly reported as missing data | 25.2s |

Earlier v7 attempts reached Sleuth or Fixer after successful Sparky retrieval but
failed upstream with 429. One Fixer completion also rejected its structured
arguments before the bounded correction encountered 429. At this baseline, those paths had not yet
passed end to end. The latest successful Weather attempt occurred
later than the allocation change; it does not prove throttling is permanently
resolved or explain the earlier failures.

**Overall runtime acceptance remains incomplete.** The Data Agent's structured
response is intermittent; no validator was weakened, direct-source fallback
added, or generated prose promoted into certified evidence. Current-day telemetry
is unavailable (last observed timestamp `2026-10-23T21:22:22.26164Z`), Weather
mapping/forecast tables are empty, and added graph projection remains blocked by
the portal's Unsupported Datasource eligibility result. The empty-data checks do
not certify nonempty BAD-quality reconciliation, RCA, draft creation, approved SQL
writes or SQL/combined-source execution provenance.

### Native evidence and work-review follow-up (Hosted Agents 8 and 9)

Source `513c1f5` deployed Hosted Agent **8** from **22:03:58 to 22:17:24
UTC**. Source `236ca91` deployed Hosted Agent **9** from **22:30:07 to
22:44:55 UTC**. Both canonical deployments passed all eight sections.

The fixes preserve rejected raw native receipts before validation, validate the
complete MCP step rather than each intermediate receipt, and forward the complete
compiled assignment to the Data Agent. Missing JSON and incomplete coverage share
the existing **one** same-source correction budget; conflicting, malformed,
inconsistent or unbounded evidence still fails closed. There is no direct-source
fallback. The incomplete-coverage correction requests an exact UTC predicate and
a scalar telemetry count without `by`, preserving a count row for empty input.
Missing current telemetry or canonical identity now explicitly leads to
`needs_clarification`, not an unsupported `no_draft` coverage claim.

The v9 authenticated draft and RCA/draft workflows finished in **130/99 seconds**;
Weather finished in **26.4 seconds**. Facts still failed after **125 seconds**.
Downloading that run's two immutable native responses established the remaining
parser defect: both contained JSON followed by Fabric's generated-file footer.
The first also explicitly reported incomplete coverage and contradictory
requested-window count/freshness; the second contained a complete zero-row
bundle. The parser incorrectly classified both as absent JSON.

### Latest canonical deployment and authenticated acceptance (Hosted Agent 10)

Source **`489de40`** deployed from **22:55:47 to 23:07:05 UTC** in
**677.343 seconds (11m 17.343s)**, printing `SUCCESS` and `DEPLOYED_APP_URL`.
The authenticated browser loaded **1.0.1055 / 489de40**. The active Hosted
Agent is **`hydro-orchestrator:10`**, image **`src-247959b77642fffb`**.
Sparky remains version 2, Fixer version 3, and Weather version 1.

The JSON adapter now removes only the exact Fabric transport suffix
`File(s) result_N.json ... were generated as a result of the query.` before
parsing a complete JSON value. It does not extract evidence from arbitrary
surrounding prose. The original response and footer remain in the audit.
Replaying the actual rejected receipts verifies that the incomplete first
response still fails coverage validation and the complete retry now passes.
All **57** bridge tests, app typecheck and environment validation passed.
The preceding service fixes also passed focused native/review Python tests
and pyright with zero errors or warnings.

| Canonical deployment section | v8 seconds | v9 seconds | v10 seconds |
|---|---:|---:|---:|
| Tenant, workspace and capacity verification | 19.250 | 17.734 | 14.922 |
| SPA resolution and redirect preservation snapshot | 15.000 | 12.985 | 12.093 |
| Source verification, specialists, new Hosted Agent image and activation | 425.359 | 421.562 | 424.188 |
| Tenant-scoped Rayfin authentication | 1.641 | 2.578 | 1.406 |
| Static application build/publication | 164.047 | 142.594 | 67.656 |
| Backend runtime/database settings and both connectors | 82.093 | 186.360 | 57.735 |
| Browser sign-in permissions and consent verification | 55.813 | 58.156 | 58.859 |
| Hosting, endpoint, CORS and protected transport readiness | 42.953 | 45.265 | 40.484 |
| **Total** | **806.156** | **887.234** | **677.343** |

The final deployment reused locked dependencies, target Rayfin state and the
verified published SQL source, without NB11 or demo DML. All **40** original SPA
redirects remain registered. New immutable Hosted Agent builds/activation account
for the largest section; source-unchanged deployments can reuse the verified
image instead. The latest observed total is 23.7% below v9, but remote build and
Fabric backend timings vary; this is not a guaranteed speedup or a five-minute
fresh-deployment promise. The earlier source-unchanged deployment took 501.671
seconds. Neither fast path skips backend/CORS/readiness validation.

The final sequential authenticated checks used the three exact requested prompts
and a Weather availability prompt. Durations below are the application's
reported elapsed times, excluding browser-tool deferred-result handling.

| Workflow | Latest observed result | UI elapsed |
|---|---|---:|
| BAD quality today and existing work | Sparky and Supervisor finished; no affected turbines could be verified from the empty current evidence | 2m 10s |
| Facts plus work-order drafts | Sparky, Fixer and Supervisor finished; `needs_clarification`, no unsupported work order or draft | 1m 16s |
| Facts plus RCA and drafts | Sparky, Sleuth, Fixer and Supervisor finished; no evidence-backed turbine-level RCA or draft | 1m 23s |
| Latest available Weather forecast | Weather and Supervisor finished; location and forecast datasets explicitly returned zero rows | 24.6s |

**All four tested routes now finish on the latest deployment; full operational
data readiness remains incomplete.** Empty native claims do not identify affected
turbines, establish healthy telemetry or prove that equipment has no open work.
The responses retain the SQL/combined-source provenance limitation and label
specialist-only freshness notes as unverified rather than promoting prose to
evidence. No physical cause, nonempty reconciliation, staged diagnostic draft or
approved SQL-write execution is certified by this empty-data acceptance.
No production work order was created or approved. No HTTP 429 occurred in this
final batch; that does not establish permanent freedom from throttling.

Current-day telemetry remains absent in the source checks, with last observed
timestamp **2026-10-23T21:22:22.26164Z**. Weather mappings and forecasts remain
empty; publishing Weather is not an ingestion repair. The captured failed native
responses also carry output-moderation error annotations; the transport parser
fix neither repairs nor establishes the cause of that product-side annotation.
Additional ontology graph eligibility remains blocked by **Unsupported
Datasource** in the portal. These source/product prerequisites are not silently
turned into successful data, synthetic work orders or fabricated topology.

A subsequent read-only check established a concrete Weather ingestion blocker:
`03_Pipe_Weather` failed on its October 24 **09:20, 15:20 and 21:20 UTC**
scheduled runs in `Weather_002_fetch_area_weather`, with pipeline error **2451**
and notebook error **`Aurora STAC search failed with HTTP 401`**. The latest
failed pipeline job is `cc387744-15b1-4a57-aba3-f41f9e130785`.
An earlier Runtime 2.0 credential probe also recorded HTTP 401 with **invalid
subscription key**, not missing-key errors, for both the configured query-key
and APIM-header authentication methods. This is not evidence that swapping the
authentication parameter or retrying ingestion will fix the provider rejection.

The operator must install a valid Aurora/MAI Weather subscription key in the
configured Key Vault secret **`mai-weather-api-key`**, without putting it in
notebook parameters, source code or chat. Then rerun the existing Weather pipeline
and verify nonempty location/forecast serving tables and a successful Weather
answer. No provider credentials were displayed, copied from another environment,
or replaced during this check. The pipeline was not rerun with the known rejected
credential. Full Weather acceptance remains blocked until that external
credential prerequisite is satisfied.

The last full preservation readback, after v9, again confirmed all **48** V3
definition parts and **45** V3 item identities unchanged, all **40** original
FEATURE items retained, and only the intended independent SQLDatabase and its
automatic SQL endpoint added. FEATURE still has **44** ontology definition parts;
the original native graph schema remains five node types/four edge types.
The final footer-only app deployment does not alter that ontology or copy data.

### Short final verification follow-up

Read-only checks at approximately October 24 **23:24-23:32 UTC** reconfirmed
`hydro-weather-agent:1` as enabled through the Foundry API and displayed it as
**Agent running** in the correct FEATURE project. `hydro-orchestrator:10` remains
active. The working direct Weather portal link is now in the
[Foundry guide](foundry-deployment-and-multi-agent-setup.md#finding-weather-in-foundry).

The authenticated hosted app still reports **1.0.1055 / 489de40**. Its maintenance
page read the selected turbine's existing **WO-2841**, inspection records, an
existing notification and the spare-parts inventory through the deployed
connectors. No records were edited or created. The earlier same-deployment four
agent-route results above were reused rather than rerunning model requests or
test suites.

A fresh native-graph UI attempt remained on its loading screen during the bounded
150-second observation. The shared browser page reported `visibility: hidden`;
this observation does not establish a new graph-service or application-code
cause, and is not a fresh successful graph-instance verification. The earlier
successful native-core query/render and the separate expanded-graph portal
eligibility blocker remain the established results. A foreground graph check is
still needed before claiming current full graph readiness.

## Evidence and compatibility: pre-implementation assessment baseline

The following table describes the original read-only assessment, before the
FEATURE extension and independent-store cutover documented above.

| Check | V3 candidate | FEATURE assessment baseline |
|---|---|---|
| Workspace | `ws-vteam-demoV3` | `ws-FabricIQAppsDemoFEATURE` |
| Ontology | `Ontology_Test` (`6586f4b1-23d0-46b2-8ee8-2dd29255a41b`) | `RTI_Demo_Ontology_V11` (`1a5f7447-81be-4b90-ae00-e27b4aeb27b4`) |
| Live numeric generation | 2 | 2 |
| Definition parts | 48 | 18 |
| Entities / semantic relationships | 19 / 34 | 5 / 4 |
| Physical table declarations | 22 | Core Lakehouse tables plus Eventhouse backing |
| Actual app parser | Rejects duplicate `keyProperty` in `WeatherLatestObservation` | Parses all 5 entities / 4 relationships, without warnings |
| Native queryable schema read | Listed GraphModel returned HTTP 204, no schema | Explicitly bound graph returned HTTP 200, 5 node types / 4 edge types |

The candidate graph result is not proof of its ownership or a diagnosis of its
materialization state. It does mean this review cannot certify a usable native
projection. Names and item co-location are not authoritative lineage.

The complete candidate was exercised through the real
[`parseOntologyContract`](../HydroOperationsApp/src/services/ontologyContract.ts),
not a replacement parser. All candidate property value/ordering-column references
resolve to *declared* table columns. That structural check does not prove the
columns exist in the physical sources or that keys are unique.

### 1. Twelve Direct Lake partitions reference unlisted physical tables

Both Lakehouse table inventories were read with no continuation pages outstanding.
The following candidate partition `entityName` values are absent from the V3
Delta table inventory. The intended project table names are:

| Candidate physical `entityName` | Actual project table |
|---|---|
| `WeatherSource` | `weather_sources` |
| `WeatherVariable` | `weather_variables` |
| `Facility` | `silver_facilities` |
| `System` | `silver_systems` |
| `Equipment` | `silver_equipment` |
| `Instrument` | `silver_instruments` |
| `WeatherArea` | `weather_areas` |
| `WeatherObservation` | `weather_observations` |
| `WeatherForecast` | `weather_forecasts` |
| `WeatherAreaMetric` | `weather_area_metrics` |
| `WeatherLatestForecast` | `weather_latest_forecasts` |
| `WeatherIngestionRun` | `weather_ingestion_runs` |

Keep the logical entity/table aliases if desired; correct the physical partition
targets instead of renaming existing data. Revalidate column schemas after
rebinding. A semantic alias is not a substitute for a physical Delta table.

### 2. Ten operational relationships are semantic labels without join backing

These candidate relationships have neither a backing configuration nor a
corresponding physical relationship:

- `asset3dmodels_for_equipment`
- `inspections_for_equipment`, `inspections_for_instruments`,
  `inspections_for_opcua_nodes`
- `maintenance_notifications_for_equipment`,
  `maintenance_notifications_for_instruments`,
  `maintenance_notifications_for_opcua_nodes`
- `workorders_for_equipment`, `workorders_for_instruments`,
  `workorders_for_opcua_nodes`

The five operational entity tables do point at V3's existing SQLDatabase through
a Direct Lake expression. Their presence does not make these joins executable.
Add physical joins only after verifying the actual fields and cardinalities:

- Work orders, inspections, notifications and 3D models have `equipmentId`;
  equipment has `equipment_id`.
- Work orders also have optional `instrumentId` and `opcuaNodeId`.
- Inspections and notifications have optional `opcuaNodeId`, not an
  `instrumentId` field. Use a verified signal identity/route, not an invented FK.
- Spare parts have equipment-type compatibility, not an equipment-ID FK.

Only materialized native instances may supply canvas/tree/scope topology. Do not
render these labels as invented edges or use a SQL/FK topology fallback.

### 3. Preserve the historical telemetry contract

The candidate merges signal semantics into `Instrument`, keyed by `instrument_id`,
and keeps `opcua_node_id` as an alternate stitch field. It exposes Eventhouse
`event_time` and `quality` as scalars while `value` is a time series.

FEATURE instead retains `signal_master`, keyed by `opcua_node_id`, with
`event_time`, `value` and `quality` all time-series properties and an explicit
physical Eventhouse/static stitch. Preserve this known contract. The candidate's
different grain and scalar quality/timestamp are not evidence that historical
BAD-quality samples remain aligned with their values. Alternate-key stitching
must be validated, not assumed unsupported or assumed successful.

Acceptance must cover **any BAD sample today**, including signals whose latest
sample is GOOD, multiple samples and turbines with no open work. A latest-only
answer does not satisfy the three requested BAD-quality workflows.

### 4. Composite identities need a valid physical scalar key

`WeatherLatestObservation` declares `(source_id, location_id)`;
`InstrumentClassification` declares `(system_id, signal_type)`.
Both Fabric's `updateDefinition` parser and the app reject repeated
`keyProperty` declarations. This is an authoring error, not merely an app
compatibility limitation. Do not "fix" it by dropping one natural-key member.

Publish one physically backed scalar identity derived from every natural-key
member. Observations also require `observed_at_utc`; forecast identity includes
both reference and valid times. Keep single-key behavior unchanged and verify
the physical source's complete grain before admitting an entity.

`WeatherLatestForecast.source_item_id` is declared in the candidate; this review
does **not** establish that it is missing from the physical source. Its existence,
uniqueness and alignment with forecast reference/valid times still need checking.
The unqualified `target_id -> WeatherArea.area_id` relationship must also respect
`target_kind`; location targets must not be treated as areas.

The bounded direct SQL-schema probe could not run because the required SQL ODBC
driver was unavailable locally. No driver was installed and no row-uniqueness or
forecast execution claim is made.

## Independent operational-store design: original assessment

FEATURE already has SQLDatabase `hydro-operations-ui`,
`702f470c-ef06-4d0a-bff4-d6238fe42dd2`, independently listed beside AppBackend
`3519b2ef-0a84-4d41-bb6d-3cc2085897aa`. Its current schema and CRUD contract are
[`schema.ts`](../HydroOperationsApp/rayfin/data/schema.ts) and
[`rayfin.ts`](../HydroOperationsApp/src/services/rayfin.ts).

Recommended target:

1. Provision/reuse an explicitly identified **Fabric SQLDatabase** during shared
   data setup, before ontology authoring. Keep its lifecycle outside the app's
   managed database and apply versioned, idempotent schema migrations. Do not seed
   demo rows by default or select a database merely because its name matches.
2. Keep Lakehouse/Eventhouse as analytical stores. Use the read-only
   `hydrolakehouse` connector for analytical reads and a separate writable
   `fabric-sqldatabase` connector for operational CRUD. Installed Rayfin 1.36.2
   documents SQLDatabase read/create/update/delete support; the Lakehouse
   `fabric-sqlanalytics` connector cannot replace SQL writes.
3. Preserve signed-in-user authorization, explicit entity policies, user
   attribution, human-approved work-order submission, deduplication and verified
   readback. Update both SPA and hosted source bridge, not just UI CRUD.
4. Bind ontology SQL representations and the published Data Agent's SQL source to
   the same verified store. Preserve compatible custom SQL sources/content.
   OneLake/graph representations may lag: keep the independent fresh SQL
   open-work check before approving a new work order.
5. Preserve all existing IDs, rows and the original database during any cutover.
   Verify schema, counts, relationships and approved-write behavior before
   switching the active target. Do not dual-write or silently reseed.
6. Route fresh and existing deployments through the canonical local deploy
   app/orchestrator. Maintain Runtime 2.0 for every Spark notebook. Never recreate
   the retired `Hydro_STID_API`; AppBackend/connector GraphQL transport is a
   different, legitimate surface.

The following constraint applied to the original assessment; the separately
implemented and verified FEATURE cutover is recorded above.
The new-store cutover is a broader operation than a non-cutover FEATURE prototype.
It requires an explicit decision about copying existing operational records and
switching the app/agent write target; this review does not perform that operation.

## Project-wide acceptance before any switch

- Preserve the current five core entity identities, source bindings and three
  telemetry time-series properties; publish only after generation-2 readback.
- Keep existing Operations Agent playbook/actions and alert/channel provisioning.
  Validate its selectors against the selected native graph.
- Keep all three BAD-quality workflows and fresh independent SQL coverage; a
  broader ontology does not eliminate these runtime evidence requirements.
- Preserve connector asset/map reads, SQL operational UI independence and the
  no-persistent-analytical-cache policy.
- Materialize eligible entities/relationships through the selected ontology's
  **Manage graph -> Continue -> Materialize** flow. Establish authoritative
  lineage or an operator-verified explicit graph binding; do not infer ownership.
- Validate weather source freshness and forecast grain independently. Adding
  weather entities or publishing Weather does not repair failed ingestion.
- Test compound-key parsing, native schema identity, source failure handling and
  operational read/write readback before a canonical FEATURE deployment.

## Weather agent: correct project, not a missing deployment

Initial live project readbacks found `hydro-weather-agent:1` alongside
`hydro-orchestrator:4` in FEATURE project
`ai-project-hydro-foundry-884a839f3132`, account `ai-account-yz6usfcwfov5q`,
resource group `rg-hydro-foundry-884a839f3132`, subscription
`dac16501-5c35-4c91-aff0-11af8de17f2e`.
The latest deployment uses orchestrator version 10, as recorded above; Weather
remains version 1.

The screenshots match the **older** `foundryfabriciqappsdemo` project: orchestrator
140, Sparky 16, Fixer 21, Sleuth 25 and Gauge 20, without Weather.
The deployed FEATURE SPA selects the new project. Select that project in Foundry,
then open its Agents list. See the
[Foundry deployment guide](foundry-deployment-and-multi-agent-setup.md#finding-weather-in-foundry)
for navigation. Do not publish into the older project merely to change the
screenshots or alter a protected workspace's runtime.

Agent publication and authenticated Weather connector execution were verified.
Fresh provider data remains unavailable; see the latest acceptance table above.

## Preservation verification

After the read-only review, every candidate definition part (48) and current
FEATURE definition part (18) matched the respective saved baseline exactly.
Workspace item ID/type/name inventories also matched: V3 45 items, FEATURE 40.
No notebooks, source refreshes, materialization, SQL DML, agent creation or
deployment ran as part of this assessment.

## October 25 follow-up: Weather source drift and SQL lifecycle

PR #20's current head, `67fd898`, is the second parent of feature merge `ccca826`.
It is integrated into the feature branch; the GitHub PR remains open against
`main`. This is not evidence that the feature merge is missing.

Actual FEATURE definition readback found a separate publication gap:
`Weather_002_fetch_area_weather` still selected the retired
`aurora-1p5-zarr-staging` collection, and Weather_003 also differed from the
feature code. Both existing notebook definitions were updated from the feature
source without replacing Lakehouse/Environment dependencies or unknown parts.
Executable-source and dependency readback passed. Weather_001 and Weather_020
also match the feature executable source. No probe items were created.

The pre-existing pipeline run `4f7f4b17-00f4-43e3-a34f-6a64184a8872`
(07:08:46-07:24:11 UTC) ended with `Aurora STAC search failed with HTTP 401`,
including retry `93c18c89-efe6-4066-8bfa-2ba6832665fb`. Source publication does
not repair a rejected provider credential or prove that an already-started job
executed the newly published source. The 03:20 scheduled run failed separately
with `CapacityNotActive`. No duplicate pipeline was launched, and ingestion
success is not certified. A valid provider credential must be installed securely
in the configured Key Vault secret `mai-weather-api-key` before another run.

The original `hydro-operations-ui` SQL database is explicitly marked in the
Fabric portal as a **child item of a Fabric App**, with a schema-edit warning.
`HydroOperationalStore` remains the independent, active operational source; the
75-record cutover and legacy write boundary are unchanged. The deployer now
checks the verified shared-store publication before requiring legacy SQL
discovery, so a completed cutover no longer imposes that migration prerequisite
on routine deployment. This is not proof that the platform-managed child can
be deleted or that it is used only for authentication. Direct read-only TDS
catalog inspection from this machine timed out; no schema or row was changed.

### SQL lifecycle correction applied to FEATURE

Feature commit `2c6c864` integrates independent SQL provisioning into both NB01
variants and NB11 seeding/publication into the setup DAG. Fresh workspaces use
`RTI_Demo_SQLDatabase_<env_suffix>` in the demo folder. NB11 runs after the
Data Agent, Operations Agent, and Weather setup activities; fresh setup does
not require an AppBackend database or an explicit legacy migration source.
Seed MERGEs insert missing demo records without updating matched records.
The transaction verifies preservation of existing IDs and known operational
fields and minimum populations in all five operational tables before commit.
The app seed action and deployer resolve the saved SQL identity rather than
hardcoding the old display name. Related deployment and architecture docs
were updated.

Fabric rejected an in-place SQL rename with
`SQLDatabaseDisplayNameCannotBeChanged`. Therefore FEATURE retains
`HydroOperationalStore` and item ID
`7972fa8c-e2cd-4c9a-bdd6-053b37293f19`; correcting its existing name requires
an explicitly approved replacement/copy/rebind/retirement migration. No third
database was created and neither existing database was deleted. Folder-list
readback confirms the independent database and its analytics endpoint are
both in `RTI_DEMO_V11` (`92053cc6-8a9d-42b2-ad73-b3cbbb8fabf0`).
The AppBackend-managed child remains retained, not the active operational target.

The two NB01 variants, NB11, and setup orchestrator were published to their
existing FEATURE items with dependency metadata and unknown parts preserved
and executable-source readback verified. Runtime 2.0 NB11 job
`5ca2f136-dd90-4a66-a025-388e93e88302` completed from
09:19:54 to 09:25:38 UTC on October 25 (5 minutes 44 seconds), with
`seed_demo_data=True`, `provision_shared_store=True`, and no legacy copy.
Its successful path includes the transactional preservation/population checks.
The exact count/hash receipt was not independently retrieved; do not present
the earlier 75-record migration receipt as a new before/after measurement.
Post-run readback verified the published Data Agent SQL source and source-backed
v2 ontology. This does not certify SQL/combined-source agent answers.

Validation was limited to 69 focused Python tests, app environment validation
and typecheck, notebook mirror checks, and whitespace checks; all passed.
The canonical app deployment then stopped at SPA discovery because fresh
Microsoft sign-in was required, before Rayfin deployment changes. There is
no new app deployment success receipt: the prior live app remains, and the
updated app source is pushed but not redeployed. Weather provider authentication
and the previously recorded runtime-acceptance limits remain unresolved.
The protected V3 workspace was not modified.

The subsequent continuation compiled the current app successfully
(`npm run build`, Vite 34.60 seconds; local Node 26.12.1). This does not replace
the canonical deployer's Node 24 production build. A bounded, noninteractive
lookup of the existing SPA confirmed the exact Entra failure:
`InteractionRequired / TokenCreatedWithOutdatedPolicies`. Interactive sign-in
cannot be completed unattended. The user-input tool reported the user
unavailable for both sign-in and replacement-migration approval, so no new
SQL database, deletion, or deployment workaround was attempted. The deployment
plan remains validation-blocked; the hosted app is unchanged.

### Authentication recovered and FEATURE app redeployed

The user subsequently returned and requested interactive authentication. The
target-tenant sign-in completed. Initial Microsoft Graph requests still rejected
tokens, including claims-aware attempts; a later direct Graph probe and Azure CLI
lookup both passed. The existing SPA was verified in the correct tenant. No
Conditional Access policy, directory grant, app registration, or vault network
setting was changed. Individual scope/broker changes are not proven to be the
cause of recovery.

Tenant-only sign-in left a placeholder default account with the tenant ID as its
subscription ID. A deployment attempt stopped during Foundry discovery with
`SubscriptionNotFound`, before creating another project. Refreshing subscription
discovery and selecting the verified existing Foundry subscription restored the
intended context. The successful run reused the existing FEATURE project and all
five specialist agents, including `hydro-weather-agent:1`; Hosted coordinator image
`src-247959b77642fffb` was already current and was not republished.

The canonical deployment of feature source `853e7e8` completed on October 25,
2026, from 09:59:55 to 10:09:27 UTC, with both `SUCCESS` and `DEPLOYED_APP_URL`.
The app is published at
`https://brisk-ford-6c2fad844b-swedencentral.webapp.fabricapps.net`.

| Section | Seconds |
| --- | ---: |
| Tenant/workspace/capacity | 14.766 |
| SPA discovery and redirect baseline | 15.359 |
| Configuration and existing-agent verification | 135.391 |
| Rayfin authentication | 1.687 |
| App build and static publishing | 155.844 |
| Redirect configuration and backend runtime reapplication | 109.922 |
| Browser live-auth setup | 72.875 |
| Hosting, endpoint and sign-in readiness | 66.250 |
| **Total** | **572.094 (9m 32s)** |

The deployer reused the verified published SQL source without running NB11 or
demo DML again. Runtime/CORS, DAB/SQL and both connectors were reapplied; all 40
existing SPA redirects and the delegated permission contract were verified.
Required preflights returned 200. GraphQL POST returned 200; unauthenticated token
POST returned 400, and both protected connector POSTs returned 401 as expected.
Those protected responses prove transport readiness, not successful source queries.

Hosting was verified as the identity-matched private sign-in gate, not an
authenticated app shell. A separate browser reload reached the same gate; automated
sign-in-button interaction timed out. No authenticated operational-data, Lakehouse,
agent-answer, work-order-write or alert-delivery acceptance is claimed.

This successful deployment supersedes the earlier app sign-in blocker. SQL folder
placement and immutable existing identity/name remain as recorded above; no
replacement database or deletion was authorized. The AppBackend-managed child
remains retained. Weather provider authentication and the separate runtime
acceptance limits are still unresolved. V3 was not modified.

## October 25: verified semantic repair and native graph diagnosis

This is a fresh comparison of the complete live generation-2 definitions, not a reuse of the
earlier binding-only assessment. V3 `Ontology_Test` was read only. FEATURE's existing ontology
identity, keys, physical source bindings, telemetry stitch, unknown parts, and graph scope were
preserved.

| Semantic surface | V3 reference | FEATURE before | FEATURE verified after |
| --- | ---: | ---: | ---: |
| Entity types | 19 | 18 | 18 |
| Description lines | 199 | 0 | 189 |
| Synonyms | 373 | 0 | 372 |
| Semantic annotations | 71 | 0 | 67 |
| Exported scoped business rules | 0 | 0 | 6 |

All **171 existing FEATURE properties** and all 18 entities now have descriptions. Counts are
not intended to match the reference: FEATURE has different canonical grains and source bindings.
The reference's richer semantics are useful, but it should not replace the working ontology
wholesale. Its combined `Instrument` semantics were adapted into the distinct physical
`instruments` registry and `signal_master` telemetry entity keyed by `opcua_node_id`.
Facility text no longer assumes a weather location is embedded in the facility binding.
Spare-part text no longer incorrectly labels FEATURE's SQL-backed inventory as unbound.

Six explicitly scoped rules cover any-BAD-sample windows, aligned historical telemetry, duplicate
maintenance prevention, evidence-qualified RCA, operator-approved work-order writes, and weather
evidence limitations. They use Microsoft's documented `statement`, `ruleReferencedEntity`,
`propertyScope: specific`, and `ruleReferencedProperty` syntax. These are natural-language
guardrails, not database constraints or runtime-execution certification.

The final semantic-only update was verified by live generation-2 readback at
**2026-10-25 10:50:54 UTC**. Every original part/setting and new description/rule was checked;
the resulting definition is idempotent. Initial TMDL validation rejected metadata child objects
placed before scalar settings; the producer now appends them correctly, with a regression test.
No rejected validation response was treated as a successful deployment.

### Graph scope, eligibility, and loading

A bounded native read returned **201 nodes / 198 edges**, no isolated nodes, and **three
67-node components**, one per facility. Those separate facility trees are legitimate topology.
The existing queryable graph has five node types and four relationship types; its complete
queryable schema was unchanged after semantic/rule publication.

Microsoft documents keys, supported Delta/mirrored sources, binding, and a single backing table
as graph eligibility requirements. The multi-backing static/Eventhouse signal entity should
not be made eligible by removing its working telemetry stitch. Use the selected ontology's
**Manage graph -> select eligible entities/relationships -> Continue -> Materialize** flow;
prefer the four static asset types and three hierarchy relationships for a smaller initial scope.
The portal worker failed to load in this browser session, so no Ineligible tooltip or new scope
selection was claimed as verified, and no portal-owned graph was rewritten through another API.

NB06 previously forced a five-type projection and graph refresh/GQL jobs. That phase is now
read-only authoritative ownership discovery, recording `portal-prerequisite` or
`configured-not-runtime-verified`. It no longer overwrites a portal selection or couples
ontology/required-agent setup to graph refresh. Ontology-backed Data Agent questions do not
universally require materialization; the app's native canvas/tree/scopes do.

Native node/edge query times were **51.344 / 54.891 seconds** in parallel. This locates the measured
delay inside Fabric rather than proving a rendering problem. The app source now loads independent
metadata/schema concurrently and refreshes its ontology contract on manual graph refresh.
Existing identity-keyed in-memory caching/coalescing and failure invalidation remain intact.
No persistent analytical cache, synthetic edge, or GraphQL/STID topology fallback was added.
No reduction in Fabric's cold-query latency is claimed from these source changes.

### Verification and remaining boundaries

- Seven focused Python cases, eleven native-graph cases, app TypeScript checking, and notebook
  mirror compilation/synchronization checks passed; no full suite, build, app redeploy, or Spark
  setup/seed rerun was performed.
- The live FEATURE ontology is repaired. Notebook/app producer changes are committed source,
  not a claim that hosted app code or live notebook definitions were redeployed.
- FEATURE still exposes latest-weather snapshots, not the candidate's three separate long-history
  fact entities. Their physical schemas were inspected, but adding a new physical-key migration
  was not folded into this metadata-only repair. The live weather-location table was empty.
  Provider readiness, historical weather answers, cross-source answers, writes, and delivery
  remain separate runtime acceptance work.
- V3 was not changed. The existing healthy native graph was retained, not rematerialized merely
  to change its node count.

Official references:

- [New ontology TMDL definition, including rule parts and the complete rule example](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition)
- [Ontology graph purpose, optional materialization, and eligibility limitations](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-ontology-graph)
- [Natural-language business rules and linked concepts](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-rules)

## October 25: staged energy, Foundry and final app publication

Source `8c65c37` was published to FEATURE using the canonical deployment engine.
PR #19's map was previously unavailable because the normal deployment disabled
its visibility flag and FEATURE had no energy artifacts. Both prerequisites are
now prepared; same-target app publication preserves the enabled flag.

The local launcher now separates **energy sources**, **Foundry agents**, and
**final app publication**. The full workflow orders Git/core setup, selected
energy setup, Foundry setup, then app publication. The final stage performs
read-only prepared-agent verification rather than provisioning Foundry through
the app. See the [deployment guide](../HydroOperationsApp/DEPLOY.md#independent-local-modules-app-publication-last).

| Measured module | UTC start / finish, October 25 | Seconds |
|---|---|---:|
| Energy source setup and serving readback | 11:10:35 / 11:30:29 | 1193.922 |
| Standalone Foundry setup, launched from the local UI | 11:33:10 / 11:36:39 | 208.812 |
| Final app-only publication and readiness checks | 11:36:51 / 11:46:32 | 581.125 |

Final publication sections:

| Section | Seconds |
|---|---:|
| Tenant/workspace/capacity checks | 16.984 |
| SPA discovery and redirect preservation | 17.391 |
| Configuration, read-only Foundry and energy verification | 109.219 |
| Rayfin authentication | 1.672 |
| Static build/publication, existing backend reused | 168.125 |
| Runtime/SQL/connector reapplication and redirect configuration | 112.500 |
| Browser sign-in setup, permissions and consent | 91.359 |
| Hosting, endpoint and CORS readiness | 63.875 |

- Energy serving readback verified all **12 source layers**, **9 reservoir
  polygons**, and **75 asset-message links**. No core setup or SQL seed rerun was
  needed.
- Foundry reused the existing project and Hosted image `src-247959b77642fffb`.
  QA `2`, RCA `2`, WorkOrder `3`, FabricIQ `2` and Weather `1` were verified.
  No new project or Hosted version was created.
- Final app publication verified prepared agents without republishing them,
  retained all **40** registered SPA redirects, applied both Lakehouse and
  independent operational-SQL connectors, and passed browser-equivalent
  preflights/POST readiness. It emitted `SUCCESS` and `DEPLOYED_APP_URL`.
- App URL: <https://brisk-ford-6c2fad844b-swedencentral.webapp.fabricapps.net>.
  The interactive browser session had expired, and its sign-in pop-up was
  blocked. Authenticated map rendering/navigation and connector data execution
  were **not** accepted; source readback and protected hosting do not prove them.
- Dedicated Map Data Agent chat remains a separate prerequisite. Weather's
  previously observed provider HTTP 401 is not resolved by app publication.
  Agent inference, operational writes and alert delivery are not certified here.
- This split removes cold ingestion and agent provisioning from routine app
  publication; it does not promise a shorter Fabric build. The measured final
  stage was 581.125 seconds versus the earlier 572.094-second combined run,
  with additional energy readback now required.
- V3 was not changed. The healthy native graph was not rematerialized. Earlier
  notebook producer changes were not published by this app-only stage.

## October 25: focused deployment and artifact-placement follow-up

- Simplified the primary [deployment guide](../HydroOperationsApp/DEPLOY.md):
  one local launcher, explicit prerequisites, scoped administrator handoff, core
  setup only when required, independent energy/Foundry preparation, app last.
  Removed obsolete manual Rayfin/Entra/seeding sequences and dated acceptance
  material from the operator checklist; detailed evidence remains in this record.
- The launcher derives repository/branch from its checkout, leaves Key Vault,
  Teams and run-as defaults tenant-neutral, validates required destinations before
  starting Spark, and hides specialist parameters under Advanced. Its browser
  check verified `feat/dibakar`, blank target defaults, collapsed advanced fields,
  the served guide, and no duplicate HTML IDs.
- Foundry setup accepts an exact existing project ARM resource ID and reuses its
  verified same-target subscription. It reads the selected resource directly
  rather than requiring subscription-wide resource enumeration. Explicit
  identity/endpoint conflicts fail without discovery or replacement creation.
  This is an access/simplicity improvement, not a measured cloud speedup.
- Fixed NB11's failed-seed persistence: preserve the original failed status/reason
  instead of indexing a nonexistent receipt and masking the source error.
  Successful receipts and non-seeding rerun behavior remain compatible.
- Moved FEATURE's owned `Hydro_GeoContext_V11` Lakehouse
  `e0a06e85-f7af-4540-ab48-3f7d9eb0c383` into `RTI_DEMO_V11`
  (`92053cc6-8a9d-42b2-ad73-b3cbbb8fabf0`). Readback also verified its SQL endpoint
  `809f70c1-a853-43c1-9e31-6cc49ec27907` in that folder. No recreation,
  ingestion, source-path change or agent rebinding was required. Future energy
  provisioning enforces the owned-item move and verifies folder membership.
- FEATURE's independent SQL `7972fa8c-e2cd-4c9a-bdd6-053b37293f19` was also read
  back in the same folder. Its name remains `HydroOperationalStore`: Fabric
  rejects in-place rename. Approval for a replacement/copy/rebinding migration
  was unavailable, so no duplicate was created and no database was deleted.
- Focused offline deployment/notebook contracts, app typecheck and canonical/Raw
  notebook mirror checks passed after fixing directly coupled stale fixtures.
  Source-preservation tests explicitly reject replacing another SQL source at
  the preferred Data Agent path. No full cloud workflow, cold ingestion,
  agent provisioning, core setup or new app publication ran for this review.
- These source fixes are not live notebook/pipeline publication. V3 remained
  untouched. The earlier authenticated-app, Weather provider HTTP 401,
  dedicated map-chat and agent execution/delivery limits remain unresolved;
  this review does not certify them.

## October 25: canonical SQL naming cutover completed

This supersedes the earlier approval-unavailable/legacy-active statements above.
Source changes `cedb097` and `fe6ded4` completed the non-destructive replacement
and dependent GraphQL source reconciliation in FEATURE only.

- Active operational SQL is now **`RTI_Demo_SQLDatabase_V11`**
  (`9b3dfea4-0934-4122-aaa4-1d7f17a183bd`) in **`RTI_DEMO_V11`**.
  Exact field/ID comparisons and per-table SHA-256 receipts verified all **75**
  records. The former `HydroOperationalStore`
  (`7972fa8c-e2cd-4c9a-bdd6-053b37293f19`) remains a write-blocked archive with
  an explicit replacement marker, not a second active operational store.
  The platform-managed AppBackend child SQL is retained separately; it is not
  the operational source and was not deleted.
- Rebound only the operational expression/source metadata in the existing
  generation-2 ontology. Preserved core bindings, native graph and unknown parts.
  Published both NB01 variants and NB11 with live dependency metadata preserved;
  executed **only NB11**, without reseeding. Its successful run took
  **333.921 seconds** (12:17:49.999527 / 12:23:23.920071 UTC).
  New Data Agent SQL selections passed draft/published readback; the archived
  base tables were deselected and compatible custom content retained.
- Standalone Foundry setup reused the existing project and image
  `src-247959b77642fffb`, activating coordinator **version 11** against the new
  source identity. It completed in **327.891 seconds**
  (12:23:42.781092 / 12:29:10.664296 UTC). No new project was created.
- The first app attempt failed after **429.250 seconds** because Fabric's
  retained GraphQL definition still exposed the archived SQL's five node types.
  This was a SQL API attachment conflict, not native graph materialization.
  The deployer now handles that exact duplicate-node failure by removing only
  the archived managed projections, verifying readback, attaching their unchanged
  projections to the verified replacement and verifying again. It backs up the
  definition and preserves custom objects, mappings, unrelated sources and parts.
  Other failures still propagate.
- The canonical app-only retry completed **12:49:06.249643 /
  13:00:59.928798 UTC**, **713.672 seconds**, emitting `SUCCESS` and
  `DEPLOYED_APP_URL`. This includes the one-time attachment repair; it is not a
  claimed deployment speedup. Both connectors applied successfully, all **40**
  SPA redirects were preserved, and endpoint/CORS/preflight/POST checks passed.

| App publication section | Seconds |
|---|---:|
| Tenant/workspace/capacity checks | 19.750 |
| SPA discovery and redirect preservation | 17.406 |
| Configuration and read-only prepared-service checks | 149.078 |
| Rayfin authentication | 1.391 |
| Static build/publication, existing backend reused | 191.031 |
| Runtime/connectors, SQL attachment repair and redirect configuration | 192.391 |
| Browser sign-in setup, permissions and consent | 81.640 |
| Hosting, endpoint and CORS readiness | 60.985 |

After deployment, actual delegated connector reads against the new SQL returned
**12 work orders, 6 notifications, 30 inspections, 12 spare parts and 15 asset
models**. No writes were performed. The verification client required both
`GraphQLApi.Execute.All` and `Item.Execute.All`; a single-scope diagnostic failed
at token exchange and was not treated as a data-source failure.

The GeoContext Lakehouse and its SQL endpoint remain verified in `RTI_DEMO_V11`.
The local workflow keeps source setup and Foundry preparation separate from
final app publication; this cutover did not rerun core ingestion or rebuild the
native graph. V3 was not changed.

App: <https://brisk-ford-6c2fad844b-swedencentral.webapp.fabricapps.net>.
These checks verify publication and operational connector execution, not
authenticated browser UX, agent inference, approved writes or Teams/email
delivery. The separately recorded Weather provider HTTP 401, dedicated Map Data
Agent prerequisite and expanded graph portal-eligibility limits are not resolved
by this naming cutover.

### October 25: focused Weather follow-up

Read-only verification after the SQL cutover confirmed the assigned capacity
`0f4dac6b-ad68-483d-8027-3d8bf9655d53` is **Active**. The complete Weather PR #20
tip `67fd898e` is an ancestor of `feat/dibakar`, and the live
`Weather_002_fetch_area_weather` executable source matches this checkout.
PR #20 itself remains open against `main`; that is separate from its incorporation
into the feature branch.

The latest Weather pipeline job `69b5c2d9-002e-46e9-be46-59197810aa29`
(11:56:12 / 12:10:54 UTC) failed in that deployed notebook with
**`Aurora STAC search failed with HTTP 401`**. The preceding 11:40 run failed
the same way. The existing credential probe identified an invalid subscription
key using both supported placements. This is a provider-authentication blocker,
not missing PR deployment, paused capacity, SQL connector configuration or an
ontology repair. No new ingestion run, credential change or permission/network
expansion was attempted. The credential owner must verify/replace the configured
secret through an authorized connection before a bounded pipeline rerun.
