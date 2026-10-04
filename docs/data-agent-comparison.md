# Data Agent and Foundry: five-prompt investigation

Date: October 4, 2026. Workspace: `ws-vteam-demoV3`.

## Outcome and limits

The operational-count error and chart/timer defects were reproduced and corrected.
The published Data Agent's facility-count query improved from an incorrect **41.8 s**
response to three consecutive correct responses in **36.0, 33.3, and 34.9 s**
(mean **34.7 s**, approximately **17% faster** than that baseline).
Two subsequent independent service/protocol checks also returned the correct counts.

**Full latency parity with the native Fabric UI is not established.** The final
five-prompt results below still show a gap, particularly on the three-source question.
These are small sequential samples, not p95 measurements or a reliability SLA.

The authenticated hosted shell and running Battle timer were exercised. However,
the hosted Data Agent request then waited for the separate Entra delegated-permission
popup before any MCP tool step appeared. Reloading cancelled that flow and produced
`user_cancelled`. That wait is **not** a measured Data Agent execution.
Consequently, the service measurements below are explicitly **not authenticated
hosted-browser end-to-end timings**.

## Measurement boundaries

All five prompts were sent unchanged. Runs were sequential, with conversations reset
between prompts. No extra planner, schema change, source-routing prefix, or ontology
source removal was introduced.

| Path | What was actually measured |
|---|---|
| Native Fabric UI | Actual shared Fabric UI, cleared chat for each prompt. Both send-to-answer wall time and Fabric's displayed response time were captured. |
| App Data Agent service | The actual application `askDataAgent`, verification, MCP SDK, context, and chart-extraction code, executed from Node. Authentication used the same tenant user's Azure CLI token instead of browser MSAL. This is not a hosted-browser test. |
| Direct published MCP | The supported published Streamable HTTP endpoint, independent of the application service. |
| Foundry mini service | The actual application Foundry Responses loop, current default instructions, tool definitions, filtering, and chart construction, using `gpt-5-mini`. GraphQL and KQL used the real application service functions. Operational rows came from a delegated direct-SQL adapter instead of AppBackend/Rayfin authentication. This is not an identical browser transport benchmark. |

CLI token acquisition was completed before timed prompts. App-service MCP warm-up,
including source verification, took **16.1 s** separately; direct protocol setup took
**7.4 s** separately. Neither setup duration is included in the per-prompt columns.
The app already restricts warm-up to Hydro Intelligence, not application startup.

The native UI used the draft configuration after reload. Draft and published NB09
instructions and Preview Runtime flags were read back and matched. Native UI and
published MCP are nevertheless different execution surfaces; matching configuration
does not prove identical internal orchestration.

## Final five-prompt results

Seconds, rounded to one decimal:

| Prompt | Native UI wall time | Fabric displayed time | App Data Agent service | Direct MCP | Foundry mini service |
|---|---:|---:|---:|---:|---:|
| Facility/type/country/asset count | 18.0 | 15 | 26.8 | 25.7 | 13.6 |
| Latest T004 power and quality | 34.6 | 30 | 40.3 | 37.6 | 19.3 |
| Facility assets and open work orders | 35.3 | 31 | 34.6 | 31.5 | 26.0 |
| T003 signals, units, latest values/quality, open orders | 27.4 | 24 | 40.8 | 51.0 | 20.8 |
| Ranked open orders with chart | 37.1 | 33 | 31.6 | 36.3 | 21.5 |
| **Mean** | **30.5** | **26.6** | **34.8** | **36.4** | **20.2** |

Do not compare Fabric's displayed server time directly with an end-to-end browser
timer, or treat the Foundry SQL adapter as a measured AppBackend path.

Exact prompts:

1. `Summarize the facilities with their type, country, and number of assets.`
2. `What is the latest power output and quality for T004?`
3. `Summarize each facility with its number of assets and open work orders.`
4. `For EQUIP_RTI_T003, list every signal with its unit, latest value, latest quality, and the total number of open work orders for the equipment.`
5. `Which assets have the most open work orders? Give a ranked table and chart.`

The fifth prompt differs from the older six-signal T005 report: it now covers the
ranked-work-order chart defect reported in Battle. The older measurements should
not be substituted into this table.

## Independent correctness checks

Direct SQL and KQL reads, plus the Lakehouse equipment/facility inventory, established:

- Three facilities, five assets each, 15 assets total.
- 13 work orders, of which two are `Completed`; **11 open**.
- Both completed rows have null `completedAt`. Filtering on that date yields the
  incorrect total of 13.
- Open orders: **Sloy 3, Foyers 4, Pitlochry 4**.
- T008 has two open orders; nine other affected assets each have one.
- Latest T004 power: **2037.692 MW, GOOD**, at
  `2026-10-03T10:14:49.346737Z`.
- T003 has one open order, and the following latest signal values:

| Signal | Value | Quality |
|---|---:|---|
| inlet_pressure | 75.281 | GOOD |
| power_output | 2263.852 | GOOD |
| turbine_speed | 1097.132 | UNCERTAIN |
| turbine_temp | 73.148 | GOOD |
| vibration_a | 1.234 | BAD |
| vibration_d | 2.827 | GOOD |

