# ADR-4: One egress gateway for every external call

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

One egress gateway is the only code that can call an external provider.

## Alternatives considered

Checks spread through the code.

## Reason and evidence

One place to test: the S3 canary sends sensitive requests through every failure path and finds 0 leaks.
