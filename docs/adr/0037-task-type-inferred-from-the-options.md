# ADR-37: task_type inferred from the options

**Status:** Accepted, implemented. **Date:** 14 Nov 2026 (implementation).

## Decision

`task_type` is optional in `POST /v1/answer`: exactly 4 options are read as a choice question, anything else as a label task. The response echoes how the request was read.

## Alternatives considered

Require the caller to send `task_type`.

## Reason and evidence

Customers just type; nobody should have to say what kind of question it is.
