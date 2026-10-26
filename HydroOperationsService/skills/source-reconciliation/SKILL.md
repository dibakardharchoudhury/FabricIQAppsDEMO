---
name: source-reconciliation
description: Reconcile verified ontology, native Data Agent and direct-source results without substituting sources or inventing identity mappings.
---

# Source reconciliation

- Preserve the explicitly selected source. Data Agent and direct ontology are separate connections.
- Require the selected live numeric ontology generation 2 and matching published source identity.
- Schema discovery is not instance execution. Report actual values and available execution receipts.
- Compare equivalent scope, UTC windows, signal/equipment identity, aggregation and work-status filters.
- Use `query_work_backlog` for complete open-work ranking or facility/equipment count reconciliation instead of
  rebuilding its SQL-to-Lakehouse joins or counts from separate reads.
- Direct verification must not be restricted to the native agent's reported population.
- Retain unmatched IDs, precision differences, missing metadata and unresolved disagreements.
- Never prefer a claim solely because an agent returned it or its configuration succeeded.
- Never fabricate topology, ownership, native execution provenance or SQL/combined-source certification.
- Preserve exact source failures; do not turn them into empty work or silently use another endpoint.
