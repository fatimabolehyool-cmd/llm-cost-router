# ADR-24: Ollama parallelism left at 1

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Ollama keeps its default of one request at a time per model.

## Alternatives considered

`OLLAMA_NUM_PARALLEL=4`.

## Reason and evidence

E1b: the default gives the fastest single request (0.9 s p50) and uses 5 GB less memory; 4 raises peak throughput by 45%, kept as the burst option.
