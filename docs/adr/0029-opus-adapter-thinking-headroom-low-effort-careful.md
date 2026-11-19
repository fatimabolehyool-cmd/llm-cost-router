# ADR-29: Opus adapter: thinking headroom, low effort, careful retries

**Status:** Accepted, implemented. **Date:** 10 Nov 2026 (implementation).

## Decision

The Opus adapter adds a 1,024-token thinking headroom to the answer budget and sets `effort: low`; the gateway reserves spend for the full output budget. It authenticates with `Authorization: Bearer` and API version 2026-10-21. It retries once after 1 s on 429, 500 or 529 or a failed connection, never on other 4xx and never after a read timeout. No sampling parameters are sent.

## Alternatives considered

The designed `max_tokens 32` with no headroom; disabling thinking (not possible on this model).

## Reason and evidence

Opus 5.5 always thinks and `max_tokens` covers thinking plus text, so a 32-token budget returned empty answers. A read timeout may come after the call was billed.

## As built

Cost per call $0.0027 against the $0.0025 estimate (E4). Without sampling parameters the senior is not deterministic, a stated limitation.
