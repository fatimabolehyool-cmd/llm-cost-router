# ADR-25: MLflow with 1 worker and a 3 GB limit

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

The MLflow server runs with 1 worker and a 3 GB memory limit.

## Alternatives considered

The default 4 workers in 1 GB.

## Reason and evidence

Measured: 4 workers were killed repeatedly; 1 worker idles at 1.7 GB.
