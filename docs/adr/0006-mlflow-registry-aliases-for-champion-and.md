# ADR-6: MLflow registry aliases for champion and challenger

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

The MLflow registry marks versions with the aliases `champion` and `challenger` (and `previous_champion` for rollback).

## Alternatives considered

Registry stages (deprecated in MLflow 3).

## Reason and evidence

One-step promotion and rollback (S2, lifecycle demo).
