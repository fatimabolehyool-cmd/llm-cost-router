# Cost-Aware LLM Router: feasibility evidence for the architecture deliverable

[![CI](https://github.com/fatimabolehyool-cmd/llm-cost-router/actions/workflows/ci.yml/badge.svg)](https://github.com/fatimabolehyool-cmd/llm-cost-router/actions/workflows/ci.yml)

This repository is the start of the project. It currently contains:
- **two experiments** (Section I of the brief);
- **three feasibility checks**, which prove every tool named in the architecture runs on this Mac;
- the **minimum deployable router skeleton** that the checks run against.

**Run everything on the MacBook (M5 Pro, 48 GB).** Numbers must come from the machine the system runs on, and must be measured, not estimated.

| | Question | Brief / feedback it answers |
|---|---|---|
| **E1** | Which two local models give a usable agreement signal, and how fast are they in parallel and under load? | D, I, feedback 3 |
| **E2** | Is the HTTP hop from the router to the policy server (MLServer) within budget? | B, F, I, feedback 7 |
| **S1** | Does the core stack (router + PostgreSQL) start in order, serve all modes, cap overload, survive a database outage and recover? | F, H, feedback 7 |
| **S2** | Does Airflow run the re-validation pipeline, and does MLflow register the challenger and enforce the release gate? | G, feedback 9 |
| **S3** | Can sensitive data leak to the external provider, the database or the logs through any path? | C, H, feedback 10 |

## One-time setup (≈ 30 min, mostly downloads)

1. Install **Docker Desktop** (Apple Silicon) and start it. In Settings → Resources, give it at least **8 GB** of memory.
2. Install **Ollama natively**, not in Docker (Docker on macOS can't use the Apple GPU):
   `brew install ollama`, then `ollama serve` in its own Terminal window.
3. In this folder:
   ```bash
   make venv        # host Python env + records exact versions in results/host-env.txt
   make test        # 60 unit tests must pass
   make pull        # ~11 GB: gemma3:4b, qwen3:4b, phi4-mini, llama3.2:3b (4 different labs)
   make data        # 100 MMLU (choice) + 100 Banking77 (label) questions, fixed seed, SHA-256 saved
   ```

## Development checks (the same ones CI runs on every pull request)

```bash
make lint     # ruff lint + format check
make cov      # unit tests with the coverage floor from pyproject.toml
make audit    # gitleaks secret scan + pip-audit on the lock files (brew install gitleaks)
```
CI (`.github/workflows/ci.yml`) runs three independent jobs: lint, tests with coverage, and the security scans. Accepted dependency risks are recorded in `docs/security/dependency-audit.md`.

## E1: juniors (≈ 30–40 min, unattended)
```bash
make exp1-screen                              # every model × every question, every pair scored
make exp1-load PAIR="<model-a> <model-b>"     # pick the pair from the screen summary
```
Pick the pair on **gap, false agreement and latency together**, not gap alone. Close other apps first.

## E2: policy serving (≈ 15 min, first build downloads ~1 GB)
```bash
make exp2-build && make exp2-train && make exp2-up && make exp2-bench && make exp2-stats && make exp2-down
```

## S1–S3: feasibility checks (Docker Desktop running; Ollama not needed, they use mock models)
```bash
make s1     # ≈ 5 min first time (builds images)
make s3     # ≈ 2 min
make s2     # ≈ 10–15 min first time (Airflow image is large)
make down   # stop everything
```
Each one writes `results/sX_..._summary.md` with PASS/FAIL per check, measured times and memory.

Web UIs while running (local only): router API docs <http://localhost:8000/docs>, MLflow <http://localhost:5001>, Airflow <http://localhost:8081>.

## What to send back
Everything in `results/`, plus one line of observations (fan noise, anything odd).

## Layout
| Path | What |
|---|---|
| `router/` | Router skeleton: API, orchestrator (rule-v0), adapters with circuit breakers, egress gateway, privacy, async PostgreSQL logger |
| `common/` | Shared by router, experiments and pipelines: answer normalizer, policy feature contract `policy-features-v1` |
| `prompts/` | Versioned junior prompts (`junior-prompts-v2`) |
| `pipelines/` | Re-validation CLI (train → evaluate vs rule-v0 → gate → register challenger), promote/rollback |
| `dags/` | Airflow DAG; it only schedules the pipelines CLI |
| `mock_models/` | Mock Ollama + mock senior provider; records what each "provider" received |
| `smoke/` | Drivers for S1–S3 |
| `exp1_juniors/`, `exp2_policy_serving/` | The two experiments |
| `infra/` | Dockerfiles, dependency lock files, PostgreSQL init script |
| `docker-compose.yml` | The stack, staged with profiles: core → `mocks` → `lifecycle` (MLflow) → `orchestration` (Airflow) |

## Notes and known limits
- **Scope (22 Oct):** task types are `choice` and `label`; `number` was dropped. Banking77 has 77 fine-grained categories, so it's harder than a typical 8-category help desk; treat results as conservative.
- E2's policy and S2's replay data are **synthetic**: they prove mechanics, not policy quality.
- Copy `.env.example` to `.env` (`make env` does it). The defaults are for local testing only; change every `CHANGE` value before anything else.
- **Checked on a Linux test machine before delivery** (not the Mac; repeat for real numbers):
  - all 52 unit tests pass;
  - S1 checks pass against a real PostgreSQL 16;
  - S3 passes: 252 sensitive requests, 0 leaks, positive controls detected;
  - Airflow 3.3.2 ran the DAG, which registered a challenger in MLflow 3.17.2 on PostgreSQL;
  - the release gate refused to promote a failing version.
  - **The Docker images themselves have not been built yet; that happens on your Mac.**
- **Findings so far, for the ADRs:**
  - MLServer 1.7.1 doesn't install on Python 3.13; MLflow 3.x refuses the file store.
  - MLflow 3.x sends telemetry unless disabled.
  - MLflow 3.x rejects requests addressed to its container name (`mlflow:5000`) unless `--allowed-hosts` is set.
  - SQLAlchemy 2.1 uses the psycopg 3 driver for `postgresql://` URLs.
  - A malformed policy request returns HTTP 500, not 400.
  - macOS uses port 5000 for AirPlay, so MLflow is published on 5001.
  - The circuit breaker's 60 s cooldown hides failure paths in tests, so S3 runs with cooldown 0.
