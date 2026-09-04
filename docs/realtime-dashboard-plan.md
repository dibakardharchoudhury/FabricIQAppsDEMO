# Plan — Fabric Real-Time Dashboard for Hydro Telemetry

Status as of 2026-09-04: **phases 1–3 done, deployed and wired into the provisioner**, phase 4
(embedding into the Rayfin UI) is scheduled for a separate session.

Goal: replace the hand-written telemetry chart in `HydroOperationsApp` with a native Fabric
Real-Time Dashboard, so the demo shows RTI visuals rendered inside the custom app.

For the day-to-day how-to (change a dashboard, add one, make filters data-driven, embed it in the
app), see [dev-dashboards.md](dev-dashboards.md). This document records the design decisions.

---

## Phase 1 — Data-driven asset mapping (done)

`OPCUAEvents` is deliberately slim — `event_time`, `opcua_node_id`, `value`, `quality`. It carries
no station, turbine or sensor-group column, so a dashboard cannot filter on them directly.

The hierarchy already exists in the Lakehouse silver tables. Rather than hard-coding a lookup in
KQL, the Eventhouse now reads those tables live through OneLake shortcuts.

**Created in `RTI_Demo_Eventhouse_V6` (idempotently, by the notebook):**

| Object | Kind | Source |
| --- | --- | --- |
| `silver_instruments` | delta external table | `Energy_IQ_LakehouseRTI_V6/Tables/silver_instruments` |
| `silver_equipment` | delta external table | `…/Tables/silver_equipment` |
| `silver_facilities` | delta external table | `…/Tables/silver_facilities` |
| `AssetMaster()` | function | joins the three above |
| `TelemetryEnriched(start, end, stations, turbines)` | function | `OPCUAEvents` ⋈ `AssetMaster()`, parameter-filtered |

```kusto
AssetMaster()
// opcua_node_id | Station | Turbine | Signal | SignalGroup | Unit
```

Connection strings use `;impersonate`, so OneLake is read as the signed-in user — the dashboard
inherits Fabric workspace permissions rather than a stored credential.

**Consequence:** every filter value is derived from data. Adding a facility or turbine to the STID
source files and re-running the medallion notebook makes it appear in the dashboard filters with no
dashboard edit. Sensor groups and units likewise come from `instrument_type` / `unit`, not a
hard-coded `case()`.

Verified live: 90 nodes → 3 stations, 15 turbines, 5 sensor groups (`power`, `pressure`, `speed`,
`temperature`, `vibration`), units `MW`, `bar`, `rpm`, `C`, `mm_s`.

## Phase 2 — Dashboard definition as a file (done)

**File:** [Raw/RTI_Notebooks/dashboards/RTI_Hydro_Telemetry_Basic.json](../Raw/RTI_Notebooks/dashboards/RTI_Hydro_Telemetry_Basic.json)
(schema_version 77, `RTDashboard_Regular`).

The file — not notebook code — is the source of truth. RTI_008 embeds its definition in a Python
string literal; this one deliberately does not, so a redesign in the Fabric UI round-trips.

**Layout** (24-column grid, single page "Telemetry"):

| Row | Tiles |
| --- | --- |
| 0 | `Readings` · `Turbines reporting` · `Avg power output (MW)` · `Good quality %` (cards, w6 h3) |
| 3 | `Power output (MW)` · `Inlet pressure (bar)` (timecharts, w12 h8) |
| 11 | `Turbine speed (rpm)` · `Turbine temperature (C)` |
| 19 | `Vibration (mm/s)` · `Latest reading per signal` (table) |

**Parameters:**

| Filter | Variable | Type | Source |
| --- | --- | --- | --- |
| Time range | `_startTime` / `_endTime` | duration, default 4 h | built in |
| Station | `_station` | multi-select, Select-all | query `AssetMaster() \| distinct Station` |
| Turbine | `_turbine` | multi-select, Select-all, auto-reset | query narrowed by `_station` |

Charts bin adaptively so a wide time range does not melt the F2 capacity:

```kusto
let Bin = case(_endTime - _startTime > 7d, 1h,
               _endTime - _startTime > 1d, 15m,
               _endTime - _startTime > 6h, 5m,
                                           30s);
```

Sensor-group predicates use `=~` (case-insensitive) so a casing change in `instrument_type` does not
silently blank a chart.

The checked-in copy carries `__CLUSTER_QUERY_URI__` / `__KQL_DB_ID__` / `__KQL_DB_NAME__` /
`__WORKSPACE_ID__` placeholders; the notebook overwrites `dataSources[*]` unconditionally, so a file
downloaded from *any* workspace redeploys correctly into the current one.

## Phase 3 — Provisioning notebook, wired into the provisioner (done)

**Generated from one source.** [Raw/RTI_Notebooks/tools/build_rti_012.py](../Raw/RTI_Notebooks/tools/build_rti_012.py)
holds the cell text and emits all three artifacts:

| Output | Role |
| --- | --- |
| `Raw/RTI_Notebooks/RTI_012_build_basic_telemetry_dashboard.ipynb` | readable copy |
| `Notebooks/RTI_012_build_basic_telemetry_dashboard.Notebook/notebook-content.py` | the Fabric git item the provisioner actually deploys |
| `…/.platform` | item manifest (`logicalId` `6d2f0f1a-…`) |

```powershell
python Raw\RTI_Notebooks\tools\build_rti_012.py           # regenerate
python Raw\RTI_Notebooks\tools\build_rti_012.py --check   # non-zero if stale
```

Run `--check` before opening a PR — the dashboard JSON is embedded in the notebook as a seed, and
this is what stops the two copies drifting.

