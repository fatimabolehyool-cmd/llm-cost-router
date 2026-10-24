# S1 minimum deployable system (2026-10-24T08:12:58+00:00)

**Result: PASS.** All values measured on this machine.

| Check | Observed | Required | Result |
|---|---|---|---|
| Clean start: build + start + healthy | 51 s | < 600 s | pass |
| Router restart until ready (warm) | 0.5 s | < 30 s | pass |
| All modes answer (HTTP, reason) | {"cheapest": [200, "juniors_agreed"], "balanced": [200, "juniors_agreed"], "best_quality": [200, "best_quality_mode"], "balanced_disagree": [200, "escalated_disagreement"]} | all 200 | pass |
| Requests written to request_log | 4 | 4 | pass |
| No API key / invalid body | 401 / 400 | 401 / 400 | pass |
| Overload: 12 concurrent slow requests, cap 4 | 4 served, 8 rejected 503 | 4 served, 8 rejected | pass |
| Senior calls = served escalations only (no overload escalations) | 4 vs 4 | equal | pass |
| PostgreSQL down: /ready and /v1/answer | 503 / 503 | 503 / 503 | pass |
| Recovery after PostgreSQL restarts (no router restart) | 0.5 s | < 60 s | pass |

## Memory and CPU (docker stats, idle after the checks)

```
router-experiments-router-1	42.38MiB / 512MiB	0.10%
router-experiments-postgres-1	21.81MiB / 1GiB	0.02%
router-experiments-mock-models-1	34.41MiB / 256MiB	0.11%
```

## Images (for docs/versions.md)

```
[{"ID":"sha256:61cd70379a5cf42d495c0911fa0a6c531252c6a69100e4b67cf74725169d6147","ContainerName":"router-experiments-mock-models-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":248065901,"Created":"2026-10-24T08:12:44.613429215Z","LastTagTime":"2026-10-24T08:12:45.178574298Z"},{"ID":"sha256:61cd70379a5cf42d495c0911fa0a6c531252c6a69100e4b67cf74725169d6147","ContainerName":"router-experiments-router-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":248065901,"Created":"2026-10-24T08:12:44.613429215Z","LastTagTime":"2026-10-24T08:12:45.178574298Z"},{"ID":"sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36","ContainerName":"router-experiments-postgres-1","Repository":"postgres","Tag":"16-alpine","Platform":"linux/arm64/v8","Size":410684018,"Created":"2026-10-21T23:19:19.130817935Z","LastTagTime":"2026-10-24T08:12:14.084221465Z"}]
```

Limitations: mock models (no model latency); one request at a time except the overload check.
