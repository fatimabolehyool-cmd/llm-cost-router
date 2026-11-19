# ADR-30: Offline shadow report instead of live router-side shadow

**Status:** Accepted, implemented. **Date:** 12 Nov 2026 (implementation).

## Decision

A challenger is shadowed offline: `pipelines/shadow.py` replays the release split through the challenger and the serving champion, request by request, and reports changed decisions, cost and an exact sign test.

## Alternatives considered

A second model scoring live traffic inside the router.

## Reason and evidence

The same evidence without a second model on the request path; a scratch run reproduced the gate's numbers exactly (no train/serve skew).

## As built

Trade-off: the shadow sees replayed, not live, traffic.
