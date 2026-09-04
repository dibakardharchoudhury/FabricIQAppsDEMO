# Dev guide — Real-Time Dashboards

How to change, add, provision and embed a Fabric Real-Time Dashboard in this repo.

For *why* the current dashboard is built the way it is, see
[realtime-dashboard-plan.md](realtime-dashboard-plan.md).

---

## Where things live

| Path | What it is |
| --- | --- |
| `Raw/RTI_Notebooks/dashboards/*.json` | **Dashboard definitions — the source of truth** |
| `Raw/RTI_Notebooks/tools/build_rti_012.py` | Generator: owns the notebook cell text, emits all three artifacts |
| `Notebooks/RTI_012_….Notebook/` | The Fabric git item the provisioner actually deploys |
| `Raw/RTI_Notebooks/RTI_012_….ipynb` | Readable mirror — **generated, do not hand-edit** |
| `Notebooks/RTI_Orchestrator_Setup.Notebook/` | Stage 2 DAG that runs the provisioning notebooks |

> [!IMPORTANT]
> `Raw/` is **not** a Fabric item folder. The provisioner deploys workspace items purely through
> Fabric Git integration, which only picks up `Notebooks/<name>.Notebook/` (`.platform` +
> `notebook-content.py`) and `Orchestrator_Pipelines/<name>.DataPipeline/`. Anything under `Raw/`
> is a source/readable copy that never reaches Fabric on its own.

---

## Recipe A — redesign an existing dashboard

The definition file round-trips, so do the visual work in Fabric rather than in JSON.

1. Open the dashboard in Fabric, switch to **Editing**, change what you want, **Save**.
2. **Manage → Download file**.
3. Overwrite `Raw/RTI_Notebooks/dashboards/RTI_Hydro_Telemetry_Basic.json` with the download.
4. Refresh the embedded seed and both notebook copies:

   ```powershell
   python Raw\RTI_Notebooks\tools\build_rti_012.py
   ```

5. Re-run `RTI_012_build_basic_telemetry_dashboard` (or the whole `Pipe_Setup`).

The notebook overwrites `dataSources[*]` unconditionally, so a file downloaded from *any* workspace
redeploys correctly into the current one — cluster URI, database id, workspace id and name are all
replaced from live settings. That is what makes the definition portable across tenants.

Run `--check` before opening a PR; it exits non-zero when the generated files are stale:

```powershell
python Raw\RTI_Notebooks\tools\build_rti_012.py --check
```

## Recipe B — add a new dashboard

1. Author the definition. Easiest start: build it in the Fabric UI, download the JSON, drop it in
   `Raw/RTI_Notebooks/dashboards/`. Replace the concrete data-source values with the placeholder
   tokens `__CLUSTER_QUERY_URI__`, `__KQL_DB_ID__`, `__KQL_DB_NAME__`, `__WORKSPACE_ID__` so the
   checked-in file is tenant-neutral.
2. Copy `build_rti_012.py` to a new generator, change `DASHBOARD_NAME`, `NOTEBOOK_NAME` and
   **generate a fresh `LOGICAL_ID`** (a duplicate collides on Git sync).
3. Generate, then add the notebook to the Stage 2 DAG in
   [RTI_Orchestrator_Setup](../Notebooks/RTI_Orchestrator_Setup.Notebook/notebook-content.py) —
   both the `Notebooks/` item and the `Raw/` mirror:

   ```python
   {"name": "NB13_mydash", "path": "RTI_013_build_my_dashboard",
    "dependencies": ["NB02_eventhouse", "NB03_medallion"],
    "args": _lh, "timeoutPerCellInSeconds": per_notebook_timeout_secs},
   ```

   Nothing auto-discovers notebooks — the DAG is a hard-coded list.
4. Add a row to the notebook table in the root [README](../README.md).

Depend on **NB03** as well as NB02 whenever the dashboard reads Lakehouse dimension data through
shortcuts; the silver tables must exist before the shortcuts resolve.

---

## Make filters data-driven

`OPCUAEvents` is deliberately slim — `event_time`, `opcua_node_id`, `value`, `quality`. Never
hard-code a station/turbine lookup as a KQL `datatable`; it silently rots the moment the STID CSVs
change. Instead surface the Lakehouse dimensions in the Eventhouse.

