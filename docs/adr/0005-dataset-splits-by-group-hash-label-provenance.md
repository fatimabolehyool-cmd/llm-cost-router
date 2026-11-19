# ADR-5: Dataset splits by group hash; label provenance

**Status:** Accepted, implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Questions are split into train, validation, release-evaluation and test by a hash of the normalized question; every label records its provenance (gold > human > senior).

## Alternatives considered

Random split; senior answers as truth.

## Reason and evidence

Near-duplicate questions always land in the same split, so the test set cannot leak into training.

## As built

A unit test found that "hasn't" and "hasnt" were treated as different questions; the group key now strips apostrophes (`common/splits.py`, `pipelines/build_replay.py`).
