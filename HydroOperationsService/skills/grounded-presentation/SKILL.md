---
name: grounded-presentation
description: Present concise literal source tables, explicit requested charts and real specialist receipts without field aliases, fabricated values or unrequested visualizations.
---

# Grounded presentation

- Use actual evidence IDs and JSON pointers for every table cell.
- Preserve literal source field keys, scalar values, numerical precision, nulls and timestamps.
- Never put values from different equipment/signals into one row without verified matching identities.
- Tables are the default; do not emit JSON/CSV dumps as operator-facing prose.
- Create charts only when requested and only from verified rows with suitable units.
- A requested chart must either be supplied from supported data or explicitly identified as unavailable.
- Never infer a count chart merely because numeric fields exist; do not replace nulls with zero.
- Keep output concise; put large technical payloads in the audit.
- Report real executed agent versions/response identities and material source limitations.
- Historical conversation is context, not current evidence or approval.
