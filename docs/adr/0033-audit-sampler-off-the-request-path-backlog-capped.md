# ADR-33: Audit sampler off the request path, backlog capped at 50

**Status:** Accepted, implemented. **Date:** 12 Nov 2026 (implementation).

## Decision

Audits run as background tasks after the answer is returned, with 2 at a time and at most 50 pending; anything that goes wrong is counted as a skip, never raised.

## Alternatives considered

Audit inline before returning the answer.

## Reason and evidence

An audit must never slow down or fail an answer.

## As built

Trade-off: under load audits are skipped (counted in `router_audits_skipped`).
