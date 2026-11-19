# ADR-31: Hot reload ordered MLServer, then router, with a version check

**Status:** Accepted, implemented. **Date:** 12 Nov 2026 (implementation).

## Decision

Promotion and rollback load the new version in MLServer first, then call `POST /admin/policy/reload`; the router switches only if the policy server serves the same `model_version`.

## Alternatives considered

Restart the router; reload the router first.

## Reason and evidence

No restart, and the router never sends one model's features to another; in the gap requests are decided by rule-v0, which can cost more but is never less safe.

## As built

Lifecycle demo: promote and rollback in about 3 s each, 13 requests during each switch, 0 failed (`results/lifecycle_demo_*`). Contract: `docs/interfaces/I7-policy-admin.md`.