**Notebook cells:**

| Cell | Does |
| --- | --- |
| 0 | Config; every override blank = auto-resolve |
| 1 | Settings from `rti_demo_settings`, else Fabric REST discovery. Tokens via `notebookutils.credentials.getToken`, Key Vault SPN as fallback |
| 2 | Creates the shortcuts + functions from phase 1 |
| 3 | **Generated** embedded seed of the definition |
| 4 | Loads the definition: Lakehouse `Files/dashboards/` → `DASHBOARD_SOURCE_URL` → embedded seed |
| 5 | Re-points the data source, then **executes all 12 queries and aborts the deploy if any fail** |
| 6 | Creates or `updateDefinition`s the `KQLDashboard` item |
| 7 | Writes the resolved copy back to `Files/dashboards/`, records ids in `rti_demo_settings` |

The seed follows the RTI_001 precedent (STID CSVs embedded, written out with `notebookutils.fs.put`)
so a fresh tenant provisions with no external network access. The **file always wins** when present,
which preserves the redesign loop.

**Provisioner wiring.** `Raw/workspace-reset/` deploys items only through Fabric Git integration, so
the `Notebooks/…​.Notebook/` folder above is what ships. Execution is hooked into the Stage 2 DAG in
[RTI_Orchestrator_Setup](../Notebooks/RTI_Orchestrator_Setup.Notebook/notebook-content.py):

```python
{"name": "NB12_basicdash", "path": "RTI_012_build_basic_telemetry_dashboard",
 "dependencies": ["NB02_eventhouse", "NB03_medallion"], ...}
```

It depends on **NB03 as well as NB02** — the shortcuts read `silver_instruments` /
`silver_equipment` / `silver_facilities`, so the medallion tables must exist first.

End-to-end chain:

```
launch.py → sync_workspace_from_git.py → run_pipeline.py (01_Pipe_Setup)
  → RTI_001 → RTI_Orchestrator_Setup
                └─ runMultiple: NB02, NB03, NB04, NB05, NB06, NB08, NB09, NB10, NB12
```

**Redesign loop:**
1. Edit the dashboard in Fabric.
2. `Manage → Download file`.
3. Drop the JSON into the Lakehouse at `Files/dashboards/RTI_Hydro_Telemetry_Basic.json` and commit
   it to `Raw/RTI_Notebooks/dashboards/`.
4. `python Raw\RTI_Notebooks\tools\build_rti_012.py` to refresh the seed, then re-run RTI_012.

**Verified this session** in workspace `hkton2026`:

- Dashboard `RTI_Hydro_Telemetry_Basic` = `0b7f7c49-85ea-4998-a597-500373a78f25`; read-back confirms
  10 tiles, 12 queries, 3 parameters, data source on `RTI_Demo_Eventhouse_V6`.
- The git-format artifact (`notebook-content.py`, `dependencies: {}`) was pushed and run **standalone
  with no default lakehouse and with the Lakehouse definition copy deleted** — it fell back to REST
  discovery, resolved the right lakehouse by name, bootstrapped from the embedded seed, redeployed the
  dashboard, and rewrote `Files/dashboards/…json` (17,350 bytes). Job Completed.

> The lakehouse lookup deliberately does not take the first `Lakehouse` item: the workspace also
> contains the ontology's own `…_lh_…` lakehouse. Resolution order is `lakehouse_id` setting →
> `lakehouse_name` setting → first non-`_lh_` lakehouse.

## Phase 4 — Embed into the Rayfin UI (next session)

Fabric Embed (public preview) supports exactly one item type today: Real-Time Dashboard. The
prerequisites are already satisfied — MSAL SPA, F SKU capacity, delegated user auth, redirect URIs
managed by `npm run setup-live-auth`.

Planned work:

1. `npm i @microsoft/fabric-embed`.
2. Add the delegated scope **`Fabric.Embed`** to the SPA registration through `setup-live-auth`
   (not the portal). `Item.Read.All` is already requested in
   [HydroOperationsApp/src/services/fabric.ts](../HydroOperationsApp/src/services/fabric.ts).
3. Extend `discoverConfig()` to pick up the `KQLDashboard` item id — it already enumerates workspace
   items, so this is one more filter. `rti_demo_settings.basic_dashboard_id` is the fallback.
4. Render `EmbedManager` + `KQLDashboardEmbedClient` (`viewMode: View`, `accessTokenProvider` wired
   to the existing `silentToken` / `popupToken` pair) behind the existing
   [TelemetryViewToggle](../HydroOperationsApp/src/ui-shared/components/telemetry/TelemetryViewToggle.tsx).

**Add the embedded dashboard as a second view rather than replacing the custom chart.** The custom
chart is driven by the signal tree and range control and supports cross-component selection; an
embedded dashboard is self-contained and filters only through its own parameters. Keeping both gives
the native-Fabric demo moment without losing the interactive drill-down, and leaves a fallback if the
preview SDK contract shifts.

### Known trade-offs

- Fabric Embed is public preview — SDK package name, type names and config surface may change.
- No app-owns-data: every viewer needs Fabric access to the dashboard *and* the Eventhouse. The
  current direct Kusto call has the same constraint, so this is not a regression.
- Dashboard tiles consume capacity. Auto-refresh is set to 1 min with a 30 s floor; lower it before a
  long demo on the F2.
- `external_table()` re-reads the Delta log on every query. Fine for 90 rows; if tile latency becomes
  visible, materialise `AssetMaster()` into a real Kusto table with `.set-or-replace` in RTI_012 and
  point the function at it.
