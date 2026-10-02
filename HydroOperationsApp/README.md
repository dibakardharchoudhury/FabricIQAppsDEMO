# Hydro Operations Fabric App

A React + Leaflet + [Rayfin](https://www.npmjs.com/package/@microsoft/rayfin-cli) single‑page app
that runs **inside Microsoft Fabric** and gives a hydropower operations team one screen composing
**three independent data stores** — plus an in‑browser 3D digital‑twin viewer.

> **To deploy, follow [DEPLOY.md](DEPLOY.md).** This README covers the architecture and data model.
> Moving to a different tenant, workspace, or capacity region? See
> [DEPLOY.md → Redeploying to a different tenant, workspace, or region](DEPLOY.md#redeploying-to-a-different-tenant-workspace-or-region)
> (the repository orchestrator backs up target-specific state, resolves the tenant SPA, provisions
> the selected workspace, and checks Fabric App Items preview feature/region gating).
>
> **Entra prerequisite:** runtime sign-in uses a single-tenant SPA. **`Hydro Operations Fabric
> Client`** is the deployer's configurable default discovery name, not an Entra requirement; the
> tenant-specific client ID is resolved dynamically. When the operator cannot create or identify
> the SPA, deployment stops before changing Rayfin state. Use the role split and portal fallback in
> [DEPLOY.md → No admin rights?](DEPLOY.md#no-admin-rights-hand-this-to-your-entra-admin): an
> Application Administrator / Cloud Application Administrator configures the SPA and grants
> tenant-wide consent. That consent is optional where the tenant allows user consent, since every
> requested scope is user-consentable.

| Store | Owns | Accessed via |
|---|---|---|
| **Lakehouse (STID)** | Engineering master data — facilities, systems, equipment, instruments | Workspace **GraphQL API** item (created + auto‑bound by `RTI_011`, discovered at runtime) |
| **Eventhouse (telemetry)** | `OPCUAEvents(event_time, opcua_node_id, value, quality)` | KQL query |
| **Rayfin SQL (operational)** | Work orders, notifications, inspections, spare parts, 3D models | Rayfin `data` client (Data API Builder) |

The stores are **never merged server‑side** — the app queries each independently and joins in the
browser by `equipmentId` / `instrumentId` / `opcuaNodeId`, so every panel shows its source. Demo
data is synthetic but each record lives where it would in production (no reference or telemetry rows
are copied into Rayfin SQL).

Lakehouse STID and Eventhouse telemetry are live-only browser state. The app does not persist either
source in local storage, removes legacy STID/telemetry browser caches on startup, and clears the
corresponding UI data when a refresh fails or the source is unavailable. Rayfin SQL remains
independent and can continue serving operational records while Fabric analytical sources are rebuilt.
Within the active SPA, the shared data provider and memoized projections reuse STID and telemetry
across the map, Knowledge Graph, and digital twin. Weather uses a five-minute, source-keyed in-memory
cache with concurrent-request coalescing, forced-refresh bypass, and failure invalidation. Ontology
definitions/native graph results use their own bounded, source-keyed in-memory cache. None of these
analytical caches survive a full page reload.

On a full reload, STID, Weather, and latest telemetry use the exact GraphQL URL, Eventhouse query
URI, and KQL database injected by the verified deployment before attempting slower workspace
discovery. The live GraphQL/KQL requests still determine whether those sources exist and are
queryable: missing, deleted, or failed sources clear the corresponding UI data rather than displaying
a persisted snapshot.

The initial HTML stays small and references browser-compressible, cacheable application JavaScript
and CSS. Normal navigation pages are available without page-level lazy-loading delays. The
interactive Digital Twin model remains lazy-loaded when a selected asset supports it, then renders
automatically without an additional operator action. The bootstrap uses local system fonts and does
not wait on a third-party font stylesheet.

The **Knowledge Graph** visualizes this composition as a scoped property graph. Its performant
Cytoscape 2D analysis view provides hierarchy, force-directed, and concentric layouts without a
WebGL dependency. It defaults to the selected turbine and synchronizes that selection with Overview,
Real-Time Telemetry, Digital Twin, and Maintenance. It requires a verified
**generation-2 Ontology** and an explicitly bound,
ontology-managed native GraphModel. Native entities and relationships are the topology authority;
Eventhouse KQL readings and Rayfin SQL records enrich actual native entities as external context.
The explicit graph binding lets startup read the exact Ontology directly—without enumerating the
workspace—so verified Ontology-bound STID entities render progressively while the authoritative
native GraphModel query completes in the background.
No legacy Ontology, guessed GraphModel association, or STID/FK-fabricated graph is queried.
When the native graph is loaded, the toolbar exports its verified Ontology schema and materialized
instances as either **OWL 2 in Turtle** or **RDF 1.1 in Turtle**. Compatibility-mode and operational
SQL/KQL overlays are intentionally excluded so an export never claims synthesized topology is
governed Ontology data.
See the canonical
[`Knowledge Graph design`](../docs/knowledge-graph.md) for implementation details, operational
scenarios, historical screenshots, freshness behavior, and native graph prerequisites.

Individual signal colours in the graph, 3D twin, and map reflect the reading's quality.
Open work orders remain visible and contribute to asset-level health, but do not turn a GOOD
reading red. Freshness is separate and uses the event timestamp, not the last successful fetch.

### Ontology v2 only

The app requires **v2 TMDL** definitions over the Fabric REST `/v1` endpoints (REST API version
is not Ontology generation). The live item's read-only `properties.generation` must be numeric `2`.
Generation `1`, missing/unknown generation, legacy JSON, and mixed definitions are rejected.
Replace a legacy Ontology using the project's v2 setup workflow, update dependent agents to its
new identity, configure the desired Ontology name when ambiguous, and refresh discovery.
The browser's ontology reader does not migrate items or write ontology definitions. Model `ref`
statements are not required for parsing; fresh authoring still supplies the default namespace and
its model reference, as required by Fabric creation validation.
Definition reads handle synchronous responses and long-running operations, preserving operation
URL query parameters when fetching the result and surfacing request/poll/result failures.

- V2 `entities/{name}.tmdl` and `entities/{namespace}#{name}.tmdl` supply entity identity,
  scalar `keyProperty`, property types, optional lineage tags, and backing-table metadata.
  Quoted names, documentation comments, Unicode, tabs/spaces, and CRLF are supported.
  Namespaces stay distinct; display names and namespace are derived from the qualified name.
- **Only `entityRelationships.tmdl` defines semantic relationship types.** `relationships.tmdl`
  resolves referenced single-column physical joins, never additional semantic edge types.
  Progressive loading uses only declared semantic relationships and their supported backing-table
  keys over the existing STID rows; it does not turn arbitrary physical FK relationships into ontology edges.
  Native graph results take precedence when available.
- Primitive, `Any`, `TimeSeries<T>`, and single-line `complexDataType` JSON metadata are
  retained, including `additionalBackingTable` references for Eventhouse time-series bindings.
  Complex values and recursive `valueBackingConfiguration` metadata are **not evaluated**.
  Resource links, rules, metrics, and junction-table edges are not executed/rendered; the UI
  reports these limitations. Composite keys, multiline expressions, unknown entity/property
  constructs, invalid payloads, unresolved endpoints, and mixed v1/v2 definitions produce errors
  rather than a silently empty contract. The parser is bounded, not a general TOM engine.
- A v2 materialized GraphModel is required for **native GQL results**, not for displaying already
  available Ontology-bound STID entities with KQL/SQL enrichment.
  On a fresh Ontology, `RTI_006` follows the authoritative relation to its attached service-owned
  GraphModel and authors the complete static five-entity/four-relationship projection. It verifies
  persisted readback, automatic refresh, queryable schema, and the Operations Agent GQL query.
  Missing authoritative lineage is a setup error; use **Manage graph → select eligible
  entities/relationships → Continue → Materialize** only as the explicit operator fallback.
  Configure the `.env` binding with single-quoted JSON:
  `RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING='{"workspaceId":"<guid>","ontologyId":"<guid>","graphModelId":"<guid>"}'`.
  Retain the outer single quotes when adding alias maps: dotenv truncates an unquoted value at
  `#`, including inside JSON double quotes, so exact ontology names containing `#` require quoting.
  Optional `nodeTypes`/`edgeTypes` maps resolve queryable graph aliases to exact ontology type
  names or IDs when projected labels differ; no namespace-label delimiter is assumed.
  Public REST metadata does not expose ownership: names, sole-graph discovery, and structural
  similarity are not proof. The explicit operator binding records the association established in
  the portal; live metadata/contract/type/endpoint checks establish consistency.
  Graph sources require keys and Delta/mirrored bindings. Fabric does not document an
  ontology-owned materialization API. `RTI_006` uses the attached child's public GraphModel
  definition API and automatic refresh; static properties from all five Delta-backed entities enter
  the graph, while `event_time`, `value`, and `quality` remain Eventhouse-bound.
  Native GQL reads use GET `getQueryableGraphType?beta=true` and POST `executeQuery?beta=true`
  on the configured `graphModels/{id}`. Opaque string `result.nextPage` continuations are followed.
  Query errors, warnings, truncation, malformed/dangling results, or exceeding 2,000 nodes/4,000 edges
  fail explicitly. Malformed native results are never reinterpreted as valid native topology.
- Ontology definitions and native results are cached and concurrent requests are coalesced.
  The page can show the existing Ontology-bound compatibility view while native queries run; a
  native failure is reported in the source-state tooltip and console without removing available
  bound entities. Definition errors still prevent unverified governed topology. Workspace refreshes
  restart obsolete reads against current discovery rather than surfacing cache-invalidation errors.
  Manual refresh updates STID and the ontology contract before querying the native graph.
  The 30-second native poll does not repeatedly download the TMDL definition.

These read capabilities do **not** imply Data Agent semantic runtime or Operations Agent playbook
execution succeeds. The latest live attempt accepted source publication and stopped-agent
configuration, but the real ontology MCP question failed inside `analyze_ontology` with an
unsupported API version despite `isError: false`. A SQL-only question correctly returned 12 work
orders; it does not validate ontology reasoning or resolve the external playbook-runtime issue.
The app does not fabricate success for either service.
Guided setup marks only SQL/GraphQL and app data connection steps complete. It does not read
notebook capability statuses or treat a completed notebook as agent readiness: review
`data_agent_deployment_status`/`data_agent_deployment_reason` and
`ops_agent_deployment_status`/`ops_agent_deployment_reason` in the shared configuration.
A missing published Data Agent raises an actionable error. Before every MCP invocation the app
reads the candidate's real `getDefinition` response and verifies **published** ontology datasource
`artifactId` and `workspaceId` against the selected, verified generation-2 Ontology. Draft sources,
agent names, and notebook completion are insufficient. A different/legacy ontology source, an
unreadable or malformed definition, or no matching published source blocks invocation explicitly.
Invocation uses Fabric's published Data Agent MCP endpoint over the documented Streamable HTTP
transport. The app preserves a bounded conversation transcript for follow-up meaning, while every
question explicitly resets data scope to all facilities and assets. It never injects the selected
facility/asset, and scope filters from prior turns do not carry into a later question.
Follow-up questions reuse the authenticated MCP connection and discovered tool while the endpoint
and access token remain unchanged. Workspace refreshes, token/endpoint changes, or MCP failures
invalidate that session; published generation-2 source verification still runs before every call.
Verification uses the actual `Files/Config/published/{source}/datasource.json` parts and the
`type: ontology`, `artifactId`, and `workspaceId` fields used by the notebook publishers.
Matching published identity plus the selected item's authoritative live generation `2` proves
**configured source identity only**, not runtime readiness. It permits the real MCP attempt;
backend/product failures propagate unchanged, and onboarding may still be unavailable.
The public datasource schema's enum omission is not a permanent execution gate. No name/latest-version
heuristic or generation-only check bypasses source verification. Reading the published definition
requires appropriate Data Agent read permissions; read failures remain errors. The app does not
invoke or certify an Operations Agent playbook.

Reference: [Microsoft Ontology (new) definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition).
Regression coverage: `npm run test:knowledge-graph` (real-shaped TMDL, legacy rejection, caches,
and LRO responses), plus `node --import tsx --test scripts/artifact-discovery.test.mjs`
(published source verification and honest setup readiness).

## Architecture

```
              ┌────────────────────────┐
              │  Hydro Operations SPA  │  (React + Leaflet, hosted by Rayfin in Fabric)
              └──────┬─────────┬───────┬┘
      TMDL/GraphQL │   KQL   │       │  Rayfin data client
                     ▼         ▼       ▼
      ┌────────────────┐ ┌──────────┐ ┌───────────────────────────┐
      │Ontology v2 +   │ │Eventhouse│ │  Rayfin SQL (operational) │
      │Lakehouse rows  │ │OPCUAEvents│ │  WorkOrders, Inspections, │
      └────────────────┘ └──────────┘ │  SpareParts, Asset3DModels│
        built by RTI_001…010          │  MaintenanceNotifications │
        (see root README)             └───────────────────────────┘
```

- **Lakehouse + Eventhouse** are produced by the RTI notebooks / `Pipe_Setup` — see the [root README](../README.md).
- **Rayfin SQL** schema and seed are owned by this app (below).
- **Agent provisioning is always attempted; there are no agent mode or skip flags.** RTI_010
  retains both Operations Agents, their separate playbooks/actions, Teams, and
  `Pipe_SendEmailAlert` provisioning against the verified v2 ontology. Real failures propagate
  through RTI_009/010/011 and setup. Review deployment status/reasons and validate playbook execution
  and alert delivery; an Outlook connection, item creation, or completed job alone does not prove
  runtime readiness. The latest live NB10 attempt retained the full configuration after restoring
  the action enum from `DataPipeline` to the original `Pipeline`. Instructions, ontology source,
  connected email action, and BAD/UNCERTAIN rules were visible in the actual Operations Agent UI.
  Activation, monitoring, action execution, and Teams/email sends remain unverified.
  NB09 records `data_agent_publication_status=published` independently. Only a successful bounded
  facility-record smoke check sets `data_agent_deployment_status=ready` and
  `data_agent_runtime_status=verified`; execution provenance is not attested, and this does not
  certify SQL/combined-source answers or the whole agent. NB10 remains `configured`/stopped.
  NB11 always attempts to extend the live Data Agent with SQL, preserves existing sources and
  custom configuration, and requires exact draft/published SQL-source readback; it does not treat
  NB09 runtime evidence as proof of SQL or combined-source execution. Failed or inconclusive NB09
  checks fail setup except for the exact temporary unsupported-Ontology-v2 product response.
  That response is persisted as `known_product_limitation`, while publication remains `published`.
  Both draft and published configurations retained all five SQL tables, all five ontology
  elements, and custom instructions/selections; a subsequent SQL-only MCP query still returned
  exactly `{"workOrderCount":12}`. This validates failure handling and preservation, not healthy
  ontology runtime. NB11 and full setup were not executed in that bounded validation.
  See the root mode/status contract and the
  [latest V3 acceptance record](../docs/knowledge-graph.md#latest-v3-acceptance-september-30-2026).

The latest postdeployment production-parser/model check used the new native graph, actual KQL
readings for 90 signals, and a fresh SQL export, producing 174 nodes / 171 edges with empty STID
topology inputs. The complete five-entity projection still fails on all three TimeSeries properties;
the selected four-entity projection supplies 111 native nodes / 108 edges. Hosting checks succeeded
at the same `icy-twist` URL with 32 redirects preserved, but the protected gate/authentication popup
prevented interactive app acceptance. These are API/model/configuration results, not full UI E2E.

## Rayfin SQL data model

Defined in [`rayfin/data/schema.ts`](rayfin/data/schema.ts) (`@microsoft/rayfin-core` decorators;
table names are PascalCase‑pluralized). These five entities are the exact set seeded by `RTI_011`.

| Entity → Table | Purpose | Joins by |
|---|---|---|
| `WorkOrder` → `dbo.WorkOrders` | Maintenance work orders | `equipmentId`, `instrumentId`, `opcuaNodeId` |
| `MaintenanceNotification` → `dbo.MaintenanceNotifications` | Operational alerts | `equipmentId`, `opcuaNodeId` |
| `Inspection` → `dbo.Inspections` | Condition inspections (VISUAL/THERMOGRAPHIC/VIBRATION/LUBRICATION) | `equipmentId`, `opcuaNodeId` |
| `SparePart` → `dbo.SpareParts` | Inventory with reorder levels (`partNumber` unique, max 255) | `equipmentType` |
| `Asset3DModel` → `dbo.Asset3DModels` | Digital‑twin GLB registry | `equipmentId` |

Every `equipmentId` (`EQUIP_RTI_T###`) and `opcuaNodeId` (`ns=2;s=T###.<signal>`) resolves to a real
STID Lakehouse row, so cross‑store joins always land.

## Demo data

- **STID master data** (3 facilities / 15 turbines / 90 instruments) is seeded into the Lakehouse by
  `RTI_001` when `Pipe_Setup` runs.
- **Operational data** (12 work orders, 6 notifications, 30 inspections, 12 spare parts, 15 3D models)
  is upserted into Rayfin SQL by `RTI_011` — via the app's **Seed & provision** button. Its embedded
  `MERGE` mirrors [`sql/seed-operational-data.sql`](sql/seed-operational-data.sql); the client
  fallback seed lives in [`src/services/seedData.ts`](src/services/seedData.ts).

## Local development

All commands run from `HydroOperationsApp/` on **Node 24**. On a machine whose default Node differs,
prefix any command with `npx -y -p node@24 -c "…"`.

```powershell
npm install       # also installs the pinned Rayfin CLI locally
npm run typecheck # tsc --noEmit
npm run lint      # eslint
npm run test:knowledge-graph # v2 contract, projection, discovery, cache and LRO regressions
npm run validate-env # required before a build
npm run build     # production build (rayfin env auto‑injected via prebuild)
npm run dev       # dev server
```

Without Fabric environment values the app shows explicit disconnected states — it never fabricates data.
Published Data Agent source/invocation guards have separate coverage:
`node --import tsx --test scripts/artifact-discovery.test.mjs`.

## Project layout

```
HydroOperationsApp/
├── rayfin/
│   ├── rayfin.yml           # Rayfin app definition (db + auth + static hosting + data)
│   ├── data/schema.ts       # SQL entity definitions (source of truth for tables)
│   └── .env.example         # Fabric/Entra configuration template
├── sql/
│   ├── seed-operational-data.sql  # Idempotent MERGE seed (sqlcmd/SSMS path)
│   └── validate-seed.sql          # Post‑seed verification
└── src/
    ├── App.tsx                     # Composed operations view + facility selector + Seed button
    ├── components/FacilityMap.tsx  # Multi‑facility Leaflet map
    ├── components/AssetModelViewer.tsx # Inline GLB digital‑twin viewer
    └── services/
        ├── fabric.ts               # Native graph + GraphQL (other pages) + KQL + Data Agent
        ├── ontologyGraphQuery.ts   # Explicit graph binding, native GQL, type validation
        ├── rayfin.ts               # Rayfin data client + list/create/update/delete + self‑seeder
        └── seedData.ts             # Typed operational seed arrays
```

  Pages shared by the original and redesigned app layouts, including Knowledge Graph, Telemetry,
  Digital Twin, and Maintenance, live under
  `src/ui-shared/`. `src/ui-shared/knowledgeGraphModel.ts` enriches native graph topology and
  `src/ui-shared/pages/KnowledgeGraphPage.tsx` owns scope, filtering, shared asset selection, and the
  entity inspector.
  Layout names such as V1/V2 do not enable Ontology v1; both layouts use the same v2-only services.
