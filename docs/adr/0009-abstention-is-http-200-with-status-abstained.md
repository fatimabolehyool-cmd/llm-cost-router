# ADR-9: Abstention is HTTP 200 with status abstained

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

When an answer cannot be trusted and escalation is not allowed, the router returns HTTP 200 with `status: abstained`, `answer: null` and a reason code.

## Alternatives considered

An error code; an unverified answer.

## Reason and evidence

Abstaining is a valid outcome, not a failure.
