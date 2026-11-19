# ADR-17: Three version layers

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Versions live in three layers: the API path (`/v1`), behaviour versions (policy, prompt, models) in every response and log row, and release tags.

## Alternatives considered

Variant builds of the project.

## Reason and evidence

Any answer can be traced to its exact configuration.
