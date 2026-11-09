# Hydro Operations Fabric App

The shared build also hosts **Concord**, a separate reusable master-data stewardship
experience at `/master-data.html`. See the [Concord guide](../MasterDataManagement/README.md)
for column-overlap review, ontology mappings, merge rules and governed Silver-to-Gold
execution. It does not add Hydro navigation tabs or change existing source/agent provisioning.

Hydro Intelligence has the same three accessible tabs in `ui=v1` and `ui=v2`:

- **Fabric IQ** invokes the verified published Fabric Data Agent directly as the signed-in
  delegated user, without Foundry, Supervisor, an answer writer, or prompt rewriting.
- **Multi Agent** (default) retains the governed Foundry request and human-approval flow.
- **Battle of the Agents** sends the identical question to separate Direct and Multi Agent
  conversations, showing measured durations, citations and failures independently. It is
  comparison-only: no approval/rejection/creation controls, judge, winner or silent fallback.

Histories and Multi Agent continuation IDs stay in memory and are isolated by mode. Direct
MCP questions are independent; its visible history is not sent as conversational context. Switching tabs
does not submit requests. Authoritative workspace/source/user changes clear histories and
invalidate approvals; cancellation stops local waiting, not necessarily remote execution.
No starter/sample prompts are inserted. Optional backend workflow summaries remain visible;
full unchanged native answers, their verified SHA-256 digests and RCA detail expand separately.
The direct answer itself is never summarized, truncated or replaced.

