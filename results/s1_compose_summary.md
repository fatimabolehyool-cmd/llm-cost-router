# S1 minimum deployable system (2026-11-19T18:44:13+00:00)

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
router-experiments-router-1	43.71MiB / 512MiB	0.19%
router-experiments-mock-models-1	34.24MiB / 256MiB	0.16%
router-experiments-postgres-1	22.1MiB / 1GiB	0.04%
router-experiments-policy-server-1	170.3MiB / 512MiB	11.69%
router-experiments-grafana-1	131.4MiB / 512MiB	0.93%
router-experiments-prometheus-1	54.13MiB / 512MiB	1.37%
```

## Images (for docs/versions.md)

```
[{"ID":"sha256:661be8c0b7f40de4da5006715274a79db6ef9532908a18b6cad70de811649e8d","ContainerName":"router-experiments-policy-server-1","Repository":"llm-router-policy","Tag":"dev","Platform":"linux/arm64","Size":1445017416,"Created":"2026-11-13T17:17:18.777659294Z","LastTagTime":"2026-11-19T17:58:51.885136466Z"},{"ID":"sha256:6ac590e7cabc2fbe8d7b8fc1ce9c9f0582177b334e0df9c927ebd9670469440f","ContainerName":"router-experiments-grafana-1","Repository":"grafana/grafana","Tag":"12.1.0","Platform":"linux/arm64","Size":904903883,"Created":"2025-07-18T20:32:24.50956284Z","LastTagTime":"2026-11-12T14:15:14.543381044Z"},{"ID":"sha256:66f1dd294bebf0602740f56175a9145d188c89da93eb776a5cdb92d14840d2b5","ContainerName":"router-experiments-router-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":249091899,"Created":"2026-11-19T18:23:10.447023335Z","LastTagTime":"2026-11-19T18:44:01.463364345Z"},{"ID":"sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996","ContainerName":"router-experiments-prometheus-1","Repository":"prom/prometheus","Tag":"v3.5.0","Platform":"linux/arm64","Size":423337409,"Created":"2025-07-14T16:46:37.161626617Z","LastTagTime":"2026-11-12T14:15:16.381744837Z"},{"ID":"sha256:66f1dd294bebf0602740f56175a9145d188c89da93eb776a5cdb92d14840d2b5","ContainerName":"router-experiments-mock-models-1","Repository":"llm-router","Tag":"dev","Platform":"linux/arm64","Size":249091899,"Created":"2026-11-19T18:23:10.447023335Z","LastTagTime":"2026-11-19T18:44:01.463364345Z"},{"ID":"sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36","ContainerName":"router-experiments-postgres-1","Repository":"postgres","Tag":"16-alpine","Platform":"linux/arm64/v8","Size":410684018,"Created":"2026-10-21T23:19:19.130817935Z","LastTagTime":"2026-10-24T08:12:14.084221465Z"}]
```

Limitations: mock models (no model latency); one request at a time except the overload check.
