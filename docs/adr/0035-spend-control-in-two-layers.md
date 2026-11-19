# ADR-35: Spend control in two layers

**Status:** Accepted, implemented. **Date:** 10 Nov 2026 (implementation).

## Decision

Spending is capped twice: prepaid provider credits ($10) and the router's own cap (`SPEND_CAP_USD=8`), reserved atomically in PostgreSQL before every paid call.

## Alternatives considered

Only the router's cap; per-key budgets.

## Reason and evidence

Either layer alone stops a runaway bill; the atomic reservation means concurrent requests cannot both pass.

## As built

An integration test found that a changed cap was ignored once the row existed, and that a float/NUMERIC mismatch refused a request that fitted exactly; both are fixed (`router/store.py`).
