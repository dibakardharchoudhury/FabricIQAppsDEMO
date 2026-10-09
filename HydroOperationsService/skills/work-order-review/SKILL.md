---
name: work-order-review
description: Prepare source-bound editable work-order proposals after fresh target and duplicate-coverage checks, preserving explicit human approval and read-only reconciliation.
---

# Work-order review

- Resolve active equipment and signal identity from authoritative sources.
- Check complete fresh open-work coverage before proposing a new card.
- Use propose_work_order for an actual editable card; prose is not a staged proposal.
- Ask only for unresolved equipment identity, not optional title, description, priority or assignee.
- Use factual editable wording without inventing a diagnosis. Preserve operator-supplied priority.
- If no evidence-backed uncovered need exists, call complete_work_order_review with a specific reason.
- No-save instructions permit staging for review, not SQL creation.
- A draft is never a created SQL work order. Approval/rejection belongs to the human.
- Submission identity, reviewed fields, source/run binding and expiration are immutable approval safeguards.
- Never retry an uncertain create. Reconciliation checks an existing identical intent and cannot initiate a first write.
- Disabled, blocked and uncertain outcomes are distinct; disclose the actual result.
