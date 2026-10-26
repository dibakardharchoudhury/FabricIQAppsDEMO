---
name: condition-triage
description: Retrieve turbine condition and existing work using literal source facts, explicit time windows and verified equipment/signal identities.
---

# Condition triage

Use the permitted direct source tools and their exact schemas.

- BAD means telemetry signal quality, not physical damage, an outage or an approved alarm.
- A hottest-turbine ranking is not a fault threshold. Preserve latest raw versus averaged measurements.
- Preserve the requested UTC window, units, precision and source observation times.
- Resolve equipment and signals from authoritative metadata. Do not infer joins from display names.
- Read complete open work for the resolved scope; disclose incomplete coverage and truncation.
- Missing or failed reads are not empty inventories or evidence of healthy equipment.
- Stale readings cannot establish current operating conditions.
- Do not investigate causes, stage work or write SQL when assigned only factual retrieval.
