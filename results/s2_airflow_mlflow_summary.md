# S2 Airflow + MLflow (2026-10-24T08:39:33+00:00)

**Result: PASS.** All values measured on this machine. The replay data is SYNTHETIC: this proves the mechanics, not the policy's quality.

| Check | Observed | Required | Result |
|---|---|---|---|
| Full stack built, started, Airflow healthy | 22 s | < 900 s | pass |
| DAG import errors | none | none | pass |
| Airflow ran the pipeline (dags test) | exit 0 in 6 s | success | pass |
| MLflow registry: challenger alias set | version 1, gate_passed=False | a version | pass |
| Promotion of a gate-failing version is refused | exit 2 | exit 2 | pass |

## Memory and CPU, whole stack (docker stats)

```
router-experiments-router-1	58.75MiB / 512MiB	12.52%
router-experiments-mlflow-1	1.673GiB / 2GiB	1.29%
router-experiments-mock-models-1	34.88MiB / 256MiB	9.39%
router-experiments-airflow-1	1.231GiB / 3GiB	4.13%
router-experiments-postgres-1	69.91MiB / 1GiB	1.44%
```

## Images (for docs/versions.md)

```
[{"ID":"sha256:0d39fa9c1e156359d4fdaaf31ac31accf971d11e26649340a6274cc99ed95c78","ContainerName":"router-experiments-airflow-1","Repository":"","Tag":"","Platform":"unknown","Size":0},{"ID":"sha256:d69363b790ed998fb6c247b62b7bb709295dabf94dafb416f83915d42c18cc33","ContainerName":"router-experiments-mlflow-1","Repository":"llm-router-mlflow","Tag":"dev","Platform":"linux/arm64","Size":1328999816,"Created":"2026-10-24T08:24:56.944650346Z","LastTagTime":"2026-10-24T08:39:03.983052376Z"},{"ID":"sha256:9e822e29a89e90726647f540d890f3c24a1ba6db1c2d2b17c8c288c0630292f0","ContainerName":"router-experiments-router-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":248065901,"Created":"2026-10-24T08:12:44.613429215Z","LastTagTime":"2026-10-24T08:39:03.985140085Z"},{"ID":"sha256:9e822e29a89e90726647f540d890f3c24a1ba6db1c2d2b17c8c288c0630292f0","ContainerName":"router-experiments-mock-models-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":248065901,"Created":"2026-10-24T08:12:44.613429215Z","LastTagTime":"2026-10-24T08:39:03.985140085Z"},{"ID":"sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36","ContainerName":"router-experiments-postgres-1","Repository":"postgres","Tag":"16-alpine","Platform":"linux/arm64/v8","Size":410684018,"Created":"2026-10-21T23:19:19.130817935Z","LastTagTime":"2026-10-24T08:12:14.084221465Z"}]
```

## Pipeline output (s2_revalidate_2026-10-19.json)

```json
{
  "window": "2026-10-19",
  "run_id": "84103c06b053471ab327bf11264a8351",
  "model": "routing-policy",
  "version": "1",
  "alias": "challenger",
  "data_sha256": "b590b4d77b2368d5bae0f5eea6e5b98915c073cd606608e34a280f489602b743",
  "threshold": 0.5939720966378047,
  "champion_rule_v0": {
    "coverage": 0.5333333333333333,
    "accepted_accuracy": 0.7121212121212122,
    "false_accept_rate": 0.28787878787878785,
    "false_accept_upper95": 0.34523139617472076,
    "n": 495,
    "n_accepted": 264
  },
  "challenger": {
    "coverage": 0.5333333333333333,
    "accepted_accuracy": 0.7121212121212122,
    "false_accept_rate": 0.28787878787878785,
    "false_accept_upper95": 0.34523139617472076,
    "n": 495,
    "n_accepted": 264
  },
  "gate": {
    "accuracy_not_worse": true,
    "coverage_within_2pts": true,
    "false_accept_upper95_le_25pct": false
  },
  "gate_passed": false,
  "next_step": "stay on champion"
}
```
