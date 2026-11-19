# ADR-23: Challenger model: logistic regression

**Status:** Accepted, amended by ADR-28. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

The challenger is a logistic regression on `policy-features-v1`.

## Alternatives considered

Gradient boosting; a neural model.

## Reason and evidence

Calibrated probabilities from a small model with nine features.

## As built

Features v1 failed the gate on accuracy; the same model on features v2 passed (ADR-28).
