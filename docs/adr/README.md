# Architecture decision records

One file per decision. ADR-0 to ADR-27 come from the architecture deliverable (24 Oct 2026);
ADR-28 to ADR-37 were made during implementation (10-17 Nov); ADR-38 during the
resubmission (19 Nov). Each record states the decision, the alternatives, the reason and
evidence, and what changed when it was built. Superseded parts stay in place so the history
can be read.

| ADR | Decision | Status |
| --- | --- | --- |
| [0](0000-modular-monolith-for-the-request-path.md) | Modular monolith for the request path | Accepted, implemented |
| [1](0001-staged-build-as-compose-profiles.md) | Staged build as Compose profiles | Accepted, implemented |
| [2](0002-policy-served-by-mlserver-behind-a-policyclient.md) | Policy served by MLServer behind a PolicyClient | Accepted, implemented |
| [3](0003-the-policy-predicts-p-junior-answer-correct-modes.md) | The policy predicts P(junior answer correct); modes are thresholds | Accepted, implemented |
| [4](0004-one-egress-gateway-for-every-external-call.md) | One egress gateway for every external call | Accepted, implemented |
| [5](0005-dataset-splits-by-group-hash-label-provenance.md) | Dataset splits by group hash; label provenance | Accepted, implemented |
| [6](0006-mlflow-registry-aliases-for-champion-and.md) | MLflow registry aliases for champion and challenger | Accepted, implemented |
| [7](0007-junior-pair-gemma3-12b-and-phi4-14b-chosen-by.md) | Junior pair gemma3:12b and phi4:14b, chosen by correlated error | Accepted, implemented |
| [8](0008-ollama-native-on-macos-outside-docker.md) | Ollama native on macOS, outside Docker | Accepted, implemented |
| [9](0009-abstention-is-http-200-with-status-abstained.md) | Abstention is HTTP 200 with status abstained | Accepted, implemented |
| [10](0010-replay-adapters-and-a-mock-model-server-for-tests.md) | Replay adapters and a mock model server for tests | Accepted, implemented |
| [11](0011-docker-compose-on-one-mac.md) | Docker Compose on one Mac | Accepted, implemented |
| [12](0012-no-response-cache-in-this-phase.md) | No response cache in this phase | Accepted (cache deferred) |
| [13](0013-junior-concurrency-cap-of-4-overload-returns-503.md) | Junior concurrency cap of 4; overload returns 503 | Accepted, implemented |
| [14](0014-senior-model-claude-opus-5-5.md) | Senior model: Claude Opus 5.5 | Accepted, amended by ADR-29 |
| [15](0015-pipelines-are-a-python-cli-airflow-dags-only-call.md) | Pipelines are a Python CLI; Airflow DAGs only call it | Accepted, implemented |
| [16](0016-prometheus-and-grafana-alert-rules-in-grafana.md) | Prometheus and Grafana; alert rules in Grafana | Accepted, versions changed |
| [17](0017-three-version-layers.md) | Three version layers | Accepted, implemented |
| [18](0018-no-vercel-the-router-serves-the-demo-page.md) | No Vercel; the router serves the demo page | Accepted, implemented |
| [19](0019-scalability-claimed-for-one-mac-only.md) | Scalability claimed for one Mac only | Accepted |
| [20](0020-task-types-choice-and-label.md) | Task types choice and label | Accepted, implemented |
| [21](0021-english-only.md) | English only | Accepted, implemented |
| [22](0022-re-validation-on-replayed-time-windows.md) | Re-validation on replayed time windows | Partly implemented |
| [23](0023-challenger-model-logistic-regression.md) | Challenger model: logistic regression | Accepted, amended by ADR-28 |
| [24](0024-ollama-parallelism-left-at-1.md) | Ollama parallelism left at 1 | Accepted, implemented |
| [25](0025-mlflow-with-1-worker-and-a-3-gb-limit.md) | MLflow with 1 worker and a 3 GB limit | Accepted, implemented |
| [26](0026-audit-5-of-agreed-non-sensitive-answers.md) | Audit 5% of agreed, non-sensitive answers | Accepted, implemented; refined by ADR-33 |
| [27](0027-target-customer-the-help-desk-of-a-uae-bank.md) | Target customer: the help desk of a UAE bank | Accepted |
| [28](0028-policy-features-v2-with-an-answer-prior.md) | Policy features v2 with an answer prior | Accepted, implemented |
| [29](0029-opus-adapter-thinking-headroom-low-effort-careful.md) | Opus adapter: thinking headroom, low effort, careful retries | Accepted, implemented |
| [30](0030-offline-shadow-report-instead-of-live-router-side.md) | Offline shadow report instead of live router-side shadow | Accepted, implemented |
| [31](0031-hot-reload-ordered-mlserver-then-router-with-a.md) | Hot reload ordered MLServer, then router, with a version check | Accepted, implemented |
| [32](0032-separate-admin-key-rotation-by-restart.md) | Separate admin key; rotation by restart | Accepted, implemented |
| [33](0033-audit-sampler-off-the-request-path-backlog-capped.md) | Audit sampler off the request path, backlog capped at 50 | Accepted, implemented |
| [34](0034-demo-page-served-by-the-router-with-a-strict-csp.md) | Demo page served by the router with a strict CSP | Accepted, implemented |
| [35](0035-spend-control-in-two-layers.md) | Spend control in two layers | Accepted, implemented |
| [36](0036-one-release-gate-for-re-validation-and-shadow.md) | One release gate for re-validation and shadow, with a sign test | Accepted, implemented |
| [37](0037-task-type-inferred-from-the-options.md) | task_type inferred from the options | Accepted, implemented |
| [38](0038-evaluation-rebuilt-from-saved-inputs-and.md) | Evaluation rebuilt from saved inputs and regression-tested | Accepted, implemented |
