# ADR-3: The policy predicts P(junior answer correct); modes are thresholds

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

The policy predicts the probability that the junior answer is right; each mode is a threshold; outcomes are accept, escalate or abstain. The agreement rule is the v0 champion and the trained classifier is the challenger.

## Alternatives considered

Agreement rule only; classifier only.

## Reason and evidence

The proposal's learned cascade (51.0%) did not beat the rule (52.1%), so the classifier has to earn promotion through the release gate.

## As built

Policy v2 passed the gate and serves as champion, but on the untouched test split it is not significantly better than rule-v0 (+0.1 point, interval contains 0; `results/eval_summary_20261119-2203.md`).
