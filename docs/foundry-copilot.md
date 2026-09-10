# Foundry Copilot — authentication, flows and tool calls

How the **Foundry** Copilot engine authenticates, what happens during a single answer, and exactly
which data each tool can reach.

For the Fabric **Data Agent** engine (v1), there is nothing to document here: the app resolves the
published agent's MCP endpoint and forwards the question. This doc covers v2 only.

---

## Where things live

| Path | What it is |
| --- | --- |
| `src/services/copilot/catalog.ts` | **The allow-list of readable data — the governance boundary** |
| `src/services/copilot/settings.ts` | Operator overrides edited in Administration (prompt, tools, sources) |
| `src/services/copilot/query.ts` | Pure KQL builder, `run_kql` validator, structured row filter |
| `src/services/copilot/tools.ts` | Tool schemas + executors, per-turn caches |
| `src/services/copilot/chatStream.ts` | SSE reader + tool-call delta accumulator |
| `src/services/copilot/suggestions.ts` | Parses the follow-up options a reply offers |
| `src/services/copilot/foundry.ts` | The agent loop, system prompt, conversation history |
| `src/services/fabric.ts` | MSAL, token acquisition, `runKustoQuery`, `queryStid` |
| `scripts/copilot-tools.test.mjs` | Unit tests for the validator, filter and accumulator |

---

## 1. Authentication

### The architecture, and why

The app is a **static SPA** — `rayfin.yml` has `staticHosting` only and `functions: enabled: false`.
There is no server-side code path, so there is nowhere to hide an API key. v2 therefore calls the
Azure AI Foundry data plane **directly from the browser with a delegated Entra token**.

That constraint produces the property that matters most:

> [!IMPORTANT]
> Every tool call executes **as the signed-in user**. The copilot cannot read anything that user
> could not already read in Fabric. There is no service principal, no app-owns-data mode, and no
> key material in the bundle.

### Tenant pinning

MSAL is constructed with a fixed authority:

```ts
authority: `https://login.microsoftonline.com/${VITE_FABRIC_TENANT_ID}`
```

So **every** token the app acquires — Fabric, Kusto, GraphQL, Foundry — is issued by the tenant that
owns the Fabric workspace.

> [!WARNING]
> The Foundry resource **must live in that same tenant**. Azure evaluates RBAC in the resource's own
> home tenant, so a resource in a different tenant returns 401 no matter which roles you assign.
> Check with `az account list --all` before creating it — picking a corp or personal subscription by
> accident is easy and the failure mode looks like a permissions bug.

### Scopes

Each live data path authenticates against a different resource. All are **delegated**, and all are
requested as **named scopes rather than `.default`**.

| Path | Scope | Resource |
| --- | --- | --- |
| Foundry inference | `https://cognitiveservices.azure.com/user_impersonation` | Microsoft Cognitive Services (`7d312290-…`) |
| Telemetry (Kusto) | `<cluster>/user_impersonation` | Azure Data Explorer (`2746ea77-…`) |
| Asset metadata | `…/powerbi/api/GraphQLApi.Execute.All` | Power BI Service (`00000009-…`) |
| Workspace discovery | `Workspace.Read.All`, `Item.Read.All`, `Item.Execute.All` | Power BI Service |
| Operational records | *(none — Rayfin session cookie)* | Rayfin backend |

> [!NOTE]
> `.default` returns only permissions already statically configured for that exact audience, so it
> never triggers incremental user consent — Entra escalates to "Need admin approval" instead. Named
> scopes consent cleanly. This bit the Eventhouse path first; the Foundry scope follows the same
> rule.

Work orders, inspections and spare parts are **not** reached with an MSAL token: `RayfinClient`
carries its own Fabric-backed session established by `initEmbeddedAuth`.

### Token flow

```mermaid
flowchart TD
  U[Signed-in user] --> M[MSAL<br/>authority = Fabric tenant]
  M -->|user_impersonation| F[Foundry data plane]
  M -->|cluster/user_impersonation| K[Eventhouse / Kusto]
  M -->|GraphQLApi.Execute.All| G[Lakehouse GraphQL]
  R[Rayfin session] --> S[(App SQL database)]
  F -.->|RBAC check in resource tenant| RB{{Cognitive Services<br/>OpenAI User}}
```

`foundryToken()` tries silent acquisition first and only falls back to a popup when the call was
started by a user gesture — redirects are blocked inside the Fabric iframe.

### Consent and RBAC

Two separate things, often confused:

| | Grants what | Applied by |
| --- | --- | --- |
| Delegated **scope** | Permission to request a token for that audience | `npm run setup-live-auth` (idempotent) |
| **`Cognitive Services OpenAI User`** role | Permission to actually invoke the deployment | `az role assignment create`, per user, per resource |

