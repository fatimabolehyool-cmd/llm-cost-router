# ADR-14: Senior model: Claude Opus 5.5

**Status:** Accepted, amended by ADR-29. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

The senior is Claude Opus 5.5 (`claude-opus-5-5`), set in configuration.

## Alternatives considered

Several seniors; Haiku as the senior.

## Reason and evidence

Premium quality at $4 / $20 per million tokens.

## As built

Opus 5.5 always thinks, which changed the adapter (ADR-29). Measured cost per escalation: $0.0027 (E4).
