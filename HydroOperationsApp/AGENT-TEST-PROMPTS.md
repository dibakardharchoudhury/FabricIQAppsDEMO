# Ten copy-and-paste multi-agent tests

Use **Intelligence -> Foundry**, not the native-only Data Agent mode. Start a
**New chat** for each numbered test. Expand **Agent crew** and inspect execution
receipts, not just animation. These are expected routes, not claims of passing tests.
Chief coordinates every handoff; arrows below omit the returns through Chief.
For observed results and limitations, see [acceptance](AGENT-ACCEPTANCE.md).

Do not approve/save test work orders or send notifications. Reject any test cards.
Stale/empty telemetry, missing BOM data, or an upstream error must be disclosed,
never replaced with an invented diagnosis, record or successful result.
Native compound requests have taken 3-5 minutes; this is not a latency guarantee.

For **every test**, structured records must be real HTML tables, not pipe-delimited
paragraphs, JSON or CSV dumps. Suitable numeric/time-series or categorical records
must also produce charts automatically, even without the word "chart". Tables stay
visible alongside charts. Check source-receipt links, exact row counts, units,
timestamps, truncation and missing-value warnings. Raw payloads are collapsed
diagnostics. An agent-response-only table is labelled as not independently
verified; displaying a table does not establish source provenance.

## 1. BAD signals -> investigation -> gap review -> verification

Expected specialists: Gauge -> Sleuth -> Fixer -> Gauge.

```text
Which turbines have latest signal quality BAD today (UTC), and what work is already open on each? Select the turbine with the most BAD signals, breaking ties by equipment ID. Investigate it using available telemetry, inspections and existing work, separating facts from hypotheses. Prepare an editable inspection draft only for an evidence-backed gap not already covered by open work. Finally, independently check the selected equipment/signal identity and existing-work coverage, and summarize any changes needed to the draft. Show a findings table and distinguish stale readings from current conditions. Do not save a work order.
```

Check: BAD means signal quality; preserve all matching signals and work. A conditional
draft may correctly result in no draft.

## 2. Ontology identity -> direct evidence -> investigation -> gap review

Expected specialists: Sparky (ontology) -> Gauge -> Sleuth -> Fixer.

```text
Use the selected ontology directly to resolve T005's equipment identity, facility and available relationships; return actual instance values, not just schema. Using those verified IDs, inspect its latest raw telemetry within the last six hours and all open SQL work. Investigate any supported concerns and prepare an editable inspection draft only if a specific uncovered issue warrants one. Show the ontology context, evidence timestamps, work coverage and uncertainty in separate sections. Do not create a SQL record.
```

Check: actual ontology instance receipt, not schema discovery alone. Empty telemetry
does not establish that the turbine is healthy or physically faulty.

## 3. Native work inventory -> verification -> investigation -> gap review

Expected specialists: Sparky (Data Agent) -> Gauge -> Sleuth -> Fixer.

```text
Ask the published Fabric Data Agent for every open operational SQL work order, including equipment ID, order number, title, priority and status. Verify that inventory against direct SQL reads, without treating SQL as a work category. Choose the equipment with the most open orders, breaking ties by equipment ID, and investigate whether its recent telemetry and inspections justify additional work. Prepare an editable follow-up draft only for a demonstrated coverage gap; otherwise explain why no new order is justified. Include a backlog table and orders-per-equipment chart. Do not save anything.
```

Check: complete inventory, explicit tie-break, chart agrees with table, no duplicate
work proposed merely because equipment already has a backlog.

## 4. Native BAD/hottest/work -> independent reconciliation -> investigation

Expected specialists: Sparky (Data Agent) -> Gauge -> Sleuth.

