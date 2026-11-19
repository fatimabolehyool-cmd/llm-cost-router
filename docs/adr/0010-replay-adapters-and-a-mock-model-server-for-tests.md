# ADR-10: Replay adapters and a mock model server for tests

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Tests and CI use recorded answers and a mock model server instead of live models.

## Alternatives considered

Live model calls in tests.

## Reason and evidence

Free and repeatable; S1, S3 and the end-to-end tests use them.