**1. Shortcut the Lakehouse table** (delta external table, idempotent):

```kusto
.create-or-alter external table silver_instruments kind=delta
(
  h@'abfss://<workspaceId>@onelake.dfs.fabric.microsoft.com/<lakehouseId>/Tables/silver_instruments;impersonate'
)
```

`;impersonate` reads OneLake as the signed-in user, so the dashboard inherits Fabric workspace
permissions instead of carrying a stored credential.

**2. Wrap the join in a function** so tile queries stay short and editable in the UI:

```kusto
.create-or-alter function
with (docstring='…', folder='RTI')
AssetMaster() {
    external_table('silver_instruments')
    | project opcua_node_id, Signal = tag, equipment_id, facility_id, Unit = unit, SignalGroup = instrument_type
    | join kind=inner (external_table('silver_equipment')  | project equipment_id, Turbine = tag) on equipment_id
    | join kind=inner (external_table('silver_facilities') | project facility_id, Station = facility_name) on facility_id
    | project opcua_node_id, Station, Turbine, Signal, SignalGroup, Unit
}
```

**3. Point the parameter query at it:**

```kusto
AssetMaster()
| distinct Station
| sort by Station asc
```

Result: adding a facility or turbine to the STID CSVs makes it appear in the filters after the next
medallion run, with no dashboard edit.

> [!WARNING]
> The workspace contains **more than one lakehouse** — the ontology item creates its own
> `…_lh_<guid>`. Never take the first `Lakehouse` item from the items list. Resolve by
> `lakehouse_id` setting → `lakehouse_name` setting → first non-`_lh_` name.

---

## RTD schema 77 cheat sheet