These are historical latest readings, not evidence of a currently running stream.

All five final primary numeric/source-value checks passed for the application Data
Agent service and corrected Foundry service. The ranked chart used the same counts
and asset labels as its table. This is not a blanket answer-quality certification:

- A separate direct-MCP run numbered equal counts sequentially instead of giving
  tied ranks.
- An application-service answer added a `Type` column containing facility type in
  an asset ranking. The requested counts were correct, but that extra column is
  potentially misleading.
- Formatting and explicit staleness wording still vary between runs.

## Root causes and changes

### Operational joins and unnecessary work

NB09 now owns a grouped SQL count query with the overall total. Open means status
not `Completed` or `Cancelled`; Draft remains open. Facility totals are computed
relationally rather than by asking the model to count prose rows.

The first revised publication exposed a real source-tool boundary: the isolated
Lakehouse analyzer could not see a previous SQL tool result. NB09 now requires the
agent to copy every actual returned count tuple into the Lakehouse tool question,
not refer to an unattached "previous result". No example counts are hardcoded.

Changes were executed through the actual NB09 notebook, publication was awaited,
and draft/published instructions, sources, and Preview flags were read back.
Ontology, Lakehouse, Eventhouse, SQL sources, schema selections, and custom content
were retained. Final direct-MCP traces had no `analyze_ontology` notification.
That observation is not execution-provenance attestation.

### Foundry was not universally correct

The fresh mini-model run also reproduced an incorrect lookup:
`equipment.tag = "EQUIP_RTI_T003"`, followed by a false missing-asset answer.
It also tried nonexistent enriched-telemetry columns and progressively larger time
windows for an unbounded latest-value question.

The shipped Foundry instructions now distinguish full equipment IDs from tags and
request a single raw `OPCUAEvents` `arg_max` query for unbounded latest-per-signal
questions. The final run returned all six T003 readings correctly, with no tool
errors. Only exact previous shipped default prompts are upgraded; custom prompts
are preserved. There is no new routing or joining logic in the chat UI.

### Timers, traces, charts, and connection reuse

- Each Battle pane has its own live timer; queued time is not counted as execution.
- The 250 ms timer updates only its small component, not the answer/chart parent.
- It freezes independently on completion/failure and clears its interval on unmount.
- An epoch timestamp and an elapsed duration were previously stored in the same
  field. Repeated lifecycle notifications could produce millions of displayed
  minutes. Start times and durations are now separate.
- Internal Fabric tool notifications say **status received**, not execution duration,
  including copied transcripts. Notification counts are not query counts.
- Categorical axes and bar tooltips now show actual asset labels. Numeric and
  time-series axes retain their respective scales.
- New Chat/Battle reset clears semantic question history but retains the reusable,
  stateless MCP connection. Token/endpoint/disconnection invalidation remains.
- One reconnect for recognized transport/socket failures remains in place; no
  unbounded retries or success-shaped fallback was added.

Browser component checks verified timer advancement, a frozen final value, queued
`--`, zero intervals after completion/failure/unmount, and no parent rerender while
the timer ticks. Asset labels/tooltips were verified in the rendered SVG.

## Why the mini model remains faster

The recorded Foundry trace has a small, explicit tool set and cached asset rows
within each turn. For the corrected T003 question it made three tool calls:
metadata, one grouped latest-reading KQL query, and operational rows. Rendering the
chart adds no further source query.

Fabric's Preview SQL analyzer can perform schema/filter/example reasoning internally.
Its public MCP progress does not expose enough detail to equate lifecycle notifications
with actual SQL/KQL executions. The observed application-service and direct-MCP times
do not show a consistent additional multi-round planner in the web app, but neither
prove that every remaining difference is inside Fabric.

The supported MCP endpoint was verified. The older Python consumption guide uses
retired Assistants APIs and directs consumers to MCP; it is not a supported faster
replacement. Preview Runtime was retained. Internal Fabric UI endpoints were not
adopted as an undocumented production API.

## Validation and remaining acceptance

- 27 focused application tests passed; the final Foundry instruction/migration
  follow-up passed eight targeted tests, type-check, and lint.
- 19 related Python NB09/SQL preservation tests passed.
- Production build passed; existing large-chunk warnings remain.
- Canonical Fabric deployment completed with backend CORS/preflight/POST checks
  and preservation of all 36 existing SPA redirects.
- Actual hosted shell version and the live timer were checked. Browser MCP/Foundry
  end-to-end comparison remains blocked by the separate delegated sign-in flow.
- No socket failure occurred in the completed final protocol/service benchmark
  batches. This does not promise that socket failures can never occur.
- Initial-load/data-ready and navigation p95 acceptance, authenticated browser
  timing parity, and the answer-fidelity issues above are **not certified** by this report.

## Official references

- [Data Agent MCP server](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server)
- [Python consumption and MCP migration](https://learn.microsoft.com/en-us/fabric/data-science/consume-data-agent-python)
- [Data Agent runtime and publication](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-runtime)
- [SQL sources and Preview NL2SQL behavior](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-sql-sources)
- [Agent and source configurations](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configurations)
- [Visuals and the chart-only 200-row limit](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-visuals)
