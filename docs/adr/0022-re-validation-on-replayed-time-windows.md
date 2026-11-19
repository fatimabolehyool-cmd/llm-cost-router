# ADR-22: Re-validation on replayed time windows

**Status:** Partly implemented. **Date:** 24 Oct 2026 (architecture deliverable).

## Decision

Policy re-validation runs on replayed time windows, scheduled weekly by Airflow.

## Alternatives considered

Wait four live weeks.

## Reason and evidence

The same lifecycle evidence in days (S2).

## As built

The weekly DAG passes a `--window` label to `pipelines.revalidate`, but every run uses the same replay pool: the window names the run, it does not select data. Real windows are future work (resubmission plan, extra E4).
