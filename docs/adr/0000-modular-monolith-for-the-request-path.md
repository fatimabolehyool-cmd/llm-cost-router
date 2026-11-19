# ADR-0: Modular monolith for the request path

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

One FastAPI app with internal modules (API, orchestrator, adapters, privacy, egress gateway, policy client, audit sampler, store) serves every request.

## Alternatives considered

Microservices; serverless functions.

## Reason and evidence

The request path changes and is released as one unit, while the parts with their own release cycle or runtime are already separate (policy server, Ollama, PostgreSQL). The juniors, not the router, are the bottleneck (E1b), so splitting the router would add contracts and failure tests without adding throughput.
