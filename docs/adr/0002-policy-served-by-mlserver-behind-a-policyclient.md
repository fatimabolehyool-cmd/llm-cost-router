# ADR-2: Policy served by MLServer behind a PolicyClient

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

The routing policy is served by MLServer 1.7.1 and called through `PolicyClient`; the agreement rule runs in-process as the fallback.

## Alternatives considered

Seldon Core on Kubernetes; in-process only.

## Reason and evidence

E2: 166 MB and 1.9 ms p95 per call. MLServer speaks the same V2 inference protocol as Seldon Core, so moving later does not change the router.
