# ADR-1: Staged build as Compose profiles

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Build in stages: core stack, then MLflow, then Airflow, then monitoring, each a Docker Compose profile.

## Alternatives considered

Everything at once.

## Reason and evidence

Each stage is checked on its own (S1, S2), as the proposal feedback asked for a minimum deployable system first.
