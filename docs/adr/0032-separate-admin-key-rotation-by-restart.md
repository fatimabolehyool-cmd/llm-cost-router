# ADR-32: Separate admin key; rotation by restart

**Status:** Accepted, implemented. **Date:** 12 Nov 2026 (implementation).

## Decision

Admin endpoints use their own key, stored as SHA-256 and required to differ from the caller key. `make admin-key` creates or rotates it; a restart applies it.

## Alternatives considered

A key-rotation API.

## Reason and evidence

A caller key must never be able to change the model; the same effect as a rotation API with no new endpoint.

## As built

Trade-off: rotation needs `make real-up`.