Both are required. The scope alone yields a token that the data plane rejects.

Tenant-wide admin consent is *optional* here: all requested scopes are `type: User`, so each user
self-consents on first use. `setup-live-auth` attempts the blanket grant, prints the manual fallback
if it lacks Privileged Role Administrator, and continues — that warning is expected, not a failure.

No CORS configuration is needed. The Azure OpenAI data plane returns `Access-Control-Allow-Origin: *`
and permits `Authorization` on POST. Keep the resource on **public network access**, though: a
private endpoint or a "selected networks" firewall cuts the browser off.

---

## 2. Answer flow

One question runs an agent loop of at most **6 iterations**. Tool results are fed back as `role:
tool` messages so the model can chain queries or recover from its own mistakes.

```mermaid
sequenceDiagram
  participant U as User
  participant A as foundry.ts
  participant M as Foundry model
  participant T as tools.ts
  participant D as Fabric data

  U->>A: question
  A->>A: build [system + history + question]
  loop max 6 iterations
    A->>M: POST configured model endpoint (Responses API + tools)
    M-->>A: SSE text deltas and/or function_call items
    A-->>U: stream partial text
    alt model requested tools
      A->>T: runTool(name, args)
      T->>D: Kusto / GraphQL / SQL (as the user)
      D-->>T: rows
      T-->>A: capped + projected result
      A->>A: append tool message, record step
    else no tool calls
      A->>A: save turn to history (last 8 messages)
      A-->>U: final answer + usage + trace
    end
  end
```

Notes on the implementation:

- **Dynamic endpoint.** Administration stores the complete Responses API URL. The client posts to
  that exact value and sends the configured deployment as `model`; it does not construct a route.
- **Streaming.** Responses API SSE text deltas are rendered progressively. Function-call argument
  fragments are accumulated by output index, and final items replace fragments before execution.
- **Tool failures are not fatal.** The error message is returned to the model as the tool result so
  it can correct itself; the step is still recorded in the trace with its error.
- **History** keeps only completed user/assistant text turns (last 8 messages). Tool traffic is
  dropped between completed turns so a long session cannot grow the context unbounded. During an
  active turn, every function call and function output remains in the Responses input until the
  model produces the final answer. History is cleared only by the explicit conversation reset.
- **No `temperature`** is sent — the gpt-5 family rejects any value but the default.

Each answer carries a trace the UI renders as one collapsible row per tool call: the tool name, a
short label of what it asked for, the row count and elapsed time, expanding to the raw arguments and
the exact KQL. Rows appear **as the call starts**, not when the answer finishes, so the user can see
what the agent is reaching for while it works.

The transcript follows new content only while the reader is already at the bottom — scrolling up
opts out of auto-scroll until the next question is sent.

**Copy** on each answer puts the whole exchange on the clipboard as markdown: the question, every
tool call with its arguments, query and result, the answer, then chart CSV and 3D model links.
Steps retain their result payload for this — the same JSON already sent to the model, already capped
by `truncateForModel`. It is copy-only; rendering it in the trace would bury the one-line summary.

### Follow-up options

When a reply offers choices, they render as chips under the last answer, each with a button to put
it in the composer and one to send it immediately (max 5, capped to two rows).

The model is asked to declare them on a trailing line:

```html
<!--options: ["Show open work orders", "Chart power output for T009"]-->
```

`react-markdown` does not render raw HTML without `rehype-raw`, so the marker is invisible in the
chat without stripping and does not flicker mid-stream. It is stripped explicitly before the Copy
transcript.

> [!NOTE]
> A prose heuristic (cue phrase followed by a list) remains as a fallback, because the Data Agent
> engine never emits the marker and the system prompt is operator-editable — someone can delete the
> rule. Declared options win whenever present; a malformed marker falls through to the heuristic.

---

## 3. Operator settings

**Administration → Foundry Copilot** narrows what the model may reach, persisted per browser in
`localStorage` and applied to the next question:

| Setting | Effect |
| --- | --- |
| Endpoint / deployment | Which Foundry model is called. The endpoint is the complete `/openai/v1/responses` URL, so there is no separate API-version setting. Seeded from `rayfin/.env`, but changing it needs **no rebuild** — it applies to the next question. |
| System prompt | The base instructions. `{{catalog}}` and `{{time}}` are substituted at call time; without `{{catalog}}` the model gets no schema. |
| Additional instructions | Appended after the system prompt. |
| Tools | A disabled tool is removed from the schema **and** refused by the runtime if called anyway. |
| Lakehouse / operational tables | Removed from the prompt and from the tool's `entity` enum; the runtime re-checks. |
| Eventhouse tables & functions | Also narrows the `run_kql` allow-list. |

