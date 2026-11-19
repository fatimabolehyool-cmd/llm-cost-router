# ADR-13: Junior concurrency cap of 4; overload returns 503

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

At most 4 junior calls run at once; further requests get 503 `overloaded`.

## Alternatives considered

Escalate to the senior on junior timeout or overload.

## Reason and evidence

Overload must not turn into paid escalations (S1). E1b: p95 5.3 s at 4 concurrent requests.

## As built

P1 load test: 460 of 460 requests succeeded; routing overhead p95 12-21 ms (`results/p1_load_*`).
