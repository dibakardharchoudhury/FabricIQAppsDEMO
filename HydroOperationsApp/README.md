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

The **Knowledge Graph** visualizes this composition as a scoped Cytoscape property graph. It defaults
to the selected turbine and synchronizes that selection with Overview, Real-Time Telemetry, Digital
Twin, and Maintenance. It requires a verified **generation-2 Ontology** and projects its supported
semantic relationships onto Lakehouse entities in an explicitly labeled compatibility view.
Signals are enriched with current Eventhouse KQL readings, and Rayfin SQL records remain external
operational overlays. No legacy Ontology or implicitly associated Graph Model is queried.
See the canonical
[`Knowledge Graph design`](../docs/knowledge-graph.md) for implementation details, operational
scenarios, screenshots, freshness behavior, and direct Graph Model architecture.

### Ontology v2 only

The app requires **v2 TMDL** definitions over the Fabric REST `/v1` endpoints (REST API version
is not Ontology generation). The live item's read-only `properties.generation` must be numeric `2`.
Generation `1`, missing/unknown generation, legacy JSON, and mixed definitions are rejected.
Replace a legacy Ontology using the project's v2 setup workflow, update dependent agents to its
new identity, configure the desired Ontology name when ambiguous, and refresh discovery.
The browser does not migrate or write Fabric items. Model `ref` statements are not required for parsing.
Definition reads handle synchronous responses and long-running operations, preserving operation
URL query parameters when fetching the result and surfacing request/poll/result failures.

- V2 `entities/{name}.tmdl` and `entities/{namespace}#{name}.tmdl` supply entity identity,
  scalar `keyProperty`, property types, optional lineage tags, and backing-table metadata.
  Quoted names, documentation comments, Unicode, tabs/spaces, and CRLF are supported.
  Namespaces stay distinct; display names and namespace are derived from the qualified name.
- **Only `entityRelationships.tmdl` defines semantic edges.** `relationships.tmdl` is used
  solely to resolve the referenced single-column physical joins, never to invent semantic edges.
- Primitive, `Any`, `TimeSeries<T>`, and single-line `complexDataType` JSON metadata are
  retained, including `additionalBackingTable` references for Eventhouse time-series bindings.
  Complex values and recursive `valueBackingConfiguration` metadata are **not evaluated**.
  Resource links, rules, metrics, and junction-table edges are not executed/rendered; the UI
  reports these limitations. Composite keys, multiline expressions, unknown entity/property
  constructs, invalid payloads, unresolved endpoints, and mixed v1/v2 definitions produce errors
  rather than a silently empty contract. The parser is bounded, not a general TOM engine.
- A v2 materialized Graph Model is **optional**. The supported public item/definition API does
  not expose a linked Graph Model identity. Until it does, the app never selects a workspace
  graph by name, labels, or merely because it is the only graph. Legacy graph discovery and execution
  have been removed.
  The UI explicitly reports v2 graph unavailability and uses the Lakehouse compatibility view:
  Hydro facilities/systems/equipment/instruments, unambiguous backing tables, and validated
  direct FK relationships. This is not a full v2 graph query engine. Eventhouse readings and
  Rayfin operational overlays retain their existing behavior.
- Failed refreshes clear semantic caches and graph timestamps. Definition errors hide governed
  topology and offer retry; graph errors are visible above a labeled compatibility view.
  Ontology contracts are not restored from unscoped browser storage. Manual refresh rediscovers
  the workspace so a failed initial discovery or changed generation can recover.

These read capabilities do **not** imply Data Agent v2 source onboarding is available or that
Operations Agent playbook generation succeeds (the reported issue1970 remains external).
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
- **Operations Agent email alerts** are not verified by this app. The v2 playbook capability may
  remain product-blocked; review `ops_agent_deployment_status` and its reason before attempting
  alerts. An Outlook connection alone does not establish v2 agent readiness.

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
npm run build     # production build (rayfin env auto‑injected via prebuild)
npm run dev       # dev server
```

Without Fabric environment values the app shows explicit disconnected states — it never fabricates data.

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
        ├── fabric.ts               # GraphQL (STID) + KQL (telemetry) + Data Agent
        ├── rayfin.ts               # Rayfin data client + list/create/update/delete + self‑seeder
        └── seedData.ts             # Typed operational seed arrays
```

  Shared V1/V2 pages, including Knowledge Graph, Telemetry, Digital Twin, and Maintenance, live under
  `src/ui-shared/`. `src/ui-shared/knowledgeGraphModel.ts` builds the current application graph and
  `src/ui-shared/pages/KnowledgeGraphPage.tsx` owns scope, filtering, shared asset selection, and the
  entity inspector.
