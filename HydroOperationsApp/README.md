# Hydro Operations Fabric App

A React + Leaflet/MapLibre + [Rayfin](https://www.npmjs.com/package/@microsoft/rayfin-cli) single‑page app
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
| **GeoContext Lakehouse (optional)** | Imported public energy features and source-health snapshots | Bounded Eventhouse queries over `HydroGeoFeatures` / `HydroGeoStatus` Delta external tables |

The stores are **never merged server‑side** — the app queries each independently and joins in the
browser by `equipmentId` / `instrumentId` / `opcuaNodeId`, so every panel shows its source. Demo
data is synthetic but each record lives where it would in production (no reference or telemetry rows
are copied into Rayfin SQL).

## Operations Map

The separate **Map** tab in both UI shells uses a lazy-loaded MapLibre renderer.
It is distinct from the Overview facility map and shows **real imported energy
data**, not simulated STID coordinates. Enable its Fabric provisioning with
`enable_energy_map: true` in the local feature-bootstrap config; deploy through
the existing orchestrator in [DEPLOY.md](DEPLOY.md).

`04_Pipe_EnergyMap` runs `Geo_001_ingest_energy_context` against a separate
`Hydro_GeoContext_<suffix>` Lakehouse. Layers cover NVE's six public grid datasets,
hydropower reference records, reservoir **area** statistics, Statnett country
balance/interconnector flows/frequency, and Nord Pool UMM. UMM importance is
explicitly labeled rule-based application enrichment, not an official market or
grid-safety rating. Aviation providers are not included.

**Reservoir overlay:** `geo_reservoir_areas` stores NVE NO1-NO5 price-area
polygons and country backgrounds for Norway, Sweden, Finland and Denmark.
Country coverage includes provider-supplied offshore territories. Geometry
comes from NVE Nettomraader and pinned Natural Earth 1:10m data, with provenance
and raw snapshots retained. Non-Norwegian reservoir figures are unavailable,
not zero; gray polygons are explicitly no-data. Norway's national statistic is
an aggregate, not a measurement for every island or reservoir.

**Grid frequency** is a separate tile above the map, showing the latest imported
Statnett sample and its observation age. It is not a map marker or a live feed.

**UMM coverage:** the last 30 publication days (at most 10,000 reconciled revisions),
not every older, still-active outage. A changed/incomplete upstream page fails the
import instead of silently truncating coverage. Latest revisions retain all affected
areas; cancellations and unlocated notices remain in Fabric.
The public area-directory endpoint returned 403, so only area names and EIC/name
pairs explicitly present in provider messages are resolved.

Market messages are displayed **only for the selected asset**, through
`geo_market_asset_links` / `HydroGeoMarketAssetLinks`. Automatic links require
an unambiguous normalized unit-name and owner/publisher match; a shared price
area, proximity or an area EIC is not an asset identity. Matching evidence is
shown, ambiguous notices stay unlinked, and manual corrections are preserved.
Reads join both message ID and revision, so an old link is not silently assigned
to a revised notice. There are no global UMM dots or unrelated-message lists.

**Importance `rules-v1`:** direct Norwegian relevance +10; explicitly unplanned
+25; largest reported single-unit interval unavailable capacity >0/+5,
>=100 MW/+20, >=1000 MW/+35; longest continuous interval >=4 h/+5, >=24 h/+15.
High is >=65, medium >=35, otherwise low. Missing evidence or inactive notices
are unranked. Overlapping units/intervals are not summed into a fictitious system
impact, and capacities are not extracted from prose.

The browser queries only selected layers in the current viewport, with
zoom thresholds for dense distribution/mast data and a visible 4,000-feature
limit. It never downloads the full national network at startup. Layer filters sit
in a responsive panel above the map and are compact by default. Expand a filter's
**Details** to see its provider, zoom guidance, import age, errors and unmapped
counts. Unknown or failed data is not replaced with invented features.

Plant marker area scales monotonically against the global installed-capacity
maximum. Transformer capacity is not supplied by the imported source: those
markers are uniform and explicitly capacity-unknown, not sized by voltage.
Bulky source/GIS properties are fetched only for a selected feature and appear
as collapsed sections at the end of its details.

The MapLibre renderer retains separate versioned sources per layer, so changing
an owner/price-area filter does not re-index unchanged national line or area
geometry. Raw provider objects are omitted from viewport responses, offscreen
updates are deferred, and framebuffer allocation is capped for Fabric's iframe.
Context-loss recovery and a renderer-only retry preserve data/filter state.

The **Layers** button minimizes the entire layer box; **Properties** opens a
separate, independently collapsible filter group. Panel visibility is remembered
in the browser, and minimizing a group does not clear its selections.

| Property group | Filters |
| --- | --- |
| Hydropower plants | Main owner (multi-select), installed capacity (MW range), in operation (All/True/False), price area (multi-select), gross head (m range), plant status |
| Transformer substations | Owner (multi-select), source layer (multi-select), voltage (kV range), network level (multi-select) |

Dropdown values and numeric maxima come from the **complete imported plant and
transformer snapshots**, not just the current viewport. Numeric controls span
zero to the available maximum and let users set minimum and maximum values.
Property predicates run in KQL **before** the viewport feature cap. Selections
within a dropdown are alternatives; different properties combine. Plant filters
affect only plants, transformer filters affect only transformers, and other
selected layers remain visible as context.

Each property group includes a **Show on map** checkbox; transformer markers still
require zoom level 8 or closer. NVE source-layer and network-level values are shown
as source codes. Unrestricted filters include missing values; narrowed numeric
ranges exclude missing measurements, and **Not provided** selects missing
categorical values. Reset buttons restore unrestricted filtering. The source
snapshots and the original synthetic-data stores are not modified by filtering.

**Reload map** rereads Fabric snapshots; **Import latest data** starts the cloud
ingestion pipeline. Initial provisioning is on-demand: no new recurring schedule
is enabled automatically. Stale snapshots remain labeled as stale. Provider
permissions, terms and approved refresh cadence must be reviewed before enabling
continuous collection or wider redistribution. OpenStreetMap supplies only the
basemap; energy overlays come from Fabric. Review its tile usage policy before
production traffic and replace the basemap with an approved provider when needed.

Create a schema-disabled GeoContext by omitting `creationPayload` from the REST
create request (the orchestrator does this; `enableSchemas` accepts only `true`).
Keep the pipeline's concurrency at one. Its Delta feature table
is partitioned by layer; a complete validated snapshot atomically replaces only
that layer. Raw responses and attempt logs remain in OneLake. Healthy grid/plant
snapshots are reused for 24 hours unless `force_refresh=true`; `refresh_mode=operational`
always skips those static sources. Existing baseline setup/seed/telemetry jobs are
not rerun just because map ingestion is added or changed.

The **Knowledge Graph** visualizes this composition as a scoped Cytoscape property graph. It defaults
to the selected turbine and synchronizes that selection with Overview, Real-Time Telemetry, Digital
Twin, and Maintenance. It discovers the live Ontology and its child Graph Model, queries
materialized bound nodes and relationships directly with Fabric GQL, enriches signals with current
Eventhouse KQL readings, and adds Rayfin SQL records as external operational overlays. It requeries
GQL every 30 seconds while visible; Fabric must first finish the child Graph Model refresh for
upstream Lakehouse changes. See the canonical
[`Knowledge Graph design`](../docs/knowledge-graph.md) for implementation details, operational
scenarios, screenshots, freshness behavior, and direct Graph Model architecture.

## Architecture

```
              ┌────────────────────────┐
              │  Hydro Operations SPA  │  (React + Leaflet, hosted by Rayfin in Fabric)
              └──────┬─────────┬───────┬┘
           GQL     │   KQL   │       │  Rayfin data client
                     ▼         ▼       ▼
      ┌────────────────┐ ┌──────────┐ ┌───────────────────────────┐
      │Ontology Graph  │ │Eventhouse│ │  Rayfin SQL (operational) │
      │nodes + edges   │ │OPCUAEvents│ │  WorkOrders, Inspections, │
      └────────────────┘ └──────────┘ │  SpareParts, Asset3DModels│
        built by RTI_001…010          │  MaintenanceNotifications │
        (see root README)             └───────────────────────────┘
```

- **Lakehouse + Eventhouse** are produced by the RTI notebooks / `Pipe_Setup` — see the [root README](../README.md).
- **Rayfin SQL** schema and seed are owned by this app (below).
- **Operations Agent email alerts** (`RTI_010` / `Pipe_SendEmailAlert`) need a one‑time **OAuth2 Office 365 Outlook** connection created in the Fabric portal (a Service Principal connection can’t send mail) — see [root README → Prerequisites](../README.md) and [DEPLOY.md → Prerequisites](DEPLOY.md#prerequisites).

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
