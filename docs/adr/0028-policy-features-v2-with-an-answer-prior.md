# ADR-28: Policy features v2 with an answer prior

**Status:** Accepted, implemented. **Date:** 11 Nov 2026 (implementation).

## Decision

The policy uses `policy-features-v2`: the nine v1 features plus an answer prior, how often this answer was right in the training data for its task type. The prior table ships with the model in `policy_meta.json`.

## Alternatives considered

Keep features v1; a separate per-category always-escalate list (FR-17).

## Reason and evidence

Features v1 failed the release gate on accuracy; v2 passed and became champion `run-e1d8cf42`. One mechanism replaces the planned always-escalate list, and the release gate checks it.

## As built

Trade-off: one more artifact to version with the model.
