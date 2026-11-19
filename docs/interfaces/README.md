# Interface contracts (as built)

Eleven interfaces connect the parts. This page gives each one as the code implements it at
`v1.1.0`; where implementation changed the architecture-phase contract (24 Oct 2026), the row
says so. I7 has its own page: [I7-policy-admin.md](I7-policy-admin.md). The diagram that places
them is in [../architecture.md](../architecture.md).

| ID | Caller → consumer | Protocol and endpoint | Request → response | Authentication | Timeout and retry | Errors and failure behaviour | Versioning |
| --- | --- | --- | --- | --- | --- | --- | --- |
| I1 | Application or demo page → router | HTTP/JSON (localhost), `POST /v1/answer`, synchronous | `AnswerRequest` → `AnswerResponse` (below) | `X-API-Key`, compared as SHA-256 in constant time, before the body is validated | Clients retry only on 503 | 400 `invalid_request` (also non-English), 401 `unauthorized`, 503 `overloaded` / `models_unavailable` / `database_unavailable`, all before any paid call; an abstention is a normal 200 | `/v1`; only additive fields (`policy_p`, `task_type` were added); a breaking change ships as `/v2` |
| I2 | Application → router | `POST /v1/feedback`, synchronous | `{request_id, flag: wrong \| correct \| unsure, note?}` → `{received: true}` | `X-API-Key` | No retry needed | 400, 401, 404 unknown request, 503 database down. Upsert per (request, caller); the note is masked | As I1 |
| I3 | Junior adapter → Ollama | HTTP/JSON, `POST /api/chat` at `host.docker.internal:11434`; both juniors at once | Model tag, system and user message, temperature 0, seed 42, `num_predict` = answer budget, `num_ctx` 4096, `think: false` → reply text, token counts | None; Ollama listens on the Mac's loopback only | 8 s; no retry (the designed retry on a connection error was not built); the breaker opens after 5 failures for 60 s | Timeout, error status or open breaker → that junior gives no answer; both → 503 `models_unavailable` | Model tags in `.env`; prompts `junior-prompts-v2` |
| I4 | Egress gateway → Anthropic | HTTPS/JSON, `POST https://api.anthropic.com/v1/messages` | `claude-opus-5-5`, system and user message, `max_tokens` = answer budget + 1,024 thinking headroom, `effort: low` → text blocks, token usage | `Authorization: Bearer` with the key from `.env`; never logged (changed from `x-api-key`) | 20 s; one retry after 1 s on 429, 500, 529 or a failed connection; never on other 4xx, never after a read timeout | Spend reserved before the call and settled after. Sensitive → `egress_blocked`; cap reached → `budget_exceeded`; failure → `senior_unavailable`; unreadable reply → `senior_unreadable` (all abstentions) | `anthropic-version: 2026-10-21`; model ID in configuration |
| I5 | Policy client → MLServer | HTTP/JSON, V2 inference, `POST /v2/models/routing-policy/infer` | `features`, FP32, shape [1, 10] (`policy-features-v2`) → `predict_proba`, `model_version` | None; internal Docker network | 60 ms; one retry on a dropped connection | Invalid vector (checked before sending), timeout, any 5xx, or a different `model_version` → rule-v0 decides, counted in `router_policy_fallbacks` | Feature version and threshold in `policy_meta.json`; a feature change is a new model version |
| I6 | Router → PostgreSQL | SQL over asyncpg, pool of 1–5 connections | Spend reservation and settlement, request log, feedback, audit results, readiness check | Database user from `.env` | Acquire 1 s, statement 2 s; no retry | The spend reservation is one atomic `UPDATE … RETURNING`. The log is queued (1,000 rows) and written in the background; a full queue drops rows and counts them. Database down → 503 `database_unavailable` | Numbered migrations, applied at start-up |
| I7 | Operator → registry, MLServer, router | `pipelines.registry promote \| rollback`, then MLServer `POST /v2/repository/models/routing-policy/load`, then router `POST /admin/policy/reload` | Registry version → model loaded and serving | Separate admin key (SHA-256 stored), never the caller key | Seconds; no automatic retry | MLServer load fails → the previous version keeps serving; reload fails → 409 and the previous policy keeps serving | MLflow registry version and aliases ([details](I7-policy-admin.md)) |
| I8 | Pipelines → MLflow | MLflow client over HTTP, `http://mlflow:5000` | Parameters, metrics, the MLServer package as an artifact, registry aliases; the shadow report attached to the run | None; internal network; MLflow accepts only its own host names | Client defaults | Run marked failed; the champion is unchanged | MLflow REST API |
| I9 | Airflow → pipelines CLI | `BashOperator` subprocess | `revalidate_policy`, weekly: re-validate and gate, then the shadow report. `data_retention`, daily at 03:00 | Runs inside the Airflow container | Retried once, after 2 min (re-validation) or 5 min (retention) | Exit 0 whether the gate passes or fails; a non-zero exit fails the task. The `--window` argument only labels the run (ADR-22) | CLI `--help`; DAGs in Git |
| I10 | Prometheus → router, MLServer | `GET /metrics`, every 15 s | Prometheus text format: counts and timings only, never request content | Internal network only | Scrape timeout 10 s | A failed scrape leaves a gap | Metric names start with `router_` |
| I11 | Compose and probes → every service | `GET /health`, `GET /ready`, `GET /v2/health/ready`, `pg_isready` | → 200 or 503 | Internal | Every 3–10 s, 20–30 retries | Unhealthy → dependants wait (`depends_on: service_healthy`) | – |

## The answer contract (I1)

**Request.** `question` 1–4,000 characters, mostly English; `options` 2–100 unique strings of
1–200 characters; `task_type` `choice` (exactly 4 options) or `label`, optional: when it is
omitted, 4 options are read as `choice` and any other number as `label` (ADR-37); `mode`
`cheapest` | `balanced` (default) | `best_quality`; `sensitive` defaults to false.

```json
{
  "question": "I am still waiting on my card?",
  "options": ["card_arrival", "lost_or_stolen_card", "refund_not_showing_up", "pin_blocked"],
  "mode": "balanced",
  "sensitive": false
}
```

**Response.** Every answered or abstained request returns the same routing record. The example
uses the mock models' names (`junior-a`, `senior-mock`); with the real models they read
`gemma3:12b` and `claude-opus-5-5`.

```json
{"request_id": "req_5a144e7c448c", "status": "answered", "answer": "card_arrival",
 "verified": true, "escalated": false, "model": "junior-a", "reason": "juniors_agreed",
 "data_class": "non_sensitive", "cost_usd": 0.0, "latency_ms": 34, "policy_p": null,
 "task_type": "label",
 "versions": {"api": "v1", "policy": "rule-v0", "prompt": "junior-prompts-v2",
              "junior1": "junior-a", "junior2": "junior-b", "senior": "senior-mock"}}
```

**Reasons.** Answered: `juniors_agreed`, `policy_accepted`, `escalated_disagreement`,
`escalated_low_confidence`, `best_quality_mode`, `cheapest_unverified`. Abstained:
`sensitive_disagreement`, `sensitive_low_confidence`, `juniors_unreadable`,
`senior_unavailable`, `senior_unreadable`, `budget_exceeded`, `egress_blocked`.

**Errors** share one shape and never echo the input:
`{"error": {"code": "overloaded", "message": "junior capacity full; retry later"}}`.

FastAPI also serves the generated OpenAPI description at `http://localhost:8000/docs` when the
router is running.