```text
Ask the published Fabric Data Agent which turbines have latest signal quality BAD today (UTC), the five hottest turbines from latest raw temperature readings today, and all open work on those turbines. Independently verify both sets using direct telemetry snapshots and SQL work records. Investigate any disagreement: distinguish signal quality from temperature, same-signal from equipment-level work, averaging from latest raw readings, and stale from current data. Return a reconciliation table showing source claims, verified evidence and unresolved differences. Do not silently prefer an answer or propose new work.
```

Check: direct populations are not restricted to native-reported nodes; temperature
ranking is not an approved hot/fault threshold. Preserve precision differences.

## 5. Ontology + native SQL -> direct facility backlog reconciliation

Expected specialists: Sparky (ontology), Sparky (Data Agent) -> Gauge.

```text
Use the selected ontology directly to list facility instances. Ask the published Fabric Data Agent for open operational SQL work by equipment. Reconcile those results with direct asset and work-order records, then show a facility-level backlog table and chart. Preserve unmatched IDs and source limitations instead of guessing relationships. This turn is read-only.
```

Check: two distinct native source receipts; complete direct work/equipment/facility
inventories; unmatched IDs disclosed and chart/table counts agree.

## 6. Parts stock -> risk investigation -> conditional work review

Expected specialists: Gauge -> Sleuth -> Fixer.

```text
List spare parts at or below reorder level and related open maintenance work. Investigate whether the evidence supports a parts-related risk; prepare an editable inspection draft only for a verified uncovered equipment issue. Do not order parts or save work.
```

Check: include equality at reorder level. Stock alone cannot prove equipment
compatibility, reservations, supplier lead time or a physical fault.

## 7. Telemetry/work -> downtime investigation

Expected specialists: Gauge -> Sleuth.

```text
Inspect T005's recent telemetry and open work, then investigate whether downtime can actually be established from those sources. Separate sensor quality from loss of production; do not infer an outage from BAD alone.
```

Check: no invented outage duration, shutdown instruction or calendar availability.

## 8. Notifications/work -> consistency investigation -> gap review

Expected specialists: Gauge -> Sleuth -> Fixer.

```text
Read maintenance notifications and open work for T005. Investigate inconsistencies and prepare an editable inspection draft only if an uncovered issue is supported. Do not save or send a notification.
```

Check: empty scoped reads remain empty; no invented notification or delivery claim.
Optional follow-up in the same chat:

```text
Prepare a concise unsent notification from that assessment. Include verified observations and evidence gaps, but no invented fault or recipient. Do not send it or save a work order.
```

## 9. Station chart -> scientific review -> follow-up verification

Expected specialists: Gauge -> Sleuth; follow-up Gauge.

```text
Chart average power output per station over the last 24 hours. Investigate whether the returned data support a change in equipment condition. Keep observations, competing explanations and missing evidence separate.
```

Then, without resetting:

```text
Independently verify chart values, units, sample counts and freshness. Explain why mean turbine readings are not total station power or energy.
```

Check: one mean per station, MW, sample/BAD counts, exact aggregation semantics and
timestamps. New rolling-window values may change; do not claim unchanged values
without comparison. Neither a mean nor a small difference proves causation.

## 10. Editable low-priority work -> coverage verification -> fault review

Expected specialists: Fixer and Gauge; follow-up Sleuth.

```text
Check T005 identity and all existing open work. Prepare one editable Low-priority inspection work-order draft titled Acceptance review T005 DO NOT DISPATCH, with no claimed fault. Independently verify coverage. Do not save.
```

Reject the card, then ask:

```text
Investigate whether any physical fault was established. Keep the draft separate from saved work and do not submit it. Show the evidence gaps.
```

Check: exact title and Low priority, one editable card, rejection disables it, no
claim of SQL creation or an established physical fault.

## Record results

For each run record app version, prompt, actual participating agents, response/tool
receipts, duration, answer/chart/card outcome and any error. A completed animation
does not mean a correct answer. A successful native call does not attest its internal
query provenance. Battle work-order cards use a labelled shared Foundry approval
workflow; they are not evidence of independent native Data Agent writes.
