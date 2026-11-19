# ADR-20: Task types choice and label

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Only two task types: `choice` (4 options) and `label` (one category from a closed list).

## Alternatives considered

Also `number`.

## Reason and evidence

Numeric answers need long step-by-step replies, which make the juniors slow.

## As built

The caller may omit `task_type`; it is inferred from the options (ADR-37).
