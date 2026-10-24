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
deployment verification is recorded separately below when completed.

## FEATURE implementation

- Preserve the original five core entities, bindings and unknown definition parts.
- Add five operational entities and eight weather/classification entities with
  actual physical source names and 13 physically backed semantic relationships.
- Support ordered compound scalar keys in the application parser and RDF export.
- Provision `HydroOperationalStore` during either NB01 setup variant; NB11 handles
  the explicit, verified legacy-record cutover for an existing workspace.
- Route operational reads and approved writes through the delegated
  `fabric-sqldatabase` connector. Keep the original database and every original row.
- Preserve compatible custom Data Agent SQL content, deselect only the five
  archived base tables, and verify both draft and published definitions.
- Keep native graph materialization separate; the original core projection is not
  replaced or reconstructed from SQL relationships.

The parser compatibility finding below is the **pre-change** result. The updated
parser accepts valid compound keys; the physical-source and relationship findings
still prevent adopting the candidate unchanged.

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

### 4. Composite identities need real application support

`WeatherLatestObservation` declares `(source_id, location_id)`;
`InstrumentClassification` declares `(system_id, signal_type)`.
The app currently treats `keyProperty` as a singleton and rejects the first
compound entity. Do not "fix" this by dropping one key member.

Support and test ordered, scalar, unique key-property lists throughout contract
parsing and consuming graph/enrichment paths before admitting those entities.
Keep current single-key behavior unchanged. Separately verify historical weather
record keys against their stated source/variable/location/time grains.

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
