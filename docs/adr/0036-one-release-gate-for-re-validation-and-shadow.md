# ADR-36: One release gate for re-validation and shadow, with a sign test

**Status:** Accepted, implemented. **Date:** 17 Nov 2026 (implementation).

## Decision

`common/release_gate.py` holds the only gate rule, used by both re-validation and the shadow report: accuracy not worse than rule-v0, coverage within 2 points, false-acceptance upper bound at most 25%, and not significantly worse in an exact sign test (alpha 0.05). The p-value and paired counts are recorded in MLflow.

## Alternatives considered

Two copies of the gate; decisions on point estimates only.

## Reason and evidence

Version 1 was refused by one question out of 1,057, which is noise; a significance test makes the decision principled, and one copy cannot drift.

## As built

The release split flatters v2 (p = 0.009) because it was reused while v2 was designed; the untouched test split shows no difference.
