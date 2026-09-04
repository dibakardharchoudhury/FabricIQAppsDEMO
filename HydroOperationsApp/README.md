# Hydro Operations Fabric App

A React + Leaflet + [Rayfin](https://www.npmjs.com/package/@microsoft/rayfin-cli) single‑page app
that runs **inside Microsoft Fabric** and gives a hydropower operations team one screen composing
**three independent data stores** — plus an in‑browser 3D digital‑twin viewer.

> **To deploy, follow [DEPLOY.md](DEPLOY.md).** This README covers the architecture and data model.
> Moving to a different tenant, workspace, or capacity region? See
> [DEPLOY.md → Redeploying to a different tenant, workspace, or region](DEPLOY.md#redeploying-to-a-different-tenant-workspace-or-region)
> (reset `.deployments.json`, re-register the SPA, `rayfin up --workspace-id <guid> --yes`, and the
> Fabric App Items preview feature/region gating).
>
> **Entra prerequisite:** runtime sign-in uses the single-tenant SPA
> **`Hydro Operations Fabric Client`**. When the operator cannot create/configure app registrations
> or grant admin consent, AppBackend/static-host deployment still succeeds with degraded-auth
> warnings; browser sign-in and live Fabric data remain unavailable. Use the role split and portal fallback in
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

## Architecture

```
              ┌────────────────────────┐
              │  Hydro Operations SPA  │  (React + Leaflet, hosted by Rayfin in Fabric)
              └──────┬─────────┬───────┬┘
         GraphQL     │   KQL   │       │  Rayfin data client
                     ▼         ▼       ▼
      ┌────────────────┐ ┌──────────┐ ┌───────────────────────────┐
      │ Lakehouse STID │ │Eventhouse│ │  Rayfin SQL (operational) │
      │ silver_*       │ │OPCUAEvents│ │  WorkOrders, Inspections, │
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
