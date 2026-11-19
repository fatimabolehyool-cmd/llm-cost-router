# ADR-34: Demo page served by the router with a strict CSP

**Status:** Accepted, implemented. **Date:** 12-14 Nov 2026 (implementation).

## Decision

The demo page is a static file served by the router under a strict content security policy, with no credential in the page.

## Alternatives considered

A separately hosted page.

## Reason and evidence

Nothing to host elsewhere and no secret to leak.

## As built

Trade-off: the CSP allows the page's inline script. The chat replies are prepared texts per request type; the router chooses which one.
