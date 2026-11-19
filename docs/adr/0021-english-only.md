# ADR-21: English only

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Only English questions are accepted; other languages get 400.

## Alternatives considered

Language routing.

## Reason and evidence

Removes a bias and monitoring area.

## As built

The check counts non-ASCII letters, so Spanish with few accents passes; a characterisation test documents this limitation.
