# Hydro Operations Fabric App

A React + Leaflet + [Rayfin](https://www.npmjs.com/package/@microsoft/rayfin-cli) single-page
application that runs **inside Microsoft Fabric** and gives a hydropower operations team one
screen that composes **three independent Fabric data stores**:

| Store | Owns | Surfaced in the app as | Accessed via |
|---|---|---|---|
| **Lakehouse (STID)** | Engineering master data — facilities, systems, equipment, instruments | Facility map, asset registry, signal labels | `Hydro_STID_API` GraphQL item over the Lakehouse SQL endpoint |
| **Eventhouse (telemetry)** | `OPCUAEvents(event_time, opcua_node_id, value, quality)` | Live asset signals / "Recent signals" | KQL query against the Eventhouse cluster |
| **Rayfin SQL (operational)** | Mutable operational records — work orders, notifications, inspections, spare parts, 3D models | Work orders, digital twin, inspections, spare-part inventory, maintenance notifications | Rayfin `data` client (Data API Builder) |

The three stores are **never merged server-side**. The app queries each independently and joins
records in the browser by `equipmentId` / `instrumentId` / `opcuaNodeId`, so provenance stays
explicit (every panel shows its source: *Lakehouse STID*, *Eventhouse*, or *Rayfin SQL*).

> **Demo data is synthetic but ownership is real.** The STID facilities and the operational SQL
> rows are synthetic demo data (see the regeneration scripts below), yet each record still lives in
> the store that would own it in production. No reference or telemetry rows are copied into Rayfin SQL.
> The map uses only the facility-level latitude/longitude in `silver_facilities`.

---

## Architecture at a glance

```
                 ┌────────────────────────┐
                 │  Hydro Operations SPA  │  (this app, hosted by Rayfin in Fabric)
                 │  React + Leaflet       │
                 └───────────┬────────────┘
        GraphQL │            │ KQL              │ Rayfin data client (REST/DAB)
                ▼            ▼                   ▼
   ┌────────────────┐ ┌──────────────┐ ┌───────────────────────────┐
   │ Lakehouse STID │ │  Eventhouse  │ │  Rayfin SQL (operational) │
   │ silver_*       │ │  OPCUAEvents │ │  WorkOrders, Inspections, │
   │ (3 facilities) │ │  (telemetry) │ │  SpareParts, Asset3DModels│
   └────────────────┘ └──────────────┘ │  MaintenanceNotifications │
        ▲                    ▲          └───────────────────────────┘
        │ built by RTI_001…010 notebooks │            ▲
        └──────────── (see root README) ─┘            │ schema + seed (this README)
```

- The **Lakehouse + Eventhouse** are produced by the `RTI_001`…`RTI_010` notebooks and the
  `Pipe_Setup` / `Pipe_Stream` pipelines — documented in the [root README](../README.md).
- The **Rayfin SQL** schema and demo data are owned by *this* app and documented here.

---

## Prerequisites

1. **Node 24.** All CLI/build commands below are wrapped in `npx -y -p node@24 -c "…"` so they run on
   Node 24 regardless of the machine default. (Windows note: `npm run typecheck` chains with `;` which
   breaks in PowerShell — call `tsc` directly as shown in *Local preview*.)
2. **A deployed RTI Fabric environment** (Lakehouse `Energy_IQ_LakehouseRTI_*`, Eventhouse
   `RTI_Demo_Eventhouse_*`, and the `Hydro_STID_API` GraphQL item). Build it from the
   [root README](../README.md) by running `Pipe_Setup`.
3. **A Fabric workspace where you can create a Rayfin AppBackend** and an Entra SPA app
   registration (delegated `GraphQLApi.Execute.All`, Azure Data Explorer, and Fabric API access;
   no client secret — interactive sign-in only).

Install dependencies once:

```powershell
cd HydroOperationsApp
npx -y -p node@24 -c "npm install"
```

---

## Rayfin SQL data model

Defined in [`rayfin/data/schema.ts`](rayfin/data/schema.ts) with `@microsoft/rayfin-core` decorators.
Table names are PascalCase-pluralized (`WorkOrder` → `dbo.WorkOrders`).

| Entity | Table | Purpose | Joins to Lakehouse/Eventhouse by |
|---|---|---|---|
| `WorkOrder` | `dbo.WorkOrders` | Maintenance work orders | `equipmentId`, `instrumentId`, `opcuaNodeId` |
| `MaintenanceNotification` | `dbo.MaintenanceNotifications` | Operational alerts | `equipmentId`, `opcuaNodeId` |
| `Inspection` | `dbo.Inspections` | Condition inspection results (VISUAL / THERMOGRAPHIC / VIBRATION / LUBRICATION) | `equipmentId`, `opcuaNodeId` |
| `SparePart` | `dbo.SpareParts` | Spare-part inventory with reorder levels (`partNumber` unique, max 255) | `equipmentType` |
| `Asset3DModel` | `dbo.Asset3DModels` | Digital-twin GLB model registry | `equipmentId` |
| `OperatorNote` | `dbo.OperatorNotes` | Free-text operator notes | `equipmentId`, `opcuaNodeId` |
| `ShiftHandover` | `dbo.ShiftHandovers` | Shift handover log | — |
| `AlarmAcknowledgement` | `dbo.AlarmAcknowledgements` | Alarm acknowledgements | `opcuaNodeId` |
| `StreamRun` | `dbo.StreamRuns` | `Pipe_Stream` run audit | `pipelineItemId` |

Every `equipmentId` (`EQUIP_RTI_T###`) and `opcuaNodeId` (`ns=2;s=T###.<signal>`) resolves to a real
STID Lakehouse row, so the cross-store joins in the UI always land.

---

## Synthetic demo data — two generators (source of truth)

Two deterministic Python scripts at the **repository root** regenerate every piece of demo data.
They take no arguments and are safe to re-run.

### 1. `_gen_stid.py` — 3-facility STID (Lakehouse)

Writes the four STID CSVs and rewrites the embedded `STID_FILES` block in the self-contained
lakehouse notebook (both the Fabric `.py` and the readable `.ipynb` mirror):

```powershell
python _gen_stid.py
# → 3 facilities, 15 equipment (turbines T001–T015), 90 instruments
```

Outputs:
- `Raw/stid_rti_fixed_source_files/{facilities,systems,equipment,instruments}_stid.csv`
- `Notebooks/RTI_001_create_lakehouse_SelfContained.Notebook/notebook-content.py`
- `Raw/RTI_Notebooks/RTI_001_create_lakehouse_SelfContained.ipynb`

Data model: 3 Norwegian hydropower facilities (`FACILITY_RTI_001/002/003`), 5 turbines each,
6 signals per turbine (inlet_pressure, power_output, turbine_speed, turbine_temp, vibration_a,
vibration_d). The whole Fabric medallion (`RTI_003`→`004`→`005`→`006`→`007`) is **data-driven off
these CSVs** — `silver_signal_master` is derived from `silver_instruments`, and the `RTI_007`
telemetry simulator loops every active signal — so **no downstream notebook changes are needed** to
grow from 1 to 3 facilities.

### 2. `_gen_seed.py` — operational demo data (Rayfin SQL)

Generates the operational seed in **two forms** from one source of truth:

```powershell
python _gen_seed.py
# → WorkOrders: 12  Notifications: 6  Inspections: 30  SpareParts: 12  Models: 15
```

Outputs:
- [`sql/seed-operational-data.sql`](sql/seed-operational-data.sql) — idempotent `MERGE` script (for
  `sqlcmd`/SSMS if you have direct SQL access).
- [`src/services/seedData.ts`](src/services/seedData.ts) — typed arrays consumed by the app's
  **client-side self-seeder** (the supported path — see below).

Content: 12 work orders (across all 3 facilities, incl. completed), 6 maintenance notifications,
30 inspections (2 per turbine; `T013` VIBRATION = FAIL for a realistic anomaly), 12 spare parts
(several below reorder level), and 15 digital-twin 3D models (each pointing at a public Khronos
sample `.glb`).

---

## Deploying the app end to end

All commands run from `HydroOperationsApp/` on Node 24.

### 1. Configure the environment

Copy the example env and fill in your Fabric values:

```powershell
Copy-Item rayfin/.env.example rayfin/.env
```

Populate `RAYFIN_PUBLIC_*` (GraphQL endpoint, KQL cluster/database, Entra client & tenant IDs,
lakehouse SQL endpoint, stream pipeline/notebook IDs, workspace name). The Vite `VITE_*` values are
generated automatically by `rayfin env` during the build.

### 2. Provision / update the Rayfin backend and DB schema

```powershell
# Creates the AppBackend + auth + data services (first time), or updates them.
npx -y -p node@24 -c "node node_modules/@microsoft/rayfin-cli/scripts/main up"

# Applies rayfin/data/schema.ts to the live SQL database (creates/updates all tables).
npx -y -p node@24 -c "node node_modules/@microsoft/rayfin-cli/scripts/main up db apply --yes"
```

> `db apply` generates a Data API Builder config from `schema.ts` and pushes it to the remote Rayfin
> item — it uses your cached Fabric login. It **creates the tables but does not insert rows**.
> (`npm run rayfin:up` / `npm run rayfin:db` are shortcuts for these two commands.)

### 3. Build and deploy the static app

```powershell
npx -y -p node@24 -c "npm run build:rayfin && node node_modules/@microsoft/rayfin-cli/scripts/main up staticapp deploy --yes"
```

`build:rayfin` runs `rayfin env --framework vite` (injects `VITE_*` from the deployment) then
`vite build`. The deploy prints the hosting URL and a deployment ID.

### 4. Seed the operational data (supported path)

Rayfin exposes **no direct SQL host** — its documented pattern is to seed through the authenticated
data client. The app ships an **idempotent self-seeder**:

1. Open the deployed app and sign in (avatar button).
2. Click **"Seed demo data"** in the header.

The seeder ([`seedOperationalDataIfEmpty`](src/services/rayfin.ts)) is safe to re-run:
- `WorkOrder` and `MaintenanceNotification` are seeded **per record** (by `workOrderNumber` /
  `equipmentId+summary`), so only missing rows are added.
- `Inspection`, `SparePart`, and `Asset3DModel` are seeded **only when their table is empty**.

Re-clicking reports either what was added or *"Operational data already present — nothing to seed."*

> If you have direct SQL access instead, run [`sql/seed-operational-data.sql`](sql/seed-operational-data.sql)
> with `sqlcmd` (pass `-v ActorOid=<your-oid>`), then verify with
> [`sql/validate-seed.sql`](sql/validate-seed.sql).

---

## Full regeneration checklist (from scratch)

To reproduce everything since the app was created:

1. **Build the RTI Fabric environment** — follow the [root README](../README.md): fill the
   `Pipe_Setup` parameters and run it (creates Lakehouse, Eventhouse, ontology, GraphQL API, agents).
2. **Regenerate the 3-facility STID data** (optional if you want to re-derive it):
   `python _gen_stid.py`, then in Fabric re-run `Pipe_Setup` (Stage 1 reloads `Files/bronze/stid`
   and the medallion) so the Lakehouse holds 3 facilities / 15 turbines / 90 instruments.
   *(This Fabric run is the one step that must happen in Fabric — the app cannot reload the live
   Lakehouse for you.)*
3. **Run `Pipe_Stream`** (`RTI_007`) to push a telemetry burst into the Eventhouse.
4. **Regenerate the operational seed** (optional): `python _gen_seed.py`.
5. **Deploy the app**: steps 1–3 in *Deploying the app end to end* above.
6. **Seed operational data**: click **Seed demo data** (step 4 above).
7. **Verify** in the browser: 3 facilities on the map, asset signals from Eventhouse, and the
   Digital twin / Inspections / Spare parts / Maintenance notifications / Work orders panels populated.

---

## Local preview

```powershell
cd HydroOperationsApp
npx -y -p node@24 -c "node ./node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json"   # typecheck
npx -y -p node@24 -c "node ./node_modules/eslint/bin/eslint.js ."                              # lint
npx -y -p node@24 -c "node ./node_modules/vite/bin/vite.js build"                             # production build
npx -y -p node@24 -c "node ./node_modules/vite/bin/vite.js"                                   # dev server
```

Without Fabric environment values the app shows explicit disconnected states — it does not fabricate
source values or Data Agent answers.

---

## Project layout

```
HydroOperationsApp/
├── rayfin/
│   ├── rayfin.yml                 # Rayfin app definition (db + auth + static hosting + data)
│   ├── data/schema.ts             # SQL entity definitions (source of truth for tables)
│   └── .env.example               # Fabric/Entra configuration template
├── sql/
│   ├── seed-operational-data.sql  # Idempotent MERGE seed (sqlcmd/SSMS path)  ── generated by _gen_seed.py
│   └── validate-seed.sql          # Post-seed verification
├── src/
│   ├── App.tsx                    # Composed operations view (3 stores) + facility selector + Seed button
│   ├── components/FacilityMap.tsx # Multi-facility Leaflet map (fits bounds to all facilities)
│   └── services/
│       ├── fabric.ts              # GraphQL (STID) + KQL (telemetry) + Data Agent
│       ├── rayfin.ts              # Rayfin data client, list/create fns, seedOperationalDataIfEmpty
│       └── seedData.ts            # Typed operational seed arrays  ── generated by _gen_seed.py
└── README.md                      # this file
```

Regeneration scripts live at the repository root: `_gen_stid.py`, `_gen_seed.py`.
