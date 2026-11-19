# ADR-12: No response cache in this phase

**Status:** Accepted (cache deferred). **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

No response cache is built.

## Alternatives considered

Redis; a PostgreSQL table.

## Reason and evidence

Low value at about 0.1 requests per second; one less leak path to prove safe.
