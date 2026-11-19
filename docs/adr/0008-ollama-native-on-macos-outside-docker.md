# ADR-8: Ollama native on macOS, outside Docker

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Ollama runs natively on the Mac; the router container reaches it at `host.docker.internal:11434`.

## Alternatives considered

Ollama in a container.

## Reason and evidence

Docker on macOS cannot use the Apple GPU.

## As built

E3 measured the container-to-host hop: +1.3 ms per junior call at p50 (`results/e3_*`).