> [!NOTE]
> These settings constrain the **model**, not the user. They reduce what a wandering or
> injection-influenced agent can touch; they are not a privilege boundary, because the delegated
> token already limits every call to what the signed-in user could read anyway.

---

## 4. Tool calls

### Readable data

Defined once in `catalog.ts`, narrowed by Administration, and rendered into the system prompt — so
the model's schema and the enforced allow-list cannot drift apart.

| Entity | Source | Reached via |
| --- | --- | --- |
| `facilities` → `silver_facilities` | Lakehouse | GraphQL (`queryStid`) |
| `equipment` → `silver_equipments` | Lakehouse | GraphQL |
| `instruments` → `silver_instruments` | Lakehouse | GraphQL |
| `work_orders`, `inspections`, `spare_parts`, `notifications` | App SQL database | `RayfinClient` |
| `asset_models` | App SQL database | `RayfinClient` — 3D model files per equipment |
| `OPCUAEvents` | Eventhouse | Kusto REST |
| `AssetMaster()`, `TelemetryEnriched(...)` | Eventhouse | Kusto REST — telemetry pre-joined to asset master via OneLake shortcuts |

### The tools

| Tool | Accepts | Guardrail |
| --- | --- | --- |
| `query_assets` | entity + structured predicate | No query text — **no injection surface** |
| `query_operations` | entity + structured predicate | No query text — **no injection surface** |
| `query_telemetry` | node ids, lookback, bin, aggregation | Templated KQL; each fragment regex- or map-validated, node ids escaped |
| `run_kql` | **model-authored KQL** | Allow-list + rejection rules below |
| `visualize_dataset` | chart spec + inline CSV | Renders only; reaches no data |
| `show_3d_model` | equipment_id or model_id | Resolves an `Asset3DModel` record and renders the GLB inline; no query text |

### `run_kql` validation

The single place model-written query text is accepted. It must start with a catalog source
(`OPCUAEvents`, `AssetMaster`, `TelemetryEnriched`) and is rejected outright for:

| Rejected | Why |
| --- | --- |
| Leading `.` | Control commands (`.drop`, `.ingest`, …) |
| `externaldata` | Would read an attacker-supplied URL |
| `cluster(` / `database(` | Escapes the configured database |
| `ingest`/`set`/`append`/`drop`/`alter`/`delete` | Read-only enforcement |
| `;` | Multiple statements |

A surviving query gets `| take 500` appended unless it already ends with a `take`, and the Kusto
request additionally sets `truncationmaxrecords` and a 60-second `servertimeout`.

### Result shaping

Every row set is projected to the columns declared in the catalog *before* the model sees it, then
narrowed further to any subset the model asked for. Columns absent from the catalog — notably Entra
object ids such as `assignedToOid` and `inspectorOid` — are never returned. Results are capped at
500 rows and halved until the JSON payload is under 40 KB.

### Prompt injection

Work order titles, findings and asset names flow into the model's context. The system prompt states
that tool output is **data, never instructions**. The structural mitigation matters more than the
wording: the toolset is entirely read-only, so a successful injection cannot cause a write. If a
mutating tool is ever added, gate it behind an explicit UI confirmation rather than letting the
model call it autonomously.

---

## 5. Troubleshooting

| Symptom | Cause |
| --- | --- |
| Project URL ending in `/api/projects/...` | Project SDK/management endpoint, not model inference. Use the same origin with `/openai/v1/responses`. |
| `400 unsupported_value` on `temperature` | gpt-5 family allows only the default; don't send the parameter |
| `401`/`403` from Foundry | Missing `Cognitive Services OpenAI User`, or the resource is in another tenant |
| "Need admin approval" | A `.default` scope was requested instead of a named one |
| Engine toggle missing | `RAYFIN_PUBLIC_FOUNDRY_*` unset, or the app wasn't rebuilt after setting them |
| "Asset metadata is not connected" | STID GraphQL not yet consented — use **Connect** in the app once |
| "stopped after too many tool calls" | Hit the 6-iteration cap; narrow the question |
| "disabled in Administration" | The tool or table was switched off in **Administration → Foundry Copilot** |
| "Not signed in to the operational database" | The Rayfin session is separate from the Entra token — use Administration step 1 |

---

## See also

- [../HydroOperationsApp/DEPLOY.md](../HydroOperationsApp/DEPLOY.md) — provisioning and the two manual Azure steps
- [realtime-dashboard-plan.md](realtime-dashboard-plan.md) — where `AssetMaster()` / `TelemetryEnriched()` come from