Authoritative schemas (fetch them, don't guess):
`https://dataexplorer.azure.com/static/d/schema/77/{dashboard,tile,query,parameter,baseQuery,dataSource}.json`

| Trap | Reality |
| --- | --- |
| `usedParamVariables` on a tile | **Rejected.** Schema 20 only; schema 77 sets `unevaluatedProperties: false` |
| `"xColumn": {"type": "infer"}` | **Rejected.** `xColumn` is `string \| null`; `null` means infer |
| Single-value KPI visual | `"visualType": "card"` plus the `multiStat__*` options |
| Required top-level keys | `schema_version`, `tiles`, `baseQueries`, `parameters`, `dataSources`, `pages`, `queries`, `embeddedApps` — all must be present, even if empty |
| Required tile keys | `id`, `title`, `layout`, `pageId`, `visualType`, `visualOptions`, `queryRef` |
| Grid width | 24 columns; `layout.width` minimum 2 |

**Multi-select parameter** — the shape that actually works:

```jsonc
{
  "kind": "string",
  "variableName": "_station",
  "selectionType": "array",
  "includeAllOption": true,
  "allIsNull": true,
  "defaultValue": { "kind": "all" },
  "dataSource": {
    "kind": "query",
    "queryRef": { "kind": "query", "queryId": "…" },
    "columns": { "value": "Station" }
  }
}
```

Consume it with the Select-all guard, otherwise "all" filters everything out:

```kusto
| where Station in (_station) or isempty(_station)
```

**Bin adaptively** so a wide time range does not melt a small capacity:

```kusto
let Bin = case(_endTime - _startTime > 7d, 1h,
               _endTime - _startTime > 1d, 15m,
               _endTime - _startTime > 6h, 5m,
                                           30s);
```

Prefer `=~` over `==` when matching values that originate in source data (e.g. `instrument_type`),
so a casing change doesn't silently blank a chart.

---

## Validate before you deploy

A dashboard that deploys is not a dashboard that renders. `RTI_012` executes **every** query with the
parameters bound and aborts the deploy on any failure — keep that step in any new provisioning
notebook:

```python
BINDINGS = {
    "_startTime": "let _startTime = ago(4h);",
    "_endTime":   "let _endTime = now();",
    "_station":   "let _station = dynamic(null);",
    "_turbine":   "let _turbine = dynamic(null);",
}
for query in dashboard_def["queries"]:
    prelude = "\n".join(BINDINGS[v] for v in query.get("usedVariables", []) if v in BINDINGS)
    kusto(f"{prelude}\n{query['text']}", endpoint="query")
```

Also assert that each tile's `visualOptions.xColumn` / `yColumns` / `seriesColumns` /
`multiStat__valueColumn` actually exist in its query's result columns — a typo there produces an
empty tile with no error.

---

## Embed a dashboard in the React app

Microsoft Fabric Embed (public preview) supports **exactly one item type: Real-Time Dashboard**.
Power BI reports still go through Power BI embedded analytics; other Fabric items can't be embedded.

Prerequisites, all already true for `HydroOperationsApp`:

| Requirement | Status |
| --- | --- |
| MSAL.js SPA with Entra user sign-in | [src/services/fabric.ts](../HydroOperationsApp/src/services/fabric.ts) |
| Workspace on an F SKU capacity | yes |
| Redirect URI registered | handled by `npm run setup-live-auth` |
| Viewer has access to dashboard **and** Eventhouse | already required by the direct Kusto path |

**1. Install:**

```powershell
npm i @microsoft/fabric-embed
```

**2. Add the `Fabric.Embed` delegated scope.** Do it through `npm run setup-live-auth`, never by
hand in the Entra portal — see the repo Copilot instructions. `Item.Read.All` is already in
`FABRIC_SCOPES`.

**3. Discover the dashboard id** rather than hard-coding it. `discoverConfig()` already enumerates
workspace items, so this is one more filter; `rti_demo_settings.basic_dashboard_id` is the fallback.

**4. Render it.** `silentToken` / `popupToken` are module-private in `fabric.ts`, so export a small
token provider next to them rather than reaching into MSAL again:

```ts
const EMBED_SCOPES = ['https://api.fabric.microsoft.com/.default']

export async function fabricEmbedToken(interactive: boolean): Promise<string> {
  const silent = await silentToken(EMBED_SCOPES)
  if (silent) return silent
  if (!interactive) throw new Error('Fabric consent required.')
  return popupToken(EMBED_SCOPES)
}
```

```tsx
const embedManager = new EmbedManager({ embedClientClasses: [KQLDashboardEmbedClient] })

const client = embedManager.embed(containerRef.current, {
  accessToken: { token: await fabricEmbedToken(true) },
  itemId: dashboardId,
  itemType: 'KQLDashboard',
  workspaceId,
  viewMode: KQLDashboardViewMode.View,
  eventHooks: {
    accessTokenProvider: {
      callback: async (input?: { scopes?: string[] }) => ({ token: await fabricEmbedToken(false) }),
    },
  },
})
client.on('error', console.error)
```

The `accessTokenProvider` is not optional — without it the embed dies when the first token expires.

> [!NOTE]
> The app itself can run inside a Fabric iframe, where **auth redirects are blocked**. Keep using
> `acquireTokenPopup`, and only from a user gesture.

**5. Add it as a second view, not a replacement.** The custom chart is driven by the signal tree and
range control and supports cross-component selection; an embedded dashboard is self-contained and
filters only through its own parameters. Put both behind
[TelemetryViewToggle](../HydroOperationsApp/src/ui-shared/components/telemetry/TelemetryViewToggle.tsx)
so the preview SDK can't take the demo down.

### Embedding gotchas

- **Preview contract** — SDK package name, type names and config surface may change before GA.
- **Delegated only** — no service principal, no app-owns-data. The signed-in user must be able to
  open the item in Fabric.
- **Capacity cost** — embedded tiles run on the F SKU. Auto-refresh defaults to 1 min with a 30 s
  floor; lower it before a long demo on an F2.
- **`external_table()` re-reads the Delta log per query.** Fine for 90 rows. If tile latency shows,
  materialise `AssetMaster()` into a real Kusto table with `.set-or-replace` in the provisioning
  notebook and repoint the function.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Deploy succeeds, tile is blank | `visualOptions` column name doesn't match a query result column |
| Filter dropdown empty | Parameter query returned no rows, or `columns.value` names a missing column |
| Everything empty when a filter is on "all" | Missing `or isempty(_param)` guard |
| `FeatureNotAvailable` on item create | Workspace capacity region doesn't support the item type |
| Git sync fails with a name collision | Two `.platform` files share a `logicalId`, or the display name already exists under a different one |
| "Need admin approval" on a Kusto scope | `.default` was requested; use the named `<cluster>/user_impersonation` scope |
