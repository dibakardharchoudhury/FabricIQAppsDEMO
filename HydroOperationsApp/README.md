# Hydro Operations Fabric App

Hydro Intelligence is one **Supervisor-led multi-agent experience**, with no
Direct Data Agent tab, engine selector or comparison entry. The Supervisor routes
Fabric business questions through **Sparky / Fabric IQ** to the published Fabric
Data Agent. Sparky forwards the original question once and returns the original
answer unchanged, without another LLM wrapper or answer writer. Other specialists
participate only when needed; work-order drafts still require explicit review
and approval. The hosted Responses transport consumes completion events
incrementally rather than waiting for the streaming connection to close.
The legacy MCP/browser-coordination implementation described below is not this
hosted execution path.

A React + Leaflet/MapLibre + [Rayfin](https://www.npmjs.com/package/@microsoft/rayfin-cli) single‑page app
that runs **inside Microsoft Fabric** and gives a hydropower operations team one screen composing
**independent Fabric data stores** — plus an in‑browser 3D digital‑twin viewer.

> **To deploy, follow [DEPLOY.md](DEPLOY.md).** This README covers the architecture and data model.
> The local launcher separates Fabric/energy source setup, Foundry multi-agent
> provisioning, and final app publication. The hosted Administration page manages
> source connections and refreshes; workspace setup owns operational seeding.
> It is not a Foundry deployment interface.
> **Agent behavior and acceptance:** see [AGENT-ACCEPTANCE.md](AGENT-ACCEPTANCE.md) for the
> complete routing policy, actual tools/runtime, scientific RCA criteria, proposed enterprise
> specialists, and ten multi-turn test scenarios with verified versus pending outcomes.
> **Try it yourself:** [ten copy-and-paste prompts](AGENT-TEST-PROMPTS.md).
> **End-to-end work record:** [Foundry and multi-agent implementation, October 7-8](../docs/foundry-multi-agent-implementation.md)
> separates deployed behavior, local Agent Framework work, test evidence and pending migration.
> **Durable orchestration work:** [local Agent Framework validation](../HydroOperationsService/README.md)
> is isolated and is not yet the production backend.
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

Administration step 6 is the single entry point for Norwegian Energy Map preparation
and maintenance in both layouts. Once prepared, its action becomes **Refresh snapshots**.
Running progress appears above Guided Setup in the same full-width banner stack as
telemetry and weather, with the live Fabric status and elapsed time. The map bar is
indeterminate because Fabric does not report a completion percentage. Completion
and failures stay visible in the step; source details and the exceptional
full national-source rebuild are collapsed under **Maintenance and source details**.
There is no separate map-maintenance card.
Successful weather ingestion completes quietly without a global alert banner;
weather failures and sign-in requirements remain visible.

| Store | Owns | Accessed via |
|---|---|---|
| **Lakehouse (STID/weather)** | Engineering master data and weather serving tables | Read-only delegated Rayfin `hydrolakehouse` connector (`fabric-sqlanalytics`) |
| **Eventhouse (telemetry)** | `OPCUAEvents(event_time, opcua_node_id, value, quality)` | KQL query |
| **Independent Fabric SQL (`RTI_Demo_SQLDatabase_<env_suffix>`)** | Work orders, notifications, inspections, spare parts, 3D models | Writable delegated Rayfin `hydrooperations` connector (`fabric-sqldatabase`) |
| **GeoContext Lakehouse (optional)** | Imported public energy features, boundaries, revision-qualified links and source-health snapshots | Read-only delegated Rayfin `hydrogeo` connector (`fabric-sqlanalytics`) |

The stores are **never merged server‑side** — the app queries each independently and joins in the
browser by `equipmentId` / `instrumentId` / `opcuaNodeId`, so every panel shows its source. Demo
data is synthetic but each record lives where it would in production (no reference or telemetry rows
are copied into operational SQL).

Lakehouse STID and Eventhouse telemetry are live-only browser state. The app does not persist either
source in local storage, removes legacy STID/telemetry browser caches on startup, and clears the
corresponding UI data when a refresh fails or the source is unavailable. Operational SQL remains
independent and can continue serving operational records while Fabric analytical sources are rebuilt.
Within the active SPA, the shared data provider and memoized projections reuse STID and telemetry
across the map, Knowledge Graph, and digital twin. Weather uses a five-minute, source-keyed in-memory
cache with concurrent-request coalescing, forced-refresh bypass, and failure invalidation. Ontology
definitions/native graph results use their own bounded, source-keyed in-memory cache. None of these
analytical caches survive a full page reload.

On a full reload, STID and Weather use the deployed Rayfin Lakehouse connector with the signed-in
user's Fabric identity. Its workspace and SQL analytics endpoint ID are rebound from live target
metadata by the canonical deployer. Every entity is keyless and relationship-free because SQL
discovery supplies no PK/FK metadata; no keys are inferred from names or sample values. All pages
are read before returning a complete inventory. The browser ships scalar selections, never server
decorator classes. Telemetry uses the verified Eventhouse query URI and KQL database.
The live connector/KQL requests still determine whether those sources exist and are
queryable: missing, deleted, or failed sources clear the corresponding UI data rather than displaying
a persisted snapshot.

NB11 does not provision a separate GraphQL API. SPA and hosted reads use the source-bound
Lakehouse and independent SQL connectors; `Hydro_STID_API` is retired.
The retained AppBackend-managed SQL/GraphQL children are not the active operational store.
The deployer applies and checks all three source connectors, including each Lakehouse's
distinct SQL analytics endpoint. GeoContext reads do not fall back to Eventhouse.
External GeoContext aliases remain available for the optional Fabric map Data Agent;
they are not the browser map's transport.
This does not change Eventhouse telemetry/live frequency or ontology-owned native graph topology.
The connector reads SQL-visible tables, not OneLake files,
and is unrelated to ontology generation. See [Rayfin connectors](https://microsoft.github.io/rayfin/docs/guide/cli/connectors/).

The Entra SPA is retained for direct Eventhouse access, Fabric jobs/ontology/Data Agent
calls and the Foundry supervisor's delegated-user checks. Connector migration does
not replace those token flows. Setup remains automatic; no standalone STID GraphQL
API or separate legacy GraphQL setup is required. Rayfin's own GraphQL-backed
connector transport and its delegated permission remain required.

The initial HTML stays small and references browser-compressible, cacheable application JavaScript
and CSS. Each layout loads only the selected navigation page; graph, map, telemetry embed,
and chat rendering libraries are deferred until their page needs them. The Data Agent MCP
client/transport is loaded only when establishing an agent session, not on application startup.
Page loading has a visible fallback and an explicit reload action on failure. The
interactive Digital Twin model remains lazy-loaded when a selected asset supports it, then renders
automatically without an additional operator action. The bootstrap uses local system fonts and does
not wait on a third-party font stylesheet.

The **Knowledge Graph** defaults to an **Ontology model**: one node per entity type and
one arrow per declared relationship, read directly from the live generation-2 definition.
It does not require graph materialization, execute source-row joins, or claim to show individual
assets. Relationship endpoint identities and directions are preserved. Components are packed
separately, genuinely unrelated types remain visible in a compact group, and search filters the
type list rather than cutting connections out of the diagram. The inspector shows property
definitions, declared keys and relationship cardinalities. Schema export uses JSON.
The model reuses the rich graph canvas: sized, icon/color-coded business types, visible
relationship labels and direction, network/hierarchy/concentric layouts, zoom, fit, selected-neighborhood
focus and an expanded view (Escape restores it). The category legend describes presentation only,
not inferred relationships or operational health. **Neighborhood** shows only declarations incident
to the selected type; **All types** restores the full definition. Schema nodes have no telemetry,
health rings or animated operational flows. Equipment-type and spare-part categories do not imply
installed parts or verified compatibility.

For a browser-only presentation check without Fabric access, run
`node scripts\knowledge-graph-preview.mjs` from `HydroOperationsApp` and open
`http://127.0.0.1:5189/ontology-preview`. This development-only server mounts the real model page
with explicitly declared synthetic schema inputs and a conspicuous fixture banner; it is not
part of the application bundle or a live-read certification. The page links exercise light/dark
themes, unrelated types and a failed read. Stop it with Ctrl+C after checking.
To inspect an exact saved readback instead, run
`node scripts\knowledge-graph-preview.mjs --definition "C:\path\readback.json"`.
Add `--port 5193` to select another localhost port (default `5189`; occupied ports fail rather
than silently switching).
The envelope must contain `metadata` (item GUID, display name, numeric
`properties.generation: 2`) and `definition.parts`. The preview uses the application’s actual
TMDL parser and topology validation, rejects conflicting identities, and does not copy the file
into the repository. Saved mode disables the synthetic isolates override. This remains an
offline preview, not independent verification of live generation, materialization or agent runtime.

The separate **Native asset instances** view visualizes operational composition as a scoped property graph. Its performant
Cytoscape 2D analysis view provides hierarchy, force-directed, and concentric layouts without a
WebGL dependency. It defaults to the selected turbine and synchronizes that selection with Overview,
Real-Time Telemetry, Digital Twin, and Maintenance. It requires a verified
**generation-2 Ontology** and an explicitly bound, materialized ontology-owned
GraphModel. Source-reconstructed topology is not a canvas fallback. SQL and Eventhouse
only enrich actual native entities. Native header counts describe returned instances and links;
advanced details disclose visible connected components and isolates. Selecting an asset
in the tree switches to its selected scope. Native exports use OWL/RDF Turtle.
Missing materialization, source eligibility, access, valid identities or complete reads
fail closed. FEATURE currently has an unresolved portal **Unsupported Datasource**
eligibility blocker; successful hosting does not certify native graph availability.
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
  Only actual native graph instances supply topology; declared binding keys and STID rows
  do not synthesize missing entities or relationships.
- Primitive, `Any`, `TimeSeries<T>`, and single-line `complexDataType` JSON metadata are
  retained, including `additionalBackingTable` references for Eventhouse time-series bindings.
  Complex values and recursive `valueBackingConfiguration` metadata are **not evaluated**.
  Resource links, rules, metrics, and junction-table edges are not executed/rendered; the UI
  reports these limitations. Composite keys, multiline expressions, unknown entity/property
  constructs, invalid payloads, unresolved endpoints, and mixed v1/v2 definitions produce errors
  rather than a silently empty contract. The parser is bounded, not a general TOM engine.
- A v2 materialized GraphModel is required for the graph's **canvas, tree and scopes**.
  Complete **Manage graph → select eligible entities/relationships → Continue → Materialize**
  in the selected Ontology. Item creation, child-definition readback and a queryable schema
  do not prove entity eligibility or populated native topology.
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
  ontology-owned materialization API. Keep portal materialization separate from automated
  deployment. After eligible static entities are materialized, `event_time`, `value`, and `quality`
  remain Eventhouse-bound enrichment rather than fabricated native entities.
  Native GQL reads use GET `getQueryableGraphType?beta=true` and POST `executeQuery?beta=true`
  on the configured `graphModels/{id}`. Opaque string `result.nextPage` continuations are followed.
  Query errors, warnings, truncation, malformed/dangling results, or exceeding 2,000 nodes/4,000 edges
  fail explicitly. Malformed native results are never reinterpreted as valid native topology.
- Ontology definitions and native results are cached and concurrent requests are coalesced.
  The page shows a loading state while native queries run and an explicit unavailable state
  after native failures; no STID compatibility graph is rendered. Definition errors also prevent
  unverified topology. Workspace refreshes
  restart obsolete reads against current discovery rather than surfacing cache-invalidation errors.
  Manual refresh queries the native source independently of STID connectivity.
  The five-minute native poll does not repeatedly download the TMDL definition.

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
A missing published Data Agent raises an actionable error. For each active workspace/agent/Ontology
configuration the app reads the candidate's real `getDefinition` response and verifies **published** ontology datasource
`artifactId` and `workspaceId` against the selected, verified generation-2 Ontology. Draft sources,
agent names, and notebook completion are insufficient. A different/legacy ontology source, an
unreadable or malformed definition, or no matching published source blocks invocation explicitly.
Invocation uses Fabric's published Data Agent MCP endpoint over the documented Streamable HTTP
transport. Fabric's published MCP tool accepts only one `userQuestion` and is stateless across
calls, even on a reused transport. Standalone questions are sent unchanged. For explicitly
referential follow-ups such as `this asset`, `same signal`, or a leading `or`, the app includes at
most four recent user questions in a bounded context envelope; it never replays the potentially
large agent answers or makes an additional summarization call. Starting a new chat clears the
bounded user-question context but retains the reusable MCP transport and tool metadata. Transport
reuse does not create semantic memory or carry a previous answer into a standalone question.
Global all-facilities/all-assets scope
and cross-source join rules live in the published Data Agent instructions rather than being
prepended by the web app. The app never injects the selected facility/asset.
Follow-up questions reuse the authenticated MCP connection and discovered tool while the endpoint
and access token remain unchanged. Workspace refreshes, token/endpoint changes, or MCP failures
invalidate that session. SDK close notifications evict disconnected sessions, and a call that races
with a stale transport reconnects and retries once instead of exposing a raw `Not connected` error.
The verified identity is keyed to the exact workspace, Data Agent, Ontology, and live generation.
Workspace/config changes invalidate it immediately and force fresh published-definition readback.
Warm-up starts only when Hydro Intelligence opens with the Data Agent selected, so Data Agent
connection and verification work cannot delay initial application load or unrelated tabs. It also
retains discovered MCP tool metadata across transport reconnects. On a cold request, source verification and MCP
connection setup run in parallel, but the tool call cannot start until verification succeeds.
The published server advertises the `io.modelcontextprotocol/tasks` extension, but live simple and
visual task-creation probes did not return `taskCreated`, persisted no task in `tasks/list`, and
timed out. The production app therefore retains the proven synchronous `tools/call` path instead
of enabling a capability whose live execution is inconclusive.
No app-authored scope reset or source-routing hint is injected into user prompts. When the Data
Agent returns plot-ready fenced CSV, the app converts it directly into the existing local chart
model. Multi-signal CSV containing `opcua_node_id` is rendered as grouped series. This adds no
model call or data query and therefore does not add network latency.
Fabric-rendered visual images are currently limited to the native Data Agent experience. NB09
therefore keeps visual semantics in the agent instructions and requires no more than the documented
200 plot rows so external clients can reconstruct the chart without adding source-routing or join
instructions to the user question.
The regular chat hides Data Agent internal progress and uses the Signal Sprint thinking animation.
Fabric can batch lifecycle notifications near completion
and exposes internal tool names/statuses, not generated SQL, arguments, intermediate rows, or
execution duration. Those steps say **status received**, including in copied transcripts; notification
counts are not query counts. Repeated/out-of-order notifications cannot turn a duration into an
epoch timestamp. Foundry steps have measured client execution durations and actual arguments/results.
The remote Data Agent owns its internal orchestration. Experimental MCP task
invocation was not enabled because the live Fabric endpoint
either rejected the SDK's automatic task shape or timed out with explicit task parameters.
Verification uses the actual `Files/Config/published/{source}/datasource.json` parts and the
`type: ontology`, `artifactId`, and `workspaceId` fields used by the notebook publishers.
Matching published identity plus the selected item's authoritative live generation `2` proves
**configured source identity only**, not runtime readiness. It permits the real MCP attempt;
backend/product failures propagate unchanged, and onboarding may still be unavailable.
The public datasource schema's enum omission is not a permanent execution gate. No name/latest-version
heuristic or generation-only check bypasses source verification. Reading the published definition
requires appropriate Data Agent read permissions; read failures remain errors. The app does not
invoke or certify an Operations Agent playbook.

Battle of the Agents has been removed, including its Administration option, comparison page,
runner and styles. Old browser settings cannot enable it. Hydro Intelligence is the single
Supervisor-led chat experience. Categorical chart axes display actual asset labels, not row indices.

NB09 owns the Data Agent's published count/join instructions, including explicit transfer of actual
SQL count tuples into an isolated Lakehouse analyzer request. Open orders exclude `Completed` and
`Cancelled`; `completedAt` is not authoritative. SQL performs facility aggregation rather than
asking the model to manually count rows. Foundry's shipped default instructions use the same open
definition; only saved prompts exactly matching the old shipped default are upgraded, preserving
operator-authored prompts. No operational predicate or routing rule is injected by the chat UI.
Foundry's instructions distinguish full equipment IDs from short tags and use a single raw
Eventhouse `arg_max` query for unbounded latest-per-signal requests, retaining explicitly requested
time windows and reporting the actual timestamp/staleness.

Foundry chat uses five separately provisioned, versioned Prompt Agents. The persistent Supervisor
chooses Q&A, RCA, Work Order, or Fabric IQ using a delegation function. The client invokes the selected
agent by its Foundry identity and executes its allowed direct tools as the signed-in user. This is
code-mediated agent delegation, not local role-prompt switching, not native server-to-server A2A,
and not Foundry Workflows. Microsoft Agent Framework is not required for this client-coordinated
design; a hosted Framework coordinator would need a separate delegated tool/authentication boundary.
Browser coordination is intended for user-attended requests, not durable enterprise workflows:
closing/reloading the tab interrupts coordination and loses in-memory approval state. Durable
resumption, unattended jobs, centrally enforced orchestration policy, and durable approvals require
server-side implementation; adding a Framework library alone does not provide those guarantees.
Q&A and RCA retain direct Lakehouse, Eventhouse, and operational SQL tools. The Fabric IQ
specialist uses `fabric_iq_preview` with separate Data Agent and Ontology connections; the
deprecated `fabric_dataagent_preview` tool is not used. Data Agent direct-table queries do not
depend on its currently limited ontology query runtime. The independent direct Ontology endpoint
requires its own runtime acceptance; configuration is not proof that it executes successfully.
The trace records real Foundry response IDs and agent identities; it does not expose hidden reasoning.

Hydro Intelligence in both layouts now starts with the Supervisor, regardless of an old saved
engine preference. There is no separate Data Agent selector in ordinary chat; explicit Data Agent
requests route through the Fabric IQ specialist. Missing Foundry configuration
fails explicitly instead of silently changing engines.

The animated crew reuses the existing yellow Signal Sprint helpers. Real parent/call IDs drive
Supervisor-to-specialist request packets and matching return/failure packets, with distinct working
motions per role. Packets play once for recent recorded handoffs, not continuously while waiting
or when reopening old results. The activity feed distinguishes browser tool receipts from Foundry
events. Pause and reduced-motion controls preserve all textual status and diagnostics.

Authentication reuses the signed-in user's delegated identity and silent token cache. The current
Foundry Agent Service audience replaces the obsolete Cognitive Services inference audience in
deployment requirements. Fabric IQ source-definition verification requests ordinary Fabric read
scopes, not Data Agent execution consent. Native Data Agent execution requests
`DataAgent.Execute.All`. Setup bundles the required scopes in one idempotent provisioning step;
tenant policy may require an administrator to consent. Existing grants are not automatically revoked,
and consent does not replace resource access/RBAC or MFA.

Work-order requests use the Foundry approval flow. A structured draft appears
in an editable card with Yes/No buttons. Equipment and signal identity cannot be changed through
the card; request a new draft for a different target. Approval revalidates target identity and
checks duplicate open titles before the SQL write. Drafts expire after 30 minutes and reset clears
them. A failed write response is marked uncertain and is not retried automatically. A successful
write displays the returned work-order number and refreshes the operational views.

Draft cards appear first; duplicate prose is collapsed under **Supporting findings and
sources**. Short answers, material limitations and requested results take precedence over
repeated tool explanations. Supporting evidence remains expandable and in transcript
exports. The animated crew uses a compact strip by default, with an optional resizable
desktop side panel (bounded-height on mobile), rather than taking over the answer area.
These latest source changes require the hosted acceptance gate documented in
[AGENT-ACCEPTANCE.md](AGENT-ACCEPTANCE.md).

Chat uses the shared presentation contract and evidence dashboard. Charts
are derived from the same returned table/CSV rows, without a top-16 UI cap or treating empty values
as zero. Different measures get separate charts. This is presentation consistency, not a guarantee
that two independent query planners return identical facts; live quality acceptance must compare
the same population, time window, exact key set and counts against authoritative source results.
For current signal-quality questions, Foundry's structured quality-snapshot tool selects the latest
row per node before applying the quality filter, resolves only the matching nodes, and retrieves
their open equipment work concurrently. This avoids both implicit result caps and the previous
multi-round fleet inventory plan.

See the [five-prompt investigation](../docs/data-agent-comparison.md) for measured results,
source-truth checks, benchmark boundaries, and remaining browser/quality acceptance gaps.

Reference: [Microsoft Ontology (new) definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/ontology-definition).
Regression coverage: `npm run test:knowledge-graph` (real-shaped TMDL, legacy rejection, caches,
and LRO responses), plus `node --import tsx --test scripts/artifact-discovery.test.mjs`,
`scripts/supervisor-entrypoint.test.mjs`, and `scripts/copilot-settings.test.ts`
(published source verification and honest setup readiness), plus
`scripts/copilot-orchestration.test.ts` and `scripts/answer-presentation.test.ts`
(query semantics, persisted agent definitions, full datasets and approval safety).

## Architecture

```
              ┌────────────────────────┐
              │  Hydro Operations SPA  │  (React + Leaflet, hosted by Rayfin in Fabric)
              └──────┬─────────┬───────┬┘
      TMDL/connector│   KQL   │       │  hydrooperations connector
                     ▼         ▼       ▼
      ┌────────────────┐ ┌──────────┐ ┌───────────────────────────┐
      │Ontology v2 +   │ │Eventhouse│ │ RTI_Demo_SQLDatabase_*    │
      │Lakehouse rows  │ │OPCUAEvents│ │  WorkOrders, Inspections, │
      └────────────────┘ └──────────┘ │  SpareParts, Asset3DModels│
        built by RTI_001…010          │  MaintenanceNotifications │
        (see root README)             └───────────────────────────┘
```

- **Lakehouse + Eventhouse** are produced by the RTI notebooks / `Pipe_Setup` — see the [root README](../README.md).
- **Independent operational SQL** is created/reused by NB01 and initialized without demo
  rows. NB11 performs the verified existing-data cutover and Data Agent source extension.
  The SPA and Hosted Agent use the `hydrooperations` connector, not the root Rayfin data
  client. The original SQL item is a Fabric App child item, not the active operational
  store; replacing the operational source does not establish that the platform-managed
  child can be deleted. See [the SQL lifecycle contract](DEPLOY.md#independent-operational-sql-lifecycle).
- **Agent provisioning is always attempted; there are no agent mode or skip flags.** RTI_010
  retains the ontology-backed Operations Agent, its playbook/actions, Teams, and
  `Pipe_SendEmailAlert` provisioning against the verified v2 ontology. The retired
  Eventhouse-Knowledge agent and its settings are removed with identity/deletion readback checks;
  shared telemetry, SQL/KQL grounding, and alert dependencies remain. Real failures propagate
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

## Independent operational SQL data model

The shared-store schema is defined in
[`operational_store_support.py`](../Raw/workspace-reset/operational_store_support.py).
These five tables are exposed through the delegated `hydrooperations` connector.
[`rayfin/data/schema.ts`](rayfin/data/schema.ts) retains the legacy app-owned entity
definitions; it is not the app's current operational read/write target.

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
- **Existing operational data** (12 work orders, 6 notifications, 30 inspections, 12 spare parts,
  15 3D models) was copied into the independent store with exact field/ID verification.
  NB01 provisions `RTI_Demo_SQLDatabase_<env_suffix>` inside `RTI_DEMO_<env_suffix>`;
  setup Stage 2 automatically runs NB11 to insert missing demo rows and publish the
  SQL Data Agent source. Existing values are never overwritten. Normal app deployment
  does not reseed. The hosted Administration page has no duplicate seed action;
  the reference SQL is
  [`sql/seed-operational-data.sql`](sql/seed-operational-data.sql).
  Fabric rejects renaming existing SQL databases: FEATURE uses the verified replacement
  `RTI_Demo_SQLDatabase_V11` in `RTI_DEMO_V11`, retaining `HydroOperationalStore`
  as a write-blocked archive. Active-source discovery excludes only an archive whose
  marker identifies that exact replacement;
  see the [SQL lifecycle contract](DEPLOY.md#independent-operational-sql-lifecycle).

## Local development

All commands run from `HydroOperationsApp/` on **Node 24**. On a machine whose default Node differs,
prefix any command with `npx -y -p node@24 -c "…"`.

```powershell
npm ci            # fresh checkout or changed lockfile: restore pinned packages
npm run typecheck # tsc --noEmit
npm run lint      # eslint
npm run test:knowledge-graph # v2 contract, projection, discovery, cache and LRO regressions
npm run validate-env # required before a build
npm run build     # production build (rayfin env auto‑injected via prebuild)
npm run dev       # dev server
```

Required environment values must pass validation before the dev server/build starts.
Configured sources that are unavailable show explicit disconnected/error states; the app
never fabricates data. Local development uses the existing verified backend and
`http://localhost:5173` redirect configured by live-auth setup. It is not a second deployment
flow: use the [one-shot command or local deployment UI](DEPLOY.md#start-here-choose-your-interface-not-a-different-deployment-process)
to publish. Never hand-edit generated environment or deployment state.
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
    ├── App.tsx                     # Legacy composed operations view + facility selector
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