A React + Leaflet/MapLibre + [Rayfin](https://www.npmjs.com/package/@microsoft/rayfin-cli) single‑page app
that runs **inside Microsoft Fabric** and gives a hydropower operations team one screen composing
**independent Fabric data stores** — plus an in‑browser 3D digital‑twin viewer.

## Catchment terrain (Rasputin)

**Catchment Terrain** (`tab=terrain`) is available in both layouts, independently of
the optional Norwegian Energy Map. It displays one locally imported, pre-generated
[Rasputin](https://github.com/expertanalytics/rasputin) surface mesh. Orbit, zoom,
pan, reset-view and wireframe controls work without a Fabric source connection.
Elevation colours are relative to the imported mesh, with a 1:1 vertical scale.

Generate the mesh outside the app with Rasputin's Python/C++ toolchain:

```sh
rasputin mesh --dem terrain.tif --domain catchment.geojson --tolerance 5 --out catchment.ply
```

Use your actual input paths and projected output CRS; for a geographic DEM,
specify a supported UTM `--out-crs`. Rasputin is installed from source, requires
Python 3.12+ and a C++20 compiler, and does not support Windows natively; see its
[installation guide](https://github.com/expertanalytics/rasputin/blob/master/INSTALL.md).
Pin a reviewed Rasputin revision for reproducible exports.

Create a matching JSON sidecar (this is the viewer's metadata contract, not a
native Rasputin run record). Replace **every example value** with actual
provenance and compute `meshSha256` from the exact complete PLY bytes:
`shasum -a 256 catchment.ply` on macOS, `sha256sum catchment.ply` on Linux, or
`Get-FileHash catchment.ply -Algorithm SHA256` in PowerShell.

```json
{
  "schemaVersion": 1,
  "catchmentName": "Your catchment name",
  "crs": "EPSG:25833",
  "elevationUnit": "m",
  "verticalDatum": "Your DEM's documented vertical datum",
  "source": "Your DEM dataset, version and acquisition date",
  "attribution": "Required source credits and data licence",
  "generatedAt": "2026-11-05T10:00:00Z",
  "meshSha256": "<replace with the mesh file's 64-character SHA-256>",
  "toleranceMetres": 5
}
```

Select the surface `.ply` and sidecar `.json`, then **Import terrain**. The app
verifies their SHA-256 match and validates the complete mesh in a cancellable
worker before rendering. It supports Rasputin ASCII and binary little-endian
PLY 1.0 with double x/y/z vertices and triangular faces. Limits are 32 MiB per
mesh, 16 KiB per sidecar, 500,000 vertices, 500,000 triangles, and a 60-second
processing timeout. Export a smaller domain or use Rasputin's triangle budget
for larger files. Truncated files, invalid indices, non-finite coordinates,
degenerate triangles, unsupported declarations and hash mismatches fail visibly
without retaining a previous mesh. Replacing either file or clearing the import
also clears the old view and cancels outstanding work.

Only metre-based UTM EPSG:25828-25838, EPSG:32601-32660 and EPSG:32701-32760 are
accepted. Coordinates must be easting, northing, elevation in metres. The viewer
subtracts a double-precision local origin before uploading float positions to
the GPU, preserving small coordinate differences around large UTM coordinates.
The original coordinate bounds, CRS, datum, credits and file hash remain visible.
No reprojection, basemap overlay or vertical-datum conversion is performed.

Catchment names, CRS, datum, timestamp, source, attribution and optional tolerance
are **author-supplied**, not independently certified. A matching hash proves
file pairing, not provenance or DEM accuracy. Additional face fields such as
land-cover codes, fractions and DEM height shares are consumed but not
visualized, and are explicitly listed as unsupported projections.
VTK, GeoPackage, edge-only PLY, boundary overlays and land-cover colouring are
not supported. Rasputin is MIT-licensed by Expert Analytics; its software licence
does not replace the input datasets' attribution/licensing obligations.

Imports exist only in the current page's memory and are discarded on navigation
or reload. No files are uploaded, saved in browser storage, bundled as fallback
terrain or written to operational SQL. This is reference visualization, not
live source readiness, a calibrated inflow forecast, or ontology/asset linkage.
No processing service, agent tool, Fabric notebook or deployment change is
provisioned. Run `npm run test:terrain` for the import/metadata/navigation checks.

## Deployment and architecture

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

Hydro Intelligence keeps its independent transcripts, running requests and hosted
conversation scopes in an app-lifetime, in-memory provider. Refreshing telemetry
or navigating between pages does not reset them. Data-cache invalidation is not
a conversation identity change. A different verified ontology, source or signed-in
principal still clears the conversation; New chat resets only the selected mode.
This session state is not stored in browser storage and does not survive a full
browser reload. Direct Fabric IQ retains its visible transcript but its published
MCP questions remain independent; no native continuation is invented.
Ordinary hosted Q&A accepts the backend's nullable optional `workflow_text`
without replacing its answer. Supplied non-null summaries must still be
non-empty strings; malformed presentations and native-evidence digests fail.

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

The **Knowledge Graph** is one live ontology-bound instance view, without separate schema or
operational tabs. It verifies the generation-2 definition, reads actual rows through authenticated
source connectors, and evaluates only declared relationships against their physical keys.
It preserves the entity icons, weather nodes, directional links, network/hierarchy/concentric
layouts, zoom, fit, selected-asset scope and expanded view. Eventhouse enrichment supplies actual
readings, units, quality, timestamps and freshness. Critical red and warning amber halos pulse
around affected nodes and respect reduced motion. Neither graph materialization nor a GraphModel
binding is required. Source coverage and non-atomic read timing are disclosed; invalid or failed
reads clear the graph instead of displaying invented or previous data.

For a separate schema-only development preview without Fabric access, run
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

The instance view defaults to the selected turbine and synchronizes selection with Overview,
Real-Time Telemetry, Digital Twin and Maintenance. Header counts describe returned instances and
links; advanced details disclose supported and excluded projections, read timing and the exact
contract signature. OWL/RDF Turtle exports identify source-backed instances without claiming a
native GraphModel identity. Native GQL utilities remain available internally but are not the
Knowledge Graph page's topology source or a prerequisite for its canvas.
See the canonical
[`Knowledge Graph design`](../docs/knowledge-graph.md) for implementation details, operational
scenarios, freshness behavior and superseded native-graph implementation history.

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
  Actual rows from verified source bindings and their declared joins supply instance topology.
  Names, STID rows and undeclared foreign keys do not synthesize missing entities or relationships.
- Primitive, `Any`, `TimeSeries<T>`, and single-line `complexDataType` JSON metadata are
  retained, including `additionalBackingTable` references for Eventhouse time-series bindings.
  Complex values and recursive `valueBackingConfiguration` metadata are **not evaluated**.
  Resource links, rules, metrics, and junction-table edges are not executed/rendered; the UI
  reports these limitations. Composite keys, multiline expressions, unknown entity/property
  constructs, invalid payloads, unresolved endpoints, and mixed v1/v2 definitions produce errors
  rather than a silently empty contract. The parser is bounded, not a general TOM engine.
- The graph's **canvas, tree and scopes** use verified source-backed instances; materialization
  is not required. Optional native GQL utilities separately require an explicitly bound,
  ontology-owned GraphModel materialized through the selected ontology's Manage graph flow.
  For those utilities only, configure the `.env` binding with single-quoted JSON:
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
- Ontology definitions and source-backed results use bounded, identity-keyed in-memory caches
  and concurrent reads are coalesced. The page shows loading and explicit unavailable states;
  failed reads clear the instance graph. Definition errors also prevent unverified topology.
  Workspace refreshes restart obsolete reads against current discovery. Manual refresh and
  five-minute source polling work independently of STID connectivity. Work-order mutations
  invalidate the source snapshot immediately.

These read capabilities do **not** imply every Data Agent semantic query or Operations Agent playbook
execution succeeds. An earlier live attempt accepted source publication and stopped-agent
configuration, but its ontology MCP question failed inside `analyze_ontology` with an
unsupported API version despite `isError: false`. A later first question through the official
published MCP endpoint succeeded against the verified ontology-backed agent and returned three
facilities with a CSV resource, as detailed below. This does not certify every cross-source
question or resolve the separate playbook-runtime issue.
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
Direct execution uses the official published MCP endpoint with existing delegated Fabric
token acquisition. It selects exactly one agent
whose **published** definition matches the selected live numeric generation-2 ontology, not
an agent name or a sole-item guess. It does not read a Foundry connection or require Foundry
configuration. Zero or multiple matching agents are an explicit configuration blocker.

Each request initializes a fresh MCP client, discovers the single tool and question argument,
then sends that argument the exact operator question. No selected facility, context envelope,
clock hint or answer-format instruction is prepended. The native text is retained unchanged;
embedded result resources are downloadable with their original bytes and MIME types, and a
downloadable native MCP envelope preserves returned resource/citation metadata. The answer's
SHA-256 is displayed. The operation has a five-minute deadline, bounded evidence/artifacts,
local cancellation and no automatic question replay. Provider errors, invalid identities and
artifact failures fail closed.

**Continuation limitation:** the published tool currently declares only `userQuestion: string`,
not `previous_response_id` or `conversation`. Direct supports first and subsequent independent
questions, but does not claim that a referential follow-up inherits earlier context. This is
disclosed above the Direct transcript and in Battle. No context wrapper, hidden answer writer
or undeclared continuation argument is substituted.

Browser prerequisites are delegated Fabric `Workspace.Read.All`, `Item.Read.All`,
`DataAgent.Execute.All`, published Data Agent access, and CORS support from Fabric discovery
and the official published MCP endpoint to the current app origin. Direct MCP does not request
Power BI, Foundry, regional routing or generated workload-endpoint access.
Missing consent/RBAC or browser CORS/network support is surfaced as a real blocker, never
worked around through Foundry and labelled Direct. Live hosting/browser acceptance remains
necessary; unit tests do not certify these product/tenant prerequisites. Entra token caching
for this SPA uses MSAL `sessionStorage`, scoped to the app origin and browser tab, so returning
to the Fabric app reuses the selected tenant account instead of reopening the account picker.
Closing the tab ends that cache; expired sessions, revoked consent or tenant policy can still
require explicit sign-in. Only authentication state is cached there, never analytical data.
The older `askDataAgent` helper remains for
other callers but its prompt rewriting is not used by these Hydro Intelligence modes.

Direct MCP diagnostics identify initialization, tool discovery or question execution.
HTTP failures retain their status and SDK cause; cancellation remains cancellation.
A fetch failure before an HTTP response is not proof of CORS rejection: connection,
browser policy and redirect failures require separate investigation. Requests are
not automatically replayed, and the repair adds no new request headers.
The injected fetch function is bound to the browser global object so native
`Window.fetch` is never called with the conversation instance as its receiver.

Mode selection is a compact right-side dropdown beside Team, not a separate tab bar.
Administration's **Agent runtime settings > Chat mode visibility** checkboxes control the
Fabric IQ, Multi Agent and Battle choices. Save changes applies them across both app layouts
and preserves them in the existing per-browser settings. Hiding the active mode selects an
enabled mode (preferring Multi Agent); hiding all modes displays an explicit disabled message.
These are presentation preferences, not agent-provisioning flags or access controls.
Direct notices and Battle controls use the existing app typography. The Direct
notice occupies an auto-sized grid row, leaving the flexible row for messages in
both normal and maximized chat.
While a response or comparison is running, its compact square Stop icon cancels
the local request. The tooltip and accessible label identify the action; stopping
does not certify that remote execution has ended.

**Why regional Responses is not the active browser entry:** the regional ML
`POST /metadata/v201606/generatemwctokenv2` preflight was rejected without
`Access-Control-Allow-Origin` for the deployed Fabric App origin, despite valid SPA-issued
delegated tokens and successful workspace/cluster discovery. That Responses route cannot
execute from the inspected hosted browser. More consent or a different model does not repair
provider CORS. It is not called before MCP, retried through MCP, or used as a fallback.

Microsoft's [SDK documentation](https://learn.microsoft.com/en-us/fabric/data-science/fabric-data-agent-sdk)
identifies the published [MCP endpoint](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server)
as the runtime consumption surface:
`https://api.fabric.microsoft.com/v1/mcp/workspaces/{WorkspaceId}/dataagents/{DataAgentId}/agent`.
Its documented flow is `initialize`, `tools/list`, then `tools/call` with the discovered question
argument. An MCP transport session alone does not prove native conversational continuation.
The [official Responses sample](https://github.com/microsoft/fabric-samples/blob/main/docs-samples/data-science/data-agent-sdk/responses-api/responses-api-notebook.ipynb)
demonstrates Python `previous_response_id`/`conversation`, not a browser API contract.
Inspection of SDK `0.1.34a0` confirmed headless Responses uses the same regional routing request;
notebook execution consumes Fabric runtime workload-host context, not a SPA-provided equivalent.
Read-only live MCP metadata checks from the hosted browser succeeded (`initialize` HTTP 200,
`notifications/initialized` HTTP 202, `tools/list` HTTP 200). Both listed published agents
advertised only `userQuestion: string`, no declared native continuation argument, and no
`outputSchema`. Tasks/list/cancel capabilities were advertised, but tasks are execution handles,
not proof of conversation memory. A browser-readable MCP session header was not present.
A subsequent first read-only `tools/call` against the verified ontology-backed agent succeeded
with HTTP 200 and `isError: false`, returning three actual facility IDs/names in unchanged native
text and an embedded CSV resource. This verifies that first-question route, not native
continuation or every cross-source question.

Verification recognizes the dedicated v2
`Files/Config/published/ontologies/{name}/ontology.json` part's exact `artifactId` and `workspaceId`,
as well as typed ontology `Files/Config/published/{source}/datasource.json` bindings.
The dedicated ontology record need not contain a `type` property. A SQL/custom datasource
beside that record does not make the agent SQL-only. Both formats fail closed on mismatched
identity, malformed content, draft-only bindings or a conflicting explicit type.
Matching published identity plus the selected item's authoritative live generation `2` proves
**configured source identity only**, not runtime readiness. It permits the real native attempt;
backend/product failures propagate unchanged, and onboarding may still be unavailable.
The public datasource schema's enum omission is not a permanent execution gate. No name/latest-version
heuristic or generation-only check bypasses source verification. Reading the published definition
requires appropriate Data Agent read permissions; read failures remain errors. The app does not
invoke or certify an Operations Agent playbook.

Battle is an ordinary Hydro Intelligence tab, not an Administration feature flag. It retains
independent conversations and failures, without exposing typed approval capabilities.
Categorical chart axes display actual asset labels, not row indices.

NB09 owns the Data Agent's published count/join instructions, including explicit transfer of actual
SQL count tuples into an isolated Lakehouse analyzer request. Open orders exclude `Completed` and
`Cancelled`; `completedAt` is not authoritative. SQL performs facility aggregation rather than
asking the model to manually count rows. Foundry's shipped default instructions use the same open
definition; only saved prompts exactly matching the old shipped default are upgraded, preserving
operator-authored prompts. No operational predicate or routing rule is injected by the chat UI.
Foundry's instructions distinguish full equipment IDs from short tags and use a single raw
Eventhouse `arg_max` query for unbounded latest-per-signal requests, retaining explicitly requested
time windows and reporting the actual timestamp/staleness.

Multi Agent uses the hosted governed coordinator and separately provisioned, versioned Foundry
specialists. The browser sends the operator request and delegated source credentials through
the accepted hosted transport, not local role-prompt switching. Sparky retrieves read-only native
evidence, Sleuth performs RCA, and Fixer prepares human-review drafts. The backend owns routing,
evidence validation and decision/write enforcement. Displaying a draft is not approval.
The trace records actual response IDs and agent identities without exposing hidden reasoning.
The independently selected Direct Fabric IQ tab bypasses this entire hosted workflow.

Hydro Intelligence in both layouts defaults to Multi Agent regardless of old saved engine
preferences. Fabric IQ is an explicit direct tab; Multi Agent still routes through the governed
Supervisor workflow. Missing configuration fails in the selected mode instead of changing engines.

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
  `src/ui-shared/`. `src/ui-shared/knowledgeGraphModel.ts` enriches verified ontology-source topology and
  `src/ui-shared/pages/KnowledgeGraphPage.tsx` owns scope, filtering, shared asset selection, and the
  entity inspector.
  Layout names such as V1/V2 do not enable Ontology v1; both layouts use the same v2-only services.
