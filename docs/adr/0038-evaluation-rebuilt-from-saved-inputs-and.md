# ADR-38: Evaluation rebuilt from saved inputs and regression-tested

**Status:** Accepted, implemented. **Date:** 19 Nov 2026 (resubmission).

## Decision

`pipelines/evaluate.py` is built from small pure functions. The report saves the policy probabilities it fetched from MLServer, and `--policy-probs` rebuilds it from that file without the stack. Regression tests recompute the submitted summary and the current one from committed data.

## Alternatives considered

Keep one long untested report function that needs a running MLServer.

## Reason and evidence

Every headline number came from untested code. The re-run against the live policy server on 19 Nov reproduced the submitted numbers exactly.
