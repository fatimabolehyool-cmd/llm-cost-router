# ADR-15: Pipelines are a Python CLI; Airflow DAGs only call it

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Training, gating, shadow reports, promotion, evaluation and retention are Python CLIs; DAGs only schedule them.

## Alternatives considered

Logic inside DAGs; a `POST /train` endpoint.

## Reason and evidence

Testable without Airflow (S2).
