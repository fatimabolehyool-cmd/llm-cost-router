# S1 minimum deployable system (2026-11-13T11:49:36+00:00)

**Result: PASS.** All values measured on this machine.

| Check | Observed | Required | Result |
|---|---|---|---|
| Clean start: build + start + healthy | 6 s | < 600 s | pass |
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
router-experiments-router-1	43.49MiB / 512MiB	0.21%
router-experiments-mock-models-1	33.49MiB / 256MiB	0.22%
router-experiments-postgres-1	29.23MiB / 1GiB	1.08%
router-experiments-policy-server-1	186.3MiB / 512MiB	0.26%
router-experiments-airflow-1	1.071GiB / 3GiB	2.27%
router-experiments-grafana-1	248.1MiB / 512MiB	0.20%
router-experiments-prometheus-1	47.26MiB / 512MiB	0.00%
router-experiments-mlflow-1	1.695GiB / 3GiB	3.63%
```

## Images (for docs/versions.md)

```
[{"ID":"sha256:658a9d29d0a2138449f7c5b8d53b39a62a7ce44bcb491066c7324ffa8de3f44d","ContainerName":"router-experiments-router-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":249004027,"Created":"2026-11-12T15:31:53.480990423Z","LastTagTime":"2026-11-13T11:49:24.639276798Z"},{"ID":"sha256:658a9d29d0a2138449f7c5b8d53b39a62a7ce44bcb491066c7324ffa8de3f44d","ContainerName":"router-experiments-mock-models-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":249004027,"Created":"2026-11-12T15:31:53.480990423Z","LastTagTime":"2026-11-13T11:49:24.639276798Z"},{"ID":"sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996","ContainerName":"router-experiments-prometheus-1","Repository":"prom/prometheus","Tag":"v3.5.0","Platform":"linux/arm64","Size":423337409,"Created":"2025-07-14T16:46:37.161626617Z","LastTagTime":"2026-11-12T14:15:16.381744837Z"},{"ID":"sha256:bb44981f66b3bdef7d34b8ecdb56311646b188c0b3437a5058332134a8376839","ContainerName":"router-experiments-airflow-1","Repository":"llm-router-airflow","Tag":"dev","Platform":"linux/arm64","Size":4396379550,"Created":"2026-11-11T06:21:58.192726758Z","LastTagTime":"2026-11-11T06:22:39.721556763Z"},{"ID":"sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36","ContainerName":"router-experiments-postgres-1","Repository":"postgres","Tag":"16-alpine","Platform":"linux/arm64/v8","Size":410684018,"Created":"2026-10-21T23:19:19.130817935Z","LastTagTime":"2026-10-24T08:12:14.084221465Z"},{"ID":"sha256:fe801a5e5bad80cbd2cd2b84b6b0d5d1fb5d68cf0a89ee201e0301565764fc92","ContainerName":"router-experiments-mlflow-1","Repository":"","Tag":"","Platform":"unknown","Size":0},{"ID":"sha256:733c9e42729f655854d4abf870c64128577d71df48f4a0e8d602931d570c5626","ContainerName":"router-experiments-policy-server-1","Repository":"llm-router-policy","Tag":"dev","Platform":"linux/arm64","Size":1444990801,"Created":"2026-11-12T13:14:33.594370008Z","LastTagTime":"2026-11-12T15:31:53.333294423Z"},{"ID":"sha256:6ac590e7cabc2fbe8d7b8fc1ce9c9f0582177b334e0df9c927ebd9670469440f","ContainerName":"router-experiments-grafana-1","Repository":"grafana/grafana","Tag":"12.1.0","Platform":"linux/arm64","Size":904903883,"Created":"2025-07-18T20:32:24.50956284Z","LastTagTime":"2026-11-12T14:15:14.543381044Z"}]
```

Limitations: mock models (no model latency); one request at a time except the overload check.
