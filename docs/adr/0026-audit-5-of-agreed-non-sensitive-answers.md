# ADR-26: Audit 5% of agreed, non-sensitive answers

**Status:** Accepted, implemented; refined by ADR-33. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

5% of agreed, non-sensitive answers are re-asked to the senior off the request path; disagreements go to human review.

## Alternatives considered

No live check; audit every answer; a third junior as verifier.

## Reason and evidence

Without it, agreed answers are never checked live. About 3.6% extra paid calls, no added latency, never touches sensitive data.

## As built

The S3 canary runs with `AUDIT_RATE=1` and finds 0 leaks on this path (architecture feedback A1).
