# Ontology_Test review and operational-store recommendation

Reviewed on October 24, 2026. This is an assessment, not a migration or runtime
acceptance certificate. `ws-vteam-demoV3` was accessed read-only.

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
- Provision `HydroOperationalStore` during either NB01 setup variant; NB11 handles
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
verify the published source bindings and reuse the immutable image; another warm
run was deliberately not performed merely to obtain a timing number. The earlier
351.375-second warm measurement is documented in
[DEPLOY.md](../HydroOperationsApp/DEPLOY.md#speed-changes-and-measurements).

### Acceptance limits: do not report everything as runtime-certified

1. Additional graph entities/relationships still need the selected ontology's
   **Manage graph -> select eligible entities/relationships -> Continue ->
   Materialize** portal flow. No public materialization API was invented.
   Before the source-metadata repair below, the portal marked all 18 entities
   **Ineligible**, with **Unsupported Datasource** on the inspected core entity.
   Eligibility after repair has not yet been verified; do not describe this as
   merely a pending materialization click.
2. Hosting verification reached Fabric's protected sign-in gate, not an
   authenticated SPA session. A read-only operational connector probe using the
   existing Azure CLI token was rejected by the brokered token endpoint with
   **`Delegated scope 'Item.Execute.All' is required`**. The SPA's own delegated
   permission/consent configuration passed, but that does not add the permission
   to Azure CLI's separate client identity. A named-scope acquisition attempt was
   stopped when it did not complete; no CLI consent grants were changed and no
   authentication bypass was used. Authenticated UI/connector acceptance remains
   pending normal user sign-in.
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

This repairs source authoring, **not a verified graph-materialization result**.
Afterward the shared Fabric portal remained in its loading shell; opening a new
portal page encountered an aborted normal Microsoft sign-in navigation. The
hosted app still reported **Please allow pop-ups and try again** after normal
sign-in clicks. No tokens were injected, grants changed, graph APIs invented, or
production work orders created. Post-repair graph eligibility/materialization,
authenticated connector/UI reads and live agent answers remain acceptance gates.
The completed full deployment above was not repeated merely to test this
metadata-only repair; its helper was exercised directly against the live API.

## Evidence and compatibility

| Check | V3 candidate | Current FEATURE |
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

## Independent operational-store design

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

Live project readbacks found `hydro-weather-agent:1` alongside
`hydro-orchestrator:4` in FEATURE project
`ai-project-hydro-foundry-884a839f3132`, account `ai-account-yz6usfcwfov5q`,
resource group `rg-hydro-foundry-884a839f3132`, subscription
`dac16501-5c35-4c91-aff0-11af8de17f2e`.

The screenshots match the **older** `foundryfabriciqappsdemo` project: orchestrator
140, Sparky 16, Fixer 21, Sleuth 25 and Gauge 20, without Weather.
The deployed FEATURE SPA selects the new project. Select that project in Foundry,
then open its Agents list. See the
[Foundry deployment guide](foundry-deployment-and-multi-agent-setup.md#finding-weather-in-foundry)
for navigation. Do not publish into the older project merely to change the
screenshots or alter a protected workspace's runtime.

Agent publication was verified; Weather inference and fresh provider data are
separate acceptance checks, not certified by this review.

## Preservation verification

After the read-only review, every candidate definition part (48) and current
FEATURE definition part (18) matched the respective saved baseline exactly.
Workspace item ID/type/name inventories also matched: V3 45 items, FEATURE 40.
No notebooks, source refreshes, materialization, SQL DML, agent creation or
deployment ran as part of this assessment.
